# 002 — corrections in response to `001-review.md`

- **Ticket / branch:** `research/whisper-cpp-streaming`
- **Reviewed commit:** `a4c9c7d`; review commit `1210637` (`001-review.md`).
- **This commit:** the docs-only correction below. Only `REPORT.md` and this file changed.
- **Constraints held:** no `src/ges2t/` or `tests/` change; no `data/`, harness, or measured-number
  change; no microphone (the silence control was regenerated with stdlib `wave`); no `sudo`; no
  installs; no network; every command bounded by `timeout`; `GIT_EDITOR=true`; worktree left clean.

The review confirmed the central claim and the `cap`/N=2 mirror, so the conclusion and every
verified number are unchanged. The review's four requested fixes and the sibling sweep are applied
below. The verdict is unchanged: **do not ship a whisper.cpp streaming path.**

## Correction 1 — §3.1 latency column was sourced from the wrong scheme (6 of 12 cells)

The caption said the `latency` column was the "timestamped twin's real median audio lag", but the
`jfk` and `clip30` rows printed the **`-nt` synthetic** `commit_latency_median_s` of the `ntcap-*`
rows instead. Fixed the six cells to the timestamped (`-ojf`) twin values, and clarified the
caption to name the `-ojf` twin explicitly.

| row | was (`-nt` synthetic) | now (`-ojf` twin) |
|---|---|---|
| jfk 5/1 | 0.357 | **0.995** |
| jfk 5/2 | 0.65 | **1.67** |
| jfk 10/2 | 1.0 | **2.03** |
| clip30 5/1 | 0.833 | **1.315** |
| clip30 5/2 | 0.625 | **2.23** |
| clip30 10/2 | 1.721 | **2.23** |

Evidence (`data/summary.jsonl`, the `cap-*` `-ojf` twins):

```
TSLAT ('medium-q5_0', 5.0, 1.0, 'jfk')    0.995
TSLAT ('medium-q5_0', 5.0, 2.0, 'jfk')    1.67
TSLAT ('medium-q5_0', 10.0, 2.0, 'jfk')   2.03
TSLAT ('medium-q5_0', 5.0, 1.0, 'clip30') 1.315
TSLAT ('medium-q5_0', 5.0, 2.0, 'clip30') 2.23
TSLAT ('medium-q5_0', 10.0, 2.0, 'clip30') 2.23
```

The quiet-long and car rows were already the timestamped values, so only the 6 cells changed. §3.2's
latency **ranges** were already built from the timestamped twins and stay correct.

## Correction 2 — §3.3(d) silence claim was `-ojf`-only, unlabelled, and understated

The review showed that on the report's **primary `-nt` decode**, `medium` and `small` also emit
`you` and the rule commits `you you` — so the old "(medium and small … commit nothing)" contrast was
decode-specific and unlabelled. Rewrote §3.3(d) (and §1 point 4) to state the behaviour **under the
primary `-nt` decode**: all three models hallucinate, turbo as `Thank you.` → `thank you thank you`,
medium/small as `you` → `you you`; the `-ojf` behaviour is noted separately. Also fixed the stale
`(8 commits)`: the count-based rule yields **two** commits (four words for turbo), and the raw
`commits` field is a legacy per-pass counter (see provenance note).

I re-ran this myself rather than taking it on faith (`/tmp` only; 10 s of `wave`-generated silence,
`--scheme cap --no-timestamps`, W5/H1):

```
rev2-nt-ggml-medium-q5_0            committed='you you' commits=2 words=2
rev2-nt-ggml-small-q5_0             committed='you you' commits=2 words=2
rev2-nt-ggml-large-v3-turbo-q5_0    committed='you you' commits=2 words=2
```

Committed `-ojf` silence data (`data/` rows, unchanged): medium `''`, small `''`, turbo
`'thank you thank you'`. This **strengthens** the conclusion and is stated as such in §1.

## Correction 3 — §1 collapsed duty ranges off by a rounding step

From `data/summary.jsonl` (`-nt` Vulkan duty min–max per model/config):

```
('small', 5.0, 2.0) 0.2396 - 0.2533
('medium',5.0, 2.0) 0.6056 - 0.6956
('turbo', 10.0,2.0) 0.7557 - 0.8409
```

Fixed §1: `small 5/2` 0.24–0.26 → **0.24–0.25**; `medium 5/2` 0.62–0.70 → **0.61–0.70**;
`turbo 10/2` 0.76–0.81 → **0.76–0.84**. (Sibling: `small 10/2` "0.26" → **0.26–0.27**.) All six
extremes remain below 1, so the direction is unchanged. §3.2's per-clip values were already correct.

## Correction 4 — load-sensitivity caveat added

The review reproduced the harness on this worktree under load average ~2.9 and measured `medium`
car W5/H2 duty **0.918** (vs 0.646 here) and `jfk` **0.933** (vs 0.696). Added a caveat to §1's duty
paragraph and expanded the §5 load bullet: the `< 1` margin at H=2 is small and machine-load
dependent and must not be read as a hard guarantee; only `small` has robust headroom.

## Sibling sweep (same error classes)

- **Wrong-source/scheme:** checked every remaining column of §3.1 (passes/windows/duty/first
  commit/prefix agree/regressed/rewritten/dup/WER) against `summary.jsonl` — all match; only the
  latency column had been mixed. §3.2 and the `data/timings.md` tables were already on one source.
- **Stated number disagreeing with the data:** fixed three more —
  - §1 point 5, `quiet-long` streaming WER range `0.15–0.21` → **`0.15–0.26`** (all-model
    `-nt` range is 0.1528–0.2639);
  - §4 comparison, latency `0.2–3.5 s median` → **`0.27–3.0 s median`** (all timestamped-twin
    medians run 0.27–3.0);
  - §7, `medium` W10/2 `latency ≈ 2–3.5 s` → **`≈ 2–3 s`** (medians 2.03–2.92).
- **§4 hallucination row** updated to match the corrected §3.3(d) ("all three commit `you you` on
  the primary `-nt` decode; turbo `thank you thank you` on `-ojf`").
- **Provenance note** added to §5: the `data/passes*/` per-pass `committed_new`/`committed_total_words`
  are legacy time-watermark counters; every report number is the count-based rebuild from
  `transcript`/`overlap_*`/`agreed`/`flushed` that `recompute_summary.py` reproduces byte-identically.
- **H naming** (review's minor point): added a parenthetical in §2.2 that in the `cap` scheme `H` is
  the shipped `chunk_seconds` and `W` the `window_seconds`, not only a hop.

## Not changed

- The conclusion, the `cap`-mirrors-`drive()`/N=2 finding, and every number the review reproduced
  (duty/WER/prefix-agreement/regressed/rewritten/dup, the flush-dominance split, the `-nt` decode
  effect, the boundary-duplication trace, the falsification list).
- `data/`, the harnesses, and all measured numbers.
