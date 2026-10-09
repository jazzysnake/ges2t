# 003 — re-review: B1 containment fix (a raising `StatusUI` must not abort a transition)

**Ticket:** `status-ui-outside-lock` · **Branch:** `feat/status-ui-outside-lock` · **HEAD:** `080a2a4`
**Role:** re-reviewer. The original review (`002`) is accepted in full; this review only judges the
fix for its one blocking finding and hunts for regressions the fix introduced. Every probe was a
throwaway under `/tmp` (removed); the branch is clean.

## Verdict: **pass — B1 is resolved.**

`002`'s blocking finding **B1** was: the `_transition_scope` display drain sits in the context
manager's `finally`, outside `handle`'s defensive `try/except`, so a raising `status_ui` callback
aborted the deferred side effect that follows the scope and (on `RECORDING → TRANSCRIBING`) left the
service **permanently wedged in `TRANSCRIBING`**. The fix wraps each queued display action in
`try/except Exception: continue`. I reproduced the wedge with my own independent probe on the
reverted code and showed it is gone with the fix; I confirmed the guard swallows **only** the
queued `status_ui` callbacks, not `session.start/finish/cancel` or `injector.inject`; and I confirmed
the drain still runs **after** the lock is released and still runs on the exception path.

No new blocking findings. One non-blocking follow-up on silent display failures (§4).

---

## 1. B1 is genuinely gone — my own pre-fix / post-fix reproduction

I did not rely on the implementer's tests. I wrote an independent probe (`/tmp/probe_rereview.py`,
removed) with my own collaborators, drove `IDLE → RECORDING → TRANSCRIBING` with a `StatusUI` whose
`transcribing()` raises `RuntimeError`, and ran it against `2220dd1`'s `service.py` (guard reverted)
and against `080a2a4` (HEAD).

### Pre-fix (guard reverted, `git checkout 2220dd1 -- src/ges2t/service.py`)

```
=== A. wedge: raising status_ui.transcribing on RECORDING+toggle ===
  first toggle            : OK recording
  second toggle           : RAISED RuntimeError('status_ui.transcribing exploded')
  state                   : transcribing
  finish called           : False
  worker alive            : False
  later status            : OK transcribing err= None
  later cancel            : OK transcribing err= busy
  final state             : transcribing  raised= ['transcribing']
```

That is the wedge: the reply raises, `worker.start()` never runs, `finish()` is never called, and
every later command answers `busy` until restart.

### Post-fix (HEAD `080a2a4`)

```
=== A. wedge: raising status_ui.transcribing on RECORDING+toggle ===
  first toggle            : OK recording
  second toggle           : OK CommandResponse(ok=True, state=<ServiceState.TRANSCRIBING: 'transcribing'>, error=None, message=None)
  state                   : idle
  finish called           : True
  worker alive            : False
  later status            : OK idle err= None
  later cancel            : OK idle err= not-recording
  final state             : idle  raised= ['transcribing']
```

The deferred side effect ran (`finish()` called), the worker completed, and the service returned to
`IDLE`; a later `status` is `ok`/`IDLE`, not `busy`. B1 is gone.

### Test-level confirmation (the implementer's five tests)

```
$ git checkout 2220dd1 -- src/ges2t/service.py
$ uv run pytest tests/test_state_machine.py -k "raising_status_ui" -q --tb=line
E   RuntimeError: status_ui.transcribing exploded
... (one RuntimeError per test, all from the StatusUI)
FAILED tests/test_state_machine.py::test_raising_status_ui_transcribing_does_not_wedge_the_service
FAILED tests/test_state_machine.py::test_raising_status_ui_recording_does_not_change_a_start
FAILED tests/test_state_machine.py::test_raising_status_ui_error_does_not_mask_a_start_failure
FAILED tests/test_state_machine.py::test_raising_status_ui_idle_still_cancels_the_session
FAILED tests/test_state_machine.py::test_raising_status_ui_does_not_skip_shutdown_cleanup
5 failed, 47 deselected, 1 warning in 0.08s
```

The implementer's stated `5 failed, 47 deselected in 0.08s` is exactly right (my earlier run without
`--tb` finished in 0.04s). The five failures are the intended `RuntimeError`s, not incidental
assertions. Restored and re-run:

```
$ git checkout HEAD -- src/ges2t/service.py      # git status clean
$ uv run pytest tests/test_state_machine.py -k "raising_status_ui" -q
5 passed, 47 deselected in 0.03s
```

### The new tests fail loudly, they do not hang — confirmed

Every affected command in the five tests is wrapped in `call_with_timeout(..., 1.5)` and cleanup in
`shutdown_with_timeout` (1.0 s), both of which `join(timeout=...)` a daemon thread and raise an
`AssertionError` on expiry; `wait_for(..., timeout=1.5)` polls to a deadline. The one unwrapped
`service.handle("toggle")` (in `test_raising_status_ui_does_not_skip_shutdown_cleanup`) cannot hit the
raising callback (`raise_on=["idle"]`, and entering `RECORDING` queues `recording`), so it cannot
hang; the pre-fix run above completed in 0.08 s. No new unbounded wait was introduced.

