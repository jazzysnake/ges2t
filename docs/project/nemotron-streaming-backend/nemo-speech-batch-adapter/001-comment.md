# 001 — implementer comment (ticket 58, NeMo-Speech batch adapter)

Branch: `feat/nemo-speech-batch-adapter` · worktree `.worktrees/nemo-speech-batch-adapter`

## What this ticket is

Make `nemo_speech` a selectable engine with a working **batch** path, built on ticket 57's
acquisition (`NEMO_SPEECH_ENGINE`, `nemo_engine.py`, `model_store.ensure_nemo_model`/`nemo_model_path`).
`supports_streaming = False` for this ticket; ticket 59 flips it. No composition-root change (ticket 60),
no `doctor` change (ticket 61).

## Plan

1. `config.py`: `NEMO_SPEECH_ENGINE` → `KNOWN_ENGINES` (not `BATCH_ONLY_ENGINES`).
2. `transcribers/nemo_speech_config.py` (new): `[engine.nemo_speech]` (`device`, `chunk_seconds`,
   `model`); `window_seconds` falls through to the existing unknown-key warning.
3. `transcribers/nemo_speech.py` (new): `NemoSpeechLibrary` Protocol + `CtypesNemoSpeechLibrary` +
   `NemoSpeechTranscriber` (`ensure_model`/`load`/`transcribe`/`stream` refuses/`close`).
4. `tests/test_nemo_speech.py` (new): hermetic, fake-library driven + a real-seam version check.

## C-ABI confirmation (real header, no download, no run)

Header read: `~/.cache/ges2t-discovery/nemotron-3-5-asr-streaming-measurement/engine/nemo-speech-0.2.0-linux-x86_64-vulkan/include/nemo_speech/asr.h`
(299 lines). The symbols the ticket names all exist, with these signatures:

```c
nemo_speech_asr_status nemo_speech_asr_create(const nemo_speech_asr_recognizer_config* cfg,
                                              nemo_speech_asr_recognizer** out);
void nemo_speech_asr_destroy(nemo_speech_asr_recognizer* recognizer);
nemo_speech_asr_status nemo_speech_asr_recognize_f32(nemo_speech_asr_recognizer* recognizer,
        const nemo_speech_asr_recognition_options* options, const float* samples,
        size_t n_samples, int32_t sample_rate, nemo_speech_asr_result** out);
const char* nemo_speech_asr_last_error(void);
```

Plus the accessors the batch path needs: `nemo_speech_asr_recognition_options_default`,
`nemo_speech_asr_result_transcript(result, alt)`, `nemo_speech_asr_result_language_count` /
`nemo_speech_asr_result_language_code(result, alt, i)`, `nemo_speech_asr_result_destroy`,
`nemo_speech_asr_version`.

`backend.gpu` comment: `-1 = CPU`; the ticket-55 harness used `0` for Vulkan. No callback in the
ASR ABI (confirmed in ticket 56 §4.1) — batch uses the same polled/push shape.

<!-- appended incrementally as work proceeds -->

## Implemented

