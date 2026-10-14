# 003-review — ticket 55 re-review of the review fixes (`2eb512b`)

- **Reviewer:** independent verifier (ges2t reviewer)
- **Branch / worktree:** `feat/nemotron-3-5-asr-streaming-measurement` @ `2eb512b` (parent `5ce75e8`)
- **Reviewed diff:** `git diff 5ce75e8 2eb512b`
- **Verdict:** **pass** — F1–F6 resolved; both central claims remain safe to relay.

I did not write this work and changed nothing in it. My only write is this artifact. This is a focused
re-review of my own `002-review.md` findings; the earlier independent re-derivations stand.

---

## 1. F1 — §1 headline RSS direction (was blocking) — **RESOLVED**

The inverted sentence is **gone**. `REPORT.md` §1 now reads:

> "…strictly append-only incremental text, with no seam duplication, no cut-off hallucination, and no
> silence hallucination. **Vulkan is the cheaper path on *both* counts — host RSS ~0.2× CPU's (the
> q8_0 weights live in VRAM) and roughly half the per-chunk latency — at the price of ~950 MB of
> VRAM; the real cost of the streaming model is accuracy**, and the Hungarian accuracy that motivated
> the epic is not measurable here."

Checks:

```
$ grep -rn "RSS of CPU\|costs ~4×\|4× the RSS\|× the RSS" REPORT.md 001-comment.md
NONE (inverted phrasing gone)
```

- Direction is now true: C-ABI Vulkan 197 MB / CPU 1,064 MB → `~0.2×`; CLI 181 / 1,849 MB → ~0.1×.
  `~0.2×` is fair for the C-ABI headline measurement (the one the sentence summarises).
- "half the per-chunk latency" — Vulkan p90 45 ms vs CPU p90 88 ms (§5.3): ratio 0.51. ✓
- Consistent with the rest: §5.2 line 247 (`~197 MB (Vulkan) vs ~1,064 MB (CPU)`), §11 line 499
  (`Vulkan is 5–10× lighter in host RAM`), and `001-comment.md` lines 19–20 & 72
  (`the GPU win here is host RAM (~197 MB vs ~1 GB), not speed`). No internal contradiction remains.

## 2. F2 — whisper `0.062` needs the `-nt` qualifier — **RESOLVED**

Every occurrence now carries it:

```
REPORT.md:68   ... Whisper `medium q5_0` scored 0.062 on the same car clip under its `-nt` decode (0.156 without it) ...
REPORT.md:405  | ... | whisper `medium q5_0` batch (vk, `-nt`) |
REPORT.md:410  | car (capture) | 64 | 0.312 | 0.312 | 0.234 | 0.062 (0.156 without `-nt`) |
REPORT.md:414  ... (0.312 vs 0.062 with `-nt`, 0.156 with whisper's CLI default) ...
001-comment.md:34  ... scored 0.062 on the same car clip with its `-nt` decode (0.156 without it) — so the fair gap on whisper's CLI default is ~2× ...
```

The `0.156` figure traces to `whisper-cpp-provider/004-addendum-nt-decode.md` (re-verified in the
first review). The direction (whisper more accurate) is unchanged and now honestly sized.

## 3. F3 — Parakeet-v3 accuracy is published, not measured — **RESOLVED**

`REPORT.md` §8 lines 416–418:

> "…the survey's published Parakeet-v3 Hungarian FLEURS is much higher (`15.72` vs this model's
> ≈ 29–33), but ticket 54 was obsolete and never measured v3 here, so that number is **published
> FLEURS only**, and Parakeet v3 is batch-only in every engine."

`grep -rn "much higher accuracy\|runs Parakeet"` → **NONE**. The unqualified "much higher accuracy"
claim is gone.

## 4. F4 — all per-partial traces committed; repo-alone reproduction — **RESOLVED**

```
$ ls data/partials/*.jsonl | wc -l   → 25      # was 6
$ ls data/sweep/*.jsonl   | wc -l   → 10      # was 0
$ head -1 data/partials/stream-jfk-vk.jsonl → keys: [..., 'word_times']   # word_times kept
```

Re-run from the repo alone (no scratch dir), exactly the snippet the report now documents:

```
$ PYTHONPATH=src python3 analyze_streaming.py --partials data/partials --out /tmp/re55_analysis.jsonl
... 25 rows, every stream row c_ok=1.0 rewr=0 mono=True dups=0 ...   # exit 0
$ diff <(sort data/analysis.jsonl) <(sort /tmp/re55_analysis.jsonl)
IDENTICAL (sorted)          # 25 vs 25 lines
```

