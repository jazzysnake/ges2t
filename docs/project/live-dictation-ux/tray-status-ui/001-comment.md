# 001 — Tray status UI with the mandatory AppIndicator assertion

Ticket: `ticket.md` (`tray-status-ui`) · Branch: `feat/tray-status-ui` ·
Worktree: `.worktrees/tray-status-ui` · Status: **in review**

## What was built

- `src/ges2t/ui/__init__.py` — package docstring; nothing is imported at package
  import time.
- `src/ges2t/ui/status.py` — `NullStatusUI` (moved out of `pipeline.py`), the shared
  `StatusState` enum, the `TrayFailure` record, and the `StoppableStatusUI` /
  `DiagnosticStatusUI` Protocols.
- `src/ges2t/ui/tray.py` — `TrayStatusUI`, `TrayBackendError`,
  `assert_appindicator_backend()`, `build_tray_status_ui()`, and the `TrayIcon`
  Protocol that makes the failure handling testable without a display.
- `src/ges2t/pipeline.py` — `build_status_ui()` (tray or no-op, fall back with a
  warning), and `run_service` now builds the configured status UI when none is
  injected and stops/reports it on exit.
- `tests/test_status_ui.py`, `tests/test_tray_backend.py` — 22 new tests.
- `tests/test_pipeline.py` — only the moved `NullStatusUI` import and a
  `build_status_ui` monkeypatch in the two `run_service` tests (see below).

No other module changed. `gi` and `pystray` are imported **only inside**
`TrayStatusUI.start()` / `assert_appindicator_backend()` / `_create_icon()`;
`import ges2t.cli`, `import ges2t.pipeline`, `import ges2t.ui.tray` and
`import ges2t.ui.status` pull in none of `gi`, `pystray`, `PIL` (pinned by a new
fresh-interpreter test).

## The backend assertion — the point of the ticket

`pystray` resolves its backend **at import time** in `pystray/__init__.py`:
`backend()` tries `_appindicator`, then `_gtk`, then `_xorg`, and binds
`Icon = backend().Icon`. Under a venv without system site packages the first two
fail with `ModuleNotFoundError: No module named 'gi'` and it silently binds
`pystray._xorg`, which Plasma 6.7.5 cannot display.

`assert_appindicator_backend()` therefore makes two checks, in order:

1. `import gi; gi.require_version("AppIndicator3", "0.1")` — proves `python-gobject`
   and the `libappindicator` typelib are visible. `gi.require_version` raises
   `ValueError: Namespace AppIndicator3 not available` when the typelib is missing,
   and `import gi` raises `ModuleNotFoundError` in a venv built without
   `--system-site-packages`.
2. `import pystray` and inspect `pystray.Icon.__module__`. The class pystray binds
   records which backend won the import-time selection; the assertion requires
   exactly `"pystray._appindicator"`. This is the technique the earlier probe used
   and it is deliberate: `pystray` deletes its `backend` function, so inspecting
   the resolved `Icon` class is the only clean way to see the decision.

Both failures raise `TrayBackendError` with the three Arch packages and the
`--system-site-packages` requirement named. `assert_appindicator_backend()` runs
**before** `_create_icon()`, so a wrong backend never produces an icon at all
(ADR 0013). `build_status_ui()` catches `TrayBackendError` (and any other icon
construction failure), prints a two-line warning, and returns `NullStatusUI()`, so
a missing tray never makes ges2t unusable.

### Real-machine evidence for the technique

The assertion passes on this machine and resolves to AppIndicator (no icon is
created by this check — it only imports):

```
$ uv run python -c "
from ges2t.ui.tray import assert_appindicator_backend
import pystray, gi
assert_appindicator_backend()
print('assertion OK; pystray.Icon module =', pystray.Icon.__module__)
"
assertion OK; pystray.Icon module = pystray._appindicator
```

Forcing pystray to select the invisible backend is rejected with the actionable
message (this imports `_xorg` and opens an X connection; it creates no window, no
icon, no tray):

```
$ PYSTRAY_BACKEND=xorg uv run python -c "
from ges2t.ui.tray import assert_appindicator_backend, TrayBackendError
try:
    assert_appindicator_backend()
except TrayBackendError as exc:
    print(str(exc))
"
refusing to start a tray that Plasma 6 cannot display: pystray selected the
'pystray._xorg' backend instead of the AppIndicator one. Install the Arch
packages 'python-gobject', 'gtk3' and 'libappindicator' and create the
virtualenv with 'uv venv --system-site-packages --python /usr/bin/python3' so
'gi' is importable, then run 'ges2t doctor' (ADR 0013).
```

## The observability mechanism (the new DoD bullet)

Ticket 21 made `service.py` contain every `StatusUI` callback exception so a
broken display can never wedge the state machine. That is right for safety and
left the hole ticket 12/17 call out: a tray that raised on every call would just
stop updating. `TrayStatusUI` is therefore responsible for its own visibility:

- Every display operation goes through `_guard(action, operation)`. `_guard`
  catches the exception, increments `self._failures`, stores
  `self._last_failure = "<action>: <exc>"`, and prints the first occurrence of
  each **distinct** message to stderr (identical repeats only bump the count, so a
  wedged service cannot flood the log).
