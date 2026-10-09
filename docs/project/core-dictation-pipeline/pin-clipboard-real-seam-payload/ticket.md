# Pin the real-seam payload delivery (clipboard immediate runner)

- **Priority:** P1 (test gap on a path that has now broken twice)
- **Status:** in review
- **Complexity:** XS
- **Epic:** ../../core-dictation-pipeline/epic.md

## Current state / context

Ticket 36's review found a live coverage gap by mutation:

> mutating `input=stdin_bytes` → `input=b""` leaves the **entire 877-test suite green**.

**No test drives a non-empty payload through the real immediate runner.** Every clipboard test injects
`FakeClipboardRunner`, so the real `_run_command` receives no payload at all — the fakes answer
before the bytes matter.

This matters because the same blind spot has already cost this project twice:

- **Ticket 25's D1** — the deferred restore never received the previous bytes, and the test double
  recorded `stdin=previous`, so every restore test passed while the real seam did the opposite.
- **Ticket 36** — `wl-copy` forks and the immediate path blocked on its inherited pipes; the whole
  suite was green because no test ran a real child.

Both were found by the user's manual run, not by a test. The mechanism is the same each time: **the
fake models a simpler contract than the real seam.**

The established pattern for this is tickets 26 and 27 ("pin the falsified test guarantees"). This is
the same kind of ticket.

## Definition of done

- [x] **A real-seam test that asserts a non-empty payload reaches the child's stdin** through the
      **real** `_run_command` immediate path — no fake runner, and **no clipboard**: use a harmless
      stand-in such as `cat` writing to a path under `tmp_path` and assert the bytes round-trip
      exactly (including a multi-byte payload).
- [x] **Assert the stdout round-trip too** — stdout is captured from a regular file now, and it
      carries `wl-paste`'s previous selection, so the capture path must be pinned, not just the input.
- [x] **Falsify it**: mutate `input=stdin_bytes` → `input=b""` and show the new test **fails**; revert
      byte-identically (sha256) and show it passes. A test that passes both before and after tests
      nothing — say so rather than keeping it.
- [x] Confirm the new test **does not** touch the real clipboard (no `wl-copy`/`wl-paste` with data)
      and does not leak child processes.
- [x] **Fix a transcription error in ticket 36's artifact**: `001-comment.md` quotes
      `git show HEAD:src/ges2t/injectors/clipboard.py` as producing the pre-fix blob hash. `HEAD`
      contains the fix; the pre-fix blob comes from `main` (`git show main:…`). The **recorded hash is
      correct** — only the quoted command is wrong. Correct it in place.
- [x] `uv run pytest`, `uv run mypy src/ges2t tests` green.

## Notes

- **Test-only ticket.** No behaviour change, and no change to `src/ges2t/injectors/clipboard.py` —
  if you find yourself editing that file, stop and say why instead.
- **Never touch the user's real clipboard** in the new test. `cat`/`tee` under `tmp_path` is the
  whole point: it exercises the real seam without the real binary.
- Do not weaken or reorder existing tests to make room for this one.
- The generic "a future daemonising binary holds stdin open" hazard recorded in ticket 36's
  `002-review.md` §5 is **out of scope** here — it is not reachable with the real binaries. Do not
  attempt to solve it in this ticket.

## Related tickets

- corrects/extends: ../../core-dictation-pipeline/clipboard-injection-timeout/ticket.md (ticket 36),
  ../../core-dictation-pipeline/clipboard-paste-injector/ticket.md (ticket 25)
- pattern precedent: ../../core-dictation-pipeline/pin-cli-test-guarantees/ticket.md (26),
  ../../core-dictation-pipeline/pin-layout-test-guarantees/ticket.md (27)
- related ADRs: ../../../adr/0014-testing-and-quality-bar.md
