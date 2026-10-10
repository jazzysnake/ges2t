"""Whisper backend selection and strict startup validation (ticket 45, ADR 0021).

Three groups, matching the ticket's DoD:

* the **engine selector** accepts ``transcriber.engine = "whisper_cpp"`` while the
  default stays ``whistle``, and the generic parse stays pure (no machine probe);
* the **device-aware default model** (``medium-q5_0`` on Vulkan, ``small-q8_0``
  otherwise) is resolved at *build* time by the one rule ``ges2t setup`` also
  uses (ADR 0021 §4);
* the **strict startup validation** refuses ``whisper_cpp`` with streaming, with a
  model that is not acquired, and with a library that is not built (ADR 0021 §8).

The artifact tests use a faked ``XDG_DATA_HOME`` and create the *sparse* pinned
file, so the real :func:`ges2t.model_store.whisper_model_path` and
:func:`ges2t.whisper_engine.verify_build` run unchanged; only the data directory
is redirected. **Model-vs-GPU sizing is deliberately not checked** and one test
pins that.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ges2t import config as config_module
from ges2t import model_store, pipeline, setup_command, whisper_engine
from ges2t.config import (
    Config,
    ConfigError,
    StreamingConfig,
    TranscriberConfig,
    load_config,
)
from ges2t.pipeline import PipelineError
from ges2t.transcribers.whisper_cpp import WhisperCppTranscriber
from ges2t.transcribers.whisper_cpp_config import WhisperCppConfigError
from ges2t.ui.status import NullStatusUI
from ges2t.whisper_engine import (
    ENTRY_LIBRARY_NAME,
    SHADER_COMPILER_NAME,
    SPIRV_HEADER_PATH,
    VULKAN_HEADER_PATH,
    VULKAN_LIBRARY_NAME,
    BuildVariant,
    EngineBuild,
    EngineLibraries,
    VulkanStatus,
    _write_manifest,
)
from tests.fakes import FakeAudioSource, FakeInjector
from tests.test_setup_command import FakeSystemProbe


def _cpu_probe() -> FakeSystemProbe:
    """A probe on a machine with no Vulkan at all."""
    return FakeSystemProbe()


def _vulkan_probe() -> FakeSystemProbe:
    """A probe on a machine whose Vulkan runtime, headers and ``glslc`` are present."""
    return FakeSystemProbe(
        executables=(SHADER_COMPILER_NAME,),
        headers=(VULKAN_HEADER_PATH, SPIRV_HEADER_PATH),
        libraries=(VULKAN_LIBRARY_NAME,),
    )


# --- the engine selector ------------------------------------------------------


def test_known_engines_adds_whisper_cpp_and_keeps_whistle_default() -> None:
    assert config_module.KNOWN_ENGINES == ("whistle", "whisper_cpp")
    assert config_module.DEFAULT_ENGINE == "whistle"


def test_config_parses_the_whisper_cpp_engine(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        '[transcriber]\nengine = "whisper_cpp"\n\n[streaming]\nenabled = false\n',
        encoding="utf-8",
    )
    result = load_config(path)
    assert result.config.transcriber.engine == "whisper_cpp"


def test_config_defaults_to_whistle_when_the_selector_is_absent(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[streaming]\nenabled = false\n", encoding="utf-8")
    assert load_config(path).config.transcriber.engine == "whistle"


def test_config_rejects_an_unknown_engine_naming_whisper_cpp(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[transcriber]\nengine = "nope"\n', encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        load_config(path)
    assert "whisper_cpp" in str(excinfo.value)


def test_the_generic_parse_never_probes_the_machine_for_a_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The device-aware default is applied at build time, not while parsing: a pure
    # ADR 0009 parse must stay side-effect-free (ticket 45). If `load_config`
    # called the rule, this would raise.
    def _boom(system: object = None) -> str:
        raise AssertionError("config parse must not probe the machine")

    monkeypatch.setattr(whisper_engine, "default_whisper_model", _boom)
    path = tmp_path / "config.toml"
    path.write_text(
        '[transcriber]\nengine = "whisper_cpp"\n\n[engine.whisper_cpp]\nlanguage = "en"\n',
        encoding="utf-8",
    )
    result = load_config(path)
    # The block crosses the boundary verbatim: the generic layer never inspects it.
    assert result.config.engine_blocks["whisper_cpp"] == {"language": "en"}


# --- the device-aware default model -------------------------------------------


def test_omitted_model_resolves_to_the_cpu_default_without_vulkan() -> None:
    resolved = pipeline.resolve_whisper_cpp_config({}, system=_cpu_probe())
    assert resolved.model == "small-q8_0"


def test_omitted_model_resolves_to_the_gpu_default_with_vulkan() -> None:
    resolved = pipeline.resolve_whisper_cpp_config({}, system=_vulkan_probe())
    assert resolved.model == "medium-q5_0"


def test_partial_vulkan_resolves_to_the_cpu_default() -> None:
    # A Vulkan runtime but no dev headers/glslc builds a CPU engine, so the
    # default model must follow the build the same detection would choose.
    runtime_only = FakeSystemProbe(libraries=(VULKAN_LIBRARY_NAME,))
    assert (
        pipeline.resolve_whisper_cpp_config({}, system=runtime_only).model
        == "small-q8_0"
    )


def test_an_explicit_model_wins_over_the_device_rule() -> None:
    resolved = pipeline.resolve_whisper_cpp_config(
        {"model": "tiny-q8_0"}, system=_vulkan_probe()
    )
    assert resolved.model == "tiny-q8_0"


def test_the_rule_is_one_source_of_truth_for_setup_and_startup() -> None:
    # `ges2t setup` and startup must resolve the same model; both delegate to the
    # one function (ADR 0021 §4/§6/§8, ticket 45).
    assert setup_command.default_whisper_model is whisper_engine.default_whisper_model
    for probe in (_cpu_probe(), _vulkan_probe()):
        setup_model = setup_command.plan_setup("whisper.cpp", None, system=probe).model
        startup_model = pipeline.resolve_whisper_cpp_config({}, system=probe).model
        assert setup_model == startup_model


def test_build_transcriber_resolves_an_omitted_whisper_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        whisper_engine, "default_whisper_model", lambda system=None: "tiny-q8_0"
    )
    transcriber = pipeline.build_transcriber(
        Config(transcriber=TranscriberConfig(engine="whisper_cpp"))
    )
    assert isinstance(transcriber, WhisperCppTranscriber)
    assert transcriber.config.model == "tiny-q8_0"


def test_build_transcriber_still_validates_every_whisper_value() -> None:
    config = Config(
        transcriber=TranscriberConfig(engine="whisper_cpp"),
        engine_blocks={"whisper_cpp": {"gpu": "cuda"}},
    )
    with pytest.raises(WhisperCppConfigError) as excinfo:
        pipeline.build_transcriber(config)
    assert "engine.whisper_cpp.gpu" in str(excinfo.value)


# --- strict startup validation -------------------------------------------------


def _redirect_data_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Path:
    """Point ``$XDG_DATA_HOME`` at ``tmp_path`` so the real path helpers apply."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    return tmp_path / "ges2t"


