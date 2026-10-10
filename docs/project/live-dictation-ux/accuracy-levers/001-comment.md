# 001 — accuracy levers: what was measured, what it means, and the filter recommendation

- **Ticket:** [accuracy-levers](ticket.md) (ticket 39)
- **Branch:** `feat/accuracy-levers`
- **Worktree:** `.worktrees/accuracy-levers`
- **Date:** 2026-10-10
- **Status:** measurement complete, filter recommendation made
- **Harness:** [`measure_accuracy_levers.py`](measure_accuracy_levers.py) — a docs script, not
  part of the shipped package; `uv run pytest` never sees it (ADR 0014)
- **Raw reports (under `/tmp` only, as ticket 32 kept them):**
  `/tmp/ges2t-accuracy-levers-25s-report.txt` (the primary input),
  `/tmp/ges2t-accuracy-levers-clip5-report.txt` (the clean cross-check)

## 1. Headline

**Do not build a noise filter.** On the user's 25 s recording, a 60 Hz high-pass removes
**86.0 %** of the recording's AC power (a 150 Hz one removes 92.9 %) and moves word error
rate by **2 words out of 64** — from WER 0.391 to 0.359, below this measurement's explicit
4-word materiality bar — while introducing 3 new substitutions and fixing 5. The 85 Hz and
120 Hz corners fix a single word each. Clipping and level move the same 2 words or fewer.

The one lever that moved WER materially was **streaming versus batch on identical audio**:
0.391 for the shipped streaming default against **0.281** for the one-shot batch path
(**−0.109, 7 words**). That is a decoder-path question (ticket 35 / ADR 0011), not a
filtering one, and one length is still just one sample.

The recording **supports the orchestrator's leading hypothesis**: the errors are acoustic
confusions of the 16 MB model, not signal corruption. Eight variant runs produced **four**
distinct transcripts; two unrelated transforms (a 60 Hz high-pass and a 0.85 gain) produced
the *identical* transcript, which is a decoding-basin flip, not a graded signal-quality
effect. The homophone-style errors (`valley`→`rally`, `crews`→`cruise`, `flood`→`front`,
`river`→`reverse`) survive the filter that removed almost all the recording's low-frequency
power.

## 2. What was measured, and on what

The harness drives the **real** engine through the **shipped** code, reusing ticket 32's
`run_utterance` (`WhistleTranscriber.stream` → `streaming.drive` →
`_WhistleStreamingDecoder`, with the decoder seam recorded) and ticket 32's normalisation
(`word_diff`/`tokenise`: case-folded, punctuation dropped, `3.2` kept intact). It imports
`../commit-cadence-diagnostic/measure_commit_cadence.py` **by path** rather than copying it,
so there is only one normalisation in the project. It builds no filter and changes no
shipped code.

The primary input is the user's existing 25 s ground-truth recording,
`/tmp/ges2t-commit-cadence-capture-20261009-195914.wav`, against the passage recorded in
[`ticket.md`](ticket.md), which normalises to **64 words**. Eight variants were run:

| variant | what it is |
|---|---|
| `baseline` | the shipped streaming path on the raw WAV, no transform |
| `baseline_rerun` | the same, again, to measure the run-to-run noise floor |
| `highpass_60` / `highpass_85` / `highpass_120` | a second-order Butterworth high-pass at that corner applied to the audio **before** the engine (and therefore before the gate too) |
| `gain_0.85` / `gain_0.50` | the audio scaled so its peak is 0.85 / 0.50, so no sample can reach the clip point |
| `batch` | the one-shot `transcribe()` path on the **gate-passed** audio streaming saw, so both paths receive the same samples (ticket 35's confound (a) controlled) |

## 3. The input: LF noise present, and clipping present

The recording **does** contain the low-frequency nuisance, and it dominates the recording's
energy. Reported as the fraction of AC power below each corner (bounded in [0, 1], computed
as `1 − (output RMS / input RMS)²`):

| high-pass corner | output RMS | power removed |
|---|---|---|
| 60 Hz | 0.04051 | **86.0 %** |
| 85 Hz | 0.03441 | 89.9 % |
| 120 Hz | 0.03050 | 92.1 % |
| 150 Hz | 0.02894 | 92.9 % |

Level and clipping, read from the WAV's own int16 domain (ticket 32's convention):
`400 698` samples, peak 1.0000, DC offset −0.010496, AC RMS 0.1084;
**1262** samples at `|v| ≥ 32767` (0.315 %), all 1239 at −32767 and 23 at −32768 — the
figure ticket 32 reported. The float32 buffer the engine actually sees has only **23**
samples at `|float| ≥ 1.0`, because `−32767/32768 = 0.99997`; the rest of the int16 count
is a quantisation convention, not a sample pinned at the rail.

