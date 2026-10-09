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
