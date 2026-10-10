# whisper-transcriber-adapter — implementation notes

- **Branch:** `feat/whisper-transcriber-adapter`
- **Worktree:** `.worktrees/whisper-transcriber-adapter`
- **Date:** 2026-10-10
- **Ticket:** `docs/project/whisper-cpp-backend/whisper-transcriber-adapter/ticket.md`

This is the in-process `ctypes` shape over `libwhisper.so` (ADR 0021). It is the
whisper analogue of the Whistle adapter's `libneedle3.so` use: the model is
loaded **once** by `WhisperCppTranscriber.load()` and kept resident. There is no
subprocess and no per-utterance reload.

## What changed

| file | change |
|---|---|
| `src/ges2t/transcribers/whisper_cpp.py` | **new** — `WhisperCppTranscriber`, the `ctypes` bindings (`CtypesWhisperLibrary`), the `WhisperLibrary` Protocol seam, the ctypes struct mirror of the pinned ABI, the pinned-decode helper, the typed errors, the device rule |
| `src/ges2t/transcribers/whisper_cpp_config.py` | **new** — `[engine.whisper_cpp]` block: `model`, `language`, `threads`, `gpu`; validated, frozen config + warnings |
| `src/ges2t/pipeline.py` | wired `_build_whisper_cpp` into `_TRANSCRIBER_BUILDERS`; `_missing_artifacts` defers the whisper backend to the adapter's `load()`; `_startup` already calls `transcriber.load()` once, so the whisper model loads at startup |
| `tests/test_whisper_cpp.py` | **new** — fake-library Protocol tests, device rule, pinned decode, pipeline wiring, one real-seam test |
| `tests/test_whisper_cpp_config.py` | **new** — the config block matrix |
| `tests/test_pipeline.py` | the "unknown engine" stand-in renamed from `whisper_cpp` (now handled) to `whispercpp` |

No `src/ges2t/` file outside the transcriber adapter and the one pipeline
factory changed. No behaviour of the default `needle`/Whistle path changed.

## The exact `ctypes` calls

The loaded library is `libwhisper.so`; every symbol used is bound with explicit
`argtypes`/`restype`, because ctypes' default `int` would truncate the 64-bit
context pointer and misread the by-value structs:

```python
lib.whisper_version.restype = ctypes.c_char_p
lib.whisper_context_default_params.restype = WhisperContextParams
lib.whisper_init_from_file_with_params.restype = ctypes.c_void_p
lib.whisper_init_from_file_with_params.argtypes = [ctypes.c_char_p, WhisperContextParams]
lib.whisper_free.argtypes = [ctypes.c_void_p]
lib.whisper_full_default_params.restype = WhisperFullParams
lib.whisper_full_default_params.argtypes = [ctypes.c_int]
lib.whisper_full.restype = ctypes.c_int
lib.whisper_full.argtypes = [ctypes.c_void_p, WhisperFullParams,
                             ctypes.POINTER(ctypes.c_float), ctypes.c_int]
lib.whisper_full_n_segments.argtypes = [ctypes.c_void_p]
lib.whisper_full_get_segment_text.argtypes = [ctypes.c_void_p, ctypes.c_int]
lib.whisper_full_lang_id.argtypes = [ctypes.c_void_p]
lib.whisper_lang_str.argtypes = [ctypes.c_int]
```

`WhisperCppTranscriber.load()` then:

```python
cparams = lib.whisper_context_default_params()      # by value
cparams.use_gpu = (device is Device.VULKAN)
handle = lib.whisper_init_from_file_with_params(os.fsencode(str(model_path)), cparams)
# NULL -> WhisperCppLoadError (model missing/corrupt)
```

`WhisperCppTranscriber.transcribe()` -> `CtypesWhisperLibrary.decode()`:

