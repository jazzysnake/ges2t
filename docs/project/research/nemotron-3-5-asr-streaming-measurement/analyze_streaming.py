#!/usr/bin/env python3
"""Committed-delta stability analysis for the NeMo-Speech.cpp streaming runs.

Reads the per-partial records produced by ``measure_nemo_streaming.py`` and
measures, for every clip/device, the properties ges2t's committed-delta
contract needs, using the metric shapes of
``docs/project/research/whisper-cpp-streaming/REPORT.md`` so the two are
directly comparable:

- **append-only** -- is the interim transcript stream a monotonically extending
  sequence? Both at the **word** level (previous word list is a prefix of the
  current) and at the **character** level (previous string is a prefix of the
  current).
- **regressed / rewritten** pairs -- interims where the previous text was *not*
  reproduced (the failure the whisper.cpp windows showed).
- **seam duplication** -- adjacent repeated words in the committed text.
- **WER (stream)** vs the project's reference truth, scored with ticket 32's
  ``word_diff`` (same normalisation as the whisper reports).
- **algorithmic lag** -- for each interim, ``audio_processed_s`` minus the last
  committed word's end time, i.e. how much audio the encoder is ahead of the
  text it has emitted.

Run with ``PYTHONPATH=src`` so ticket 32's harness imports ``ges2t``.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Sequence

TICKET32_HARNESS: Path = (
    Path(__file__).resolve().parents[2]
    / "live-dictation-ux"
    / "commit-cadence-diagnostic"
    / "measure_commit_cadence.py"
)
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


def lcp_words(a: Sequence[str], b: Sequence[str]) -> int:
    limit = min(len(a), len(b))
    for i in range(limit):
        if a[i] != b[i]:
            return i
    return limit


def adjacent_dups(words: Sequence[str]) -> list[tuple[int, str]]:
    return [(i, words[i]) for i in range(1, len(words)) if words[i] == words[i - 1]]


def median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def analyse(partials_path: Path, reference: str | None, t32: Any) -> dict[str, Any]:
    records = [json.loads(line) for line in partials_path.read_text().splitlines() if line]
    if not records:
        return {"error": "no records"}
    interims = [r for r in records if r["kind"] == "interim"]
    finals = [r for r in records if r["kind"] == "final"]
    final = finals[-1] if finals else records[-1]
    final_text = final["transcript"]
    final_words, _ = t32.tokenise(final_text)
    final_times = [w[2] / 1000.0 for w in final["word_times"]]

    # Append-only = the interim transcript is a monotonically extending string.
    char_pairs = 0
    char_ok = 0
    rewritten = 0
    worst_lcp_ratio = 1.0
    revisions: list[dict[str, Any]] = []
    committed_counts: list[int] = []
    lags: list[float] = []
    first_text_audio: float | None = None
    prev_text = ""
    prev_c = 0
    for record in interims:
        text = record["transcript"]
        if prev_text:
            char_pairs += 1
            if text.startswith(prev_text):
                char_ok += 1
            else:
                rewritten += 1
                common = len(prev_text)
                while common and not text.startswith(prev_text[:common]):
                    common -= 1
                worst_lcp_ratio = min(worst_lcp_ratio, common / max(1, len(prev_text)))
                revisions.append({"prev": prev_text, "cur": text, "common_chars": common})
        if first_text_audio is None and text.strip():
            first_text_audio = record["audio_processed_s"]
        # How many of the final words are already fully present, in order?
        tokens, _ = t32.tokenise(text)
        c = 0
        for i in range(min(len(tokens), len(final_words))):
            if tokens[i] == final_words[i]:
                c += 1
            else:
                break
        committed_counts.append(c)
        for j in range(prev_c, c):
            if j < len(final_times):
                lags.append(record["audio_processed_s"] - final_times[j])
        prev_c = max(prev_c, c)
        prev_text = text

    monotone_commit = all(
        committed_counts[i] >= committed_counts[i - 1] for i in range(1, len(committed_counts))
    )
    dup = adjacent_dups(final_words)
    out: dict[str, Any] = {
        "label": records[0]["label"],
        "n_interims": len(interims),
        "n_finals": len(finals),
        "final_words": len(final_words),
        "char_pairs": char_pairs,
        "append_only_char_pairs": char_ok,
        "append_only_char_frac": round(char_ok / char_pairs, 4) if char_pairs else None,
        "rewritten_char_pairs": rewritten,
        "worst_lcp_ratio": round(worst_lcp_ratio, 4),
        "n_revisions": len(revisions),
        "revisions_sample": revisions[:5],
        "monotone_committed_words": monotone_commit,
        "committed_words_final": committed_counts[-1] if committed_counts else 0,
        "adjacent_duplicate_words": len(dup),
        "adjacent_duplicates_sample": dup[:8],
        "algorithmic_lag_median_s": round(median(lags), 3) if lags else None,
        "algorithmic_lag_max_s": round(max(lags), 3) if lags else None,
        "first_text_audio_s": first_text_audio,
        "last_interim_equals_final": interims[-1]["transcript"] == final_text if interims else None,
        "final_extends_last_interim": final_text.startswith(interims[-1]["transcript"])
        if interims
        else None,
        "final_text": final_text,
    }
    if reference is not None:
        diff = t32.word_diff(reference, final_text)
        out["wer_stream_worddiff"] = round(diff.wer, 4)
        out["worddiff_sub"] = diff.substitutions
        out["worddiff_ins"] = diff.insertions
        out["worddiff_del"] = diff.deletions
        out["wer_stream_std"] = round(
            _std_wer(t32.tokenise(reference)[0], t32.tokenise(final_text)[0]), 4
        )
    return out


def final_text_safe(record: dict[str, Any]) -> str:
    return record["transcript"]


def _std_wer(reference_words: Sequence[str], hyp_words: Sequence[str]) -> float:
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--partials", type=Path, required=True)
    parser.add_argument("--glob", default="*.jsonl")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    t32 = _load(TICKET32_HARNESS, "ges2t_ticket32_measure")
    provider = _load(PROVIDER_HARNESS, "ges2t_whisper_provider")
    references: dict[str, str] = provider.REFERENCES
    # Map clip stem -> reference key.
    key_of = {
        "jfk": "jfk",
        "quiet-short": "quiet-short",
        "quiet-long": "quiet-long",
        "ges2t-commit-cadence-capture-20261009-195914": "commit",
    }
    rows = []
    for path in sorted(args.partials.glob(args.glob)):
        # label: stream-<stem>-<device>
        label = path.stem
        body = label[len("stream-") :] if label.startswith("stream-") else label
        stem, _, device = body.rpartition("-")
        reference = references.get(key_of.get(stem, ""))
        row = analyse(path, reference, t32)
        row["clip"] = stem
        row["device"] = device
        row["reference_key"] = key_of.get(stem)
        rows.append(row)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")

    print(
        f"{'label':44s} {'int':>4s} {'c_ok':>5s} {'rewr':>4s} "
        f"{'mono':>4s} {'dups':>4s} {'lag_med':>7s} {'wer':>6s}"
    )
    for row in rows:
        print(
            f"{row['label']:44s} {row['n_interims']:4d} "
            f"{str(row.get('append_only_char_frac')):>5s} "
            f"{row['rewritten_char_pairs']:4d} "
            f"{str(row['monotone_committed_words']):>4s} "
            f"{row['adjacent_duplicate_words']:4d} "
            f"{str(row.get('algorithmic_lag_median_s')):>7s} "
            f"{str(row.get('wer_stream_worddiff')):>6s}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
