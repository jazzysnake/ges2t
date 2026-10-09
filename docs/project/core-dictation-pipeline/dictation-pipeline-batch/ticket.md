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
- [ ] **Manual, end-to-end, on the reference machine — this is the epic's headline check.**
      **This step is owned by the user, not by you:** it requires a human voice (someone must
      actually speak a phrase) and a focused window, so no agent can complete it, and no agent
      may inject keystrokes without explicit approval. Your job is to *prepare* it: automate
      everything that can be automated, then hand over a precise, copy-pasteable script with the
      exact commands and what to look for, and record the result in `001-comment.md` once the
      user reports back. The steps, after `ges2t setup`, with `ydotoold` active and
      `/dev/uinput` accessible, and `uv run ges2t run` active, are:
      1. `uv run ges2t toggle`, say a short phrase, `uv run ges2t toggle` → the text appears in
         a focused text editor;
      2. repeat into a focused **terminal**;
      3. dictate twice in a row → two space-separated words (ADR 0012);
      4. dictate in silence → **nothing** is typed;
      5. `uv run ges2t cancel` during a recording → nothing is typed and state returns to
         `idle`.
      Expected known failures — **do not re-litigate these and do not treat them as new
      blockers**: (a) on the reference machine's **non-US `hu` layout** the injected characters
      will be **wrong** (`0`→`ö`, `y`→`z`) because the current injector still calls
      `ydotool type`, which maps text through a hardcoded US keymap; ADR 0020 and tickets
      `../xkb-keycode-mapping/ticket.md` + `../batched-key-emission/ticket.md` exist to fix
      exactly this. (b) For the same reason the injection portion of the round trip will be
      **far above 1 s**. Record both as **observed known in-flight defects with their ticket
      numbers**, and record what *does* work (text appears at all, silence types nothing,
      `cancel` types nothing and returns to `idle`, two phrases give two space-separated words).
      If something fails **outside** those two known defects, that is a `blocked` ticket with a
      comment describing precisely what failed — not a workaround.
- [ ] **Framework latency, measured and reported as a number** in `001-comment.md`: the
      non-model portion of a round trip — socket call, capture stop and drain, buffer handoff,
      and injection of a ≤20-word phrase — measured by timing each stage. This must be
      **under 1 second** total (ADR 0001). State the method, not just the number.
- [ ] **Injection latency: record the number as the pre-rewrite baseline.** Ticket 7 left
      `ydotool` at its default 12 ms per key event and asked this ticket to settle the
      `--key-delay` question. **That question is superseded** — ADR 0020 abandoned
      `ydotool type` altogether (US-only keymap, ~40 ms/char) in favour of a batched
      `ydotool key` emission, and `../batched-key-emission/ticket.md` now owns the
      injection-latency measurement and the 1 s budget. So: do **not** spend effort tuning
      `--key-delay`. Instead measure and record in `001-comment.md` the framework portion that
      *is* this ticket's to measure — socket call, capture stop and drain, buffer handoff, and
      the injection call itself — **excluding model time**, and state the observed injection
      cost as the **before** number ticket 23 will be measured against. Exceeding 1 s here is a
      **known in-flight defect owned by ticket 23**, not a blocker for this ticket: record it
      and move on. `ydotoold` is already running and enabled on the reference machine, so no
      `sudo` and no service changes are needed — and none are permitted.

- [ ] `uv run mypy src/ges2t tests` exits 0 (per ADR 0019's amendment the enforcement command
      covers `tests/` too): every definition fully annotated and every
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
