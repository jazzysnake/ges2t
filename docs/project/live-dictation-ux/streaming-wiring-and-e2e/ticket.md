# Streaming wiring and end-to-end verification

- **Priority:** P1
- **Status:** to do
- **Complexity:** S
- **Epic:** ../epic.md

## Current state / context

`../streaming-session/ticket.md` provides `StreamingDictationSession` but nothing selects it.
`../../core-dictation-pipeline/dictation-pipeline-batch/ticket.md` deliberately left session
selection out of `run_service` so that this ticket owns the decision.

The rule comes from ADR 0011 and the user's requirement: **streaming is the default when the
engine supports it**, and `streaming.enabled = false` (generic config, ADR 0009) must fall
back to the batch path with behaviour unchanged from the core epic. The capability flag
`Transcriber.supports_streaming` is the mechanism — never an exception, never a probe
(ADR 0010).

Relevant decisions: ADR 0011 (default-on, capability flag, window/chunk from
`[engine.whistle]`), ADR 0009 (which config key is generic vs engine-specific), ADR 0014
(this is a required automated test).

## Definition of done

- [ ] `src/ges2t/pipeline.py`'s `run_service` gains a single, clearly named selection step:
      use `StreamingDictationSession` when `config.streaming.enabled` **and**
      `transcriber.supports_streaming` are both true, otherwise `BatchDictationSession`.
      The window/chunk values come from `WhistleConfig` (`config.engine_blocks["whistle"]`
      through the engine-config parser) — **not** from the generic config.
- [ ] The session's partial text is forwarded to `status_ui.partial(...)` so the tray can show
      live text (ADR 0011). This is display-only and must not affect the injected text.
- [ ] `run_service` logs (at a debug level) which session implementation it chose and why, so a
      user can tell whether streaming is active — `ges2t doctor` need not report it, but the
      information must be available without a debugger.
- [ ] `tests/test_pipeline_streaming.py` asserts, with fakes and no threads left running:
      - `streaming.enabled = true` + a streaming-capable fake → the streaming session is used;
      - `streaming.enabled = true` + `supports_streaming = False` → the **batch** session is
        used (capability flag, not an exception);
      - `streaming.enabled = false` + a streaming-capable fake → the **batch** session is
        used, and the produced text and injected text are identical to the core epic's
        behaviour;
      - a non-default `chunk_seconds` from `[engine.whistle]` reaches the **session**, and a
        non-default `window_seconds` reaches the **transcriber adapter** (assert both
        constructor arguments) — the window is the adapter's property and the feed granularity
        is the session's, so they are asserted separately (see `../streaming-session/ticket.md`);
- [ ] `uv run pytest tests/test_pipeline_streaming.py` passes and `uv run pytest` stays green.
- [ ] **Manual, end-to-end, on the reference machine, reported in `001-comment.md`:**
      1. with defaults, dictate a **~40-second** utterance (well past the engine's 30 s
         per-call ceiling) and confirm the complete text is typed — this is the case the batch
         path cannot do at all (ADR 0005, ADR 0011);
      2. confirm the tray/status shows advancing partial text during the utterance (or, before
         `../tray-status-ui/ticket.md` lands, confirm via a debug log that partials are
         produced);
      3. set `streaming.enabled = false`, repeat a ~10-second dictation, and confirm it still
         works unchanged;
      4. report the measured per-chunk latency median and the total wall time for the long
         utterance as numbers.

- [ ] `uv run mypy src/ges2t` exits 0: every definition fully annotated and every
      signature interface-typed — ADR 0019.

## Notes

- Keep the selection logic to a couple of lines in one place. If it grows a third branch,
  that is a sign a capability needs to move onto the `Transcriber` protocol — which would be
  an ADR-level change (ADR 0010).
- Do not touch `service.py`; the service is already session-agnostic (ADR 0015).
- Do not add a config key for session selection beyond `streaming.enabled`. "Which session" is
  derived, not configured, except by the one global switch the user asked for.

## Related tickets

- depends on: ../streaming-session/ticket.md,
  ../../core-dictation-pipeline/dictation-pipeline-batch/ticket.md,
  ../../core-dictation-pipeline/cli-surface-and-commands/ticket.md
- blocks: ../user-documentation-and-setup-guide/ticket.md
- related ADRs: ../../../adr/0011-streaming-sliding-window.md,
  ../../../adr/0010-transcriber-interface-design.md,
  ../../../adr/0009-configuration-toml.md
