#!/usr/bin/env python3
"""Accuracy-levers measurement: which input-side lever moves WER (ticket 39).

This script is a **diagnostic**, not part of the shipped package. It lives outside
``src/ges2t`` on purpose: ``uv run pytest`` must stay headless and engine-free (ADR 0014),
so nothing here is imported by the package or by the test suite.

It answers the user's question -- "should we build a noise filter?" -- with numbers,
by transcribing the **same** existing 25 s recording of a known passage through the
**real** engine under several input-side variants and scoring each transcript against
the reference text recorded in ``ticket.md``. It builds **no filter** and changes **no
shipped code**; it is a measurement and a recommendation.

Variants measured
-----------------
1. ``baseline``      -- exactly what ships today (raw WAV through the shipped streaming path).
   ``baseline_rerun`` -- a deliberate repeat, to show the run-to-run noise floor.
2. ``highpass_60`` / ``highpass_85`` / ``highpass_120`` -- a second-order Butterworth
   high-pass applied to the audio **before** the shipped path (and therefore before the
   engine and the gate), at three corners in the 60-120 Hz region.
3. ``gain_0.85`` / ``gain_0.50`` -- the same audio scaled so its peak no longer reaches
   the int16 clip point, to separate clipping from everything else.
4. ``batch``         -- the one-shot ``transcribe()`` path on the **gate-passed** audio,
   so streaming and batch see the same samples (ticket 35's confound (a) controlled).

Normalisation and the run loop are **reused** from ticket 32's harness
(``../commit-cadence-diagnostic/measure_commit_cadence.py``) rather than re-implemented:
its ``word_diff``/``tokenise`` normalisation (case-folded, punctuation dropped, ``3.2``
kept intact) scores every transcript, and its ``run_utterance`` drives the shipped
``stream()`` path with the decoder seam recorded. This script adds the input-side
variants, the low-frequency analysis and the clipping analysis, and the scoring.

Honesty bounds (also printed in the report)
-------------------------------------------
* One 25 s recording of read speech is a **single sample**. The reader may have deviated
  from the reference (a stumble, a re-read, a paraphrase), so every absolute WER here is
  an **upper bound** on the model's real error rate. The load-bearing comparison is
  **variant versus variant on identical audio**, not the absolute number.
* The persisted WAV is an int16 round-trip of the live float32 capture and is known not to
  reproduce the live transcript (ticket 32 ``003-comment.md`` §"run-to-run caveat"). Every
  variant here is measured on the **same** WAV, so the comparison is fair even though the
  absolute transcript is not the one the user heard live.
* No microphone is recorded. No keyboard event is emitted. No clipboard is touched. No
  audio is written anywhere by this script.

Usage
-----
    uv run python docs/project/live-dictation-ux/accuracy-levers/measure_accuracy_levers.py \
        --wav /tmp/ges2t-commit-cadence-capture-20261009-195914.wav \
        --out /tmp/ges2t-accuracy-levers-report.txt

Design rules honoured here: full type hints including ``-> None``, Protocol-typed
parameters, and classes rather than positional tuples (ADR 0019).
"""

from __future__ import annotations

import argparse
import difflib
import importlib.util
import math
import sys
import time
import wave
from array import array
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, cast

from ges2t.audio.pw_record import SAMPLE_RATE
from ges2t.config import Config
from ges2t.interfaces import AudioBuffer
from ges2t.transcribers.whistle import WhistleTranscriber
from ges2t.transcribers.whistle import _ac_rms
from ges2t.transcribers.whistle_config import WhistleConfig

__all__ = ["main"]

#: The user's existing 25 s ground-truth recording (ticket 32's capture). It lives under
#: ``/tmp`` only and is never committed; if it is gone the measurement is impossible and
#: the script says so rather than substituting a clip.
DEFAULT_WAV: Path = Path("/tmp/ges2t-commit-cadence-capture-20261009-195914.wav")

#: Ticket 32's harness, reused for its normalisation and its run loop. Imported by path
#: because it is a docs script, not an installed module.
TICKET32_HARNESS: Path = (
    Path(__file__).resolve().parent.parent
    / "commit-cadence-diagnostic"
    / "measure_commit_cadence.py"
)

#: Directory for the report file only. No audio is ever written.
TMP_DIR: Path = Path("/tmp")

#: The high-pass corners the ticket asks for, in the 60-120 Hz region. Speech
#: fundamentals start around 85 Hz; the LF nuisance measured on this device is a
#: broadband ~20-25 Hz drift, so 60 Hz is the aggressive end and 120 Hz the mild end.
HIGHPASS_CORNERS_HZ: tuple[float, ...] = (60.0, 85.0, 120.0)

#: The gains applied for the level lever. Both keep the peak below the int16 clip point
#: (32767), so no sample can clip anywhere in the chain.
REDUCED_GAINS: tuple[float, ...] = (0.85, 0.50)

