#!/usr/bin/env python3
"""whisper.cpp provider discovery harness (docs script, not shipped code).

Runs an already-built ``whisper-cli`` over a 16 kHz mono WAV and records the
measurement the discovery ticket asks for: model file and size, wall time, RTF,
peak RSS, and the verbatim transcript -- plus WER against a reference, scored
both with the project's own ``word_diff`` (reused by path from the
commit-cadence diagnostic, for comparability with ticket 39) and a standard
word edit distance.

Why this script exists rather than ``/usr/bin/time -v``: the Arch ``time``
package is not installed and there is no sudo here, so peak RSS is read from
``os.wait4``'s per-child ``rusage`` (``ru_maxrss``), which is the child's own
high-water mark.

Design rules honoured (ADR 0019): full type hints including ``-> None``,
Protocol-typed parameters, frozen data classes rather than positional tuples.
Nothing here is imported by the package or the test suite; it lives under
``docs/`` on purpose (ADR 0014).

Usage
-----
    uv run python docs/project/research/whisper-cpp-provider/measure_whisper_cpp.py run \
        --label tiny-q5_0-jfk --model <path.bin> --audio <clip.wav> \
        --backend cpu --bs 1 --bo 1 --threads 16 \
        --reference-key jfk --results <runs.jsonl>

Adding another clip later (the quiet-room recordings) needs only a
``--reference-text-file`` (or a registered key); no model is re-downloaded.
"""

from __future__ import annotations

import argparse
import difflib
import importlib.util
import json
import os
import resource
import subprocess
import sys
import time
import wave
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol, cast

__all__ = ["main"]

#: Absolute path to the whisper-cli binary (scratch, never in the repo).
DEFAULT_CLI: Path = Path.home() / ".cache/ges2t-discovery/whisper-cpp-provider/whisper.cpp/build/bin/whisper-cli"

#: The commit-cadence diagnostic, reused **by path** for its ``word_diff`` and
#: ``tokenise`` normalisation so this discovery and ticket 39 score identically.
TICKET32_HARNESS: Path = (
    Path(__file__).resolve().parents[2]
    / "live-dictation-ux"
    / "commit-cadence-diagnostic"
    / "measure_commit_cadence.py"
)

#: The user's 25 s ground-truth recording (also the ticket's primary input).
COMMIT_CAPTURE: Path = (
    Path.home()
    / ".cache/ges2t-discovery/whisper-cpp-provider/audio"
    / "ges2t-commit-cadence-capture-20261009-195914.wav"
)

#: The reference passage for the 25 s capture, as recorded in ``ticket.md``.
#: The bracketed pause line is an annotation, not spoken text -- it is omitted.
COMMIT_REFERENCE: str = (
    "Good morning. Last night's storm knocked out power to roughly four thousand homes "
    "across the northern valley, and crews expect to restore most of them by Friday "
    "afternoon. Officials say the river crested at 3.2 metres just after midnight, well "
    "below the flood barrier. Is that reading correct? Two low-lying roads remain closed "
    "tonight, and the ferry service resumes at half past six."
)

#: The standard ``jfk.wav`` reference (whisper.cpp's own sample clip).
JFK_REFERENCE: str = (
    "And so my fellow Americans, ask not what your country can do for you, "
    "ask what you can do for your country."
)

#: Quiet-room recording ``quiet-short.wav`` (13.51 s) reference (orchestrator).
QUIET_SHORT_REFERENCE: str = (
    "Hi Sam, the tiles arrive Tuesday. The invoice came to four hundred and twenty euros, "
    "so check whether their quote included delivery. I'll send the measurements tonight."
)

#: Quiet-room recording ``quiet-long.wav`` (23.81 s) reference (orchestrator).
QUIET_LONG_REFERENCE: str = (
    "Hi Sam, quick update on the kitchen order. The tiles arrive Tuesday morning, and the "
    "invoice came to four hundred and twenty euros, a bit more than we budgeted, so check "
    "whether their quote included delivery. The plumber can only start on the fourteenth, "
    "which means the painting may slip by a week. I'll confirm the exact measurements for "
    "the north wall tonight. Let me know if that still works for you."
)

