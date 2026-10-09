# 001 — call StatusUI (and every other collaborator) outside the state lock

**Ticket:** `status-ui-outside-lock` · **Branch:** `feat/status-ui-outside-lock` · **Status:** in review

## Files

- `src/ges2t/service.py` (modified)
- `tests/test_state_machine.py` (modified — 3 tests added, 150 insertions, 0 deletions)

No other module was touched.

## The bug, and the fix in one sentence

`Service._set_state` ran with `Service._lock` held and called
`status_ui.idle()/recording()/transcribing()/error()` from inside it. `self._lock` is a plain,
non-reentrant `threading.Lock`, so a `StatusUI` that reads `service.state` back — exactly what
ticket 17's tray does — deadlocks. `_set_state` now only records the state and **queues** the
display call; the new `Service._transition_scope` context manager invokes the queued calls once
the lock has been released.

## Before / after locking discipline

**Before**

```python
with self._lock:
    decision = self._dispatch_locked(command)
    # _set_state(...) -> status_ui.recording()   <-- collaborator called under the lock
# after the lock:
if decision.after is not None:
    decision.after()          # worker.start() / session.cancel()
```

**After**

```python
with self._transition_scope():     # holds self._lock, collects status_ui calls
    decision = self._dispatch_locked(command)   # _set_state only queues a display call
# lock released; _transition_scope has already run the queued status_ui calls here
if decision.after is not None:                 # worker.start() / session.cancel()
    replacement = decision.after()             # may return a replacement decision
    if replacement is not None:
        decision = replacement
```

`_transition_scope` owns the queue in a local list (`self._pending_display` is set/cleared
*inside* the lock, so two threads never share it), drains it after `with self._lock:` exits on
both the normal and exceptional path, and thus guarantees the display call happens with the
lock released. The lock was not made reentrant and no behaviour was restructured beyond the
deferral.

## Calls moved out of the lock

The audit covered every collaborator mention in `service.py` (`status_ui`, `injector`, the
session seam). Two kinds of call were under the lock; both were moved out.

1. **`status_ui.idle/recording/transcribing/error`, from `_set_state`.** Now queued; run by
   `_transition_scope` after release. This was the reproduced defect (review `002` follow-up 1).
2. **`session_factory()` and `session.start()`, from `_start_session_locked`.** These are
   collaborator calls on the command path and were also under the lock. They now run in
   `_commit_start`, which is the `after` side effect of `_begin_start_locked`: the session is
   created and started with no lock held, then the `RECORDING` state is committed under a fresh
   `_transition_scope`. `_Decision.after` was widened from `Callable[[], None]` to
   `Callable[[], _Decision | None]` so a deferred start that raises can replace the optimistic
   `RECORDING` decision with the `start-failed` / `ERROR` one, preserving the exact response.

Everything else was already outside the lock and was **not** changed:

- `session.finish()` and `injector.inject()` — on the `ges2t-finish` worker, no lock
  (`_run_finish`).
- `session.cancel()` — in `_cancel_locked`'s `after` and in `shutdown()` after the scope.
- `session.partials()` and `status_ui.partial()` — on the dedicated `ges2t-partials` thread,
  which never holds the lock.

Audit answer: the lock was held across the two calls above and nowhere else. It was **not**
"only the state transitions".

### Deliberate one-window behavioural note (must not be read as a regression)

