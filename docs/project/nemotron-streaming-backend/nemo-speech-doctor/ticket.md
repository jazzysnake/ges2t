# Nemotron doctor checks

- **Priority:** P2
- **Status:** to do
- **Complexity:** S
- **Epic:** ../epic.md

## Current state / context

`ges2t doctor` diagnoses every prerequisite so a broken setup is visible before the user tries to
dictate. The whisper.cpp backend already has its checks (`doctor.py` — the whisper checks around
`:742`), and the config-validator registration lives at `doctor.py:1400`. This ticket adds the same
coverage for the `nemo_speech` engine, gated on it being the **selected** engine, so a user who never
selects it sees no new output.

This is diagnostics in one file. It is deliberately **separate** from the composition-root wiring
(ticket 60): different file, different concern. Its dependencies are the identifier and engine module
(ticket 57) and the selectable-by-name config + validator (ticket 58) — nothing from the streaming or
pipeline tickets.

Read [the design report §7.1](../../research/nemo-pure-streaming-integration/REPORT.md) and
[ADR 0022](../../../adr/0022-nemotron-streaming-pure-streaming-backend.md) first.

## Definition of done

- [ ] `doctor.py` gains **`check_nemo_engine`** (the engine archive present and hash-verified),
      **`check_nemo_model`** (the GGUF present, correct size/hash), and **`check_nemo_device`**
      (whether the **Vulkan** device is available and what the effective backend is).
- [ ] All three are **gated by the selected engine**: with `transcriber.engine` set to anything else,
      `doctor` output is unchanged. Model-vs-GPU **sizing** is not checked (parity with whisper).
- [ ] The `[engine.nemo_speech]` config validator is **registered** (`doctor.py:1400` pattern), so a
      bad `device`/`chunk_seconds` value is reported like other engine blocks.
- [ ] Failure messages name the **exact resolution**, mirroring the whisper checks (e.g. the `setup`
      line to run), not a generic error.
- [ ] Hermetic `doctor` tests: selected vs unselected gating, a missing engine archive, a missing/wrong
      model, and the Vulkan-present vs Vulkan-absent device paths (the test seam is whatever the
      whisper `doctor` tests use — reuse it, do not invent a new one).
- [ ] `uv run pytest` green, `uv run mypy src/ges2t tests` 0 errors; docs link check 0; README row
      updated.

## Notes

- **Never a silent substitution** (ADR 0020): the device check must report the **effective** backend,
  not just "auto".
- Reuse the whisper `doctor` helpers and message style; the two native engines should diagnose
  identically in shape.
- Do not add network calls to `doctor` (ADR 0018) — presence/hash checks only.

## Related tickets

- depends on: [../nemo-speech-engine-and-model-acquisition/ticket.md](../nemo-speech-engine-and-model-acquisition/ticket.md) (57) ·
  [../nemo-speech-batch-adapter/ticket.md](../nemo-speech-batch-adapter/ticket.md) (58)
- blocks: [../nemo-speech-docs/ticket.md](../nemo-speech-docs/ticket.md) (62)
- related ADRs: [ADR 0016](../../../adr/0016-cli-surface.md) ·
  [ADR 0017](../../../adr/0017-model-acquisition-via-setup.md) ·
  [ADR 0020](../../../adr/0020-layout-aware-keycode-mapping.md) ·
  [ADR 0021](../../../adr/0021-whisper-cpp-second-backend.md) ·
  [ADR 0022](../../../adr/0022-nemotron-streaming-pure-streaming-backend.md)
