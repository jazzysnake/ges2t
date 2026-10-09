# 002 — Review: streaming session (ticket 15)

Reviewer: independent review of branch `feat/streaming-session`
(worktree `.worktrees/streaming-session`, HEAD `2d64f83`).
Reviewed against `git diff main...feat/streaming-session` (three dots).

## Verdict

**Changes requested — do not merge as-is.**

The ticket's hardest rule (ADR 0011, "nothing is injected until release") **holds**,
the public behaviours all pass independent falsification, locking is mechanically
proven clean, scope is clean, and the mechanicals (536 tests, 21 new, mypy 36 files)
pass. Two findings must be addressed first:

1. **BLOCKING — waiting tests are not bounded: a deadlock regression hangs the
   pytest process.** Under the implementer's own M14 mutation (lock held across
   `audio.stop()`/`join`) the test phase reports `1 failed, 20 passed in 2.17s`,
   but the **pytest invocation never exits** — it was killed by a 20 s cap
   (`exit 124`). The non-daemon session worker is left blocked on the lock held by
   the deadlocked daemon `finish` thread, so `threading._shutdown` joins it forever.
   Most tests call `session.finish()`/`cancel()` directly, and the production
   `worker.join()` has **no timeout**, so those tests can hang rather than fail.
   Details in §4.
2. **Test-strength gap — the suite cannot distinguish consecutive dedup from
   *global* dedup.** A mutation to `segment.text not in self._partials` (drop every
   value ever seen, not just the previous one) leaves **all 21 tests green**, even
   though it deletes a legitimate later repeat (`['a','a b','a']` → `['a','a b']`).
   The shipped code is correct (consecutive-only); this is a missing test, not a
   product bug. The ticket and the review brief explicitly require testing **both
   directions** of the dedup rule. Details in §2/§7.

Everything else is clean. If finding 1 is deemed inherent to the required
`daemon=False` worker rather than a test defect, the orchestrator should say so
explicitly; by the brief's criterion ("a test that can hang is a blocking finding")
it is reported as blocking.

## Method

All probes live outside the repo (`/tmp/t15probe/`), import the worktree source via
`uv run python`, and revert everything (`git status` clean after every step). No
microphone, model, subprocess, network, keystrokes or GUI window was used. No config
was changed; bounds come from local daemon-thread + `join(timeout)` helpers, never a
pytest timeout plugin.

**Methodological trap found and handled (report to the orchestrator).** Restoring a
mutated file with `cp` inside the same wall-clock second leaves a `.pyc` whose
recorded source mtime collapses to the same second, so Python's timestamp-based
invalidation serves **stale bytecode compiled from the mutation**. I was fooled once
(a restored `finish()` still returned the partial). Every probe below was run after
`rm -rf src/ges2t/__pycache__ tests/__pycache__`. This is the same family as the
same-byte-length `git checkout` mypy-cache trap the brief warns about, but it affects
**runtime bytecode**, not just mypy.

## 1. Injection boundary (ADR 0011) — holds, both directions

**Probe (mine, `/tmp/t15probe/boundary.py`).** Drives the **real `Service`**
(`ges2t.service.Service`) with the **real `StreamingDictationSession`**, a
`FakeAudioSource`, a streaming `FakeTranscriber`, and `FakeInjector`/`FakeStatusUI`.
Partial text is a distinct sentinel textually impossible to confuse with the final:
`SENTINEL = "ZQX-7731-SENTINEL-ONLY display partial"`, `FINAL =
"released transcript 4242 zephyr"`.

Normal run (real output):

```
injected: ['released transcript 4242 zephyr']
status partials: ['ZQX-7731-SENTINEL-ONLY display partial']
PASS: injector received ONLY the final text
```

So the partial reached `StatusUI.partial()` and the injector received only the final
text — the display/injection split is real at the service boundary.

**Falsification.** Mutated `finish()` to return the latest partial:

```
-        return final_text if final_text is not None else ""
+        return self._partials[-1] if self._partials else ""
```

Same probe under the mutation (real output):

