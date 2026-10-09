# Transcriber interface owns model acquisition; interfaces and data classes everywhere

- **Status:** accepted
- **Date:** 2026-10-09

## Context

The project's founding requirement is that **"the model runtime should be abstracted away so
that the project benefits from later advancements."** The user expanded on this while
answering the interview:

- Both **streaming and batch** must exist on the interface, because not every runtime will
  support streaming; where streaming is supported it should be the default (ADR 0011).
- The interface must include an explicit **load-model** entry point.
- The interface must **own model downloading**.
- A second engine is *not* required for v1, but the design should "almost always depend on
  interfaces and return classes where it makes sense, because it makes testing easy".
- Post-processing is out of scope for v1.

This matters beyond aesthetics: the closed-engine risk (ADR 0006) is only survivable because
the swap point is narrow, and the whole test strategy (ADR 0014) depends on being able to
substitute fakes for the engine, the microphone, and `/dev/uinput` (ADR 0008, ADR 0003).

## Decision

Define three `typing.Protocol` interfaces with small, explicit data classes as their
currency. All of them live in a dedicated module (`ges2t/interfaces.py` or equivalent) that
**imports nothing from the concrete implementations**, so importing it never pulls in the
engine, PipeWire, or DBus.

**`AudioBuffer`** (frozen dataclass): `samples` (float32 mono array), `sample_rate`. Sample
rate is carried explicitly and validated as 16 kHz at the transcriber boundary.

**`TranscriptionSegment`** / **`TranscriptionResult`** (dataclasses): the text, the detected
`language`, whether it is `is_final`, and — where the engine provides them — the engine's own
timings (`ttft_ms`, `decode_tps`). Engine-reported timings are passed through, not
recomputed (ADR 0005).

**`Transcriber`** (Protocol):

- `name` — identifier matching the `transcriber.backend` config value.
- `supports_streaming: bool` — declared capability, not a runtime probe.
- `ensure_model(progress=None) -> Path` — **acquisition**: download if missing, verify the
  pinned checksum and size, return the local path. Idempotent and safe to call repeatedly.
- `load() -> None` — load the model into memory (the "load model" entry point the user
  asked for). Separate from acquisition, so the service can warm up without touching the
  network.
- `transcribe(buffer: AudioBuffer) -> TranscriptionResult` — the batch path.
- `stream(chunks: Iterable[AudioBuffer]) -> Iterator[TranscriptionSegment]` — the streaming
  path, yielding non-final partials and a final segment.
- `close() -> None` — release the process-global model (ADR 0005).

**`AudioSource`** (Protocol): start/stop capture and yield `AudioBuffer`s (ADR 0008).

**`Injector`** (Protocol): `inject(text: str) -> None` (ADR 0003).

Rules:

- Callers depend on the **Protocol**, never on a concrete class. Concrete classes are
  selected in exactly one place — a small factory that maps config values to
  implementations.
- **Data in, data out.** No interface method takes or returns a raw `dict`; engine JSON is
  parsed inside the adapter and returned as a typed class.
- `supports_streaming = False` must be a legitimate, first-class state. Callers branch on the
  capability flag; they do not catch an exception to discover it.
- Adapters are the **only** modules allowed to import a backend library. This is the rule
  that makes the wholesale engine swap (ADR 0006) a one-module change.

## Consequences

- Tests inject fakes for all three interfaces and exercise the service state machine with
  **no microphone, no `/dev/uinput`, and no model** (ADR 0014).
- `ensure_model()` is the single place the network is touched anywhere in the codebase
  (ADR 0018), which makes the "no network at runtime" audit a one-function read.
- Because acquisition and loading are separate calls, `ges2t setup` can download without
  loading, and `ges2t run` can load without downloading — and a missing model produces an
  actionable "run `ges2t setup`" error instead of an unexpected download mid-dictation.
- The protocol surface is wider than v1's single implementation strictly needs. That is the
  deliberate price of the project's core requirement; it is kept cheap by making the data
  classes small and by forbidding engine types from appearing in signatures.
- If a future backend cannot honour a synchronous `transcribe()` (async-only runtimes
  exist), that is an interface-breaking change and needs a superseding ADR — not an
  ad-hoc async method bolted onto one adapter.
