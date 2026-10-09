# Tray failure diagnostics over the service socket

- **Priority:** P2
- **Status:** to do
- **Complexity:** S
- **Epic:** ../epic.md

## Current state / context

Ticket 21 made `service.py` contain **every** `StatusUI` callback exception as best-effort, so a broken
display can never wedge the service in `TRANSCRIBING`. That is correct for safety and leaves an
**observability** hole: a tray that raises on every call simply stops updating, with no error, no
notification and no diagnostic — the silent-degradation class ADR 0013 exists to prevent.

Ticket 17 closed half of it: `TrayStatusUI` records its own failures (`failure_count` + last error) and
exposes `failure_report()`. Ticket `../doctor-command/ticket.md` was then required to **surface** that
record — but it cannot. The record lives **in the service process**, and `doctor` is a separate,
read-only process that must not attach to anything. So `doctor`'s tray-failure check has **no live
provider**: after ticket 12's fix it honestly reports that the state **cannot be determined from here**
rather than a false green `OK`, and points at the service's stderr and shutdown summary.

Those fallbacks are real but weak: a user running the service in the background will never read its
stderr. This ticket supplies the missing channel so the requirement is **actually** met rather than
apparently met.

Relevant decisions: ADR 0013 (the tray; fail loudly), ADR 0015 (the tray lives in the service
process), ADR 0010, ADR 0016 (the CLI/socket surface — **this ticket amends it**, see below),
ADR 0020 (a configured alternative is never silently substituted).

## ⚠️ This ticket requires an explicit ADR amendment

Adding a socket verb changes the documented surface in **ADR 0016**. Per this project's practice,
**amend ADR 0016 explicitly** as part of this ticket — state the new verb, why it exists, and that it is
**read-only** — rather than letting the surface drift undocumented. Alternatively, if you conclude a
new verb is the wrong shape, propose the alternative and say so; do not silently widen the protocol.

## Definition of done

- [ ] Add a **read-only** `diagnostics` (name it as you see fit; keep it consistent with the existing
      verb vocabulary) request to the socket protocol, returning the service's tray failure record:
      the failure count and the last error/message, or a clear "no failures recorded" / "not available"
      response.
- [ ] The service answers it **from its in-process record** (`failure_report()`), without changing
      state, without holding the state lock across I/O, and **without blocking the socket loop** — a
      slow or absent client must not wedge the service (this project has already had a reproduced
      deadlock of exactly that shape, ticket `../status-ui-outside-lock/ticket.md`).
- [ ] `doctor`'s tray-failure check **queries it when the service is listening** and reports `OK`/`FAIL`
      from the real record; when no service is running it keeps today's honest "cannot be determined"
      behaviour. `ges2t status` may surface the same record; say whether you did that and why.
- [ ] **Bounded**: a hung or absent service must not hang `doctor` or `status`. Use a timeout and fail
      softly with a clear message.
- [ ] **Read-only and safe**: the verb must not mutate state, write to disk, or expose anything beyond
      the diagnostic record. The socket is user-local; the failure text is the tray's own error string.
- [ ] `tests/` cover: the service answers with a record and with no record; a client that never reads
      does not wedge the service; `doctor` reports `FAIL` from a real failing record and `OK` from a
      clean one; and an unreachable service degrades to the honest unverifiable state rather than
      hanging or crashing. **Falsify each** — break the behaviour, watch the test fail, revert.
- [ ] `uv run pytest` and **`uv run mypy src/ges2t tests`** green (ADR 0019 covers `tests/`). Report
      before/after counts; no test deleted, skipped or weakened.
- [ ] `001-comment.md` records the verb, the ADR 0016 amendment, the boundedness argument, and the
      falsification evidence.

## Notes

- **Do not weaken the existing behaviour:** ticket 21's containment, ticket 17's `failure_report()`, the
  tray backend assertion, and ticket 12's other 21 checks must all be untouched.
- Do **not** make `doctor` or `status` start a service, attach a debugger, or read another process's
  memory. The socket is the interface.
- No new dependency; follow the project's existing socket/subprocess approach.
- **No keystrokes, no clipboard, no windows, no `sudo`, no service changes, no network** — and the
  agent must not stop or start the user's `ydotoold` or the service.
- The end-to-end confirmation (make the tray fail on purpose and see `doctor` report it) is
  **user-owned**; provide the steps and leave that box unticked.
