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
from ges2t.transcribers.whisper_cpp_config import (
    WhisperCppConfig,
    WhisperCppConfigError,
)
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
from tests.fakes import FakeAudioSource, FakeInjector, FakeTranscriber
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
    assert config_module.KNOWN_ENGINES == ("whistle", "whisper_cpp", "nemo_speech")
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


def test_the_generic_parse_never_probes_the_machine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A pure ADR 0009 parse must touch **no** hardware probe (ticket 45, review T1).

    Every probe entry point reachable from the engine module is instrumented --
    ``HostSystemProbe``, ``inspect_vulkan``, ``default_whisper_model``,
    ``shutil.which`` and ``Path.is_file`` -- so this fails if ``load_config``
    reaches for the machine by *any* of them, not only through the shared rule.
    The guard is proved live before it is trusted, so the test cannot pass for a
    reason other than "``load_config`` probed nothing".
    """
    probes: list[str] = []

    class _RecordingProbe:
        def __init__(self, *args: object, **kwargs: object) -> None:
            probes.append("HostSystemProbe")

        def executable(self, name: str) -> None:
            probes.append("executable")

        def header(self, relative_path: str) -> None:
            probes.append("header")

        def library(self, name: str) -> None:
            probes.append("library")

    def _recording_inspect(system: object) -> whisper_engine.VulkanStatus:
        probes.append("inspect_vulkan")
        return whisper_engine.VulkanStatus(
            runtime=None, headers=None, spirv_headers=None, shader_compiler=None
        )

    def _recording_default(system: object = None) -> str:
        probes.append("default_whisper_model")
        return "small-q8_0"

    def _recording_which(name: str, path: str | None = None) -> None:
        probes.append("shutil.which")

    real_is_file = Path.is_file

    def _recording_is_file(self: Path) -> bool:
        probes.append("Path.is_file")
        return real_is_file(self)

    monkeypatch.setattr(whisper_engine, "HostSystemProbe", _RecordingProbe)
    monkeypatch.setattr(whisper_engine, "inspect_vulkan", _recording_inspect)
    monkeypatch.setattr(whisper_engine, "default_whisper_model", _recording_default)
    monkeypatch.setattr(whisper_engine.shutil, "which", _recording_which)
    monkeypatch.setattr(Path, "is_file", _recording_is_file)

    # The guard is live: the reviewer's mutation -- a *direct* engine probe -- is
    # recorded, so a parse that reached the machine by this path would fail below.
    whisper_engine.inspect_vulkan(whisper_engine.HostSystemProbe())
    assert probes == ["HostSystemProbe", "inspect_vulkan"]
    probes.clear()

    path = tmp_path / "config.toml"
    path.write_text(
        '[transcriber]\nengine = "whisper_cpp"\n\n[engine.whisper_cpp]\nlanguage = "en"\n',
        encoding="utf-8",
    )
    result = load_config(path)

    # The block crosses the boundary verbatim, and a pure parse probed nothing.
    assert result.config.engine_blocks["whisper_cpp"] == {"language": "en"}
    assert probes == []


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


def test_a_directly_constructed_adapter_resolves_the_default_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The adapter's safety net resolves an omitted model once, at load time (ticket 45).

    The composition root normally resolves it, so this pins the fallback for a
    directly-constructed ``WhisperCppTranscriber(WhisperCppConfig())``: without it
    ``model_store.whisper_model_path(None)`` would fail. It is resolved once and
    cached.
    """
    calls: list[str] = []

    def _recording_default(system: object = None) -> str:
        calls.append("default_whisper_model")
        return "tiny-q8_0"

    monkeypatch.setattr(whisper_engine, "default_whisper_model", _recording_default)
    adapter = WhisperCppTranscriber(WhisperCppConfig())
    assert adapter.config.model is None  # the raw config stays unresolved
    assert adapter._model_name() == "tiny-q8_0"  # noqa: SLF001
    assert adapter._model_name() == "tiny-q8_0"  # noqa: SLF001 - cached
    assert calls == ["default_whisper_model"]
    assert adapter._model_path() == model_store.whisper_model_path("tiny-q8_0")  # noqa: SLF001


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


# --- ticket 50: the default follows the engine; only an explicit true errors ---


