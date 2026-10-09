# Dictation pipeline (batch): composition root and end-to-end wiring

- **Priority:** P0
- **Status:** to do
- **Complexity:** M
- **Epic:** ../epic.md

## Current state / context

All the parts now exist in isolation: config, model store, the Whistle adapter, `pw-record`
capture, the ydotool injector, and the service with its state machine and socket. Nothing
composes them. This ticket is the **composition root** — the only place where concrete
implementations are chosen — and it is where the epic's end-to-end acceptance test finally
becomes possible.

It implements the `DictationSession` seam published by
`../service-state-machine-and-socket/ticket.md`, in its **batch** form: record the whole
utterance, then transcribe once, then inject once. The streaming form is
`../../live-dictation-ux/streaming-wiring-and-e2e/ticket.md`, which will add a second
session implementation and a selection rule; this ticket must therefore keep composition in
small named functions rather than one blob.

Relevant decisions: ADR 0010 (single factory, protocol-typed callers), ADR 0015 (the service
owns state), ADR 0005 (30 s batch ceiling), ADR 0012 (empty text is never injected — though
the service enforces it too), ADR 0001 (the latency budget being measured here).

## Definition of done

- [ ] `src/ges2t/pipeline.py` provides:
      - `build_transcriber(config) -> Transcriber` — maps `config.transcriber.engine` to a
        concrete transcriber (only `"whistle"` in v1, using `WhistleConfig.from_mapping` on
        `config.engine_blocks.get(config.transcriber.engine, {})`), and raises a clear error
        for an unhandled engine. **Use `.get(...)` with an empty-mapping default, not
        `[...]`** — the generic config layer deliberately never synthesises an
        `[engine.<name>]` block, so an absent block is the normal case and must mean "all
        defaults", not a `KeyError` (see ticket 4's `001-comment.md`);
      - `build_audio_source(config) -> AudioSource` — `PwRecordAudioSource(config.audio.device)`;
      - `build_injector(config) -> Injector` — `YdotoolInjector(config.ui.trailing_space)`;
      - `class BatchDictationSession` implementing `DictationSession`: `start()` starts
        capture; `partials()` yields nothing (batch has no partials — ADR 0011); `finish()`
        stops capture, returns `""` when zero samples were captured, otherwise calls
        `transcriber.transcribe()` and returns the text; `cancel()` stops capture and
        discards the audio;
      - `run_service(config) -> int` — the entry point ticket
        `../cli-surface-and-commands/ticket.md` calls: ensure the model and engine are present
        **without downloading** (a missing artifact produces an actionable "run `ges2t setup`"
        error and a non-zero return), apply `model_store.env_overrides()`, `load()` the
        transcriber once, construct the `Service` with a session factory and a no-op status
        UI, run the socket server until `SIGINT`/`SIGTERM`, and return `0`.
- [ ] Model location resolution honours `config.transcriber.model_path` when set (non-empty),
      and otherwise uses `model_store.default_data_dir()`. The engine path always comes from
      the model store — an override for the *engine* is not supported.
- [ ] **No network can be reached from this path**: `run_service` must never call
      `ensure_model()`/`ensure_engine()`. A test asserts that with a monkeypatched
      `model_store.ensure_model` that raises if called.
- [ ] `tests/test_pipeline.py` covers, with fakes only (no mic, no engine, no subprocess):
      `build_transcriber` returns a `WhistleTranscriber` for `"whistle"` and raises for an
      unknown engine; `BatchDictationSession.finish()` returns `""` on zero samples and does
      **not** call the transcriber in that case; `finish()` returns the transcriber's text
      unchanged otherwise; `cancel()` produces no transcription; and the no-download
      assertion above.
- [ ] `uv run pytest tests/test_pipeline.py` passes and `uv run pytest` stays green.
- [ ] **Manual, end-to-end, on the reference machine — this is the epic's headline check:**
      after `ges2t setup`, enabling `ydotoold` and granting `/dev/uinput` access, with
      `uv run ges2t run` active:
      1. `uv run ges2t toggle`, say a short phrase, `uv run ges2t toggle` → the text appears in
         a focused text editor;
      2. repeat into a focused **terminal**;
      3. dictate twice in a row → two space-separated words (ADR 0012);
      4. dictate in silence → **nothing** is typed;
      5. `uv run ges2t cancel` during a recording → nothing is typed and state returns to
         `idle`.
      Record the exact commands, the observed results, and any failure in `001-comment.md`. If
      a step fails, that is a `blocked` ticket with a comment describing precisely what
      failed — not a workaround.
- [ ] **Framework latency, measured and reported as a number** in `001-comment.md`: the
      non-model portion of a round trip — socket call, capture stop and drain, buffer handoff,
      and injection of a ≤20-word phrase — measured by timing each stage. This must be
      **under 1 second** total (ADR 0001). State the method, not just the number.
- [ ] **Settle the `--key-delay` question with a number, not an opinion.** Ticket 7 left
      `ydotool` at its default 12 ms per key event and flagged that this likely blows the 1 s
      budget (the man page implies roughly **2.8 s for a 20-word phrase**). Once injection is
      live, measure it and either accept the default with the real number recorded, or reduce
      the delay and record the dropped-character behaviour. Ticket 7's live injection test is
      **deferred to this ticket** because it needs a running `ydotoold` (see that ticket's
      `001-comment.md`); the epic's latency criterion is **not** satisfied until this is
      measured. Do **not** run `sudo` or enable services — if the daemon is not running, record
      the measurement as pending and report it.

- [ ] `uv run mypy src/ges2t` exits 0: every definition fully annotated and every
      signature interface-typed — ADR 0019.

## Notes

- This is the **only** module allowed to construct concrete implementations. If any other
  module imports both a backend and the service, the abstraction has been broken (ADR 0010).
- The status UI here is a documented no-op (`NullStatusUI`); the tray arrives in epic 2 and
  will be injected in the same place, which is why `run_service` should accept an optional
  `status_ui=None` parameter.
- `transcriber.load()` happens **once** at startup and the model stays warm for the process's
  lifetime (26 ms load, ~69 MB — ADR 0005, ADR 0015).
- Do not implement streaming here, and do not add a `streaming.enabled` branch — ticket
  `../../live-dictation-ux/streaming-wiring-and-e2e/ticket.md` owns that decision and
  will extend `run_service`'s session selection.
- Windows of >30 s are guaranteed to fail on this path; the error message from the adapter
  should be surfaced to the user as a notification in epic 2, but for now a clear stderr
  message is enough.

## Related tickets

- depends on: ../whistle-transcriber-adapter-batch/ticket.md,
  ../audio-capture-pw-record/ticket.md, ../ydotool-injector/ticket.md,
  ../service-state-machine-and-socket/ticket.md, ../cli-surface-and-commands/ticket.md
- blocks: ../../live-dictation-ux/streaming-wiring-and-e2e/ticket.md,
  ../../live-dictation-ux/tray-status-ui/ticket.md
- related ADRs: ../../../adr/0010-transcriber-interface-design.md,
  ../../../adr/0015-single-service-process-architecture.md,
  ../../../adr/0001-python-implementation-language.md
