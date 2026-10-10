# 002 — Review of accuracy-levers (ticket 39)

- **Ticket:** [accuracy-levers](ticket.md) (ticket 39)
- **Reviewer:** independent review pass (read-only; the harness and `001-comment.md` were not modified)
- **Branch under review:** `feat/accuracy-levers`, rebased onto `main` at `783ce3b`
- **Artifacts reviewed:** `ticket.md`, `001-comment.md`, `measure_accuracy_levers.py`, `docs/project/README.md`
- **Date:** 2026-10-10
- **Verdict: REQUEST CHANGES** — the measurement reproduces exactly and the *conclusion*
  ("do not build a filter") is sound and safe to relay; but two supporting claims in
  `001-comment.md` do not hold as written and one piece of contrary evidence is missing, so the
  report should not go to the user until they are corrected.

## 0. What was run

```
# primary input, fresh report path
uv run python docs/project/live-dictation-ux/accuracy-levers/measure_accuracy_levers.py \
    --wav /tmp/ges2t-commit-cadence-capture-20261009-195914.wav \
    --out /tmp/ges2t-review-25s-report.txt                       # exit 0

# clean cross-check
uv run python .../measure_accuracy_levers.py \
    --wav /tmp/whinspect/clip5.wav --reference-file /tmp/ges2t-clip5-reference.txt \
    --out /tmp/ges2t-review-clip5-report.txt                      # exit 0

timeout 300 uv run pytest -q                                      # 904 passed, exit 0
rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests     # Success, 53 files
uv run mypy .../measure_accuracy_levers.py                        # Success, 1 file
python3 <orchestrator-state.md link check>                        # 0 broken references
```

Both fresh runs were compared line-by-line against the author's stored reports
(`/tmp/ges2t-accuracy-levers-25s-report.txt`, `/tmp/ges2t-accuracy-levers-clip5-report.txt`):
the headline lines are **byte-identical**.

## 1. Reproduction table

Every number `001-comment.md` puts in front of the reader was re-measured on the real engine.

| # | Claim in `001-comment.md` | Claimed | Measured | Status |
|---|---|---|---|---|
| 1 | reference length | 64 words | 64 words | reproduces |
| 2 | `baseline` WER (sub/ins/del) | 0.391 (22/2/1) | 0.391 (22/2/1) | reproduces |
| 3 | `baseline_rerun` WER | 0.391, byte-identical transcript | 0.391, byte-identical | reproduces |
| 4 | `gain_0.50` WER | 0.391 | 0.391 | reproduces |
| 5 | `gain_0.85` WER | 0.359 | 0.359 | reproduces |
| 6 | `highpass_60` WER | 0.359 | 0.359 | reproduces |
| 7 | `highpass_85` WER | 0.375 | 0.375 | reproduces |
| 8 | `highpass_120` WER | 0.375 | 0.375 | reproduces |
| 9 | `batch` WER (sub/ins/del) | 0.281 (16/0/2) | 0.281 (16/0/2) | reproduces |
| 10 | LF power removed, 60 Hz | 86.0 % | 86.0 % (out RMS 0.04051) | reproduces |
| 11 | LF power removed, 85 Hz | 89.9 % | 89.9 % (0.03441) | reproduces |
| 12 | LF power removed, 120 Hz | 92.1 % | 92.1 % (0.03050) | reproduces |
| 13 | LF power removed, 150 Hz | 92.9 % | 92.9 % (0.02894) | reproduces (analysis only, see F1) |
| 14 | int16 clipped samples | 1262 (0.315 %) | 1262 (0.315 %) | reproduces |
| 15 | int16 split | 1239 at −32767, 23 at −32768, 0 at +32767 | 23 at −32768, 0 at +32767, so 1239 at −32767 | reproduces |
| 16 | float samples ≥ 1.0 | 23 | 23 | reproduces |
| 17 | 8 runs / distinct transcripts | 4 | 4 | reproduces |
| 18 | `{highpass_60, gain_0.85}` identical | yes | yes | reproduces |
| 19 | `{baseline, baseline_rerun, gain_0.50}` identical | yes | yes | reproduces |
| 20 | `{highpass_85, highpass_120}` identical | yes | yes | reproduces |
| 21 | `clip5`: 8/8 identical, WER 0.000 | yes | yes | reproduces |
| 22 | `clip5`: peak, LF < 60 Hz | 0.6534, 1.4 % | 0.6534, 1.4 % | reproduces |
| 23 | `pytest` | 904, exit 0 | 904 passed, exit 0 | reproduces |
| 24 | `mypy` src+tests / harness | 0 errors / 0 errors | 0 / 0 | reproduces |
| 25 | link check | 0 broken references | 0 | reproduces |

