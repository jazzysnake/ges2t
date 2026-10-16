# Nemotron 3.5 pure-streaming integration — how the append-only interim stream is carried into the committed-delta path

- **Ticket / question:** ticket 56 of Epic 6. *"kick off a discovery that looks into the code and tells
  me how this streaming model can be integrated without requiring two passes of a sliding window and
  just using the pure streaming stuff."*
- **Type:** code investigation / design. **No `src/` or `tests/` change. No new measurement.**
- **Branch:** `feat/nemo-pure-streaming-integration`
- **Worktree:** `.worktrees/nemo-pure-streaming-integration`
- **Decision it serves:** [ADR 0022](../../../adr/0022-nemotron-streaming-pure-streaming-backend.md)
  — **accepted (user sign-off 2026-10-14)**. ADR 0011 is **not** amended and **not** edited.
- **Prior evidence:** ticket 55
  ([`../nemotron-3-5-asr-streaming-measurement/REPORT.md`](../nemotron-3-5-asr-streaming-measurement/REPORT.md))
  §7, and its committed per-interim traces under
  `../nemotron-3-5-asr-streaming-measurement/data/partials/`.
- **Engine on disk (read-only):**
  `~/.cache/ges2t-discovery/nemotron-3-5-asr-streaming-measurement/engine/nemo-speech-0.2.0-linux-x86_64-vulkan/include/nemo_speech/asr.h`.

> **Method.** This is a *reading* ticket. Every structural claim below carries a `file:line` reference
> into the worktree, or a command that was actually run. The two real commands run for this report are
> (a) reading the installed C header, and (b) re-deriving the append-only property from ticket 55's
> **committed** per-interim traces alone (§10). No engine was launched, no microphone, no `sudo`, no
> package install, no network. Where the code does not settle a question the report says so instead of
> inventing a design.

---

## 1. Answer (headline)

**The window can be bypassed cleanly, and the falsifier in ADR 0022 did not fire.**

The 5 s window and the flush cadence are **not** in the session and **not** in the service. They live
in exactly one place: `ges2t.streaming.drive` (`src/ges2t/streaming.py:95`), which has **one
production caller**, `WhistleTranscriber.stream` (`src/ges2t/transcribers/whistle.py:630`, returning
`drive(...)` at `:665`). The streaming session
(`StreamingDictationSession._run`, `src/ges2t/streaming_session.py:265`) only iterates
`transcriber.stream(...)` (`:268`); the service (`src/ges2t/service.py`) only forwards the resulting
`LiveText` values. **Nothing above the `Transcriber` Protocol knows a window exists.**

Therefore a pure-streaming adapter bypasses the window **by construction**: it implements
`Transcriber.stream` as a generator over the NeMo-Speech C ABI and simply never calls
`streaming.drive`. The commit policy is a string operation on the engine's interim — commit the
prefix up to the last completed word, hold the trailing token as `pending` — and the existing
`CommittedTyper` (`src/ges2t/incremental_typing.py:69`) types the committed prefix with its typed
watermark and applies the single trailing space at `finish` (`:109`). No growing buffer is kept
(only the cumulative committed **string**, exactly as the Whistle path already keeps `transcript` in
`streaming.py:160`), and no second pass is needed.

**Recommended seam:** add a small, engine-agnostic **append-only driver**
`ges2t.streaming.drive_append_only` with a narrow `AppendOnlyDecoder` Protocol (`push`/`finish`),
reusing the existing chunk-regrouping `streaming._group_chunks` (`src/ges2t/streaming.py:211`) and the
existing `StreamingPass`/`TranscriptionSegment` vocabulary. The `nemo_speech` adapter supplies a
`_NemoSpeechDecoder` over a `NemoSpeechLibrary` ctypes Protocol (the ADR 0021 `WhisperLibrary`
precedent, `src/ges2t/transcribers/whisper_cpp.py:223`). The windowed `drive` and the Whistle path
are untouched.

---

## 2. A precise map of the current streaming path (DoD 1)

### 2.1 The vocabulary (`src/ges2t/interfaces.py`)

- `Transcriber.stream(chunks) -> Iterator[TranscriptionSegment]` — `interfaces.py:176`. The **only**
  streaming seam. Its docstring already says the quiet part: *"The caller decides how many chunks to
  feed; windowing and commit logic live in the streaming module, not here"* (`:181`).
- `LiveText(committed, pending)` — `interfaces.py:70`. `committed` is cumulative and append-only;
  `pending` is the display-only tail; `display = committed + pending` (`:97`–`:99`).
- `TextTyper.type_text(text)` — `interfaces.py:224` (class) / `:237` (method);
  `LiveTyper.type_committed` / `finish` — `interfaces.py:243` (class) / `:254`/`:258` (methods).
- `Injector.inject(text)` — `interfaces.py:215` (batch only; not used on the streaming path).

### 2.2 The windowed driver (`src/ges2t/streaming.py`, ticket 13 / ADR 0011)

- `StreamingPass(committed, pending, language)` — `streaming.py:44`. `committed` is the **delta**
  *"the words the engine's two-consecutive-passes-agree rule confirmed"* (`:47`–`:48`); this is where
  the **two-pass agreement rule** is *modelled*. It is **not computed in Python** — it is the Whistle
  engine's own rule (ADR 0011 quotes the `needle.h` contract verbatim) and is surfaced to the driver
  as `committed` vs `pending`.
- `StreamingDecoder` Protocol (`process` / `flush`) — `streaming.py:67`, methods at `:77`/`:86`.
- `drive(...)` — `streaming.py:95`, delegating to `_drive` at `:151`.
- **The 5 s window** is `streaming.py:179`: `if fed_samples >= window_seconds * group.sample_rate:`
  → `transcript += decoder.flush()` (`:180`). `fed_samples` is sample-based (`:178`), reset after a
  flush (`:184`). The window length itself is configuration:
  `whistle_config.DEFAULT_WINDOW_SECONDS = 5.0` (`src/ges2t/transcribers/whistle_config.py:57`).
