# Epic: whisper.cpp batch backend

- **Priority:** P1
- **Status:** to do
- **Epic owner:** orchestrator
- **Research:** [../research/whisper-cpp-provider/REPORT.md](../research/whisper-cpp-provider/REPORT.md) and
  [../research/whisper-cpp-streaming/REPORT.md](../research/whisper-cpp-streaming/REPORT.md)

## Goal

Add **whisper.cpp** as a second `Transcriber` backend, selected by configuration, supporting the full
whisper.cpp model range (`tiny` … `large-v3-turbo`, `q5_0`/`q8_0`), with **`medium q5_0` as the
default model** — and make it the **shipped default backend**.

It is **batch-only**. On the reference machine `medium q5_0` transcribes a 25 s utterance in
**~1.7–3 s** (Vulkan) at **WER ≈ 0.062**, against Whistle's **0.281** (batch) / **0.391** (the
current streaming default) on the same recording — roughly **4.5× fewer errors**.

## Why it matters

Accuracy is the user's stated reason for hesitating to daily-drive, and the provider research puts the
dominant remaining errors in **model capacity** — the risk ADR 0005 accepted. whisper.cpp is the
escape hatch ADR 0006 already names; it is MIT end-to-end (whisper.cpp and the weights), it was
measured on this machine, and it needs no closed binary.

The cost is explicit and measured: whisper.cpp's partial-window output is **not stable enough** for
ges2t's commit-cadence streaming (seam duplication, flush-dominated commits, cut-off and silence
hallucination, 2.3–4.8× worse WER — see the streaming report). So this epic **accepts that the
whisper path does not type incrementally**: text appears when the utterance ends. That is the
trade the user chose, and it is recorded in ADR 0021.

## Deliverable

A config-selectable `whisper_cpp` backend behind the existing `Transcriber` Protocol, defaulting to
`medium q5_0`, with pinned model acquisition, `doctor` coverage, a measured framework overhead, and
the batch-only limitation documented — while Whistle remains selectable.

## Acceptance criteria

- [ ] `uv run pytest` green (headless) and `uv run mypy src/ges2t tests` 0 errors — ADR 0014/0019.
- [ ] `transcriber.backend = "whisper_cpp"` selects it, and it is the **shipped default**;
      `[transcriber.whisper_cpp]` names the model (default `medium q5_0`), quant, threads and GPU
      use. Whistle stays fully selectable and unchanged.
- [ ] The decode is requested with **`-nt` pinned in code**, never inherited from the CLI default —
      the provider report's headline is `-nt`-specific (0.062 vs 0.156), see
      [../research/whisper-cpp-provider/004-addendum-nt-decode.md](../research/whisper-cpp-provider/004-addendum-nt-decode.md).
- [ ] `supports_streaming = False`; with whisper selected and `streaming.enabled = true`, the batch
      path is used and the limitation is **surfaced** (doctor/log), never silent.
- [ ] Model acquisition downloads and **sha256 + size-verifies** the pinned ggml files, idempotently,
      from the canonical `ggerganov/whisper.cpp` host; no runtime network outside acquisition (ADR 0018).
- [ ] `ges2t doctor` checks the `whisper-cli` binary, the resolved model, and Vulkan availability,
      and states the consequence of running CPU-only.
- [ ] **Framework overhead measured and reported as a number** with its method (ADR 0001: non-model
      round trip < 1 s), including the resident-model-vs-subprocess trade.
- [ ] **Manual, on the reference machine:** with `transcriber.backend = "whisper_cpp"`, speak → the
      correct text is typed into a focused editor **and** a terminal, on par with the measured
      transcript for the same audio (user-owned step).
- [ ] Every ticket's `docs/project/README.md` row is `done`.

## Out of scope for this epic

- Streaming / incremental typing on whisper (measured not viable here).
- Bundling `whisper-cli`, `ggml`, or the model weights (built/acquired outside the repo, as Whistle's
  engine already is).
- Automatic fallback between backends (ADR 0020 — a configured alternative is never substituted).
- Removing Whistle, a GUI model picker, or a model-download progress UI.
- Any number/spelling normalisation of the transcript (**out of scope project-wide**; the residual
  `4,000`/`metres` WER is formatting, not mishearing).

## Decisions this epic implements / amends

- ADR 0005 — the Whisper/Needle backend (**this adds the escape hatch it names**)
- ADR 0006 — closed-engine accepted risk (whisper.cpp is the open, auditable alternative)
- ADR 0010 — the `Transcriber` Protocol (unchanged; whisper sits behind it)
- ADR 0011 — streaming (this epic records that the default backend is batch-only)
- ADR 0014 — testing and quality bar
- ADR 0018 — no runtime network
- ADR 0019 — full annotations, interface-typed signatures
- ADR 0020 — a configured alternative is never silently substituted
- **ADR 0021 (new, authored with this epic) — whisper.cpp as a batch-only backend; the shipped
  default flips to it, accepting the loss of incremental typing. Presented in full for the user's
  sign-off before the merge that carries it.**
