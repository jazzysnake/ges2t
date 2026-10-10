#!/usr/bin/env python3
"""Render the whisper.cpp streaming summaries into markdown tables.

Reads ``data/summary.jsonl`` (rebuilt by ``recompute_summary.py``) and writes
``data/timings.md``. Pure read; no model, no audio, no network.

Two decodes appear in the data:

* ``nt``  — ``whisper-cli -nt -np``, the **exact** decode the merged whisper.cpp report
  used (car-clip batch word_diff WER 0.062). This is the primary dataset for accuracy,
  stability and duty.
* ``ts``  — ``whisper-cli -ojf`` (token timestamps), used for the audio-lag measurement and
  the slide scheme. Its decode differs (car-clip batch WER 0.156), so its WER is not
  compared to the merged report's batch number.
"""

from __future__ import annotations

import json
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
SUMMARY = DATA / "summary.jsonl"

ORDER_CLIP = ["quiet-long", "commit", "clip30", "jfk", "zzz"]
CLIP_LABEL = {
    "quiet-long": "quiet-long",
    "commit": "car",
    "clip30": "clip30",
    "jfk": "jfk",
}
#: Merged-report batch word_diff WER for the human-referenced clips.
BATCH_WER = {"quiet-long": 0.153, "commit": 0.062, "jfk": 0.0, "clip30": None}


def fmt(value: object) -> str:
    """Render a value for a markdown cell."""
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return str(value)


def load() -> list[dict]:
    """Load the summary rows."""
    if not SUMMARY.exists():
        return []
    return [json.loads(line) for line in SUMMARY.read_text(encoding="utf-8").splitlines() if line]


def _clip(row: dict) -> str:
    """Clip order key for a row."""
    key = row.get("reference_key") or ""
    if key in ORDER_CLIP:
        return key
    if "clip30" in row["label"]:
        return "clip30"
    return "zzz"


def _is_silence(row: dict) -> bool:
    """Whether a row is the silence control."""
    return "silence" in row["label"]


def _model(row: dict) -> str:
    """Short model name."""
    return row["model"].replace("-q5_0", "")


def cap_table(rows: list[dict], latency: dict[tuple, float] | None = None) -> str:
    """The primary table: cap scheme, given decode.

    ``latency`` optionally supplies the real (timestamped) median commit lag keyed by
    ``(model, window, hop, reference_key)`` for the `-nt` table, whose own times are
    synthetic.
    """
    header = (
        "| model/backend | W/H | clip | passes | windows | duty | first commit (s) "
        "| latency (s) | prefix agree | regressed | rewritten | dup words "
        "| WER (stream) | WER (batch) |"
    )
    sep = "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"
    lines = [header, sep]
    for r in rows:
        batch = BATCH_WER.get(r["reference_key"])
        key = (r["model"], r["window_s"], r["hop_s"], r["reference_key"], r["backend"])
        lat = latency.get(key) if latency else r["commit_latency_median_s"]
        lines.append(
            "| {model}/{backend} | {w}/{h} | {clip} | {passes} | {windows} | {duty} "
            "| {first} | {lat} | {pfx} | {reg} | {rw} | {dup} | {wer} | {batch} |".format(
                model=_model(r),
                backend=r["backend"],
                w=fmt(r["window_s"]),
                h=fmt(r["hop_s"]),
                clip=CLIP_LABEL.get(r["reference_key"], r["reference_key"] or "silence"),
                passes=r["passes"],
                windows=r["windows"],
                duty=fmt(r["duty"]),
                first=fmt(r["first_commit_audio_s"]),
                lat=fmt(lat),
                pfx=fmt(r["prefix_agreement"]),
                reg=fmt(r["regressed_passes"]),
                rw=fmt(r["rewritten_pairs"]),
                dup=fmt(r["adjacent_duplicate_count"]),
                wer=fmt(r["wer_worddiff"]),
                batch=fmt(batch) if batch is not None else "n/a",
            )
        )
    return "\n".join(lines)