No headline number failed to reproduce. Absolute WER values are inflated by the scorer
convention, but that is a property of the reused scorer applied uniformly (see §4/F2).

## 2. Harness fidelity — it measures the shipped path (PASS)

- **Shipped path, not a re-implementation.** `measure_accuracy_levers.py` imports ticket 32's
  harness by path (`load_ticket32()`, `importlib.util.spec_from_file_location`) and calls its
  `run_utterance`, `read_wav`, `chunk_buffer`, `load_engine`, `word_diff`, `tokenise`. Ticket 32's
  `run_utterance` drives `transcriber.stream(...)` → shipped `WhistleTranscriber.stream` →
  `streaming.drive` → `_WhistleStreamingDecoder`, with the decoder seam patched by `DecoderRecorder`.
  Confirmed by reading `src/ges2t/transcribers/whistle.py` and `src/ges2t/streaming.py`.
- **One normalisation.** `_normalise_token` / `tokenise` / `word_diff` exist in exactly one place
  (`commit-cadence-diagnostic/measure_commit_cadence.py`); the accuracy harness declares only a
  `Protocol` for them and calls ticket 32's implementation for every variant, including `batch`.
  The harness's `align_errors` is a *display* aligner that consumes `tokenise` output; it is a
  second alignment, not a second normalisation, and its disagreement with `word_diff` is documented
  (§7). No silently-diverging copy.
- **No side effects.** The harness does not import `PwRecordAudioSource`, write any audio, touch the
  clipboard, or emit keys. It writes only the text report under `/tmp`. It changes no shipped code.

## 3. The causal claim — "decoding-basin flip, not a graded signal effect"

**What the evidence supports.** For the decision the ticket has to make — *is a filter justified?* —
the reasoning is sound:

- The effect is **2 of 64 words** on the best corner, below the study's own explicit 4-word bar.
- It is **non-monotone in filter strength**: 60 Hz → 2 words, 85 Hz → 1, 120 Hz → 1. A mechanism in
  which LF noise corrupts the decode predicts the benefit to grow (or at least persist) as more LF
  is removed; here it shrinks.
- An **unrelated transform** (a flat 0.85 gain, which removes no LF) lands on the *same* transcript,
  and a different gain (0.50) lands back on baseline. That is the signature of a model with a handful
  of attractor outputs, not a graded function of the removed signal.
- The **dominant homophone errors survive** the filter that removed 86 % of the power.

On that basis "the 2-word movement is not a stable, reproducible improvement" is a fair and
decision-relevant conclusion.

**Where it overreaches.** "It is a decoding-basin flip, not a graded signal-quality effect" is stated
as the *mechanism*. The harness cannot distinguish a basin flip from "a small real effect competing
with filter-induced artefacts at the higher corners"; it establishes only that the effect is **not a
reliable, monotone lever of the removed signal**. That weaker statement is what the data prove, and it
is sufficient. `001-comment.md` partially self-limits this by listing replication as unverified (§8),
so it is a wording problem, not a broken conclusion.

**What would falsify the basin reading** (and therefore re-open the filter question):

1. A second recording where the same 60 Hz high-pass yields **≥4 net words**.
2. A **monotone corner sweep** (e.g. 40 / 60 / 85 / 120 / 150 Hz) whose WER falls monotonically —
   a graded LF mechanism.
3. A **gain sweep** at fixed 60 Hz filtering (1.0 / 0.9 / 0.85 / 0.8 / 0.7 / 0.6 / 0.5) producing a
   smooth WER-vs-level curve — a level/clipping mechanism.