- `failure_count`, `last_failure` and `failure_report() -> TrayFailure | None`
  expose the record. `TrayFailure.summary()` renders exactly
  `the tray has failed N times: <last error>` — the sentence `ges2t doctor`
  and/or `ges2t status` are to print.
- `TrayStatusUI` implements `status.DiagnosticStatusUI`, and `run_service` calls
  `_shutdown_status_ui` on every exit path, which prints the summary if any
  failure was recorded. So the failure is visible on stderr immediately **and**
  reported at shutdown, even though `doctor` (ticket 12) is not implemented yet
  and `status` is a separate process.
- Menu command failures are recorded the same way (a `ges2t toggle` sent while
  the socket is down must not kill the GTK callback or vanish).

The service guard stays as the outer safety net; this is an inner, reporting
guard. Tested with an injected broken icon and no display (see below).

## How `run_service` injects and falls back

```python
ui = status_ui if status_ui is not None else build_status_ui(config)
```

- Explicitly injected UI always wins (the headless-test seam).
- `config.ui.tray = false` → `NullStatusUI`.
- otherwise `build_status_ui` builds and **starts** the tray; on
  `TrayBackendError` (or any construction failure) it prints the warning and
  returns `NullStatusUI`.
- `run_service` stops a stoppable UI and prints a diagnostic UI's failure report
  on every exit path (startup failure and serve failure included).

Menu actions: `Toggle dictation` sends `toggle` and `Cancel` sends `cancel`
through `ges2t.socket_proto.send_command` on the default socket path — the same
client the CLI uses, so no service logic is reimplemented. `Quit` has no socket
verb (the ADR 0016 verb set is closed), so it requests the process shutdown the
service already handles cleanly: `os.kill(os.getpid(), SIGTERM)`. It is injected
as a `Callable[[], None]`, so the test exercises it without signalling the test
process. Ctrl-C and every CLI command remain the primary paths; nothing is
reachable only through the tray.

## Verification (real output)

Baseline on this branch before the work (from `git stash`, HEAD `33413b3`):
`uv run pytest -q` → **515 passed**; `uv run mypy src/ges2t tests` →
**34 source files**.

```
$ uv run pytest tests/test_status_ui.py tests/test_tray_backend.py -q
......................                                                   [100%]
22 passed in 0.59s

$ uv run pytest -q
........................................................................ [ 93%]
.................................                                        [100%]
537 passed in 2.25s

$ uv run mypy src/ges2t tests
Success: no issues found in 39 source files
```

`mypy` covers `tests/` (ADR 0019 amendment). The 5 extra source files are the two
new test files, `src/ges2t/ui/__init__.py`, `src/ges2t/ui/status.py` and
`src/ges2t/ui/tray.py`.

## Falsification evidence (every new test was made to fail)

Mutation harness in `/tmp` (not committed): it applies one textual mutation at a
time, runs the single test that guards that behaviour, records the result, and
restores the file. It also hashes the three touched sources before and after and
reports `files restored: True`. Result: **21/21 mutations made the guarded test
fail**.

```
M1  failure_count never increments                -> test_tray_records_and_exposes_injected_failures
M2  failure is never printed                      -> test_tray_records_and_exposes_injected_failures
M3  guard re-raises instead of recording           -> test_tray_records_and_exposes_injected_failures
M4  partial does not update the display            -> test_tray_partial_updates_the_tooltip_without_typing
M5  recording maps to the idle state               -> test_tray_maps_every_state_onto_the_tooltip
M6  state update skips the tooltip                 -> test_tray_maps_every_state_onto_the_tooltip
M7  stop does not stop the icon                    -> test_tray_stop_stops_the_icon
M8  toggle sends cancel                            -> test_tray_menu_actions_use_the_socket_client...
M9  quit does not request shutdown                 -> test_tray_menu_actions_use_the_socket_client...
M10 failure_report always returns None             -> test_tray_records_and_exposes_injected_failures
M11 StatusState.IDLE drifts from ServiceState      -> test_status_state_mirrors_the_service_state_values
M12 NullStatusUI.error is silent                   -> test_null_status_ui_error_writes_to_stderr
M13 backend assertion is a no-op                   -> test_assertion_rejects_the_xorg_backend...
M14 assertion drops the resolved-backend check     -> test_assertion_rejects_the_xorg_backend...
M15 assertion requires the wrong typelib           -> test_assertion_passes_with_fake_gi...
M16 error message omits the packages               -> test_assertion_rejects_the_xorg_backend...
M17 build_status_ui ignores ui.tray = false        -> test_build_status_ui_is_null_when_the_tray_is_disabled
M18 build_status_ui re-raises instead of falling back -> test_build_status_ui_falls_back_with_a_warning...
M19 run_service ignores build_status_ui            -> test_run_service_builds_the_status_ui_when_none_is_injected
M20 pystray imported at tray module import time    -> test_importing_a_gui_free_module... [ges2t.ui.tray]
M21 gi imported at tray module import time         -> test_importing_a_gui_free_module... [ges2t.ui.tray]
files restored: True
```

