# Parakeet v3 — engine landscape, Vulkan feasibility, integration cost

- **Priority:** P0
- **Status:** to do
- **Complexity:** M
- **Epic:** ../epic.md
- **Type:** discovery (no `src/` change, no `tests/` change, no `sudo`, no microphone)
- **Branch / worktree:** `feat/parakeet-v3-engine-landscape` in `.worktrees/parakeet-v3-engine-landscape`

## Current state / context

**ges2t** is a fully offline Linux speech-to-text tool (Python + `uv`, `src/` layout, Wayland-only,
Arch + KDE Plasma 6 reference target). Transcription sits behind the **ADR 0010 `Transcriber`
Protocol** in `src/ges2t/interfaces.py`, with concrete backends in `src/ges2t/transcribers/`
(`whistle.py`, `whisper_cpp.py`) and engine-identifier constants in `src/ges2t/identifiers.py`.
There are two shipped backends:

- **`whistle`** (default) — Cactus Whistle on the closed Needle 3 engine, 7 languages
  (en/de/fr/es/it/nl/pl), **streams and types incrementally**. See
  [ADR 0005](../../../adr/0005-stt-backend-cactus-whistle.md).
- **`whisper_cpp`** (opt-in) — in-process `ctypes` over `libwhisper.so`, **batch-only** (no text until
  the utterance ends). See [ADR 0021](../../../adr/0021-whisper-cpp-second-backend.md).

The user is daily-driving `whisper_cpp` and is happy with accuracy and speed, but the **batch-only**
behaviour is the annoyance: they cannot see what the tool currently thinks they said. The user's
request, verbatim: *"I want to add the parakeet v3 model to the supported models list. can you do an
explore run and Figure out which engine best supports it. How hard would it be to integrate the
engine and if it would support Vulkan GPU on this PC."*

This ticket is the **literature/feasibility half**. Ticket 54 is the on-machine measurement half and
must not start until this ticket recommends a candidate. The user's stated gate: *"First look at the
literature and if that suggests it's possible then do measurements on this machine."*

**Reference material to reuse, not re-derive:**

- `docs/project/research/whisper-cpp-provider/REPORT.md` and
  `docs/project/research/whisper-cpp-streaming/REPORT.md` — the **template** for what a provider
  report answers, and the source of the reusable `word_diff` / accuracy-harness conventions and the
  reference clips.
- ADRs 0005, 0006, 0010, 0011, 0015, 0016, 0017, 0018, 0019, 0020, 0021 (links from the epic).

## Definition of done

Produce **`docs/project/research/parakeet-v3-engine-landscape/REPORT.md`** answering all of the
following for the exact model **`nvidia/parakeet-tdt-0.6b-v3`**. Every claim needs a source URL or a
local command + its real output; every "measured" line must name the command that produced it.

1. **Model identity** — exact Hugging Face repo and revision, **licence**, parameter count, on-disk
   size per published artifact/quantisation (state the byte sizes), architecture (TDT transducer),
   and the **supported language list** — explicitly confirm or deny **Hungarian**. State whether an
   official **ONNX export** exists and enumerate its files and sizes.
2. **Engine inventory** — every runtime that can execute *this* model today. For each: upstream URL,
   pinned version/revision, integration language and linkage (Python wheel / C ABI / `ctypes` /
   subprocess / server), **complete transitive dependency list** (e.g. `torch`, `nemo_toolkit`,
   `onnxruntime`, CUDA/ROCm runtime, and **whether `numpy` becomes required — it is deliberately
   absent from this project**), licence, and CPU vs GPU support (and via which API). Cover at least
   NVIDIA NeMo, ONNX Runtime (all Linux execution providers), sherpa-onnx, and **any ggml-based
   Parakeet port** (search for `parakeet.cpp`, CrispASR, or equivalents — do not assume none exists,
   and do not assume one does).
3. **Vulkan verdict for this PC** — an explicit **yes / no / unknown** per engine for **AMD Radeon
   780M (RDNA3) under RADV on Arch**, with the evidence (execution-provider lists, support matrices,
   upstream issues). If **no** engine can use Vulkan here, say so plainly and name the reason
   (e.g. "ONNX Runtime has no Vulkan EP; only a ggml/ggml-vulkan port would"). Also record what the
   fallback (CPU, or ROCm/MIGraphX) costs.
