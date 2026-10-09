# Tray via `pystray`'s AppIndicator backend; venv with `--system-site-packages`

- **Status:** accepted
- **Date:** 2026-10-09

## Context

The user wants a tray icon showing **idle / recording / transcribing** state (ADR 0000, and
confirmed item-by-item in the interview). This is harder than it looks on the reference
machine, and the naive choice fails *silently*.

The user first said they wanted the tray dependency to be **Python-only** (pip-installable,
no Arch packages). That was tested and does not hold on this machine:

- `pystray` 0.19.5 installs pip-only, pulling `pillow`, `python-xlib`, `six` — no declared
  system dependency. ✅
- But in a venv **without** system site packages, `pystray._appindicator` and `pystray._gtk`
  both fail with `ModuleNotFoundError: No module named 'gi'`, so `pystray` silently selects
  its **`_xorg`** (XEmbed) backend. Constructing an icon succeeds — the failure is invisible
  at the API level.
- **Plasma 6.7.5 removed XEmbed systray support.** So the pip-only configuration yields a
  running process and an **icon that never appears**.
- The working path is `pystray` with the **AppIndicator** backend, which needs `gi`,
  i.e. Arch packages `python-gobject` (3.56.3), `gtk3` (3.24.52), `libappindicator`
  (12.10.1) — **all already installed**, with the needed typelibs present
  (`AppIndicator3-0.1.typelib`, `Gtk-3.0.typelib`).
- Verified working: `uv venv --system-site-packages` makes `gi` + `Gtk 3.0` importable on
  the machine's Python 3.14.7.

Alternatives considered and rejected: **PyQt6/PySide6** (pip-only and works on Wayland via
StatusNotifierItem, but an 80–100 MB dependency — larger than the model); a hand-rolled
**StatusNotifierItem over `dbus-next`** (pure-pip, but real spec work and more failure
modes). The user was shown this table and chose the AppIndicator path.

## Decision

Use **`pystray` with the AppIndicator backend**, and run ges2t's virtualenv with
**`--system-site-packages`** so `gi` is visible.

- **Document the three Arch packages** as prerequisites in the README
  (`python-gobject`, `gtk3`, `libappindicator`). This is acceptable because packaging and
  distribution are out of scope for v1 (ADR 0000).
- **Fail loudly, never fall back.** On startup, ges2t must **verify the selected backend is
  `appindicator`** and refuse to run the tray — with an actionable error and a
  `ges2t doctor` check — if `pystray` selected `_xorg` or `_gtk`. The silent XEmbed fallback
  is precisely the failure mode this ADR exists to prevent.
- The tray is behind a thin **`StatusUI`** interface exposing state transitions
  (`idle`, `recording`, `transcribing`, `error`). v1's implementation uses `pystray` on its
  own thread; the state machine (ADR 0004) never touches `pystray` directly.
- The tray runs in the **service** process (`ges2t run`), which is also the process that
  owns the process-global model (ADR 0005).
- If the desktop environment cannot show a tray at all, ges2t must still be fully usable
  from the CLI — the tray is a **status display and menu**, not a required control surface.
  Nothing may be reachable *only* through the tray.

## Consequences

- One documented setup step involving system packages, in exchange for not pulling in a
  100 MB GUI toolkit for a 16 MB model.
- The dependency set is Arch-flavoured and untested elsewhere, consistent with the
  Wayland/KDE/Arch reference target (ADR 0002).
- Because we assert on the backend, a future `pystray` release that changes backend
  selection order cannot silently break the UI — it will produce a clear error instead.
- GTK is imported only inside the tray implementation, never in interfaces or the state
  machine, so headless unit tests never need `gi` (ADR 0014).
