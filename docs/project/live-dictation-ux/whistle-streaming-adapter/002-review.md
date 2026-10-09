# 002 — Review: Whistle streaming adapter (`stream()` on the Whistle transcriber)

- **Ticket:** [ticket.md](ticket.md) (whistle-streaming-adapter)
- **Branch:** `feat/whistle-streaming-adapter` @ `f51d543`
- **Commits reviewed:** `e621c87` (code + tests), `f51d543` (docs + board row)
- **Implementer notes reviewed:** [001-comment.md](001-comment.md) — every claim checked, not
  trusted
- **Spec / dependencies:** [ADR 0011](../../../adr/0011-streaming-sliding-window.md) (measured
  basis), [ADR 0007](../../../adr/0007-whistle-integration-via-cactus-needle-package.md),
  [ADR 0010](../../../adr/0010-transcriber-interface-design.md),
  [ADR 0019](../../../adr/0019-code-style-typing-and-interfaces.md), and ticket 13's reviewer
  notes [../streaming-window-engine/002-review.md](../streaming-window-engine/002-review.md)
  (the decoder contract and its adversarial cases)
- **Reviewer:** independent review run. `git status` clean at start and end; every probe
  reverted; the mutation worktree removed.

## Verdict

**pass** — no blocking findings.

I reproduced the generator contract against the **real pinned engine** (`cactus-needle` 3.1.3
wrapper over the 3.2.0 `libneedle.so`, weights `whistle/2.0.0`) and the seam proof against the
real `WhistleTranscriber.stream()`. The contract holds exactly as documented (N chunks → N
process dicts + 1 tail, key set `{text,pending,language,received,pass_ms,words}`, bare text),
and the seam does not duplicate or drop a word: my own run produced the implementer's exact
windowed and one-shot transcripts, `difflib` yielded **only `equal`/`replace` opcodes**, every
window-boundary word is distinct, and the token sum of committed deltas + flush tails (68)
equals the final transcript's token count (68). Window/chunk sizes come from `WhistleConfig`
and windowing is delegated to `drive()`. Mutations of the decoder are all caught by the new
tests. `uv run mypy src/ges2t tests` is green (25 files) and the config bites; the full suite
passes with the engine's import blocked.

Four non-blocking risks/follow-ups are recorded (R1–R4); the only real one is that the
one-chunk-per-`next()` generator shape is asserted only against the pinned package version, so
a future package upgrade could break the real seam while the fake-based suite stays green (R1).

## Per-criterion findings

### 1. The generator contract, reproduced independently — pass

I wrote my own probe (`/tmp/t14review/contract_probe.py`, not committed) that imported the real
`needle`, set `model_store.env_overrides()`, constructed `needle.Whistle(weights=<managed
path>)`, and stepped `engine.stream(feeder, language=None)` one `next()` at a time over the
30 s clip's 30 × 1 s chunks, feeding one chunk before each `next()`.

Observed, without exception:

- **30 chunks → 30 process dicts + 1 tail dict**, then `StopIteration`. Exactly the claimed
  shape.
- **Key set for all 30 process dicts: `['language','pass_ms','pending','received','text','words']`**
  (one key set, ×30). The tail dict carries the same six keys.
- **`text` and `pending` are bare**: of the 30 `text` fields and 30 `pending` fields, **zero**
  had leading or trailing whitespace. `pending` contained an ellipsis (`...`) in exactly one
  pass (chunk 1), matching the "may be truncated" claim.
- No `ttft_ms`/`decode_tps` on any pass; `received` reached `30.0`; `pass_ms` present on every
  pass and `0.0` on the tail.
- Engine languages seen across the run: `en` then `es` (the mixed-language segment), as ADR
  0011 records.

The first five rows reproduced the implementer's table almost verbatim (single-run CPU jitter
only in `pass_ms`):

```
0 keys ['language','pass_ms','pending','received','text','words']
   text=''  pending='He hoped.'  language='en'  received=1.0  pass_ms=65.4  words=0
1 text='He' pending='hoped there would be stew for...' language='en' received=2.0 pass_ms=81.1 words=1
2 text='hoped there would be stew' pending='for dinner, turnips.' received=3.0 pass_ms=118.7 words=5
3 text='for dinner,' pending='turnips and carrots and' received=4.0 pass_ms=155.7 words=2
4 text='turnips and carrots and' pending='bruised potatoes.' received=5.0 pass_ms=205.5 words=4
tail keys ['language','pass_ms','pending','received','text','words']
tail: {'text':'', 'words':0, 'pending':'', 'language':'en', 'received':30.0, 'pass_ms':0.0}
generator exhausted after tail: OK
```

