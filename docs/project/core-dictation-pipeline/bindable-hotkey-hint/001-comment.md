# 001 — bindable-hotkey-hint: the tool now prints the path it is actually running as

- **Ticket:** [bindable-hotkey-hint](ticket.md) (ticket 40)
- **Branch:** `feat/bindable-hotkey-hint`
- **Worktree:** `.worktrees/bindable-hotkey-hint`
- **Date:** 2026-10-10
- **Status:** implemented, tests green, in review
- **Commits:** `fb8d6e4` (resolution, strings, tests), plus the docs commit for this file
  and the board/ticket status.

## 1. The defect and the fix

Three shipped strings told the user to bind **`ges2t toggle`** to a KDE shortcut. That name is
not on a launcher's `PATH` (`uv sync` installs the console script into `.venv/bin/`), so the
advice cannot work; `uv run ges2t toggle` is not a substitute because a shortcut provides no
working directory.

Rather than write better prose about paths, the fix makes the process resolve **itself**. A venv
and a system install both put the console script beside the interpreter, so
`Path(sys.executable).with_name("ges2t")` finds it. The new
[`ges2t.console_script`](../../../../src/ges2t/console_script.py) module does exactly that:

- it never symlink-resolves the candidate, because `.venv/bin/python` is a symlink to the system
  interpreter and resolving it would name `/usr/bin/ges2t` — the wrong script;
- it returns a path **only when that path exists**, so an unresolvable request yields `None`;
- the two consumers — `doctor.hotkey_fix` / `doctor.kde_binding_section` and
  `cli.kde_shortcut_hint` — fall back to the bare `ges2t` command on `None` (or `""`).

`doctor` passes the resolved value through its injectable `DoctorProbes.console_script`, and the
resolved value is carried on the `DoctorReport` so `render_report` prints the same path; the CLI
resolves once in `build_parser`. The console script's own shebang is absolute
(`#!/home/matee/Workspace/Personal/ges2t/.worktrees/bindable-hotkey-hint/.venv/bin/python`), which
is why binding that exact path works from a launcher with no `PATH` and no cwd.

## 2. The before/after rendered output

`uv run ges2t doctor`, full output. This is the real evidence: the previous run was produced by
checking out the two source files from the base commit (`HEAD~1`) in this worktree, running
doctor, then restoring them; the after run is the shipped change.

### Before

```console
ges2t doctor

Package imports           OK    all 17 package modules import (Python 3.14.7)
Architecture              OK    linux-x86_64 has a pinned engine artifact
Model present             OK    whistle.cact is present at /home/matee/.local/share/ges2t/models/whistle/2.0.0/whistle.cact (16919407 bytes)
Model verified            OK    not requested; pass --verify to hash the model against its pinned sha256
Engine present            OK    libneedle.so is present at /home/matee/.local/share/ges2t/engine/3.2.0/libneedle.so (1581448 bytes)
cactus-needle importable  OK    'needle' imports
Engine/package versions   OK    cactus-needle 3.1.3 matches the pin 3.1.3; engine 3.2.0
Config valid              OK    the configuration parsed and every generic value has the right type
Engine config valid       OK    [engine.whistle] is valid
Injection config valid    OK    [injection.ydotool] is valid
Config warnings           OK    no unknown keys were found
Audio device              OK    audio.device is empty: the system default input is used
pw-record present         OK    'pw-record' is at /usr/bin/pw-record
PipeWire session          OK    a PipeWire session is reachable
ydotoold                  OK    the ydotoold daemon is reachable at /run/user/1000/.ydotool_socket
/dev/uinput access        OK    /dev/uinput is writable by this user
Tray backend              OK    pystray selected the visible AppIndicator backend
Tray failures             WARN  tray failures cannot be determined from here: the record lives in the running service process, and doctor does not connect to it or write a file
                                fix: watch the service's stderr for 'ges2t: the tray has failed ...' lines and its shutdown summary, or run 'ges2t run' in a terminal to see them
Service socket            OK    the service is listening at /run/user/1000/ges2t.sock
Keyboard layout           OK    resolved from the live compositor keymap; libxkbcommon compiled it
Clipboard backend         OK    not selected (injection.backend = 'ydotool'); 'wl-copy' is not required
Hotkey bindings           WARN  ges2t does not grab the hotkey itself; bind the command in KDE
                                fix: System Settings -> Shortcuts -> Add -> Command or URL: bind 'ges2t toggle' (recommended), or 'ges2t ptt-start' and 'ges2t ptt-stop', plus 'ges2t cancel'

Summary: 22 checks, 20 ok, 2 warning(s), 0 failure(s).

Recommended KDE key bindings (System Settings -> Shortcuts -> Add -> Command or URL):
  ges2t toggle      start or stop with one key (recommended default)
  ges2t ptt-start   press-to-talk: bind to the key press
  ges2t ptt-stop    press-to-talk: bind to the key release
  ges2t cancel      abort a recording without transcribing
ges2t never edits your compositor settings or your config file (ADR 0004).
```

