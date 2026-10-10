# 001 — comment: the user documentation and setup guide (ticket 19)

- **Ticket:** [ticket.md](ticket.md)
- **Status of this comment:** written by the agent that implemented the ticket
- **Worktree:** `.worktrees/user-documentation-and-setup-guide`, branch
  `feat/user-documentation-and-setup-guide`

## What was written

| File | What it is |
|---|---|
| `README.md` (rewritten) | The user-facing documentation: the 13 sections the DoD lists, in order |
| `docs/setup/ges2t.service` (new) | A systemd **user** unit running `ges2t run`, with a comment explaining the two paths to edit |
| `docs/setup/autostart.md` (new) | Enabling, checking, logging, stopping the unit; and what the unit does *not* do |
| This file | The verification record |

The README's sections, in the DoD's order: 1 what ges2t is · 2 status · 3 requirements ·
4 install · 5 the `/dev/uinput` permission · 6 `ges2t setup` · 7 the KDE key binding ·
8 run it (+ autostart) · 9 use it · 10 configuration reference · 11 troubleshooting ·
12 architecture and decisions · 13 known limitations.

No source file was modified. The documentation-only scope of the ticket (`docs/`,
`README.md`) was respected.

## Accuracy method

Every command, path, key name and default was read **out of the code in this worktree** —
`src/ges2t/config.py`, `doctor.py`, `cli.py`, `pipeline.py`, `service.py`, `layout.py`,
`whistle_config.py`, `model_store.py`, `incremental_typing.py`, `clipboard.py`, `tray.py`,
`notify.py` — and not from an older ticket comment or from memory. Defaults came from the
dataclass field defaults and the module constants, not from the ADRs (the ADRs and the code
were cross-checked where they differ, see "Discrepancies" below).

## Commands actually run, with their real output

All commands below were run in the worktree, on the reference machine (Arch, Plasma 6.7.5,
Wayland, `uv` 0.12.23, Python 3.14.7), on 2026-10-10. **No microphone was opened, no
keystroke was emitted and the real clipboard was never touched**: every command below is
read-only or hashes existing files. `ges2t toggle`/`ptt-start`/`ptt-stop`/`cancel` were
deliberately **not** run (they would record and type).

### Setup (README section 4)

```console
$ uv venv --system-site-packages --python /usr/bin/python3
Using CPython 3.14.7 interpreter at: /usr/bin/python3
Creating virtual environment at: .venv

$ uv sync
 … + typing-extensions==4.16.0            # (tail of the resolver output)

$ ls -l .venv/bin/ges2t
-rwxr-xr-x 1 matee matee 374 Oct 10 10:32 .venv/bin/ges2t

$ .venv/bin/ges2t --version
0.1.0

$ .venv/bin/python -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk; print('ok')"
ok
```

This verifies the README's `--system-site-packages` step, the resulting absolute console
script path used in the KDE binding section, and the `gi` check.

### The model (README section 6)

```console
$ uv run ges2t models
artifact       version  status      path
whistle.cact   2.0.0    present     /home/matee/.local/share/ges2t/models/whistle/2.0.0/whistle.cact
libneedle.so   3.2.0    present     /home/matee/.local/share/ges2t/engine/3.2.0/libneedle.so

$ uv run ges2t models --verify
artifact       version  status      path
whistle.cact   2.0.0    verified    /home/matee/.local/share/ges2t/models/whistle/2.0.0/whistle.cact
libneedle.so   3.2.0    verified    /home/matee/.local/share/ges2t/engine/3.2.0/libneedle.so
```

### The daemon and the permission (README section 5)