#: int16 magnitude at which this project has called a sample "at the clip point"
#: (ticket 32 ``004-review.md``: ``abs(sample) >= 32767 : 1262``).
CLIP_MAGNITUDE: int = 32767

#: How many reference words the best input-side lever must fix before a filter is worth
#: building. 4 of 64 words is ~6 % WER. The bar is explicit so the recommendation is a
#: rule applied to the numbers, not an adjective. A smaller change is a decoding-basin
#: flip: the transcript-equivalence section shows the same change is reachable by an
#: unrelated transform and is lost again at a different level.
MATERIAL_LEVER_WORDS: int = 4

#: The reference passage, exactly as recorded in ``ticket.md``. The bracketed pause line
#: is an annotation, not spoken text, and is not part of the reference.
REFERENCE_TEXT: str = (
    "Good morning. Last night's storm knocked out power to roughly four thousand homes "
    "across the northern valley, and crews expect to restore most of them by Friday "
    "afternoon. Officials say the river crested at 3.2 metres just after midnight, well "
    "below the flood barrier. Is that reading correct? Two low-lying roads remain closed "
    "tonight, and the ferry service resumes at half past six."
)


# --------------------------------------------------------------------------------------
# The reused ticket-32 surface, typed for mypy
# --------------------------------------------------------------------------------------


class WordDiff(Protocol):
    """The shape of ticket 32's ``WordDiff`` result, as this script consumes it."""

    substitutions: int
    insertions: int
    deletions: int
    reference_words: int

    @property
    def errors(self) -> int:
        """Total edit operations (substitutions + insertions + deletions)."""
        ...

    @property
    def wer(self) -> float:
        """Word error rate against the reference."""
        ...


class Ticket32(Protocol):
    """The parts of ticket 32's harness this script reuses."""

    def word_diff(self, reference: str, hypothesis: str) -> WordDiff:
        """Word-level edit counts between two texts, both normalised by ticket 32."""
        ...

    def tokenise(self, text: str) -> tuple[list[str], Any]:
        """Normalised words and their whitespace tokens (spans unused here)."""
        ...

    def read_wav(self, path: Path) -> AudioBuffer:
        """Read a 16-bit mono WAV into a float32 :class:`AudioBuffer`."""
        ...

    def chunk_buffer(
        self, buffer: AudioBuffer, chunk_seconds: float
    ) -> tuple[AudioBuffer, ...]:
        """Split a buffer into consecutive chunks (the shipped chunking)."""
        ...

    def run_utterance(
        self,
        transcriber: WhistleTranscriber,
        buffers: Sequence[AudioBuffer],
        *,
        label: str,
        path: Path | None,
        config: WhistleConfig,
        with_batch_reference: bool,
    ) -> Any:
        """Drive the shipped ``stream()`` over ``buffers`` and return the measurement."""
        ...

    def load_engine(
        self, config_path: Path | None = None
    ) -> tuple[WhistleTranscriber, WhistleConfig, Config]:
        """Load the user's config and the real engine through the shipped adapter."""
        ...


def load_ticket32() -> Ticket32:
    """Import ticket 32's harness by path and return its typed surface."""
    spec = importlib.util.spec_from_file_location(
        "ges2t_ticket32_measure", TICKET32_HARNESS
    )
    if spec is None or spec.loader is None:
        raise SystemExit(
            f"cannot import ticket 32's harness at {TICKET32_HARNESS}; it is the source "
            "of this measurement's normalisation and run loop"
        )
    module = importlib.util.module_from_spec(spec)
    # Register before executing: dataclasses (and other introspection) look the module up
    # in ``sys.modules`` by name while the decorators run.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return cast(Ticket32, module)


# --------------------------------------------------------------------------------------
# The report sink (the same two-sink pattern ticket 32 used)
# --------------------------------------------------------------------------------------


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
# Signal analysis: clipping and the low-frequency band
# --------------------------------------------------------------------------------------


def biquad_highpass(
    samples: Sequence[float], sample_rate: int, cutoff_hz: float
) -> list[float]:
    """Second-order Butterworth high-pass (RBJ biquad), forward only.

    A real-time filter would run exactly these difference equations over the incoming
    audio, so applying them to the whole recording before chunking is equivalent to
    filtering each chunk with the state carried across the boundary. ``Q = 1/sqrt(2)`` is
    the Butterworth pole placement. Forward-only (not zero-phase) is deliberate: it is
    what a capture chain can actually do, and phase shift does not matter to a
    magnitude-spectrum model.
    """
    omega = 2.0 * math.pi * cutoff_hz / sample_rate
    cos_omega = math.cos(omega)
    sin_omega = math.sin(omega)
    alpha = sin_omega / (2.0 * (1.0 / math.sqrt(2.0)))
    b0 = (1.0 + cos_omega) / 2.0
    b1 = -(1.0 + cos_omega)
    b2 = (1.0 + cos_omega) / 2.0
    a0 = 1.0 + alpha
    a1 = -2.0 * cos_omega
    a2 = 1.0 - alpha
    b0, b1, b2, a1, a2 = (b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0)
    out: list[float] = []
    x1 = x2 = y1 = y2 = 0.0
    for x0 in samples:
        y0 = b0 * x0 + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        out.append(y0)
        x2, x1 = x1, x0
        y2, y1 = y1, y0
    return out


