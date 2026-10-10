#!/usr/bin/env python3
"""Render the whisper.cpp discovery tables from ``data/runs.jsonl``.

The raw rows are the source of truth; this script only aggregates them (medians
over repeat runs) and formats markdown. It is a docs script, not shipped code.

Usage
-----
    uv run python docs/project/research/whisper-cpp-provider/render_tables.py \
        --runs data/runs.jsonl --out data/timings.md
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ["main"]

#: The canonical car/noisy clip.
CAR: str = "ges2t-commit-cadence-capture-20261009-195914.wav"


@dataclass(frozen=True)
class Key:
    """A grouping key: one measured configuration."""

    backend: str
    model: str
    decode: str
    bs: int
    audio: str


def load(runs: Path) -> list[dict[str, Any]]:
    """Read the JSON-lines run log."""
    return [json.loads(line) for line in runs.read_text().splitlines() if line.strip()]


def group(rows: list[dict[str, Any]]) -> dict[Key, list[dict[str, Any]]]:
    """Group rows by measured configuration, preserving no order."""
    out: dict[Key, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        out[Key(row["backend"], row["model_file"], row["decode"], row["beam_size"], row["audio"])].append(row)
    return out


def wer_of(rows: list[dict[str, Any]]) -> tuple[float, float] | None:
    """(worddiff_wer, std_wer) for a config, or None when unscored."""
    scored = [r for r in rows if r["worddiff_wer"] is not None]
    if not scored:
        return None
    assert len({r["worddiff_wer"] for r in scored}) == 1, "WER must be deterministic per config"
    assert len({r["std_wer"] for r in scored}) == 1, "WER must be deterministic per config"
    return scored[0]["worddiff_wer"], scored[0]["std_wer"]


def timing_cell(rows: list[dict[str, Any]]) -> str:
    """Median [min-max] wall seconds and median RTF over the repeats."""
    walls = sorted(r["wall_seconds"] for r in rows)
    rtfs = sorted(r["rtf"] for r in rows)
    med_w = statistics.median(walls)
    if len(walls) == 1:
        return f"{med_w:.2f} \\| {rtfs[0]:.2f}"
    return f"{med_w:.2f} ({walls[0]:.2f}-{walls[-1]:.2f}) \\| {statistics.median(rtfs):.2f}"


def n_of(rows: list[dict[str, Any]]) -> int:
    """Number of runs in the group."""
    return len(rows)


def table(
    title: str,
    groups: dict[Key, list[dict[str, Any]]],
    keys: list[Key],
    labels: dict[Key, str],
) -> str:
    """Render one markdown table for an explicit, ordered list of keys."""
    lines = [f"### {title}", "", "| config | n | wall_s median (min-max) \\| RTF | peak RSS MB | word_diff WER | std WER |", "|---|---|---|---|---|---|---|"]
    for key in keys:
        rows = groups.get(key)
        if not rows:
            lines.append(f"| {labels[key]} | 0 | MISSING | | | |")
            continue
        wer = wer_of(rows)
        wd, std = wer if wer else (float("nan"), float("nan"))
        rss = statistics.median(r["peak_rss_mb"] for r in rows)
        lines.append(
            f"| {labels[key]} | {n_of(rows)} | {timing_cell(rows)} | {rss:.0f} "
            f"| {wd:.3f} | {std:.3f} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    """Render and write the tables."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    rows = load(args.runs)
    groups = group(rows)

    def K(backend: str, model: str, audio: str, decode: str = "greedy", bs: int = 1) -> Key:
        return Key(backend, model, decode, bs, audio)

    def lab(backend: str, model: str, audio: str, decode: str = "greedy", bs: int = 1) -> str:
        return f"{backend} {model} [{decode}] on {audio}"

    sections: list[str] = [
        "# whisper.cpp discovery — timings and accuracy tables",
        "",
        "Rendered from `data/runs.jsonl`. `wall_s median (min-max) | RTF` summarises repeat runs;",
        "accuracy is deterministic per configuration (asserted in the renderer). RTF = audio_s / wall_s.",
        "`word_diff` WER is the project's metric; `std` WER is a standard word edit distance.",
        "",
    ]

    # A: greedy q5_0 sweep
    sweep = [f"ggml-{m}-q5_0.bin" for m in ("tiny", "base", "small", "medium", "large-v3-turbo")]
    for audio in (CAR, "jfk.wav"):
        keys = [K("cpu", m, audio) for m in sweep]
        sections.append(table(f"Greedy q5_0 sweep — CPU — {audio}", groups, keys, {k: lab("cpu", k.model, audio) for k in keys}))

    # B: quant effect
    for audio in (CAR,):
        for size in ("small", "medium"):
            keys = [K("cpu", f"ggml-{size}-q5_0.bin", audio), K("cpu", f"ggml-{size}-q8_0.bin", audio), K("cpu", f"ggml-{size}.bin", audio)]
            sections.append(table(f"Quantisation — {size} — CPU — {audio}", groups, keys, {k: lab("cpu", k.model, audio) for k in keys}))

    # C: decode
    keys = [K("cpu", "ggml-small-q5_0.bin", CAR), K("cpu", "ggml-small-q5_0.bin", CAR, "beam5", 5),
            K("cpu", "ggml-large-v3-turbo-q5_0.bin", CAR), K("cpu", "ggml-large-v3-turbo-q5_0.bin", CAR, "beam5", 5)]
    sections.append(table("Greedy vs beam-5 — CPU — car clip", groups, keys, {k: lab("cpu", k.model, CAR, k.decode, k.bs) for k in keys}))

    # D: .en vs multi
    keys = [K("cpu", "ggml-base-q5_0.bin", CAR), K("cpu", "ggml-base.en-q5_0.bin", CAR)]
    sections.append(table(".en vs multilingual — base q5_0 — CPU — car clip", groups, keys, {k: lab("cpu", k.model, CAR) for k in keys}))

    # E: CPU vs Vulkan
    for audio in (CAR, "jfk.wav", "quiet-long.wav"):
        keys = []
        for m in ("ggml-small-q5_0.bin", "ggml-medium-q5_0.bin", "ggml-large-v3-turbo-q5_0.bin"):
            keys.append(K("cpu", m, audio))
            keys.append(K("vulkan", m, audio))
        sections.append(table(f"CPU vs Vulkan — {audio}", groups, keys, {k: lab(k.backend, k.model, audio) for k in keys}))

    # F: second condition
    for audio in ("quiet-short.wav", "quiet-long.wav"):
        keys = [K("cpu", m, audio) for m in [
            "ggml-tiny-q5_0.bin", "ggml-base-q5_0.bin", "ggml-base.en-q5_0.bin",
            "ggml-small-q5_0.bin", "ggml-medium-q5_0.bin", "ggml-large-v3-turbo-q5_0.bin"]]
        sections.append(table(f"Second condition (quiet room) — CPU — {audio}", groups, keys, {k: lab("cpu", k.model, audio) for k in keys}))

    # G: window / length
    keys = []
    for m in ("ggml-small-q5_0.bin", "ggml-medium-q5_0.bin", "ggml-large-v3-turbo-q5_0.bin"):
        keys.append(K("cpu", m, "clip5.wav"))
        keys.append(K("vulkan", m, "clip5.wav"))
    keys += [K("cpu", "ggml-medium-q5_0.bin", "clip30.wav"), K("vulkan", "ggml-medium-q5_0.bin", "clip30.wav"), K("vulkan", "ggml-large-v3-turbo-q5_0.bin", "clip30.wav")]
    sections.append(table("Per-window cost (5 s clip5) and 30 s clip30 — no reference", groups, keys, {k: lab(k.backend, k.model, k.audio) for k in keys}))

    # H: transcripts
    seen: set[tuple[str, str]] = set()
    lines = ["### Verbatim transcripts (car clip, greedy, q5_0 unless noted)", ""]
    for row in rows:
        if row["audio"] != CAR or row["backend"] != "cpu":
            continue
        key = (row["model_file"], row["decode"])
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- **{row['model_file']} [{row['decode']}]**, word_diff WER {row['worddiff_wer']:.3f}, std WER {row['std_wer']:.3f}:")
        lines.append(f"  > {row['transcript']}")
    lines.append("")
    sections.append("\n".join(lines))

    args.out.write_text("\n".join(sections) + "\n", encoding="utf-8")
    print(f"wrote {args.out} ({len(rows)} runs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