### After

```console
ges2t doctor

Package imports           OK    all 18 package modules import (Python 3.14.7)
Architecture              OK    linux-x86_64 has a pinned engine artifact
Model present             OK    whistle.cact is present at /home/matee/.local/share/ges2t/models/whistle/2.0.0/whistle.cact (16919407 bytes)
Model verified            OK    not requested; pass --verify to hash the model against its pinned sha256
Engine present            OK    libneedle.so is present at /home/matee/.local/share/ges2t/engine/3.2.0/libneedle.so (1581448 bytes)
cactus-needle importable  OK    'needle' imports
Engine/package versions   OK    cactus-needle 3.1.3 matches the pin 3.1.3; engine 3.2.0
Config valid              OK    the configuration parsed and every generic value has the right type
Engine config valid       OK    [engine.whistle] is valid
Injection config valid    OK    [injection.ydotool] is valid
Config warnings           OK    no unknown keys were found
Audio device              OK    audio.device is empty: the system default input is used
pw-record present         OK    'pw-record' is at /usr/bin/pw-record
PipeWire session          OK    a PipeWire session is reachable
ydotoold                  OK    the ydotoold daemon is reachable at /run/user/1000/.ydotool_socket
/dev/uinput access        OK    /dev/uinput is writable by this user
Tray backend              OK    pystray selected the visible AppIndicator backend
Tray failures             WARN  tray failures cannot be determined from here: the record lives in the running service process, and doctor does not connect to it or write a file
                                fix: watch the service's stderr for 'ges2t: the tray has failed ...' lines and its shutdown summary, or run 'ges2t run' in a terminal to see them
Service socket            OK    the service is listening at /run/user/1000/ges2t.sock
Keyboard layout           OK    resolved from the live compositor keymap; libxkbcommon compiled it
Clipboard backend         OK    not selected (injection.backend = 'ydotool'); 'wl-copy' is not required
Hotkey bindings           WARN  ges2t does not grab the hotkey itself; bind the command in KDE
                                fix: System Settings -> Shortcuts -> Add -> Command or URL: bind '/home/matee/Workspace/Personal/ges2t/.worktrees/bindable-hotkey-hint/.venv/bin/ges2t toggle' (recommended), or '/home/matee/Workspace/Personal/ges2t/.worktrees/bindable-hotkey-hint/.venv/bin/ges2t ptt-start' and '/home/matee/Workspace/Personal/ges2t/.worktrees/bindable-hotkey-hint/.venv/bin/ges2t ptt-stop', plus '/home/matee/Workspace/Personal/ges2t/.worktrees/bindable-hotkey-hint/.venv/bin/ges2t cancel'

Summary: 22 checks, 20 ok, 2 warning(s), 0 failure(s).

Recommended KDE key bindings (System Settings -> Shortcuts -> Add -> Command or URL):
  /home/matee/Workspace/Personal/ges2t/.worktrees/bindable-hotkey-hint/.venv/bin/ges2t toggle     start or stop with one key (recommended default)
  /home/matee/Workspace/Personal/ges2t/.worktrees/bindable-hotkey-hint/.venv/bin/ges2t ptt-start  press-to-talk: bind to the key press
  /home/matee/Workspace/Personal/ges2t/.worktrees/bindable-hotkey-hint/.venv/bin/ges2t ptt-stop   press-to-talk: bind to the key release
  /home/matee/Workspace/Personal/ges2t/.worktrees/bindable-hotkey-hint/.venv/bin/ges2t cancel     abort a recording without transcribing
ges2t never edits your compositor settings or your config file (ADR 0004).
```

The only new check line is `all 17 → all 18 package modules import`, because the new
`ges2t.console_script` module joined doctor's own-modules list (it is part of the package now).
Everything else before the binding section is byte-identical; this is a text change, not a
behaviour change to any check.

