"""The batch-vs-streaming selection rule and the wired end-to-end path (ADR 0011).

Ticket 11 left session selection out of the composition root on purpose so this
ticket owns it; this module is the coverage for that decision and for the path it
turns on. Everything here runs with the shared fakes -- no microphone, no engine,
no ``ydotool``, no subprocess that types, and no network (ADR 0014).

The single rule under test is derived, not configured: streaming is used when
``streaming.enabled`` **and** ``Transcriber.supports_streaming`` are both true,
and the batch session otherwise (ADR 0010, ADR 0011). The commit window stays the
adapter's property and the feed granularity is the session's, so they are
asserted against their separate owners.

Every ``finish()`` driven directly is wrapped in a bounded call so a regression
fails loudly instead of hanging the suite (the failure mode ticket 15's review
found).
"""

from __future__ import annotations

import inspect
import logging
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from typing import TypeVar

import pytest

from ges2t import pipeline
from ges2t.config import Config, InjectionConfig, StreamingConfig, UIConfig
from ges2t.injectors.keymap import KeyEvent, KeySequence
from ges2t.injectors.ydotool import YdotoolInjector
from ges2t.interfaces import (
    AudioBuffer,
    LiveText,
    TranscriptionResult,
    TranscriptionSegment,
)
from ges2t.pipeline import BatchDictationSession
from ges2t.service import Service, ServiceState
from ges2t.streaming_session import StreamingDictationSession
from ges2t.transcribers.whistle import WhistleTranscriber
from ges2t.transcribers.whistle_config import (
    DEFAULT_CHUNK_SECONDS,
    DEFAULT_WINDOW_SECONDS,
)
from ges2t.ui.status import NullStatusUI
from tests.fakes import FakeAudioSource, FakeInjector, FakeStatusUI, FakeTranscriber

SAMPLE_RATE = 16000

_T = TypeVar("_T")


# -- helpers ----------------------------------------------------------------


def _buffer(seconds: float = 0.5) -> AudioBuffer:
    """One mono buffer of ``seconds`` at 16 kHz (the samples are irrelevant)."""
    return AudioBuffer(
        samples=[0.0] * round(seconds * SAMPLE_RATE), sample_rate=SAMPLE_RATE
    )


def _partial(text: str) -> TranscriptionSegment:
    return TranscriptionSegment(
        live=LiveText(committed=text, pending=""),
        language="en",
        is_final=False,
        pass_ms=None,
    )


def _live(committed: str, pending: str) -> TranscriptionSegment:
    """A non-final segment with the two lifetimes carried separately."""
    return TranscriptionSegment(
        live=LiveText(committed=committed, pending=pending),
        language="en",
        is_final=False,
        pass_ms=None,
    )


def _final(text: str) -> TranscriptionSegment:
    return TranscriptionSegment(
        live=LiveText(committed=text, pending=""),
        language="en",
        is_final=True,
        pass_ms=None,
    )


def _result(text: str) -> TranscriptionResult:
    return TranscriptionResult(text=text, language="en", ttft_ms=None, decode_tps=None)


