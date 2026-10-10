"""Tests for :mod:`ges2t.whisper_engine` (ticket 42).

No real ``git``, ``cmake`` or compiler is ever invoked: the module's
:class:`~ges2t.whisper_engine.SystemProbe`, :class:`~ges2t.whisper_engine.CommandRunner`
and :class:`~ges2t.whisper_engine.EngineBuilder` seams are all Protocol-typed, and
every test substitutes a fake at the relevant one (ADR 0014/0019). That is what
lets the suite cover the prerequisite check, the CPU-vs-Vulkan rule, the exact
configure/build argv, the idempotent path and the install step with no toolchain
present.

The fakes live here rather than in ``tests/fakes.py``: they exist only for this
module, and grouping them keeps the shared interface fakes untouched.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from ges2t.whisper_engine import (
    BUILD_TARGET,
    CLONE_TIMEOUT_SECONDS,
    DEFAULT_INCLUDE_DIRECTORIES,
    ENGINE_DIRECTORY_NAME,
    MANIFEST_NAME,
    PACKAGE_CMAKE,
    PACKAGE_SHADERC,
    PACKAGE_SPIRV_HEADERS,
    PACKAGE_TOOLCHAIN,
    PACKAGE_VULKAN_HEADERS,
    SHADER_COMPILER_NAME,
    SPIRV_HEADER_PATH,
    VULKAN_HEADER_PATH,
    VULKAN_LIBRARY_NAME,
    WHISPER_CPP_REVISION,
    WHISPER_CPP_TAG,
    WHISPER_CPP_URL,
    WHISPER_CPP_VERSION,
    BuildFailedError,
    BuildRequest,
    BuildVariant,
    EngineBuild,
    EngineLibraries,
    EngineManifestError,
    HostSystemProbe,
    PrerequisiteError,
    PrerequisiteKind,
    PrerequisiteReport,
    ProgressCallback,
    SubprocessRunner,
    VulkanStatus,
    WhisperCppBuilder,
    check_prerequisites,
    default_build_root,
    default_jobs,
    engine_dir,
    ensure_engine,
    inspect_vulkan,
    manifest_path,
    prerequisites_for,
    read_build,
    verify_build,
)

# The fake builder writes the real manifest format with the module's own writer,
# so "a complete build" means exactly what the real builder means by it.
from ges2t.whisper_engine import _write_manifest

# --- Fakes (Protocol-typed seams) --------------------------------------------


class FakeSystemProbe:
    """An in-memory :class:`~ges2t.whisper_engine.SystemProbe`.

    Each argument is the set of names that "exist"; everything else is absent,
    which is how a test simulates a missing ``cmake`` or ``spirv-headers``.
    """

    def __init__(
        self,
        *,
        executables: Iterable[str] = (),
        headers: Iterable[str] = (),
        libraries: Iterable[str] = (),
    ) -> None:
        self.executables: set[str] = set(executables)
        self.headers: set[str] = set(headers)
        self.libraries: set[str] = set(libraries)

    def executable(self, name: str) -> Path | None:
        """Return a fake path when ``name`` is configured, else ``None``."""
        return Path("/fake/bin") / name if name in self.executables else None

    def header(self, relative_path: str) -> Path | None:
        """Return a fake path when the header is configured, else ``None``."""
        if relative_path not in self.headers:
            return None
        return Path("/fake/include") / relative_path

    def library(self, name: str) -> Path | None:
        """Return a fake path when the library is configured, else ``None``."""
        return Path("/fake/lib") / f"lib{name}.so.1" if name in self.libraries else None


#: Every executable a CPU build needs. ``git`` is here because the *builder* shells
#: out to it (the reported prerequisite set deliberately does not include it; see
#: ``WhisperCppBuilder._clone``).
_TOOLCHAIN_EXECUTABLES: tuple[str, ...] = ("c++", "cc", "make", "cmake", "git")


def _cpu_probe() -> FakeSystemProbe:
    """A probe on a machine with the toolchain but nothing Vulkan."""
    return FakeSystemProbe(executables=_TOOLCHAIN_EXECUTABLES)


def _vulkan_probe() -> FakeSystemProbe:
    """A probe on a machine with Vulkan capable of a GPU build."""
    return FakeSystemProbe(
        executables=(*_TOOLCHAIN_EXECUTABLES, SHADER_COMPILER_NAME),
        headers=(VULKAN_HEADER_PATH, SPIRV_HEADER_PATH),
        libraries=(VULKAN_LIBRARY_NAME,),
    )


#: The library families a build emits: stem -> (version, soname major).
_LIBRARY_FAMILIES: tuple[tuple[str, str, str], ...] = (
    ("libwhisper.so", "1.9.5", "1"),
    ("libggml.so", "0.26.0", "0"),
    ("libggml-base.so", "0.26.0", "0"),
    ("libggml-cpu.so", "0.26.0", "0"),
    ("libggml-vulkan.so", "0.26.0", "0"),
)


def _create_build_output(
    build_dir: Path, *, vulkan_library: bool
) -> None:
    """Write the library set (real files + symlinks) CMake produces, under ``bin``."""
    bin_dir = build_dir / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for stem, version, soname_major in _LIBRARY_FAMILIES:
        if stem.startswith("libggml-vulkan") and not vulkan_library:
            continue
        real = bin_dir / f"{stem}.{version}"
        real.write_bytes(b"\x7fELF fake\n")
        soname = bin_dir / f"{stem}.{soname_major}"
        if not soname.exists():
            os.symlink(real.name, soname)
        os.symlink(soname.name, bin_dir / stem)


class FakeCommandRunner:
    """A recording :class:`~ges2t.whisper_engine.CommandRunner`.

    It answers ``git rev-parse HEAD`` with the configured ``revision`` and, when
    it sees the CMake build step, materialises the library set CMake would have
    produced under ``<build>/bin`` — real files plus the SONAME symlink chain.
    It never runs a process.
    """

    def __init__(
        self,
        *,
        revision: str = WHISPER_CPP_REVISION,
        produce_libraries: bool = True,
        vulkan_library: bool = False,
    ) -> None:
        self.revision = revision
        self.produce_libraries = produce_libraries
        self.vulkan_library = vulkan_library
        self.calls: list[tuple[str, ...]] = []
        self.timeouts: list[float] = []

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        timeout_seconds: float = 0.0,
    ) -> str:
        """Record ``argv`` and emulate the two steps whose output matters."""
        self.calls.append(tuple(argv))
        self.timeouts.append(timeout_seconds)
        if "rev-parse" in argv:
            return f"{self.revision}\n"
        if tuple(argv[:2]) == ("git", "clone"):
            Path(argv[-1]).mkdir(parents=True, exist_ok=True)
        elif argv[0].endswith("cmake") and "--build" in argv:
            build_dir = Path(argv[argv.index("--build") + 1])
            if self.produce_libraries:
                _create_build_output(build_dir, vulkan_library=self.vulkan_library)
        return ""

    def git_calls(self) -> list[tuple[str, ...]]:
        """Every recorded invocation of ``git`` (the only networked commands)."""
        return [call for call in self.calls if call and call[0] == "git"]

    def configure_calls(self) -> list[tuple[str, ...]]:
        """Every recorded CMake configure invocation."""
        return [
            call for call in self.calls if "-DCMAKE_BUILD_TYPE=Release" in call
        ]

    def build_calls(self) -> list[tuple[str, ...]]:
        """Every recorded CMake build invocation."""
        return [call for call in self.calls if "--build" in call]


class RecordingBuilder:
    """A Protocol-conforming :class:`~ges2t.whisper_engine.EngineBuilder` fake.

    It records the request, writes a complete engine (library set + manifest) into
    the requested directory, and reports progress, so the orchestration around it
    — idempotency, variant selection, prerequisite ordering — is tested without
    any real build.
    """

    def __init__(self) -> None:
        self.requests: list[BuildRequest] = []
        self.progress_sinks: list[ProgressCallback | None] = []
        self.builds: int = 0

    def build(
        self,
        request: BuildRequest,
        progress: ProgressCallback | None = None,
    ) -> EngineBuild:
        """Record the request and write a complete fake engine build."""
        self.requests.append(request)
        self.progress_sinks.append(progress)
        self.builds += 1
        request.directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as scratch_text:
            output = Path(scratch_text)
            _create_build_output(
                output,
                vulkan_library=request.variant is BuildVariant.VULKAN,
            )
            libraries = _install_fake_libraries(output / "bin", request.directory)
        build = EngineBuild(
            version=request.version,
            tag=request.tag,
            revision=request.revision,
            variant=request.variant,
            directory=request.directory,
            libraries=libraries,
            vulkan=VulkanStatus(
                runtime=None,
                headers=None,
                spirv_headers=None,
                shader_compiler=None,
            ),
            built_at="2026-10-10T00:00:00+00:00",
        )
        _write_manifest(build)
        if progress is not None:
            progress(1.0)
        return build


def _install_fake_libraries(build_bin: Path, destination: Path) -> EngineLibraries:
    """Mirror the builder's install step for the fake build outputs."""
    entry = destination / "libwhisper.so"
    ggml: list[Path] = []
    for source in sorted(build_bin.iterdir()):
        target = destination / source.name
        if target.is_symlink() or target.exists():
            target.unlink()
        if source.is_symlink():
            os.symlink(os.readlink(source), target)
        else:
            target.write_bytes(source.read_bytes())
        if source.name.startswith("libggml"):
            ggml.append(target)
    return EngineLibraries(libwhisper=entry, ggml=tuple(ggml))


