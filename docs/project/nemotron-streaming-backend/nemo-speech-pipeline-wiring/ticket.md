# Nemotron pipeline wiring (composition root)

- **Priority:** P1
- **Status:** in progress
- **Complexity:** M
- **Epic:** ../epic.md

## Current state / context

Tickets 57–59 are **merged**: an acquirable engine+model, a batch adapter, and the pure-streaming
driver. This ticket makes the engine **reachable and working end to end** by wiring it into the
**composition root**, `src/ges2t/pipeline.py`. It is deliberately scoped to `pipeline.py` (the earlier
combined ticket mixed this with `doctor`; the user split them — `doctor` is ticket 61).

**Main is transiently in a gap until this ticket lands:** `nemo_speech` is selectable by name
(ticket 58) and advertises `supports_streaming = True` (59), but it is **not in
`_TRANSCRIBER_BUILDERS`**, so it cannot be built/run yet, and `_streaming_chunk_seconds` is still
Whistle-hardcoded. The default engine and all existing behaviour are unchanged.

Three things live here:

1. **Register the builder.** `pipeline.py` has a `_TRANSCRIBER_BUILDERS` registry
   (`pipeline.py:250`) that maps an engine name to its constructor — the one place concrete engines are
   chosen (ADR 0010). `nemo_speech` needs its builder registered and dispatched.
2. **Generalise `_streaming_chunk_seconds`.** It currently **hardcodes `WhistleConfig`**
   (`pipeline.py:430`, the `WhistleConfig` reference at `:440`) — the one existing coupling that
   assumes the selected streaming engine is Whistle. With a second streaming engine it must resolve
   the chunk size from **whichever engine is selected**.
3. **Resolve the batch/stream text divergence — R1, inherited from ticket 59's review.** The engine's
   **offline** decode (`nemo_speech_asr_recognize_f32`, used by `transcribe()`) runs with
   `enable_automatic_punctuation=False` (the library default) and returns **lowercased, unpunctuated**
   text, while `stream()` — which sets the flag `True`, the configuration ticket 55 actually measured
   — returns **punctuated/capitalised** text. Both are correct for their own path, but the two disagree
   on the same audio. This is out of ticket 59's scope by design and is **owed here**.

Read [the design report §7.1 and §7.3](../../research/nemo-pure-streaming-integration/REPORT.md) and
[ADR 0022](../../../adr/0022-nemotron-streaming-pure-streaming-backend.md) first.

## Definition of done

- [ ] `_build_nemo_speech` is added and registered in `_TRANSCRIBER_BUILDERS` (`pipeline.py:250`), so
      `transcriber.engine = "nemo_speech"` constructs the `NemoSpeechTranscriber` and the service runs
      it — batch **and** streaming.
- [ ] `_streaming_chunk_seconds` (`pipeline.py:430`) is **generalised** so it is not Whistle-hardcoded:
      it resolves the chunk size from the selected engine's config (Whistle keeps its current value;
      Nemo reads `[engine.nemo_speech] chunk_seconds`, default **0.16**). Its signature/typing stays
      interface-typed (ADR 0019).
- [ ] Artifact presence is dispatched for the engine (as it is for whisper.cpp), so an un-acquired
      model or absent engine library is reported by the existing path rather than crashing.
- [ ] `tests/test_pipeline_streaming.py:244`
      (`test_defaults_come_from_whistle_config_when_the_block_is_absent`) still passes unchanged for
      `whistle`, and a **new case** pins the Nemo chunk default and the Nemo builder dispatch.
- [ ] An **end-to-end wiring test** proves the engine is selected from config and produces the
      expected transcribed output through the streaming session (with a fake library), with the
      committed-delta path (tickets 32–34) intact.
- [ ] No change to `streaming_session.py`, `service.py`, `socket_proto.py`, or `incremental_typing.py`;
      the Whistle and whisper paths are byte-for-byte behaviourally unchanged.
- [ ] **R1 resolved (the user raised this explicitly):** first **measure** — on the existing reference
      clips (reuse ticket 55's harness; **no download, no `sudo`, no microphone**) — the **offline
      `recognize_f32`** output against the **streaming-drained-to-final** output, same normalisation,
      `word_diff`. Record the numbers.
- [ ] **Then decide, from that evidence:** if streaming-drain is within noise of (or better than)
      offline, reimplement `transcribe()` as a **drain of `stream()`** — one code path, the divergence
      gone — and say so; otherwise keep both decoders and **align their configuration/casing** so the
      same audio yields consistent text. Document the choice and the numbers in the ticket and the
      docs. Do not pick a branch without the measurement.
- [ ] `uv run pytest` green, `uv run mypy src/ges2t tests` 0 errors; docs link check 0; README row
      updated.

## Notes

- **Ticket 59 is already merged, so this ticket completes the wiring.** After it lands,
  `transcriber.engine = "nemo_speech"` runs end to end for the first time. Re-review the streaming
  path's wiring together with this ticket (the design's "59 and 60 ship together" note).
- Generalising `_streaming_chunk_seconds` is the **only real refactor** in Epic 6 (report §7.3 point 3);
  keep it small and covered by a test — do not restructure the pipeline around it.
- `pipeline.py` is also touched by tickets 12/16/19 historically; keep the diff tight so it merges
  cleanly.

## Related tickets

- depends on: [../nemo-speech-engine-and-model-acquisition/ticket.md](../nemo-speech-engine-and-model-acquisition/ticket.md) (57) ·
  [../nemo-speech-pure-streaming/ticket.md](../nemo-speech-pure-streaming/ticket.md) (59)
- blocks: [../nemo-speech-docs/ticket.md](../nemo-speech-docs/ticket.md) (62)
- related ADRs: [ADR 0010](../../../adr/0010-transcriber-interface-design.md) ·
  [ADR 0015](../../../adr/0015-single-service-process-architecture.md) ·
  [ADR 0019](../../../adr/0019-code-style-typing-and-interfaces.md) ·
  [ADR 0020](../../../adr/0020-layout-aware-keycode-mapping.md) ·
  [ADR 0022](../../../adr/0022-nemotron-streaming-pure-streaming-backend.md)
