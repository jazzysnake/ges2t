# 001 — Streaming session implementation notes

Ticket: `ticket.md` (`streaming-session`) · Branch: `feat/streaming-session`
Worktree: `.worktrees/streaming-session` · Status: **in review**

## What was built

Exactly the two files the ticket allows:

- `src/ges2t/streaming_session.py` — `StreamingDictationSession`, the second
  `DictationSession` implementation (the batch one is in `pipeline.py`).
- `tests/test_streaming_session.py` — 21 tests, all against `tests/fakes.py`
  (`FakeAudioSource`, `FakeTranscriber`, `FakeInjector`, `FakeStatusUI`) plus the
  real `Service` for one boundary test. No engine, no microphone, no subprocess,
  no network.

`pipeline.py` was **not** touched, no `streaming.enabled` branch was added, and
the composition is untouched — ticket 16 extends `_session_factory` itself.

## Final public shapes

```python
class StreamingSessionError(RuntimeError): ...

class StreamingDictationSession:  # implements service.DictationSession
    def __init__(
        self, audio: AudioSource, transcriber: Transcriber, *, chunk_seconds: float
    ) -> None: ...
    def start(self) -> None: ...
    def partials(self) -> Iterator[str]: ...
    def finish(self) -> str: ...
    def cancel(self) -> None: ...
```