@dataclass
class _ProgressSink:
    """A :data:`ges2t.interfaces.ProgressCallback` that records the fractions."""

    fractions: list[float] = field(default_factory=list)
    raise_on_call: bool = False

    def __call__(self, fraction: float) -> None:
        if self.raise_on_call:
            raise RuntimeError("broken sink")
        self.fractions.append(fraction)


def _request(variant: BuildVariant, directory: Path) -> BuildRequest:
    """A :class:`BuildRequest` for ``variant`` installing into ``directory``."""
    directory.mkdir(parents=True, exist_ok=True)
    return BuildRequest(
        version=WHISPER_CPP_VERSION,
        tag=WHISPER_CPP_TAG,
        revision=WHISPER_CPP_REVISION,
        url=WHISPER_CPP_URL,
        variant=variant,
        directory=directory,
        jobs=2,
    )


# --- Prerequisites (ADR 0021 §7) ---------------------------------------------


def test_cpu_requirement_set_is_the_base_devel_and_cmake_line() -> None:
    report = check_prerequisites(BuildVariant.CPU, _cpu_probe())
    assert report.satisfied
    assert report.packages == (PACKAGE_TOOLCHAIN, PACKAGE_CMAKE)
    assert report.pacman_command == "sudo pacman -S base-devel cmake"


