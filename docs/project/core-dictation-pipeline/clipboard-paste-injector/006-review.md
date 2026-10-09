# 006 — Focused re-review: the R2 delta (clipboard-paste injector, #25)

- **Ticket:** [ticket.md](ticket.md) (clipboard-paste-injector, #25)
- **Branch:** `feat/clipboard-paste-injector`
- **Worktree:** `.worktrees/clipboard-paste-injector`
- **Reviewed HEAD:** `fc556cc` (`005-comment.md`)
- **Previous PASS (delta base):** `1c93a35` (`004-review.md`)
- **Fix commit under review:** `726358a` — *ticket 25: fix R2 (restore thread-start failure must not fail injection)*
- **Delta basis:** `git diff 1c93a35..HEAD` — exactly three files (`005-comment.md`, `clipboard.py`, `test_clipboard_injector.py`)
- **Scope:** **the delta only.** D1/D2/D3 were verified in `004-review.md` and are not re-reviewed here.

## Verdict: **PASS** — the delta fixes R2 narrowly; one disclosed claim is factually wrong and is recorded as a new non-blocking residual risk (R5)

The delta does what it says: it wraps **only** the deferred restore's
`_start_deferred_stdin` call in `except RuntimeError` and returns the synthetic
non-zero status the deferred branch already used for a spawn failure, so
`_restore_clipboard` emits a `ClipboardRestoreWarning` instead of failing an
injection whose payload was already pasted. I reproduced the falsification
independently against the exact pre-fix blob, probed every fatal path with the
real `_run_command`, and found the fix does **not** widen the payload write or
the paste chord. The new test is load-bearing.

Two things the orchestrator must carry forward, neither a delta blocker:

1. **R5 (new, measured, non-blocking):** `005-comment.md` says the already-spawned
   restore child is "unreferenced and its stdin closes on collection, so a real
   `wl-copy` would restore an empty selection." That mechanism is **wrong**: a
   still-running `Popen` is appended to `subprocess._active`, which keeps the
   `Popen` and its stdin pipe alive, so the child **blocks indefinitely** on
   stdin for the life of the service. The empty restore only happens when the
   parent closes the fd (normally at process exit). The user-visible clipboard
   outcome is still "restore did not happen" (the transcript stays), which is no
   worse than the R1 truncation already recorded, but the disclosed timing is
   wrong and the child is leaked. See §4/§7.
2. **Integration:** the branch is based on an old merge-base; `main` has advanced
   and an in-memory merge conflicts in `docs/project/README.md` and
   `src/ges2t/pipeline.py` (not files the delta touches). The branch needs a
   rebase/reconciliation before merge. See §9.

---

## 0. Safety of this review

No `wl-copy`/`wl-paste`/`ydotool`/`ges2t run`, no keystroke, no microphone. All
probes used synthetic runners plus harmless `true`/`tee`/`sleep`/missing-binary
under `/tmp/r2-verify`; the only real children the suite spawns are `tee` under
`pytest`'s `tmp_path` (the D1 seam). All mutations were reverted byte-identically
(`sha256sum -c` + empty `git diff`), verified in §1/§2/§5.

---

## 1. The falsification is real: the new test fails on the exact pre-fix blob

I did not trust the quoted output. I overlaid `1c93a35:src/ges2t/injectors/clipboard.py`,
confirmed the sha256, ran the single test, then restored HEAD byte-identically.

```
$ git show 1c93a35:src/ges2t/injectors/clipboard.py | sha256sum
3c4e8ed6ee700524fb8bfa0f2b414ed3a5d1d41db5844c84bd0ec2e6d719dfdb  -
# (matches 005-comment.md's quoted pre-fix hash exactly)
$ cp /tmp/r2-verify/prefix.py src/ges2t/injectors/clipboard.py
$ rm -rf .mypy_cache && find . -name __pycache__ -exec rm -rf {} +
$ timeout 120 uv run pytest -q -p no:cacheprovider tests/test_clipboard_injector.py -k thread_start_failure
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

The `RuntimeError` escapes through `_restore_clipboard` exactly as R2 described.
Restored and re-run:

```
$ cp /tmp/r2-verify/keep_head.py src/ges2t/injectors/clipboard.py
$ sha256sum -c /tmp/r2-verify/head_sha256.txt
src/ges2t/injectors/clipboard.py: OK
tests/test_clipboard_injector.py: OK
$ git diff --stat -- src tests          # empty
$ timeout 120 uv run pytest -q -p no:cacheprovider tests/test_clipboard_injector.py -k thread_start_failure
1 passed, 87 deselected in 0.19s
POST_FIX_EXIT=0
```

The pre-fix and post-fix source hashes are distinct
(`3c4e8ed6…` vs `deef264e…`), the working tree is byte-identical to `HEAD`, and
the test flips from FAIL to PASS. **It tests something.**

---

## 2. The fix is not too broad — the fatal paths are intact (probed, not read)

This is the load-bearing risk, and it is clean. I probed the actual fixed code
with synthetic runners (one configured failure per binary/phase) **and** the real
`_run_command` immediate branch with harmless commands under `/tmp`. Every fatal
path still produces its exact typed error; a `RuntimeError` on the payload or
chord path still propagates (it is not reached by the new arm).

```
=== fatal payload write (wl-copy) remains typed ===
[PASS] payload FileNotFoundError: raised ClipboardNotFoundError
[PASS] payload TimeoutExpired: raised ClipboardTimeoutError
[PASS] payload OSError: raised ClipboardSpawnError
[PASS] payload returncode!=0: raised ClipboardInjectionError
=== fatal paste chord (ydotool) remains typed ===
[PASS] chord FileNotFoundError: raised ClipboardNotFoundError
[PASS] chord TimeoutExpired: raised ClipboardTimeoutError
[PASS] chord OSError: raised ClipboardSpawnError
[PASS] chord returncode!=0: raised ClipboardInjectionError
=== read path tolerance unchanged (D3) ===
[PASS] read TimeoutExpired tolerated: no exception
[PASS] read FileNotFoundError tolerated: no exception
=== a RuntimeError on the payload/chord path is NOT swallowed by the new arm ===
[PASS] payload RuntimeError propagates: raised RuntimeError
[PASS] chord RuntimeError propagates: raised RuntimeError
=== real _run_command immediate branch: raw exceptions are what _run maps ===
[PASS] real missing binary -> FileNotFoundError
[PASS] real timeout -> TimeoutExpired after 5.0s
[PASS] real non-executable -> PermissionError: [Errno 13] Permission denied
15/15 probes passed
```

Structurally this is guaranteed by **placement**: the immediate branch
(`delay_s is None`) `return`s before the new `try`, and the typed errors are
raised in `_run`, one level up — the new arm cannot see them. The probe confirms
the placement holds in the running code.

### Sensitivity check (mutation M1) — the probe is load-bearing

To prove the probe would catch a too-broad fix, I mutated `_run_command`'s
immediate branch to swallow a spawn failure as success (the D3 hazard: a broken
paste reported as fine):

```python
    if delay_s is None:
        try:
            return subprocess.run(...)
        except BaseException:
            return subprocess.CompletedProcess(args=list(argv), returncode=0, ...)
```

Result:

```
=== probe against M1 ===
... synthetic-runner probes still PASS (they bypass _run_command) ...
[FAIL] real missing binary: no exception
[FAIL] real timeout: no exception
[FAIL] real non-executable: no exception
12/15 probes passed
PROBE_M1_EXIT=1
$ uv run pytest -q ... -k "missing_wl_copy or timeout_maps or generic_spawn or missing_ydotool or wl_copy_nonzero"
6 passed, 82 deselected
PYTEST_M1_EXIT=0
```

So the real-command probes catch the swallow (the suite's fatal-path tests do
not, because they inject a fake runner and only exercise `_run`'s mapping — a
pre-existing coverage gap, noted in §7, not a blocker). M1 was reverted and the
tree verified byte-identical.

**Conclusion:** the delta widens nothing that should stay fatal. A broken
`wl-copy` payload or a broken paste chord is still fatal and still typed.

---

## 3. What else can raise `RuntimeError` inside that `try`

The `try` body is exactly one statement, `_start_deferred_stdin(proc, stdin_bytes)`
(verified by source containment), whose body is exactly
`threading.Thread(target=…, args=…, daemon=True).start()`. Enumerating the
`RuntimeError` raisers in those two calls on this interpreter (CPython 3.14.7):

```
Thread.__init__: ["raise RuntimeError('daemon threads are disabled in this (sub)interpreter')"]
Thread.start:    ['raise RuntimeError("thread.__init__() not called")',
                  'raise RuntimeError("threads can only be started once")']
```

plus the real exhaustion error from the underlying `_thread` C call
(`RuntimeError: can't start new thread`, what the test simulates). The
constructor case is reachable because the code passes `daemon=True`; I forced it
and confirmed the new arm handles it:

```
=== Thread constructor RuntimeError is inside the try ===
[PASS] ctor RuntimeError -> rc=1 stderr=b'restore thread could not be started'
```

**Finding:** the catch is broader than "`Thread.start()` only" by exactly one
case — the `Thread` **constructor** under a subinterpreter with daemon threads
disabled. That case has the same meaning (no deferred writer thread can exist)
and the same correct remedy (best-effort restore), so it is benign. No other
statement in the `try` can raise `RuntimeError`. The `try` is not an
`except Exception` around the runner.

---

## 4. The already-spawned child — `005-comment.md` is **not accurate**

`005-comment.md` (and its "Anything a reviewer must know") states the child is
unreferenced and "its stdin closes on collection, so a real `wl-copy` would
restore an empty selection", i.e. a prompt EOF. I measured otherwise with a real
`tee` under `/tmp` (a child that reads stdin, unlike the test's `true`):

```
$ timeout 90 uv run python /tmp/r2-verify/probe_child_blocked.py
rc=1 stderr=b'restore thread could not be started'
subprocess._active after gc: [<Popen: returncode: None args: ['sh', '-c', 'sleep 0.300; exec "$@"', ...>]
after gc + 2.5s parent still alive:
  live tee processes: ['3833853 Ss   tee /tmp/r2-verify/tee_out2']
  subprocess._active: 1 entries
  tee_out2 size: 0
-- dropping subprocess._active (closes the write end) --
  live tee processes after close: <none>
  tee_out2 size after child exits: 0
```

The cause is in the stdlib, confirmed by reading `subprocess.Popen.__del__`:

```python
    if self.returncode is None and _active is not None:
        # Child is still running, keep us alive until we can wait on it.
        _active.append(self)
```

So when the failed-start path drops its local `Popen`, the object is **not**
finalized to close the pipes; it is retained in `subprocess._active`, its stdin
`BufferedWriter` stays open, and the child blocks on `read()` forever. A real
`wl-copy` would therefore **hang open for the life of the ges2t process** (and a
`wl-copy` is created per injection that hits this path, so continued dictation
under thread exhaustion leaks one blocked `wl-copy` each time). It only sees EOF
when the parent closes the fd — normally at process exit — at which point it
writes an empty selection.

- **Is the outcome acceptable?** Yes. `wl-copy` never wrote anything, so the
  clipboard keeps the transcript, which is exactly the "restore did not happen"
  outcome a failed best-effort restore means. It does **not** corrupt the
  selection the way R1's truncation does, and it does not touch the transcript.
- **Is it newly introduced?** No. The child was already spawned on the pre-fix
  path too, and `service.py` catches the escaping exception
  (`except Exception as exc: self._fail(...)`), drops the traceback, and the same
  `_active` retention leaks the same blocked child. The delta changes the service
  state from `ERROR` to success; it does not create the leak.
- **Cost:** a genuine (rare) process leak, and the disclosed mechanism is wrong.
  A one-line fix on the failure path — `proc.stdin.close()` (or `proc.kill()` /
  `proc.wait()`) — would make a real `wl-copy` read EOF and exit promptly and make
  the comment true. The implementer called terminating the child "a separate
  (larger) change"; closing the pipe is not.

---

## 5. Nothing else moved

```
$ git diff 1c93a35..HEAD --name-only
docs/project/core-dictation-pipeline/clipboard-paste-injector/005-comment.md
src/ges2t/injectors/clipboard.py
tests/test_clipboard_injector.py
```

- **No test deleted, weakened, skipped or xfailed.** `git diff 1c93a35..HEAD --
  tests/test_clipboard_injector.py` is purely additive plus the
  behavior-preserving `DelegatingRestoreRunner.__init__(target: Path | None)`
  widening (a real `target` still routes to the `tee` D1 seam; `None` routes to
  `true`). `grep -nE "skip|xfail" tests/test_clipboard_injector.py
  tests/test_pipeline.py` → none. The file collects 88 (was 87 before the one new
  test).
- **`ydotool.py` and its tests are unchanged across the delta** and `ydotool.py`
  is byte-identical to `main` (`da8d2d56…`). Note: `tests/test_ydotool_emitter.py`
  and `tests/test_layout.py` are byte-identical to the merge-base but **differ
  from `main`'s tip** — because `main` advanced, not because this branch touched
  them. (005's `git diff main...HEAD -- …` is a three-dot/merge-base comparison,
  so its "byte-identical" wording is true only relative to the merge-base; see
  §9.)
- **The board row was not touched by the delta:**
  `git diff 1c93a35..HEAD -- docs/project/README.md` is empty. Row 25 already
  reads `in review`, which is correct.
- The folder holds `001`–`005` plus `ticket.md`; `006-review.md` (this file) is
  the next free number.

---

## 6. Full suite and type check (caches cleared)

```
$ rm -rf .mypy_cache && find . -name __pycache__ -exec rm -rf {} +
$ timeout 300 uv run pytest -q
687 passed in 2.41s
PYTEST_EXIT=0            # process exits; no leaked-thread hang at exit
$ rm -rf .mypy_cache && uv run mypy src/ges2t tests
Success: no issues found in 41 source files
MYPY_EXIT=0
```

`686 (1c93a35) + 1 = 687` matches the implementer's count.

---

## 7. New / residual risks

- **R5 (new in this delta, measured, non-blocking):** on the thread-start-failure
  path the already-spawned restore child is retained via `subprocess._active`
  and blocks on stdin for the life of the service; it is leaked, one per
  occurrence, until the parent closes the fd. `005-comment.md`'s claim that
  "stdin closes on collection" is wrong. User-visible clipboard outcome is
  acceptable (transcript stays; no corruption), and the leak is not newly
  introduced, but it should be corrected in the record and preferably fixed with
  `proc.stdin.close()` on the failure path.
- **R2 is closed** for the injection-failure symptom: a failed restore-thread
  start no longer fails a pasted dictation.
- **R1 (truncated >64 KiB restore on process exit) and R3 (blocked daemon writer
  thread) remain** as recorded in `004-review.md`; the delta does not address
  them.
- **Coverage gap (pre-existing, non-blocking):** the suite's typed-error tests use
  a fake runner and therefore do not pin that the **real** `_run_command` immediate
  branch raises `FileNotFoundError`/`TimeoutExpired`/`OSError`. Mutation M1
  (swallow as success) passed those tests but failed my real-command probe. A
  small test using a missing binary / `sleep` under `/tmp` would close it; out of
  scope for this delta.

---

## 8. What I could not verify

- **Real thread exhaustion.** The `RuntimeError` is injected by patching
  `Thread.start` (and separately the constructor); the OS condition itself was
  not reproduced, only its exception contract.
- **Real `wl-copy`/`wl-paste`/`ydotool`/`ydotoold` behaviour and the manual
  end-to-end run.** Forbidden here (writes the user's clipboard, types into the
  focused window) and user-owned; `001-comment.md`'s checklist remains the gate.
- **The real `wl-copy` behaviour on the leaked-child path.** I used `tee`; the
  pipe/`_active` mechanics are generic, but I did not observe a real `wl-copy`
  blocked on stdin.
- **The real 0.4 s-vs-application-round-trip race** — unobservable without a live
  Wayland session.
- Whether any Python version other than the local CPython 3.14.7 changes the
  `subprocess._active` retention, though it is long-standing behaviour.
- **The end-to-end service consequence** of the fix (the injector now returns
  success where it used to raise); the state machine itself was not re-reviewed.

---

## 9. Merge safety

**The delta is safe to merge.** It is a two-line-scope behavioral change plus one
test, it preserves every fatal path, and the new test is load-bearing. The one
defect in the delta is a wrong sentence in `005-comment.md` (R5), which should be
corrected in the record; it does not change the code's behaviour or corrupt
anything.

Separately, **the branch as a whole is not merge-clean against the current
`main`**, independently of this delta. `main` has advanced well past the
merge-base (`00adf753`), and a read-only in-memory merge reports conflicts:

```
$ git merge-tree --write-tree main HEAD
cef44ee00257a6c7d782a6edc63cee65477e122b
...
CONFLICT (content): Merge conflict in docs/project/README.md
CONFLICT (content): Merge conflict in src/ges2t/pipeline.py
Auto-merging tests/test_pipeline.py
MERGE_TREE_EXIT=1
```

The branch changed `docs/project/README.md`, `src/ges2t/pipeline.py`,
`tests/test_pipeline.py` and added the clipboard artifacts relative to the
merge-base; `main` also changed the first three (plus
`src/ges2t/injectors/layout.py`, `tests/test_layout.py`,
`tests/test_ydotool_emitter.py`, and more). A rebase onto `main` and a
reconciliation of the board row and `build_injector` wiring is required before
merge; the delta's own files (`clipboard.py`, `test_clipboard_injector.py`,
`005-comment.md`) do not conflict. This is an integration task, not a delta
defect.

## Recommendation

Merge the delta after (a) correcting the `005-comment.md` claim about the restore
child and adding R5, and (b) rebasing the branch onto `main`. Prefer also closing
`proc.stdin` on the failure path, which is a one-line change that makes the
disclosed empty-restore behaviour prompt and stops the child leak.
