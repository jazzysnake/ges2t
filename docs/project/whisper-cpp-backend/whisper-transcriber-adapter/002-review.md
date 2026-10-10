# 002 — review: whisper.cpp transcriber adapter (in-process)

- **Ticket:** `docs/project/whisper-cpp-backend/whisper-transcriber-adapter/ticket.md`
- **Branch:** `feat/whisper-transcriber-adapter` (worktree `.worktrees/whisper-transcriber-adapter`, tip `84b9a72`)
- **Reviewer:** ges2t reviewer (independent; implementation not touched)
- **Date:** 2026-10-10
- **Diff read as:** `git diff main...feat/whisper-transcriber-adapter` (merge-base `1f05adb`)

## Verdict: **pass**

Every definition-of-done item holds. The load-bearing claim — the `ctypes` mirror of the pinned
v1.9.5 ABI — was **independently re-derived against the real header** and matches byte-for-byte. The
pinned decode, the device rule, the real seam and the overhead were all exercised mechanically, and
each new load-bearing guard was falsified and reverted. No `src/` file was left modified
(`git diff --stat` empty after every probe).

---

## Checks run (real output)

```
$ uv run pytest -q
1077 passed in 5.76s          # re-run after all probes: 1077 passed in 9.84s (exit 0)

$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 61 source files
```

`[tool.mypy]` has `disallow_untyped_defs = true`, no `files`/`exclude` scope, and the command names
`tests` explicitly — the scope does **not** exclude tests (the ADR 0019 trap).

Baseline test count verified at the merge-base's `main` tip (`27370b6`, a docs-only "plan" commit):
`1018 passed`. The branch adds **59** tests (37 in `test_whisper_cpp.py` + 22 in
`test_whisper_cpp_config.py`), matching the comment's "+59" claim.

---

## Per-criterion findings

### 1. `WhisperCppTranscriber` implements the `Transcriber` Protocol — **holds**
`name = "whisper_cpp"`, `supports_streaming = False`, and all six Protocol members exist;
`tests/test_whisper_cpp.py::test_transcriber_satisfies_the_protocol_and_declares_batch_only` and the
existing `Transcriber` protocol tests pass. Concrete class appears only in its own module, the
composition root (`pipeline._build_whisper_cpp`) and the test that tests it — ADR 0019.

### 2. Model loaded once, at startup; idempotent `load()`/`close()`; typed fatal error — **holds**
`pipeline._startup` calls `transcriber.load()` exactly once (line 655), and
`test_startup_builds_and_loads_the_whisper_transcriber_once` asserts `len(fake.loaded) == 1`.
`test_load_is_idempotent_and_the_context_stays_resident` proves one `load_context` and two decodes on
the **same handle**. `test_close_releases_the_context_and_is_idempotent`,
`test_close_then_load_reloads` pass. `WhisperCppLoadError` is raised by `load()` for a missing model,
a missing library and a NULL `whisper_init_from_file_with_params`; `run_service`'s generic
`except Exception` prints it and returns `_EXIT_FAILURE`
(`test_a_whisper_load_failure_is_fatal_with_a_clear_message` passes).

Resident benefit verified mechanically in the overhead probe: **1** `load_context` call across
**1040** decodes.

### 3. Decode pinned (`no_timestamps`, greedy, language/threads configurable) — **holds, and reaches the real library**
- Falsified: setting `NO_TIMESTAMPS: bool = False` fails
  `test_apply_decode_options_pins_no_timestamps_and_greedy`,
  `test_the_pin_constants_are_the_measured_configuration` and
  `test_transcribe_passes_the_pinned_decode_to_the_library` (3 failed).
- Falsified: `BEST_OF: int = 2` fails the same three (3 failed).
- **Direct proof it reaches `whisper_full`** (not just the fake): I replaced
  `CtypesWhisperLibrary._lib.whisper_full` with a recorder around the real CDLL function. The real
  library received `no_timestamps=True, greedy.best_of=1, beam_search.beam_size=1, strategy=0,
  n_threads=4, language=b'en'`:
  ```
  patched whisper_full: True
  captured params (no_ts, best_of, beam, strategy, n_threads, lang): [(True, 1, 1, 0, 4, b'en')]
  ```
