# Nemotron documentation

- **Priority:** P2
- **Status:** to do
- **Complexity:** S
- **Epic:** ../epic.md

## Current state / context

Epic 6 ships a third backend whose **limits are the story**: it streams (unlike whisper), it costs
VRAM rather than host RAM, its English accuracy is **weaker than whisper `medium`** on noisy real-device
audio, and its **Hungarian accuracy was never measured on this machine**. If those are not written down,
the next person will rediscover them as bugs. This ticket is the documentation pass that closes the
epic, and it is **docs only** — ADR 0022 is already **accepted (user sign-off 2026-10-14)**, so there is
no sign-off gate here.

The templates are the whisper.cpp docs: the README engine section, the config reference, and the
per-engine doc the whisper backend received.

Read [the measurement report §1 and §8–§9](../../research/nemotron-3-5-asr-streaming-measurement/REPORT.md)
for the numbers and [ADR 0022](../../../adr/0022-nemotron-streaming-pure-streaming-backend.md) for the
decision and its known limits.

## Definition of done

- [ ] The **README** lists `nemo_speech` as a selectable engine, states that it **streams** (commits
      while speaking) and that the **default engine is unchanged**, and gives the exact config block:
      `[transcriber] engine = "nemo_speech"` plus `[engine.nemo_speech]` (`device`, `chunk_seconds`).
- [ ] A **config reference** entry documents `device` (`auto`/`vulkan`/`cpu`), `chunk_seconds`
      (default 0.16), and that **`window_seconds` has no effect for this engine** (and warns).
- [ ] The **known limits are documented explicitly**, with the measured numbers, not adjectives:
      - Vulkan needs **~950 MB VRAM**; CPU-only costs **~1 GB host RSS** and ~2× per-chunk latency;
      - English accuracy is **weaker than whisper `medium`** on noisy audio (car clip word-diff WER
        **0.312** vs whisper **0.062** with `-nt`, 0.156 without);
      - **Hungarian is published-FLEURS only** (≈28.7–33.4) and **unmeasured on this machine**;
      - Vulkan and CPU transcripts are **not byte-identical** (deterministic per device).
- [ ] `ges2t setup nemo_speech` is documented end to end (what it downloads, the pins, that it needs no
      build tooling and no `sudo`), and `doctor`'s new checks are described.
- [ ] The **OpenMDW-1.1** model licence and the **Apache-2.0** engine licence are recorded with
      attribution (the CC-BY/ADR 0006 discipline).
- [ ] Docs link check reports **0 broken references**; `uv run pytest` and `uv run mypy src/ges2t tests`
      still green; README row updated.

## Notes

- **Do not overclaim.** The Hungarian number is published FLEURS, not a local measurement, and must be
  labelled as such. Do not present CPU/Vulkan parity as verified.
- Keep the docs in the existing style; do not introduce a new doc structure.
- If the shipped behaviour diverges from the report, document **what shipped**, not what the report
  predicted — and leave a comment noting the divergence.

## Related tickets

- depends on: [../nemo-speech-pipeline-wiring/ticket.md](../nemo-speech-pipeline-wiring/ticket.md) (60) ·
  [../nemo-speech-doctor/ticket.md](../nemo-speech-doctor/ticket.md) (61)
- blocks: —
- related ADRs: [ADR 0006](../../../adr/0006-needle3-closed-engine-accepted-risk.md) ·
  [ADR 0017](../../../adr/0017-model-acquisition-via-setup.md) ·
  [ADR 0021](../../../adr/0021-whisper-cpp-second-backend.md) ·
  [ADR 0022](../../../adr/0022-nemotron-streaming-pure-streaming-backend.md)