- `flush()` is the engine's `stop()`: *"Commit the unconfirmed tail, reset the stream"*
  (`streaming.py:86`–`:91`). This reset is the reason the two paths cannot share a driver verbatim.
- The final flush is unconditional at end of input (`streaming.py:202`), and the only
  `is_final=True` segment is yielded there (`:203`).
- `_group_chunks` (`streaming.py:211`) regroups arbitrary incoming buffers into `chunk_seconds`
  groups by sample count; `_target_samples` at `:248`.

### 2.3 The Whistle adapter (`src/ges2t/transcribers/whistle.py`)

- `_WhistleStreamingDecoder` — `whistle.py:387`. `process` (`:435`) feeds one chunk into a
  `needle.Whistle.stream` generator and returns the engine's `text` as `committed` and its `pending`
  as `pending`; `flush` (`:456`) ends the generator (engine `stop()`), takes the tail, and opens a
  fresh stream.
- `WhistleTranscriber.stream` — `whistle.py:630`; it validates the silence gate `_speech_only`
  (`:294`) and returns `streaming.drive(decoder, _speech_only(chunks), window_seconds=…,
  chunk_seconds=…)` (`:665`–`:669`). **This is the only production call to `drive`.**

### 2.4 The session (`src/ges2t/streaming_session.py`)

- `_run` — `streaming_session.py:265`; it is the worker body and the only consumer of
  `transcriber.stream`: `for segment in self._transcriber.stream(self._audio.chunks(self._chunk_seconds))`
  (`:268`–`:269`).
- `_record` — `:280`: non-final segments advance `self._partials` (`:290`, deduped on `display`),
  the final segment is stored in `self._final_text` (`:285`), a **separate** field (`:126`).
- `partials()` — `:154` (blocking generator yielding `LiveText`); `finish()` — `:177` returns only
  `_final_text` (`:201`); `cancel()` — `:212`; `_stop_capture` — `:245` (`audio.stop()` then
  `worker.join()` at `:262`, unbounded on purpose).
- The session holds **no** window and **no** `window_seconds` parameter; the docstring states the
  window is the adapter's property.

### 2.5 The committed-delta typing path (`src/ges2t/incremental_typing.py`, tickets 32–34)

- `CommittedTyper` — `incremental_typing.py:69`; `type_committed` (`:97`) types only the suffix of
  the cumulative `committed` beyond the typed watermark `self._typed` (`:87`, `:134`–`:151`);
  `finish(final_text)` (`:109`) reconciles with the final transcript and applies ADR 0012's trailing
  space **once** via `decide_injection` (`:126`). A non-extension raises `IncrementalTypingError`
  (`:59`).

### 2.6 The service (`src/ges2t/service.py`, ticket 21)

- `handle` — `service.py:252`; state transitions run under `self._lock`, every collaborator call is
  **deferred past the lock** (`_transition_scope`, `:475`; the state lock is `threading.Lock`).
- `_commit_start` — `:337` creates and `start()`s the session off-lock, then under the lock binds
  `self._live_typer` and starts the partials forwarder (`_start_partials_locked`, `:525`).
- The forwarder (`:525`–`:542`) calls `live_typer.type_committed(live.committed)` (`:541`) and
  `self._status_ui.partial(live)` (`:542`) on the `ges2t-partials` thread, **not** under the service
  lock.
- `_run_finish` — `:427` runs on the `ges2t-finish` thread: `session.finish()` then
  `live_typer.finish(text)` (`:447`) or, on the batch path, `injector.inject(text)`.

### 2.7 The composition root (`src/ges2t/pipeline.py`)

- `_TRANSCRIBER_BUILDERS` — `pipeline.py:250`; `build_transcriber` — `:256`.
- `_streaming_is_selected` / `_select_session_kind` — `:443`/`:455` (streaming needs
  `streaming.enabled and transcriber.supports_streaming`).
- `_streaming_chunk_seconds` — `:430`; **this is the one hard coupling**: it reads
  `WhistleConfig.from_mapping(block).config.chunk_seconds` (`:440`) regardless of the selected
  engine. A third engine must generalise this (§8).
- `_session_factory` — `:618`; `_build_live_typer_factory` — `:592` (builds a fresh `CommittedTyper`
  per recording, typed against `TextTyper`).

### 2.8 How committed deltas reach the injector (end to end)

```
engine interim ─► decoder.process() ─► StreamingPass.committed (delta)
   └─ drive folds deltas ─► LiveText.committed (cumulative) ─► TranscriptionSegment
        └─ StreamingDictationSession._run yields ─► _record ─► _partials
             └─ partials() forwarder (ges2t-partials) ─► CommittedTyper.type_committed(committed)
                  └─ TextTyper.type_text(suffix beyond watermark) ─► ydotool
final transcript ─► _final_text ─► Service._run_finish ─► CommittedTyper.finish(final)
   └─ decide_injection adds ADR 0012's single trailing space once
```

The `pending` tail never appears in any of these arrows except `status_ui.partial` (display).

---

## 3. The exact seam for a pure-streaming engine (DoD 2)

### 3.1 Which Protocol methods the adapter implements

The adapter implements the same `Transcriber` Protocol (`interfaces.py:146`); **no interface change is
required**:

| method | pure-streaming implementation |
|---|---|
| `name` | `"nemo_speech"` (a new identifier) |
| `supports_streaming` | `True` |
| `ensure_model` | pin+acquire the engine archive and the q8_0 GGUF via `model_store` |
| `load` | create the `Recognizer` once, resident for the process life (ADR 0021 shape) |
| `transcribe` | the offline call `nemo_speech_asr_recognize_f32` (batch path, `streaming.enabled=false`) |
| `stream` | a generator over the C ABI's interim stream — **the seam** |
| `close` | destroy the recognizer |

### 3.2 How the window is bypassed

`streaming.drive` has exactly one production caller (`whistle.py:665`); the session and service call
`transcriber.stream` generically (`streaming_session.py:268`). A `nemo_speech` adapter that returns
its own generator from `stream` never touches `drive`, so the window, the flush cadence, and the
engine-reset semantics are **absent by construction** — not "disabled by a flag". This is the cleanest
possible form of bypass: no new branch in existing production code, no change to the Whistle path.