```console
$ pacman -Ql ydotool | grep -i service
ydotool /usr/lib/systemd/user/ydotool.service

$ systemctl --user status ydotool.service --no-pager | head -4
● ydotool.service - Starts ydotoold service
     Loaded: loaded (/usr/lib/systemd/user/ydotool.service; enabled; preset: enabled)
     Active: active (running) since Fri 2026-10-09 14:08:11 CEST; 20h ago
   Main PID: 3339542 (ydotoold)

$ ls -l /dev/uinput
crw-rw----+ 1 root input 10, 223 Oct  9 14:08 /dev/uinput

$ pacman -Q ydotool python-gobject gtk3 libappindicator libxkbcommon xkeyboard-config wl-clipboard pipewire
ydotool 1.0.4-2
python-gobject 3.56.3-1
gtk3 1:3.24.52-1
libappindicator 12.10.1-2
libxkbcommon 1.13.2-1
xkeyboard-config 2.48-1
wl-clipboard 1:2.3.0-1
pipewire 1:1.6.8-1
```

These are the exact output shapes quoted in README section 5. The `enable --now` command
itself was **not** run: the unit is already enabled and running, and re-running it would
have changed nothing while proving nothing. The README states that the check is
`systemctl --user is-active ydotool.service`, which was verified.

### `ges2t doctor` (README section 11)

```console
$ uv run ges2t doctor ; echo EXIT=$?
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
EXIT=0
```

This report is what the README reproduces as its worked example; the two `WARN` lines are the
expected warnings the README names (tray-failures unreadable from outside the service, and
the always-manual hotkey binding). Exit 0 with no `FAIL` is the healthy state.

**Noise observed on stderr, from a dependency, not from ges2t:**

```console
/home/matee/.../.venv/lib/python3.14/site-packages/pystray/_util/gtk.py:53: SyntaxWarning: 'return' in a 'finally' block
  return False
```

`pystray` 0.19.5 emits a `SyntaxWarning` under Python 3.14 the first time its GTK module is
imported (which the tray-backend check does). It is harmless, it is third-party, and it
appears before the report. Reported here because it can look like a ges2t problem to a user;
it was **not** fixed (no source changes are in scope).

### `--help` output (used verbatim in README sections 7 and 11)

```console
$ uv run ges2t --help
… (9 subcommands, and the epilog naming the KDE binding path)

$ uv run ges2t toggle --help
usage: ges2t toggle [-h] [--config PATH] [--debug]
… Binding (KDE): System Settings -> Shortcuts -> Add -> Command or URL, then
enter the ges2t command shown above. True press-and-hold (ptt-start/ptt-stop)
works only when the compositor can fire separate key-press and key-release
commands; if it cannot, bind 'ges2t toggle' instead. ges2t never edits your
compositor settings or your config file.

$ uv run ges2t --version
0.1.0
```

### The documented failure paths (README section 11)

```console
$ uv run ges2t status
idle

$ XDG_RUNTIME_DIR=/tmp/ges2t-fake-runtime uv run ges2t status ; echo EXIT=$?
ges2t: the ges2t service is not running (no answer from /tmp/ges2t-fake-runtime/ges2t.sock: [Errno 2] No such file or directory); start it with 'ges2t run'
EXIT=2
```

The second invocation is how the "service is not running → exit 2" row was verified without
stopping the user's running service (a fake `XDG_RUNTIME_DIR`, no side effects).

```console
$ printf '[streaming]\nenabled = true\n\n[engine.whistle]\nwindow_seconds = 9\n\n[typo]\nnope = 1\n' > /tmp/ges2t-doctest/config.toml
$ uv run ges2t doctor --config /tmp/ges2t-doctest/config.toml
Config warnings           WARN  2 unknown key(s) were ignored: … unknown key 'typo'; engine.whistle.window_seconds = 9.0 is too large for the 1 s latency budget: measured per-chunk latency crosses 1 s above about 6 seconds of buffered audio, so this knowingly trades latency for context

$ printf '[audio]\ndevice = 5\n' > /tmp/ges2t-doctest/bad.toml
$ uv run ges2t doctor --config /tmp/ges2t-doctest/bad.toml
Config valid              FAIL  invalid configuration: /tmp/ges2t-doctest/bad.toml: key 'audio.device': expected str, got int (5)
                                fix: fix the file named above, or pass a different one with '--config <path>'
```

