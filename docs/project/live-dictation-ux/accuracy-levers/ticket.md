# Accuracy levers: measure before building a filter

- **Priority:** P1 (the user's stated reason for hesitating to daily-drive it)
- **Status:** done
- **Complexity:** M
- **Epic:** ../../live-dictation-ux/epic.md

## Current state / context

The user reports that dictation and streaming now work well, but that accuracy is **not as good as they
want**, and asks whether a **noise filter** is possible.

**The honest position is that we do not know which of several causes dominates, and building a filter on
a hunch is exactly the trap this project has already fallen into twice** — every silence-gate
threshold was derived from synthetic inputs and reviewed rigorously against them, while the real device's
audio never went through the gate until the user toggled it. So: **measure the levers first, then build
only what the numbers justify.**

### The candidate causes, and the evidence for each

| # | Candidate | Evidence |
|---|---|---|
| 1 | **Low-frequency noise** — a broadband ~20–25 Hz drift, ≈0.04 amplitude idle, and 0.036–0.26 AC RMS in a quiet room rising to 0.28–0.35 RMS in a car | **Measured.** Its physical origin is still **undetermined** (acoustic rumble vs analogue-front-end artefact). Note the engine sees the **raw** audio — the gate's high-pass is deliberately analysis-only (ticket 29/31) |
| 2 | **Input clipping** — the saved 25 s capture is int16 with **~1262 clipped samples**, and re-reading it shifts the transcript | **Measured that it clips**; not measured whether it costs accuracy. Cheap to test by lowering gain |
| 3 | **Streaming vs batch** — the shipped default diverges from batch by WER 0.22 (30 s clip) to 0.34 (25 s recording), and eyeballing suggested batch is closer | **Divergence measured** (ticket 32's harness); accuracy is **not** — see `../streaming-vs-batch-quality/ticket.md` |
| 4 | **Model capacity** (the 16 MB Whistle model, ADR 0005's accepted risk) | The observed errors are **plausible-but-wrong words**: `valley`→`rally`, `crews`→`cruise`, `ferry`→`fairy`. These are homophone-style confusions, **not** noise corruption. **This is the orchestrator's leading hypothesis**, and a filter cannot fix it |

**That last row is the reason to measure first.** If the dominant error mode is acoustic confusion rather
than signal corruption, a high-pass filter will not move the number, and the honest answer becomes "this
is what a 16 MB model does" — which is a scope question (ADR 0005), not a filtering one.

## The reference text (ground truth) — otherwise lost

The user read this passage aloud for ticket 32's recording. It was **never written into a repo
artifact** — only quoted in fragments — so it is recorded here, because a WER measurement without a
reference is not a measurement.

> Good morning. Last night's storm knocked out power to roughly four thousand homes across the northern
> valley, and crews expect to restore most of them by Friday afternoon.
>
> *[a deliberate pause of roughly one to two seconds — **not** spoken text]*
>
> Officials say the river crested at 3.2 metres just after midnight, well below the flood barrier. Is
> that reading correct? Two low-lying roads remain closed tonight, and the ferry service resumes at half
> past six.

**Treat it as indicative, not as absolute truth.** Two honest limitations:

- The reader may have deviated — a stumble, a re-read, or a small paraphrase all count as model errors
  against this text, so a WER computed from it is an **upper bound** on the model's real error rate.
- The original instruction was to keep going rather than correct a stumble, so minor deviations are
  *expected* rather than exceptional.

**Consequence for the method:** the load-bearing comparison is **variant versus variant on identical
audio** (baseline vs high-pass vs reduced gain), with the reference used to judge which is *closer* — not
a single absolute WER quoted as the model's accuracy. Say so in the report.

## Definition of done

- [ ] **Start from audio we already have.** The user's 25 s recording of a **known passage** exists at
      `/tmp/ges2t-commit-cadence-capture-20261009-195914.wav` (from ticket 32's run) and the exact text
      they read is recorded in the ticket-32 artifacts. That is a ground-truth pair: **no new recording is
      needed to test levers 1 and 2**, and none may be assumed — if the file is gone, say so rather than
      substituting a clip.
- [ ] **Build a small offline comparison harness** (a `docs/.../measure_*.py` script, **not** part of the
      shipped package, and `uv run pytest` must stay headless and engine-free) that transcribes **the same
      audio through the real engine** under each variant and scores each transcript against the known
      reference (word error rate, case- and punctuation-insensitive, plus substitutions/insertions/
      deletions — reuse ticket 32's normalisation rather than inventing a second one).
- [ ] **Measure, at minimum:**
      1. **baseline** (exactly what ships today);
      2. **high-pass before the engine** — try at least two corners in the 60–120 Hz region, since speech
         fundamentals start around 85 Hz. Report whether the LF noise is even present in this recording,
         and at what level;
      3. **gain/level** — the same audio at a reduced level that does not clip, to separate clipping from
         everything else;
      4. **streaming vs batch** on the same audio (this is `../streaming-vs-batch-quality/ticket.md`'s
         question — if it has been done, cite it; do not duplicate it).
- [ ] **Report the ranking, not just the numbers**: which lever moves accuracy most, by how much, and
      which moves it not at all. **A null result is a result** — if the high-pass changes nothing, that is
      the finding, and it must be stated as plainly as a positive one.
- [ ] **Say explicitly whether the evidence justifies building a filter.** If it does, say which filter,
      where it belongs (capture chain vs a pre-engine step vs a PipeWire module), and what it costs. If it
      does not, say that too — **"do not build it" is an acceptable and useful conclusion**.
- [ ] **Bound the claim.** One 25 s recording of read speech is a single sample. State the confidence
      honestly and say what a second recording would buy.

## Notes

- **This ticket builds no filter.** Its deliverable is a *measurement and a recommendation*. The filter, if
  any, is a follow-up ticket that cites this one.
- **Permission:** if fresh audio turns out to be needed, it is the **user's** to record — ask first, state
  the duration, never record unaided, and never commit audio.
- **Manage expectations in the report.** The 16 MB model is an accepted risk (ADR 0005). If the lever is
  model capacity, the answer is a scope conversation with the user, not a filter.
- The user is also being asked for a **quiet-room vs noisy-room** comparison for ticket 31. If that
  happens, its recordings are **ground-truth-capable too** and would strengthen this ticket enormously —
  cross-reference rather than duplicate.

## Related tickets

- depends on: — (it can start on the existing recording)
- informed by: ../../live-dictation-ux/commit-cadence-diagnostic/001-comment.md (the recording and its
  reference text), ../streaming-vs-batch-quality/ticket.md, ../../core-dictation-pipeline/silence-gate/ticket.md,
  ../../core-dictation-pipeline/speech-gate-recalibration/ticket.md (the LF measurements)
- related ADRs: ../../../adr/0005-stt-backend-cactus-whistle.md (`this is a 16 MB model and that is
  accepted`), ../../../adr/0008-audio-capture-via-pw-record.md,
  ../../../adr/0012-verbatim-output-and-trailing-space.md
