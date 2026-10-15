# Epic: Nemotron 3.5 streaming backend (incremental text)

- **Priority:** P1
- **Status:** to do
- **Epic owner:** orchestrator
- **Research:** [../research/parakeet-v3-engine-landscape/REPORT.md](../research/parakeet-v3-engine-landscape/REPORT.md)
  (the engine/Vulkan/incremental survey) · [../research/nemotron-3-5-asr-streaming-measurement/REPORT.md](../research/nemotron-3-5-asr-streaming-measurement/REPORT.md)
  (ticket 55 — the on-machine measurement) · a code-investigation design report (ticket 56, to be produced)
- **Supersedes:** [../parakeet-v3-backend/epic.md](../parakeet-v3-backend/epic.md) (Epic 5 — Parakeet v3,
  closed **obsolete** on the user's decision, 2026-10-14)
- **Decision:** **[ADR 0022](../../adr/0022-nemotron-streaming-pure-streaming-backend.md)** (new,
  **engine-specific**). **ADR 0011 is NOT amended** — see "Pure streaming, not a window" below.

## Goal

Add **`nvidia/nemotron-3.5-asr-streaming-0.6b`** (OpenMDW-1.1) as a selectable, **fully offline**
`Transcriber` backend that **streams**: text is committed and typed **while the user is still
speaking**, which is exactly what the shipped `whisper_cpp` backend cannot do. It runs on the
**`NVIDIA/NeMo-Speech.cpp`** engine (Apache-2.0, C++/`llama.cpp`/ggml) from the **official in-repo
`q8_0` GGUF**, giving **Vulkan** on this PC's Radeon 780M through ggml and requiring **no Python, no
`torch`, no `numpy`**.

The **default engine stays `whistle`**, and both existing backends stay intact; this is a third,
opt-in engine.

## Why it matters

The user's daily driver is `whisper_cpp`. It is accurate but **batch-only** — no text until the
utterance ends — and that is the annoyance the user set out to fix. The Epic 5 discovery found that
Parakeet v3 cannot fix it (**batch-only in every engine**), but that the **cache-aware streaming
sibling** can: it maintains per-layer encoder caches and commits **append-only**, non-overlapping
frames. Ticket 55 measured it on this machine and confirmed the property holds (report §7). The user
chose this model over Parakeet v3 with the trade-off stated: Hungarian accuracy is **broad-coverage**
(published FLEURS ≈ 29–33) versus v3's 15.72.

## Pure streaming, not a window (the user's decision, 2026-10-14)

ADR 0011 exists *because* the Whistle engine re-transcribes a **growing buffer**: it needs a 5 s window
to cap the buffer and a two-pass agreement rule to decide what is stable. A cache-aware streaming
engine has neither problem — ticket 55 measured it committing **append-only** (every interim is a
string-prefix extension of the previous one, 1.0 across 20 runs and 5 chunk sizes, with no seam
duplication and no silence hallucination).

**The user's directive: do not amend ADR 0011.** This epic integrates the streaming model using the
engine's **pure streaming** output — no sliding window, no two-pass agreement, no growing-buffer
re-decode — and records that as a **new, engine-specific ADR (0022)**. ADR 0011 keeps the Whistle path
unchanged and is **not edited**; ADR 0022 states its own relationship to it (see the ADR's
"Relationship to ADR 0011").

The committed contract from tickets 32–34 is **preserved exactly**: committed text is append-only and
never revised, only committed text is injected, the pending tail is display-only, the trailing space is
emitted once. The mechanism that carries it is measured and designed by **ticket 56**, not assumed.

## Deliverable

An opt-in `nemo_speech` `Transcriber` engine that streams `nemotron-3.5-asr-streaming-0.6b` in-process
(ctypes over the C ABI, model resident from service start, the ADR 0021 shape), driving the engine's
**pure streaming** output into the committed-delta path, acquired through `ges2t setup` with
**sha256 + size pinning**, with `doctor` coverage, documentation, and **ADR 0022** accepted by the
user. The default engine is unchanged and ADR 0011 is untouched.

## Acceptance criteria

- [ ] Ticket 55's report exists with **real, on-machine numbers** — done, merged (`4cf5fc2`).
- [ ] Ticket 56's design report exists and specifies, from an actual reading of the code, **how the
      pure-streaming output is carried into the committed-delta path with no sliding window and no
      second pass**, including the exact seam/Protocol shape, the C-ABI interim delivery, threading and
      the service lock, and a proposed implementation ticket breakdown.
- [ ] `transcriber.engine` selects the streaming engine; the **default stays unchanged** and `whistle`
      and `whisper_cpp` behaviour is unchanged.
- [ ] The engine reports `supports_streaming = True` and the committed text is **append-only**; the
      tickets 32–34 contract (committed-only injection, typed watermark, trailing space once) is
      preserved.
- [ ] **No sliding window and no two-pass agreement rule** is applied to this engine; **ADR 0011 is
      not modified**.
- [ ] Engine archive and model are pinned by **sha256 + byte size**; `ges2t setup` **requires and never
      installs** prerequisites and never runs `sudo`; **no runtime network** (ADR 0018).
- [ ] **No `numpy`, no `torch`, no `onnxruntime`** enters the project.
- [ ] `uv run pytest` and `uv run mypy src/ges2t tests` green; `doctor` reports the engine library,
      the model, and the Vulkan/GPU state when this engine is configured.
- [ ] The **Hungarian accuracy reality is documented** (published FLEURS vs the reference clips that
      actually exist), not glossed over.
- [x] **ADR 0022 is presented in full for user sign-off** — **accepted 2026-10-14**; no further
      sign-off is required and this criterion is met.
- [ ] Every ticket's `docs/project/README.md` row is `done` (or `obsolete` with the reason).

## Out of scope for this epic

- **Parakeet v3 itself** — obsoleted by the user's decision; its discovery report is retained as
  evidence only. See [../parakeet-v3-backend/epic.md](../parakeet-v3-backend/epic.md).
- **Amending ADR 0011** — explicitly rejected by the user; the streaming decision is recorded as a new
  engine-specific ADR instead.
- Changing the **default** engine, or retiring either existing backend.
- Automatic fallback between engines (ADR 0020).
- Bundling any engine binary, model weight, or audio file in the repo.
- Text normalisation of the transcript (**project-wide** out of scope).
- Packaging/distribution (ADR 0000); installing system packages or running `sudo`.

## Decisions this epic may touch

- ADR 0010 — the `Transcriber` Protocol seam
- ADR 0011 — streaming / sliding-window commit cadence (**left untouched**; ADR 0022 records that the
  window does not apply to a cache-aware streaming engine)
- ADR 0015 — single-service-process architecture
- ADR 0016 — the CLI surface (`setup`, `doctor`, `engine`)
- ADR 0017 — model acquisition via `setup`
- ADR 0018 — no runtime network
- ADR 0019 — code style, typing, interfaces
- ADR 0020 — a configured alternative is never silently substituted
- ADR 0021 — the whisper.cpp backend and its in-process, resident precedent
- **[ADR 0022](../../adr/0022-nemotron-streaming-pure-streaming-backend.md) (new, engine-specific) — Nemotron
  3.5 streaming via pure streaming**