def latency_table(rows: list[dict]) -> str:
    """The timestamped twin: real per-commit audio lag."""
    header = "| model/backend | W/H | clip | commit latency median (s) | p90 (s) | max (s) |"
    sep = "|---|---|---|---|---|---|"
    lines = [header, sep]
    for r in rows:
        lines.append(
            "| {model}/{backend} | {w}/{h} | {clip} | {lat} | {p90} | {mx} |".format(
                model=_model(r),
                backend=r["backend"],
                w=fmt(r["window_s"]),
                h=fmt(r["hop_s"]),
                clip=CLIP_LABEL.get(r["reference_key"], r["reference_key"] or "?"),
                lat=fmt(r["commit_latency_median_s"]),
                p90=fmt(r["commit_latency_p90_s"]),
                mx=fmt(r["commit_latency_max_s"]),
            )
        )
    return "\n".join(lines)


def main() -> int:
    """Write ``data/timings.md``."""
    rows = load()
    nt_cap = [
        r
        for r in rows
        if r["decode"] == "nt" and r["scheme"] == "cap" and not _is_silence(r)
    ]
    ts_cap = [
        r
        for r in rows
        if r["decode"] == "ts" and r["scheme"] == "cap" and not _is_silence(r)
    ]
    slides = [r for r in rows if r["scheme"] == "slide"]
    silence = [r for r in rows if _is_silence(r)]
    cpu = [r for r in ts_cap if r["backend"] == "cpu"]

    key = lambda r: (ORDER_CLIP.index(_clip(r)), _model(r), r["window_s"], r["hop_s"])  # noqa: E731
    latency_by_key = {
        (r["model"], r["window_s"], r["hop_s"], r["reference_key"], r["backend"]): r[
            "commit_latency_median_s"
        ]
        for r in ts_cap
    }

    out: list[str] = []
    out.append("# whisper.cpp streaming — timings and agreement tables")
    out.append("")
    out.append("Rendered from `data/summary.jsonl` (rebuilt from `data/passes*/` by")
    out.append("`recompute_summary.py`). Per-pass transcripts are in `data/passes/`.")
    out.append("")
    out.append("Definitions:")
    out.append("")
    out.append("- **duty** = total `whisper-cli` wall / audio seconds. < 1 is the real-time")
    out.append("  condition (each pass must finish inside one hop). Includes the per-invocation")
    out.append("  model load.")
    out.append("- **first commit** = source-audio time of the first committing pass.")
    out.append("- **latency** = window end minus the audio time of the newest committed word")
    out.append("  (real token timestamps; see the latency table).")
    out.append("- **prefix agree** = agreed words / previous pass words over same-window pairs")
    out.append("  (1.0 = the new pass fully reproduces the previous one).")
    out.append("- **regressed** = same-window pairs where the new pass did not reproduce the words")
    out.append("  already committed in the window.")
    out.append("- **rewritten** = same-window pairs whose overlap text was not identical.")
    out.append("- **dup words** = adjacent repeated words in the committed text (the seam-")
    out.append("  duplication signature).")
    out.append("- **WER (batch)** is the merged report's one-shot `medium q5_0` word_diff number.")
    out.append("")
    out.append("## Primary — cap scheme, `-nt` decode (merged-report comparable)")
    out.append("")
    out.append("The latency column is taken from the timestamped twins below (the `-nt` decode")
    out.append("has no token times, so its own times are synthetic). Every other column is")
    out.append("measured on the `-nt` decode.")
    out.append("")
    out.append(cap_table(sorted(nt_cap, key=key), latency=latency_by_key))
    out.append("")
    out.append("## Commit latency — timestamped twins (`-ojf` decode)")
    out.append("")
    out.append(latency_table(sorted(ts_cap, key=key)))
    out.append("")
    out.append("## Fixed sliding window — slide scheme (`-ojf` decode, secondary)")
    out.append("")
    out.append(cap_table(sorted(slides, key=key)))
    out.append("")
    out.append("## `medium q5_0` CPU reference (`-ojf` decode)")
    out.append("")
    out.append(cap_table(sorted(cpu, key=key)))
    out.append("")
    out.append("## Silence control — 10 s of digital silence")
    out.append("")
    out.append(cap_table(sorted(silence, key=lambda r: r["model"])))
    out.append("")
    DATA.joinpath("timings.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"wrote {DATA / 'timings.md'} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
