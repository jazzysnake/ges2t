# 004 — Re-review: clipboard-paste injector (#25)

- **Ticket:** [ticket.md](ticket.md)
- **Branch:** `feat/clipboard-paste-injector`
- **Worktree:** `.worktrees/clipboard-paste-injector`
- **Reviewed HEAD:** `d2d65f2` (`003-comment.md`); fix commit `4ca89db`
- **Rejected HEAD:** `2b5fb26` (`002-review.md`)
- **Diff basis:** `git diff main...feat/clipboard-paste-injector`

## Verdict: **PASS** — the three fixes are real; no regression found

The three blocking defects in [`002-review.md`](002-review.md) are genuinely fixed,
mechanically verified, and I reproduced each falsification myself against the
**actual rejected revision** `2b5fb26` (not only against a hand mutation): the new
tests fail there and pass on `d2d65f2`. The D1 fix moves the deferred write onto a
daemon thread, and I measured that this does **not** block the caller even for a
4 MiB payload (call returns in ~1.4 ms; a synchronous variant blocks ~413 ms). D2's
letter table matches the system evdev header for all 26 letters. D3 did not widen
into the fatal path.

I found **no blocking regression**. There are four **non-blocking** residual risks,
including one new, disclosed, measured limitation of the daemon-thread approach: if
the process exits inside the restore-delay window, a selection larger than the
64 KiB pipe buffer is restored **truncated to exactly 64 KiB** (see §2.2). This is
consistent with the ticket's explicit "restore is a courtesy, not a guarantee" and
does not affect the transcript, but it is worse than "empty" in the sense that a
truncated copy of the old selection survives. It is listed for the orchestrator,
not as a merge blocker.

The suite still cannot touch the user's real clipboard, and `ydotool.py` and its
tests are still byte-identical.

---

## 0. Real-clipboard safety (still holds)

```
$ grep -rn "ClipboardPasteInjector()" tests/ src/   -> (empty)
$ grep -rn "Popen" tests/                           -> only a docstring in test_clipboard_injector.py:481
$ grep -rn "\.inject(" tests/test_pipeline.py       -> (empty; build_injector is exercised, never inject())
```

All three `ClipboardPasteInjector(...)` constructions in the suite pass
`runner=` (`tests/test_clipboard_injector.py:178`, `:502`, `:781`). The only real
child process the suite spawns is the harmless `tee` under `pytest`'s `tmp_path`
via the D1 tests — exactly the technique `002-review.md` prescribed. No
`wl-copy`/`wl-paste`/`ydotool`/`ges2t run` was executed by the suite or by this
review. My probes used `tee`, `cat`, `true` and `sleep` writing only under
`/tmp/r25b`.

---

## 1. The three fixes: falsification against the real rejected revision

I ran the **current** `tests/test_clipboard_injector.py` against the **actual
pre-fix source**, not a hand-approximation:

```
$ cp src/ges2t/injectors/clipboard.py /tmp/r25b/keep_current.py
$ git show 2b5fb26:src/ges2t/injectors/clipboard.py > src/ges2t/injectors/clipboard.py
$ rm -rf .mypy_cache && find . -name __pycache__ ... -exec rm -rf {} +
$ timeout 180 uv run pytest -q -p no:cacheprovider tests/test_clipboard_injector.py
25 failed, 62 passed in 10.89s
PYTEST_EXIT=1
$ cp /tmp/r25b/keep_current.py src/ges2t/injectors/clipboard.py && sha256sum -c /tmp/r25b/head_sha.txt
src/ges2t/injectors/clipboard.py: OK
tests/test_clipboard_injector.py: OK
```

The 25 failures decompose exactly into the three defects:

- **2 D1** — `test_real_deferred_runner_delivers_stdin_bytes`,
  `test_restore_bytes_reach_the_real_deferred_child`
  (`AssertionError: assert b'' == b'PREVIOUS-SELECTION'` /
  `assert b'' == b'previous'`),
- **1 D3** — `test_wl_paste_timeout_is_tolerated_like_a_missing_binary`
  (`subprocess.TimeoutExpired: Command 'wl-paste' timed out after 5.0 seconds`),
- **22 D2** — 21 `test_every_letter_resolves_to_its_real_evdev_code[...]` params
  (`b,d,e,f,g,h,i,j,k,l,m,n,o,p,q,r,s,t,u,w,y`) plus
  `test_no_letter_silently_resolves_to_a_different_key`.