- **Accuracy claim reproduced through the adapter's own ctypes path** (`ggml-medium-q5_0.bin`, the
  25.044 s reference capture, greedy):
  ```
  no_timestamps=True : word_diff wer=0.0625  sub=4 ins=0 del=0
  no_timestamps=False: word_diff wer=0.15625 sub=8 ins=0 del=2
  ```
  This matches the addendum's `0.0625 (4s/0i/0d)` vs `0.1562 (8s/0i/2d)` exactly, so the comment's
  quoted numbers trace to a real run. (Note: the comment's sentence is model-agnostic; the number is
  the `medium-q5_0` result the ADR attributes it to. On `tiny` this clip is unchanged by `-nt`.)

### 4. GPU/CPU device selection, never a silent switch, observable — **holds**
Exercised each branch of `resolve_device()`:
```
auto/vulkan-engine -> vulkan
auto/cpu-engine    -> cpu
auto/missing       -> cpu
cpu/vulkan-engine  -> cpu           # explicit cpu wins
vulkan/vulkan-engine -> vulkan
vulkan/cpu-engine  -> WhisperCppLoadError: engine.whisper_cpp.gpu = 'vulkan' but the installed … CPU-only
vulkan/missing     -> WhisperCppLoadError
```
`use_gpu` maps into the **real** `whisper_context_params` — intercepting
`whisper_init_from_file_with_params` captured `cparams.use_gpu == [False, True]` for
`load_context(use_gpu=False/True)`. Falsified: turning the `vulkan`-preference guard into a silent
fallback (`if False:`) fails
`test_resolve_device_vulkan_preference_needs_a_vulkan_engine` with `DID NOT RAISE`. The choice is
recorded on `Transcriber.device` and logged (`test_load_records_the_chosen_device`), and
`test_load_records_the_chosen_device` asserts `use_gpu=True` reached the library.

### 5. Engine timings `None` / ges2t's own clock, documented — **holds**
`transcribe()` returns `ttft_ms=None, decode_tps=None` (`test_transcribe_returns_text_and_omits_engine_timings`)
and records `last_transcribe_seconds` / `load_seconds`. The module docstring and `001-comment.md`
explain this as ADR 0011's streaming-path situation. whisper.cpp exposes neither field.

### 6. Framework overhead measured and reported as a number — **holds**
Reproduced the model-stubbed framework path on the 25.044 s capture (20 warm-up + 500 measured):
```
audio: 400698 samples = 25.044 s @16kHz mono
adapter.transcribe, model stubbed (fake lib): median 3.200 ms  p90 4.780  p99 5.905
BatchDictationSession.finish, model stubbed:   median 2.694 ms  p90 4.508  p99 5.910
ctypes marshalling list->array('f')->buffer:  median 5.033 ms  p90 6.113  p99 7.013
model-stub transcribe made 1 load_context calls; 1040 decode calls
```
Total ≈ **8.2 ms** vs the comment's ≈ **8.5 ms** — same number, and ≈**120×** under ADR 0001's 1 s.
The resident benefit is real and mechanical: one `load_context`, 1040 decodes on the same handle.

### 7. Tests: fake drives the contract, one real-seam test, falsifiable — **holds**
The real-seam tests **passed** (not skipped) in this environment:
```
tests/test_whisper_cpp.py::test_real_libwhisper_transcribes_cached_audio PASSED
tests/test_whisper_cpp.py::test_real_libwhisper_reports_its_version PASSED
2 passed, 35 deselected in 1.29s
```
They **skip cleanly** when an artifact is absent — simulated by pointing the env override at a missing
path:
```
GES2T_WHISPER_LIBRARY=/tmp/abi_probe/does-not-exist/libwhisper.so uv run pytest -v -rs -k real
… SKIPPED … the whisper.cpp real-seam artifacts are absent …: /tmp/…/libwhisper.so
2 skipped, 35 deselected
```
The real-seam test constructs `WhisperCppTranscriber` with only `library_path_override` (no injected
`library`), so `load()` really builds `CtypesWhisperLibrary` over the scratch `libwhisper.so` — the
seam wraps a real child library, not a simpler fake (enforcement point 6). Falsified the model-path
wiring (`_model_path` ignoring the override): `test_load_uses_the_model_override`,
`test_startup_builds_and_loads_the_whisper_transcriber_once` **and the real-seam test** all failed
(3 failed), then reverted.

