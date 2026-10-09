# Project scope and non-goals for v1

- **Status:** accepted
- **Date:** 2026-10-09

## Context

ges2t ("good enough speech 2 text") is a minimal, fully offline speech-to-text utility for
Linux. The user wants a tool that transcribes speech and injects the resulting text into
whatever text field or terminal currently has focus, running a local speech-to-text model
with no cloud dependency. The user's own machine is an Arch Linux box running KDE Plasma 6
on Wayland, and that machine is the reference target: v1 exists to be usable as a daily
driver there.

The user was asked to define "finished" for the whole project. The answer: a clean,
documented, extendable MVP that the user actually uses every day. Distribution to third
parties on arbitrary systems is explicitly *not* part of v1.

## Decision

The v1 deliverable is **"usable as the user's daily driver on their own KDE/Wayland/Arch
machine"**.

**In scope for v1:**

- A long-running local service with a system tray icon (idle / recording / transcribing).
- A user-configurable global hotkey, defaulting to press-to-talk, with toggle also supported.
- Microphone capture, local transcription with the Cactus Whistle model, and text injection
  into the focused window.
- Both **batch** and **streaming** transcription paths, because streaming is a v1 goal.
- A documented one-time setup step for model acquisition and for the `ydotool` permission
  prerequisites.
- A `doctor` command that explains a broken setup rather than failing silently.
- Unit tests for everything that can be tested without a real mic, a real compositor, or a
  real engine (see ADR 0014).

**Explicitly out of scope for v1:**

- Packaging, distribution, or installability for strangers: no AUR package, no AppImage, no
  Flatpak, no Windows/macOS support.
- A GUI settings window. Configuration is a TOML file (ADR 0009).
- Any text post-processing: no punctuation restoration, no casing fixes, no filler-word
  removal, no translation. The engine's output is injected verbatim (ADR 0012).
- Fine-tuning or training a model.
- A second speech-to-text engine. The interface must *allow* one (ADR 0010), but v1 ships
  Whistle only.
- CI. Tests run locally (ADR 0014).
- Clipboard-paste injection. It is a plausible future alternative but is not a v1 fallback
  (ADR 0003).

## Consequences

- We optimise for one machine and one compositor. Portability work is deferred, not
  designed around.
- "Runs on my box" is the acceptance test for the epic, which keeps the ticket count small.
- Because distribution is out of scope, documented system prerequisites (three Arch
  packages for the tray, `ydotool` and its uinput permission, ADR 0013 and ADR 0003) are an
  acceptable substitute for packaging them.
- The abstraction requirement ("the model runtime should be abstracted away so the project
  benefits from later advancements") is honoured through interfaces (ADR 0010), not through
  shipping a second engine.
