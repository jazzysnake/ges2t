"""Tests for the streaming :class:`~ges2t.service.DictationSession` (ADR 0011).

The whole surface is the shared fakes: :class:`~tests.fakes.FakeAudioSource` and
a streaming-capable :class:`~tests.fakes.FakeTranscriber` configured with a
sequence of segments. The session is typed against the
:class:`~ges2t.interfaces.Transcriber` Protocol, so no decoder double (the
window engine's fake) is needed here — windowing is the adapter's job
(ticket 14), not the session's.

Both the "advancing partials" tests and the service integration test rely on the
session retaining every distinct partial until it is consumed, which is what
makes them deterministic rather than timing-dependent. There is no engine, no
microphone, no subprocess, and no sleeping beyond a bounded wait-for helper.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import TypeVar

import pytest

from ges2t.interfaces import AudioBuffer, TranscriptionSegment
from ges2t.service import DictationSession, Service, ServiceState
from ges2t.streaming_session import (
    StreamingDictationSession,
    StreamingSessionError,
)
from tests.fakes import FakeAudioSource, FakeInjector, FakeStatusUI, FakeTranscriber

SAMPLE_RATE = 16000
CHUNK_SECONDS = 1.0

_T = TypeVar("_T")


# -- helpers ----------------------------------------------------------------


def _buffer(seconds: float) -> AudioBuffer:
    """One mono buffer of ``seconds`` at 16 kHz (samples are irrelevant here)."""
    return AudioBuffer(
        samples=[0.0] * round(seconds * SAMPLE_RATE), sample_rate=SAMPLE_RATE
    )


def _partial(text: str) -> TranscriptionSegment:
    return TranscriptionSegment(
        text=text, language="en", is_final=False, pass_ms=None
    )


def _final(text: str) -> TranscriptionSegment:
    return TranscriptionSegment(
        text=text, language="en", is_final=True, pass_ms=None
    )


def _session(
    segments: Sequence[TranscriptionSegment],
    *,
    buffers: Sequence[AudioBuffer] | None = None,
    chunk_seconds: float = CHUNK_SECONDS,
) -> tuple[StreamingDictationSession, FakeAudioSource, FakeTranscriber]:
    audio = FakeAudioSource(buffers=list(buffers) if buffers is not None else [_buffer(0.5)])
    transcriber = FakeTranscriber(segments=list(segments))
    session = StreamingDictationSession(
        audio, transcriber, chunk_seconds=chunk_seconds
    )
    return session, audio, transcriber


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


def _call_with_timeout(fn: Callable[[], _T], *, timeout: float) -> _T:
    """Run ``fn`` on a worker thread and fail if it has not returned in time.

    A session that held its lock across ``audio.stop()``/the engine call, or
    that waited for a partials consumer, would wedge here instead of hanging CI.
    Every test that drives ``finish()``/``cancel()`` on the main thread goes
    through this, so a wedged lifecycle fails as an assertion rather than
    silence.
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


class _BlockingAudioSource(FakeAudioSource):
    """A :class:`FakeAudioSource` whose chunk iterator blocks until ``stop()``.

    This lets a test hold the worker inside the chunk iterator so a
    ``partials()`` consumer is provably waiting when ``finish()`` is called.
    Everything else is the shared fake.
    """

    def __init__(self, buffers: Sequence[AudioBuffer]) -> None:
        super().__init__(buffers=list(buffers))
        self.chunks_started = threading.Event()
        self._released = threading.Event()

    def chunks(self, chunk_seconds: float) -> Iterator[AudioBuffer]:
        self.calls.append("chunks")
        self.chunk_seconds_seen.append(chunk_seconds)
        self.chunks_started.set()
        yield from self.buffers
        self._released.wait(timeout=5.0)

    def stop(self) -> AudioBuffer:
        self._released.set()
        return super().stop()


# -- construction / lifecycle ----------------------------------------------


def test_streaming_session_is_a_dictation_session() -> None:
    session, _, _ = _session([_final("x")])

    assert isinstance(session, StreamingDictationSession)
    assert isinstance(session, DictationSession)


@pytest.mark.parametrize("chunk_seconds", [0.0, -1.0])
def test_non_positive_chunk_seconds_is_rejected(chunk_seconds: float) -> None:
    audio = FakeAudioSource()
    transcriber = FakeTranscriber()

    with pytest.raises(ValueError, match="chunk_seconds must be positive"):
        StreamingDictationSession(audio, transcriber, chunk_seconds=chunk_seconds)