#: Registered reference texts by key. A clip not listed here is transcribed but
#: not scored unless ``--reference-text-file`` names its passage.
REFERENCES: dict[str, str] = {
    "jfk": JFK_REFERENCE,
    "commit": COMMIT_REFERENCE,
    "quiet-short": QUIET_SHORT_REFERENCE,
    "quiet-long": QUIET_LONG_REFERENCE,
}


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
        """Normalised words and their whitespace tokens."""
        ...


def load_ticket32() -> Ticket32:
    """Import ticket 32's harness by path and return its typed surface."""
    spec = importlib.util.spec_from_file_location("ges2t_ticket32_measure", TICKET32_HARNESS)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot import ticket 32's harness at {TICKET32_HARNESS}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return cast(Ticket32, module)


@dataclass(frozen=True)
class RunRow:
    """One whisper-cli measurement, ready to serialise as a JSON line."""

    label: str
    backend: str
    model_file: str
    model_bytes: int
    quant: str
    audio: str
    audio_seconds: float
    threads: int
    decode: str
    beam_size: int
    best_of: int
    lang: str
    flash_attn: bool
    wall_seconds: float
    rtf: float
    peak_rss_mb: float
    exit_code: int
    transcript: str
    reference_key: str | None
    reference_words: int | None
    worddiff_wer: float | None
    worddiff_sub: int | None
    worddiff_ins: int | None
    worddiff_del: int | None
    std_wer: float | None
    std_edits: int | None
    note: str


def wav_seconds(path: Path) -> float:
    """Duration of a WAV file in seconds."""
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / float(handle.getframerate())


def standard_wer(
    reference_words: Sequence[str], hypothesis_words: Sequence[str]
) -> int:
    """Standard word edit distance (insert/delete/substitute, each cost 1)."""
    n, m = len(reference_words), len(hypothesis_words)
    previous = list(range(m + 1))
    for i in range(1, n + 1):
        current = [i] + [0] * m
        for j in range(1, m + 1):
            cost = 0 if reference_words[i - 1] == hypothesis_words[j - 1] else 1
            current[j] = min(
                previous[j] + 1,
                current[j - 1] + 1,
                previous[j - 1] + cost,
            )
        previous = current
    return previous[m]


def run_cli(
    *,
    cli: Path,
    model: Path,
    audio: Path,
    threads: int,
    beam_size: int,
    best_of: int,
    lang: str,
    flash_attn: bool,
    timeout_s: float,
    extra: Sequence[str] = (),
) -> tuple[str, float, float, int, bool]:
    """Run whisper-cli once; return (transcript, wall_s, rss_mb, exit_code, timed_out).

    The run is bounded by ``timeout_s``: on expiry the child is killed and
    reaped, so no spawned process can outlive this call. Peak RSS is
    ``RUSAGE_CHILDREN.ru_maxrss`` (kilobytes on Linux); this harness runs exactly
    one child per process, so that high-water mark is this run's footprint.
    """
    stdout_path = audio.parent.parent / "runs" / f".stdout.{os.getpid()}.txt"
    stderr_path = audio.parent.parent / "runs" / f".stderr.{os.getpid()}.txt"
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    argv = [
        str(cli),
        "-m", str(model),
        "-f", str(audio),
        "-l", lang,
        "-t", str(threads),
        "-bs", str(beam_size),
        "-bo", str(best_of),
        "-nt",
        "-np",
        *extra,
    ]
    if not flash_attn:
        argv.append("-nfa")
    start = time.monotonic()
    with open(stdout_path, "wb") as out, open(stderr_path, "wb") as err:
        proc = subprocess.Popen(argv, stdout=out, stderr=err)
        timed_out = False
        try:
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()
            proc.wait()
    wall = time.monotonic() - start
    rss_mb = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024.0
    exit_code = proc.returncode if proc.returncode is not None else -1
    # The child has been waited on, so it is gone before the transcript is read.
    text = stdout_path.read_text(encoding="utf-8", errors="replace").strip()
    stdout_path.unlink(missing_ok=True)
    stderr_path.unlink(missing_ok=True)
    return text, wall, rss_mb, exit_code, timed_out


