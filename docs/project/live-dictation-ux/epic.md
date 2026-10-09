# Epic: Live dictation UX

- **Priority:** P1
- **Status:** to do
- **Epic owner:** orchestrator

## Goal

Turn the working command-line pipeline into something the user actually wants to use every
day: **live streaming transcription** as the default path, a **tray icon** that shows what
ges2t is doing, **notifications** when something goes wrong, a **cancel** action, and
**documentation** that takes a fresh machine from zero to dictating.

## Why it matters

The user's stated v1 bar is "I use it as my daily driver". A tool that only works when two
terminals are open, gives no indication that it is recording, and silently swallows errors
is not a daily driver. Each piece here removes a specific reason to stop using it:

- **Streaming** is an explicit v1 goal, not a future nicety, and it is what makes longer
  dictation possible at all given the engine's 30-second per-call ceiling (ADR 0005, ADR
  0011).
- **The tray** is the only always-visible signal of state; without it, a stuck recording is
  invisible until the user notices text not appearing.
- **Notifications** are what turn a silent failure (dead engine, refused injection) into
  something the user can act on.
- **Documentation** is load-bearing rather than decorative here, because packaging is out of
  scope (ADR 0000): the Arch packages, the `ydotoold` setup, the `/dev/uinput` permission
  and the KDE key binding are all manual steps the user must be told about exactly once.

## Deliverable

A dictating tool with live partial transcription, a reliable tray state display, actionable
error notifications, a bindable cancel action, and a README that a reader can follow from a
fresh Arch + Plasma 6 install to a working dictation.

## Acceptance criteria

- [ ] `uv run pytest` passes, still fully headless, including the streaming-window tests and
      the tray backend assertion — ADR 0014.
- [ ] With `streaming.enabled = true` (the default) and a backend that supports it, partial
      text is produced **during** a long utterance rather than only after release; with
      `streaming.enabled = false`, the batch path is used and behaviour is unchanged from
      the core epic — ADR 0011.
- [ ] A dictation longer than the engine's 30 s per-call ceiling completes successfully on
      the streaming path, and the seams between windows do not duplicate or drop words —
      verified by transcript diff against a one-shot reference, with the result reported.
- [ ] The tray icon is visible on Plasma 6.7.5 and shows `idle` → `recording` →
      `transcribing` → `idle` through a real dictation; the pystray backend is asserted to be
      `appindicator`, and ges2t refuses to start a tray it cannot display rather than
      silently falling back to the invisible XEmbed backend — ADR 0013.
- [ ] A failing engine and a failing injection each produce a desktop notification naming
      the problem and the command that fixes it.
- [ ] `ges2t cancel` aborts an in-progress recording, types nothing, and returns the tray to
      `idle`; it is documented as bindable to a key — ADR 0004.
- [ ] Following only the README, a reader can install the Arch packages, enable `ydotoold`,
      grant `/dev/uinput` access, run `ges2t setup`, bind a key in System Settings, and
      dictate. The manual steps are enumerated and each one is checked by `ges2t doctor`.
- [ ] Every ticket's `docs/project/README.md` row is `done`.

## Out of scope for this epic

A GUI settings window, punctuation/casing post-processing, clipboard-paste injection and any
automatic fallback, a KDE-native `KGlobalAccel` hotkey implementation, a second speech
engine, packaging for other machines, and CI — all per ADR 0000.

## Decisions this epic implements

- ADR 0011 — streaming is a v1 feature
- ADR 0013 — tray via pystray's AppIndicator backend
- ADR 0004 — hotkey via compositor-bound commands
- ADR 0012 — verbatim output plus trailing space
- ADR 0016 — CLI surface (the commands being bound and diagnosed)
- ADR 0006 — closed-engine risk (the escape hatch stays open)
- ADR 0019 — full type annotations, interface-typed signatures, classes over tuples
