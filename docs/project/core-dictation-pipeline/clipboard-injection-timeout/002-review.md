# 002 — Review: clipboard injection timeout (#36)

- **Ticket:** [ticket.md](ticket.md)
- **Branch:** `feat/clipboard-injection-timeout`
- **Worktree:** `.worktrees/clipboard-injection-timeout`
- **Reviewed HEAD:** `700ec44` (`ticket 36: comment artifact …`)
- **Fix commit:** `2e9fb05` — *ticket 36: stop blocking on a forked child's inherited pipes in the immediate runner*
- **Diff basis:** `git diff main...HEAD` (`main` = `78141d2`)
- **Pre-fix blob:** `src/ges2t/injectors/clipboard.py` sha256 `deef264efdf6bd370ddacd4944b4f2e1cc01d56fbebc8f349154b6f7f97f7bfb`
- **Fixed blob:** `9e0f4e9b35191622e09c5b970149924027068ec47e6f885e466b0a345f2bfed5`
- **Test blob:** `tests/test_clipboard_injector.py` sha256 `c1ec752bcbd5759ac6a0fd5f0b80464dfcfe4f40b849528e9653db0a6fa53040`

## Verdict: **PASS**

The defect is real, the fix removes it, the new real-child test is load-bearing
(I reproduced it failing against the pre-fix blob and passing after a
byte-identical restore), stderr and stdout are both still captured, the timeout
bound survives, and the suite and type-check are green. The user can be told to
re-run their manual clipboard test.

Two non-blocking findings, neither a reason to hold the branch:

1. **A transcript inaccuracy, not a code defect.** `001-comment.md`'s
   falsification quotes `git show HEAD:src/ges2t/injectors/clipboard.py` as
   producing the pre-fix `deef264e…`. That command produces the **fixed**
   blob `9e0f4e9b…` (HEAD already contains the fix). The pre-fix file must come
   from `main` (or `2e9fb05^`). The recorded hash is correct and matches
   `git show main:…`; only the quoted command is wrong. See §2.
2. **Coverage gap (pre-existing, relevant to the DoD's "real seam" intent).** No
   test exercises delivery of a **non-empty** payload through the real
   immediate runner: mutating `input=stdin_bytes` → `input=b""` leaves the whole
   suite green (877 passed). See §11 (M4). Recommend adding one real-child
   assertion that stdin reaches the child.

Two residuals are correctly **not** treated as fixed: R5 (see §8) and the
generic stdin-buffer hazard for hypothetical binaries (see §5).

---

## 0. Safety of this review

No real clipboard was touched: no `wl-copy`/`wl-paste` with data, no `ydotool`,
no keystroke, no microphone, no `ges2t run`. Every probe used harmless `sh`,
`cat`, `tee`, `/usr/bin/python3`, and tiny stand-in scripts under
`/tmp/t36-review`, writing only under `/tmp`. Real `wl-copy` was **not**
executed; its behaviour was read from the installed man page and the upstream
`v2.3.0` source (the installed package is `wl-clipboard 1:2.3.0-1`). All
mutations were reverted and the tree verified byte-identical (`sha256sum -c` +
empty `git status`) before the next step.

---

## 1. The fix removes the blocking for a real forking child (measured)

Through the **real** `_run_command` (no clipboard), against the fixed tree:

```
$ timeout 120 uv run python /tmp/t36-review/probe_runner.py
module file: …/src/ges2t/injectors/clipboard.py
--- forking child (stdout+stderr captured) ---
returned elapsed=0.01s (7, b'MARKER\n', b'BOOM\n')
--- stdout captured ---
returned elapsed=0.00s b'PREVIOUS'
--- wedged no-fork (sleep 30) ---
raised TimeoutExpired elapsed=0.50s: TimeoutExpired(['sh', '-c', 'sleep 30'], 0.4999…)
```

The forking stand-in is `sh -c 'sleep 30 & echo $! > …; echo MARKER; echo BOOM >&2; exit 7'`
— the backgrounded `sleep` inherits stdout/stderr exactly as `wl-copy`'s daemon
does, and the call now returns in 0.01 s with `rc=7`, stdout `MARKER`, stderr
`BOOM`. Identical stand-in against the pre-fix blob raises `TimeoutExpired`
(§2). **The fix works.**

I also read upstream `wl-copy.c` (`v2.3.0`) to confirm the mechanism rather than
assume it:

- `main()` calls `dump_stdin_into_a_temp_file()` (a fork+`waitpid` of `cat`, not a
  daemon) — **stdin is fully read before the daemonising fork**;
- `did_set_selection_callback()` does `dup2(devnull, STDOUT_FILENO)` and
  `dup2(devnull, STDIN_FILENO)` **before** `fork()`, but leaves **stderr**
  untouched, then the parent `exit(0)`s.

So the forked daemon holds the inherited **stderr** pipe (stdout is re-pointed
to `/dev/null`); `subprocess.run(capture_output=True)` waited on that stderr
pipe's EOF, which never comes while the selection lives. This matches the
ticket exactly. Redirecting **both** streams to regular files is correct and, if
anything, the more robust choice.

---

## 2. Falsification of the new test (reproduced independently)

`git show main:src/ges2t/injectors/clipboard.py` = `deef264e…`, matching the
artifact's recorded pre-fix hash. (Note: `git show HEAD:…` = `9e0f4e9b…`, the
**fixed** blob, so the artifact's quoted command is mis-transcribed — the hash,
not the command, is right.)

