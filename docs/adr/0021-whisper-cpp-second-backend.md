# 0021 — whisper.cpp as a second, in-process, batch-only transcriber backend

- **Status:** proposed (needs the user's explicit sign-off)
- **Date:** 2026-10-10
- **Related:** 0005, 0006, 0010, 0011, 0016, 0017, 0018, 0019, 0020
- **Updates:** 0016 (the CLI surface) and 0017 (model acquisition) — recorded here rather than as
  separate amendments
- **Evidence (measured on this machine):**
  - [whisper.cpp **provider** discovery](../project/research/whisper-cpp-provider/REPORT.md) — accuracy,
    speed, model sizes, Vulkan, integration fit
  - [whisper.cpp **streaming** discovery](../project/research/whisper-cpp-streaming/REPORT.md) — why
    batch-only
  - [`-nt` decode addendum](../project/research/whisper-cpp-provider/004-addendum-nt-decode.md) — the
    flag the accuracy headline depends on

## Context

The user's obstacle to daily-driving ges2t is **accuracy**, and the provider research established that
the dominant residual errors are **model capacity** — the risk ADR 0005 accepted for the 16 MB Whistle
model — not signal processing. A noise filter was measured and **not** justified. ADR 0006 already
names whisper.cpp as the escape hatch; this ADR takes it.

## Why — the evidence behind each choice

### Why whisper.cpp at all
On the user's own 25 s recording, `medium q5_0` scores **word_diff 0.062 against Whistle's 0.281
(batch) / 0.391 (streaming)** — roughly **4.5× fewer errors** with `-nt` (~1.8× on the CLI default).
Its residual errors are essentially one real word (`river`→`rivers`) plus number/spelling forms, and
the model is **MIT end-to-end**, so it is also strictly more auditable than ADR 0006's closed engine.
See [provider REPORT §3, §4, §14](../project/research/whisper-cpp-provider/REPORT.md).

### Why **batch-only**
The streaming discovery replayed recorded utterances through whisper in rolling/windowed passes and
measured the result. Whisper's partial-window output is **not stable enough** for ges2t's
commit-cadence loop: **boundary duplication** at window seams (`country country`), the flush tail doing
~81% of the committing so the agreement rule barely protects the text, **cut-off hallucination**
(`north of the country` for `northern valley`, committed), and **silence hallucination** — all three
models emit and commit text on digital silence (`Thank you.` / `you you`) — with **2.3–4.8× worse
WER** than the same model in batch. Hopping (non-overlapping) windows were measured too and are
**worse**: slicing the audio at all is what costs accuracy (whole clip 0.062 → 10 s chunks 0.141 → 5 s
chunks 0.266), so the good end of that dial is simply "batch".
See [streaming REPORT](../project/research/whisper-cpp-streaming/REPORT.md).
**Consequence accepted here:** choosing whisper means **no text until the utterance ends.**

### Why **`medium q5_0` on GPU, `small q5_0` on CPU-only**
The size sweep found `medium q5_0` the **most accurate model in every condition tested** (car 0.062,
quiet-short 0.296, quiet-long 0.111) and, on Vulkan, the best accuracy-per-second — 1.72 s for the
25 s clip, Pareto-dominating every CPU configuration except `tiny`. **CPU, however, is ~6× real time**
(medium ~24 s for a 30 s clip), so on a CPU-only machine the sensible default is the cheaper
`small q5_0` (WER 0.141, still better than Whistle's 0.281). Quantisation is accuracy-invariant between
`q5_0`/`q8_0` and **`f16` is never better** (only slower and larger), so `q5_0` is chosen for size.
See [provider REPORT §3, §4, §8, §9](../project/research/whisper-cpp-provider/REPORT.md).

### Why **`-nt` is pinned**
`-nt` is a **decode** switch, not an output-format switch: the same model on the same clip scores
**0.0625 with `-nt` and 0.1562 without** it (`knocked out power` → `no doubt powered`). Because
ges2t types text and does not need timestamps, the accurate decode is pinned **in code**, never
inherited from the CLI default. See
[the `-nt` addendum](../project/research/whisper-cpp-provider/004-addendum-nt-decode.md).

### Why **in-process, resident**
The user requires **no process spawn and no model reload per utterance**. A per-call `whisper-cli`
subprocess would reload ~540 MB every time (a measured ~0.8 s fixed cost). ges2t already loads a
native library in-process for Whistle (`libneedle3.so` via `ctypes`), so the same shape is reused and
the model is loaded **once at service start**.

## Decision

1. **whisper.cpp is a second backend**, selected by `transcriber.backend = "whisper_cpp"`; the
   **default backend remains `needle`**, so the shipped live/incremental typing is unchanged.
2. **In-process integration**: `ctypes` over `libwhisper.so` and the `libggml*.so` libraries, with the
   model **loaded once at service start and kept resident**. No per-utterance subprocess, no reload.
3. **Batch-only**: `supports_streaming = False`. Selecting whisper trades away live/incremental typing
   (ADR 0011) for accuracy.
4. **Model selection**: `medium-q5_0` by default when a Vulkan device is available, `small-q5_0`
   otherwise; overridable in config. Names are `<size>-<quant>`; only upstream-published quants are
   supported (`q8_0` for every size, `q5_0` for `medium`/`large-v3-turbo`). **Never `f16`.**
5. **Decode pinned**: `-nt` (no timestamps) and greedy — the measured configuration.
6. **Acquisition and setup are backend- and model-parameterised** (the only networked path,
   ADR 0018). `ges2t setup [backend] [model]`: with **no arguments** it sets up `needle` and its
   default model, `whistle`; `ges2t setup whisper.cpp <model>` **builds** whisper.cpp from a pinned
   revision and fetches the model from Hugging Face, sha256 + size pinned. The **model argument
   applies to either engine** — the needle model is not hardcoded to `whistle`.
7. **Setup requires, and never installs, its prerequisites**: it checks for the C++ toolchain,
   `cmake`, and — for the GPU build — the Vulkan dev packages, and fails with the exact `pacman` line.
   No `sudo`, no package installs, no fetching of build tooling. No Vulkan runtime → **CPU-only build**.
8. **Startup validation is strict**: with `transcriber.backend = "whisper_cpp"`, a config is rejected
   at startup when `streaming.enabled = true`, when the configured model is not acquired, or when the
   whisper library is not built. **Model-vs-GPU sizing is deliberately not checked.**
9. **Licence**: whisper.cpp and the Whisper weights are MIT.
10. **The needle/Whistle model is selectable too**, with `whistle` as its default; the **needle
    backend remains the shipped default** because it keeps live/incremental typing, which whisper
    cannot.

## Consequences

- The default experience is **unchanged**; whisper is opt-in.
- Choosing whisper means **no text until the utterance ends**, and the first dictation after service
  start is preceded by a one-time model load.
- ~0.8–1 GB resident for `medium-q5_0` while the service runs (less for `small-q5_0`); on the Vulkan
  path the weights live in GPU-visible memory.
- A crash inside `libwhisper.so` takes down the service process — the same exposure ADR 0006 already
  accepts for Needle.
- The repo gains **no** runtime dependency and ships **no** binaries or weights; the build and the
  models live outside it.
- ADR 0011's streaming behaviour is untouched for the default backend; it simply does not apply to
  whisper.

## Escape hatch

Unchanged and still open: if whisper.cpp disappoints, the backend is one config value away from
`needle`, and the whole integration sits behind the ADR 0010 `Transcriber` Protocol.
