# Python as the implementation language for v1

- **Status:** accepted
- **Date:** 2026-10-09

## Context

The machine has `python3` **3.14.7**, `cargo`/`rustc` 1.78, and `node` available. The Cactus
Needle 3 engine ships a C ABI and a static/shared library, so several languages could drive
it. The user was asked which language to use and chose Python for the MVP, on the grounds of
fastest iteration, with an explicit willingness to rewrite if performance becomes a problem.

Measured facts that make Python safe here:

- The engine does the heavy lifting in C. On this machine a 11 s clip transcribes in
  **1.68 s** (RTF 0.15), with **69 MB** peak RSS and 26 ms load time (research pass,
  2026-10-09). Python only orchestrates.
- The official `cactus-needle` Python package is pure Python and is the chosen integration
  path (ADR 0007).
- Python 3.14 wheel availability was checked for the packages we depend on: `numpy` 2.5.3,
  `sounddevice` 0.5.6, `pystray` 0.19.5, and `pillow` all resolve with 3.14 wheels.
- `uv` 0.12.23 is installed, so environment management is a solved problem.

## Decision

Implement ges2t in **Python 3.12+ (developed and verified on the machine's 3.14.7)**,
packaged as a single `uv`-managed project with a `src/ges2t/` layout and a console script
entry point (`ges2t`).

Rationale: the transcription model, not our orchestration code, dominates runtime; the
project's stated v1 bar is a daily-driver MVP on one machine; and Python makes the
audio/DBus/injection glue and the mock-based test suite cheap to write.

**Latency budget, restated in the terms the user asked for:** the framework's own overhead
— everything ges2t does that is *not* model processing — must be **under 1 second**
end-to-end per dictation. Model processing time is explicitly not held to a fixed budget,
since it depends on the model and the hardware.

## Consequences

- Any future performance problem is a *hotspot* problem (audio buffering, injection
  throughput, IPC), not a language problem. If we ever need to move, the Transcriber
  interface (ADR 0010) and the CLI/service split (ADR 0004) are the seams; the engine is
  already a C library.
- We inherit Python's packaging quirks. One is load-bearing: the GTK tray path needs system
  `gi`, so the venv is created with `--system-site-packages` (ADR 0013).
- Developers must not reach for compiled dependencies casually, because Python 3.14 wheels
  are still thin. Prefer stdlib and subprocesses (ADR 0008).
- A `python_requires` of `>=3.12` is set, and the project pins `cactus-needle` (ADR 0007).
