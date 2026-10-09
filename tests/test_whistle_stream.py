"""Tests for the Whistle **streaming** adapter, :mod:`ges2t.transcribers.whistle`.

These tests need **no engine, no model, and no network**. As in
``tests/test_whistle_batch.py``, ``needle`` is the lazy module global
``whistle._needle``, replaced here with :class:`FakeStreamingNeedle`; the
fixture also poisons ``sys.modules['needle']`` so a stray real import would fail
on a machine where the package happens to be installed (ADR 0014).

The streaming engine is modelled as the real ``cactus-needle`` package exposes
it: ``Whistle.stream(chunks, language=None, keywords=None)`` is a **generator**
that pulls one chunk per ``next()`` and yields one dict per chunk, then one tail
dict after ``needle_stream_transcribe_stop`` (measured with the real engine, see
``001-comment.md``). The fake records the exact raw bytes it is handed, which is
how the float32 conversion path is asserted, and lets a test script the
committed deltas, the display-only ``pending`` tail, and the language.

Covered here: the raw float32 conversion, ``process()``/``flush()`` mapping to
:class:`~ges2t.streaming.StreamingPass` and the tail text, the concatenation-safe
whitespace convention that ticket 13's ``drive()`` relies on, the accumulated
transcript over several windows (never reconstructed from ``pending``), language
propagation, and the windowing being delegated to ``drive()`` (ADR 0011).
"""

from __future__ import annotations

import sys
from array import array
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from ges2t import model_store
from ges2t.interfaces import AudioBuffer, Transcriber, TranscriptionSegment
from ges2t.streaming import StreamingPass, drive
from ges2t.transcribers import whistle
from ges2t.transcribers.whistle import (
    SAMPLE_RATE,
    WhistleInputError,
    WhistleNotLoadedError,
    WhistleTranscriber,
)
from ges2t.transcribers.whistle_config import WhistleConfig

# --- fake engine --------------------------------------------------------------


@dataclass(frozen=True)
class FakeScript:
    """Deterministic streaming output the fake engine replays.

    ``chunks`` holds the committed delta for each ``process()`` call (indexed by
    the global chunk number, across windows); ``tails`` holds the tail committed
    when each window's stream ends (indexed by the window number). ``pending``
    and ``languages`` are optional per-chunk extras; a missing entry means the
    empty tail and ``None`` respectively.
    """

    chunks: tuple[str, ...]
    tails: tuple[str, ...] = ()
    pending: tuple[str, ...] = ()
    languages: tuple[str | None, ...] = ()

    def chunk_text(self, index: int) -> str:
        return self.chunks[index] if index < len(self.chunks) else ""

    def chunk_pending(self, index: int) -> str:
        return self.pending[index] if index < len(self.pending) else ""

    def chunk_language(self, index: int) -> str | None:
        return self.languages[index] if index < len(self.languages) else None

    def window_tail(self, window: int) -> str:
        return self.tails[window] if window < len(self.tails) else ""


