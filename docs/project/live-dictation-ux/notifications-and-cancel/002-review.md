# 002 — Review: ticket 18 `NotifyingStatusUI` (notifications-and-cancel)

Reviewer: independent review pass. Branch `feat/notifications-and-cancel`, worktree
`.worktrees/notifications-and-cancel`, HEAD `483f1b2`. Reviewed diff:
`git diff main...feat/notifications-and-cancel`.

Claimed baseline: `main` 639 tests / 45 mypy files. Claimed head: 661 tests / 47 files.

## Verdict

**pass** (with the manual DoD box correctly left unticked and user-owned). Full findings
below; no blocking item found. The only open item is the user-owned manual DoD box.

Status: **final**.

---

## 1. Safety gate — NO real notification was sent, and none can be

**Reproduced independently.** A recording `notify-send` shim was placed first on `PATH`
(`/tmp/t18_review_fakebin/notify-send`) that appends `INVOKED ... argv=...` to
`/tmp/t18_review_calls` and exits 0. The shim was first proven live:

```
$ PATH=/tmp/t18_review_fakebin:$PATH command -v notify-send
/tmp/t18_review_fakebin/notify-send
$ PATH=/tmp/t18_review_fakebin:$PATH notify-send --app-name=ges2t "shim self-test"
INVOKED pid=3723786 argv=--app-name=ges2t shim self-test
```

Then the full suite under that `PATH` (caches cleared first):

```
$ PATH=/tmp/t18_review_fakebin:$PATH timeout 300 uv run pytest -q
661 passed in 2.19s
PYTEST_EXIT=0
--- shim calls ---
NO FILE: zero notify-send invocations
```

And `tests/test_notify.py` alone under the same shim:

```
$ PATH=/tmp/t18_review_fakebin:$PATH timeout 300 uv run pytest tests/test_notify.py -q
22 passed in 0.23s
NOTIFY_EXIT=0
--- shim calls after test_notify ---
NO FILE: zero notify-send invocations
```

**Recorded count: 0** for both runs (the file was never created; the self-test proves the
shim would have recorded an invocation had one occurred).

Every test in `tests/test_notify.py` injects a fake runner (`_RecordingRunner`,
`_RaisingRunner`) or monkeypatches `notify.subprocess.run`; none spawns `notify-send`, and
nothing reaches `org.freedesktop.Notifications`. `test_the_default_runner_passes_an_explicit_
argument_list_without_a_shell` monkeypatches `notify.subprocess.run` and never runs a
process. No other test file references `notify-send` (only the `.pyc` of `test_notify.py`
matches a grep). **Confirmed.**

---

## 2. Pure wrapper — falsification

### 2a. Forwarding (independent probe)

`/tmp/t18_wrap_probe.py` drives `NotifyingStatusUI` with a recording fake and asserts the
call sequence, arguments, lifecycle delegation and that only `error` notifies. Result:

```
FORWARDING OK: ['idle', 'recording', 'transcribing', 'partial', 'error']
               ['live words', 'ydotoold is not reachable'] stops= 1
NOTIFY ARGV: ['notify-send', '--app-name=ges2t', '--urgency=critical',
              'ges2t error', 'ydotoold is not reachable']
```

Every `StatusUI` member forwards unchanged (message verbatim), `stop()` delegated
(`stops=1`), `failure_report()` returned the inner object, and exactly one runner call
resulted from the single `error`.

### 2b. Lifecycle delegation and the two `_DiagnosticStatusUI` tests

`run_service` wraps an injected UI too (default `ui.notifications=true`), so
`_shutdown_status_ui` now receives the wrapper. Without `stop`/`failure_report`
delegation the wrapper is a `StoppableStatusUI`/`DiagnosticStatusUI` by structure
(`isinstance` succeeds) but does nothing — which is exactly the silent ticket-17
regression. The two unchanged `tests/test_pipeline.py` shutdown tests
(`test_run_service_reports_the_status_ui_failure_on_the_normal_path` and
`test_run_service_reports_the_status_ui_failure_on_a_serve_error_path`) assert
`ui.stop_calls == 1` and the printed summary; they ride through the wrapper, so they do
exercise delegation (see mutation M-stop below).

### 2c. Falsify by breaking it

(see §5 mutations for the independent runs and the exact failing tests: dropping the
`error` forward, dropping `stop`, and dropping `failure_report` each fail a guarding test).

