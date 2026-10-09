# 002 — Review: tray status UI (ticket 17)

Reviewer: independent falsification pass · Branch: `feat/tray-status-ui` · HEAD `fea1ecb`
Worktree: `.worktrees/tray-status-ui` · Merge-base with `main`: `33413b3` (the `main`
tip has since advanced to `a1530669`, so all diff claims below use `main...feat/tray-status-ui`
which is the merge-base; scope check confirms it).

## Verdict

**Changes requested — one blocking finding.**

The heart of the ticket works: the AppIndicator backend assertion is real, runs before any
icon is created, rejects the silent `_xorg`/`_gtk`/`_dummy` backends with the actionable
message, and I could not construct a false pass on this machine. The observability
mechanism is correct and reaches every `run_service` exit path. Import discipline holds.
Tests, typing and scope are clean.

The blocking finding is the **breadth of the tray fallback** in `build_status_ui`: an
`except Exception` converts genuine programming errors (`AttributeError`, `TypeError`,
`NameError`, `AssertionError`, `KeyError`, …) inside tray construction into "no tray, carry
on", which is exactly the loud-failure-for-quiet-failure trade this project has paid for
before. Details in §2. This is a small, local fix (narrow the `except`, add one test).

## 1. The backend assertion — confirmed, and my attempts to defeat it

Claim: `assert_appindicator_backend()` does (a) `import gi;
gi.require_version("AppIndicator3","0.1")` and (b) `import pystray` and requires
`pystray.Icon.__module__ == "pystray._appindicator"`, because pystray binds `Icon` at import
time and then `del backend`.

**Confirmed by reading the vendored pystray** (`.venv/.../pystray/__init__.py`): `Icon =
backend().Icon; del backend`, and the resolution order is `_appindicator, _gtk, _xorg`,
each candidate catching only `ImportError`. So the bound class is the only surviving witness
— the implementation's rationale is correct, not folklore.

Real-machine results (no icon, no window; the checks only import):

- **Correct case passes:** `assert_appindicator_backend()` → OK, and
  `pystray.Icon.__module__ == "pystray._appindicator"`.
- **`PYSTRAY_BACKEND=xorg` is rejected** with the exact two-line class of message, naming
  `python-gobject`, `gtk3`, `libappindicator` and `--system-site-packages` (the prompt's
  `--system-support-packages` was a typo; the code and message are correct), plus `ges2t
  doctor` and `(ADR 0013)`.

**Version constraint is real, not decorative.** In fresh interpreters:
`gi.require_version("AppIndicator3","0.2")` → `ValueError: Namespace AppIndicator3 not
available for version 0.2`; `("AppIndicator3","0.1")` → OK; an unknown namespace →
`ValueError: Namespace NopeNs not available`. `require_version` genuinely constrains the
typelib version. (It is also sticky/process-global: a second call with a different version
raises "already requires version …", which the assertion never triggers in a fresh process.)

**Attacks I ran and what they showed:**

| Attack | Result |
|---|---|
| Does the assertion run *before* `_create_icon`? Patched `assert` and the class's `_create_icon` to record order. | `order == ['assert', 'create']`. When the assertion raises, `order == ['assert']` — **no icon is created**. |
| `_gtk` backend (gi present, AppIndicator3 absent). Fake `gi` + fake `pystray.Icon.__module__ == "pystray._gtk"`. | **Rejected** (module check). Separately, with a fake `gi` whose `require_version("AppIndicator3")` raises, step (a) rejects first. So `_gtk` is caught twice over. |
| `_dummy` backend. | **Rejected** (module check). |
| Missing `gi`. | **Rejected** at step (a), actionable message. |
| **Stale import order**: `import pystray` under `PYSTRAY_BACKEND=xorg` first, then `gi.require_version` succeeds, then assert. | **Rejected** — the cached `pystray` module's `Icon` is still `_xorg`. A stale import makes the check *conservative*, never a false pass. |
| Can fake `sys.modules` satisfy it? | Yes, but that is the deliberate test seam; no production path injects modules or an icon. |