## 2. Did the fix swallow too much? — **No.** Functional collaborators still reach `ERROR`

I constructed each named collaborator failure and ran it under the fix:

```
=== B. do real collaborator failures still reach ERROR? ===
  raising start   -> reply=OK state=error err=start-failed error_ui_calls=1 final_state=error
  raising finish  -> state= error  error_ui_calls= 1
  raising cancel  -> state= error  error_ui_calls= 1  cancel called= True
  raising inject  -> state= error  error_ui_calls= 1
```

All four still move the service to `ERROR` and invoke `status_ui.error` exactly once. The
pre-fix probe is byte-for-byte identical for these four, so the fix changed none of them.

The reason is structural: the drain iterates only the local `display` list, and the only writer of
that list is `_set_state`, which queues `self._status_ui.idle/recording/transcribing/error` (or a
`functools.partial` of `error`) and nothing else:

```
$ grep -n "_pending_display\|_set_state(" src/ges2t/service.py
198:        self._pending_display: list[Callable[[], None]] | None = None
433:                self._pending_display = display
437:                    self._pending_display = None
458:        pending = self._pending_display
...  (every _set_state call site)
```

No `session.*` or `injector.*` callable is ever placed in the queue, so no functional call can be
caught by the guard. Confirmed analytically and by experiment.

## 3. `except Exception` breadth and masking — **correct, and masking is strictly reduced**

- **`BaseException` propagates.** The guard catches `Exception` only, so `KeyboardInterrupt` and
  `SystemExit` raised by a display callback still propagate (probe D):

  ```
  === D. BaseException (KeyboardInterrupt) from a display callback ===
    toggle (recording raises KI): RAISED  state=recording
    cancel (idle raises KI)     : RAISED  state=idle
  ```

  This is the right call: swallowing `KeyboardInterrupt`/`SystemExit` from a callback would be worse
  than the bug. A non-contractual `BaseException` from a tray is out of scope and should surface.
  The boundary to be aware of: a display callback that raises `BaseException` can still abort the
  remaining queued drains and propagate out of `handle`; this matches the pre-existing contract that
  only `Exception` is tolerated from collaborators (`handle`'s own defensive clause is also
  `except Exception`).

- **A raising display no longer masks an in-flight real error — it now un-masks it.** The
  orchestrator's exact scenario: the deferral raises *and* the resulting display callback raises.
  Post-fix the real error still reaches the caller; pre-fix it did not:

  ```
  === C. real start error AND raising status_ui.error (both in flight) ===
  POST-FIX  reply : OK error start-failed could not start recording: no audio device
            state : error  ui.raised= ['error']
  PRE-FIX   reply : RAISED (the display exception escaped handle; the start-failed
            response and its message were lost)
  ```

  Same result for a body exception whose queued `error` display also raises (probe E3):

  ```
  POST-FIX  E3 body+error-display raise -> reply=OK err=internal-error raised=['error'] state=error
  PRE-FIX   E3 body+error-display raise -> reply=RAISED err=None raised=['error'] state=error
  ```

  So the fix does not merely avoid masking a real error — it repairs a second instance of the same
  `002` defect class (the real error response was previously replaced by the display exception).

The guard also wraps each action individually, so one raising callback no longer prevents the rest
of the queue from draining. The `continue` is redundant (it is the last statement of the loop body)
but harmless; a style nit, not a defect.

## 4. Does the fix weaken the original invariant? — **No**

The drain still runs after `with self._lock` exits, and still runs on the exception path:

```
=== E. drain after lock released; drained on exception path ===
  E1 state-read-back + cancel: ok, under-lock display calls: []
  E2 body raises -> reply=OK err=internal-error state=error ui.calls=['idle', 'error']
```

