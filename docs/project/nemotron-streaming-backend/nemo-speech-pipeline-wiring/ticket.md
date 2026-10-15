# Nemotron pipeline wiring (composition root)

- **Priority:** P1
- **Status:** to do
- **Complexity:** M
- **Epic:** ../epic.md

## Current state / context

Tickets 57–59 build the pieces: an acquirable engine+model, a batch adapter, and the pure-streaming
driver. This ticket makes the engine **reachable and working end to end** by wiring it into the
**composition root**, `src/ges2t/pipeline.py`. It is deliberately scoped to `pipeline.py` (the earlier
combined ticket mixed this with `doctor`; the user split them — `doctor` is ticket 61).

Two concrete changes live here:

1. **Register the builder.** `pipeline.py` has a `_TRANSCRIBER_BUILDERS` registry
   (`pipeline.py:250`) that maps an engine name to its constructor — the one place concrete engines are
   chosen (ADR 0010). `nemo_speech` needs its builder registered and dispatched.
2. **Generalise `_streaming_chunk_seconds`.** It currently **hardcodes `WhistleConfig`**
   (`pipeline.py:430`, the `WhistleConfig` reference at `:440`) — the one existing coupling that
   assumes the selected streaming engine is Whistle. With a second streaming engine it must resolve
   the chunk size from **whichever engine is selected**.

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
- [ ] `uv run pytest` green, `uv run mypy src/ges2t tests` 0 errors; docs link check 0; README row
      updated.

## Notes

- **Tickets 59 and 60 ship together** (report §8): the streaming engine is only reachable end to end
  once both land, so review them as a pair if they are merged close together.
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
