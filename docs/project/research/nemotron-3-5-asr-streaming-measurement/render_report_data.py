#!/usr/bin/env python3
"""Curate the committed evidence for the Nemotron streaming report.

Copies the small, re-checkable artefacts out of the scratch dir into the
report's ``data/`` folder and renders the report tables. The bulk (the raw
per-partial JSON with word lists, the sliced WAVs, the engine, the model)
stays in scratch, which the report names.

Usage: PYTHONPATH=src python3 render_report_data.py
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SC = Path.home() / ".cache/ges2t-discovery" / "nemotron-3-5-asr-streaming-measurement"
DATA = HERE / "data"

# Per-partial traces are committed for every streaming, paced, silence, and sweep run so
# that ``analyze_streaming.py`` reproduces ``analysis.jsonl`` from the repo alone. The
# redundant ``words`` array is dropped (reconstructible from the transcript, and the
# analyzer does not use it); ``word_times`` is kept for the algorithmic-lag metric.
def collect_traces() -> list[tuple[Path, str]]:
    traces: list[tuple[Path, str]] = []
    for path in sorted((SC / "partials").glob("*.jsonl")):
        traces.append((path, f"partials/{path.name}"))
    for path in sorted((SC / "sweep").glob("*.jsonl")):
        if path.name != "chunk_sweep.jsonl":
            traces.append((path, f"sweep/{path.name}"))
    return traces


def full_partial(line: str) -> str:
    d = json.loads(line)
    d.pop("words", None)
    return json.dumps(d, ensure_ascii=False)


def load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def main() -> int:
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "partials").mkdir(exist_ok=True)
    (DATA / "sweep").mkdir(exist_ok=True)

    shutil.copy(SC / "evidence" / "doctor.txt", DATA / "engine_doctor.txt")
    shutil.copy(SC / "cli_runs.jsonl", DATA / "cli_runs.jsonl")
    shutil.copy(SC / "stream_summary.jsonl", DATA / "stream_summary.jsonl")
    shutil.copy(SC / "analysis.jsonl", DATA / "analysis.jsonl")
    shutil.copy(SC / "paced_summary.jsonl", DATA / "paced_summary.jsonl")
    shutil.copy(SC / "sweep" / "chunk_sweep.jsonl", DATA / "chunk_sweep.jsonl")

    traces = collect_traces()
    kept: set[Path] = set()
    for src, rel in traces:
        out = DATA / rel
        kept.add(out)
        with open(out, "w", encoding="utf-8") as handle:
            for line in src.read_text().splitlines():
                if line:
                    handle.write(full_partial(line) + "\n")
    # Drop any stale traces from a previous curation pass.
    for stale in list((DATA / "partials").glob("*.jsonl")) + list(
        (DATA / "sweep").glob("*.jsonl")
    ):
        if stale not in kept:
            stale.unlink()

    # Model revision evidence.
    main_meta = json.loads((SC / "hf-model-main.json").read_text())
    gguf = next(
        s for s in main_meta["siblings"] if s["rfilename"].endswith(".gguf")
    )
    (DATA / "model_revision.md").write_text(
        "# Resolved model revision\n\n"
        "Resolved 2026-10-14 from the HF API (`?blobs=true`) and the engine's own\n"
        "`share/nemo-speech/model-index.json`. The two revisions are **different commits**.\n\n"
        "| source | revision | size (B) | sha256 |\n"
        "|---|---|---:|---|\n"
        f"| HF `main` | `{main_meta['sha']}` | {gguf['size']} | `{gguf['lfs']['sha256']}` |\n"
        "| **engine index (chosen)** | `1c8deaecc64b91f034d73e08dd8b64625eb3395d` | "
        "741548352 | `a5c435f294eea8f88ce68dd27b8c3bfea7f777cb2fbba04fcd30eaa555f429ae` |\n\n"
        "The downloaded GGUF is the engine-index revision; its sha256 verified with\n"
        "`sha256sum -c` (OK). The HF `main` pointer is `ea30d66d…` at 742,090,464 B,\n"
        "sha256 `3fc991d3…` — 542,112 B larger, a different commit the engine does not\n"
        "pin. `huggingface_hub`'s `/api/models?revision=<sha>` fell back to `main`, so the\n"
        "pinned revision was confirmed with the `resolve/<sha>` LFS headers instead\n"
        "(`x-repo-commit`, `x-linked-size`, `x-linked-etag`).\n",
        encoding="utf-8",
    )

    _render_tables()
    print("wrote", DATA)
    return 0


def _render_tables() -> None:
    cli = load(DATA / "cli_runs.jsonl")
    stream = load(DATA / "stream_summary.jsonl")
    analysis = {r["label"]: r for r in load(DATA / "analysis.jsonl")}
    paced = load(DATA / "paced_summary.jsonl")

    lines: list[str] = []
    lines.append("# Rendered measurement tables\n")
    lines.append("All numbers come from `cli_runs.jsonl`, `stream_summary.jsonl`,")
    lines.append("`analysis.jsonl`, and `paced_summary.jsonl` beside this file.\n")

    lines.append("## CLI batch — wall, RTF, peak RSS, accuracy\n")
    lines.append("| label | audio s | wall s | RTF | RSS MB | wer(word_diff) | wer(std) | backend |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---|")
    for r in cli:
        wer = "-" if r["wer_worddiff"] is None else f"{r['wer_worddiff']:.3f}"
        std = "-" if r["wer_std"] is None else f"{r['wer_std']:.3f}"
        be = r["backend_line"].split("backend=")[-1]
        lines.append(
            f"| {r['label']} | {r['audio_seconds']:.2f} | {r['wall_seconds']:.2f} | "
            f"{r['rtf']:.2f} | {r['peak_rss_mb']:.1f} | {wer} | {std} | {be} |"
        )

    lines.append("\n## C-ABI streaming — throughput, memory, latency\n")
    lines.append("| label | audio s | stream s | RTF | RSS MB | VRAM MB | interims | first text (s audio) | steady p50 ms | steady p90 ms |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in stream:
        vram = "-" if r["vram_mb"] in (None, 0.0) else f"{r['vram_mb']:.0f}"
        lines.append(
            f"| {r['label']} | {r['audio_s']:.2f} | {r['stream_wall_s']:.2f} | {r['rtf']:.2f} | "
            f"{r['peak_rss_mb']:.1f} | {vram} | {r['n_interims']} | "
            f"{r['first_interim_audio_s']} | {r['steady_ms_p50']} | {r['steady_ms_p90']} |"
        )

    lines.append("\n## Committed-delta stability\n")
    lines.append("| label | interims | append-only (char) frac | rewritten pairs | monotone commits | adjacent dups | algorithmic lag med s | WER (stream) |")
    lines.append("|---|---:|---:|---:|---|---:|---:|---:|")
    for r in analysis.values():
        lines.append(
            f"| {r['label']} | {r['n_interims']} | {r['append_only_char_frac']} | "
            f"{r['rewritten_char_pairs']} | {r['monotone_committed_words']} | "
            f"{r['adjacent_duplicate_words']} | {r['algorithmic_lag_median_s']} | "
            f"{r.get('wer_stream_worddiff', '-')} |"
        )

    lines.append("\n## Real-time paced runs\n")
    lines.append("| label | audio s | stream s | first text wall s | first text audio s | steady p50 ms | steady p90 ms |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for r in paced:
        lines.append(
            f"| {r['label']} | {r['audio_s']:.2f} | {r['stream_wall_s']:.2f} | "
            f"{r['first_interim_wall_s']} | {r['first_interim_audio_s']} | "
            f"{r['steady_ms_p50']} | {r['steady_ms_p90']} |"
        )

    lines.append("\n## Chunk-size sweep (Vulkan, `rnnt_right_context` sets the step)\n")
    lines.append("| label | step ms | stream s | RTF | interims | steady p50 ms | steady p90 ms | first text (s audio) |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for r in load(DATA / "chunk_sweep.jsonl"):
        lines.append(
            f"| {r['label']} | {r['chunk_ms']} | {r['stream_wall_s']:.2f} | {r['rtf']:.2f} | "
            f"{r['n_interims']} | {r['steady_ms_p50']} | {r['steady_ms_p90']} | "
            f"{r['first_interim_audio_s']} |"
        )

    (DATA / "timings.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