### 3.3 Concrete shape (recommended)

```python
# src/ges2t/streaming.py (additive; the windowed drive() is untouched)

@runtime_checkable
class AppendOnlyDecoder(Protocol):
    """A cache-aware streaming engine that emits append-only interims (ADR 0022)."""
    def push(self, buffer: AudioBuffer) -> StreamingPass: ...   # feed audio, return the pass
    def finish(self) -> str: ...                                # end of input, return the final text

def drive_append_only(
    decoder: AppendOnlyDecoder,
    chunks: Iterable[AudioBuffer],
    *,
    chunk_seconds: float,
    on_partial: Callable[[LiveText], None] | None = None,
) -> Iterator[TranscriptionSegment]:
    """Fold an append-only interim stream into the committed-delta contract.

    No window: `decoder.finish()` is called exactly once, at end of input. Reuses
    ``_group_chunks`` so arbitrary buffer sizes are regrouped by sample count exactly
    as the windowed path does.
    """
```

Reuses, unchanged: `StreamingPass` (`streaming.py:44`), `_group_chunks` (`:211`),
`TranscriptionSegment`/`LiveText` (`interfaces.py:103`/`:70`). The adapter:

```python
# src/ges2t/transcribers/nemo_speech.py
class _NemoSpeechDecoder:
    def push(self, buffer):        # lib.push(...) then drain lib.next() -> StreamingPass
    def finish(self):              # lib.finish(), drain lib.next() -> final transcript

class NemoSpeechTranscriber:
    name = NEMO_SPEECH_ENGINE
    supports_streaming = True
    def stream(self, chunks):
        return streaming.drive_append_only(
            _NemoSpeechDecoder(self._library, self._recognizer, ...),
            _speech_only(chunks),          # or a Nemo-specific gate; see §5.4
            chunk_seconds=self.config.chunk_seconds,
        )
```

### 3.4 What tests break or need pinning

- **Nothing existing breaks.** `streaming.py` is additive; `drive`/`StreamingDecoder` keep their
  signatures and behaviour. `test_streaming.py` (the driver's spec), `test_whistle_stream.py`
  (the adapter-through-`drive` pins, e.g. `test_drive_is_the_windowing_implementation` at
  `tests/test_whistle_stream.py:430`), `test_streaming_session.py`, `test_incremental_typing.py`, and
  `test_pipeline_streaming.py` are untouched.
- **New pins needed:** `test_streaming_append_only.py` (driver: no window flush, exactly one final
  segment, committed cumulative/append-only, pending never in the transcript), and
  `test_nemo_speech_stream.py` (adapter over a fake `NemoSpeechLibrary`, mirroring
  `FakeStreamingDecoder` in `tests/test_streaming.py` and the fake-library pattern in
  `test_whisper_cpp.py`).
- **One existing test becomes a guard rather than a spec** if `_streaming_chunk_seconds` is
  generalised: `test_defaults_come_from_whistle_config_when_the_block_is_absent`
  (`tests/test_pipeline_streaming.py:244`) must keep passing for `whistle`, and a new case must pin
  the Nemo chunk default.

---

## 4. C-ABI interim delivery (DoD 3)

### 4.1 Delivery shape: **polled, non-blocking, on the calling thread**

From the installed header (`include/nemo_speech/asr.h`, read in the scratch engine tree):

- `nemo_speech_asr_stream_push_f32(...)` — *"Buffer mono float32 audio (**no decode**). … next() drives
  decoding."* (header, streaming section).
- `nemo_speech_asr_stream_next(stream, &out)` — *"Pull one result. On `NEMO_SPEECH_ASR_OK`, `*out` is a
  result handle the caller must destroy, **or NULL when nothing is currently available (need more
  audio)**."*
- `nemo_speech_asr_stream_finish(stream)` — *"No more audio: flush the tail. The end-of-stream final is
  then returned by `nemo_speech_asr_stream_next`."*
- Partial vs final: `nemo_speech_asr_result_is_final(result)`; the single `is_final=true` is the
  end-of-stream final. `nemo_speech_asr_result_audio_processed(result)` is the encoder watermark.
- There is **no callback** in the ABI: no function pointer, no listener. The only reader is the
  `stream_next` poll loop. (`grep -in "callback" asr.h` → no hits; the word appears only in the
  unrelated whisper.cpp parameters, not here.)

This is corroborated by ticket 55's harness, which drives that exact loop:
`measure_nemo_streaming.py` does `push_f32(...)` then `_collect(...)`, and `_collect` repeatedly calls
`stream_next` until it returns a NULL handle. So a single push may yield 0, 1, or several results; the
harness keeps **every** one.

### 4.2 Which thread, and the service lock

The C ABI is driven **only** on the session's worker thread, `ges2t-streaming-worker`
(`StreamingDictationSession._run`, `streaming_session.py:265`). That thread:
- calls `audio.chunks(chunk_seconds)` and `transcriber.stream(...)` in the `for` header
  (`:268`–`:269`) — i.e. the engine call happens **outside** `_record`'s brief condition-lock
  (`:280`);
- never touches the service state lock (`service.py:252`, held only for transitions);
- never touches the `CommittedTyper` lock (that is the `ges2t-partials`/`ges2t-finish` threads'
  domain, `service.py:541`/`:447`).

So the ticket-21 rule — *no collaborator called under the service lock* — is preserved **unchanged**:
the engine runs on the same class of thread the Whistle adapter already uses, and the status UI stays
outside the lock (`_transition_scope`, `service.py:475`). **No re-entrancy**: `stream_next` is
re-entered by the same thread that pushed, and no callback re-enters ges2t.

### 4.3 Blocking hazards to flag explicitly

1. **Decode is synchronous in `stream_next`.** At the measured 160 ms step the engine spends ~24–47 ms
   per chunk on Vulkan (ticket 55 §5.2), i.e. the worker blocks for that long. That is well inside the
   budget and identical in kind to Whistle's `next(generator)`. It must **not** run on the socket loop;
   it does not.
2. **`finish()` joins the worker unboundedly** (`streaming_session.py:262`). If `stream_finish`
   wedges, a recording's `finish()` blocks on the `ges2t-finish` thread (bounded by the service's 5 s
   shutdown join, `service.py:_SHUTDOWN_JOIN_TIMEOUT_SECONDS`). This is the existing, documented
   accepted risk (ADR 0011), inherited, not new.
