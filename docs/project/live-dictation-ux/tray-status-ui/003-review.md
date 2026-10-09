# 003 — Re-review: tray status UI (ticket 17)

Reviewer: re-review after `002-review.md` requested changes · Branch: `feat/tray-status-ui`
Worktree: `.worktrees/tray-status-ui` · HEAD `40b5ccd` · implementation `fea1ecb` ·
review 002 `003bcaf` · merge-base with `main`: `33413b3`.

Scope of this pass: judge the fixes in `git diff 003bcaf..40b5ccd` (claimed
`src/ges2t/pipeline.py`, `src/ges2t/ui/tray.py`, `tests/test_pipeline.py`,
`tests/test_tray_backend.py`, `001-comment.md`), reproduce both falsifications,
and rule on two adversarial angles (thread-start breadth; the other broad catch in
`run_service`).

---

## Verdict

**changes requested** — one narrow, blocking item: Angle A (§4).

§2 and §3 stand: the `002` blocking finding (fallback breadth) is **RESOLVED**,
and N1/R3 is **RESOLVED**; both were reproduced by falsification, not accepted on
prose. §5's second broad catch is a pre-existing, *aborting* handler and is
non-blocking as recorded there.

The blocker is §4. Narrowing `build_status_ui`'s clause to
`(ImportError, ModuleNotFoundError, OSError)` means a `RuntimeError` from
`TrayStatusUI.start()`'s `thread.start()` — an environmental thread/`RLIMIT_NPROC`
failure — now escapes `build_status_ui` and `run_service` and aborts `ges2t run`
at `cli.main`'s top-level handler. That is the outcome ADR 0013 forbids ("a
missing tray must never make ges2t unusable"), and it is a **regression
introduced by `40b5ccd`**, not pre-existing. Blocking on §4's three grounds: an
ADR violation introduced by the fix under review; review 002 §2 explicitly named
the thread-start `RuntimeError` in the environmental set, so the fix is knowingly
incomplete; and the remedy is ~4 lines in this ticket's own file.

**Required change (the fix §4 names).** In `TrayStatusUI.start()`, wrap the single
`thread.start()` call:

```python
try:
    thread.start()
except RuntimeError as exc:  # OS refused a new thread: environmental, not a bug
    raise TrayBackendError(f"the tray thread could not be started ({exc})") from exc
```

`build_status_ui`'s existing `except TrayBackendError` branch then degrades to
`NullStatusUI` + the two warning lines; no other change is needed. This is local
and specific — it wraps only the failing call, so a programming `RuntimeError`
anywhere else still propagates and the 002 finding is **not** re-opened (catching
bare `RuntimeError` at `build_status_ui` would). Widen `TrayBackendError`'s wording
(or add a sibling `TrayStartError`) so a thread failure is not mis-described as a
backend-selection failure, and add one test.

**If the orchestrator overrides this to `pass`,** the override must record the
above as an explicit follow-up naming the local `thread.start()` wrap as the fix —
not drop it. Everything else in the branch is mergeable as-is.

---

## 1. Baseline, counts, hermeticity, mypy

- Branch/HEAD confirmed: `feat/tray-status-ui` at `40b5ccd`; merge-base `33413b3`;
  `git status --porcelain` clean at start.
- **Full suite (displays unset):**
  `timeout 300 env -u DISPLAY -u WAYLAND_DISPLAY uv run pytest -q`
  → **549 passed in 2.47s**, exit status 0 (the run exits; no hang).
- **Fresh mypy:** `rm -rf .mypy_cache && timeout 300 uv run mypy src/ges2t tests`
  → `Success: no issues found in 39 source files`, exit status 0.
- **Test counts by file** (count of `def test_`/`    def test_`):
  `test_pipeline.py:24`, `test_status_ui.py:13`, `test_tray_backend.py:11`;
  all other files unchanged from the numbers review 002 recorded. Base was 537
  after the implementation commit; the addendum claims 549 (12 added).

