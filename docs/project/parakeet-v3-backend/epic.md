# Epic: Parakeet v3 backend (discovery-gated)

- **Priority:** P1
- **Status:** to do
- **Epic owner:** orchestrator
- **Research:** *(produced by tickets 53 and 54 — no report exists yet)*
- **Decision:** *(proposed after 53/54; next free ADR number is 0022)*

## Goal

Make **NVIDIA Parakeet TDT 0.6B v3** (`nvidia/parakeet-tdt-0.6b-v3`) available as a selectable,
**fully offline** `Transcriber` backend — **if and only if** discovery establishes an engine that

1. actually runs the model **on this machine** (Arch + KDE Plasma 6, **AMD Radeon 780M / RDNA3**,
   RADV Vulkan, **no CUDA**, no `numpy` in the project),
2. fits the hard constraints (no runtime network — ADR 0018; setup downloads only, requires and never
   installs prerequisites — ADR 0017/0021), and
3. offers a **real advantage over the two existing backends** — ideally **stable incremental
   (streamed) text**, which whisper.cpp cannot provide, and better multilingual coverage than
   Whistle's seven languages.

This epic is **discovery-gated**: ticket 53 answers the engine/Vulkan/incremental questions from the
literature, ticket 54 measures the winner on this machine, and **only then** are integration tickets
created. If nothing qualifies, the epic closes **obsolete** with the evidence recorded and **no
integration code is written**.

## Why it matters

The user's daily driver is the whisper.cpp backend. It is accurate and fast, but it is **batch-only**:
the transcript appears only when the utterance ends, so the user cannot see what the tool currently
thinks they said. That visibility gap is the stated motivation for this epic. Parakeet v3 is a
0.6 B-parameter transducer that (per the model card — **to be verified by ticket 53**) claims ~25
European languages, which would also cover **Hungarian**, a language neither Whistle (en/de/fr/es/it/nl/pl)
nor the rest of the shipped defaults target.

## Deliverable

Either (a) a new, config-selectable `Transcriber` engine for Parakeet v3, acquired through
`ges2t setup` with **sha256 + size pinning**, wired through the ADR 0010 seam with `doctor` coverage and
documentation — with the **default engine unchanged** and both existing backends intact; **or** (b) a
closed-obsolete epic whose reports explain, with evidence, why no engine qualifies on this machine.

## Acceptance criteria

- [ ] Ticket 53's report exists and answers, with sources: model identity and licence; the full
      **engine inventory** with each engine's **complete dependency list**; an explicit
      **Vulkan-on-this-PC** verdict per engine; and whether **any** engine delivers stable
      incremental text before utterance end.
- [ ] Ticket 54 **either** measures the recommended engine on this machine (real numbers: latency,
      RTF, peak RSS, accuracy against the project's reference truth, incremental stability) **or** is
      closed obsolete with the reason recorded — consistent with ticket 53's gate.
- [ ] If an engine qualifies: `transcriber.engine` selects it; the **default stays unchanged**;
      `whistle` and `whisper_cpp` behaviour is unchanged; it is **batch-only or streaming exactly as
      measured**, never assumed.
- [ ] Model acquisition is pinned by **sha256 + byte size**; `ges2t setup` **requires and never
      installs** its prerequisites and never runs with `sudo`.
- [ ] **No runtime network** (ADR 0018): once the engine and model are acquired, dictation works
      offline. No weights, binaries, or audio are committed to the repo.
- [ ] `uv run pytest` and `uv run mypy src/ges2t tests` green; `doctor` reports the Parakeet
      engine/model state when it is configured.
- [ ] A new ADR (0022) or an amendment to ADR 0021 is **presented in full for user sign-off** before
      the merge that carries it.
- [ ] Every ticket's `docs/project/README.md` row is `done` (or `obsolete` with the reason).

## Proposed integration shape (finalised only after 53/54 — not yet tickets)

The whisper.cpp epic (3) is the template. If a candidate passes, expect roughly:

- a `ParakeetTranscriber` adapter behind the ADR 0010 `Transcriber` Protocol (in-process if a C ABI
  exists, otherwise the least-bad linkage the measurement justifies);
- a model-store entry + a `setup` acquisition path (pinned sha256/size; engine build if the engine
  must be compiled, mirroring ticket 42's "check prerequisites, never install" rule);
- engine identifier constants (Epic 4 rule) + config block + strict startup validation;
- `doctor` checks for the engine library, the model, and Vulkan/GPU availability;
- documentation: the config line, the batch/streaming reality, and the memory cost;
- **only if streaming is real:** a commit-cadence design consistent with ADR 0011 / tickets 32–34.

## Out of scope for this epic

- Changing the **default** engine, or retiring either existing backend.
- Automatic fallback between engines (ADR 0020).
- Bundling any engine binary, model weight, or audio file in the repo.
- Text normalisation of the transcript (**project-wide** out of scope).
- Packaging/distribution (ADR 0000).
- Installing system packages or running `sudo` (escalate to the user instead).

## Decisions this epic may touch

- ADR 0005 — the Whistle/Needle 3 backend and its seven languages
- ADR 0006 — closed-engine risk (an open, auditable alternative)
- ADR 0010 — the `Transcriber` Protocol seam
- ADR 0011 — streaming / sliding-window commit cadence
- ADR 0015 — single-service-process architecture
- ADR 0016 — the CLI surface (`setup`, `doctor`, `engine`)
- ADR 0017 — model acquisition via `setup`
- ADR 0018 — no runtime network
- ADR 0019 — code style, typing, interfaces
- ADR 0020 — a configured alternative is never silently substituted
- ADR 0021 — the whisper.cpp backend and its batch-only precedent
- **ADR 0022 (proposed) — the Parakeet v3 engine decision**, or an ADR 0021 amendment
