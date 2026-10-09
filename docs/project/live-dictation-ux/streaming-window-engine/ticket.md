# Streaming window engine: the generic windowed-commit driver

- **Priority:** P1
- **Status:** to do
- **Complexity:** M
- **Epic:** ../epic.md

## Current state / context

This is the first of four streaming tickets, split so each is a single focused session. It
contains **pure logic and no I/O**: the algorithm that turns a stream of 1-second audio chunks
into committed text with a bounded re-transcription window.

The measured reason it exists (ADR 0011 — read it in full): the engine's streaming API
**re-transcribes the buffered audio**, so cost grows with buffer length. It crosses the 1 s
per-chunk budget at ~6 s of buffer, and an uncapped 30 s stream costs 0.75–0.92× realtime. A
window capped at 5 s committed via the engine's `stop()` gives **148 ms median per chunk,
0.19× realtime over 30 s, and no word loss at the seams** (68 reference words → 68 hypothesis
words), with WER 0.176 vs 0.191 for a single 30 s one-shot call.

The window and chunk sizes are **configurable and live in the engine-specific config block**
`[engine.whistle]` (ADR 0009, ADR 0011: `window_seconds` default 5, `chunk_seconds` default
1), which is why this driver takes them as parameters rather than hard-coding them. This
driver is generic — any engine that needs windowed re-transcription can reuse it — while
`../whistle-streaming-adapter/ticket.md` supplies the engine-specific decoder.

Relevant decisions: ADR 0011 (the algorithm and its measured basis), ADR 0010 (protocols and
data classes), ADR 0014 (this is a primary unit-test target).

## Definition of done

- [ ] `src/ges2t/streaming.py` defines a `StreamingDecoder` Protocol — the narrow, engine-facing
      seam this driver drives:
      - `process(buffer: AudioBuffer) -> StreamingPass` where `StreamingPass` is a **frozen
        dataclass** `StreamingPass(committed: str, pending: str)` — not a tuple (ADR 0019);
      - `flush() -> str` returning the text committed by ending the current stream (the
        engine's `stop()`), after which the next `process()` begins a new stream;
      and `drive(decoder, chunks, *, window_seconds, chunk_seconds, on_partial=None) ->
      Iterator[TranscriptionSegment]` implementing exactly:
      1. group incoming audio into `chunk_seconds`-sized `AudioBuffer`s (a partial final group
         is flushed at the end, never dropped);
      2. feed each group to `decoder.process()`, accumulating `StreamingPass.committed` into
         the running transcript;
      3. **when the cumulative audio fed since the last `flush()` reaches `window_seconds`,
         call `flush()`**, add its return value to the transcript, and continue with a fresh
         stream;
      4. yield a **non-final** `TranscriptionSegment` after each group whose text is
         `committed + pending` (display-only live text, ADR 0011), and call `on_partial` with
         the same string when provided;
      5. at end of input, `flush()` a final time and yield exactly one **final**
         `TranscriptionSegment` (with `is_final=True`) containing the whole accumulated
         transcript.
- [ ] Duration accounting is by **actual samples and sample rate**, not by counting chunks, so
      a short or long final group still triggers the window boundary correctly.
- [ ] The final segment's text is the concatenation of every committed delta plus a single
      final flush, and is **never** reconstructed from `pending`.
- [ ] An empty input produces exactly one final segment with empty text; it must not raise and
      must not call `flush()` more times than specified.
- [ ] `tests/test_streaming.py` uses a **deterministic fake decoder** and asserts:
      - `flush()` is called exactly at the window boundaries (e.g. 30 s of 1 s chunks at a 5 s
        window → 6 boundary flushes plus the final one);
      - **no word is duplicated or lost at a seam** — the concatenated final text equals the
        decoder's known-perfect output for a synthetic utterance spanning several windows;
      - partials are yielded after each group, are non-final, and equal `committed + pending`;
      - the final segment is yielded exactly once and is marked final;
      - a window/chunk pair that divides unevenly (e.g. 3.5 s of 1 s chunks at a 5 s window)
        still flushes the remaining audio exactly once;
      - an exception raised by the decoder propagates rather than being swallowed, and
        `flush()` is not retried in a loop.
- [ ] `uv run pytest tests/test_streaming.py` passes and `uv run pytest` stays green.

- [ ] `uv run mypy src/ges2t` exits 0: every definition fully annotated and every
      signature interface-typed — ADR 0019.

## Notes

- **No I/O, no threads, no engine, no audio**: this module takes an iterable and yields
  segments. Everything concrete is in
  `../whistle-streaming-adapter/ticket.md` and `../streaming-session/ticket.md`. If you find
  yourself importing `subprocess` or `cactus_needle`, stop.
- Do not re-validate `window_seconds`/`chunk_seconds` beyond a cheap sanity assertion; the
  authoritative validation is in `../../core-dictation-pipeline/whistle-engine-config/ticket.md`
  (ADR 0011). Guard against `chunk_seconds <= 0` and `window_seconds < chunk_seconds` with an
  `AssertionError` or `ValueError` so a programming error cannot become an infinite loop.
- Timestamps are deliberately **not** part of the v1 output beyond what
  `TranscriptionSegment` already carries; the engine restarts timestamps when its stream
  resets, so a naive offset would silently corrupt them. If word times are ever needed, that
  is a new ticket.
- Keep the driver a **generator** so memory does not grow with utterance length.

## Related tickets

- depends on: ../../core-dictation-pipeline/core-interfaces-and-fakes/ticket.md
- blocks: ../whistle-streaming-adapter/ticket.md, ../streaming-session/ticket.md
- related ADRs: ../../../adr/0011-streaming-sliding-window.md,
  ../../../adr/0010-transcriber-interface-design.md,
  ../../../adr/0019-code-style-typing-and-interfaces.md
