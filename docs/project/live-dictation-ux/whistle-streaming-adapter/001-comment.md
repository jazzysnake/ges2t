# 001 — Whistle streaming adapter implementation notes

Ticket: `ticket.md` (`whistle-streaming-adapter`) · Branch:
`feat/whistle-streaming-adapter` · Worktree: `.worktrees/whistle-streaming-adapter`

## What was built

- `src/ges2t/transcribers/whistle.py` — added the private
  `_WhistleStreamingDecoder` (plus `_ChunkFeeder`, `_stream_text`,
  `_stream_language`) and implemented `WhistleTranscriber.stream()` as
  `streaming.drive(_WhistleStreamingDecoder(...), chunks, window_seconds=...,
  chunk_seconds=...)`. The windowing is **not** reimplemented; `drive()` owns it.
- `tests/test_whistle_stream.py` — 21 new tests against a fake `needle` module
  that models the real generator shape. No engine, model, network, numpy,
  sleeps, or threads.
- `tests/test_whistle_batch.py` — one obsolete test renamed/changed: the
  `NotImplementedError` stub assertion became
  `test_stream_before_load_fails_eagerly` (the stub it guarded is this ticket's
  deliverable). The batch file's docstring was updated to match. No other test
  was touched.

No `needle` type appears in any signature. The engine handle is `Any`, which is
the deliberate third-party boundary already used by the batch path (ADR 0019).
`supports_streaming` is still `True` and `WhistleTranscriber` still satisfies
`Transcriber`.

## The empirically determined generator contract (the hard part)

Measured with the real pinned engine (`~/.local/share/ges2t/engine/3.2.0`,
`~/.local/share/ges2t/models/whistle/2.0.0/whistle.cact`) on
`/tmp/whinspect/clip30.wav`, via a probe that stepped
`needle.Whistle.stream(feeder, language=None)` one `next()` at a time. Raw
output for the first five 1 s chunks:

```
0 keys ['language','pass_ms','pending','received','text','words']
   text=''  pending='He hoped.'  language='en'  received=1.0  pass_ms=45.5  words=0
1 text='He' pending='hoped there would be stew for...' language='en' received=2.0 pass_ms=81.8 words=1
2 text='hoped there would be stew' pending='for dinner, turnips.' language='en' received=3.0 pass_ms=119.7 words=5
3 text='for dinner,' pending='turnips and carrots and' language='en' received=4.0 pass_ms=156.4 words=2
4 text='turnips and carrots and' pending='bruised potatoes.' language='en' received=5.0 pass_ms=205.4 words=4
tail {'text': 'bruised potatoes.', 'pending': '', 'language': 'en', 'received': 5.0, 'pass_ms': 0.0}
tail words 2
generator exhausted after tail: OK
```

The actual yield sequence, confirmed empirically:

- **N chunks → N dicts + 1 tail dict.** The dict for chunk *i* is produced when
  the generator pulls chunk *i*; the extra dict is produced when the input
  iterator is exhausted and the generator calls
  `needle_stream_transcribe_stop`. After the tail dict, `next()` raises
  `StopIteration`.
- **Keys are exactly** `text`, `pending`, `language`, `received`, `pass_ms`,
  `words`. `text`/`pending` are **bare** — no leading or trailing whitespace;
  the package's own `listen()` reader is what inserts separators. `pending` is
  display text and may be truncated with an ellipsis (`'...stew for...'`).
  `pass_ms` is present on process dicts and `0.0` on the tail. There is no
  `ttft_ms`/`decode_tps` (ADR 0011). `needle.Whistle.stream` is a pure-Python
  generator around `needle_stream_transcribe_process`/`_stop`, and its
  `_result()` parses the engine's JSON.
- **`stop()` on a never-fed fresh stream is safe** and returns an empty tail
  (`text=''`), which is why `drive()`'s unconditional extra final flush costs
  nothing and keeps the flush count deterministic (ADR 0011; 30 s at a 5 s
  window = 30 `process` + 7 `flush`).
- **`stop()` resets the stream**, so a new generator starts a new engine
  stream; committed text must be accumulated by the caller (ADR 0011).

## Decoder design

`_WhistleStreamingDecoder(engine, language)` holds **one package-level generator
per window**:

- `__init__` opens the first `engine.stream(feeder, language=language)`.
- `process(buffer)` validates the 16 kHz contract (`_validate_buffer`, as in the
  batch path), pushes `_float32_bytes(buffer.samples)` into the feeder, then
  calls `next(generator)` once. The chunk the generator pulls and the dict it
  yields therefore line up one-to-one, and the engine sees **raw float32 bytes**
  (ADR 0007), never a WAV path.
