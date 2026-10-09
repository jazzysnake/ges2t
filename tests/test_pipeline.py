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
from ges2t.config import (
    AudioConfig,
    Config,
    InjectionConfig,
    TranscriberConfig,
    UIConfig,
    load_config,
)
from ges2t.injectors.clipboard import ClipboardPasteInjector
from ges2t.injectors.layout import DEFAULT_PASTE_KEYS
from ges2t.injectors.ydotool import XkbKeySequenceSource, YdotoolInjector
from ges2t.interfaces import AudioBuffer, LiveText, Transcriber, TranscriptionResult
from ges2t.notify import NotifyingStatusUI
from ges2t.service import DictationSession, Service
from ges2t.transcribers.whistle import WhistleTranscriber
from ges2t.transcribers.whistle_config import (
    DEFAULT_CHUNK_SECONDS,
    DEFAULT_LANGUAGE,
    DEFAULT_WINDOW_SECONDS,
)
from ges2t.ui.status import NullStatusUI, TrayFailure
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


def test_build_injector_carries_the_configured_key_delay_and_layout() -> None:
    config = Config(injection_blocks={"ydotool": {"key_delay_ms": 5, "layout": "us"}})

    injector = pipeline.build_injector(config)

    assert isinstance(injector, YdotoolInjector)
    assert injector.key_delay_ms == 5
    source = injector._source
    assert isinstance(source, XkbKeySequenceSource)
    assert source._layout == "us"


def test_build_injector_defaults_when_the_ydotool_block_is_absent() -> None:
    # The generic layer never synthesizes an injection block, so its absence is
    # the normal case and must mean all defaults, never a KeyError (ADR 0009).
    injector = pipeline.build_injector(Config())

    assert isinstance(injector, YdotoolInjector)
    # 0 ms is the measured default (ticket 23 `001-comment.md`); do not "tidy".
    assert injector.key_delay_ms == 0


# --- build_injector: backend selection (ticket 25) ----------------------------


def test_build_injector_returns_the_clipboard_backend_when_selected() -> None:
    config = Config(injection=InjectionConfig(backend="clipboard"))

    injector = pipeline.build_injector(config)

    assert isinstance(injector, ClipboardPasteInjector)
    assert injector.paste_keys == list(DEFAULT_PASTE_KEYS)


def test_build_injector_reads_the_clipboard_paste_keys() -> None:
    config = Config(
        injection=InjectionConfig(backend="clipboard"),
        injection_blocks={"clipboard": {"paste_keys": ["ctrl+shift+v"]}},
    )

    injector = pipeline.build_injector(config)

    assert isinstance(injector, ClipboardPasteInjector)
    assert injector.paste_keys == ["ctrl+shift+v"]


def test_build_injector_clipboard_defaults_when_the_block_is_absent() -> None:
    # The generic layer never synthesizes ``[injection.clipboard]``; its absence
    # must mean all defaults, never a KeyError (ADR 0009).
    config = Config(injection=InjectionConfig(backend="clipboard"))

    injector = pipeline.build_injector(config)

    assert isinstance(injector, ClipboardPasteInjector)
    assert injector.paste_keys == ["ctrl+v"]
    assert injector.last_warnings == []


def test_build_injector_clipboard_carries_the_trailing_space_setting() -> None:
    config = Config(
        injection=InjectionConfig(backend="clipboard"), ui=UIConfig(trailing_space=False)
    )

    injector = pipeline.build_injector(config)

    assert isinstance(injector, ClipboardPasteInjector)
    assert injector.trailing_space is False


def test_build_injector_clipboard_surfaces_the_block_warnings() -> None:
    config = Config(
        injection=InjectionConfig(backend="clipboard"),
        injection_blocks={"clipboard": {"past_keys": ["ctrl+v"]}},
    )

    injector = pipeline.build_injector(config)

    assert isinstance(injector, ClipboardPasteInjector)
    assert injector.last_warnings == [
        "injection.clipboard: unknown key 'past_keys'"
    ]


def test_build_injector_rejects_an_unknown_backend_instead_of_defaulting() -> None:
    config = Config(injection=InjectionConfig(backend="clipbord"))

    with pytest.raises(pipeline.PipelineError) as excinfo:
        pipeline.build_injector(config)

    message = str(excinfo.value)
    assert "clipbord" in message
    assert "ydotool" in message
    assert "clipboard" in message


