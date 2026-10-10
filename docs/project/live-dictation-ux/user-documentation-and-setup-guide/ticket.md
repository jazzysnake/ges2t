# User documentation and setup guide

- **Priority:** P1
- **Status:** done
- **Complexity:** M
- **Epic:** ../epic.md

## Current state / context

This is the last ticket of the project, and it is **load-bearing rather than decorative**.
Packaging and distribution are out of scope (ADR 0000), so ges2t depends on a set of manual
system steps: three Arch packages for the tray (ADR 0013), enabling `ydotoold` and granting
`/dev/uinput` access (ADR 0003, and the daemon is currently **disabled** on the reference
machine), a venv created with `--system-site-packages` (ADR 0013), a key bound in KDE System
Settings (ADR 0004), and a one-time `ges2t setup` (ADR 0017). Each of those is a place a new
user gets stuck, and the README is the only thing that unblocks them.

Ticket `../../core-dictation-pipeline/project-scaffold/ticket.md` created a README **stub**, and
`ges2t doctor` (ticket `../../core-dictation-pipeline/doctor-command/ticket.md`) implements the
same knowledge as executable checks. This ticket makes the two agree: every prerequisite
`doctor` checks must be documented, and every documented step must be checkable by `doctor`.

Relevant decisions: ADR 0000 (no packaging — hence manual steps), ADR 0003, ADR 0004,
ADR 0009 (config reference), ADR 0013, ADR 0016, ADR 0017, ADR 0006 (the honest
dependency/risk disclosure).

## Definition of done

- [ ] `README.md` is complete and covers, in this order, with real copy-pasteable commands:
      1. **What ges2t is**, one paragraph, offline and local, no cloud.
      2. **Status** — early personal-MVP, not packaged, no distribution target (ADR 0000).
      3. **Requirements** — Arch Linux, Wayland (KDE Plasma 6 reference), PipeWire, Python
         3.12+, `uv`, plus the system packages: `ydotool`, `python-gobject`, `gtk3`,
         `libappindicator`.
      4. **Install** — clone, `uv venv` **with system site packages** (the exact verified
         command from the scaffold ticket), `uv sync`, and the explicit warning that without
         system site packages the tray is invisible on Plasma 6 (ADR 0013).
      5. **Prepare the input permission** — `systemctl --user enable --now ydotoold` plus the
         `/dev/uinput` access requirement (`input` group or ydotool's udev rule) and the
         re-login note (ADR 0003). State plainly that this is required for text to be typed.
      6. **Get the model** — `uv run ges2t setup`, what it downloads, that it is
         checksum-pinned, and that it is the **only** step needing network access (ADR 0017,
         ADR 0018).
      7. **Bind a key** — step-by-step KDE **System Settings → Shortcuts → Add → Command or
         URL**, with the exact commands (`ges2t toggle` as the default, and optionally
         `ges2t ptt-start`/`ptt-stop`/`cancel`), and an explicit note that true press-and-hold
         depends on the compositor being able to fire separate key-press and key-release
         commands, otherwise toggle is the mode to use (ADR 0004).
      8. **Run it** — `uv run ges2t run`, and the optional systemd user unit for autostart,
         shipped as a ready-to-copy file at `docs/setup/ges2t.service` and explained in
         `docs/setup/autostart.md`.
      9. **Use it** — the `toggle` / `ptt` / `cancel` / `status` flows, plus the two behaviours
         worth knowing: a trailing space is appended per utterance (ADR 0012), and silent
         input types nothing.
      10. **Configuration** — a full reference for `~/.config/ges2t/config.toml` **split
          explicitly into the generic layer and engine-specific blocks**, documenting
          `transcriber.engine` as the engine selector and `[engine.whistle]` with `language`,
          `window_seconds` (default 5) and `chunk_seconds` (default 1), including the ADR 0011
          warning that raising `window_seconds` above ~6 s costs latency (ADR 0009).
      11. **Troubleshooting** — start with `uv run ges2t doctor`, then the specific failures
          this project actually has: invisible tray, `ydotoold` unreachable, nothing typed,
          service already running, stale socket, model missing, corrupt model, unknown config
          key, and a dictation that exceeds the batch limit (use streaming).
      12. **Architecture and decisions** — a short map of the module layout and an index
          linking every ADR in `docs/adr/`.
      13. **Known limitations** — the closed-source engine binary and what that means
          (ADR 0006), no post-processing (ADR 0012), mixed-language detection instability
          measured per-pass (ADR 0011), batch-path 30 s ceiling (ADR 0005), and that only
          KDE/Arch/Wayland is verified (ADR 0002).
- [ ] `docs/setup/ges2t.service` is a **valid systemd user unit** running `ges2t run`, with a
      short comment explaining the `WorkingDirectory`/venv path the user must adjust. It is
      **documentation only** — nothing installs it automatically (ADR 0015).
- [ ] `docs/setup/autostart.md` explains enabling that unit, checking it with `systemctl --user
      status`, and reading its logs.
- [ ] **The documentation matches `ges2t doctor` exactly**: every `FAIL` check in
      `src/ges2t/doctor.py` has a corresponding README or setup-guide section. Verify by listing
      the doctor checks and ticking each one off; record that comparison in `001-comment.md`.
- [ ] **Verification by a fresh reader:** the ticket is not done until someone can follow the
      README from a clean state. Record in `001-comment.md` the exact sequence performed —
      creating the venv, running `ges2t setup`, enabling `ydotoold`, binding the key, dictating
      — and note every place where a step was wrong, missing, or in the wrong order, fixing the
      README as you go.
- [ ] No documentation claims an unverified ability. Where something is unverified (e.g. other
      compositors), say so rather than implying support.

- [ ] `uv run mypy src/ges2t` exits 0: every definition fully annotated and every
      signature interface-typed — ADR 0019.

## Notes

- Do not invent a packaging story. There is no AUR package, no AppImage, and no installer by
  design (ADR 0000); the README should say so early so a reader does not look for one.
- Do not document clipboard-paste as a fallback, because it is not one (ADR 0003).
- Do not document post-processing knobs; there are none (ADR 0000, ADR 0012).
- The config reference must show the **generic vs engine-specific split** explicitly — that
  separation was an explicit user requirement (ADR 0009), and a reader who thinks
  `window_seconds` is a generic key has been misled.
- This ticket also inherits the KDE-binding and autostart work previously listed separately:
  there is deliberately one writer for the user-facing story, so the README and the setup
  guide cannot contradict each other.

## Related tickets

- depends on: ../../core-dictation-pipeline/cli-surface-and-commands/ticket.md,
  ../../core-dictation-pipeline/doctor-command/ticket.md,
  ../../core-dictation-pipeline/dictation-pipeline-batch/ticket.md,
  ../streaming-wiring-and-e2e/ticket.md, ../tray-status-ui/ticket.md,
  ../notifications-and-cancel/ticket.md
- blocks: none — this is the final ticket of the project
- related ADRs: ../../../adr/0000-project-scope-and-non-goals.md,
  ../../../adr/0003-text-injection-via-ydotool.md,
  ../../../adr/0004-global-hotkey-via-cli-and-service.md,
  ../../../adr/0009-configuration-toml.md,
  ../../../adr/0013-tray-via-pystray-appindicator.md,
  ../../../adr/0016-cli-surface.md, ../../../adr/0017-model-acquisition-via-setup.md
