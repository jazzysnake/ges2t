# Whistle engine-specific config block

- **Priority:** P0
- **Status:** to do
- **Complexity:** S
- **Epic:** ../epic.md

## Current state / context

`config-loading` implements the generic layer and deliberately hands `[engine.<name>]` tables
through as raw mappings without interpreting them (ADR 0009). Nothing validates
`[engine.whistle]` yet.

The user explicitly required this split: engine knobs must not live in the generic config.
The Whistle block holds `language`, and — because they are properties of *this* engine's
re-transcription behaviour — `window_seconds` and `chunk_seconds` for streaming (ADR 0011).

Since `language` is needed by the batch adapter, this ticket lands **before**
`../whistle-transcriber-adapter-batch/ticket.md`, which consumes it.

Relevant decisions: ADR 0009 (split and rules), ADR 0011 (window/chunk defaults and the
measured latency knee), ADR 0005 (engine's 30 s per-call ceiling).

## Definition of done

- [ ] `src/ges2t/transcribers/__init__.py` exists (may be empty) so the package is importable.
- [ ] `src/ges2t/transcribers/whistle_config.py` defines a frozen dataclass
      `WhistleConfig(language: str = "", window_seconds: float = 5.0, chunk_seconds: float =
      1.0)`.
- [ ] `WhistleConfig.from_mapping(raw: Mapping[str, Any]) -> WhistleConfigResult` returns a
      **frozen dataclass** `WhistleConfigResult(config: WhistleConfig, warnings: list[str])` —
      never a tuple (ADR 0019).
- [ ] Validation, each with a clear error naming the key, the offending value, and the
      allowed range:
      - `language`: must be a string; any string is accepted (the engine auto-detects when
        empty) — do not hard-code an allowed-language list, because the engine's language set
        is upstream data, not our contract;
      - `chunk_seconds < 0.25` → error;
      - `chunk_seconds >= window_seconds` → error;
      - `window_seconds > 30` → error (the engine's own per-call ceiling, ADR 0005);
      - `window_seconds > 6` → **not an error**, but append a warning explaining that the
        measured per-chunk latency crosses the 1 s budget above ~6 s of buffer (ADR 0011),
        so the user is knowingly trading latency for context.
- [ ] Wrong-typed values produce errors of the same quality (e.g. `window_seconds = "five"`).
- [ ] `tests/test_whistle_config.py` covers: empty mapping → all defaults; unknown key →
      warning and the key is ignored; each validation rule above (error and warn cases);
      wrong type; and that `window_seconds = 5`, `chunk_seconds = 1` is accepted with **no**
      warnings.
- [ ] `uv run pytest tests/test_whistle_config.py` passes and `uv run pytest` stays green.

- [ ] `uv run mypy src/ges2t` exits 0: every definition fully annotated and every
      signature interface-typed — ADR 0019.

## Notes

- **Do not add a `model_path` key here.** Model location is generic
  (`transcriber.model_path`, ADR 0009) because it is not engine-specific.
- Do not import `cactus_needle` in this module — it must be importable and testable with no
  backend present (ADR 0010).
- The warning text is user-facing and surfaces through `ges2t doctor`; write it as a full
  sentence a user can act on, not a code.
- Keep this module free of any streaming or windowing logic. It validates numbers and
  strings; `../../live-dictation-ux/streaming-window-engine/ticket.md` uses them.

## Related tickets

- depends on: ../config-loading/ticket.md
- blocks: ../whistle-transcriber-adapter-batch/ticket.md,
  ../../live-dictation-ux/whistle-streaming-adapter/ticket.md
- related ADRs: ../../../adr/0009-configuration-toml.md,
  ../../../adr/0011-streaming-sliding-window.md,
  ../../../adr/0019-code-style-typing-and-interfaces.md
