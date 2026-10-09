# Doctor command: diagnose every prerequisite

- **Priority:** P0
- **Status:** to do
- **Complexity:** M
- **Epic:** ../epic.md

## Current state / context

Because packaging is out of scope (ADR 0000), ges2t depends on several **manual** system
prerequisites. The user approved this, and explicitly required that ges2t **detect and explain**
a broken environment rather than failing silently (ADR 0003 for the daemon, ADR 0013 for the
tray backend, ADR 0016 for the command itself).

`doctor` is the payoff: it is the single place that answers "why isn't it working?", and
ADR 0016 makes it the designated home for **every** environment check discovered by any
ticket. The reference machine is currently in a "broken" state for at least one check —
`ydotoold` is inactive/disabled — so this ticket has a real failing case to develop against.

Relevant decisions: ADR 0016 (the command, exit codes, no silent mutation), ADR 0003
(ydotoold/uinput), ADR 0013 (tray backend must not silently fall back), ADR 0009 (config
warnings), ADR 0017 (model presence), ADR 0015 (socket/single instance).

## Definition of done

- [ ] `src/ges2t/doctor.py` provides `run_doctor(config) -> int`, the entry point ticket
      `../cli-surface-and-commands/ticket.md` calls lazily.
- [ ] Each check below is an independent function returning a result object with a status of
      `OK`, `WARN`, or `FAIL`, a one-line summary, and — for anything not `OK` — an
      **actionable fix line naming an exact command or setting**:

      | Check | FAIL when | Fix line must say |
      |---|---|---|
      | Python/pytest sanity | the package cannot import its own modules | reinstall instructions |
      | Architecture | the platform has no pinned engine artifact | which platforms are pinned |
      | Model present | `whistle.cact` missing or the wrong size | `ges2t setup` |
      | Model verified | `--verify`: sha256 mismatch | `ges2t setup` (re-download) |
      | Engine present | `libneedle.so` missing or the wrong size | `ges2t setup` |
      | `cactus-needle` importable | the package cannot be imported | `uv sync` |
      | Engine/package versions | always reported; **WARN** if the installed `cactus-needle` version differs from the pinned one | the pinned vs installed versions |
      | Config valid | TOML error or bad-typed value | the file path and the offending key |
      | Engine config valid | a `WhistleConfigError` from the selected engine's block (ticket 4 raises a **distinct** type from the generic `ConfigError`, deliberately — see its `001-comment.md`) | the block name, key and allowed range |
      | Config warnings | unknown keys anywhere (generic **and** the selected engine block — ADR 0009) | which keys were ignored |
      | Audio device exists | `audio.device` is non-empty **and** does not appear in the session's node list (`pw-dump`/`wpctl status`) | the configured name and how to list available devices |
      | `pw-record` present | not on `PATH` | which package provides it |
      | PipeWire session | `pw-record` cannot reach a session | how to check the session |
      | `ydotoold` running | the daemon is unreachable (true today) | `systemctl --user enable --now ydotoold` |
      | `/dev/uinput` access | the process cannot open it | `input` group membership or the ydotool udev rule, plus re-login |
      | Tray backend | `gi` or the `AppIndicator3` typelib is unavailable, **or** `pystray` would select `_xorg` (ADR 0013) | the three Arch packages and the `--system-site-packages` venv |
      | Service/socket | a socket exists but refuses connections (stale) | restart `ges2t run` |
      | Hotkey binding | always `WARN` | print the exact commands to bind in System Settings (ADR 0004) |

- [ ] Output is human-readable, one line per check, with a final summary line. `FAIL` entries
      are listed again at the end with their fix lines, so the user does not have to scroll.
- [ ] Exit code is `0` when there are no `FAIL`s (warnings are allowed) and non-zero when any
      check fails, consistent with ADR 0016.
- **The PipeWire-session check is the mitigation for a real race in ticket 6.** Ticket 6's
  review measured it: because a zero-sample recording is a legitimate outcome (start then stop
  before any audio arrives), `stop()` within roughly **50 ms** of `start()` can swallow a
  genuine "no session" failure and report a normal empty buffer instead of an error (measured:
  at 0.0 s no raise, at 0.05 s raise). The heuristic is sound and the race is inherent to
  treating zero samples as normal, so it is **not** fixed in the audio source; `doctor` is
  where a broken session is caught, once, before dictation rather than during it. Make this
  check unconditional and reliable.
- **Report the installed engine and package versions, and warn on skew.** Ticket 14's review
  flagged a real risk (**R1**): the streaming decoder is coupled to `cactus-needle==3.1.3`'s
  exact generator shape (one dict per `next()`, terminal tail dict, and the engine's own
  `stop()`). The test fake encodes that same shape, so a package upgrade could break the real
  seam **while the suite stays green**. `pyproject.toml` pins the version exactly, which makes
  an upgrade deliberate rather than accidental, but nothing currently asserts it at runtime.
  `doctor` should print the installed package version and the engine `.so` version, and WARN
  when either differs from the pins in ADR 0017 — that turns silent behavioural drift into a
  visible line of output. Do **not** add a runtime version assertion inside the adapter (that
  would fail dictation for a cosmetic mismatch); `doctor` is the right place.