- **No weakening (verified by diff, not just prose):**
  - `git diff 003bcaf..40b5ccd -- tests/ | grep '^-' | grep 'def test_'` → **NONE** (no test function line removed).
  - `git diff 003bcaf..40b5ccd | grep '^+' | grep -i 'skip\|xfail'` → only the word "skipped" in prose; **no `skip`/`xfail` marker added**.
  - Comparing all `def test_` names between the two revisions: **6 functions added, 0 removed**:
    `test_assertion_accepts_ayatana_when_appindicator3_is_absent`,
    `test_assertion_rejects_when_neither_typelib_is_available`,
    `test_build_status_ui_falls_back_for_environmental_failures`,
    `test_build_status_ui_propagates_programming_errors`,
    `test_run_service_reports_the_status_ui_failure_on_a_serve_error_path`,
    `test_run_service_reports_the_status_ui_failure_on_the_normal_path`.
  - 6 new functions → **12 collected tests** via parametrization (5 programming
    errors + 3 environmental + 1 Ayatana + 1 neither-typelib + 1 normal + 1 serve
    error). 537 + 12 = **549**, matching the run above.
  - The only edits to *existing* tests are additive: `tests/test_pipeline.py`'s
    one import line gained `TrayFailure`; `_FakeGi.__init__` gained an optional
    `unavailable` keyword. No assertion was removed or loosened.
- **Scope of the addendum commit** (`003bcaf..40b5ccd`): exactly
  `001-comment.md`, `src/ges2t/pipeline.py`, `src/ges2t/ui/tray.py`,
  `tests/test_pipeline.py`, `tests/test_tray_backend.py`. **Full branch scope**
  (`main...feat/tray-status-ui`): `docs/project/README.md` (board row), the
  ticket's own docs, `src/ges2t/pipeline.py`, `src/ges2t/ui/{__init__,status,tray}.py`,
  `tests/test_pipeline.py`, `tests/test_status_ui.py`, `tests/test_tray_backend.py`.
  Nothing outside the ticket's declared scope.

---

## 2. Falsification 1 — fallback breadth (the 002 blocking finding)

**Reproduced, not accepted.** I restored the original `except Exception` in
`build_status_ui` (`git checkout` afterwards) and ran the two new tests:

```
$ find . -path ./.venv -prune -o -name '__pycache__' -type d -exec rm -rf {} + ; rm -rf .mypy_cache
$ timeout 120 uv run pytest \
    tests/test_tray_backend.py::test_build_status_ui_propagates_programming_errors \
    tests/test_tray_backend.py::test_build_status_ui_falls_back_for_environmental_failures -q
FAILED tests/test_tray_backend.py::test_build_status_ui_propagates_programming_errors[AttributeError]
FAILED tests/test_tray_backend.py::test_build_status_ui_propagates_programming_errors[TypeError]
FAILED tests/test_tray_backend.py::test_build_status_ui_propagates_programming_errors[NameError]
FAILED tests/test_tray_backend.py::test_build_status_ui_propagates_programming_errors[AssertionError]
FAILED tests/test_tray_backend.py::test_build_status_ui_propagates_programming_errors[KeyError]
5 failed, 3 passed in 0.22s
```

Each failure is `DID NOT RAISE <type>` and `build_status_ui` printed
`ges2t: the tray could not be created ('a genuine bug in tray construction')` +
`the tray is disabled; ges2t remains fully usable from the CLI`. That is exactly
the quiet degradation review 002 described. The **3 environmental cases still
passed even under `except Exception`** — the environmental test is a genuine
fallback assertion, not an artefact of the narrow clause.

**Over-narrowing check (the direction that would break ADR 0013):** after
reverting to the committed narrowed clause:

```
$ git checkout -- src/ges2t/pipeline.py   # sha256 matches the pre-probe hash
$ timeout 120 uv run pytest <same two tests> -q
........                                                                 [100%]
8 passed in 0.16s
```

