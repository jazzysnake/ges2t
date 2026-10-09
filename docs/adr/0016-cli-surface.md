# CLI surface

- **Status:** accepted
- **Date:** 2026-10-09

## Context

The user approved a proposed command list and asked for nothing else, so this ADR fixes the
surface rather than inventing it. It exists because the commands are **user-facing API** —
they are what the user binds to keys in KDE (ADR 0004) and what the README documents — and
because three of them (`setup`, `doctor`, `models`) are the entire answer to "how does the
user fix a broken install?" in the absence of packaging (ADR 0000).

Two constraints shape it:

- Commands bound to keys must be **fast and never load the engine** (ADR 0015).
- Exit codes are behaviour: a compositor binding and a human both need to distinguish
  "worked" from "the service isn't running" from "the injection failed".

## Decision

One console script, `ges2t`, with exactly these subcommands in v1:

| Command | Purpose | Needs service? | Exit codes |
|---|---|---|---|
| `ges2t run` | Run the service + tray (ADR 0015) | — (starts it) | 0 clean shutdown; non-zero if a socket is already in use or the model is missing |
| `ges2t toggle` | Start recording if idle, else stop + transcribe. **Default key binding.** | yes | 0 ok; 2 service not running; 3 engine/injection failed |
| `ges2t ptt-start` | Begin recording (press-to-talk) | yes | 0 ok; 2 service not running |
| `ges2t ptt-stop` | End recording and transcribe | yes | as `toggle` |
| `ges2t cancel` | Abort recording without transcribing | yes | 0 ok; 2 service not running |
| `ges2t status` | Print current state (`idle`/`recording`/`transcribing`/`error`) | yes | 0 always when the service exists |
| `ges2t setup` | Acquire and verify the model (ADR 0017) | no | 0 ok; non-zero on download/verification failure |
| `ges2t doctor` | Diagnose environment: model present, `ydotoold`, uinput access, tray backend, config validity, unknown config keys, service/socket state, `pw-record` presence | no | 0 all-good; non-zero when something is broken |
| `ges2t models` | List known models and whether each is present/verified | no | 0 ok |

Cross-cutting rules:

- **`--config <path>`** is accepted by every subcommand that reads configuration
  (ADR 0009).
- **Every failure prints an actionable sentence**, not a traceback: what is wrong, and the
  exact command that fixes it (`ges2t setup`, "enable ydotoold with …", "add the shortcut
  in System Settings"). Tracebacks only behind a debug flag.
- **A non-zero exit is a documented code**, not an arbitrary 1, because compositor bindings
  and users rely on it.
- **Key-bound commands must not import the engine or GTK.** Imports are lazy per subcommand
  so `ges2t toggle` pays for nothing it does not use.
- Help text for each command includes the KDE shortcut setup line for the commands that are
  meant to be bound.
- No subcommand silently mutates the user's config file or their compositor settings.

## Consequences

- The surface is small enough to document exhaustively in the README, which is the plan.
- `doctor` becomes the designated home for every environment check discovered by any other
  ticket. Tickets that add a new prerequisite are expected to extend `doctor`, and this is a
  checklist item in the epic acceptance criteria.
- Because `cancel` and `status` were added alongside the approved list, the KDE binding
  documentation is the authoritative mapping; `ges2t doctor` should print the recommended
  commands to bind.
- Adding a subcommand later is cheap; renaming one is a breaking change to the user's
  compositor configuration and needs a superseding ADR.
