# Integrate Whistle through the official `cactus-needle` package

- **Status:** accepted
- **Date:** 2026-10-09

## Context

Two integration paths were on the table for driving the Needle 3 engine:

- **(a)** the official **`cactus-needle`** package from PyPI — pure Python, Apache-2.0,
  `python_requires >=3.9`, whose only declared runtime dependency is `huggingface_hub`. It
  fetches the engine wheel from Hugging Face on first use. **Measured correction
  (2026-10-09):** it calls the engine `CDLL` **in-process** — there is *no* subprocess hop
  for transcription. The length-prefixed-JSON worker (`needle/_worker.py`) is created only
  by `needle.Needle` for **fine-tuned** weights, never by `Whistle`/`needle.stream`. An
  earlier draft of this ADR claimed a worker subprocess for all inference; that was wrong.
  Measured package overhead versus calling `libneedle3.so` directly via `ctypes` on the same
  preloaded buffer: 5 s clip 0.19 s vs 0.18 s, 30 s clip 2.02 s vs 2.03 s — **no measurable
  difference**.
- **(b)** our own **`ctypes`** wrapper over `libneedle3.so`, extracted from the engine wheel.
  The research pass demonstrated this works in about twenty lines with no crashes, because
  `needle.h` is `extern "C"` and the shared library exports exactly eleven `needle_*`
  symbols.

The trade is concrete: (a) is less code but leaves the auto-download step to us to control;
(b) is more code but gives us explicit control over loading. After measurement removed the
process-hop objection to (a), the remaining difference is small and (a) wins on simplicity.

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
- **Keep the engine call in-process.** Transcription runs on the `CDLL` in the service
  process. The adapter must never route a dictation through `needle.Needle` or any API that
  spawns the fine-tune worker — that path is for tuned weights only and would add a process
  hop we have no reason to pay for.
- **Pinning is real, not aspirational.** Because the package auto-fetches the engine on
  first use, ges2t both sets `NEEDLE_WHISTLE_WEIGHTS` and `NEEDLE3_LIB_PATH` to the
  artifacts its own `ges2t setup` downloaded and verified (ADR 0017), and records the engine
  version and the extracted `libneedle.so` sha256 it is running against.
- **Buffering/encoding is the adapter's problem.** The engine wants 16 kHz float32 mono and
  the package accepts a WAV path, `bytes` (**raw little-endian float32** — int16 bytes are
  silently interpreted as garbage, which is a real trap), `array('f')`, any object exposing
  `.tobytes()` + `.dtype`, or an iterable of floats. The adapter normalises once, and the
  rest of the app only ever sees the `Transcriber` interface (ADR 0010). Do **not** pass a
  WAV path: re-parsing costs 0.036 s for 5 s and 0.218 s for 30 s of audio, all of it pure
  waste against our sub-1-second framework budget (ADR 0001).

## Consequences

- Least code and an upstream-maintained loading path, and — as measured — **no performance
  penalty** for the package versus a hand-written `ctypes` wrapper. The earlier expectation
  of a subprocess hop was wrong; no framework-latency budget is consumed by the choice of
  integration path.
- We partly undo the package's convenience by pointing it at our own artifacts with
  `NEEDLE_WHISTLE_WEIGHTS`/`NEEDLE3_LIB_PATH`. That is a deliberate trade: it makes the
  pinning in ADR 0017 real instead of nominal, at the cost of tracking the engine wheel URL
  and the extracted `.so` hash ourselves.
- The package is still a dependency we do not control, and its fetch path is still reachable
  from `Whistle()` if our env overrides are missing or wrong. The adapter must therefore
  **assert at load time** that the engine and weights it loaded are the ones we pinned, and
  fail loudly otherwise rather than silently fetching something else.
- `numpy` is **not** required by the package for transcription. We may still use it for
  audio buffering, but it is not a load-bearing dependency of the integration and must not
  become a reason the adapter cannot be tested.
- Swapping to path (b) later is a contained, single-ticket change with no interface churn,
  and now with no performance argument either way — it would be motivated purely by wanting
  to drop the package dependency.
- This choice is independent of the closed-engine risk (ADR 0006); both are consequences of
  the same upstream dependency and both end at the same escape hatch.
