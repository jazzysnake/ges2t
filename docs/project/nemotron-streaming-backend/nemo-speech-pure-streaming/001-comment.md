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

## Step 2 — `transcribers/nemo_speech.py` (the adapter + decoder), + tests

* `NemoSpeechLibrary` gained the streaming half of the same real library:
  `open_stream` / `push_audio` / `next_result` / `finish_stream` / `close_stream`, and a frozen
  `NemoSpeechStreamResult` (transcript, is_final, audio_processed_s, language) — ADR 0019. The one
  `CtypesNemoSpeechLibrary` implements both halves; the ticket-58 fake was extended to model the
  streaming lifecycle too (it must not be simpler than the real library).
* `_NemoSpeechDecoder` (`push` -> `StreamingPass`, `finish` -> whole final text) drives the real ABI:
  `stream_push_f32` (no decode) -> drain `stream_next` until NULL -> `stream_finish` -> drain the
  final. `stream_close` runs in a `finally` (`_stream_append_only`), so an abandoned generator cannot
  leak the native handle.
* `_committed_prefix` — the **string** word boundary (`rstrip` then cut at the last space), exactly the
  report §5.1 rule; interims carry no word timestamps (report §10.3).
* `NemoSpeechTranscriber.supports_streaming = True`; `stream()` returns
  `drive_append_only(_NemoSpeechDecoder(...), chunks, chunk_seconds=self.config.chunk_seconds)`.
  `NemoSpeechStreamingUnsupportedError` (ticket 58's refusal) is removed as dead code.
* **No change** to `streaming_session.py`, `service.py`, `socket_proto.py`, `incremental_typing.py`.
* Tests: new `tests/test_streaming_append_only.py` (driver) and `tests/test_nemo_speech_stream.py`
  (adapter + payload + guards + opt-in real seam). The two ticket-58 tests that pinned the old batch-only
  refusal were repurposed (not weakened) — `supports_streaming is True` and `stream()` now yields the
  sequence — and `test_whisper_backend_config.py`'s cross-engine capability guard dropped its
  ticket-58 `nemo_speech` exception (the two sets now agree).

```
$ uv run pytest -q
1373 passed, 3 skipped in 6.10s
$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 72 source files
```

## Real-seam finding — `enable_automatic_punctuation` is required, not cosmetic

Driving the **real** engine (CPU, pinned v0.2.0 GGUF, `clip5.wav`) exposed an engine behaviour the
report did not record, so I measured it before deciding (read-only probe in `/tmp`, no `src/` change,
no network/sudo/mic):

```
last interim: 'He hoped there would be stew for dinner, turnips and carrots and bruised po'
final       : 'he hoped there would be stew for dinner turnips and carrots and bruised potatoes'
final.startswith(last interim)? False          # library default: punc OFF
```
With `enable_automatic_punctuation = True` (the request flag ticket 55's harness sets):

```
last interim: 'He hoped there would be stew for dinner, turnips and carrots and bruised po'
final       : 'He hoped there would be stew for dinner, turnips and carrots and bruised potatoes'
final.startswith(last interim)? True
```

So the **library default** (`punc=False`) makes the *final* lowercased and punctuation-stripped while
the interims stay capitalised — the final then does **not** extend the last interim, and
`CommittedTyper._extend` would raise `IncrementalTypingError` at `finish`. `enable_automatic_punctuation
= True` restores the measured, append-only relationship. I set it in `open_stream` and pinned it with
the real-seam test (mutation G below). `enable_word_time_offsets` does not affect the text and is left
off (report §10.3: the boundary is string-based). This is the ADR-0022 configuration ticket 55
measured, not a workaround — and the falsifier did **not** fire: the append-only stream carries into
the committed path with no growing buffer and no second pass (the re-decode is *inside* the engine,
report §9.1(3)).

**Orchestrator note:** the batch path (`transcribe()`) still uses the library default (punc off), so it
returns lowercased, unpunctuated text, while `stream()` returns capitalised, punctuated text. That is a
real batch/stream divergence; it is out of ticket 59's scope (ticket 58 owns `transcribe()`), so I did
not touch it. Ticket 60's wiring should decide whether to align them.

## Falsification — every guard broken, watched to fail, reverted; `src/` byte-identical

`sha256sum -c` against the pre-falsification backup after every revert: both `src/` files `OK`
(`streaming.py a4bbbc27…`, `nemo_speech.py 6cbd1dab…`).

* **A — driver flushes on a window** (`decoder.finish()` mid-loop):
  `test_finish_is_called_exactly_once_at_the_very_end` → `assert ['push','finish',…] == ['push',…]` **FAILED**.
* **B — driver injects the pending tail** (`committed=final_text + pending`):
  `test_pending_never_reaches_the_transcript` → `assert False` (POISON in committed) **FAILED**.
* **D — skip the word-boundary hold** (`_committed_prefix` returns the whole interim):
  `test_the_trailing_partial_word_is_held_pending_and_never_committed` and
  `test_committed_prefix_is_the_last_completed_word` **FAILED** (`'And so my fel' == 'And so my'`).
* **E — remove the append-only guard**: `test_the_decoder_raises_when_an_interim_revises_earlier_text`
  → `DID NOT RAISE NemoSpeechAppendOnlyError` **FAILED**.
* **F — remove the mid-stream-final guard**: `test_a_mid_stream_final_is_rejected`
  → `DID NOT RAISE NemoSpeechDecodeError` **FAILED**.
* **G — drop `enable_automatic_punctuation`** (real seam): `test_real_streaming_engine_appends_without_a_window`
  → `AssertionError: assert False` (final does not extend the last interim) **FAILED**.
* **H — remove the missing-final guard**: `test_a_finish_without_an_end_of_stream_final_is_rejected`
  → `DID NOT RAISE NemoSpeechDecodeError` **FAILED**.
* **I — remove the abandoned-generator `close()` finally**:
  `test_the_stream_is_closed_when_the_generator_is_abandoned` → `assert [] == [2]` **FAILED**.