def _acquire_model(name: str) -> Path:
    """Create the *sparse* pinned model file, so acquisition is satisfied."""
    path = model_store.whisper_model_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.truncate(model_store.whisper_model(name).size_bytes)
    return path


def _build_engine() -> Path:
    """Write a complete, manifest-bearing CPU engine install (empty libraries)."""
    directory = whisper_engine.engine_dir()
    directory.mkdir(parents=True, exist_ok=True)
    lib = directory / ENTRY_LIBRARY_NAME
    lib.write_bytes(b"")
    ggml = directory / "libggml.so"
    ggml.write_bytes(b"")
    build = EngineBuild(
        version=whisper_engine.WHISPER_CPP_VERSION,
        tag=whisper_engine.WHISPER_CPP_TAG,
        revision=whisper_engine.WHISPER_CPP_REVISION,
        variant=BuildVariant.CPU,
        directory=directory,
        libraries=EngineLibraries(libwhisper=lib, ggml=(ggml,)),
        vulkan=VulkanStatus(
            runtime=None, headers=None, spirv_headers=None, shader_compiler=None
        ),
        built_at="2026-10-10T00:00:00+00:00",
    )
    _write_manifest(build)
    return directory


def _whisper_config(
    *,
    streaming: bool,
    block: dict[str, object] | None = None,
) -> Config:
    return Config(
        transcriber=TranscriberConfig(engine="whisper_cpp"),
        streaming=StreamingConfig(enabled=streaming),
        engine_blocks={"whisper_cpp": block or {"model": "tiny-q8_0"}},
    )