3. **One recognizer, but the engine supports concurrent calls.** The header documents *"[t]ransparent
   dynamic batching across concurrent recognition calls. A single recognizer owns one model; callers
   submit independent utterances from multiple threads and compatible neural work is combined into GPU
   batches"* (`asr.h:131`–`:134`). It is explicitly designed for concurrent calls, not a
   single-threaded singleton. ges2t keeps **one** recognizer resident for the process (the ADR 0021
   shape, created once in `load()`), and the **operational rule still holds**: the service state
   machine allows at most one recording at a time, so the adapter opens exactly one stream per
   recording (`nemo_speech_asr_streaming_recognize`) and closes it (`nemo_speech_asr_stream_close`) in
   a `finally`. The rule holds because of ges2t's single-recording contract, **not** because the
   engine forbids concurrency; a leaked stream handle would still keep a stream (and its caches)
   alive — flag it as a test pin.
4. **`nemo_speech_asr_last_error` is thread-local** (header: *"valid until the next call on the same
   thread"*), so any error string must be read on the worker at the point of failure, never later on
   another thread.
5. **Endpointing must stay off.** With `endpointing.enable = true` the engine can emit `is_final=true`
   mid-recording, which would split one hotkey-held utterance into several. Ticket 55's matrix left it
   off (one final per clip). v1 must construct the recognizer with endpointing disabled and treat the
   final as end-of-input only. If it is ever enabled, the driver must close and reopen a stream at
   each mid-stream final — call it out as a follow-on, not v1.

### 4.4 Cadence and the partial/final distinction (real data)

From the committed traces (command in §10): `stream-jfk-vk.jsonl` has 62 interims + 1 final for 11.00 s
of audio at 160 ms chunks (≈ 68 chunks), so **not every chunk yields a result** and a result is
sometimes a repeat of the previous transcript. Interims are the norm; the final is emitted only after
`stream_finish`. Real examples:

```
  0 interim audio=1.28  'And'
  4 interim audio=1.92  'And so my'
  5 interim audio=2.08  'And so my fel'
  6 interim audio=2.24  'And so my fellow'
  7 interim audio=2.40  'And so my fellow Ame'
 62 final   audio=11.0  'And so my fellow Americans … your country'
```

**Key finding for the commit policy:** across all 1,841 interims in the committed traces, **zero end
with whitespace and zero begin with whitespace** (`ends=0 starts=0`; §10), and **word timestamps are
present only on the final result** — in `stream-jfk-vk.jsonl`, `1 of 63` rows has a non-empty
`word_times`, and it is the final. So the adapter **cannot** use the engine's word list to locate a
word boundary during streaming; the boundary must be computed from the **interim string** (§5).

---

## 5. Commit policy (DoD 4)

### 5.1 The rule

The adapter commits **the prefix of the latest interim up to and including the last completed word**
and holds the trailing whitespace-delimited token as `pending` (display-only). Given the observed
shapes, that is exactly:

```python
def _committed_prefix(interim: str) -> str:
    """Longest prefix of an interim ending at a completed word boundary.

    Interims are plain space-separated tokens with no leading/trailing whitespace
    (measured: ends=0/1841, starts=0/1841). The final token may still be extended
    by the next interim, so it is held pending.
    """
    stripped = interim.rstrip()
    cut = stripped.rfind(" ")
    return "" if cut < 0 else stripped[:cut]
```

Per `push`:

```python
interim = last remaining stream_next transcript      # engine interim
target  = _committed_prefix(interim)                 # one word behind, by design
delta   = target[len(self._committed):]              # StreamingPass.committed
pending = interim[len(target):]                      # StreamingPass.pending, e.g. " fel"
self._committed = target
```

This reproduces ticket 55's stated nuance (*"the adapter must commit only up to the last word/frame
boundary and treat the trailing partial as pending"*) and mirrors `FakeStreamingDecoder`
(`tests/test_streaming.py`), whose *"newest revealed word stays pending and every earlier one is
committed"* is the same one-word-behind rule.

Worked example on real interims:

| interim | committed prefix | `committed` delta | `pending` |
|---|---|---|---|
| `And so my fel` | `And so my` | `And so my` | ` fel` |
| `And so my fellow` | `And so my` | `""` | ` fellow` |
| `And so my fellow Ame` | `And so my fellow` | ` fellow` | ` Ame` |
| final `And so my fellow Americans …` | whole | ` Americans …` | `""` |

Only `committed` is ever typed; the `fellow` token is typed when the *next* word starts, and the last
word is typed at `finish()`.

### 5.2 Reused functions (tickets 32–34) — all already exist

- `LiveText` carries the two lifetimes separately (`interfaces.py:70`).
- `CommittedTyper.type_committed` (`incremental_typing.py:97`) diffs each cumulative `committed` against
  the typed watermark — it does **not** sum deltas, so a repeated or collapsed command is safe
  (`_extend`, `:134`–`:151`).
- `CommittedTyper.finish` (`:109`) reconciles with the final transcript, assembles the document with
  `decide_injection`, and applies the **single trailing space** once (`:126`). Whitespace-only input
  types nothing.
- `Service._run_finish` (`service.py:447`) and the forwarder (`:541`) are unchanged.

So the pure-streaming path reuses the whole tickets 32–34 contract with **no change**; the only new
logic is the string boundary in §5.1.

### 5.3 Edge cases

- **Non-advancing interims** (`And so` three times in a row, `stream-jfk-vk` rows 1–3): empty delta,
  identical `pending`; `StreamingDictationSession._record` dedups on `display` (`streaming_session.py:290`),
  so nothing is re-typed. A driver may also skip yielding an unchanged pass.
- **Punctuation** (`'… your country.'`): the last token is `country.`; it is held pending exactly like a
  partial. Harmless.
- **Silence**: the engine emits **0 interims** and an empty final on digital silence (ticket 55 §7;
  reproduced in §10), so `drive_append_only` yields one empty final and the typer types nothing.
- **Final extends the last interim** (2 of 20 runs completed a trailing partial word): the final
  segment sets `committed=final_text, pending=""`; `CommittedTyper.finish` types the remaining suffix.

### 5.4 The silence gate

The Whistle path runs `_speech_only` (`whistle.py:294`) **before** `drive` to avoid feeding silence.
For Nemo the engine already returns nothing on silence (0 interims), so a ges2t-side gate is
optional. Recommendation: **do not** add a second energy gate in v1 (it is Whistle-specific and the
engine is silent-on-silence), but keep an equivalent "drop a chunk that is pure silence" only if a
measured need appears — otherwise we would be re-deriving a fix for a defect this engine does not have.

---

## 6. Design alternatives + recommendation (DoD 5)

| | **A — dedicated append-only driver** (recommended) | **B — adapter-local generator** | **C — `window_seconds: float \| None` on existing `drive`** |
|---|---|---|---|
| Where the commit fold lives | `streaming.drive_append_only` (+ `AppendOnlyDecoder`) | inside `NemoSpeechTranscriber.stream` | `streaming._drive` with a new branch |
| Blast radius on `streaming.py` | **additive only** (`drive`/`StreamingDecoder` untouched) | **zero** | **modifies the shared `_drive`** — the Whistle driver's core loop |
| Blast radius on `streaming_session.py` | none | none | none |
| Test churn | new `test_streaming_append_only.py`; 0 existing tests change | new adapter test only; the fold is tested through the adapter | existing `test_streaming.py` must be re-read for the new branch; risk of a parameterised matrix |
| Risk to Whistle | none (separate function) | none | **real**: one loop now serves two commit models with opposite `flush()` semantics |
| ADR 0011 principle ("window/commit logic lives in its own module, unit-testable against a fake") | satisfied | violated (handler logic in the adapter) | satisfied but overloads `flush()` |
| Reuse of `_group_chunks` | yes | would duplicate or import a private helper | yes |

- **A** keeps the fold in the unit-testable module, mirrors the existing `drive`/`StreamingDecoder`
  pairing, and shares the non-trivial `_group_chunks` regrouping. `flush()` semantics do **not** have
  to be overloaded (the new Protocol uses `finish()`), so there is no "reset vs end" ambiguity.
- **B** is the smallest diff but puts the commit fold in the adapter, which contradicts ADR 0011's
  placement rule and makes the fold only testable through a fake library.
- **C** is rejected: the Whistle `flush()` means *reset the engine and continue*, while a pure engine's
  end means *finish and stop*; one loop serving both is exactly the kind of silent coupling the
  codebase avoids.

**Recommendation: A**, with the windowed `drive` and the Whistle adapter left byte-for-byte unchanged.

---

## 7. Recommended wiring architecture (the substantial changes, and why)

The streaming *mechanism* is small (§3–§5); the **wiring** is the substantial part, because a third
engine touches the composition root, acquisition, and `doctor`. The architecture below keeps the
ADR 0010 seam intact and mirrors the ADR 0021 whisper.cpp precedent, so no new pattern is invented.

```
config.toml: transcriber.engine = "nemo_speech"
        │
        ▼
pipeline.build_transcriber ──► _TRANSCRIBER_BUILDERS["nemo_speech"] = _build_nemo_speech
        │                         (pipeline.py:250)
        ▼
NemoSpeechTranscriber (new)  ──► NemoSpeechLibrary Protocol  ──► CtypesNemoSpeechLibrary
  name/supports_streaming=True        (ctypes seam, testable)        (libnemo_speech_asr_c)
  ensure_model → model_store.ensure_nemo_model + nemo_engine.ensure_engine
  load         → nemo_speech_asr_create (recognizer resident)
  transcribe   → nemo_speech_asr_recognize_f32   (batch)
  stream       → streaming.drive_append_only(_NemoSpeechDecoder(...))   ← §3
  close        → nemo_speech_asr_destroy
        │
        ▼
StreamingDictationSession (unchanged) ──► Service (unchanged) ──► CommittedTyper (unchanged)
```

### 7.1 What changes, and why

| file | change | why |
|---|---|---|
| `identifiers.py` | add `NEMO_SPEECH_ENGINE = "nemo_speech"` | rule 1: one definition per identifier (`identifiers.py` header). The engine name is compared in the factory, the config, and `doctor`. |
| `config.py` | add it to `KNOWN_ENGINES` (`config.py:80`); **not** to `BATCH_ONLY_ENGINES` (`:100`) | the engine streams, so an omitted `streaming.enabled` must default to `True` (ticket 50's engine-aware default). **Owned by ticket 58** (§8). |
| `transcribers/nemo_speech_config.py` (new) | validate `[engine.nemo_speech]`: `device` (`auto`/`vulkan`/`cpu`), `chunk_seconds` (default 0.16), model override | the block is engine-specific (ADR 0009); a `window_seconds` key gets the existing unknown-key warning, which *"says so rather than silently accepting a value that has no effect"* (ADR 0022 consequence). |
| `transcribers/nemo_speech.py` (new) | the adapter + `NemoSpeechLibrary` Protocol + ctypes impl + `_NemoSpeechDecoder` | the only module allowed to import the backend (ADR 0010); the ctypes-Protocol seam is the ADR 0021 pattern (`whisper_cpp.py:223`). |
| `nemo_engine.py` (new) | engine **archive** pin (sha256 + size), extraction, `engine_dir()`, `verify_engine()`, device detection; **no build** | mirrors `whisper_engine.py` so `setup`/`doctor`/service share one definition of the engine (the module docstring's stated reason). |
| `model_store.py` | add a Nemo model pin (`sha256 a5c435f2…`, size `741,548,352`, revision `1c8deae…`) + `nemo_model_path()`/`ensure_nemo_model()` | acquisition is one module (ADR 0017); reuse `_ensure`/`verify` (`model_store.py:702`/`:611`). |
| `pipeline.py` | `_build_nemo_speech` + register in `_TRANSCRIBER_BUILDERS`; **generalise `_streaming_chunk_seconds`** (`:430`); dispatch artifact presence for the engine | the factory is the one place concrete engines are chosen (ADR 0010). `_streaming_chunk_seconds` currently hardcodes `WhistleConfig` (`:440`) — it is the one existing coupling. |
| `doctor.py` | `check_nemo_engine`, `check_nemo_model`, `check_nemo_device` (Vulkan/CPU), gated by the selected engine; register the config validator (`doctor.py:1400`) | the epic requires `doctor` to report the library, the model, and the Vulkan/GPU state; the whisper checks (`:742`) are the template. |
| `setup_command.py` / `cli.py` | a `nemo_speech` setup backend; `plan_setup`/`run_setup` acquire the archive + GGUF; CLI help | `setup` is the only network command (ADR 0017/0018) and never installs or runs `sudo`. |
| `docs/` | README engine list, a config reference, an engine doc, and the documented limits (Hungarian published-FLEURS only; CPU/Vulkan) — **documentation only** | the remaining epic acceptance criteria. The ADR-0022 sign-off criterion is **already met** (accepted 2026-10-14), so ticket 62 carries no gate. |

### 7.2 What deliberately does **not** change

- `interfaces.py` — the `Transcriber` Protocol already expresses the seam; adding `supports_streaming`
  was always enough (ADR 0010's design paying off a third time).
- `streaming.py` — except for the **additive** `drive_append_only`/`AppendOnlyDecoder`. The windowed
  `drive` is untouched.
- `streaming_session.py`, `service.py`, `socket_proto.py`, `incremental_typing.py` — the whole point:
  the committed-delta path is engine-agnostic and needs no window awareness.
- The **default engine** and both existing backends.

### 7.3 Why this architecture (the "whys")

1. **It reuses the seam that already exists.** The user's question — "integrate without two passes of a
   sliding window" — is answered by the fact that the window is *adapter-local*, not architectural. A
   third adapter is the intended extension point (ADR 0010); no new abstraction is needed.
2. **It mirrors ADR 0021 exactly.** `whisper_cpp` already established the in-process `ctypes` +
   library-Protocol + resident model + manifest + `doctor` + `setup` pattern. Following it means one
   mental model for two native engines and no new conventions.
3. **It isolates the one real refactor.** `_streaming_chunk_seconds` is the only place that assumes the
   selected streaming engine is Whistle. Generalising it (a per-engine resolver, or an optional
   `chunk_seconds` attribute read by the composition root) is small and testable, and it is the honest
   cost of a third engine.
4. **It keeps risk off the Whistle path.** The windowed driver, its tests, and the WhisperConcrete
   adapter are not touched; the new driver is a sibling function.

---

## 8. Proposed implementation ticket breakdown (DoD 6)

Each ticket is scoped for a capable junior in one session; dependencies are explicit. All are Epic 6
integration tickets created from this report.

| # | slug | scope (one line) | files | deps | size |
|---|---|---|---|---|---|
| 57 | `nemo-speech-engine-and-model-acquisition` | Pin, acquire and verify the prebuilt NeMo-Speech.cpp archive (sha256 + size) and the q8_0 GGUF (sha256/size/revision); expose them to `setup` and `doctor`. | `identifiers.py`, `nemo_engine.py` (new), `model_store.py`, `setup_command.py`, `cli.py`, tests | 56 | **M** |
| 58 | `nemo-speech-batch-adapter` | Add `NEMO_SPEECH_ENGINE` to `KNOWN_ENGINES` so the engine is **selectable by name**; `[engine.nemo_speech]` config + `NemoSpeechTranscriber` behind a `NemoSpeechLibrary` ctypes Protocol: `ensure_model`/`load`/`transcribe` (offline `recognize_f32`)/`close`. `supports_streaming = False` for now (usable batch-only). | `config.py`, `transcribers/nemo_speech_config.py` (new), `transcribers/nemo_speech.py` (new), `tests/test_nemo_speech.py` (new) | 57 | **M** |
| 59 | `nemo-speech-pure-streaming` | The append-only commit mechanism: `streaming.drive_append_only` + `AppendOnlyDecoder` + the string word-boundary rule; `NemoSpeechTranscriber.stream`; flip `supports_streaming = True`; word-boundary/pending/exactly-once tests. | `streaming.py` (additive), `transcribers/nemo_speech.py`, `tests/test_streaming_append_only.py` (new), `tests/test_nemo_speech_stream.py` (new) | 58 | **M** |
| 60 | `nemo-speech-pipeline-wiring` | The composition root only: register `_build_nemo_speech` in `_TRANSCRIBER_BUILDERS`; **generalise `_streaming_chunk_seconds`** so it is not Whistle-hardcoded; dispatch artifact-presence for the engine; end-to-end wiring test. | `pipeline.py`, tests | 57, 59 | **M** |
| 61 | `nemo-speech-doctor` | `doctor` only: `check_nemo_engine`, `check_nemo_model`, `check_nemo_device` (Vulkan/CPU), gated by the selected engine; register the config validator. | `doctor.py`, tests | 57, 58 | **S** |
| 62 | `nemo-speech-docs` | README/config/engine docs and the documented limits (Hungarian published-FLEURS only; CPU/Vulkan). | `docs/` | 60, 61 | **S** |

Notes:
- **`config.py` is ticket 58's.** `config.py:80` (`KNOWN_ENGINES`) gates engine selection
  (`if engine_value not in KNOWN_ENGINES`, `config.py:399`), so `transcriber.engine = "nemo_speech"`
  is not a valid config until the name is in that tuple. `identifiers.py` (ticket 57) defines the
  constant first; ticket 58 consumes it in `KNOWN_ENGINES` and in the new `[engine.nemo_speech]`
  block. Runtime selection through `build_transcriber` (`pipeline.py:256`) still needs ticket 60 to
  register `_build_nemo_speech` in `_TRANSCRIBER_BUILDERS` (`pipeline.py:250`), so the name is
  *accepted at 58* and *startable at 60*. **Ticket 59 and ticket 60 must ship together** for the
  correct feed granularity — 59 flips `supports_streaming = True` while 60 generalises
  `_streaming_chunk_seconds`, and until 60 lands an un-generalised path would silently run the Nemo
  stream at Whistle's 1.0 s default instead of 0.16 s.
- **No ticket modifies `interfaces.py`, `streaming_session.py`, `service.py`, `socket_proto.py`, or
  `incremental_typing.py`** — that is the design's claim of a clean bypass, asserted by ticket 60's
  wiring test rather than trusted.
- Ticket 59 is the only one that could invalidate ADR 0022; if it cannot implement
  `drive_append_only` without a growing buffer or a second pass, it must stop and escalate (§9).

---

## 9. Risks, confounds, and what would falsify (DoD 7)

### 9.1 What would falsify the central claim

The central claim is: **on this machine, the NeMo-Speech.cpp cache-aware interim stream can be carried
into ges2t's committed-delta path with no sliding window, no second pass, and no growing-buffer
re-decode.** Consistent with ADR 0022's falsifier, it would be falsified by any of:

1. **The interim is not append-only.** Ticket 55 measured `append_only_char_frac = 1.0`, 0 rewritten
   pairs, monotone commits, 0 adjacent dups across 23 speech runs, and this report **re-derived**
   that from the committed traces alone (§10). If a run showed a non-prefix interim, a ges2t-side
   de-duplication or re-decode (a second pass) would be required — escalate.
2. **The word-boundary rule cannot be computed from the interim string.** It can: interims are plain
   space-separated tokens with no leading/trailing whitespace, and the final-only word list means the
   string is the *only* available boundary source (§4.4). If a future model emitted interims that
   rewrite earlier tokens, the rule would violate `CommittedTyper`'s append-only invariant and
   `IncrementalTypingError` would fire — which is the correct, loud failure.
3. **Carrying the interim requires a growing buffer.** It does not: the adapter stores only the
   cumulative committed **string** (the same O(utterance) string the windowed path already keeps at
   `streaming.py:160`) and pushes each chunk straight to the engine. The caches that grow live inside
   the engine, not ges2t.
4. **The engine call cannot be made under the service lock.** It is not made under it: the C ABI runs
   on `ges2t-streaming-worker` (`streaming_session.py:265`), and the service lock is never held across
   collaborators (ticket 21, `service.py:475`).
5. **Interims are only obtainable by re-decoding a buffer** (e.g. the C ABI exposed interims only via
   a whole-file call). They are not: `stream_push_f32` + `stream_next` deliver them incrementally
   (header + ticket 55's harness), and 1,841 interims were recorded.

**Result: none of these fired.** The falsifier did not trigger; the pure-streaming decision stands.

### 9.2 Risks and confounds

- **Accuracy is the weak side and is the model's.** English WER vs the whisper reports' clips: jfk
  0.000, quiet-short 0.259, quiet-long 0.167, the user's car clip 0.312 (whisper `medium q5_0` scored
  0.062 with `-nt`). **Hungarian is unmeasured here** (published FLEURS `hu_hu` ≈ 28.7–33.4 only).
  Nothing in this design improves or worsens that; do not relay it as a win.
- **CPU/Vulkan transcripts are not byte-identical** (deterministic per device). Any golden-transcript
  test must be per-backend, not CPU/Vulkan-agnostic.
- **Device/Vulkan cost.** ~950 MB VRAM on Vulkan; CPU falls back to ~1 GB host RSS at roughly 2× the
  per-chunk latency. The adapter must never silently substitute (ADR 0020); `doctor` reports the
  effective backend.
- **Confounds of the evidence.** Ticket 55's numbers are from **one machine, one driver** (RADV on
  Radeon 780M), under a non-idle load (load avg ~1.4–3.3); accuracy/append-only are deterministic and
  load-insensitive, timings are not. The boundary rule here is exercised only against the **committed
  transcripts**, not a live adapter — the adapter's own boundary handling is ticket 59's test
  obligation, and this report does not claim it is measured.
- **Bounded runs.** The reproduction command in §10 was wrapped in `timeout 300`; no engine process is
  spawned by this ticket, so there is nothing to leak.
- **A known limits item from ADR 0022** that this mechanism inherits: `window_seconds` is irrelevant
  for this engine; the config layer must **warn** if it is set (the generic unknown-key path does this
  once the Nemo block does not declare it).

### 9.3 What this report does **not** establish

- It does **not** measure a live adapter; it reads the engine's ABI on disk and re-derives the
  append-only property from ticket 55's committed data.
- It does **not** claim Hungarian accuracy, CPU/Vulkan parity, or that the boundary rule is
  empirically exercised end-to-end (that is ticket 59's tests).

---

## 10. Evidence and exact commands

### 10.1 Read the installed C header (no download, no run)

```bash
E=~/.cache/ges2t-discovery/nemotron-3-5-asr-streaming-measurement/engine/nemo-speech-0.2.0-linux-x86_64-vulkan
wc -l $E/include/nemo_speech/asr.h            # 299
grep -in "callback\|blocking\|poll" $E/include/nemo_speech/asr.h   # no callback in the ASR ABI
sed -n '200,260p' $E/include/nemo_speech/asr.h # streaming API: push / force_endpoint / finish / next
```

The streaming section (header lines 213–241) is quoted in §4.1; the header is the installed v1 binary
contract (append-only structs starting with `size`).

### 10.2 Re-derive the append-only property from the **committed** traces (repo alone)

```bash
R=docs/project/research/nemotron-3-5-asr-streaming-measurement
timeout 300 env PYTHONPATH=src python3 $R/analyze_streaming.py \
  --partials $R/data/partials --out /tmp/nemo56_analysis.jsonl
# exit 0; 25 rows (23 speech + 2 silence)
```

Summarised with a small stdlib read of that output:

```
total rows=25  speech rows=23  silence rows=2
speech: distinct append_only_char_frac = [1.0]
speech: distinct rewritten_char_pairs = [0]
speech: all monotone_committed_words True = True
speech: distinct adjacent_duplicate_words = [0]
speech: final_extends_last_interim all True = True
silence labels: ['silence-10s-cpu', 'silence-10s-vulkan'] n_interims: [0, 0] final_text empty: [True, True]
```

### 10.3 Interim shape (why the boundary rule is string-based)

```bash
R=docs/project/research/nemotron-3-5-asr-streaming-measurement
python3 - "$R/data/partials" <<'PY'
import json,glob,sys
ends=starts=total=0
for p in sorted(glob.glob(sys.argv[1]+"/*.jsonl")):
    for l in open(p):
        r=json.loads(l)
        if r["kind"]!="interim": continue
        t=r["transcript"]; total+=1
        ends += t.endswith((" ","\t")); starts += t.startswith(" ")
print(f"interims={total} end_with_space={ends} start_with_space={starts}")
PY
# interims=1841 end_with_space=0 start_with_space=0

python3 - "$R/data/partials/stream-jfk-vk.jsonl" <<'PY'
import json,sys
rows=[json.loads(l) for l in open(sys.argv[1])]
print("total", len(rows), "finals", sum(r["kind"]=="final" for r in rows))
print("rows with non-empty word_times:", sum(1 for r in rows if r.get("word_times")),
      "— all on the final")
PY
# total 63 finals 1
# rows with non-empty word_times: 1 — all on the final
```

Real interim sample (`stream-jfk-vk.jsonl`, `index kind audio_processed_s transcript`):

```
  0 interim 1.28 'And'
  4 interim 1.92 'And so my'
  5 interim 2.08 'And so my fel'
  6 interim 2.24 'And so my fellow'
  7 interim 2.40 'And so my fellow Ame'
 62 final  11.0 'And so my fellow Americans ask not what your country can do for you. Ask what you can do for your country'
```

### 10.4 Call-graph claims (grep)

```bash
grep -rn "streaming.drive" src/ tests/     # one production caller: whistle.py:665
```

`streaming.drive` is referenced in `src/ges2t/transcribers/whistle.py` only; the session
(`streaming_session.py:268`) calls `transcriber.stream`, not `drive`. This is the structural fact that
makes the bypass clean.

---

## 11. Answer and recommendation

**Answer.** The 5 s window and the two-pass agreement rule do not need to be defeated with a flag or a
rewrite, because they are not in the shared architecture: they are in `ges2t.streaming.drive`
(`streaming.py:95`), whose only production caller is the Whistle adapter (`whistle.py:665`). The
session (`streaming_session.py:268`) and the service talk to the `Transcriber.stream` Protocol and are
window-blind. A `nemo_speech` adapter therefore bypasses the window by returning its own generator from
`stream`; it commits the NeMo-Speech interim at the last **string** word boundary (interims carry no
word timestamps until the final), holds the trailing token as `pending`, and feeds the cumulative
`committed` string to the existing `CommittedTyper`, which types it exactly once and applies the single
trailing space at `finish`. No growing buffer (only the committed string), no second pass. The
falsifier in ADR 0022 did not fire.

**Recommendation.**

1. Implement the commit fold as a new, additive `streaming.drive_append_only` + `AppendOnlyDecoder`
   Protocol (§3.3, §6-A), reusing `_group_chunks`, `StreamingPass`, and the tickets 32–34 typing path
   unchanged.
2. Build the adapter over a `NemoSpeechLibrary` ctypes Protocol (the ADR 0021 pattern), with the
   recognizer resident from `load` and a fresh stream handle per recording, endpointing off,
   `is_final` handling at end-of-input only.
3. Wire it with the new engine identifier, a `[engine.nemo_speech]` block, `nemo_engine.py` +
   `model_store` pins, `doctor` checks, and `setup nemo_speech` — and generalise the one hard coupling,
   `_streaming_chunk_seconds` (`pipeline.py:430`).
4. Do it as tickets 57–62 (§8), with ticket 59 as the only one that can invalidate ADR 0022.
5. **ADR 0022 is accepted (user sign-off 2026-10-14)** — no further sign-off is required; ticket 62
   is **documentation only**. **ADR 0011 stays untouched.**

---

## 12. Addendum — R1 resolved (ticket 60, the wiring ticket)

**The divergence this report left open is now decided, from a measurement.** Ticket 59's review (R1)
recorded that the offline decode (`recognize_f32`, library-default punctuation **off**) returns
lowercased/unpunctuated text while `stream()` (punctuation **on**) returns capitalised/punctuated text
for the **same audio**. Ticket 60 measured both paths on the ticket-55 reference clips (same audio,
0.16 s feed, ticket 32's `word_diff`) through the real adapter (`measure_r1.py`).

| device | Σ WER offline | Σ WER offline+punc | Σ WER stream→final | mean Δ(stream−offline) |
|---|---:|---:|---:|---:|
| Vulkan (the machine default) | 0.7385 | 0.7385 | **0.7385** | **+0.0000** |
| CPU | 0.7251 | 0.7251 | 0.7541 | +0.0073 |

On the default device the two decodes are **WER-identical** on all four clips; on CPU the difference is
bidirectional and tiny (stream better on two clips, worse on one). **Aligning** the punctuation flag
leaves the word-level divergence untouched, so alignment cannot make the two paths agree — only one
decode path can. Ticket 60 therefore **unified** them: `NemoSpeechTranscriber.transcribe()` now drains
`stream()` and returns its end-of-stream final, and the offline `recognize_f32` path was removed. The
batch and streaming sessions now share a single text-producing path; `transcribe()` returns
capitalised/punctuated text. The numbers and the raw per-clip transcripts are in
`../../nemotron-streaming-backend/nemo-speech-pipeline-wiring/001-comment.md`.