So a high-pass has plenty to act on, and clipping is present but tiny. **Neither fact
predicts whether either costs accuracy; the variant runs do.**

## 4. The numbers: every variant, ranked

Primary input, 25.04 s, 64 reference words, `window_seconds=5.0`,
`chunk_seconds=1.0`, `language=''`. WER is ticket 32's `word_diff`.

| rank | variant | WER | sub | ins | del | errors | Δ WER vs baseline | audio fed |
|---|---|---|---|---|---|---|---|---|
| 1 | `batch` | **0.281** | 16 | 0 | 2 | 18 | −0.109 | 25.00 s |
| 2= | `gain_0.85` | 0.359 | 20 | 2 | 1 | 23 | −0.031 | 25.00 s |
| 2= | `highpass_60` | 0.359 | 20 | 2 | 1 | 23 | −0.031 | 25.00 s |
| 4= | `highpass_85` | 0.375 | 21 | 2 | 1 | 24 | −0.016 | 25.00 s |
| 4= | `highpass_120` | 0.375 | 21 | 2 | 1 | 24 | −0.016 | 25.00 s |
| 6= | `baseline` | 0.391 | 22 | 2 | 1 | 25 | — | 25.00 s |
| 6= | `baseline_rerun` | 0.391 | 22 | 2 | 1 | 25 | +0.000 | 25.00 s |
| 6= | `gain_0.50` | 0.391 | 22 | 2 | 1 | 25 | +0.000 | 25.00 s |

**Run-to-run noise floor: zero.** `baseline_rerun` reproduces the baseline transcript
byte-for-byte, so a one-word difference between variants is attributable to the variant, not
to engine nondeterminism. This is what makes the small differences rankable at all.

**Transcript equivalence — the decisive shape of the result.** The eight runs produced only
**four distinct transcripts**:

```
baseline, baseline_rerun, gain_0.50      WER 0.391
highpass_60, gain_0.85                   WER 0.359
highpass_85, highpass_120                WER 0.375
batch                                    WER 0.281
```

A 60 Hz high-pass that removes 86 % of the AC power and a mere 0.85 gain that removes
nothing both land on the **same** alternate transcript; a 0.50 gain, which also removes the
clipping, falls back to the baseline transcript. That is a discrete basin flip, not a
gradient that tracks signal quality. It is exactly the signature of a small model choosing
between a handful of plausible readings of the same acoustic evidence.

**Error stability.** Of the 19 substitution pairs the baseline produces, the 60 Hz
high-pass retains 14 and introduces 3 new ones; 85/120 Hz retain 13 and introduce 5. The
load-bearing homophone-style errors survive every streaming variant: `valley`→`rally`,
`crews`→`cruise`, `flood`→`front`, `river`→`reverse`, `roads`→`roadroom`, `remain`→`in`,
`closed`→`close`, `knocked`→`no`, `out`→`doubt`. The one example from the ticket that does
**not** reproduce here is `ferry`→`fairy`: on this WAV the baseline already gets `ferry`
right (the `fairy` was in the live-run transcript, `001-comment.md` §3.1; the int16
round-trip is a different decode — see §7).