`_complete_transcription` (the worker's queued-toggle path) now reads-and-clears
`_pending_start` under the lock and then commits the new session in `_commit_start` with the
lock released. A `toggle` that arrives in that small window is coalesced into the already
starting recording (the `_pending_start = False` commit), which is the same "N toggles while
transcribing yield exactly one recording" rule the transition table already specifies. The
sequential transition table is byte-for-byte identical; `test_duplicate_toggle_never_stacks_recordings`
and `test_toggle_during_transcribing_queues_exactly_one_restart` pass unchanged.

## Regression test (proof the bug is caught)

Added to `tests/test_state_machine.py`:

- `StateReadingStatusUI` — `idle/recording/transcribing/error/partial` each read
  `service.state` back (a tray).
- `StateReadingSession` — a `FakeDictationSession` whose `start/finish/cancel` also read state.
- `test_status_ui_reading_state_back_does_not_deadlock` — drives `IDLE → RECORDING` (toggle),
  `RECORDING → TRANSCRIBING` (toggle) and `TRANSCRIBING → IDLE` (worker), each command on a
  bound `call_with_timeout(..., 1.5)`.
- `test_status_ui_reading_state_back_on_error_does_not_deadlock` — `IDLE → ERROR` via a raising
  `start()`, also bounded.
- `test_session_reading_state_back_does_not_deadlock` — proves the `session.start()` audit fix.

Cleanup uses the new `shutdown_with_timeout`, so a re-introduced deadlock fails with an
assertion instead of wedging the suite (a `finally:` that called `shutdown()` directly would
block on the same lock the bug holds).

### Pre-fix failure (fix temporarily reverted with `git checkout -- src/ges2t/service.py`)

```
$ uv run pytest tests/test_state_machine.py -q -k reading_state_back
>           raise AssertionError(f"call did not return within {timeout:.1f}s")
E           AssertionError: call did not return within 1.5s
...
>           raise AssertionError(f"shutdown did not return within {timeout:.1f}s")
E           AssertionError: shutdown did not return within 1.0s
FAILED tests/test_state_machine.py::test_status_ui_reading_state_back_does_not_deadlock
FAILED tests/test_state_machine.py::test_status_ui_reading_state_back_on_error_does_not_deadlock
FAILED tests/test_state_machine.py::test_session_reading_state_back_does_not_deadlock
3 failed, 44 deselected in 7.71s
```

The suite exited in 7.7 s: it failed loudly and did not hang. With the fix restored the same
three tests pass in 0.06 s.

## Verification (real output)

```
$ uv run pytest
350 passed in 1.34s

$ uv run pytest tests/test_state_machine.py -q
47 passed in 0.18s

$ uv run mypy src/ges2t tests
Success: no issues found in 29 source files
```

Test counts: **before** 347 total / 73 state-machine+socket (44 + 29); **after** 350 total /
76 state-machine+socket (47 + 29). The original 73 are unchanged and still pass; the three new
tests are the only additions to the test file (`git diff --numstat` → `150 0`).

A throwaway probe (removed) confirmed the same fact end to end: with the fix, a `StatusUI` that
reads `service.state` on every transition observes `[RECORDING, TRANSCRIBING, IDLE]`.

## Notes for the orchestrator / ticket 17

- The `StatusUI` interface is unchanged; ticket 17's tray may read `service.state` from any
  status callback.
- The tray must still not be *called* under the lock by other code, but the service no longer
  requires any such discipline from `StatusUI`.
- `_Decision.after` now returns `_Decision | None`; both existing callbacks return `None`, so
  this is source-compatible for the only two producers.

## Addendum — B1 containment: a raising `StatusUI` must not abort a transition (review 002)

**Ticket:** `status-ui-outside-lock` · **Branch:** `feat/status-ui-outside-lock` · **Status:** in review

Review `002` accepted the deadlock fix (mechanically proven: 0 collaborator calls under the
lock across all 20 transition-table cells and the exception paths) but raised one blocking
finding, **B1**: the new display drain is not contained.

### What B1 was

`_transition_scope` drains the queued `status_ui` calls in its `finally`, which is **outside**
`handle`'s defensive `try/except`. So a raising `status_ui` callback propagated out of
`handle` and **skipped the deferred side effect** that runs after the scope:

```python
with self._transition_scope():        # <-- drain raised at __exit__
    try:
        decision = self._dispatch_locked(command)
    except Exception as exc:          # <-- does NOT catch the drain
        ...
if decision.after is not None:        # <-- skipped when the drain raised
    decision.after()                  #     worker.start() / session.cancel()
```

Three concrete regressions, all in the "hotkey stops responding with no error" class this
ticket exists to prevent:

1. **Permanent wedge.** `RECORDING + toggle` with a raising `status_ui.transcribing` set the
   state to `TRANSCRIBING`, then raised before `worker.start()` ran. `_session` was already
   `None`, so every later command answered `busy` until the process restarted. Pre-fix (before
   ticket 21) it landed in `ERROR` and recovered; ticket 21 had turned that into a wedge.
2. **Skipped cancel.** `RECORDING + cancel` with a raising `status_ui.idle` skipped
   `session.cancel()`, leaking the capture.
3. **Unclean shutdown.** `shutdown()` with a raising `status_ui.idle` skipped both
   `session.cancel()` and the worker/partials joins.

### The fix (one block, `Service._transition_scope`)

Make the drain best-effort, mirroring the **existing** partials policy in
`_start_partials_locked` (which silently `return`s on a failing `session.partials()` iterator):

```python
finally:
    for action in display:
        try:
            action()
        except Exception:
            # Display is best-effort ...
            continue
```

Each queued callback is guarded individually, so one raising display call still lets the rest
of the queue drain. The policy is deliberately *silent*, exactly like partials — no logging
convention was introduced into `service.py` — even though review `002`'s suggestion allowed
"`pass` (or log)".

### Display vs. real exceptions (the whole point)

Only the queued **display** callbacks become best-effort. Nothing else is swallowed:

| collaborator call | policy |
| --- | --- |
| `status_ui.idle/recording/transcribing/error` (queued) | best-effort: `except Exception: continue` in the drain |
| `status_ui.partial` (partials thread) | best-effort: already swallowed by `_start_partials_locked` |
| `session_factory()`, `session.start()` | unchanged — a raise moves the service to `ERROR` (`start-failed`) |
| `session.finish()` | unchanged — a raise moves the service to `ERROR` (`transcription failed`) |
| `injector.inject()` | unchanged — a raise moves the service to `ERROR` (`injection failed`) |
| `session.cancel()` | unchanged — a raise moves the service to `ERROR` |

The fix does not touch the locking discipline: the drain already ran after the lock was
released, and it still does. No collaborator is called under the lock.

### Regression tests added (fail pre-fix, pass post-fix)

Added to `tests/test_state_machine.py` (a `RaisingStatusUI` that records the real call and then
raises on a chosen callback):

- `test_raising_status_ui_transcribing_does_not_wedge_the_service` — `RECORDING + toggle` with
  a raising `transcribing`: the reply is still `ok`/`TRANSCRIBING`, `finish()` really ran, the
  state returns to `IDLE`, and a later `status` is not `busy`.
- `test_raising_status_ui_recording_does_not_change_a_start` — a raising `recording` does not
  change the start outcome (`RECORDING`, session started once).
- `test_raising_status_ui_error_does_not_mask_a_start_failure` — a raising `error` does not mask
  a real `start()` failure (reply stays `start-failed` / `ERROR`).
- `test_raising_status_ui_idle_still_cancels_the_session` — `RECORDING + cancel` with a raising
  `idle`: `session.cancel()` was called, state `IDLE`.
- `test_raising_status_ui_does_not_skip_shutdown_cleanup` — `shutdown()` with a raising `idle`
  returns, still cancels, and joins the worker/partials threads.

Every command runs through the bounded `call_with_timeout(..., 1.5)` (a join timeout, never an
unbounded wait), so a regression fails loudly instead of hanging the suite.

### Pre-fix failure output (fix temporarily reverted, tests kept)

Reverting only the drain guard (`git`-tracked file restored afterwards) and running the new
tests:

```
$ uv run pytest tests/test_state_machine.py::test_raising_status_ui_transcribing_does_not_wedge_the_service -q --tb=short
F                                                                        [100%]
=================================== FAILURES ===================================
________ test_raising_status_ui_transcribing_does_not_wedge_the_service ________
tests/test_state_machine.py:536: in test_raising_status_ui_transcribing_does_not_wedge_the_service
    response = call_with_timeout(lambda: service.handle("toggle"), timeout=1.5)
tests/test_state_machine.py:144: in call_with_timeout
    raise errors[0]
tests/test_state_machine.py:134: in run
    results.append(fn())
    response = call_with_timeout(lambda: service.handle("toggle"), timeout=1.5)
src/ges2t/service.py:227: in handle
    with self._transition_scope():
/usr/lib/python3.14/contextlib.py:148: in __exit__
    next(self.gen)
src/ges2t/service.py:440: in _transition_scope
    action()
tests/test_state_machine.py:501: in transcribing
    self._maybe_raise("transcribing")
tests/test_state_machine.py:489: in _maybe_raise
    raise RuntimeError(f"status_ui.{name} exploded")
E   RuntimeError: status_ui.transcribing exploded
1 failed in 0.19s
```

All five fail pre-fix, loudly and quickly (never a hang):

```
$ uv run pytest tests/test_state_machine.py -k "raising_status_ui" -q --tb=line
FAILED tests/test_state_machine.py::test_raising_status_ui_transcribing_does_not_wedge_the_service
FAILED tests/test_state_machine.py::test_raising_status_ui_recording_does_not_change_a_start
FAILED tests/test_state_machine.py::test_raising_status_ui_error_does_not_mask_a_start_failure
FAILED tests/test_state_machine.py::test_raising_status_ui_idle_still_cancels_the_session
FAILED tests/test_state_machine.py::test_raising_status_ui_does_not_skip_shutdown_cleanup
5 failed, 47 deselected, 1 warning in 0.08s
```

A throwaway probe (removed) against the reverted code showed the wedge exactly:

```
===== PRE-FIX PROBE =====
toggle -> recording
second toggle RAISED: RuntimeError tray blew up
state           = transcribing
finish called   = False
worker alive    = False
status -> transcribing
cancel -> busy state = transcribing     <-- permanently wedged

===== POST-FIX PROBE =====
toggle -> recording
second toggle -> transcribing None
state           = idle
finish called   = True
worker alive    = False
status -> idle
cancel -> not-recording state = idle
```

### Verification (real output, post-fix)

```
$ uv run pytest
355 passed in 1.39s

$ uv run pytest tests/test_state_machine.py -q
52 passed in 0.16s

$ uv run mypy src/ges2t tests
Success: no issues found in 29 source files
```

Test counts: **before this addendum** 350 total / 76 state-machine+socket (47 + 29); **after**
355 total / 81 state-machine+socket (52 + 29). The five new tests are the only additions; the
previous 350 (including the original transition-table, deadlock, and socket tests) pass
unchanged. `git diff --stat` for the addendum: `src/ges2t/service.py` +13/−1,
`tests/test_state_machine.py` +166/−0.

