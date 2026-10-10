# 001 — comment: whisper backend config and startup validation

- **Ticket:** `docs/project/whisper-cpp-backend/whisper-backend-config-and-validation/ticket.md`
- **Branch:** `feat/whisper-backend-config-and-validation`
- **Worktree:** `.worktrees/whisper-backend-config-and-validation`
- **Status:** in progress

## Scope read first

Read before touching code: the ticket, `../epic.md`, ADR 0021, ADR 0009, ADR 0011, ADR 0020,
the merged dependencies (`whisper-transcriber-adapter` ticket 43, `setup-backend-and-model-arguments`
ticket 44), and the current code (`config.py`, `transcribers/whisper_cpp*.py`, `pipeline.py`,
`whisper_engine.py`, `setup_command.py`, `model_store.py`).

## Decisions

_(recorded incrementally; evidence appended below)_

1. **Engine vocabulary.** The engine selector is `transcriber.engine`; the default engine is
   `whistle` (the human "needle" name is the loose ticket/epic spelling). This ticket adds
   `"whisper_cpp"` to `KNOWN_ENGINES`; the default stays `whistle` and Whistle's behaviour is
   untouched.
2. **Where the omitted `[engine.whisper_cpp] model` is resolved: at *transcriber build*, not in
   the generic config parse.** `WhisperCppConfig.from_mapping` now represents an omitted model as
   `None` (unresolved) and does **not** probe the machine — a pure parse stays side-effect-free
   (ADR 0009). The pipeline's composition root resolves `None` with the single shared
   `whisper_engine.default_whisper_model()`, which is also what `ges2t setup` uses, so the
   resident model and the model `setup` would fetch cannot disagree (single source of truth).
3. **Device-aware rule lives with the probe.** `DEFAULT_WHISPER_MODEL_GPU` / `_CPU` and
   `default_whisper_model()` move to `whisper_engine.py` (which owns `inspect_vulkan`); `setup_command`
   re-exports them so ticket 44's public API is unchanged. No probe is duplicated.

_(more below as work lands)_
