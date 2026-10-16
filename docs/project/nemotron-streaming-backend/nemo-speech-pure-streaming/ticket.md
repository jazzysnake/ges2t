# Nemotron pure-streaming integration (append-only, no window)

- **Priority:** P1
- **Status:** in review
- **Complexity:** M
- **Epic:** ../epic.md

## Current state / context

This is **the** ticket of Epic 6 and the mechanism [ADR 0022](../../../adr/0022-nemotron-streaming-pure-streaming-backend.md)
decided: the Nemotron cache-aware streaming engine emits **append-only interims**, so its output is
carried into the committed-delta path with **no 5 s sliding window, no two-pass agreement rule, and no
growing-buffer re-decode**. Ticket 55 measured the property (report §7): every interim is a strict
string-prefix extension of the previous one (1.0 across 20 runs + 5 chunk sizes), no seam duplication,
no cut-off or silence hallucination.

Ticket 58 built the batch adapter; this ticket adds its `stream()`. The **windowed path is a single
function** (`streaming.drive`, `streaming.py`), and its **only production caller is
`WhistleTranscriber.stream`** (`whistle.py:665`) — verified. So the bypass is **additive**: a sibling
driver, with the windowed `drive` untouched.

**Read first:** [the design report §3 (the seam), §4 (C-ABI delivery), §5 (commit policy)](../../research/nemo-pure-streaming-integration/REPORT.md)
and the [ADR 0022](../../../adr/0022-nemotron-streaming-pure-streaming-backend.md).

**Recommended shape (report §3.3) — additive in `src/ges2t/streaming.py`:**

```python
@runtime_checkable
class AppendOnlyDecoder(Protocol):
    def push(self, buffer: AudioBuffer) -> StreamingPass: ...
    def finish(self) -> str: ...

def drive_append_only(
    decoder: AppendOnlyDecoder,
    chunks: Iterable[AudioBuffer],
    *,
    chunk_seconds: float,
    on_partial: Callable[[LiveText], None] | None = None,
) -> Iterator[TranscriptionSegment]:
    """Fold an append-only interim stream into the committed-delta contract. No window:
    decoder.finish() is called exactly once, at end of input."""
```

Reuses, unchanged: `StreamingPass` (`streaming.py:44`), `_group_chunks` (`streaming.py:211`),
`TranscriptionSegment` / `LiveText` (`interfaces.py:103` / `:70`), and the tickets 32–34 committed
typing in `incremental_typing.py`.

**C-ABI (report §4.1, header `include/nemo_speech/asr.h`)** — the authoritative source is the acquired
header; confirm every symbol: `nemo_speech_asr_stream_push_f32` (buffer audio, **no decode**),
`nemo_speech_asr_stream_next` (pull one result; `NULL` when nothing is ready yet),
`nemo_speech_asr_stream_finish`, `nemo_speech_asr_stream_close`, `nemo_speech_asr_result_is_final`,
`nemo_speech_asr_result_audio_processed`. Delivery is **polled and non-blocking on the calling
thread** — there is **no callback**.

## Definition of done

- [x] `src/ges2t/streaming.py` gains the **additive** `AppendOnlyDecoder` Protocol and
      `drive_append_only`: **no window flush**, `finish()` called **exactly once** at end of input, and
      it yields the committed-delta sequence the session already consumes. `drive` and
      `StreamingDecoder` keep their signatures and behaviour.
- [x] `_NemoSpeechDecoder` in `transcribers/nemo_speech.py` drives the C ABI
      (`stream_push_f32` → drain `stream_next` → `stream_finish`) and returns `StreamingPass` values.
- [x] `NemoSpeechTranscriber.stream()` uses `drive_append_only`, and the class reports
      **`supports_streaming = True`**.
- [x] **Commit policy (ADR 0022 §5):** commit only the **completed-word prefix** of the interim and hold
      the trailing **partial token pending** — it is never injected. Committed text is **append-only and
      never revised**; only committed text is injected; the **typed watermark** and the **single
      trailing space** come from the existing `CommittedTyper` path (tickets 32–34), unchanged.
- [x] **The boundary rule is string-based**, because interims carry **no word timestamps** (only the
      final does — report §10.3). Do not try to use `word_times` on interims.
- [x] Nothing in `streaming_session.py`, `service.py`, `socket_proto.py`, or `incremental_typing.py`
      needs to change; if it does, stop and report — that invalidates the design.
- [x] `tests/test_streaming_append_only.py` (new) pins the driver: **no** window flush, exactly one
      final segment, committed cumulative/append-only, and **pending never in the transcript**.
      `tests/test_nemo_speech_stream.py` (new) drives the adapter over a **fake `NemoSpeechLibrary`**.
- [x] `tests/test_whistle_stream.py` (including `test_drive_is_the_windowing_implementation` at `:430`)
      still passes — the Whistle path is unchanged.
- [x] `uv run pytest` green, `uv run mypy src/ges2t tests` 0 errors; docs link check 0; README row updated.

## Notes

- **The falsifier for ADR 0022 (report §9.1).** If the interim stream **cannot** be carried into the
  committed path without a growing buffer or a second pass, **stop and set the ticket `blocked`** with
  the finding — do not add a window. That finding invalidates ADR 0022 and must be escalated.
- **Threading:** `stream()` runs on the session worker thread (`streaming_session.py:148`,
  `ges2t-streaming-worker`) and must stay **off the service lock** (ticket 21). Because delivery is
  polled on the calling thread there is no re-entrancy — but do not introduce a blocking wait.
- **Keep endpointing off** for this engine (report §5.4); the silence gate's behaviour is specified
  there.
- Engine chunks are 160 ms (`chunk_seconds` default 0.16); the driver regroups arbitrary buffer sizes
  by sample count via `_group_chunks`.
- Tickets **59 and 60 ship together** (report §8) — the engine is not reachable end-to-end until the
  composition root feeds it.

## Related tickets

- depends on: [../nemo-speech-batch-adapter/ticket.md](../nemo-speech-batch-adapter/ticket.md) (58)
- blocks: [../nemo-speech-pipeline-wiring/ticket.md](../nemo-speech-pipeline-wiring/ticket.md) (60)
- related ADRs: [ADR 0010](../../../adr/0010-transcriber-interface-design.md) ·
  [ADR 0011](../../../adr/0011-streaming-sliding-window.md) (**not amended** — this ADR is engine-scoped) ·
  [ADR 0015](../../../adr/0015-single-service-process-architecture.md) ·
  [ADR 0019](../../../adr/0019-code-style-typing-and-interfaces.md) ·
  [ADR 0022](../../../adr/0022-nemotron-streaming-pure-streaming-backend.md)
