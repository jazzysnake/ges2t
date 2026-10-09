# 001 — `NotifyingStatusUI`: actionable popups, one per message per window

Ticket: `ticket.md` (`notifications-and-cancel`) · Branch: `feat/notifications-and-cancel` ·
Worktree: `.worktrees/notifications-and-cancel` · Status: **in review**

Commits: `cae7f59` (wrapper + wiring + tests), `e992346` (dedup tests hardened against
the stale-constant trap).

## What was built

- `src/ges2t/notify.py` — **new**. `NotifyingStatusUI`, `NotificationRunner` (a Protocol),
  `run_notify_send` (the default runner), `NotificationError`, and the module constants
  (`SUPPRESSION_WINDOW_SECONDS = 5.0`, fixed app name, summary, urgency).
- `src/ges2t/pipeline.py` — `_apply_notifications(config, ui)` and one changed line in
  `run_service` (`ui = _apply_notifications(config, base_ui)`); module/`run_service`
  docstrings updated. The public `run_service` signature is **unchanged**
  (`config, status_ui=None`), so ticket 16's `test_the_public_run_service_signature_is_unchanged`
  still holds.
- `tests/test_notify.py` — **new**, 22 tests, all with fake runners and an injected clock.
- `tests/test_pipeline.py`, `tests/test_tray_backend.py` — the two existing tests that
  asserted the raw status UI reaches the service/startup now account for the wrapper
  (see "Existing tests touched" below). No test deleted, skipped, or weakened.

`notify.py` imports only stdlib (`logging`, `subprocess`, `time`), `ges2t.interfaces` and
`ges2t.ui.status`. It pulls in **no** `gi`/`pystray`/PIL, so the GUI-free import guarantee
(ticket 17, ADR 0014) is intact — the full suite's
`test_importing_a_gui_free_module` still passes.

## The wrapper is pure — every call forwards (ticket 17 must not regress)

`NotifyingStatusUI` is composition, not inheritance. `idle`, `recording`, `transcribing`,
`partial` and `error` each call the wrapped UI **unchanged**; only `error` does anything
else. `partial` deliberately never reaches the notification path (ADR 0011).

It also forwards the two **lifecycle** members `run_service` uses, when the wrapped UI
implements them:

```python
def stop(self) -> None:
    if isinstance(self._wrapped, StoppableStatusUI):
        self._wrapped.stop()

def failure_report(self) -> TrayFailure | None:
    if isinstance(self._wrapped, DiagnosticStatusUI):
        return self._wrapped.failure_report()
    return None
```

This matters: `_shutdown_status_ui(ui)` receives the wrapper, and without the delegation a
wrapped `TrayStatusUI` would never be stopped and its `failure_report()` would never be
printed — a silent regression of ticket 17's observability fix. `stop()` on a wrapper
around a bare `NullStatusUI` is a no-op and `failure_report()` returns `None`. A read-only
`wrapped` property exposes the inner UI (used by the wiring tests).

`run_service` keeps a single `_shutdown_status_ui(ui)` call and passes it the wrapper; the
delegation makes that correct.

## The dedup rule (chosen and pinned)

**At most one notification per distinct message text per `SUPPRESSION_WINDOW_SECONDS`
(5.0 s, module constant — not a config key).**

Implementation: `self._last_notified: dict[str, float]` maps the message to the monotonic
time of its last **attempt**. On `error(message)`, the wrapper notifies iff the message has
no entry or its entry is at least the window old; the attempt is recorded *before* the
runner runs; stale entries are pruned so the map stays bounded.

Why this rule, over the alternatives:

- **Keyed on the message text, never on a state.** Two different errors each get their own
  popup — the ticket's Notes require exactly this.
- **Per message, not "remember only the last one".** The obvious cheaper rule (one
  `_last_message` slot) still suppresses the common "hammer the key" case, but it
  re-notifies `A` when `B` happened in between even though `A` was sent seconds ago. The
  per-message map keeps the promise "the same error repeated within a few seconds sends one
  notification" literally true. `test_interleaved_identical_errors_inside_the_window_stay_suppressed`
  (A, B, A → exactly `[A, B]`) pins this and is falsified by the last-message-only mutation
  (harness M10).