So under the shipped code the 5 programming-error cases **do** raise and the 3
environmental cases **do** fall back to `NullStatusUI` + the two warning lines.
The clause is exactly `except (ImportError, ModuleNotFoundError, OSError)` with
`TrayBackendError` above it; I read the committed source and the diff to confirm
no other class is caught.

**Verdict on the 002 blocking finding: RESOLVED.** Probe reverted; `git status`
clean of source changes (only the untracked review file).

---

## 3. Falsification 2 — observability N1/R3

**Reproduced, not accepted.** I deleted exactly the shutdown call from
`run_service`:

```diff
     except OSError as exc:
         print(f"ges2t: could not serve on {service.socket_path}: {exc}", file=sys.stderr)
         return _EXIT_FAILURE
-    finally:
-        _shutdown_status_ui(ui)
     return _EXIT_SUCCESS
```

Caches cleared (`find … -name __pycache__ -exec rm -rf` + `rm -rf .mypy_cache`),
then the two new tests under a hard timeout:

```
$ timeout 120 uv run pytest \
    tests/test_pipeline.py::test_run_service_reports_the_status_ui_failure_on_the_normal_path \
    tests/test_pipeline.py::test_run_service_reports_the_status_ui_failure_on_a_serve_error_path -q
FF                                                                       [100%]
… assert ui.stop_calls == 1
E       assert 0 == 1
…
2 failed in 0.24s
EXIT_STATUS=1
```

Both fail on `assert ui.stop_calls == 1` (`assert 0 == 1`) — the missing summary
is downstream of that first assertion, so the tests are pinned to the shutdown
call, not to a string. **Bounded:** the run finished in 0.24 s against a 120 s
timeout and the *exit status* was 1 (a failure, not a timeout/hang — a hanging
suite would have returned 124 and hidden the failure). I then ran the **whole**
suite under the same mutation to rule out a cross-test interaction:

```
$ time timeout 300 env -u DISPLAY -u WAYLAND_DISPLAY uv run pytest -q
…
2 failed, 547 passed in 3.57s
real    0m4.284s
EXIT_STATUS=1
```

Exactly the two new tests fail; nothing else depends on the shutdown call, so
the guard is the *only* thing pinning N1/R3. Reverted with `git checkout`;
`sha256sum -c` over `pipeline.py`, `ui/tray.py` and both test files reports
**OK** for all four, and the restored run is `2 passed in 0.19s`. `git status`
shows only the untracked review file.

**Verdict on N1/R3: RESOLVED.** The two tests are genuine, bounded, and fail
when the behaviour they describe is removed.

---

## 4. Angle A — does the narrowed clause break ADR 0013's core promise?

**Yes — a real, narrow regression, introduced by this fix. Ruling: BLOCKING.**

ADR 0013's decision text is unconditional: *"If the desktop environment cannot
show a tray at all, ges2t must still be fully usable from the CLI — the tray is
a status display and menu, not a required control surface."* The ticket's own
DoD repeats it: *"a missing tray must never make ges2t unusable"*. The narrowed
clause makes one environmental tray failure able to do exactly that.

**The chain, verified, not assumed.** `TrayStatusUI.start()` ends with
`thread.start()` (`src/ges2t/ui/tray.py`); CPython raises
`RuntimeError("can't start new thread")` there when the OS refuses a thread
(resource/`RLIMIT_NPROC` exhaustion). `RuntimeError` is **not** in the shipped
`except (ImportError, ModuleNotFoundError, OSError)` tuple, so it escapes
`build_status_ui`. `run_service` calls `build_status_ui` **outside any `try`**:

```python
ui = status_ui if status_ui is not None else build_status_ui(config)
try:
    service = _startup(config, ui)
```

so it escapes `run_service` too and lands in `cli.main`'s top-level
`except Exception`, which prints `ges2t: unexpected failure: can't start new
thread` + the `--debug` hint and returns `EXIT_FAILURE`. `ges2t run` therefore
**refuses to start** — usable-looking single-threaded work is impossible —
because a thread could not be created for a decorative tray. That is ADR 0013's
forbidden outcome.

