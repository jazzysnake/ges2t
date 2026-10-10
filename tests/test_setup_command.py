"""Tests for the backend- and model-parameterised ``ges2t setup`` (ticket 44).

Nothing here builds anything and nothing touches the network: the whisper engine
builder, the two model acquirers and the Needle engine acquirer are all fakes,
and the Vulkan probe is in-memory. The real engine build is **user-owned**
(``cmake`` is not installed on the reference machine), so it is exercised only
through fakes — the fake builder's contract is the same one
:class:`ges2t.whisper_engine.EngineBuilder` defines.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pytest

from ges2t.model_store import (
    ModelStoreError,
    NeedleModelNameError,
    ProgressCallback,
    WhisperModelNameError,
)
from ges2t.setup_command import (
    DEFAULT_WHISPER_MODEL_CPU,
    DEFAULT_WHISPER_MODEL_GPU,
    PartialSetupError,
    SetupBackend,
    SetupDependencies,
    SetupPlan,
    UnknownBackendError,
    default_whisper_model,
    plan_setup,
    run_setup,
)
from ges2t.whisper_engine import (
    SHADER_COMPILER_NAME,
    SPIRV_HEADER_PATH,
    VULKAN_HEADER_PATH,
    VULKAN_LIBRARY_NAME,
    BuildVariant,
    EngineBuild,
    EngineLibraries,
    VulkanStatus,
    WhisperEngineError,
)

# --- fakes ---------------------------------------------------------------------

#: The tools present on a machine that could build a Vulkan engine. ``cmake`` and
#: the toolchain are deliberately absent: this probe only ever drives the
#: default-model rule, which inspects Vulkan, not the build prerequisites.
_VULKAN_EXECUTABLES: tuple[str, ...] = (SHADER_COMPILER_NAME,)


class FakeSystemProbe:
    """An in-memory :class:`~ges2t.whisper_engine.SystemProbe`.

    Each argument is the set of names that "exist"; everything else is absent.
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


def _cpu_probe() -> FakeSystemProbe:
    """A probe on a machine with no Vulkan at all."""
    return FakeSystemProbe()


def _vulkan_probe() -> FakeSystemProbe:
    """A probe on a machine whose Vulkan runtime, headers and glslc are present."""
    return FakeSystemProbe(
        executables=_VULKAN_EXECUTABLES,
        headers=(VULKAN_HEADER_PATH, SPIRV_HEADER_PATH),
        libraries=(VULKAN_LIBRARY_NAME,),
    )


class FakeNeedleModelInstaller:
    """Records every needle-model acquisition and can be made to fail."""

    def __init__(self, path: Path, log: list[str]) -> None:
        self.path = path
        self.log = log
        self.names: list[str] = []
        self.error: Exception | None = None

    def __call__(
        self, name: str, *, progress: ProgressCallback | None = None
    ) -> Path:
        self.log.append("needle-model")
        self.names.append(name)
        if self.error is not None:
            raise self.error
        return self.path


class FakeNeedleEngineInstaller:
    """Records every Needle-engine acquisition and can be made to fail."""

    def __init__(self, path: Path, log: list[str]) -> None:
        self.path = path
        self.log = log
        self.calls = 0
        self.error: Exception | None = None

    def __call__(self, *, progress: ProgressCallback | None = None) -> Path:
        self.log.append("needle-engine")
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.path


class FakeWhisperEngineInstaller:
    """Records every whisper-engine build and can be made to fail."""

    def __init__(self, build: EngineBuild, log: list[str]) -> None:
        self.build = build
        self.log = log
        self.calls = 0
        self.error: Exception | None = None

    def __call__(self, *, progress: ProgressCallback | None = None) -> EngineBuild:
        self.log.append("whisper-engine")
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.build


class FakeWhisperModelInstaller:
    """Records every whisper-model acquisition and can be made to fail."""

    def __init__(self, path: Path, log: list[str]) -> None:
        self.path = path
        self.log = log
        self.names: list[str] = []
        self.error: Exception | None = None

    def __call__(
        self, name: str, *, progress: ProgressCallback | None = None
    ) -> Path:
        self.log.append("whisper-model")
        self.names.append(name)
        if self.error is not None:
            raise self.error
        return self.path


@dataclass
class Fakes:
    """A wired set of recording installers for one :func:`run_setup` call."""

    dependencies: SetupDependencies
    log: list[str]
    needle_model: FakeNeedleModelInstaller
    needle_engine: FakeNeedleEngineInstaller
    whisper_engine: FakeWhisperEngineInstaller
    whisper_model: FakeWhisperModelInstaller


