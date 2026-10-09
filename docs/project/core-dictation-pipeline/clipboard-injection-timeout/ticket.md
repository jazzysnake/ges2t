# Clipboard injection times out because `wl-copy` forks

- **Priority:** P1 (corrective for ticket 25, whose manual gate failed)
- **Status:** to do
- **Complexity:** S
- **Epic:** ../../core-dictation-pipeline/epic.md

## The defect

Found by the user's manual DoD run, **not** by any test. Symptom:

```
Injection failed: wl-copy did not finish within 5s, so ges2t gave up on the clipboard
injection. A wedged Wayland compositor is the usual cause; check that the session is
responsive.
```

**The diagnostic detail that identifies it: the transcript *did* reach the clipboard.** The write
succeeded and was then reported as a failure.

**Root cause.** `wl-copy` **forks by default** (`-f, --foreground` is documented as "Stay in the
foreground *instead of forking*"). The forked child stays alive to serve the selection and
**inherits the stdout/stderr pipes** created by `_run_command`'s immediate path:

```python
return subprocess.run(
    list(argv),
    input=stdin_bytes,
    env=environment,
    capture_output=True,          # <- creates pipes the forked child inherits
    check=False,
    timeout=CLIPBOARD_COMMAND_TIMEOUT_SECONDS,
)
```

`subprocess.run` reads to **EOF** on those pipes, and EOF cannot arrive while the daemon holds a
write end. So every real clipboard write blocks for the full 5 s and raises
`ClipboardTimeoutError` — before `_send_paste_chord` ever runs. **The paste never happens.**

Measured, with a harmless forking stand-in (`sh -c '( sleep 20 ) & echo wrote-and-forked'`):

```
capture_output=True  -> TimeoutExpired after 3.00s
DEVNULL redirect     -> returned in 0.01s, rc=0
```

## Why the suite was green

Every clipboard test injects a `FakeClipboardRunner`, so the real `_run_command` immediate path is
**never exercised by a real child process**. The fakes model a simpler contract than the real
binary: they return when the call returns. No test could have caught this, and the reviews honestly
recorded "real `wl-copy` behaviour — could not verify, user-owned". The user's run is what found it.

## Definition of done

- [ ] The immediate path in `_run_command` **must not block on a forked child's inherited pipes**,
      for `wl-copy` (which forks) or any other daemonising binary.
- [ ] **`stderr` must still be captured** and must still reach the typed error messages — the
      ticket-25 error contract ("non-zero exits including the children's stderr") must survive.
      Losing stderr to `DEVNULL` to dodge the bug is **not** an acceptable fix.
- [ ] The **timeout must remain** for a genuinely wedged compositor — this fix must not remove the
      bound, only stop it firing on a healthy forking child.
- [ ] **A test that uses a real forking child process** against the **real** `_run_command`, with no
      clipboard involved — e.g. `sh -c '( sleep <n> ) & echo <marker>'` writing to a temp path. This
      is the test that must exist so this class of bug cannot recur; a fake runner cannot cover it.
      It must **fail against the current code** (changing the test to just pass would defeat the
      point).
- [ ] Check the **other** real-child paths for the same hazard and report what you find:
      `wl-paste` (does it fork?), the deferred restore child, and `ydotool key`. Say explicitly
      which ones can fork and how each is handled.
- [ ] `uv run pytest`, `uv run mypy src/ges2t tests` green.

## Notes

- **Do not** "fix" this with `wl-copy --foreground`: that makes it block deliberately, which is the
  opposite of what is wanted.
- **Do not** reach for `--paste-once` or any other behaviour change; the mechanism and the restore
  semantics are ticket 25's and are not in question. The only defect is that we wait on a pipe we
  should not be waiting on.
- The **user must re-run the manual test** afterwards (it writes the clipboard and types into a
  focused window, so it is theirs). Ticket 25's DoD box stays unticked until that run passes.
- Related residual worth a glance while you are in this file: R5 (`007-comment.md`) — the leaked
  blocked restore child — has the same underlying shape (an inherited pipe held by a forked child).
  You are not required to fix R5, but if this fix removes its cause, say so.

## Related tickets

- corrects: ../../core-dictation-pipeline/clipboard-paste-injector/ticket.md (ticket 25, merged)
- related ADRs: ../../../adr/0003-text-injection-via-ydotool.md (amended),
  ../../../adr/0010-transcriber-interface-design.md,
  ../../../adr/0019-code-style-typing-and-interfaces.md