I reproduced every step without a real icon (injected fake icon into
`TrayStatusUI`; probe in `/tmp/probe_angle_a.py`, `uv run python`, displays
unset):

```
1) TrayStatusUI.start() escaped: RuntimeError: can't start new thread
2) build_status_ui escaped:      RuntimeError: can't start new thread
4) run_service line: 'ui = status_ui if status_ui is not None else build_status_ui(config)'
   next non-empty lines: ['try:', 'service = _startup(config, ui)']
```

And it is a **regression this commit introduced**: I restored the old
`except Exception`, re-ran the same `RuntimeError` injection, and got

```
ges2t: the tray could not be created (can't start new thread)
  the tray is disabled; ges2t remains fully usable from the CLI
old clause returned: NullStatusUI
```

then reverted (`sha256sum -c` OK on all four probed files, `git status` clean).
So before 40b5ccd this case degraded correctly; after it, it aborts startup.

**The implementer's defence is wrong on reachability.** The addendum says a
thread-start failure "cannot be named specifically" and that wrapping
`thread.start()` is "outside `build_status_ui`'s reach". The first half is true
and the second is a non-sequitur: the operation is reachable one layer down, in
`TrayStatusUI.start()`, which is *this ticket's own file*. A local, specific
catch at the failing operation is exactly the right shape:

```python
thread = threading.Thread(target=self._run_icon, name="ges2t-tray", daemon=True)
self._thread = thread
try:
    thread.start()
except RuntimeError as exc:  # OS refused a new thread: environmental, not a bug
    raise TrayBackendError(f"the tray thread could not be started ({exc})") from exc
```

`build_status_ui`'s existing `except TrayBackendError` branch already turns that
into `NullStatusUI` + the two-line warning, so no other change is needed. This
is **local and specific**: it wraps only the single failing call, so a
programming `RuntimeError` anywhere else still propagates, and it does **not**
re-open the 002 blocking finding (catching bare `RuntimeError` at
`build_status_ui` would). One wrinkle to fix alongside: `TrayBackendError`'s
docstring/`_backend_error` wording says "`pystray` would not select the visible
AppIndicator backend"; a thread-start failure is not that, so the message set
should be widened (or a sibling `TrayStartError`) rather than silently
overloaded.

**Why blocking, not a note.** Three reasons, weighed against the fact that the
trigger is rare (thread/`RLIMIT_NPROC` exhaustion, which is genuinely unlikely):
(1) it is an *ADR violation introduced by the fix under review*, not a pre-
existing one; (2) review 002 §2's recommended shape explicitly included "the
`RuntimeError` from a failed thread start" in the environmental set, so the fix
is knowingly incomplete; (3) the remedy is ~4 lines in the ticket's own file
plus one test, far smaller than the cost of a papered-over ADR breach. This is
consistent with 002 blocking the (also theoretical) swallowed-programming-error
case. I am recording it as blocking; if the orchestrator decides the residual
risk is acceptable, it must be an explicit recorded follow-up **with the local
wrap of `thread.start()` named as the fix**, not dropped.

---

## 5. Angle B — the other broad catch in `run_service`

`src/ges2t/pipeline.py:408`, unchanged in shape since it was written:

```python
try:
    service = _startup(config, ui)
except PipelineError as exc:
    _shutdown_status_ui(ui)
    _print_failure(exc)
    return _EXIT_FAILURE
except Exception as exc:  # startup failure: report, never traceback at the top level
    _shutdown_status_ui(ui)
    print(f"ges2t: could not start the service: {exc}", file=sys.stderr)
    print("  hint: run 'ges2t setup' to (re)install the pinned model and engine, then try again", file=sys.stderr)
    return _EXIT_FAILURE
```

**Provenance first, because it decides the verdict.** `git blame` shows the
`except Exception` clause is from `76008fa` (ticket 11, dictation-pipeline-batch)
and is **not** touched by this branch; ticket 17 only added the
`_shutdown_status_ui(ui)` line inside it (`fea1ecb`). So this is a pre-existing
catch, not something the fix introduced or is obliged to repair.

