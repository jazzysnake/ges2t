# Tray status UI

- **Priority:** P1
- **Status:** to do
- **Complexity:** M
- **Epic:** ../epic.md

## Current state / context

`run_service` currently injects a documented `NullStatusUI`, so the user has no visible signal
of what ges2t is doing: a stuck recording is indistinguishable from an idle one until they
notice text not arriving. This ticket supplies the real `StatusUI` (ADR 0013).

The reference machine makes this ticket unusually subtle, and ADR 0013 exists because of it:

- `pystray` 0.19.5 installs pip-only, but in a venv **without** system site packages its
  `_appindicator` and `_gtk` backends both fail with `ModuleNotFoundError: No module named
  'gi'`, and it **silently selects `_xorg`** instead.
- **Plasma 6.7.5 removed XEmbed systray support**, so that silent fallback produces a process
  that looks fine and an icon that never appears.
- The working path is the **AppIndicator** backend, which needs `gi` — available because the
  venv is created with `--system-site-packages` (ticket
  `../../core-dictation-pipeline/project-scaffold/ticket.md`) and the Arch packages
  `python-gobject`, `gtk3`, `libappindicator` are installed.

Relevant decisions: ADR 0013 (backend choice, fail loudly, no fallback), ADR 0002 (KDE
reference), ADR 0015 (the tray lives in the service process), ADR 0011 (partials are
display-only).

## Definition of done

- [ ] `src/ges2t/ui/__init__.py` exists; `src/ges2t/ui/status.py` defines `NullStatusUI`
      (moved out of `pipeline.py`, which currently defines it) plus any shared state enum.
- [ ] `src/ges2t/ui/tray.py` defines `class TrayStatusUI` implementing `StatusUI`:
      - `idle()`, `recording()`, `transcribing()`, `error(message)`, `partial(text)` map onto
        a visible icon/tooltip state; `partial(text)` updates the tooltip or a menu label and
        **never** types anything;
      - a small menu with `Toggle dictation`, `Cancel`, and `Quit`, which invoke the **service
        socket client** (ADR 0016) rather than reimplementing service logic;
      - `pystray` runs on its own thread; the state machine never blocks on it;
      - `stop()` shuts the icon down cleanly so the service can exit.
- [ ] **The backend assertion is mandatory and is the point of this ticket.** Before creating
      the icon, assert the selected `pystray` backend is the AppIndicator one (by importing
      `pystray._appindicator` with `gi.require_version("AppIndicator3", "0.1")`, or by
      inspecting the class that `pystray.Icon` would use). If it is not, raise a clear error
      naming the three Arch packages and the `--system-site-packages` requirement, and **do
      not** start a tray that would be invisible (ADR 0013).
- [ ] `run_service` in `src/ges2t/pipeline.py` injects `TrayStatusUI` when `config.ui.tray` is
      true and `NullStatusUI` otherwise, and falls back to `NullStatusUI` with a printed
      warning when the backend assertion fails — a missing tray must never make ges2t
      unusable, and nothing may be reachable **only** through the tray (ADR 0013).
- [ ] `tests/test_status_ui.py` covers `NullStatusUI` trivially, and
      `tests/test_tray_backend.py` covers the assertion logic with **fake modules injected into
      `sys.modules`**: a fake `gi`+`AppIndicator3` → assertion passes; a fake `pystray` whose
      backend resolves to `_xorg`, and a missing-`gi` case, → assertion fails with the
      actionable message. No test may create a real tray icon or require a display.
- [ ] `uv run pytest tests/test_status_ui.py tests/test_tray_backend.py` passes and
      `uv run pytest` stays green.
- [ ] **Manual, on the reference machine, reported in `001-comment.md`:** start `ges2t run` and
      observe a tray icon that is actually **visible in the Plasma 6.7.5 system tray**, stepping
      `idle` → `recording` → `transcribing` → `idle` through a real dictation; confirm the menu
      items work; and confirm that deliberately hiding `gi` makes ges2t print the actionable
      warning and fall back to `NullStatusUI` instead of showing nothing. Include a screenshot
      or a precise description of what the icon looked like.

- [ ] `uv run mypy src/ges2t` exits 0: every definition fully annotated and every
      signature interface-typed — ADR 0019.

## Notes

- Import `gi` and `pystray` **inside** the tray module, never at package import time — the
  service, CLI, and tests must all work with no GTK present (ADR 0014).
- If `pystray`'s internal backend selection is such that a clean assertion is not possible,
  implement the check by constructing the icon and inspecting the resulting object's class
  (the earlier probe on this machine showed `pystray.Icon(...)` resolves to
  `pystray._xorg` when `gi` is absent), and document the technique in a comment.
- Do not add GTK/Qt directly; go through `pystray` so the backend decision stays in one place.
- Keep icons tiny and generated in code or shipped as small PNGs; do not add an icon-theme
  dependency for a 16 MB app.

## Related tickets

- depends on: ../../core-dictation-pipeline/status-ui-outside-lock/ticket.md (that ticket
  fixes a **reproduced deadlock**: the service currently calls `StatusUI` while holding a plain
  non-reentrant `threading.Lock`, so a tray that reads `service.state` back would hang the
  socket loop — meaning the hotkey stops responding with no error. Do **not** work around it by
  avoiding `service.state` in this ticket),
  ../../core-dictation-pipeline/dictation-pipeline-batch/ticket.md,
  ../../core-dictation-pipeline/cli-surface-and-commands/ticket.md
- blocks: ../notifications-and-cancel/ticket.md,
  ../user-documentation-and-setup-guide/ticket.md
- related ADRs: ../../../adr/0013-tray-via-pystray-appindicator.md,
  ../../../adr/0015-single-service-process-architecture.md,
  ../../../adr/0011-streaming-sliding-window.md