E1 replaced `Service._lock` with a lock wrapper that records the owning thread, wrapped every
`status_ui` transition to (a) flag any call made by the current lock owner and (b) read
`service.state` back (the tray pattern), and drove `toggle` + `cancel`: **zero** under-lock display
calls, and no deadlock. E2 forced the body (`_dispatch_locked`) to queue an `idle` display and then
raise; both the pre-raise `idle` and the resulting `error` displays were still drained. The
predecessor's three `reading_state_back` deadlock tests still pass (`3 passed, 49 deselected in
0.06s`), and the full transition table is unchanged (§5).

## 5. Mechanical claims — **confirmed**

- **Test counts.** `uv run pytest` → `355 passed in 0.94s`; `uv run pytest tests/test_state_machine.py`
  → `52 passed`. Per-file collection: state-machine 52 + socket (`test_socket_proto.py`) 29 = **81**
  state-machine+socket, up from `002`'s 76 (47 + 29); total 355, up from 350. Matches the addendum.
- **Additions only.** `git diff 2220dd1..HEAD --numstat -- tests/` → `166 0`; `git diff | grep '^-'`
  yields no removed lines. Across the whole ticket (`f90fc35..HEAD`) the test file is `316 0`. An
  existing test cannot have been modified, weakened, or deleted.
- **`mypy` (ADR 0019).** `uv run mypy src/ges2t tests` → `Success: no issues found in 29 source files`
  (exit 0). Config-bites probe: adding `tests/_probe_untyped.py` with `def probe_untyped(x): return x`
  gives `error: Function is missing a type annotation [no-untyped-def]`, exit 1; removing it restores
  exit 0 and a clean `git status`.
- **Scope.** `git diff f90fc35..HEAD --name-status`:
  ```
  M docs/project/README.md
  A docs/project/core-dictation-pipeline/status-ui-outside-lock/001-comment.md
  A docs/project/core-dictation-pipeline/status-ui-outside-lock/002-review.md
  M docs/project/core-dictation-pipeline/status-ui-outside-lock/ticket.md
  M src/ges2t/service.py
  M tests/test_state_machine.py
  ```
  The only `src/` file is `service.py`. The only board change is row 21 (`to do` → `in review`,
  branch set); the fix commit (`2220dd1..HEAD`) touches no README. No conflict markers.
- **Hygiene.** `git status` is clean; no `ges2t` process or socket was left behind (the only
  `/tmp/ges2t*` files are the ticket-9 `ges2t_measure*.py` leftovers, predating this review; the one
  running `pytest` is in the unrelated `xkb-keycode-mapping` worktree). My probe was under `/tmp`
  and is removed.
- **Minor doc nit (non-blocking).** The addendum says the fix is `src/ges2t/service.py +13/−1`; the
  actual numstat is `+12/−1` (13 is git's changed-line column). Cosmetic.

## 6. Verdict on the silent-swallow question — **not blocking; require a follow-up diagnostic**

The fix swallows a raising display callback with no log, no state change, no notification
(`except Exception: continue`). A `StatusUI` whose callbacks *always* raise would therefore stop
updating the tray **invisibly**: no error, no notification, no diagnostic, no `ERROR` state.

I weighed both sides:

- **For silence:** it mirrors the file's existing partials policy
  (`_start_partials_locked`'s `except Exception: return`), `002` explicitly allowed "`pass` (or
  log)", and the codebase has **no logging convention at all** — `grep -rn "import logging" src/ges2t`
  is empty, so introducing one here would be a new pattern for a single call site.
- **Against silence:** the project has repeatedly chosen to fail loudly. ADR 0013 exists precisely to
  prevent a *silently invisible tray*; ADR 0015 says failures must "produce an `ERROR` state plus a
  notification"; the injection workstream exists because `ydotool` silently corrupted text. A tray
  that never updates is the ADR-0013 failure class in miniature.

**My verdict: silent containment is the *correct safety* choice but the *incomplete observability*
choice, and I rate the missing diagnostic a non-blocking follow-up, not a blocker.** Reasons: (1)
containment is mandatory — the alternative wedges the service; (2) the tray is explicitly a
non-essential surface with a CLI fallback (ADR 0013: "Nothing may be reachable only through the
tray"), so degrading it cannot lose a control path; (3) `002`, whose verdict is accepted, already
permitted `pass`; (4) making a non-essential display path transition the whole service to `ERROR`
would be the wrong anti-silent response and would violate ADR 0013's "still fully usable from the
CLI". Holding the correct fix for an observability nicety would be disproportionate.

**Minimal follow-up fix (do not fold into this ticket):** have the service *record* display failures
and surface them where the project already surfaces diagnostics — e.g. keep a monotonic
`display_failures` count and the last exception string, expose them through `ges2t doctor` (ticket 12)
and/or the `status` response; a one-shot `warnings.warn` in the drain is the smallest acceptable
alternative. This continues the "surface failures" pattern without inventing a dependency or
routing a cosmetic failure into `ERROR`. It should be filed against ticket 12/17 (tray), not treated
as a defect in this containment fix.

## 7. Explicitly not verified / limitations

- I did not re-run `002`'s full lock-ownership instrumentation sweep (accepted as-is); I only
  re-confirmed the drain ordering and exception-path draining, plus the predecessor's deadlock tests.
- I did not exercise a real `pystray` tray; display failures use synthetic `StatusUI`s, as in `002`.
- I did not fuzz scheduler interleavings; the scenarios tested are the enumerated pre/post-fix ones.
- The `BaseException` behavior of a *non-contractual* display callback that raises
  `KeyboardInterrupt`/`SystemExit` is reported but not guarded; whether the socket loop should catch
  those is a service-wide question outside this ticket.
