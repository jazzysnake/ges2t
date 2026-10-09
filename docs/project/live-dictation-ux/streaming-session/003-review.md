# 003 — Re-review: streaming session (ticket 15)

Re-reviewer: independent verification of the fixes in `04e0e50` / `e1575f5` on
branch `feat/streaming-session` (worktree `.worktrees/streaming-session`, HEAD
`e1575f5`; review `002` at `baff8f2`). Reviewed against
`git diff main...feat/streaming-session` (three dots) and `git diff
baff8f2..e1575f5`.

Scope of this re-review (per the brief): judge the two fixes from `002` and hunt
for what they introduced. The `002` verified-good findings (injection boundary,
locking, other behaviours) are **not** redone except where a fix could have
disturbed them.

Status: **complete** (written incrementally; see the detailed Verdict at the end).

## Verdict (summary)

**PASS — both `002` findings are resolved and nothing blocking was introduced.**
Full reasoning, evidence and recorded follow-ups are in §1–§4; the short form is at
the end of the file.

## 1. Reproduce the blocking fix (M14) — RESOLVED

The blocking finding was: a deadlock regression wedges a test, the summary prints,
and the pytest **process never exits** (`exit 124`), because the leaked
`daemon=False` session worker is joined forever by `threading._shutdown`.

Method: the M14 mutation wraps the body of `StreamingDictationSession._stop_capture`
in `with self._condition:`, so `finish()`/`cancel()` call `audio.stop()` and
`worker.join()` **while holding the lock** (the deadlock the module documents).
For the "before" run I restored the `baff8f2` versions of both files
(`git show baff8f2:<path>`), applied the same mutation, and cleared
`src/ges2t/__pycache__ tests/__pycache__ .mypy_cache .pytest_cache` before every
run — the stale-bytecode trap the `002` appendix warns about. Everything was
restored with `git checkout --` plus cache deletion; `git status` is clean of
probes after every step.

New file under M14 (cap 20 s, exit status checked):

| Revision | exit | pytest summary |
|---|---|---|
| before (`baff8f2` src+tests) | **124** (killed by cap) | `1 failed, 20 passed in 2.18s` |
| after (`e1575f5`) | **1** | `1 failed, 21 passed in 2.19s` |

Full suite under M14 (cap 30 s), after revision:

```
full exit=1
1 failed, 536 passed in 5.05s
```

Both implementer claims reproduce exactly. The **process exits** in the after
case; before it is killed by the cap. The one failure is always
`test_a_waiting_partials_consumer_does_not_block_finish`, which reports
`AssertionError: call did not return within 2.0s` from the bounded helper.

**Implementer caveat (a race can add failures because `finish()` may win the lock
against the fake worker), verified:** the new file was run **12 times** under M14
(cap 20 each): 12/12 `exit=1` with exactly **1** failure
(`test_a_waiting_partials_consumer_does_not_block_finish`) and no hang. I then ran
the other 21 tests (excluding the waiting-consumer test) **60 more times** under
M14: **0/60** non-zero exits. So the caveat is theoretically real (any test whose
worker is still mid-flight when `finish()` grabs the lock would now fail loudly
instead of hanging) but I did not observe a single additional failure. What
matters for the finding is that **no schedule hangs**: every main-thread
`finish()`/`cancel()` is wrapped in the 2 s bounded helper, and the worker plus all
test threads are daemons, so interpreter shutdown can always complete. The fix is
genuine and the blocking finding is **resolved**.

Restored-file sanity: after each probe `git diff` was empty and only the new
`003-review.md` is untracked.

## 2. Adversarial angle: does `daemon=True` open a data-loss window? — analysed

This is the change that most needed scrutiny: a daemon thread does not keep the
interpreter alive, so a worker can be killed abruptly at process teardown.

### Exit-path enumeration

