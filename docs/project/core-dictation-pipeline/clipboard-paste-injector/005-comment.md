# 005 — Fix comment: R2 (a failed restore-thread start must not fail the injection)

- **Ticket:** [ticket.md](ticket.md) (clipboard-paste-injector, #25)
- **Branch:** `feat/clipboard-paste-injector`
- **Worktree:** `.worktrees/clipboard-paste-injector`
- **Status:** in review (re-submitted after the R2 follow-up)
- **Fix commit:** `726358a` — *ticket 25: fix R2 (restore thread-start failure must not fail injection)*
- **Reviewed HEAD:** `1c93a35` (`004-review.md`, PASS with residual risks)
- **Pre-fix source used for falsification:** `1c93a35:src/ges2t/injectors/clipboard.py`
- **Docs commit:** the commit that adds this file (`005-comment.md`)
- **Base:** `1c93a35`. No ADR was edited.

This comment answers residual risk **R2** from
[`004-review.md`](004-review.md) §6. It is a two-file change: the local fix in
`_run_command` and one new test. It does **not** re-describe the module; see
[`001-comment.md`](001-comment.md) for the design and
[`003-comment.md`](003-comment.md) for the D1/D2/D3 fixes.

Nothing here (or in the suite) writes the user's real clipboard, runs
`wl-copy`/`wl-paste`/`ydotool`/`ges2t run`, or emits a keystroke. The R2 test
patches `threading.Thread.start` and routes the deferred restore through the real
`_run_command`/`_start_deferred_stdin` seam pointed at the harmless `true` (which
reads no stdin); payload write and paste chord stay on the injected fake runner.
No microphone is touched.

## The defect (R2)

`_start_deferred_stdin` calls `threading.Thread(...).start()`. In `_run_command`
only the `Popen` was inside `try/except OSError`; the thread start was **outside**
it, so a `RuntimeError` ("can't start new thread" under thread exhaustion)
propagated:

`_run_command` → the injected runner → `_restore_clipboard`'s
`except (OSError, subprocess.SubprocessError)`, which does **not** catch
`RuntimeError` → escaped `inject()`.

The consequence was specific and bad: this happens **after the payload has already
been pasted**, so a dictation whose text was successfully typed reported failure
and the service moved to `ERROR`. This bug class shipped once already in ticket 17
(narrowing an `except` stopped catching thread-start failures), which is why the
user asked for it to be fixed rather than filed.

## The fix (`clipboard.py:371-392`, commit `726358a`)

Only the deferred (restore) branch tolerates a failed thread start. The `except`
is deliberately scoped to the `_start_deferred_stdin` call and returns the same
synthetic non-zero status the branch already returns for a `Popen` spawn failure,
so `_restore_clipboard`'s existing `returncode != 0` arm emits a
`ClipboardRestoreWarning` and the injection still completes:

```python
    try:
        _start_deferred_stdin(proc, stdin_bytes)
    except RuntimeError:
        return subprocess.CompletedProcess(
            args=list(argv),
            returncode=1,
            stdout=b"",
            stderr=b"restore thread could not be started",
        )
```

It is **not** an `except Exception` around the whole runner. The payload write
(`wl-copy`) and the paste chord keep their typed fatal errors in `_run`
(`ClipboardInjectionError`, `ClipboardNotFoundError`, `ClipboardTimeoutError`,
`ClipboardSpawnError`); swallowing those would hide a broken paste — the D3
lesson. The comment in the code records which path is widened and why the other
must stay fatal.

## The test and its falsification

`tests/test_clipboard_injector.py::test_restore_thread_start_failure_does_not_fail_injection`
patches `threading.Thread.start` to raise `RuntimeError("can't start new thread")`
and drives `inject()`. The deferred restore goes through the real
`_run_command`/`_start_deferred_stdin` seam (`DelegatingRestoreRunner(target=None)`
retargets it to `true`), so the **real** thread creation is what fails, while the
payload write and chord stay on `FakeClipboardRunner`. The test asserts the
payload still reached `wl-copy` (with ADR 0012's trailing space), the chord was
still emitted, no exception escaped, and a `ClipboardRestoreWarning` was recorded.

`DelegatingRestoreRunner` gained an optional `target: Path | None`; a real target
keeps the existing D1 `tee` behaviour unchanged, and `None` selects `true` so no
child can block on stdin when the writer thread never starts.

### Falsification — the new test FAILS against the pre-fix source

The pre-fix file came from git, not a hand mutation:

```
$ git show 1c93a35:src/ges2t/injectors/clipboard.py > src/ges2t/injectors/clipboard.py
pre-fix sha256: 3c4e8ed6ee700524fb8bfa0f2b414ed3a5d1d41db5844c84bd0ec2e6d719dfdb
$ rm -rf .mypy_cache __pycache__ && timeout 120 uv run pytest -q -p no:cacheprovider \
      tests/test_clipboard_injector.py -k thread_start_failure
...
src/ges2t/injectors/clipboard.py:371: in _run_command
    _start_deferred_stdin(proc, stdin_bytes)
src/ges2t/injectors/clipboard.py:383: in _start_deferred_stdin
    ).start()
...
    def failing_start(self: threading.Thread) -> None:
>       raise RuntimeError("can't start new thread")
E       RuntimeError: can't start new thread
...
FAILED tests/test_clipboard_injector.py::test_restore_thread_start_failure_does_not_fail_injection
1 failed, 87 deselected in 0.29s
PRE_FIX_EXIT=1
```

The `RuntimeError` escapes through `_restore_clipboard` exactly as R2 describes.
(`pytest.warns` then also reports `DID NOT WARN` because the exception aborted the
block before its warning check; the traceback above is the load-bearing evidence.)

### Restored fix — the same test passes, byte-identically

```
$ cp /tmp/r2falsify/keep_clipboard.py src/ges2t/injectors/clipboard.py
$ sha256sum -c /tmp/r2falsify/head_sha.txt
src/ges2t/injectors/clipboard.py: OK
tests/test_clipboard_injector.py: OK
$ git diff --stat -- src tests      # empty: restored byte-identically
$ rm -rf .mypy_cache __pycache__ && timeout 120 uv run pytest -q -p no:cacheprovider \
      tests/test_clipboard_injector.py -k thread_start_failure
.                                                                        [100%]
1 passed, 87 deselected in 0.19s
POST_FIX_EXIT=0
```

So the test fails before the fix and passes after: it is load-bearing, not a
tautology.

## Verification (real output, caches cleared)

```
$ rm -rf .mypy_cache __pycache__ && timeout 300 uv run pytest -q
687 passed in 4.22s
PYTEST_EXIT=0                     # process exits; no leaked-thread hang

$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 41 source files
MYPY_EXIT=0
```

The suite is `686 + 1 = 687` (the reviewed HEAD's `686` plus this one R2 test).
`.mypy_cache`/`__pycache__` are cleared before both commands, and again before the
pre-fix falsification run, so a stale cache cannot mask a reverted mutation. No
test was weakened, skipped, xfailed or deleted: the only edit to the existing
suite is the additive `target: Path | None` on `DelegatingRestoreRunner`, which
preserves the D1 tests' real-`tee` path. `git diff main...HEAD --
src/ges2t/injectors/ydotool.py tests/test_ydotool_emitter.py tests/test_layout.py`
is empty (the `ydotool` path and its tests are byte-identical).

## What I could NOT verify

- **Real `wl-copy`/`wl-paste`/`ydotool`/`ydotoold` behaviour and the manual
  end-to-end run.** Forbidden here (writes the user's clipboard and types into the
  focused window) and user-owned; the checklist in `001-comment.md` remains the
  gate.
- **Real thread exhaustion.** The `RuntimeError` is injected by patching
  `Thread.start`; the actual OS condition ("can't start new thread") was not
  reproduced, only its exception contract.
- **The real 0.4 s-vs-application-round-trip race** — unobservable without a live
  Wayland session.
- **What the spawned restore child does when the writer thread never starts.** In
  the test the child is `true`; in production it would be `wl-copy` reading EOF
  once the unreferenced `Popen` is collected, i.e. an empty restore. That is a
  best-effort outcome and is **not** cleaned up here (see R3/R1 below).

## Out of scope (not attempted, as instructed)

- **R1** (truncated >64 KiB restore on process exit) and **R4** (a non-
  `FileNotFoundError` `OSError` on the read path still escapes raw) are untouched.
- **R3** (a daemon writer thread blocked forever when the restore child never
  reads) is untouched. This fix only stops a *failed start* from failing the
  injection; it does not address a thread that starts and then blocks.

## Anything a reviewer must know

- The fix lands in the deferred branch of `_run_command`, which is used **only**
  for the restore; the immediate path (payload write and, via the injector, the
  read) is unchanged.
- When `Thread.start` fails, the already-spawned restore child is not killed. It
  is unreferenced and its stdin closes on collection, so a real `wl-copy` would
  restore an empty selection. This is inside "restore is a courtesy, not a
  guarantee"; if the reviewer wants the child terminated on this path, that is a
  separate (larger) change.
- The ticket status and `docs/project/README.md` row 25 already read `in review`
  on this branch; no row was changed (row 25 needs no update for this fix) and no
  renumbering was done. The folder now holds `001-comment.md`, `002-review.md`,
  `003-comment.md`, `004-review.md`, `005-comment.md`, and `ticket.md`.
