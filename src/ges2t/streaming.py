"""The generic windowed-commit streaming driver (ADR 0011).

The Whistle engine re-transcribes everything buffered on every streaming pass, so
an uncapped stream gets slower as the utterance grows and crosses the 1 s
per-chunk budget at about 6 s of buffer (ADR 0011, measured). The fix is to cap
the buffered window: feed chunks, and once the audio fed into the current stream
reaches ``window_seconds``, call the engine's ``stop()`` -- modelled here as
``StreamingDecoder.flush()`` -- which commits the unconfirmed tail and resets
the stream. The transcript is the concatenation of each pass's committed delta
plus each flush's tail.

This module is **pure logic and no I/O**: it takes an iterable of
:class:`~ges2t.interfaces.AudioBuffer` and yields
:class:`~ges2t.interfaces.TranscriptionSegment`. It imports no backend, no
audio library, no subprocess and no thread. The engine-specific
:class:`StreamingDecoder` is supplied by the adapter; tests supply a
deterministic fake.

Absolute timestamps are deliberately absent: the engine restarts its clock when
a stream resets, so offsetting its word times by the window start would silently
corrupt them (ADR 0011). Segments here therefore carry ``pass_ms=None``; timing
is the caller's concern. ``language`` is the most recent non-``None`` language
the decoder reported for the utterance (ADR 0011: language is whatever the
engine says), or ``None`` if it never reported one.

The window/chunk sizes are engine-specific configuration (ADR 0009, ADR 0011)
and are passed in; this module only sanity-checks them so a programming error
cannot become an infinite loop. The authoritative validation lives in
``ges2t/transcribers/whistle_config.py`` (ADR 0011).

This module also holds the **additive** pure-streaming sibling, ADR 0022's
:class:`AppendOnlyDecoder` + :func:`drive_append_only`. That driver serves a
cache-aware engine whose interims are append-only; it has **no window and no
flush**, and the windowed :func:`drive` and :class:`StreamingDecoder` above keep
their signatures and behaviour untouched (the Whistle path is unchanged).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ges2t.interfaces import AudioBuffer, LiveText, TranscriptionSegment

__all__ = [
    "AppendOnlyDecoder",
    "StreamingDecoder",
    "StreamingPass",
    "drive",
    "drive_append_only",
]


@dataclass(frozen=True)
class StreamingPass:
    """One engine pass over the buffered window (ADR 0019).

    ``committed`` is the text **this pass** newly committed -- the words the
    engine's two-consecutive-passes-agree rule confirmed -- and is appended to
    the running transcript. ``pending`` is the unconfirmed tail: display-only
    live text that is never part of the final transcript unless a later pass or
    a :meth:`StreamingDecoder.flush` commits it.

    ``language`` is the language the engine reported for this pass, or ``None``
    when it reported none. It is not necessarily the utterance's language: the
    driver keeps the most recent non-``None`` value across passes (ADR 0011).

    A frozen dataclass rather than a ``tuple[str, str]``: the two strings are
    easy to confuse positionally and the caller must not (ADR 0019).
    """

    committed: str
    pending: str
    language: str | None = None


@runtime_checkable
class StreamingDecoder(Protocol):
    """The narrow, engine-facing seam the driver drives (ADR 0010, ADR 0011).

    The driver never sees the engine, the audio device, or the clock. It only
    calls :meth:`process` with buffered audio and :meth:`flush` exactly at a
    window boundary and once at end of input. The concrete implementation lives
    in the engine adapter, which is the only place allowed to import the
    backend (ADR 0010).
    """

    def process(self, buffer: AudioBuffer) -> StreamingPass:
        """Append ``buffer`` to the current stream and return its pass result.

        The returned ``committed`` text is a delta: the words this pass agreed
        on, which the caller concatenates. ``pending`` is the tail that is not
        yet committed.
        """
        ...

    def flush(self) -> str:
        """Commit the unconfirmed tail, reset the stream, and return the tail text.

        This models the engine's ``stop()``: after it returns, the next
        :meth:`process` call begins a fresh stream whose timestamps start over.
        """
        ...


@runtime_checkable
class AppendOnlyDecoder(Protocol):
    """A cache-aware streaming engine that emits append-only interims (ADR 0022).

    This is the sibling of :class:`StreamingDecoder` for an engine that keeps its
    own caches and commits **append-only** frames, so the driver never resets it
    and never needs a window. The seam therefore has no ``flush``: a windowed
    engine's ``flush`` means *reset and continue*, while this engine has only one
    end, ``finish``, which is *end and stop*. Conflating the two on one loop is
    exactly the silent coupling ADR 0022 and report §6-C reject.

    The engine-specific implementation lives in the adapter, the only place
    allowed to import the backend (ADR 0010); tests supply a deterministic fake.
    """

    def push(self, buffer: AudioBuffer) -> StreamingPass:
        """Feed ``buffer`` to the engine and return this pass's result.

        ``committed`` is the delta this pass newly committed and is appended to
        the running transcript; ``pending`` is the display-only tail, never
        injected. The engine's own caches persist across calls, so no reset
        happens here (ADR 0022).
        """
        ...

    def finish(self) -> str:
        """End of input: flush the engine and return the **whole** final transcript.

        Called exactly once, at end of input, never mid-stream: there is no
        boundary at which a caches-aware engine needs to be reset (ADR 0022).
        The returned text extends every ``committed`` value the earlier
        :meth:`push` calls reported, so it is safe as the final, cumulative
        committed value the committed-delta path consumes.
        """
        ...


def drive(
    decoder: StreamingDecoder,
    chunks: Iterable[AudioBuffer],
    *,
    window_seconds: float,
    chunk_seconds: float,
    on_partial: Callable[[LiveText], None] | None = None,
) -> Iterator[TranscriptionSegment]:
    """Drive ``decoder`` over ``chunks``, yielding partials and one final segment.

    The algorithm is ADR 0011's, exactly:

    1. group the incoming audio into ``chunk_seconds``-sized
       :class:`~ges2t.interfaces.AudioBuffer` groups (audio is accumulated by
       sample count, so a short or long incoming buffer is split or padded
       correctly; a partial final group is flushed at the end, never dropped);
    2. feed each group to ``decoder.process()`` and append its ``committed``
       delta to the running transcript;
    3. once the samples fed since the last flush reach ``window_seconds``, call
       ``decoder.flush()``, append its return value, and continue on a fresh
       stream;
    4. after each group yield a non-final
       :class:`~ges2t.interfaces.TranscriptionSegment` whose ``live`` is the
       committed transcript so far plus the current stream's pending tail --
       display-only live text, never injected -- and pass the same value to
       ``on_partial`` when one is supplied;
    5. at end of input, ``flush()`` a final time and yield exactly one final
       segment (``is_final=True``) holding the whole accumulated transcript.

    The final text is the concatenation of every committed delta plus the final
    flush and is never reconstructed from ``pending`` (ADR 0011). The function
    returns a lazy iterator, so memory does not grow with utterance length; the
    guards below run eagerly at call time, so a bad argument raises immediately
    rather than on first iteration.

    ``window_seconds``/``chunk_seconds`` are only sanity-checked here (positive
    chunk, window at least one chunk); the authoritative validation -- the
    engine's 0.25 s floor, the 30 s per-call ceiling, the latency-knee warning
    -- lives in the Whistle config (ADR 0011).
    """
    if chunk_seconds <= 0:
        raise ValueError(f"chunk_seconds must be positive, got {chunk_seconds!r}")
    if window_seconds < chunk_seconds:
        raise ValueError(
            "window_seconds must be at least chunk_seconds, got "
            f"window_seconds={window_seconds!r} < chunk_seconds={chunk_seconds!r}"
        )
    return _drive(
        decoder,
        chunks,
        window_seconds=window_seconds,
        chunk_seconds=chunk_seconds,
        on_partial=on_partial,
    )


def _drive(
    decoder: StreamingDecoder,
    chunks: Iterable[AudioBuffer],
    *,
    window_seconds: float,
    chunk_seconds: float,
    on_partial: Callable[[LiveText], None] | None,
) -> Iterator[TranscriptionSegment]:
    """The generator behind :func:`drive`; arguments are already validated."""
    transcript = ""
    pending = ""
    # The engine detects the language per pass and an early pass may not have
    # reported one yet, so a later ``None`` must not erase a language already
    # seen: track the most recent non-``None`` value and carry it forward, onto
    # the final segment too (ADR 0011: the language is taken from the final
    # committed pass).
    language: str | None = None
    fed_samples = 0
    for group in _group_chunks(chunks, chunk_seconds=chunk_seconds):
        pass_ = decoder.process(group)
        transcript += pass_.committed
        pending = pass_.pending
        if pass_.language is not None:
            language = pass_.language

        # Sample-based accounting, never chunk counting: the boundary must fire
        # on the real duration even when the final group is short or long.
        fed_samples += len(group.samples)
        if fed_samples >= window_seconds * group.sample_rate:
            transcript += decoder.flush()
            # The flush committed this stream's tail, so nothing is pending in
            # the fresh stream until its first process() call.
            pending = ""
            fed_samples = 0

        # ``transcript`` is the cumulative committed prefix (append-only) and
        # ``pending`` is the current stream's replaced unconfirmed tail. They
        # are carried to the consumer separately: merging them here would
        # destroy the boundary and make the committed prefix unrecoverable
        # (ticket ``committed-delta-interface``).
        live = LiveText(committed=transcript, pending=pending)
        if on_partial is not None:
            on_partial(live)
        yield TranscriptionSegment(
            live=live, language=language, is_final=False, pass_ms=None
        )

    # A final flush even when the last group landed exactly on a boundary: the
    # new stream has nothing pending, so it contributes the empty string, and
    # the segment count stays deterministic. The final segment is cumulative:
    # ``committed`` is the whole transcript and there is no pending tail.
    transcript += decoder.flush()
    yield TranscriptionSegment(
        live=LiveText(committed=transcript, pending=""),
        language=language,
        is_final=True,
        pass_ms=None,
    )


def drive_append_only(
    decoder: AppendOnlyDecoder,
    chunks: Iterable[AudioBuffer],
    *,
    chunk_seconds: float,
    on_partial: Callable[[LiveText], None] | None = None,
) -> Iterator[TranscriptionSegment]:
    """Fold an append-only interim stream into the committed-delta contract (ADR 0022).

    This is the pure-streaming sibling of :func:`drive` for a cache-aware engine
    whose interims only ever extend the previous one (ticket 55 measured the
    property; report §7). Because the engine does that work itself there is **no
    window and no flush**: ``decoder.finish()`` is called **exactly once**, at end
    of input, and contributes the whole final transcript. The rest of the fold is
    identical to :func:`drive`, so the session and the service -- which only see
    the resulting :class:`~ges2t.interfaces.TranscriptionSegment` values -- are
    unchanged and window-blind (ADR 0011, report §1).

    1. regroup the incoming audio into ``chunk_seconds``-sized groups by sample
       count, reusing :func:`_group_chunks` exactly as the windowed path does (a
       short or long incoming buffer is split or combined; the partial final
       group is delivered, never dropped);
    2. feed each group to ``decoder.push()`` and append its ``committed`` delta
       to the running transcript;
    3. yield one non-final segment per group whose ``live`` is the cumulative
       committed transcript plus the current pending tail -- display-only, never
       injected -- and pass the same value to ``on_partial`` when one is supplied;
    4. at end of input, call ``decoder.finish()`` once and yield exactly one final
       segment (``is_final=True``) whose ``committed`` is the whole final
       transcript and whose ``pending`` is empty.

    The final text is the decoder's own end-of-stream transcript, never
    reconstructed from ``pending``; the committed sequence stays cumulative and
    append-only because ``finish`` extends every earlier ``committed`` value. The
    function returns a lazy iterator, so the guards below run eagerly at call time
    and a bad argument raises immediately rather than on first iteration.

    ``chunk_seconds`` is only sanity-checked here (positive); the authoritative
    validation lives in the engine's config block (report §7.1). The engine's own
    decoding step (its ``chunk_size``) is a model property the adapter does not
    set here -- ``chunk_seconds`` is ges2t's feed/poll granularity.
    """
    if chunk_seconds <= 0:
        raise ValueError(f"chunk_seconds must be positive, got {chunk_seconds!r}")
    return _drive_append_only(
        decoder,
        chunks,
        chunk_seconds=chunk_seconds,
        on_partial=on_partial,
    )


def _drive_append_only(
    decoder: AppendOnlyDecoder,
    chunks: Iterable[AudioBuffer],
    *,
    chunk_seconds: float,
    on_partial: Callable[[LiveText], None] | None,
) -> Iterator[TranscriptionSegment]:
    """The generator behind :func:`drive_append_only`; arguments already validated."""
    transcript = ""
    pending = ""
    # The engine reports the language per result and an early pass may not have
    # one yet, so a later ``None`` must not erase a language already seen: track
    # the most recent non-``None`` value and carry it forward, onto the final
    # segment too (ADR 0011, and the same rule ``drive`` applies).
    language: str | None = None
    for group in _group_chunks(chunks, chunk_seconds=chunk_seconds):
        pass_ = decoder.push(group)
        transcript += pass_.committed
        pending = pass_.pending
        if pass_.language is not None:
            language = pass_.language

        # No sample accounting and no boundary test: this engine has no window,
        # so the loop has no condition under which it would reset the decoder
        # (ADR 0022). The committed prefix stays cumulative and append-only; the
        # pending tail is carried separately and is never merged into it.
        live = LiveText(committed=transcript, pending=pending)
        if on_partial is not None:
            on_partial(live)
        yield TranscriptionSegment(
            live=live, language=language, is_final=False, pass_ms=None
        )

    # The one and only end: no window boundary flush exists, and this call is
    # unconditional -- even for empty input -- so ``finish()`` runs exactly once
    # and the segment count stays deterministic. ``finish()`` returns the whole
    # final transcript, so the final segment is cumulative with no pending tail.
    final_text = decoder.finish()
    yield TranscriptionSegment(
        live=LiveText(committed=final_text, pending=""),
        language=language,
        is_final=True,
        pass_ms=None,
    )


def _group_chunks(
    chunks: Iterable[AudioBuffer], *, chunk_seconds: float
) -> Iterator[AudioBuffer]:
    """Regroup ``chunks`` into consecutive ``chunk_seconds``-sized buffers.

    Audio is accumulated by sample count, so incoming buffers that are shorter
    or longer than ``chunk_seconds`` are combined or split; the leftover tail is
    emitted as a final shorter group by :func:`drive`'s caller loop when the
    input ends. All buffers in one stream must share a sample rate, which the
    16 kHz contract guarantees (ADR 0005); a mismatch would corrupt duration
    accounting, so it is a loud error rather than silent concatenation.
    """
    accumulator: list[float] = []
    sample_rate: int | None = None
    for incoming in chunks:
        if sample_rate is None:
            sample_rate = incoming.sample_rate
        elif incoming.sample_rate != sample_rate:
            raise ValueError(
                "all buffers in a stream must share one sample rate, got "
                f"{sample_rate} and {incoming.sample_rate}"
            )
        accumulator.extend(incoming.samples)

        target_samples = _target_samples(chunk_seconds, sample_rate)
        while len(accumulator) >= target_samples:
            yield AudioBuffer(
                samples=accumulator[:target_samples], sample_rate=sample_rate
            )
            del accumulator[:target_samples]

    if accumulator:
        # sample_rate is set whenever accumulator is non-empty.
        assert sample_rate is not None
        yield AudioBuffer(samples=accumulator, sample_rate=sample_rate)


def _target_samples(chunk_seconds: float, sample_rate: int) -> int:
    """Number of samples in one ``chunk_seconds`` group at ``sample_rate``."""
    return max(1, round(chunk_seconds * sample_rate))
