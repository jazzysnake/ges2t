# Speech-to-text backend: Cactus Whistle on the Needle 3 engine

- **Status:** accepted
- **Date:** 2026-10-09

## Context

The user had already tested a small Cactus Compute model and found it "good enough", and
wanted it as the starting point. A research pass was run to establish exactly what the
artifact is, and produced a correction plus a set of hard numbers.

**Naming correction:** the model is Cactus Compute's **Whistle**, not "Whistly". There is no
product called Whistly. Its runtime is Cactus's own **Needle 3** engine — not `whisper.cpp`,
`ggml`, `onnx`, or `tflite`.

Verified facts (all from the research pass on this machine, 2026-10-09):

| Fact | Value |
|---|---|
| Artifact | `whistle.cact`, one file, **16,919,407 bytes** |
| Source | `https://huggingface.co/Cactus-Compute/whistle/resolve/main/whistle.cact` (stable; `HEAD` `x-linked-etag` equals the sha256) |
| sha256 | `b6e02f048568ac5d01a2042556c658061e699acbc0aa2a1439f52f3d461dffeb` |
| Format | Cactus `.cact` container, Cactus Quants at 2.125 bits/weight, ~55 M params |
| Weights license | **Apache-2.0** |
| Languages | `en, de, fr, es, it, nl, pl`, auto-detected |
| Measured latency | 11.0 s clip → **1.68 s** wall, RTF **0.15**, `ttft_ms` 1547, 199 decode tok/s |
| Measured footprint | load 26 ms, **69 MB** peak RSS, CPU-only |
| Audio contract | 16 kHz mono **float32 in [-1, 1]**, **≤30 s per call**, int16 rejected, **no VAD** |
| Threading | Model is **process-global and not thread-safe** ("one process-global model per kind") |

Accuracy is competitive with Whisper base at roughly one-ninth the size (published WER:
LS test-clean 4.31, test-other 10.49, TED-LIUM 7.61).

One published number is **not** trustworthy: the model card advertises 11.1 ms TTFT on a
10 s clip, but the same field measured **1,547 ms** here. Do not plan against the card's
figure. Likewise, the engine's "streaming" cost was measured *rising* with buffer length —
see ADR 0011, which handles that explicitly.

## Decision

The v1 speech-to-text backend is **Cactus Whistle running on the Cactus Needle 3 engine**.

- Pin the model by **sha256 and byte size**, not by URL alone. `ges2t setup` downloads and
  verifies (ADR 0017).
- Treat the audio contract as a hard interface: the audio layer must hand the transcriber
  **16 kHz, mono, float32 in [-1, 1]** (ADR 0008 satisfies this natively).
- Respect the **30 s per-call ceiling** in the batch path; the streaming path is what lifts
  the length limit (ADR 0011).
- Respect the **process-global, non-thread-safe** model: exactly one component owns the
  engine, and it is owned by the service process (ADR 0015).
- Never rely on the engine for silence detection. If the utterance is silent the engine
  returns an empty string, which is a valid outcome and must not be injected as garbage.

## Consequences

- We depend on a 16 MB model backed by a young engine, in a format with no third-party
  tooling. The escape hatch is Whisper-family `ggml`/`gguf` (whisper.cpp is MIT, base.en
  ~148 MB) or a CTranslate2/faster-whisper path; both fit the same `Transcriber` interface
  (ADR 0010).
- The `<30 s` ceiling and the absence of VAD become explicit design inputs, not surprises.
- Language is auto-detected by the engine; v1 does not expose a language setting beyond
  passing through what the engine returns.
- The engine's per-call JSON (including `ttft_ms`, `decode_tps`, and optionally word
  timestamps) is the source of measurement truth. Where the interface exposes timing, it
  should carry these through rather than re-inventing them.