**I could not make it pass while the real backend is unusable.** The one genuine
false-*negative* is worth recording (non-blocking): pystray's `_appindicator` falls back to
`AyatanaAppIndicator3` when `AppIndicator3` is absent, but the assertion hard-requires
`AppIndicator3`. On a machine with only the Ayatana typelib, pystray would select the working
`_appindicator` backend and the assertion would still refuse. On the reference machine both
`AppIndicator3-0.1.typelib` and `AyatanaAppIndicator3-0.1.typelib` are present, so this is
not a live defect — only a note for `doctor`/future portability. Failing closed is the right
default here.

**Verdict on §1: the assertion is the real thing and is the correct centerpiece.**

## 2. What must stay fatal — BLOCKING finding

`src/ges2t/pipeline.py::build_status_ui`:

```python
try:
    return build_tray_status_ui(_send_command_to_service)
except TrayBackendError as exc:
    _print_tray_warning(str(exc)); return NullStatusUI()
except Exception as exc:                       # <-- too broad
    _print_tray_warning(f"the tray could not be created ({exc})")
    return NullStatusUI()
```

I injected each exception class through a monkeypatched `build_tray_status_ui`:

```
AttributeError   -> NullStatusUI  (swallowed)
TypeError        -> NullStatusUI  (swallowed)
NameError        -> NullStatusUI  (swallowed)
AssertionError   -> NullStatusUI  (swallowed)
KeyError         -> NullStatusUI  (swallowed)
```

A genuine bug in tray construction is converted into a warning and "the tray is disabled;
ges2t remains fully usable from the CLI". That is the quiet failure ADR 0013 exists to
prevent, and it is precisely the mechanism the implementer's own M17 uncovered.

**Ruling:** only *environmental* failures may be swallowed here. `TrayBackendError`
(environment/typelib/backend) stays a fallback. A programming error (`AttributeError`,
`TypeError`, `NameError`, `AssertionError`, …) must propagate and crash loudly — the service
is allowed to be unusable *because of a bug we must fix*, but it must not silently downgrade.
The correct shape is to catch the environmental set explicitly (`TrayBackendError`,
`ImportError`/`ModuleNotFoundError`, `OSError`, and the `RuntimeError` from a failed thread
start) and let programming errors escape, or to re-raise non-environmental exceptions after
printing. This is a small, local change. (The warning print is at least not *nothing*, but it
replaces a traceback with a one-liner and continues — that is the trade the ticket's own
framing forbids.)

## 3. Observability — mechanism confirmed; every `run_service` exit path surfaces it