Both message shapes are quoted in the README's unknown-key and invalid-config rows.

### The systemd unit (README section 8, `docs/setup/autostart.md`)

```console
$ systemd-analyze --user verify docs/setup/ges2t.service ; echo EXIT=$?
EXIT=0
```

No output and exit 0: the unit parses, its dependency names resolve, and `ExecStart` points at
an executable that exists. The unit was **not** copied to `~/.config/systemd/user/` and
**not** enabled: doing so would have fought the user's already-running service for the
socket, which is exactly the clash the unit's own comment warns about.

The environment the unit's tray would need was verified read-only:

```console
$ systemctl --user show-environment | grep -E 'WAYLAND_DISPLAY|XDG_CURRENT_DESKTOP|DBUS'
DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
WAYLAND_DISPLAY=wayland-0
XDG_CURRENT_DESKTOP=KDE

$ systemctl --user is-active graphical-session.target
active
```

### Repo checks (the ticket's quality bar)

```console
$ timeout 300 uv run pytest -q
904 passed in 5.96s
PYTEST_EXIT=0

$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 53 source files
MYPY_EXIT=0

$ python3 -c "import re,pathlib; tok=re.compile(r'(?<![\\w/\`.])((?:\.\./)+[\\w./-]+?\.md)'); bad=[(str(f),m.group(1)) for f in sorted(pathlib.Path('docs').rglob('*.md')) for m in tok.finditer(f.read_text()) if not (f.parent/m.group(1)).resolve().exists()]; print(f'{len(bad)} broken references')"
0 broken references
```

