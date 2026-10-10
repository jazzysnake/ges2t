# whisper engine naming: one spelling, and setup tells you what to configure

- **Priority:** P1
- **Status:** to do
- **Complexity:** S
- **Epic:** ../epic.md

## Current state / context

The user hit this on their first real run. `ges2t setup` names the backend **`whisper.cpp`**, while the
config engine value is **`whisper_cpp`**:

```
ges2t setup whisper.cpp medium-q5_0      # the CLI token
transcriber.engine = "whisper.cpp"       # ✗ the user's config — never valid
transcriber.engine = "whisper_cpp"       # ✓ the real value
```

After a successful `setup whisper.cpp medium-q5_0`, setting `transcriber.engine = "whisper.cpp"` fails
with *"… is not a known engine; known engines: 'whistle'"* — with nothing telling the user the value
they typed is a CLI token, not an engine name, or that `setup` does not (and cannot, ADR 0004) edit
their config. **One thing, two spellings is the defect.** It is the direct motivation for
`../../code-quality/epic.md` (ticket 48).

## Definition of done

- [ ] **One canonical engine value, plus a forgiving alias.** `whisper_cpp` is canonical (TOML block
      names make `whisper.cpp` awkward); the config layer accepts **`whisper.cpp` as an alias**,
      normalised to `whisper_cpp` at parse, and `ges2t setup` accepts **both** tokens.
- [ ] The `[engine.<name>]` block is resolved through the same normalisation, so
      `transcriber.engine = "whisper.cpp"` reads the `[engine.whisper_cpp]` block (and an actual
      `[engine.whisper.cpp]` block, if present, is either read or rejected with a clear message — decide
      and document).
- [ ] **`ges2t setup` prints the exact line to add** to `~/.config/ges2t/config.toml` for the backend it
      just set up (`transcriber.engine = "whisper_cpp"`), because it cannot edit the config itself
      (ADR 0004). Same for `needle` (the default needs no line, but say so).
- [ ] The rejection message for an unknown engine **names the valid set** and, when the value looks like
      the CLI token, points at the right one.
- [ ] Docs (README + the setup guide) show **one** spelling and note the accepted alias.
- [ ] Tests: the alias normalises in both the CLI and the config value; the block resolves; setup prints
      the line; the rejection names the valid set. **Falsify each** — remove the normalisation, watch a
      test fail, revert.
- [ ] `uv run pytest` and `uv run mypy src/ges2t tests` green; report counts; no behaviour change beyond
      the alias and the message.

## Notes

- Depends on ticket 45 (`whisper_cpp` in `KNOWN_ENGINES`); this ticket is what makes the naming
  usable rather than just present.
- Keep the alias surface minimal — one spelling in the docs, one accepted alias.
- **No keystrokes, no clipboard, no windows, no microphone, no `sudo`, no service changes, no network.**

## Related tickets

- depends on: ../whisper-backend-config-and-validation/ticket.md
- related: ../../code-quality/centralize-identifier-literals/ticket.md
- related ADRs: ../../../adr/0009-configuration-toml.md, ../../../adr/0016-cli-surface.md, ../../../adr/0021-whisper-cpp-second-backend.md
