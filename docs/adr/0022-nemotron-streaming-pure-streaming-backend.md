# 0022 — Nemotron 3.5 streaming (NeMo-Speech.cpp) as an opt-in, **pure-streaming** transcriber backend

- **Status:** proposed (awaiting user sign-off)
- **Date:** 2026-10-14
- **Related:** 0005, 0006, 0010, 0011, 0015, 0016, 0017, 0018, 0020, 0021
- **Relationship to 0011:** **engine-specific; ADR 0011 is NOT amended** (see below)
- **Evidence (measured on this machine):**
  - [engine / Vulkan / incremental survey](../project/research/parakeet-v3-engine-landscape/REPORT.md) — ticket 53
  - [Nemotron 3.5 streaming measurement](../project/research/nemotron-3-5-asr-streaming-measurement/REPORT.md) — ticket 55

## Context

The user's obstacle is **visibility**: the `whisper_cpp` backend (ADR 0021) is accurate but
**batch-only**, so text appears only when the utterance ends. The Epic 5 survey found Parakeet v3
batch-only in every engine and selected the **`NVIDIA/NeMo-Speech.cpp`** engine (Apache-2.0,
C++/`llama.cpp`/ggml). The user chose the engine's **cache-aware streaming** sibling,
`nvidia/nemotron-3.5-asr-streaming-0.6b` (OpenMDW-1.1).

Ticket 55 measured it here: the **Vulkan** path engages (backend line + ~952 MB VRAM + host RSS
~197 MB vs CPU ~1,064 MB; ~41–45 ms per 160 ms chunk vs ~72–88 ms), and — the core property — the
committed text is **append-only**: every interim is a strict string-prefix extension of the previous
one (1.0 across 20 runs and 5 chunk sizes), with **0** rewrites, no seam duplication, no cut-off
hallucination, and no silence hallucination. The costs are documented: ~950 MB VRAM, weaker English
accuracy than whisper `medium` on noisy speech (car WER 0.312 vs 0.062 with `-nt`), and Hungarian
accuracy that is **published-FLEURS only** (≈28.7–33.4) and not measurable on this machine.

## Decision

1. Add **`nemo_speech`** as an **opt-in** `transcriber.engine`; the **default stays `whistle`**, and
   `whisper_cpp` is unchanged.
2. **Pure streaming.** The engine's **append-only interim stream is the commit source**. For this
   engine there is **no 5 s sliding window, no two-pass agreement rule, and no growing-buffer
   re-decode** — the engine has already done the work the window existed to approximate.
3. **ADR 0011 is not amended and not edited.** It remains in force, unchanged, for the Whistle path
   (see "Relationship to ADR 0011").
4. The **tickets 32–34 contract is preserved exactly**: committed text is append-only and never
   revised; **only** committed text is injected; the pending/trailing tail is display-only; the
   trailing space is emitted once.
5. **Commit at word boundaries**: the adapter commits only the completed-word prefix of the engine's
   interim and holds any trailing partial token **pending**, never injected.
6. **In-process via `ctypes` over `libnemo_speech_asr_c`**, model resident from service start — the
   ADR 0021 shape. **No Python, no `torch`, no `numpy`.**
7. **Device:** Vulkan when the binary and driver provide it, CPU otherwise; **never a silent
   substitution** (ADR 0020). `doctor` reports the engine library, the model, the detected device, and
   the effective backend.
8. **Pins (ADR 0017):** engine archive sha256 `c8d0ed5b…` (v0.2.0 `linux-x86_64-vulkan`); model
   `nemotron-3.5-asr-streaming-0.6b.q8_0.gguf` sha256 `a5c435f2…`, size `741,548,352`, revision
   `1c8deae…`. `setup` **requires and never installs** prerequisites and never runs `sudo`.
9. **No runtime network** (ADR 0018): `setup` acquires and verifies the archive and the GGUF;
   inference is offline.
10. **Licences:** engine Apache-2.0 (ggml MIT); model **OpenMDW-1.1**. Attribution is recorded in the
    README.
11. **The mechanism is designed, not assumed.** The exact seam shape, the C-ABI interim delivery,
    threading, and the service lock are established by **ticket 56's code investigation**; this ADR
    fixes the **decision**, ticket 56 fixes the **mechanism**.

## Relationship to ADR 0011

ADR 0011 defines a sliding-window + two-pass commit cadence for a transcriber that **re-transcribes a
growing buffer** (the Needle/Whistle engine). This ADR **does not modify ADR 0011** and does not change
the Whistle path. It records a **separate, engine-scoped decision**: for a `Transcriber` whose engine
emits a **cache-aware, append-only interim stream** — a property ticket 55 measured, not inferred —
the window and the agreement rule are not used. Where the two would conflict **for this engine**, this
ADR governs; for every other engine, ADR 0011 governs unchanged. The user explicitly directed this
structure (a new engine-specific ADR rather than an amendment) on 2026-10-14.

## Known limits (to be stated in the docs)

- Vulkan needs ~950 MB of VRAM.
- CPU-only machines pay ~1 GB host RSS and roughly 2× the per-chunk latency.
- English accuracy is **weaker than whisper `medium`** on noisy real-device audio.
- Hungarian accuracy is **published-FLEURS only** and unmeasured here.
- Vulkan and CPU transcripts are **not byte-identical** (deterministic per device).

## Consequences

- A third backend behind the ADR 0010 seam; **no shipped default change**; ADR 0011 untouched.
- The repo gains **no** dependency, binary, weight, or audio file.
- The window cap and the de-duplication step are **absent** for this engine, removing the entire class
  of seam errors the whisper-streaming report documented.
- `window_seconds` is **irrelevant** for this engine; validation must say so rather than silently
  accept a value that has no effect.

## What would falsify this

If ticket 56 finds that the engine's interim stream cannot be carried into the committed-delta path
**without** a growing buffer or a second pass — e.g. if the C ABI delivers interims in a form that
forces re-decoding or cannot be consumed under the service lock — then the pure-streaming decision
would have to be revisited before any integration code is written.
