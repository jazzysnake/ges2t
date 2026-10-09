#!/usr/bin/env python3
"""Commit-cadence diagnostic: the measurement gate for incremental typing (ticket 32).

This script is a **diagnostic**, not part of the shipped package. It is deliberately
outside ``src/ges2t``: ``uv run pytest`` must stay headless and engine-free (ADR 0014),
so nothing here is imported by the package or by the test suite.

What it measures
----------------
``ges2t.streaming.drive`` builds the transcript with ``transcript += pass_.committed``,
so every intermediate value of ``transcript`` is a literal prefix of the final transcript
*provided the engine's committed deltas are themselves append-only*. The engine is
closed-source (ADR 0006), so that is currently the ADR's assertion and has never been
measured on real speech. This script drives the **real** engine through the **shipped**
adapter (``WhistleTranscriber.stream`` -> ``streaming.drive`` -> ``_WhistleStreamingDecoder``)
and reports:

1. the append-only property over every intermediate transcript snapshot;
2. the commit cadence: how often ``committed`` becomes non-empty, how many characters each
   commit carries, and the seconds between commits;
3. the ``pending`` (uncommitted, display-only) size and how long it persists;
4. any observed restatement, with the before/after text **verbatim**.

It ends with a recommendation and the injection cadence the measurements support.

How it observes ``transcript``
-----------------------------
``drive()`` is a pure fold over the decoder's return values, and that fold is shipped, so
the script does not re-implement it. Instead it temporarily replaces the adapter's decoder
class (``whistle._WhistleStreamingDecoder``) with a recording subclass for the duration of
one run: the shipped ``stream()`` body, the shipped gate, the shipped ``drive()`` and the
shipped decoder all run unmodified, and the subclass only records what crosses the seam.
Because ``transcript`` is exactly the prefix sum of those recorded deltas, the snapshots
are read straight off the shipped fold.

Honesty note: the *prefix* check on a concatenation is structurally guaranteed (every
prefix of a concatenation is a prefix of it) and therefore can almost never fail; the
check that can genuinely fail, and that decides the feature, is whether the engine's
committed deltas are **new** text. That is measured two independent ways:

* a committed-word overlap scan (does a later commit repeat words already committed?);
* a word-level diff of the final streaming transcript against a one-shot batch
  transcription of the same audio through the same adapter.

The user-owned capture step
---------------------------
Any fresh recording uses the user's microphone and **only the user may run it**, because
the agent cannot tell the user when to start. Run this script with ``--capture`` (or with
no arguments) in your own terminal. It prints the device, waits for Enter, counts 3-2-1,
prints ``● RECORDING — speak now`` with a visible seconds counter, records for a fixed
duration, prints ``■ stopped``, and writes the full report to a file as well as stdout.
The script refuses to record when stdin is not a terminal, so it can never capture
anything from an automated invocation. Audio is written under ``/tmp`` only and is never
committed.

Usage
-----
    # the shipped clips (no microphone needed)
    uv run python docs/project/live-dictation-ux/commit-cadence-diagnostic/measure_commit_cadence.py \
        --clips /tmp/whinspect

    # the harness validating its own restatement detector (no engine, no device)
    uv run python docs/project/live-dictation-ux/commit-cadence-diagnostic/measure_commit_cadence.py \
        --self-test

    # the user-owned fresh recording, optionally with the text read aloud
    uv run python docs/project/live-dictation-ux/commit-cadence-diagnostic/measure_commit_cadence.py \
        --capture --seconds 25 --reference /tmp/passage.txt

Design rules honoured here: full type hints including ``-> None``, Protocol-typed
parameters, and classes rather than positional tuples (ADR 0019).
"""

from __future__ import annotations

import argparse
import difflib
import sys
import time
import wave
from array import array
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ges2t import streaming
from ges2t.audio.pw_record import SAMPLE_RATE
from ges2t.audio.pw_record import PROGRAM as PW_RECORD_PROGRAM
from ges2t.audio.pw_record import PwRecordAudioSource, PwRecordError
from ges2t.config import Config, load_config
from ges2t.interfaces import AudioBuffer
from ges2t.transcribers import whistle
from ges2t.transcribers.whistle import WhistleTranscriber
from ges2t.transcribers.whistle_config import WhistleConfig

__all__ = ["main"]

#: The fixed capture duration the script asks the user for. Long enough to cross the 5 s
#: window several times; the ticket asks for ~25 s.
DEFAULT_CAPTURE_SECONDS: float = 25.0

#: Where the previous inspection tickets left the shipped reference clips. ``clip30.wav``
#: is 30 s, so it crosses the 5 s window six times and exercises ``flush()`` on its own.
DEFAULT_CLIP_DIR: Path = Path("/tmp/whinspect")

#: The shipped clips this script knows how to measure, in order.
SHIPPED_CLIPS: tuple[str, ...] = ("clip5.wav", "clip30.wav")

#: Directory for the report file and the (never-committed) capture WAV.
TMP_DIR: Path = Path("/tmp")

#: How many characters of a delta / pending string to show in the per-pass table.
DELTA_PREVIEW_CHARS: int = 70
PENDING_PREVIEW_CHARS: int = 50


# --------------------------------------------------------------------------------------
# The measurement records
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PassEvent:
    """One decoder call, with the text it returned and where in the audio it happened.

    ``source_seconds`` is the position in the *original* recording: for a ``process``
    call it is the start of the chunk the engine was handed, for a ``flush`` call it is
    the end of the last chunk fed into that window. It is reconstructed from the shipped
    silence gate's per-chunk verdict (see :func:`gated_timeline`), so a silence-dropped
    chunk still advances it. ``engine_seconds`` is the cumulative audio actually fed to
    the engine, which differs from ``source_seconds`` exactly by the gated silence.

    ``wall_seconds`` is the wall-clock time of the call's completion relative to a
    per-run origin, so gaps between commits can be read in real time as well.
    """

    kind: str
    source_seconds: float
    engine_seconds: float
    wall_seconds: float
    committed: str
    pending: str
    language: str | None

    @property
    def is_commit(self) -> bool:
        """Whether this event carried new committed text (a commit happened)."""
        return self.committed != ""


@dataclass(frozen=True)
class UtteranceMeasurement:
    """Everything measured for one input (a clip, a WAV file, or a live capture)."""

    label: str
    path: Path | None
    duration_seconds: float
    fed_seconds: float
    window_seconds: float
    chunk_seconds: float
    events: tuple[PassEvent, ...]
    snapshots: tuple[str, ...]
    partial_texts: tuple[str, ...]
    final_text: str
    final_language: str | None
    batch_reference: str | None
    reference_text: str | None
    timeline_from_gate: bool

    @property
    def commits(self) -> tuple[PassEvent, ...]:
        """The events that carried new committed text, in order."""
        return tuple(event for event in self.events if event.is_commit)

    @property
    def flush_events(self) -> tuple[PassEvent, ...]:
        """The ``flush()`` calls, which are the window boundaries."""
        return tuple(event for event in self.events if event.kind == "flush")


