# Whistle streaming adapter: implement `stream()` on the Whistle transcriber

- **Priority:** P1
- **Status:** done
- **Complexity:** M
- **Epic:** ../epic.md

## Current state / context

`../../core-dictation-pipeline/whistle-transcriber-adapter-batch/ticket.md` implemented
`WhistleTranscriber` with a `stream()` stub raising `NotImplementedError`. This ticket
implements it. `../streaming-window-engine/ticket.md` provides the generic
`StreamingDecoder`/`drive()` helper this adapter plugs into.

What the probe established about the engine's streaming API (`needle.h` ≥ 3.2.0, quoted in
ADR 0011):

- `needle_stream_transcribe_process(pcm, samples, language, keywords, out, out_capacity)`
  appends audio and returns `{"text","words","pending","language","received","pass_ms"}` where
  `text` is the words **this call committed** and `pending` is the unconfirmed tail.
- `needle_stream_transcribe_stop(out, out_capacity)` commits the tail and **resets the
  stream**; the next `process()` starts a new one. It measured **0.0 ms**.
- The engine commits **words two consecutive passes agree on**; streaming always exposes
  `words[]` with start/end/probability; streaming results carry **no `ttft_ms`/`decode_tps`**,
  only `pass_ms`.
- The `cactus-needle` package exposes this as a generator:
  `Whistle.stream(chunks, language=None, keywords=None)` → one dict per chunk plus one tail
  dict; `needle.stream(...)` likewise. It is an in-process generator (ADR 0007).

**The subtlety this ticket must resolve empirically, not by assumption:** the package's
`stream()` generator owns the engine's `stop()` call — it presumably issues it when its input
iterator is exhausted. Our 5 s window needs a `stop()` at a boundary *we* choose. The
expected approach is therefore **one package-level `stream()` generator per window**, driven
to exhaustion over exactly the window's chunks, so each generator's own terminal `stop()`
becomes our window boundary. The implementer must verify this produces no duplication or loss
at the seams, and record the verification. If that approach cannot be made correct, do **not**
improvise a different integration; leave a `002-comment.md` with the evidence and set the
ticket `blocked`.

Relevant decisions: ADR 0011 (window/commit model, measured numbers), ADR 0007 (integration
path), ADR 0010 (adapter isolation), ADR 0005 (audio contract).

## Definition of done

- [x] `src/ges2t/transcribers/whistle.py` gains a private `_WhistleStreamingDecoder`
      implementing `streaming.StreamingDecoder` (`process()`/`flush()`), constructed with an
      already-`load()`ed engine handle and `WhistleConfig.language`:
      - `process(buffer)` passes **raw little-endian float32 bytes** (`array('f')`), never a
        WAV path (ADR 0007 measured 0.036 s/5 s and 0.218 s/30 s of pure parsing waste);
      - `process()` returns a `StreamingPass(committed=..., pending=...)` (ADR 0019);
      - `flush()` ends the current stream, returns the tail text, and leaves the decoder ready
        for a new stream.
- [x] `WhistleTranscriber.stream(chunks)` is implemented as
      `streaming.drive(_WhistleStreamingDecoder(...), chunks, window_seconds=
      config.window_seconds, chunk_seconds=config.chunk_seconds)` — the windowing logic must
      **not** be reimplemented here.
- [x] `supports_streaming` remains `True`, and a test asserts the adapter's `stream()` is
      exercised through `drive()` (e.g. by asserting window behaviour end-to-end on a fake
      engine).
- [x] No `needle` type appears in any signature; all engine JSON is parsed inside the
      decoder and returned as `TranscriptionSegment`/strings (ADR 0010).
- [x] `tests/test_whistle_stream.py` runs **without the engine installed** (monkeypatched
      module-level `_needle`, per the batch ticket's convention) and asserts:
      `process()` passes the correct raw float32 byte length; the engine's JSON is parsed into
      a `StreamingPass` as documented; `flush()` returns the tail; and the
      accumulated transcript over several windows is exactly the concatenation of the fakes'
      committed deltas (no reconstruction from `pending`).
- [x] `uv run pytest tests/test_whistle_stream.py` passes and `uv run pytest` stays green.
- [x] **Manual, on the reference machine — the seam proof, reported in `001-comment.md`:**
      run a **30-second** speech clip through `WhistleTranscriber.stream()` at the default 5 s
      window and report:
      - per-chunk latency (median and p90) and total elapsed time;
      - the final transcript, compared against a single 30 s one-shot `transcribe()` call on
        the same audio, with a WER figure or an explicit word-count comparison (the probe
        measured 68 → 68 words, WER 0.176 vs 0.191);
      - an explicit statement of whether any word was **duplicated or dropped at a seam**,
        with the evidence.
      If duplication or loss is observed, write `002-comment.md` with the transcripts and set
      the ticket `blocked` rather than tuning the window to hide it.

- [x] `uv run mypy src/ges2t` exits 0: every definition fully annotated and every
      signature interface-typed — ADR 0019.

## Notes

- **Deltas must be concatenation-safe.** `drive()` builds the transcript by concatenating the
  `committed` deltas **verbatim**, with no separator inserted, so `_WhistleStreamingDecoder`
  must return deltas that already carry whatever whitespace the text needs. If a committed
  delta is a continuation, the leading space is the decoder's responsibility — do not rely on
  `drive()` to join words. Ticket 13's `001-comment.md` flags this as the ticket-14 contract.
- **Report the engine's language on the pass that knows it.** `StreamingPass` carries
  `language: str | None`, and `drive()` propagates the most recent non-`None` value onto the
  segments it yields. Populate it from the engine's JSON rather than leaving it `None`,
  because ADR 0011 requires the streaming path to report the engine's language; the engine
  auto-detects per pass and may be unstable on mixed-language audio, which is recorded as a
  measurement and deliberately **not** worked around in v1.
- Do not put the window cap in the generic config; it is engine-specific and already lives in
  `[engine.whistle]` (ADR 0009, validated by the engine-config ticket).
- Streaming must not be attempted before `load()`, and must reuse the single loaded model
  (the engine is process-global and not thread-safe — ADR 0005). Do not create a second
  engine instance for streaming.
- Do not add `numpy`. `array('f')` from the stdlib is sufficient, as in the batch ticket.
- Because streaming JSON has no `ttft_ms`/`decode_tps`, do not read those fields here; timing
  is the caller's job (ADR 0011). `pass_ms` may be surfaced on the segment.
- Keep `keywords` unset (`None`) — keyword biasing is not a v1 feature.

## Related tickets

- depends on: ../streaming-window-engine/ticket.md,
  ../../core-dictation-pipeline/whistle-transcriber-adapter-batch/ticket.md
- blocks: ../streaming-session/ticket.md
- related ADRs: ../../../adr/0011-streaming-sliding-window.md,
  ../../../adr/0007-whistle-integration-via-cactus-needle-package.md,
  ../../../adr/0010-transcriber-interface-design.md