def make_fakes(tmp_path: Path) -> Fakes:
    """Build fakes that return plausible paths without touching disk or network."""
    log: list[str] = []
    needle_model = FakeNeedleModelInstaller(tmp_path / "whistle.cact", log)
    needle_engine = FakeNeedleEngineInstaller(tmp_path / "libneedle.so", log)
    whisper_engine = FakeWhisperEngineInstaller(make_engine_build(tmp_path), log)
    whisper_model = FakeWhisperModelInstaller(tmp_path / "ggml.bin", log)
    return Fakes(
        dependencies=SetupDependencies(
            needle_model=needle_model,
            needle_engine=needle_engine,
            whisper_engine=whisper_engine,
            whisper_model=whisper_model,
        ),
        log=log,
        needle_model=needle_model,
        needle_engine=needle_engine,
        whisper_engine=whisper_engine,
        whisper_model=whisper_model,
    )


def make_engine_build(tmp_path: Path) -> EngineBuild:
    """Return a minimal, self-consistent :class:`EngineBuild` for the fakes."""
    directory = tmp_path / "engine" / "v1.9.5"
    directory.mkdir(parents=True, exist_ok=True)
    libwhisper = directory / "libwhisper.so"
    libwhisper.write_bytes(b"\x7fELF whisper")
    libggml = directory / "libggml.so"
    libggml.write_bytes(b"\x7fELF ggml")
    return EngineBuild(
        version="v1.9.5",
        tag="v1.9.5",
        revision="d1be6fde11ac6e0407606b4e42fe72d34add8037",
        variant=BuildVariant.CPU,
        directory=directory,
        libraries=EngineLibraries(libwhisper=libwhisper, ggml=(libggml,)),
        vulkan=VulkanStatus(
            runtime=None, headers=None, spirv_headers=None, shader_compiler=None
        ),
        built_at="2026-10-10T00:00:00+00:00",
    )


# --- argument parsing ----------------------------------------------------------


def test_no_arguments_resolve_to_needle_and_whistle() -> None:
    plan = plan_setup(None, None)
    assert plan == SetupPlan(backend=SetupBackend.NEEDLE, model="whistle")


def test_explicit_needle_backend_is_accepted() -> None:
    assert plan_setup("needle", None) == SetupPlan(SetupBackend.NEEDLE, "whistle")


def test_needle_model_argument_is_honoured_not_hardcoded() -> None:
    # The only pinned needle model is whistle; the point is that the argument is
    # validated and carried, not ignored.
    assert plan_setup("needle", "whistle") == SetupPlan(SetupBackend.NEEDLE, "whistle")


def test_unknown_needle_model_is_typed_and_names_the_valid_set() -> None:
    with pytest.raises(NeedleModelNameError) as excinfo:
        plan_setup("needle", "dragon")
    assert excinfo.value.name == "dragon"
    assert "whistle" in str(excinfo.value)


def test_explicit_whisper_backend_and_model_is_accepted() -> None:
    assert plan_setup("whisper.cpp", "medium-q5_0") == SetupPlan(
        SetupBackend.WHISPER_CPP, "medium-q5_0"
    )


def test_unpublished_whisper_quant_is_typed_and_names_the_valid_set() -> None:
    # ``small-q5_0`` is not published upstream (that is *why* the CPU default is
    # small-q8_0); the validation must reject it before any build or download.
    with pytest.raises(WhisperModelNameError) as excinfo:
        plan_setup("whisper.cpp", "small-q5_0")
    message = str(excinfo.value)
    assert "valid models:" in message
    assert "small-q8_0" in message


def test_unknown_whisper_size_is_rejected() -> None:
    with pytest.raises(WhisperModelNameError):
        plan_setup("whisper.cpp", "colossal-q8_0")


def test_unknown_backend_is_typed_and_names_the_valid_set() -> None:
    with pytest.raises(UnknownBackendError) as excinfo:
        plan_setup("whisper", None)
    error = excinfo.value
    assert error.name == "whisper"
    assert error.valid == ("needle", "whisper.cpp")
    assert "whisper.cpp" in str(error)


# --- the GPU/CPU default rule (falsifiable) -----------------------------------


