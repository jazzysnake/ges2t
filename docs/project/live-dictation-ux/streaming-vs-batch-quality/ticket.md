# Streaming vs batch transcription quality (ground truth)

- **Priority:** P2
- **Status:** to do
- **Complexity:** M
- **Epic:** ../epic.md

## ⚠️ Nothing is gated by this ticket

This ticket **gates nothing**. No other ticket depends on it, no DoD may cite it, and no work should
wait on its outcome. It changes **no behaviour** — it is a measurement. If it finds a problem, that
problem gets its own ticket.

It exists because ticket 32's harness incidentally produced a signal that is too interesting to drop
and too weak to act on: see below.

## Current state / context

**Streaming is the shipped default** (ticket 16), chosen when `streaming.enabled` and
`Transcriber.supports_streaming` are both true. Ticket 32's diagnostic transcribes the same audio
both ways, and the two **diverge**:

| input | streaming vs batch |
|---|---|
| `clip5.wav` (5 s) | WER **0.000** |
| `clip30.wav` (30 s) | WER **0.221** |
| the user's 25 s recording | WER **0.339** |

Eyeballing the user's recording against the passage they read suggests **batch is closer**:

| the user said | streaming (the default) | batch |
|---|---|---|
| `last night's` | `Last night` ✗ | `last night's` ✓ |
| `midnight` | `Great night.` ✗ | `midnight` ✓ |
| `ferry` | `fairy` ✗ | `ferry` ✓ |
| `Two low-lying roads remain closed tonight` | `Do low light. road drawing close to night` ✗ | `Two low lying roads remain closed night` ✓ |

**Why this is not yet actionable — the three reasons the ticket exists:**

1. **The number is streaming *versus* batch, not accuracy.** That is a *divergence* measurement. If
   both paths were equally wrong, it would read the same. Only a **ground-truth reference** says
   which is better.
2. **It grew with length** (0.000 → 0.221 → 0.339), so a fair measurement must **vary utterance
   length** rather than sample once.
3. **The original figure was confounded.** Before ticket 32's R2 fix, the batch reference was
   transcribed from the **whole file** while streaming saw only the **gate-passed** audio — so
   `clip30`'s number conflated *silence removal* with *windowing*. The confound is fixed in the
   harness; it must not reappear in this measurement.

**And a fourth, unmeasured possibility that would change the conclusion entirely:** the capture
itself may be degrading everything. The saved capture is int16 with ~1262 clipped samples. If the
input clips, that affects both paths and is a cheaper lever than any decoder change.

## Definition of done

- [ ] Transcribe the **same audio** through both paths **against a ground-truth reference**, and
      report **accuracy** (not divergence) for each: substitutions, insertions, deletions and a word
      error rate, per input.
- [ ] **Vary utterance length** — at least three lengths, including one beyond the 30 s per-call
      ceiling that forces a window flush. The effect is length-dependent; a single sample cannot
      support a conclusion.
- [ ] **Control the confounds explicitly, and say so in the report:**
      (a) both paths must receive the **same audio** (use the gate-passed audio for both, or state
      plainly that they did not);
      (b) record the **window and chunk sizes** used;
      (c) record whether the input **clips**, and if so, whether the conclusion survives a
      non-clipping level.
- [ ] **Report the raw transcripts** for each input and path, verbatim, so the numbers can be checked
      by eye against the reference.
- [ ] **State the answer plainly:** is the shipped default materially worse than batch, by how much,
      and under what conditions (length? silence? noise?). If the evidence does not support a
      conclusion, say exactly what evidence is missing rather than hedging.
- [ ] **Do not change any behaviour.** No code in `src/ges2t/` may be modified for this ticket.

## Notes

- **Priority.** Deliberately P2 and deliberately unscheduled. It answers a question the project would
  like answered; it is not on the path to the user's v1 bar.
- **Reuse, don't extend.** Ticket 32's harness already transcribes both ways
  (`../commit-cadence-diagnostic/`). Read it and its `004-review.md` first; reuse the parts that fit
  rather than editing that ticket's diagnostic in place, which belongs to a closed ticket.
- **Clips may suffice** — unlike ticket 32, this measurement does not need the user's voice, only
  known-text speech at several lengths. If fresh speech is needed, it is **user-owned**: ask first,
  say how long, never record unaided, never commit audio.
- **Do not present a streaming-vs-batch number as an accuracy result.** That is precisely the mistake
  this ticket exists to correct.
- If the answer turns out to be "streaming is materially worse", the follow-up is a decision about
  the **default** — which would touch ADR 0011's territory and the ticket-16 selection rule, and must
  be raised as its own question rather than folded in here.

## Related tickets

- depends on: — (**nothing**)
- blocks: — (**nothing**)
- informed by: ../commit-cadence-diagnostic/ticket.md and its `001-comment.md` / `004-review.md`
- related: ../streaming-window-engine/ticket.md, ../whistle-streaming-adapter/ticket.md,
  ../streaming-wiring-and-e2e/ticket.md (the selection rule)
- related ADRs: ../../../adr/0011-streaming-sliding-window.md,
  ../../../adr/0005-stt-backend-cactus-whistle.md,
  ../../../adr/0014-testing-and-quality-bar.md