```
$ git show main:src/ges2t/injectors/clipboard.py > src/ges2t/injectors/clipboard.py
$ sha256sum src/ges2t/injectors/clipboard.py
deef264efdf6bd370ddacd4944b4f2e1cc01d56fbebc8f349154b6f7f97f7bfb  src/ges2t/injectors/clipboard.py
$ rm -rf .mypy_cache __pycache__ && find . -name __pycache__ -type d -prune -exec rm -rf {} +
$ timeout 120 uv run pytest -q -p no:cacheprovider tests/test_clipboard_injector.py \
    -k "real_forking_child or real_immediate_runner"
F..
…
    stderr_seq = [b'no compositor\n'], skip_check_and_raise = False
…
E           subprocess.TimeoutExpired: Command '['sh', '-c', "sleep 30 & echo $! > …/forked-child.pid; printf 'wrote-and-forked' > …/forked-child-marker.txt; echo 'no compositor' >&2; exit 7"]' timed out after 5.0 seconds
FAILED tests/test_clipboard_injector.py::test_real_forking_child_does_not_block_the_immediate_path
1 failed, 2 passed, 88 deselected in 5.64s
PREFIX_EXIT=1
```

The traceback is decisive: `stderr_seq = [b'no compositor\n']` shows the child
had already written stderr, yet the pre-fix call still blocked to the 5 s
timeout. The other two new tests pass pre-fix too (they pin properties the fix
must not break, not the bug).

Restore, verified byte-identically, then re-run:

```
$ cp /tmp/t36-review/fixed_keep.py src/ges2t/injectors/clipboard.py
$ sha256sum -c /tmp/t36-review/fixed_keep.sha
…/fixed_keep.py: OK
$ git status --porcelain          # empty
$ rm -rf .mypy_cache __pycache__ && find . -name __pycache__ -type d -prune -exec rm -rf {} +
$ timeout 120 uv run pytest -q -p no:cacheprovider tests/test_clipboard_injector.py \
    -k "real_forking_child or real_immediate_runner"
...                                                                      [100%]
3 passed, 88 deselected in 0.44s
POSTFIX_EXIT=0
```

The test flips FAIL → PASS against distinct, verified blobs. **It tests
something.**

---

## 3. Error contract survives (code **and** real probe)

Code: `_run_immediate` reads both `TemporaryFile` objects back into a
`CompletedProcess`; `_write_payload`/`_send_paste_chord` feed `completed.stderr`
to `_decode_stderr` and the typed messages. No stream is sent to `DEVNULL`.

