# The hotkey advice the tool prints is not literally bindable

- **Priority:** P1 (shipped user-facing output that misleads — v1's first-run experience)
- **Status:** to do
- **Complexity:** XS
- **Epic:** ../epic.md

## The defect

Found while writing ticket 19's setup guide. Three places tell the user to bind **`ges2t toggle`** to a
KDE shortcut:

- `src/ges2t/doctor.py` — `HOTKEY_FIX` and `KDE_BINDING_SECTION`
- `src/ges2t/cli.py` — `KDE_SHORTCUT_HINT`

**That command is not on the shortcut's `PATH`.** `uv sync` installs the console script into
`.venv/bin/`, and `.venv/bin` is not on a KDE shortcut's environment — so a shortcut bound to
`ges2t toggle` fails to launch. `uv run ges2t toggle` is not a drop-in substitute either, because `uv
run` needs a working directory (the project root) to find the project, and a shortcut has no cwd
guarantee.

The README written by ticket 19 gives the **absolute path** to the console script and explains why, so
the user documentation is correct — **but the tool's own advice is still wrong**, which is the worse
place for it: a user runs `ges2t doctor`, is told exactly what to bind, binds it, and nothing happens.

This is the same class the project has already been bitten by — **shipped output that reads as
verification or as instruction and is neither.**

## Definition of done

- [ ] Every user-facing string that names a command to bind names a command that **actually works from a
      desktop launcher** — the absolute path to the console script, or an equally reliable form.
- [ ] The strings and the README **agree**. Ticket 19's README is the reference for the correct form;
      do not invent a third variant.
- [ ] The advice accounts for the two distinct cases it must cover: a venv install (absolute path to
      `.venv/bin/ges2t`) and a system install (bare `ges2t` on `PATH`). Pick whichever the message can
      honestly determine, and if it cannot determine it, say both without pretending one is universal.
- [ ] **Tests pin the strings**, so they cannot drift back to a non-bindable form. Falsify: change one
      back and show the test fails.
- [ ] Confirm no other shipped output makes the same claim (`grep` for `ges2t toggle`, `ges2t ptt`,
      `yolobind`, etc. across `src/`, and check `doctor`'s own rendered output).
- [ ] `uv run pytest`, `uv run mypy src/ges2t tests` green.

## Notes

- **Do not** change the CLI's actual commands, the `ydotoold` advice, or anything else `doctor` checks —
  this is about the *text* of the binding advice being actionable.
- Run `uv run ges2t doctor` before and after, and compare its real output — the fix's evidence is what the
  user will actually see, not a string constant in a test.
- Ticket 19's setup guide is the authoritative description of the manual steps; if any part of it is wrong
  rather than this, say so instead of changing the code to match the docs.

## Related tickets

- found by: ../../live-dictation-ux/user-documentation-and-setup-guide/ticket.md (ticket 19)
- related: ../../core-dictation-pipeline/doctor-command/ticket.md (ticket 12, which owns `doctor`'s output),
  ../../core-dictation-pipeline/cli-surface-and-commands/ticket.md (ticket 10)
- related ADRs: ../../../adr/0004-global-hotkey-via-cli-and-service.md,
  ../../../adr/0016-cli-surface.md