Independent append-only re-derivation over the **committed** traces (my own checker, no
`analyze_streaming.py`): **25/25 partials and 10/10 sweep traces → `app=1.0`, `rewr=0`, `dups=0`,
final extends last interim** (`TOTAL NOT-OK: 0`). Silence traces remain `transcript: ""`.

Folder stays small and binary-free:

```
$ du -sh docs/project/research/nemotron-3-5-asr-streaming-measurement    → 1.2M   (data 1.1M)
$ largest committed file                                                → 142,654 B (data/sweep/clip30-80.jsonl)
$ total committed bytes in the research folder                          → 1,104.2 KiB across 50 files
```

`render_report_data.py` was updated to glob all `partials/*.jsonl` and `sweep/*.jsonl` and drop the
redundant `words` array while keeping `word_times` — the change is confined to trace curation.

**Residual (cosmetic, non-blocking):** the §"Raw evidence" line says "all **5** sweep traces in
`data/sweep/`", but there are **10** sweep trace files (5 chunk sizes × {jfk, clip30}). The report
consistently calls this "5 sweep runs" (jfk only) elsewhere (§7 line 475), so reword to "the sweep
traces" or "10 sweep traces". Does not affect any number or the verdict.

## 5. F5 — wall vs audio seconds — **RESOLVED**

`REPORT.md` line 55: "…emits its first text after **~0.85–1.16 s of wall clock** (the engine's audio
watermark at that point is 0.96–1.28 s)". Wall and audio are now disambiguated.

## 6. F6 — `numpy` claim — **RESOLVED**

`REPORT.md` lines 616–617: "No `numpy` dependency or import exists anywhere in the project or the
measured path (`grep -r numpy` finds only the project's own "numpy is deliberately absent" prose and
its guard test)." That is the defensible, literally-true form.

## 7. No measured number changed

```
$ git diff 5ce75e8 2eb512b -- data/cli_runs.jsonl data/stream_summary.jsonl data/analysis.jsonl \
      data/paced_summary.jsonl data/chunk_sweep.jsonl data/timings.md
(empty)   # byte-identical
```

The re-run of `analyze_streaming.py` against the **committed** partials reproduces `analysis.jsonl`
byte-identically, so every transcript/watermark in the committed traces is the same measurement as at
`5ce75e8`. The report diff contains only the F1/F2/F3/F5/F6 edits plus the trace-provenance sentence.

## 8. Boundaries, hygiene, DoD

| check | command | result |
|---|---|---|
| no `src/`/`tests/`/`docs/adr/` change | `git diff --name-only main...HEAD -- src/ tests/ docs/adr/` | empty |
| worktree clean | `git status --porcelain` | empty |
| docs link check | `orchestrator-state.md` §10 snippet | **0 broken references** |
| no binary/model/audio committed | `git ls-files` / largest file | none; max 142,654 B |
| every run bounded | `run_matrix.sh` | `timeout 240–300` (unchanged) |
| ticket + README row | `ticket.md`, `README.md` | `in review` (unchanged) |

The prior DoD table (`002-review.md` §7) still holds; the one item that previously **failed** — the
§1 answer sentence — now **passes**. All other DoD items remain pass.

## 9. Safe to relay?

**Yes.** F1–F6 are resolved: the §1 headline states the true direction and agrees with §5.2/§5.3/§11
and `001-comment.md`; every `0.062` carries its `-nt` qualifier; the Parakeet-v3 comparison is
labelled published-FLEURS-only; the full trace set is committed and the append-only result is
reproducible **from the repo alone**; the wall/audio and `numpy` wordings are fixed; and no measured
number moved.

**Both central claims remain safe to relay:**
1. *Strictly append-only streaming text, 0 revisions / 0 seam duplicates / 0 silence hallucination on
   all 20 runs + 5 sweep runs* (re-derived from the committed traces).
2. *Vulkan is really used — `backend=Vulkan0`, VRAM Δ 952.5 MB vs 0, peak RSS ~197 MB vs ~1,064 MB,
   CPU/Vulkan transcripts differ; no silent CPU fallback.*

The remaining caveats stay as stated: Hungarian is not locally measured, Vulkan and CPU transcripts
are not byte-identical, and the streaming model's real cost versus whisper is accuracy. The one
cosmetic residual ("5 sweep traces" should read 10) does not affect any claim.

## 10. Residual / follow-ups

- **R1 (cosmetic):** fix the "all 5 sweep traces" count in §Raw evidence (10 files). No number or
  claim depends on it.
- No other residual. No new risks introduced by the fixes; the trace curation drops only the
  redundant `words` array and keeps everything the analyzer needs.
