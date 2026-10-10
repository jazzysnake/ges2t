# whisper backend config and startup validation

- **Priority:** P1
- **Status:** to do
- **Complexity:** S
- **Epic:** ../epic.md

## Current state / context

Configuration is TOML at `~/.config/ges2t/config.toml` (ADR 0009); the backend is chosen by
`transcriber.backend`. Ticket 34 set the precedent for **refusing to start** on a contradictory config
(`clipboard` + streaming is a hard startup error naming both settings). The user requires the same
strictness here: **on launch, the config must be validated**, and a whisper config that cannot work
must fail loudly rather than degrade.

**The default backend does not change** — it stays `needle`.

## Definition of done

- [ ] `transcriber.backend = "whisper_cpp"` is accepted and selects the adapter; `needle` remains the
      default and its behaviour is unchanged.
- [ ] The **needle model is selectable** too (default `whistle`), so neither engine's model is
      hardcoded (ADR 0021).
- [ ] A `[transcriber.whisper_cpp]` block (named consistently with the existing config vocabulary):
      **model** (default per the GPU/CPU rule below), **gpu** (default: auto), **threads** (default:
      the machine's). Every value validated with a typed, actionable error.
- [ ] **Default model rule** (ADR 0021): `medium-q5_0` when a Vulkan device is available, `small-q8_0`
      otherwise; overridable. It is falsifiable in a test.
- [ ] **Startup validation** — with `transcriber.backend = "whisper_cpp"`, the service **refuses to
      start** (clear error naming the offending setting(s)) when:
      - `streaming.enabled = true`;
      - the configured model is not acquired on disk;
      - the whisper library is not built.
      **Model-vs-GPU sizing is deliberately NOT checked** (not a startup error, not a doctor warning).
- [ ] The service constructs and **loads** the whisper transcriber **at startup** (the ADR 0021
      resident-model requirement), and a load failure is fatal with a clear message.
- [ ] **ADR 0021** reviewed against the implementation and presented for the user's sign-off (the
      orchestrator handles the sign-off step) before this ticket merges.
- [ ] User docs: how to select each backend, the accuracy/latency trade, and the batch-only
      consequence for whisper (no text until the utterance ends).
- [ ] Tests: the validation matrix (each rejection above), the default-model rule, and that
      `needle` still streams. **Falsify each** — remove a check, watch the test fail, revert.
- [ ] `uv run pytest` and `uv run mypy src/ges2t tests` green; report counts.

## Notes

- Follow ticket 34's refusal style exactly: name both settings, fail closed, no silent downgrade.
- Do **not** add automatic fallback between backends (ADR 0020).
- If this ticket and `../setup-backend-and-model-arguments/ticket.md` both touch `cli.py` or the
  config module, **serialize them**.
- **No keystrokes, no clipboard, no windows, no microphone, no `sudo`, no service changes, no network.**

## Related tickets

- depends on: ../whisper-transcriber-adapter/ticket.md
- related: ../../live-dictation-ux/incremental-typing/ticket.md (the `clipboard` + streaming
  refusal precedent)
- related ADRs: ../../../adr/0009-configuration-toml.md, ../../../adr/0011-streaming-sliding-window.md, ../../../adr/0020-layout-aware-keycode-mapping.md, ../../../adr/0021-whisper-cpp-second-backend.md
