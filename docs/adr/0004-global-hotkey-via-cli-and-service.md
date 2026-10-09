# Global hotkey via a compositor-bound CLI command and a long-lived service

- **Status:** accepted
- **Date:** 2026-10-09

## Context

A global hotkey is essential: the user wants press-to-talk by default, toggle as a
configurable alternative, and the hotkey itself configurable. But on Wayland there is no
portable "register a global hotkey" API, and the three candidate approaches differ sharply
in cost and risk. They were presented to the user as:

- **(a) KDE-native** — register through `org.kde.kglobalaccel`. Real hold-to-talk, no manual
  binding, but KDE-only and fiddly to drive correctly from Python.
- **(b) Raw `evdev`** — read the input device directly. Works on any Wayland compositor, but
  requires membership in the `input` group and gives ges2t visibility into **every
  keystroke the user types**, including passwords. Large security surface.
- **(c) Compositor-bound CLI command** — the user binds a key in their compositor's
  shortcut settings to a small ges2t command. No extra permissions, no keylogging.

The user asked what (c) looks like in practice and then chose **(c)**, with a KDE-native
backend becoming possible later behind an interface.

## Decision

Adopt **(c)**. ges2t runs as a long-lived service (`ges2t run`, ADR 0016) that holds the
warm model and listens on a **Unix domain socket** under `$XDG_RUNTIME_DIR`. Thin CLI
subcommands talk to it:

- `ges2t toggle` — start recording if idle, stop and transcribe if recording (this is the
  default binding)
- `ges2t ptt-start` / `ges2t ptt-stop` — press-to-talk, for compositors that can fire
  distinct key-press and key-release commands
- `ges2t cancel` — abort a recording without transcribing

The user binds these to keys in **KDE → System Settings → Shortcuts → Add → Command or
URL**. Default documented binding: the `toggle` command on a single dedicated key.

Design constraints this imposes:

- The service must be **idempotent and safe under rapid, out-of-order invocations**:
  `ptt-stop` arriving twice, or `ptt-start` arriving before the previous recording has been
  transcribed, must be handled by an explicit state machine, not by hoping. This state
  machine is a primary unit-test target (ADR 0014).
- The service must survive a missing socket peer, and CLI commands must fail with a clear
  message when the service is not running, not hang.
- The design must not assume press/release semantics exist: **toggle is the guaranteed mode,
  press-to-talk is dependent on the compositor's shortcut granularity.** Where the
  compositor cannot distinguish key events, `ptt-start`/`ptt-stop` degrade to a toggle pair
  and this is documented rather than worked around.
- Hotkey capture is behind a `HotkeySource` interface whose v1 implementation is
  `CliCommandHotkeySource` (the socket). A future `KGlobalAccelHotkeySource` can be added
  without touching the state machine.

## Consequences

- No elevated privileges, no `input` group membership, and ges2t never observes keystrokes
  it did not ask for. The keylogging risk of (b) is avoided entirely.
- Onboarding includes a documented one-time step: add the shortcut in System Settings.
  Accepted, per the out-of-scope decision on packaging (ADR 0000).
- True press-and-hold is best-effort, not guaranteed. The user accepted this trade when
  choosing (c).
- A shell-out (~milliseconds) occurs per keypress, which is negligible against the
  sub-1-second framework budget (ADR 0001).
- Because the hotkey path is just a socket, the mapping from "key" to "recording" is
  entirely in the user's compositor settings — ges2t's own `hotkey` config key is therefore
  documentation and default-generation, not a grab.
