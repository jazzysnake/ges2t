# 003 — re-review: correction of the 001 findings

- **Artifact:** [`REPORT.md`](REPORT.md) at commit `02e6002` (`docs(whisper-cpp-provider): correct 001-review findings in REPORT.md`)
- **Prior review:** [`001-review.md`](001-review.md) (verdict: changes requested); agent response [`002-comment.md`](002-comment.md)
- **Branch:** `research/whisper-cpp-provider` · **Worktree:** `.worktrees/whisper-cpp-provider`
- **Scope:** the **new paths** — the fourteen corrections in `002-comment.md` plus the report diff `1ed4b92..HEAD`. The data was re-verified as unchanged, and the full WER recomputation and table render were re-run once as a regression check. I did not modify the report, the harness, the data, or `src/`/`tests/`.

## Verdict

**pass.**

Every finding from `001-review.md` is corrected in the report, and each corrected claim matches `data/runs.jsonl`. The data files are byte-identical to the reviewed commit (blob hashes), so no measured WER, transcript, timing, or RSS value changed. `render_tables.py` still reproduces `data/timings.md` byte-for-byte. I found no new contradiction introduced by the sibling sweep. The remaining items are nits, not findings.

---

## 1. Data untouched — **confirmed**

```
$ git diff main...HEAD --stat -- src tests
(empty)
$ git diff 1ed4b92..HEAD --name-only
docs/project/research/whisper-cpp-provider/002-comment.md
docs/project/research/whisper-cpp-provider/REPORT.md
```

Only `REPORT.md` and the new comment changed. Data blobs are identical:

```
SAME  data/runs.jsonl         6d2eec40362eac9618267c0c1ac0f7ed9b9228ae
SAME  data/timings.md         df09b70d87d01908ec3fe86335fc5cef068c029c
SAME  measure_whisper_cpp.py  75ca6c57d341036ae02fc1b3904937986f87100c
SAME  render_tables.py        a3e8bed7bae8c792fa4d72a5b47a473ba3b7d36e
```

## 2. Each 001 finding is fixed — verified against the data, not the summary

Re-computed from `runs.jsonl` (command `uv run python /tmp/rereview_probe.py`, output verbatim):

```
=== (a) §5 quant identity ===
small q5_0==q8_0: False
   small diff: -expects
   small diff: +expect
   small diff: -flood
   small diff: +fad
small q8_0==f16: True
medium q5_0==q8_0: False
   medium diff: -Valley,
   medium diff: +Valley
   medium diff: -tonight,
   medium diff: +tonight
medium q8_0==f16: True
```

| finding | new text | verdict |
|---|---|---|
| **(a) §5/§3 byte-identity** | §5 now "**WER is quantisation-invariant, but the transcripts are not always byte-identical**", with the exact `expects`/`expect`, `flood`/`fad` pair and the punctuation-only `medium` case, and "q8_0 and f16 are byte-identical at both sizes". §1/§3 changed from "byte-for-byte identical" / "identical" to "**WER is equal** … transcripts not always byte-identical". §13.4 "*q8_0 equal WER*". | **fixed**, matches data |
| **(b) §3 turbo-ties-medium** | "ties `medium` only on `quiet-long` (on `quiet-short` it is worse, 0.333 vs 0.296)" | **fixed** — turbo q-s 0.3333, q-l 0.1111; medium q-s 0.2963, q-l 0.1111 |
| **(c) §9 base.en-vs-base** | "`base.en` and `base` are **equal** on `quiet-short` (both 0.407) and `base` is better on `quiet-long` (0.139 vs 0.153)" | **fixed** — both q-s 0.4074; q-l base 0.1389 vs base.en 0.1528 |
| **(d) RTF single convention** | `grep -n "13\.8" REPORT.md` → **no match**; `grep -n "14\.6"` → lines 34, 203, 348, 587, all paired with 1.72 s; §8 table 14.6 | **fixed**, consistent |
| **(e) ratio** | §1/§14 "roughly **4.5×** (batch, 0.281→0.0625) and **6.3×** (streaming, 0.391→0.0625)"; `grep "5–6"` → no match | **fixed** — 0.281/0.0625 = 4.50, 0.391/0.0625 = 6.26 |
| **(f) RSS columns** | CPU car small/medium/turbo now **336/828/733**; VK small/medium/turbo **94/105/118**; §11.6 the same. Header "median over repeats" (unchanged) now matches | **fixed** — medians 336.4/827.9/732.9 and 94.5/105.0/117.5 |
| **(g) speed-up range** | §1/§8/§14 now **4.7–13.4×** | **fixed** — computed per-run ratio range 4.69–13.44 |

For (f), the full re-computation:

```
cpu ggml-tiny-q5_0.bin   median=104.1 -> 104 (report 104) OK
cpu ggml-base-q5_0.bin   median=150.6 -> 151 (report 151) OK
cpu ggml-small-q5_0.bin  median=336.4 -> 336 (report 336) OK
cpu ggml-medium-q5_0.bin median=827.9 -> 828 (report 828) OK
cpu ggml-large-v3-turbo-q5_0.bin median=732.9 -> 733 (report 733) OK
vulkan ggml-small-q5_0.bin       median=94.5 -> 94  (report 94)  OK
vulkan ggml-medium-q5_0.bin      median=105.0 -> 105 (report 105) OK
vulkan ggml-large-v3-turbo-q5_0.bin median=117.5 -> 118 (report 118) OK
```