@dataclass(frozen=True)
class PrefixCheck:
    """The result of the append-only (prefix) check over one utterance."""

    checked: int
    failures: tuple[str, ...]

    @property
    def ok(self) -> bool:
        """Whether every intermediate snapshot was a prefix of the final transcript."""
        return not self.failures


@dataclass(frozen=True)
class Overlap:
    """A committed delta whose leading words repeat words already committed."""

    overlap_words: int
    source_seconds: float
    before: str
    delta: str


@dataclass(frozen=True)
class WordDiff:
    """A word-level diff between a reference text and a hypothesis text."""

    substitutions: int
    insertions: int
    deletions: int
    reference_words: int

    @property
    def errors(self) -> int:
        """Total edit operations (substitutions + insertions + deletions)."""
        return self.substitutions + self.insertions + self.deletions

    @property
    def wer(self) -> float:
        """Word error rate against the reference (0.0 when the reference is empty)."""
        if self.reference_words == 0:
            return 0.0
        return self.errors / self.reference_words


@dataclass(frozen=True)
class TokenSpan:
    """One whitespace-delimited token of a text, with its normalised word indices."""

    raw: str
    normalised: tuple[str, ...]
    start: int

    @property
    def end(self) -> int:
        """Index one past this token's last normalised word."""
        return self.start + len(self.normalised)


@runtime_checkable
class Report(Protocol):
    """The line sink the harness writes through: stdout and the report file at once."""

    def line(self, text: str = "") -> None:
        """Append one line to the report and print it."""
        ...

    def heading(self, title: str) -> None:
        """Start a named section."""
        ...