**Does it swallow programming errors?** Yes, in the narrow sense of catching
them and re-labelling them — but, unlike `build_status_ui`, it does **not**
continue. I injected a genuine `AttributeError` into `_startup` (probe
`/tmp/probe_angle_b.py`, no display, no model):

```
exit code: 1
stderr:
ges2t: could not start the service: 'NoneType' object has no attribute 'engine'
  hint: run 'ges2t setup' to (re)install the pinned model and engine, then try again
```

The bug is not hidden (non-zero exit, a message is printed), but the diagnosis
is wrong in two ways: the **exception type is dropped** (`str(exc)` never names
it), and the **`ges2t setup` hint is actively misleading** for a coding bug.
Worse, the traceback is discarded *before* `cli.main` is reached, so the
supported `--debug` escape hatch prints nothing extra — `main`'s
`traceback.print_exc()` only runs for exceptions that propagate to it, and this
one does not. A genuine startup bug is therefore not just under-reported, it is
**un-debuggable through the documented path**.

**Why the catch nevertheless has a real job.** `_startup` genuinely raises
environmental startup failures that are *not* `PipelineError`:
`WhistleLoadError` (weights/engine mismatch, ADR 0007), `ModelStoreError`,
`ImportError` from `_import_needle()`, and plain `OSError` from loading. Those
deserve exactly the concise message + `setup` hint this handler prints. So this
is a purposeful environmental handler that happens to also be a catch-all, not
the pointless blanket `build_status_ui` had.

**Ruling: acceptable to leave for this ticket — non-blocking, recorded
follow-up.** The decisive difference from the 002 blocking finding is that this
path **aborts**; it never continues with a quietly degraded system, so the
silent-degradation class ADR 0013 exists to prevent is absent. Combined with its
pre-existing provenance (repairing it here is unrelated scope creep) and the
fact that a startup failure is already fatal by design, it does not block the
tray merge.

Recommended follow-up (small, for the pipeline owner): keep the fatal handler
but stop lying about it — at minimum print
`f"ges2t: could not start the service: {type(exc).__name__}: {exc}"` so the type
survives, and preferably keep the traceback reachable for `--debug` (either
re-raise after printing, letting `cli.main`'s existing debug branch show it, or
print it here when `--debug` is set). That preserves the concise default while
making a real startup bug diagnosable. Not required to merge ticket 17.

---

## 6. FIX 3 — Ayatana typelib accepted, core check untouched

**Core module check is byte-identical — verified, not assumed.**
`git diff 003bcaf..40b5ccd -- src/ges2t/ui/tray.py` is 3 hunks / `+21 −7`, all at
the typelib site:

- `@@ -57,6 +57,9 @@` — adds
  `_APPINDICATOR_TYPELIBS = ("AppIndicator3", "AyatanaAppIndicator3")`.
- `@@ -106,9 +109,13 @@` — check‑1 docstring only.
- `@@ -120,12 +127,19 @@` — replaces the single
  `gi.require_version("AppIndicator3", "0.1")` with the `_APPINDICATOR_TYPELIBS`
  loop, and splits the `import gi` except out.

No hunk touches check 2. Extracting that block from both revisions:

```
$ git show 003bcaf:src/ges2t/ui/tray.py | sed -n '135,140p' | sha256sum
016c20b0cc76040bb6b38881f065c2f639ba3162a65e09fb37428b14edac4515  -
$ sed -n '149,154p' src/ges2t/ui/tray.py | sha256sum
016c20b0cc76040bb6b38881f065c2f639ba3162a65e09fb37428b14edac4515  -
```

`selected = getattr(pystray.Icon, "__module__", None)` / `if selected !=
_APPINDICATOR_MODULE:` is unchanged, as is the `_APPINDICATOR_MODULE` constant.

