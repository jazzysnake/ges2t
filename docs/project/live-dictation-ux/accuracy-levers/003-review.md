# 003 — Re-review of the accuracy-levers corrections (ticket 39)

- **Ticket:** [accuracy-levers](ticket.md) (ticket 39)
- **Reviewer:** independent re-review pass (read-only; neither `001-comment.md` nor the harness
  was modified)
- **Branch under review:** `feat/accuracy-levers`
- **Delta reviewed:** `8d3092c` — `001-comment.md` only (`+107/−39`)
- **Artifacts reviewed:** `001-comment.md` (corrected), `002-review.md`, `measure_accuracy_levers.py`,
  `docs/adr/0011-streaming-sliding-window.md`, `/tmp/ges2t-accuracy-levers-25s-report.txt`,
  `/tmp/ges2t-accuracy-levers-clip5-report.txt`, `../commit-cadence-diagnostic/*`
- **Date:** 2026-10-10
- **Verdict: PASS** — F1, F2 and F3 are corrected as required, the conclusion is unchanged, and
  the sweep found no remaining claim of a result that was never executed. Safe to merge and safe
  to relay to the user.

## 0. Scope and method

This pass reviewed the one-commit delta `8d3092c` and its subject matter; the full measurement
was **not** re-run (002-review already reproduced every headline number byte-identically, and that
stands). Evidence used:

- `git show 8d3092c` / `--name-only` — the delta touches exactly one file, no `src/`, no `tests/`.
- The harness source (`build_variants()`, `HIGHPASS_CORNERS_HZ`, `REDUCED_GAINS`,
  `report_input_analysis()`, the `batch` block) as the ground truth for what was run.
- The two stored reports under `/tmp`, cross-checked against the corrected artifact line by line.
- A read-only re-score of the three reconstructed transcripts using ticket 32's `tokenise` plus an
  independent standard Levenshtein, to test F2's claim (no harness re-run, no microphone, no
  clipboard, no keystroke; probe was inline, no file left behind).
- ADR 0011 and ticket 32's artifacts for the cited figures.

## 1. F1 — the unrun 150 Hz variant (corrected)

The pre-delta §6 said a 150 Hz high-pass "buys **fewer** words (1 or 0)". The harness's transcribed
corners are `HIGHPASS_CORNERS_HZ = (60.0, 85.0, 120.0)`; 150 Hz appears only in the power loop
`for corner in (*HIGHPASS_CORNERS_HZ, 150.0)` in `report_input_analysis()` and never reaches
`build_variants()`.

The corrected artifact is now correctly scoped in every place 150 Hz appears:

- §1: "a 150 Hz corner, **analysed for power only**, removes 92.9 %; it was never transcribed".
- §3: "The 150 Hz row is a **power analysis only** … so no 150 Hz transcript exists and none is
  claimed (see §6)."
- §6: "Removing *more* (85/120 Hz) buys **1 word each** … A 150 Hz corner was analysed for power
  (92.9 % removed) but was never transcribed, so it makes no accuracy claim."
- §11 F1 bullet: records the exact defect and the exact fix.

**Result: PASS.** The claim is removed and explicitly relabelled; the underlying argument (the
effect does not grow with more removal) now rests only on the measured 60/85/120 corners.

## 2. The sweep — every stated variant and number traced

The point of this review. Every variant named in the corrected artifact was matched against
`build_variants()` / the `batch` block, and every number against the stored primary report.