- **The audio-device check exists because a bad device cannot be detected any other way.**
  ADR 0008's amendment (2026-10-09) records that `pw-record --target` is only a hint and that an
  unresolved target silently falls back to the default microphone, exiting normally with empty
  stderr. So a typo in `audio.device` would otherwise produce a *working-looking* recording from
  the wrong microphone. `doctor` is the only place this is catchable, and this check is a
  **WARN** when the configured device is absent (the user may still be dictating fine, from the
  wrong mic) and a FAIL only if the device name is syntactically invalid. Use `pw-dump` or
  `wpctl status` — both already installed — and make the probe injectable so tests touch no real
  session.
- [ ] `doctor` must catch **both** configuration error types: `ges2t.config.ConfigError`
      (generic layer, names a file path) and the selected engine adapter's own error type,
      beginning with `WhistleConfigError` (engine block, names a block key). They are
      deliberately distinct — the generic layer must not know what an engine-specific key
      means (ADR 0009) — so `doctor` is where they are unified into one user-facing report.
      Do not "fix" the duplication by making both inherit from a shared class without an ADR;
      the two types exist for a reason.
- [ ] `doctor` performs **no network I/O**, writes nothing to disk, and never mutates config or
      system state (ADR 0016). It must be safe to run at any time, including while the service
      is running.
- [ ] **Reports the injection/layout environment** (ADR 0020, added after the `hu` discovery, so it
      is easy to miss): the resolved keymap/layout and where it came from (configured → live keymap
      → detected), whether `libxkbcommon` is loadable, whether `ydotoold` is reachable and
      `/dev/uinput` writable, and — if the clipboard backend is selected — whether `wl-copy` exists.
      A user whose layout cannot be resolved must learn it here, **before** dictation silently
      produces wrong characters. Depends on ticket
      `../layout-detection-and-config/ticket.md` (done) and `../xkb-keycode-mapping/ticket.md`.
- [ ] **Surfaces a silently-failing tray.** `service.py` contains every `StatusUI` callback
      exception as best-effort (ticket `../status-ui-outside-lock/ticket.md`, review `003`), which is
      correct for safety but means a tray whose callbacks always raise simply stops updating with no
      signal. `TrayStatusUI` records its own failures (count + last error) per ticket
      `../../live-dictation-ux/tray-status-ui/ticket.md`; `doctor` (and/or `status`) must be able to
      report them, so a broken tray is visible rather than merely absent.
- [ ] A `--verify` flag forces the full sha256 model check; without it, only existence and
      size are checked (ADR 0017).
- [ ] `tests/test_doctor.py` covers each check's `OK`/`WARN`/`FAIL` branch by injecting fake
      probes (a fake `PATH`, fake daemon probe callables, a fake `pystray` module, a fake data
      dir), plus: the failure exit code, the warning-only exit code, that every `FAIL` produces
      a non-empty fix line, and that no network client is invoked.
- [ ] `uv run pytest tests/test_doctor.py` passes and `uv run pytest` stays green.
- [ ] **Manual, on the reference machine**: `uv run ges2t doctor` reports the current state
      truthfully — specifically, it must **FAIL** on `ydotoold` (inactive/disabled) and pass
      the rest once `ges2t setup` has run. Paste the real output into `001-comment.md`.

- [ ] `uv run mypy src/ges2t tests` exits 0 (per ADR 0019's amendment the enforcement command
      covers `tests/` too): every definition fully annotated and every
      signature interface-typed — ADR 0019.

## Notes

- Detecting the tray problem is subtler than importing `gi`: ADR 0013 explains that `pystray`
  *silently* selects its `_xorg` backend when `gi` is missing, and that Plasma 6 has no
  XEmbed systray. So the check must attempt the AppIndicator import explicitly (for example by
  importing `pystray._appindicator` with the right `gi.require_version`) and treat failure as a
  `FAIL`, not as "tray unavailable, fine".
- The `ydotoold` check should probe the daemon socket rather than shelling out to
  `systemctl` only; a daemon started by hand is equally valid (ADR 0003). Use both signals
  with `systemctl` as the fix suggestion.
- Keep the probes injectable: each check function should take its dependencies as parameters
  with real defaults, so tests never touch the real system.
- Every future ticket that adds a prerequisite is expected to add a row here; note that in the
  module docstring.

## Related tickets

- depends on: ../model-store-and-verification/ticket.md, ../audio-capture-pw-record/ticket.md,
  ../ydotool-injector/ticket.md, ../config-loading/ticket.md,
  ../service-state-machine-and-socket/ticket.md, ../cli-surface-and-commands/ticket.md
- blocks: ../../live-dictation-ux/user-documentation-and-setup-guide/ticket.md
- related ADRs: ../../../adr/0016-cli-surface.md, ../../../adr/0003-text-injection-via-ydotool.md,
  ../../../adr/0013-tray-via-pystray-appindicator.md, ../../../adr/0009-configuration-toml.md