4. A **no-op-like transform** (e.g. a 0.999 gain, or a corner low enough to remove almost nothing)
   reproducing *either* alternate transcript — that would further corroborate the basin reading
   rather than falsify it.

The artifact's §6 bar already names (1) and (2) in substance; stating (3) would make the falsifier
set complete.

## 4. Scorer falsification — it discriminates, with a documented quirk

Probed ticket 32's actual `word_diff`/`tokenise` (probe kept under `/tmp` only):

| probe | result |
|---|---|
| perfect transcript | `sub=0 ins=0 del=0`, **WER 0.000** |
| one word corrupted (`valley`→`rally`) | `sub=1`, WER 0.016 |
| two words corrupted | `sub=2`, WER 0.031 |
| `"a b c"` vs `"x"` (replace-block quirk) | `word_diff` `sub=3 del=2` → **errors 5**; standard edit distance **3** |

So the scorer is not degenerate: it scores a perfect transcript 0.000 and moves sensibly with
deliberate corruption.

**The quirk is real and slightly understated in the artifact.** `word_diff` adds
`max(ref,hyp)` substitutions on a `replace` block **and then also** adds
`max(0, m−n)` deletions / `max(0, n−m)` insertions for the same block, so its total is
`max(m,n) + |m−n|` — it double-counts the length differential. §7 describes only that the listing
"pairs what it can and lists the remainder"; that is what `align_errors` does, and it is the one that
matches the standard word edit distance. On the real transcripts:

| variant | `word_diff` errors → WER | standard word edit distance → WER |
|---|---|---|
| `baseline` | 25 → 0.391 | 22 → 0.344 |
| `highpass_60` | 23 → 0.359 | 20 → 0.312 |
| `batch` | 18 → 0.281 | 16 → 0.250 |

**Does it affect the ranking?** No. Every variant (including `batch`) is scored by the same
`word_diff`, and the ordering is identical under both conventions (`batch` < `highpass_60` <
`highpass_85`/`120` < `baseline`). The filter delta is 2 words under both. The one material
difference is the streaming-vs-batch headline: **`word_diff` says 7 words / −0.109; the standard
edit distance says 6 words / −0.094.** That is F2 below. The conclusion still clears the 4-word bar
either way, but the quoted figure is inflated by one word.

## 5. Null result and honesty audit

**The null is not softened (PASS).** §1 opens "**Do not build a noise filter.**"; §6 is headed
"Should we build a filter? **No.**" and gives the structural reason first; the positive
(streaming-vs-batch) finding is stated as a separate, lesser point; §5 item 5 says the input-side
levers "do not move the dominant errors". Nothing hedges the null, and the null is placed above the
one positive finding.

**Bounds present (PASS):**

- int16 round-trip does not reproduce the live transcript; absolute WER is an upper bound while
  variant-vs-variant is fair — §1, §7 bullet 3, §8.
- `ferry`→`fairy` does not reproduce on the WAV (baseline gets `ferry` right) — §4.
- reader may have deviated from the reference — §7 bullet 1.
- one 25 s input; ticket 35 owns the length sweep — §5.1, §7, §8.
- `clip5` is a second *sample*, not a second copy of the passage — §7.
- the engine may already high-pass internally — §8.
- no microphone/keyboard/clipboard — §8.

## 6. Batch fairness (PASS)

Verified from the code, not the prose: the `batch` score is `baseline_measurement.batch_reference`,
computed inside ticket 32's `run_utterance(..., with_batch_reference=True)` from
`whole = concatenation of speech_buffers`, where `speech_buffers` is exactly the gate-passed subset
that `stream()` fed the engine (`gated_timeline` re-runs the shipped `_has_speech` on the same
buffers). Both paths therefore receive the same samples; ticket 35's confound (a) is genuinely
controlled. The artifact says so in §2 and is explicit that this is **one length** and does not
replace ticket 35's three-length design (§5.1, §7, §8).

## 7. The clean cross-check (PASS)

`clip5.wav` reproduces exactly: 5.00 s / 80 000 samples, peak 0.6534, 0 clipped, 1.4 % of AC power
below 60 Hz, 14-word reference, and all 8 variants produce the one identical transcript at WER
0.000. It is the correct evidence that the variants only bite on the noisy/long input.

