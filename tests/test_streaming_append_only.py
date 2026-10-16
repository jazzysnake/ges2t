"""Tests for the append-only pure-streaming driver (ADR 0022, ticket 59).

Everything here runs against a deterministic fake
:class:`AppendOnlyDecoder` defined in this file; no engine, audio device, or
clock is involved. The fake models a cache-aware engine whose interims only ever
extend the previous one: each pass reveals more words and keeps the newest one
``pending``, and ``finish()`` returns the whole utterance.

What the file pins, per the ticket's definition of done:

* **no window flush** -- the driver has no ``window_seconds`` and there is no
  call to the decoder between the last ``push`` and ``finish``;
* ``finish()`` is called **exactly once**, at end of input (even for empty input);
* exactly **one final segment**, and the committed values it and the partials
  carry are cumulative and append-only;
* the ``pending`` tail is display-only and **never** reaches the transcript.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Sequence

import pytest

from ges2t.interfaces import AudioBuffer, LiveText, TranscriptionSegment
from ges2t.streaming import AppendOnlyDecoder, StreamingPass, drive_append_only

SAMPLE_RATE = 16000

#: A distinct, easy-to-count token per word, so a duplicated token is detectable.
WORDS_68: list[str] = [f"w{i}" for i in range(68)]


class FakeAppendOnlyDecoder:
    """A deterministic ``AppendOnlyDecoder`` with a known-perfect transcript.

    ``words`` is the whole utterance and ``total_samples`` the exact number of
    samples that will be fed. A pass reveals ``floor(fed * len(words) /
    total_samples)`` words in total; the newest revealed word stays ``pending``
    and everything before it is the ``committed`` delta, so the word is committed
    only once a later pass exists -- the engine's append-only cadence, one word
    behind. ``finish()`` returns the whole utterance.

    The fake records the order and count of every call, which is how "no window
    flush, ``finish`` exactly once at the end" is asserted without the driver
    ever seeing a window. The deltas carry their own leading separator space (the
    convention a real engine must follow, because the driver concatenates the
    ``committed`` values verbatim), so ``committed + pending`` is always a clean
    prefix of the utterance.
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
        self.push_calls = 0
        self.finish_calls = 0
        #: The exact call sequence, e.g. ``["push", "push", "finish"]``.
        self.events: list[str] = []
        #: Samples received by each ``push()`` call, in order.
        self.pushed_samples: list[int] = []
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

    def push(self, buffer: AudioBuffer) -> StreamingPass:
        self.push_calls += 1
        self.events.append("push")
        self.pushed_samples.append(len(buffer.samples))
        self._fed_samples += len(buffer.samples)
        available = min(
            len(self._words),
            self._fed_samples * len(self._words) // self._total_samples,
        )
        # The engine commits everything before the newest revealed word and keeps
        # that word pending: a word is committed only once the next word starts.
        commit_end = max(self._committed, available - 1)
        committed = self._prefix(commit_end)[len(self._prefix(self._committed)) :]
        pending = self._prefix(available)[len(self._prefix(commit_end)) :]
        self._committed = commit_end
        self._available = available
        language = (
            self._languages[self.push_calls - 1]
            if self.push_calls - 1 < len(self._languages)
            else None
        )
        return StreamingPass(committed=committed, pending=pending, language=language)

    def finish(self) -> str:
        self.finish_calls += 1
        self.events.append("finish")
        return self._prefix(len(self._words))


