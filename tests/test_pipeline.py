"""Tests for the composition root :mod:`ges2t.pipeline` (ADR 0010, ADR 0014, ADR 0019).

Everything here runs with **fakes only**: no microphone, no ``pw-record``, no
engine, no subprocess, and no network. The concrete builders are exercised with
fakes at the interface seam, and the one rule that would be catastrophic to
break — that ``run_service`` never downloads — is asserted with a monkeypatched
``model_store.ensure_model`` that raises if it is ever called (ADR 0018).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from ges2t import model_store, pipeline
from ges2t.audio.pw_record import PwRecordAudioSource
from ges2t.config import AudioConfig, Config, TranscriberConfig, UIConfig
from ges2t.injectors.ydotool import YdotoolInjector
from ges2t.interfaces import AudioBuffer, Transcriber, TranscriptionResult
from ges2t.service import DictationSession, Service
from ges2t.transcribers.whistle import WhistleTranscriber
from ges2t.transcribers.whistle_config import (
    DEFAULT_CHUNK_SECONDS,
    DEFAULT_LANGUAGE,
    DEFAULT_WINDOW_SECONDS,
)
from ges2t.ui.status import NullStatusUI
from tests.fakes import FakeAudioSource, FakeStatusUI, FakeTranscriber

# --- build_transcriber --------------------------------------------------------


def test_build_transcriber_returns_whistle_for_the_default_engine() -> None:
    transcriber = pipeline.build_transcriber(Config())

    assert isinstance(transcriber, WhistleTranscriber)
    assert isinstance(transcriber, Transcriber)
    assert transcriber.name == "whistle"


def test_build_transcriber_raises_for_an_unknown_engine() -> None:
    config = Config(transcriber=TranscriberConfig(engine="whisper_cpp"))

    with pytest.raises(pipeline.PipelineError) as excinfo:
        pipeline.build_transcriber(config)

    message = str(excinfo.value)
    assert "whisper_cpp" in message
    assert "whistle" in message


def test_build_transcriber_accepts_a_missing_engine_block_as_all_defaults() -> None:
    # The generic layer never synthesises ``[engine.whistle]``, so an absent
    # block is the normal case. ``.get(..., {})`` must not be ``[...]`` (ticket 4).
    transcriber = pipeline.build_transcriber(Config())

    assert isinstance(transcriber, WhistleTranscriber)
    assert transcriber.config.language == DEFAULT_LANGUAGE
    assert transcriber.config.window_seconds == DEFAULT_WINDOW_SECONDS
    assert transcriber.config.chunk_seconds == DEFAULT_CHUNK_SECONDS


def test_build_transcriber_passes_the_engine_block_through() -> None:
    config = Config(
        engine_blocks={
            "whistle": {
                "language": "de",
                "window_seconds": 3.0,
                "chunk_seconds": 0.5,
            }
        }
    )

    transcriber = pipeline.build_transcriber(config)

    assert isinstance(transcriber, WhistleTranscriber)
    assert transcriber.config.language == "de"
    assert transcriber.config.window_seconds == 3.0
    assert transcriber.config.chunk_seconds == 0.5


def test_build_transcriber_uses_the_managed_weights_without_an_override() -> None:
    transcriber = pipeline.build_transcriber(Config())

    assert isinstance(transcriber, WhistleTranscriber)
    assert transcriber._weights_path() == model_store.model_path()


def test_build_transcriber_honours_a_non_empty_model_path_override() -> None:
    config = Config(
        transcriber=TranscriberConfig(model_path="/tmp/custom/whistle.cact")
    )

    transcriber = pipeline.build_transcriber(config)

    assert isinstance(transcriber, WhistleTranscriber)
    assert transcriber._weights_path() == Path("/tmp/custom/whistle.cact")


# --- build_audio_source / build_injector --------------------------------------


def test_build_audio_source_passes_the_configured_device() -> None:
    config = Config(audio=AudioConfig(device="my-mic"))

    source = pipeline.build_audio_source(config)

    assert isinstance(source, PwRecordAudioSource)
    assert "my-mic" in source._build_argv()


def test_build_audio_source_omits_the_target_by_default() -> None:
    source = pipeline.build_audio_source(Config())

    assert isinstance(source, PwRecordAudioSource)
    assert "--target" not in source._build_argv()


def test_build_injector_carries_the_trailing_space_setting() -> None:
    config = Config(ui=UIConfig(trailing_space=False))

    injector = pipeline.build_injector(config)

    assert isinstance(injector, YdotoolInjector)
    assert injector.trailing_space is False


# --- BatchDictationSession ----------------------------------------------------


def _result(text: str) -> TranscriptionResult:
    return TranscriptionResult(
        text=text, language="en", ttft_ms=None, decode_tps=None
    )


def _session(
    *,
    buffers: list[AudioBuffer] | None = None,
    result_text: str = "hello world",
) -> tuple[pipeline.BatchDictationSession, FakeAudioSource, FakeTranscriber]:
    audio = FakeAudioSource(buffers=buffers if buffers is not None else [])
    transcriber = FakeTranscriber(result=_result(result_text))
    return pipeline.BatchDictationSession(transcriber, audio), audio, transcriber


def test_batch_session_is_a_dictation_session() -> None:
    session, _, _ = _session()

    assert isinstance(session, pipeline.BatchDictationSession)
    assert isinstance(session, DictationSession)


def test_batch_session_start_starts_capture() -> None:
    session, audio, _ = _session()

    session.start()

    assert audio.calls == ["start"]


def test_batch_session_partials_yields_nothing() -> None:
    session, _, _ = _session()

    partials: Iterator[str] = session.partials()

    assert list(partials) == []


def test_finish_with_zero_samples_returns_empty_and_does_not_transcribe() -> None:
    session, audio, transcriber = _session(buffers=[])

    text = session.finish()

    assert text == ""
    assert audio.calls == ["stop"]
    assert "transcribe" not in transcriber.calls


def test_finish_returns_the_transcriber_text_unchanged() -> None:
    buffer = AudioBuffer(samples=[0.1, 0.2, 0.3], sample_rate=16000)
    session, audio, transcriber = _session(
        buffers=[buffer], result_text="  Verbatim Text!  "
    )

    text = session.finish()

    assert text == "  Verbatim Text!  "
    assert audio.calls == ["stop"]
    assert transcriber.calls == ["transcribe"]
    assert len(transcriber.transcribed) == 1


def test_cancel_stops_capture_and_does_not_transcribe() -> None:
    buffer = AudioBuffer(samples=[0.1, 0.2, 0.3], sample_rate=16000)
    session, audio, transcriber = _session(buffers=[buffer])

    session.cancel()

    assert audio.calls == ["stop"]
    assert "transcribe" not in transcriber.calls


# --- startup: presence check, no network, one load ----------------------------


def _forbid_downloads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every acquisition entry point raise if ``run_service`` reaches it."""

    def forbidden(*args: object, **kwargs: object) -> Path:
        raise AssertionError("run_service must never download (ADR 0018)")

    monkeypatch.setattr(model_store, "ensure_model", forbidden)
    monkeypatch.setattr(model_store, "ensure_engine", forbidden)