def test_build_injector_keeps_ydotool_for_the_explicit_value() -> None:
    config = Config(injection=InjectionConfig(backend="ydotool"))

    assert isinstance(pipeline.build_injector(config), YdotoolInjector)


def test_build_injector_from_real_toml_selects_the_clipboard_backend(
    tmp_path: Path,
) -> None:
    # End-to-end through the generic loader: a real file, the generic selector,
    # the raw block, and the adapter -- proving the block name matches the
    # backend value (ticket 24's hand-off contract).
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[injection]
backend = "clipboard"

[injection.clipboard]
paste_keys = ["ctrl+shift+v"]

[injection.ydotool]
layout = "hu"
""",
        encoding="utf-8",
    )

    loaded = load_config(config_path)
    injector = pipeline.build_injector(loaded.config)

    assert loaded.warnings == []
    assert isinstance(injector, ClipboardPasteInjector)
    assert injector.paste_keys == ["ctrl+shift+v"]


def test_build_injector_from_real_toml_defaults_to_ydotool(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[injection]\nbackend = \"ydotool\"\n", encoding="utf-8")

    loaded = load_config(config_path)

    assert isinstance(pipeline.build_injector(loaded.config), YdotoolInjector)


def test_build_injector_unknown_backend_from_real_toml_raises(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[injection]\nbackend = \"magic\"\n", encoding="utf-8"
    )

    loaded = load_config(config_path)

    with pytest.raises(pipeline.PipelineError):
        pipeline.build_injector(loaded.config)


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

    partials: Iterator[LiveText] = session.partials()

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
    # The injected UI still receives every call -- through the notification
    # wrapper that ``ui.notifications = true`` (the default) now inserts.
    assert isinstance(seen[0]._status_ui, NotifyingStatusUI)
    assert seen[0]._status_ui.wrapped is status_ui


class _DiagnosticStatusUI:
    """A ``StatusUI`` that records a failure and reports it at shutdown.

    Structurally satisfies ``StatusUI``, ``StoppableStatusUI`` and
    ``DiagnosticStatusUI``, so ``run_service`` stops it and prints the report.
    """

    def __init__(self) -> None:
        self.stop_calls = 0

    def idle(self) -> None:
        """No-op."""

    def recording(self) -> None:
        """No-op."""

    def transcribing(self) -> None:
        """No-op."""

    def error(self, message: str) -> None:
        """No-op."""

    def partial(self, live: LiveText) -> None:
        """No-op."""

    def stop(self) -> None:
        """Record that the composition root stopped this UI."""
        self.stop_calls += 1

    def failure_report(self) -> TrayFailure | None:
        """Always report one recorded tray failure."""
        return TrayFailure(count=3, last_message="state update: no display available")


_EXPECTED_SUMMARY = (
    "the tray has failed 3 times: state update: no display available"
)


def test_run_service_reports_the_status_ui_failure_on_the_normal_path(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Review 002 / R3: deleting the shutdown call left the whole suite passing,
    # so this test guards the normal exit path's failure summary (N1).
    ui = _DiagnosticStatusUI()
    monkeypatch.setattr(pipeline, "_missing_artifacts", lambda config: [])
    monkeypatch.setattr(
        pipeline, "build_transcriber", lambda config: FakeTranscriber()
    )
    monkeypatch.setattr(Service, "serve", lambda self: None)
    _forbid_downloads(monkeypatch)

    result = pipeline.run_service(Config(), status_ui=ui)

    assert result == 0
    assert ui.stop_calls == 1
    assert _EXPECTED_SUMMARY in capsys.readouterr().err


def test_run_service_reports_the_status_ui_failure_on_a_serve_error_path(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The ``finally`` around ``serve`` must cover the failure paths too.
    ui = _DiagnosticStatusUI()

    def fail_to_serve(self: Service) -> None:
        raise OSError("cannot bind the socket")

    monkeypatch.setattr(pipeline, "_missing_artifacts", lambda config: [])
    monkeypatch.setattr(
        pipeline, "build_transcriber", lambda config: FakeTranscriber()
    )
    monkeypatch.setattr(Service, "serve", fail_to_serve)
    _forbid_downloads(monkeypatch)

    result = pipeline.run_service(Config(), status_ui=ui)

    assert result != 0
    assert ui.stop_calls == 1
    assert _EXPECTED_SUMMARY in capsys.readouterr().err


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