## 8. Shipped code and verification (PASS)

- `git diff --name-only main...HEAD -- src/` → empty; `-- tests/` → empty.
- `uv run pytest -q` → 904 passed, exit 0.
- `rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests` → 0 errors (53 files).
- Harness mypy → 0 errors (1 file).
- Link check → 0 broken references.

## Findings requiring changes

**F1 — `001-comment.md` §6 attributes a result to a 150 Hz high-pass that was never run.**
It says: "Removing *more* (85/120/150 Hz) buys **fewer** words (1 or 0)." The harness runs only
`HIGHPASS_CORNERS_HZ = (60, 85, 120)`; 150 Hz appears only in the LF power analysis, never as a
transcribed variant. Measured: 60 Hz → 2 words, 85 Hz → 1, 120 Hz → 1. Change to "(85/120 Hz buys
1 word each; 150 Hz was analysed for power but not transcribed)". The underlying point (the effect
does not grow with removal) survives on 60/85/120 alone.

**F2 — the streaming-vs-batch delta is quoted with the inflated scorer total.**
§1 and §5 print "−0.109, 7 words". Those are `word_diff` totals; the standard word edit distance is
6 words (−0.094). The `batch` absolute WER is 0.281 by `word_diff` / 0.250 standard; the other
variants are inflated by 2–3 errors each. Fix by either (a) quoting both conventions
("7 words by the harness's `word_diff`, 6 by a standard word edit distance"), or (b) recording that
the harness WERs are `word_diff` figures and not textbook WERs. The filter conclusion is unaffected;
only the batch magnitude is.

**F3 — the "streaming path is the real lever" framing omits contrary prior evidence.**
ADR 0011's own measurement was **WER 0.176 for the 5 s sliding window vs 0.191 for the 30 s one-shot
call** — the windowed path *at least as good* on a clean clip — which is the opposite direction to
this artifact's one 25 s sample (`streaming` 0.391 vs `batch` 0.281). `001-comment.md` never mentions
it. Add it as context and phrase the finding as a single-input observation, not a general
"streaming is worse". This matters because the orchestrator is about to relay the second half of the
headline to the user.

## What this review could not verify

- **Generality of either headline.** No second recording of the passage, and no noisy-room recording
  (ticket 31). The basin flip and the streaming-vs-batch direction are single-sample results.
- **The 150 Hz variant.** It cannot be verified because it was never run (F1).
- **The engine's internal front-end.** Closed source (ADR 0006); an internal high-pass would explain
  the null and cannot be inspected.
- **Whether the int16 round-trip itself costs accuracy.** Ticket 32 flagged it; this measurement
  cannot separate it from the model's own errors.
- **The reader's actual utterance.** Only the user can confirm the reference matches what was read;
  the ticket's own warning applies.
- **ADR 0011's 0.176/0.191 provenance.** Read from the ADR; the exact clip and conditions were not
  re-run (out of scope, and ticket 35 owns the length-swept version).
- **A standard-WER rerun of the full ranking.** My standard-WER figures are for the three
  reconstructed transcripts (`baseline`, `highpass_60`, `batch`); all eight were not re-scored under
  a second convention, because the harness itself scores only via `word_diff`.

## Bottom line for the orchestrator

- **"A filter would not help" is safe to relay**, with the ticket's stated bounds: this is one
  quiet-ish recording, a high-pass is not justified, more filtering buys less, and the dominant
  errors are model homophones that survive the filter. §6 and §8 say exactly this.
- **"The streaming path is the real lever" is only partly safe as stated.** The largest *measured*
  lever on this one recording is the decoder path (6–7 words), and it is the only material
  non-filter lever here. But it is one length, ticket 35 is explicitly not done, ADR 0011 measured
  the opposite direction on a clean clip, and the residual dominant errors are the 16 MB model, which
  neither path fixes. Relay it as "the largest lever we measured on this recording, pending ticket 35"
  — **not** as "switch to batch and accuracy is solved".
- Fix F1–F3 before the report is shown to the user; none of them changes the no-filter conclusion.