class PendingPoisonAppendOnlyDecoder:
    """A decoder whose ``pending`` is garbage that must never reach the transcript.

    ``committed`` and ``finish`` carry the real text; ``pending`` is a poison
    token. If the driver ever merged the pending tail into the committed prefix
    or built the final segment from it, the poison would show up.
    """

    def __init__(self) -> None:
        self.push_calls = 0
        self.finish_calls = 0

    def push(self, buffer: AudioBuffer) -> StreamingPass:
        self.push_calls += 1
        return StreamingPass(committed=f"committed{self.push_calls} ", pending="POISON")

    def finish(self) -> str:
        self.finish_calls += 1
        return "committed1 committed2 FINAL"


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
    decoder: AppendOnlyDecoder,
    buffers: Sequence[AudioBuffer],
    *,
    chunk_seconds: float,
    on_partial: Callable[[LiveText], None] | None = None,
) -> list[TranscriptionSegment]:
    """Consume ``drive_append_only`` fully and return every yielded segment."""
    return list(
        drive_append_only(
            decoder,
            buffers,
            chunk_seconds=chunk_seconds,
            on_partial=on_partial,
        )
    )


# --------------------------------------------------------------------------- #
# No window, one end
# --------------------------------------------------------------------------- #


def test_the_driver_has_no_window_parameter() -> None:
    parameters = inspect.signature(drive_append_only).parameters

    assert list(parameters) == ["decoder", "chunks", "chunk_seconds", "on_partial"]
    assert "window_seconds" not in parameters


def test_finish_is_called_exactly_once_at_the_very_end() -> None:
    buffers = make_varied_audio(12.0, chunk_seconds=1.0)
    decoder = FakeAppendOnlyDecoder(WORDS_68, total_samples=total_samples(buffers))

    run(decoder, buffers, chunk_seconds=1.0)

    # Twelve pushes, then the single end-of-input finish: no boundary flush in
    # between is possible -- and none happened.
    assert decoder.events == ["push"] * 12 + ["finish"]
    assert decoder.finish_calls == 1


def test_empty_input_still_calls_finish_once_and_yields_one_empty_final() -> None:
    decoder = FakeAppendOnlyDecoder([], total_samples=0)

    segments = run(decoder, [], chunk_seconds=1.0)

    assert decoder.events == ["finish"]
    assert len(segments) == 1
    assert segments[0].is_final is True
    assert segments[0].text == ""


def test_exactly_one_final_segment_and_it_is_last() -> None:
    buffers = make_varied_audio(8.0, chunk_seconds=1.0)
    decoder = FakeAppendOnlyDecoder(WORDS_68, total_samples=total_samples(buffers))

    segments = run(decoder, buffers, chunk_seconds=1.0)

    assert len(segments) == 9
    assert [segment.is_final for segment in segments] == [False] * 8 + [True]


def test_a_long_incoming_buffer_is_split_into_chunk_seconds_groups() -> None:
    buffers = make_varied_audio(12.0, chunk_seconds=1.0)
    decoder = FakeAppendOnlyDecoder(WORDS_68, total_samples=total_samples(buffers))
    one_long = AudioBuffer(samples=[0.0] * (12 * SAMPLE_RATE), sample_rate=SAMPLE_RATE)

    run(decoder, [one_long], chunk_seconds=1.0)

    assert decoder.pushed_samples == [SAMPLE_RATE] * 12


def test_short_incoming_buffers_are_combined_into_groups() -> None:
    buffers = make_varied_audio(5.0, chunk_seconds=0.25)
    decoder = FakeAppendOnlyDecoder(["a", "b"], total_samples=total_samples(buffers))

    run(decoder, buffers, chunk_seconds=1.0)

    assert decoder.pushed_samples == [SAMPLE_RATE] * 5


# --------------------------------------------------------------------------- #
# The committed/pending split
# --------------------------------------------------------------------------- #


def test_committed_is_cumulative_and_append_only_across_segments() -> None:
    buffers = make_varied_audio(12.0, chunk_seconds=1.0)
    decoder = FakeAppendOnlyDecoder(WORDS_68, total_samples=total_samples(buffers))

    segments = run(decoder, buffers, chunk_seconds=1.0)

    previous = ""
    for segment in segments:
        committed = segment.live.committed
        assert committed.startswith(previous), (previous, committed)
        assert len(committed) >= len(previous)
        previous = committed
    assert segments[-1].live.committed == " ".join(WORDS_68)


