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

## The fix the user asked for

The user bound this by hand and confirmed it works:

```
/home/matee/Workspace/Personal/ges2t/.venv/bin/ges2t toggle
```

**So the fix is not better prose about paths — it is for the tool to print its own resolved path.**
`doctor` is running as that very console script, so it can work out where it is and say so, turning a
generic example into an instruction that works on the machine in front of the user. Ticket 19's README
then has something concrete to point at instead of explaining the problem in prose.

## Definition of done

- [ ] **Preferred form — resolve the real path.** The binding advice prints the path of the console script
      the process is *actually running as*, not a generic `ges2t toggle`. In a venv the console script sits
      beside the interpreter, so `Path(sys.executable).with_name("ges2t")` resolves it. **Verify the file
      exists before recommending it**; fall back to the bare command when the path cannot be determined or
      does not exist — a plausible-looking wrong path is worse than the honest generic one. Target shape:
      `/home/matee/Workspace/Personal/ges2t/.venv/bin/ges2t toggle`, a real resolved path, not an example.
- [ ] Every user-facing string that names a command to bind names a command that **actually works from a
      desktop launcher**.
- [ ] The strings and the README **agree**. Ticket 19's README is the reference for the correct form;
      do not invent a third variant.
- [ ] The advice stays correct for a **system** install (`/usr/bin/ges2t` on `PATH`), where the resolved
      path is still right — do not assume a venv.
- [ ] **Tests pin the behaviour**, including the **fallback** (unresolvable or non-existent → bare
      command). Falsify both: break the resolution and show the relevant test fails; remove the fallback
      and show that test fails.
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