### The CLI help (`uv run ges2t toggle --help`, epilog only)

Before:

```console
Binding (KDE): System Settings -> Shortcuts -> Add -> Command or URL, then
enter the ges2t command shown above. True press-and-hold (ptt-start/ptt-stop)
works only when the compositor can fire separate key-press and key-release
commands; if it cannot, bind 'ges2t toggle' instead. ges2t never edits your
compositor settings or your config file.
```

After:

```console
Binding (KDE): System Settings -> Shortcuts -> Add -> Command or URL, then
enter '/home/matee/Workspace/Personal/ges2t/.worktrees/bindable-hotkey-hint/.venv/bin/ges2t toggle' (recommended), or '/home/matee/Workspace/Personal/ges2t/.worktrees/bindable-hotkey-hint/.venv/bin/ges2t ptt-start' and
'/home/matee/Workspace/Personal/ges2t/.worktrees/bindable-hotkey-hint/.venv/bin/ges2t ptt-stop', or '/home/matee/Workspace/Personal/ges2t/.worktrees/bindable-hotkey-hint/.venv/bin/ges2t cancel'. True press-and-hold
(ptt-start/ptt-stop) works only when the compositor can fire separate
key-press and key-release commands; if it cannot, bind '/home/matee/Workspace/Personal/ges2t/.worktrees/bindable-hotkey-hint/.venv/bin/ges2t toggle'
instead. ges2t never edits your compositor settings or your config file.
```

The absolute path makes the epilog lines long; that is the honest cost of naming a real path.
`potentially ugly wrapping` is called out under "could not verify" below.

## 3. The strings and ticket 19's README agree

README §7 is the reference for the correct form: "Use the **absolute path** to the console
script created by `uv sync`" and "`uv run ges2t toggle` is **not** a reliable shortcut command".
The README paragraph that used to say doctor "prints them as bare `ges2t …`, which a shortcut
cannot run" is updated to say doctor resolves the console script it is running as and prints
every command by that absolute path, falling back to the bare form only when it cannot resolve
an existing file. The §11 sample report is updated to the resolved path (reference-checkout
example) and to `all 18 package modules`.

Both the doctor fix line and the CLI hint now use **exactly** the resolved path plus the same
four subcommands and the same wording, so they agree with §7 and §11 rather than adding a third
variant.

## 4. Tests pin the behaviour, and both halves were falsified

New tests (18 total, `904 → 922` passing):

- `tests/test_console_script.py` (7): a venv interpreter resolves `.venv/bin/ges2t`; a system
  interpreter resolves `/usr/bin/ges2t`; a missing candidate returns `None`; an empty interpreter
  returns `None`; the venv `python` symlink is **not** followed (the `exists` probe is called
  with the venv path exactly once); a relative interpreter still yields an absolute path; the
  fallback constant is the bare name.
- `tests/test_doctor.py` (8): `hotkey_fix` and `kde_binding_section` name the resolved path for
  every subcommand; both fall back to the bare command for `None` **and** `""`;
  `check_hotkey_bindings` forwards the resolved value into its fix; `render_report` uses the
  report's `console_script`; `build_report` carries the injected `console_script` probe into both
  the report and the hotkey fix.
- `tests/test_cli.py` (3): `kde_shortcut_hint` names the resolved path, falls back to the bare
  command, and the real `toggle --help` output contains the resolved path when one exists (the
  bare form otherwise).

**Falsification 1 — break the resolution.** The existence gate in `resolve_console_script` was
replaced with an unconditional `return str(candidate)`. `uv run pytest -q
tests/test_console_script.py` then failed 2 tests:

```
FAILED tests/test_console_script.py::test_returns_none_when_the_candidate_does_not_exist
FAILED tests/test_console_script.py::test_does_not_follow_the_virtualenv_python_symlink
2 failed, 5 passed
```

The second failure is the "the exists probe must actually be consulted" half: with the gate gone,
the probe is never called. The gate was restored immediately.

**Falsification 2 — remove the fallback.** `hotkey_fix` was changed from
`executable = console_script if console_script else BARE_COMMAND` to `executable = console_script`.
`uv run pytest -q tests/test_doctor.py -k "fall_back or falls_back"` failed:

