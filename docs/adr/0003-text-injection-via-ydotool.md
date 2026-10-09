# Text injection via ydotool/uinput

- **Status:** accepted
- **Date:** 2026-10-09

## Context

The whole point of ges2t is that transcribed text lands in whatever text field or terminal
currently has focus. On Wayland the only way to synthesise keystrokes into *another*
application is a virtual input device via `/dev/uinput`. The user was offered three options:
**(a)** `ydotool`-style virtual keyboard; **(b)** clipboard plus simulated paste
(`wl-copy` + Ctrl+V); **(c)** both with a fallback. The user chose **(a)**, explicitly stated
that `ydotool` may be a dependency, and stated that clipboard-paste is a possible
*configurable alternative later* — **not** a fallback.

Facts about this machine:

- `ydotool` and `ydotoold` are installed (`/usr/bin/ydotool`, `/usr/bin/ydotoold`).
- `ydotoold` is currently **inactive/dead**; the Arch `ydotool.service` user unit is
  `disabled`. The user must start and enable it.
- `/dev/uinput` exists as `crw-rw----+ root:input`. The `+` means an ACL is present; access
  still depends on group membership or ydotool's udev rule.
- `wtype` is **not** installed; `wl-copy`/`wl-paste` are.
- `ydotool` types by synthesising key events, so it is layout-aware only insofar as the
  tool is told which keymap to use; it emits characters via keycodes.

## Decision

Use **`ydotool` as the text-injection mechanism**, invoked against a running `ydotoold`
daemon. Injection is behind an `Injector` interface with a single v1 implementation,
`YdotoolInjector`.

- The app **docs** the prerequisites (enable `ydotoold`, grant `/dev/uinput` access) and
  **detects** a broken setup at runtime, producing an actionable error via `ges2t doctor`
  (ADR 0016) rather than silently typing nothing.
- Clipboard-paste is **not** implemented in v1 and is **not** an automatic fallback. It
  remains the natural second implementation of `Injector`, selected by configuration, if it
  is ever built. Automatic fallback is explicitly rejected: silently changing mechanism
  changes behaviour the user did not ask for.
- Typing latency matters: a long transcript typed key-by-key must not blow the framework's
  sub-1-second budget (ADR 0001) or drop characters. `ydotool` supports batched typing; the
  implementation must write the text in as few daemon round-trips as practical and must
  report a measurable per-call cost.

## Consequences

- ges2t cannot work without `ydotoold` running. This is an accepted, documented setup cost,
  not a bug.
- Because injection is keyboard-event based, transcripts are subject to the target
  application's own key handling. This is a category limitation of the approach, not
  something ges2t can fix.
- Because there is no silent fallback, a broken injector produces a visible error and a
  notification — never a quiet no-op.
- The `Injector` interface plus a fake implementation keeps the pipeline unit-testable
  without `/dev/uinput` (ADR 0014).
