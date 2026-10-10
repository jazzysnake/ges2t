# 002 — response to review 001: what was corrected, and why

- **Artifact:** [`REPORT.md`](REPORT.md)
- **Review:** [`001-review.md`](001-review.md) at commit `1ed4b92` (verdict: changes requested)
- **Branch:** `research/whisper-cpp-provider` · **Worktree:** `.worktrees/whisper-cpp-provider`
- **Data:** unchanged. `data/runs.jsonl`, `data/timings.md`, `measure_whisper_cpp.py`,
  `render_tables.py` are byte-identical to the previous commit; only `REPORT.md` changed.
  `render_tables.py` still reproduces `data/timings.md` byte-for-byte after the edit.

Every finding in review 001 was reproduced against `data/runs.jsonl` before editing. **No
review finding was wrong** — including the two the reviewer listed as "could-not-verify", one of
which I resolved live and one of which the reviewer was right about and I had to retract. No
measured number, transcript, or WER was changed; corrections are prose only.

## Corrections

| # | Review finding | What changed in `REPORT.md` | Evidence |
|---|---|---|---|
| 1 | **BLOCKING §5** — "byte-identical transcript" across quants is false | §5 bullet rewritten: **WER is quantisation-invariant, the transcripts are not always byte-identical.** `small` q5_0 vs q8_0 differ in two words (`expects`/`expect`, `flood`/`fad`) whose substitutions cancel, so WER coincides (0.141) *by luck*; `medium` q5_0 vs q8_0 differ only in punctuation (`Valley,`/`Valley`, `tonight,`/`tonight`); q8_0 == f16 byte-identical. §1 and §3 bullets made consistent, §13.4 changed from "*q8_0 identical*" to "*q8_0 equal WER*" | recomputed from `runs.jsonl`: `small q5_0==q8_0 False`, `q8_0==f16 True`; `medium q5_0==q8_0 False`, `q8_0==f16 True` |
| 2 | §3 "turbo … ties medium on the quiet clips" false | §3 now says turbo ties `medium` **only on `quiet-long`**; on `quiet-short` it is worse (0.333 vs 0.296). §9 prose made to match | turbo q-s 0.333 / q-l 0.111; medium q-s 0.296 / q-l 0.111 |
| 3 | §9 "`base.en` is worse than `base` on **both** quiet clips" contradicts §9's table | §9 now says they are **equal** on `quiet-short` (both 0.407) and `base` is better only on `quiet-long` (0.139 vs 0.153) | base q-s 0.407 = base.en 0.407; q-l 0.139 vs 0.153 |
| 4 | RTF **13.8** (median wall 1.72 s) inconsistent with §8/timings (**14.6**) | Standardised on the **median** convention: `RTF 14.6` with `1.72 s` in §1, §3, §14. (13.8 was the slowest VK run, 25.044/1.821.) | VK medium car RTF: 15.00 / 14.57 / 13.75 → median **14.57** |
| 5 | "5–6× reduction" overstates | §1 and §14 now say **~4.5×** fewer errors than Whistle's batch path (0.281→0.0625) and **~6.3×** fewer than its streaming default (0.391→0.0625) | 0.281/0.0625 = 4.50; 0.391/0.0625 = 6.26 |
| 6 | §4 RSS column header says "median" but values are minima | All RSS cells switched to **medians** to match `render_tables.py`/`timings.md`: tiny 104, base 151, small **336**, medium **828**, turbo **733** (were 333/825/726 minima). Applied consistently in §3, §4, §5, §8, §11.6 | `statistics.median(peak_rss_mb)` per config: 104.1 / 150.6 / 336.4 / 827.9 / 732.9 |
| 7 | Speed-up range is 4.69–13.44×, not 4.7–11.8× | §1, §8, §14 now say **4.7–13.4×** | matched-config CPU/VK ratios over the scored clips: min 4.69 (medium car), max 13.44 (medium car) |
| sibling | §3 metric mismatch not in the review's list | §3 point 1 quoted `0.062 WER` (std) alongside `quiet clips 0.153–0.407` (word_diff) — mixed metrics. Now `0.062 **std** WER, quiet clips **0.111–0.296**` (std) | medium std: 0.062 / 0.296 / 0.111; word_diff: 0.062 / 0.407 / 0.153 |
| sibling | §3 `.en` bullet over-claimed | `base.en` "beats base … but not on the quiet clips" → "the advantage does **not** replicate (equal on `quiet-short`, `base` better on `quiet-long`)" | as #3 |
| sibling | §2.2 inventory incomplete | Added `ggml-small-q5_1.bin`, `ggml-base-q5_1.bin`, `ggml-tiny-q8_0.bin`, `ggml-base-q8_0.bin` (all on disk) | `ls models/` |
| sibling | §8 VK RSS used mixed min/median | VK RSS now medians: small **94**, medium **105** (was 115 — a single-run value), turbo 118 | VK medians 94.5 / 105.0 / 117.5 |
| sibling | §5 f16 outlier cause was an unverified assertion | Retracted the "concurrent build" *cause* for the 74.7 s car run; §5 now says the cause was **not isolated** and that the build's first object file is timestamped 15 s *after* that run finished. The clean median **26.1 s** is used | `medium-f16-commit` ran 11:38:00–11:39:14; first `build-vulkan` `.o` mtime **11:39:32**; only the `jfk` f16 run (11:39:27–11:41:02, 95.5 s) overlapped the build |
| sibling | §12 n=1 quiet clips not stated | §12 now states the **quiet-clip rows are n=1** per model (variance unmeasured) | 1 row per model per quiet clip in `runs.jsonl` |
| sibling | §11.5 "GPL-free" phrasing | → "MIT-licensed C++" | whisper.cpp is MIT |

## Review "could-not-verify" items I resolved

- **`ggml-small-q8_0.bin` equals the canonical published file — now verified live.** The reviewer
  could not check it because only one copy was on disk. I downloaded the canonical
  `ggerganov/whisper.cpp` `ggml-small-q8_0.bin` (`curl`, bounded) and compared: **same size
  (264 464 607 B) and same sha256 (`49c8fb02b65e6049…`)**. So §2.2's claim and the §11.4 pin are
  correct. (This is the only new download; no data file changed.)
- **The f16-outlier cause — the reviewer was right to doubt it.** I checked the timestamps: the
  74.7 s car run **finished before** the Vulkan build's first object file was written, so the
  "concurrent build" attribution was wrong. Only the 95.5 s `jfk` f16 run actually overlapped the
  build. §5 now says so and no longer asserts a cause for the car outlier.

## Not changed (deliberately)

- **All WER values, transcripts, timings, RSS numbers and sha256 pins** — the review verified them
  (94/94 WER rows recompute; 6/6 §11.4 pins verify) and they are the data.
- **The conclusions** — whisper.cpp is viable; `medium q5_0` is the accuracy leader; q5_0 ≈ q8_0;
  never f16; greedy; `supports_streaming = False`; Vulkan ~4.7–13.4× faster at byte-identical
  output. The review upheld all of these.
- **`data/runs.jsonl`, `data/timings.md`, `measure_whisper_cpp.py`, `render_tables.py`** — untouched.
  `git status` after this commit shows only `REPORT.md` and this comment.

## Constraints

No `src/ges2t/` or `tests/` change; no microphone, clipboard, window, notification, `sudo`, or
package install. The one network use was the bounded `curl` of the canonical `ggml-small-q8_0.bin`
to verify the hash; no binary, model, or audio is committed.
