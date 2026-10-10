# 001 — review: whisper.cpp provider discovery

- **Artifact:** [`REPORT.md`](REPORT.md) at commit `09a082a` (`research(whisper-cpp-provider): whisper.cpp provider discovery (CPU + Vulkan)`)
- **Branch:** `research/whisper-cpp-provider` · **Worktree:** `.worktrees/whisper-cpp-provider`
- **Supporting artifacts:** [`data/runs.jsonl`](data/runs.jsonl) (103 rows), [`data/timings.md`](data/timings.md), [`measure_whisper_cpp.py`](measure_whisper_cpp.py), [`render_tables.py`](render_tables.py)
- **Diff range:** `git diff main...HEAD --stat` → 5 files, 1431 insertions, all under `docs/project/research/whisper-cpp-provider/` (no `src/`, no `tests/`).
- **Reviewer:** independent verification; no file under the artifact was modified. Every command below was run with a `timeout`; no microphone, clipboard, keystroke, window, notification, `sudo`, package install, or service change.

## Verdict

**changes requested.**

The *data* is sound and reproducible: every one of the 94 scored WERs recomputes exactly from the stored transcripts with ticket 32's own `word_diff`, the model sha256 pins verify against the on-disk files, `render_tables.py` reproduces `data/timings.md` byte-for-byte, and the CPU-vs-Vulkan identity claim holds on all 7 pairs. The central recommendation (whisper.cpp is viable; `medium q5_0` is the accuracy leader; don't ship f16; greedy; `supports_streaming = False`) follows from that data.

But the report contains **one measured-sounding claim that its own data refutes** and several smaller overstatements:

1. **§5 (and §3) are false as written.** "every quant of a given size produced a byte-identical transcript" is not true for `small` (two words differ) or `medium` (punctuation differs). This is the explicit mechanism behind the quantisation conclusion, so it is load-bearing prose, not a detail.
2. §3 "merely ties medium on the quiet clips" is false for `quiet-short` (turbo 0.333 vs medium 0.296 std).
3. §9 prose "`.en` … `base.en` is *worse* than `base` on both quiet clips" contradicts §9's own table (equal 0.407 on `quiet-short`).
4. §1/§3/§14 RTF **13.8** contradicts §8's table (14.6) and the quoted 1.72 s (25.044/1.72 = 14.56).
5. "5–6× error reduction" (0.281→0.062) overstates: 0.281/0.062 = **4.53×** (0.391/0.062 = 6.31×).

None of these overturn the direction of the result; all are corrections to the report's prose. The fix is a small edit to the report, not a re-measurement. Because a measurement artifact that states a false "measured" fact is worse than one that states less, I am not passing it until §5/§3 are corrected.

---

## Per-claim findings and evidence

### 1. Recompute every WER from `runs.jsonl` with ticket 32's `word_diff`/`tokenise` — **PASS**

Ticket 32's harness was imported **by path**, registering `sys.modules[name] = mod` before `exec_module`, under `uv run python` (so `ges2t` imports). References come from the harness's own `REFERENCES` table; `standard_wer` is the harness's own function.

Command:

```
uv run python /tmp/probe_wer.py
```

Output (verbatim):

```
rows loaded: 103
scored rows: 94, unscored: 9
worddiff mismatches: 0
std mismatches: 0
```

Every stored `worddiff_wer`/`worddiff_sub`/`worddiff_ins`/`worddiff_del`/`reference_words` and `std_wer`/`std_edits` reproduces exactly. The 9 unscored rows are the `clip5`/`clip30` window-cost rows (no reference), which the report correctly reports as unscored. Metric implementation read directly: a `replace` block charges `max(m,n)` substitutions **plus** `|m−n|` insertions/deletions — the double-count §2.5 claims.

### 2. Cross-harness comparison is same-metric — **PASS (with one honest caveat)**

