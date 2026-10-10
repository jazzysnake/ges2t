# whisper backend config and default

- **Priority:** P1
- **Status:** to do
- **Complexity:** S
- **Epic:** ../epic.md

## Current state / context

Configuration is TOML at `~/.config/ges2t/config.toml` (ADR 0009); the backend is chosen by
`transcriber.backend` and the streaming/batch path by the ticket-16 selection rule. Today the only
backend is Whistle, and streaming is the default, which is what makes incremental typing work
(ADR 0011, ticket 34).

This ticket makes `whisper_cpp` selectable, configures it, and **flips the shipped default to it** —
accepting, explicitly, that the default path no longer types incrementally. **This is the ticket that
carries ADR 0021**, and the ADR is presented in full for the user's sign-off **before** the merge.

## Definition of done

- [ ] `transcriber.backend = "whisper_cpp"` is accepted and selects the adapter; Whistle remains fully
      selectable and its behaviour is unchanged.
- [ ] A `[transcriber.whisper_cpp]` block (name it consistently with the existing config vocabulary):
      **model** (default `medium`), **quant** (default `q5_0`), **threads** (default: the machine's),
      and **gpu** (default: on where available). Every value validated with a typed, actionable error.
- [ ] **The shipped default becomes `whisper_cpp` / `medium q5_0`.**
- [ ] With whisper selected and `streaming.enabled = true`, the tool uses the **batch** path and
      **surfaces** the limitation (doctor and/or an explicit message) rather than silently dropping
      incremental typing — the same principle as ticket 34's refusal to silently degrade.
- [ ] **ADR 0021 authored**: records (a) whisper.cpp as a batch-only backend, (b) the `-nt` decode
      requirement, (c) the model/licence/pinning stance, (d) the **default flip and the loss of
      incremental typing** it accepts. Presented to the user in full for sign-off before merge.
- [ ] User docs updated: how to select each backend, the accuracy/latency trade, and the batch-only
      consequence for whisper (no text until the utterance ends).
- [ ] Tests: config parse + validation (good and bad values); the selection rule picks whisper batch
      and never the streaming path for it; Whistle still streams. Falsify each.
- [ ] `uv run pytest` and `uv run mypy src/ges2t tests` green; report counts.

## Notes

- **This is a user-facing default change.** Do not merge it without the ADR 0021 sign-off; the
  orchestrator handles that step.
- Do not add automatic fallback between backends (ADR 0020).
- **No keystrokes, no clipboard, no windows, no microphone, no `sudo`, no service changes, no network.**

## Related tickets

- depends on: ../whisper-transcriber-adapter/ticket.md
- related ADRs: ../../../adr/0009-configuration-toml.md, ../../../adr/0011-streaming-sliding-window.md, ../../../adr/0020-layout-aware-keycode-mapping.md