Real probe — the module's `WL_COPY_BINARY`/`WL_PASTE_BINARY` are pointed at
harmless stand-in scripts (no real clipboard), and the **real** `_run_command`
is the runner:

```
$ timeout 60 uv run python /tmp/t36-review/probe_error_contract.py
ClipboardInjectionError binary='/tmp/t36-review/fail_copy.sh' rc=3 stderr='no compositor'
OK: stderr reached the typed error message
read_clipboard -> b'OLDCLIP'
OK: stdout still captured for the previous selection
ERROR_CONTRACT_PROBE_DONE
```

So: **stderr is still captured and still reaches `ClipboardInjectionError`**, and
**stdout is still captured** (the previous selection for `wl-paste`). This was
not achieved by dropping a stream — M1/M2 in §11 show that `DEVNULL` on either
stream fails a test.

---

## 4. The timeout is still enforced

Code: `_run_immediate` passes the same `CLIPBOARD_COMMAND_TIMEOUT_SECONDS` to
`subprocess.run(..., timeout=…)`; `_run` maps `TimeoutExpired` →
`ClipboardTimeoutError`.

Real probe with the **real 5.0 s constant**, a genuinely wedged command that
does **not** fork:

```
$ timeout 60 uv run python /tmp/t36-review/probe_cleanup_timeout.py
constant=5.0
TimeoutExpired after 5.00s timeout=5.0
```

The in-suite test `test_real_immediate_runner_still_bounds_a_wedged_child` pins
the bound with a monkeypatched 0.2 s constant. **The fix removed the false
positive, not the bound.**

---

## 5. The large-payload stdin question

**Answer: the immediate path can still block on stdin in the generic shape, but
the hazard is not reachable with the real binaries ges2t runs.** Measured, not
reasoned away.

`_run_immediate` passes the payload with `input=`, so `subprocess.run` uses
`communicate()`, which writes the payload into the child's stdin **pipe**. If a
child forks a non-reading daemon that keeps the pipe's read end, and the payload
exceeds the 64 KiB pipe buffer, the parent's write has nowhere to go:

```
$ timeout 120 uv run python /tmp/t36-review/probe_stdin.py
--- small payload, child forks holding stdin open ---
returned elapsed=0.07s CompletedProcess(… returncode=0 …)
--- 1MiB payload, child forks holding stdin open (never reads) ---
raised TimeoutExpired elapsed=3.00s: TimeoutExpired([… '/tmp/t36-review/forker.py'], 3.0)
```

A corrected drain-then-fork child (drains 1 MiB, then the parent exits while the
child sleeps) returns immediately:

```
1MiB, child drains stdin then forks+parent exits -> rc=0 elapsed=0.04s
```

Why it is **not reachable in production**:

- `wl-copy` reads **all** of stdin (`dump_stdin_into_a_temp_file`) **before** the
  daemonising fork, and `dup2`s its own stdin to `/dev/null` before forking — the
  daemon never holds the stdin pipe (upstream `v2.3.0` source, §1);
- the only other immediate binary with a payload is none: `wl-paste` and
  `ydotool key` are called with `stdin_bytes=b""`, so the write is empty.

So this is a documented, non-reachable-in-practice residual of the same class,
not a regression and not introduced by the fix. It is the reason a future
change to a *different* daemonising binary should re-open this question.

---

## 6. The new test cleans up after itself (verified)

The test's `finally: _kill_pid_file(pid_file)` SIGKILLs the backgrounded `sleep`.
I verified the pid really dies and that repeated runs leak nothing:

```
$ timeout 60 uv run python /tmp/t36-review/probe_cleanup_timeout.py
child pid=3932883 alive_before_cleanup=True
alive_after_cleanup=False (expect False)

$ for i in $(seq 1 8); do … pytest … -k real_forking_child … ; pgrep -af 'sleep 3[0]' | wc -l ; done
run 1: pytest_rc=0 strays=0 1 passed, 90 deselected in 0.12s
… (runs 2–8 identical) …
=== final strays ===
none
```

No stray processes after the focused loop or after the full suite. The pid file
is a real reaper's handle, and the process it names is dead.