def high_pass_output(
    samples: Sequence[float], sample_rate: int, corner_hz: float
) -> list[float]:
    """Return what a high-pass at ``corner_hz`` leaves: the audio the engine would see."""
    return biquad_highpass(samples, sample_rate, corner_hz)


def wav_int16_clip_stats(path: Path) -> tuple[int, int, int, int]:
    """Clip statistics in the WAV's own int16 domain (ticket 32's convention).

    Returns ``(samples at |v| >= 32767, samples at -32768, samples at 32767,
    total_samples)``. This is read from the file, **not** from the float32 buffer: the
    float conversion divides by 32768, so a sample stored as ``-32767`` becomes
    ``-0.99997`` and rounding it back loses it. Ticket 32 counted 1262 samples at
    ``abs(sample) >= 32767``, and only the int16 domain reproduces that figure.
    """
    with wave.open(str(path), "rb") as handle:
        raw = handle.readframes(handle.getnframes())
    samples = array("h")
    samples.frombytes(raw)
    at_clip = sum(1 for value in samples if abs(value) >= CLIP_MAGNITUDE)
    at_floor = sum(1 for value in samples if value == -32768)
    at_ceiling = sum(1 for value in samples if value == 32767)
    return at_clip, at_floor, at_ceiling, len(samples)


def pinned_float_count(samples: Sequence[float]) -> int:
    """How many float samples sit at or beyond the ``[-1, 1]`` rail the engine expects."""
    return sum(1 for value in samples if abs(value) >= 1.0)


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


def frame_rms_values(
    samples: Sequence[float], sample_rate: int, frame_seconds: float = 0.02
) -> list[float]:
    """Non-overlapping frame AC RMS values (the same 20 ms frame the shipped gate uses)."""
    frame_size = max(1, round(frame_seconds * sample_rate))
    return [
        _ac_rms(samples[start : start + frame_size])
        for start in range(0, len(samples) - frame_size + 1, frame_size)
    ]


# --------------------------------------------------------------------------------------
# Variants
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Variant:
    """One input-side variant: a transform plus the name and description to report."""

    name: str
    description: str
    transform: CallableTransform


class CallableTransform(Protocol):
    """A named transform from one buffer to another (or ``None`` to mark 'raw')."""

    def __call__(self, buffer: AudioBuffer) -> AudioBuffer:
        """Return the variant's version of ``buffer``."""
        ...


@dataclass(frozen=True)
class RawTransform:
    """The baseline: no transform at all."""

    def __call__(self, buffer: AudioBuffer) -> AudioBuffer:
        """Return ``buffer`` unchanged."""
        return buffer


@dataclass(frozen=True)
class HighPassTransform:
    """A second-order high-pass at ``corner_hz`` applied before the engine."""

    corner_hz: float

    def __call__(self, buffer: AudioBuffer) -> AudioBuffer:
        """Return the high-passed buffer at this corner."""
        return AudioBuffer(
            samples=biquad_highpass(buffer.samples, buffer.sample_rate, self.corner_hz),
            sample_rate=buffer.sample_rate,
        )


@dataclass(frozen=True)
class GainTransform:
    """A scale to a target peak amplitude, so nothing reaches the int16 clip point."""

    target_peak: float

    def __call__(self, buffer: AudioBuffer) -> AudioBuffer:
        """Return ``buffer`` scaled so its peak equals ``target_peak``."""
        peak = max((abs(value) for value in buffer.samples), default=0.0)
        gain = (self.target_peak / peak) if peak > 0.0 else 1.0
        return AudioBuffer(
            samples=[value * gain for value in buffer.samples],
            sample_rate=buffer.sample_rate,
        )


def build_variants() -> tuple[Variant, ...]:
    """The streaming variants this measurement runs, in report order."""
    variants: list[Variant] = [
        Variant("baseline", "the shipped path, raw WAV, no transform", RawTransform()),
        Variant(
            "baseline_rerun",
            "the shipped path again, to measure the run-to-run noise floor",
            RawTransform(),
        ),
    ]
    for corner in HIGHPASS_CORNERS_HZ:
        variants.append(
            Variant(
                f"highpass_{corner:.0f}",
                f"second-order Butterworth high-pass at {corner:.0f} Hz before the engine",
                HighPassTransform(corner),
            )
        )
    for gain in REDUCED_GAINS:
        variants.append(
            Variant(
                f"gain_{gain:.2f}",
                f"scaled to peak {gain:.2f} (no sample can reach the clip point)",
                GainTransform(gain),
            )
        )
    return tuple(variants)


