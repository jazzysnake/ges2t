# Parakeet v3 — measure the recommended engine on this machine

- **Priority:** P1
- **Status:** obsolete
- **Complexity:** L
- **Epic:** ../../parakeet-v3-backend/epic.md (Epic 5 — itself obsolete)
- **Type:** discovery/measurement (never started)

## Why this is obsolete

The user chose the **streaming sibling** `nvidia/nemotron-3.5-asr-streaming-0.6b` over Parakeet v3,
because Parakeet v3 is **batch-only in every engine** and therefore does not fix the incremental-text
motivation that started Epic 5. Measuring v3's latency/accuracy is no longer on the critical path, so
this ticket is **obsolete and was never started** — it produced no artifacts. Its DoD, its pinned
hashes and its scratch conventions were carried into its replacement,
[`nemotron-3-5-asr-streaming-measurement`](../../nemotron-streaming-backend/nemotron-3-5-asr-streaming-measurement/ticket.md)
(ticket 55) under **Epic 6**. The survey that decided this is ticket 53's
[report](../../research/parakeet-v3-engine-landscape/REPORT.md), which stays on `main` as evidence.

---

## Original scope (kept for history; do not work on it)

Ticket 53 (`../parakeet-v3-engine-landscape/ticket.md`) surveys every engine that can run
`nvidia/parakeet-tdt-0.6b-v3` and recommends one, with a written **stage-2 measurement plan**
(engine + revision, artifacts to download with sizes, exact commands, metrics). This ticket executes
that plan **on the user's actual machine** — Arch + KDE Plasma 6 on Wayland, **AMD Radeon 780M
(RDNA3)**, RADV Vulkan, **no CUDA**, `numpy` deliberately absent from the project.

This is the measurement half of the user's request: *"How hard would it be to integrate the engine and
if it would support Vulkan GPU on this PC."* It reuses the project's existing research machinery — the
`word_diff` accuracy harness and reference clips behind
[`docs/project/research/whisper-cpp-provider/REPORT.md`](../../research/whisper-cpp-provider/REPORT.md)
and [`docs/project/research/whisper-cpp-streaming/REPORT.md`](../../research/whisper-cpp-streaming/REPORT.md) —
rather than inventing a new metric. The numbers this ticket produces are the ones the ADR and the
integration tickets will rest on.

## Definition of done

- [ ] **Gate honoured:** if ticket 53 recommends no candidate, set this ticket `obsolete`, record the
      reason and a link to ticket 53's verdict, and stop — write no report. Otherwise continue.
- [ ] **`docs/project/research/parakeet-v3-vulkan-measurement/REPORT.md`** exists, written
      incrementally as the work proceeds, containing:
  - the **question** and the engine + exact revision + model artifact hashes/byte sizes actually used;
  - the **method**: every exact command, the scratch-dir path, and the reference audio + ground truth
    used;
  - **raw evidence**: per-run transcripts and timings, committed in a `data/` folder alongside the
    report and kept **small** (the bulk stays in scratch outside the repo);
  - **results**, at minimum: cold and warm **per-clip latency**, **RTF**, **peak RSS** (and VRAM if
    the engine reports it), and **accuracy vs the project's reference truth** using the same
    normalisation and metric the whisper reports use (report both the comparable and the standard
    metric where they differ). Where the engine can use Vulkan here, report **Vulkan vs CPU** on
    identical input; where it cannot, say so and report the best available device path.
  - for a **streaming/incremental** candidate: a **committed-delta stability** measurement modelled on
    the whisper-streaming report (does text committed before utterance end survive to the end? any
    seam duplication, cut-off or silence hallucination?), with the same kind of audio the earlier
    report used;
  - **confounds and limits** — what was held constant and what was not, sample size, normalisation
    choices;
  - an explicit **what-would-falsify** statement for the central claim;
  - a plain **answer + recommendation**: is the engine worth integrating, on which device path, and
    what it costs in memory and latency.
- [ ] **A drafted ADR** (candidate `0022-parakeet-v3-…`, or an ADR 0021 amendment) is included
      verbatim in the report or in this ticket's `001-comment.md`. It is **not** written into
      `docs/adr/` — the orchestrator presents it to the user for sign-off first.
- [ ] **`001-comment.md`** in this ticket folder records the headline numbers and hands the
      orchestrator an explicit **"safe to relay?"** statement plus anything that must be escalated
      (a `sudo pacman -S …` line, a new `numpy`/`torch` dependency, licence concerns, model size).
- [ ] **Every run was bounded** (`timeout <n> …`), no unbounded waits, and each spawned process is
      checked to have exited; spawned processes are cleaned up and the scratch dir is left tidy.
- [ ] No file under `src/ges2t/` or `tests/` is modified; no binary, model, weight, or audio file is
      committed; `git status` in the worktree is clean; the docs link check reports 0 broken
      references.
- [ ] Everything is committed on `feat/parakeet-v3-vulkan-measurement`; `ticket.md` status and the
      `docs/project/README.md` row are updated to `in review`.

## Notes

- **Hard boundaries** (from the discovery-agent contract): **no `sudo`, ever** — if a build genuinely
  needs a package, stop and report the exact `sudo pacman -S …` line for the user to approve; **no
  microphone** — reuse the repo's / the whisper research's reference audio, and if it is not
  available, ask the orchestrator instead of recording; no clipboard, keystrokes, windows, or
  notifications; no production code; never commit to `main`.
- **Scratch space is outside the repo** (name the path in the report). Experiments may download the
  engine and the model there; the repo must gain **no** dependency, model, or audio file.
- Downloading a model is authorized; downloading a **build toolchain via `sudo`** is not.
- Wait for ticket 53's plan before picking artifacts — the engine choice (ONNX Runtime vs NeMo vs
  sherpa-onnx vs a ggml port) determines everything here. Re-read ticket 53's report and its
  `001-comment.md` first.
- Reuse, don't re-derive: the whisper research's `measure_*.py` already contains resampling, WER and
  `word_diff` helpers. Copy them into the scratch/discovery folder rather than writing new ones.
- Keep the committed evidence curated and small. Long runs die on connection errors — write the report
  incrementally.

## Related tickets

- depends on: [../parakeet-v3-engine-landscape/ticket.md](../parakeet-v3-engine-landscape/ticket.md) (ticket 53)
- blocks: the integration tickets (created only after this ticket's verdict)
- related ADRs: [0010](../../../adr/0010-transcriber-interface-design.md) ·
  [0011](../../../adr/0011-streaming-sliding-window.md) ·
  [0017](../../../adr/0017-model-acquisition-via-setup.md) ·
  [0018](../../../adr/0018-no-runtime-network.md) ·
  [0021](../../../adr/0021-whisper-cpp-second-backend.md)
