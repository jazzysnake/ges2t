# Nemotron 3.5 ASR Streaming — measure it on this machine

- **Priority:** P0
- **Status:** to do
- **Complexity:** L
- **Epic:** ../epic.md
- **Type:** discovery/measurement (no `src/` change, no `tests/` change, no `sudo`, no microphone)
- **Branch / worktree:** `feat/nemotron-3-5-asr-streaming-measurement` in `.worktrees/nemotron-3-5-asr-streaming-measurement`

## Current state / context

The user chose to integrate **`nvidia/nemotron-3.5-asr-streaming-0.6b`** on the
**`NVIDIA/NeMo-Speech.cpp`** engine, because it is the only candidate that gives **incremental
(streaming) text** — the gap the `whisper_cpp` backend has. Ticket 53's landscape survey
(`docs/project/research/parakeet-v3-engine-landscape/REPORT.md`, merged in `792b508`) selected the
engine and wrote the stage-2 plan this ticket executes. **Ticket 54 was the Parakeet-v3 measurement and
is obsolete** — do not re-derive it; everything useful from it is folded in below.

This ticket produces the **numbers the ADR and the integration tickets rest on**. It is the
measurement half, and it is **more than a latency benchmark**: because this engine's whole point is
streaming, it must prove the output is **append-only and stable**, not just fast.

### Pinned facts (verified 2026-10-14 by ticket 53 and the orchestrator)

