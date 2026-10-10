# whisper framework overhead measurement

- **Priority:** P1
- **Status:** to do
- **Complexity:** S
- **Epic:** ../epic.md

## Current state / context

ADR 0001 budgets the **non-model** part of a dictation round trip at **< 1 s**. For the Whisper path
that budget covers the subprocess spawn, the model load, the WAV hand-off, and the transcript parse —
**none of which has been measured for a real adapter.** The provider research measured the whole
`whisper-cli` wall time (~1.7–3 s for 25 s of audio on Vulkan) and separated a **~0.8 s fixed
per-call cost** (spawn + loading 539 MB of `medium q5_0`), but no ges2t adapter existed to measure the
framework part in isolation.

This is a **measurement ticket**: it changes no shipped behaviour. If the overhead is over budget, the
finding is a follow-up (e.g. a resident-model shape), not a fix folded in here.

## Definition of done

- [ ] Measure, on the reference machine and with the real adapter from
      `../whisper-transcriber-adapter/ticket.md`, the **framework overhead** of one batch dictation
      round trip — the non-model part per ADR 0001 — and report it as a **number with its method**.
- [ ] Separate and report at least: process spawn, model load, WAV write, transcript parse, and total.
      Reuse the research harness where it fits rather than writing a new one from scratch.
- [ ] Compare the **subprocess** shape against a **resident-model** (`ctypes`) shape for the load
      component, and state whether the resident shape is worth a follow-up ticket at the measured size.
- [ ] **Do not change any behaviour**: no code in `src/ges2t/` may be modified except, if unavoidable,
      a non-behavioural instrumentation hook that the ticket explains and that is removed or gated.
- [ ] Report raw numbers and the exact commands; a claim that reads as measured must have been measured.
- [ ] **User-owned**: the end-to-end confirmation that a real dictation with the whisper backend meets
      the budget is prepared as steps and handed over, unticked.
- [ ] `uv run pytest` and `uv run mypy src/ges2t tests` unchanged/green.
- [ ] `001-comment.md` records the numbers, the method, and the falsification note.

## Notes

- The measurement needs the model and the binary on this machine; it is **not** headless. Do **not**
  record from the microphone — a saved clip is sufficient for a framework measurement, and the
  user-owned step covers the live round trip.
- **No keystrokes, no clipboard, no windows, no microphone, no `sudo`, no service changes, no network.**
- Bound every run with `timeout`; report the load state (a shared iGPU is contended by browsers and
  Discord, which was measured to roughly halve the Vulkan throughput).

## Related tickets

- depends on: ../whisper-transcriber-adapter/ticket.md
- related: ../../research/whisper-cpp-provider/REPORT.md
- related ADRs: ../../../adr/0001-python-implementation-language.md (the latency budget), ../../../adr/0014-testing-and-quality-bar.md