**Clean cross-check (`clip5.wav`, ticket 32's 5 s clip, known text).** The clip has peak
0.6534, **zero** clipping, and only **1.4 %** of its AC power below 60 Hz. All eight
variants — including both gains, all three corners, and batch — produce the **identical**
transcript at **WER 0.000**. There is no headroom for a filter to improve, and it does not
degrade a clean clip either.

## 5. The ranking, plainly

1. **Streaming vs batch — the only material lever.** −0.109 WER (7 words) on this input.
   This is a **path** choice, not an input-side filter, and it is exactly the question
   [`../streaming-vs-batch-quality/ticket.md`](../streaming-vs-batch-quality/ticket.md)
   (ticket 35) owns. Ticket 35 is still `to do` with no artifacts, so **I measured the
   accuracy here and am saying so**; I did not do its length sweep. Ticket 32's divergence
   figure (0.000 → 0.221 → 0.339 with length) is a *divergence* number, not accuracy, and
   this measurement does not replace ticket 35's three-length design.
2. **High-pass at 60 Hz — −0.031 WER (2 words), and not trustworthy as an improvement.**
   It removes 86 % of the AC power to do it, and the identical transcript is reachable by an
   unrelated gain change.
3. **Reduced gain to 0.85 — −0.031 WER (2 words), same basin flip.** Reduced gain to 0.50 —
   **0.000**, i.e. no change. Clipping (1262 int16 samples) is therefore **not** the cause
   of the observed errors.
4. **High-pass at 85 Hz and 120 Hz — −0.016 WER (1 word) each.** Effectively nothing.
5. **None of the input-side levers moves the dominant errors.** `valley`→`rally`,
   `crews`→`cruise`, `flood`→`front` are unchanged by removing almost all low-frequency
   power and by removing the clipping.

## 6. Should we build a filter? **No.**

The evidence says no, and the reason is structural, not marginal:

- The filter removes **86 %** of the recording's AC power and buys **2 of 64 words**, below
  the harness's explicit 4-word materiality bar. Removing *more* (85/120/150 Hz) buys
  **fewer** words (1 or 0). A lever whose effect does not grow as you pull it harder is not
  the cause of the error.
- The 2-word change is a **decoding-basin flip**: the same transcript is produced by an
  unrelated gain change, and it is lost again at a different gain. It is not a stable
  signal-quality improvement and has no reason to reproduce on another recording.
- The dominant errors are **acoustic confusions of the model** and survive every input-side
  transform, including the one that removed 86 % of the noise. `valley`→`rally` and
  `crews`→`cruise` also survive into the batch path.
- Clipping is **0.315 %** of int16 samples (23 in the engine's float buffer) and is not the
  cause: the non-clipping 0.50 gain produces the baseline transcript exactly.

**What would change the answer** (this is the follow-up bar, and it is a measurement bar,
not a hunch):

1. A variant that clears the **4-word** bar **and tracks the removed signal** — the effect
   must grow with the amount of LF/hum removed, across at least two recordings.
2. A recording where the noise lives **in the speech band** (≳300 Hz, e.g. fan or road
   noise with broadband energy). A high-pass cannot touch that by construction, so it would
   need a different mechanism and its own measurement.
3. The natural next input is ticket 31's **quiet-room vs noisy-room** pair. The filter's
   value, if any, is in noise this single recording does not contain. Until that exists,
   the honest answer is "no filter".

If those measurements justify anything, it is a **follow-up ticket citing this one** — this
ticket builds nothing, and the filter's home (capture chain vs pre-engine step vs a PipeWire
module) is the follow-up's decision.

**Where the effort actually is:** the material lever is the decoding path (streaming vs
batch, ticket 35), and behind that ADR 0005's 16 MB model. The observed error mode
(`valley`→`rally`, `crews`→`cruise`) is a scope conversation about model capacity or the
batch default, not a signal-processing one.

## 7. The honest bounds

- **One 25 s recording of read speech is a single sample.** The reader may have stumbled,
  re-read, or paraphrased, so every absolute WER here is an **upper bound** on the model's
  real error rate. The `four thousand` ↔ `4,000` rendering alone contributes ~2 substitutions
  to whichever variant produces digits, and `metres`→`meters` is a spelling choice, not a
  mishearing.
- **The load-bearing comparison is variant vs variant on identical audio**, not the absolute
  number. All six streaming variants and the batch score were computed from the same 400 698
  samples. That is why a 2-word difference is reportable even though the absolute WER is not
  a model accuracy claim.
- **The persisted WAV is not the live capture.** Ticket 32 (`003-comment.md`) recorded that
  the int16 round-trip does not reproduce the live float32 transcript; the WAV baseline here
  reads `... no doubt powered throughout the 4th century` where the live run read
  `... no doubt power to roughly 4,000`. The comparison is fair; the absolute transcript is
  not the one the user heard.
- **`clip5` is a second sample, not a second copy of this passage** — a different (clean,
  read) recording, useful only to show the filter is neutral when the baseline is already
  perfect.
- **`word_diff` and the display aligner disagree by convention.** Ticket 32's `word_diff`
  counts a `replace` block as `max(ref, hyp)` substitutions; the per-variant listing pairs
  what it can and lists the remainder as an insertion/deletion (baseline: 22 sub via
  `word_diff`, 19 listed). Every variant uses the same `word_diff`, so the ranking is
  unaffected; the listings are for the eye.

## 8. What could not be verified

- **The filter under real noise.** No noisy-room recording was available. The one input the
  ticket permits is quiet-ish and dominated by sub-60 Hz drift, not by speech-band noise.
  Ticket 31's recordings are what would settle this.
- **Replication.** Neither the 2-word flip nor its absence is reproduced on a second
  recording of this passage; the honest reading is that it is a basin flip, but a second
  sample would confirm it.
- **Whether the int16 round-trip itself costs accuracy.** Ticket 32 flagged this and could
  not prove it; I can show the filter and gain do not change the result materially, but I
  cannot separate the round-trip from the model's own errors.
- **Any effect of the engine's internal front-end.** The engine is closed-source (ADR 0006);
  it may already high-pass internally, which would explain the null result but cannot be
  inspected.
- **No microphone was recorded**, no keyboard event was emitted, and no clipboard was
  touched. The capture already existed.

## 9. Verification

```
$ timeout 300 uv run pytest -q
904 passed in 6.01s          # exit status 0, process exited
$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 53 source files
$ uv run mypy docs/project/live-dictation-ux/accuracy-levers/measure_accuracy_levers.py
Success: no issues found in 1 source file
$ python3 - <<'PY'   # the project's link check
...
0 broken references
```

The harness was run twice on the primary input and reproduced its transcripts exactly
(`baseline_rerun`), which is the determinism evidence quoted in §4.

## 10. Definition-of-done status

| ticket DoD item | status |
|---|---|
| start from existing audio; no new recording assumed | done — the 25 s WAV was present and used; clip5 was an extra known-text cross-check, not a substitute |
| offline harness transcribing the same audio under each variant, scoring with ticket 32's normalisation | done — `measure_accuracy_levers.py`, imports ticket 32's `word_diff`/`run_utterance` by path |
| baseline | done — WER 0.391 |
| high-pass, ≥2 corners in 60–120 Hz, LF present-or-not and level reported | done — 60/85/120 Hz; 86.0 %/89.9 %/92.1 % of AC power removed |
| gain/level, non-clipping | done — 0.85 and 0.50; both non-clipping (int16 clipping 1262 samples, 0.315 %) |
| streaming vs batch, citing ticket 35 if answered | ticket 35 is `to do` with no artifacts, so measured here and stated: streaming 0.391 vs batch 0.281 on identical audio |
| ranking, not just numbers | done — §5 |
| explicit filter recommendation | done — §6: **do not build it**, with the bar that would change the answer |
| bound the claim | done — §7–§8 |
| builds no filter, changes no shipped code | done — the harness is a docs script; `git diff` touches nothing under `src/` |