# --------------------------------------------------------------------------------------
# Scoring against the reference
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AlignedError:
    """One aligned edit between the reference and a hypothesis (for the error listing)."""

    kind: str
    reference: tuple[str, ...]
    hypothesis: tuple[str, ...]

    @property
    def pair(self) -> tuple[str, str] | None:
        """The ``(reference word, hypothesis word)`` pair for a substitution, else None."""
        if self.kind == "substitution":
            return self.reference[0], self.hypothesis[0]
        return None


def align_errors(
    reference_words: Sequence[str], hypothesis_words: Sequence[str]
) -> tuple[AlignedError, ...]:
    """List the substituted / inserted / deleted words for one hypothesis.

    The WER numbers come from ticket 32's :func:`word_diff`; this listing exists so a
    human can see *which* words changed, which is how the "shared error across variants"
    test is done.
    """
    matcher = difflib.SequenceMatcher(
        a=list(reference_words), b=list(hypothesis_words), autojunk=False
    )
    errors: list[AlignedError] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace":
            reference = list(reference_words[i1:i2])
            hypothesis = list(hypothesis_words[j1:j2])
            for index in range(max(len(reference), len(hypothesis))):
                ref = reference[index : index + 1]
                hyp = hypothesis[index : index + 1]
                if ref and hyp:
                    errors.append(AlignedError("substitution", tuple(ref), tuple(hyp)))
                elif ref:
                    errors.append(AlignedError("deletion", tuple(ref), ()))
                else:
                    errors.append(AlignedError("insertion", (), tuple(hyp)))
        elif tag == "delete":
            errors.append(
                AlignedError("deletion", tuple(reference_words[i1:i2]), ())
            )
        elif tag == "insert":
            errors.append(
                AlignedError("insertion", (), tuple(hypothesis_words[j1:j2]))
            )
    return tuple(errors)


@dataclass(frozen=True)
class VariantResult:
    """The measured and scored outcome of one variant."""

    name: str
    description: str
    hypothesis: str
    substitutes: int
    insertions: int
    deletions: int
    reference_words: int
    fed_seconds: float
    duration_seconds: float
    wall_seconds: float
    language: str | None
    errors: tuple[AlignedError, ...]

    @property
    def total_errors(self) -> int:
        """Substitutions + insertions + deletions."""
        return self.substitutes + self.insertions + self.deletions

    @property
    def wer(self) -> float:
        """Word error rate against the reference."""
        if self.reference_words == 0:
            return 0.0
        return self.total_errors / self.reference_words

    @property
    def substitution_pairs(self) -> frozenset[tuple[str, str]]:
        """The set of ``(reference, hypothesis)`` substitution pairs in this variant."""
        return frozenset(
            pair for error in self.errors if (pair := error.pair) is not None
        )

    @property
    def is_batch(self) -> bool:
        """Whether this result came from the one-shot batch path."""
        return self.name.startswith("batch")


def score(
    label: str,
    description: str,
    hypothesis: str,
    *,
    fed_seconds: float,
    duration_seconds: float,
    wall_seconds: float,
    language: str | None,
    ticket32: Ticket32,
    reference: str,
) -> VariantResult:
    """Score one transcript against the reference using ticket 32's normalisation."""
    diff = ticket32.word_diff(reference, hypothesis)
    reference_words, _ = ticket32.tokenise(reference)
    hypothesis_words, _ = ticket32.tokenise(hypothesis)
    errors = align_errors(reference_words, hypothesis_words)
    return VariantResult(
        name=label,
        description=description,
        hypothesis=hypothesis,
        substitutes=diff.substitutions,
        insertions=diff.insertions,
        deletions=diff.deletions,
        reference_words=diff.reference_words,
        fed_seconds=fed_seconds,
        duration_seconds=duration_seconds,
        wall_seconds=wall_seconds,
        language=language,
        errors=errors,
    )


# --------------------------------------------------------------------------------------
# Report rendering
# --------------------------------------------------------------------------------------