- **One popup per message**, not a rate limiter that drops *any* second error in the window:
  a distinct failure is new information and must not be hidden (ADR 0013's "fail loudly"
  and the epic's "actionable notifications" both point this way).
- **The attempt, not success, is timestamped**, so a dead notification daemon is not
  hammered once per keypress either.

## What stays fatal

Only the **notification side** is contained. `_notify` wraps the runner call in a broad
`except Exception` and logs at WARNING (`ges2t.notify`): a missing binary
(`FileNotFoundError`), a non-zero `notify-send` (`NotificationError`), or a raising injected
runner is swallowed there. Concretely:

- The **wrapped** `StatusUI`'s exception is **not** caught: `self._wrapped.error(message)`
  is outside the notification `try`, so it propagates exactly as before to
  `service.py`'s per-callback containment (ticket 21). A tray bug is not converted into
  "the tray stopped updating".
- `run_service`'s control flow is untouched: `except PipelineError` / `except Exception`
  around `_startup`, `except ServiceAlreadyRunningError` / `except OSError` around `serve`,
  and the `finally: _shutdown_status_ui(ui)` all still run and still return the documented
  exit codes.
- A **raising** `runner` still leaves the wrapped `error()` called and the real error
  visible: `test_a_missing_notify_send_is_swallowed_and_the_wrapped_ui_still_runs`,
  `test_a_failing_notify_send_is_logged_and_contained`,
  `test_a_raising_wrapped_ui_is_not_masked_and_the_popup_is_still_attempted`.

## Notification mechanism, and the no-new-dependency reasoning

`error(message)` builds an explicit argv and hands it to the injectable runner. The default
runner is:

```python
subprocess.run(
    list(argv),
    capture_output=True,
    check=False,
)
```

with `argv = ["notify-send", "--app-name=ges2t", "--urgency=critical", "ges2t error", message]`.
The **body is the service's message verbatim** (ADR 0016); the fixed app name and summary
are the only things the wrapper adds, and the message is passed as one argument with no
shell, so an error containing quotes or `;` cannot become a command.

- **No new dependency.** `notify-send` is the freedesktop Notification-Spec client already
  installed on the reference machine (libnotify `0.8.8`, verified with `notify-send --version`).
  The project deliberately talks to the system with subprocesses (`busctl`, `xkbcli`) rather
  than Python D-Bus bindings, and ADR 0001 keeps the runtime stdlib-first; a `dbus-next` /
  `pydbus` dependency would be a new pip package for one call. The ticket names
  `notify-send` explicitly.
- **No shell.** An explicit list (`list(argv)`, `shell` absent) is the ticket's requirement
  and the ADR 0016 "no traceback/quoting hazard" spirit.
- **Injectable.** `runner: NotificationRunner` is a `Protocol` with `__call__` (ADR 0019,
  interface-typed), and `clock` is a `Callable[[], float]` defaulting to `time.monotonic`,
  so tests use fakes and never sleep or pop up.
- `--urgency=critical` is a judgement call: the failures this ticket exists for break
  dictation, so the popup should not auto-expire unread before the user looks. It is a
  one-constant change (`notify._URGENCY`) if the user finds it too loud.

## `ges2t cancel` — verified, not reimplemented

The cancel path is already implemented (ADR 0016 and the service transition table:
`RECORDING → IDLE` via cancel) and does not fail, so this ticket adds **no** notification on
it and no "cancelled" popup. Verified by running the existing cancel coverage unchanged:

```
$ uv run pytest -q -k cancel
17 passed, 644 deselected in 0.44s
```

including `test_cancel_during_recording_injects_nothing` and
`test_cancel_while_idle_is_rejected`.

## Existing tests touched (and why that is not weakening them)

Wrapping is now applied to **any** status UI when `ui.notifications` is true (default),
including an explicitly injected one, so two ticket-17/16 wiring assertions that checked
object identity had to learn about the wrapper:

- `tests/test_pipeline.py::test_run_service_injects_the_status_ui_into_the_service`:
  `seen[0]._status_ui is status_ui` → asserts it is a `NotifyingStatusUI` whose
  `wrapped is status_ui` (strictly stronger: it now proves both the injection and the
  forwarding target).
- `tests/test_tray_backend.py::test_run_service_builds_the_status_ui_when_none_is_injected`:
  `seen == [sentinel]` → asserts one argument that is a `NotifyingStatusUI` wrapping
  `sentinel` (same strength: `build_status_ui`'s choice still reaches `_startup`).

The two `_DiagnosticStatusUI` shutdown tests were **not** changed and still pass, which is
the direct evidence that the lifecycle delegation above preserves ticket 17's shutdown
reporting through the wrapper.

## Real verify output

Baseline on `main` (`ca545f0`): `uv run pytest` → **639 passed**; `mypy` → **45 files**.

```
$ rm -rf .mypy_cache __pycache__ .pytest_cache && timeout 300 uv run pytest tests/test_notify.py -q
......................                                                   [100%]
22 passed in 0.12s
T_NOTIFY_EXIT=0

$ rm -rf .mypy_cache __pycache__ .pytest_cache && timeout 300 uv run pytest -q
........................................................................ [ 98%]
.............                                                            [100%]
661 passed in 3.49s
T_ALL_EXIT=0

$ rm -rf .mypy_cache __pycache__ && timeout 300 uv run mypy src/ges2t tests
Success: no issues found in 47 source files
MYPY_EXIT=0
```

**639 → 661** tests (+22, all in `tests/test_notify.py`; none deleted, skipped, xfailed or
weakened); **45 → 47** mypy files. Both commands were run under an external `timeout` and
the process **exited 0** (the "reports a failure then hangs" failure mode is not present).

**Proof that no real notification was sent by the suite.** A `notify-send` shim was put
first on `PATH`, recording any invocation to a file; the whole suite was run:

```
$ rm -f /tmp/t18_notify_calls
$ PATH=/tmp/t18_fakebin:$PATH timeout 300 uv run pytest -q
661 passed in 2.86s
EXIT=0
--- shim calls ---
NONE — no notify-send process was started
```

## Falsification evidence (every test made to fail)

A throwaway harness (`/tmp/t18_falsify.py`, not committed) applies one textual mutation at a
time, runs only the guarding test(s) under `timeout 60`, restores with `git checkout --`,
and deletes every `__pycache__` / `.mypy_cache` / `.pytest_cache` **before each run** (the
stale-bytecode trap: a same-second restore can leave a `.pyc` Python still accepts).
Result: **20/20 mutations made a guarding test fail**; the worktree was clean of
source mutations afterwards.

```
M01 error does not forward                      -> every_status_ui_call_forwards
M02 idle does not forward                       -> every_status_ui_call_forwards
M03 recording does not forward                  -> every_status_ui_call_forwards
M04 transcribing does not forward               -> every_status_ui_call_forwards
M05 partial does not forward                    -> every_status_ui_call_forwards
M06 partial notifies                            -> state_calls_and_partials_never_notify
M07 idle notifies                               -> state_calls_and_partials_never_notify
M08 body re-worded                              -> ..._verbatim + ..._names_the_ydotoold_fix
M09 dedup disabled                              -> repeated_identical_errors_send_one
M10 dedup is last-message-only                  -> interleaved_identical_errors_stay_suppressed
M11 window is zero                              -> repeat_inside_window + window_is_a_few_seconds
M12 window never expires                        -> renotifies_after_window + window_is_a_few_seconds
M13 notification failure not contained          -> a_missing_notify_send_is_swallowed
M14 wrapped failure masked by containment       -> a_raising_wrapped_ui_is_not_masked
M15 default runner uses a shell string          -> explicit_argument_list_without_a_shell
M16 default runner ignores a nonzero exit       -> raises_notification_error_on_nonzero_exit
M17 stop does not forward                       -> stop_and_failure_report_forward
M18 failure_report does not forward             -> stop_and_failure_report_forward
M19 run_service wraps even when disabled        -> does_not_wrap_when_disabled
M20 run_service never wraps                     -> wraps_when_enabled + wraps_injected
```

**The one that mattered.** M12 (window mutated to `1.0e9`) initially **survived**: the test
advanced its fake clock by `SUPPRESSION_WINDOW_SECONDS`, i.e. by the very constant under
mutation, so it moved its own goalposts — exactly the "test compares against its own
constants" class the orchestrator flagged. Fixed by hard-coding the advances (`_SHORT = 1.0`,
`_PAST_THE_WINDOW = 30.0`) and adding `test_the_suppression_window_is_a_few_seconds`
(`0 < constant <= 30`). M11/M12 now each fail two tests.

## Manual end-to-end — USER-OWNED, not run by this agent

This step needs `ydotoold` **stopped** (which breaks the user's dictation while it is down)
and a desktop session to see the popup. This agent ran **no** service change, no `ges2t run`,
no keystrokes, no `ydotoold`, no clipboard, no `sudo`. The DoD box is left **unticked** for
the user. Commands below are run from
`/home/matee/Workspace/Personal/ges2t/.worktrees/notifications-and-cancel`.

**Prerequisites (already true on the reference machine):** the venv was created with
`uv venv --system-site-packages --python /usr/bin/python3 && uv sync`; the tray packages are
installed; `ui.notifications` defaults to `true`; `notify-send` is installed.

1. Terminal A: `uv run ges2t run`. Expected: the service starts and the tray icon appears
   (ticket 17's manual step).

2. Stop the daemon:

   ```bash
   systemctl --user stop ydotool
   uv run ges2t status          # expect: idle
   ```

3. Trigger one failing dictation. In terminal B:

   ```bash
   uv run ges2t toggle          # start recording
   # say a short phrase, then:
   uv run ges2t toggle          # stop -> transcription succeeds, injection fails
   ```

   **Expected:** a desktop notification appears whose **body names the fix**, e.g.
   contains `ydotoold is not reachable`, `systemctl --user enable --now ydotool`,
   `/dev/uinput` and the `input` group. The same sentence should also be on terminal A's
   stderr (from the tray's tooltip/`NullStatusUI`) and `uv run ges2t status` should read
   `idle` afterwards (the service survived the failure).

4. Rapid repeats: run the failing `uv run ges2t toggle` cycle several times within ~5 s
   (or press the bound key repeatedly). **Expected: exactly ONE popup** for the identical
   message inside the 5 s window, not one per press. Wait > 5 s and trigger it once more:
   a new popup appears (the window has passed).

5. Distinct errors still both notify: if a second, different failure can be produced (for
   example stop the engine process), its message gets its **own** popup — suppression is
   per message text, not a blanket rate limit.

6. Restore the daemon:

   ```bash
   systemctl --user start ydotool
   uv run ges2t toggle          # a normal dictation must type text again
   ```

Record the observation and tick the manual box in `ticket.md`.

## For the orchestrator / reviewer

- The manual `ydotoold`-stopped box is deliberately **unticked**; it is user-owned.
- `run_service`'s public signature is unchanged, and its control flow/exit codes are
  unchanged; only the value passed to `_startup`/`_shutdown_status_ui` is now the wrapper.
- The wrapper delegates `stop()`/`failure_report()` so ticket 17's shutdown reporting is
  preserved; the two unchanged `_DiagnosticStatusUI` tests are the evidence.
- Re-review should confirm: (a) the five `StatusUI` members forward verbatim and `partial`
  never notifies; (b) deleting the delegation breaks the two lifecycle tests (M17/M18);
  (c) the dedup tests do **not** derive their clock from `SUPPRESSION_WINDOW_SECONDS`;
  (d) an injected/raising runner never affects the wrapped UI and is logged.