def test_start_starts_capture_and_reads_chunks_at_the_configured_size() -> None:
    session, audio, transcriber = _session(
        [_final("done")], buffers=[_buffer(2.0)], chunk_seconds=0.5
    )

    session.start()
    _wait_for(lambda: "chunks" in audio.calls, description="the worker reading chunks")
    text = _call_with_timeout(session.finish, timeout=2.0)

    assert text == "done"
    assert audio.calls == ["start", "chunks", "stop"]
    assert audio.chunk_seconds_seen == [0.5]
    assert transcriber.calls == ["stream"]


def test_start_twice_is_rejected() -> None:
    session, _, _ = _session([_final("done")])
    session.start()
    try:
        with pytest.raises(StreamingSessionError, match="already"):
            session.start()
    finally:
        _call_with_timeout(session.cancel, timeout=2.0)


def test_finish_before_start_is_defined() -> None:
    session, _, _ = _session([_final("done")])

    with pytest.raises(StreamingSessionError, match="before start"):
        _call_with_timeout(session.finish, timeout=2.0)


def test_cancel_before_start_is_defined() -> None:
    session, _, _ = _session([_final("done")])

    with pytest.raises(StreamingSessionError, match="before start"):
        _call_with_timeout(session.cancel, timeout=2.0)


# -- final text is authoritative -------------------------------------------


def test_finish_returns_the_final_segment_text_including_the_flush_tail() -> None:
    session, _, _ = _session(
        [_partial("hello"), _partial("hello world"), _final("hello world again")]
    )

    session.start()
    text = _call_with_timeout(session.finish, timeout=2.0)

    assert text == "hello world again"


def test_finish_is_empty_when_the_stream_yields_no_final_segment() -> None:
    # Partial text must never be promoted to the transcript: with no final
    # segment, the authoritative answer is the empty string, not "POISON".
    session, _, _ = _session([_partial("POISON"), _partial("POISON more")])

    session.start()

    assert _call_with_timeout(session.finish, timeout=2.0) == ""


def test_a_partial_that_looks_like_text_never_reaches_finish() -> None:
    session, _, _ = _session([_partial("POISON partial ONLY"), _final("clean final")])

    session.start()
    text = _call_with_timeout(session.finish, timeout=2.0)

    assert text == "clean final"
    assert "POISON" not in text


def test_short_utterance_still_returns_its_final_text() -> None:
    session, _, _ = _session([_final("short but final")], buffers=[_buffer(0.2)])

    session.start()
    text = _call_with_timeout(session.finish, timeout=2.0)

    assert text == "short but final"


def test_finish_twice_returns_the_cached_text_without_streaming_again() -> None:
    session, audio, transcriber = _session([_partial("a"), _final("a b")])
    session.start()

    first = _call_with_timeout(session.finish, timeout=2.0)
    second = _call_with_timeout(session.finish, timeout=2.0)

    assert first == second == "a b"
    assert transcriber.calls == ["stream"]
    assert audio.calls.count("stop") == 1


# -- partials ---------------------------------------------------------------


def test_partials_advance_and_consecutive_duplicates_are_dropped() -> None:
    session, _, _ = _session(
        [
            _partial("a"),
            _partial("a"),
            _partial("a b"),
            _partial("a b"),
            _partial("a b c"),
            _final("a b c d"),
        ]
    )
    collected: list[str] = []
    consumer_done = threading.Event()

    def consume() -> None:
        collected.extend(session.partials())
        consumer_done.set()

    session.start()
    consumer = threading.Thread(target=consume, daemon=True)
    consumer.start()
    text = _call_with_timeout(session.finish, timeout=2.0)

    assert consumer_done.wait(timeout=2.0)
    consumer.join(timeout=2.0)
    assert text == "a b c d"
    assert collected == ["a", "a b", "a b c"]
    assert "a b c d" not in collected


def test_partials_allow_a_legitimate_non_consecutive_repeat() -> None:
    """Dedup is consecutive-only, not global (both directions of the rule).

    A value that legitimately repeats after an intervening different partial
    must survive; only *adjacent* duplicates are dropped. This is the
    "legitimate later repeat" direction that
    :func:`test_partials_advance_and_consecutive_duplicates_are_dropped` does
    not pin.
    """
    session, _, _ = _session(
        [
            _partial("a"),
            _partial("a"),
            _partial("a b"),
            _partial("a b"),
            _partial("a"),
            _final("a b final"),
        ]
    )
    collected: list[str] = []
    consumer_done = threading.Event()

    def consume() -> None:
        collected.extend(session.partials())
        consumer_done.set()

    session.start()
    consumer = threading.Thread(target=consume, daemon=True)
    consumer.start()
    text = _call_with_timeout(session.finish, timeout=2.0)

    assert consumer_done.wait(timeout=2.0)
    consumer.join(timeout=2.0)
    assert text == "a b final"
    assert collected == ["a", "a b", "a"]