| Claim in `001-comment.md` | Traced to | Result |
|---|---|---|
| `baseline`, `baseline_rerun` | `build_variants()` `RawTransform`; report | 0.391 / 0.391, byte-identical |
| `highpass_60` / `highpass_85` / `highpass_120` | `HIGHPASS_CORNERS_HZ` + `HighPassTransform`; report | 0.359 / 0.375 / 0.375 |
| `gain_0.85` / `gain_0.50` | `REDUCED_GAINS` + `GainTransform`; report | 0.359 / 0.391 |
| `batch` | `run_utterance(..., with_batch_reference=True)`; report | 0.281 (16/0/2) |
| "Eight variant runs produced four distinct transcripts" | report equivalence section | 8 → 4 |
| LF power 86.0 / 89.9 / 92.1 / 92.9 % | report input analysis | exact |
| int16 clip 1262 (0.315 %), 1239 at −32767, 23 at −32768, float 23 | report input analysis | exact |
| 400 698 samples, peak 1.0000, DC −0.010496, AC RMS 0.1084 | report input analysis | exact |
| Error stability: 19 baseline pairs, 60 Hz 14 retained / 3 new, 85+120 13 / 5 | report error-stability section | exact |
| `clip5`: peak 0.6534, 0 clipping, 1.4 % < 60 Hz, 8/8 WER 0.000 | clip5 report | exact |
| Ticket 32 divergence 0.000 → 0.221 → 0.339 with length | 003-comment `clip30` 0.221 (n=68), live 25 s 0.339 (n=62), clip5 0.000 (n=14) | traced |
| standard-WER 0.344 / 0.312 / 0.250, 6 words / −0.094 | independent re-score of the three transcripts (§3 below) | exact |
| "All eight variants … produce the identical transcript at WER 0.000" (clip5) | clip5 report | exact |

**Every stated variant was actually executed, and every number traces to a real run.** No sibling
of F1 — a result asserted for something that was never run — remains. In particular there is no
residual 150 Hz accuracy claim, and no other corner outside `(60, 85, 120)` is described as
transcribed.

**One pre-existing precision note (non-blocking).** §7 says "All **six** streaming variants and the
batch score were computed from the same 400 698 samples." There are **seven** streaming runs
(baseline, baseline_rerun, three corners, two gains); "six" is correct only if the anchor `baseline`
is excluded. This is a count, not a claim of an unexecuted variant, it is traceable either way, and
it predates this delta (it is present verbatim in `8d3092c^`). It does not change any result and is
not a reason to block. Flagged only for completeness because the brief asked for *any* number that
cannot be traced.

## 3. F2 — the scorer's inflation (corrected, and the convention is accurate)

**Both figures now appear wherever the batch comparison appears:**

- §1: "**0.281** … **−0.109, 7 words** by the harness's `word_diff` scorer, or **0.250** and
  **−0.094, 6 words** by a standard word edit distance".
- §4: the ranking table carries the `word_diff` figures, and the "Scorer convention" paragraph
  immediately below gives `batch` 0.250 / `baseline` 0.344 and the 6-word / −0.094 delta, plus
  `highpass_60` 0.312.
- §5 item 1, §7 bullet, §10 DoD row, §11 F2 bullet: all give both conventions.

No place gives the inflated number alone; a reader always sees 0.250/6 alongside 0.281/7.

**The stated convention is accurate against the implementation, not merely present.** Ticket 32's
`word_diff` does:

```python
if tag == "replace":
    substitutions += max(i2 - i1, j2 - j1)
    insertions    += max(0, (j2 - j1) - (i2 - i1))
    deletions     += max(0, (i2 - i1) - (j2 - j1))
```