**`_xorg`, `_gtk` and `_dummy` are still rejected (fake modules, no icon, no
display).** Probe `/tmp/probe_typelibs.py`, run
`env -u DISPLAY -u WAYLAND_DISPLAY uv run python`, injecting a fake `gi` (every
namespace available) and a fake `pystray` whose `Icon.__module__` is the candidate:

```
pystray._xorg        -> TrayBackendError named_in_message=True
pystray._gtk         -> TrayBackendError named_in_message=True
pystray._dummy       -> TrayBackendError named_in_message=True
ayatana-only         -> accepted
preference order calls: [('AppIndicator3', '0.1')]
neither typelib      -> TrayBackendError
ALL PROBE ASSERTIONS PASSED
```

The typelib leniency does not weaken the module guard: a non-AppIndicator backend
is refused whatever `gi` reports, and only `pystray._appindicator` passes. Check 1
prefers `AppIndicator3` and falls back to Ayatana only when it raises `ValueError`.

**Ayatana test falsified (narrowed → failed → restored).** I narrowed
`_APPINDICATOR_TYPELIBS` to `("AppIndicator3",)` (caches cleared first) and ran:

```
$ env -u DISPLAY -u WAYLAND_DISPLAY timeout 120 uv run pytest \
    "tests/test_tray_backend.py::test_assertion_accepts_ayatana_when_appindicator3_is_absent" -q
FAILED … 1 failed in 0.19s   EXIT=1
E  TrayBackendError: … 'gi' has neither the AppIndicator3 nor the AyatanaAppIndicator3 typelib
```

It fails precisely because the fallback is gone, so it genuinely pins the Ayatana
acceptance. Restored with `git checkout` and caches cleared again:

```
$ sha256sum src/ges2t/ui/tray.py
1d38e2cf8537349dc73ce36e87bd590e1ea18de68243bfb21434a02d80639716  src/ges2t/ui/tray.py
$ git status --porcelain -- src/ tests/     # empty
$ … Ayatana test -q
1 passed in 0.15s    EXIT=0
```

The working-tree file is byte-identical to `HEAD`
(`git rev-parse HEAD:src/ges2t/ui/tray.py` = `b8b73735d25ff6aa40b661beb45f3da546c69c79`).

**FIX 3 verdict: RESOLVED.** The acceptance is genuine and falsifiable, and the
core AppIndicator guard is untouched.

---

## 7. Scope, counts, ADR 0019

**Scope — only the declared files.** Full branch `git diff main...HEAD --name-only`:

```
docs/project/README.md
docs/project/live-dictation-ux/tray-status-ui/001-comment.md
docs/project/live-dictation-ux/tray-status-ui/002-review.md
docs/project/live-dictation-ux/tray-status-ui/ticket.md
src/ges2t/pipeline.py
src/ges2t/ui/__init__.py
src/ges2t/ui/status.py
src/ges2t/ui/tray.py
tests/test_pipeline.py
tests/test_status_ui.py
tests/test_tray_backend.py
```

Exactly `pipeline.py`, `src/ges2t/ui/*`, the tray tests, `tests/test_pipeline.py`
and the ticket's own docs (plus the `docs/project/README.md` board row). Nothing
outside the ticket's declared scope. The fix commit `003bcaf..40b5ccd` is narrower
still: `001-comment.md`, `pipeline.py`, `ui/tray.py`, `test_pipeline.py`,
`test_tray_backend.py` (`+373 −15`).

**No pytest timeout plugin, no config change.** `pyproject.toml` has no
`[tool.pytest.ini_options]` (its only pytest reference is the `"pytest"` dev
dependency); `pytest.ini`, `setup.cfg`, `tox.ini`, `conftest.py` and
`tests/conftest.py` do not exist; `pytest_timeout` is not installed (`import
pytest_timeout` → `ModuleNotFoundError`); no `addopts`/`markers`/timeout setting
anywhere. `git diff main...HEAD` touches no config or lock file. The suite exits on
its own (`549 passed in 2.73s`); nothing was added to mask a hang.