There is deliberately **no `window_seconds` parameter** (the commit window is
the adapter's `WhistleConfig`, applied inside `WhistleTranscriber.stream()`), and
the module imports only `threading`, `collections.abc`, `enum`, and
`ges2t.interfaces`. It does not import `ges2t.streaming` and does not know that
`StreamingDecoder`/`drive()` exist; the only occurrences of "streaming"/"drive"/
"window" in the module are the docstrings that state this boundary.

## How "nothing is injected until release" is structurally enforced

ADR 0011's hardest rule is a data-flow property, not a convention:

- the worker's `_record` branches on `segment.is_final`: non-final text is
  appended to the private `_partials` list, the final segment is stored in the
  separate private `_final_text` field;
- `finish()` reads **only** `_final_text`; `partials()` reads **only**
  `_partials`. No function returns both and there is no path from the partial
  list into the transcript;
- if a misbehaving stream yields partials but **no** final segment, `finish()`
  returns `""` rather than promoting partial text (test:
  `test_finish_is_empty_when_the_stream_yields_no_final_segment`);
- the service forwards `partials()` to `StatusUI.partial()` and injects the
  result of `finish()`; test
  `test_service_displays_partials_but_never_injects_them` runs the **real**
  `Service` and asserts the injector receives only `"published transcript"` and
  never the `"POISON display only"` partial.

The falsification below shows that mutating `finish()` to return the latest
partial makes exactly that test inject `"POISON display only"` and fail.

## Threading discipline (explicit)

- One `threading.Condition` over one `threading.Lock` guards the shared fields:
  `_partials`, `_final_text`, `_error`, `_done`, and the lifecycle. The partials
  list is retained (not just the latest) so a slow or late consumer deterministically
  receives every distinct partial in order; the last element is the latest.
- The lock is **never** held across an engine call (iterating
  `transcriber.stream(...)`) or across `audio.stop()`. `_stop_capture()` does
  `stop()` and `worker.join()` with the lock released; `partials()` releases the
  lock in `wait()` and across each `yield`.
- The worker is `threading.Thread(..., daemon=False)`. `finish()` and `cancel()`
  both call `audio.stop()` before `join()` (ADR 0008's drain-on-stop), so the
  chunk iterator ends and no audio is lost.
- `finish()`/`cancel()` before `start()` raise `StreamingSessionError`; a second
  `finish()` returns the cached text; a second `cancel()` is a no-op. Nothing
  hangs.

## Judgement calls a reviewer should check

1. **Partials are retained, not just the latest.** The ticket says "records the
   latest non-final segment's text"; the implementation keeps every distinct
   partial in a list and drops only consecutive duplicates. The last element is
   the latest. This makes the partials tests deterministic (no wall-clock race
   between the worker and the consumer) while still satisfying "the latest as it
   advances". `partials()` after `finish()` drains the retained list, so
   `finish()` never waits on a consumer — `test_a_waiting_partials_consumer_does_not_block_finish`
   proves that with a gated `FakeAudioSource` subclass.
2. **No final segment ⇒ `""`.** The `Transcriber` protocol promises a final
   segment; if an adapter violates that, the session returns the empty string
   rather than raising or falling back to `pending`-style partial text. This is
   the conservative choice and it is pinned by a test.
3. **`chunk_seconds <= 0` raises `ValueError` locally.** A negative chunk period
   is a programming error that would make `audio.chunks()` nonsensical. The
   authoritative validation (0.25 s floor, 30 s ceiling, latency warning) stays in
   `whistle_config.py` (ADR 0011).
4. **`cancel()` also propagates a failing `audio.stop()`** after joining the
   worker and clearing state, so it never leaks a thread; the service turns that
   into its `ERROR` state as it does for any session.
5. **One integration test uses the real `Service`.** It does not wire streaming
   into `run_service`; it constructs `Service(lambda: session, ...)` locally to
   prove the display-only rule at the injection boundary. That is the strongest
   available "partials never reach the injector" guard and it is the test the
   falsification below targets.

## Verification (real output)

Baseline on this branch's base (`33413b3`), before any change:

```
$ uv run pytest -q
515 passed in 2.98s

$ uv run mypy src/ges2t tests
Success: no issues found in 34 source files
```

After the change:

```
$ uv run pytest tests/test_streaming_session.py -q
.....................                                                    [100%]
21 passed in 0.07s

$ uv run pytest -q
536 passed in 1.02s

$ uv run mypy src/ges2t tests
Success: no issues found in 36 source files
```

21 new tests; 34 → 36 files under mypy (one new source, one new test). No test
sleeps or takes more than a fraction of a second apart from the two bounded
`_call_with_timeout` guards (2 s cap, only reached on a regression).

## Falsification evidence

A throwaway harness (`/tmp/falsify_t15.py`, not committed) applied one textual
mutation at a time to `streaming_session.py`, ran
`tests/test_streaming_session.py`, and restored the file; `git status` is clean of
probes afterwards. Every mutation produced the expected failures, and **every one
of the 21 tests failed under at least one mutation**:

```
M1  finish returns the latest partial      -> 8 failed, incl. injection boundary test
M2  is_final inverted (final -> partial)   -> 11 failed
M3  consecutive-duplicate dedup removed    -> test_partials_advance_and_consecutive_duplicates...
M4  only the latest partial retained       -> partials advance / readable-after-finish
M5  finish/cancel never stop capture       -> start/stop call shape, cancel test
M6  worker error swallowed                 -> both raising-worker tests
M7  finish-before-start guard removed      -> test_finish_before_start_is_defined
M8  start-twice guard removed              -> test_start_twice_is_rejected
M9  cancel does not abandon partials       -> test_cancel_stops_capture...
M10 chunk_seconds validation removed       -> test_non_positive_chunk_seconds_is_rejected[±]
M11 finish twice raises                    -> test_finish_twice_returns_the_cached_text...
M12 cancel twice raises                    -> test_cancel_twice_is_idempotent
M13 cancel-before-start guard removed      -> test_cancel_before_start_is_defined
M14 finish holds lock across stop/join     -> deadlock; suite hangs, the waiting-consumer
                                              test reports the failure before the kill
M15 partials protocol method missing       -> test_streaming_session_is_a_dictation_session
```

The per-test coverage is complete: the protocol-shape test (M15), both
chunk-seconds cases (M10), capture start/chunking (M2/M5), the duplicate-start
and before-start lifecycle guards (M7/M8/M13), the authoritative-final tests
(M1/M11), the short-utterance case (M2), the advancing/deduplicated partials
tests (M3/M4), the two concurrency/partials guards (M14), the cancel tests
(M5/M9/M12), both error-propagation tests (M6), and the service injection
boundary (M1/M2).

**Proof that partial text cannot reach the injector was observed directly.**
With M1 applied, the boundary test fails with:

```
>           assert injector.injected == ["published transcript"]
E           AssertionError: assert ['POISON display only'] == ['published transcript']
E             At index 0 diff: 'POISON display only' != 'published transcript'
FAILED tests/test_streaming_session.py::test_service_displays_partials_but_never_injects_them
1 failed in 0.08s
```

so a `finish()` that leaked a partial would be caught at the exact point the text
is typed.

## Hand-off for ticket 16 (streaming wiring and E2E)

- `StreamingDictationSession(audio, transcriber, *, chunk_seconds=...)` is the
  streaming `DictationSession`; construct it in the extended `_session_factory`,
  closing over the already-built `Transcriber` and `AudioSource` (or a session
  factory per recording, exactly as the batch path does).
- `chunk_seconds` comes from `WhistleConfig`; the session does **not** know the
  window size. Do not pass `window_seconds` here.
- `partials()` is display-only and the service already forwards it to
  `StatusUI.partial()`; ticket 16 only decides streaming-vs-batch and does not
  need to change `service.py`.
- No `main` commit was made; the worktree is left in place for the reviewer.
