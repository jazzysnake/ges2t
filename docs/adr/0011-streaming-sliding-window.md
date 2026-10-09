# Streaming transcription: 5-second sliding window, commit on `stop()`

- **Status:** accepted
- **Date:** 2026-10-09

## Context

Streaming is a **v1 feature**, not a future nicety (explicit user requirement), and the user
pushed back correctly when the first measurement suggested it was hopeless: the earlier
probe had reported the cost of a growing buffer without separating model time from anything
else. A dedicated probe was run to establish the engine's actual streaming contract.

**What the header says (verbatim, `needle.h` ≥ 3.2.0):**

> Live transcription: appends 16 kHz mono float PCM, about one second at a time and at most
> 30 s per call, **re-transcribes the buffered audio and commits the words two consecutive
> passes agree on.**

So the engine *does* have a real commit model — it is not a naive re-transcription loop. The
two API calls are `needle_stream_transcribe_process(pcm, samples, language, keywords, out,
out_capacity)`, which appends audio and returns `{"text","words","pending","language",
"received","pass_ms"}`, and `needle_stream_transcribe_stop(out, out_capacity)`, which commits
the tail and **resets the stream**. There is no separate stream-init.

**Measured behaviour on the reference machine** (Ryzen 7 7840HS, CPU-only, 1 s chunks):

| Configuration | Result |
|---|---|
| **Uncapped**, 1 s chunks | Pass cost grows with buffered history (39 ms at 1 s → 712 ms at 10 s → ~1.1 s at 15 s). Crosses the 1 s chunk budget at about **6 s** of buffer. A 30 s uncapped stream costs **22.5–27.6 s wall** (0.75–0.92× realtime, ~13 min CPU). Unusable. |
| **Window capped at 5 s**, committed with `stop()` | Per-chunk **median 148 ms**, p90 ~300 ms, max 730 ms. **5.62 s total for 30 s of audio (0.19× realtime).** |
| **Seams** across 6 `stop()` calls | All `stop()` calls **0.0 ms**; **no duplicated or dropped words** (68 reference words → 68 hypothesis words). |
| **Quality** | WER **0.176** for the 5 s sliding window vs **0.191** for a single 30 s one-shot call — the windowed path is *at least as good*, and the uncapped 30 s call actually truncates and garbles a language switch. |

Two further measured facts that shape the design:

- **The batch/one-shot path is dominated by encode-to-first-token.** For a 30 s one-shot call,
  `ttft_ms` is 1655–4422 ms while decode is 0.38–1.02 s, and `wall − ttft − decode ≈ 0.000 s`.
  There is no framework overhead to reclaim; a 30 s buffered utterance is simply slower.
- **Streaming results carry no `ttft_ms`/`decode_tps`** — they expose `pass_ms`, `received`,
  `pending`, and always-present `words[]` with start/end/probability. Streaming timing must
  therefore be measured with our own clock, not read from the engine's JSON.
- **Language detection is per-pass and unstable on mixed-language audio.** The 30 s one-shot
  call mis-detected a Spanish segment as English-ish text. The windowed path handled the
  switch better, by luck of where its boundaries fell.

## Decision

**Streaming is the default transcription path when the backend declares
`supports_streaming = True`**, and the batch path (ADR 0005) remains for backends that do
not.

The v1 streaming loop is precisely:

1. Feed the engine **1-second chunks** of 16 kHz mono float32 as they are captured.
2. **Cap the buffered window at 5 seconds.** After 5 chunks have been fed, call
   `needle_stream_transcribe_stop()`, which commits the unconfirmed tail and resets the
   stream; immediately continue feeding the next chunk into the fresh stream.
3. **Accumulate committed text** across the whole utterance — each `process()` call returns
   only the words it committed, so the transcript is the concatenation of those deltas plus
   the final `stop()` tail. Do not re-derive it from `pending`.
4. On key release, call `stop()` a final time, append the tail, and inject **once**.

Design rules this fixes:

- **The 30 s per-call ceiling does not apply to the streaming path**, because no single pass
  ever sees more than a 5 s buffer. This is what lifts the length limit for the user.
- **Partial text is display-only.** `committed + pending` may be shown as live status
  (tray/notification), but **nothing is injected until release**. Typing partials into the
  focused window would duplicate and race with the committed-text revisions; the user asked
  for dictation, not ghost text.
- **Never run the window uncapped.** The 5 s cap is the whole reason streaming is viable; an
  "improvement" that lets the buffer grow reintroduces the 0.75–0.92× realtime failure.
- **Timings are ours.** Because streaming JSON lacks `ttft_ms`/`decode_tps`, per-chunk timing
  is measured in the streaming module and reported as a number (ADR 0014), never asserted
  from the engine's fields.
- **Language is whatever the engine says**, taken from the final committed pass. The known
  mixed-language instability is recorded here as a measurement and is **not** worked around
  in v1 (post-processing is out of scope, ADR 0000).
- **`streaming.enabled = false`** must be a first-class, tested configuration (ADR 0009,
  ADR 0014) that takes the batch path unchanged from epic 1.
- The window/commit logic lives in its own module (`ges2t/streaming.py`) so it is unit
  testable against a fake `Transcriber` streaming in both good and pathological ways.

## Consequences

- Longer dictation works and the total cost is **0.19× realtime**, comfortably inside the
  framework budget (ADR 0001) — the per-chunk latency is ~148 ms median, well under the 1 s
  the user asked the framework to stay below.
- Quality did not degrade by windowing; measured WER was marginally *better* than the 30 s
  one-shot path. The user's "I do not mind if it is a bit slower than real time" turned out
  not to be needed — the capped design is faster than realtime by ~5×.
- The commit model means the transcript is built from engine-agreed words, so a mid-utterance
  mis-hearing can be revised by the engine before commit rather than locked in.
- Because `stop()` resets the stream, the implementation must track committed text itself and
  must not assume `received`/timestamps are continuous across windows. This is the main
  correctness trap, and the seam test (68→68 words, no dup/drop) is the regression guard.
- The batch path stays alive and tested, both for `streaming.enabled = false` and for a
  future backend whose capability flag is `False` (ADR 0010) — which is exactly the
  abstraction requirement paying off.
