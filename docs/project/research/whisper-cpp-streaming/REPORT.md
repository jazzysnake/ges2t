# whisper.cpp streaming on Vulkan: is the partial-window output stable enough for ges2t's commit cadence?

- **Branch:** `research/whisper-cpp-streaming`
- **Worktree:** `.worktrees/whisper-cpp-streaming`
- **Date:** 2026-10-10
- **Machine:** reference box (Ryzen 7 7840HS + Radeon 780M iGPU), Arch + KDE Plasma 6, Wayland.
- **Scope:** a discovery. No shipped code changed; nothing under `src/ges2t/` or `tests/` was
  touched; models and audio were read from the existing cache, no network was needed.

## The question

Is whisper.cpp's **partial-window output stable enough** for ges2t's commit-cadence streaming —
*commit a prefix when N consecutive passes agree* — on the **Vulkan/GPU** path? At what
window/hop, which model, and what latency/duty? If it is not, exactly what fails.

## 1. Answer (headline)

**No — not safely, at any measured configuration.** The two-pass rule itself fires and mostly
behaves, but the committed text is corrupted at every window boundary, and the corruption is
structural, not a tuning problem:

1. **The window boundary commits the boundary word twice.** ges2t's `flush()` (the engine's
   `stop()`) commits the whole unconfirmed tail, then the next window re-transcribes the audio
   from the same cut point and commits the boundary word again. Concrete trace on the 11 s `jfk`
   clip, `medium q5_0`, W=5 s/H=2 s:

   ```
   pass 2  buffer [0,6]  "And so, my fellow Americans, ask not what your country"
                         FLUSH commits: not what your country
   pass 3  buffer [6,8]  "country can do for you."          <- "country" again
   pass 4  buffer [6,10] "country can do for you, ask what you can do for your country."
   committed:  ... ask not what your country country can do for you for you ask ...
   ```

   The result is `country country`, `for you for you` — adjacent duplicate words in the
   transcript a user would be typing into their editor. Whistle does not do this (ticket 32:
   "no duplicated or dropped words" across 6 `stop()` seams).

2. **In the H=2 regime the flush does most of the committing, so the rule barely protects
   anything.** On the car clip at W=5 s/H=2 s, `medium` committed 69 words of which **56 (81 %)**
   came from a `flush()` tail — a single pass, never confirmed by the agreement rule. The reason
   is arithmetic: a 5 s window fed 2 s at a time gets only three passes before it is reset, so
   the longest-common-prefix rule confirms 13 of 69 words and the boundary `stop()` supplies the
   rest. The committed text therefore rests almost entirely on exactly the text whisper is worst
   at — a hard cut mid-phrase.