- Ticket 39's `measure_accuracy_levers.py` imports ticket 32's harness by path (`measure_accuracy_levers.py:27–28, 86–91, 194–203`), and its scoring function calls `ticket32.word_diff` / `ticket32.tokenise` (`:531–535`). So ticket 39's 0.281/0.391 and this report's 0.062 use the **same** `word_diff`.
- The reference passages are **character-identical** (verified programmatically): `True`, both 64 words.

Command/output:

```
character-identical: True
whisper ref words: 64
levers  ref words: 64
```

So the headline comparison is apples-to-apples **on metric and reference**. Caveat the report already states: it is not identical *audio* — ticket 39's `batch` fed the gate-passed 25.00 s audio, this harness fed the raw 25.044 s WAV. The report calls this "cross-harness and cross-path"; that is honest.

### 3. §5 quant-invariance — **FAIL (claim falsified), conclusion survives**

§5 states: *"**Accuracy is quantisation-invariant** here: every quant of a given size produced a byte-identical transcript and the same WER."* §3 repeats "WER is byte-for-byte identical across q5_0/q8_0/f16 at both `small` and `medium`."

Reproduced from `data/runs.jsonl` (within-config transcripts are deterministic; the diff is across configs):

```
=== §5 small quant transcript diffs (car) ===
  ggml-small-q5_0.bin vs ggml-small-q8_0.bin: byte-identical=False
      -expects
      +expect
      -flood
      +fad
  ggml-small-q8_0.bin vs ggml-small.bin: byte-identical=True

=== §5 medium quant transcript diffs (car) ===
  ggml-medium-q5_0.bin vs ggml-medium-q8_0.bin: byte-identical=False
      -Valley,
      +Valley
      -tonight,
      +tonight
  ggml-medium-q8_0.bin vs ggml-medium.bin: byte-identical=True
```

So **`small q5_0` differs from `small q8_0`/`f16` in two words** (`expects`/`expect`, `flood`/`fad`), and `medium q5_0` differs from `medium q8_0`/`f16` by two commas. The WERs coincide (0.141/0.141/0.141 and 0.062/0.062/0.062) **by luck**, not by byte-identity:

```
  ggml-small-q5_0.bin: wd_wer=0.140625 sub=8 ins=1 del=0
      replace: ref[18:20]=['crews', 'expect'] -> hyp[18:20]=['cruise', 'expects']
      (flood stays correct)
  ggml-small-q8_0.bin: wd_wer=0.140625 sub=8 ins=1 del=0
      replace: ref[18:19]=['crews'] -> hyp[18:19]=['cruise']
      replace: ref[42:43]=['flood'] -> hyp[43:44]=['fad']
```