def test_vulkan_requirement_set_is_the_epic_package_line() -> None:
    report = check_prerequisites(BuildVariant.VULKAN, _vulkan_probe())
    assert report.satisfied
    assert report.packages == (
        PACKAGE_TOOLCHAIN,
        PACKAGE_CMAKE,
        PACKAGE_VULKAN_HEADERS,
        PACKAGE_SPIRV_HEADERS,
        PACKAGE_SHADERC,
    )
    assert (
        report.pacman_command
        == "sudo pacman -S base-devel cmake vulkan-headers spirv-headers shaderc"
    )


def test_cpu_set_does_not_require_vulkan_packages() -> None:
    packages = {item.package for item in prerequisites_for(BuildVariant.CPU)}
    assert PACKAGE_VULKAN_HEADERS not in packages
    assert PACKAGE_SPIRV_HEADERS not in packages
    assert PACKAGE_SHADERC not in packages


def test_vulkan_set_carries_the_runtime_out_of_its_package_list() -> None:
    # The runtime selects CPU-only when absent, so it must not appear in the
    # installable package list (ADR 0021 §7).
    packages = {item.package for item in prerequisites_for(BuildVariant.VULKAN)}
    assert "vulkan-icd-loader" not in packages


def test_missing_cmake_is_reported_with_its_package_and_command() -> None:
    probe = FakeSystemProbe(executables=("c++", "cc", "make"))
    report = check_prerequisites(BuildVariant.CPU, probe)
    assert not report.satisfied
    assert [item.package for item in report.missing] == [PACKAGE_CMAKE]
    error = PrerequisiteError(report)
    assert "cmake (cmake)" in str(error)
    assert "sudo pacman -S base-devel cmake" in str(error)


def test_missing_compiler_is_reported_as_base_devel() -> None:
    probe = FakeSystemProbe(executables=("cc", "make", "cmake"))
    report = check_prerequisites(BuildVariant.CPU, probe)
    assert [item.package for item in report.missing] == [PACKAGE_TOOLCHAIN]
    assert "C++ compiler (base-devel)" in str(PrerequisiteError(report))


def test_prerequisite_kinds_route_to_the_matching_probe() -> None:
    # cmake (executable) and the Vulkan header (header) exist; everything else —
    # including the SPIR-V header and glslc — does not.
    probe = FakeSystemProbe(
        executables=("cmake",),
        headers=(VULKAN_HEADER_PATH,),
    )
    report = check_prerequisites(BuildVariant.VULKAN, probe)
    missing = {item.name for item in report.missing}
    assert missing == {
        "C++ compiler",
        "C compiler",
        "make",
        "SPIR-V headers",
        "GLSL shader compiler",
    }
    kinds = {item.name: item.kind for item in prerequisites_for(BuildVariant.VULKAN)}
    assert kinds["Vulkan headers"] is PrerequisiteKind.HEADER
    assert kinds["cmake"] is PrerequisiteKind.EXECUTABLE


def test_prerequisite_report_satisfied_only_with_no_missing_items() -> None:
    satisfied = PrerequisiteReport(
        variant=BuildVariant.CPU,
        required=prerequisites_for(BuildVariant.CPU),
        missing=(),
    )
    assert satisfied.satisfied
    assert "pacman" in satisfied.pacman_command


# --- CPU vs Vulkan detection --------------------------------------------------


def test_vulkan_available_only_when_every_piece_is_present() -> None:
    assert inspect_vulkan(_vulkan_probe()).available
    assert not inspect_vulkan(_cpu_probe()).available


def test_vulkan_absent_when_spirv_headers_are_missing() -> None:
    probe = FakeSystemProbe(
        executables=(*_TOOLCHAIN_EXECUTABLES, SHADER_COMPILER_NAME),
        headers=(VULKAN_HEADER_PATH,),
        libraries=(VULKAN_LIBRARY_NAME,),
    )
    status = inspect_vulkan(probe)
    assert not status.available
    assert "spirv-headers" in _missing_text(status)