3. **Whisper invents an ending at the cut, and the flush commits it.** The car clip is the
   real-device recording; at the 6 s cut `medium` rendered "…across the **north of the country**"
   instead of "…across the northern valley", and the flush committed it; the next window then
   added "Valley and crews", giving "…across the north of the country valley and crews…".
   `large-v3-turbo` commits **`thank you thank you` on 10 s of digital silence** (medium and small
   correctly emit whisper's `[BLANK_AUDIO]` marker and commit nothing), which is the `thank you`
   class the project already fears — and turbo is one of the models a streaming path would use.

4. **Accuracy drops 2.3–4.8×** on the real-device car clip against the same human reference and
   the same `word_diff`: streaming `medium` W=10/2 = **0.141**, W=5/2 = **0.297**, versus the
   merged report's one-shot batch **0.062**. On `jfk` the batch path is perfect (0.0) and streaming
   is 0.045–0.136; on `quiet-long` streaming is 0.15–0.21 vs batch 0.153.

**Speed is not the blocker; it is only marginal.** On the `-nt` decode (the merged report's), the
Vulkan duty cycle (total whisper-cli wall ÷ audio seconds, model load included) is **< 1 at
W=5/H=2 for all three models** — small 0.24–0.26, medium 0.62–0.70, turbo 0.75–0.84 — and at
W=10/H=2 too (small 0.26, medium 0.65–0.68, turbo 0.76–0.81). At **H=1 only `small` fits** (0.44–0.48);
medium (1.19–1.24) and turbo (1.49–1.53) fall behind real time. The `medium q5_0` **CPU** reference
at W=5/H=2 is duty **5.87** — unusable, as the merged report predicted.

**Recommendation:** keep whisper.cpp on the **batch** path (`supports_streaming = False`). Do not
ship a whisper.cpp streaming path. If streaming is revisited it needs a resident model (to fix
the H=1 budget), a seam de-duplication step, and a policy for the cut-off tail — none of which
ges2t's current protocol provides.

## 2. Method

### 2.1 The commit rule, mirrored from ges2t

`src/ges2t/streaming.py` builds the transcript as `transcript += pass_.committed` (append only)
and calls `decoder.flush()` once the audio fed since the last flush reaches `window_seconds`;
`flush()` models the engine's `stop()`, which commits the unconfirmed tail and resets the stream
(ADR 0011). The words `process()` returns as `committed` are **"the words two consecutive passes
agree on"** — the rule's **N is 2**, stated in ADR 0011 and in the `needle.h` header it quotes.
There is no vote count, threshold, or confidence in ges2t; the engine's `pending` tail is
display-only and is never injected.

I mirror that rule exactly: two consecutive pass transcripts are compared and the **longest common
prefix** is what they agree on. Agreement is **word-level, after ticket 32's normalisation**
(`measure_commit_cadence.py::_normalise_token`/`tokenise`): case-folded, curly apostrophes
dropped, punctuation a word separator, a `.` kept only between digits, so `4,000` → `4000` and
`don't` → `dont`. Using ticket 32's own functions keeps the final WER directly comparable to the
merged whisper.cpp report. The committed prefix is tracked **by word count inside the window**
(not by estimated timestamps), so a word cannot be committed twice because its time drifted;
`flush()` commits the whole remaining tail once and resets the count.

### 2.2 Two window schemes

Both are reported, because the question names the literal method ("the last W seconds of the
accumulating audio, advancing by H each pass") while the faithful mirror of ges2t is a buffer that
grows to W and resets.

- **`cap` (primary — the ges2t mirror).** A buffer starts empty at a reset point, grows by `H`
  seconds per pass, and when the audio fed since the reset reaches `W`, `flush()` commits the whole
  tail and resets the stream — exactly `streaming.drive()`. Consecutive passes see the **same
  growing buffer**, so their transcripts are prefix-comparable and agreement is the plain longest
  common prefix (no alignment assumption).
- **`slide` (the literal "last W seconds").** A fixed window `[t-W, t]` advancing by `H`. A fixed
  window rewrites its own head each hop, so agreement is measured only over the shared audio and
  matched as the longest **suffix of the previous transcript that is a prefix of the current one**.
  This is a harsher stability probe.

### 2.3 The decode matters: `-nt` vs `-ojf`

This cost a re-run and is worth stating plainly: **the merged report's decode used `-nt`**
(`measure_whisper_cpp.py::run_cli` passes `-nt -np`), and `-nt` is *not* output-only. On the car
clip, `medium q5_0` batch word_diff WER is **0.062 with `-nt`** and **0.156 without it** — a
different greedy decode, because `-nt` masks whisper's timestamp tokens. My first streaming harness
used `-ojf` (JSON-full token timestamps, no `-nt`) to place words on the source timeline, which is
therefore not comparable to the batch number. I re-ran the cap matrix with `-nt`; those runs are
the primary dataset. `-ojf` runs are kept only for real commit-latency timings and the slide
scheme, and are labelled. (`-ojf` is also ~0.4 s/pass slower, so its duty numbers are inflated.)

### 2.4 Harness, paths, and no 30 s padding

`measure_streaming.py` (this folder) slices the WAV with stdlib `wave`/`array` (**no numpy**),
writes each passing window to a scratch WAV, and runs
`whisper-cli -m <model> -f <wav> -l en -t 16 -bs 1 -bo 1 -np (-nt | -ojf -of <base>)` — greedy,
`q5_0`, the same decode parameters as the merged report. Every run is bounded by `timeout` and the
child is killed and reaped on expiry. The WAV is **never padded to 30 s**: whisper's encoder
already runs over a 30 s mel segment, so the CLI pads internally; appending silence would only add
decoder work and re-risk the project's silence-hallucination bug (merged report §9).

Everything heavy — sliced WAVs and per-pass JSON — lives in
`~/.cache/ges2t-discovery/whisper-cpp-streaming-scratch/`. The repo gains only the curated text
files under `data/`; no model, binary, or audio is committed.

### 2.5 Matrix

Models: **`medium q5_0` Vulkan (primary)**, **`small q5_0` Vulkan**,
**`large-v3-turbo q5_0` Vulkan**, one **`medium q5_0` CPU** reference point.
Configs: `W=5, H=2`; `W=5, H=1`; `W=10, H=2`. Inputs: `quiet-long` (23.81 s), the car clip
`ges2t-commit-cadence-capture-20261009-195914.wav` (25.04 s), `clip30` (30 s), `jfk` (11 s).
Control: 10 s of digital silence (generated in scratch). Raw per-pass transcripts are in
`data/passes/` (`-nt`) and `data/passes_ojf/` (`-ojf`); the rendered tables are `data/timings.md`.

Two confounds the reader must carry through the tables:

- **The session was not idle.** `uptime` load average was ~1.4–3.5 while the matrix ran (this
  agent, a browser, and a running `ges2t run` service). The duty numbers are therefore an **upper
  bound**; on an idle machine the merged report measured a `medium` Vulkan 5 s window at 0.97 s
  where this session measured 1.4–1.9 s on the `-ojf` path. The accuracy and stability findings are
  decode-deterministic greedy output and are not load-sensitive.
- **`clip30` has no human reference in the repo**, so its WER column is blank; its per-pass
  transcript is still evidence for stability. The car clip (0.062 batch), `quiet-long` (0.153) and
  `jfk` (0.0) use the same human references as the merged report.

## 3. Results

The full tables (all 85 rows across both decodes, both schemes, and the controls) are in
`data/timings.md`. The primary table is reproduced here for `medium q5_0` Vulkan (the merged
report's best model) and summarised for the others.

### 3.1 Primary — cap scheme, `-nt` decode, `medium q5_0` Vulkan

`latency` is the timestamped twin's real median audio lag (§3.2); all other columns are `-nt`.

| W/H | clip | passes | windows | duty | first commit (s) | latency (s) | prefix agree | regressed | rewritten | dup words | WER (stream) | WER (batch) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 5/1 | quiet-long | 24 | 5 | 1.222 | 2 | 0.86 | 0.898 | 7 | 17 | 0 | 0.208 | 0.153 |
| 5/2 | quiet-long | 12 | 4 | 0.618 | 6 | 0.27 | 0.891 | 0 | 8 | 0 | 0.167 | 0.153 |
| 10/2 | quiet-long | 12 | 3 | 0.682 | 6 | 2.06 | 0.589 | 2 | 9 | 0 | 0.153 | 0.153 |
| 5/1 | car | 26 | 6 | 1.242 | 3 | 1.13 | 0.698 | 4 | 19 | 0 | 0.281 | **0.062** |
| 5/2 | car | 13 | 5 | 0.646 | 4 | 1.66 | **0.288** | 2 | 8 | 1 | **0.297** | **0.062** |
| 10/2 | car | 13 | 3 | 0.671 | 4 | 2.92 | 0.45 | 1 | 9 | 0 | **0.141** | **0.062** |
| 5/1 | jfk | 11 | 3 | 1.191 | 2 | 0.357 | 0.933 | 2 | 6 | 1 | 0.045 | 0.0 |
| 5/2 | jfk | 6 | 2 | 0.696 | 4 | 0.65 | 0.552 | 1 | 4 | 1 | 0.136 | 0.0 |
| 10/2 | jfk | 6 | 2 | 0.675 | 4 | 1 | 1.0 | 0 | 4 | 1 | 0.045 | 0.0 |
| 5/1 | clip30 | 30 | 6 | 1.240 | 2 | 0.833 | 0.868 | 12 | 17 | 0 | — | n/a |
| 5/2 | clip30 | 15 | 5 | 0.606 | 4 | 0.625 | 0.961 | 0 | 6 | 0 | — | n/a |
| 10/2 | clip30 | 15 | 3 | 0.649 | 4 | 1.721 | 0.875 | 6 | 10 | 0 | — | n/a |

Definitions: **duty** = total whisper-cli wall ÷ audio seconds (includes per-invocation model
load; < 1 is the real-time condition). **first commit** = source-audio time of the first
committing pass. **prefix agree** = agreed words ÷ previous pass words over same-window pairs
(1.0 = the new pass fully reproduces the previous one). **regressed** = same-window pairs where
the new pass did not reproduce the words already committed in the window. **dup words** = adjacent
repeated words in the committed text.

### 3.2 Model × config summary (Vulkan)

| model | W/H | duty (quiet / car / clip30 / jfk) | first commit (s) | latency median (s) | prefix agree (range) | WER car | WER quiet | WER jfk |
|---|---|---|---|---|---|---|---|---|
| medium | 5/2 | 0.62 / 0.65 / 0.61 / 0.70 | 4–6 | 0.27–2.23 | 0.29–0.96 | 0.297 | 0.167 | 0.136 |
| medium | 5/1 | 1.22 / 1.24 / 1.24 / 1.19 | 2–3 | 0.86–1.31 | 0.70–0.93 | 0.281 | 0.208 | 0.045 |
| medium | 10/2 | 0.68 / 0.67 / 0.65 / 0.67 | 4–6 | 2.03–2.92 | 0.45–1.0 | 0.141 | 0.153 | 0.045 |
| small | 5/2 | 0.25 / 0.25 / 0.24 / 0.25 | 4–6 | 0.49–2.38 | 0.51–0.86 | 0.344 | 0.181 | 0.091 |
| small | 5/1 | 0.47 / 0.46 / 0.47 / 0.44 | 2–3 | 1.00–1.51 | 0.53–0.93 | 0.312 | 0.264 | 0.045 |
| small | 10/2 | 0.26 / 0.26 / 0.27 / 0.26 | 4–6 | 2.34–3.00 | 0.53–0.94 | 0.312 | 0.153 | 0.045 |
| turbo | 5/2 | 0.75 / 0.80 / 0.77 / 0.84 | 4–6 | 0.43–2.23 | 0.37–0.92 | 0.312 | 0.167 | 0.091 |
| turbo | 5/1 | 1.52 / 1.52 / 1.53 / 1.49 | 2–3 | 0.51–1.39 | 0.72–0.93 | 0.219 | 0.264 | 0.045 |
| turbo | 10/2 | 0.76 / 0.81 / 0.79 / 0.84 | 4–6 | 1.00–2.71 | 0.53–1.0 | 0.141 | 0.167 | 0.045 |

(`first commit`, `latency`, and the exact per-clip duty/WER come from `data/summary.jsonl`; the
ranges span the four clips. `medium q5_0` **CPU** W=5/H=2 is duty **6.11 / 5.87** (quiet / car)
on the `-ojf` decode — unusable, consistent with the merged report's CPU conclusion.)

### 3.3 What actually fails, precisely

**(a) Boundary duplication.** The committed text carries adjacent duplicate words at the seam in
most `car` and `jfk` runs: `medium` car W5/2 `restore restore`, `small` car W5/2 `the the`,
`turbo` car W5/2 `the the`, `jfk` `country country` in seven of nine configs, `for you for you`,
quiet-long turbo `for`/`which`. This is not natural repetition in the reference: the reference
`jfk` and car passages have no such adjacent repeats, and the batch transcript does not produce
them. The mechanism is traced in §1: the flush commits the boundary word, then the next window
re-emits it.

**(b) Accuracy loss.** On the real-device car clip, streaming `medium` W5/2 = 0.297 vs batch
0.062; even the best streaming config (W10/2) is 0.141 vs 0.062. The committed text:

```
W5/2  good morning last nights storm no doubt powered roughly 4 000 homes across the north
      of the country valley and crews expect to restore restore most of them by friday
      afternoon officials say the river crested at 3.2 meters just after midnight well below
      the flood barrier is that trading correct incorrect two low lying roads remain closed
      tonight and the ferry service resumes at half past six you
W10/2 good morning last nights storm no doubt powered roughly 4 000 homes across the
      northern valley and crews expect to restore most of them by friday afternoon officials
      say the river is crested at 3.2 meters just after midnight well below the flood barrier
      is that reading correct two low lying roads remain closed tonight and the ferry service
      resumes at half past six
reference ... across the northern valley, and crews expect to restore most of them ... the
      river crested at 3.2 metres ... Is that reading correct? Two low-lying roads remain ...
```

**(c) Prefix rewriting.** Even though the committed transcript is append-only, the *raw* window
output is not stable: `prefix agree` is 0.29–1.0, i.e. in the worst case (medium, car, W5/2) the
new pass reproduces only **29 %** of the previous pass's words. Concrete `small q5_0` car W5/2
passes showing successive windows disagreeing completely on the shared audio:

```
[6,10]   prev: than rally and cruise expects to really stop
         cur : northern rally and cruise expects to restore most of them      agreed 0/8
[12,16]  prev: reverse crested at 3.2
         cur : the river is crested at 3.2                                   agreed 0/4
[18,22]  prev: trading correct do low line
         cur : reading correct do low lying roads remain                      agreed 0/5
```

**(d) Hallucination.** On 10 s of digital silence, `large-v3-turbo q5_0` emits `Thank you.` in
every pass, so the two passes agree and the rule **commits it**: the committed text is
`thank you thank you` (8 commits). `medium` and `small` emit `[BLANK_AUDIO]` and commit nothing.
The `[BLANK_AUDIO]`/`[MUSIC]` markers are dropped by the harness as non-speech (verified).

### 3.4 The two-pass rule vs the flush

On the car clip:

| model | W/H | committed words | from `flush()` (single pass) | from the N=2 rule |
|---|---|---|---|---|
| medium | 5/1 | 68 | 14 (21 %) | 54 |
| medium | 5/2 | 69 | **56 (81 %)** | 13 |
| medium | 10/2 | 64 | 39 (61 %) | 25 |
| small | 5/2 | 70 | 40 (57 %) | 30 |
| turbo | 5/2 | 65 | 48 (74 %) | 17 |

At H=2 the agreement rule confirms only ~1–4 words per window and the boundary `stop()` supplies
the rest. This is the core reason the failures reach the transcript: the protecting rule is not
what is producing most of the text.

### 3.5 The fixed sliding window (`slide`, secondary, `-ojf` decode)

The literal "last W seconds" reading is *worse*. On the same clips, W5/2: duty 0.65–0.71 (medium),
prefix agree 0.35–0.49, WER 0.45–0.69; W5/1: duty 1.22–1.41, WER 0.41–0.48. The window's left edge
is rewritten every hop: on `jfk` W5/2 the committed text is
`and so my fellow americans americans ask what your country can do for you` — it both duplicates
`americans` and **drops `not`**. The `cap` scheme is the better model and is still not safe.

## 4. Comparison with the current Whistle streaming path (ticket 32)

| | Whistle (ticket 32, real device) | whisper.cpp (this run) |
|---|---|---|
| transcript model | append-only committed deltas | append-only in the harness |
| restatements/duplication at seams | **0** (no word duplicated or dropped across 6 `stop()` seams) | boundary word committed twice; adjacent duplicates in most car/jfk runs |
| median gap between commits | 1.00 s source audio | ≈ 1 hop (H), by construction |
| pending persistence | median 1.00 s | H + disagreement delay (latency 0.2–3.5 s median) |
| median per-chunk / duty | 148 ms, 0.19× realtime | 0.24–1.53 duty depending on model/H |
| accuracy vs batch | WER 0.176 vs 0.191 (no degradation) | car 0.141–0.344 vs batch 0.062 (2.3–5.5×) |
| hallucination on silence | `Thank you.` appeared in `pending` twice, **never committed** | turbo commits `thank you thank you` |

Whistle's engine commits the two-pass-agreed words and its `stop()` seams were clean; whisper.cpp
re-transcribes hard-cut windows, so its seams are not. The commit *cadence* threshold is met but
the *text* is not.

## 5. Confounds and limits

- **Load.** Duty is an upper bound (load average ~1.4–3.5 during the runs). On an idle machine the
  merged report measured 0.97 s for a `medium` Vulkan 5 s window; the same call here was 1.4–1.9 s
  on the `-ojf` path and ~1.2 s on `-nt`. `small` has ~4× headroom at W5/2 regardless.
- **Decode.** Primary numbers use `-nt`, matching the merged report; `-ojf` differs (batch car
  0.062 vs 0.156) and is used only for latency and the slide scheme. The WER comparison is
  therefore apples-to-apples, but it is a **greedy `q5_0`** result; beam search was not tested
  (the merged report found beam did not change WER).
- **References.** `clip30` has no human reference; `quiet-long`, `jfk`, and the car clip use the
  same references as the merged report. The car clip is read speech recorded on the real device;
  `quiet-long`/`jfk` are clean read clips.
- **Sample size.** One deterministic run per (model, config, clip) — greedy output is reproducible
  (the merged report showed Vulkan byte-identical), but the per-window wall times are single
  samples except where repeated. Four clips, ~2–30 s each; no long-dictation run > 30 s.
- **Latency.** The `-nt` decode has no token timestamps, so the real latency column comes from the
  `-ojf` twin (same windows, different decode). The committed *text* never depends on timestamps
  (the rule is count-based), so this affects only the latency figure.
- **Harness rule fidelity.** The two-pass agreement is implemented as a longest-common-prefix over
  normalised words; ges2t delegates that to the closed engine, which may implement "agree" with
  word-boundary/probability subtleties. The direction of the finding (unstable windows, dirty
  flush tails) does not depend on those subtleties.
- **The flush-tail policy is ges2t's, not the harness's.** `streaming.drive()` adds `flush()`'s
  return to the transcript unconditionally; the harness mirrors it. A different protocol that
  redacted or re-transcribed the tail could differ, but that is not ges2t's protocol.

## 6. What would falsify "whisper.cpp is usable for streaming here"

The central claim is: **on this machine, whisper.cpp's partial windows are not stable enough for
ges2t's two-pass commit-cadence streaming, at any measured W/H/model on Vulkan.** It would be
falsified by a run that shows all of:

1. **prefix agreement ≈ 1.0** on the real-device car clip at the chosen config — consecutive
   passes reproduce the previous pass, no rewrite (`prefix agree` ≥ ~0.95, and `regressed = 0`);
2. **no boundary duplication** — the committed text has no adjacent repeated word that the batch
   transcript and reference do not also contain;
3. **final committed WER ≈ batch** on the car clip (≈ 0.062, not 0.14–0.34) and on `jfk` (0.0);
4. **duty < 1** and **latency ≤ ~1 s** at a hop a user would accept (H ≤ 1 s);
5. and no hallucination committed on the silence control.

A different engine (Whistle) meets all five; a faster integration shape (resident model) can fix
(4); a seam-dedup post-step could fix (2). None of (1), (2), or (3) was met by any of the 36
Vulkan cap combinations measured here. The closest any config came is the car clip at W=10/H=2
(`medium`/`turbo`): no adjacent duplication and duty 0.67–0.81, but `prefix agree` is only
0.45–0.92 and the final WER is 0.141 against the batch 0.062 (an inserted `is` survives), so (3)
still fails and (1) fails for `medium`.

## 7. Recommendation

**Do not integrate a whisper.cpp streaming path.** Keep `supports_streaming = False` for the
whisper.cpp provider and take the batch path, exactly as the merged report recommended. If the
user wants whisper.cpp streaming anyway:

- use `small q5_0` Vulkan at W=5/H=2 (duty 0.25, the only config with real headroom at H=1 too),
  accepting the worst accuracy;
- or `medium q5_0` Vulkan at W=10/H=2 (best accuracy among streaming configs, duty 0.67, but
  latency ≈ 2–3.5 s and a 2.3× WER penalty);
- and budget for a seam de-duplication step and a resident-model integration, because the current
  protocol's `stop()` seam is where the errors land.

The shipped `ydotool` incremental typing cannot be fed this text safely: it types the committed
prefix, and the committed prefix contains duplicated and invented words at every seam.

## 8. Reproduction and cleanup

Run from the worktree (all commands bound by `timeout`; no network, no sudo, no microphone):

```bash
bash docs/project/research/whisper-cpp-streaming/run_matrix.sh cap-nt   # primary -nt matrix
for p in medium-vk small-vk turbo-vk slide medium-cpu silence; do
  bash docs/project/research/whisper-cpp-streaming/run_matrix.sh "$p"
done
PYTHONPATH=src python3 docs/project/research/whisper-cpp-streaming/recompute_summary.py
PYTHONPATH=src python3 docs/project/research/whisper-cpp-streaming/render_tables.py
```

`measure_streaming.py --verify-tokens` cross-checks the token→word reconstruction against ticket
32's `tokenise` on the car clip (62 == 62, MATCH). `run_matrix.sh` skips labels already present in
`data/summary.jsonl`. Heavy scratch (sliced WAVs, per-pass JSON) is under
`~/.cache/ges2t-discovery/whisper-cpp-streaming-scratch/` and is not committed. No model, binary,
or audio file is in the repo; `git status` in the worktree contains only the report folder.