`q5_0` gets `flood` right but `expects` wrong; `q8_0`/`f16` get `expect` right-ish but `fad` wrong. Two offsetting substitutions → the same 8 subs + 1 ins. (The orchestrator's spot-check said `resumes`/`expects` vs `expect`; `resumes` is in all three — the actual word pairs are `expects`/`expect` and `flood`/`fad`.)

**Does the conclusion survive? Partially.** The numeric claim "no accuracy is bought by keeping more bits" and "never ship f16" survive: q8_0 and f16 have identical transcripts, and q5_0's WER equals them. What does **not** survive is the *mechanism* the report gives (byte-identical transcripts). The honest statement is: *WER is equal across q5_0/q8_0/f16; for `medium` the word sequences are identical (only punctuation differs); for `small`, q5_0 and q8_0/f16 differ in two words and happen to score equally.* §13.4's "*q8_0 identical*" is likewise only true at the WER level.

This is the one blocking finding.

### 4. CPU-vs-Vulkan identity claim — **PASS**

Compared directly from `runs.jsonl` for each (model, audio, quant, decode):

```
=== CPU vs Vulkan byte-identity ===
  ggml-small-q5_0.bin / car.wav: cpu_n=9 vk_n=3 byte_identical=True
  ggml-medium-q5_0.bin / car.wav: cpu_n=5 vk_n=3 byte_identical=True
  ggml-medium-q5_0.bin / jfk.wav: cpu_n=1 vk_n=1 byte_identical=True
  ggml-medium-q5_0.bin / quiet-long.wav: cpu_n=1 vk_n=3 byte_identical=True
  ggml-large-v3-turbo-q5_0.bin / car.wav: cpu_n=4 vk_n=3 byte_identical=True
  ggml-large-v3-turbo-q5_0.bin / jfk.wav: cpu_n=1 vk_n=1 byte_identical=True
  ggml-large-v3-turbo-q5_0.bin / quiet-long.wav: cpu_n=1 vk_n=1 byte_identical=True
pairs compared: 7
```

7/7 byte-identical, exactly as §8 claims. I also independently reproduced §8's device line by running the already-built Vulkan binary (no `-np`), bounded:

```
ggml_vulkan: 0 = AMD Radeon 780M Graphics (RADV PHOENIX) (radv) | uma: 1 | fp16: dot2 | ...
whisper_backend_init_gpu: using Vulkan0 backend
```

The line matches §8 verbatim, and the transcript on `jfk`/`medium` matched. §13.3 (Vulkan is actually used) holds.

### 5. §9 ranking and `.en` non-generality — **mostly PASS; two prose errors**

From `runs.jsonl` (std WER):

| model | car | quiet-short | quiet-long | n (car / q-s / q-l) |
|---|---|---|---|---|
| tiny | 0.281 | 0.407 | 0.319 | 3 / 1 / 1 |
| base | 0.234 | 0.407 | 0.139 | 5 / 1 / 1 |
| base.en | 0.141 | 0.407 | 0.153 | 3 / 1 / 1 |
| small | 0.141 | 0.333 | 0.139 | 9 / 1 / 1 |
| **medium** | **0.062** | **0.296** | **0.111** | 5 / 1 / 1 |
| large-v3-turbo | 0.125 | 0.333 | 0.111 | 4 / 1 / 1 |

- `medium` is strictly best on car and `quiet-short` and **tied with turbo** on `quiet-long` — §9's table is accurate; every quiet-clip row is **n=1** (the caption says "single run each", honest). §13.1's parenthetical "accuracy leader" is mildly generous given the tie.
- **§3 is wrong**: "large-v3-turbo … merely ties medium on the quiet clips". On `quiet-short` turbo is 0.333 vs medium 0.296 — turbo is worse. It ties only on `quiet-long`, which §9 correctly says.
- **§9 prose is wrong**: "`base.en` is *worse* than `base` on both quiet clips". On `quiet-short` both score **0.407** (equal); `base.en` is worse only on `quiet-long` (0.153 vs 0.139). This contradicts §9's own table.
- The `.en` non-generality claim itself holds: `small` beats `base.en` on both quiet clips (0.444 vs 0.556; 0.194 vs 0.208 word_diff), and `base.en`'s car-clip advantage does not replicate.

### 6. §13 falsification honesty and §14 recommendation — **PASS with caveats**

§13 is broadly honest: every "It held" line traces to data I recomputed (items 1–4 above). Two caveats: item 1's "leader" should be "best-or-tied" (quiet-long is a tie with turbo), and item 4's "*q8_0 identical*" inherits the §5 transcript problem (equal WER, not identical transcript). Items 5–7 are genuinely marked as untested. §14's recommendation (subprocess shape, medium q5_0 on Vulkan, small q5_0/q8_0 on CPU, greedy, `supports_streaming=False`) follows from the measured data and the §8/§10 numbers.

### 7. Prose arithmetic — **overstatements flagged**

```
  0.281/0.062 = 4.532      (the batch comparison: report says "roughly a 5–6× error reduction")
  0.391/0.062 = 6.306      (the streaming comparison)
  25.044/1.72 = 14.560     (report §1/§3/§14 pair "RTF 13.8" with "1.72 s")
  25.044/1.821 (slowest of the 3 VK medium runs) = 13.753   <- where "13.8" actually comes from
```

- "5–6×" (§1, §14) overstates the batch ratio; the truthful span is **~4.5–6.3×**. §1 does hedge that "the direction, not the exact ratio, is the claim", but the number is still wrong for the batch figure.
- **RTF 13.8 is internally inconsistent**: §8's own table and `data/timings.md` give the median Vulkan `medium` RTF as **14.6/14.57**; 13.8 is `25.044 / 1.821`, the single slowest run, paired with the *median* wall 1.72 s. The commit message repeats "RTF 13.8". Fix the headline to 14.6 (median) or pair 13.8 with 1.82 s.
- Minor: §1's "only CPU tiny is faster, and at 5× its error rate" is fine (tiny word_diff 0.3125 / medium 0.0625 = exactly 5×).
- Minor: §8 "Speed-ups ranged 4.7–11.8×". The actual range over all CPU-run×VK-run pairings is **4.69–13.44×** (medium car best-CPU/worst-VK to worst-CPU/best-VK); 11.8 is the quiet-long ratio, not the maximum. The stated range is conservative, not inflated.
- Minor: §4 table header says "median over repeats", but the **peak RSS** column (333/825/726 MB) uses the per-config **minimum**, while `render_tables.py`/`timings.md` use the median (336/828/733). Same numbers appear in §11.6.

### 8. No `src/`/`tests/` change, no committed binary, caveat applied — **PASS**

```
git diff main...HEAD --stat -- src tests      -> (empty)
git ls-tree -r HEAD --name-only | grep -Ei '\.(bin|wav|mp3|flac|ggml|so|onnx|pt)$'  -> NONE
```

All 6 model sha256 pins in §11.4 verify against the on-disk files (command `sha256sum`, output):

```
bf65504605bf…  ggml-tiny-q5_0.bin          0238cb211a43…  ggml-base-q5_0.bin
23ad5215d376…  ggml-small-q5_0.bin         19fea4b380c3…  ggml-medium-q5_0.bin
394221709cd5…  ggml-large-v3-turbo-q5_0.bin  49c8fb02b65e…  ggml-small-q8_0.bin
```

All sizes match §2.2/§11.4. The reports' "absolute WER is an upper bound" caveat is applied in §1, §2.5, §3, §12.

### Harness / renderer robustness (checks that can fail)

- `render_tables.py` reproduces `data/timings.md` **byte-for-byte**: `diff /tmp/timings_regen.md data/timings.md` → `IDENTICAL`.
- Its determinism guard is real, not decorative: on a mutated copy of `runs.jsonl` (one `worddiff_wer` changed) it raised `AssertionError: WER must be deterministic per config` and wrote no file.
- **However, the §5 "byte-identical transcript" claim has no corresponding check anywhere.** The renderer only asserts equal WER per config; byte-identity across quants is prose-only and untested — which is exactly how the false claim survived to the report.

### Provenance / data integrity

- All 103 rows have `exit_code = 0`, `threads = 16`, `lang = "en"`, no `TIMEOUT` notes; `model_bytes` is constant per file across rows. 9 unscored rows are the `clip5`/`clip30` window-cost runs.
- `data/download.log` and `download_models.sh` corroborate the canonical downloads for `medium`/`large-v3-turbo` q5_0 and q8_0, and the f16 sources. Note the inventory in §2.2 omits `ggml-tiny-q8_0.bin`, `ggml-base-q8_0.bin`, `ggml-small-q5_1.bin`, `ggml-base-q5_1.bin`, which exist on disk and (for q5_1) are reported in §5 — a completeness nit, not an error.

---

## Risks

- **The false §5 sentence is the highest risk**: it is the stated basis of the "quantisation-invariant" conclusion, and a reader would take it as measured.
- **Single-recording, n=1 quiet clips.** The second condition that carries the recommendation rests on one run per model on each quiet clip; the ranking is stable, but the variance is unmeasured.
- **Cross-harness comparison is not same-audio** (gate-passed vs raw). The direction is robust; the exact ratio is not a controlled measurement.
- **Concrete transcript drift across quants for `small`** — if a canonical transcript is ever pinned, q5_0 and q8_0 are not interchangeable at the word level even though their WER is equal.
- **CPU timing spread up to 2.6×** and f16's very slow runs (`medium` f16 jfk = 95.5 s, not mentioned) mean the f16 speed penalty is real but the per-clip attribution of the 74.7 s outlier to a "concurrent build" is asserted without a recorded cause.

## Recommended follow-ups

1. **Correct §5/§3**: state *equal WER* across q5_0/q8_0/f16, note that `medium`'s word sequence is identical (punctuation only) and `small`'s differs by two words that cancel. §13.4 to match.
2. Correct §3's turbo/quiet-clip line and §9's "base.en worse than base on both quiet clips" (equal on quiet-short).
3. Fix the RTF (13.8 → 14.6, or pair 13.8 with 1.82 s) and change "5–6×" to "~4.5–6.3×".
4. Reconcile §4/§11.6 peak-RSS (min) with §4's "median over repeats" header and `timings.md` (median).
5. Keep the existing follow-ups (real adapter + framework-overhead measurement, a second car recording, number/spelling normaliser, faster-whisper head-to-head).

## Could-not-verify (each reason is itself true)

- **Upstream Hugging Face provenance** — that `ggml-org/whisper.cpp` returns 404, and which quants upstream publishes. No network is permitted here; I verified only that the on-disk files and their sha256 pins agree.
- **That the locally-quantised `small-q8_0` equals the canonical published file** — only one such file remains on disk; the claimed two-file comparison is not reproducible now. The sha256 pin itself does verify.
- **Build time (54 s) and the exact built revision** (`d1be6fde…`, tag v1.9.5) — I did not rebuild; I did run the already-built binary to reproduce the device line.
- **The cause of the 74.7 s `medium` f16 outlier** ("taken during a concurrent build") — the row's note is only `"quant effect"`; no retained log records a concurrent build at that instant, and the sibling f16 run (`jfk`) was 95.5 s.
- **The quiet-clip reference texts as actually read** — supplied by the orchestrator; not independently checkable.
- **Ticket 39's harness run** — I read its source and its recorded numbers but did not re-execute it (it drives the real Whistle engine).
- **The device line as a committed artifact** — it was not retained in the repo; I reproduced it live from the built Vulkan binary, so the *claim* is verified even though the *captured file* does not exist.

## What would falsify the central claim

The central claim is: *whisper.cpp is a viable second provider and `medium q5_0` is the most accurate option, ~4.5–6.3× lower error than Whistle on this recording.* It would be falsified by any of:

- **A recomputation of any stored WER that disagrees with the `worddiff_*` fields** — checked; 0/94 disagree.
- **The quiet-clip second condition reversing the ranking** — it does not; `medium` is best or tied on all three.
- **The two harnesses using a different metric or reference** — they do not (same `word_diff`, character-identical 64-word reference).
- **CPU and Vulkan producing different transcripts** — they do not (7/7 byte-identical).
- **A second recording of the car passage changing the ordering** — untested; this remains the real open falsifier.

The artifact itself should state, as its falsifier list already partly does, that the **cross-harness ratio** (not the model ordering) is the soft number: it compares different audio (gate-passed vs raw) and different engines.

## Safe to relay to the user?

**Not verbatim.** The measurement is reproducible and the headline direction — whisper.cpp is viable, `medium q5_0` is the accuracy leader here, q5_0 ≈ q8_0, never f16, greedy, batch-only, Vulkan ~10× faster at identical output — is supported by data I recomputed and can be relayed. But the report as written should **not** be relayed until §5/§3 are corrected, because they assert a "byte-identical transcript" across quants that the artifact's own data refutes, alongside the smaller overstatements in §3/§7/§9 and the RTF/"5–6×" numbers. After those edits, it is safe to relay.
