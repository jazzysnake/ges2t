# 002-review — ticket 55 (Nemotron 3.5 ASR streaming measurement)

- **Reviewer:** independent verifier (ges2t reviewer)
- **Branch / worktree:** `feat/nemotron-3-5-asr-streaming-measurement` @ `4e8fb90`
- **Reviewed diff:** `git diff main...HEAD` (three-dot) — docs-only, 25 files
- **Verdict:** **changes requested** (one inverted headline sentence, `REPORT.md` §1 line 37; the two
  central claims hold and are safe to relay once that line is fixed)

I did not write this work and changed nothing in it. My only write is this artifact.

---

## 0. Headline result

Both **central claims hold under independent re-derivation**:

1. **Append-only** — `append_only_char_frac = 1.0`, **0** rewritten pairs, **0** adjacent duplicate
   words, final always extends the last interim, on **all** traces (committed + scratch + sweep).
2. **Vulkan is really used** — `backend=Vulkan0` stderr line, VRAM Δ **952.5 MB** vs **0.0**, peak RSS
   **197 MB** vs **1,063 MB**, and CPU/Vulkan transcripts differ. No silent CPU fallback.

The report is honest about the two "do-not-relay-as-a-win" caveats (Hungarian unmeasured;
CPU/Vulkan not parity). **One headline sentence is wrong:** §1 inverts the RSS direction.

---

## 1. Central claim 1 — committed text is strictly append-only

### 1.1 Independent re-derivation (my own checker, does not import `analyze_streaming.py`)

I wrote `/tmp/review55_check.py` — a plain string-prefix test over the `interim` list, plus a
whitespace/lowercase adjacent-duplicate scan of the final. It reads the **committed** traces and the
full scratch set:

```
$ python3 /tmp/review55_check.py docs/project/research/nemotron-3-5-asr-streaming-measurement/data/partials
OK  paced-jfk-vulkan.jsonl            int= 62 app= 1.0 rewr=0 dups=0 eq_final=True  ext=True
OK  silence-10s-cpu.jsonl             int=  0 app= None rewr=0 dups=0 eq_final=None  ext=None
OK  silence-10s-vulkan.jsonl          int=  0 app= None rewr=0 dups=0 eq_final=None  ext=None
OK  stream-ges2t-...-vk.jsonl         int=146 app= 1.0 rewr=0 dups=0 eq_final=True  ext=True
OK  stream-jfk-cpu.jsonl              int= 62 app= 1.0 rewr=0 dups=0 eq_final=True  ext=True
OK  stream-jfk-vk.jsonl               int= 62 app= 1.0 rewr=0 dups=0 eq_final=True  ext=True
# OK=6 NOT-OK=0

$ python3 /tmp/review55_check.py ~/.cache/ges2t-discovery/nemotron-3-5-asr-streaming-measurement/partials
... 25 files: every one OK (app=1.0, rewr=0, dups=0); eq_final=False only for stream-clip5-{cpu,vk}, ext=True
# OK=25 NOT-OK=0

$ python3 /tmp/review55_check.py ~/.cache/ges2t-discovery/nemotron-3-5-asr-streaming-measurement/sweep
... 10 traces (jfk 80/160/320/560/1120, clip30 80/160/320/560/1120): all app=1.0, rewr=0
# OK=11 NOT-OK=0   (chunk_sweep.jsonl itself has 0 interims)
```

The two `eq_final=False` runs are the report's documented "trailing partial word" case:

```
stream-clip5-cpu last interim : 'He hoped there would be stew for dinner, turnips and carrots and bruised po'
stream-clip5-cpu final        : 'He hoped there would be stew for dinner, turnips and carrots and bruised potatoes'
```

— i.e. `18` equal + `2` extend, exactly as §1/§7 state. **The report's `1.0` / `0/0` numbers are
confirmed.**

### 1.2 The repo analyzer reproduces the committed `analysis.jsonl` byte-for-byte

