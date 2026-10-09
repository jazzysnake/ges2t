# 003 — Fix comment: D1/D2/D3 (re-review hand-back)

- **Ticket:** [ticket.md](ticket.md) (clipboard-paste-injector, #25)
- **Branch:** `feat/clipboard-paste-injector`
- **Worktree:** `.worktrees/clipboard-paste-injector`
- **Status:** in review (re-submitted for review)
- **Fix commit:** `4ca89db` — *ticket 25: fix D1 restore stdin, D2 evdev letter table, D3 read timeout*
- **Rejected HEAD:** `2b5fb26` (`002-review.md`)
- **Docs commit:** the commit that adds this file (`003-comment.md`)
- **Base:** `00adf75` (merge of ticket 15). No ADR was edited.

This comment answers the three blocking defects in
[`002-review.md`](002-review.md). It does **not** re-describe the module; see
[`001-comment.md`](001-comment.md) for the design. Every claim below was produced
by a command that is quoted with its real output.

Nothing here (or in the suite) writes the user's real clipboard, runs
`wl-copy`/`wl-paste`/`ydotool`, or emits a keystroke. The only real child process
spawned is the harmless `tee` under `pytest`'s `tmp_path`, exactly as the review
itself did; all clipboard invocations still go through the injected
`ClipboardRunner` seam.

## Files changed by this fix

| File | Change |
|---|---|
| `src/ges2t/injectors/clipboard.py` | D1: deferred branch now feeds the child's stdin (threaded writer). D2: real evdev letter table replaces the arithmetic fallback. D3: `wl-paste` timeout is tolerated like a missing binary. |
| `tests/test_clipboard_injector.py` | +30 tests: 2 for the real deferred seam (D1), 26 letter-block params + 1 destructive-chord case (D2), 1 read-timeout case (D3). |

Nothing else changed. In particular
`git diff main...HEAD -- src/ges2t/injectors/ydotool.py` is still empty.

---

## D1 (CRITICAL) — the deferred restore now actually receives the previous selection

### Why the old code failed

`_run_command`'s deferred branch started `sh -c 'sleep …; exec "$@"'` with
`stdin=subprocess.PIPE`, then **discarded the `Popen` object** and assigned
`stdin_bytes = b""` to a local that was never used. Nothing wrote the pipe; when
the discarded `Popen` was collected its `stdin` writer closed, the sleeping child
then `exec`ed `wl-copy`, read EOF, and copied an **empty** selection. The fake
runner recorded `stdin=previous`, so every restore test passed against a contract
the real seam did not implement.

### The fix (`clipboard.py:321-402`)

The `Popen` handle is retained and `stdin_bytes` is written to its pipe and
closed. Because the child sleeps `restore_delay_s` before it reads, the write is
done on a **daemon thread** so a previous selection larger than the pipe buffer
(64 KiB) cannot block the service's command loop — the ticket's explicit
"never block the service's command loop" requirement. The thread holds the
`Popen` reference, so the pipe write-end stays open until the bytes are in it.

```python
    proc = subprocess.Popen(
        ["sh", "-c", script, "sh", *argv],
        stdin=subprocess.PIPE,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    ...
    _start_deferred_stdin(proc, stdin_bytes)   # D1: write the pipe, then close it
    return subprocess.CompletedProcess(args=list(argv), returncode=0, stdout=b"", stderr=b"")


def _start_deferred_stdin(proc: subprocess.Popen[bytes], payload: bytes) -> None:
    threading.Thread(
        target=_write_stdin_and_close, args=(proc, payload), daemon=True
    ).start()


def _write_stdin_and_close(proc: subprocess.Popen[bytes], payload: bytes) -> None:
    stream = proc.stdin
    if stream is None:  # pragma: no cover - deferred runs always use PIPE
        return
    try:
        stream.write(payload)
        stream.flush()
    except (BrokenPipeError, OSError):
        pass          # a lost restore write is best effort, never an error
    finally:
        try:
            stream.close()
        except OSError:  # pragma: no cover
            pass
```

### The load-bearing test: the REAL seam, not the fake

`tests/test_clipboard_injector.py:478` runs the real `_run_command` deferred
branch with `tee` writing only under `tmp_path`; `...:498` drives the same real
deferred branch **through `ClipboardPasteInjector`** with a thin delegating
runner, so the injector's `previous` bytes traverse the production path:

```python
completed = _run_command(("tee", str(target)), payload, dict(os.environ), delay_s=0.05)
...
assert target.read_bytes() == payload
```

### Falsification — the new tests FAIL against the old seam

Mutation: delete the `_start_deferred_stdin(proc, stdin_bytes)` call (this is
exactly the old "drop the handle" behaviour). Output:

```
D1 mutation applied: deferred stdin feed removed
=================================== FAILURES ===================================
...
def test_restore_bytes_reach_the_real_deferred_child(tmp_path: Path) -> None:
    ...
    injector.inject("hi")
    _wait_for_bytes(target, b"previous")
    assert target.exists()
>   assert target.read_bytes() == b"previous"
E   AssertionError: assert b'' == b'previous'
tests/test_clipboard_injector.py:510: AssertionError
=========================== short test summary info ============================
FAILED tests/test_clipboard_injector.py::test_real_deferred_runner_delivers_stdin_bytes
FAILED tests/test_clipboard_injector.py::test_restore_bytes_reach_the_real_deferred_child
2 failed in 10.30s
=== restoring ===
..                                                                       [100%]
2 passed in 0.63s
```

The `b'' == b'previous'` assertion is the defect itself reproduced against the
real seam: the child read EOF. After the fix the same two tests pass.

---

## D2 (HIGH) — the real evdev letter block replaces the arithmetic fallback

### Why the old code failed

`_resolve_code` used `30 + (ord(upper) - ord("A"))`, assuming an alphabetically
contiguous letter block. The evdev block is `QWERTYUIOP` / `ASDFGHJKL` /
`ZXCVBNM` (16..25 / 30..38 / 44..50), so `ctrl+q` silently became `ctrl+c` and
`ctrl+r` silently became `ctrl+v` — a config typo could emit a destructive chord.

### The fix (`clipboard.py:152-180`, `483-506`)

A complete, literal `_LETTER_KEY_CODES` mapping is now the single source of truth.
The five letters that used to be hardcoded in `_NAMED_KEY_CODES` (`a, z, x, c, v`)
were removed from it, so the table is genuinely exercised for *all* 26 letters.
`_resolve_code` looks the segment up in the table; a letter absent from it is
rejected with the existing loud `ClipboardKeyChordError`. Digits keep the
genuinely contiguous `KEY_1`..`KEY_0` arithmetic (codes 2..11).

### Falsification — the whole-block test FAILS against the old fallback

Mutation: restore `30 + (ord(upper) - ord("A"))`. Output:

```
D2 mutation applied: arithmetic letter fallback restored
>       assert KeyChord.parse("ctrl+q").keycodes == (29, 16)
E       assert (29, 46) == (29, 16)
tests/test_clipboard_injector.py:720: AssertionError
=========================== short test summary info ============================
FAILED ...test_every_letter_resolves_to_its_real_evdev_code[b-48]
... (25 letter params fail; only `a` passes) ...
FAILED ...test_every_letter_resolves_to_its_real_evdev_code[z-44]
FAILED ...test_no_letter_silently_resolves_to_a_different_key
26 failed, 1 passed in 0.76s
=== restoring ===
...........................                                              [100%]
27 passed in 0.18s
```

The 26 failures are the 25 non-`a` letters plus the destructive-chord case; `a`
is the one letter the arithmetic got right. The test is
`@pytest.mark.parametrize`d over a hand-written `_EVDEV_LETTER_CODES` table that
is independent of the module under test, so it cannot mirror a mutated
implementation.

---

## D3 (MEDIUM) — a `wl-paste` timeout is tolerated, not left untyped

### Decision and justification

`_read_clipboard` now catches `subprocess.TimeoutExpired` and returns `None`,
which is **the same "nothing restorable" outcome as a missing `wl-paste` or a
non-zero read**. Rationale: the read exists only to enable the courtesy restore;
a wedged compositor that makes the *old* selection unreadable must not prevent
the payload write and paste that still follow. The payload write and the chord
keep the typed-timeout behaviour via `_run` (`ClipboardTimeoutError`), so the
fatal path is unchanged. The comment in the code records this reasoning
(`clipboard.py:597-604`).

### Falsification — the test FAILS with the raw timeout

Mutation: remove the `except subprocess.TimeoutExpired: return None` arm. Output:

```
D3 mutation applied: read-timeout tolerance removed
        if error is not None:
>           raise error
E           subprocess.TimeoutExpired: Command 'wl-paste' timed out after 5.0 seconds
tests/test_clipboard_injector.py:121: TimeoutExpired
=========================== short test summary info ============================
FAILED tests/test_clipboard_injector.py::test_wl_paste_timeout_is_tolerated_like_a_missing_binary
1 failed in 0.24s
=== restoring ===
.                                                                        [100%]
1 passed in 0.15s
```

This is the reviewer's exact probe result: the raw `TimeoutExpired` escaped,
untouched by the typed error contract.

---

## Verification (real output, caches cleared)

```
$ rm -rf .mypy_cache __pycache__ && uv run pytest -q
686 passed in 4.53s

$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 41 source files
```

Test-count delta: the rejected HEAD had **656 passed**; this fix adds **30**
clipboard tests (`656 + 30 = 686`): 26 letter parameters, 1 destructive-chord
case, 1 read-timeout case, 2 real-deferred-seam tests. The clipboard file now
collects **87** tests (was 57). `.mypy_cache` and `__pycache__` are cleared
before both commands so a stale cache cannot mask a reverted mutation. The tree
is clean at `4ca89db` after every mutation above (`git status --short` is empty).

`git diff main...HEAD -- src/ges2t/injectors/ydotool.py` is empty: the
`ydotool` path is byte-identical.

---

## Corrections to `001-comment.md`

The review was right that `001-comment.md` overstated the production restore:

- The sequence diagram line `4. wl-copy < previous on stdin (delay 0.4s)` was
  **not true of the real path before this fix** (D1). It is true now.
- The "a single letter or digit" claim was false for 21 of 26 letters before this
  fix (D2). It is true now via the complete evdev table.
- `001-comment.md` said a `wl-paste` read problem is tolerated; before this fix a
  **timeout** escaped untyped (D3). It is tolerated now.
- (Reviewer's minor note, acknowledged, not changed: no DoD box in `ticket.md` is
  ticked, including the user-owned manual step.)

`001-comment.md` is left as the historical record of the rejected revision.

## What I could NOT verify

- **Real `wl-copy`/`wl-paste`/`ydotool`/`ydotoold` behaviour and the manual
  end-to-end run.** Forbidden here (it writes the user's clipboard and types into
  the focused window) and user-owned. The manual script in `001-comment.md`
  remains the gate; its `restored: True` line is now reachable in production,
  but only the user can confirm it on a live session.
- **The real restore's timing/race** (0.4 s vs the target application's selection
  round trip). Unobservable without a live Wayland session.
- **Whether a previous selection larger than the 64 KiB pipe buffer restores
  fully in production.** The threaded writer is designed so it cannot block the
  command loop, but the write's completion for a multi-megabyte selection is not
  something I could observe without a real clipboard.
- **`tee`-based tests depend on coreutils and a POSIX `sh`.** Both are present in
  this environment and in the project's Linux-only target; the real deferred
  branch already required `sh` before this fix.
- The `main` baseline test count was not re-run; `656 + 30 = 686` is arithmetic
  over the rejected HEAD's reported count plus the new tests collected here.

## Anything a reviewer must know

- The D1 fix writes the restore payload on a **daemon** thread. If the process
  exits within the 0.4 s delay window, a daemon thread can be killed before the
  write lands; that is consistent with "restore is best effort" and is the same
  window that always existed for the detached child itself.
- `001-comment.md`'s manual checklist is unchanged and still unticked; the fix
  makes D1's step 4 and step 6 observe the restored selection rather than an empty
  one.
- The ticket status and `docs/project/README.md` row 25 already read
  `in review` on this branch; no row other than #25 was touched, and no
  renumbering was done. The folder now holds `001-comment.md`, `002-review.md`,
  `003-comment.md`, and `ticket.md`.