## 3. Sibling sweep — no new contradictions

Spot-checked against the data and the report's own tables:

- **§3 metric labels** (sibling): "car clip **0.062 std WER**, quiet clips **0.111–0.296**" is now all-std and matches §9's std table (medium 0.2963/0.1111). The old mixed `0.153–0.407` (word_diff) is gone from §3; the surviving `0.407`/`0.153` occurrences are the legitimate base/base.en std cells in §9.
- **§2.2 inventory** (sibling): the four added rows match on-disk sizes exactly — `ggml-small-q5_1.bin` 190 085 487, `ggml-base-q5_1.bin` 59 707 625, `ggml-tiny-q8_0.bin` 43 537 433, `ggml-base-q8_0.bin` 81 768 585. `q51.log` corroborates the §5 q5_1 numbers (small 0.171875, base 0.34375).
- **§8 VK RSS** (sibling): 94/105/118 and the "≈6–8×" claim for medium/turbo check out (828/105 = 7.9; 733/118 = 6.2).
- **§5 f16 ratios** (sibling): "~33 % slower and ~1.9× the RAM at `small`, ~35 % slower and ~2.2× at `medium`" — 8.57/6.45 = 1.33; 638/336 = 1.90; 26.06/19.29 = 1.35; 1780/828 = 2.15. Correct (the old "~40 %" is gone).
- **§11.5 "GPL-free"→"MIT-licensed"** (sibling): whisper.cpp is MIT; consistent with the same section's licence bullet. No stale "GPL" remains.
- **§12 n=1 caveat** (sibling): "the **quiet-clip rows are n=1** per model (variance unmeasured); the car-clip rows have repeats" — matches (quiet clips: 1 row per model; car: 3–9).

## 4. The two resolved notes — **both confirmed**

**f16 outlier cause retracted (agent was right to retract).** The `run()` driver prints `### $(date +%H:%M:%S)` **at run start**, so `medium-f16-commit` started 11:38:00 and (wall 74.744 s) finished ≈11:39:14.7. The Vulkan build's earliest file is `CMakeFiles/4.4.4/CMakeSystem.cmake` at **11:39:23.1**, and its first object file `ggml-threading.cpp.o` at **11:39:32.3** — both *after* the car run ended. The report now says the car-clip outlier cause "was **not** isolated" and uses the median 26.1 s; the concurrency it does assert (the 95.5 s `jfk` f16 run, 11:39:27–11:41:02) genuinely overlapped the build. Correct.

**`ggml-small-q8_0.bin` pin re-verified.** The on-disk file is `264464607` bytes and `49c8fb02b65e6049d5fa6c04f81f53b867b5ec9540406812c643f177317f779f`, matching §11.4 exactly. I cannot re-download the canonical copy (no network, and no second copy remains on disk), so the specific *download-equals-local-quantisation* comparison is taken on the agent's word; the load-bearing part — that the shipped pin identifies the measured file — verifies.

## 5. Regression checks — no measured value changed

```
$ uv run python render_tables.py --runs data/runs.jsonl --out /tmp/rerender.md
wrote /tmp/rerender.md (103 runs)
$ diff /tmp/rerender.md data/timings.md
IDENTICAL

$ uv run python /tmp/probe_wer.py      # ticket 32's word_diff, by path
rows loaded: 103
scored rows: 94, unscored: 9
worddiff mismatches: 0
std mismatches: 0
```

`render_tables.py` reproduces `data/timings.md` byte-for-byte, and all 94 scored WERs still recompute exactly. Since the data blobs are identical to the reviewed commit, no measured value can have changed.

## Residual nits (non-blocking)

- The §5 parenthetical "the build's first object file is timestamped **15 s** after that run finished" is ~**17.6 s** from the recorded timestamps (finish ≈11:39:14.7 → first `.o` 11:39:32.3). The substantive point — the car outlier ended *before* the build began writing outputs — is correct; only the rounded figure is a little tight.
- §3 still calls `medium q5_0` "the *most accurate* model in every condition" while §13.1 was tightened to "best-or-tied". A tie on `quiet-long` is still "most accurate", so this is defensible, not an error.

## Residual risks (unchanged, all disclosed in the report)

- One recording and n=1 on both quiet clips; the quiet-clip ranking is stable but its variance is unmeasured (§12 now says so).
- The cross-harness ratio compares different audio (gate-passed vs raw); §1 states this.
- CPU timing spread up to 2.6×.

## Safe to relay to the user verbatim?

**Yes.** The 001 errors are corrected and re-verified against the data; the data, harness, and rendered tables are unchanged and reproduce; the report's own caveats (n=1 quiet clips, upper-bound WER, cross-path comparison, un-isolated f16 outlier) are present and accurate. The two nits are cosmetic and do not affect any claim.
