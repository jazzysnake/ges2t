# Streaming session: drive capture through the windowed stream

- **Priority:** P1
- **Status:** to do
- **Complexity:** M
- **Epic:** ../epic.md

## Current state / context

`../streaming-window-engine/ticket.md` gives the windowed-commit algorithm as a pure
generator, and `../whistle-streaming-adapter/ticket.md` gives the engine-specific decoder and
a working `WhistleTranscriber.stream()`. Neither is connected to a microphone.

This ticket implements the second `DictationSession` — the seam published by
`../../core-dictation-pipeline/service-state-machine-and-socket/ticket.md` — for the streaming
path. The batch equivalent is in
`../../core-dictation-pipeline/dictation-pipeline-batch/ticket.md`; this ticket must **not edit
`service.py`**, because the service already treats a session as an opaque object with
`start()`/`partials()`/`finish()`/`cancel()`.

Relevant decisions: ADR 0011 (streaming is default when supported; partials are display-only;
nothing is injected until release), ADR 0015 (never block, service outlives failures),
ADR 0008 (capture as a subprocess with drain-on-stop), ADR 0014 (fakes only in tests).

## Definition of done

- [ ] `src/ges2t/streaming_session.py` defines `class StreamingDictationSession`
      implementing `DictationSession`:
      - `__init__(self, audio: AudioSource, transcriber: Transcriber, *, chunk_seconds:
        float)`. **Note there is no `window_seconds` parameter**: the commit window is an
        engine-specific property already owned by the adapter (`WhistleConfig`), and `ticket
        14` applies it inside `WhistleTranscriber.stream()` by calling `streaming.drive()`.
        This session only needs `chunk_seconds` to size its reads from `AudioSource.chunks()`;
      - `start()` starts capture and a **worker thread** that consumes
        `audio.chunks(chunk_seconds)` and passes that iterable to
        **`transcriber.stream(...)`** — the `Transcriber` protocol, nothing lower-level. It
        records the latest **non-final** segment's text as the partial and keeps the
        **final** segment as the transcript;
      - **This module must not import `ges2t.streaming` and must not know that
        `StreamingDecoder`/`drive()` exist.** Accumulating committed deltas across window
        boundaries is the adapter's job (tickets 13/14); the session's job is capture,
        threading, and turning the segment stream into partials plus a final transcript. If
        you find yourself re-implementing delta accumulation here, stop — that is a layering
        error, and this was an inconsistency in an earlier draft of this ticket that a
        reviewer caught;
      - `partials() -> Iterator[str]` yields the latest non-final display text as it advances
        (deduplicated — do not yield the same string twice in a row), and **never** blocks
        `finish()`;
      - `finish() -> str` stops capture (which ends the chunk iterator), joins the worker, and
        returns the **full accumulated transcript** including the final flush;
      - `cancel() -> None` stops capture, abandons the accumulated text, and joins the worker;
      - calling `finish()`/`cancel()` twice, or before `start()`, is defined behaviour (a
        clear error or a no-op) rather than a hang.
- [ ] **Thread-safety discipline, explicitly implemented:** a `threading.Lock` guards only the
      shared fields (accumulated text, latest partial, stop flag). The lock is **never held
      across an engine call or across `audio.stop()`**, because that is how this design
      deadlocks.
- [ ] **The final text is authoritative.** `finish()` returns the text of the **final**
      segment yielded by `transcriber.stream()`; partial text is never used to construct it
      (ADR 0011).
- [ ] If the worker thread raises (engine error, audio error), `finish()` **propagates** that
      error rather than returning a short transcript, and `finish()` does not hang. The
      service converts that into its `ERROR` state and a notification.
- [ ] Cancelling mid-utterance types nothing and returns no text.
- [ ] `tests/test_streaming_session.py` uses `FakeAudioSource` and a **streaming-capable
      `FakeTranscriber`** (from `tests/fakes.py`) whose `stream()` yields a **configurable
      sequence of segments** — partials followed by one final. That is the whole test surface;
      the session is typed against the `Transcriber` protocol, so no decoder double is needed
      here. Tests must cover:
      `finish()` returns exactly the fake's final-segment text; `partials()` yields advancing,
      deduplicated display text; `cancel()` returns nothing and leaves no thread running
      (assert the worker joined); a raising worker surfaces through `finish()`; `finish()`
      before `start()` and `finish()` twice are defined; and a **short utterance** (fewer
      samples than one window) still produces its text (the final segment is not dropped).
- [ ] `uv run pytest tests/test_streaming_session.py` passes and `uv run pytest` stays green,
      with **no test taking longer than a few seconds** (assert no real sleeping/thread leak).

- [ ] `uv run mypy src/ges2t tests` exits 0: every definition fully annotated and every
      signature interface-typed — ADR 0019 (amended 2026-10-09 to cover tests too).

## Notes

- Drive capture **only** from the worker thread. Calling `audio.chunks()` from two threads is
  not supported by the audio source (`../../core-dictation-pipeline/audio-capture-pw-record/ticket.md`).
- `finish()` must call `audio.stop()` before joining, so the chunk iterator ends and the
  worker can exit; the audio source drains stdout on stop (ADR 0008), so no audio is lost.
- Do not implement the batch path here and do not add a capability check — session **selection**
  is `../streaming-wiring-and-e2e/ticket.md`'s job.
- If you find yourself wanting to inject partial text, re-read ADR 0011: partials are for the
  tray/notification display only.
- Use `threading.Thread(daemon=False)` and always join it, so a test failure cannot leave a
  process behind.

## Related tickets

- depends on: ../streaming-window-engine/ticket.md, ../whistle-streaming-adapter/ticket.md,
  ../../core-dictation-pipeline/service-state-machine-and-socket/ticket.md
- blocks: ../streaming-wiring-and-e2e/ticket.md
- related ADRs: ../../../adr/0011-streaming-sliding-window.md,
  ../../../adr/0015-single-service-process-architecture.md