That is precisely the "21 of 26 letters" from `002-review.md`: the only five letter
params that pass against the old code are the five it had hardcoded in
`_NAMED_KEY_CODES` (`a,c,v,x,z`). The old spot-check
`test_letter_and_digit_keys_resolve_without_offset` (which only tests `a`, `z`,
`1`, `0`) **passes against the old code** — confirming the previous review's claim
that it was insufficient. On `d2d65f2` the same file is green:

```
$ timeout 300 uv run pytest -q
686 passed in 4.39s
FINAL_PYTEST_EXIT=0        # process exits; no leaked-thread hang
```

### D1 — deferred restore now delivers the previous selection

Code (`_run_command`, `clipboard.py:347-402`): the `Popen` is retained,
`_start_deferred_stdin(proc, stdin_bytes)` starts a daemon thread that writes
`stdin_bytes` and closes the pipe, then the synthetic `returncode=0` is returned.

Independent hand mutation (delete the feed — the old "drop the handle" behaviour):
`[D1 stdin feed removed] exit=1 ... 2 failed in 10.26s ... assert b'' == b'previous'`.
So the two new tests are load-bearing: they fail with the old seam and pass with
the fix.

### D2 — literal evdev letter table

Code: `_LETTER_KEY_CODES` added, the five letters (`v,c,x,a,z`) removed from
`_NAMED_KEY_CODES`; `_resolve_code` looks letters up in the table and returns
`None` (loud `ClipboardKeyChordError`) for an unknown one.

Independent hand mutation (restore `30 + (ord(upper) - ord("A"))`), running only
the D2 tests:
`[D2-narrow] 27 failed, 1 passed in 0.64s` — the single pass is `a-30`, the only
letter the arithmetic got right. Verified against the system header in §3.

### D3 — read timeout tolerated, fatal path preserved

Code: `_read_clipboard` gains `except subprocess.TimeoutExpired: return None`.
Hand mutation (remove the arm):
`[D3 read-timeout tolerance removed] 1 failed in 0.22s ... TimeoutExpired`.
With the arm present the same test passes (part of the 686 green).

---

## 2. Regression probes ("what did this change stop doing?")

### 2.1 Does the deferred write block the caller? — **No, measured**

Using the real `_run_command` with `tee` (no clipboard), payload 4 MiB (> the
64 KiB pipe buffer), delay 0.4 s:

```
PAYLOAD=18B  delay=0.4 {'rc': 0, 'call_seconds': 0.00180, 'full_seconds': 0.4098, 'final_size': 18, 'expected': 18}
PAYLOAD=4MiB delay=0.4 {'rc': 0, 'call_seconds': 0.00139, 'full_seconds': 0.4160, 'final_size': 4194304, 'expected': 4194304}
content-all-x: True
```

The call returns in ~1.4 ms even though the writer is blocked on a full pipe until
the child wakes; the full 4 MiB lands after the 0.4 s delay. To prove the thread is
load-bearing I inlined the write (`_start_deferred_stdin(...)` ->
`_write_stdin_and_close(...)`) and re-measured:

```
SYNC PAYLOAD=4MiB delay=0.4 {'rc': 0, 'call_seconds': 0.41298, 'full_seconds': 0.41303, 'final_size': 4194304}
```

so a synchronous write would have blocked the command loop for ~413 ms. The daemon
thread is doing real work, not decoration.

### 2.2 Process exit inside the restore-delay window — **partial restore, measured**

Probe: call `_run_command(("tee", path), payload, env, delay_s=0.4)` then
`os._exit(0)` immediately.

```
small (18 B) x5 -> size=18 content=PREVIOUS-SELECTION   (all five)
large (4 MiB) x3 -> size=65536 expected=4194304          (all three)
```

- A small selection survives even an immediate exit, because the write lands in the
  pipe buffer before the process dies and the bytes stay in the kernel buffer until
  the child reads them.