The mypy command is the project's amended one (`src/ges2t tests`, not the ticket DoD's
`src/ges2t`); both are green. The link check covers `docs/**/*.md`; the README's own
`docs/...` links (which do not start with `../`, so the project's checker ignores them) were
validated with an equivalent script over `README.md` and `docs/setup/autostart.md`, which is
how a wrong `0009-configuration-is-toml.md` filename was caught and fixed **before** the
first commit. Final state: 0 missing targets in both.

## The doctor-check ↔ README comparison (DoD item 4)

Every check `build_report` runs, in report order, with where the README covers it. `FAIL`
checks are marked; the rest can warn or succeed. All 22 are covered.

| # | doctor check (`src/ges2t/doctor.py`) | README coverage | tick |
|---|---|---|---|
| 1 | Package imports — **FAIL**: a module will not import | §4 install + repair | [x] |
| 2 | Architecture — **FAIL**: no pinned engine for this platform | §3 `x86_64` only | [x] |
| 3 | Model present — **FAIL**: `whistle.cact` missing/wrong size | §6 `ges2t setup`; §11 "model or engine is missing" | [x] |
| 4 | Model verified — **FAIL** (`--verify`): sha256 mismatch | §6 `models --verify`; §11 "does not match the pinned sha256" | [x] |
| 5 | Engine present — **FAIL**: `libneedle.so` missing/wrong size | §6; §11 | [x] |
| 6 | `cactus-needle` importable — **FAIL** | §3 requirements, §4 `uv sync` | [x] |
| 7 | Engine/package versions — WARN on pin skew | §3 (pin), §11 doctor table | [x] |
| 8 | Config valid — **FAIL**: TOML/type error | §10 (invalid config is a hard error); §11 "Config valid" row | [x] |
| 9 | Engine config valid — **FAIL**: `[engine.whistle]` out of range | §10 `window_seconds`/`chunk_seconds` traps | [x] |
| 10 | Injection config valid — **FAIL**: `[injection.<backend>]` | §10 both injection blocks | [x] |
| 11 | Config warnings — WARN: unknown keys | §10 (tolerated but surfaced); §11 unknown-keys row | [x] |
| 12 | Audio device — WARN when absent, **FAIL** if syntactically invalid | §10 `audio.device`; §11 "nothing at all, for a short or quiet press" | [x] |
| 13 | `pw-record` present — **FAIL** | §3 requirements table (`pipewire`) | [x] |
| 14 | PipeWire session — **FAIL** | §3; §11 doctor table | [x] |
| 15 | `ydotoold` — **FAIL**: daemon unreachable | §5 the whole section; §11 first row | [x] |
| 16 | `/dev/uinput access` — **FAIL**: not writable | §5 `input` group + re-login; §11 first row | [x] |
| 17 | Tray backend — **FAIL**: not AppIndicator | §4 `--system-site-packages`; §11 "tray icon is invisible" | [x] |
| 18 | Tray failures — WARN without a provider | §9/§11 tray-failure rows; `docs/setup/autostart.md` §4 journal | [x] |
| 19 | Service socket — **FAIL**: stale socket; also `ges2t run` clash | §8 one service per session; §11 clash + stale rows | [x] |
| 20 | Keyboard layout — **FAIL** when unresolvable/not compilable | §10 layouts; §11 "wrong characters on a non-US layout" | [x] |
| 21 | Clipboard backend — **FAIL**: `wl-copy` missing when selected | §10 clipboard block; §11 clipboard rows | [x] |
| 22 | Hotkey bindings — always WARN | §7 the whole section | [x] |

Every documented step is *checkable*: install → checks 1, 6, 17; the daemon and permission →
15, 16; the model → 3–5; the KDE binding → 22; the config → 8–11; the backend choice → 20,
21; running it → 19.

## Verification by a fresh reader (DoD item 5): what was and was not possible

The machine is **not** a clean state — it is the fully configured reference machine with a
service already running — and several steps are forbidden to this agent (no recording, no
keystrokes, no clipboard) or impossible for it (the Plasma settings GUI). The sequence below
records exactly what was executed and what was replaced by inspection, in the order a reader
would hit it.

| README step | What was done | Result |
|---|---|---|
| 4. `uv venv --system-site-packages` + `uv sync` | **executed**, in this fresh worktree with no pre-existing `.venv` | passed; `.venv/bin/ges2t` created; `import gi` prints `ok` |
| 5. `systemctl --user enable --now ydotool.service` | **not executed** (already enabled and running); state **inspected** with `systemctl --user status` and `pacman -Ql` | unit is `ydotool.service`; `active (running)` |
| 5. `usermod -aG input "$USER"` + re-login | **not executed** (would modify the user's groups); `/dev/uinput` writability **inspected** (`ls -l`, `test -w`) | `crw-rw----+ root:input`, writable by this user |
| 6. `uv run ges2t setup` | **not executed** (the only network command, and the artifacts are already present and verified); the check commands **were executed** | `models` → `present`, `models --verify` → `verified` |
| 7. Bind a key in System Settings | **not executed** (Plasma settings GUI; also its own kind of consequential change) | the dialog wording is marked **(unverified)** in the README |
| 8. `uv run ges2t run` | **not executed** (the user's service already holds the socket) | the clash message and the unit's behaviour are quoted from the code |
| 8. systemd unit | `systemd-analyze --user verify` **executed**; install/enable **not executed** | valid unit, exit 0; tray environment **inspected** via `show-environment` |
| 9. Dictate | **not executed** (forbidden: it would record the microphone and type) | the three behaviours are quoted from code, from ADR 0011's amendment and from ticket 34's user-run live test |
| 11. `uv run ges2t doctor`, `doctor --verify` | **executed** (doctor and `models --verify`) | 22 checks, 0 failures, exit 0 |
| 11. failure paths | **executed** with a fake `XDG_RUNTIME_DIR` and temporary bad configs | exit 2; unknown-key WARN; type-error FAIL, all as documented |

**Places where the existing instructions were wrong, missing or out of order, now fixed in the
README:**

1. **The binding command.** Every pre-existing instruction (`doctor`'s `HOTKEY_FIX`,
   `KDE_BINDING_SECTION`, and `cli.py`'s `KDE_SHORTCUT_HINT`) says to bind `ges2t toggle`.
   Taken literally in KDE that binds a command that is **not on `PATH`** — the console script
   lives in `.venv/bin/` — and that has no working directory for `uv run`. README §7
   therefore gives the **absolute** `.venv/bin/ges2t` path and says explicitly why `uv run` is
   not a shortcut command. This is a real gap in the user-facing instructions, reported as a
   finding below rather than fixed in code.
2. **`ydotoold` vs `ydotool.service`.** The unit to enable is `ydotool.service`
   (verified with `pacman -Ql`); only the *binary* is `ydotoold`. README §5 uses the unit name
   throughout.
3. **`mypy` scope.** The scaffold README documented `uv run mypy src/ges2t`; the project's
   rule was amended to `src/ges2t tests` (ADR 0019, ticket 20). README §4/§12 uses the
   amended command.
4. **Incremental typing had no place in the old README at all.** It is the default behaviour
   and the single most surprising thing a new user will meet, so it is now stated in §9, its
   consequence (a confidently-wrong word can land mid-dictation) in §9 and §10, and its
   missing opt-out in §10 and §13.
5. **Ordering.** The old README put development prerequisites first; the DoD's order is
   user-first (what it is → status → requirements → install → …), which is what the README
   now follows, with the developer commands folded into §12 rather than interrupting setup.

## Claims that could NOT be verified, and why

Marked `(unverified)` in the README, and listed here with the reason:

1. **The exact wording of Plasma 6.7.5's *System Settings → Shortcuts → Add → Command or
   URL* dialog.** The step needs the GUI; this agent cannot drive it, and it is a
   consequential user-side change. The command form and the recommended binding come from
   ADR 0004's decision text and `ges2t <cmd> --help`.
2. **Whether Plasma 6.7.5 can fire separate key-press and key-release commands for a custom
   shortcut** (true press-and-hold with `ptt-start`/`ptt-stop`). No ticket in the repository
   ever verified it, and it cannot be verified without the GUI. The README therefore
   recommends `toggle`, which needs no such feature, and says so as ADR 0004 does.
3. **The systemd unit actually running ges2t**, and its tray inside a systemd-started
   process. Enabling it would have competed with the user's running service for
   `$XDG_RUNTIME_DIR/ges2t.sock`. The unit was validated with
   `systemd-analyze --user verify` (exit 0), and the environment prerequisite
   (`WAYLAND_DISPLAY`, `XDG_CURRENT_DESKTOP`, `DBUS_SESSION_BUS_ADDRESS` in
   `systemctl --user show-environment`) was inspected, but process-level behaviour under
   systemd is unverified.
4. **A fresh `ges2t setup` download.** The artifacts are present and verified on this
   machine, so `setup` would have been a no-op; forcing a re-download would have required
   deleting the user's verified artifacts. The pin values (URLs, sha256, sizes) were read
   from `src/ges2t/model_store.py` and the idempotent *check* path was exercised through
   `models`/`models --verify`.
5. **The tray icon's overflow-area behaviour.** "Plasma hides new AppIndicator icons in the
   tray's expanded area" is user-facing guidance, not something this ticket measured; the
   *cause* of a genuinely invisible tray that this project did hit is different and is
   covered exactly (a venv without `--system-site-packages` → `pystray` silently selects
   `_xorg` → ADR 0013 → `doctor` **Tray backend** FAIL).
6. **Notification popups.** No notification was triggered (that needs a failing dictation);
   the `notify-send` argv, the 5-second suppression window and the best-effort contract were
   read from `src/ges2t/notify.py`.
7. **Non-KDE compositors and non-Arch distributions.** Stated as unverified support in
   §2/§3/§13, per ADR 0002.

## Code defects and discrepancies the documentation work exposed

No source file was changed. These are reported, not fixed:

1. **`doctor`'s hotkey fix line and the CLI hint recommend a command that will not work from
   a KDE shortcut on a normal install.** `HOTKEY_FIX` / `KDE_BINDING_SECTION`
   (`src/ges2t/doctor.py`) and `KDE_SHORTCUT_HINT` (`src/ges2t/cli.py`) all print
   `ges2t toggle`, but `uv sync` installs the console script into `.venv/bin/`, which is not
   on the session `PATH` used by a shortcut, and the shortcut has no working directory for
   `uv run`. The advice is correct in intent and misleading in form. This is the documentation
   ticket discovering a user-facing instruction defect; a follow-up could print
   `sys.argv[0]`'s absolute path (or `<venv>/bin/ges2t`) in those strings.
2. **The ticketed `mypy` command disagrees with the project's own rule.** Ticket 19's last
   checkbox says `uv run mypy src/ges2t`; ADR 0019 (amended) and the board say
   `uv run mypy src/ges2t tests`. Both pass today, so nothing is broken — but the ticket text
   is stale relative to the rule it cites.
3. **`pystray` 0.19.5 emits a `SyntaxWarning` on Python 3.14** the first time the tray
   backend is imported (`SyntaxWarning: 'return' in a 'finally' block`,
   `pystray/_util/gtk.py:53`), so it precedes the first `ges2t doctor` report a user sees. It
   is third-party and harmless, but it reads like a ges2t failure. Not fixed (dependency).
4. **Board/state observation, not a defect:** the board lists ticket 31
   (`speech-gate-recalibration`) as `blocked`/parked, while its recalibrated gate **is on
   `main`** (`e1134f8` is an ancestor of this branch's base `d34d789`; the "park" commit
   `23da79d` changed only `ticket.md`). The live speech gate therefore behaves as
   recalibrated even though the ticket is not `done`. The README documents the *behaviour*
   (silence types nothing, per ADR 0012) without claiming the recalibration's live-test
   status, because that status is the ticket's business, not a user fact.
5. **The clipboard backend's "no fallback" and the streaming refusal are both load-bearing,
   and the second is a startup error rather than a warning.** The README states both loudly
   because a user who sets `backend = "clipboard"` and leaves `streaming.enabled = true`
   meets only the error message; there is no README-visible hint until then. (The error and
   its hint are actionable, so this is a documentation emphasis, not a code gap.)

## Things an orchestrator should know

- **Ticket 19's two harness commands pass**: `uv run pytest -q` → **904 passed**, exits;
  `uv run mypy src/ges2t tests` (caches cleared) → 0 errors. The link check reports
  **0 broken references**.
- **The README is the only install path** (ADR 0000), and it now names the exact Arch
  packages, the `ydotool.service` unit name, the `input`-group requirement, the
  `--system-site-packages` trap, the only network command, the KDE binding (with the
  absolute-path gotcha) and the doctor checks. The four behaviours that would otherwise be
  mistaken for bugs — incremental typing as the default with no streaming-preserving
  opt-out, the clipboard/streaming refusal, the `ctrl+shift+v` default and its unbound-chord
  failure, and `key_delay_ms = 0` — are all stated where a reader will meet them.
- **The end-to-end fresh-machine walkthrough could not be completed on this machine** (it is
  already set up, and recording/keystrokes/clipboard are forbidden to the agent): the
  executed sequence and every substitution by inspection are in the table above. If the
  orchestrator wants that leftover verification closed, it belongs to the user as a live
  test, exactly like ticket 34's.
- **Two things for a possible follow-up ticket:** the misleading `ges2t toggle` binding
  advice (finding 1) and the ticket-31 board/code mismatch (finding 4).