```python
params = lib.whisper_full_default_params(int(SamplingStrategy.GREEDY))  # by value
apply_decode_options(params, options)                                   # the pinned decode
params.language = language.encode("utf-8") if language else None        # borrowed pointer
raw = array("f", samples)                                               # 16 kHz mono float32, no resample
buffer = (ctypes.c_float * len(raw)).from_buffer(raw)
status = lib.whisper_full(ctypes.c_void_p(handle), params, buffer, len(raw))
text = "".join(lib.whisper_full_get_segment_text(handle, i).decode()
               for i in range(lib.whisper_full_n_segments(handle)))
lang = lib.whisper_lang_str(lib.whisper_full_lang_id(handle)).decode()
```

`close()` -> `lib.whisper_free(ctypes.c_void_p(handle))`.

### The struct layout is pinned to the exact ABI

`whisper_full_params` is passed **by value**, so its exact layout matters. The
ctypes `Structure` mirrors `include/whisper.h` at the pinned revision
(`v1.9.5`, commit `d1be6fde11ac6e0407606b4e42fe72d34add8037`). It was checked
against the real header with a C `sizeof`/`offsetof` program compiled against
that header:

```
$ g++ -I<ref>/include -I<ref>/ggml/include sz.c -o sz && ./sz
sizeof(whisper_full_params)=304
sizeof(whisper_context_params)=48
off no_timestamps=22 best_of=144 beam_size=148 language=104 n_threads=4 strategy=0 translate=20 vad_params=280
```

and against the ctypes classes:

```
sizeof 304
no_timestamps 22   best_of 144   beam_size 148   language 104
n_threads 4   strategy 0   translate 20   vad_params 280
```

Every offset and both sizes match byte-for-byte. The adapter was also run against
the real library (see the real-seam test): it loads `tiny-q5_0` and transcribes
the cached clip to the expected text, which would not happen with a mis-sized
struct.

## The pinned decode (ADR 0021 §5, the `-nt` addendum)

`-nt` (`no_timestamps`) is a **decode** switch: on the same clip the same model
scored **0.0625 with `-nt`** and **0.1562 without** it. It is pinned in code, in
one place (`apply_decode_options`), never inherited from a
`whisper_full_default_params` default:

| field | value | why |
|---|---|---|
| `params.no_timestamps` | `True` (`NO_TIMESTAMPS`) | the accurate decode; ges2t types text, needs no timestamps |
| `params.strategy` | `WHISPER_SAMPLING_GREEDY` (0) | greedy was measured no worse and faster |
| `params.greedy.best_of` | `1` (`BEST_OF`) | greedy |
| `params.beam_search.beam_size` | `1` (`BEAM_SIZE`) | set too, so the two knobs cannot drift |
| `params.n_threads` | configured (default: this machine's cores) | configurable |
| `params.language` | `NULL` (auto) or the configured string | configurable |
| `params.translate` / `print_*` | `False` | text out, no printing |

The pinned values are asserted on a bare `WhisperFullParams` struct
(`test_apply_decode_options_pins_no_timestamps_and_greedy`) and on the options the
adapter hands the library (`test_transcribe_passes_the_pinned_decode_to_the_library`),
so a dropped line fails a test (see the falsification section).

## The device rule (ADR 0021 §2/§7, ADR 0020)

`WhisperCppTranscriber.resolve_device()`:

- `gpu = "cpu"` -> **CPU**.
- `gpu = "vulkan"` -> **Vulkan**, or a typed `WhisperCppLoadError` if the
  installed engine is CPU-only. A user who asked for the GPU is never silently
  moved to the CPU (ADR 0020).
- `gpu = "auto"` -> **Vulkan exactly when the installed engine advertises a
  Vulkan build**, else **CPU**.

"The engine advertises a Vulkan device" is read from the installed engine's
`engine.json` manifest via `whisper_engine.verify_build()` (`variant == vulkan`);
the engine build itself only chose Vulkan when the Vulkan runtime and dev headers
were present (ticket 42). A missing/incomplete install reads back as CPU.

The choice is recorded on `WhisperCppTranscriber.device` and logged
(`_logger.info("whisper.cpp loaded: device=… threads=… model=… in …s")`), so it is
observable and never a silent switch.

## Engine timings (ADR 0011's situation, re-stated for whisper)

whisper.cpp exposes neither `ttft_ms` nor `decode_tps`, so
`TranscriptionResult.ttft_ms` / `decode_tps` are **`None`** — nothing is
fabricated. ges2t's own clock supplies the wall time instead, recorded on
`WhisperCppTranscriber.last_transcribe_seconds` and the one-time
`load_seconds`. This mirrors exactly how ADR 0011 records the Whistle *streaming*
path having to measure its own timing.

## The `AudioBuffer` contract

16 kHz mono float32 in `[-1, 1]` **is** whisper.cpp's input contract, so no
resampling happens. A non-16 kHz buffer raises `WhisperCppInputError` rather than
being resampled silently. There is **no** number/spelling normaliser (ADR 0021
forbids it) and no per-call duration cap: `whisper_full` splits long audio at
30 s internally, and slicing it ourselves is the accuracy loss the streaming
research measured.

## Framework overhead — measured, with method

ADR 0001 holds the framework's own (non-model) work to **< 1 s per dictation**.
The measurement is a real in-process run on the 25 s reference recording
(`ges2t-commit-cadence-capture-20261009-195914.wav`, 400 698 samples =
25.044 s @ 16 kHz mono), separated into the two non-model components:

1. **The adapter/pipeline path** — `WhisperCppTranscriber.transcribe()` and the
   full `BatchDictationSession.finish()` round trip, with the model call stubbed
   by the test `FakeWhisperLibrary` (its `decode` does no work), 20 warm-up runs
   then 500 measured runs.
2. **The ctypes input marshalling** — `array("f", samples)` plus
   `(c_float * n).from_buffer(...)` for the same 400 698 samples, 20 warm-up + 500
   measured runs. This is real ges2t-added cost (it lives inside
   `CtypesWhisperLibrary.decode`, alongside the model).

Method script: an ad-hoc `bench_whisper2.py` run under `uv run python` from the
worktree (deleted afterwards; not committed). `time.perf_counter()`, single
process, no other load. Result:

```
audio: 400698 samples = 25.044 s @16kHz mono
adapter.transcribe, model stubbed (fake lib): median 3.483 ms  p90 4.648  p99 6.606  max 7.174
BatchDictationSession.finish, model stubbed:   median 3.260 ms  p90 4.490  p99 5.589  max 6.205
ctypes marshalling list->array('f')->buffer: median 5.001 ms  p90 5.398  p99 7.074  max 8.564
adapter.transcribe, TINY real model (25 s clip): median 690.198 ms  [0.028x real time]
one-time load_context tiny-q5_0: 41 ms
one-time load_context medium-q5_0: 113 ms
```

**Reported number.** The non-model framework overhead of one whisper dictation is
**~3.5 ms** (adapter path, median) **+ ~5.0 ms** (ctypes marshalling of 25 s of
float32) **≈ 8.5 ms** — about **120× under** ADR 0001's 1 s budget. The model time
(here ~0.7 s for `tiny-q5_0` on CPU) is explicitly outside that budget and is
noisy on CPU (the research measured up to 2.6× spread); the **non-model** number
is stable and is what this ticket owns.

**Resident-model benefit.** `load()` runs once, at service start (`_startup` calls
`transcriber.load()` exactly once; `load()` is idempotent). The one-time in-process
`whisper_init_from_file_with_params` measured **41 ms** for `tiny-q5_0` and
**113 ms** for `medium-q5_0` on this machine. Because the model stays resident,
**no utterance pays that again** — the per-utterance cost is the ~8.5 ms above
plus the model decode. The subprocess shape ADR 0021 rejected would pay a process
spawn plus a full model reload (~0.8 s fixed, per the provider research) on
*every* utterance; the in-process shape removes both. `test_load_is_idempotent_and_the_context_stays_resident`
proves the second `transcribe()` reuses the same context handle with no second
`load_context` call.

## Falsification — the wiring is really tested

**Drop the `-nt` path.** Setting the adapter's decode option to
`no_timestamps=False` instead of the pinned `NO_TIMESTAMPS`:

```
$ uv run pytest -q tests/test_whisper_cpp.py -k "pinned_decode or pins_no_timestamps"
.F                                                                       [100%]
E       assert False is True
E        +  where False = WhisperDecodeOptions(language=None, threads=16, no_timestamps=False, ...).no_timestamps
FAILED tests/test_whisper_cpp.py::test_transcribe_passes_the_pinned_decode_to_the_library
1 failed, 1 passed, 34 deselected
```

**Drop the model path.** Making `_model_path()` ignore `model_path_override`:

```
$ uv run pytest -q tests/test_whisper_cpp.py -k "load_uses_the_model_override or startup_builds"
FAILED tests/test_whisper_cpp.py::test_load_uses_the_model_override - ...
FAILED tests/test_whisper_cpp.py::test_startup_builds_and_loads_the_whisper_transcriber_once
2 failed, 34 deselected
```

Both edits were reverted immediately (`git diff --stat` empty afterwards).

**The real seam.** `test_real_libwhisper_transcribes_cached_audio` drives the real
`libwhisper.so` from the research scratch cache (`build/bin/libwhisper.so`,
`models/ggml-tiny-q5_0.bin`, `audio/clip5.wav`) and asserts the reference words.
It **skips cleanly** (never silently passes) when any artifact is absent — the
installed engine directory does not exist on a machine without `cmake`:

```
$ uv run pytest -v -rs tests/test_whisper_cpp.py -k real
tests/test_whisper_cpp.py::test_real_libwhisper_transcribes_cached_audio PASSED [ 50%]
tests/test_whisper_cpp.py::test_real_libwhisper_reports_its_version PASSED [100%]
2 passed, 34 deselected
```

Artifacts can be pointed elsewhere with `GES2T_WHISPER_LIBRARY`,
`GES2T_WHISPER_MODEL`, `GES2T_WHISPER_AUDIO`.

## Verification

```
$ uv run pytest -q
1077 passed in 7.67s

$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 61 source files
```

Baseline on this branch's merge-base was **1018 passed, 0 mypy errors** (the
prompt's "923" is stale); this ticket adds **59** tests and keeps mypy green. No
test was deleted, skipped or weakened; the one edited test
(`test_build_transcriber_raises_for_an_unknown_engine`) only changed its
stand-in name because `whisper_cpp` is now a *handled* engine.

## Scope boundaries and hand-offs

- **Selection is not wired into the config layer here.** `KNOWN_ENGINES` in
  `config.py` still lists only `whistle`, so `transcriber.engine = "whisper_cpp"`
  is not yet accepted by `load_config`. Wiring the builder into
  `_TRANSCRIBER_BUILDERS` makes the composition root ready; accepting the value,
  the GPU/CPU **default-model rule**, and the strict startup rejections
  (streaming on, model not acquired, library not built) are ticket 45's DoD. This
  adapter's `load()` already fails these as a typed, fatal
  `WhisperCppLoadError`, and `_missing_artifacts()` defers the whisper backend to
  it rather than reporting whistle artifacts.
- **No idle unload.** The model is resident for the service lifetime; `close()`
  frees the context with `whisper_free` and is idempotent, but nothing calls it
  during normal running (the user accepted the resident memory; ADR 0021). A
  smaller model is the lever.
- **No silence gate** on the whisper batch path. The streaming research measured
  silence hallucination in *partial-window* decoding; a batch gate is a UX
  decision no ticket asked for, so none was added. `BatchDictationSession` still
  returns `""` for zero captured samples.
- **`stream()` refuses** with `WhisperCppStreamingUnsupportedError`;
  `supports_streaming = False` is the contract callers branch on (ADR 0010).
