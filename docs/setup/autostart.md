# Autostart ges2t with a systemd user unit

ges2t can run in a terminal (`uv run ges2t run`) or be started automatically with your
graphical session. [`ges2t.service`](ges2t.service) is a ready-to-copy **systemd user
unit** for the second option.

It is **documentation only**: nothing in ges2t installs, enables or edits it — packaging is
out of scope ([ADR 0000](../adr/0000-project-scope-and-non-goals.md)) and the service is
one long-lived process you own ([ADR 0015](../adr/0015-single-service-process-architecture.md)).

## 1. Edit the two paths

The unit as shipped points at the reference machine's checkout. `ExecStart` must be the
absolute path to the `ges2t` console script inside **your** virtualenv, and
`WorkingDirectory` the checkout it belongs to:

```ini
[Service]
Type=simple
ExecStart=/home/YOU/src/ges2t/.venv/bin/ges2t run
WorkingDirectory=/home/YOU/src/ges2t
Restart=on-failure
RestartSec=2
```

Do **not** use `uv run` or a shell pipeline here: systemd needs one executable to track and
does not run your shell. `%h` expands to your home directory, so `%h/src/ges2t/.venv/bin/ges2t`
is a valid `ExecStart`.

## 2. Install and enable it

```bash
mkdir -p ~/.config/systemd/user
cp docs/setup/ges2t.service ~/.config/systemd/user/ges2t.service
$EDITOR ~/.config/systemd/user/ges2t.service   # fix the two paths from step 1

systemctl --user daemon-reload
systemctl --user enable --now ges2t.service
```

`enable` makes it start with the graphical session (`WantedBy=graphical-session.target`);
`--now` starts it immediately.

**Run only one service per session.** The unit and a manual `ges2t run` cannot both work:
the second one exits with exit code 1 and `a ges2t service is already listening on …`.
Because the unit has `Restart=on-failure`, that exit is retried, but systemd's start-rate
limit (5 starts in 10 s by default) stops it and marks the unit failed instead of looping
forever. Stop the manual service before starting the unit, and vice versa.

## 3. Check it

```bash
systemctl --user status ges2t.service
```

A healthy unit shows `Active: active (running)` with a `Main PID` and the `ges2t run`
process. `Type=simple` is correct here: the service does not signal readiness, so
`active (running)` means the process started, not that a dictation has been served.

Check the actual service state through ges2t itself:

```bash
uv run ges2t status        # expected: idle
```

The tray icon appears when the graphical session is up and `ui.tray = true`. The reference
machine's systemd user manager already carries the environment the tray needs — verified with
`systemctl --user show-environment`, which includes `WAYLAND_DISPLAY=wayland-0`,
`XDG_CURRENT_DESKTOP=KDE` and `DBUS_SESSION_BUS_ADDRESS`. If your tray never appears under
systemd but works from a terminal, compare those variables:

```bash
systemctl --user show-environment | grep -E 'WAYLAND_DISPLAY|XDG_CURRENT_DESKTOP|DBUS'
```

If they are missing, import them from a terminal **inside** the session:

```bash
systemctl --user import-environment WAYLAND_DISPLAY XDG_CURRENT_DESKTOP DBUS_SESSION_BUS_ADDRESS
```

or export them from whatever runs at session start. *(This document did not enable or run the
unit itself, so the unit running under systemd is unverified — see the ticket's
`001-comment.md`.)*

## 4. Read its logs

The unit's output goes to the user journal:

```bash
journalctl --user -u ges2t.service -f          # follow it live
journalctl --user -u ges2t.service -n 100      # the last 100 lines
journalctl --user -u ges2t.service --since today
journalctl --user -u ges2t.service -p warning  # warnings and errors only
```

This is where the messages doctor cannot see surface:

- `ges2t: the tray has failed …` — the tray recorded a callback failure (see the README's
  **Tray failures** row);
- `ges2t: shutdown gave up after 5.0 s waiting for an in-flight transcription …` — a wedged
  engine was abandoned at shutdown;
- the startup failure sentence and its `hint:` line when the model is missing, the injection
  configuration is contradictory, or the socket is already in use.

## 5. Stop, disable, or restart it

```bash
systemctl --user stop ges2t.service        # stop now; the tray icon disappears
systemctl --user disable ges2t.service     # do not start it with the next session
systemctl --user restart ges2t.service     # after editing the config or upgrading ges2t
```

After `systemctl --user restart`, the tray's **Quit** menu item and `Ctrl+C` both exit the
process cleanly (exit status 0), which `Restart=on-failure` deliberately does **not** restart.
Use `systemctl --user start ges2t.service` to bring it back.

## 6. What this unit does not do

- It does not install ges2t, create the virtualenv, or download the model. Do
  [README sections 4–6](../../README.md) first.
- It does not enable `ydotoold`: that is the separate `ydotool.service` user unit
  (`systemctl --user enable --now ydotool.service`, README section 5).
- It does not bind your hotkey. That is a KDE shortcut (README section 7).
