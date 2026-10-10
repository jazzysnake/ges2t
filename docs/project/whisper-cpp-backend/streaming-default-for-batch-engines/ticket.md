# A batch-only engine must not require an explicit `streaming.enabled = false`

- **Priority:** P1
- **Status:** to do
- **Complexity:** S
- **Epic:** ../epic.md

## Current state / context

`StreamingConfig.enabled` defaults to **`True`** (`config.py`), and ticket 45's
`_whisper_streaming_error` refuses startup whenever `streaming.enabled` is true **and** the engine is
`whisper_cpp` — **even when the user never wrote a `streaming` section**. So the minimal, obvious config

```toml
[transcriber]
engine = "whisper_cpp"
```

is rejected for a setting the user did not choose. The user hit exactly this.

The intent behind ticket 45 was sound (fail loudly rather than silently drop incremental typing), but
it conflates **"the user asked for streaming"** with **"streaming is on because it is the default"**.
Only the former should be an error.

## Definition of done

- [ ] A config that selects a **batch-only** engine and does **not** set `streaming.enabled` **starts**:
      streaming is effectively **off** for that engine, and the tool says so in one line (e.g.
      "streaming is off because `whisper_cpp` is batch-only") — informed, **not** a silent downgrade.
- [ ] An **explicit** `streaming.enabled = true` with a batch-only engine is **still a startup error**,
      naming both settings (the current message, which is good — keep it).
- [ ] An explicit `streaming.enabled = false` behaves as now.
- [ ] **`whistle` is unchanged**: with streaming unset it still streams (the default), and nothing about
      the whistle path changes.
- [ ] Decide and justify the mechanism — an **engine-aware default** for `streaming.enabled`, or tracking
      whether the key was **explicitly present** — in `001-comment.md`. It must not make `load_config` a
      hardware probe or introduce hidden state.
- [ ] **ADR 0021 §8 is updated** if its text now overstates the rule (it says "rejected … when
      `streaming.enabled = true`"; with an engine-aware default, an *unset* value is not `true`). Present
      the change to the orchestrator for sign-off before merge if the ADR's meaning changes.
- [ ] Tests **falsify** the matrix: unset + batch-only → starts (batch); explicit `true` + batch-only →
      error; explicit `false` + batch-only → starts; `whistle` + unset → streams. Break each, watch a
      test fail, revert.
- [ ] `uv run pytest` and `uv run mypy src/ges2t tests` green; report counts.

## Notes

- This is a **refinement** of ticket 45, not a reversal: the goal stays "no silent loss of incremental
  typing". The fix is that the *default* follows the engine, and only an explicit conflict errors.
- Ticket 47 also touches `config.py`; **serialize with it** if both are in flight.
- **No keystrokes, no clipboard, no windows, no microphone, no `sudo`, no service changes, no network.**

## Related tickets

- depends on: ../whisper-backend-config-and-validation/ticket.md
- related: ../engine-naming-and-setup-guidance/ticket.md
- related ADRs: ../../../adr/0021-whisper-cpp-second-backend.md, ../../../adr/0011-streaming-sliding-window.md
