#!/usr/bin/env python3
"""Rebuild ``data/summary.jsonl`` from the saved per-pass JSONL.

Decouples metric changes from the expensive whisper-cli runs: everything the summary
needs is already in ``data/passes/<label>.jsonl``. Read-only over the passes; writes
``data/summary.jsonl``. No model, no audio, no network.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
PASSES = HERE / "data" / "passes"
PASSES_TS = HERE / "data" / "passes_ojf"
SUMMARY = HERE / "data" / "summary.jsonl"


def load_harness() -> Any:
    """Import the measurement harness (for ``REFERENCE``s and ``summarise``)."""
    spec = importlib.util.spec_from_file_location("measure_streaming", HERE / "measure_streaming.py")
    if spec is None or spec.loader is None:
        raise SystemExit("cannot load measure_streaming.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def parse_label(label: str, harness: Any) -> tuple[dict[str, Any], str | None]:
    """Derive the combo metadata and reference key from a label and its rows."""
    m = re.search(r"-w(\d+)h(\d+)-(.+)$", label)
    if m is None:
        raise SystemExit(f"unparseable label {label!r}")
    window, hop, clip = int(m.group(1)), int(m.group(2)), m.group(3)
    scheme = "cap" if label.startswith(("cap", "ntcap")) else "slide"
    backend = "cpu" if "-cpu" in label else "vulkan"
    model = "turbo-q5_0" if "turbo" in label else "small-q5_0" if "small" in label else "medium-q5_0"
    reference_key = clip if clip in harness.REFERENCES else None
    return (
        {
            "label": label,
            "scheme": scheme,
            "backend": backend,
            "model": model,
            "window_s": float(window),
            "hop_s": float(hop),
            "clip": clip,
        },
        reference_key,
    )


def rebuild(label: str, harness: Any, passes_dir: Path) -> dict[str, Any] | None:
    """Rebuild one summary row from its saved passes."""
    path = passes_dir / f"{label}.jsonl"
    if not path.exists():
        return None
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if not rows:
        return None
    meta, reference_key = parse_label(label, harness)
    passes: list[Any] = []
    for r in rows:
        passes.append(
            harness.PassRecord(
                index=r["index"],
                scheme=r["scheme"],
                window_start_s=r["window_start_s"],
                window_end_s=r["window_end_s"],
                buffer_s=r["buffer_s"],
                wall_s=r["wall_s"],
                transcript=r["transcript"],
                words=(),
                overlap_prev_words=tuple(r["overlap_prev_all"]),
                overlap_cur_words=tuple(r["overlap_cur_all"]),
                agreed=r["agreed"],
                committed_new=tuple(r["committed_new"]),
                commit_audio_s=r.get("commit_audio_s"),
                flush_tail_words=tuple(r["flush_tail"]),
                flushed=r["flushed"],
                committed_total_words=r["committed_total_words"],
                pending_words=r["pending_words"],
                stability_matched=r["stability_matched"],
                stability_total=r["stability_total"],
                reproduces_committed=r["reproduces_committed"],
            )
        )
    committed = rebuild_committed_two_pass(passes, meta["scheme"])
    result = harness.ComboResult(
        label=label,
        model=meta["model"],
        backend=meta["backend"],
        scheme=meta["scheme"],
        window_s=meta["window_s"],
        hop_s=meta["hop_s"],
        audio=label,
        audio_s=0.0,
        passes=passes,
        committed_words=committed,
    )
    # audio_s was not stored; recover it from the max window end.
    result.audio_s = max(p.window_end_s for p in passes)
    reference = harness.REFERENCES.get(reference_key) if reference_key else None
    summary = harness.summarise(
        result, reference=reference, batch_reference=None, reference_key=reference_key
    )
    # Override the committed text with the count-based rebuild (the harness's inline
    # watermark was time-based; the count-based rule is the one committed text must obey).
    summary["committed_text"] = " ".join(committed)
    if reference is not None:
        t32 = harness.load_ticket32()
        diff = t32.word_diff(reference, summary["committed_text"])
        summary["wer_worddiff"] = round(diff.wer, 4)
        summary["worddiff_sub"] = diff.substitutions
        summary["worddiff_ins"] = diff.insertions
        summary["worddiff_del"] = diff.deletions
        summary["reference_words"] = diff.reference_words
        ref_seq, _ = t32.tokenise(reference)
        hyp_seq, _ = t32.tokenise(summary["committed_text"])
        summary["wer_std"] = round(std_wer(ref_seq, hyp_seq), 4)
    summary["prefix_agreement"] = prefix_agreement(passes, meta["scheme"])
    # Sensitivity: if the flush tail (whisper's stop()-commit of the cut-off buffer) were
    # NOT committed, how good is the committed text that the two-pass rule alone produced?
    agreed_only = " ".join(rebuild_committed_agreed_only(passes, meta["scheme"]))
    summary["committed_text_two_pass_only"] = agreed_only
    summary["wer_worddiff_two_pass_only"] = (
        round(harness.load_ticket32().word_diff(reference, agreed_only).wer, 4)
        if reference
        else None
    )
    summary["flush_tail_words"] = sum(len(p.flush_tail_words) for p in passes)
    summary["decode"] = "ts" if label.startswith(("cap-", "slide-")) else "nt"
    # Seam-duplication evidence: adjacent repeated words in the committed text are the
    # signature of a boundary word committed by the flush tail and again by the next
    # window (e.g. "your country country", "restore restore", "for you for you").
    dups = [
        committed[i]
        for i in range(len(committed) - 1)
        if committed[i] == committed[i + 1]
    ]
    summary["adjacent_duplicate_words"] = dups
    summary["adjacent_duplicate_count"] = len(dups)
    return summary


def std_wer(reference_words: list[str], hyp_words: list[str]) -> float:
    """Standard word edit distance / reference length."""
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


def rebuild_committed_two_pass(passes: list[Any], scheme: str) -> list[str]:
    """Count-based committed text: agreed prefixes plus every flush tail."""
    committed: list[str] = []
    if scheme != "cap":
        for p in passes:
            committed.extend(p.committed_new)
            committed.extend(p.flush_tail_words)
        return committed
    window_len = 0
    for p in passes:
        cur = list(p.overlap_cur_words)
        if p.agreed > window_len:
            committed.extend(cur[window_len : p.agreed])
            window_len = p.agreed
        if p.flushed:
            committed.extend(cur[window_len:])
            window_len = 0
    return committed


def rebuild_committed_agreed_only(passes: list[Any], scheme: str) -> list[str]:
    """Committed text from the two-pass rule only (flush tails dropped)."""
    if scheme != "cap":
        return [w for p in passes for w in p.committed_new]
    committed: list[str] = []
    window_len = 0
    for p in passes:
        cur = list(p.overlap_cur_words)
        if p.agreed > window_len:
            committed.extend(cur[window_len : p.agreed])
            window_len = p.agreed
        if p.flushed:
            window_len = 0
    return committed


def prefix_agreement(passes: list[Any], scheme: str) -> float | None:
    """Fraction of the previous pass's words the current pass reproduces (same-window)."""
    pairs = [p for p in passes if p.index > 0 and p.overlap_prev_words]
    if not pairs:
        return None
    if scheme == "cap":
        denom = sum(len(p.overlap_prev_words) for p in pairs)
    else:
        denom = sum(len(p.overlap_cur_words) for p in pairs)
    if denom == 0:
        return None
    return round(sum(p.agreed for p in pairs) / denom, 4)


def main() -> int:
    """Rebuild the summary and report how many rows were written."""
    harness = load_harness()
    labels: list[tuple[str, Path]] = [(p.stem, PASSES) for p in PASSES.glob("*.jsonl")]
    labels += [(p.stem, PASSES_TS) for p in PASSES_TS.glob("*.jsonl")]
    labels.sort()
    rows = [rebuild(label, harness, where) for label, where in labels]
    rows = [r for r in rows if r is not None]
    SUMMARY.write_text(
        "\n".join(json.dumps(r) for r in rows) + ("\n" if rows else ""), encoding="utf-8"
    )
    print(f"wrote {SUMMARY} ({len(rows)} rows from {len(labels)} pass files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
