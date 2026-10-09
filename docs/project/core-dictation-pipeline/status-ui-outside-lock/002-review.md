# 002 — review: call StatusUI (and every other collaborator) outside the state lock

**Ticket:** `status-ui-outside-lock` · **Branch:** `feat/status-ui-outside-lock` · **HEAD:** `2934e9c`
**Reviewer:** adversarial, instrumented review. Every probe was a throwaway outside the repo (or
a temporary edit reverted immediately); the branch is clean and no service process or socket was
left behind.

## Verdict: **changes requested — one blocking finding (B1).**

The ticket's stated definition of done is met and its central claim is true: I proved
mechanically that **no collaborator is called while `Service._lock` is held** across every
transition-table cell, both the normal and exception paths, and the entire 350-test suite
(**0 violations**). The regression test genuinely bites (3 failures in 7.75 s when the fix is
reverted), the audit is complete (the implementer's "worse than reported" claim is correct), the
transition table is unchanged, and `uv run mypy src/ges2t tests` exits 0.

The blocking finding is not the lock invariant — it is in the new `_transition_scope` exception
handling. The display drain runs in the context manager's `finally`, **outside** `handle`'s
defensive `try/except`, so a raising `status_ui` callback is not contained: it aborts the
deferred side effect that follows the scope. On the `RECORDING → TRANSCRIBING` path that means
`worker.start()` never runs and the service is **permanently wedged in `TRANSCRIBING`** (every
later command answers `busy`) — a regression from the pre-fix behaviour, which recovered to
`ERROR`. The same mechanism skips `session.cancel()` on the cancel path and in `shutdown()`.
The fix is small (swallow display exceptions inside the drain, as `_start_partials_locked`
already does for partials). If the project judges a raising `StatusUI` out of contract, this can
be downgraded to a follow-up, but the codebase's own precedent and ADR 0015's "service outlives
failures" argue against that, and ticket 17's tray is precisely the collaborator most likely to
raise.

---

## 1. Invariant: no collaborator called under the lock — **proven, 0 violations**

I did not trust the code. I wrote an instrumented harness (throwaway, `/tmp`, removed) that:

- **replaced `Service._lock`** with a `TrackingLock` that records the owning thread id on
  `acquire()` and clears it on `release()` (a plain `threading.Lock` has exactly one owner);
- **wrapped every collaborator** the ticket names:
  - `status_ui.idle/recording/transcribing/error/partial`,
  - `injector.inject`,
  - the **session factory** (`self._session_factory`),
  - **every session method** (`start/finish/cancel/partials`) returned by the factory;
- before each wrapped call it checked `lock.owner_id() == threading.get_ident()` and recorded a
  violation with a stack if the **calling thread** held the lock.

The check is per-calling-thread on purpose: a display call from the partials thread while
another thread holds the lock is by design (display-only), whereas a call from the lock holder
is the defect.

### Harness sweep (standalone): every transition-table cell + exception paths

All 20 cells from the transition table under the normal path, all 20 under a `status_ui` that
raises on the transition, and the named exception paths:

```
=== every transition-table cell, normal path ===
[OK ] idle         + toggle / ptt-start / ptt-stop / cancel / status   violations=0
[OK ] recording    + toggle / ptt-start / ptt-stop / cancel / status   violations=0
[OK ] transcribing + toggle / ptt-start / ptt-stop / cancel / status   violations=0
[OK ] error        + toggle / ptt-start / ptt-stop / cancel / status   violations=0

=== every transition-table cell, status_ui raises on the transition ===
(20 cells) violations=0   (the RuntimeError raised is the deliberate UI failure)

=== named exception paths ===
[OK ] start raises (IDLE+toggle)                           violations=0
[OK ] factory raises (IDLE+toggle)                         violations=0
[OK ] start raises (queued restart)                        violations=0
[OK ] finish raises (RECORDING+toggle)                     violations=0
[OK ] inject raises (RECORDING+toggle)                     violations=0
[OK ] cancel raises (RECORDING+cancel)                     violations=0
[OK ] status_ui.error raises (start-error path)            violations=0
[OK ] status_ui.transcribing raises (RECORDING+toggle)     violations=0

=== collaborator entry points exercised ===
  injector.inject
  session.cancel
  session.finish
  session.partials
  session.start
  session_factory
  status_ui.error
  status_ui.idle
  status_ui.recording
  status_ui.transcribing
TOTAL UNDER-LOCK VIOLATIONS: 0
```

### Second, independent sweep: the whole suite under instrumentation

I ran the same instrumentation as a pytest plugin over all 350 tests. Every test passed and the
plugin's per-test teardown found no violation:

```
$ PYTHONPATH=/tmp uv run pytest -p instr_plugin -q
........................................................................ [ 20%]
........................................................................ [ 41%]
........................................................................ [ 61%]
........................................................................ [ 82%]
..............................................................           [100%]

instr: collaborator entry points exercised: ['injector.inject', 'session.cancel',
 'session.finish', 'session.partials', 'session.start', 'session_factory',
 'status_ui.error', 'status_ui.idle', 'status_ui.partial', 'status_ui.recording',
 'status_ui.transcribing']
instr: TOTAL UNDER-LOCK VIOLATIONS: 0
350 passed in 1.37s
```

Both sweeps independently exercised all eleven entry points. **No under-lock collaborator call
remains.**

## 2. Falsification of the regression test — **reproduced (3 failed, 7.75 s)**

I reverted only `src/ges2t/service.py` to its pre-fix state and ran the new tests:

```
$ git checkout HEAD~1 -- src/ges2t/service.py
$ uv run pytest tests/test_state_machine.py -q -k reading_state_back
...
E           AssertionError: shutdown did not return within 1.0s
FAILED tests/test_state_machine.py::test_status_ui_reading_state_back_does_not_deadlock
FAILED tests/test_state_machine.py::test_status_ui_reading_state_back_on_error_does_not_deadlock
FAILED tests/test_state_machine.py::test_session_reading_state_back_does_not_deadlock
3 failed, 44 deselected in 7.75s
```

The implementer's claim ("3 failed … in 7.71s") is accurate to the tenth of a second. I then
restored the file completely:

```
$ git checkout HEAD -- src/ges2t/service.py
$ git status --porcelain        # empty
$ uv run pytest tests/test_state_machine.py -q -k reading_state_back
3 passed, 44 deselected in 0.05s
```

## 3. The regression test fails loudly, it does not hang — **confirmed**

The three tests run each command on a worker thread with `thread.join(timeout=1.5)`
(`call_with_timeout`) and `shutdown` on a worker thread with `join(timeout=1.0)`
(`shutdown_with_timeout`); the worker's drain is observed with a deadline loop (`wait_for`).
Nothing joins unboundedly. With the bug present the command thread is wedged on the lock, the
bounded joins expire, and the test raises an `AssertionError`; pytest collected and finished in
**7.75 s**. No hang. This directly answers the concern that a mutation run already cost the
project an hour.

## 4. Audit completeness — **claim verified; no other offender exists**

The implementer claims the bug was worse than reported and that `_start_session_locked` also
called the factory and `session.start()` under the lock. The diff confirms it: pre-fix
`_start_session_locked` did

```python
try:
    session = self._session_factory()
    session.start()
...
self._session = session
...
self._set_state(ServiceState.RECORDING)
```

inside `_dispatch_locked`, which ran inside `handle`'s `with self._lock`. So there were two
offenders, not one.

I independently enumerated **every** collaborator reference in the current `service.py`
(`self._status_ui`, `self._injector`, `self._session_factory`, `session.start/finish/cancel/
partials`) and mapped each to its lock context:

| call site | context | lock held? |
|---|---|---|
| `_commit_start`: `self._session_factory()`, `session.start()` | `handle`'s `decision.after()` or `_complete_transcription` after its scope | no |
| `_cancel_locked`'s `after`: `session.cancel()` | `handle` after the scope | no |
| `_run_finish`: `session.finish()` | `ges2t-finish` worker thread | no |
| `forward()`: `session.partials()`, `status_ui.partial()` | `ges2t-partials` thread | no |
| `shutdown`: `session.cancel()` | after the scope | no |
| `_set_state`: `status_ui.idle/recording/transcribing/error` | **only queued** into `_pending_display`; invoked by `_transition_scope` after release | no |

The audit is complete: two offenders found and fixed, none remaining (also confirmed
mechanically in §1).

## 5. Behaviour unchanged; tests are additions only — **confirmed**

- `git diff HEAD~1 HEAD --numstat -- tests/test_state_machine.py` → `150  0` (150 insertions,
  0 deletions). An existing test cannot have been modified, weakened, or deleted.
- `git diff HEAD~1 HEAD -- tests/test_state_machine.py | grep '^-'` → only the `---` header.
- Counts, verified against an archived `HEAD~1` tree (`git archive HEAD~1 src tests`):

```
pre-fix  : 347 passed   (state-machine+socket: 73 = 44 + 29)
post-fix : 350 passed   (state-machine+socket: 76 = 47 + 29)
```

The transition table is unchanged: the 20 parametrized cells pass identically, and the full
suite is green.

## 6. `_transition_scope` design — **queue/drain mechanics sound, exception path is not: B1**

The positive mechanics are correct:

- The queue is a **local list** per scope; `self._pending_display` is set and cleared **inside**
  the lock, so two threads never share it.
- The lock is always released before the drain (`with self._lock` exits first).
- The drain runs on both the normal and the exception path (`finally`), so a body exception
  cannot leave the queue un-drained.
- After a raised drain, `service._pending_display` is `None` (verified), so nothing leaks into a
  later transition.
- A raising `session.start()` is handled correctly: `_commit_start` catches it, transitions to
  `ERROR`, and the service recovers (existing test passes; harness shows 0 violations).

The defect is that the drain action is the **last statement of the context manager**, and
`handle`'s defensive `except Exception` wraps only the body:

```python
with self._transition_scope():        # <-- drain raises at __exit__
    try:
        decision = self._dispatch_locked(command)
    except Exception as exc:          # <-- does NOT catch the drain
        ...
if decision.after is not None:        # <-- skipped when the drain raised
    decision.after()                  #     worker.start() / session.cancel()
```

I constructed a `status_ui` that raises on the transition and observed (post-fix vs pre-fix):

```
=== status_ui.transcribing raises on RECORDING + toggle ===
POST-FIX: handle RAISES RuntimeError; state = transcribing; worker alive = False;
          finish called = False; lock free = True; status -> transcribing;
          cancel -> busy; state after cancel = transcribing   <-- permanent wedge
PRE-FIX : handle returns 'error';    state = error;        worker alive = False;
          status -> error; cancel -> idle; state -> idle      <-- recoverable

=== status_ui.idle raises on RECORDING + cancel ===
POST-FIX: cancel RAISES RuntimeError; state = idle; session.cancel called = False
=== status_ui.idle raises on shutdown ===
POST-FIX: shutdown RAISES RuntimeError; state = idle; session.cancel called = False
```

**B1 (blocking).** A raising `StatusUI` callback is not contained by the transition scope,
because the drain sits outside `handle`'s defensive `try/except`. Consequences:

1. **Permanent wedge**: `RECORDING → TRANSCRIBING` with `status_ui.transcribing` raising leaves
   the service in `TRANSCRIBING` with no worker and `_session = None`; every subsequent command
   answers `busy` until the process is restarted. This is the same user-visible class the
   ticket exists to prevent ("the hotkey stops responding with no error"), and it is a
   regression from the pre-fix behaviour, which landed in `ERROR` and recovered.
2. **Skipped cancel**: `RECORDING + cancel` with `status_ui.idle` raising skips
   `session.cancel()` — the capture is never abandoned.
3. **Unclean shutdown**: `shutdown()` with `status_ui.idle` raising skips `session.cancel()`
   and the worker/partials joins.

Recommended fix (minimal, consistent with `_start_partials_locked`, which already swallows
partials failures): make the drain best-effort, e.g. wrap each `action()` in
`try/except Exception: pass` (or log) inside `_transition_scope`'s `finally`. Display is a
non-critical collaborator and must not be able to abort a state-machine side effect. If the
project prefers, the queued call could instead be run through `handle`'s defensive `try` — but
the drain-inside-finally structure means the display call happens after the body, so the
catch-in-drain is the smaller change.

The wedge is trivially reproducible with the probe described above; no new test currently covers
it.

## 7. Micro-window: the queued-toggle coalescing — **documented; one symmetric note**

The two queued-toggle tests (`test_duplicate_toggle_never_stacks_recordings`,
`test_toggle_during_transcribing_queues_exactly_one_restart`) pass unchanged, and the full suite
is green. I exercised the window directly with a session whose `start()` blocks (probe removed):

```
queued toggle reply: ok=True state=transcribing
deferred start blocked in second.start(); state = transcribing
cancel during window -> ok=False error=busy state=transcribing
toggle during window -> ok=True error=None state=transcribing
pending_start now: True
after window: state = recording
sessions created: 2   second.start calls: 1
```

So N toggles in the window collapse to exactly one recording, as intended. I could not construct
an interleaving that **permanently** loses a command: the toggle is coalesced (per "N toggles
while transcribing → one recording"), and a `cancel` that lands in the window is rejected
`busy` and the queued recording proceeds.

**Note (non-blocking).** That last point is a real, if narrow, behaviour change: pre-fix, a
`cancel` during a queued restart would block on the lock and then cancel the freshly committed
recording; post-fix it observes `TRANSCRIBING` and gets `busy`, so the recording starts. It is
transient (the user can cancel again once `RECORDING` is published) and it is the direct,
accepted cost of not holding the lock across `session.start()`. Worth a sentence in the ticket
or a follow-up test, not a blocker.

A theoretical display-ordering race also exists (a queued display call from one thread can be
drained after a newer transition's display call from the worker), but the socket loop is
single-threaded and the worker drains before starting new work, so I could not construct a
reachable ordering inversion; noted as low risk.

## 8. `mypy` and the ADR 0019 config — **exit 0, config proven effective**

```
$ uv run mypy src/ges2t tests
Success: no issues found in 29 source files        # exit 0
```

Config-bites probe: I added `tests/_probe_untyped.py` containing `def probe_untyped(x): return x`
and re-ran the ADR 0019 command:

```
tests/_probe_untyped.py:1: error: Function is missing a type annotation  [no-untyped-def]
Found 1 error in 1 file (checked 30 source files)   # exit 1
```

Removed the probe and re-ran: `Success: no issues found in 29 source files`. The
`disallow_untyped_defs` config genuinely governs `tests` as well as `src`. `git status` clean
after removal.

## 9. Scope and hygiene — **clean**

`git diff HEAD~1 HEAD --name-only`:

```
docs/project/README.md
docs/project/core-dictation-pipeline/status-ui-outside-lock/001-comment.md
docs/project/core-dictation-pipeline/status-ui-outside-lock/ticket.md
src/ges2t/service.py
tests/test_state_machine.py
```

- The only `src/` file changed is `src/ges2t/service.py`.
- The only README change is row 21 (`to do` → `in review`, branch set); no other row touched.
- No conflict markers in any changed file.
- No service process or socket left behind by this review (`/tmp/probe.sock` never created; the
  only `/tmp/ges2t*` files present are ticket 9's pre-existing `ges2t_measure*.py` leftovers,
  flagged in review 002, not mine). `git status` is clean.

---

## Risks / follow-ups

1. **B1 — raising `StatusUI` wedges the service** (blocking, see §6). Most important because
   ticket 17's tray is the collaborator that will run arbitrary GUI code from these callbacks.
2. **Cancel in the queued-restart micro-window** returns `busy` and lets the recording start
   (non-blocking, §7). Add a test or a note if the behaviour matters.
3. **Display ordering under concurrency** is no longer lock-ordered (non-blocking, §7).
4. The `StatusUI` interface still has no documented exception contract; whichever way B1 is
   resolved, ADR/interface text should say whether a raising callback is tolerated and that the
   service guarantees not to wedge on it.

## Explicitly not verified

- I did not run a randomized scheduler fuzz; the interleavings tested are the enumerated
  micro-window and exception scenarios plus the whole suite under instrumentation.
- I did not exercise a real `pystray` tray (ticket 17); the raising-UI finding uses a synthetic
  `StatusUI`.
- I did not measure the pre-fix suite for the deadline of every possible raising-UI timing; the
  three consequences in B1 are the ones I could construct deterministically.

## Probe cleanup

All probes (`/tmp/probe_invariant.py`, `/tmp/probe_raising_ui.py`, `/tmp/probe_microwindow.py`,
`/tmp/instr_plugin.py`, `/tmp/prefix/`) were removed after this review; the repo tree is clean
and `git status` is empty.
