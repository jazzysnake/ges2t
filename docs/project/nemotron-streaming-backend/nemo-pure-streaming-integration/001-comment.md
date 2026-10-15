# 001-comment — ticket 56 (pure-streaming integration): the mechanism, and the gate for the integration tickets

**Verdict: PROCEED.** The sliding window can be bypassed **cleanly**, and ADR 0022's falsifier did
**not** fire. The 5 s window and the two-pass agreement rule are not architectural: they live in
`ges2t.streaming.drive` (`src/ges2t/streaming.py:95`), whose **only production caller** is
`WhistleTranscriber.stream` (`src/ges2t/transcribers/whistle.py:665`). The session
(`StreamingDictationSession._run`, `src/ges2t/streaming_session.py:268`) and the service talk only to
the `Transcriber.stream` Protocol and are window-blind. A `nemo_speech` adapter therefore bypasses the
window **by construction** — it returns its own generator from `stream` and never calls `drive`.

Report: [`../../research/nemo-pure-streaming-integration/REPORT.md`](../../research/nemo-pure-streaming-integration/REPORT.md)

## Recommended design (one paragraph)

Add a small, **additive** append-only driver to `ges2t/streaming.py` — `drive_append_only` plus a
narrow `AppendOnlyDecoder` Protocol (`push(buffer) -> StreamingPass`, `finish() -> str`) — reusing the
existing chunk-regrouping `_group_chunks` (`streaming.py:211`) and the existing `StreamingPass` /
`TranscriptionSegment` / `LiveText` vocabulary. The `nemo_speech` adapter drives the C ABI
(`stream_push_f32` + `stream_next` + `stream_finish`) on the session worker thread and, for each
interim, commits the prefix up to the last **string** word boundary and holds the trailing token as
`pending` (the engine returns **no word timestamps on interims** — measured: `word_times` is non-empty
only on the final — so the boundary must be string-based). The cumulative `committed` string is fed
unchanged to the existing `CommittedTyper`, which types exactly once and applies ADR 0012's single
trailing space at `finish`. No growing buffer (only the committed string, exactly as the windowed path
already keeps at `streaming.py:167`); no second pass; the windowed `drive` and the Whistle path are
untouched.

## Why the window can be bypassed cleanly (structural evidence)

- `grep -rn "streaming.drive" src/` → one production caller: `whistle.py:665`.
- The session consumes `transcriber.stream(...)` (`streaming_session.py:268`); the service consumes
  `session.partials()` / `session.finish()` only. Neither imports `streaming`.
- So no window branch, no flag, and no change to `streaming_session.py`, `service.py`,
  `socket_proto.py`, or `incremental_typing.py` is required.

The C ABI is **polled and non-blocking on the calling thread** (`stream_next` returns a NULL handle when
no result is ready; there is no callback), and it runs on `ges2t-streaming-worker`, never under the
service lock (ticket 21's rule is preserved). Hazards called out in the report: decode is synchronous
in `stream_next` (~24–47 ms/chunk on Vulkan); `finish()` joins the worker unboundedly (existing
accepted risk); the recognizer is process-global, so one stream at a time and a fresh stream handle per
recording; `last_error` is thread-local; **endpointing must stay off** (a mid-stream `is_final` would
split one hotkey-held utterance).

## Proposed implementation ticket breakdown (Epic 6)

| # | slug | scope | files | deps | size |
|---|---|---|---|---|---|
| 57 | `nemo-speech-engine-and-model-acquisition` | pin/acquire/verify the prebuilt archive + q8_0 GGUF (sha256+size); `setup`/`doctor` plumbing | `identifiers.py`, `nemo_engine.py` (new), `model_store.py`, `setup_command.py`, `cli.py` | 56 | M |
| 58 | `nemo-speech-batch-adapter` | `[engine.nemo_speech]` config + `NemoSpeechTranscriber` over a `NemoSpeechLibrary` ctypes Protocol; batch `recognize_f32`; `supports_streaming=False` for now | `transcribers/nemo_speech_config.py` (new), `transcribers/nemo_speech.py` (new), tests | 57 | M |
| 59 | `nemo-speech-pure-streaming` | `streaming.drive_append_only` + `AppendOnlyDecoder` + the string word-boundary rule; `NemoSpeechTranscriber.stream`; flip `supports_streaming=True` | `streaming.py` (additive), `nemo_speech.py`, new tests | 58 | M |
| 60 | `nemo-speech-pipeline-and-doctor` | register the builder; **generalise `_streaming_chunk_seconds`** (`pipeline.py:430`); artifact-presence dispatch; `doctor` checks (library, model, Vulkan/CPU); end-to-end wiring test | `pipeline.py`, `doctor.py`, tests | 57, 59 | M |
| 61 | `nemo-speech-docs-and-adr-signoff` | README/config/engine docs; document the Hungarian + CPU/Vulkan limits; **present ADR 0022 in full for sign-off** | `docs/` | 60 | S |

**Ticket 59 is the only one that can invalidate ADR 0022.** If it cannot implement `drive_append_only`
without a growing buffer or a second pass, it must stop and escalate, not work around it.

## Escalate / flag

1. **The mechanism is settled; the wiring is the substantial part.** A third engine touches the
   composition root, acquisition and `doctor` (report §7). The one real refactor is
   `_streaming_chunk_seconds` (`pipeline.py:430`), which currently hardcodes `WhistleConfig` — the
   honest cost of a third engine.
2. **`window_seconds` is meaningless for this engine** (ADR 0022). The `[engine.nemo_speech]` block
   must **warn** if it is set (the generic unknown-key path does this once the block does not declare
   it) rather than silently accept a no-op key.
3. **ADR 0022 must be presented in full for user sign-off** before the merge that carries it.
   **ADR 0011 is not amended and not edited.**
4. **Do not relay Hungarian accuracy or CPU/Vulkan parity** — both remain unmeasured / false
   (ticket 55 §1, §6).