| Exit path | How the worker ends | Mid-flush at teardown? | User loses? |
|---|---|---|---|
| normal `finish()` | `_stop_capture` does `audio.stop()` then `worker.join()` **unbounded**; `finish()` returns only after the worker's `finally` set `_done` | No — join precedes return | No |
| `cancel()` | `_stop_capture` `stop()` + **unbounded** `join()`; then state cleared | No — join precedes return; text abandoned by design | No (nothing expected) |
| service shutdown (`Service.shutdown`) | session in TRANSCRIBING has `self._session is None`, so `shutdown()` does **not** call the session; it joins the service's `_run_finish` daemon thread with `_SHUTDOWN_JOIN_TIMEOUT_SECONDS = 5.0`, then `serve()` returns | **Yes, if `session.finish()` is still in flight > 5 s** | Yes — utterance not injected (see below) |
| `SIGTERM`/`SIGINT` | handled by `serve()`'s `finally: self.shutdown()`, so same as above | same as above | same as above |
| exception inside `finish()` (worker raised / `audio.stop()` raised) | worker already reached `finally`/exited before `finish()`'s join returns; `finish()` re-raises | No | No (error path, no transcript promised) |
| unhandled error / crash | process dies; nothing was injected (ADR 0011) | possible | lost utterance, same as any crash; daemon flag irrelevant |
| `SIGKILL` | process dies | possible | unavoidable, independent of daemon flag |

### The one real window, and whether `daemon=True` created it

I tested the CPython mechanism directly (non-daemon worker + daemon "injector"
waiting on it, main thread returns immediately):

```
=== nondaemon worker (old behavior) x6 ===
main returning
INJECTOR RAN              <- injector thread ran in 6/6 runs

=== daemon worker (new behavior) x6 ===
main returning             <- INJECTOR RAN never printed, 6/6
```

The abort/finalization noise in the non-daemon runs is an artifact of my probe
printing at finalization; the load-bearing observation is that the daemon
"injector" made progress while `threading._shutdown` joined the non-daemon
worker, and was killed before it ran when the worker was a daemon.

Mapping that to the service: the service's `_run_finish` thread (daemon, line
344) is what calls `session.finish()` and then `injector.inject(text)`. At
`Service.shutdown()` during a slow final flush, `shutdown()` gives up after its
5 s bound and returns; `serve()` closes the socket and returns. With the worker
`daemon=False` (before), `threading._shutdown` then joined the session worker,
and during that wait the still-live `_run_finish` thread could reach
`injector.inject(text)`. With the worker `daemon=True` (after), the interpreter
has no non-daemon thread to join and finalizes immediately, killing both
daemon threads before the injection. So **yes, the fix does create a real,
narrow window where a transcript that the old behaviour would have delivered at
shutdown is dropped.**

### Ruling

**`daemon=True` + an unbounded production join is the correct pair here; the
window is an accepted trade and a recorded follow-up, not a blocker.** Reasons:

