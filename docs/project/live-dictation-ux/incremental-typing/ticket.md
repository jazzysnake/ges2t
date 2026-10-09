# Incremental typing of committed text

- **Priority:** P1
- **Status:** to do
- **Complexity:** M
- **Epic:** ../epic.md

## Current state / context

Today **nothing is injected until release** (ADR 0011). The user asked for text to appear **as they
speak**, and they identified the safe prefix themselves: the words the engine **commits** — those two
consecutive passes agree on — are appended to the transcript and never revised, so typing them early
**cannot require a backspace**.

They also found a genuine gap in ADR 0011's reasoning. The ADR rejects typing during dictation with
*"typing partials into the focused window would duplicate and race with the committed-text revisions"*.
That is sound for `committed + pending` — which is what the pipeline currently surfaces as live text —
but it does **not** apply to `committed` alone, precisely because `committed` is the part that has
stopped changing. The ADR's rule is broader than its own stated reason requires.

This ticket changes that decision and implements the feature.

## Decisions already taken by the user — do not re-open these

1. **Focus must hold for the whole utterance.** Accepted deliberately. Committed text lands wherever
   focus is at the moment of each commit; the user does not consider mid-utterance window switching a
   real usage pattern.
2. **A word typed wrong early is recoverable** by stopping the recording, deleting it, and continuing.
   That makes the **stop path the safety valve** — so it must stay instant, and the tool must
   deliberately **not** attempt auto-correction.
3. **Interleaving with the user's own typing is out of scope.** It is a documented limitation, not a
   bug to be fixed here.
4. **The clipboard backend is incompatible with streaming** — incremental paste would rewrite the
   user's clipboard on every commit.

## Definition of done

- [ ] During a streaming dictation, committed text is injected **as it is committed**, using only the
      committed value exposed by ../committed-delta-interface/ticket.md — **never** the pending tail,
      and never `committed + pending`.
- [ ] A **typed-watermark** guarantees each committed character is typed **exactly once**. The final
      segment is **cumulative** while the per-pass values are **deltas**; a naive "type each delta,
      then type the final segment" duplicates the entire utterance. The watermark is what prevents it,
      and there must be a test that fails if the final segment is typed in full.
- [ ] Injection never blocks the recording loop. The service already has a lock discipline and a
      bounded command loop (ADR 0015); a slow or wedged injector must not stall capture or shutdown.
- [ ] The **batch** path is unchanged, and `streaming.enabled = false` behaves exactly as it does
      today.
- [ ] **Config validation:** `injection.backend = "clipboard"` together with streaming enabled is a
      **loud, actionable error** naming both settings and the fix — not a silent preference, and not a
      silent downgrade of either one. (Decide whether it stays an error or becomes a warning, justify
      it in the comment artifact, and make the message name the two settings involved.)
- [ ] ADR 0012's text rules are unchanged: still exactly one trailing space when enabled, still
      verbatim otherwise, and whitespace-only input still injects **nothing**.
- [ ] The **user's live test** — user-owned, script prepared by the implementer, and asking first
      because it types into their focused window:
      dictate a sentence and confirm text appears **while speaking**; then deliberately dictate a
      phrase the model gets wrong and confirm the **stop → delete → continue** recovery works.
      Report the observed **lag** between speaking and text appearing.
- [ ] `uv run pytest` and `uv run mypy src/ges2t tests` green.

## ⚠️ ADR 0011 must be amended, and surfaced explicitly

ADR 0011 states *"Partial text is display-only … nothing is injected until release."* **This ticket
changes that decision**, so the ADR must be amended:

- update its status and record the superseding decision;
- **correct the original reasoning in place** — the "duplicate and race with the committed-text
  revisions" objection applies to `committed + pending`, not to `committed` alone;
- cite the **measured evidence** from ../commit-cadence-diagnostic/ticket.md and the observed live lag.

**Do not edit the ADR quietly.** The user escalates on ADR amendments and expects the evidence attached.
Surface it in the hand-back report.

## Notes

- ADR 0011's *decision* to stream, and ADR 0003's *no automatic fallback* rule, both still hold. This
  changes **when** text is injected, not **how**.
- The measured commit cadence from ticket 32 governs the design: if commits land every few hundred
  milliseconds, injection must be cheap and non-blocking; if they land every few seconds, batching the
  per-commit deltas is acceptable.
- **If ticket 32 found the engine restates committed text, this ticket is cancelled.** Say so rather
  than engineering around it — the whole safety argument is the prefix property.
- Consider, but do not necessarily build, a guard that refuses to type if focus moved since the last
  commit. It is a plausible mitigation for decision 1; if it is not built, record why.

## Related tickets

- depends on: ../commit-cadence-diagnostic/ticket.md, ../committed-delta-interface/ticket.md, and the
  injection work (core-dictation-pipeline/ydotool-injector, xkb-keycode-mapping,
  batched-key-emission, injection-delay-default — all merged)
- related ADRs: ../../../adr/0011-streaming-sliding-window.md (**amended by this ticket**),
  ../../../adr/0003-text-injection-via-ydotool.md,
  ../../../adr/0020-layout-aware-keycode-mapping.md,
  ../../../adr/0012-verbatim-output-and-trailing-space.md,
  ../../../adr/0015-single-service-process-architecture.md
