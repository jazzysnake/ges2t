# Nemotron pure-streaming integration — how to wire the append-only interim stream without a sliding window

- **Priority:** P0
- **Status:** in review
- **Complexity:** M
- **Epic:** ../epic.md
- **Type:** discovery / code investigation (no `src/` change, no `tests/` change, no `sudo`, no microphone)
- **Branch / worktree:** `feat/nemo-pure-streaming-integration` in `.worktrees/nemo-pure-streaming-integration`
- **Decision it serves:** [ADR 0022](../../../adr/0022-nemotron-streaming-pure-streaming-backend.md) (accepted — user sign-off 2026-10-14)

## Current state / context

**The user's question, verbatim:** *"kick off a discovery that looks into the code and tells me how
this streaming model can be integrated without requiring two passes of a sliding window and just using
the pure streaming stuff."*

**The decision is already made** (ADR 0022): `nvidia/nemotron-3.5-asr-streaming-0.6b` on
`NVIDIA/NeMo-Speech.cpp` is integrated as an opt-in `nemo_speech` engine using the engine's **pure
streaming** output — **no 5 s sliding window, no two-pass agreement rule, no growing-buffer
re-decode**. **ADR 0011 is not amended.** Ticket 55 measured the engine committing **append-only**
(1.0 prefix-extension across 20 runs + 5 chunk sizes, no seam/cut-off/silence hallucination). This
ticket produces the **mechanism** ADR 0022 deliberately left open — by **reading the code**, not by
assuming a design.

**The existing streaming machinery to investigate** (verify names/shape as you read; `file:line`
references required in the report):

| file | what it is |
|---|---|
| `src/ges2t/interfaces.py` | the `Transcriber` / audio / injector Protocols (ADR 0010) |
| `src/ges2t/streaming.py` | the **generic 5 s windowed-commit driver** (ticket 13) — where the window and the two-pass agreement rule live |
| `src/ges2t/streaming_session.py` | the dictation session that consumes committed deltas |
| `src/ges2t/incremental_typing.py` | the committed-delta typing path (tickets 32–34) |
| `src/ges2t/transcribers/whistle.py` | the existing **streaming** adapter (the closest shape) |
| `src/ges2t/transcribers/whisper_cpp.py` | the **batch** adapter (`supports_streaming = False`) |
| `src/ges2t/pipeline.py`, `src/ges2t/service.py`, `src/ges2t/socket_proto.py` | selection + wiring + the service loop and its lock |
| `src/ges2t/config.py`, `src/ges2t/identifiers.py` | engine selection, identifiers (Epic 4), streaming config |
| `tests/test_streaming.py`, `tests/test_streaming_session.py`, `tests/test_incremental_typing.py`, `tests/test_pipeline_streaming.py` | the pinned guarantees for the window/commit path |

**The engine side, already verified and on disk** (no download needed):
- The extracted prebuilt engine is at
  `~/.cache/ges2t-discovery/nemotron-3-5-asr-streaming-measurement/engine/` — read its **C headers**
  (`libnemo_speech_asr_c` / `nemo_speech_asr*.h`) for the streaming API.
- Ticket 55's harness already drives that C ABI and records **every interim**:
  `docs/project/research/nemotron-3-5-asr-streaming-measurement/measure_nemo_streaming.py` (+
  `analyze_streaming.py`). Read it before proposing anything — it is the proof that interims are
  reachable in process.

## Definition of done

Produce **`docs/project/research/nemo-pure-streaming-integration/REPORT.md`** (written incrementally)
answering all of the following. Every structural claim needs a `file:line` reference or a real command
output — **no design from memory**.

1. **A precise map of the current streaming path**: what each file in the table above does, and
   specifically **where the 5 s window and the two-pass agreement rule are imposed**, how committed
   deltas are produced, and how they reach the injector. Quote the key functions with `file:line`.
2. **The exact seam for a pure-streaming engine.** State which `Transcriber` (or session) Protocol
   methods a cache-aware adapter must implement, and **how the window is bypassed**: e.g. (a) the
   existing driver gains a "no-window / append-only" strategy; (b) a dedicated pure-streaming session;
   (c) something else the code suggests. Give the concrete class/Protocol shape (names, signatures,
   where each lives) and say what existing tests would break or need pinning.
3. **C-ABI interim delivery.** From the engine headers + ticket 55's harness: how interims arrive
   (callback, polled push, blocking read), **on which thread**, their cadence and the partial/final
   distinction — and how that composes with the **service lock** (ticket 21 kept the status UI outside
   the lock) and the audio capture loop. Call out any blocking or re-entrancy hazard explicitly.
4. **Commit policy.** Specify how **word-boundary commits** with the trailing partial held **pending**
   is implemented on top of `incremental_typing.py` and the tickets 32–34 interface, including the
   typed watermark and the single trailing space. Show the exact reused functions if they exist.
5. **Alternatives + recommendation.** At least two candidate designs with their trade-offs (blast
   radius on `streaming.py`/`streaming_session.py`, test churn, risk to the Whistle path), and one
   recommended design.
6. **A proposed implementation ticket breakdown** — for each ticket: slug, one-line scope, the files
   it touches, its dependencies, and a size (XS–XL). This becomes the Epic 6 integration tickets.
7. **Risks, confounds, and an explicit what-would-falsify** consistent with ADR 0022's falsifier: if
   the interim stream **cannot** be carried into the committed path without a growing buffer or a
   second pass, say so and stop — that invalidates ADR 0022 and must be escalated, not worked around.
8. **`001-comment.md`**: the recommended design in a paragraph, the ticket breakdown, and anything to
   escalate (including any contradiction with ADR 0022).
9. No file under `src/ges2t/` or `tests/` is modified; no engine/model/audio committed; the docs link
   check reports **0 broken references**; `git status` clean; everything committed on
   `feat/nemo-pure-streaming-integration`; `ticket.md` status and the `docs/project/README.md` row
   updated to `in review`.

## Notes

- **Hard boundaries** (discovery-agent contract): no `sudo`, no package installs; **no microphone**;
  no clipboard/keystrokes/windows/notifications; no production code; never commit to `main`. The
  engine and model are already in scratch — this is a **reading and design** ticket, not a new
  measurement (a small experiment to confirm a header's contract is fine, but the ticket's output is a
  design, not numbers).
- This ticket is deliberately **before** any integration ticket: the user wants the mechanism worked
  out from the code first.
- Read `docs/project/research/nemotron-3-5-asr-streaming-measurement/REPORT.md` §7 (the append-only
  proof) and the ADR 0022 draft before starting.
- Keep the report curated; write it incrementally and commit per step (long runs die on connection
  errors).

## Related tickets

- depends on: ticket 55 (`../../nemotron-streaming-backend/nemotron-3-5-asr-streaming-measurement/ticket.md`) — done
- blocks: the Epic 6 integration tickets (created from this ticket's breakdown)
- related ADRs: [ADR 0022](../../../adr/0022-nemotron-streaming-pure-streaming-backend.md) (new,
  engine-specific — the decision this implements) · [ADR 0011](../../../adr/0011-streaming-sliding-window.md)
  (left **untouched**) · [ADR 0010](../../../adr/0010-transcriber-interface-design.md)