def test_final_segment_is_cumulative_and_reaches_the_whole_transcript() -> None:
    buffers = make_varied_audio(12.0, chunk_seconds=1.0)
    decoder = FakeAppendOnlyDecoder(WORDS_68, total_samples=total_samples(buffers))

    segments = run(decoder, buffers, chunk_seconds=1.0)
    final = segments[-1]

    assert final.is_final is True
    assert final.live.pending == ""
    assert final.live.committed == final.text == " ".join(WORDS_68)

    # The whole transcript is exactly the concatenation of the per-pass committed
    # deltas the cumulative sequence encodes -- every word once.
    deltas: list[str] = []
    previous = ""
    for segment in segments:
        assert segment.live.committed.startswith(previous)
        deltas.append(segment.live.committed[len(previous) :])
        previous = segment.live.committed
    assert "".join(deltas) == final.text


def test_pending_is_displayed_but_never_committed() -> None:
    buffers = make_varied_audio(12.0, chunk_seconds=1.0)
    decoder = FakeAppendOnlyDecoder(WORDS_68, total_samples=total_samples(buffers))

    segments = run(decoder, buffers, chunk_seconds=1.0)

    with_pending = [segment for segment in segments if segment.live.pending]
    assert with_pending
    # The display string is committed + pending, but the committed prefix is
    # strictly shorter whenever there is a speculative tail.
    for segment in segments:
        assert segment.text == segment.live.committed + segment.live.pending
    assert all(segment.live.committed != segment.text for segment in with_pending)


def test_pending_never_reaches_the_transcript() -> None:
    buffers = make_varied_audio(3.0, chunk_seconds=1.0)
    decoder = PendingPoisonAppendOnlyDecoder()

    segments = run(decoder, buffers, chunk_seconds=1.0)

    assert all("POISON" not in segment.live.committed for segment in segments)
    assert "POISON" not in segments[-1].text
    assert segments[-1].text == "committed1 committed2 FINAL"
    # ...while the live partials definitely did show the pending text.
    assert "POISON" in segments[0].text


# --------------------------------------------------------------------------- #
# Plumbing
# --------------------------------------------------------------------------- #


def test_on_partial_receives_exactly_the_yielded_live_values() -> None:
    buffers = make_varied_audio(6.0, chunk_seconds=1.0)
    decoder = FakeAppendOnlyDecoder(WORDS_68, total_samples=total_samples(buffers))
    seen: list[LiveText] = []

    segments = run(decoder, buffers, chunk_seconds=1.0, on_partial=seen.append)

    assert [live for live in seen] == [
        segment.live for segment in segments if not segment.is_final
    ]


def test_language_is_carried_forward_onto_the_final_segment() -> None:
    buffers = make_varied_audio(4.0, chunk_seconds=1.0)
    languages: list[str | None] = [None, "de", None, None]
    decoder = FakeAppendOnlyDecoder(
        ["a", "b", "c", "d"],
        total_samples=total_samples(buffers),
        languages=languages,
    )

    segments = run(decoder, buffers, chunk_seconds=1.0)

    assert segments[0].language is None
    assert segments[1].language == "de"
    # A later pass reporting None does not erase the language already seen.
    assert segments[-1].language == "de"


def test_chunk_seconds_must_be_positive() -> None:
    decoder = FakeAppendOnlyDecoder([], total_samples=0)

    with pytest.raises(ValueError, match="chunk_seconds must be positive"):
        drive_append_only(decoder, [], chunk_seconds=0.0)


def test_a_buffer_that_is_not_the_stream_rate_is_rejected_by_grouping() -> None:
    decoder = FakeAppendOnlyDecoder([], total_samples=0)
    buffers: list[AudioBuffer] = [
        AudioBuffer(samples=[0.0] * 100, sample_rate=SAMPLE_RATE),
        AudioBuffer(samples=[0.0] * 100, sample_rate=8000),
    ]

    with pytest.raises(ValueError, match="share one sample rate"):
        run(decoder, buffers, chunk_seconds=1.0)