4. **Incremental / streaming capability** — for each engine, whether it can emit **stable committed
   text before the utterance ends**, and by what mechanism (true streaming, cache-aware streaming,
   chunked re-decode), with evidence. Answer explicitly: **can Parakeet v3 — or a streaming sibling
   model — deliver the incremental UX whisper.cpp cannot, and at what accuracy/latency cost?** Note
   any implications for ADR 0011's sliding-window commit cadence and for tickets 32–34.
5. **Integration cost estimate** — for the top one or two engines, what a ges2t adapter would need: a
   new `src/ges2t/transcribers/*.py`, an `identifiers.py` entry, a model-store entry, a `setup`
   acquisition path (and engine build, if any), `doctor` checks, resident memory, and model-load
   behaviour at service start. Give an honest **size (XS–XL)** and compare it to the whisper.cpp
   integration's cost.
6. **Stage-2 measurement plan** — the exact engine(s) to build in a **scratch dir outside the repo**,
   the artifacts to download with their sizes, the exact commands, and the metrics ticket 54 will
   collect (cold/warm latency, RTF, peak RSS, accuracy vs the project's reference truth, and — for a
   streaming candidate — committed-delta stability modelled on the whisper-streaming report).
7. **Recommendation** — one engine, one paragraph of justification, and a **draft ADR** (candidate
   `0022-parakeet-v3-…`, or an ADR 0021 amendment) included verbatim **in the report or in the
   ticket's `001-comment.md`**. Do **not** write anything into `docs/adr/` yet.

Also required:

- [ ] **`001-comment.md`** in this ticket folder states the **gate decision**: whether the literature
      warrants ticket 54 ("proceed") or not ("stop; close 54 obsolete"), with the two or three facts
      that decide it, and flags anything the orchestrator must escalate to the user (e.g. a required
      `sudo pacman -S …` line, a `numpy` dependency, a licence problem, or a >2 GB resident model).
- [ ] No file under `src/ges2t/` or `tests/` is modified.
- [ ] No engine, model, weight, or audio file is committed; scratch work lives outside the repo and
      its path is named in the report.
- [ ] The docs link check from `docs/project/orchestrator-state.md` §10 reports **0 broken
      references**; `git status` in the worktree is clean.
- [ ] Everything is committed on `feat/parakeet-v3-engine-landscape`; `ticket.md` status and the
      `docs/project/README.md` row are updated.

## Notes

- **Hard boundaries** (from the discovery-agent contract): no `sudo`, no package installs, no
  microphone, no clipboard/keystrokes/windows, no network beyond the documentation lookups and
  download-metadata checks this task authorizes, no production code, **never commit to `main`**.
- **Do not guess.** If the honest answer is "no engine here can do it" or "it is unknown without a
  build", write that — a hedged wrong answer is worse than a clear "unknown". Asserting a
  measurement that was never run is the most serious error class in this project.
- Prefer **primary sources**: the Hugging Face model card and file listing, NVIDIA NeMo docs and
  model cards, the ONNX Runtime execution-provider docs, sherpa-onnx docs, the ROCm support matrix,
  and the upstream repos' READMEs/releases. Record versions and dates.
- Useful local checks (read-only): `vulkaninfo` / `vulkaninfo --summary` for the device and RADV,
  `nproc`, `free -g`, `pacman -Q` for what is already installed. The orchestrator's environment table
  is in `docs/project/orchestrator-state.md` §3.
- Keep the report **curated**: tens of KB, not megabytes. Raw bulk stays in scratch.

## Related tickets

- depends on: —
- blocks: [../parakeet-v3-vulkan-measurement/ticket.md](../parakeet-v3-vulkan-measurement/ticket.md) (ticket 54)
- related ADRs: [0005](../../../adr/0005-stt-backend-cactus-whistle.md) ·
  [0006](../../../adr/0006-needle3-closed-engine-accepted-risk.md) ·
  [0010](../../../adr/0010-transcriber-interface-design.md) ·
  [0011](../../../adr/0011-streaming-sliding-window.md) ·
  [0017](../../../adr/0017-model-acquisition-via-setup.md) ·
  [0018](../../../adr/0018-no-runtime-network.md) ·
  [0021](../../../adr/0021-whisper-cpp-second-backend.md)