Every one of the 8 mutations below that touched forwarding/delegation was caught, so the
wrapper's tests are not vacuous: a missing call fails a test.

### 2d. `ui.notifications = false` does not wrap

`_apply_notifications` returns the bare UI when the switch is off, so the notification path
is not constructed at all. Independently falsified: making `_apply_notifications` wrap
unconditionally makes `test_run_service_does_not_wrap_when_notifications_are_disabled` fail
(`1 failed`, exit 1). The enabled/injected wiring tests are caught by the
`never-wrap` mutation (MINE-run-service-never-wraps, 2 tests fail). `UIConfig.notifications`
defaults to `True` (`src/ges2t/config.py:137`).

---

## 3. What stays fatal, and the dedup rule

### 3a. Wrapped-UI exceptions still propagate

`error` calls `self._notify(message)` and then `self._wrapped.error(message)` **outside** the
notification `try`. A raising wrapped UI is not converted into a silent no-op:
`test_a_raising_wrapped_ui_is_not_masked_and_the_popup_is_still_attempted` asserts the
popup is attempted **and** the `RuntimeError` re-raises. Independently falsified: wrapping
`self._wrapped.error(message)` in `try/except Exception: pass` makes that test fail
(`DID NOT RAISE RuntimeError`). So the wrapper does not newly swallow a wrapped-UI
exception; `service.py`'s ticket-21 containment still owns it.

### 3b. Notification-side failures are contained

`_notify` wraps the runner in a broad `except Exception` and logs at WARNING
(`ges2t.notify`). Independently falsified in the other direction: narrowing that except to
`ZeroDivisionError` makes **two** tests fail — `test_a_missing_notify_send_is_swallowed_and_
the_wrapped_ui_still_runs` (`FileNotFoundError` escapes) and
`test_a_failing_notify_send_is_logged_and_contained` (`NotificationError` escapes). So a
missing binary, a non-zero exit, or a raising runner really is swallowed and cannot affect
the wrapped UI's calls or `run_service`'s control flow/exit codes. (The default runner,
`run_notify_send`, converts a non-zero exit to `NotificationError` and lets
`FileNotFoundError` surface — both are caught by `_notify`.) Note `except Exception` does
**not** catch `BaseException`, so `KeyboardInterrupt`/`SystemExit` are deliberately not
swallowed. No test constructs `NotifyingStatusUI` without an injected runner, so the default
runner is never reached in the suite.

### 3c. Dedup rule

My own probe (`/tmp/t18_dedup_probe.py`), driven with an injected clock:

```
error("A") -> sent; +1s error("B") -> sent; +1s error("A") -> SUPPRESSED
error("C") inside the window -> SENT (distinct text is new information)
+30s error("A") -> SENT again
DEDUP OK: ['A', 'B', 'C', 'A']
```

This is keyed on the message text, tracked **per message** (so `A, B, A` does not re-notify
`A` inside the window), and a distinct error inside the window still notifies. The attempt
is timestamped before the runner runs, so a dead daemon is not hammered either. The relevant
suite tests (`test_repeated_identical_errors_send_one_notification`,
`test_interleaved_identical_errors_inside_the_window_stay_suppressed`,
`test_distinct_errors_each_send_a_notification`,
`test_the_same_error_notifies_again_after_the_window`) pass.

### 3d. The vacuous-test fix is real (`1e9` re-mutation)

**Confirmed.** Re-applying the exact `SUPPRESSION_WINDOW_SECONDS = 1.0e9` mutation (with
caches cleared before the run) now makes **two** tests fail, exit 1:

```
test_the_same_error_notifies_again_after_the_window
    clock.advance(30.0)  # literal, not derived from the constant
    assert len(runner.argvs) == 2
E   AssertionError: assert 1 == 2
test_the_suppression_window_is_a_few_seconds
E   AssertionError: assert 0.0 < SUPPRESSION_WINDOW_SECONDS <= 30.0
```

So the previous `X == X` survival is genuinely fixed: the dedup tests advance their fake
clock by the **literal** `_SHORT = 1.0` / `_PAST_THE_WINDOW = 30.0`, and
`test_the_suppression_window_is_a_few_seconds` pins the constant independently. Not
blocking.

### 3e. Error text is actionable

