# Integrate Whistle through the official `cactus-needle` package

- **Status:** accepted
- **Date:** 2026-10-09

## Context

Two integration paths were on the table for driving the Needle 3 engine:

- **(a)** the official **`cactus-needle`** package from PyPI — pure Python, Apache-2.0,
  `python_requires >=3.9`, whose only declared runtime dependency is `huggingface_hub`. It
  fetches the engine wheel from Hugging Face on first use and runs inference in a **worker
  subprocess** (`needle/_worker.py`, length-prefixed JSON over pipes).
- **(b)** our own **`ctypes`** wrapper over `libneedle3.so`, extracted from the engine wheel.
  The research pass demonstrated this works in about twenty lines with no crashes, because
  `needle.h` is `extern "C"` and the shared library exports exactly eleven `needle_*`
  symbols.

The trade is concrete: (a) is less code but adds a process hop and an auto-download step we
do not control; (b) is more code but removes both.

The user was offered "(a) now, (b) as a fast follow" and chose **(a)**.

## Decision

Integrate via the official **`cactus-needle`** package, pinned to an exact version, inside a
single `WhistleTranscriber` adapter.

Required behaviour of the adapter:

- **Pin the version** in `pyproject.toml`. The engine wheel version is a separate pin inside
  that package; record which engine version we are on.
- **Set `NEEDLE_TELEMETRY=0` and `DO_NOT_TRACK=1`** unconditionally before the package is
  imported, at process start. Telemetry being opt-out means it is on by default if we do
  nothing — so we do something, in one place, and never rely on the user's shell.
- **Do not depend on the package's auto-download as the acquisition mechanism.** Model
  acquisition is owned by `ges2t setup` (ADR 0017). The adapter must be able to run against
  an already-present, already-verified model, and must fail with an actionable message
  rather than reaching for the network mid-dictation (ADR 0018).
- **Isolate the worker subprocess.** The package's worker is an implementation detail of the
  adapter. If a future ticket replaces (a) with (b), no other module changes.
- **Buffering/encoding is the adapter's problem.** The engine wants 16 kHz float32 mono and
  the package may want a path, bytes, or an array — the adapter normalises, and the rest of
  the app only ever sees the `Transcriber` interface (ADR 0010).

## Consequences

- Least code and an upstream-maintained loading path, at the cost of a **subprocess hop**.
  That hop must be measured against the framework's sub-1-second budget (ADR 0001) and its
  cost reported in the ticket, not assumed to be negligible.
- Worker-subprocess crash semantics differ from an in-process library: a dead worker must be
  detected and surfaced, not turned into a hang. The adapter owns restart/error policy.
- Because the package itself may download on first use, ges2t must ensure the *first* use
  happens during `ges2t setup`, under controlled network conditions, and that the cache
  paths involved are known and documented (ADR 0017).
- Swapping to path (b) later is a contained, single-ticket change with no interface churn —
  this is the main reason to prefer (a) now despite the hop.
- This choice is independent of the closed-engine risk (ADR 0006); both are consequences of
  the same upstream dependency and both end at the same escape hatch.