1. **No normal path loses text.** Every path on which the user expects a
transcript (`finish()`, and therefore the service's injection) joins the worker
**before returning**, so the final segment is recorded before `finish()` returns
and before `inject(text)` runs. The daemon flag changes only what happens when
*nobody joins* — i.e. when the service is already tearing down.
2. **The failure mode is a lost utterance, never corrupted text.** Under ADR
   0011 nothing is injected until release, and injection is a single call after
   `finish()` returns. A killed worker therefore produces *no* typed text at all;
   there is no half-typed, stale, or partial-word transcript. That is a
   qualitatively smaller harm than any scheme that returns partially accumulated
   text, and it is exactly the property ADR 0011 protects.
3. **The only reachable window is one the service already declared it was giving
   up on.** `shutdown()`'s own comment is "Shutdown must not hang forever on a
   wedged engine," and its 5 s bound is deliberate. Before the fix, non-daemon
   worker semantics *defeated* that bound by hanging the process (the exact
   blocking finding); after the fix the process actually honours its own
   give-up. `daemon=True` makes the intended ADR 0015 behaviour real rather than
   accidentally masked.
4. **The alternative is worse.** A bounded production join would fire on a
   *legitimately* slow final flush **while the service is still running and the
   user is waiting**, silently truncating a valid transcript — real data loss on
   the normal path. A visible hang/exit beats invisible truncation, as the
   implementer argues. The unbounded join is the right choice.

**Recorded follow-up (service layer, not this ticket/PR):** if a >5 s final
flush during shutdown should still be delivered, that decision belongs in
`Service.shutdown()` (e.g. do not abandon a real in-flight flush, or surface the
loss), not in `StreamingDictationSession`. This ticket only owns
`streaming_session.py`; I am not asking for a change there. Recommend noting it
in ticket 16 (streaming wiring) so the loss is a known, deliberate behaviour.

### The "5 s bound" claim

Confirmed in `src/ges2t/service.py`: `_SHUTDOWN_JOIN_TIMEOUT_SECONDS = 5.0`
(line 55), applied to both the `_run_finish` worker and the partials thread in
`Service.shutdown()` (lines 516, 518). Its interaction with a slow final
transcription is the window above: it can drop an utterance at shutdown, but it
cannot hang, and it does not affect an ordinary running service. I judge that
interaction **acceptable and consistent with ADR 0015**, with the follow-up
noted.

## 3. Verify the rest — all green

### MA — the dedup gap is closed (test-strength, not a product bug)

The new test `test_partials_allow_a_legitimate_non_consecutive_repeat` feeds
`a, a, "a b", "a b", a, final` and asserts `collected == ["a", "a b", "a"]` —
the "legitimate later repeat survives" direction the old advancing-partials test
did not pin. It pins **both** directions now: the pre-existing test pins adjacent
duplicates dropped, the new one pins a non-adjacent repeat kept.

I re-ran the MA mutation (`segment.text not in self._partials`, i.e. global dedup)
myself, caches cleared:

```
exit=1
FAILED tests/test_streaming_session.py::test_partials_allow_a_legitimate_non_consecutive_repeat
1 failed, 21 passed in 0.16s
E       AssertionError: assert ['a', 'a b'] == ['a', 'a b', 'a']
E         Right contains one more item: 'a'
```

Exactly the new test fails, with exactly the expected diff. The shipped code is
consecutive-only (`self._partials[-1] != segment.text`), so the honest framing
stands: this was a **test-strength gap, not a product bug**, and it is now closed.

### Boundedness and the helper's re-raise

Every `finish()`/`cancel()` driven on the test's main thread goes through
`_call_with_timeout(..., timeout=2.0)`. The only unwrapped `session.finish()` is
line 396, inside `test_cancel_stops_capture_joins_the_worker_and_types_nothing`:

```python
with pytest.raises(StreamingSessionError, match="after cancel"):
    session.finish()
```

That call raises in the lifecycle guard **before** `_stop_capture`, so it performs
no wait and cannot hang; it is bounded by construction. No other unbounded
main-thread lifecycle call exists.

`_call_with_timeout` catches `BaseException` into `errors` and does
`raise errors[0]`, so it can never turn a failing lifecycle into a passing one:

```
PASS re-raise RuntimeError: boom-re-raise
PASS re-raise StreamingSessionError: finish() called before start()
PASS timeout in 0.30s: call did not return within 0.3s
```

**Falsification that the lifecycle tests are not vacuous:** I neutered the helper
(`raise errors[0]` → swallow) and re-ran the file. Four tests failed — both
before-start tests and both raising-worker tests — proving they depend on the
helper re-raising:

```
FAILED test_finish_before_start_is_defined
FAILED test_cancel_before_start_is_defined
FAILED test_a_raising_stream_surfaces_through_finish
FAILED test_a_raising_chunk_iterator_surfaces_through_finish
4 failed, 18 passed
```

Restored; `git status` clean, `src/` unmodified.

### Counts and flakiness

| | base (`main` + ignore new file) | before (`baff8f2`) | after (`e1575f5`) |
|---|---|---|---|
| full suite | — | 536 | **537** |
| new file, collected items | 515 base + 21 | 21 | **22** |
| new file, `def test_` | — | 20 | **21** |

537 = 515 + 22, and 22 collected items come from 21 defs (one parametrized ×2).
The `baff8f2..HEAD` name diff shows **only** the one added def
(`test_partials_allow_a_legitimate_non_consecutive_repeat`); no test was deleted,
renamed, skipped, or weakened, and the other changes are only the bounded-helper
wrapping. `grep skip\|xfail` in the new file: none.

Flakiness: the clean new file was run **10 consecutive times** — `22 passed`
every time (0.03–0.08 s); the full suite `537 passed in 1.34s`.

### mypy

`rm -rf .mypy_cache __pycache__ src/ges2t/__pycache__ tests/__pycache__ && uv run
mypy src/ges2t tests` → `Success: no issues found in 36 source files`.

### Scope (all clean)

- `git diff main...HEAD -- src/ges2t/pipeline.py` → **empty**.
- No `streaming.enabled` branch in `src/` or `tests/` (the only non-doc hits are
  pre-existing `tests/test_config.py` assertions about the config key).
- No new ADR (`git diff main...HEAD --name-only -- docs/adr/` empty).
- No config change: `pyproject.toml` unchanged, no `pytest.ini`, no
  `conftest.py`/`tests/conftest.py`, no `[tool.pytest.ini_options]`, and the
  installed plugin set is only `pytest 9.1.1` (no `pytest-timeout`). Bounds come
  from the in-file daemon-thread helper only.
- Branch touches only: `README.md`, the three ticket docs, `streaming_session.py`,
  `tests/test_streaming_session.py`.

### Commit hygiene (nothing committed mutated)

- `e1575f5` touches **only** `.../001-comment.md` (+108).
- `04e0e50` touches **only** `src/ges2t/streaming_session.py` and
  `tests/test_streaming_session.py`.
- `git status --porcelain src/` is empty; all my probes were reverted with
  `git checkout --` plus `__pycache__`/`.mypy_cache`/`.pytest_cache` deletion, and
  the only untracked file is this `003-review.md`.

## 4. Could not verify

- The implementer's full 15-mutation matrix beyond M14 and MA (the `002` review
  spot-checked four; I only needed to re-run the two under judgement). The
  per-test load-bearingness claim across all 15 mutations is not re-proven here.