---

## 7. Hazard audit of the other real-child paths (source-verified)

| Path | Can it fork? | Verdict |
|---|---|---|
| `wl-copy` (immediate write) | **Yes**, daemonises in `did_set_selection_callback`; daemon keeps **stderr**, re-points stdin/stdout to `/dev/null`. | The defect. Now safe: stderr/stdout are regular files, so the wait ends when the immediate process exits. |
| `wl-paste` (immediate read) | **No daemon.** It internally forks `cat` (or the `--watch` command) but **`waitpid`s it** (`run_paste_command`). ges2t passes only `--no-newline`, never `--watch`. | Safe pre- and post-fix; the forked `cat` exits before `wl-paste` does. Also now uses the file redirect, so an unexpected fork is still safe. |
| Deferred restore child (`sh -c 'sleep …; exec wl-copy'`) | **Yes** (the detached daemon itself, and its `exec`'d `wl-copy` forks again). | Spawned `Popen(..., stdout=DEVNULL, stderr=DEVNULL, stdin=PIPE, start_new_session=True)` and **not waited on**; the daemon's inherited fds are all `/dev/null`, so no read-to-EOF to block on. Genuinely **unchanged** by this fix. |
| `ydotool key` (paste chord) | **No.** Upstream client has no `fork`/`exec`/`daemon`/`setsid`; it is an `AF_UNIX SOCK_DGRAM` socket client that `connect`s, `write`s and exits. | Safe; stderr still captured for the typed error. |

Conclusion: the only real daemonising binary on the fatal path is `wl-copy`, and
it is exactly what this fix covers.

---

## 8. The R5 claim — verified, not taken on trust

The implementer says R5 (the leaked blocked restore child) is **not** removed,
because it is a different branch/pipe/direction, and `proc.kill()` remains the
remedy. That reasoning is correct, and I reproduced R5 **on the fixed tree** by
simulating a failed deferred thread start with a harmless `tee`:

```
$ timeout 60 uv run python /tmp/t36-review/probe_r5.py
synthetic result rc= 1 stderr= b'restore thread could not be started'
_active entries: 1 tee pid: 3936541
tee alive after 1s: True  out size: 0 (0 => blocked on read, never wrote)
leaked child reaped by probe
R5_PROBE_DONE
```

The `Popen` is retained via `subprocess._active`, its `stdin` write end stays
open, and the child blocks on `read()` having written nothing. The fix only
changes the **immediate** branch's stdout/stderr redirection; the deferred
branch's `stdin=PIPE` and the `_active` retention are byte-for-byte untouched
(§10). So R5 persists by design and is correctly left out of scope; the future
one-line `proc.kill()` (not `stdin.close()`) remains the remedy.

---

## 9. Real-clipboard safety still holds

- All four `ClipboardPasteInjector(` construction sites in the test file
  (lines 182, 458, 566, 925) pass `runner=…` (`FakeClipboardRunner`, or a
  retargeting runner). There is no bare construction anywhere.
- The only real `_run_command` call sites in the test file are:
  `_run_command(("tee", …), …, delay_s=0.05)` (deferred, harmless), and
  `("sh", "-c", …)` (immediate, harmless). **No test runs `wl-copy` or
  `wl-paste` with data.**
- The two defer/real-seam tests substitute `tee` for `wl-copy`.
- `grep` for `wl-copy`/`wl-paste` in tests finds only fake-runner `argv`
  assertions and error-message checks — no process spawn.
- The new tests call `_run_command` directly with `sh`; they never reach
  `inject()` with the default runner.

No test can write, or read, the user's real clipboard.

---

## 10. Mechanicals

Caches cleared before both commands.

```
$ rm -rf .mypy_cache __pycache__ && find . -name __pycache__ -type d -prune -exec rm -rf {} +
$ timeout 300 uv run pytest -q
877 passed in 7.39s
PYTEST_EXIT=0                      # process exits; no leak-thread hang

$ rm -rf .mypy_cache __pycache__ && find . -name __pycache__ -type d -prune -exec rm -rf {} +
$ timeout 300 uv run mypy src/ges2t tests
Success: no issues found in 51 source files
MYPY_EXIT=0
```

No test deleted, skipped or weakened: `git diff main...HEAD -- tests/` is
`83 insertions, 0 deletions` (no `-` lines), the file goes from 50 to 53
`def test_`, and the run reports `0 skipped / 0 xfailed`. The source diff is
`48 insertions, 10 deletions`, all inside `_run_command`/`_run_immediate`; a grep
of the diff for deferred-branch tokens (`delay_s is None`,
`_start_deferred_stdin`, `start_new_session`, `stdin=subprocess.PIPE`,
`stdout=subprocess.DEVNULL`) shows **no added/removed lines** — the deferred
path is genuinely unchanged.

Board: row 36 `in progress → in review`; ticket `**Status:** in review`; row 25
was already updated on `main` and is untouched. No other row changed.

---

## 11. My own mutations (each reverted, tree re-verified)

| # | Mutation to the fixed `_run_immediate` | Result | Caught by |
|---|---|---|---|
| M1 | `stderr=stderr_file` → `stderr=subprocess.DEVNULL` (the forbidden shortcut) | FAIL | `test_real_forking_child_does_not_block_the_immediate_path` (`assert b'no compositor' in b''`); my error-contract probe |
| M2 | `stdout=stdout_file` → `stdout=subprocess.DEVNULL` | FAIL | `test_real_immediate_runner_still_captures_stdout` (`b'' == b'PREVIOUS'`); my probe (`read_clipboard -> None`) |
| M3 | `timeout=CLIPBOARD_COMMAND_TIMEOUT_SECONDS` → `timeout=None` | FAIL (30.3 s) | `test_real_immediate_runner_still_bounds_a_wedged_child` (`DID NOT RAISE TimeoutExpired`) |
| M4 | `input=stdin_bytes` → `input=b""` | **PASS** (877 passed) | **nothing** — coverage gap, see verdict finding 2 |

After each mutation: `cp /tmp/t36-review/fixed_keep.py …`; `sha256sum -c` OK;
`git status --porcelain` empty. M4's escape is why I recommend a real-seam
stdin assertion.

---

## 12. What I could not verify

- **A real live-session run** (writes the clipboard, types into a focused
  window): forbidden here and user-owned. This is the gate ticket 25 still
  needs.
- **Real `wl-copy` behaviour on a live Wayland session.** I did not execute
  `wl-copy` with data. I verified its fork order, fd redirection (stdout/stdin
  to `/dev/null`, stderr kept) and stdin drain from upstream `v2.3.0` source,
  installed package `1:2.3.0-1`.
- **Real `wl-paste`/`ydotool` execution against a live session.** Their
  no-daemon/ no-fork behaviour was verified from upstream source
  (`wl-paste.c` `run_paste_command` waits; `ydotool` 1.0.4 client has no
  `fork`/`exec`/`daemon`/`setsid`), not measured live.
- **R5 against a real `wl-copy`**: reproduced with `tee`, as in `006`/`007`; not
  observed on a real blocked `wl-copy`.
- **The 0.4 s restore-vs-application-round-trip race and real thread
  exhaustion** remain unobservable without a live session.

---

## 13. Can the user re-run their manual clipboard test?

**Yes.** The blocking cause (the daemon's inherited stderr pipe) is removed, the
write still captures stdout/stderr, the paste chord still runs, the timeout still
bounds a genuine wedge, and no test can interfere with the user's clipboard. The
manual run remains theirs and is the last gate for ticket 25's DoD box; I could
not and did not perform it.

## 14. Non-blocking follow-ups

- Fix the `git show HEAD:…` command transcription in `001-comment.md` (use
  `main`/`2e9fb05^`).
- Add a real-seam assertion that a non-empty `stdin_bytes` reaches the child
  (`_run_command(("cat",), b"…", env)` round-trip), which M4 shows is untested.
- R5 is unchanged and still wants the deferred `proc.kill()` remedy.