so an unequal-length replace block (m reference vs n hypothesis) costs
`max(m, n) + |m − n|`, whereas a standard word edit distance costs it `max(m, n)`. The artifact's §7
sentence says exactly this ("`max(m, n)` substitutions **plus** `|m − n|` insertions/deletions for
the same block … a standard word edit distance costs that block `max(m, n)` once") and calls it a
double-count of the length differential. Correct.

**Independent reproduction.** Re-scoring the three reconstructed transcripts with ticket 32's
`tokenise` and a plain Levenshtein:

| variant | `word_diff` | standard |
|---|---|---|
| `baseline` | 25 → 0.391 | 22 → 0.344 |
| `highpass_60` | 23 → 0.359 | 20 → 0.312 |
| `batch` | 18 → 0.281 | 16 → 0.250 |

so streaming-vs-batch is **6 words / −0.094** standard, matching the correction. The artifact is
also honest that only these three transcripts were re-scored under both conventions.

**Result: PASS.**

## 4. F3 — the contrary evidence and the ADR numbers

ADR 0011's own quality row reads:

> WER **0.176** for the 5 s sliding window vs **0.191** for a single 30 s one-shot call — the
> windowed path is *at least as good*

The corrected artifact cites exactly these numbers, in the same direction, in §1, §5, §6, §10 and
§11 ("windowed 0.176 vs one-shot 0.191", "ADR 0011's own clean clip measured the windowed path *at
least as good* as one-shot"). The numbers **are quoted correctly**; the contrary direction is no
longer omitted.

The conclusion is reframed to what the evidence supports — "**the largest lever measured on this
one recording**, pending ticket 35's multi-length sweep — not a settled case for switching to
batch" (§1), "the direction is also not settled" (§5), "ticket 35's multi-length sweep — not this
single 25 s sample — decides whether a batch default is actually better" (§6). That is the review's
prescribed phrasing.

*Minor observation, not a defect:* the descriptor "clean clip" is inherited from 002-review / this
brief; ADR 0011 itself describes a 30 s clip that also carries a language switch and never uses the
word "clean". The descriptor does not affect the evidentiary point (the direction and the two WERs
are exact), so it is not a finding.

**Result: PASS.**

## 5. The conclusion was not softened; the causal claim was weakened

- **No-filter conclusion intact.** §1 opens "**Do not build a noise filter.**"; §6 is headed
  "Should we build a filter? **No.**" with the structural reason first; §10 marks the explicit
  recommendation "done". A grep for hedging language over the artifact finds no "might help", no
  "a filter could still", no walk-back of the recommendation. The only scope-limiting sentence
  ("The filter's value, if any, is in noise this single recording does not contain") is the ticket's
  requested "bar that would change the answer", not a hedge of the current conclusion.
- **Causal claim weakened.** The pre-delta text asserted "It **is** a decoding-basin flip". The
  corrected text says "That is consistent with a discrete basin flip, but the data establish the
  instability, not the mechanism" (§1, §4) and makes "**not a stable, reproducible improvement**"
  the decision-relevant claim (§6, §8). This is exactly the weaker claim F3's reasoning called for.

## 6. Documentation only

- `git show 8d3092c --name-only` → `docs/project/live-dictation-ux/accuracy-levers/001-comment.md`
  alone. No file under `src/` or `tests/`.
- The harness `measure_accuracy_levers.py` is unchanged by the delta, so its behaviour is unchanged.
- `git status` clean at the end of this review.

## 7. Mechanicals

```
$ timeout 300 uv run pytest -q
904 passed in 5.70s                                  # exit 0, process exited

$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 53 source files          # exit 0

$ uv run mypy docs/.../measure_accuracy_levers.py
Success: no issues found in 1 source file            # exit 0

$ python3 <the orchestrator-state.md link check>
0 broken references                                  # exit 0
```

## 8. Findings summary

| # | Finding | Severity | Status |
|---|---|---|---|
| — | F1 (150 Hz unrun variant) | — | corrected, verified |
| — | F2 (scorer-inflated batch delta) | — | corrected, both figures everywhere, convention verified |
| — | F3 (ADR 0011 contrary evidence + causal overreach) | — | corrected, ADR numbers exact |
| N1 | §7 says "six streaming variants" where there are seven streaming runs (six excluding the baseline anchor) | cosmetic, pre-existing, traceable | non-blocking; no result changes |

No finding asserts a result for something that was never executed.

## 9. Bottom line

- **Verdict: PASS.** F1–F3 landed; the corrections did not introduce a sibling error; the sweep
  found every stated variant and number traceable to a real harness run or to the cited ADR/ticket.
- **Safe to merge** and **safe to put in front of the user.**
- **The conclusion can be relayed as written.** "Do not build a noise filter" stands unhedged, and
  the positive finding is correctly bounded: *streaming vs batch was the largest accuracy lever
  measured on this one recording — 6–7 words by either scorer — pending ticket 35's multi-length
  sweep, with ADR 0011 showing the opposite direction on its own clip.*