| item | value |
|---|---|
| engine | `https://github.com/NVIDIA/NeMo-Speech.cpp` — Apache-2.0, C++/`llama.cpp`/ggml, tag **`v0.2.0`** |
| engine binary | `https://github.com/NVIDIA/NeMo-Speech.cpp/releases/download/v0.2.0/nemo-speech-0.2.0-linux-x86_64-vulkan.tar.gz` — **16,618,142 B**, sha256 **`c8d0ed5b9ac5fb6d5c460015ba38ea217415c44edc1e6d49120646781009aee6`** |
| C ABI | `libnemo_speech_asr_c` (for the later `ctypes` adapter — not measured here) |
| model | `nvidia/nemotron-3.5-asr-streaming-0.6b`, cache-aware streaming FastConformer-RNNT, **OpenMDW-1.1**, 40 locales incl. `hu-HU` (broad-coverage tier) |
| model artifact | `nemotron-3.5-asr-streaming-0.6b.q8_0.gguf` — **re-pin the revision + sha256 at measurement time** (see "Revision drift") |
| Hungarian reference | published FLEURS WER ≈ 28.7–33.4 (vs Parakeet v3's 15.72) — **a documented trade-off, not to be hidden** |

**Revision drift to resolve.** The report found the HF `main` artifact at `742,090,464 B` (revision
`ea30d66d…`) while NeMo-Speech.cpp's own `models/index.json` pins an earlier revision `1c8deae…` at
`741,548,352 B`, sha256 `a5c435f2…`. **Pick one revision, record which, and verify its sha256 from the
HF LFS metadata** (`https://huggingface.co/api/models/nvidia/nemotron-3.5-asr-streaming-0.6b?blobs=true`).
Prefer consistency with the engine's own index unless the measurement shows a reason not to.

### Reference audio and harness (reuse, do not invent)

- Audio: `~/.cache/ges2t-discovery/whisper-cpp-provider/audio/` — `clip5.wav`, `clip30.wav`,
  `jfk.wav`, `quiet-long.wav`, `quiet-short.wav`, `2.wav`, `3.wav`, `four.wav`, `cny16.wav`, and the
  user's own `ges2t-commit-cadence-capture-20261009-195914.wav`. These are the clips the whisper
  research measured, with ground truth.
- Harness: `docs/project/research/whisper-cpp-provider/measure_whisper_cpp.py` (resampling, WER,
  `word_diff`) and `docs/project/research/whisper-cpp-streaming/measure_streaming.py` (the
  committed-delta/partial-output recorder). Copy them into the scratch dir rather than writing new ones.
- **Hungarian:** there is **no Hungarian ground-truth clip** in the repo or scratch. Do **not** record
  one (no microphone). Report the published FLEURS number, mark Hungarian accuracy as **not locally
  measured**, and flag in `001-comment.md` that a Hungarian sample + transcript from the user would be
  needed to settle it.

## Definition of done

Produce **`docs/project/research/nemotron-3-5-asr-streaming-measurement/REPORT.md`** (written
incrementally) plus committed raw evidence in a `data/` folder beside it, and satisfy:

- [ ] **Engine runs here.** The prebuilt Vulkan archive is downloaded to a scratch dir outside the
      repo, its sha256 verified, extracted, and `nemo-speech doctor` run; record its real output
      (compiled backends + detected devices).
- [ ] **Vulkan is actually used, proven not assumed.** The measured GPU run must demonstrate the
      device is Vulkan (e.g. `--device vulkan:0` plus `doctor`/`check_backend_coverage` output, or an
      explicit CPU-vs-Vulkan timing gap), **and** GPU and CPU transcripts are **byte-compared** on the
      same input. If the offline/streaming graph silently falls back to CPU, say so plainly.
- [ ] **Latency and cost numbers**, on the clips above and on the user's capture: per-chunk latency
      for the documented chunk sizes (80/160/320/560/1120 ms where the engine exposes them), real-time
      factor, cold warm-up vs steady-state, and **peak RSS** (plus VRAM if reported).
- [ ] **Committed-delta stability** (the core claim), modelled on
      `docs/project/research/whisper-cpp-streaming/REPORT.md`: feed each clip in streaming mode, record
      **every** partial, and measure whether committed text is **append-only** (never revised), and
      whether **boundary/seam duplication**, **cut-off hallucination**, or **silence hallucination**
      occur. Reuse the earlier report's metric shapes so the two are comparable. State the chunk sizes
      and the commit policy tested.
- [ ] **Accuracy** against the existing English ground truth (standard and `word_diff` WER, with the
      normalisation stated) on the same clips the whisper reports used, so the comparison to
      `whisper_cpp`/`whistle` is apples-to-apples. Hungarian is reported as published-FLEURS only, with
      that limitation explicit.
- [ ] **A confounds/limits section**, an explicit **what-would-falsify** statement for the central
      claim ("the streaming model gives stable append-only incremental text on this PC on Vulkan"), and
      a plain **answer + recommendation**.
- [ ] **Drafted ADR 0022** (the Nemotron streaming engine decision) **and a drafted ADR 0011
      amendment** (the 5 s sliding window does not apply to a cache-aware streaming engine; the
      append-only contract is preserved), both included verbatim in the report or in `001-comment.md`.
      **Do not write anything into `docs/adr/`** — the orchestrator presents both to the user for
      sign-off.
- [ ] **`001-comment.md`**: headline numbers, the resolved revision/sha256, an explicit
      **"safe to relay?"**, and anything to escalate (a `sudo pacman -S …` line if a build is needed,
      a licence concern, model size, or the missing Hungarian ground truth).
- [ ] Every run is **bounded** (`timeout <n> …`), spawned processes are checked to have exited and are
      cleaned up, and the scratch dir is named and left tidy.
- [ ] No file under `src/ges2t/` or `tests/` is modified; no engine, model, weight, or audio is
      committed; `git status` is clean; the docs link check reports **0 broken references**.
- [ ] Everything is committed on `feat/nemotron-3-5-asr-streaming-measurement`; `ticket.md` status and
      the `docs/project/README.md` row are updated to `in review`.

## Notes

- **Hard boundaries** (discovery-agent contract): **no `sudo`, ever** — if a build genuinely needs a
  package, stop and report the exact `sudo pacman -S …` line (`ninja` and `sentencepiece` are **not**
  installed here); **no microphone**; no clipboard, keystrokes, windows, or notifications; no
  production code; never commit to `main`.
- **The prebuilt archive needs no build tooling and no `sudo`** — use it. Only a from-source build
  would need `sudo pacman -S ninja sentencepiece` (`cmake`, `glslc`, SPIR-V/Vulkan headers are present).
- Scratch stays **outside the repo** (name the path in the report). Downloading the engine archive and
  the GGUF is authorized; installing packages is not.
- `numpy` must not appear anywhere in the project — if the engine's Python tooling requires it, that is
  a **finding to escalate**, not something to work around.
- Re-read `docs/project/research/parakeet-v3-engine-landscape/REPORT.md` §5 and §7 before starting —
  they hold the streaming rationale and the exact commands.
- Long runs die on connection errors: write the report incrementally and commit per step.

## Related tickets

- depends on: [../../parakeet-v3-backend/parakeet-v3-engine-landscape/ticket.md](../../parakeet-v3-backend/parakeet-v3-engine-landscape/ticket.md) (ticket 53 — done, the survey this executes)
- supersedes: [../../parakeet-v3-backend/parakeet-v3-vulkan-measurement/ticket.md](../../parakeet-v3-backend/parakeet-v3-vulkan-measurement/ticket.md) (ticket 54 — obsolete)
- blocks: the Nemotron integration tickets (created only after this ticket's verdict) and ADR 0022
- related ADRs: [0010](../../../adr/0010-transcriber-interface-design.md) ·
  [0011](../../../adr/0011-streaming-sliding-window.md) ·
  [0017](../../../adr/0017-model-acquisition-via-setup.md) ·
  [0018](../../../adr/0018-no-runtime-network.md) ·
  [0021](../../../adr/0021-whisper-cpp-second-backend.md)