def test_vulkan_absent_when_the_runtime_is_missing() -> None:
    probe = FakeSystemProbe(
        executables=(*_TOOLCHAIN_EXECUTABLES, SHADER_COMPILER_NAME),
        headers=(VULKAN_HEADER_PATH, SPIRV_HEADER_PATH),
    )
    status = inspect_vulkan(probe)
    assert not status.available
    assert "vulkan-icd-loader" in _missing_text(status)


def test_vulkan_absent_when_the_shader_compiler_is_missing() -> None:
    probe = FakeSystemProbe(
        executables=_TOOLCHAIN_EXECUTABLES,
        headers=(VULKAN_HEADER_PATH, SPIRV_HEADER_PATH),
        libraries=(VULKAN_LIBRARY_NAME,),
    )
    status = inspect_vulkan(probe)
    assert not status.available
    assert "shaderc" in _missing_text(status)


def test_vulkan_status_summary_names_the_available_pieces() -> None:
    assert "glslc" in inspect_vulkan(_vulkan_probe()).summary()
    assert "spirv-headers" in inspect_vulkan(_cpu_probe()).summary()


def _missing_text(status: VulkanStatus) -> str:
    """Join a Vulkan status's missing-piece names for substring assertions."""
    return " | ".join(status.missing)


# --- ensure_engine: variant selection and ordering ---------------------------


def test_ensure_engine_builds_cpu_only_when_vulkan_is_absent(tmp_path: Path) -> None:
    builder = RecordingBuilder()
    build = ensure_engine(
        directory=tmp_path / "engine",
        system=_cpu_probe(),
        builder=builder,
    )
    assert build.variant is BuildVariant.CPU
    assert build.backend == "CPU"
    assert len(builder.requests) == 1
    assert builder.requests[0].variant is BuildVariant.CPU


def test_ensure_engine_builds_vulkan_when_every_piece_is_present(
    tmp_path: Path,
) -> None:
    builder = RecordingBuilder()
    build = ensure_engine(
        directory=tmp_path / "engine",
        system=_vulkan_probe(),
        builder=builder,
    )
    assert build.variant is BuildVariant.VULKAN
    assert build.backend == "Vulkan"
    assert builder.requests[0].variant is BuildVariant.VULKAN


def test_ensure_engine_fails_before_building_when_a_prerequisite_is_missing(
    tmp_path: Path,
) -> None:
    builder = RecordingBuilder()
    probe = FakeSystemProbe(executables=("c++", "cc", "make"))
    with pytest.raises(PrerequisiteError) as caught:
        ensure_engine(directory=tmp_path / "engine", system=probe, builder=builder)
    assert "sudo pacman -S base-devel cmake" in str(caught.value)
    assert builder.requests == []
    assert builder.builds == 0


def test_ensure_engine_passes_the_pinned_revision_and_default_jobs(
    tmp_path: Path,
) -> None:
    builder = RecordingBuilder()
    ensure_engine(directory=tmp_path / "engine", system=_cpu_probe(), builder=builder)
    request = builder.requests[0]
    assert request.url == WHISPER_CPP_URL
    assert request.tag == WHISPER_CPP_TAG
    assert request.revision == WHISPER_CPP_REVISION
    assert request.version == WHISPER_CPP_VERSION
    assert request.jobs == default_jobs()
    assert default_jobs() >= 1


def test_ensure_engine_forwards_an_explicit_job_count(tmp_path: Path) -> None:
    builder = RecordingBuilder()
    ensure_engine(
        directory=tmp_path / "engine",
        system=_cpu_probe(),
        builder=builder,
        jobs=3,
    )
    assert builder.requests[0].jobs == 3


def test_ensure_engine_returns_the_build_the_builder_produced(tmp_path: Path) -> None:
    builder = RecordingBuilder()
    build = ensure_engine(
        directory=tmp_path / "engine", system=_cpu_probe(), builder=builder
    )
    assert build.directory == tmp_path / "engine"
    assert build.summary.startswith("whisper.cpp v1.9.5")
    assert read_build(build.directory) is not None


# --- ensure_engine: idempotency -----------------------------------------------


def test_ensure_engine_is_idempotent_and_builds_only_once(tmp_path: Path) -> None:
    builder = RecordingBuilder()
    first = ensure_engine(
        directory=tmp_path / "engine", system=_cpu_probe(), builder=builder
    )
    second = ensure_engine(
        directory=tmp_path / "engine", system=_cpu_probe(), builder=builder
    )
    assert builder.builds == 1
    assert second == first


def test_ensure_engine_reports_completion_when_already_built(tmp_path: Path) -> None:
    builder = RecordingBuilder()
    ensure_engine(directory=tmp_path / "engine", system=_cpu_probe(), builder=builder)
    sink = _ProgressSink()
    ensure_engine(
        directory=tmp_path / "engine",
        system=_cpu_probe(),
        builder=builder,
        progress=sink,
    )
    assert sink.fractions == [1.0]


