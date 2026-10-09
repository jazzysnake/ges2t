# Notifications: turn silent failures into actionable messages

- **Priority:** P1
- **Status:** to do
- **Complexity:** S
- **Epic:** ../epic.md

## Current state / context

The service already routes failures to `status_ui.error(message)` (ADR 0015), and
`../tray-status-ui/ticket.md` makes that visible in a tooltip. A tooltip is easy to miss
during dictation: the failure the user actually hits first is `ydotoold` not running, and
today it would produce an error nobody notices.

`notify-send` is installed on the reference machine. The user approved notifications for
errors in the interview ("notifications" and "on error" both in scope).

Relevant decisions: ADR 0016 (every failure names the command that fixes it), ADR 0003
(the daemon/uinput failure is the expected first failure), ADR 0013 (nothing may be reachable
only through the tray), ADR 0012 (empty transcriptions are not an error).

## Definition of done

- [ ] `src/ges2t/notify.py` defines a `NotifyingStatusUI` that **wraps** another `StatusUI`
      (composition, not inheritance) and forwards every call, additionally emitting a desktop
      notification on `error(message)`.
- [ ] Notification behaviour:
      - `error(message)` sends one notification whose body is the **actionable message** from
        the service — which, per ADR 0016, already names the fix (e.g. "start and enable
        `ydotoold`"). Do not re-word it into something vaguer;
      - `idle()`, `recording()`, `transcribing()`, and `partial(text)` send **no**
        notification. In particular, `partial` must never notify (ADR 0011);
      - **duplicate suppression**: the same error message repeated within a short window (a few
        seconds) sends one notification, so a user hammering a bound key does not get a stack
        of identical popups. The suppression window is a module constant, not a config key;
      - notifications are sent through `notify-send` via `subprocess` with an explicit argument
        list, using a fixed application name; failure to notify (no notification daemon,
        `notify-send` missing) is **logged and swallowed** — it must never take the service
        down or mask the original error;
      - an injectable runner is provided so tests do not execute `notify-send`.
- [ ] `run_service` in `src/ges2t/pipeline.py` wraps the status UI with `NotifyingStatusUI`
      when `config.ui.notifications` is true, and uses the bare UI otherwise (ADR 0009).
- [ ] Cancelling is already implemented (`ges2t cancel`, ADR 0016 and the service transition
      table); this ticket adds a notification **only** if the cancel path can fail — it cannot,
      so it adds none, and does not add a "cancelled" popup on success.
- [ ] `tests/test_notify.py` covers: every `StatusUI` call forwards to the wrapped UI; `error`
      triggers exactly one notification containing the message verbatim; `partial` and the
      state calls trigger **none**; duplicate suppression within the window and re-notification
      after it (inject the clock or the window, do not sleep); a raising/missing `notify-send`
      is swallowed and does not affect the wrapped UI's calls; and `notifications = false`
      means `run_service` does not wrap at all.
- [ ] `uv run pytest tests/test_notify.py` passes and `uv run pytest` stays green.
- [ ] **Manual, once, reported in `001-comment.md`:** with `ydotoold` stopped, trigger a
      dictation and confirm a desktop notification appears whose text names the daemon fix;
      then repeat rapidly and confirm only one popup appears. Restore `ydotoold` afterwards.

- [ ] `uv run mypy src/ges2t` exits 0: every definition fully annotated and every
      signature interface-typed — ADR 0019.

## Notes

- Wrap, do not modify: `TrayStatusUI`, `NullStatusUI`, and the service are all untouched by
  this ticket except for the one wiring line in `run_service` (which
  `../tray-status-ui/ticket.md` and `../streaming-wiring-and-e2e/ticket.md` also touch —
  expect this ticket to run after them so the edit is a small addition).
- Keep the suppression keyed on the message text, not on a state, so two different errors
  still both notify.
- No new dependency: `notify-send` is a subprocess, consistent with ADR 0008's approach to
  system integration.

## Related tickets

- depends on: ../tray-status-ui/ticket.md, ../streaming-wiring-and-e2e/ticket.md
- blocks: ../user-documentation-and-setup-guide/ticket.md
- related ADRs: ../../../adr/0016-cli-surface.md, ../../../adr/0003-text-injection-via-ydotool.md,
  ../../../adr/0012-verbatim-output-and-trailing-space.md