def test_partials_are_readable_after_finish_without_re_running_the_stream() -> None:
    session, _, transcriber = _session([_partial("a"), _partial("a b"), _final("a b c")])
    session.start()
    _call_with_timeout(session.finish, timeout=2.0)

    assert list(session.partials()) == ["a", "a b"]
    assert transcriber.calls == ["stream"]


def test_a_waiting_partials_consumer_does_not_block_finish() -> None:
    audio = _BlockingAudioSource([_buffer(0.5)])
    transcriber = FakeTranscriber(segments=[_partial("a"), _final("a b")])
    session = StreamingDictationSession(audio, transcriber, chunk_seconds=1.0)
    collected: list[str] = []
    consumer_done = threading.Event()

    def consume() -> None:
        collected.extend(session.partials())
        consumer_done.set()

    session.start()
    assert audio.chunks_started.wait(timeout=2.0)
    consumer = threading.Thread(target=consume, daemon=True)
    consumer.start()

    text = _call_with_timeout(session.finish, timeout=2.0)

    assert text == "a b"
    assert consumer_done.wait(timeout=2.0)
    consumer.join(timeout=2.0)
    assert collected == ["a"]


def test_finish_does_not_drain_unconsumed_partials() -> None:
    # Nobody consumes partials(); finish() must still return promptly and must
    # not depend on the partial buffer being drained.
    segments: list[TranscriptionSegment] = [
        _partial(f"p{index}") for index in range(500)
    ]
    segments.append(_final("final"))
    session, _, _ = _session(segments)

    session.start()
    text = _call_with_timeout(session.finish, timeout=2.0)

    assert text == "final"


# -- cancel -----------------------------------------------------------------


def test_cancel_stops_capture_joins_the_worker_and_types_nothing() -> None:
    session, audio, _ = _session(
        [_partial("must not survive"), _final("also must not")], buffers=[_buffer(1.0)]
    )
    session.start()
    _wait_for(lambda: "chunks" in audio.calls, description="the worker reading chunks")

    # ``cancel()`` is annotated ``-> None``; it must not raise and must leave
    # nothing observable behind.
    _call_with_timeout(session.cancel, timeout=2.0)

    worker = session._worker
    assert worker is not None
    assert not worker.is_alive()
    assert "stop" in audio.calls
    # The transcript and the partials are abandoned.
    assert list(session.partials()) == []
    with pytest.raises(StreamingSessionError, match="after cancel"):
        session.finish()


def test_cancel_twice_is_idempotent() -> None:
    session, _, _ = _session([_final("done")])
    session.start()

    _call_with_timeout(session.cancel, timeout=2.0)
    _call_with_timeout(session.cancel, timeout=2.0)


# -- error propagation ------------------------------------------------------


def test_a_raising_stream_surfaces_through_finish() -> None:
    audio = FakeAudioSource(buffers=[_buffer(0.5)])
    transcriber = FakeTranscriber(
        segments=[_final("never")], raise_on_stream=RuntimeError("engine exploded")
    )
    session = StreamingDictationSession(audio, transcriber, chunk_seconds=1.0)

    session.start()
    with pytest.raises(RuntimeError, match="engine exploded"):
        _call_with_timeout(session.finish, timeout=2.0)

    worker = session._worker
    assert worker is not None
    assert not worker.is_alive()


def test_a_raising_chunk_iterator_surfaces_through_finish() -> None:
    audio = FakeAudioSource(
        buffers=[_buffer(0.5)], raise_on_chunks=RuntimeError("capture failed")
    )
    transcriber = FakeTranscriber(segments=[_final("never")])
    session = StreamingDictationSession(audio, transcriber, chunk_seconds=1.0)

    session.start()
    with pytest.raises(RuntimeError, match="capture failed"):
        _call_with_timeout(session.finish, timeout=2.0)

    assert "stop" in audio.calls


# -- the service never injects partials -------------------------------------


def test_service_displays_partials_but_never_injects_them(tmp_path: Path) -> None:
    """The "nothing injected until release" rule at the service boundary (ADR 0011).

    The real :class:`~ges2t.service.Service` drives the real session with a fake
    injector. The partial text is forwarded to the status UI and must never be
    handed to the injector; only the final segment's text is injected.
    """
    audio = FakeAudioSource(
        buffers=[_buffer(0.5)], stop_buffer=_buffer(0.5)
    )
    transcriber = FakeTranscriber(
        segments=[_partial("POISON display only"), _final("published transcript")]
    )
    session = StreamingDictationSession(audio, transcriber, chunk_seconds=0.25)
    injector = FakeInjector()
    status_ui = FakeStatusUI()
    service = Service(lambda: session, injector, status_ui, tmp_path / "ges2t.sock")
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

        assert injector.injected == ["published transcript"]
        assert all("POISON" not in text for text in injector.injected)
        assert "POISON display only" in status_ui.partials
    finally:
        service.shutdown()