```
FAILED tests/test_doctor.py::test_hotkey_bindings_falls_back_to_the_bare_command
FAILED tests/test_doctor.py::test_hotkey_fix_falls_back_to_the_bare_command
2 failed, 1 passed
E   assert "bind 'ges2t toggle'" in "...bind 'None toggle' (recommended), or 'None ptt-start'..."
```

The same experiment on `cli.kde_shortcut_hint` failed
`test_kde_shortcut_hint_falls_back_to_the_bare_command` with `"enter 'None toggle'"`. Both
fallbacks were restored immediately, and `grep -rn FALSIFICATION src/` is empty.

## 5. Grep accounting for other shipped output

`grep -rn "ges2t toggle|ges2t ptt|ges2t cancel|bind '|Command or URL|Shortcuts" src/ --include=*.py`
and a broader scan of every `'ges2t …'` string literal found these; all are accounted for:

| Location | What it is | Action |
|---|---|---|
| `doctor.py` `hotkey_fix()` | user-facing fix line, named `ges2t toggle` | **fixed**: resolved path |
| `doctor.py` `kde_binding_section()` | user-facing report footer, named `ges2t …` | **fixed**: resolved path |
| `cli.py` `kde_shortcut_hint()` (epilog of `toggle`, `ptt-start`, `ptt-stop`, `cancel`, `doctor`) | user-facing help, named `ges2t toggle` | **fixed**: resolved path |
| `cli.py` `_TOP_LEVEL_EPILOG` (`ges2t --help`) | names the *subcommands* and where they are bound, then says `run 'ges2t <command> --help'` | **unchanged**: it does not name a command to bind; it points at `--help`, which now prints the resolved path |
| `doctor.py` `HOTKEY_SUMMARY` ("bind the command in KDE") | summary half of the WARN, names no command | **unchanged** |
| `socket_proto.py:32` docstring ("a hotkey bound to `ges2t toggle` must fail fast") | internal module docstring, not shipped output | **unchanged** |
| `doctor.py` `ges2t setup` / `ges2t run` / `ges2t doctor` fixes, `cli.py` `ges2t setup`, `pipeline.py`, `whistle.py`, `tray.py`, `notify.py`, `socket_proto.py` | shell commands (not shortcut bindings): "run 'ges2t setup'", "start it with 'ges2t run'" | **out of scope**: they are not bindings, and this ticket must not change anything else doctor checks |

`doctor.py`'s `ydotoold` advice and every other check are untouched.

## 6. The fallback behaviour

`resolve_console_script` returns `None` when the interpreter string is empty, or when the
candidate beside it does not exist. Both consumers then use `BARE_COMMAND == "ges2t"`:

- a **system install** (`/usr/bin/python3` → `/usr/bin/ges2t`, which exists) prints the absolute
  `/usr/bin/ges2t`, so "do not assume a venv" holds;
- an unresolvable interpreter prints the bare `ges2t`, which is honest because the only case
  where the tool cannot see its own script is one where the script is not beside the interpreter
  — typically a system install on `PATH`. Crucially it never prints a path it has not verified.

## 7. Verification

```console
$ timeout 300 uv run pytest -q
922 passed in 7.27s        # process exited 0; 904 before + 18 new

$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 55 source files

$ python3 - <<'PY'   # the project's link check
...
PY
0 broken references
```

## 8. What I could not verify

- **A real KDE shortcut.** Binding a shortcut needs the Plasma GUI and is a consequential change;
  this agent did not touch the user's shortcut settings. The evidence is the rendered absolute
  path plus the console script's absolute shebang, and the user's own hand-run of
  `.venv/bin/ges2t toggle` recorded in `ticket.md`. The path being bindable from a launcher is
  therefore inferred from how the script is built, not observed in Plasma.
- **A real system install.** The `/usr/bin/ges2t` case is exercised with the injectable
  `exists`/`executable` arguments, not against a system-wide install on this machine (the
  reference install is a uv venv).
- **Readability of the long help lines.** The absolute path makes the CLI epilog lines exceed 200
  columns when rendered in a narrow terminal. That is cosmetic; the requirement was a path that
  works, and a shorter form would be a path that does not. No line-breaking was invented because
  it cannot know the terminal width.
- **Whether any user actually had `ges2t` shadowed on `PATH`.** The defect was verified from the
  install layout and ADR 0004, not reproduced by the user binding a failing shortcut; the user's
  own working command is the positive evidence for the fix.