def test_run_service_never_downloads_and_loads_once_before_serving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    fake = FakeTranscriber()
    fake.raise_on_ensure_model = AssertionError("the transcriber must not ensure")
    monkeypatch.setattr(pipeline, "build_transcriber", lambda config: fake)
    monkeypatch.setattr(pipeline, "_missing_artifacts", lambda config: [])
    # The tray needs a display; this test injects the no-op instead.
    monkeypatch.setattr(pipeline, "build_status_ui", lambda config: NullStatusUI())
    monkeypatch.setattr(FakeTranscriber, "load", lambda self: events.append("load"))
    monkeypatch.setattr(Service, "serve", lambda self: events.append("serve"))
    _forbid_downloads(monkeypatch)

    result = pipeline.run_service(Config())

    assert result == 0
    assert events == ["load", "serve"]


def test_run_service_injects_the_status_ui_into_the_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[Service] = []
    status_ui = FakeStatusUI()
    monkeypatch.setattr(pipeline, "_missing_artifacts", lambda config: [])
    monkeypatch.setattr(
        pipeline, "build_transcriber", lambda config: FakeTranscriber()
    )
    monkeypatch.setattr(Service, "serve", lambda self: seen.append(self))

    result = pipeline.run_service(Config(), status_ui=status_ui)

    assert result == 0
    assert len(seen) == 1
    assert seen[0]._status_ui is status_ui