```
injected: ['ZQX-7731-SENTINEL-ONLY display partial']
status partials: ['ZQX-7731-SENTINEL-ONLY display partial']
AssertionError: INJECTION BOUNDARY VIOLATED: injector got ['ZQX-7731-SENTINEL-ONLY display partial']
probe exit=1
```

Restored, `git status` clean. The boundary test fails exactly when `finish()` can
see partials. It is a genuine guard, not a vacuous assertion.

I also confirmed the mutation is caught by the committed suite
(`MF_finish_returns_partial`: `10 failed, 11 passed`), including
`test_service_displays_partials_but_never_injects_them`.

## 2. Per-behaviour falsification

Probe `/tmp/t15probe/behaviour.py`, all calls bounded by a daemon-thread
`join(timeout=3)`. Real output:

```
(a) partials: ['a', 'a b', 'a']
(a) PASS consecutive-only dedup (both directions)
(b) PASS finish == final segment only
(c) PASS no final => ''
(d1) PASS finish before start raises: finish() called before start()
(d2) PASS cancel before start raises: cancel() called before start()
(d3) PASS double finish returns cache, no re-stream; caller cannot mutate
(d4) PASS double cancel is an idempotent no-op
(e) PASS cancel propagates failing audio.stop() after joining (bounded, no hang)
(f) PASS chunk_seconds <= 0 raises ValueError locally
ALL BEHAVIOURAL PROBES PASSED
```

- **(a) consecutive-only dedup, both directions.** Segments
  `a, a, "a b", "a b", a, final`. The two adjacent duplicates are dropped **and the
  later legitimate repeat of `'a'` survives**: `['a','a b','a']`. This is exactly the
  direction the committed suite does not pin (§7, MA).
- **(b)** `finish()` returns only the final segment's text, never partial text.
- **(c)** A partial-only stream returns `""` (partials are not promoted).
- **(d)** `finish()`/`cancel()` before `start()` raise `StreamingSessionError`;
  a second `finish()` returns the cached value, does **not** re-run the stream
  (`transcriber.calls == ["stream"]`) and does **not** call `audio.stop()` twice;
  a second `cancel()` is a no-op. `finish()` returns a `str`, so the caller cannot
  mutate stored state through it (rebinding + `_final_text` unchanged verified).
- **(e)** `cancel()` propagates a failing `audio.stop()` (`RuntimeError("stop
  exploded")`) after `worker.join()`, and the worker is not alive afterwards. No
  hang within the bound.
- **(f)** `chunk_seconds ∈ {0.0, -1.0, -0.001}` raise `ValueError` locally.

**Authority check (`chunk_seconds`).** The session validates only `> 0`, and the
value is used **only** to size `audio.chunks()`; it is never passed to the
transcriber. The authoritative range validation (`< 0.25`, `>= window_seconds`,
`window_seconds > 30`, latency warning) lives in
`src/ges2t/transcribers/whistle_config.py` (`MIN_CHUNK_SECONDS`, `_validate...`).
There is no duplicate/conflicting authority: the session guard cannot mask an
adapter error because the adapter receives `window_seconds`/`chunk_seconds` from its
own config inside `WhistleTranscriber.stream()`, not through this session.

## 3. Locking — instrumented, not read

Probe `/tmp/t15probe/locking.py`. Replaces `session._condition` with an
owner-tracking proxy (`TrackingCondition`) around the real `threading.Condition`;
records `is_held()` at exact instants via an instrumented transcriber (engine entry
and each yield), an instrumented `FakeAudioSource.stop()`, and a wrapped
`worker.join`. A **positive control** proves the proxy is live and actually used by
the session (`proxy acquisitions during start+finish: 7`; `with session._condition:
is_held()` is `True` inside, `False` outside).

Real output:

```
finish -> 'FINAL'
engine_held: [False]
yield_held : [False, False]
stop_held  : [False]
join_held  : [False]
call order : ['stop', 'join']
PASS: lock never held across engine call / audio.stop() / join; stop before join
cancel stop_held: [False] join_held: [False]
PASS: cancel path also stops/joins outside the lock
```