- A selection larger than 64 KiB is truncated to exactly the pipe buffer (65536
  bytes). The daemon writer is killed mid-write and the write end closes, so the
  child (already exec'd `wl-copy`) restores only what was buffered.

This is **new** relative to the pre-fix behaviour (which always restored empty), and
it is not measured or disclosed in `003-comment.md` (which says only "a daemon
thread can be killed before the write lands"). It is nevertheless consistent with
the ticket's "restoring the clipboard is a courtesy, not a guarantee" and does not
affect the transcript. Recorded as residual risk R1 (§6), not blocking.

### 2.3 Thread leak / late exception

```
threads at start: 1
after 5 large writes to a non-reading child (true): 1      # BrokenPipeError swallowed, no traceback
after 200 small deferred writes: 1                          # no leak
child that sleeps 30s without reading stdin: after 1s threads=2, process exited RC=0
```

The writer never raised after the caller returned `returncode=0`; the only leak is
the expected one when the child never reads (a daemon thread blocked on a full pipe
— daemon, so it cannot hang shutdown). Recorded as R3. A `Thread.start()` failure
(`RuntimeError`) is not caught by `_restore_clipboard`'s
`except (OSError, subprocess.SubprocessError)` — see R2.

### 2.4 Immediate (`delay_s is None`) path unchanged

The diff touches only the deferred branch; the immediate branch is still
`subprocess.run(list(argv), input=stdin_bytes, ..., timeout=CLIPBOARD_COMMAND_TIMEOUT_SECONDS)`.
Measured:

```
immediate _run_command(("cat",), b"hello-immediate") -> rc 0 stdout b'hello-immediate' seconds 0.0037
immediate _run_command(("sleep","10"), b"") -> TimeoutExpired after 5.01s (cmd=['sleep','10'])
configured timeout: 5.0
```

### 2.5 `_wait_for_bytes` helper

It is bounded (`deadline = time.monotonic() + 5.0`) but returns **silently** on
timeout. Both call sites assert immediately afterwards
(`assert target.exists()` then `assert target.read_bytes() == payload`), so a
timeout still fails the test: the old-code run above spent ~5 s per D1 test and
then failed on the `b'' == payload` assertion. It is not a "check that cannot
fail". A future call site that used the helper without a following assertion
would be, so this is worth remembering.

---

## 3. D2 verified against the real evdev table (not the implementation's own)

Independent source: `/usr/include/linux/input-event-codes.h`.

```
$ uv run python - <<'PY'
... parse system header KEY_[A-Z], compare to module and test table ...
PY
system letters: 26
impl letters: 26 keys==letters: True
impl == system: True
named: {'ctrl': 29, 'control': 29, 'shift': 42, 'alt': 56, 'super': 125, 'meta': 125, 'insert': 110}
named letters left: []
test letters: 26 keys==letters: True
test == system header: True | test == impl: True
digit 1: impl=2 expected=2 / digit 9: impl=10 / digit 0: impl=11
```

The header gives `Q..P = 16..25`, `A..L = 30..38`, `Z..M = 44..50`,
`LEFTCTRL=29`, `LEFTSHIFT=42`, `LEFTALT=56`, `LEFTMETA=125`, `INSERT=110` — the
module table, the test's `_EVDEV_LETTER_CODES`, and the header agree exactly. The
whole-letter-block test is `@pytest.mark.parametrize`d over all 26 letters (not a
spot check) and its table is declared independently in the test file. The five
removed named letters are all covered by the table; no other named key changed.

---

## 4. D3 semantics are defensible and did not widen

Probe with a synthetic runner (no clipboard) to exercise each path directly:

```
wl-copy payload timeout      : ClipboardTimeoutError: wl-copy did not finish within 5s ...
ydotool chord timeout        : ClipboardTimeoutError: ydotool did not finish within 5s ...
wl-copy payload OSError      : ClipboardSpawnError: wl-copy could not be started ...
wl-paste read timeout        : NO RAISE (returned normally)
wl-paste read OSError        : OSError: read boom            # see R4
```

The payload write and the chord still take the typed fatal path
(`_run` -> `ClipboardTimeoutError`/`ClipboardSpawnError`). Only the *read* timeout
is tolerated, which is the same "nothing restorable" outcome as a missing
`wl-paste` or a non-zero read, and is the right call: the read exists solely to
enable the courtesy restore, so a wedged compositor must not fail an injection
whose payload write and paste are still viable. This is the semantic the ticket
supports ("tolerating an empty/absent selection so text-type clipboards that cannot
be read do not break injection").

---

## 5. Nothing weakened; mechanicals

```
$ git diff main...HEAD -- tests/ | grep -E "^-" | grep -vE "^---"
-from ges2t.config import AudioConfig, Config, TranscriberConfig, UIConfig   # widened import only
$ git diff main...HEAD -- src/ges2t/injectors/ydotool.py       -> empty
$ git diff main...HEAD --stat -- tests/test_ydotool_emitter.py tests/test_layout.py -> empty
$ git diff main...HEAD --name-only | sort
docs/.../001-comment.md 002-review.md 003-comment.md ticket.md, docs/project/README.md,
src/ges2t/injectors/clipboard.py, src/ges2t/pipeline.py,
tests/test_clipboard_injector.py, tests/test_pipeline.py
```

One deleted line (an import widening); no assertion removed, no test deleted,
skipped or xfailed (`grep -nE "skip|xfail" tests/test_clipboard_injector.py` finds
nothing beyond `parametrize`; `test_pipeline.py` still collects 34).

```
$ rm -rf .mypy_cache __pycache__ && timeout 300 uv run pytest -q
686 passed in 4.39s                          exit 0 (no hang)
$ rm -rf .mypy_cache && timeout 300 uv run mypy src/ges2t tests
Success: no issues found in 41 source files   exit 0
$ uv run pytest tests/test_clipboard_injector.py --collect-only -q
87 tests collected
```

`656` (rejected HEAD) `+ 30 = 686` and `57 + 30 = 87` are consistent with
`003-comment.md`'s count claim (2 D1 + 26 letter params + 1 destructive + 1 D3 =
30 new tests). ADR 0019 remains satisfied (mypy clean, annotated, typed seam).
The tree is clean and `src/`/`tests/` are byte-identical to `HEAD` after all
mutations (`sha256sum -c` OK; `git diff HEAD --stat -- src tests` empty).

---

## 6. New / residual risks (all non-blocking)

- **R1 (new, measured): truncated restore for >64 KiB selections on process exit.**
  If the process exits inside the 0.4 s window, the restore child gets at most the
  64 KiB pipe buffer (§2.2). Best-effort and transcript-safe, but `003-comment.md`
  understates it. Suggested: document it, or if it matters, have the deferred
  branch fall back to a synchronous bounded write when the payload is small (the
  common case) — but that trades the non-blocking property, so documenting is the
  cheaper choice.
- **R2 (new, theoretical): an uncaught `RuntimeError` from `Thread.start()`.**
  `_restore_clipboard` catches `(OSError, subprocess.SubprocessError)`; a
  thread-creation failure (`RuntimeError: can't start new thread`) would escape and
  fail an injection that already pasted, contradicting "failing to restore must
  never fail the injection". One-line hardening: widen that `except` (or the
  `_start_deferred_stdin` call site) to swallow `RuntimeError`/`Exception` and
  return the synthetic failure status. Extremely rare; not blocking.
- **R3 (new, theoretical): one daemon thread remains blocked if the restore child
  never reads stdin** (e.g. a hung `wl-copy`). Daemon, so it cannot block process
  exit, and in production `wl-copy` reads stdin.
- **R4 (pre-existing, not introduced by this fix): a non-`FileNotFoundError`
  `OSError` on the read path (e.g. `PermissionError` from `wl-paste`) still escapes
  raw, unlike the now-tolerated timeout.** `_read_clipboard` calls `self._runner`
  directly and catches only `FileNotFoundError`/`TimeoutExpired`, not the generic
  `OSError` that `_run` would map to `ClipboardSpawnError`. Out of D3's scope and
  present before the fix; worth a follow-up if the read path's error typing is in
  scope for a later ticket.
- Minor: the tolerated read timeout produces no warning (the restore path does
  warn). Consistent with the other tolerated read outcomes (`returncode != 0`),
  so not a defect.

---

## 7. What I could not verify

- **Real `wl-copy`/`wl-paste`/`ydotool`/`ydotoold` behaviour and the manual
  end-to-end run.** Forbidden here (it writes the user's clipboard and types into
  the focused window) and user-owned; the `001-comment.md` checklist remains the
  gate. In particular its `restored: True` line is now reachable in production but
  only the user can confirm it live.
- **The real 0.4 s-vs-application-round-trip race** — unobservable without a live
  Wayland session.
- **Whether a real multi-megabyte `wl-copy` restores fully with the process alive** —
  my `tee` probe shows the writer delivers 4 MiB in ~0.016 s after the sleep, but
  that is not `wl-copy`.
- **The real `wl-copy` behaviour with a truncated/empty stdin** (R1's user-visible
  consequence: does it clear the selection or store a short one?). Both are
  "not really restored".
- The `main` baseline test count was not re-run; `686 = 656 + 30` is arithmetic
  over the rejected HEAD's reported number plus the collected new tests.

---

## 8. Recommended (non-blocking) follow-ups

1. Add the truncated-restore-on-exit caveat to `001-comment.md`/the config
   reference (R1).
2. Catch a thread-start failure in the deferred path so the restore stays
   best-effort even under thread exhaustion (R2).
3. Optionally type the read path's generic `OSError` (R4) in a later ticket.

The ticket's own user-owned manual step is still unticked, as required.