def test_ensure_engine_forwards_the_progress_sink_to_the_builder(
    tmp_path: Path,
) -> None:
    builder = RecordingBuilder()
    sink = _ProgressSink()
    ensure_engine(
        directory=tmp_path / "engine",
        system=_cpu_probe(),
        builder=builder,
        progress=sink,
    )
    assert builder.progress_sinks == [sink]
    assert sink.fractions == [1.0]


def test_a_broken_progress_sink_cannot_abort_a_successful_ensure(
    tmp_path: Path,
) -> None:
    builder = RecordingBuilder()
    ensure_engine(directory=tmp_path / "engine", system=_cpu_probe(), builder=builder)
    sink = _ProgressSink(raise_on_call=True)
    ensure_engine(
        directory=tmp_path / "engine",
        system=_cpu_probe(),
        builder=builder,
        progress=sink,
    )
    assert builder.builds == 1


def test_ensure_engine_rebuilds_when_an_installed_library_was_deleted(
    tmp_path: Path,
) -> None:
    builder = RecordingBuilder()
    build = ensure_engine(
        directory=tmp_path / "engine", system=_cpu_probe(), builder=builder
    )
    (build.directory / "libggml.so.0.26.0").unlink()
    ensure_engine(directory=tmp_path / "engine", system=_cpu_probe(), builder=builder)
    assert builder.builds == 2


def test_ensure_engine_rebuilds_when_the_manifest_is_corrupt(tmp_path: Path) -> None:
    builder = RecordingBuilder()
    build = ensure_engine(
        directory=tmp_path / "engine", system=_cpu_probe(), builder=builder
    )
    manifest_path(build.directory).write_text("{not json", encoding="utf-8")
    ensure_engine(directory=tmp_path / "engine", system=_cpu_probe(), builder=builder)
    assert builder.builds == 2
    assert read_build(build.directory) is not None


def test_ensure_engine_keeps_an_existing_vulkan_build_without_rebuilding(
    tmp_path: Path,
) -> None:
    # Idempotency is about the installed tree, not about re-probing the machine.
    builder = RecordingBuilder()
    first = ensure_engine(
        directory=tmp_path / "engine", system=_vulkan_probe(), builder=builder
    )
    assert first.variant is BuildVariant.VULKAN
    second = ensure_engine(
        directory=tmp_path / "engine", system=_cpu_probe(), builder=builder
    )
    assert builder.builds == 1
    assert second == first


# --- The real builder, driven by a fake runner --------------------------------


def _builder(
    tmp_path: Path, runner: FakeCommandRunner, probe: FakeSystemProbe
) -> WhisperCppBuilder:
    """A :class:`WhisperCppBuilder` with its scratch tree under ``tmp_path``."""
    return WhisperCppBuilder(
        runner=runner,
        system=probe,
        build_root=tmp_path / "scratch",
    )