So: the `Condition` is **not held** when the engine is entered or yields, not at
`audio.stop()` entry, and not at `worker.join()` entry, on both the `finish()` and
`cancel()` paths, and `_stop_capture` orders **stop before join** outside the lock.

## 4. Boundedness — BLOCKING finding

The brief: waiting tests must be bounded so a regression fails loudly instead of
hanging. Under the implementer's own M14 mutation (lock held across stop/join):

- Whole file, with a 20 s external cap, **no pipe** (so the exit code is pytest's):

  ```
  pytest-invocation exit=124
  FAILED tests/test_streaming_session.py::test_a_waiting_partials_consumer_does_not_block_finish
  1 failed, 20 passed in 2.17s
  ```

  The summary prints in 2.17 s, then the process **never exits** and is killed at
  20 s. `exit 124` == hung/killed, not a pass.
- Per-test loop with a 6 s cap each: the whole file's tests pass/fail individually
  except `test_a_waiting_partials_consumer_does_not_block_finish`, which reports
  `HANG`.

Cause: the session worker is `daemon=False` (required by the ticket). Under M14 the
daemon `finish` thread holds the lock and blocks in `join`; the non-daemon worker
blocks acquiring the lock; at interpreter shutdown `threading._shutdown` joins the
non-daemon worker and never returns. `_call_with_timeout` bounds the **call**, and
the test does report its `AssertionError` — but it cannot force the blocked worker
to die, so the process hangs.

Structural corroboration: most tests call `session.finish()`/`cancel()` **directly**
(test lines 167, 189, 208, 220, 227, 237, 246–247, 278, 290, 347, 356, 363–364), and
the production `_stop_capture` calls `worker.join()` with **no timeout**. Any
regression that wedges `finish()` therefore wedges those tests and the whole run.
Only the two error-path tests and the waiting-consumer test use the bounded
`_call_with_timeout`.

The other bounded waits are fine: `_wait_for` (2 s), `consumer_done.wait(2 s)`,
`consumer.join(2 s)`, `audio.chunks_started.wait(2 s)`,
`_BlockingAudioSource._released.wait(timeout=5.0)`.

## 5. Scope

- `git diff main...feat/streaming-session -- src/ges2t/pipeline.py` is **empty**.
- Files changed: `docs/project/README.md`, `…/streaming-session/001-comment.md`,
  `…/streaming-session/ticket.md`, `src/ges2t/streaming_session.py`,
  `tests/test_streaming_session.py`. No `service.py`, no composition root.
- No `streaming.enabled` branch anywhere in code (the only occurrence in the diff is
  the prose sentence in `001-comment.md` saying none was added).
- No new ADR (`git diff main...feat/streaming-session --name-only -- docs/adr/` empty).
- `streaming_session.py` imports only `threading`, `collections.abc`, `enum`,
  `ges2t.interfaces`. It does not import `ges2t.streaming`; the only "streaming"/
  `drive` mentions are docstrings. No `subprocess`/`socket`/`numpy`/`ydotool`.

## 6. Mechanicals

- `uv run pytest -q` → **536 passed** (base `33413b3` = **515**; verified by
  `uv run pytest --ignore=tests/test_streaming_session.py --collect-only -q` →
  `515 tests collected`). New file collects **21**.
- New file run **10 consecutive times**: `21 passed` every time (0.04–0.07 s); no
  flakiness. `uv run pytest` itself has no timeout plugin/config and no skips.
- No test deleted/skipped/weakened: the only test change is the new file; no
  existing test file is in the diff; no `skip`/`xfail` markers in the new file.
- Fakes only: `FakeAudioSource`, `FakeTranscriber`, `FakeInjector`, `FakeStatusUI`
  from `tests/fakes.py` (the established shape, reused from earlier tickets); one
  local `_BlockingAudioSource` subclasses the shared fake. No mic, no real model,
  no subprocess, no network. No decoder double is used (correct: the session is
  typed against the `Transcriber` protocol).
- `rm -rf .mypy_cache && uv run mypy src/ges2t tests` → `Success: no issues found in
  36 source files` (re-run after all mutations, fresh cache).
- ADR 0019: `disallow_untyped_defs` passes, so test defs are fully annotated
  (`-> None` included). No positional tuple is a public return in the module
  (`finish -> str`, `partials -> Iterator[str]`, `_stop_capture -> BaseException |
  None`). The private test helper `_session(...) -> tuple[...]` returns a positional
  tuple, consistent with the existing, accepted test idiom
  (`tests/test_pipeline.py:_session`); it is private and unpacked at the call site,
  which ADR 0019 permits.

## 7. My own mutations (each with a per-run timeout, restored)

Harness `/tmp/t15probe/mutate.py` (60 s external cap, `finally` restore, pycache
cleared). Real output:

| # | Mutation | Suite result | Caught? |
|---|---|---|---|
| MF | `finish()` returns latest partial | 10 failed, 11 passed | yes |
| MB | skip the final segment (`continue` on `is_final` in `_run`) | 9 failed, 12 passed | yes |
| MC | off-by-one: compare `_partials[0]` instead of `_partials[-1]` | 1 failed, 20 passed | yes |
| **MA** | **global dedup `segment.text not in self._partials`** | **21 passed** | **NO** |

MA is the subtle one and it is **not** caught. Demonstrated concretely with my
behaviour probe under MA:

```
(a) partials: ['a', 'a b']
AssertionError:  ... assert ['a','a b'] == ['a','a b','a']
```

The legitimate later repeat vanishes, yet the committed tests stay green because
`test_partials_advance_and_consecutive_duplicates_are_dropped` contains no
non-consecutive repeat. The shipped code uses `self._partials[-1] != segment.text`
(consecutive-only), so this is a **test gap**, not a product bug.

I could not independently re-run the implementer's full 15-mutation matrix; I
spot-checked four mutations. Three were caught. The claim "all 21 tests fail under at
least one mutation" is about per-test load-bearingness and is plausible but
unverified beyond my four.

## 8. Findings

**Blocking**
1. Unbounded waiting tests: M14-style deadlock makes `uv run pytest` hang at
   interpreter shutdown (`exit 124` after the summary prints). Tests that call
   `finish()`/`cancel()` directly cannot fail loudly. Fix direction: bound the
   production join, and/or ensure a deadlocked regression cannot leak a non-daemon
   worker (e.g. a test-scoped construction that does not leave it blocked, or
   accepting the risk explicitly in the ticket). A pytest timeout plugin is **not**
   an option (config must not change).

**Required test strengthening (non-product-bug)**
2. Consecutive-vs-global dedup is untested in the "legitimate repeat survives"
   direction; add a sequence with a non-consecutive repeat (e.g. `a, a, "a b",
   "a b", a`) and assert `["a","a b","a"]`.

**Verified good**
- Injection boundary (ADR 0011) holds and is a real guard (mutation falsified).
- All lifecycle, final-only, no-final ⇒ `""`, cancel-propagation and
  `chunk_seconds` behaviours.
- Lock never held across engine call / `audio.stop()` / `join`; stop before join.
- Scope, fakes-only, test counts, mypy, no skips.

## 9. Could not verify

- The implementer's complete 15-mutation matrix (only 4 spot-checked).
- Full-suite (`uv run pytest`, 536) behaviour under M14 — I ran the **new file**; the
  hang is caused by a test in that file, so the full suite hangs too, but I did not
  run it to completion under the mutation.
- Absence of any other latent unbounded wait under a *different* regression class;
  I established that the direct `finish()`/`cancel()` waits are the unbounded ones
  and that the regression that triggers them is real (M14). No live microphone,
  model, subprocess or network test was run (out of scope/forbidden).

## Appendix — stale-bytecode trap (method, for the orchestrator)

A mutation restored by `cp` in the same second leaves a `.pyc` that Python accepts
as current, so the next probe runs the mutated bytecode against the restored source.
Symptoms: `inspect.getsource` shows the correct source while the call behaves as the
mutation. Always delete `__pycache__` (and `.mypy_cache`) after a probe that mutates
`src/`. This is distinct from and additional to the same-byte-length mypy-cache trap.