M17 is worth recording: the first version of the test monkeypatched
`build_tray_status_ui` to raise `AssertionError`, and it still passed under the
mutation because `build_status_ui`'s broad fallback swallowed the
`AssertionError`. The test now records whether the builder was called and asserts
`called == []`, which fails correctly under M17.

The real assertion was also falsified on the machine with `PYSTRAY_BACKEND=xorg`
(above): the wrong backend is rejected with the exact message.

## Manual end-to-end — user-owned, NOT run by this agent

This needs the user's desktop session (and, for the dictation step, a microphone
and a focused window). This agent did **not** start a tray, a terminal or any GUI
window. Everything below is copy-pasteable; run it from
`/home/matee/Workspace/Personal/ges2t/.worktrees/tray-status-ui`.

**Prerequisite:** the venv must be the system-site-packages one
(`uv venv --system-site-packages --python /usr/bin/python3 && uv sync`), and the
Arch packages `python-gobject`, `gtk3`, `libappindicator` installed (all already
true on the reference machine).

### 1. The icon is really visible on Plasma 6.7.5, and steps through the states

Terminal A:

```bash
uv run ges2t run
```

Expected: the service starts and a small round icon appears in the Plasma system
tray. The tooltip (hover) reads `ges2t: idle`. The icon is a grey circle in the
idle state; it is **not** a running process with no icon.

Terminal B, with any focused text field, dictate a short phrase:

```bash
uv run ges2t toggle     # tooltip -> "ges2t: recording" (red circle)
# say "the quick brown fox", then:
uv run ges2t toggle     # tooltip -> "ges2t: transcribing" (amber), then back to idle (grey)
uv run ges2t status     # expect: idle
```

**Record:** did the icon appear at all, and did the colour/tooltip follow
`idle -> recording -> transcribing -> idle`? Include a screenshot of the tray or
a precise description (shape/colour/position).

### 2. The menu items work

Click the tray icon (or right-click) and choose each item:

- **Toggle dictation** — same as `ges2t toggle`.
- **Cancel** — starts nothing and aborts a recording; nothing is typed.
- **Quit** — the `ges2t run` process exits cleanly (terminal A returns to the
  prompt), and the icon disappears.

### 3. A broken backend falls back loudly (no tray at all, not an invisible one)

This is the heart of ADR 0013. The simplest, no-`sudo` way to make pystray select
the backend Plasma cannot display is to force it:

```bash
PYSTRAY_BACKEND=xorg uv run ges2t run
```

Expected (exact two lines before the service continues):

```
ges2t: refusing to start a tray that Plasma 6 cannot display: pystray selected
the 'pystray._xorg' backend instead of the AppIndicator one. Install the Arch
packages 'python-gobject', 'gtk3' and 'libappindicator' and create the virtualenv
with 'uv venv --system-site-packages --python /usr/bin/python3' so 'gi' is
importable, then run 'ges2t doctor' (ADR 0013).
  the tray is disabled; ges2t remains fully usable from the CLI
```

No icon should appear, and `uv run ges2t toggle` / `status` must still work from
terminal B. Stop it with Ctrl-C. (The venv-without-system-site-packages route
exercises the same missing-`gi` branch and is covered by the fake-module tests;
`PYSTRAY_BACKEND=xorg` is the lighter manual check and was verified on this
machine.)

### 4. A tray failure is reported, not silent (optional, already unit-tested)

The unit tests inject a broken icon and assert the count/last error and the
stderr line. On a real run the equivalent visible signal is: if the tray starts
but a display update ever fails, the terminal prints
`ges2t: the tray has failed N times: state update: ...` on the first occurrence,
and the same summary again when the service exits.

The user should paste the observations back here so the manual DoD checkbox can be
ticked. This agent may not start the tray itself.

## For the orchestrator / reviewer

- The manual tray-visibility DoD checkbox is intentionally **left unticked**; it
  is user-owned and cannot be completed by an agent without taking over the
  desktop session. Everything else is done and verified.
- `NullStatusUI` moved to `ges2t.ui.status`; the only consumer test updated is
  `tests/test_pipeline.py` (one import + two `build_status_ui` monkeypatches). No
  behaviour of ticket 11's tests changed.
- Ticket 12 (`doctor`): its tray check should call
  `ges2t.ui.tray.assert_appindicator_backend()` (or the same two checks) and print
  the same message. The runtime failure count is available from
  `TrayStatusUI.failure_report()` for a future `status`/`doctor` wire-up; today it
  is printed to stderr immediately and again at shutdown.
- Ticket 15/16 (streaming) and ticket 18 (notifications): `TrayStatusUI.partial()`
  is wired and updates the tooltip only; it never types (ADR 0011). `error()`
  currently changes the icon/tooltip but does not raise a desktop notification —
  that is ticket 18's job.
- No keystrokes were injected, no `ydotool`, no clipboard, no `sudo`, no service
  change, no network, no GUI window were used. No ADR changed.