def _wait_for(
    predicate: Callable[[], bool],
    *,
    timeout: float = 2.0,
    description: str = "condition",
) -> None:
    """Poll ``predicate`` until it is true, failing instead of hanging."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.001)
    raise AssertionError(f"{description} was not met within {timeout:.1f}s")


def _call_with_timeout(fn: Callable[[], _T], *, timeout: float = 2.0) -> _T:
    """Run ``fn`` on a daemon thread and fail if it has not returned in time.

    The streaming session's ``finish()`` joins its worker, which is unbounded on
    purpose (ticket 15). A regression that wedged it must surface as an
    assertion, not a hung suite.
    """
    results: list[_T] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            results.append(fn())
        except BaseException as exc:  # re-raised on the main thread
            errors.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    if thread.is_alive():
        raise AssertionError(f"call did not return within {timeout:.1f}s")
    if errors:
        raise errors[0]
    return results[0]


# -- the selection rule --------------------------------------------------------


def test_streaming_is_selected_when_enabled_and_the_engine_supports_it() -> None:
    config = Config()
    transcriber = FakeTranscriber(supports_streaming=True)

    session = pipeline._session_factory(config, transcriber, FakeAudioSource())()

    assert isinstance(session, StreamingDictationSession)


def test_batch_is_selected_when_streaming_is_enabled_but_the_engine_cannot() -> None:
    # The capability flag, not an exception or a probe (ADR 0010): a backend
    # that declares supports_streaming = False gets the batch path.
    config = Config()
    transcriber = FakeTranscriber(supports_streaming=False)

    session = pipeline._session_factory(config, transcriber, FakeAudioSource())()

    assert isinstance(session, BatchDictationSession)


def test_batch_is_selected_when_the_switch_is_off_even_for_a_capable_engine() -> None:
    config = Config(streaming=StreamingConfig(enabled=False))
    transcriber = FakeTranscriber(supports_streaming=True)

    session = pipeline._session_factory(config, transcriber, FakeAudioSource())()

    assert isinstance(session, BatchDictationSession)


def test_the_public_run_service_signature_is_unchanged() -> None:
    # Ticket 11's reviewer required the config/streaming settings to be threaded
    # through the private factory, leaving run_service's public shape alone.
    parameters = inspect.signature(pipeline.run_service).parameters

    assert list(parameters) == ["config", "status_ui"]
    assert parameters["status_ui"].default is None


def test_session_selection_is_logged_at_debug_level(
    caplog: pytest.LogCaptureFixture,
) -> None:
    transcriber = FakeTranscriber(name="fake", supports_streaming=True)

    with caplog.at_level(logging.DEBUG, logger="ges2t.pipeline"):
        pipeline._session_factory(Config(), transcriber, FakeAudioSource())

    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "using the streaming dictation session" in message
        and "supports streaming" in message
        for message in messages
    )


def test_batch_session_selection_logs_the_reason(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG, logger="ges2t.pipeline"):
        pipeline._session_factory(
            Config(streaming=StreamingConfig(enabled=False)),
            FakeTranscriber(),
            FakeAudioSource(),
        )

    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "using the batch dictation session" in message
        and "streaming.enabled = false" in message
        for message in messages
    )


# -- chunk and window reach their separate owners ------------------------------


def test_window_stays_with_the_adapter_and_chunk_reaches_the_session() -> None:
    # The commit window is the adapter's property (it drives
    # ``streaming.drive``); the feed granularity is the session's. Both come from
    # ``[engine.whistle]`` but are asserted against different owners (ADR 0011).
    config = Config(
        engine_blocks={
            "whistle": {"window_seconds": 3.0, "chunk_seconds": 0.5}
        }
    )

    adapter = pipeline.build_transcriber(config)
    assert isinstance(adapter, WhistleTranscriber)
    assert adapter.config.window_seconds == 3.0
    assert adapter.config.chunk_seconds == 0.5

    audio = FakeAudioSource(buffers=[_buffer()])
    transcriber = FakeTranscriber(supports_streaming=True, segments=[_final("done")])
    session = pipeline._session_factory(config, transcriber, audio)()
    assert isinstance(session, StreamingDictationSession)
    assert session._chunk_seconds == 0.5

    session.start()
    assert _call_with_timeout(session.finish) == "done"
    assert audio.chunk_seconds_seen == [0.5]


def test_defaults_come_from_whistle_config_when_the_block_is_absent() -> None:
    config = Config()

    adapter = pipeline.build_transcriber(config)
    assert isinstance(adapter, WhistleTranscriber)
    assert adapter.config.window_seconds == DEFAULT_WINDOW_SECONDS

    session = pipeline._session_factory(
        config, FakeTranscriber(), FakeAudioSource()
    )()
    assert isinstance(session, StreamingDictationSession)
    assert session._chunk_seconds == DEFAULT_CHUNK_SECONDS


# -- the wired path: committed text is typed, pending is display-only --------


def test_wired_streaming_path_types_committed_text_and_never_the_pending_tail() -> None:
    # Ticket 34: the committed prefix is typed as it is committed; the pending
    # tail (where the engine's hallucinations live) is display-only. The batch
    # injector is never used on the streaming path.
    config = Config()
    transcriber = FakeTranscriber(
        segments=[
            _live(committed="published ", pending="POISON display only"),
            _final("published transcript"),
        ]
    )
    audio = FakeAudioSource(buffers=[_buffer()])
    injector = FakeInjector()
    status_ui = FakeStatusUI()
    service = pipeline._build_service(config, transcriber, audio, injector, status_ui)
    try:
        assert service.handle("toggle").state is ServiceState.RECORDING
        _wait_for(
            lambda: bool(status_ui.partials), description="a forwarded partial"
        )

        assert service.handle("toggle").state is ServiceState.TRANSCRIBING
        _wait_for(
            lambda: service.state is ServiceState.IDLE,
            description="idle after injection",
        )

        assert "stream" in transcriber.calls
        assert "transcribe" not in transcriber.calls
        assert injector.injected == []
        assert "".join(injector.typed) == "published transcript "
        assert all("POISON" not in text for text in injector.typed)
        assert "published POISON display only" in [
            live.display for live in status_ui.partials
        ]
    finally:
        service.shutdown()


def test_wired_streaming_path_recovers_a_collapsed_advance_from_finish() -> None:
    # partials() dedups on display, so the pass that advanced committed from
    # "abc" to "abcdef" while pending shrank is dropped; the final segment
    # carries it and finish() must type it (ticket 33's review, section 3).
    config = Config()
    transcriber = FakeTranscriber(
        segments=[_live(committed="abc", pending="def"), _final("abcdef")]
    )
    audio = FakeAudioSource(buffers=[_buffer()])
    injector = FakeInjector()
    status_ui = FakeStatusUI()
    service = pipeline._build_service(config, transcriber, audio, injector, status_ui)
    try:
        assert service.handle("toggle").state is ServiceState.RECORDING
        _wait_for(
            lambda: bool(status_ui.partials), description="a forwarded partial"
        )
        assert service.handle("toggle").state is ServiceState.TRANSCRIBING
        _wait_for(
            lambda: service.state is ServiceState.IDLE,
            description="idle after injection",
        )

        assert "".join(injector.typed) == "abcdef "
        assert "".join(injector.typed) != "abcabcdef "
    finally:
        service.shutdown()


def test_wired_streaming_path_types_the_byte_identical_document() -> None:
    # The load-bearing criterion: the incrementally typed document is exactly
    # what the single end-of-utterance injection produces, byte for byte,
    # including one trailing space (ADR 0012).
    final_text = "Good morning. Last night storm"
    config = Config()
    transcriber = FakeTranscriber(
        segments=[
            _live("Good morning.", " Last"),
            _live("Good morning. Last", " night storm"),
            _final(final_text),
        ]
    )
    audio = FakeAudioSource(buffers=[_buffer()])
    injector = FakeInjector()
    status_ui = FakeStatusUI()
    service = pipeline._build_service(config, transcriber, audio, injector, status_ui)
    try:
        assert service.handle("toggle").state is ServiceState.RECORDING
        _wait_for(
            lambda: bool(status_ui.partials), description="a forwarded partial"
        )
        assert service.handle("toggle").state is ServiceState.TRANSCRIBING
        _wait_for(
            lambda: service.state is ServiceState.IDLE,
            description="idle after injection",
        )

        assert "".join(injector.typed) == final_text + " "
    finally:
        service.shutdown()


def test_wired_streaming_path_types_nothing_for_a_whitespace_final_text() -> None:
    # Silence in, nothing out (ADR 0012); the streaming path must not resurrect
    # partial text for an empty final segment.
    config = Config()
    transcriber = FakeTranscriber(
        segments=[_live(committed="", pending="POISON display only"), _final("   ")]
    )
    audio = FakeAudioSource(buffers=[_buffer()])
    injector = FakeInjector()
    status_ui = FakeStatusUI()
    service = pipeline._build_service(config, transcriber, audio, injector, status_ui)
    try:
        assert service.handle("toggle").state is ServiceState.RECORDING
        _wait_for(
            lambda: bool(status_ui.partials), description="a forwarded partial"
        )
        assert service.handle("toggle").state is ServiceState.TRANSCRIBING
        _wait_for(
            lambda: service.state is ServiceState.IDLE,
            description="idle after the empty transcription",
        )

        assert injector.calls == []
        assert injector.injected == []
        assert injector.typed == []
    finally:
        service.shutdown()


def test_each_streaming_recording_gets_a_fresh_watermark() -> None:
    # The typer carries a per-utterance watermark; a service that reused one
    # instance would type the first recording and then silently type nothing.
    config = Config()
    transcriber = FakeTranscriber(segments=[_live("first", ""), _final("first")])
    audio = FakeAudioSource(buffers=[_buffer()])
    injector = FakeInjector()
    status_ui = FakeStatusUI()
    service = pipeline._build_service(config, transcriber, audio, injector, status_ui)
    try:
        for _ in range(2):
            assert service.handle("toggle").state is ServiceState.RECORDING
            _wait_for(
                lambda: bool(status_ui.partials), description="a forwarded partial"
            )
            assert service.handle("toggle").state is ServiceState.TRANSCRIBING
            _wait_for(
                lambda: service.state is ServiceState.IDLE,
                description="idle after injection",
            )

        assert "".join(injector.typed) == "first first "
    finally:
        service.shutdown()


def test_a_streaming_typing_failure_moves_the_service_to_error() -> None:
    # A failing injector mid-utterance must be visible: CommittedTyper latches it
    # and finish() re-raises it on the service's error path.
    config = Config()
    transcriber = FakeTranscriber(segments=[_live("abc", ""), _final("abcdef")])
    audio = FakeAudioSource(buffers=[_buffer()])
    injector = FakeInjector(error=RuntimeError("ydotoold is not reachable"))
    status_ui = FakeStatusUI()
    service = pipeline._build_service(config, transcriber, audio, injector, status_ui)
    try:
        assert service.handle("toggle").state is ServiceState.RECORDING
        _wait_for(
            lambda: bool(status_ui.partials), description="a forwarded partial"
        )
        assert service.handle("toggle").state is ServiceState.TRANSCRIBING
        _wait_for(
            lambda: service.state is ServiceState.ERROR,
            description="error after a failed injection",
        )

        assert status_ui.errors
        assert "injection failed" in status_ui.errors[-1]
    finally:
        service.shutdown()


# -- config validation: clipboard is incompatible with streaming (ticket 34) --


def test_clipboard_with_streaming_is_a_loud_actionable_error() -> None:
    config = Config(injection=InjectionConfig(backend="clipboard"))

    with pytest.raises(pipeline.PipelineError) as excinfo:
        pipeline._validate_injection_backend(config)

    message = str(excinfo.value)
    assert "injection.backend" in message
    assert "clipboard" in message
    assert "streaming.enabled" in message
    hint = excinfo.value.hint or ""
    assert "ydotool" in hint
    assert "streaming.enabled = false" in hint


def test_clipboard_with_streaming_disabled_is_not_refused() -> None:
    config = Config(
        injection=InjectionConfig(backend="clipboard"),
        streaming=StreamingConfig(enabled=False),
    )

    pipeline._validate_injection_backend(config)  # does not raise


def test_build_service_structurally_refuses_streaming_with_a_non_typing_injector() -> None:
    # The backstop for a direct ``_build_service`` call: a streaming session
    # needs a TextTyper, and the clipboard injector deliberately is not one.
    config = Config(injection=InjectionConfig(backend="clipboard"))

    with pytest.raises(pipeline.PipelineError) as excinfo:
        pipeline._build_service(
            config,
            FakeTranscriber(supports_streaming=True),
            FakeAudioSource(),
            pipeline.build_injector(config),
            FakeStatusUI(),
        )

    assert "streaming.enabled" in str(excinfo.value)


def test_clipboard_with_streaming_disabled_keeps_the_batch_injection() -> None:
    config = Config(
        injection=InjectionConfig(backend="clipboard"),
        streaming=StreamingConfig(enabled=False),
    )
    transcriber = FakeTranscriber(
        supports_streaming=True, result=_result("hello world")
    )
    audio = FakeAudioSource(buffers=[_buffer()])
    injector = FakeInjector()
    status_ui = FakeStatusUI()
    service = pipeline._build_service(config, transcriber, audio, injector, status_ui)
    try:
        assert service.handle("toggle").state is ServiceState.RECORDING
        assert service.handle("toggle").state is ServiceState.TRANSCRIBING
        _wait_for(
            lambda: service.state is ServiceState.IDLE,
            description="idle after injection",
        )

        assert injector.injected == ["hello world"]
        assert injector.typed == []
    finally:
        service.shutdown()


class _RecordingKeySequenceSource:
    """A :class:`~ges2t.injectors.ydotool.KeySequenceSource` that records the payload.

    Returning a canned non-empty sequence lets the real
    :class:`~ges2t.injectors.ydotool.YdotoolInjector` apply
    :func:`~ges2t.injectors.ydotool.decide_injection` without ``libxkbcommon`` or
    a live layout.
    """

    def __init__(self) -> None:
        self.texts: list[str] = []

    def sequence_for(self, text: str) -> KeySequence:
        self.texts.append(text)
        return KeySequence(
            events=(
                KeyEvent(keycode=30, pressed=True),
                KeyEvent(keycode=30, pressed=False),
            ),
            unreachable=(),
        )


class _NullRunner:
    """A :data:`~ges2t.injectors.ydotool.CommandRunner` that emits nothing."""

    def __call__(
        self, argv: Sequence[str], stdin_bytes: bytes, env: Mapping[str, str]
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            args=list(argv), returncode=0, stdout=b"", stderr=b""
        )


def _wired_real_injector(
    *, trailing_space: bool
) -> tuple[YdotoolInjector, _RecordingKeySequenceSource]:
    """Build the real injector through the composition root with a mapping double.

    :func:`ges2t.pipeline.build_injector` threads ``ui.trailing_space`` from the
    config and constructs an ``XkbKeySequenceSource`` that does no work until
    ``sequence_for`` is called (ticket 23), so swapping the source for a recorder
    keeps the test headless while still exercising the real ``decide_injection``
    at the end of the wired path.
    """
    config = Config(ui=UIConfig(trailing_space=trailing_space))
    injector = pipeline.build_injector(config)
    assert isinstance(injector, YdotoolInjector)
    source = _RecordingKeySequenceSource()
    injector._source = source
    injector._runner = _NullRunner()
    return injector, source


@pytest.mark.parametrize(
    ("trailing_space", "expected"),
    [(True, ["hello world "]), (False, ["hello world"])],
)
def test_wired_streaming_path_applies_the_trailing_space_rule(
    trailing_space: bool, expected: list[str]
) -> None:
    # The real injector at the end of the wired streaming path sees exactly the
    # final text plus (when enabled) one ASCII space -- never a partial, never
    # two spaces (ADR 0012).
    config = Config(ui=UIConfig(trailing_space=trailing_space))
    transcriber = FakeTranscriber(
        segments=[_final("hello world")], supports_streaming=True
    )
    audio = FakeAudioSource(buffers=[_buffer()])
    injector, source = _wired_real_injector(trailing_space=trailing_space)
    status_ui = FakeStatusUI()
    service = pipeline._build_service(config, transcriber, audio, injector, status_ui)
    assert isinstance(service, Service)
    try:
        assert service.handle("toggle").state is ServiceState.RECORDING
        assert service.handle("toggle").state is ServiceState.TRANSCRIBING
        _wait_for(
            lambda: service.state is ServiceState.IDLE,
            description="idle after injection",
        )
    finally:
        service.shutdown()

    assert source.texts == expected


def test_run_service_selects_streaming_at_startup(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The selection is reached through run_service, not only through the private
    # factory: startup builds the service from the configured capability.
    monkeypatch.setattr(pipeline, "_missing_artifacts", lambda config: [])
    monkeypatch.setattr(
        pipeline,
        "build_transcriber",
        lambda config: FakeTranscriber(supports_streaming=True, name="whistle"),
    )
    monkeypatch.setattr(pipeline, "build_audio_source", lambda config: FakeAudioSource())
    monkeypatch.setattr(pipeline, "build_injector", lambda config: FakeInjector())
    monkeypatch.setattr(pipeline, "build_status_ui", lambda config: NullStatusUI())
    monkeypatch.setattr(FakeTranscriber, "load", lambda self: None)
    monkeypatch.setattr(Service, "serve", lambda self: None)

    with caplog.at_level(logging.DEBUG, logger="ges2t.pipeline"):
        assert pipeline.run_service(Config()) == 0

    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "using the streaming dictation session" in message and "whistle" in message
        for message in messages
    )


def test_wired_batch_path_produces_and_injects_the_core_epic_text() -> None:
    # With the switch off, the wired path is the core epic's batch behaviour:
    # one ``transcribe()``, no partials, the text injected unchanged (ADR 0012).
    config = Config(streaming=StreamingConfig(enabled=False))
    transcriber = FakeTranscriber(
        supports_streaming=True, result=_result("hello world")
    )
    audio = FakeAudioSource(buffers=[_buffer()])
    injector = FakeInjector()
    status_ui = FakeStatusUI()
    service = pipeline._build_service(config, transcriber, audio, injector, status_ui)
    try:
        assert service.handle("toggle").state is ServiceState.RECORDING
        assert service.handle("toggle").state is ServiceState.TRANSCRIBING
        _wait_for(
            lambda: service.state is ServiceState.IDLE,
            description="idle after injection",
        )

        assert transcriber.calls == ["transcribe"]
        assert status_ui.partials == []
        assert injector.injected == ["hello world"]
    finally:
        service.shutdown()
