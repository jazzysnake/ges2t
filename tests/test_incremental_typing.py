"""The typed watermark and its reconciliation against ``finish()`` (ticket 34).

Everything here is deterministic and headless: the raw typing seam is a recording
stub, so no test emits a keystroke, touches the clipboard, or records audio
(ADR 0014). The interesting property is **exactly-once**: the committed prefix is
cumulative and append-only (ticket 33), the session dedups partials on display,
and the last collapsed advance is only visible in the final transcript -- so a
consumer that diffs each committed value and then reconciles against ``finish()``
types every character once, and one that only iterates ``partials()`` silently
loses the tail.
"""

from __future__ import annotations

import pytest

from ges2t.incremental_typing import CommittedTyper, IncrementalTypingError
from ges2t.injectors.ydotool import TypeText, decide_injection
from ges2t.streaming import drive
from tests.test_streaming import (
    WORDS_68,
    FakeStreamingDecoder,
    make_varied_audio,
    total_samples,
)


class RecordingTyper:
    """A :class:`~ges2t.interfaces.TextTyper` that records the exact payloads.

    ``error`` makes the next :meth:`type_text` raise, so a test can prove a
    delivery failure is latched rather than swallowed.
    """

    def __init__(self) -> None:
        self.typed: list[str] = []
        self.error: Exception | None = None

    def type_text(self, text: str) -> None:
        if self.error is not None:
            raise self.error
        self.typed.append(text)


# -- exactly once --------------------------------------------------------------


def test_types_each_committed_character_exactly_once() -> None:
    raw = RecordingTyper()
    typer = CommittedTyper(raw, trailing_space=False)

    for committed in ["a", "ab", "abc"]:
        typer.type_committed(committed)
    typer.finish("abc")

    assert raw.typed == ["a", "b", "c"]
    assert typer.typed == "abc"


def test_repeated_cumulative_values_are_not_retried() -> None:
    # The session dedups on display, so the same committed value can arrive on a
    # later pass (or be re-reported); a watermark, not a delta sum, is what keeps
    # it exactly once (ticket 33).
    raw = RecordingTyper()
    typer = CommittedTyper(raw, trailing_space=False)

    for committed in ["abc", "abc", "abcd", "abcd"]:
        typer.type_committed(committed)
    typer.finish("abcd")

    assert raw.typed == ["abc", "d"]
    assert typer.typed == "abcd"


def test_a_change_confined_to_pending_types_nothing() -> None:
    # Only the committed value is ever typed; the pending tail is revised before
    # commit and is where the engine's hallucinations live (the `Thank you.`
    # hallucination never committed, ticket 32).
    raw = RecordingTyper()
    typer = CommittedTyper(raw, trailing_space=False)

    typer.type_committed("confirmed")
    typer.type_committed("confirmed")  # pending changed, committed did not
    typer.finish("confirmed")

    assert raw.typed == ["confirmed"]


# -- the collapsed advance from ticket 33's review -----------------------------


def test_a_collapsed_advance_is_recovered_from_finish() -> None:
    # partials() dedups on display: pass 1 committed "abc", pending "def"; pass 2
    # committed "abcdef", pending "" has the same display and is dropped. Only
    # the final transcript carries "def", so finish() must reconcile.
    raw = RecordingTyper()
    typer = CommittedTyper(raw, trailing_space=False)

    typer.type_committed("abc")
    typer.finish("abcdef")

    assert raw.typed == ["abc", "def"]
    assert typer.typed == "abcdef"


def test_the_final_segment_is_not_typed_in_full_after_the_partials() -> None:
    # The naive "type each delta, then type the final segment" duplicates the
    # whole utterance; the watermark types only the suffix.
    raw = RecordingTyper()
    typer = CommittedTyper(raw, trailing_space=True)

    typer.type_committed("abc")
    typer.finish("abcdef")

    assert raw.typed == ["abc", "def "]  # NOT ["abc", "abcdef "]
    assert typer.typed == "abcdef "
    assert typer.typed.count("abcdef") == 1


def test_a_late_partial_after_finish_is_a_noop() -> None:
    # The service's stop path does not join the partials forwarder, so finish()
    # can run first. A late committed value must not be typed or latch an error.
    raw = RecordingTyper()
    typer = CommittedTyper(raw, trailing_space=False)

    typer.finish("abcdef")
    typer.type_committed("abc")
    # A second finish is legitimate (idempotent) and must not surface the late
    # partial as a spurious non-append-only error.
    typer.finish("abcdef")

    assert raw.typed == ["abcdef"]
    assert typer.typed == "abcdef"


# -- the byte-identical document ----------------------------------------------