**Counts: 537 → 549, additive only.**

```
$ git diff 003bcaf..40b5ccd | grep '^-' | grep 'def test_'          # NONE removed
$ git diff 003bcaf..40b5ccd | grep '^+' | grep 'def test_'          # 6 added functions
$ git diff 003bcaf..40b5ccd -- tests/ | grep '^-' | grep -v '^---'
-from ges2t.ui.status import NullStatusUI
-    def __init__(self, *, exc: Exception | None = None) -> None:
```

Only two pre-existing lines change, both additive in effect: the import gains
`TrayFailure`, and `_FakeGi.__init__` gains an optional
`unavailable: frozenset[str] = frozenset()` keyword (default preserved, existing
calls unaffected). No assertion is removed or loosened, and no `skip`/`xfail`
marker is added anywhere — the only `skip`/`xfail` string in the diff is prose in
`001-comment.md` (`"none deleted, skipped, xfailed or weakened"`). Six new
functions = four unparametrized (`Ayatana`, `neither-typelib`, normal path, serve
error) plus `[3]` environmental + `[5]` programming = **+12 collected tests**;
`549 − 12 = 537`, the recorded baseline. The suite re-runs green:
`549 passed in 2.73s`, exit 0.

**mypy.**

```
$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 39 source files                       # exit 0
```

**ADR 0019 holds in the new test code.** Every new `def` (test and helper) carries
full parameter and return annotations including `-> None`; `_FakeGi.__init__`'s new
keyword is annotated; the inner fakes are `-> object` / `-> None`; the new
`_DiagnosticStatusUI` methods use `-> TrayFailure | None` and friends. No
positional tuple is returned (`git diff … | grep -e 'return (' -e '-> tuple'` →
NONE). The only unannotated callables are throwaway
`lambda config: …` / `lambda self: None` arguments to `monkeypatch.setattr` (Python
forbids a return annotation on a lambda) — the "not a throwaway" exception, and
`mypy` accepts them.

---

## What I could not verify / disclosures

- **The manual desktop tray check was never run.** It is user-owned and its DoD
  box is unticked. I did not create a real tray icon at any point, did not run
  `ges2t run`, opened no terminal or GUI window, and used no display, keystrokes,
  clipboard, `sudo`, or network change. Every probe used fake `gi`/`pystray`
  modules and fake icons, so nothing here proves a real icon appears on Plasma 6.
- **The real Ayatana-only desktop is unverified.** The Ayatana acceptance (§6) is
  proven only against a fake `gi` that raises `ValueError` for `AppIndicator3`;
  this machine has no display and no Ayatana typelib, so the fallback was not
  exercised against real `gi`/`pystray`.
- **Angle A (§4) is a reproduction by injected fakes, not by inducing the OS
  condition.** I injected `RuntimeError("can't start new thread")` at
  `thread.start()`; I did not exhaust threads/`RLIMIT_NPROC` for real. The static
  chain (`RuntimeError` ∉ the caught tuple; `build_status_ui` called outside any
  `try` in `run_service`; `cli.main`'s top-level handler) was read from source.
- **Angle B (§5) used an injected `AttributeError`** in `_startup`, not a real
  startup bug; the traceback-suppression claim follows from `cli.main`'s structure.
- **The 537 baseline is derived, not re-run.** I did not check out `003bcaf` and
  run pytest there; `537 = 549 − 12` new collected tests, cross-checked against the
  per-file `def test_` counts (`test_pipeline.py` 22→24, `test_tray_backend.py`
  7→11, `test_status_ui.py` 13→13) and §1's recorded base.
- **This file (`003-review.md`) was untracked before this commit**, so it is not
  part of the branch diff inspected above.

No probe mutated a committed file at the end state: after both falsifications
`git status --porcelain -- src/ tests/` is empty, and `src/ges2t/ui/tray.py`,
`src/ges2t/pipeline.py` and both test files are byte-identical to `HEAD`.