Mechanism, independently probed with my own broken icon (not the test's):

- Every display/menu operation funnels through `_guard` → `_record_failure`: `_apply_display`
  (state, tooltip, `update_menu`), `stop`, and the three menu actions via `_send` /
  `_on_quit`; the icon loop's own exception is recorded in `_run_icon`. There is no unguarded
  display path.
- Count increments; `last_failure` retains the most recent `"<action>: <exc>"`.
- **Repeated identical failures print once.** 4 × identical → `failure_count == 4`, exactly
  **1** stderr line. 4 × distinct → **4** lines (each new message once).
- `failure_report()` returns `None` with no failures and otherwise a `TrayFailure` equal to
  `TrayFailure(count=n, last_message=last_failure)`; `TrayFailure.summary()` is **exactly**
  `the tray has failed N times: <last error>`.
- `TrayFailure` is a **frozen dataclass** (`@dataclass(frozen=True)`), verified: assignment
  raises `FrozenInstanceError`. Not a tuple (ADR 0019).

**`run_service` exit paths, probed one by one** with an injected Stoppable+Diagnostic UI and
`failure_count = 1` (no real service, no display):

| Exit path | returned | UI stopped | summary on stderr |
|---|---|---|---|
| normal (serve returns) | 0 | yes | **yes** |
| `ServiceAlreadyRunningError` | 1 | yes | **yes** |
| `OSError` from serve | 1 | yes | **yes** |
| startup `PipelineError` | 1 | yes | **yes** |
| startup generic `Exception` | 1 | yes | **yes** |
| unexpected `ValueError` from serve (propagates) | raised | yes | **yes** (`finally` fires) |

So the claim "stops the UI and prints the summary on every exit path" is **true**; no path
skips it. Two notes: (a) on startup failures the summary prints *before* the primary error
(cosmetic); (b) the `finally` on the serve block is what covers the last four rows.

**Non-blocking test gap (N1):** no test asserts this. Mutating `run_service` to delete the
`finally: _shutdown_status_ui(ui)` was **not caught** by the whole suite (see §6, mutation R3).
The behavior is correct today but unguarded — a future refactor could silently delete the only
route from a broken tray to the shutdown summary. Recommended: one test that injects a
Diagnostic/Stoppable UI into `run_service` for the normal and one failure path and asserts
`the tray has failed N times` on stderr.

**Limitation (N4, for the orchestrator):** `failure_report()` lives in the service process.
`ges2t status` is a separate socket client and `ges2t doctor` is standalone, so neither can
read it today (the ADR 0016 verb set is closed; no verb carries it). The ticket says doctor
and/or status "must be able to surface" the sentence; the 001-comment is honest that this is
still unwired. The failure is visible on stderr immediately and again at shutdown, so the
required "recorded and retrievable" test is met, but the cross-process surfacing is a
follow-up (ticket 12 can at least re-run `assert_appindicator_backend()`).

## 4. Import discipline — falsified and restored

The fresh-interpreter test (`sys.executable -I -c` importing a module and checking
`sys.modules`) covers `ges2t.cli`, `ges2t.pipeline`, `ges2t.ui.tray`.

- **Falsification:** I added a top-level `import pystray` to `src/ges2t/ui/tray.py`; the
  `[ges2t.ui.tray]` case **failed**. I then `git checkout -- src/ges2t/ui/tray.py`; the file
  is byte-identical to the pre-probe backup and the tree is clean.
- `NullStatusUI` **moved**: exactly one definition remains,
  `src/ges2t/ui/status.py:63`; `pipeline.py` imports it. No duplicate, no stale
  `pipeline.NullStatusUI`.
- **Nit (N3):** the 001-comment claims `import ges2t.ui.status` is "pinned by a
  fresh-interpreter test", but `ges2t.ui.status` is not in the parametrize list. It is
  transitively pinned (tray imports status at module scope, so a GUI import in status would
  fail the tray case), and I confirmed directly with a fresh interpreter that importing
  `ges2t.ui.status` leaves `gi`/`pystray`/`PIL` all absent. Claim holds; the test list is just
  one entry short of the prose.

## 5. Nothing is reachable only through the tray

- `Toggle dictation` → `Command.TOGGLE.value` and `Cancel` → `Command.CANCEL` go through
  `_send_command_to_service`, which is `send_command(default_socket_path(), command)` — the
  same client the CLI uses (ADR 0016). No service logic is reimplemented. Test asserts
  `calls == ["toggle", "cancel"]`.
- `Quit` → `os.kill(os.getpid(), signal.SIGTERM)`. I confirmed this is a **genuinely clean**
  shutdown: `Service.serve()` enters `signal_shutdown(server)` which installs a handler for
  `(SIGINT, SIGTERM)` that calls `server.stop()`; `serve_forever()` then returns, the
  `finally` runs `self.shutdown()`/`server.close()`, and `run_service` returns 0. The handler
  is installed in the main thread and Python delivers the signal to the main thread, so a
  GTK-thread callback firing it is fine.
- Everything the tray offers is available elsewhere: `ges2t toggle`/`ptt-start`/`ptt-stop`/
  `cancel`/`status` cover the menu verbs, and Ctrl-C / `SIGTERM` / stopping the systemd unit
  covers Quit. The tray adds no unique capability (ADR 0013). `partial()` only touches the
  tooltip; no injector is referenced anywhere in `tray.py` (ADR 0011).

## 6. Tests, style, scope — and my own mutations

- **Headless:** the full suite passes with `DISPLAY` and `WAYLAND_DISPLAY` unset
  (`env -u DISPLAY -u WAYLAND_DISPLAY uv run pytest -q` → **537 passed**), as do the two new
  files (**22 passed**). Source scan: every `TrayStatusUI(...)` construction in tests injects
  `icon=_FakeIcon(...)`; no test imports real `gi`/`pystray`/`PIL`; `tests/test_tray_backend.py`
  only puts fakes in `sys.modules`. No test creates a real icon or needs a display.
- **Counts:** `uv run pytest -q` → **537 passed**; the two new files → **22 passed**
  (consistent with base 515; I verified 537 and 22 directly — I did **not** independently
  reproduce the 515 base number, it is only arithmetically consistent).
- **No weakening:** at the merge-base, every pre-existing test file has the same number of
  `def test_` functions; the only changed existing file is `tests/test_pipeline.py`, whose
  diff is purely additive (one import, two `build_status_ui` monkeypatches, and
  `pipeline.NullStatusUI()` → `NullStatusUI()` in one test). No negative diff lines remove an
  assertion. No `skip`/`xfail` was added.
- **mypy:** `rm -rf .mypy_cache && uv run mypy src/ges2t tests` → `Success: no issues found in
  39 source files` (0 errors). ADR 0019: no `-> tuple[...]` in the new src/tests, no `Any`,
  full annotations including `-> None`, records are frozen dataclasses.
- **Scope:** exactly `docs/project/README.md`, the ticket and `001-comment.md`,
  `src/ges2t/pipeline.py`, `src/ges2t/ui/{__init__,status,tray}.py`,
  `tests/test_pipeline.py`, `tests/test_status_ui.py`, `tests/test_tray_backend.py`. Nothing
  else. Board row set to `in review`.

**My three mutations** (each with a hard 180 s per-run timeout; each restored with
`git checkout --` and verified byte-identical + clean):

| # | Mutation | Guarding test | Result |
|---|---|---|---|
| R1 | subtle: `failure_report()` returns `count=self._failures + 1` | `test_tray_records_and_exposes_injected_failures` | **caught** |
| R2 | subtle: swap the two icon-state arguments (`icon.icon = tooltip; icon.title = image`) | `test_tray_maps_every_state_onto_the_tooltip` | **caught** |
| R3 | delete `finally: _shutdown_status_ui(ui)` from `run_service` | full suite (`test_pipeline`, `test_tray_backend`, `test_status_ui`) | **NOT caught** |

**2/3 caught.** R3 is N1 above: the exit-path observability has no regression guard. The
implementation's claimed 21/21 mutations all target behavior that *is* guarded; none of them
covers the shutdown summary, which is why R3 survives.

## What I could not verify / disclosures

- **Accidental real-icon probe (disclosed):** during my first ordering check I patched the
  module global `tray._create_icon`, but `start()` calls the bound method `self._create_icon`,
  so one short-lived `uv run python -c` subprocess constructed and briefly ran a **real
  pystray AppIndicator icon** before `ui.stop()`. It ran for milliseconds in a subprocess that
  has since exited; no process or socket remains (`ps` clean, no `ges2t.sock`). This was not
  intended and I did not repeat it — the corrected ordering probe patches the class method and
  creates nothing. I am reporting it because the ticket forbids creating a real icon.
- **Manual desktop DoD is user-owned and unticked**, as required. I did not run `ges2t run`
  and did not observe the icon in Plasma.
- The **515 base count** was not independently reproduced (see §6).
- The Ayatana-only false negative (§1) cannot be exercised on this machine (both typelibs are
  installed); it is reasoned from pystray's source, not observed.

## What the orchestrator must know before merging

1. **Blocking:** narrow `build_status_ui`'s `except Exception` so programming errors
   (`AttributeError`/`TypeError`/`NameError`/`AssertionError`/…) are not swallowed; keep only
   environmental failures (backend/typelib/import/OS/thread) as fallbacks, and add a test that
   an injected `AttributeError` propagates.
2. **Strongly recommended (N1):** add a `run_service` exit-path test asserting the failure
   summary reaches stderr; R3 shows the behavior is currently unguarded.
3. Non-blocking: N3 (parametrize `ges2t.ui.status` explicitly to match the prose), N4
   (doctor/status cannot read the in-process failure record yet — ticket 12 follow-up), and
   the Ayatana-only false negative.
4. The backend assertion itself is sound; do not weaken it while fixing the fallback.