@pytest.mark.parametrize("trailing_space", [True, False])
def test_typed_document_is_byte_identical_to_the_batch_injection(
    trailing_space: bool,
) -> None:
    # The load-bearing acceptance criterion: the incrementally typed document is
    # exactly what the batch path's single end-of-utterance injection produces,
    # including exactly one trailing space when ui.trailing_space is enabled
    # (ADR 0012).
    final_text = "Good morning. Last night storm"
    raw = RecordingTyper()
    typer = CommittedTyper(raw, trailing_space=trailing_space)

    for committed in [
        "Good morning.",
        "Good morning. Last",
        "Good morning. Last night storm",
    ]:
        typer.type_committed(committed)
    typer.finish(final_text)

    document = decide_injection(final_text, trailing_space=trailing_space)
    assert isinstance(document, TypeText)
    assert typer.typed == document.payload
    if trailing_space:
        assert typer.typed == final_text + " "
        assert not typer.typed.endswith("  ")


def test_realistic_deltas_do_not_get_a_space_per_commit() -> None:
    # The engine's later deltas already carry their leading separator, so the
    # trailing-space rule must not run per commit. The whole-utterance rule gives
    # single interior separators and exactly one trailing space.
    raw = RecordingTyper()
    typer = CommittedTyper(raw, trailing_space=True)

    for committed in ["Good morning.", "Good morning. Last", "Good morning. Last night storm"]:
        typer.type_committed(committed)
    typer.finish("Good morning. Last night storm")

    assert typer.typed == "Good morning. Last night storm "
    assert "  " not in typer.typed
    assert raw.typed == ["Good morning.", " Last", " night storm", " "]


def test_a_final_remainder_of_only_the_trailing_space_is_still_typed() -> None:
    # The watermark already reached the whole transcript when finish() runs; the
    # single trailing space must still be typed (it is part of the document, not
    # a whitespace-only utterance).
    raw = RecordingTyper()
    typer = CommittedTyper(raw, trailing_space=True)

    typer.type_committed("abcdef")
    typer.finish("abcdef")

    assert raw.typed == ["abcdef", " "]
    assert typer.typed == "abcdef "


def test_whitespace_only_final_utterance_types_nothing() -> None:
    # Silence in, nothing out (ADR 0012), even with the trailing space enabled.
    raw = RecordingTyper()
    typer = CommittedTyper(raw, trailing_space=True)

    typer.type_committed("")
    typer.finish("   ")

    assert raw.typed == []
    assert typer.typed == ""


# -- idempotence and failure surfacing ----------------------------------------


def test_finish_is_idempotent() -> None:
    raw = RecordingTyper()
    typer = CommittedTyper(raw, trailing_space=True)

    typer.type_committed("ab")
    typer.finish("ab")
    typer.finish("ab")

    assert raw.typed == ["ab", " "]
    assert typer.typed == "ab "


def test_a_non_append_only_value_stops_typing_and_finish_raises() -> None:
    # LiveText.committed is append-only (ticket 33); a value that is not an
    # extension means typing cannot continue without a backspace, which the
    # design forbids.
    raw = RecordingTyper()
    typer = CommittedTyper(raw, trailing_space=False)

    typer.type_committed("abc")
    typer.type_committed("abx")

    assert raw.typed == ["abc"]
    with pytest.raises(IncrementalTypingError):
        typer.finish("abx")


def test_a_delivery_failure_is_latched_and_reraised_by_finish() -> None:
    # type_committed runs on the display thread, whose broad except would swallow
    # an exception; latching it and re-raising in finish() puts it back on the
    # service's error path.
    raw = RecordingTyper()
    raw.error = RuntimeError("ydotoold is not reachable")
    typer = CommittedTyper(raw, trailing_space=False)

    typer.type_committed("abc")  # must not raise

    with pytest.raises(RuntimeError, match="ydotoold is not reachable"):
        typer.finish("abc")
    assert raw.typed == []
    assert typer.typed == ""


# -- the real drive() fold -----------------------------------------------------


def test_drive_output_reconciles_to_the_batch_document() -> None:
    # End to end over the shipped fold: the committed values drive() emits,
    # plus the final segment's committed value, produce exactly the batch
    # document -- every word once and one trailing space.
    buffers = make_varied_audio(12.0, chunk_seconds=1.0)
    decoder = FakeStreamingDecoder(WORDS_68, total_samples=total_samples(buffers))
    segments = list(
        drive(decoder, buffers, window_seconds=5.0, chunk_seconds=1.0)
    )

    raw = RecordingTyper()
    typer = CommittedTyper(raw, trailing_space=True)
    for segment in segments[:-1]:
        typer.type_committed(segment.live.committed)
    final_text = segments[-1].live.committed
    typer.finish(final_text)

    expected = decide_injection(final_text, trailing_space=True)
    assert isinstance(expected, TypeText)
    assert typer.typed == expected.payload
    assert typer.typed == " ".join(WORDS_68) + " "
