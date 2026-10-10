# Epic: whisper.cpp backend

- **Priority:** P1
- **Status:** to do
- **Epic owner:** orchestrator
- **Research:** [../research/whisper-cpp-provider/REPORT.md](../research/whisper-cpp-provider/REPORT.md) ·
  [../research/whisper-cpp-streaming/REPORT.md](../research/whisper-cpp-streaming/REPORT.md)
- **Decision:** [ADR 0021](../../adr/0021-whisper-cpp-second-backend.md) (proposed — sign-off required)

## Goal

Add **whisper.cpp** as a second, **selectable** `Transcriber` backend, integrated **in-process**
(`ctypes` over `libwhisper.so`, model loaded **once at service start** and kept resident),
supporting the full whisper.cpp model range (`tiny` … `large-v3-turbo`, `q5_0`/`q8_0`) with
**`medium-q5_0` as the default on GPU devices and `small-q5_0` on CPU-only machines**.

It is **batch-only**. The **default backend stays `needle`/Whistle**, so the shipped live and
incremental typing are unchanged; choosing whisper trades that away for accuracy — **~4.5× fewer
errors than Whistle** on the reference recording (`-nt`; ~1.8× on the CLI default), at ~1.7–3 s per
25 s utterance on the Radeon 780M.

## Why it matters

Accuracy is the user's obstacle to daily-driving, and the provider research put the dominant residual
errors in **model capacity** — the risk ADR 0005 accepted. whisper.cpp is the escape hatch ADR 0006
names, it is MIT end-to-end, and it was measured on this machine. The user wants it available
**without** giving up the default experience and **without** paying a process spawn plus a ~0.5 GB
model reload on every utterance — hence in-process, resident, and loaded at startup.

## Deliverable

A config-selectable `whisper_cpp` backend — in-process and resident — that `ges2t setup` builds and
feeds, with strict startup validation, `doctor` coverage, the pinned `-nt` decode, and the
batch-only limitation documented. The default backend is unchanged.

## Acceptance criteria

- [ ] `uv run pytest` green (headless) and `uv run mypy src/ges2t tests` 0 errors — ADR 0014/0019.
- [ ] `transcriber.backend = "whisper_cpp"` selects it; the **default stays `needle`** and Whistle's
      behaviour is unchanged.
- [ ] The model loads **once, at service start**, through `ctypes` over `libwhisper.so`; there is **no
      per-utterance spawn or reload**. The non-model round trip is **measured and reported as a number**
      under ADR 0001's 1 s budget.
- [ ] Default model: **`medium-q5_0` where a Vulkan device is available, `small-q5_0` otherwise**;
      overridable in config.
- [ ] `supports_streaming = False`, and startup **rejects** `whisper_cpp` + `streaming.enabled = true`.
- [ ] Startup **rejects** a whisper backend whose model is not acquired, and one whose library is not
      built. **Model-vs-GPU sizing is not checked.**
- [ ] The decode is pinned (`-nt`, greedy) in code, never inherited from the CLI default.
- [ ] `ges2t setup` = needle + its default model (`whistle`); `ges2t setup whisper.cpp <model>` builds
      whisper.cpp and fetches `<model>`; `ges2t setup needle <model>` is accepted — **the needle model
      is selectable too, defaulting to `whistle`**. Downloads come from Hugging Face and are
      sha256 + size pinned.
- [ ] Setup **requires and never installs** its prerequisites (C++ toolchain, `cmake`, and the Vulkan
      dev packages for a GPU build) and **fails with the exact package list**; no `sudo`, no package
      installs, no fetching of build tooling. No Vulkan runtime → **CPU-only build**.
- [ ] `ges2t doctor` checks the built library, the resolved model, and Vulkan availability.
- [ ] **Manual, on the reference machine (user-owned):** with `transcriber.backend = "whisper_cpp"`,
      speak → the correct text is typed into an editor **and** a terminal.
- [ ] Every ticket's `docs/project/README.md` row is `done`.

## Out of scope for this epic

- Streaming / incremental typing on whisper (measured not viable).
- Making whisper the default backend.
- Bundling `whisper-cli`, `ggml`, `libwhisper.so` or model weights in the repo.
- Automatic fallback between backends (ADR 0020).
- Any number/spelling normalisation of the transcript (**project-wide out of scope**).
- A model-vs-GPU sizing check.

## Decisions this epic implements

- ADR 0005 — the Whisper/Needle backend (adds the escape hatch it names)
- ADR 0006 — closed-engine risk (whisper.cpp is the open alternative)
- ADR 0010 — the `Transcriber` Protocol
- ADR 0011 — streaming (untouched for the default; recorded as not applying to whisper)
- ADR 0017 — model acquisition via `setup`
- ADR 0018 — no runtime network
- ADR 0019 — full annotations, interface-typed signatures
- ADR 0020 — a configured alternative is never silently substituted
- **ADR 0021 (new, proposed) — whisper.cpp as a second, in-process, batch-only backend**
