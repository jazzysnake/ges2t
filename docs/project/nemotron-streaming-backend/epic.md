# Epic: Nemotron 3.5 streaming backend (incremental text)

- **Priority:** P1
- **Status:** to do
- **Epic owner:** orchestrator
- **Research:** [../research/parakeet-v3-engine-landscape/REPORT.md](../research/parakeet-v3-engine-landscape/REPORT.md)
  (the engine/Vulkan/incremental survey that selected this path) · ticket 55's report (to be produced)
- **Supersedes:** [../parakeet-v3-backend/epic.md](../parakeet-v3-backend/epic.md) (Epic 5 — Parakeet v3,
  closed **obsolete** on the user's decision, 2026-10-14)
- **Decision:** *proposed after ticket 55; next free ADR number is 0022. Expected to also require an
  **ADR 0011 amendment** — see below.*

## Goal

Add **`nvidia/nemotron-3.5-asr-streaming-0.6b`** (OpenMDW-1.1) as a selectable, **fully offline**
`Transcriber` backend that **streams**: text is committed and typed **while the user is still
speaking**, which is exactly what the shipped `whisper_cpp` backend cannot do. It runs on the
**`NVIDIA/NeMo-Speech.cpp`** engine (Apache-2.0, C++/`llama.cpp`/ggml) — the engine ticket 53
selected — from the **official in-repo `q8_0` GGUF**, giving **Vulkan** on this PC's Radeon 780M
through ggml and requiring **no Python, no `torch`, no `numpy`**.

The **default engine stays `whistle`**, and both existing backends stay intact; this is a third,
opt-in engine.

## Why it matters

The user's daily driver is `whisper_cpp`. It is accurate but **batch-only** — no text until the
utterance ends — and that is the annoyance the user set out to fix. The Epic 5 discovery found that
Parakeet v3 cannot fix it (**batch-only in every engine**), but that the **cache-aware streaming
sibling** can: it maintains per-layer encoder caches and commits **append-only**, non-overlapping
frames, so unlike a growing-window re-decode there is no seam duplication to de-duplicate and no
window cap. The user chose this model over Parakeet v3 with the trade-off stated: Hungarian accuracy
is **broad-coverage** (FLEURS ≈ 29–33) versus v3's 15.72.

## Why this is not just "another backend"

ADR 0011's **sliding-window commit cadence** exists *because* Whistle re-transcribes a growing buffer.
A cache-aware streaming model removes that reason: it commits append-only tokens per chunk. So this
epic must:

- reuse ADR 0011's **contract** — committed is append-only and never revised, a typed watermark, the
  trailing space once (tickets 32–34);
- **retire the 5 s window** for this engine specifically, which is a change to ADR 0011's design and
  therefore needs an **ADR 0011 amendment presented in full for user sign-off** — not a quiet edit.

## Deliverable

An opt-in `nemo_speech` `Transcriber` engine that streams `nemotron-3.5-asr-streaming-0.6b` in-process
(ctypes over the C ABI, model resident from service start, the ADR 0021 shape), acquired through
`ges2t setup` with **sha256 + size pinning**, with `doctor` coverage, documentation, and ADR 0022
(+ the ADR 0011 amendment) accepted by the user. The default engine is unchanged.

## Acceptance criteria

- [ ] Ticket 55's report exists with **real, on-machine numbers**: per-chunk latency, RTF, peak RSS,
      a **Vulkan-vs-CPU** comparison (with proof the GPU is actually used, not silently falling back),
      and a **committed-delta stability** run proving append-only commits with no seam duplication,
      cut-off hallucination, or silence hallucination.
- [ ] `transcriber.engine` selects the streaming engine; the **default stays unchanged** and `whistle`
      and `whisper_cpp` behaviour is unchanged.
- [ ] The engine reports `supports_streaming = True` and the committed text is **append-only**; the
      ADR 0011 contract (committed-only injection, typed watermark, trailing space once) is preserved.
- [ ] Engine archive and model are pinned by **sha256 + byte size**; `ges2t setup` **requires and never
      installs** prerequisites and never runs `sudo`; **no runtime network** (ADR 0018).
- [ ] **No `numpy`, no `torch`, no `onnxruntime`** enters the project.
- [ ] `uv run pytest` and `uv run mypy src/ges2t tests` green; `doctor` reports the engine library,
      the model, and the Vulkan/GPU state when this engine is configured.
- [ ] The **Hungarian accuracy reality is documented** (published FLEURS vs the reference clips that
      actually exist), not glossed over.
- [ ] **ADR 0022 and the ADR 0011 amendment are presented in full for user sign-off** before the merge
      that carries them.
- [ ] Every ticket's `docs/project/README.md` row is `done` (or `obsolete` with the reason).

## Out of scope for this epic

- **Parakeet v3 itself** — obsoleted by the user's decision; its discovery report is retained as
  evidence only. See [../parakeet-v3-backend/epic.md](../parakeet-v3-backend/epic.md).
- Changing the **default** engine, or retiring either existing backend.
- Automatic fallback between engines (ADR 0020).
- Bundling any engine binary, model weight, or audio file in the repo.
- Text normalisation of the transcript (**project-wide** out of scope).
- Packaging/distribution (ADR 0000); installing system packages or running `sudo`.

## Decisions this epic may touch

- ADR 0010 — the `Transcriber` Protocol seam
- **ADR 0011 — streaming / sliding-window commit cadence (needs an amendment: the 5 s window does not
  apply to a cache-aware streaming engine)**
- ADR 0015 — single-service-process architecture
- ADR 0016 — the CLI surface (`setup`, `doctor`, `engine`)
- ADR 0017 — model acquisition via `setup`
- ADR 0018 — no runtime network
- ADR 0019 — code style, typing, interfaces
- ADR 0020 — a configured alternative is never silently substituted
- ADR 0021 — the whisper.cpp backend and its in-process, resident precedent
- **ADR 0022 (proposed) — the Nemotron streaming engine decision**