```
$ PYTHONPATH=src timeout 300 python3 analyze_streaming.py \
    --partials "$SC/partials" --out /tmp/review55_analysis.jsonl
... 25 rows, every stream row c_ok=1.0 rewr=0 mono=True dups=0 ...
$ diff <(sort data/analysis.jsonl) <(sort /tmp/review55_analysis.jsonl)
IDENTICAL (sorted)          # 25 vs 25 lines
```

### 1.3 Silence control

```
silence-10s-cpu.jsonl   : {"index": 0, "kind": "final", ..., "transcript": ""}
silence-10s-vulkan.jsonl: {"index": 0, "kind": "final", ..., "transcript": ""}
```

0 interims, empty final, both devices. Confirmed.

### 1.4 The metric is falsifiable — I broke it and watched it fail

A check that cannot fail reads as verification. I mutated copies **in `/tmp` only**:

```
# Mutation A — replace one interim with a non-prefix
mutation A: interim30 'And so my fellow Americans ask not' -> 'And so my MANGLED fellow Americans ask not'

$ python3 /tmp/review55_check.py /tmp/falsify55
FAIL-REWRITE  mutA-nonprefix.jsonl  int=62 app=0.9672 rewr=2 ...
# OK=0 NOT-OK=1

$ PYTHONPATH=src python3 analyze_streaming.py --partials /tmp/falsify55b --out /tmp/falsify55b/out.jsonl
stream-jfk-vk  62  0.9672  2  False  0  0.24  0.0        # c_ok<1, rewr=2, mono=False

# Mutation B — duplicate an adjacent word in the final
$ PYTHONPATH=src python3 analyze_streaming.py --partials /tmp/falsify55c/parts --out /tmp/falsify55c/out/a.jsonl
stream-jfk-vk  62  1.0  0  True  1  0.24  0.0455        # dups=1
```

Both the repo analyzer and my checker **fail on the mutation and only pass on the real data** — the
`1.0 / 0 / 0` is not vacuous. (Probe files removed; see §8. Note the committed `data/partials/*.jsonl`
are the **compact** traces without `word_times`, so `analyze_streaming.py` cannot run against them
directly — it needs the scratch traces. That is a traceability point, F4 below.)

---

## 2. Central claim 2 — Vulkan is actually used, no silent CPU fallback

### 2.1 `doctor` reproduces

```
$ $BIN doctor
NeMo-Speech.cpp 0.2.0
Features: asr backend_vulkan diarization http integrated_vad model_pull ...
Devices:
  [0] integrated-gpu AMD Radeon 780M Graphics (RADV PHOENIX) (17.5 GiB)
  [1] cpu            AMD Ryzen 7 7840HS with Radeon 780M Graphics (27.1 GiB)
Model downloads: /usr/bin/curl          # exit 0
```

Byte-identical to `data/engine_doctor.txt`.

### 2.2 Fresh CLI run, both devices — backend line + RSS direction

```
$ measure_nemo_cli.py --audio jfk.wav --device vulkan:0
{'peak_rss_mb': 180.7, 'backend_line': '[asr] model=....gguf head=rnnt backend=Vulkan0',
 'transcript': '... your country'}          # no trailing period
$ measure_nemo_cli.py --audio jfk.wav --device cpu
{'peak_rss_mb': 1848.6, 'backend_line': '[asr] model=....gguf head=rnnt backend=CPU',
 'transcript': '... your country.'}         # trailing period
```

Confirms: (a) the `backend=Vulkan0` / `backend=CPU` stderr lines; (b) **Vulkan is the low-RSS path**
(181 vs 1849 MB); (c) the CPU/Vulkan transcripts are **not** byte-identical (trailing `.`), exactly as
§6 says.

### 2.3 Fresh C-ABI run — the exact ~197 vs ~1064 numbers

```
$ measure_nemo_streaming.py --device vulkan ... --label review-stream-jfk-vulkan
{'peak_rss_mb': 197.0, 'vram_mb': 952.5, 'n_interims': 62, 'errors': []}
$ measure_nemo_streaming.py --device cpu ... --label review-stream-jfk-cpu
{'peak_rss_mb': 1063.3, 'vram_mb': 0.0,  'n_interims': 62, 'errors': []}
```