def test_builder_clones_the_pinned_tag_shallowly(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    _builder(tmp_path, runner, _cpu_probe()).build(
        _request(BuildVariant.CPU, tmp_path / "engine")
    )
    clone = next(call for call in runner.calls if call[:2] == ("git", "clone"))
    assert clone[:4] == ("git", "clone", "--depth", "1")
    assert clone[4:6] == ("--branch", WHISPER_CPP_TAG)
    assert clone[6] == WHISPER_CPP_URL
    assert clone[7].endswith("whisper.cpp")


def test_builder_refuses_a_revision_that_does_not_match_the_pin(
    tmp_path: Path,
) -> None:
    runner = FakeCommandRunner(revision="deadbeef")
    with pytest.raises(BuildFailedError) as caught:
        _builder(tmp_path, runner, _cpu_probe()).build(
            _request(BuildVariant.CPU, tmp_path / "engine")
        )
    assert WHISPER_CPP_REVISION in str(caught.value)
    assert "deadbeef" in str(caught.value)


def test_the_only_git_commands_are_the_pinned_clone_and_its_revision_check(
    tmp_path: Path,
) -> None:
    runner = FakeCommandRunner()
    _builder(tmp_path, runner, _cpu_probe()).build(
        _request(BuildVariant.CPU, tmp_path / "engine")
    )
    git_calls = runner.git_calls()
    assert len(git_calls) == 2
    assert git_calls[0][:2] == ("git", "clone")
    assert git_calls[1][:2] == ("git", "-C")
    assert git_calls[1][3:] == ("rev-parse", "HEAD")


def test_builder_configures_a_release_library_only_origin_rpath_build(
    tmp_path: Path,
) -> None:
    runner = FakeCommandRunner()
    _builder(tmp_path, runner, _cpu_probe()).build(
        _request(BuildVariant.CPU, tmp_path / "engine")
    )
    configure = runner.configure_calls()[0]
    assert "-DWHISPER_BUILD_TESTS=OFF" in configure
    assert "-DWHISPER_BUILD_EXAMPLES=OFF" in configure
    assert "-DCMAKE_BUILD_WITH_INSTALL_RPATH=ON" in configure
    assert "-DCMAKE_INSTALL_RPATH=$ORIGIN" in configure
    assert "-DGGML_VULKAN=ON" not in configure


def test_builder_adds_the_vulkan_flag_only_for_a_vulkan_build(
    tmp_path: Path,
) -> None:
    runner = FakeCommandRunner(vulkan_library=True)
    _builder(tmp_path, runner, _vulkan_probe()).build(
        _request(BuildVariant.VULKAN, tmp_path / "engine")
    )
    assert "-DGGML_VULKAN=ON" in runner.configure_calls()[0]


def test_builder_builds_the_whisper_target_and_never_the_cli(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    _builder(tmp_path, runner, _cpu_probe()).build(
        _request(BuildVariant.CPU, tmp_path / "engine")
    )
    build_call = runner.build_calls()[0]
    assert build_call[build_call.index("--target") + 1] == BUILD_TARGET
    assert "whisper-cli" not in " ".join(build_call)


def test_builder_bounds_every_command(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    _builder(tmp_path, runner, _cpu_probe()).build(
        _request(BuildVariant.CPU, tmp_path / "engine")
    )
    assert runner.timeouts and all(timeout > 0 for timeout in runner.timeouts)


def test_builder_uses_a_bounded_clone_timeout(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    _builder(tmp_path, runner, _cpu_probe()).build(
        _request(BuildVariant.CPU, tmp_path / "engine")
    )
    clone_index = next(
        index for index, call in enumerate(runner.calls) if call[:2] == ("git", "clone")
    )
    assert runner.timeouts[clone_index] == CLONE_TIMEOUT_SECONDS


def test_builder_installs_the_library_set_and_preserves_symlinks(
    tmp_path: Path,
) -> None:
    runner = FakeCommandRunner()
    build = _builder(tmp_path, runner, _cpu_probe()).build(
        _request(BuildVariant.CPU, tmp_path / "engine")
    )
    directory = tmp_path / "engine"
    assert build.libraries.libwhisper == directory / "libwhisper.so"
    assert (directory / "libwhisper.so").is_symlink()
    assert (directory / "libwhisper.so.1.9.5").is_file()
    assert (directory / "libggml.so.0.26.0").is_file()
    names = {path.name for path in build.libraries.ggml}
    assert {"libggml.so", "libggml-base.so", "libggml-cpu.so"} <= names
    assert build.libraries.complete()


def test_builder_installs_the_vulkan_library_for_a_vulkan_build(
    tmp_path: Path,
) -> None:
    runner = FakeCommandRunner(vulkan_library=True)
    build = _builder(tmp_path, runner, _vulkan_probe()).build(
        _request(BuildVariant.VULKAN, tmp_path / "engine")
    )
    assert (tmp_path / "engine" / "libggml-vulkan.so.0.26.0").is_file()
    assert "libggml-vulkan.so" in {path.name for path in build.libraries.ggml}


def test_builder_raises_when_the_whisper_library_was_not_produced(
    tmp_path: Path,
) -> None:
    runner = FakeCommandRunner(produce_libraries=False)
    with pytest.raises(BuildFailedError) as caught:
        _builder(tmp_path, runner, _cpu_probe()).build(
            _request(BuildVariant.CPU, tmp_path / "engine")
        )
    assert "libwhisper.so" in str(caught.value)


def test_builder_raises_when_a_vulkan_build_produced_no_vulkan_library(
    tmp_path: Path,
) -> None:
    runner = FakeCommandRunner(vulkan_library=False)
    with pytest.raises(BuildFailedError) as caught:
        _builder(tmp_path, runner, _vulkan_probe()).build(
            _request(BuildVariant.VULKAN, tmp_path / "engine")
        )
    assert "libggml-vulkan" in str(caught.value)


def test_builder_raises_prerequisite_error_when_cmake_is_absent(
    tmp_path: Path,
) -> None:
    probe = FakeSystemProbe(executables=("c++", "cc", "make"))
    builder = WhisperCppBuilder(
        runner=FakeCommandRunner(),
        system=probe,
        build_root=tmp_path / "scratch",
    )
    with pytest.raises(PrerequisiteError) as caught:
        builder.build(_request(BuildVariant.CPU, tmp_path / "engine"))
    assert "cmake" in str(caught.value)


def test_builder_raises_a_typed_error_when_git_is_absent(tmp_path: Path) -> None:
    probe = FakeSystemProbe(executables=("c++", "cc", "make", "cmake"))
    builder = WhisperCppBuilder(
        runner=FakeCommandRunner(),
        system=probe,
        build_root=tmp_path / "scratch",
    )
    with pytest.raises(BuildFailedError) as caught:
        builder.build(_request(BuildVariant.CPU, tmp_path / "engine"))
    assert "git" in str(caught.value)
    assert "sudo pacman -S git" in str(caught.value)


# --- SubprocessRunner failure modes (real subprocess, trivial and bounded) ---


def test_subprocess_runner_wraps_a_nonzero_exit_with_stderr() -> None:
    runner = SubprocessRunner()
    command = [
        sys.executable,
        "-c",
        "import sys; sys.stderr.write('boom'); sys.exit(3)",
    ]
    with pytest.raises(BuildFailedError) as caught:
        runner.run(command, cwd=None, timeout_seconds=30.0)
    assert "exit 3" in str(caught.value)
    assert "boom" in str(caught.value)


def test_subprocess_runner_wraps_a_timeout() -> None:
    runner = SubprocessRunner()
    command = [sys.executable, "-c", "import time; time.sleep(5)"]
    with pytest.raises(BuildFailedError) as caught:
        runner.run(command, cwd=None, timeout_seconds=1.0)
    assert "timed out" in str(caught.value)


def test_subprocess_runner_wraps_a_missing_executable() -> None:
    runner = SubprocessRunner()
    with pytest.raises(BuildFailedError) as caught:
        runner.run(
            ["this-binary-does-not-exist-ges2t"],
            cwd=None,
            timeout_seconds=30.0,
        )
    assert "command not found" in str(caught.value)


def test_subprocess_runner_returns_stdout() -> None:
    runner = SubprocessRunner()
    output = runner.run(
        [sys.executable, "-c", "print('hello')"],
        cwd=None,
        timeout_seconds=30.0,
    )
    assert output.strip() == "hello"


def test_builder_writes_a_manifest_read_back_by_read_build(tmp_path: Path) -> None:
    runner = FakeCommandRunner()
    build = _builder(tmp_path, runner, _cpu_probe()).build(
        _request(BuildVariant.CPU, tmp_path / "engine")
    )
    raw = json.loads(manifest_path(tmp_path / "engine").read_text(encoding="utf-8"))
    assert raw["revision"] == WHISPER_CPP_REVISION
    assert raw["tag"] == WHISPER_CPP_TAG
    assert raw["variant"] == "cpu"
    assert raw["entry_library"] == "libwhisper.so"
    assert read_build(tmp_path / "engine") == build


def test_builder_records_the_vulkan_status_it_observed(tmp_path: Path) -> None:
    runner = FakeCommandRunner(vulkan_library=True)
    build = _builder(tmp_path, runner, _vulkan_probe()).build(
        _request(BuildVariant.VULKAN, tmp_path / "engine")
    )
    assert build.vulkan.available
    assert build.vulkan.shader_compiler is not None


def test_builder_removes_its_scratch_tree(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    runner = FakeCommandRunner()
    WhisperCppBuilder(
        runner=runner, system=_cpu_probe(), build_root=scratch
    ).build(_request(BuildVariant.CPU, tmp_path / "engine"))
    assert scratch.exists()
    assert list(scratch.iterdir()) == []


def test_builder_reports_coarse_progress(tmp_path: Path) -> None:
    builder = _builder(tmp_path, FakeCommandRunner(), _cpu_probe())
    sink = _ProgressSink()
    builder.build(_request(BuildVariant.CPU, tmp_path / "engine"), sink)
    assert sink.fractions[0] == 0.0
    assert sink.fractions[-1] == 1.0
    assert sink.fractions == sorted(sink.fractions)


def test_builder_uses_the_injected_clock_for_the_timestamp(tmp_path: Path) -> None:
    builder = WhisperCppBuilder(
        runner=FakeCommandRunner(),
        system=_cpu_probe(),
        build_root=tmp_path / "scratch",
        clock=lambda: "2020-01-01T00:00:00+00:00",
    )
    build = builder.build(_request(BuildVariant.CPU, tmp_path / "engine"))
    assert build.built_at == "2020-01-01T00:00:00+00:00"


# --- Manifest ----------------------------------------------------------------


def _complete_engine(directory: Path) -> None:
    """Write a minimal complete engine tree (manifest + listed libraries)."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "libwhisper.so").write_bytes(b"lib")
    (directory / "libggml.so").write_bytes(b"lib")
    (directory / MANIFEST_NAME).write_text(
        json.dumps(
            {
                "version": "v1.9.5",
                "tag": "v1.9.5",
                "revision": WHISPER_CPP_REVISION,
                "variant": "cpu",
                "entry_library": "libwhisper.so",
                "libraries": ["libggml.so"],
                "built_at": "2026-10-10T00:00:00+00:00",
                "vulkan": {
                    "runtime": "/usr/lib/libvulkan.so.1",
                    "headers": None,
                    "spirv_headers": None,
                    "shader_compiler": None,
                },
            }
        ),
        encoding="utf-8",
    )


def test_read_build_returns_none_when_no_manifest_exists(tmp_path: Path) -> None:
    assert read_build(tmp_path / "empty") is None
    assert verify_build(tmp_path / "empty") is None


def test_read_build_round_trips_a_manifest(tmp_path: Path) -> None:
    directory = tmp_path / "engine"
    _complete_engine(directory)
    build = read_build(directory)
    assert build is not None
    assert build.variant is BuildVariant.CPU
    assert build.backend == "CPU"
    assert build.libraries.libwhisper == directory / "libwhisper.so"
    assert build.libraries.ggml == (directory / "libggml.so",)
    assert build.vulkan.runtime == Path("/usr/lib/libvulkan.so.1")
    assert build.vulkan.headers is None
    assert not build.vulkan.available


def test_read_build_rejects_a_malformed_manifest(tmp_path: Path) -> None:
    directory = tmp_path / "engine"
    directory.mkdir()
    manifest_path(directory).write_text("{not json", encoding="utf-8")
    with pytest.raises(EngineManifestError):
        read_build(directory)
    assert verify_build(directory) is None


def test_read_build_rejects_an_unknown_variant(tmp_path: Path) -> None:
    directory = tmp_path / "engine"
    _complete_engine(directory)
    raw = json.loads(manifest_path(directory).read_text(encoding="utf-8"))
    raw["variant"] = "cuda"
    manifest_path(directory).write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(EngineManifestError) as caught:
        read_build(directory)
    assert "cuda" in str(caught.value)


def test_read_build_rejects_a_missing_key(tmp_path: Path) -> None:
    directory = tmp_path / "engine"
    _complete_engine(directory)
    raw = json.loads(manifest_path(directory).read_text(encoding="utf-8"))
    del raw["revision"]
    manifest_path(directory).write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(EngineManifestError):
        read_build(directory)


def test_read_build_rejects_a_non_object_vulkan_section(tmp_path: Path) -> None:
    directory = tmp_path / "engine"
    _complete_engine(directory)
    raw = json.loads(manifest_path(directory).read_text(encoding="utf-8"))
    raw["vulkan"] = "yes"
    manifest_path(directory).write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(EngineManifestError):
        read_build(directory)


def test_verify_build_requires_a_manifest_and_existing_libraries(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "engine"
    _complete_engine(directory)
    assert verify_build(directory) is not None
    (directory / "libggml.so").unlink()
    assert verify_build(directory) is None


def test_verify_build_rejects_a_manifest_with_no_ggml_libraries(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "engine"
    _complete_engine(directory)
    raw = json.loads(manifest_path(directory).read_text(encoding="utf-8"))
    raw["libraries"] = []
    manifest_path(directory).write_text(json.dumps(raw), encoding="utf-8")
    assert verify_build(directory) is None


# --- Locations ----------------------------------------------------------------


def test_engine_dir_mirrors_the_needle_layout_under_the_data_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert engine_dir() == (
        tmp_path / "ges2t" / "engine" / ENGINE_DIRECTORY_NAME / WHISPER_CPP_VERSION
    )
    assert engine_dir("v9.9.9").name == "v9.9.9"


def test_default_build_root_is_a_cache_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    root = default_build_root()
    assert root == tmp_path / "ges2t" / "whisper-cpp-build" / WHISPER_CPP_VERSION
    # The build tree must never live under the data directory: a failed build is
    # not an installed engine.
    assert "whisper-cpp-build" in root.parts


def test_host_system_probe_reports_absent_things_as_none() -> None:
    probe = HostSystemProbe(include_directories=DEFAULT_INCLUDE_DIRECTORIES)
    assert probe.executable("this-binary-does-not-exist-ges2t") is None
    assert probe.header("this/header/does/not/exist.h") is None
    assert probe.library("this-library-does-not-exist-ges2t") is None


# --- Records -----------------------------------------------------------------


def test_build_variant_labels_distinguish_gpu_from_cpu() -> None:
    assert "CPU" in BuildVariant.CPU.label
    assert "Vulkan" in BuildVariant.VULKAN.label
    assert str(BuildVariant.CPU) == "cpu"
    assert str(BuildVariant.VULKAN) == "vulkan"


def test_engine_libraries_complete_and_missing(tmp_path: Path) -> None:
    entry = tmp_path / "libwhisper.so"
    dependency = tmp_path / "libggml.so"
    entry.write_bytes(b"lib")
    libraries = EngineLibraries(libwhisper=entry, ggml=(dependency,))
    assert libraries.missing() == (dependency,)
    assert not libraries.complete()
    dependency.write_bytes(b"lib")
    assert libraries.complete()
    assert libraries.all == (entry, dependency)


def test_engine_build_summary_names_the_variant_and_directory(tmp_path: Path) -> None:
    build = EngineBuild(
        version=WHISPER_CPP_VERSION,
        tag=WHISPER_CPP_TAG,
        revision=WHISPER_CPP_REVISION,
        variant=BuildVariant.VULKAN,
        directory=tmp_path,
        libraries=EngineLibraries(libwhisper=tmp_path / "libwhisper.so", ggml=()),
        vulkan=VulkanStatus(
            runtime=Path("/usr/lib/libvulkan.so.1"),
            headers=Path("/usr/include/vulkan/vulkan.h"),
            spirv_headers=Path("/usr/include/spirv/unified1/spirv.h"),
            shader_compiler=Path("/usr/bin/glslc"),
        ),
        built_at="2026-10-10T00:00:00+00:00",
    )
    assert "Vulkan" in build.summary
    assert str(tmp_path) in build.summary
    assert build.backend == "Vulkan"