### 8. `pytest` + `mypy` green, counts reported — **holds**
1077 passed / 0 errors in 61 files (above).

### 9. `001-comment.md` records ctypes calls, device rule, pinned decode, overhead — **holds**
All four present. The `g++ sizeof`/`offsetof` transcript is accurate (see below), and the overhead
method (script name, run counts, `perf_counter`) and device rule are stated.

---

## The central claim: the ABI mirror (independently re-derived)

The pinned header is genuine **v1.9.5**:
```
$ git -C ~/.cache/ges2t-discovery/whisper-cpp-provider/whisper.cpp describe --tags
v1.9.5        (HEAD d1be6fd "whisper : bump version to 1.9.5 (#4102)")
```
I compiled my **own** `sizeof`/`offsetof` C program against that header (independent of the
implementer's) and compared **every** field of the ctypes classes against it:
```
sizeof(whisper_full_params)=304   alignof=8
sizeof(whisper_context_params)=48 sizeof(whisper_vad_params)=24 sizeof(whisper_aheads)=16
off n_threads=4  off no_timestamps=22  off language=104  off greedy.best_of=144
off beam_search.beam_size=148  off strategy=0  off translate=20  off vad_params=280
…
=== MISMATCHES ===
NONE — every field offset and both struct sizes match
```
ctypes side: `sizeof(WhisperFullParams)=304`, `sizeof(WhisperContextParams)=48`,
`sizeof(WhisperVadParams)=24`, `sizeof(_WhisperAheads)=16`. **Every offset of every declared field
matches the real header, and both load-bearing struct sizes match.** A wrong offset would corrupt
decoding silently; it does not. The central claim holds.

**What would falsify it (and did not):** any C `offsetof`/`sizeof` differing from the ctypes class;
the interception showing the wrong pinned values reaching `whisper_full`; a real-seam decode that
crashes or produces garbage. All three checks passed.

---

## Scope and honesty

- `supports_streaming = False`; `stream()` raises `WhisperCppStreamingUnsupportedError`
  (`test_stream_refuses_loudly`). The session factory chooses the **batch** session whenever the
  capability flag is false (`pipeline._streaming_is_selected`), so a `whisper_cpp` +
  `streaming.enabled=true` config degrades to batch rather than reaching `stream()`.
- **No number/spelling normaliser and no silent resampling**: grep of both new modules finds only
  prose mentions; a non-16 kHz buffer raises `WhisperCppInputError`
  (`test_transcribe_rejects_a_non_16khz_buffer`).
- **Config vocabulary matches the project style**: the block is `engine.whisper_cpp` alongside the
  existing `engine.whistle`, both fed through the generic layer's uninterpreted `[engine.*]` mapping
  and validated by an engine-local `from_mapping(...) -> Result` (ADR 0009). The name matches the
  `Transcriber.name`/`backend` value the epic uses.
- **Ticket 45's work is correctly *not* implemented here**:
  `config.KNOWN_ENGINES` is still `("whistle",)` (unchanged in this diff), so
  `load_config` does not yet accept `whisper_cpp`; the device-dependent **default-model rule** and the
  strict startup rejections are absent/deferred (the adapter names a CPU-safe `small-q8_0` default and
  defers selection to ticket 45 — see its own docstring and the comment's hand-off section).
  `pipeline._missing_artifacts` early-returns `[]` for a non-whistle engine, deferring the whisper
  artifacts to the adapter's typed `load()` error. This is the correct boundary and does not regress
  the whistle path (all pre-existing `_missing_artifacts` tests use the default whistle engine and
  still pass).
- **No binary or model committed**: `git ls-tree -r feat/whisper-transcriber-adapter` is clean of
  `.bin/.so/.ggml/.gguf/.wav/.onnx/.pt`; the diff adds only `.py`/`.md`.
- **ADR 0019 style**: full annotations; `WhisperLibrary` Protocol at the seam; frozen dataclasses
  (`WhisperDecodeOptions`, `WhisperTranscript`) instead of tuples; the one `Any` usage is
  `Mapping[str, Any]` at the raw-TOML boundary; the one `# type: ignore[misc]` is a narrow frozen-
  assignment in a test.
- **No import side effects** (ADR 0010): monkeypatching `ctypes.CDLL` before
  `import ges2t.transcribers.whisper_cpp` records zero calls — the native library is opened only in
  `load()`.
- Ticket `Status: in review`; `docs/project/README.md` row updated to `in review` with the branch.

## Hygiene

`git diff --stat` empty and `git status --short` empty after every mutation probe; `.mypy_cache`/
`__pycache__` removed after each mutation; probe scripts under `/tmp/abi_probe` deleted; no long-lived
process, socket or file left behind. No microphone, clipboard, keystroke, window, notification,
`sudo`, package install, service change or network was used. All child runs were bounded with
`timeout`; no run exceeded its bound and pytest exited `0` (no `124`).

---

## Risks / follow-ups

1. **(Minor, non-blocking) "never a silent switch" is only as good as the manifest.**
   `resolve_device()` reads the installed engine's `engine.json` (`verify_build`), not the live
   runtime. A Vulkan-built engine whose Vulkan runtime later disappears could let whisper.cpp fall
   back to the CPU internally with `use_gpu=True`, which ges2t would not detect. This is the build's
   and `whisper-doctor`'s (ticket 46) territory, not a defect in this adapter. Worth a doctor check
   that the Vulkan runtime is present when the manifest says Vulkan.
2. **No model-vs-GPU sizing check** — deliberate (ADR 0021 §8); a `large` model on CPU will just be
   slow. Recorded, not a defect.
3. **Comment wording nit:** the `001-comment.md` statement "on the same clip the same model scored
   0.0625 with `-nt` and 0.1562 without it" does not name `medium-q5_0`. It is accurate for
   `medium-q5_0` (reproduced), but the model should be named so the number cannot be misread as
   applying to every model (on `tiny` this clip is unchanged). Doc-only; no code change requested.

## Could not verify

- **Exact concurrency-sensitive model timings**: the comment's `adapter.transcribe, TINY real model
  (25 s) median 690.198 ms` and `load_context 41 ms (tiny) / 113 ms (medium)` were **not**
  reproduced at face value. My measurements under concurrent orchestrator load were
  `tiny load 73–91 ms`, `medium load 214–225 ms`, `tiny 25 s decode 1160–2272 ms` — roughly **2×**
  the comment's values. The **framework** numbers (this ticket's own claim) reproduced within ~10%
  (`3.07–3.20` vs `3.483`; `2.69` vs `3.260`; `5.03` vs `5.001` ms). The difference is consistent with
  CPU contention (other agents were running the suite concurrently, and `main` advanced mid-review),
  and the comment itself flags model time as CPU-noisy with "up to 2.6× spread". The load-once /
  resident conclusion is unaffected and was verified mechanically. **The exact ms are not a claim I
  can confirm; the qualitative claim (one-time, never per-utterance) I can.**
- I did not re-run the full 59-test suite under `coverage` or prove that every individual one of the
  59 tests is non-vacuous. I falsified the load-bearing guards (pinned decode ×2, device fallback,
  model path, config bool guard) and inspected the rest; the remainder are straightforward assertions
  on typed values.

## Safe to relay to the user?

**Yes.** The headline this ticket carries — "the ctypes struct layout matches the pinned v1.9.5
header byte-for-byte, `no_timestamps`/greedy reach the real `whisper_full`, the model loads once and
stays resident, and the non-model round trip is ~8 ms, far under the 1 s budget" — is verified by
independent instruments (a C `offsetof`/`sizeof` program against the real header, live interception of
the real `whisper_full`, and a real `libwhisper.so` decode that reproduced the addendum's accuracy
numbers). The only caveat to carry along is item 1 (manifest-vs-runtime Vulkan) and the
silent-normaliser/selection boundaries that belong to tickets 45/46.
