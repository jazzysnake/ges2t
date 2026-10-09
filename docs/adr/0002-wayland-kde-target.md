# Wayland-only, with KDE Plasma 6 as the reference compositor

- **Status:** accepted
- **Date:** 2026-10-09

## Context

The reference machine runs `XDG_SESSION_TYPE=wayland`, `XDG_CURRENT_DESKTOP=KDE`,
`kwin_wayland` (Plasma **6.7.5**). An X11 session is also technically reachable on this box
(`DISPLAY=:1`), but the user does not use it.

Wayland deliberately forbids the X11 model of a background process grabbing global input and
synthesising keystrokes into other windows. Two consequences follow, and both are
architectural:

- **Global hotkeys** cannot be captured with `XGrabKey`/`xdotool`-style tricks; they must go
  through the compositor (ADR 0004).
- **Text injection** cannot use XTEST; it requires `/dev/uinput` (ADR 0003).

The user was asked whether X11 support is needed and answered that **Wayland-only is the
goal**, and that they do not know how much extra effort a generic-Wayland implementation
would cost versus a KDE-specific one.

## Decision

Target **Wayland only**. Do not support X11 sessions, in v1 or as a designed-for
contingency. Write no X11-specific code paths.

KDE Plasma 6 on Arch is the **reference compositor**: it is what we develop against and what
the acceptance criteria are evaluated on. Other Wayland compositors (sway, Hyprland, GNOME)
are neither tested nor blocked — the hotkey approach chosen in ADR 0004 happens to be
compositor-agnostic — but they are **best-effort and unverified**.

Qt/KDE specifics that we must *avoid* hardcoding: nothing in the app may depend on
`KGlobalAccel`, Plasma DBus interfaces, or Qt libraries. Plasma 6 dropped XEmbed systray
support, which constrains the tray choice (ADR 0013).

## Consequences

- The app never needs `xdotool` or an X11 connection, even though `xdotool` exists on the
  box. `DISPLAY` is irrelevant to us.
- The `_xorg`/XEmbed path is a trap: a tray implementation that silently falls back to it
  produces a working-looking process and an invisible icon on Plasma 6. ADR 0013 exists
  specifically to prevent that.
- Since we do not test other compositors, any future "does it work on Hyprland?" question is
  new work, not a regression.
- The two Wayland-hard problems (hotkey, injection) are both isolated behind interfaces
  (`HotkeySource`, `Injector`), so a future compositor- or desktop-specific implementation
  is additive rather than a rewrite.