def test_run_service_reports_missing_artifacts_and_returns_nonzero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing_weights = tmp_path / "missing.cact"
    missing_engine = tmp_path / "missing.so"
    config = Config(transcriber=TranscriberConfig(model_path=str(missing_weights)))
    monkeypatch.setattr(
        model_store, "engine_path", lambda version=model_store.ENGINE_VERSION: missing_engine
    )
    # Startup fails before serving; the no-op avoids needing a display for the tray.
    monkeypatch.setattr(pipeline, "build_status_ui", lambda config: NullStatusUI())
    loaded: list[str] = []
    served: list[str] = []
    monkeypatch.setattr(WhistleTranscriber, "load", lambda self: loaded.append("load"))
    monkeypatch.setattr(Service, "serve", lambda self: served.append("serve"))
    _forbid_downloads(monkeypatch)

    result = pipeline.run_service(config)

    assert result != 0
    assert loaded == []
    assert served == []
    stderr = capsys.readouterr().err
    assert str(missing_weights) in stderr
    assert str(missing_engine) in stderr
    assert "ges2t setup" in stderr


def _fake_engine_pin(size: int) -> model_store.ArtifactPin:
    return model_store.ArtifactPin(
        name="libneedle.so", url="", sha256="", size=size
    )


def test_missing_artifacts_detects_a_wrong_sized_managed_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    wrong_size = tmp_path / "whistle.cact"
    wrong_size.write_bytes(b"too small")
    engine = tmp_path / "libneedle.so"
    engine.write_bytes(b"")
    monkeypatch.setattr(
        model_store, "model_path", lambda version=model_store.MODEL_VERSION: wrong_size
    )
    monkeypatch.setattr(
        model_store, "engine_path", lambda version=model_store.ENGINE_VERSION: engine
    )
    monkeypatch.setattr(model_store, "engine_pin", lambda: _fake_engine_pin(0))

    problems = pipeline._missing_artifacts(Config())

    assert len(problems) == 1
    assert str(wrong_size) in problems[0]
    assert "Whistle weights" in problems[0]


def test_missing_artifacts_accepts_a_present_override_and_engine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    override = tmp_path / "custom.cact"
    override.write_bytes(b"caller-supplied weights, any size")
    engine = tmp_path / "libneedle.so"
    engine.write_bytes(b"")
    monkeypatch.setattr(
        model_store, "engine_path", lambda version=model_store.ENGINE_VERSION: engine
    )
    monkeypatch.setattr(model_store, "engine_pin", lambda: _fake_engine_pin(0))
    config = Config(transcriber=TranscriberConfig(model_path=str(override)))

    problems = pipeline._missing_artifacts(config)

    assert problems == []


def test_missing_artifacts_reports_an_unsupported_platform(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    override = tmp_path / "custom.cact"
    override.write_bytes(b"weights")
    config = Config(transcriber=TranscriberConfig(model_path=str(override)))

    def unsupported() -> model_store.ArtifactPin:
        raise model_store.UnsupportedPlatformError("no engine pinned for this platform")

    monkeypatch.setattr(model_store, "engine_pin", unsupported)

    problems = pipeline._missing_artifacts(config)

    assert len(problems) == 1
    assert "no engine pinned" in problems[0]


def test_null_status_ui_error_writes_to_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    NullStatusUI().error("the engine rejected the audio")

    assert "the engine rejected the audio" in capsys.readouterr().err