def quant_of(name: str) -> str:
    """The quantisation tag of a ggml model filename."""
    stem = name[:-4] if name.endswith(".bin") else name
    for tag in ("q2_k", "q3_k", "q4_0", "q4_1", "q4_k", "q5_0", "q5_1", "q5_k", "q6_k", "q8_0"):
        if stem.endswith("-" + tag):
            return tag
    return "f16"


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and run one measurement."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--cli", type=Path, default=DEFAULT_CLI)
    parser.add_argument("--backend", default="cpu", choices=["cpu", "vulkan"])
    parser.add_argument("--threads", type=int, default=16)
    parser.add_argument("--bs", type=int, default=1, help="beam size (1 = greedy)")
    parser.add_argument("--bo", type=int, default=1, help="best-of")
    parser.add_argument("--lang", default="en")
    parser.add_argument("--no-flash-attn", action="store_true")
    parser.add_argument("--timeout", type=float, default=1200.0)
    parser.add_argument("--reference-key", default=None)
    parser.add_argument("--reference-text-file", type=Path, default=None)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--note", default="")
    parser.add_argument("--extra", nargs="*", default=[])
    args = parser.parse_args(argv)

    ticket32 = load_ticket32()
    reference: str | None = None
    if args.reference_text_file is not None:
        reference = args.reference_text_file.read_text(encoding="utf-8").strip()
    elif args.reference_key is not None:
        reference = REFERENCES.get(args.reference_key)
        if reference is None:
            raise SystemExit(f"unknown reference key {args.reference_key!r}")

    audio_seconds = wav_seconds(args.audio)
    model_bytes = args.model.stat().st_size
    text, wall, rss_mb, exit_code, timed_out = run_cli(
        cli=args.cli,
        model=args.model,
        audio=args.audio,
        threads=args.threads,
        beam_size=args.bs,
        best_of=args.bo,
        lang=args.lang,
        flash_attn=not args.no_flash_attn,
        timeout_s=args.timeout,
        extra=args.extra,
    )

    worddiff_wer = worddiff_sub = worddiff_ins = worddiff_del = None
    reference_words_count = None
    std_wer = std_edits = None
    if reference is not None:
        diff = ticket32.word_diff(reference, text)
        worddiff_wer = diff.wer
        worddiff_sub = diff.substitutions
        worddiff_ins = diff.insertions
        worddiff_del = diff.deletions
        reference_words_count = diff.reference_words
        ref_words, _ = ticket32.tokenise(reference)
        hyp_words, _ = ticket32.tokenise(text)
        std_edits = standard_wer(ref_words, hyp_words)
        std_wer = std_edits / len(ref_words) if ref_words else 0.0

    row = RunRow(
        label=args.label,
        backend=args.backend,
        model_file=args.model.name,
        model_bytes=model_bytes,
        quant=quant_of(args.model.name),
        audio=args.audio.name,
        audio_seconds=round(audio_seconds, 3),
        threads=args.threads,
        decode="greedy" if args.bs <= 1 else f"beam{args.bs}",
        beam_size=args.bs,
        best_of=args.bo,
        lang=args.lang,
        flash_attn=not args.no_flash_attn and "-nfa" not in args.extra,
        wall_seconds=round(wall, 3),
        rtf=round(audio_seconds / wall, 3) if wall > 0 else 0.0,
        peak_rss_mb=round(rss_mb, 1),
        exit_code=exit_code,
        transcript=text,
        reference_key=args.reference_key,
        reference_words=reference_words_count,
        worddiff_wer=worddiff_wer,
        worddiff_sub=worddiff_sub,
        worddiff_ins=worddiff_ins,
        worddiff_del=worddiff_del,
        std_wer=std_wer,
        std_edits=std_edits,
        note=args.note + (" TIMEOUT" if timed_out else ""),
    )
    args.results.parent.mkdir(parents=True, exist_ok=True)
    with open(args.results, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(row), ensure_ascii=False) + "\n")
    print(json.dumps(asdict(row), ensure_ascii=False, indent=2))
    return 0 if exit_code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