The fresh partials are themselves append-only (`pairs 61 ok 61 frac 1.0 rewr 0 dups 0 final_ext True`).
The committed `data/stream_summary.jsonl` (20 rows) shows the same shape: Vulkan peak RSS
`196.4–201.1`, CPU `1063.4–1064.4`; Vulkan VRAM `952.5–953.0`, CPU `0.0`; `errors == []` everywhere.

### 2.4 Pins verified locally

```
$ sha256sum nemo-speech-0.2.0-linux-x86_64-vulkan.tar.gz
c8d0ed5b9ac5fb6d5c460015ba38ea217415c44edc1e6d49120646781009aee6   16618142 B   ✓ (report)
$ sha256sum nemotron-3.5-asr-streaming-0.6b.q8_0.gguf
a5c435f294eea8f88ce68dd27b8c3bfea7f777cb2fbba04fcd30eaa555f429ae   741548352 B  ✓ (report)
```

The engine's own extracted `share/nemo-speech/model-index.json` pins the same triple
(`revision 1c8deaecc64b91f034d73e08dd8b64625eb3395d`, size `741548352`, sha256 `a5c435f2…`) — so the
revision→artifact attribution is confirmed against the **verified tarball**, not just prose.

The report's claim that `huggingface_hub`'s `?revision=<sha>` fell back to `main` is corroborated by
scratch: both `hf-model-main.json` and `hf-model-pinned.json` carry `sha = ea30d66d…` (size
742,090,464, sha256 `3fc991d3…`), i.e. the "pinned" query returned `main`.

---

## 3. Traceability of the headline numbers

| report number | source (committed/scratch) | status |
|---|---|---|
| append-only 1.0 / 0 / 0 across 20 runs | `data/analysis.jsonl` + re-derivation | **confirmed** |
| jfk 0.000 / quiet-short 0.259 / quiet-long 0.167 / car 0.312 (word_diff) | `data/analysis.jsonl`, `cli_runs.jsonl` (0.0/0.2593/0.1667/0.3125) | **confirmed** |
| per-chunk Vulkan ~41–45 ms, CPU ~72–88 ms | `data/paced_summary.jsonl` (p50 40.8/41.2, p90 44.7/45.0; CPU p50 71.8, p90 87.7) | **confirmed** |
| first text ~0.85–1.16 s (wall) | `paced_summary.jsonl` `first_interim_wall_s` 0.854/1.164 | confirmed (see F5 wording) |
| RTF 1.8–4.6 (C-ABI) / 0.98–0.99 (paced) | `stream_summary.jsonl` [1.784…4.583]; `paced_*` [0.984…0.997] | **confirmed** |
| chunk sweep stream s / RTF / p50 / first text | `data/chunk_sweep.jsonl` (all 10 rows match §5.4) | **confirmed** |
| CPU↔Vulkan word-diff divergence table (jfk 0.000 … quiet-short 0.185, car 0.091) | re-derived from `cli_runs.jsonl` transcripts | **confirmed** (quiet-long 0.072 vs report 0.101 — my simpler normalisation; direction same) |
| whisper `medium q5_0` = 0.062 on the car clip | `whisper-cpp-provider/REPORT.md` §1 + `data/runs.jsonl` (`medium-q5_0-commit` = 0.0625) | **traces, but needs the `-nt` qualifier — F2** |
| whisper `medium` quiet-long 0.153 | `whisper-cpp-provider/data/runs.jsonl` `medium-q5_0-quiet-long` = 0.1528 | **confirmed** |
| "the same engine runs Parakeet v3 at much higher accuracy" | published FLEURS, not measured here | **overclaim — F3** |

---

## 4. Findings

### F1 — **CHANGES REQUESTED** — §1 inverts the RSS direction

`REPORT.md` line 37 (the §1 headline answer), verbatim:

> "…strictly append-only incremental text, with no seam duplication, no cut-off hallucination, and no
> silence hallucination — but **it costs ~4× the RSS of CPU to save only a little latency**, and the
> Hungarian accuracy that motivated the epic is not measurable here."

This is **inverted** and contradicts the report's own evidence:

- `data/stream_summary.jsonl` / fresh C-ABI run: **Vulkan peak RSS ≈ 197 MB, CPU ≈ 1,064 MB**.
- `data/paced_summary.jsonl`: Vulkan `197.1`, CPU `1063.4`.
- Fresh CLI run: Vulkan `180.7`, CPU `1848.6`.

So **CPU costs ≈5× the host RSS of Vulkan** (the q8_0 weights live in VRAM on the Vulkan path), not
the reverse. The report says exactly this everywhere else — §5.2 line 240 ("peak RSS ~197 MB (Vulkan)
vs ~1,064 MB (CPU)"), §11 line 489 ("Vulkan is 5–10× lighter in host RAM"), and `001-comment.md`
lines 19–20 & 71 ("the GPU win here is host RAM (~197 MB vs ~1 GB), not speed"). **Only line 37 is
wrong, and it is the sentence a reader of the headline takes away.** It can drive the wrong
default-device decision, so it must be fixed.

The second half of the same clause — "to save only a little latency" — is also understated: Vulkan is
~41–45 ms/chunk vs CPU ~72–88 ms (roughly 2×, ~3.5× vs ~1.8× real-time headroom). "modest CPU time"
(§11) is fairer.

**Fix:** state it as *"Vulkan saves host RAM (~0.2× CPU's RSS) and roughly halves per-chunk latency, at
the cost of ~950 MB of VRAM."*

### F2 — minor — the whisper `0.062` needs its `-nt` qualifier

`REPORT.md` lines 62, 403, 407 and `001-comment.md` line 34 quote whisper `medium q5_0` = **0.062** on
the car clip. That number is real (`runs.jsonl`), but the whisper report's own addendum
(`whisper-cpp-provider/004-addendum-nt-decode.md`, and its §1 caveat) says it **holds only under
`whisper-cli -nt`**; with default flags it is **0.1562**. The fair car comparison is therefore
`0.312 vs 0.156` (≈2×), not `0.312 vs 0.062` (≈5×). The direction (whisper more accurate) is
unaffected. Recommend adding "(with `-nt`; 0.156 without)".

### F3 — minor — "same engine runs Parakeet v3 at much higher accuracy" is published, not measured

`REPORT.md` line 408. Ticket 54 (the Parakeet measurement) is **obsolete / not run**, so this is the
survey's **published FLEURS** figure (`15.72` vs `28.7–33.4`), and it is a **Hungarian** number, not
English. Recommend "the survey's published Parakeet-v3 FLEURS is much higher (batch-only)".

### F4 — minor/traceability — only 6 of 25 per-partial traces are committed

`data/partials/` holds **6** files (`paced-jfk-vulkan`, `silence-10s-{cpu,vulkan}`,
`stream-ges2t-…-vk`, `stream-jfk-{cpu,vk}`). The other **14** streaming traces and all **5** sweep
traces exist only in the scratch dir, and the committed traces lack `word_times`, so
`analyze_streaming.py` **cannot** be re-run from the repo alone — it needs the scratch dir. The
aggregate `data/analysis.jsonl` (25 rows) is committed and I reproduced it exactly from scratch, so
the result is not in doubt; but "record **every** partial" (ticket DoD) is only partially committed.
The report should either commit the full trace set or state the committed subset explicitly. Not
blocking (the scratch dir is present and named).

### F5 — cosmetic — "0.85–1.16 s of speech" mixes wall and audio seconds

§1 point 3 says "first text after ~0.85–1.16 s **of speech**"; those are `first_interim_wall_s`
(0.854/1.164), while the audio-watermark values are 0.96–1.28 s (§5.3 labels both). Say "wall".

### F6 — cosmetic — "numpy appears nowhere in the repo"

§11. Literally, `numpy` appears in prose/comments and in `tests/test_interfaces.py::BANNED_MODULES`;
what is true is **no numpy dependency or import anywhere in the measured path**. The measured scripts
only mention it as "no numpy" (verified by grep). Reword to match the true claim.

---

## 5. Honest-caveat checklist (all present and correct)

- **Vulkan ≠ CPU byte-identical** — report §1.6, §6, §9, `001-comment.md` ("do not claim parity").
  Verified: jfk differs by trailing `.`; all 10 CLI clips differ; divergence 0.000–0.185 (re-derived).
- **English WER worse than whisper, attributed to the model, not the engine** — §8, §1.5. Verified
  against `analysis.jsonl` / `cli_runs.jsonl` (car 0.312 vs whisper 0.062/0.156).
- **Hungarian not locally measured (published FLEURS only)** — §1.7, §8, §9, `001-comment.md`
  escalation #1. Verified: no Hungarian clip/transcript in scratch; the ticket says no microphone.
- Vulkan/CPU divergence treated as an integration constraint (per-backend golden transcripts) —
  §9, `001-comment.md` escalation #2.

## 6. ADR drafts

- **ADR 0022** and the **ADR 0011 amendment** are both drafted **verbatim in the report** ("Draft ADR
  0022 / ADR 0011 amendment" section), which the ticket allows ("in the report or in `001-comment.md`").
- **Nothing was written into `docs/adr/`** — `git diff --name-only main...HEAD -- docs/adr/` is empty.
- **The amendment is internally consistent with ADR 0011.** It retires the 5 s `window_seconds` and the
  two-pass agreement rule **only for cache-aware streaming engines**, and explicitly **preserves** the
  existing contract: committed is append-only and never revised; only committed text is injected;
  `pending` is display-only; ADR 0012's trailing space once; commit at word boundaries with the
  trailing partial held pending; and "validation must say so, not silently accept it" (ADR 0020). These
  are exactly ADR 0011's accepted rules — the amendment narrows *where* the window applies, it does not
  loosen the commit contract. No contradiction found.

## 7. Boundaries + Definition of Done

| DoD item | verdict | evidence |
|---|---|---|
| Engine runs; sha256 verified; extract + `doctor` recorded | **pass** | tarball sha256 `c8d0ed5b…`/16,618,142 B ✓; `doctor` reproduces `backend_vulkan` + device `[0]` RADV PHOENIX |
| Vulkan actually used, proven; CPU/Vulkan byte-compared | **pass** | fresh runs: `backend=Vulkan0`/`backend=CPU`; RSS 197 vs 1,063; VRAM 952.5 vs 0; transcripts differ |
| Latency/RTF/RSS on clips + user capture; chunk sizes; warm-up vs steady; VRAM | **pass** | §5.1–5.4; `cli_runs.jsonl`, `stream_summary.jsonl`, `paced_summary.jsonl`, `chunk_sweep.jsonl`, `timings.md` |
| Committed-delta stability (append-only, seam dup, cut-off, silence) | **pass** | §1 above — 1.0/0/0 re-derived; metric falsified by mutation |
| Accuracy vs English ground truth; Hungarian published-only | **pass** | §3 table; Hungarian unmeasured stated in §1.7/§8/§9 |
| Confounds/limits + what-would-falsify + answer/recommendation | **fail** | section present and falsifiers concrete, **but the §1 answer sentence inverts the RSS direction — F1** |
| ADR 0022 + ADR 0011 amendment drafted; nothing in `docs/adr/` | **pass** | report §"Draft ADR …"; `git diff … -- docs/adr/` empty |
| `001-comment.md`: numbers, revision/sha256, safe-to-relay, escalate | **pass** | all sections present; revision/sha256 resolved; 5 escalations |
| Bounded runs; processes exited/cleaned; scratch named & tidy | **pass** | `timeout 240–300` in `run_matrix.sh`; no `nemo-speech` process left (`pgrep` → none); scratch named in report |
| No `src/`/`tests/` change; no binary/model/audio committed; clean; 0 broken links | **pass** | `git diff --name-only main...HEAD -- src/ tests/` empty; no `.wav/.gguf/.tar.gz/.so` in `git ls-files`; largest committed file 47,939 B; `git status --porcelain` empty; link check → `0 broken references` |
| Committed on branch; ticket + README row `in review` | **pass** | single commit `4e8fb90`; `ticket.md` `Status: in review`; README line 158 `in review` |

**Also true:** no `sudo`, no network during measurement, no microphone (script-level review); no
`numpy` import in the measured path.

## 8. Hygiene

- All probes outside the repo (`/tmp/review55_*`, `/tmp/falsify55*`); removed after use.
- No `src/`/`tests/` file touched; `git status --porcelain` empty before and after.
- No `nemo-speech` process left running (`pgrep -af nemo-speech` → none).
- `rm -rf .mypy_cache __pycache__` not required (no mypy/python-cache probe on repo files). Never
  recorded from the microphone, clipboard, keystrokes, windows, notifications; no `sudo`; no network.

## 9. Could not verify (and each reason is itself true)

1. **HF revision `1c8deae…` against the live HF LFS headers** — no network (machine-safety rule).
   Mitigated: the same revision/size/sha256 triple is in the **engine's own `model-index.json`** inside
   the sha256-verified tarball, and matches the downloaded GGUF.
2. **The original run's wall/RTF timings exactly** — load-sensitive; the matrix ran under load average
   ~1.4–3.3 (not reproducible now). My fresh runs differ slightly (e.g. CLI jfk Vulkan wall 2.83 s vs
   the committed 5.11 s) but the **direction and magnitude** of every headline hold.
3. **VRAM Δ attribution to this exact process** — `sysfs mem_info_vram_used` is a global counter, so a
   concurrent GPU user adds noise; I confirmed 952.5 MB (Vulkan) vs exactly 0.0 (CPU) on a fresh run.
4. **Load average ~1.4–3.3 during the original session** — historical; `uptime` now would not
   reconstruct it.
5. **The 5 sweep runs' append-only from the repo alone** — the sweep partials are not committed; I
   re-derived them from the (present) scratch dir → all `1.0`/`0`.
6. **`pytest`/`mypy`** — not run: the branch changes **no** `src/`/`tests/` file (verified), so main's
   suite is unchanged and the epic's acceptance of green tests is main's, not this ticket's.
7. **The exact `word_diff` divergence for quiet-long (report 0.101)** — my simpler normaliser gave
   0.072; the difference is the ticket-32 tokeniser, which I did not re-implement. Direction confirmed.

## 10. Risks / follow-ups

- **R1 (blocking-ish):** the §1 RSS inversion would, if relayed, argue *against* the correct default
  (Vulkan), which the report itself recommends on RAM grounds. Fixed by the F1 reword.
- **R2:** the committed evidence is a *sample* of per-partial traces (F4); a future reader without the
  scratch dir cannot re-derive the non-committed runs. Consider committing the full set under
  `data/partials/`.
- **R3 (for the ADR/integration):** the CPU↔Vulkan transcript divergence (up to 0.185 WER) means any
  golden-transcript test must be **per-backend**, and the default device must be pinned explicitly
  (already captured in `001-comment.md` escalation #2 and the ADR 0022 draft §6.4).

## 11. Safe to relay to the user?

**Yes, with one correction before the headline is quoted.** The two central claims — *append-only
streaming text, no seam duplication/cut-off/silence hallucination on all 20 runs*, and *Vulkan is
really used (backend line + 952 MB VRAM + 197 vs 1,064 MB RSS)* — are **independently confirmed** and
safe to relay. The pins (`c8d0ed5b…`, `a5c435f2…`/741,548,352 B/`1c8deae…`) are confirmed.

**Not safe to relay as-is:** the §1 sentence "it costs ~4× the RSS of CPU to save only a little
latency" — the direction is inverted. After that single line is corrected (CPU costs ~5× Vulkan's
host RSS; Vulkan roughly halves per-chunk latency at the cost of ~950 MB VRAM), the report is safe to
relay. F2/F3 are recommended qualifications; F4/F5/F6 are cosmetic.

## 12. What would falsify the central claim (reviewer's re-statement)

The central claim ("append-only stable incremental text on this PC on Vulkan") is **falsified** by any
one run with `append_only_char_frac < 1.0`, `rewritten > 0`, or an adjacent duplicate word in a
committed text, or by non-empty text on digital silence, or by paced RTF ≥ 1. I hunted for all five:
none occurred. I additionally demonstrated the metric **can** fail (mutations A/B) so a green result
means something.