class FakeStreamingEngine:
    """Stand-in for ``needle.Whistle`` exposing the real streaming generator shape."""

    def __init__(self, weights: str, script: FakeScript) -> None:
        self.weights = weights
        self.script = script
        self.stream_calls = 0
        self.chunk_calls = 0
        self.tail_calls = 0
        self.raw_chunks: list[bytes] = []
        self.tail_texts: list[str] = []
        self.languages_seen: list[str | None] = []

    def stream(
        self,
        chunks: Iterable[bytes],
        language: str | None = None,
        keywords: str | list[str] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield one dict per pulled chunk, then one tail dict (the engine's ``stop()``)."""
        window = self.stream_calls
        self.stream_calls += 1
        self.languages_seen.append(language)
        last_language: str | None = None
        for raw in chunks:
            index = self.chunk_calls
            self.chunk_calls += 1
            self.raw_chunks.append(raw)
            last_language = self.script.chunk_language(index)
            yield {
                "text": self.script.chunk_text(index),
                "pending": self.script.chunk_pending(index),
                "language": last_language,
                "words": [],
                "received": 0.0,
                "pass_ms": 0.0,
            }
        tail = self.script.window_tail(window)
        self.tail_calls += 1
        self.tail_texts.append(tail)
        yield {
            "text": tail,
            "pending": "",
            "language": last_language,
            "words": [],
            "received": 0.0,
            "pass_ms": 0.0,
        }


class FakeLibrary:
    """Stand-in for the ctypes ``CDLL`` handle (the adapter only reads ``_name``)."""

    def __init__(self, name: str) -> None:
        self._name = name


class FakeStreamingNeedle:
    """Stand-in for the top-level ``needle`` package whose ``Whistle`` streams."""

    def __init__(
        self,
        script: FakeScript,
        *,
        engine_path: str | None = None,
    ) -> None:
        self.script = script
        self._engine_path = (
            engine_path if engine_path is not None else str(model_store.engine_path())
        )
        self.created: list[FakeStreamingEngine] = []
        self.agent = SimpleNamespace(whistle=SimpleNamespace(_lib=self._lib))

    def _lib(self) -> FakeLibrary:
        return FakeLibrary(self._engine_path)

    def Whistle(self, weights: str) -> FakeStreamingEngine:
        engine = FakeStreamingEngine(weights, self.script)
        self.created.append(engine)
        return engine


SCRIPT = FakeScript(
    chunks=(
        "",
        "the quick brown",
        "fox jumps",
        "over",
        "the lazy",
        "then",
        "it",
        "ran",
        "away",
        "quickly",
    ),
    tails=("dog.", "into", ""),
    languages=(
        "en",
        "en",
        "en",
        "en",
        "en",
        "de",
        "de",
        "de",
        "de",
        "de",
    ),
)

#: The transcript the fake's deltas and tails must accumulate to, in event order
#: at a 5-chunk window: chunks 0-4, tail 0, chunks 5-9, tail 1, final tail 2.
EXPECTED_TRANSCRIPT = (
    "the quick brown fox jumps over the lazy dog. then it ran away quickly into"
)


# --- fixtures -----------------------------------------------------------------


@pytest.fixture()
def fake_needle(monkeypatch: pytest.MonkeyPatch) -> FakeStreamingNeedle:
    """Replace the lazy engine import with the streaming fake and block a real import."""
    fake = FakeStreamingNeedle(SCRIPT)
    monkeypatch.setattr(whistle, "_needle", fake)
    monkeypatch.setitem(sys.modules, "needle", None)
    return fake


def _loaded_transcriber(
    fake_needle: FakeStreamingNeedle, config: WhistleConfig | None = None
) -> WhistleTranscriber:
    adapter = WhistleTranscriber(config or WhistleConfig())
    adapter.load()
    return adapter


def _chunk(seconds: float = 1.0, value: float = 0.0) -> AudioBuffer:
    return AudioBuffer(
        samples=[value] * int(seconds * SAMPLE_RATE), sample_rate=SAMPLE_RATE
    )


# --- interface conformance ----------------------------------------------------


def test_supports_streaming_remains_true() -> None:
    adapter = WhistleTranscriber(WhistleConfig())

    assert adapter.supports_streaming is True
    assert isinstance(adapter, Transcriber)


def test_stream_before_load_fails_eagerly() -> None:
    adapter = WhistleTranscriber(WhistleConfig())

    with pytest.raises(WhistleNotLoadedError):
        adapter.stream([])


# --- the decoder: process()/flush() mapping -----------------------------------


def test_process_passes_raw_float32_bytes(fake_needle: FakeStreamingNeedle) -> None:
    adapter = _loaded_transcriber(fake_needle)
    samples: Sequence[float] = [0.0, 0.5, -0.25, 1.0, -1.0]
    buffer = AudioBuffer(samples=samples, sample_rate=SAMPLE_RATE)

    list(adapter.stream([buffer]))

    engine = fake_needle.created[-1]
    assert len(engine.raw_chunks) == 1
    assert isinstance(engine.raw_chunks[0], bytes)
    assert len(engine.raw_chunks[0]) == len(samples) * 4
    recovered = array("f")
    recovered.frombytes(engine.raw_chunks[0])
    assert list(recovered) == pytest.approx(samples)


def test_process_maps_the_engine_json_to_a_streaming_pass(
    fake_needle: FakeStreamingNeedle,
) -> None:
    adapter = _loaded_transcriber(fake_needle)
    engine = fake_needle.created[-1]
    decoder = whistle._WhistleStreamingDecoder(engine, "")

    result = decoder.process(_chunk())

    assert isinstance(result, StreamingPass)
    assert result.committed == ""
    assert result.pending == ""
    assert result.language == "en"


def test_pending_is_not_prefixed_before_any_commit() -> None:
    script = FakeScript(chunks=("",), pending=("unconfirmed tail",))
    engine = FakeStreamingEngine(str(model_store.model_path()), script)
    decoder = whistle._WhistleStreamingDecoder(engine, "")

    result = decoder.process(_chunk())

    assert result.committed == ""
    assert result.pending == "unconfirmed tail"


def test_empty_input_yields_one_empty_final_segment(
    fake_needle: FakeStreamingNeedle,
) -> None:
    fake_needle.script = FakeScript(chunks=(), tails=("",))
    adapter = _loaded_transcriber(fake_needle)

    segments = list(adapter.stream([]))

    assert len(segments) == 1
    assert segments[0].is_final is True
    assert segments[0].text == ""
    assert fake_needle.created[-1].tail_calls == 1


def test_process_carries_the_engines_language_and_committed_delta(
    fake_needle: FakeStreamingNeedle,
) -> None:
    script = FakeScript(
        chunks=("hallo",), tails=("welt",), pending=("unchecked",), languages=("de",)
    )
    engine = FakeStreamingEngine(str(model_store.model_path()), script)
    decoder = whistle._WhistleStreamingDecoder(engine, "de")

    result = decoder.process(_chunk())

    assert result == StreamingPass(committed="hallo", pending=" unchecked", language="de")


def test_flush_returns_the_tail_and_resets_for_a_new_stream(
    fake_needle: FakeStreamingNeedle,
) -> None:
    script = FakeScript(chunks=("one", "two", "four"), tails=("three",))
    engine = FakeStreamingEngine(str(model_store.model_path()), script)
    decoder = whistle._WhistleStreamingDecoder(engine, "")

    first = decoder.process(_chunk())
    decoder.process(_chunk())
    tail = decoder.flush()

    assert first.committed == "one"
    assert tail == " three"
    # The fresh stream's first delta continues the transcript, so it carries the
    # leading space; the decoder never resets the "already emitted" state.
    second = decoder.process(_chunk())
    assert second.committed == " four"


def test_flush_on_a_fresh_decoder_returns_the_empty_tail() -> None:
    script = FakeScript(chunks=(), tails=("",))
    engine = FakeStreamingEngine(str(model_store.model_path()), script)
    decoder = whistle._WhistleStreamingDecoder(engine, "")

    assert decoder.flush() == ""
    assert engine.tail_calls == 1


def test_process_rejects_a_buffer_that_is_not_16khz(
    fake_needle: FakeStreamingNeedle,
) -> None:
    adapter = _loaded_transcriber(fake_needle)
    decoder = whistle._WhistleStreamingDecoder(fake_needle.created[-1], "")

    with pytest.raises(WhistleInputError, match="16000"):
        decoder.process(AudioBuffer(samples=[0.0], sample_rate=8000))


# --- the adapter through drive() ----------------------------------------------


def test_stream_yields_one_partial_per_chunk_and_one_final_segment(
    fake_needle: FakeStreamingNeedle,
) -> None:
    adapter = _loaded_transcriber(fake_needle)
    chunks = [_chunk() for _ in range(10)]

    segments = list(adapter.stream(chunks))

    assert len(segments) == 11
    assert all(not segment.is_final for segment in segments[:-1])
    assert segments[-1].is_final is True


def test_accumulated_transcript_is_exactly_the_joined_deltas(
    fake_needle: FakeStreamingNeedle,
) -> None:
    adapter = _loaded_transcriber(fake_needle)
    chunks = [_chunk() for _ in range(10)]

    segments = list(adapter.stream(chunks))

    assert segments[-1].text == EXPECTED_TRANSCRIPT


def test_windowing_is_delegated_to_drive(
    fake_needle: FakeStreamingNeedle,
) -> None:
    adapter = _loaded_transcriber(fake_needle)
    chunks = [_chunk() for _ in range(10)]

    list(adapter.stream(chunks))

    engine = fake_needle.created[-1]
    # 10 one-second chunks at a 5 s window: two window generators plus one for
    # drive()'s unconditional final flush, and a stop() tail per generator.
    assert engine.stream_calls == 3
    assert engine.chunk_calls == 10
    assert engine.tail_calls == 3
    assert engine.tail_texts == ["dog.", "into", ""]


def test_a_single_long_buffer_is_split_into_chunk_seconds_groups(
    fake_needle: FakeStreamingNeedle,
) -> None:
    adapter = _loaded_transcriber(fake_needle)

    segments = list(adapter.stream([_chunk(seconds=3.0)]))

    # drive() split the one 3 s buffer into three 1 s groups, so the fake saw
    # three process() calls; the adapter did not reimplement the grouping.
    assert fake_needle.created[-1].chunk_calls == 3
    assert len(segments) == 4


def test_drive_is_the_windowing_implementation(
    fake_needle: FakeStreamingNeedle,
) -> None:
    """The adapter's stream() must be exactly ``drive(decoder, chunks, ...)``."""
    adapter = _loaded_transcriber(fake_needle)
    chunks = [_chunk() for _ in range(10)]
    reference_engine = FakeStreamingEngine(str(model_store.model_path()), SCRIPT)

    expected = [
        segment.text
        for segment in drive(
            whistle._WhistleStreamingDecoder(reference_engine, ""),
            [_chunk() for _ in range(10)],
            window_seconds=WhistleConfig().window_seconds,
            chunk_seconds=WhistleConfig().chunk_seconds,
        )
    ]
    actual = [segment.text for segment in adapter.stream(chunks)]

    assert actual == expected


def test_configured_window_and_chunk_sizes_are_used(
    fake_needle: FakeStreamingNeedle,
) -> None:
    config = WhistleConfig(window_seconds=2.0, chunk_seconds=1.0)
    adapter = _loaded_transcriber(fake_needle, config)
    chunks = [_chunk() for _ in range(6)]

    list(adapter.stream(chunks))

    engine = fake_needle.created[-1]
    # 6 s at a 2 s window: three boundaries plus the final flush -> 4 generators.
    assert engine.stream_calls == 4
    assert engine.tail_texts == ["dog.", "into", "", ""]


def test_final_segment_reports_the_most_recent_language(
    fake_needle: FakeStreamingNeedle,
) -> None:
    adapter = _loaded_transcriber(fake_needle)

    segments = list(adapter.stream([_chunk() for _ in range(10)]))

    assert segments[-1].language == "de"


def test_pending_is_display_only_and_never_reaches_the_transcript(
    fake_needle: FakeStreamingNeedle,
) -> None:
    poison = "POISON-DISPLAY-ONLY"
    script = FakeScript(
        chunks=("hello", "world"), tails=("again",), pending=(poison, poison)
    )
    fake_needle.script = script
    adapter = _loaded_transcriber(fake_needle)

    segments = list(adapter.stream([_chunk(), _chunk()]))

    assert poison not in segments[-1].text
    assert segments[-1].text == "hello world again"
    # The partials do show the pending tail, as display-only text (ADR 0011).
    assert any(poison in segment.text for segment in segments[:-1])


def test_engine_call_failure_does_not_leave_a_usable_stream(
    fake_needle: FakeStreamingNeedle,
) -> None:
    adapter = _loaded_transcriber(fake_needle)

    with pytest.raises(WhistleInputError):
        list(adapter.stream([AudioBuffer(samples=[0.0], sample_rate=8000)]))


def test_config_language_is_passed_to_the_engine(
    fake_needle: FakeStreamingNeedle,
) -> None:
    adapter = _loaded_transcriber(fake_needle, WhistleConfig(language="de"))

    list(adapter.stream([_chunk()]))

    assert fake_needle.created[-1].languages_seen == ["de"]


def test_stream_uses_the_already_loaded_engine(
    fake_needle: FakeStreamingNeedle,
) -> None:
    adapter = _loaded_transcriber(fake_needle)

    list(adapter.stream([_chunk()]))

    # Exactly one Whistle was constructed by load(); streaming reused it rather
    # than creating a second process-global engine (ADR 0005).
    assert len(fake_needle.created) == 1