`test_the_notification_body_names_the_ydotoold_fix` takes the real generator
`_daemon_unreachable_message` (so it cannot drift into a test-only string) and asserts the
body contains `systemctl --user enable --now ydotool`, `/dev/uinput`
and the `input` group. The body is the service message verbatim; the wrapper adds only the
fixed app name, summary `ges2t error`, and `--urgency=critical`. Actionable, not merely
"error".

---

## 4. The two modified assertions — per-assertion verdict

**Assertion A** — `tests/test_pipeline.py::test_run_service_injects_the_status_ui_into_the_service`

- Before: `assert seen[0]._status_ui is status_ui`
- After: `assert isinstance(seen[0]._status_ui, NotifyingStatusUI)` **and**
  `assert seen[0]._status_ui.wrapped is status_ui`

**Verdict: strictly stronger.** The old identity check is preserved verbatim inside
`.wrapped is status_ui` (the `wrapped` property returns the exact injected object), and the
new `isinstance` additionally proves the notification wrapper is inserted. Losing the
old single assertion removes no coverage. The `never-wrap` mutation fails this test.

**Assertion B** — `tests/test_tray_backend.py::test_run_service_builds_the_status_ui_when_none_is_injected`

- Before: `assert seen == [sentinel]`
- After: `assert len(seen) == 1` **and** `assert isinstance(seen[0], NotifyingStatusUI)`
  **and** `assert seen[0].wrapped is sentinel`

**Verdict: strictly stronger.** `NullStatusUI` defines no `__eq__`/`__hash__`, so the old
list comparison `seen == [sentinel]` was already `len == 1 and seen[0] is sentinel` by
identity. The new form states that identity explicitly (`wrapped is sentinel`) and adds the
`isinstance` wrapper check. No coverage removed.

Neither modification quietly removed coverage; both add the wrapper contract while keeping
the exact-object forwarding target. The two unchanged `_DiagnosticStatusUI` shutdown tests
are the evidence that lifecycle delegation survives the wrapper (see §2b and §5).

---

## 5. Mechanicals and mutation evidence

### Suite / mypy / exit

```
$ PATH=<shim> timeout 300 uv run pytest -q
661 passed in 2.19s        PYTEST_EXIT=0

$ rm -rf .mypy_cache __pycache__ .pytest_cache && timeout 180 uv run mypy src/ges2t tests
Success: no issues found in 47 source files      MYPY_EXIT=0

$ tests/test_notify.py                                    22 passed
$ -k "identical or distinct or repeat or window"           6 passed
```

Baseline on `main` is claimed 639; the delta is exactly `+22`, and
`tests/test_notify.py` has 22 `def test_` functions. `git diff main...HEAD -- tests/ | grep
'^-' | grep 'def test_'` → **NONE** (no test deleted). No `skip`/`xfail` added. The suite
exits (not the "reports a failure then hangs" mode): `PYTEST_EXIT=0` under an external
`timeout`.

### My own mutations (9, all caught)

Harness `/tmp/t18_mutate.py`: one textual mutation, `timeout 90` per run, `__pycache__` +
`.mypy_cache` + `.pytest_cache` cleared **before and after** every run, file restored from a
hash-verified backup (`restored=True` on every line). Every run exited non-zero because a
guarding test failed:

| Mutation | Failing guarding test(s) |
|---|---|
| `error` drops the forward | `test_every_status_ui_call_forwards_to_the_wrapped_ui`, `test_a_missing_notify_send_is_swallowed_...` |
| `partial` notifies | `test_state_calls_and_partials_never_notify` |
| narrow the containment `except` | `test_a_missing_notify_send_...`, `test_a_failing_notify_send_is_logged_and_contained` |
| `SUPPRESSION_WINDOW_SECONDS = 1e9` | `test_the_same_error_notifies_again_after_the_window`, `test_the_suppression_window_is_a_few_seconds` |
| `stop()` is a no-op | `test_stop_and_failure_report_forward_...` + **both** `tests/test_pipeline.py` shutdown tests |
| `failure_report()` returns `None` | `test_stop_and_failure_report_forward_...` + `test_run_service_reports_the_status_ui_failure_on_the_normal_path` |
| move wrapped `error` inside the `try` | `test_a_raising_wrapped_ui_is_not_masked_and_the_popup_is_still_attempted` |
| `_apply_notifications` never wraps | `test_run_service_wraps_when_notifications_are_enabled`, `test_run_service_wraps_an_injected_status_ui_too` |
| `_apply_notifications` always wraps (ignores switch) | `test_run_service_does_not_wrap_when_notifications_are_disabled` |

