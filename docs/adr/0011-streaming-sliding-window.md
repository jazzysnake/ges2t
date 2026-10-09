# Streaming transcription: 5-second sliding window, commit on `stop()`

- **Status:** accepted — **amended 2026-10-09** by ticket `incremental-typing` (see the
  [Amendment](#amendment-2026-10-09--inject-the-committed-prefix-as-it-is-committed) at the end:
  the committed prefix is now typed as it is committed; the original "nothing is injected until
  release" rule was broader than its own reason)
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
4. On key release, call `stop()` a final time, append the tail, and type the
   remaining suffix **once** -- the committed prefix has already been typed as it
   arrived (ADR 0011 as amended below).

Design rules this fixes:

- **The 30 s per-call ceiling does not apply to the streaming path**, because no single pass
  ever sees more than a 5 s buffer. This is what lifts the length limit for the user.
- **Partial text is display-only; the committed prefix is typed as it is committed.**
  `committed + pending` may be shown as live status (tray/notification) and is **never**
  injected. During a streaming dictation the **committed** prefix -- the words two consecutive
  passes agree on, which are append-only and never revised -- is injected **as it is
  committed**, and the final flush applies ADR 0012's single trailing space once. The original
  rule ("nothing is injected until release") was broader than its own stated reason: typing
  `committed + pending` would indeed duplicate and race with the committed-text revisions, but
  that objection does **not** apply to `committed` alone, which is exactly the part that has
  stopped changing. Ticket 34 measured the change on real audio and made it; the evidence is
  in the amendment below. The user asked for dictation, not ghost text -- and text appearing
  while they speak is dictation, not ghost text.
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
- **The window and chunk sizes are configurable**, in the **engine-specific** config block
  `[engine.whistle]` (ADR 0009), because they are properties of *this* engine's
  re-transcription behaviour and mean nothing to an engine that streams properly:
  `window_seconds` (default **5**) and `chunk_seconds` (default **1**). Validation is the
  Whistle adapter's job and must reject: `chunk_seconds < 0.25`, `chunk_seconds >=
  window_seconds`, and `window_seconds > 30` (the engine's own per-call ceiling).
  **`window_seconds` above ~6 s is permitted but warned about**, because the measured curve
  crosses the 1 s per-chunk budget there; a user who raises it is choosing the latency.
- The window/commit logic lives in its own module (`ges2t/streaming.py`) so it is unit
  testable against a fake `Transcriber` streaming in both good and pathological ways.

## Consequences

- Longer dictation works and the total cost is **0.19× realtime**, comfortably inside the
  framework budget (ADR 0001) — the per-chunk latency is ~148 ms median, well under the 1 s
  the user asked the framework to stay below.
- The defaults are deliberately tuned to the measured knee in the curve, and they are
  configurable rather than frozen, so a faster machine or a future engine can be retuned
  without a code change. The warning above the knee is what keeps configurability from
  silently degrading the experience.
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

## Amendment 2026-10-09 — inject the committed prefix as it is committed

**Superseding decision (ticket `incremental-typing`, the payload of the incremental-typing
chain):** during a streaming dictation, ges2t injects the words the engine has **committed**
**as they are committed**, instead of nothing until release. The injected text is only the
`committed` prefix — never `pending`, never `committed + pending`. A typed watermark over the
cumulative committed string guarantees each committed character is typed exactly once, and the
final reconciliation against `finish()` recovers any advance the deduplicated partials skipped.
ADR 0012's trailing-space rule is applied **once at the end of the utterance**, not per commit.

### Why the original reasoning did not require the original rule

The original decision rejected typing during dictation because *"typing partials into the
focused window would duplicate and race with the committed-text revisions"*. That reason is
sound for the value the pipeline surfaced at the time — `committed + pending` — because
`pending` is replaced on every pass and is where the engine's tentative words live. It does
**not** apply to `committed` alone: `committed` is the part the engine has already agreed on
twice and is append-only (ticket `committed-delta-interface` made that boundary explicit on
`LiveText`). Typing only `committed` therefore cannot require a backspace. **The rule was
broader than its own reason required, and this amendment narrows it to the reason.**

### The measured evidence

Commit cadence, from ticket 32's real-device run (`commit-cadence-diagnostic`, 25.04 s recorded
/ 25.00 s fed, 17 commits through the real `pw-record` path, with a deliberate mid-utterance
pause), folded in with the shipped clips (3 inputs, 57.0 s of audio). The **40 commits total**
below is the persisted-WAV re-read total (5 + 19 + 16); the authoritative live run had **17**
commits, and adding the clips' 24 gives **41** for the live run, so 40 is not a live-run total:

| measurement | result |
|---|---|
| median characters per commit | **17** (clips 20; p90 34–38, max 63) |
| median gap between commits, source audio | **1.00 s** (p90 2.50 s, max 4.00 s) |
| median `pending` size | **18 characters**, persisting **1.00 s** |
| committed deltas that repeat already-committed words | **0** (0 with 2+ words, both detectors) |
| injection cost | ticket 23 measured **0.38 ms/char** for the `ydotool key` emission, so a median commit is **~6 ms** of emission. This excludes the per-commit `XkbKeySequenceSource.sequence_for` keymap rebuild, so it is a lower bound, not the whole per-commit cost. |

The measured cost is cheap enough that the real risk is **locking**, not speed, and the median
commit is small enough that typing it as it arrives keeps up with speech. Two observations from
the real recording shape the caveat below:

- the `Thank you.` hallucination appeared in `pending` **twice** and was **never committed**, so
  committed-only typing types none of it — this is the design's best evidence; and
- the deliberate mid-utterance pause produced a **committed** `' Great night.'` that nothing
  later contradicted. The two-pass commit rule protects against *revision*, not against a
  *stable hallucination*.

### Consequences accepted with the change (user decisions, not open questions)

- **A stable hallucination can now be delivered mid-dictation.** The `' Great night.'` case
  above is the concrete instance. The user accepted this and its recovery: **stop the recording,
  delete the wrong text, and continue** — which is why the stop path stays instant and the tool
  deliberately attempts **no** auto-correction.
- **Focus must hold for the whole utterance.** Committed text lands wherever focus is at each
  commit; the user does not consider mid-utterance window switching a real usage pattern.
- **Interleaving with the user's own typing is out of scope** and is a documented limitation.
- **The clipboard backend is incompatible with streaming**: incremental paste would rewrite the
  user's clipboard on every commit. The composition root refuses `injection.backend =
  "clipboard"` together with `streaming.enabled = true` with a loud, actionable error naming both
  settings and the fix (set `ydotool`, or disable streaming).
- **The streaming change is cancel-safe but not cancel-undoable:** once a commit has been typed it
  is in the focused window; `cancel` abandons the rest of the utterance and types nothing further,
  it cannot remove what was already typed. This is the same accepted risk as the hallucination.

### What does *not* change

- ADR 0011's decision to **stream** (5 s sliding window, commit on `stop()`) is unchanged.
- ADR 0003's **no automatic fallback** rule still holds; the clipboard/streaming refusal is
  exactly that rule applied to a new configuration contradiction.
- ADR 0012's text rules are unchanged: otherwise verbatim, exactly one trailing space when
  `ui.trailing_space` is enabled, and whitespace-only input injects **nothing**.
- The **batch** path and `streaming.enabled = false` behave exactly as before.
- Language, timestamps, and the window/chunk configuration are untouched.