def report_input_analysis(
    report: ConsoleReport,
    buffer: AudioBuffer,
    ticket32: Ticket32,
    *,
    path: Path,
) -> None:
    """Report the recording's level, clipping, and low-frequency content."""
    samples = buffer.samples
    duration = len(samples) / buffer.sample_rate
    mean = math.fsum(samples) / len(samples)
    ac_rms = _ac_rms(samples)
    peak = max(abs(value) for value in samples)
    at_clip, at_floor, at_ceiling, total = wav_int16_clip_stats(path)
    pinned = pinned_float_count(samples)
    report.heading("input analysis: level, clipping, and the low-frequency band")
    report.line(f"file            : {path}")
    report.line(
        f"duration        : {duration:.2f} s, {len(samples)} samples at "
        f"{buffer.sample_rate} Hz mono"
    )
    report.line(
        f"level           : peak {peak:.4f}, DC offset {mean:+.6f}, AC RMS {ac_rms:.4f}"
    )
    report.line(
        f"clipping (int16): {at_clip} sample(s) at |v| >= {CLIP_MAGNITUDE} "
        f"({100.0 * at_clip / total:.3f} % of {total}); {at_floor} at -32768 and "
        f"{at_ceiling} at 32767 -- ticket 32's convention, read from the file"
    )
    report.line(
        f"clipping (float): {pinned} sample(s) at |float| >= 1.0 in the buffer the "
        "engine actually sees (the int16-to-float conversion drops most of the int16 "
        "count, because -32767/32768 is 0.99997)"
    )
    report.line(
        "low-frequency band -- the component a high-pass at each corner would remove:"
    )
    frames = frame_rms_values(samples, buffer.sample_rate)
    report.line(
        f"  20 ms frame AC RMS over the whole recording: "
        f"min {min(frames):.5f} | p10 {percentile(frames, 0.10):.5f} | "
        f"median {percentile(frames, 0.50):.5f} | max {max(frames):.5f}"
    )
    quiet = sorted(frames)[: max(1, len(frames) // 20)]
    report.line(
        f"  quietest 5% of frames (a proxy for the pauses): median AC RMS "
        f"{percentile(quiet, 0.5):.5f}"
    )
    for corner in (*HIGHPASS_CORNERS_HZ, 150.0):
        filtered = high_pass_output(samples, buffer.sample_rate, corner)
        out_rms = _ac_rms(filtered)
        power_removed = 1.0 - (out_rms / ac_rms) ** 2 if ac_rms > 0 else 0.0
        report.line(
            f"  corner {corner:5.1f} Hz : high-pass output RMS {out_rms:.5f} "
            f"({100.0 * out_rms / ac_rms:5.1f} % of total AC RMS); "
            f"power removed {100.0 * power_removed:5.1f} %"
        )
    report.line(
        "  interpretation: 'power removed' is the fraction of the recording's AC power "
        "that lies below the corner -- the most a filter at that corner can act on. "
        "It is computed as 1 - (output RMS / input RMS)^2 and is bounded in [0, 1]."
    )


def report_variant(report: ConsoleReport, result: VariantResult) -> None:
    """Report one variant's score, then its transcript and its aligned errors."""
    report.heading(f"variant: {result.name}")
    report.line(f"description     : {result.description}")
    report.line(
        f"audio fed       : {result.fed_seconds:.2f} s of {result.duration_seconds:.2f} s "
        f"(the shipped silence gate dropped the rest)"
    )
    report.line(f"language        : {result.language!r}")
    report.line(f"wall            : {result.wall_seconds:.2f} s")
    report.line(
        f"WER             : {result.wer:.3f}  (substitutions {result.substitutes}, "
        f"insertions {result.insertions}, deletions {result.deletions}; "
        f"n={result.reference_words} reference words)"
    )
    report.line("hypothesis verbatim:")
    for line in wrap_text(result.hypothesis, width=100):
        report.line(f"  {line}")
    substitutions = [e for e in result.errors if e.kind == "substitution"]
    deletions = [e for e in result.errors if e.kind == "deletion"]
    insertions = [e for e in result.errors if e.kind == "insertion"]
    report.line(
        f"aligned edits   : {len(substitutions)} substitution(s), "
        f"{len(insertions)} insertion(s), {len(deletions)} deletion(s) (display only; "
        "the WER above is ticket 32's word_diff)"
    )
    for error in result.errors[:40]:
        if error.kind == "substitution":
            report.line(
                f"    sub  {' '.join(error.reference)!r} -> "
                f"{' '.join(error.hypothesis)!r}"
            )
        elif error.kind == "insertion":
            report.line(f"    ins  + {' '.join(error.hypothesis)!r}")
        else:
            report.line(f"    del  - {' '.join(error.reference)!r}")


def report_ranking(report: ConsoleReport, results: Sequence[VariantResult]) -> None:
    """Rank the streaming variants by WER and separate them from the batch path."""
    report.heading("ranking and comparison")
    ordered = sorted(results, key=lambda result: (result.wer, result.name))
    report.line("all variants, best WER first:")
    report.line(
        "  variant            WER    sub ins del  fed s   errors  note"
    )
    for result in ordered:
        report.line(
            f"  {result.name:<17} {result.wer:.3f}  {result.substitutes:3d} "
            f"{result.insertions:3d} {result.deletions:3d}  {result.fed_seconds:5.2f}  "
            f"{result.total_errors:6d}  {result.description}"
        )

    streaming = [result for result in results if not result.is_batch]
    by_name = {result.name: result for result in results}
    baseline = by_name.get("baseline")
    report.line()
    report.line("streaming-variant deltas against the baseline (same audio, same engine):")
    if baseline is None:
        report.line("  no baseline result was produced; cannot rank.")
        return
    for result in sorted(streaming, key=lambda item: item.wer):
        delta = result.wer - baseline.wer
        report.line(
            f"  {result.name:<17} WER {result.wer:.3f} ({delta:+.3f} vs baseline), "
            f"total errors {result.total_errors} vs {baseline.total_errors}"
        )
    best = min(streaming, key=lambda item: item.wer)
    worst = max(streaming, key=lambda item: item.wer)
    report.line(
        f"  best streaming variant  : {best.name} at WER {best.wer:.3f} "
        f"({best.wer - baseline.wer:+.3f} vs baseline)"
    )
    report.line(
        f"  worst streaming variant : {worst.name} at WER {worst.wer:.3f} "
        f"({worst.wer - baseline.wer:+.3f} vs baseline)"
    )


def report_error_stability(
    report: ConsoleReport, results: Sequence[VariantResult]
) -> None:
    """Test whether the baseline's errors survive the input-side variants.

    If every baseline substitution pair reappears unchanged in each variant, the errors
    are stable properties of the audio/model, not artefacts of a signal the variant
    removed. That is the measurement of the orchestrator's leading hypothesis.
    """
    baseline = next((r for r in results if r.name == "baseline"), None)
    report.heading("error stability: do the same words stay wrong?")
    if baseline is None:
        report.line("  no baseline result; cannot test error stability.")
        return
    base_pairs = baseline.substitution_pairs
    report.line(
        f"baseline has {baseline.substitutes} substitution(s) over "
        f"{baseline.reference_words} reference words: "
        + ", ".join(f"{ref!r}->{hyp!r}" for ref, hyp in sorted(base_pairs))
    )
    report.line()
    report.line(
        "  variant            retained baseline sub-pairs   new sub-pairs   net sub count"
    )
    for result in results:
        if result.name == "baseline":
            continue
        pairs = result.substitution_pairs
        retained = base_pairs & pairs
        new = pairs - base_pairs
        report.line(
            f"  {result.name:<17} {len(retained):>2}/{len(base_pairs):<2} "
            f"                    {len(new):>2}              {result.substitutes:>2}"
        )
    report.line()
    if all(
        (baseline.substitution_pairs & result.substitution_pairs)
        == baseline.substitution_pairs
        for result in results
        if result.name != "baseline" and not result.is_batch
    ):
        report.line(
            "  every baseline substitution pair survives every streaming variant "
            "unchanged: the errors are not caused by anything the variants remove."
        )
    else:
        report.line(
            "  at least one streaming variant changes which words are wrong; see the "
            "per-variant listings above."
        )


def report_transcript_equivalence(
    report: ConsoleReport, results: Sequence[VariantResult]
) -> None:
    """Group variants that produced the **identical** transcript.

    If several input-side variants land on the same transcript, the model has a small set
    of attractor outputs and a WER difference between two variants is a discrete basin
    flip, not a graded signal-quality improvement. That distinction is what decides
    whether a filter is a real lever or an arbitrary perturbation.
    """
    report.heading("transcript equivalence: how many distinct outputs are there?")
    groups: dict[str, list[str]] = {}
    for result in results:
        groups.setdefault(result.hypothesis, []).append(result.name)
    ordered = sorted(groups.items(), key=lambda item: (-len(item[1]), item[1][0]))
    for hypothesis, names in ordered:
        wers = ", ".join(
            f"{name}={next(r.wer for r in results if r.name == name):.3f}"
            for name in names
        )
        report.line(f"  transcript produced by: {', '.join(names)}")
        report.line(f"      ({wers})")
    report.line(
        f"  {len(results)} variant run(s) produced {len(groups)} distinct transcript(s)."
    )
    if len(groups) < len(results):
        report.line(
            "  Identical transcripts across different transforms mean the difference is a "
            "decoding-basin flip, not a graded effect of the signal the transform changed."
        )


def report_recommendation(
    report: ConsoleReport, results: Sequence[VariantResult]
) -> None:
    """State the filter recommendation and the bounds on the claim.

    The bar is made explicit: an input-side lever must fix at least
    :data:`MATERIAL_LEVER_WORDS` reference words before it is worth building. A smaller
    change is a basin flip -- the equivalence section shows the same change is reachable
    by an unrelated transform and is lost again at a different level -- so it is not a
    stable signal-quality improvement.
    """
    report.heading("recommendation and bounds")
    baseline = next((r for r in results if r.name == "baseline"), None)
    rerun = next((r for r in results if r.name == "baseline_rerun"), None)
    batch = next((r for r in results if r.is_batch), None)
    if baseline is not None and rerun is not None:
        if baseline.hypothesis == rerun.hypothesis:
            report.line(
                "  run-to-run noise floor: the baseline re-run reproduces the baseline "
                "transcript exactly, so a variant difference of one word is attributable "
                "to the variant."
            )
        else:
            report.line(
                "  run-to-run noise floor: the baseline re-run does NOT reproduce the "
                "baseline transcript; single-word differences between variants are "
                "within noise and must not be ranked."
            )
    if baseline is not None and batch is not None:
        report.line(
            f"  streaming vs batch on identical (gate-passed) audio: streaming "
            f"{baseline.wer:.3f}, batch {batch.wer:.3f} "
            f"({batch.wer - baseline.wer:+.3f} for batch). Ticket 35 owns the "
            "length-swept version of this question; this is one 25 s input."
        )
    streaming = [r for r in results if not r.is_batch and not r.name.endswith("rerun")]
    if baseline is not None and streaming:
        best = min(streaming, key=lambda item: item.wer)
        worst = max(streaming, key=lambda item: item.wer)
        spread = worst.wer - best.wer
        report.line(
            f"  input-side lever spread across streaming variants: {spread:.3f} WER "
            f"({best.name} {best.wer:.3f} to {worst.name} {worst.wer:.3f})."
        )
        fixed = baseline.total_errors - best.total_errors
        report.line(
            f"  the best input-side lever fixes {fixed} reference word(s) of "
            f"{baseline.reference_words} (the bar for 'worth building' here is "
            f"{MATERIAL_LEVER_WORDS})."
        )
        if fixed < MATERIAL_LEVER_WORDS:
            report.line(
                "  DO NOT BUILD A FILTER FROM THIS EVIDENCE. The best input-side lever "
                f"moves {fixed} word(s), below the {MATERIAL_LEVER_WORDS}-word bar, and "
                "the equivalence section shows those words move between decoding "
                "basins rather than tracking the removed signal."
            )
        else:
            report.line(
                "  the best input-side lever clears the bar; a filter is a candidate for "
                "a follow-up ticket that cites these numbers (this ticket builds none)."
            )
    report.line()
    report.line(
        "  What this supports: the recommendation is written from the numbers above. "
        "A high-pass only helps if the errors it fixes are caused by energy the filter "
        "removes; the input analysis reports exactly how much energy that is, and the "
        "error-stability and equivalence sections report whether the errors move in a "
        "way that tracks it."
    )
    report.line(
        "  What would change the answer: a variant that clears the material bar AND "
        "tracks the removed signal (the quieter the input, the larger the effect), "
        "reproduced on a second recording."
    )


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


# --------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Options:
    """Parsed command line."""

    wav_path: Path
    out_path: Path | None
    config_path: Path | None
    reference_file: Path | None


def resolve_reference(reference_file: Path | None) -> tuple[str, str]:
    """Return the ground-truth reference text and a label describing where it came from."""
    if reference_file is None:
        return REFERENCE_TEXT, "the passage recorded in ticket.md"
    if not reference_file.exists():
        raise SystemExit(f"reference file not found: {reference_file}")
    return reference_file.read_text(encoding="utf-8").strip(), f"{reference_file}"


def parse_args(argv: Sequence[str] | None) -> Options:
    """Parse the command line into :class:`Options`."""
    parser = argparse.ArgumentParser(
        description=(
            "Measure which input-side lever moves accuracy on the existing 25 s "
            "ground-truth recording (ticket 39). No filter is built, no shipped code "
            "changes, and no microphone is ever recorded."
        )
    )
    parser.add_argument(
        "--wav",
        default=str(DEFAULT_WAV),
        metavar="PATH",
        help=f"the existing 16 kHz mono ground-truth WAV (default {DEFAULT_WAV})",
    )
    parser.add_argument(
        "--out",
        default=None,
        metavar="PATH",
        help="report file (default /tmp/ges2t-accuracy-levers-<timestamp>-report.txt)",
    )
    parser.add_argument(
        "--reference-file",
        default=None,
        metavar="PATH",
        help=(
            "read the ground-truth text from PATH instead of the built-in ticket.md "
            "passage (used for a secondary cross-check input such as clip5.wav)"
        ),
    )
    parser.add_argument(
        "--config", default=None, metavar="PATH", help="config file to use"
    )
    args = parser.parse_args(argv)
    return Options(
        wav_path=Path(args.wav),
        out_path=Path(args.out) if args.out is not None else None,
        config_path=Path(args.config) if args.config is not None else None,
        reference_file=(
            Path(args.reference_file) if args.reference_file is not None else None
        ),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the measurement and return a process exit status."""
    options = parse_args(argv)
    report = ConsoleReport()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = options.out_path or (
        TMP_DIR / f"ges2t-accuracy-levers-{stamp}-report.txt"
    )

    report.line("=" * 72)
    report.line("Accuracy levers (ticket 39): which input-side lever moves WER?")
    report.line(f"run at : {datetime.now().isoformat(timespec='seconds')}")
    report.line(f"argv   : {list(sys.argv if argv is None else argv)}")
    report.line("=" * 72)

    if not options.wav_path.exists():
        report.line()
        report.line(
            f"the ground-truth recording is GONE at {options.wav_path}. Per the ticket "
            "this cannot be worked around by substituting a clip: the reference text "
            "belongs to that recording, and only the user can record a replacement. "
            "Nothing was measured."
        )
        report.write(out_path)
        print(f"\nreport written to {out_path}")
        return 1

    ticket32 = load_ticket32()
    transcriber, whistle_config, config = ticket32.load_engine(options.config_path)
    reference, reference_label = resolve_reference(options.reference_file)
    report.heading("engine and configuration")
    report.line(f"engine          : {config.transcriber.engine}")
    report.line(
        f"window_seconds  : {whistle_config.window_seconds}  "
        f"chunk_seconds: {whistle_config.chunk_seconds}  "
        f"language: {whistle_config.language!r}"
    )
    report.line(
        f"sample rate     : {SAMPLE_RATE} Hz mono float32 (ADR 0005)"
    )
    reference_words, _ = ticket32.tokenise(reference)
    report.line(
        f"reference       : {reference_label}, {len(reference_words)} words after "
        "ticket 32 normalisation"
    )

    buffer = ticket32.read_wav(options.wav_path)
    report_input_analysis(report, buffer, ticket32, path=options.wav_path)

    results: list[VariantResult] = []
    for variant in build_variants():
        variant_buffer = variant.transform(buffer)
        buffers = ticket32.chunk_buffer(variant_buffer, whistle_config.chunk_seconds)
        started = time.perf_counter()
        measurement = ticket32.run_utterance(
            transcriber,
            buffers,
            label=variant.name,
            path=None,
            config=whistle_config,
            with_batch_reference=False,
        )
        wall = time.perf_counter() - started
        results.append(
            score(
                variant.name,
                variant.description,
                measurement.final_text,
                fed_seconds=measurement.fed_seconds,
                duration_seconds=measurement.duration_seconds,
                wall_seconds=wall,
                language=measurement.final_language,
                ticket32=ticket32,
                reference=reference,
            )
        )

    # The batch path, on the same gate-passed audio streaming saw (ticket 35's
    # confound (a)): drive the shipped stream once more with the batch reference on,
    # and score the reference it computes.
    buffers = ticket32.chunk_buffer(buffer, whistle_config.chunk_seconds)
    started = time.perf_counter()
    baseline_measurement = ticket32.run_utterance(
        transcriber,
        buffers,
        label="baseline_for_batch",
        path=None,
        config=whistle_config,
        with_batch_reference=True,
    )
    wall = time.perf_counter() - started
    if baseline_measurement.batch_reference is not None:
        results.append(
            score(
                "batch",
                "one-shot transcribe() on the gate-passed audio (ticket 35's question)",
                baseline_measurement.batch_reference,
                fed_seconds=baseline_measurement.fed_seconds,
                duration_seconds=baseline_measurement.duration_seconds,
                wall_seconds=wall,
                language=None,
                ticket32=ticket32,
                reference=reference,
            )
        )
    else:
        report.line()
        report.line(
            "batch reference NOT COMPUTED: "
            + baseline_measurement.batch_reference_note
        )

    for result in results:
        report_variant(report, result)
    report_ranking(report, results)
    report_error_stability(report, results)
    report_transcript_equivalence(report, results)
    report_recommendation(report, results)

    report.heading("what this measurement does not establish")
    report.line(
        "  - this input is a single sample of read speech; every absolute WER is an "
        "upper bound because the reader may have deviated from the reference."
    )
    report.line(
        "  - the load-bearing comparison is variant versus variant on identical audio, "
        "not the absolute number."
    )
    report.line(
        "  - the persisted WAV is an int16 round-trip of the live float32 capture and is "
        "known not to reproduce the live transcript (ticket 32, 003-comment.md); the "
        "comparison is fair, the absolute transcript is not the one heard live."
    )
    report.line(
        "  - streaming vs batch is a single length here; ticket 35 owns the length sweep."
    )
    report.line(
        "  - a second recording (ideally quiet-room and noisy-room siblings from ticket "
        "31) is what would turn a single sample into a distribution."
    )

    report.write(out_path)
    report.line()
    report.line(f"full report written to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