Restore verified afterwards: `git status` shows only the new `002-review.md` untracked, and
`git diff --stat` over `src/ges2t/notify.py src/ges2t/pipeline.py tests/` is **empty**;
`sha256sum src/ges2t/notify.py` = `e90d4250...62d0c`, `pipeline.py` = `ee1ba513...2531b7`.

### ADR 0019 conformance

- Full annotations, `-> None` throughout; `Clock = Callable[[], float]` (full signature);
  `NotificationRunner` is a `Protocol` with `__call__(self, argv: Sequence[str]) -> None`;
  `runner` and `clock` are injected (default `run_notify_send` / `time.monotonic`).
- No public positional tuple: `_notification_argv -> list[str]` is a homogeneous sequence;
  `TrayFailure` is a frozen dataclass (reused, not re-defined).
- **No new dependency:** `pyproject.toml` is unchanged; `notify.py` imports only `logging`,
  `subprocess`, `time`, `collections.abc`, `typing`, and `ges2t.interfaces`/
  `ges2t.ui.status` — no `gi`/`pystray`/PIL, so the GUI-free import guarantee holds.
- **No ADR edits** and **no pytest/config changes** in the diff.

### Scope / public surface

Files in the diff: `src/ges2t/notify.py` (new), `src/ges2t/pipeline.py` (`_apply_notifications`
+ one wiring line), `tests/test_notify.py` (new, 22), `tests/test_pipeline.py`,
`tests/test_tray_backend.py`, and the ticket's own docs/board row. `run_service`'s public
signature is unchanged — `def run_service(config: Config, status_ui: StatusUI | None = None)
-> int` — and `test_the_public_run_service_signature_is_unchanged` still passes.

### `ges2t cancel` — verified, not reimplemented

No change to `cli.py`, `socket_proto.py`, `service.py`, or the state machine in the diff; the
cancel path predates this ticket (ticket 10 / ADR 0016). The ticket's own cancel sanity run
(`uv run pytest -q -k cancel` → 17 passed) is consistent with coverage that already existed;
the ticket adds no notification and no "cancelled" popup on the cancel path.

### Manual step is user-owned and unticked

`001-comment.md` contains the exact script (stop `ydotoold`, `ges2t run` in terminal A, a
failing `toggle`, rapid repeats within ~5 s, distinct-error check, restore `ydotoold`) and
flags the service interruption ("breaks the user's dictation while it is down"). The DoD box
in `ticket.md` remains **unticked** (`- [ ] **Manual, once — USER-OWNED; ...`).

---

## What I could not verify

- **The manual ydotoold-stopped end-to-end** (popup visible, one popup on rapid repeats,
  distinct errors each notify on a real desktop). User-owned; deliberately not attempted here
  (no service change, no `ydotoold`, no keystrokes, no clipboard, no `ges2t run`, no GUI).
- **The real `notify-send`/D-Bus call actually reaches a daemon.** Only the argv, the
  contained failure paths, and `subprocess.run`'s call shape are tested; the suite never
  talks to `org.freedesktop.Notifications` (by design).
- **Baseline 639 on `main`** was not independently re-run (a second worktree would accrue
  caches); the `+22` delta and the absence of deleted tests were verified directly instead.
- **No timeout on `subprocess.run` inside `run_notify_send`**: a daemon that accepts the
  connection and then hangs would block the caller, which is not the "missing binary /
  non-zero exit / raising runner" class the ticket contains. Not in the DoD and used
  elsewhere in the project's subprocess style; noted as a residual, non-blocking risk.

---

## Verdict

**pass.** Zero real notifications (0 shim invocations, reproduced with my own shim); the
wrapper is pure and forwards every member with arguments, delegates `stop`/`failure_report`
(falsified — both pipeline shutdown tests break without it); wrapped exceptions still
propagate while notification-side failures are contained (falsified both ways); the dedup
rule is per-message-text and the `1e9` vacuous-test defect is genuinely fixed (re-mutation
now fails two tests); the two changed assertions are strictly stronger, not weaker; and
the mechanicals hold (`661`/`22` tests, `mypy` 0/47, suite exits 0). The only open item is
the user-owned manual DoD box, correctly left unticked. **Mergeable.**