def test_streaming_with_whisper_is_refused_naming_both_settings() -> None:
    config = _whisper_config(streaming=True)
    with pytest.raises(PipelineError) as excinfo:
        pipeline._validate_whisper_config(config)  # noqa: SLF001
    message = str(excinfo.value)
    assert 'transcriber.engine = "whisper_cpp"' in message
    assert "streaming.enabled = true" in message
    assert "batch-only" in message
    assert excinfo.value.hint is not None
    assert "streaming.enabled = false" in excinfo.value.hint


def test_a_model_that_is_not_acquired_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _redirect_data_dir(monkeypatch, tmp_path)
    _build_engine()  # the library is fine; only the model is missing
    problem = pipeline._whisper_missing_artifacts(  # noqa: SLF001
        _whisper_config(streaming=False)
    )
    assert len(problem) == 1
    assert "tiny-q8_0" in problem[0]
    assert "engine.whisper_cpp.model" in problem[0]
    assert "not acquired" in problem[0]

    with pytest.raises(PipelineError) as excinfo:
        pipeline._validate_whisper_config(  # noqa: SLF001
            _whisper_config(streaming=False)
        )
    assert "tiny-q8_0" in str(excinfo.value)
    assert excinfo.value.hint is not None
    assert "setup whisper.cpp" in excinfo.value.hint


def test_a_library_that_is_not_built_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _redirect_data_dir(monkeypatch, tmp_path)
    _acquire_model("tiny-q8_0")  # the model is fine; only the library is missing
    problem = pipeline._whisper_missing_artifacts(  # noqa: SLF001
        _whisper_config(streaming=False)
    )
    assert len(problem) == 1
    assert "not built" in problem[0]
    assert ENTRY_LIBRARY_NAME in problem[0]


def test_a_complete_whisper_install_passes_validation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _redirect_data_dir(monkeypatch, tmp_path)
    _acquire_model("tiny-q8_0")
    _build_engine()
    assert (
        pipeline._whisper_missing_artifacts(  # noqa: SLF001
            _whisper_config(streaming=False)
        )
        == []
    )
    pipeline._validate_whisper_config(_whisper_config(streaming=False))  # noqa: SLF001


def test_model_vs_gpu_sizing_is_not_checked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A complete install passes regardless of the chosen model/device: sizing is
    # deliberately NOT a startup error (ADR 0021 §8).
    _redirect_data_dir(monkeypatch, tmp_path)
    _acquire_model("tiny-q8_0")
    _build_engine()
    config = _whisper_config(
        streaming=False, block={"model": "tiny-q8_0", "gpu": "vulkan"}
    )
    pipeline._validate_whisper_config(config)  # noqa: SLF001  # must not raise


def test_whistle_config_is_untouched_by_the_whisper_validation() -> None:
    config = Config(transcriber=TranscriberConfig(engine="whistle"))
    pipeline._validate_whisper_config(config)  # noqa: SLF001  # no-op, no raise


def test_run_service_refuses_a_whisper_misconfig_before_any_load(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    built: list[str] = []

    def _record(config: Config) -> object:
        built.append("built")
        raise AssertionError("the transcriber must not be built after a refusal")

    monkeypatch.setattr(pipeline, "build_transcriber", _record)
    monkeypatch.setattr(pipeline, "build_audio_source", lambda config: FakeAudioSource())
    monkeypatch.setattr(pipeline, "build_injector", lambda config: FakeInjector())

    result = pipeline.run_service(_whisper_config(streaming=True), status_ui=NullStatusUI())
    assert result != 0
    assert built == []
    stderr = capsys.readouterr().err
    assert "streaming.enabled = true" in stderr


# --- whistle still streams (unchanged) ----------------------------------------


def test_whistle_is_still_the_default_and_still_streams() -> None:
    transcriber = pipeline.build_transcriber(Config())
    assert transcriber.name == "whistle"
    assert transcriber.supports_streaming is True
    kind = pipeline._select_session_kind(Config(), transcriber)  # noqa: SLF001
    assert kind == "streaming"