- `flush()` calls `feeder.end()`, which makes the generator exhaust its input;
  the generator then calls `stop()` and yields the tail dict. The tail's `text`
  is returned, the generator is advanced once more to `StopIteration`, and a
  fresh generator+feeder is opened for the next window.
- `_format(text)` owns the **inter-delta whitespace** ticket 13 requires: a
  non-empty text is returned verbatim the first time and with one leading space
  on every later non-empty text (the engine's bare-word contract means
  `drive()`'s verbatim concatenation would otherwise run words together). The
  `_emitted` flag is never reset on a window boundary, so the first delta of a
  new window is correctly spaced. This mirrors `needle`'s own
  `committed += (" " if committed and step["text"] else "") + step["text"]`.
- `pending` is prefixed with a space under the same condition so `drive()`'s
  display string `transcript + pending` does not run words together. It is
  display-only and never contributes to the transcript (ADR 0011).
- `language` is copied straight from the engine dict into
  `StreamingPass.language`; `drive()` keeps the most recent non-`None` value
  (ticket 13). The empty `WhistleConfig.language` still means auto-detect: it is
  passed verbatim and the package turns `""` into `None`.

A `_ChunkFeeder` is a deque-backed `Iterator[bytes]`, not a `queue.Queue`: 
`drive()` calls `process()`/`flush()` synchronously on one thread, so no
blocking queue is needed. A pull with nothing queued and no end signalled raises
`WhistleTranscriberError` rather than silently ending the stream early (which
would lose audio).

`WhistleTranscriber.stream()` calls `self._require_loaded()` **eagerly** and
then returns `streaming.drive(...)`. It is a normal function, not a generator
function, so a caller who forgot `load()` learns immediately at the call instead
of on first iteration after starting capture.

## Measured seam proof (reference machine, real engine)

Command: `uv run python /tmp/seam_proof.py`, driven by the **actual**
`WhistleTranscriber.stream()` (a logging subclass of the private decoder
records every returned delta; the probe is not committed). Audio:
`/tmp/whinspect/clip30.wav`, 480000 samples = 30.0 s, 30 × 1 s chunks, default
5 s window, no audio committed.

```
segments: 31 (30 partials + 1 final)
per-chunk latency (n=30): median=121.8 ms  p90=199.5 ms  max=263.9 ms
final-flush latency (last next): 0.1 ms
total elapsed: 3.75 s  (0.125x realtime)
final language: en

event count: 37 (30 process, 7 flush)
verbatim concat of decoder deltas == final.text: True
word count from deltas: 68
```

Final windowed transcript (exact):

```
He hoped there would be stew for dinner, turnips and carrots and bruised
potatoes. and fat mutton pieces to be latled out in thick peppered flour fat
and sauce. ¿Quién las ramas medio sumergidas revolucionan algunos pájaros de
quién? maybe queen legendary After early nightfall, the yellow Lamps would
light up here and there the squalid quarter of the brothels. "Stuff it into
you," his belly counselled him.
```

One-shot `WhistleTranscriber.transcribe()` on the same 30 s audio:

```
He hoped there would be stew for dinner, turnips and carrots, and bruised
potatoes, and fat mutton pieces to be laid out in thick, peppered, flour fat
and sauce. "In the Ramas medius meridas revoloteraman and bonus bajados de y
medicuille legendario tumacti." After early nightfall the yellow lamps would
light up here and there the squalid quarter of the brothels. "Stuff it into
you," his belly counselled him.
```

| Measurement | This run | ADR 0011 |
|---|---|---|
| per-chunk median / p90 | 121.8 ms / 199.5 ms | median 148 ms, p90 ~300 ms |
| total, 30 s audio | 3.75 s (0.125× realtime) | 5.62 s (0.19×) |
| windowed vs `ref_perclip.txt` WER | **0.176** (68 ref / 68 hyp) | 0.176 |
| one-shot vs `ref_perclip.txt` WER | **0.191** (68 ref / 69 hyp) | 0.191 |
| windowed vs one-shot WER | 0.188 (69 ref / 68 hyp) | — |
| word counts | windowed 68, one-shot 69 | 68 → 68 |

**Seam verdict: no word was duplicated or dropped at a seam.** The evidence is
threefold:

1. All seven `stop()` flushes report distinct boundary words:
   `...'bruised potatoes'` → `'and'...`, `...'flour fat and sauce'` →
   `'quién las ramas medio'...`, `...'algunos pájaros de quién'` →
   `'maybe queen'...`, `...'early nightfall the yellow'` →
   `'lamps would light up'...`, `...'brothels'` → `'stuff it into you'...`, and
   the two empty final flushes. No boundary word is repeated across a seam.
2. `difflib` over the one-shot and windowed token sequences (autojunk off)
   produced **only `replace` opcodes and equal runs — no `insert` and no
   `delete`**. A duplicated word would appear as an `insert`; a dropped word as
   a `delete`. The only word-count difference is a single 6→5-token `replace`
   inside the garbled Spanish section (`medius meridas revoloteraman and bonus
   bajados` → `medio sumergidas revolucionan algunos pájaros`), which is engine
   mis-recognition, not a seam.
3. The decoder deltas concatenated **verbatim** equal `final.text` exactly, and
   the windowed word count (68) equals the `ref_perclip` reference count (68).

The windowed transcript differs from the one-shot in the Spanish segment and in
one word (`laid` → `latled`); both are engine errors, and windowing's WER is the
*better* of the two, reproducing ADR 0011's central result. A single run can
vary (CPU contention); this run's numbers sit inside ADR 0011's measured range.

## Judgement calls the orchestrator should know

1. **`stream()` is not a generator function.** It validates the load state
   eagerly and returns `drive(...)`, so the not-loaded error is raised at call
   time. (The batch review speculated the body would "gain a `yield`"; that
   would have made the guard lazy. This ticket's explicit `drive(...)` form is
   used instead.)
2. **`pass_ms` cannot be surfaced.** `StreamingPass` has no `pass_ms` field
   (ticket 13), so every yielded segment has `pass_ms=None`, exactly as ticket
   13 documented. The engine's `pass_ms` is present in the JSON and would be
   trivially surfaceable if a later ticket extends `StreamingPass`; timing is
   otherwise the session's job (ADR 0011).
3. **Per-chunk `_validate_buffer`.** The decoder enforces the 16 kHz contract on
   every buffer, matching the batch path. The 30 s branch of that helper is
   unreachable for streaming (a group is at most `chunk_seconds`) but harmless;
   `window_seconds > 30` is rejected earlier by `whistle_config.py`.
4. **`pending` gets a display space.** The driver renders `transcript +
   pending`; the engine's pending is bare, so the decoder prefixes it. `pending`
   remains display-only and is never reconstructible into the transcript, as the
   `POISON-DISPLAY-ONLY` test asserts.
5. **`_ChunkFeeder` is a deque, not `queue.Queue`.** The package's own `listen()`
   uses a queue because its chunks arrive on an audio thread; `drive()` is
   single-threaded, so a queue would add blocking semantics for no benefit.
   `streaming.py`'s "no thread" property is preserved.
6. **Test-file scope.** `tests/test_whistle_batch.py` needed one edit (the
   obsolete `NotImplementedError` assertion). Everything else is the two new
   files plus this document and the board row.

## Hand-off for ticket 15 (streaming session)

- `WhistleTranscriber.stream(chunks)` is implemented and internally is exactly
  `streaming.drive(_WhistleStreamingDecoder(engine, config.language), chunks,
  window_seconds=config.window_seconds, chunk_seconds=config.chunk_seconds)`.
- It requires `load()` first; otherwise it raises `WhistleNotLoadedError`
  eagerly. It reuses the single loaded engine and never constructs a second one.
- It yields one display-only partial per chunk and exactly one `is_final=True`
  segment. Empty input yields a single empty final segment.
- `TranscriptionSegment.language` is the most recent non-`None` engine language;
  `pass_ms` is always `None` (ticket 13).
- `TranscriptionSegment.text` on partials is `committed + pending` (display
  only); the final text is the verbatim concatenation of the decoder's
  concatenation-safe deltas plus flush tails.

## Verification (real output)

```
$ uv run pytest
274 passed in 0.96s

$ uv run pytest tests/test_whistle_stream.py -q
21 passed in 0.16s

$ uv run mypy src/ges2t tests
Success: no issues found in 25 source files
```

`uv run mypy src/ges2t tests` is green on this branch. (Ticket 13's review
recorded a pre-existing `tests/test_whistle_config.py` failure there; it no
longer reproduces on this branch's base.)

The seam proof above is the manual reference-machine run required by the
definition of done. No audio file was committed; only `/tmp/whinspect/` inputs
were read.
