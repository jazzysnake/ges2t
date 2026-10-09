# 001 — Comment: pinning the real-seam immediate payload delivery

**Ticket:** `pin-clipboard-real-seam-payload` (#37) · **Branch:** `feat/pin-clipboard-real-seam-payload` ·
**Worktree:** `.worktrees/pin-clipboard-real-seam-payload` · **Status:** in review

**Test-only ticket. No source behaviour change.**

- **Test commit:** `93ed56a` — *ticket 37: pin real-seam immediate stdin and stdout payload delivery*
- **Correction commit:** `330822f` — *ticket 37: correct the pre-fix blob command quoted in ticket 36's
  `001-comment.md`*
- **Base:** `main` = `97604eb` (*docs: ticket 37 pins the real-seam payload delivery*)
- **Source untouched:** `git diff --stat main..HEAD -- src/` is empty; the tests diff is
  `62 insertions(+), 0 deletions(-)`.

## 1. The gap this closes

Ticket 36's review found by mutation (its `002-review.md` §11, M4) that changing
`input=stdin_bytes` → `input=b""` in `_run_immediate` leaves **the entire 877-test suite green**.
Every clipboard test injects `FakeClipboardRunner`, so the fakes answer before the bytes matter and
the **real** `_run_command` immediate path never sees a non-empty payload. That is the same blind
spot that hid ticket 25's D1 and ticket 36 itself — the fake models a simpler contract than the real
seam, so only a real child can falsify it. This is the `pin-*-test-guarantees` pattern of tickets 26
and 27.

## 2. The new tests

Both live in `tests/test_clipboard_injector.py`, in the real-immediate-runner section, and drive the
**real** `_run_command` (`delay_s` omitted, so `_run_immediate`) with **no fake runner and no
clipboard**. The only binaries spawned are `sh` and `cat`; nothing writes the user's real clipboard
and no keystroke is emitted.

1. **`test_real_immediate_runner_delivers_a_non_empty_payload_to_stdin`** — `sh -c 'cat > "$1"'`
   redirects the child's stdin to a file under `tmp_path`. The payload
   `"naïve café — 日本語 🎤\n".encode()` is non-empty and multi-byte UTF-8, and the file is compared
   **byte-for-byte** with the payload. This is the assertion the mutation escapes.
2. **`test_real_immediate_runner_captures_a_non_empty_stdout_payload`** — a real `cat` prints a
   multi-byte file under `tmp_path` (a stand-in for `wl-paste`'s previous selection) and the runner
   must return `completed.stdout` exactly, with `completed.stderr == b""`. This pins ticket 36's new
   regular-file stdout capture path, which the same immediate route depends on for the deferred
   restore.

The payload lengths keep the tests honest: both assert `len(...) > 0` and
`any(byte > 0x7F for byte in ...)`, so a text-mode or re-encoding defect cannot slip through and the
"non-empty" requirement is explicit in the test itself.

`_run_command(("cat", str(source)), ...)` and the `sh` redirect both exit immediately, so the tests
leave no child processes; unlike the ticket 36 forking-child test there is nothing to reap.

## 3. Falsification (real output)

### 3.1 The M4 mutation on stdin — the new test fails

Fixed blob before the mutation:

```
$ sha256sum src/ges2t/injectors/clipboard.py
9e0f4e9b35191622e09c5b970149924027068ec47e6f885e466b0a345f2bfed5  src/ges2t/injectors/clipboard.py
```

Mutate `input=stdin_bytes` → `input=b""` (exactly one occurrence in the file):

```
$ perl -pi -e 's/^            input=stdin_bytes,$/            input=b"",/' src/ges2t/injectors/clipboard.py
$ grep -n "input=" src/ges2t/injectors/clipboard.py
420:            input=b"",
$ sha256sum src/ges2t/injectors/clipboard.py
cb971d8df4e52e0e773b16dc94a05a362721c9fedb431144bb84c5ae48c00e76  src/ges2t/injectors/clipboard.py
$ rm -rf .mypy_cache __pycache__ && timeout 120 uv run pytest -q -p no:cacheprovider \
    tests/test_clipboard_injector.py -k "delivers_a_non_empty_payload_to_stdin"
F                                                                        [100%]
...
>       assert target.read_bytes() == payload, "the child did not receive stdin bytes"
E       AssertionError: the child did not receive stdin bytes
E       assert b'' == b'na\xc3\xafv...x9f\x8e\xa4\n'
tests/test_clipboard_injector.py:688: AssertionError
1 failed, 92 deselected in 0.29s
MUTANT_EXIT=1
```

Run the **whole** suite on the mutant to prove no other test catches it (the point of the ticket):

```
$ timeout 300 uv run pytest -q -p no:cacheprovider
1 failed, 878 passed in 7.54s
FULL_MUTANT_EXIT=1
```

Exactly one failure — the new test — and the other 878 tests are green. The new assertion is the
only thing standing between the mutation and the suite.

Revert **byte-identically** and re-run:

```
$ cp /tmp/t37/clipboard_fixed.py src/ges2t/injectors/clipboard.py
$ sha256sum -c /tmp/t37/fixed.sha
src/ges2t/injectors/clipboard.py: OK
$ sha256sum src/ges2t/injectors/clipboard.py
9e0f4e9b35191622e09c5b970149924027068ec47e6f885e466b0a345f2bfed5  src/ges2t/injectors/clipboard.py
$ git status --porcelain          # empty
$ rm -rf .mypy_cache __pycache__ && timeout 120 uv run pytest -q -p no:cacheprovider \
    tests/test_clipboard_injector.py -k "non_empty_payload_to_stdin or non_empty_stdout_payload"
..                                                                       [100%]
2 passed, 91 deselected in 0.21s
REVERTED_EXIT=0
```

The sha256 is identical to the pre-mutation blob and `git status` is empty, so the revert is
byte-for-byte, not approximately. The test flips **FAIL → PASS** across two distinct verified blobs.

### 3.2 The stdout capture path is falsified too (extra)

The DoD only requires the stdin mutation, but the stdout test would be worthless if it passed for an
empty stream. Mutating `stdout=stdout_file` → `stdout=subprocess.DEVNULL`:

```
$ sha256sum src/ges2t/injectors/clipboard.py
495cdd109b2abc22205e863323eb39b71e0b0b28441fc02cd25c5fcd9e5aec7f  src/ges2t/injectors/clipboard.py
$ timeout 120 uv run pytest -q -p no:cacheprovider \
    tests/test_clipboard_injector.py -k "non_empty_stdout_payload"
>       assert completed.stdout == previous, "stdout bytes were not captured exactly"
E       AssertionError: stdout bytes were not captured exactly
E       assert b'' == b'old selecti...0\x9f\x8e\xa4'
1 failed, 92 deselected in 0.25s
STDOUT_MUTANT_EXIT=1

$ cp /tmp/t37/clipboard_fixed.py src/ges2t/injectors/clipboard.py
$ sha256sum -c /tmp/t37/fixed.sha
src/ges2t/injectors/clipboard.py: OK
$ git status --porcelain          # empty
```

So both new tests are load-bearing, and the source was restored byte-identically after both
mutations (verified by sha256 and an empty `git status`) before anything else was run.

## 4. The transcription error in ticket 36's `001-comment.md`

Ticket 36's `001-comment.md` §3 quoted the falsification command as

```
$ git show HEAD:src/ges2t/injectors/clipboard.py > src/ges2t/injectors/clipboard.py
```

and recorded the pre-fix blob as
`deef264efdf6bd370ddacd4944b4f2e1cc01d56fbebc8f349154b6f7f97f7bfb`. `HEAD` contains the fix, so
that command yields the **fixed** blob. Measured during this ticket:

```
$ git show main:src/ges2t/injectors/clipboard.py | sha256sum
9e0f4e9b35191622e09c5b970149924027068ec47e6f885e466b0a345f2bfed5  -

$ git show 2e9fb05^:src/ges2t/injectors/clipboard.py | sha256sum
deef264efdf6bd370ddacd4944b4f2e1cc01d56fbebc8f349154b6f7f97f7bfb  -

$ git show HEAD:src/ges2t/injectors/clipboard.py | sha256sum
9e0f4e9b35191622e09c5b970149924027068ec47e6f885e466b0a345f2bfed5  -
```

**The recorded hash was correct; only the quoted command was wrong.** I corrected the command in
place to `git show 2e9fb05^:src/ges2t/injectors/clipboard.py` and added a visible
`> **Correction (ticket 37 …)**` note rather than silently rewriting the artifact.

One nuance the ticket's own DoD got slightly stale: it says the pre-fix blob "comes from `main`",
which was true when the ticket was written (review-time `main` = `78141d2`, blob `deef264e…`). But
`main` has since **merged** ticket 36's fix as `ddeb278`, so today `git show main:…` yields the
**fixed** blob as well. The correction therefore points at the stable pre-fix commit `2e9fb05^`
(which is `78141d2`), not at the moving `main`. This is documented in the correction note itself.

## 5. Verification (real output)

Caches cleared before both commands, per ticket 36's warning that a reverted mutation can look fixed:

```
$ rm -rf .mypy_cache __pycache__ && find . -name __pycache__ -type d -prune -exec rm -rf {} +
$ timeout 300 uv run pytest -q
879 passed in 7.45s
PYTEST_EXIT=0            # 877 + the 2 new tests; the process exits, no leaked-thread hang

$ rm -rf .mypy_cache __pycache__ && find . -name __pycache__ -type d -prune -exec rm -rf {} +
$ timeout 300 uv run mypy src/ges2t tests
Success: no issues found in 51 source files
MYPY_EXIT=0
```

No test was deleted, weakened or reordered: `git diff --numstat main..HEAD -- tests/` is
`62  0  tests/test_clipboard_injector.py` (zero deletions), and the file goes from 53 to 55
`def test_` functions. No stray stand-in processes are left behind (`pgrep` finds none after the
suite).

## 6. Real-clipboard safety

- The new tests spawn only `sh` and `cat`; neither is `wl-copy` or `wl-paste`, and neither is given
  clipboard data. They never call `inject()` with the default runner.
- No `ydotool`, no keystroke, no microphone, no `ges2t run`.
- The source file `src/ges2t/injectors/clipboard.py` was not changed by this ticket. It was
  temporarily mutated **only** for the falsification in §3 and restored byte-identically
  (`sha256sum -c` OK, `git status` empty) immediately after each mutation.

## 7. What I could not verify

- **A real live Wayland clipboard write/paste.** Forbidden here and user-owned. These tests
  deliberately use `cat`/`sh` stand-ins; the real `wl-copy`/`wl-paste`/`ydotool` path still needs
  the user's manual run (ticket 25's gate), which this ticket does not replace.
- **That `wl-paste`'s stdout capture against a live compositor returns exactly these bytes.** The
  capture mechanism is pinned with a regular file, but the compositor is not involved.
- **The generic daemonising-child stdin-buffer hazard** recorded in ticket 36's `002-review.md` §5
  (a child that forks without reading a payload larger than the pipe buffer). The payloads here are
  tiny by design; that residual is explicitly out of scope and unreachable with the real binaries.

## 8. Board

Row 37 in `docs/project/README.md` was moved `in progress → in review`; the ticket's `**Status:**`
line was set to `in review`. No other row was touched.
