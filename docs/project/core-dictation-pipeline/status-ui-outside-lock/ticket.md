# Call StatusUI outside the state lock

- **Priority:** P1
- **Status:** done
- **Complexity:** XS
- **Epic:** ../epic.md

## Current state / context

`src/ges2t/service.py` (ticket 9, merged) holds `Service._lock` — a plain, non-reentrant
`threading.Lock` — while calling into the injected `StatusUI`. Ticket 9's reviewer found this
by experiment and reported it as non-blocking follow-up #1:

> **`status_ui` is called while the state lock is held.** `_set_state` invokes
> `status_ui.idle()/recording()/transcribing()/error()` inside `with self._lock`. A `StatusUI`
> that reads live state back from the service — **which is exactly what a tray (ADR 0013) that
> shows the current state might do** — deadlocks, because `Service._lock` is a plain
> `threading.Lock`. Reproduced on a throwaway copy:
> ```
> DEADLOCK: handle('toggle') did not return within 1.5s
> ```
> (probe: a `StatusUI.recording()` that calls `service.state`.)

This is latent, not hypothetical: ticket 17's tray is a `StatusUI` whose whole job is showing
the current state, and a tray that renders its initial label or a menu entry from `state` is the
most natural implementation in the world. A deadlock there would hang the socket loop, i.e. the
hotkey would stop responding entirely, with no error.

Relevant decisions: ADR 0015 (the service must never block its command loop; the service
outlives failures), ADR 0013 (the tray is the status surface), ADR 0014 (quality bar).

## Definition of done

- [x] `Service._set_state` (or whatever assigns the state) **collects the display action** while
      the lock is held but **invokes `status_ui` after the lock is released**, so no
      collaborator is ever called under the lock.
- [x] The same rule applies to every other collaborator call on the command path: audit
      `service.py` for **any** call into `status_ui`, `injector`, or a session made while
      `self._lock` is held, and move each one outside. Report what you found, even if the answer
      is "only `_set_state`".
- [x] A **regression test** proves the deadlock is gone: construct a `Service` with a
      `StatusUI` whose `recording()` (and each other transition) reads `service.state` back, and
      assert the command **returns promptly** — use a real bounded wait (e.g. run the command on
      a thread with a join timeout, or wrap it in a short timeout) so the test **fails loudly**
      rather than hanging the suite if the bug returns. Do not write a test that would hang.
- [x] The existing 73 state-machine/socket tests still pass unchanged, and the full transition
      table behaviour is identical — this is a locking fix, **not** a behaviour change. Report
      before/after test counts.
- [x] `uv run pytest` passes and **`uv run mypy src/ges2t tests`** exits 0 (ADR 0019 amended
      2026-10-09 to cover tests).
- [x] `001-comment.md` records: the exact before/after locking discipline, what calls were moved
      out of the lock, and the regression test that proves it.

## Notes

- Keep the change minimal. Do **not** restructure the service, introduce an event queue, or make
  the lock reentrant — a reentrant lock would paper over the bug rather than remove the
  "no collaborator under the lock" invariant, which is what actually protects us.
- Do not hold the lock across `finish()` or `audio.stop()` — ticket 9's review separately
  confirmed that discipline is already correct; do not regress it.
- The tray (ticket 17) depends on this ticket. Do not "fix" it by documenting that
  `StatusUI` must not call `state`; the interface does not promise that and the service should
  not require it.

## Related tickets

- depends on: ../service-state-machine-and-socket/ticket.md (the code being fixed)
- blocks: ../../live-dictation-ux/tray-status-ui/ticket.md
- related ADRs: ../../../adr/0015-single-service-process-architecture.md,
  ../../../adr/0013-tray-via-pystray-appindicator.md