class ConsoleReport:
    """A report that prints every line immediately and keeps it for the file."""

    def __init__(self) -> None:
        self._lines: list[str] = []

    def line(self, text: str = "") -> None:
        """Print ``text`` now and remember it for :meth:`write`."""
        self._lines.append(text)
        print(text, flush=True)

    def heading(self, title: str) -> None:
        """Start a section with a blank line and a rule under the title."""
        self.line()
        self.line(f"--- {title} " + "-" * max(0, 68 - len(title)))

    def write(self, path: Path) -> None:
        """Write the whole report to ``path``."""
        path.write_text("\n".join(self._lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------------------
# Reading audio
# --------------------------------------------------------------------------------------


def read_wav(path: Path) -> AudioBuffer:
    """Read ``path`` (16-bit PCM mono) into an :class:`AudioBuffer` of float32 samples."""
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        if channels != 1 or width != 2 or rate != SAMPLE_RATE:
            raise ValueError(
                f"{path} is {channels}ch/{width * 8}-bit/{rate} Hz; the engine contract "
                f"is 1ch/16-bit/{SAMPLE_RATE} Hz (ADR 0005)"
            )
        raw = handle.readframes(handle.getnframes())
    samples = array("h")
    samples.frombytes(raw)
    return AudioBuffer(
        samples=[value / 32768.0 for value in samples], sample_rate=rate
    )


def write_wav(path: Path, buffer: AudioBuffer) -> None:
    """Write ``buffer`` as 16-bit PCM mono under ``path`` (always inside ``/tmp``)."""
    samples = array(
        "h",
        (
            max(-32768, min(32767, round(value * 32767.0)))
            for value in buffer.samples
        ),
    )
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(buffer.sample_rate)
        handle.writeframes(samples.tobytes())


def chunk_buffer(buffer: AudioBuffer, chunk_seconds: float) -> tuple[AudioBuffer, ...]:
    """Split ``buffer`` into consecutive ``chunk_seconds`` blocks (last one may be short)."""
    step = max(1, round(chunk_seconds * buffer.sample_rate))
    samples = buffer.samples
    return tuple(
        AudioBuffer(samples=samples[start : start + step], sample_rate=buffer.sample_rate)
        for start in range(0, len(samples), step)
    )


# --------------------------------------------------------------------------------------
# Driving the shipped stack, with the decoder seam recorded
# --------------------------------------------------------------------------------------


class DecoderRecorder:
    """Temporarily installs a recording subclass over the adapter's decoder class.

    ``WhistleTranscriber.stream`` constructs ``_WhistleStreamingDecoder`` eagerly and hands
    it to ``streaming.drive``, so swapping the module attribute for the duration of one run
    records every value that crosses that seam while the shipped code runs unchanged.
    """

    def __init__(self) -> None:
        self.events: list[PassEvent] = []
        self._original: Any = whistle._WhistleStreamingDecoder  # noqa: SLF001
        self._starts: tuple[float, ...] = ()
        self._widths: tuple[float, ...] = ()
        self._origin: float = 0.0
        self._process_index: int = 0
        self._engine_seconds: float = 0.0

    def install(
        self,
        starts: Sequence[float],
        widths: Sequence[float],
        origin: float,
    ) -> None:
        """Record ``starts``/``widths`` for the gated chunks and patch the decoder class."""
        self.events = []
        self._starts = tuple(starts)
        self._widths = tuple(widths)
        self._origin = origin
        self._process_index = 0
        self._engine_seconds = 0.0
        original = self._original
        recorder = self

        class Recording(original):  # type: ignore[misc, valid-type]
            def process(self, buffer: AudioBuffer) -> streaming.StreamingPass:
                result = super().process(buffer)
                recorder._record("process", result, len(buffer.samples) / SAMPLE_RATE)
                return result

            def flush(self) -> str:
                tail = super().flush()
                recorder._record(
                    "flush",
                    streaming.StreamingPass(committed=tail, pending="", language=None),
                    0.0,
                )
                return tail

        whistle._WhistleStreamingDecoder = Recording  # noqa: SLF001  # type: ignore[assignment]

    def uninstall(self) -> None:
        """Restore the adapter's own decoder class."""
        whistle._WhistleStreamingDecoder = self._original  # noqa: SLF001  # type: ignore[assignment]

    def _record(
        self, kind: str, result: streaming.StreamingPass, fed_seconds: float
    ) -> None:
        """Append one :class:`PassEvent`, mapping it back onto the source timeline."""
        index = self._process_index
        if kind == "process":
            self._engine_seconds += fed_seconds
            if index < len(self._starts):
                source = self._starts[index]
            elif self._starts:
                source = self._starts[-1] + self._widths[-1]
            else:
                source = 0.0
            self._process_index += 1
        elif index > 0 and index - 1 < len(self._starts):
            source = self._starts[index - 1] + self._widths[index - 1]
        else:
            source = 0.0
        self.events.append(
            PassEvent(
                kind=kind,
                source_seconds=source,
                engine_seconds=self._engine_seconds,
                wall_seconds=time.perf_counter() - self._origin,
                committed=result.committed,
                pending=result.pending,
                language=result.language,
            )
        )


def gated_timeline(
    buffers: Sequence[AudioBuffer],
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Return the source start time and width of each chunk the shipped gate forwards.

    The predicate is the shipped ``whistle._has_speech`` itself, called on the same
    buffers ``stream()`` will see, so the flags cannot disagree with the run. It is used
    only to reconstruct the audio timeline for reporting; the audio path is untouched.
    The ``process`` calls map one-to-one onto these chunks because every input buffer is
    exactly one chunk long (the last may be short), which is the only shape this script
    feeds; :func:`run_utterance` verifies that count and falls back to engine-fed seconds
    if it ever stops being true.
    """
    starts: list[float] = []
    widths: list[float] = []
    cursor = 0.0
    for buffer in buffers:
        width = len(buffer.samples) / buffer.sample_rate
        if whistle._has_speech(buffer):  # noqa: SLF001
            starts.append(cursor)
            widths.append(width)
        cursor += width
    return tuple(starts), tuple(widths)


def run_utterance(
    transcriber: WhistleTranscriber,
    buffers: Sequence[AudioBuffer],
    *,
    label: str,
    path: Path | None,
    config: WhistleConfig,
    reference_text: str | None,
    with_batch_reference: bool,
) -> UtteranceMeasurement:
    """Drive the shipped ``stream()`` over ``buffers`` and collect the measurement."""
    chunk_seconds = config.chunk_seconds
    window_seconds = config.window_seconds
    duration = sum(len(buffer.samples) / buffer.sample_rate for buffer in buffers)
    starts, widths = gated_timeline(buffers)

    recorder = DecoderRecorder()
    origin = time.perf_counter()
    recorder.install(starts, widths, origin)
    try:
        segments = list(transcriber.stream(list(buffers)))
    finally:
        recorder.uninstall()

    partials = tuple(segment.text for segment in segments if not segment.is_final)
    finals = [segment for segment in segments if segment.is_final]
    if not finals:
        raise RuntimeError("the shipped drive() yielded no final segment")
    final = finals[-1]

    transcript = ""
    snapshots: list[str] = []
    for event in recorder.events:
        transcript += event.committed
        if transcript != (snapshots[-1] if snapshots else ""):
            snapshots.append(transcript)

    batch_reference: str | None = None
    if with_batch_reference and duration <= whistle.MAX_SECONDS:
        whole = AudioBuffer(
            samples=[sample for buffer in buffers for sample in buffer.samples],
            sample_rate=SAMPLE_RATE,
        )
        try:
            batch_reference = transcriber.transcribe(whole).text
        except whistle.WhistleInputError as exc:  # pragma: no cover - defensive
            batch_reference = f"<batch reference unavailable: {exc}>"

    process_calls = sum(1 for event in recorder.events if event.kind == "process")
    fed_seconds = recorder.events[-1].engine_seconds if recorder.events else 0.0
    return UtteranceMeasurement(
        label=label,
        path=path,
        duration_seconds=duration,
        fed_seconds=fed_seconds,
        window_seconds=window_seconds,
        chunk_seconds=chunk_seconds,
        events=tuple(recorder.events),
        snapshots=tuple(snapshots),
        partial_texts=partials,
        final_text=final.text,
        final_language=final.language,
        batch_reference=batch_reference,
        reference_text=reference_text,
        timeline_from_gate=process_calls == len(starts),
    )


# --------------------------------------------------------------------------------------
# The four measurements
# --------------------------------------------------------------------------------------


def check_prefix_property(measurement: UtteranceMeasurement) -> PrefixCheck:
    """Assert every intermediate transcript snapshot is a prefix of the final transcript.

    The snapshots are the prefix sums of the decoder's recorded deltas -- exactly the
    values ``drive()``'s ``transcript += ...`` fold passes through.
    """
    final = measurement.final_text
    failures: list[str] = []
    for index, snapshot in enumerate(measurement.snapshots):
        if not final.startswith(snapshot):
            failures.append(
                f"snapshot #{index} is not a prefix of the final transcript:\n"
                f"    snapshot: {snapshot!r}\n"
                f"    final   : {final!r}"
            )
    return PrefixCheck(checked=len(measurement.snapshots), failures=tuple(failures))


def find_committed_overlaps(
    measurement: UtteranceMeasurement, *, min_words: int = 1
) -> tuple[Overlap, ...]:
    """Find committed deltas whose leading words repeat already-committed words.

    A restatement that matters for incremental typing looks exactly like this: the engine
    returns committed text that repeats text it has already committed, so the appended
    transcript would type the same words twice. Legitimate natural repetition ("the ...
    the") also matches at one word, which is why every hit is reported verbatim rather
    than counted silently.
    """
    committed: list[str] = []
    overlaps: list[Overlap] = []
    for event in measurement.commits:
        words, _ = tokenise(event.committed)
        limit = min(len(words), len(committed), 12)
        found = 0
        for size in range(limit, min_words - 1, -1):
            if words[:size] == committed[-size:]:
                found = size
                break
        if found >= min_words:
            overlaps.append(
                Overlap(
                    overlap_words=found,
                    source_seconds=event.source_seconds,
                    before=" ".join(committed[-found:]),
                    delta=event.committed,
                )
            )
        committed.extend(words)
    return tuple(overlaps)


def word_diff(reference: str, hypothesis: str) -> WordDiff:
    """Word-level edit counts between two texts, both normalised first."""
    reference_words, _ = tokenise(reference)
    hypothesis_words, _ = tokenise(hypothesis)
    substitutions = insertions = deletions = 0
    matcher = difflib.SequenceMatcher(
        a=reference_words, b=hypothesis_words, autojunk=False
    )
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "replace":
            substitutions += max(i2 - i1, j2 - j1)
            insertions += max(0, (j2 - j1) - (i2 - i1))
            deletions += max(0, (i2 - i1) - (j2 - j1))
        elif tag == "delete":
            deletions += i2 - i1
        elif tag == "insert":
            insertions += j2 - j1
    return WordDiff(
        substitutions=substitutions,
        insertions=insertions,
        deletions=deletions,
        reference_words=len(reference_words),
    )


def percentile(values: Sequence[float], fraction: float) -> float:
    """Linear-interpolated percentile of ``values`` (``values`` must be non-empty)."""
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def distribution(values: Sequence[float], *, unit: str = "", digits: int = 1) -> str:
    """Render a median / p90 / max / n summary of ``values``."""
    if not values:
        return "n=0 (no samples)"
    return (
        f"median {percentile(values, 0.5):.{digits}f}{unit} | "
        f"p90 {percentile(values, 0.9):.{digits}f}{unit} | "
        f"max {max(values):.{digits}f}{unit} | n={len(values)}"
    )


def commit_gaps(measurement: UtteranceMeasurement) -> tuple[float, ...]:
    """Source-seconds between consecutive commits (the cadence the feature would type at)."""
    times = [event.source_seconds for event in measurement.commits]
    return tuple(b - a for a, b in zip(times, times[1:]))


def pending_persistence(measurement: UtteranceMeasurement) -> tuple[float, ...]:
    """How long each pending value stayed unchanged, in source-seconds.

    This is the observable lag between a word being spoken and it appearing: a word in
    ``pending`` is not part of the transcript and must not be typed. It is measured on the
    source timeline, so a gated silence counts as the pause the user actually took.
    """
    durations: list[float] = []
    previous: str | None = None
    previous_seconds = 0.0
    for event in measurement.events:
        if previous is not None and event.pending != previous:
            durations.append(event.source_seconds - previous_seconds)
        if event.pending != previous:
            previous = event.pending
            previous_seconds = event.source_seconds
    if previous is not None and previous != "":
        tail = measurement.events[-1].source_seconds if measurement.events else 0.0
        durations.append(max(0.0, tail - previous_seconds))
    return tuple(durations)


# --------------------------------------------------------------------------------------
# Verbatim-token rendering (informational, from the user's addendum)
# --------------------------------------------------------------------------------------

_PUNCTUATION_EDGES = ".,;:!?\"“”()[]{}«»—–-…"


def _normalise_token(token: str) -> list[str]:
    """Normalise one whitespace token to lowercase words, keeping decimal points.

    Case is folded, curly apostrophes are unified and then dropped so ``don't`` ==
    ``dont``, hyphens and other punctuation become word separators so ``well-known`` ==
    ``well known``, and a ``.`` survives only between two digits so ``3.2`` stays one
    token. Both sides of every comparison go through this, so it cannot favour either.
    """
    folded = token.lower().replace("’", "'")
    characters: list[str] = []
    for index, char in enumerate(folded):
        if char.isalnum():
            characters.append(char)
        elif (
            char == "."
            and 0 < index < len(folded) - 1
            and folded[index - 1].isdigit()
            and folded[index + 1].isdigit()
        ):
            characters.append(char)
        elif char == "'":
            continue
        else:
            characters.append(" ")
    return [word for word in "".join(characters).split() if word]


def tokenise(text: str) -> tuple[list[str], list[TokenSpan]]:
    """Return the normalised word list and the whitespace tokens that produced it."""
    words: list[str] = []
    spans: list[TokenSpan] = []
    for raw in text.split():
        normalised = tuple(_normalise_token(raw))
        spans.append(TokenSpan(raw=raw, normalised=normalised, start=len(words)))
        words.extend(normalised)
    return words, spans


def strip_edges(token: str) -> str:
    """Return ``token`` without surrounding punctuation, for display."""
    return token.strip(_PUNCTUATION_EDGES)


@dataclass(frozen=True)
class Alignment:
    """A ref-to-hypothesis word alignment, plus the surface tokens behind both."""

    reference_words: tuple[str, ...]
    hypothesis_words: tuple[str, ...]
    reference_surface: tuple[str, ...]
    hypothesis_surface: tuple[str, ...]
    matches: dict[int, tuple[int, int]]


def align(reference: str, hypothesis: str) -> Alignment:
    """Align two texts at word level and keep the surface token of every word."""
    reference_words, reference_spans = tokenise(reference)
    hypothesis_words, hypothesis_spans = tokenise(hypothesis)
    reference_surface = tuple(
        token
        for span in reference_spans
        for token in [span.raw] * len(span.normalised)
    )
    hypothesis_surface = tuple(
        token
        for span in hypothesis_spans
        for token in [span.raw] * len(span.normalised)
    )
    matches: dict[int, tuple[int, int]] = {}
    matcher = difflib.SequenceMatcher(
        a=reference_words, b=hypothesis_words, autojunk=False
    )
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                matches[i1 + offset] = (j1 + offset, j1 + offset + 1)
        elif tag == "replace":
            for index in range(i1, i2):
                matches[index] = (j1, j2)
    return Alignment(
        reference_words=tuple(reference_words),
        hypothesis_words=tuple(hypothesis_words),
        reference_surface=reference_surface,
        hypothesis_surface=hypothesis_surface,
        matches=matches,
    )


def render_aligned(alignment: Alignment, span: TokenSpan) -> str:
    """Render the hypothesis surface words aligned to ``span``'s words, or a marker.

    Consecutive words that map to the same alignment block are rendered once, and the
    result is capped so a wholesale mismatch cannot print a wall of text.
    """
    pieces: list[str] = []
    seen: set[tuple[int, int]] = set()
    for index in range(span.start, span.end):
        location = alignment.matches.get(index)
        if location is None or location in seen:
            continue
        seen.add(location)
        start, end = location
        pieces.extend(alignment.hypothesis_surface[start:end])
    if not pieces:
        return "<not in transcript>"
    return preview(" ".join(pieces), 80)


# --------------------------------------------------------------------------------------
# Rendering the report
# --------------------------------------------------------------------------------------


def preview(text: str, limit: int) -> str:
    """Return ``text`` truncated to ``limit`` characters with an ellipsis marker."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"…(+{len(text) - limit})"


def report_engine_header(
    report: Report, config: Config, whistle_config: WhistleConfig
) -> None:
    """Print what engine, window and chunk the measurement ran with."""
    report.heading("engine and configuration")
    report.line(f"engine          : {config.transcriber.engine}")
    report.line(
        f"window_seconds  : {whistle_config.window_seconds}  "
        f"chunk_seconds: {whistle_config.chunk_seconds}  "
        f"language: {whistle_config.language!r}"
    )
    report.line(f"sample rate     : {SAMPLE_RATE} Hz mono float32 (ADR 0005)")


def report_utterance(report: Report, measurement: UtteranceMeasurement) -> None:
    """Report one input: the four measurements plus its verbatim transcript."""
    report.heading(f"input: {measurement.label}")
    where = f" ({measurement.path})" if measurement.path is not None else ""
    report.line(
        f"duration        : {measurement.duration_seconds:.2f} s recorded, "
        f"{measurement.fed_seconds:.2f} s fed to the engine "
        f"(the shipped silence gate dropped the rest)"
    )
    report.line(
        f"flush() calls   : {len(measurement.flush_events)} total, "
        f"{max(0, len(measurement.flush_events) - 1)} window crossings inside the audio "
        "(the extra one is the final flush)"
    )
    report.line(f"partials yielded: {len(measurement.partial_texts)}")
    report.line(f"source file     : {where if where else '<live capture>'}")
    report.line(
        "timeline        : "
        + (
            "source seconds reconstructed from the shipped gate's per-chunk verdicts; "
            "every process() call maps onto exactly one fed chunk"
            if measurement.timeline_from_gate
            else "WARNING the process-call count and the fed-chunk count disagree, so "
            "source seconds fall back to engine-fed seconds and are approximate"
        )
    )

    check = check_prefix_property(measurement)
    report.line(
        f"[1] append-only : {check.checked} intermediate transcript snapshots checked; "
        f"{'PASS' if check.ok else 'FAIL'} "
        f"(every snapshot is a prefix of the final transcript)"
    )
    for failure in check.failures:
        report.line(f"    FAILURE {failure}")

    commits = measurement.commits
    chars = [len(event.committed) for event in commits]
    gaps = commit_gaps(measurement)
    report.line(f"[2] commit cadence: {len(commits)} commits over the utterance")
    report.line(f"    chars per commit : {distribution([float(c) for c in chars], digits=1)}")
    report.line(f"    gap, source secs : {distribution(gaps, unit='s', digits=2)}")
    wall_gaps = [
        b.wall_seconds - a.wall_seconds
        for a, b in zip(commits, commits[1:])
        if b.wall_seconds >= a.wall_seconds
    ]
    report.line(f"    gap, wall secs   : {distribution(wall_gaps, unit='s', digits=2)}")

    pending_sizes = [float(len(event.pending)) for event in measurement.events]
    persistence = pending_persistence(measurement)
    report.line(f"[3] pending size : {distribution(pending_sizes, digits=1)}")
    report.line(
        f"    pending persistence (source secs, how long the uncommitted tail "
        f"stayed unchanged): {distribution(persistence, unit='s', digits=2)}"
    )

    overlaps = find_committed_overlaps(measurement)
    strong = [overlap for overlap in overlaps if overlap.overlap_words >= 2]
    report.line(
        f"[4] restatements: {len(overlaps)} committed deltas repeat already-committed "
        f"words at their start ({len(strong)} with 2+ words)"
    )
    for overlap in overlaps[:20]:
        report.line(
            f"    t=+{overlap.source_seconds:.2f}s  {overlap.overlap_words} word(s) repeated"
        )
        report.line(f"        already committed: {overlap.before!r}")
        report.line(f"        new delta        : {overlap.delta!r}")

    if measurement.batch_reference is not None:
        diff = word_diff(measurement.batch_reference, measurement.final_text)
        report.line(
            f"    vs one-shot batch reference: substitutions={diff.substitutions} "
            f"insertions={diff.insertions} deletions={diff.deletions} "
            f"WER={diff.wer:.3f} (n={diff.reference_words} reference words)"
        )

    report.heading(f"pass table: {measurement.label}")
    report.line(
        "  t_source    t_engine      t_wall  kind     chars  committed / pending"
    )
    for event in measurement.events:
        committed = preview(event.committed, DELTA_PREVIEW_CHARS)
        pending = preview(event.pending, PENDING_PREVIEW_CHARS)
        report.line(
            f"  {event.source_seconds:8.2f}s {event.engine_seconds:9.2f}s "
            f"{event.wall_seconds:8.2f}s  {event.kind:7s} {len(event.committed):5d}  "
            f"committed={committed!r} pending={pending!r}"
        )

    report.heading(f"final transcript verbatim: {measurement.label}")
    report.line(f"language reported: {measurement.final_language!r}")
    for line in wrap_text(measurement.final_text, width=100):
        report.line(f"  {line}")
    if measurement.batch_reference is not None:
        report.line()
        report.line("one-shot batch reference (same audio, same adapter, different path):")
        for line in wrap_text(measurement.batch_reference, width=100):
            report.line(f"  {line}")


def wrap_text(text: str, *, width: int) -> list[str]:
    """Wrap ``text`` on whitespace to ``width`` characters, never dropping a word."""
    words = text.split()
    if not words:
        return ["<empty>"]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        if len(current) + 1 + len(word) <= width:
            current = f"{current} {word}"
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def report_pending_revisions(report: Report, measurement: UtteranceMeasurement) -> None:
    """Show the verbatim pending tail that sat on screen before each commit landed.

    This is display-only text: revising it is safe and is exactly what the two-pass commit
    rule is for. It is reported because it is the mirror image of a restatement -- the case
    that is *not* dangerous -- and because a reviewer can then see both sides of the rule.
    """
    pairs: list[tuple[float, str, str]] = []
    previous_pending = ""
    for event in measurement.events:
        if event.kind == "process" and event.pending:
            previous_pending = event.pending
        if event.is_commit:
            pairs.append((event.source_seconds, previous_pending, event.committed))
            previous_pending = ""
    if not pairs:
        return
    report.line(
        f"    uncommitted tail on screen before each commit ({len(pairs)} commits); "
        "the tail is revised before commit, it is never typed:"
    )
    for seconds, pending, committed in sample_spread(pairs, limit=8):
        report.line(f"      t=+{seconds:.2f}s")
        report.line(f"        pending   : {pending!r}")
        report.line(f"        committed : {committed!r}")


def sample_spread[T](items: Sequence[T], *, limit: int) -> list[T]:
    """Return at most ``limit`` items: all of them when short, else head and tail ends."""
    if len(items) <= limit:
        return list(items)
    head = limit // 2
    tail = limit - head
    return list(items[:head]) + list(items[-tail:])


def report_accuracy(report: Report, measurement: UtteranceMeasurement) -> None:
    """Informational accuracy section against a user-supplied reference text.

    This is **not** a gate. The engine is a 16 MB model with an accepted "good enough"
    bar (ADR 0000), so imperfect output on hard words is expected and is not a defect.
    Nothing here feeds back into the engine: the reference is never part of the audio path.
    """
    reference = measurement.reference_text
    if reference is None:
        return
    hypothesis = measurement.final_text
    report.heading(f"accuracy (informational, not a gate): {measurement.label}")

    diff = word_diff(reference, hypothesis)
    report.line(
        f"word-level comparison: substitutions={diff.substitutions} "
        f"insertions={diff.insertions} deletions={diff.deletions} "
        f"WER={diff.wer:.3f} (reference has {diff.reference_words} words)"
    )
    reference_words, _ = tokenise(reference)
    hypothesis_words, _ = tokenise(hypothesis)
    report.line(
        f"normalised word counts: reference={len(reference_words)} "
        f"transcript={len(hypothesis_words)}"
    )

    report.line()
    report.line("raw reference text (what was read aloud):")
    for line in wrap_text(reference, width=100):
        report.line(f"  {line}")
    report.line()
    report.line("raw transcript text (verbatim, nothing stripped):")
    for line in wrap_text(hypothesis, width=100):
        report.line(f"  {line}")

    alignment = align(reference, hypothesis)

    report.line()
    report.line("digits and decimal numbers:")
    digit_spans = [
        span
        for span in tokenise(reference)[1]
        if any(character.isdigit() for character in span.raw)
    ]
    if not digit_spans:
        report.line("  (the reference contains no digits)")
    for span in digit_spans:
        report.line(f"  reference {span.raw!r}  ->  transcript {render_aligned(alignment, span)!r}")
    report.line(
        f"  digit characters: reference={sum(c.isdigit() for c in reference)} "
        f"transcript={sum(c.isdigit() for c in hypothesis)}"
    )

    report.line()
    report.line("punctuation and casing:")
    report.line(
        f"  '?'  reference={reference.count('?')} transcript={hypothesis.count('?')}; "
        f"apostrophes reference={reference.count(chr(39)) + reference.count(chr(8217))} "
        f"transcript={hypothesis.count(chr(39)) + hypothesis.count(chr(8217))}; "
        f"hyphens reference={reference.count('-') + reference.count('–') + reference.count('—')} "
        f"transcript={hypothesis.count('-') + hypothesis.count('–') + hypothesis.count('—')}"
    )
    for label, predicate in (
        ("question-marked tokens", lambda token: token.rstrip().endswith(("?", "？"))),
        ("apostrophe tokens", lambda token: "'" in token or "’" in token),
        ("hyphenated tokens", lambda token: any(dash in token for dash in "-–—")),
    ):
        spans = [span for span in tokenise(reference)[1] if predicate(span.raw)]
        report.line(f"  {label} in the reference ({len(spans)}):")
        if not spans:
            report.line("    (none)")
        for span in spans:
            report.line(
                f"    {span.raw!r}  ->  {render_aligned(alignment, span)!r}"
            )

    report.line()
    report.line("proper-noun casing (capitalised non-sentence-initial reference words):")
    raw_tokens = reference.split()
    proper = []
    for index, token in enumerate(raw_tokens):
        stripped = strip_edges(token)
        if not stripped or not stripped[0].isupper():
            continue
        sentence_start = index == 0 or raw_tokens[index - 1].rstrip().endswith(
            (".", "!", "?", ";", ":")
        )
        if sentence_start:
            continue
        proper.append(stripped)
    if not proper:
        report.line("  (the reference has no mid-sentence capitalised words)")
    for word in dict.fromkeys(proper):
        matching = [
            span
            for span in tokenise(reference)[1]
            if strip_edges(span.raw) == word
        ]
        if matching:
            report.line(f"  {word!r}  ->  {render_aligned(alignment, matching[0])!r}")
    transcript_capitalised = [
        token for token in hypothesis.split() if strip_edges(token)[:1].isupper()
    ]
    report.line(
        f"  capitalised tokens in the transcript: {len(transcript_capitalised)}"
    )
    if transcript_capitalised:
        report.line(f"    {transcript_capitalised}")


def report_summary(report: Report, measurements: Sequence[UtteranceMeasurement]) -> None:
    """Aggregate the numbers and state the recommendation they support."""
    report.heading("summary and recommendation")
    total_commits = sum(len(measurement.commits) for measurement in measurements)
    all_chars = [
        float(len(event.committed))
        for measurement in measurements
        for event in measurement.commits
    ]
    all_gaps = [
        gap for measurement in measurements for gap in commit_gaps(measurement)
    ]
    all_overlaps = [
        overlap
        for measurement in measurements
        for overlap in find_committed_overlaps(measurement)
    ]
    strong_overlaps = [
        overlap for overlap in all_overlaps if overlap.overlap_words >= 2
    ]
    prefix_failures = sum(
        1 for measurement in measurements if not check_prefix_property(measurement).ok
    )
    pending_fed = sum(measurement.fed_seconds for measurement in measurements)
    report.line(f"inputs measured        : {len(measurements)}")
    report.line(f"audio fed to the engine: {pending_fed:.2f} s")
    report.line(f"commits observed       : {total_commits}")
    report.line(f"chars per commit       : {distribution(all_chars, digits=1)}")
    report.line(f"gap between commits    : {distribution(all_gaps, unit='s', digits=2)}")
    report.line(
        f"prefix property        : {len(measurements) - prefix_failures}/{len(measurements)} "
        f"inputs PASS"
    )
    report.line(
        f"committed-word repeats : {len(all_overlaps)} hit(s), "
        f"{len(strong_overlaps)} with 2+ words"
    )
    report.line()
    report.line("recommendation:")
    if prefix_failures or strong_overlaps:
        report.line(
            "  STOP. The engine restated committed text. Every hit above is reported "
            "verbatim; the append-only assumption this feature depends on did not hold "
            "on these inputs, so ticket 34 must be cancelled or redesigned."
        )
        return
    median_gap = percentile(all_gaps, 0.5) if all_gaps else 0.0
    p90_gap = percentile(all_gaps, 0.9) if all_gaps else 0.0
    minimum_gap = min(all_gaps) if all_gaps else 0.0
    median_chars = percentile(all_chars, 0.5) if all_chars else 0.0
    report.line(
        f"  PROCEED WITH THE NAMED CAVEAT. On {len(measurements)} input(s) "
        f"({pending_fed:.1f} s of audio) no committed delta restated already-committed "
        f"text, and every intermediate transcript snapshot was a prefix of the final one."
    )
    if minimum_gap > 0:
        report.line(
            f"  The measurements support typing each non-empty commit as it arrives: a "
            f"median of {median_chars:.0f} characters per commit, one commit every "
            f"{median_gap:.2f} s of source audio (p90 {p90_gap:.2f} s, shortest "
            f"{minimum_gap:.2f} s). Because audio arrives in real time, the injector "
            f"never needs to fire more often than about once every {minimum_gap:.2f} s."
        )
    else:
        report.line(
            f"  The measurements support typing each non-empty commit as it arrives: a "
            f"median of {median_chars:.0f} characters per commit, one commit every "
            f"{median_gap:.2f} s of source audio (p90 {p90_gap:.2f} s)."
        )
    report.line(
        "  CAVEAT 1: the fragment injection unit is the commit delta, never `pending`; "
        "`pending` is display-only and is revised before commit (see the revisions above)."
    )
    report.line(
        "  CAVEAT 2: this is a measurement on the inputs listed above. The shipped clips "
        "are read speech; the fresh user recording is what carries this device's noise "
        "floor and this user's voice, and it must be added to this report before the gate "
        "can be considered closed."
    )
    report.line(
        "  CAVEAT 3: the engine is closed-source (ADR 0006); a restatement on an input "
        "outside this set cannot be ruled out. The injector should therefore stay "
        "cancel-safe, and a restatement seen in the wild should be treated as a bug "
        "report against the gate, not smoothed over."
    )


# --------------------------------------------------------------------------------------
# Self-test: prove the detector fires on a deliberately restating engine
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ScriptedStep:
    """One decoder return value in the self-test, with no engine behind it."""

    kind: str
    committed: str
    pending: str = ""


def measurement_from_steps(
    label: str, steps: Sequence[ScriptedStep]
) -> UtteranceMeasurement:
    """Build an :class:`UtteranceMeasurement` from scripted decoder returns.

    Used only by :func:`run_self_test`: a restatement cannot be produced on demand by the
    real engine, so the detector is validated against a decoder that restates by
    construction. No engine and no microphone are involved.
    """
    events: list[PassEvent] = []
    engine_seconds = 0.0
    snapshots: list[str] = []
    transcript = ""
    for step in steps:
        if step.kind == "process":
            engine_seconds += 1.0
        events.append(
            PassEvent(
                kind=step.kind,
                source_seconds=engine_seconds,
                engine_seconds=engine_seconds,
                wall_seconds=0.0,
                committed=step.committed,
                pending=step.pending,
                language=None,
            )
        )
        transcript += step.committed
        if transcript != (snapshots[-1] if snapshots else ""):
            snapshots.append(transcript)
    return UtteranceMeasurement(
        label=label,
        path=None,
        duration_seconds=engine_seconds,
        fed_seconds=engine_seconds,
        window_seconds=5.0,
        chunk_seconds=1.0,
        events=tuple(events),
        snapshots=tuple(snapshots),
        partial_texts=(),
        final_text=transcript,
        final_language=None,
        batch_reference=None,
        reference_text=None,
        timeline_from_gate=True,
    )


def run_self_test(report: Report) -> int:
    """Assert the restatement detector fires on a restating engine and stays quiet otherwise.

    This is the harness validating itself. It needs no engine, no device and no audio, so
    it can be run anywhere (and it is what keeps the ``[4] restatements: 0`` line from
    being vacuous).
    """
    report.heading("self-test: does the restatement detector fire?")
    cases: tuple[tuple[str, tuple[ScriptedStep, ...], int], ...] = (
        (
            "append-only engine (the shipped behaviour)",
            (
                ScriptedStep("process", "Hello"),
                ScriptedStep("process", " world", "!"),
                ScriptedStep("flush", "!"),
            ),
            0,
        ),
        (
            "engine restates a committed phrase in a later pass",
            (
                ScriptedStep("process", " I scream"),
                ScriptedStep("process", " I scream", " for ice cream"),
            ),
            2,
        ),
        (
            "flush() re-commits a tail the window already committed",
            (
                ScriptedStep("process", " hello world"),
                ScriptedStep("flush", " hello world"),
            ),
            2,
        ),
    )
    failures = 0
    for label, steps, expected in cases:
        measurement = measurement_from_steps(label, steps)
        overlaps = find_committed_overlaps(measurement)
        prefix = check_prefix_property(measurement)
        strongest = max((overlap.overlap_words for overlap in overlaps), default=0)
        detected = strongest >= expected if expected else not overlaps
        ok = detected and prefix.ok
        failures += 0 if ok else 1
        report.line(
            f"  {'PASS' if ok else 'FAIL'}  {label}: expected overlap>=({expected}) word(s), "
            f"detected {strongest}; prefix check {'PASS' if prefix.ok else 'FAIL'}"
        )
        for overlap in overlaps:
            report.line(
                f"        {overlap.overlap_words} word(s) repeated; already committed "
                f"{overlap.before!r}; new delta {overlap.delta!r}"
            )
    report.line()
    report.line(
        f"  {len(cases) - failures}/{len(cases)} self-test cases behaved as expected"
    )
    report.line(
        "  note: the prefix check PASSES even on the restating cases, which is exactly why "
        "the committed-word overlap scan and the batch-reference diff exist."
    )
    return 0 if failures == 0 else 1


# --------------------------------------------------------------------------------------
# The user-owned capture
# --------------------------------------------------------------------------------------


#: The refusal printed when a capture is attempted without a terminal. The capture step
#: is user-owned: the agent cannot tell the user when to start, so the script must never
#: record unattended.
CAPTURE_NOT_A_TTY: str = (
    "refusing to record: stdin is not a terminal. The capture step is user-owned -- run "
    "this script yourself, in your own terminal, and press Enter at the prompt so you "
    "choose the moment recording starts."
)


def describe_device(device: str) -> str:
    """Human-readable name of the capture device ``pw-record`` will use."""
    return device if device else "the system default input device"


def print_reference_text(text: str) -> None:
    """Print the passage the user is expected to read aloud, before recording starts."""
    print()
    print("Read the following passage aloud while recording:")
    print()
    for line in wrap_text(text, width=78):
        print(f"    {line}")
    print()


def capture_from_microphone(
    report: Report,
    *,
    config: Config,
    chunk_seconds: float,
    seconds: float,
    reference_text: str | None,
) -> AudioBuffer:
    """Record ``seconds`` from the user's microphone, announcing every step.

    Only the user runs this: the agent cannot tell them when to start. The function
    refuses to record when stdin is not a terminal, so an automated invocation can never
    capture the microphone.
    """
    if not sys.stdin.isatty():
        raise SystemExit(CAPTURE_NOT_A_TTY)
    device = config.audio.device
    report.line(f"capture device  : {describe_device(device)}")
    report.line(f"recorder        : {PW_RECORD_PROGRAM} at {SAMPLE_RATE} Hz mono")
    report.line(f"requested length: {seconds:.0f} s (fixed)")
    report.line(
        "tip             : leave a deliberate 2-3 s pause in the middle; a pause is "
        "where the engine's flush() is most likely to commit a tail."
    )
    if reference_text is not None:
        report.line("reference text  : supplied (see below); it is not part of the audio path")

    if reference_text is not None:
        print_reference_text(reference_text)

    print()
    print(f"Recording from {describe_device(device)} for {seconds:.0f} s.")
    print("Press Enter to arm the recorder (nothing is captured before you do).")
    input()
    for count in (3, 2, 1):
        print(f"  {count}...")
        time.sleep(1.0)

    source = PwRecordAudioSource(device)
    source.start()
    print()
    print("● RECORDING — speak now")
    elapsed = 0.0
    try:
        for _chunk in source.chunks(chunk_seconds):
            elapsed += chunk_seconds
            shown = min(int(elapsed), int(seconds))
            print(
                f"\r  {shown:2d} s / {seconds:.0f} s recorded",
                end="",
                flush=True,
            )
            if elapsed >= seconds:
                break
    except KeyboardInterrupt:
        print("\n  interrupted by the user")
    finally:
        whole = source.stop()
    print()
    print("■ stopped")
    return whole


# --------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Options:
    """Parsed command line."""

    clip_dir: Path | None
    wav_paths: tuple[Path, ...]
    capture: bool
    self_test: bool
    seconds: float
    reference: str | None
    out_path: Path | None
    config_path: Path | None


def parse_args(argv: Sequence[str] | None) -> Options:
    """Parse the command line into :class:`Options`."""
    parser = argparse.ArgumentParser(
        description=(
            "Measure the real engine's commit cadence through the shipped streaming "
            "adapter (ticket 32). No behaviour change, no injection, engine optional."
        )
    )
    parser.add_argument(
        "--clips",
        nargs="?",
        const=str(DEFAULT_CLIP_DIR),
        default=None,
        metavar="DIR",
        help=f"measure the shipped clips in DIR (default {DEFAULT_CLIP_DIR})",
    )
    parser.add_argument(
        "--wav",
        action="append",
        default=[],
        metavar="PATH",
        help="measure an existing 16 kHz mono WAV (repeatable)",
    )
    parser.add_argument(
        "--capture",
        action="store_true",
        help=(
            "record from the microphone (user-owned: it waits for Enter, then a 3-2-1 "
            "countdown). This is the default when no other input is given."
        ),
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help=(
            "validate the restatement detector against a scripted restating engine "
            "(no engine, no device, no audio), then exit"
        ),
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=DEFAULT_CAPTURE_SECONDS,
        help=f"capture length in seconds (default {DEFAULT_CAPTURE_SECONDS:.0f})",
    )
    parser.add_argument(
        "--reference",
        default=None,
        metavar="TEXT_OR_PATH",
        help=(
            "reference text read aloud during the capture, for the informational "
            "accuracy section. A path is read as a file; anything else is used verbatim."
        ),
    )
    parser.add_argument(
        "--out",
        default=None,
        metavar="PATH",
        help="report file (default /tmp/ges2t-commit-cadence-<timestamp>-report.txt)",
    )
    parser.add_argument("--config", default=None, metavar="PATH", help="config file to use")
    args = parser.parse_args(argv)

    capture = args.capture or (
        args.clips is None and not args.wav and not args.self_test
    )
    return Options(
        clip_dir=Path(args.clips) if args.clips is not None else None,
        wav_paths=tuple(Path(path) for path in args.wav),
        capture=capture,
        self_test=args.self_test,
        seconds=args.seconds,
        reference=args.reference,
        out_path=Path(args.out) if args.out is not None else None,
        config_path=Path(args.config) if args.config is not None else None,
    )


def resolve_reference(value: str | None) -> str | None:
    """Return the reference text: ``value`` as a file when it is one, else ``value``."""
    if value is None:
        return None
    candidate = Path(value)
    if "\n" not in value and candidate.is_file():
        return candidate.read_text(encoding="utf-8").strip()
    return value


def load_engine(config_path: Path | None = None) -> tuple[WhistleTranscriber, WhistleConfig, Config]:
    """Load the user's config and the real engine through the shipped adapter.

    Nothing is downloaded: if the managed artifacts are missing the script says so and
    tells the user to run ``ges2t setup``, rather than silently fetching anything.
    """
    from ges2t import model_store

    loaded = load_config(config_path)
    config = loaded.config
    whistle_result = WhistleConfig.from_mapping(
        config.engine_blocks.get(config.transcriber.engine, {})
    )
    whistle_config = whistle_result.config
    transcriber = WhistleTranscriber(whistle_config)
    weights = model_store.model_path()
    engine_library = model_store.engine_path()
    if not weights.exists() or not engine_library.exists():
        raise SystemExit(
            "the managed Whistle artifacts are missing:\n"
            f"  weights: {weights} ({'present' if weights.exists() else 'MISSING'})\n"
            f"  engine : {engine_library} "
            f"({'present' if engine_library.exists() else 'MISSING'})\n"
            "Run 'ges2t setup' first. This diagnostic never downloads anything."
        )
    transcriber.load()
    if loaded.warnings:
        for warning in loaded.warnings:
            print(f"config warning: {warning}")
    for warning in whistle_result.warnings:
        print(f"config warning: {warning}")
    return transcriber, whistle_config, config


def measure_clip_directory(
    report: Report,
    transcriber: WhistleTranscriber,
    whistle_config: WhistleConfig,
    directory: Path,
) -> list[UtteranceMeasurement]:
    """Measure every shipped clip present in ``directory``."""
    measurements: list[UtteranceMeasurement] = []
    missing = [name for name in SHIPPED_CLIPS if not (directory / name).exists()]
    if missing:
        report.line(
            f"clip directory {directory} is missing {missing}; if /tmp was reaped, "
            "these inputs no longer exist and must be regenerated (pw-record) or the "
            "numbers they carried are gone."
        )
    for name in SHIPPED_CLIPS:
        path = directory / name
        if not path.exists():
            continue
        measurements.append(
            measure_file(report, transcriber, whistle_config, path, label=f"shipped clip {name}")
        )
    return measurements


def measure_file(
    report: Report,
    transcriber: WhistleTranscriber,
    whistle_config: WhistleConfig,
    path: Path,
    *,
    label: str,
    reference_text: str | None = None,
) -> UtteranceMeasurement:
    """Measure one WAV file through the shipped streaming path."""
    buffer = read_wav(path)
    buffers = chunk_buffer(buffer, whistle_config.chunk_seconds)
    started = time.perf_counter()
    measurement = run_utterance(
        transcriber,
        buffers,
        label=label,
        path=path,
        config=whistle_config,
        reference_text=reference_text,
        with_batch_reference=True,
    )
    report.line(
        f"measured {label} in {time.perf_counter() - started:.2f} s wall"
    )
    return measurement


def measure_capture(
    report: Report,
    transcriber: WhistleTranscriber,
    whistle_config: WhistleConfig,
    config: Config,
    *,
    seconds: float,
    reference_text: str | None,
) -> UtteranceMeasurement:
    """Record (user-owned) and then measure the fresh real-device recording."""
    buffer = capture_from_microphone(
        report,
        config=config,
        chunk_seconds=whistle_config.chunk_seconds,
        seconds=seconds,
        reference_text=reference_text,
    )
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    wav_path = TMP_DIR / f"ges2t-commit-cadence-capture-{stamp}.wav"
    write_wav(wav_path, buffer)
    report.line(
        f"captured {len(buffer.samples) / buffer.sample_rate:.2f} s; "
        f"kept under /tmp only (never committed): {wav_path}"
    )
    buffers = chunk_buffer(buffer, whistle_config.chunk_seconds)
    measurement = run_utterance(
        transcriber,
        buffers,
        label=f"fresh device recording ({stamp})",
        path=wav_path,
        config=whistle_config,
        reference_text=reference_text,
        with_batch_reference=True,
    )
    return measurement


def print_capture_instructions(seconds: float) -> None:
    """Tell the user exactly what the interactive capture will look like."""
    print("This run records from your microphone; you own the start.")
    print(f"  - first it prints the device and waits for you to press Enter")
    print(f"  - then a 3-2-1 countdown")
    print(f"  - then '● RECORDING — speak now' with a seconds counter")
    print(f"  - it records for {seconds:.0f} s and prints '■ stopped'")
    print("  - the full report goes to stdout and to a file under /tmp")
    print()


def main(argv: Sequence[str] | None = None) -> int:
    """Run the diagnostic and return a process exit status."""
    options = parse_args(argv)
    report = ConsoleReport()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = options.out_path or (TMP_DIR / f"ges2t-commit-cadence-{stamp}-report.txt")

    report.line("=" * 72)
    report.line("Commit-cadence diagnostic (ticket 32, the gate for incremental typing)")
    report.line(f"run at : {datetime.now().isoformat(timespec='seconds')}")
    report.line(f"argv   : {list(sys.argv if argv is None else argv)}")
    report.line("=" * 72)

    if options.capture and not sys.stdin.isatty():
        print(CAPTURE_NOT_A_TTY, file=sys.stderr)
        return 1

    if options.self_test:
        status = run_self_test(report)
        report.write(out_path)
        report.line()
        report.line(f"full report written to {out_path}")
        return status

    transcriber, whistle_config, config = load_engine(options.config_path)
    report_engine_header(report, config, whistle_config)

    measurements: list[UtteranceMeasurement] = []
    if options.capture:
        print_capture_instructions(options.seconds)
        measurements.append(
            measure_capture(
                report,
                transcriber,
                whistle_config,
                config,
                seconds=options.seconds,
                reference_text=resolve_reference(options.reference),
            )
        )
    if options.clip_dir is not None:
        measurements.extend(
            measure_clip_directory(report, transcriber, whistle_config, options.clip_dir)
        )
    for path in options.wav_paths:
        measurements.append(
            measure_file(
                report,
                transcriber,
                whistle_config,
                path,
                label=f"wav {path.name}",
                reference_text=resolve_reference(options.reference),
            )
        )

    if not measurements:
        report.line()
        report.line("no inputs were measured: nothing to report.")
        report.write(out_path)
        print(f"\nreport written to {out_path}")
        return 1

    for measurement in measurements:
        report_utterance(report, measurement)
        report_pending_revisions(report, measurement)
        report_accuracy(report, measurement)

    report_summary(report, measurements)
    report.write(out_path)
    report.line()
    report.line(f"full report written to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
