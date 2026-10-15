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
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from ges2t import model_store, nemo_engine, whisper_engine
from ges2t.config import Config
from ges2t.identifiers import NEMO_SPEECH_ENGINE
from ges2t.model_store import (
    NEMO_MODEL,
    ModelStoreError,
    NeedleModelNameError,
    ProgressCallback,
    WhisperModelNameError,
)
from ges2t.nemo_engine import (
    ENTRY_LIBRARY_RELATIVE_PATH,
    EXECUTABLE_RELATIVE_PATH,
    NEMO_SPEECH_ARCHIVE_NAME,
    NEMO_SPEECH_SHA256,
    NEMO_SPEECH_SIZE,
    NemoEngine,
    NemoEngineError,
)
from ges2t.setup_command import (
    ACCEPTED_BACKEND_TOKENS,
    DEFAULT_WHISPER_MODEL_CPU,
    DEFAULT_WHISPER_MODEL_GPU,
    PartialSetupError,
    SetupBackend,
    SetupDependencies,
    SetupError,
    SetupPlan,
    UnknownBackendError,
    config_guidance,
    default_dependencies,
    default_whisper_model,
    plan_setup,
    run_setup,
)
from ges2t.transcribers.whisper_cpp_config import DEFAULT_GPU, GpuPreference
from ges2t.whisper_engine import (
    SHADER_COMPILER_NAME,
    SPIRV_HEADER_PATH,
    VULKAN_HEADER_PATH,
    VULKAN_LIBRARY_NAME,
    BuildVariant,
    EngineBuild,
    EngineLibraries,
    NoticeCallback,
    VulkanStatus,
    WhisperEngineError,
    cpu_only_build_hint,
    inspect_vulkan,
    vulkan_dev_package_hint,
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
        self.preferences: list[GpuPreference] = []
        self.forces: list[bool] = []
        self.notices: list[NoticeCallback | None] = []
        self.notice_message: str | None = None

    def __call__(
        self,
        *,
        preference: GpuPreference = DEFAULT_GPU,
        force: bool = False,
        progress: ProgressCallback | None = None,
        notice: NoticeCallback | None = None,
    ) -> EngineBuild:
        self.log.append("whisper-engine")
        self.calls += 1
        self.preferences.append(preference)
        self.forces.append(force)
        self.notices.append(notice)
        if self.error is not None:
            raise self.error
        if self.notice_message is not None and notice is not None:
            notice(self.notice_message)
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


class FakeNemoEngineInstaller:
    """Records every nemo engine extraction and can be made to fail."""

    def __init__(self, engine: NemoEngine, log: list[str]) -> None:
        self.engine = engine
        self.log = log
        self.calls = 0
        self.error: Exception | None = None

    def __call__(self, *, progress: ProgressCallback | None = None) -> NemoEngine:
        self.log.append("nemo-engine")
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.engine


class FakeNemoModelInstaller:
    """Records every nemo GGUF acquisition and can be made to fail."""

    def __init__(self, path: Path, log: list[str]) -> None:
        self.path = path
        self.log = log
        self.calls = 0
        self.error: Exception | None = None

    def __call__(self, *, progress: ProgressCallback | None = None) -> Path:
        self.log.append("nemo-model")
        self.calls += 1
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
    nemo_engine: FakeNemoEngineInstaller
    nemo_model: FakeNemoModelInstaller


def make_fakes(tmp_path: Path) -> Fakes:
    """Build fakes that return plausible paths without touching disk or network."""
    log: list[str] = []
    needle_model = FakeNeedleModelInstaller(tmp_path / "whistle.cact", log)
    needle_engine = FakeNeedleEngineInstaller(tmp_path / "libneedle.so", log)
    whisper_engine = FakeWhisperEngineInstaller(make_engine_build(tmp_path), log)
    whisper_model = FakeWhisperModelInstaller(tmp_path / "ggml.bin", log)
    nemo_engine = FakeNemoEngineInstaller(make_nemo_engine(tmp_path), log)
    nemo_model = FakeNemoModelInstaller(tmp_path / "nemotron.gguf", log)
    return Fakes(
        dependencies=SetupDependencies(
            needle_model=needle_model,
            needle_engine=needle_engine,
            whisper_engine=whisper_engine,
            whisper_model=whisper_model,
            nemo_engine=nemo_engine,
            nemo_model=nemo_model,
        ),
        log=log,
        needle_model=needle_model,
        needle_engine=needle_engine,
        whisper_engine=whisper_engine,
        whisper_model=whisper_model,
        nemo_engine=nemo_engine,
        nemo_model=nemo_model,
    )


def make_nemo_engine(tmp_path: Path) -> NemoEngine:
    """Return a minimal, self-consistent :class:`NemoEngine` for the fakes."""
    directory = tmp_path / "engine" / "nemo-speech" / "0.2.0"
    entry_library = directory / ENTRY_LIBRARY_RELATIVE_PATH
    executable = directory / EXECUTABLE_RELATIVE_PATH
    entry_library.parent.mkdir(parents=True, exist_ok=True)
    executable.parent.mkdir(parents=True, exist_ok=True)
    entry_library.write_bytes(b"\x7fELF nemo asr c")
    executable.write_bytes(b"\x7fELF nemo cli")
    return NemoEngine(
        version="0.2.0",
        tag="v0.2.0",
        archive=NEMO_SPEECH_ARCHIVE_NAME,
        sha256=NEMO_SPEECH_SHA256,
        size=NEMO_SPEECH_SIZE,
        directory=directory,
        entry_library=entry_library,
        executable=executable,
        files=(entry_library, executable),
        extracted_at="2026-10-14T00:00:00+00:00",
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


def test_canonical_whisper_engine_spelling_is_accepted_as_the_backend_token() -> None:
    # "whisper_cpp" is the config value; a user who has read the config docs must
    # not be told it is an unknown backend (ticket 47).
    assert plan_setup("whisper_cpp", "medium-q5_0") == SetupPlan(
        SetupBackend.WHISPER_CPP, "medium-q5_0"
    )
    assert SetupBackend.WHISPER_CPP.value == "whisper.cpp"
    assert SetupBackend.WHISPER_CPP.engine == "whisper_cpp"
    assert SetupBackend.NEEDLE.engine == "whistle"


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
    assert error.valid == ("needle", "whisper.cpp", "nemo_speech", "whisper_cpp")
    message = str(error)
    for token in error.valid:
        assert token in message
    # A near miss points at the right spelling as well as listing the set.
    assert "did you mean 'whisper_cpp'?" in message


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


# --- the nemo_speech backend (ticket 57, ADR 0022) -----------------------------


def test_nemo_backend_token_is_the_engine_value() -> None:
    # Rules 1/2: one spelling. The setup token, the engine value and (ticket 58's)
    # TOML block name are the same string, so there is no alias to normalise and
    # no second constant to keep in step.
    assert NEMO_SPEECH_ENGINE == "nemo_speech"
    assert SetupBackend.NEMO_SPEECH.value == NEMO_SPEECH_ENGINE
    assert SetupBackend.NEMO_SPEECH.engine == NEMO_SPEECH_ENGINE
    assert NEMO_SPEECH_ENGINE in ACCEPTED_BACKEND_TOKENS


def test_nemo_speech_plan_resolves_the_one_pinned_model() -> None:
    assert plan_setup(NEMO_SPEECH_ENGINE, None) == SetupPlan(
        backend=SetupBackend.NEMO_SPEECH, model=NEMO_MODEL.name
    )


def test_nemo_speech_refuses_a_model_argument_it_cannot_honour() -> None:
    # There is one pinned GGUF; accepting a name and ignoring it is how a user
    # comes to believe they switched models (rule 5).
    with pytest.raises(SetupError) as excinfo:
        plan_setup(NEMO_SPEECH_ENGINE, "some-other-model")

    message = str(excinfo.value)
    assert NEMO_MODEL.name in message
    assert "takes no model argument" in message
    assert "some-other-model" in message


def test_nemo_run_extracts_the_engine_then_acquires_the_model(tmp_path: Path) -> None:
    fakes = make_fakes(tmp_path)

    outcome = run_setup(
        SetupPlan(SetupBackend.NEMO_SPEECH, NEMO_MODEL.name),
        dependencies=fakes.dependencies,
    )

    assert fakes.log == ["nemo-engine", "nemo-model"]
    assert outcome.nemo_engine == fakes.nemo_engine.engine
    assert outcome.nemo_model_path == fakes.nemo_model.path
    assert outcome.needle_model_path is None
    assert outcome.needle_engine_path is None
    assert outcome.whisper_engine is None
    assert outcome.whisper_model_path is None


def test_nemo_engine_failure_propagates_and_never_acquires_a_model(
    tmp_path: Path,
) -> None:
    fakes = make_fakes(tmp_path)
    fakes.nemo_engine.error = NemoEngineError("archive download refused")

    with pytest.raises(NemoEngineError):
        run_setup(
            SetupPlan(SetupBackend.NEMO_SPEECH, NEMO_MODEL.name),
            dependencies=fakes.dependencies,
        )

    assert fakes.log == ["nemo-engine"]
    assert fakes.nemo_model.calls == 0


def test_nemo_model_failure_after_the_extraction_is_typed_and_names_state(
    tmp_path: Path,
) -> None:
    fakes = make_fakes(tmp_path)
    fakes.nemo_model.error = ModelStoreError("download refused")

    with pytest.raises(PartialSetupError) as excinfo:
        run_setup(
            SetupPlan(SetupBackend.NEMO_SPEECH, NEMO_MODEL.name),
            dependencies=fakes.dependencies,
        )

    error = excinfo.value
    assert error.missing == f"model {NEMO_MODEL.name}"
    assert "v0.2.0" in error.installed
    assert isinstance(error.cause, ModelStoreError)
    assert fakes.log == ["nemo-engine", "nemo-model"]


def test_default_dependencies_use_the_real_acquisions() -> None:
    deps = default_dependencies()
    assert deps.nemo_engine is nemo_engine.ensure_engine
    assert deps.nemo_model is model_store.ensure_nemo_model
    assert deps.needle_model is model_store.ensure_model
    assert deps.whisper_engine is whisper_engine.ensure_engine


# --- the config line setup cannot write itself (ticket 47) ---------------------


def test_config_guidance_prints_the_exact_engine_line() -> None:
    plan = SetupPlan(SetupBackend.WHISPER_CPP, "medium-q5_0")
    guidance = config_guidance(plan, path=Path("/home/u/.config/ges2t/config.toml"))
    assert 'transcriber.engine = "whisper_cpp"' in guidance
    assert "/home/u/.config/ges2t/config.toml" in guidance
    # The consequence of the batch-only engine is spelled out, but the old
    # "also set [streaming] enabled = false" line is gone: ticket 50 makes the
    # default follow the engine, so the line is redundant (and misleading).
    assert "batch-only" in guidance
    assert "streaming is off by default" in guidance
    assert "enabled = false" not in guidance


def test_config_guidance_for_the_default_engine_says_no_line_is_needed() -> None:
    plan = SetupPlan(SetupBackend.NEEDLE, "whistle")
    guidance = config_guidance(plan, path=Path("/home/u/.config/ges2t/config.toml"))
    assert "whistle" in guidance
    assert "no line is needed" in guidance
    assert "transcriber.engine" not in guidance


def test_config_guidance_for_nemo_prints_the_exact_engine_line() -> None:
    plan = SetupPlan(SetupBackend.NEMO_SPEECH, NEMO_MODEL.name)
    guidance = config_guidance(plan, path=Path("/home/u/.config/ges2t/config.toml"))
    assert 'transcriber.engine = "nemo_speech"' in guidance
    assert "/home/u/.config/ges2t/config.toml" in guidance
    # The whisper-only batch-only paragraph must not leak into the nemo guidance.
    assert "batch-only" not in guidance


def test_config_guidance_defaults_to_the_xdg_config_path() -> None:
    plan = SetupPlan(SetupBackend.WHISPER_CPP, "medium-q5_0")
    guidance = config_guidance(plan)
    assert "ges2t/config.toml" in guidance


# --- the CPU-only-build diagnostic (ticket 47, falsifiable) --------------------


def _build_with(
    tmp_path: Path, *, variant: BuildVariant, vulkan: VulkanStatus
) -> EngineBuild:
    """Return the fake build with its variant and recorded Vulkan status replaced."""
    return replace(make_engine_build(tmp_path), variant=variant, vulkan=vulkan)


#: A machine with the Vulkan runtime but no ``spirv-headers``: a GPU is present,
#: so a CPU-only build is a surprise the user should be told about.
_GPU_WITHOUT_SPIRV: VulkanStatus = VulkanStatus(
    runtime=Path("/fake/lib/libvulkan.so.1"),
    headers=Path("/fake/include/vulkan/vulkan.h"),
    spirv_headers=None,
    shader_compiler=Path("/fake/bin/glslc"),
)


def test_cpu_only_hint_is_silent_when_there_is_no_vulkan_runtime(
    tmp_path: Path,
) -> None:
    # "No GPU at all": no hint, because there is no GPU path to lose.
    no_gpu = VulkanStatus(
        runtime=None, headers=None, spirv_headers=None, shader_compiler=None
    )
    build = _build_with(tmp_path, variant=BuildVariant.CPU, vulkan=no_gpu)
    assert cpu_only_build_hint(build, system=FakeSystemProbe()) is None


def test_cpu_only_hint_names_the_missing_dev_package_and_the_pacman_line(
    tmp_path: Path,
) -> None:
    build = _build_with(tmp_path, variant=BuildVariant.CPU, vulkan=_GPU_WITHOUT_SPIRV)
    # The probe agrees: runtime, vulkan headers and glslc are present, the
    # SPIR-V header is not.
    probe = FakeSystemProbe(
        executables=(SHADER_COMPILER_NAME,),
        headers=(VULKAN_HEADER_PATH,),
        libraries=(VULKAN_LIBRARY_NAME,),
    )
    hint = cpu_only_build_hint(build, system=probe)
    assert hint is not None
    assert "spirv-headers" in hint
    assert "CPU-only" in hint
    assert "sudo pacman -S spirv-headers" in hint
    assert "ges2t setup whisper.cpp" in hint


def test_cpu_only_hint_is_the_shared_sentence_itself(tmp_path: Path) -> None:
    # The single-source property (tickets 46/47): the setup diagnostic is not a
    # second wording, it is exactly ``vulkan_dev_package_hint`` of the live
    # status. If the shared function changes, setup changes with it.
    build = _build_with(tmp_path, variant=BuildVariant.CPU, vulkan=_GPU_WITHOUT_SPIRV)
    probe = FakeSystemProbe(
        executables=(SHADER_COMPILER_NAME,),
        headers=(VULKAN_HEADER_PATH,),
        libraries=(VULKAN_LIBRARY_NAME,),
    )
    shared = vulkan_dev_package_hint(inspect_vulkan(probe))
    assert shared is not None
    assert cpu_only_build_hint(build, system=probe) == shared


def test_cpu_only_hint_names_every_missing_dev_package(tmp_path: Path) -> None:
    build = _build_with(tmp_path, variant=BuildVariant.CPU, vulkan=_GPU_WITHOUT_SPIRV)
    probe = FakeSystemProbe(libraries=(VULKAN_LIBRARY_NAME,))
    hint = cpu_only_build_hint(build, system=probe)
    assert hint == vulkan_dev_package_hint(inspect_vulkan(probe))
    assert hint is not None
    # The one ``pacman`` line names only the pieces actually missing.
    assert hint.count("sudo pacman -S") == 1
    for package in ("vulkan-headers", "spirv-headers", "shaderc"):
        assert package in hint
    assert hint.count("spirv-headers") == 2  # the list and the pacman line


def test_cpu_only_hint_is_silent_for_a_vulkan_build(tmp_path: Path) -> None:
    full = VulkanStatus(
        runtime=Path("/fake/lib/libvulkan.so.1"),
        headers=Path("/fake/include/vulkan/vulkan.h"),
        spirv_headers=Path("/fake/include/spirv/unified1/spirv.h"),
        shader_compiler=Path("/fake/bin/glslc"),
    )
    build = _build_with(tmp_path, variant=BuildVariant.VULKAN, vulkan=full)
    assert cpu_only_build_hint(build, system=_vulkan_probe()) is None


def test_cpu_only_hint_is_silent_when_nothing_dev_is_missing(tmp_path: Path) -> None:
    # A CPU-only build whose missing dev piece has since been installed: the
    # "install X" advice would now be wrong, so it is not printed.
    build = _build_with(tmp_path, variant=BuildVariant.CPU, vulkan=_GPU_WITHOUT_SPIRV)
    assert cpu_only_build_hint(build, system=_vulkan_probe()) is None


# --- the device preference crossing into the engine build (ticket 49) ----------


def test_plan_setup_reads_the_gpu_preference_from_the_config() -> None:
    config = Config(engine_blocks={"whisper_cpp": {"gpu": "cpu"}})
    plan = plan_setup("whisper.cpp", "tiny-q8_0", config=config)
    assert plan.preference is GpuPreference.CPU


def test_plan_setup_defaults_the_preference_to_auto_without_a_config() -> None:
    assert plan_setup("whisper.cpp", "tiny-q8_0").preference is GpuPreference.AUTO
    assert (
        SetupPlan(SetupBackend.WHISPER_CPP, "tiny-q8_0").preference
        is GpuPreference.AUTO
    )


def test_plan_setup_leaves_the_needle_backend_free_of_the_whisper_block() -> None:
    # A broken whisper block must not break `ges2t setup needle` (it is not read).
    config = Config(engine_blocks={"whisper_cpp": {"gpu": "cuda"}})
    plan = plan_setup("needle", None, config=config)
    assert plan.preference is GpuPreference.AUTO


def test_an_invalid_gpu_preference_is_a_setup_error() -> None:
    config = Config(engine_blocks={"whisper_cpp": {"gpu": "cuda"}})
    with pytest.raises(SetupError) as excinfo:
        plan_setup("whisper.cpp", "tiny-q8_0", config=config)
    assert "gpu" in str(excinfo.value)


def test_run_setup_passes_the_preference_rebuild_and_notice_to_the_build(
    tmp_path: Path,
) -> None:
    fakes = make_fakes(tmp_path)
    messages: list[str] = []

    def notice(message: str) -> None:
        messages.append(message)

    run_setup(
        SetupPlan(SetupBackend.WHISPER_CPP, "tiny-q8_0", GpuPreference.CPU),
        dependencies=fakes.dependencies,
        rebuild=True,
        notice=notice,
    )
    assert fakes.whisper_engine.preferences == [GpuPreference.CPU]
    assert fakes.whisper_engine.forces == [True]
    assert fakes.whisper_engine.notices == [notice]


def test_run_setup_defaults_the_build_to_auto_and_no_force(tmp_path: Path) -> None:
    fakes = make_fakes(tmp_path)
    run_setup(
        SetupPlan(SetupBackend.WHISPER_CPP, "tiny-q8_0"),
        dependencies=fakes.dependencies,
    )
    assert fakes.whisper_engine.preferences == [GpuPreference.AUTO]
    assert fakes.whisper_engine.forces == [False]
    assert fakes.whisper_engine.notices == [None]
