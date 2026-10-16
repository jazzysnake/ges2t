# 001 — implementer comment (ticket 59)

Branch `feat/nemo-speech-pure-streaming`, worktree `.worktrees/nemo-speech-pure-streaming`,
base `a6da1c7` (`docs(state): ticket 59 dispatched`). Implementing the pure-streaming
append-only mechanism ADR 0022 decided, per the design report §3–§5.

Status of the ADR-0022 **falsifier**: not fired (rationale below, before code).

## Reading done first

Ticket + epic + [ADR 0022](../../../adr/0022-nemotron-streaming-pure-streaming-backend.md) +
[report §3/§4/§5/§9](../../research/nemo-pure-streaming-integration/REPORT.md), and the code it
builds on: `streaming.py` (`drive`/`StreamingDecoder`), `transcribers/whistle.py`
(`stream` → `drive`), `transcribers/nemo_speech.py` (ticket 58), `incremental_typing.py`
(`CommittedTyper`), `streaming_session.py` (the worker + `_record`).

The installed C header at
`~/.cache/ges2t-discovery/nemotron-3-5-asr-streaming-measurement/engine/nemo-speech-0.2.0-linux-x86_64-vulkan/include/nemo_speech/asr.h`
confirms every symbol the ticket names: `nemo_speech_asr_streaming_recognize`,
`nemo_speech_asr_stream_push_f32` (**no decode**), `nemo_speech_asr_stream_next` (NULL when nothing
ready), `nemo_speech_asr_stream_finish`, `nemo_speech_asr_stream_close`,
`nemo_speech_asr_result_is_final`, `nemo_speech_asr_result_audio_processed`,
`nemo_speech_asr_result_transcript`, `nemo_speech_asr_result_destroy`. **No callback** anywhere in
the ASR ABI — delivery is polled on the calling thread, exactly as report §4.1 says.

Two engine-default facts I checked before writing code (they decide the seam, no code change needed):

* `share/doc/nemo-speech/docs/asr/configuration.md` gives `asr.streaming.chunk_size` default
  **0.16** and `asr.streaming.rnnt_right_context` default **1** — i.e. the recognizer ticket 58
  already creates with `streaming = NULL` uses the *measured* 160 ms / rc=1 config. So the decoder
  does not need to (and must not) rebuild the recognizer or set a streaming substruct.
* `asr.endpointing.enable` default **false** → one `is_final` per stream, only after
  `stream_finish`; v1 does not enable it (report §5.4).

<!-- appended incrementally as work proceeds -->

## The ADR-0022 falsifier — not fired (before writing code)

The falsifier is: *the interim stream cannot be carried into the committed-delta path without a
growing buffer or a second pass*. It does not fire, for the report's §9.1 reasons, re-checked here:

1. **Append-only** — ticket 55 measured it (1.0 across 20 runs + 5 chunk sizes), report §10
   re-derived it from the committed traces. The commit rule is a pure string prefix
   (`_committed_prefix`), so no re-decode is needed.
2. **Boundary is string-computable** — interims carry no word timestamps (final only, §10.3); the
   boundary is the last space in the interim. If the engine ever revised an earlier token the decoder
   would violate append-only and raise (guard below), the loud failure report §9.1(2) expects.
3. **No growing buffer** — the adapter keeps only the cumulative committed **string** (O(utterance),
   the same the windowed path keeps), plus the current interim string; each chunk is pushed straight
   to the engine. The caches that grow live *inside* the engine.
4. **Off the service lock** — the ABI is driven on `ges2t-streaming-worker` (`streaming_session.py`
   `_run`), which never takes the service lock; delivery is polled on the calling thread with no
   callback, so no re-entrancy (report §4.1/§4.2).
5. **Interims are incremental, not whole-file** — `stream_push_f32` (no decode) + `stream_next`
   deliver them; 1,841 were recorded.

## Step 1 — `src/ges2t/streaming.py` (additive), `tests/test_streaming_append_only.py` (new)

* Added the `AppendOnlyDecoder` Protocol (`push(buffer) -> StreamingPass`, `finish() -> str`) and
  `drive_append_only(decoder, chunks, *, chunk_seconds, on_partial=None)`.
* `drive` and `StreamingDecoder` are **byte-for-byte untouched** — `git diff` shows only additions.
* Reused, unchanged: `StreamingPass`, `_group_chunks`, `LiveText`/`TranscriptionSegment`.
* `finish()` is called **exactly once**, unconditionally, at end of input (empty input included), and
  its returned whole final transcript becomes the final segment's cumulative `committed`; there is no
  window and no boundary flush.

```
$ uv run pytest tests/test_streaming_append_only.py -q
14 passed in 0.11s
$ uv run pytest tests/test_streaming.py tests/test_whistle_stream.py -q
53 passed in 0.72s
```