- Whether the daemon shutdown window (section 2) is reachable end-to-end on a
  real machine with a genuinely slow Whisper flush: no real engine, microphone,
  subprocess or network was used (forbidden). The window is established from
  code reading plus a direct CPython shutdown-semantics probe, not an E2E run.
- The absence of *any* additional-failure schedule under M14: I observed 0/60
  additional failures, which is evidence, not proof; the structural argument
  (daemons + bounded main-thread calls) is what guarantees no hang.

## Verdict

**PASS — both `002` findings are resolved and nothing blocking was introduced.**

- **Blocking finding (suite can hang): resolved.** Before `exit=124` (killed by
the cap after the summary printed); after `exit=1` under the same M14 mutation,
both for the new file and the full suite. 12/12 mutated repeats and 60/60
extra iterations exited non-zero with no hang. Daemon worker + bounded
main-thread test calls is a sound fix.
- **MA (dedup test gap): resolved.** The new test pins the non-consecutive
repeat direction; the global-dedup mutation now fails exactly that test. Framed
honestly as a test-strength gap, matching the shipped consecutive-only code.
- **`daemon=True` + unbounded production join: correct pair.** Normal paths
join before returning, so the transcript is complete before `finish()` returns
and before injection; the only window is service teardown mid-flush, where the
loss is a *lost utterance* (nothing injected, ADR 0011), never partial/corrupt
text, and the service's own 5 s shutdown bound had already declared it was
giving up. A production join timeout would be worse (silent truncation on the
normal path). **Not blocking**; recorded follow-ups below.

### Recorded follow-ups for the orchestrator (not blockers)

1. **Service layer (ticket 16 or a service ticket):** at `Service.shutdown()`
   during a final flush longer than the 5 s bound, `daemon=True` now lets the
   process exit and drop an utterance that the old non-daemon behaviour could
   have delivered (the still-live `_run_finish` thread completed the injection
   while `threading._shutdown` waited on the worker). Decide whether that loss
   should be surfaced or the flush waited on at shutdown. This is outside
   `streaming_session.py`.
2. **Ticket note is stale:** `ticket.md` still says "Use
   `threading.Thread(daemon=False)`". The fix deliberately uses `daemon=True`
   (and documents it) because `daemon=False` is what let a wedged worker trap the
   process. Update the note so the ticket and code agree.

### Methodology note

After every probe that wrote to `src/` or `tests/`, bytecode and type caches were
deleted (`__pycache__`, `.mypy_cache`, `.pytest_cache`) and the file was restored
with `git checkout --`, then `git status` was checked. This avoids the
same-second stale-`.pyc` trap (`002` appendix) and the same-byte-length mypy-cache
trap. Probe scripts live under `/tmp/t15probe/` and are not committed. No
keystrokes, `ydotool`, clipboard, `sudo`, service change, network, terminal or GUI
window was used.

