#!/usr/bin/env python3
"""NeMo-Speech.cpp CLI discovery harness (docs script, not shipped code).

Runs ``nemo-speech transcribe`` over a WAV and records wall time, RTF, peak
child RSS, the verbatim transcript, the compiled backend line from stderr, and
WER against a reference -- using ticket 32's ``word_diff`` by path so the scores
are identical to the whisper.cpp reports.

Peak RSS comes from ``os.wait4``'s ``RUSAGE_CHILDREN.ru_maxrss`` (the Arch
``time`` package is absent and there is no sudo), the same technique as
``whisper-cpp-provider/measure_whisper_cpp.py``.

Usage
-----
    python3 measure_nemo_cli.py --cli <bin>/nemo-speech --model m.gguf \
        --audio clip.wav --device vulkan --reference-key jfk \
        --results data/cli_runs.jsonl --timeout 300
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import time
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence, cast

#: ticket 32's harness, imported by path so scoring matches the merged reports.
TICKET32_HARNESS: Path = (
    Path(__file__).resolve().parents[2]
    / "live-dictation-ux"
    / "commit-cadence-diagnostic"
    / "measure_commit_cadence.py"
)

#: The whisper-cpp-provider harness, imported for its reference passages verbatim.
PROVIDER_HARNESS: Path = (
    Path(__file__).resolve().parents[1] / "whisper-cpp-provider" / "measure_whisper_cpp.py"
)


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class CliRow:
    label: str
    device: str
    chunk_sec: float | None
    stream_flag: bool
    model_file: str
    model_bytes: int
    audio: str
    audio_seconds: float
    wall_seconds: float
    rtf: float
    peak_rss_mb: float
    exit_code: int
    timed_out: bool
    backend_line: str
    transcript: str
    reference_key: str | None
    wer_worddiff: float | None
    worddiff_sub: int | None
    worddiff_ins: int | None
    worddiff_del: int | None
    reference_words: int | None
    wer_std: float | None
    note: str


def wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / float(handle.getframerate())


def standard_wer(reference_words: Sequence[str], hyp_words: Sequence[str]) -> float:
    n, m = len(reference_words), len(hyp_words)
    if n == 0:
        return 0.0
    previous = list(range(m + 1))
    for i in range(1, n + 1):
        current = [i] + [0] * m
        for j in range(1, m + 1):
            cost = 0 if reference_words[i - 1] == hyp_words[j - 1] else 1
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
        previous = current
    return previous[m] / n


def run_cli(
    *,
    cli: Path,
    model: Path,
    audio: Path,
    device: str,
    stream_flag: bool,
    chunk_sec: float | None,
    timeout_s: float,
) -> tuple[str, str, float, float, int, bool]:
    import os
    import resource  # noqa: F401  (documented in the docstring)

    argv = [
        str(cli),
        "transcribe",
        str(audio),
        "--model",
        str(model),
        "--device",
        device,
    ]
    if stream_flag:
        argv.append("--stream")
    if chunk_sec is not None:
        argv += ["--asr.streaming.chunk_size", str(chunk_sec)]
    start = time.monotonic()
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    timed_out = False
    try:
        out, err = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        out, err = proc.communicate()
    wall = time.monotonic() - start
    # ru_maxrss is the high-water mark for reaped children; this process runs one.
    rss_mb = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024.0
    stderr_text = (err or b"").decode("utf-8", "replace")
    backend_line = next(
        (ln.strip() for ln in stderr_text.splitlines() if ln.startswith("[asr] backend")
        or ("head=" in ln and "backend=" in ln)),
        "",
    )
    exit_code = proc.returncode if proc.returncode is not None else -1
    # Guard against a leaked child holding the pipes.
    if proc.poll() is None:
        proc.kill()
    return (out or b"").decode("utf-8", "replace").strip(), backend_line, wall, rss_mb, exit_code, timed_out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", required=True, type=Path)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--device", default="vulkan")
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--chunk-sec", type=float, default=None)
    parser.add_argument("--label", default="run")
    parser.add_argument("--reference-key", default=None)
    parser.add_argument("--reference-text-file", type=Path, default=None)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--note", default="")
    parser.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args(argv)

    ticket32 = _load(TICKET32_HARNESS, "ges2t_ticket32_measure")
    provider = _load(PROVIDER_HARNESS, "ges2t_whisper_provider")
    references = cast(dict[str, str], provider.REFERENCES)

    reference: str | None = None
    if args.reference_text_file is not None:
        reference = args.reference_text_file.read_text(encoding="utf-8").strip()
    elif args.reference_key is not None:
        reference = references.get(args.reference_key)
        if reference is None:
            raise SystemExit(f"unknown reference key {args.reference_key!r}")

    audio_seconds = wav_seconds(args.audio)
    transcript, backend_line, wall, rss_mb, exit_code, timed_out = run_cli(
        cli=args.cli,
        model=args.model,
        audio=args.audio,
        device=args.device,
        stream_flag=args.stream,
        chunk_sec=args.chunk_sec,
        timeout_s=args.timeout,
    )

    wer_worddiff = sub = ins = deletion = ref_words = None
    wer_std = None
    if reference is not None:
        diff = ticket32.word_diff(reference, transcript)
        wer_worddiff = diff.wer
        sub, ins, deletion, ref_words = (
            diff.substitutions,
            diff.insertions,
            diff.deletions,
            diff.reference_words,
        )
        ref_seq, _ = ticket32.tokenise(reference)
        hyp_seq, _ = ticket32.tokenise(transcript)
        wer_std = standard_wer(ref_seq, hyp_seq)

    row = CliRow(
        label=args.label,
        device=args.device,
        chunk_sec=args.chunk_sec,
        stream_flag=args.stream,
        model_file=args.model.name,
        model_bytes=args.model.stat().st_size,
        audio=args.audio.name,
        audio_seconds=round(audio_seconds, 3),
        wall_seconds=round(wall, 3),
        rtf=round(audio_seconds / wall, 3) if wall > 0 else 0.0,
        peak_rss_mb=round(rss_mb, 1),
        exit_code=exit_code,
        timed_out=timed_out,
        backend_line=backend_line,
        transcript=transcript,
        reference_key=args.reference_key,
        wer_worddiff=wer_worddiff,
        worddiff_sub=sub,
        worddiff_ins=ins,
        worddiff_del=deletion,
        reference_words=ref_words,
        wer_std=round(wer_std, 4) if wer_std is not None else None,
        note=args.note + (" TIMEOUT" if timed_out else ""),
    )
    args.results.parent.mkdir(parents=True, exist_ok=True)
    with open(args.results, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(row), ensure_ascii=False) + "\n")
    print(json.dumps(asdict(row), ensure_ascii=False, indent=2))
    return 0 if exit_code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