Files touched (exactly the ticket's scope):

| file | change |
|---|---|
| `src/ges2t/config.py` | imported/exported `NEMO_SPEECH_ENGINE`; added it to `KNOWN_ENGINES`; amended the `BATCH_ONLY_ENGINES` docstring (see below). The engine is **not** in `BATCH_ONLY_ENGINES`. |
| `src/ges2t/transcribers/nemo_speech_config.py` (new) | validates `[engine.nemo_speech]`: `device` (`auto`/`vulkan`/`cpu`, default `auto`), `chunk_seconds` (default `0.16`, must be > 0 and finite), `model` (an optional `.gguf` path override). `window_seconds` is **not** a key, so it gets the ordinary `engine.nemo_speech: unknown key 'window_seconds'` warning. |
| `src/ges2t/transcribers/nemo_speech.py` (new) | `NemoSpeechLibrary` Protocol + `CtypesNemoSpeechLibrary` (the only module that loads `libnemo_speech_asr_c.so`) + `NemoSpeechTranscriber` (`name = NEMO_SPEECH_ENGINE`, `supports_streaming = False`, `ensure_model`/`load`/`transcribe`/`stream` refuses/`close`). |
| `tests/test_nemo_speech.py` (new) | hermetic fake-library contract + config-block tests + a real-seam version check (skips loudly when the `.so` is absent). |
| `tests/test_whisper_backend_config.py` | two engine-selector guards updated for the third engine (see "The one guard that had to change"). |

### The `model` key is a **path**, not a catalogue name (a decision worth stating)

The ticket/REPORT say the block owns "a model override" without fixing its meaning. This engine has
**exactly one** pinned model and **no catalogue** (`model_store` has `NEMO_MODEL_NAME` and no
`parse_nemo_model_name`), so a whisper-style *name* key (`model = "medium-q5_0"`) would be degenerate —
it could only ever be one value. I implemented `model` as a **path to a `.gguf`**, which is the only
override with an effect (it mirrors the adapter's `model_path_override` seam). It is validated for the
pinned `.gguf` suffix (derived from `model_store.NEMO_MODEL_FILE_NAME`, not spelled twice), so a
whisper-style name given to this key fails at config load with a clear message rather than being read
as a relative path. **This is the one place I had to choose an interpretation; a reviewer who wanted a
name key should say so and I will change it.**

### Device selection is never silent (ADR 0020)

`resolve_device()`: `cpu` → CPU; `vulkan` with no Vulkan loader → `NemoSpeechLoadError` (never a
silent fallback); `auto` → Vulkan iff `nemo_engine.inspect_device().vulkan_available`, else CPU. The
chosen device is recorded on `NemoSpeechTranscriber.device` and logged at `load()`. Device detection is
lazy (first `resolve_device()`), so import and construction probe nothing.

### `supports_streaming = False` + `KNOWN_ENGINES` (why the guard test changed)

Ticket 58 deliberately puts `nemo_speech` in `KNOWN_ENGINES` but **not** in `BATCH_ONLY_ENGINES`, while
`supports_streaming` is `False` until ticket 59. The old guard
`test_batch_only_engines_agree_with_the_transcriber_capability` asserted the two sets were *equal*, so
it had to change. It was **not weakened**: it still covers every engine in `KNOWN_ENGINES`, still
asserts `BATCH_ONLY_ENGINES == {engines whose declared capability is False}` for every engine, and now
names `nemo_speech` as the single, explicit transitional exception (and asserts it is the exception).
A future batch-only engine still has to be added to `BATCH_ONLY_ENGINES` or the guard fails. The
`BATCH_ONLY_ENGINES` docstring was amended to state the same meaning ("batch-only by nature").

Runtime impact today: `_select_session_kind` checks `supports_streaming` **first**, so even with an
omitted `[streaming]` (→ `enabled = True`) a `nemo_speech` transcriber would pick the **batch** session
— no refusal. The engine is not startable until ticket 60 registers the builder, so there is no
runtime path to that today.

### The startup-validation bullet

The adapter's `load()` is the startup gate at this layer: a missing model or a missing engine library
raises `NemoSpeechLoadError` naming the exact path and the `ges2t setup nemo_speech` fix. Wiring that
into the pipeline's pre-flight (the whisper `_whisper_missing_artifacts` analogue) is the composition
root, which this ticket's Notes reserve for ticket 60 — so it is deliberately **not** here.

## Real-seam evidence (`recognize_f32` against the real library)

The scratch engine tree is present on this machine, so the binding was driven for real (CPU; no
download, no `sudo`, no network):

```
$ python3 -c '... CDLL(libnemo_speech_asr_c.so); version(); recognition_options_default() ...'
version: nemo-speech-asr 0.2.0
lib default size: 72 our sizeof: 72          # the ctypes RecognitionOptions mirrors the header
```

```
$ GES2T_NEMO_REAL_SEAM=1 uv run pytest \
    tests/test_nemo_speech.py::test_real_library_recognizes_cached_audio \
    tests/test_nemo_speech.py::test_real_library_rejects_a_missing_model -q
2 passed in 2.31s
```

`test_real_library_recognizes_cached_audio` creates the recognizer on the pinned GGUF and runs
`recognize_f32` on `silence-10s.wav`; the transcript is `""`, matching ticket 55's silence result. The
full decode is **opt-in** (`GES2T_NEMO_REAL_SEAM=1`) so plain `uv run pytest` stays offline and does
not load ~1 GB of weights; the cheap `version()` check runs whenever the `.so` is present.

## Verify commands (real output, run in the worktree)

```
$ uv run pytest -q
1340 passed, 2 skipped in 9.21s

$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 70 source files

$ python3 - <<'PY'  (the §10 docs link check)
0 broken references

$ git status --short
(clean)
```

The main branch collects 1294 tests; this branch adds 48 (parametrised), and every main test still
passes.

## Falsification (each mutation made, watched to fail, reverted via `git checkout --`)

| mutation | test that failed |
|---|---|
| `resolve_device` `vulkan` silently falls back to CPU | `test_resolve_device_vulkan_preference_needs_a_vulkan_loader` |
| `from_mapping` drops the unknown-key warnings | `test_window_seconds_produces_the_existing_unknown_key_warning`, `test_an_unknown_key_is_warned_and_ignored` |
| `load()` drops the model-presence check | `test_load_fails_when_the_model_is_missing` |
| `_validate_buffer` no-op | `test_transcribe_rejects_a_non_16khz_buffer` |
| `stream()` stops refusing | `test_stream_refuses_loudly` |
| `chunk_seconds <= 0` guard off | `test_chunk_seconds_rejects_zero_and_negative` |
| `.gguf` suffix guard off | `test_a_non_gguf_model_value_is_rejected` |

`git status` was clean after every revert (the source files were committed before falsifying, so each
mutated file could be restored with `git checkout --`).