def _write_toml(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def _loaded_config(tmp_path: Path, engine: str, streaming: str | None) -> Config:
    """Load a config selecting ``engine`` with the ``[streaming]`` body (or none)."""
    text = f'[transcriber]\nengine = "{engine}"\n'
    if streaming is not None:
        text += f"\n[streaming]\n{streaming}\n"
    return load_config(_write_toml(tmp_path, text)).config


def test_batch_only_engines_agree_with_the_transcriber_capability() -> None:
    # The engine-aware default (ticket 50) is driven by config.BATCH_ONLY_ENGINES,
    # while the real source of truth for "can this engine stream" is the adapter's
    # declared supports_streaming. The two must not drift: one guard test over
    # every known engine, constructed directly so no machine is probed.
    #
    # nemo_speech is the one deliberate exception (ticket 58): it is a *streaming*
    # engine (ADR 0022) whose adapter does not stream yet -- ticket 59 flips
    # supports_streaming to True -- so BATCH_ONLY_ENGINES must stay without it
    # (an omitted streaming.enabled defaults to True). Excluding exactly this
    # engine keeps the guard live for every other engine.
    from ges2t.identifiers import NEMO_SPEECH_ENGINE
    from ges2t.transcribers.nemo_speech import NemoSpeechTranscriber
    from ges2t.transcribers.nemo_speech_config import NemoSpeechConfig
    from ges2t.transcribers.whistle import WhistleTranscriber
    from ges2t.transcribers.whistle_config import WhistleConfig

    declared = {
        "whistle": WhistleTranscriber(WhistleConfig()).supports_streaming,
        "whisper_cpp": WhisperCppTranscriber(WhisperCppConfig()).supports_streaming,
        "nemo_speech": NemoSpeechTranscriber(
            NemoSpeechConfig()
        ).supports_streaming,
    }
    assert set(declared) == set(config_module.KNOWN_ENGINES)
    assert config_module.BATCH_ONLY_ENGINES == {
        engine
        for engine, streams in declared.items()
        if not streams and engine != NEMO_SPEECH_ENGINE
    }
    assert NEMO_SPEECH_ENGINE not in config_module.BATCH_ONLY_ENGINES
    assert declared[NEMO_SPEECH_ENGINE] is False


def test_omitted_streaming_with_a_batch_only_engine_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The minimal config the user actually hit: only the engine line. It must
    # start (batch), with the default resolved to false and no streaming refusal.
    config = _loaded_config(tmp_path, "whisper_cpp", None)
    assert config.streaming.enabled is False
    monkeypatch.setattr(pipeline, "_whisper_missing_artifacts", lambda config: [])
    pipeline._validate_whisper_config(config)  # noqa: SLF001  # must not raise


def test_explicit_true_streaming_with_a_batch_only_engine_is_still_refused(
    tmp_path: Path,
) -> None:
    config = _loaded_config(tmp_path, "whisper_cpp", "enabled = true")
    assert config.streaming.enabled is True
    with pytest.raises(PipelineError) as excinfo:
        pipeline._validate_whisper_config(config)  # noqa: SLF001
    message = str(excinfo.value)
    assert 'transcriber.engine = "whisper_cpp"' in message
    assert "streaming.enabled = true" in message
    assert "batch-only" in message


def test_explicit_false_streaming_with_a_batch_only_engine_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _loaded_config(tmp_path, "whisper_cpp", "enabled = false")
    assert config.streaming.enabled is False
    monkeypatch.setattr(pipeline, "_whisper_missing_artifacts", lambda config: [])
    pipeline._validate_whisper_config(config)  # noqa: SLF001  # must not raise


def test_whistle_with_omitted_streaming_still_streams_from_a_file(tmp_path: Path) -> None:
    config = _loaded_config(tmp_path, "whistle", None)
    assert config.streaming.enabled is True
    transcriber = pipeline.build_transcriber(config)
    assert pipeline._select_session_kind(config, transcriber) == "streaming"  # noqa: SLF001


def test_a_batch_only_engine_is_announced_in_one_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Informed, not a silent downgrade: with the default following the engine the
    # user no longer writes a [streaming] line, so the consequence is stated.
    pipeline._announce_batch_only_engine(  # noqa: SLF001
        Config(streaming=StreamingConfig(enabled=False)),
        FakeTranscriber(name="whisper_cpp", supports_streaming=False),
    )
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert "whisper_cpp" in err
    assert "batch-only" in err
    assert "no text until the utterance ends" in err


def test_a_streaming_engine_is_not_announced(capsys: pytest.CaptureFixture[str]) -> None:
    pipeline._announce_batch_only_engine(  # noqa: SLF001
        Config(), FakeTranscriber(supports_streaming=True)
    )
    assert capsys.readouterr().err == ""


def test_startup_announces_a_batch_only_engine(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    transcriber = FakeTranscriber(name="whisper_cpp", supports_streaming=False)
    monkeypatch.setattr(pipeline, "build_transcriber", lambda config: transcriber)
    monkeypatch.setattr(pipeline, "build_audio_source", lambda config: FakeAudioSource())
    monkeypatch.setattr(pipeline, "build_injector", lambda config: FakeInjector())
    monkeypatch.setattr(pipeline, "_whisper_missing_artifacts", lambda config: [])

    config = Config(
        transcriber=TranscriberConfig(engine="whisper_cpp"),
        streaming=StreamingConfig(enabled=False),
    )
    pipeline._startup(config, NullStatusUI())  # noqa: SLF001

    err = capsys.readouterr().err
    assert "batch-only" in err
    assert "no text until the utterance ends" in err


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
