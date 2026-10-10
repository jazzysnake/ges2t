# setup guidance: one engine spelling, the config line, and the GPU dev-package hint

- **Priority:** P1
- **Status:** in review
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

**Second gap, from the same run.** The user has an **AMD GPU and a working Vulkan runtime**, but was
missing `spirv-headers`, so the engine built **CPU-only with no hint** that one package would give the
GPU path (the manifest records `"variant": "cpu"`, `"spirv_headers": null`). `setup` should tell them.

## Definition of done

- [x] **One canonical engine value, plus a forgiving alias.** `whisper_cpp` is canonical (TOML block
      names make `whisper.cpp` awkward); the config layer accepts **`whisper.cpp` as an alias**,
      normalised to `whisper_cpp` at parse, and `ges2t setup` accepts **both** tokens.
- [x] The `[engine.<name>]` block is resolved through the same normalisation, so
      `transcriber.engine = "whisper.cpp"` reads the `[engine.whisper_cpp]` block (an actual
      `[engine.whisper.cpp]` block is **read** as the same block — TOML's dotted form is folded
      through the normaliser; having both spellings is a hard error naming both).
- [x] **`ges2t setup` prints the exact line to add** to `~/.config/ges2t/config.toml` for the backend it
      just set up (`transcriber.engine = "whisper_cpp"`), because it cannot edit the config itself
      (ADR 0004). Same for `needle` (the default needs no line, so it says so).
- [x] The rejection message for an unknown engine **names the valid set** and, when the value looks like
      the CLI token (or a near miss such as `whisper` / `Whisper.CPP`), points at the right one.
- [x] Docs (README + the setup guide) show **one** spelling and note the accepted alias.
- [x] Tests: the alias normalises in both the CLI and the config value; the block resolves; setup prints
      the line; the rejection names the valid set. **Falsified** (F1–F5 in `001-comment.md`).
- [x] **When a Vulkan runtime/GPU is present but a Vulkan dev piece is missing**, `ges2t setup`
      prints a hint **naming the missing package** (e.g. `spirv-headers`) and the consequence
      (**CPU-only build**), with the exact `pacman` line — a **diagnostic, not an error**. "No GPU at
      all" gets no hint; "GPU present, dev packages missing" does, via
      `inspect_vulkan`/`check_prerequisites`.
- [x] `uv run pytest` (1159 passed) and `uv run mypy src/ges2t tests` (0 errors, 64 files) green;
      no behaviour change beyond the alias, the messages and the hint.

## Notes

- Depends on ticket 45 (`whisper_cpp` in `KNOWN_ENGINES`); this ticket is what makes the naming
  usable rather than just present.
- Keep the alias surface minimal — one spelling in the docs, one accepted alias.
- **No keystrokes, no clipboard, no windows, no microphone, no `sudo`, no service changes, no network.**

## Related tickets

- depends on: ../whisper-backend-config-and-validation/ticket.md
- related: ../../code-quality/centralize-identifier-literals/ticket.md
- related ADRs: ../../../adr/0009-configuration-toml.md, ../../../adr/0016-cli-surface.md, ../../../adr/0021-whisper-cpp-second-backend.md