def test_default_model_is_small_q8_0_without_vulkan() -> None:
    assert default_whisper_model(_cpu_probe()) == "small-q8_0"
    assert DEFAULT_WHISPER_MODEL_CPU == "small-q8_0"


def test_default_model_is_medium_q5_0_with_vulkan() -> None:
    assert default_whisper_model(_vulkan_probe()) == "medium-q5_0"
    assert DEFAULT_WHISPER_MODEL_GPU == "medium-q5_0"


def test_omitted_whisper_model_uses_the_cpu_default_without_vulkan() -> None:
    plan = plan_setup("whisper.cpp", None, system=_cpu_probe())
    assert plan.model == "small-q8_0"


def test_omitted_whisper_model_uses_the_gpu_default_with_vulkan() -> None:
    plan = plan_setup("whisper.cpp", None, system=_vulkan_probe())
    assert plan.model == "medium-q5_0"


def test_partial_vulkan_selects_the_cpu_default() -> None:
    # A Vulkan runtime but no dev headers/glslc builds a CPU engine, so the
    # default model must follow the *build* the same detection would choose.
    runtime_only = FakeSystemProbe(libraries=(VULKAN_LIBRARY_NAME,))
    assert default_whisper_model(runtime_only) == "small-q8_0"


# --- orchestration order -------------------------------------------------------


def test_needle_run_acquires_the_model_then_the_engine(tmp_path: Path) -> None:
    fakes = make_fakes(tmp_path)
    outcome = run_setup(
        SetupPlan(SetupBackend.NEEDLE, "whistle"), dependencies=fakes.dependencies
    )
    assert fakes.log == ["needle-model", "needle-engine"]
    assert fakes.needle_model.names == ["whistle"]
    assert outcome.needle_model_path == fakes.needle_model.path
    assert outcome.needle_engine_path == fakes.needle_engine.path
    assert outcome.whisper_engine is None
    assert outcome.whisper_model_path is None


def test_whisper_run_builds_the_engine_then_acquires_the_model(tmp_path: Path) -> None:
    fakes = make_fakes(tmp_path)
    outcome = run_setup(
        SetupPlan(SetupBackend.WHISPER_CPP, "medium-q5_0"),
        dependencies=fakes.dependencies,
    )
    assert fakes.log == ["whisper-engine", "whisper-model"]
    assert fakes.whisper_model.names == ["medium-q5_0"]
    assert outcome.whisper_engine == fakes.whisper_engine.build
    assert outcome.whisper_model_path == fakes.whisper_model.path
    assert outcome.needle_model_path is None
    assert outcome.needle_engine_path is None


# --- partial failure is diagnosable, not corrupt -------------------------------


def test_whisper_engine_failure_propagates_and_never_acquires_a_model(
    tmp_path: Path,
) -> None:
    fakes = make_fakes(tmp_path)
    fakes.whisper_engine.error = WhisperEngineError("cmake is missing")

    with pytest.raises(WhisperEngineError):
        run_setup(
            SetupPlan(SetupBackend.WHISPER_CPP, "small-q8_0"),
            dependencies=fakes.dependencies,
        )

    assert fakes.log == ["whisper-engine"]
    assert fakes.whisper_model.names == []


def test_whisper_model_failure_after_the_build_is_typed_and_names_state(
    tmp_path: Path,
) -> None:
    fakes = make_fakes(tmp_path)
    fakes.whisper_model.error = ModelStoreError("download refused")

    with pytest.raises(PartialSetupError) as excinfo:
        run_setup(
            SetupPlan(SetupBackend.WHISPER_CPP, "small-q8_0"),
            dependencies=fakes.dependencies,
        )

    error = excinfo.value
    assert error.missing == "model small-q8_0"
    assert "v1.9.5" in error.installed
    assert isinstance(error.cause, ModelStoreError)
    message = str(error)
    assert "installed" in message
    assert "download refused" in message
    assert "re-run 'ges2t setup'" in message
    # The engine was built; the model was attempted, in that order.
    assert fakes.log == ["whisper-engine", "whisper-model"]


def test_needle_engine_failure_propagates_after_the_model_is_fetched(
    tmp_path: Path,
) -> None:
    fakes = make_fakes(tmp_path)
    fakes.needle_engine.error = ModelStoreError("engine download refused")

    with pytest.raises(ModelStoreError):
        run_setup(
            SetupPlan(SetupBackend.NEEDLE, "whistle"), dependencies=fakes.dependencies
        )

    assert fakes.log == ["needle-model", "needle-engine"]
