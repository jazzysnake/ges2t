"""Tests for the generic windowed-commit streaming driver (ADR 0011).

Everything here runs against a deterministic fake :class:`StreamingDecoder`
defined in this file; no engine, audio device, or clock is involved. The fake
models the engine's two-consecutive-passes-agree commit rule as a word revealed
at a fixed rate over the utterance: on each pass the last revealed word stays
``pending`` and every earlier one is committed, and ``flush()`` commits the
pending word. That makes "no word duplicated or lost at a seam" a hard,
checkable property of the concatenated transcript.

The fake is part of the shared test surface: the streaming-session ticket drives
this same ``drive()`` and imports :class:`FakeStreamingDecoder` from here rather
than redefining it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence

import pytest

from ges2t.interfaces import AudioBuffer, LiveText, TranscriptionSegment
from ges2t.streaming import StreamingDecoder, StreamingPass, drive

__all__ = ["FakeStreamingDecoder"]

SAMPLE_RATE = 16000

#: A distinct, easy-to-count token per word, so a duplicated token is detectable.
WORDS_68: list[str] = [f"w{i}" for i in range(68)]


class FakeStreamingDecoder:
    """A deterministic ``StreamingDecoder`` with a known-perfect transcript.

    ``words`` is the whole utterance and ``total_samples`` the exact number of
    samples that will be fed. A pass reveals ``floor(fed * len(words) /
    total_samples)`` words in total; the newest revealed word stays ``pending``
    and the rest is the ``committed`` delta, so a word is committed only once a
    later pass exists. ``flush()`` commits the pending tail. Consequently the
    concatenation of every ``committed`` delta and every ``flush()`` return value
    is exactly ``" ".join(words)``.

    The deltas carry their own leading separator space (the convention a real
    engine must follow, because :func:`ges2t.streaming.drive` concatenates them
    verbatim), so ``committed + pending`` is always a clean prefix of the
    utterance.

    ``languages`` optionally supplies the ``StreamingPass.language`` for each
    ``process()`` call, in order; a shorter sequence, or ``None``, leaves the
    later passes reporting ``None``. ``flush()`` never reports a language.
    """

    def __init__(
        self,
        words: Sequence[str],
        *,
        total_samples: int,
        sample_rate: int = SAMPLE_RATE,
        languages: Sequence[str | None] | None = None,
    ) -> None:
        self._words: list[str] = list(words)
        self._total_samples = total_samples
        self._sample_rate = sample_rate
        self._languages: list[str | None] = (
            list(languages) if languages is not None else []
        )
        self.process_calls = 0
        self.flush_calls = 0
        #: Samples received by each process() call, in order.
        self.processed_samples: list[int] = []
        #: Words revealed (globally) after each process() call, in order.
        self.available_history: list[int] = []
        #: The process_calls count at each flush(), in order.
        self.flush_after_process_calls: list[int] = []
        #: The text each flush() returned, in order.
        self.flush_returns: list[str] = []
        self.raise_on_process: Exception | None = None
        self.raise_on_flush: Exception | None = None
        self._fed_samples = 0
        self._committed = 0
        self._available = 0

    @property
    def words(self) -> list[str]:
        """The utterance this decoder is a perfect model of."""
        return list(self._words)

    def _prefix(self, word_count: int) -> str:
        """The utterance text up to ``word_count`` words, with separators."""
        return " ".join(self._words[:word_count])

    def process(self, buffer: AudioBuffer) -> StreamingPass:
        self.process_calls += 1
        if self.raise_on_process is not None:
            raise self.raise_on_process
        self.processed_samples.append(len(buffer.samples))
        self._fed_samples += len(buffer.samples)
        available = min(
            len(self._words),
            self._fed_samples * len(self._words) // self._total_samples,
        )
        # The engine commits words two consecutive passes agree on: keep the
        # newest revealed word pending, commit everything before it. Deltas are
        # slices of the full text so concatenation reproduces the separators.
        commit_end = max(self._committed, available - 1)
        committed = self._prefix(commit_end)[len(self._prefix(self._committed)) :]
        pending = self._prefix(available)[len(self._prefix(commit_end)) :]
        self._committed = commit_end
        self._available = available
        self.available_history.append(available)
        language = (
            self._languages[self.process_calls - 1]
            if self.process_calls - 1 < len(self._languages)
            else None
        )
        return StreamingPass(committed=committed, pending=pending, language=language)

    def flush(self) -> str:
        self.flush_calls += 1
        self.flush_after_process_calls.append(self.process_calls)
        if self.raise_on_flush is not None:
            raise self.raise_on_flush
        tail = self._prefix(self._available)[len(self._prefix(self._committed)) :]
        self._committed = self._available
        self.flush_returns.append(tail)
        return tail


class PendingPoisonDecoder:
    """A decoder whose ``pending`` is garbage that must never reach the transcript.

    ``committed`` and ``flush()`` carry the real text; ``pending`` is a poison
    token. If the driver ever reconstructed the final segment from ``pending``
    instead of from committed deltas plus the final flush, the poison would show
    up (ADR 0011).
    """

    def __init__(self) -> None:
        self.process_calls = 0
        self.flush_calls = 0

    def process(self, buffer: AudioBuffer) -> StreamingPass:
        self.process_calls += 1
        return StreamingPass(
            committed=f"committed{self.process_calls} ", pending="POISON"
        )

    def flush(self) -> str:
        self.flush_calls += 1
        return "tail"


class PendingOnlyAdvanceDecoder:
    """A decoder whose ``pending`` changes while the committed delta stops.

    The first pass commits ``"confirmed"``; every later pass commits the empty
    delta, so the driver's cumulative committed transcript must stay fixed while
    only the pending tail changes. This is the decoder ticket 33's "a change
    confined to pending does not advance committed" invariant is pinned against.
    """

    def __init__(self) -> None:
        self.process_calls = 0
        self.flush_calls = 0

    def process(self, buffer: AudioBuffer) -> StreamingPass:
        self.process_calls += 1
        committed = "confirmed" if self.process_calls == 1 else ""
        return StreamingPass(
            committed=committed,
            pending=" candidate" + " more" * (self.process_calls - 1),
        )

    def flush(self) -> str:
        self.flush_calls += 1
        return ""


def make_audio(
    seconds: Sequence[float], *, sample_rate: int = SAMPLE_RATE
) -> list[AudioBuffer]:
    """One :class:`AudioBuffer` per requested duration, at ``sample_rate``."""
    return [
        AudioBuffer(
            samples=[0.0] * round(duration * sample_rate), sample_rate=sample_rate
        )
        for duration in seconds
    ]


def make_varied_audio(
    total_seconds: float,
    *,
    chunk_seconds: float = 1.0,
    sample_rate: int = SAMPLE_RATE,
) -> list[AudioBuffer]:
    """``total_seconds`` delivered in ``chunk_seconds`` buffers (last one short)."""
    buffers: list[AudioBuffer] = []
    remaining = total_seconds
    while remaining > 0:
        block = min(chunk_seconds, remaining)
        buffers.append(
            AudioBuffer(
                samples=[0.0] * round(block * sample_rate), sample_rate=sample_rate
            )
        )
        remaining -= block
    return buffers


def total_samples(buffers: Sequence[AudioBuffer]) -> int:
    """Sum the samples in ``buffers`` (for the fake's perfect-word model)."""
    return sum(len(buffer.samples) for buffer in buffers)


def run(
    decoder: StreamingDecoder,
    buffers: Sequence[AudioBuffer],
    *,
    window_seconds: float,
    chunk_seconds: float,
    on_partial: Callable[[LiveText], None] | None = None,
) -> list[TranscriptionSegment]:
    """Consume ``drive`` fully and return every yielded segment."""
    return list(
        drive(
            decoder,
            buffers,
            window_seconds=window_seconds,
            chunk_seconds=chunk_seconds,
            on_partial=on_partial,
        )
    )


# --------------------------------------------------------------------------- #
# Window boundaries
# --------------------------------------------------------------------------- #


def test_thirty_seconds_at_five_second_window_flushes_six_boundaries_plus_final() -> None:
    buffers = make_varied_audio(30.0, chunk_seconds=1.0)
    decoder = FakeStreamingDecoder(WORDS_68, total_samples=total_samples(buffers))

    run(decoder, buffers, window_seconds=5.0, chunk_seconds=1.0)

    assert decoder.process_calls == 30
    # Boundary flushes after 5, 10, 15, 20, 25 and 30 s, then the final flush.
    assert decoder.flush_calls == 7
    assert decoder.flush_after_process_calls == [5, 10, 15, 20, 25, 30, 30]


def test_window_equal_to_chunk_flushes_after_every_group() -> None:
    buffers = make_varied_audio(3.0, chunk_seconds=1.0)
    decoder = FakeStreamingDecoder(["a", "b", "c"], total_samples=total_samples(buffers))

    run(decoder, buffers, window_seconds=1.0, chunk_seconds=1.0)

    assert decoder.flush_after_process_calls == [1, 2, 3, 3]


def test_one_long_incoming_buffer_is_split_into_groups() -> None:
    buffers = make_audio([12.0])
    decoder = FakeStreamingDecoder(WORDS_68, total_samples=total_samples(buffers))

    run(decoder, buffers, window_seconds=5.0, chunk_seconds=1.0)

    assert decoder.process_calls == 12
    assert decoder.flush_after_process_calls == [5, 10, 12]


def test_short_incoming_buffers_are_combined_into_groups() -> None:
    buffers = make_varied_audio(5.0, chunk_seconds=0.25)
    decoder = FakeStreamingDecoder(["a", "b"], total_samples=total_samples(buffers))

    run(decoder, buffers, window_seconds=5.0, chunk_seconds=1.0)

    # Twenty 0.25 s buffers become five 1 s groups: one boundary flush, one final.
    assert [len(buffer.samples) for buffer in buffers] == [4000] * 20
    assert decoder.process_calls == 5
    assert decoder.processed_samples == [16000] * 5
    assert decoder.flush_after_process_calls == [5, 5]


# --------------------------------------------------------------------------- #
# Seams: no word duplicated or lost
# --------------------------------------------------------------------------- #


def test_final_text_is_the_known_perfect_utterance_across_windows() -> None:
    buffers = make_varied_audio(30.0, chunk_seconds=1.0)
    decoder = FakeStreamingDecoder(WORDS_68, total_samples=total_samples(buffers))

    segments = run(decoder, buffers, window_seconds=5.0, chunk_seconds=1.0)
    final = segments[-1]

    assert final.is_final is True
    assert final.text == " ".join(WORDS_68)
    assert len(final.text.split()) == 68
    assert len(set(final.text.split())) == 68


def test_final_text_is_never_built_from_pending() -> None:
    buffers = make_audio([1.0, 1.0])
    decoder = PendingPoisonDecoder()

    segments = run(decoder, buffers, window_seconds=5.0, chunk_seconds=1.0)
    final = segments[-1]

    assert final.is_final is True
    assert final.text == "committed1 committed2 tail"
    assert "POISON" not in final.text
    # ...while the live partials definitely did show the pending text.
    assert segments[0].text == "committed1 POISON"


def test_uneven_tail_shorter_than_a_window_is_flushed_once() -> None:
    buffers = make_varied_audio(3.5, chunk_seconds=1.0)
    decoder = FakeStreamingDecoder(["a", "b", "c", "d"], total_samples=total_samples(buffers))

    segments = run(decoder, buffers, window_seconds=5.0, chunk_seconds=1.0)
    final = segments[-1]

    assert [len(buffer.samples) for buffer in buffers] == [16000, 16000, 16000, 8000]
    assert decoder.process_calls == 4
    assert decoder.processed_samples == [16000, 16000, 16000, 8000]
    # No boundary was reached; the final flush commits the 0.5 s tail exactly once.
    assert decoder.flush_calls == 1
    assert decoder.flush_after_process_calls == [4]
    assert final.text == "a b c d"


def test_uneven_tail_that_crosses_the_window_flushes_remaining_audio_once() -> None:
    buffers = make_varied_audio(5.5, chunk_seconds=1.0)
    decoder = FakeStreamingDecoder(["a"] * 6, total_samples=total_samples(buffers))

    run(decoder, buffers, window_seconds=5.0, chunk_seconds=1.0)

    # Boundary flush after the fifth full second, then the final flush of the
    # 0.5 s tail -- exactly once.
    assert decoder.flush_after_process_calls == [5, 6]


# --------------------------------------------------------------------------- #
# The committed/pending split (ticket committed-delta-interface)
# --------------------------------------------------------------------------- #


def test_committed_is_cumulative_and_append_only_across_segments() -> None:
    """(a): successive committed values only ever grow by appending."""
    buffers = make_varied_audio(12.0, chunk_seconds=1.0)
    decoder = FakeStreamingDecoder(WORDS_68, total_samples=total_samples(buffers))

    segments = run(decoder, buffers, window_seconds=5.0, chunk_seconds=1.0)

    previous = ""
    for segment in segments:
        committed = segment.live.committed
        assert committed.startswith(previous), (previous, committed)
        assert len(committed) >= len(previous)
        previous = committed
    assert segments[-1].live.committed == " ".join(WORDS_68)

    # The split is real, not a copy of the display: whenever there is a pending
    # tail the committed value is strictly shorter than ``committed + pending``,
    # so the committed prefix never swallows speculative text.
    with_pending = [segment for segment in segments if segment.live.pending]
    assert with_pending
    assert all(segment.live.committed != segment.text for segment in with_pending)


def test_final_segment_is_cumulative_and_reaches_the_whole_transcript() -> None:
    """(b): the final segment is the concatenation of the committed parts."""
    buffers = make_varied_audio(12.0, chunk_seconds=1.0)
    decoder = FakeStreamingDecoder(WORDS_68, total_samples=total_samples(buffers))

    segments = run(decoder, buffers, window_seconds=5.0, chunk_seconds=1.0)
    final = segments[-1]

    assert final.is_final is True
    assert final.live.pending == ""
    # Cumulative, not a delta: the whole utterance, not just the last flush.
    assert final.live.committed == final.text == " ".join(WORDS_68)

    # That whole transcript is exactly the concatenation of the per-pass
    # committed deltas the cumulative sequence encodes -- every word once.
    deltas: list[str] = []
    previous = ""
    for segment in segments:
        assert segment.live.committed.startswith(previous)
        deltas.append(segment.live.committed[len(previous):])
        previous = segment.live.committed
    assert "".join(deltas) == final.text


def test_display_string_is_byte_for_byte_the_pre_split_output() -> None:
    """(c): ``text`` is still exactly ``committed + pending``."""
    buffers = make_varied_audio(30.0, chunk_seconds=1.0)
    decoder = FakeStreamingDecoder(WORDS_68, total_samples=total_samples(buffers))

    segments = run(decoder, buffers, window_seconds=5.0, chunk_seconds=1.0)
    partials = [segment for segment in segments if not segment.is_final]

    assert [segment.text for segment in partials] == [
        segment.live.committed + segment.live.pending for segment in partials
    ]
    # ``decoder.available_history`` is exactly the merged ``transcript +
    # pending`` string the pre-split pipeline rendered after each pass.
    assert [segment.text for segment in partials] == [
        " ".join(WORDS_68[:available]) for available in decoder.available_history
    ]
    assert segments[-1].text == " ".join(WORDS_68)


def test_a_change_confined_to_pending_does_not_advance_committed() -> None:
    """(d): speculative tail changes must not move the committed prefix."""
    buffers = make_varied_audio(3.0, chunk_seconds=1.0)
    decoder = PendingOnlyAdvanceDecoder()

    segments = run(decoder, buffers, window_seconds=5.0, chunk_seconds=1.0)
    partials = [segment for segment in segments if not segment.is_final]

    assert decoder.process_calls == 3
    assert [segment.live.committed for segment in segments] == ["confirmed"] * 4
    assert [segment.live.pending for segment in partials] == [
        " candidate",
        " candidate more",
        " candidate more more",
    ]
    # The display still advances, because it is ``committed + pending`` ...
    assert [segment.text for segment in partials] == [
        "confirmed candidate",
        "confirmed candidate more",
        "confirmed candidate more more",
    ]
    # ... while the committed value a consumer may act on did not move at all.
    assert len({segment.live.committed for segment in segments}) == 1


# --------------------------------------------------------------------------- #
# Partials
# --------------------------------------------------------------------------- #


def test_partials_are_yielded_after_each_group_and_match_committed_plus_pending() -> None:
    buffers = make_varied_audio(30.0, chunk_seconds=1.0)
    decoder = FakeStreamingDecoder(WORDS_68, total_samples=total_samples(buffers))
    seen: list[LiveText] = []

    segments = run(
        decoder, buffers, window_seconds=5.0, chunk_seconds=1.0, on_partial=seen.append
    )
    partials = [segment for segment in segments if not segment.is_final]

    assert len(partials) == 30
    # After each pass the live text is exactly the words revealed so far:
    # transcript-so-far (committed) plus this pass's pending tail.
    expected = [" ".join(WORDS_68[:available]) for available in decoder.available_history]
    assert [partial.text for partial in partials] == expected
    assert [live.display for live in seen] == [partial.text for partial in partials]
    assert all(partial.language is None for partial in partials)
    assert all(partial.pass_ms is None for partial in partials)


def test_partials_are_monotone_prefixes_of_the_final_text() -> None:
    buffers = make_varied_audio(12.0, chunk_seconds=1.0)
    decoder = FakeStreamingDecoder(WORDS_68, total_samples=total_samples(buffers))

    segments = run(decoder, buffers, window_seconds=5.0, chunk_seconds=1.0)
    final = segments[-1]

    previous = ""
    for segment in segments:
        assert final.text.startswith(segment.text)
        assert len(segment.text) >= len(previous)
        previous = segment.text


# --------------------------------------------------------------------------- #
# Language propagation
# --------------------------------------------------------------------------- #


def test_language_is_the_most_recent_non_none_reported_by_the_decoder() -> None:
    buffers = make_varied_audio(5.0, chunk_seconds=1.0)
    decoder = FakeStreamingDecoder(
        ["a"] * 5,
        total_samples=total_samples(buffers),
        languages=["en", None, "es", None, None],
    )

    segments = run(decoder, buffers, window_seconds=5.0, chunk_seconds=1.0)

    # Passes report en, None, es, None, None; a later None must not erase the
    # language already seen, so the last non-None ("es") carries to the end.
    assert decoder.process_calls == 5
    assert [segment.language for segment in segments] == [
        "en",
        "en",
        "es",
        "es",
        "es",
        "es",
    ]
    assert segments[-1].is_final is True
    assert segments[-1].language == "es"


def test_later_language_replaces_an_earlier_one() -> None:
    buffers = make_varied_audio(2.0, chunk_seconds=1.0)
    decoder = FakeStreamingDecoder(
        ["a", "b"],
        total_samples=total_samples(buffers),
        languages=["en", "de"],
    )

    segments = run(decoder, buffers, window_seconds=5.0, chunk_seconds=1.0)

    assert [segment.language for segment in segments] == ["en", "de", "de"]
    assert segments[-1].language == "de"


def test_language_is_none_when_the_decoder_never_reports_one() -> None:
    buffers = make_varied_audio(3.0, chunk_seconds=1.0)
    decoder = FakeStreamingDecoder(
        ["a", "b", "c"], total_samples=total_samples(buffers)
    )

    segments = run(decoder, buffers, window_seconds=5.0, chunk_seconds=1.0)

    assert [segment.language for segment in segments] == [None] * 4
    assert segments[-1].language is None


# --------------------------------------------------------------------------- #
# Final segment and empty input
# --------------------------------------------------------------------------- #


def test_exactly_one_final_segment_is_yielded_last() -> None:
    buffers = make_varied_audio(7.0, chunk_seconds=1.0)
    decoder = FakeStreamingDecoder(WORDS_68, total_samples=total_samples(buffers))

    segments = run(decoder, buffers, window_seconds=5.0, chunk_seconds=1.0)

    finals = [segment for segment in segments if segment.is_final]
    assert len(finals) == 1
    assert segments[-1].is_final is True
    assert finals[0] is segments[-1]
    assert len(segments) == decoder.process_calls + 1


def test_empty_input_yields_one_empty_final_segment() -> None:
    decoder = FakeStreamingDecoder([], total_samples=SAMPLE_RATE)

    segments = run(decoder, [], window_seconds=5.0, chunk_seconds=1.0)

    assert len(segments) == 1
    assert segments[0].is_final is True
    assert segments[0].text == ""
    assert decoder.process_calls == 0
    assert decoder.flush_calls == 1


# --------------------------------------------------------------------------- #
# Laziness and guards
# --------------------------------------------------------------------------- #


def test_drive_is_lazy_until_iterated() -> None:
    buffers = make_varied_audio(3.0, chunk_seconds=1.0)
    decoder = FakeStreamingDecoder(["a", "b"], total_samples=total_samples(buffers))

    iterator = drive(decoder, buffers, window_seconds=5.0, chunk_seconds=1.0)

    assert isinstance(iterator, Iterator)
    assert decoder.process_calls == 0
    assert decoder.flush_calls == 0

    first = next(iterator)

    assert first.is_final is False
    assert decoder.process_calls == 1


@pytest.mark.parametrize("chunk_seconds", [0.0, -1.0])
def test_non_positive_chunk_seconds_is_rejected(chunk_seconds: float) -> None:
    decoder = FakeStreamingDecoder([], total_samples=SAMPLE_RATE)
    with pytest.raises(ValueError, match="chunk_seconds must be positive"):
        drive(decoder, [], window_seconds=5.0, chunk_seconds=chunk_seconds)


def test_window_smaller_than_chunk_is_rejected() -> None:
    decoder = FakeStreamingDecoder([], total_samples=SAMPLE_RATE)
    with pytest.raises(ValueError, match="window_seconds must be at least"):
        drive(decoder, [], window_seconds=0.5, chunk_seconds=1.0)


# --------------------------------------------------------------------------- #
# Error propagation
# --------------------------------------------------------------------------- #


def test_decoder_process_error_propagates_and_stops_iteration() -> None:
    buffers = make_varied_audio(3.0, chunk_seconds=1.0)
    decoder = FakeStreamingDecoder(["a", "b"], total_samples=total_samples(buffers))
    decoder.raise_on_process = RuntimeError("engine exploded")

    with pytest.raises(RuntimeError, match="engine exploded"):
        run(decoder, buffers, window_seconds=5.0, chunk_seconds=1.0)

    assert decoder.process_calls == 1
    assert decoder.flush_calls == 0


def test_boundary_flush_error_propagates_and_is_not_retried() -> None:
    buffers = make_varied_audio(7.0, chunk_seconds=1.0)
    decoder = FakeStreamingDecoder(["a", "b"], total_samples=total_samples(buffers))
    decoder.raise_on_flush = RuntimeError("flush exploded")

    with pytest.raises(RuntimeError, match="flush exploded"):
        run(decoder, buffers, window_seconds=5.0, chunk_seconds=1.0)

    # One boundary flush failed and the loop stopped; nothing retried it.
    assert decoder.process_calls == 5
    assert decoder.flush_calls == 1


def test_final_flush_error_propagates_and_is_not_retried() -> None:
    buffers = make_varied_audio(2.0, chunk_seconds=1.0)
    decoder = FakeStreamingDecoder(["a", "b"], total_samples=total_samples(buffers))
    decoder.raise_on_flush = RuntimeError("final flush exploded")

    with pytest.raises(RuntimeError, match="final flush exploded"):
        run(decoder, buffers, window_seconds=5.0, chunk_seconds=1.0)

    assert decoder.flush_calls == 1
