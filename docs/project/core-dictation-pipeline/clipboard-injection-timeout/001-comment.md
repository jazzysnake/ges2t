# 001 — Comment: the immediate runner must not wait on a forked child's pipes

**Ticket:** `clipboard-injection-timeout` (#36) · **Branch:** `feat/clipboard-injection-timeout` ·
**Worktree:** `.worktrees/clipboard-injection-timeout` · **Status:** in review

- **Fix commit:** `2e9fb05` — *ticket 36: stop blocking on a forked child's inherited pipes in the
  immediate runner*
- **Pre-fix blob:** `src/ges2t/injectors/clipboard.py` sha256
  `deef264efdf6bd370ddacd4944b4f2e1cc01d56fbebc8f349154b6f7f97f7bfb` (the reviewed `007` HEAD)
- **Fixed blob:** `src/ges2t/injectors/clipboard.py` sha256
  `9e0f4e9b35191622e09c5b970149924027068ec47e6f885e466b0a345f2bfed5`

## Files changed

- `src/ges2t/injectors/clipboard.py` (+48/−10) — new `_run_immediate` helper, `tempfile` import, the
  immediate branch of `_run_command` now delegates to it.
- `tests/test_clipboard_injector.py` (+83/−0) — three real-child tests plus a `_kill_pid_file`
  cleanup helper and a `signal` import. No fake was changed, no test weakened.

No other module was touched. Nothing here writes the user's real clipboard, runs `wl-copy`/`wl-paste`
with data, runs `ydotool`, or emits a keystroke: every probe and test uses harmless `sh`/`sleep`
stand-ins writing only under `tmp_path`/`/tmp`. The `wl-copy`/`wl-paste` fork behaviour below was
read from the installed man page and binary strings, not measured against a live session.

## 1. The defect and the fix

`_run_command`'s immediate path used `capture_output=True`, which creates stdout/stderr **pipes**.
`wl-copy` forks by default (the man page: *"By default, `wl-copy` forks and serves data requests in
the background"*; `-f, --foreground` *"overrides that behavior"*), and the forked child inherits
those pipes. `subprocess.run` reads to **EOF**, which never arrives while the daemon holds a write
end, so the call blocked for the full `CLIPBOARD_COMMAND_TIMEOUT_SECONDS` and raised
`ClipboardTimeoutError` **before** `_send_paste_chord` ran. The user's manual run is the signature:
the transcript reached the clipboard and *then* the injection reported failure.

The immediate branch now runs through a new `_run_immediate` helper that points stdout and stderr at
two `tempfile.TemporaryFile()` **regular files** instead of pipes, then reads those files after the
immediate process exits and rebuilds the `CompletedProcess`:

```python
    with (
        tempfile.TemporaryFile() as stdout_file,
        tempfile.TemporaryFile() as stderr_file,
    ):
        completed = subprocess.run(
            list(argv),
            input=stdin_bytes,
            env=dict(environment),
            stdout=stdout_file,
            stderr=stderr_file,
            check=False,
            timeout=CLIPBOARD_COMMAND_TIMEOUT_SECONDS,
        )
        stdout_file.seek(0)
        stderr_file.seek(0)
        return subprocess.CompletedProcess(
            args=list(argv),
            returncode=completed.returncode,
            stdout=stdout_file.read(),
            stderr=stderr_file.read(),
        )
```

Why this is the right trade-off, against the three constraints the ticket names:

- **It stops waiting on a pipe a forked child can hold.** A regular file has no reader-to-EOF step,
  so the bounded wait is only for the immediate process. The forked child may inherit the file
  descriptors and keep them open forever; that cannot block the parent. (Opening the file with
  `tempfile.TemporaryFile` also means it is unlinked immediately on Linux, so there is no on-disk
  leak to clean up.)
- **`stderr` is still captured**, and therefore still reaches
  `ClipboardInjectionError`/`ClipboardSpawnError`. `stdout` is captured too, because the *same*
  immediate path also serves `wl-paste`, whose output is the previous selection restored later.
  Redirecting only stderr and leaving stdout a pipe (or vice versa) would not have been safe: a
  `stderr=PIPE` still makes `communicate()` wait for EOF on the inherited pipe.
- **The timeout is unchanged** — the same `subprocess.run(..., timeout=...)` still runs, so a
  genuinely wedged compositor still raises `subprocess.TimeoutExpired` for `_run` to classify.

The alternatives were rejected deliberately: `wl-copy --foreground` makes the call block on purpose
(the opposite of the fix); `DEVNULL` discards stderr and breaks ticket 25's error contract; changing
the mechanism, the restore semantics, or `--paste-once` is out of scope by the ticket's own notes.

## 2. Measured before/after (harmless stand-in, no clipboard)

Pre-fix, through the real `_run_command`:

```
$ timeout 30 uv run python /tmp/probe_prefix.py
TimeoutExpired elapsed=5.01
EXIT=0
```

Post-fix, same stand-in `sh -c '( sleep 30 ) & echo MARKER; echo BOOM >&2; exit 7'`:

```
$ timeout 60 uv run python /tmp/probe_postfix.py
rc=7 stdout=b'MARKER\n' stderr=b'BOOM\n' elapsed=0.01
rc=0 stdout=b'PREVIOUS' stderr=b''          # wl-paste-style stdout capture still works
wedged still times out after 5.00           # a real hang is still bounded
EXIT=0
```

## 3. The new real-forking-child test, and its falsification

`tests/test_clipboard_injector.py` now drives the **real** `_run_command` with a **real forking
child** and no clipboard: `sleep 30 &` is backgrounded the way `wl-copy` daemonises, the foreground
`sh` writes a marker and a pid file under `tmp_path`, emits `no compositor` on stderr, and exits 7.
The test asserts the call returns well under the timeout (the child's inherited fds did not stall
it), that `returncode == 7`, and that the child's **stderr was captured**. `_kill_pid_file` reaps
the harmless background `sleep` in a `finally` so the suite leaks no process. Two smaller real-seam
tests pin that stdout is still captured (for `wl-paste`) and that a genuinely sleeping child still
raises `TimeoutExpired` (the bound survives); the latter monkeypatches the module constant to 0.2 s
so it does not add 5 s to the suite.

**Falsification** (overlay the pre-fix blob from `HEAD`, run, restore byte-identically):

```
$ git show HEAD:src/ges2t/injectors/clipboard.py > src/ges2t/injectors/clipboard.py
$ sha256sum src/ges2t/injectors/clipboard.py
deef264efdf6bd370ddacd4944b4f2e1cc01d56fbebc8f349154b6f7f97f7bfb  src/ges2t/injectors/clipboard.py
$ rm -rf .mypy_cache __pycache__ && uv run pytest -q -p no:cacheprovider \
    tests/test_clipboard_injector.py -k forking
...
    stderr_seq = [b'no compositor\n'],
>           raise TimeoutExpired(
E           subprocess.TimeoutExpired: Command '['sh', '-c', "sleep 30 & echo $! > .../forked-child.pid; printf 'wrote-and-forked' > .../forked-child-marker.txt; echo 'no compositor' >&2; exit 7"]' timed out after 5.0 seconds
FAILED tests/test_clipboard_injector.py::test_real_forking_child_does_not_block_the_immediate_path
1 failed, 90 deselected in 5.42s
PREFIX_EXIT=1

$ cp /tmp/clipboard_fixed.py src/ges2t/injectors/clipboard.py
$ sha256sum -c /tmp/fixed_sha.txt
src/ges2t/injectors/clipboard.py: OK
$ uv run pytest -q -p no:cacheprovider tests/test_clipboard_injector.py \
    -k "forking or captures_stdout or bounds_a_wedged"
...                                                                      [100%]
3 passed, 88 deselected in 0.41s
POSTFIX_EXIT=0
```

The traceback is the strongest possible evidence: `stderr_seq = [b'no compositor\n']` shows the
child had already written its stderr, yet the pre-fix call still blocked to the timeout. The test
**fails on the pre-fix blob and passes after the fix**, and the restore was verified byte-identical
by sha256 before the passing run.

## 4. Hazard audit of the other real-child paths

| Path | Can it fork? | How it is handled |
|---|---|---|
| `wl-copy` (immediate payload write) | **Yes**, by default (documented; `-f` opts out) | The defect. Now runs through `_run_immediate` with regular-file stdout/stderr, so the daemon's inherited fds cannot stall the wait. |
| `wl-paste` (immediate read) | **No** in normal mode. It connects, reads, prints and exits. Only `-w/--watch` spawns a command (*"Run a command each time the selection changes"*), and ges2t never passes `--watch` (arguments are just `--no-newline`). | Also runs through `_run_immediate`, so even an unexpected fork is now safe; stdout is still captured for the previous selection. |
| Deferred restore child (`sh -c 'sleep …; exec wl-copy'`) | **Yes** — it is the detached daemon itself, and the `exec`'d `wl-copy` forks again. | Spawned with `Popen(..., stdout=DEVNULL, stderr=DEVNULL, stdin=PIPE, start_new_session=True)` and **not waited on**; the grandchild's inherited fds are `/dev/null`, not pipes, so there is no read-to-EOF to block. `_run_command` returns a synthetic `CompletedProcess` immediately. |
| `ydotool key` (paste chord) | **No** — the client connects to the `ydotoold` socket and exits (man page: *"The `ydotoold` daemon must be running"*; binary strings are all socket connect errors). | Runs through `_run_immediate`; stderr is captured, so a non-zero `ydotool` still produces the typed `ClipboardInjectionError` with its message. |

**Conclusion:** the only real forking binary on the fatal path is `wl-copy`, and it is exactly the
one this fix covers. `wl-paste` and `ydotool key` are ordinary clients; the deferred restore child
deliberately detaches with `DEVNULL`, so it has no pipe-EOF hazard. The new file redirect makes even
an unexpected fork on any immediate path harmless.

## 5. Does this remove residual R5's cause? No

R5 (`clipboard-paste-injector/007-comment.md`) is the leaked restore child on the failed
`Thread.start` path: the restore `Popen` is spawned with `stdin=PIPE`, `_start_deferred_stdin`'s
thread never starts, and the dropped `Popen` is retained in `subprocess._active`, keeping the
**stdin write end** open. The child therefore blocks on `read()` for the life of the service.

That is only the **same shape** as this defect in the abstract — a live write end of a pipe keeps the
other end from seeing EOF — but it is a different branch, a different pipe (stdin, not stdout/stderr),
and a different direction (the *child* is blocked, not the parent). `_run_immediate` changes only the
immediate branch's stdout/stderr redirection; the deferred branch's `stdin=PIPE` and the
`subprocess._active` retention are untouched. **The fix does not remove R5's cause.** The cheap
future fix remains the one `007` names: `proc.kill()` (not `stdin.close()`) on the failed-thread-start
path.

## 6. Verification (real output)

Caches cleared before both commands, per the ticket's warning that a reverted mutation can look
fixed:

```
$ rm -rf .mypy_cache __pycache__ && find . -name __pycache__ -type d -prune -exec rm -rf {} +
$ timeout 300 uv run pytest -q
877 passed in 7.43s
PYTEST_EXIT=0            # the process exits; no leaked-thread hang after the pass

$ rm -rf .mypy_cache __pycache__ && find . -name __pycache__ -type d -prune -exec rm -rf {} +
$ timeout 300 uv run mypy src/ges2t tests
Success: no issues found in 51 source files
MYPY_EXIT=0
```

No `sleep` stand-in was left running after the suite (`pgrep -af "sleep 30"` finds only the pgrep
shell itself). The board row 36 was moved `in progress → in review`; the ticket's `**Status:**` line
was set to `in review`; no other row was touched.

## 7. What I could not verify

- **A real live-session run.** Forbidden here and user-owned: it writes the clipboard and types into
  the focused window. **The user must re-run ticket 25's manual test against this branch**; ticket
  25's DoD box stays unticked until that passes.
- **Real `wl-copy` forking on this machine.** Confirmed from the installed man page and binary
  strings, and reproduced with a `sh` stand-in of the same shape; I did not run `wl-copy` with data.
- **Whether `wl-paste` ever forks in a mode ges2t uses.** The man page and strings say no for the
  default read; only `--watch` spawns a command, which ges2t never passes. Not measured live.
- **R5 with a real `wl-copy`** (unchanged from `006`/`007`): the `_active` retention was measured
  there with `tee`, not observed against a real blocked `wl-copy`.
- **The 0.4 s restore race** and **real thread exhaustion** remain unobservable without a live
  session and a real thread-exhausted process.