I also confirmed with the real engine that `flush()` on a **never-fed** fresh decoder returns
`''` (so `drive()`'s unconditional final flush is free and the empty-input path is sound), and
that a `process()` after that empty flush still works.

The design rests on this contract and the contract is real. **R1** notes its version coupling.

### 2. The seam guarantee, reproduced independently — pass

`/tmp/t14review/seam_probe2.py` drove the **actual** `WhistleTranscriber.stream()` over 30 × 1 s
`AudioBuffer`s at the default 5 s window / 1 s chunk (a logging subclass of the private decoder
recorded every delta and every flush; the probe is not committed). My numbers:

| Measurement | My independent run | Implementer's 001-comment | ADR 0011 |
|---|---|---|---|
| per-chunk latency median / p90 | **122.5 ms / 195.0 ms** (max 260.9) | 121.8 ms / 199.5 ms | 148 ms / ~300 ms |
| final-flush latency | 0.1 ms | 0.1 ms | 0.0 ms |
| total, 30 s | **3.78 s (0.126× realtime)** | 3.75 s (0.125×) | 5.62 s (0.19×) |
| segments | 31 (30 partials + 1 final) | 31 | — |
| process / flush events | 30 / 7 | 30 / 7 | 6 boundary + 1 final |
| windowed words | 68 | 68 | 68 |
| one-shot words | 69 | 69 | 68 → 68 |
| WER windowed vs `ref_perclip` (normalised) | **0.1765** | 0.176 | 0.176 |
| WER one-shot vs `ref_perclip` (normalised) | **0.1912** | 0.191 | 0.191 |

The windowed transcript I observed is **byte-for-byte identical** to the one in
`001-comment.md`, and so is the one-shot transcript — the engine path is deterministic here.
`verbatim concat of decoder deltas+tails == final.text: True`.

**Duplication / dropped words: none, and I agree with the implementer.** My `difflib` run over
the one-shot → windowed token sequences (`autojunk=False`) reported
`difflib tags: ['equal', 'replace']` — no `insert`, no `delete`. A seam duplication would be an
`insert`; a drop a `delete`. The only length-changing hunk is a 9→8-token `replace` in the
garbled Spanish section (engine mis-recognition, not a seam). This is corroborated by three
further checks in `/tmp/t14review/boundary_probe.py`:

```
tail_last='potatoes.' -> next_first='and'     DUPLICATE=False
tail_last='sauce.'    -> next_first='¿Quién'  DUPLICATE=False
tail_last='quién?'    -> next_first='maybe'   DUPLICATE=False
tail_last='yellow'    -> next_first='Lamps'   DUPLICATE=False
tail_last='brothels.' -> next_first='"Stuff'  DUPLICATE=False
adjacent duplicate words in final: []
sum of committed+tail tokens=68 final tokens=68 equal=True
```

**No blocking finding.** The `stop()` resets correctly across all seven flushes (the 30 s run
fed seven consecutive stream generators end-to-end without error).

### 3. Inter-delta whitespace is the decoder's job — pass

Read: `_format()` returns `""` for empty text, prepends exactly one space when `self._emitted`
is set, and sets `_emitted` on the first non-empty delta; `_emitted` is **not** reset by
`_open_stream()`, so the first delta of a new window still gets its separator. `pending` is
prefixed by the same condition for the `drive()` display string only. `drive()` concatenates
`committed`/`flush()` verbatim, so this is the right owner.

I inspected the real 30 s transcript specifically at all six window boundaries:

```
first non-empty process delta: idx=1 value='He' leading_space=False
final text has double space: False
boundary flush#5  tail=' bruised potatoes.'  @char 64:  ...'urnips and carrots and bruised potatoes. and'...
boundary flush#11 tail=' latled out in thick peppered flour fat and sauce.'  @char 110: ...'at mutton pieces to be latled out in thick p'...
boundary flush#17 tail=' revolucionan algunos pájaros de quién?'  @char 194: ...'ramas medio sumergidas revolucionan algunos '...
boundary flush#23 tail=' After early nightfall, the yellow'  @char 255: ...' maybe queen legendary After early nightfall'...
boundary flush#29 tail=' brothels.'  @char 352: ...'squalid quarter of the brothels. "Stuff it i'...
boundary flush#35 tail=''  @char 409: ...' belly counselled him.'...
```

- **Leading space on the first delta:** not present (`'He'`); a transcript never starts with a
  space.
- **Missing space at a window boundary:** none. Every boundary is word-separated; the char at
  each boundary is a space, never a letter.
- **Doubled space:** none anywhere in the final text.
- **Delta after `flush()` forgetting the separator:** the `_emitted` flag persists across the
  boundary (mutation M2 below fails the tests when reset).

All four failure modes the ticket warned about are absent in the real transcript.

### 4. `pending` never reaches the transcript — pass

By reading: `streaming._drive()` builds `transcript` only from `pass_.committed` and
`decoder.flush()`; `pending` is assigned only to compute the display `text = transcript +
pending` and is cleared on flush. The adapter's `pending` is never fed back into `committed`.

By mutation/garbage: the committed test
`test_pending_is_display_only_and_never_reaches_the_transcript` scripts `pending =
"POISON-DISPLAY-ONLY"` on every pass and asserts the poison is absent from the final text while
the partials do show it; it passes. I re-ran it in the mutation pass and also confirmed by the
real-engine probe that the final text equals the committed-delta concatenation exactly.

### 5. Window/chunk sizes from `WhistleConfig`, windowing delegated — pass

`stream()` is literally `streaming.drive(decoder, chunks, window_seconds=self.config.window_seconds,
chunk_seconds=self.config.chunk_seconds)` (the diff adds no boundary math). The private decoder
has no grouping or window accounting — it only maps one `process()` to one generator `next()`
and one `flush()` to the generator's own terminal `stop()`. `test_drive_is_the_windowing_implementation`
compares the adapter's output against a direct `drive(...)` call; `test_configured_window_and_chunk_sizes_are_used`
shows a 2 s window produces 4 generators over 6 s (3 boundaries + final flush). Confirmed by
reading and by both tests.

### 6. Load-path integrity — pass

- `stream()` calls `self._require_loaded()` **eagerly** and returns `drive(...)`; it is a normal
  function, not a generator function, so a missing `load()` raises at call time. Verified
  against the real adapter: `eager before-load error: WhistleNotLoadedError`.
- It reuses `self._engine` (no second `Whistle` is constructed); verified
  `engine reused (identity same): True` and by `test_stream_uses_the_already_loaded_engine`.
- `_import_needle()`, `_apply_env_overrides()`, and `_assert_managed_artifacts()` are untouched
  by the diff; `load()` is byte-identical to `main`. Verified `env unchanged across stream():
  True` and `needle module identity stable: True`.
- No env var or path is written by `stream()`; `_apply_env_overrides()` remains the only
  `os.environ` writer and is called only in `load()`.

### 7. The out-of-scope `tests/test_whistle_batch.py` change — necessary and minimal

Exact diff (`git diff main...HEAD -- tests/test_whistle_batch.py`):

```diff
@@ -9,8 +9,9 @@
-the managed-path assertions, ``ensure_model`` delegation, and the deliberate
-``stream()`` stub (ADR 0010, ADR 0011, ADR 0012, ADR 0017, ADR 0019).
+the managed-path assertions, ``ensure_model`` delegation, and the eager
+before-``load()`` failure of ``stream()`` (now implemented by the streaming
+adapter, ADR 0010, ADR 0011, ADR 0012, ADR 0017, ADR 0019).
@@ -410,8 +411,12 @@ def test_close_returns_the_adapter_to_the_unloaded_state(
-def test_stream_is_a_not_implemented_stub(transcriber: WhistleTranscriber) -> None:
-    with pytest.raises(NotImplementedError):
+def test_stream_before_load_fails_eagerly(transcriber: WhistleTranscriber) -> None:
+    # The batch ticket's ``stream()`` was a NotImplementedError stub; the
+    # streaming adapter ticket replaced the body (ADR 0011). What is still worth
+    # guarding here is that the error is raised at call time, not on iteration:
+    # a caller must not start capturing audio for a stream that cannot run.
+    with pytest.raises(WhistleNotLoadedError):
         transcriber.stream([])
```

**Justified.** The `NotImplementedError` stub this test guarded *is this ticket's deliverable*,
so the assertion must change. The replacement preserves the property the batch review (002,
criterion 7) actually cared about — the error is raised **eagerly, not as a generator** — and it
does so with a stronger, more precise exception. The `WhistleNotLoadedError` import already
exists in the file (line 43), so no import churn. No other batch assertion was changed or
relaxed: batch coverage is neither weakened nor removed.

### 8. ADR 0019 and the widened check — pass

`uv run mypy src/ges2t tests` → `Success: no issues found in 25 source files`. No positional
tuple returns, no bare `Any` where a real type is knowable (`_needle: Any` and `engine: Any` are
the deliberate third-party boundary, matching the batch path), no implicit `Optional` (`str |
None`), and every definition — including tests and `def __next__`/`def __init__` — carries a
return annotation. `StreamingPass(committed=..., pending=..., language=...)` is constructed by
keyword.

Config-effectiveness probe (temporary, fully reverted): adding `def untyped_probe(x): return x`
to `src/ges2t/_probe_untyped.py` made the wide command report
`error: Function is missing a type annotation [no-untyped-def]` (checked 26 files); after
removal `git status --porcelain` is empty and mypy is green again.

### 9. Tests are honest, headless, fast — pass

- `uv run pytest tests/test_whistle_stream.py -q` → **21 passed in 0.15 s**.
- `uv run pytest -q` → **274 passed in 0.96 s**.
- No engine: `needle` is replaced by the fake `_needle` and `sys.modules['needle'] = None`.
  I additionally ran the **whole suite with a meta-path import blocker for `needle`** (simulating
  an uninstalled package): **274 passed in 0.96 s**. No test imports `needle` (grep confirms; the
  only textual match is a comment).
- Mutation testing in a **throwaway worktree** at `f51d543` (removed afterwards, reviewed
  worktree clean):

| Mutation to `whistle.py` | Failing tests |
|---|---|
| leading-space logic disabled in `_format` | 3 failed |
| `_emitted` reset at a window boundary | 2 failed |
| `flush()` does not open a fresh stream | 7 failed |
| `process()` validation removed | 2 failed |
| `stream()` made non-eager (`self._engine`) | 1 failed |
| language report forced to `None` | 3 failed |

Every mutation is caught; the anti-fabrication, whitespace, windowing, and eager-load
assertions all bite.

### 10. Scope / hygiene — pass

`git diff --name-only main...HEAD` lists exactly:

```
docs/project/README.md
docs/project/live-dictation-ux/whistle-streaming-adapter/001-comment.md
docs/project/live-dictation-ux/whistle-streaming-adapter/ticket.md
src/ges2t/transcribers/whistle.py
tests/test_whistle_batch.py
tests/test_whistle_stream.py
```

- The only `src/` file touched is `whistle.py`; the only README change is the ticket-14 row
  (`to do` → `in review`, branch filled in). No other README row changed.
- No conflict markers in any changed file (the `=====` hits from a naive grep are pytest
  separator lines in unrelated, pre-existing docs).
- No audio or binary is committed (`--numstat` shows only text).
- Relative links in `ticket.md`, `001-comment.md`, and `README.md` all resolve (0 broken).
- `git diff --check` reports one cosmetic trailing space at `001-comment.md:99` (R3).

## Evidence — command output

```
$ uv run pytest tests/test_whistle_stream.py -q
21 passed in 0.15s

$ uv run pytest -q
274 passed in 0.96s

$ PYTHONPATH=/tmp/t14review uv run pytest -p needle_blocker -q   # needle import blocked
274 passed in 0.96s

$ uv run mypy src/ges2t tests
Success: no issues found in 25 source files

$ uv run mypy src/ges2t tests   # with src/ges2t/_probe_untyped.py present, then deleted
src/ges2t/_probe_untyped.py:1: error: Function is missing a type annotation  [no-untyped-def]
Found 1 error in 1 file (checked 26 source files)
```

Real-engine generator contract and seam numbers are transcribed in criteria 1–3. The
independent load-path probe:

```
eager before-load error: WhistleNotLoadedError
engine reused (identity same): True
env unchanged across stream(): True
needle module identity stable: True
segments: 2 final text: 'He hoped.'
```

## Downstream contract for ticket 15

Verified by reading, by the real-engine probes, and by the new tests:

- **`WhistleTranscriber.stream(chunks)` is exactly** `streaming.drive(_WhistleStreamingDecoder(
  engine, config.language), chunks, window_seconds=config.window_seconds,
  chunk_seconds=config.chunk_seconds)`. Ticket 15 needs only `Transcriber.stream()`; it must
  **not** import `ges2t.streaming`. Nothing I saw contradicts ticket 15's
  `streaming-session` requirement — the adapter exposes the full, window-agnostic segment stream.
- **Segments:** one **display-only** non-final segment per `chunk_seconds`-sized group, then
  exactly **one `is_final=True`** segment. A partial's `text` is `transcript + pending`
  (never inject it); the final `text` is the committed transcript (partials + flush tails).
- **Empty input → exactly one empty final segment** and one `stop()` flush. Verified with the
  fake test and with the real engine (`flush()` on a never-fed stream returns `''`).
- **`language`** is the most recent non-`None` engine language seen on any pass, carried onto
  the final segment; the engine auto-detects per pass and can switch mid-utterance (this run
  reported `en` then `es`).
- **`pass_ms` is always `None`** (`StreamingPass` has no `pass_ms` field; `TranscriptionSegment`
  gets `None`). Timing is the session's job (ADR 0011).
- `stream()` raises `WhistleNotLoadedError` at **call** time if `load()` has not run, so ticket
  15 should `load()` (or rely on the composition root) before constructing the session.
- Ticket 15's `finish()` must consume the stream to `StopIteration` to obtain the final segment
  (the final flush happens only when the chunk iterable ends). `cancel()` can simply abandon the
  iterator; the underlying package generator stops the engine stream on garbage collection.
- The adapter does **not** use `drive()`'s `on_partial` callback; partials arrive as segments.
  Ticket 15's `partials()` should therefore take the latest non-final segment's `text` and
  deduplicate (as its ticket requires).

## Risks / follow-ups (all non-blocking)

- **R1 — the generator contract is coupled to the pinned package version.** The decoder depends
  on `cactus-needle==3.1.3`'s exact shape: `stream()` is a pure-Python generator that pulls
  exactly one chunk per `next()` and issues its terminal `stop()` when the input iterator is
  exhausted. The unit fake encodes the same shape, so a future package upgrade that buffered or
  batched chunks would keep the suite green while breaking the real seam. The version is pinned
  in `pyproject.toml`, but nothing asserts it at load time. A `needle.__version__` guard (or a
  real-engine contract test, if the suite ever allowed one) would close the gap. Not a defect in
  this ticket.
- **R2 — misleading eager-error message.** `_require_loaded()` raises
  `"WhistleTranscriber.transcribe() was called before load()"`, which is also what a caller sees
  from `stream()`. The exception type is correct; the method name in the message is not. A
  one-line message change would be clearer.
- **R3 — cosmetic.** `001-comment.md:99` has trailing whitespace (`git diff --check`).
- **R4 — WER method not recorded.** The reported 0.176/0.191 WER figures require token
  normalisation (lowercase + punctuation stripping). Raw punctuation-sensitive WER is 0.2647 for
  both. The implementer's numbers reproduce exactly under normalisation, so the claim is sound,
  but the normalisation is not stated in `001-comment.md`. Recording it would make the evidence
  self-contained.

## Not verified / out of reach

- A filesystem with `cactus-needle` genuinely uninstalled: the package is installed in the
  worktree venv. I simulated it with an import blocker for `needle`, which is the property that
  matters (the tests never import the backend), but I did not uninstall and reinstall the
  dependency.
- The microphone/threading end-to-end path (`StreamingDictationSession`, ticket 15) and the
  service wiring (ticket 16) are out of scope for this ticket.
- Long-duration stability beyond the 30 s reference clip (e.g. many consecutive windows,
  minutes-long utterances) was not exercised; the 30 s run does exercise seven consecutive
  generator/flush cycles.
