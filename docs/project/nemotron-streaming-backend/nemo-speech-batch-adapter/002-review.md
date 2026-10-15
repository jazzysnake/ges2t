# 002 — review (ticket 58, NeMo-Speech batch adapter)

Independent review. Branch `feat/nemo-speech-batch-adapter`, worktree
`.worktrees/nemo-speech-batch-adapter`, base diffed with **three dots**
(`git diff main...feat/nemo-speech-batch-adapter`), tip `f163ade`. Reviewer did not write the code
and did not modify it; the only write is this artifact.

## Verdict

**changes requested.**

The shipped code is correct and every Definition-of-done item is met and verified against the real
C ABI. But the hermetic fake-driven suite does **not** pin the seam's central data contract: a
mutation that drops the audio payload before the library call (`buffer.samples` → `[]`) survives the
**whole** `tests/test_nemo_speech.py` *and* the opt-in real-seam decode test. That is the
"a check that cannot fail" class the review bar treats as worse than no check — it reads as
verification of the float32 seam and is not (finding **F1**). A second, smaller claim overstates what
its assertion proves (**F2**). Both are cheap to fix; the implementation itself is not in question.

## Per-criterion findings (Definition of done)

### DoD 1 — `config.py`: `NEMO_SPEECH_ENGINE` in `KNOWN_ENGINES`, not in `BATCH_ONLY_ENGINES` — **PASS**

Ran the real config resolution (not the test):

```
NEMO_SPEECH_ENGINE = 'nemo_speech'
in KNOWN_ENGINES: True
KNOWN_ENGINES: ('whistle', 'whisper_cpp', 'nemo_speech')
in BATCH_ONLY_ENGINES: False
BATCH_ONLY_ENGINES: frozenset({'whisper_cpp'})
omitted streaming -> engine: nemo_speech enabled: True
explicit true -> enabled: True warnings: []
explicit false -> enabled: False
```

The gate at `config.py:399` (`if engine_value not in KNOWN_ENGINES`) now accepts `"nemo_speech"`;
an omitted `streaming.enabled` resolves to `True` (engine-aware default, ticket 50); an explicit
`true` is not rejected. The `NEMO_SPEECH_ENGINE` re-export was added to `config.__all__`, matching
`WHISPER_CPP_ENGINE`.

### DoD 2 — `nemo_speech_config.py` validates `device`/`chunk_seconds`/`model`; `window_seconds` warns — **PASS**

Direct run (not tests):

```
window_seconds warnings: ["engine.nemo_speech: unknown key 'window_seconds'"] config: NemoSpeechConfig(device=<NemoDevicePreference.AUTO: 'auto'>, chunk_seconds=0.16, model=None)
defaults: NemoSpeechConfig(device=<NemoDevicePreference.AUTO: 'auto'>, chunk_seconds=0.16, model=None) warnings: []
rejected {'device': 'gpu'} -> engine.nemo_speech.device = 'gpu' is invalid: valid values are auto, cpu, vulkan
rejected {'chunk_seconds': 0} -> engine.nemo_speech.chunk_seconds = 0.0 is invalid: chunk_seconds must be greater than 0
rejected {'chunk_seconds': -0.1} -> engine.nemo_speech.chunk_seconds = -0.1 is invalid: ... greater than 0
rejected {'chunk_seconds': nan} -> ... is invalid: expected a finite number of seconds
rejected {'model': 'medium-q5_0'} -> ... is invalid: expected a path to a .gguf model file ...
```

Default `0.16`; `window_seconds` gets the **one** unknown-key wording (`_unknown_key_warning`, the
single source of that sentence — rule 3). Two guards re-falsified (see Evidence, mutations C/D).

### DoD 3 — `nemo_speech.py`: Protocol + ctypes impl + `NemoSpeechTranscriber` methods — **PASS**

`NemoSpeechTranscriber` implements `ensure_model` (delegates to `model_store.ensure_nemo_model`,
bypassed when the caller owns the file), `load` (creates the recognizer once, resident, idempotent),
`transcribe` (`recognize_f32`), `stream` (refuses), `close` (destroys, idempotent, re-loadable). All
confirmed by the fake suite and by the real decode.

### DoD 4 — adapter is the only module touching the native library; `name`/`supports_streaming` — **PASS**

```
$ grep -rn "libnemo_speech_asr_c" src/          # only nemo_speech.py
$ grep -rn "ctypes.CDLL" src/                    # nemo_speech.py:325, whisper_cpp.py:421, keymap.py (unrelated)
```

`name = NEMO_SPEECH_ENGINE` (from `identifiers`, no bare literal — `grep '"nemo_speech"' src/` finds
no comparison); `supports_streaming = False`; `stream()` raises
`NemoSpeechStreamingUnsupportedError` (falsified, mutation D). No static test enforces the
"only module" rule, but the grep confirms it and the composition root is untouched.

### DoD 5 — selection starts+transcribes a batch utterance; startup validation rejects missing model/library — **PASS (adapter layer; end-to-end deferred by the ticket's own Notes)**

The ticket Notes reserve the composition root and its pre-flight for ticket 60, so there is no
`_build_nemo_speech` to select at runtime yet. At the adapter layer the gate is `load()`:
`test_load_fails_when_the_model_is_missing` and `test_load_fails_when_the_library_is_missing` exist and
the model check was falsified (mutation E). The real round-trip is exercised end-to-end from
`load()`→`transcribe()`→`recognize_f32` on the real library (Evidence, real seam). Reproducing a
*pipeline-level* "select nemo_speech and start" is not possible on this branch and is not this
ticket's scope.

### DoD 6 — device selection never silent (ADR 0020) — **PASS**

`cpu` → CPU; `vulkan` with no loader → `NemoSpeechLoadError` (never a fallback); `auto` → Vulkan iff a
loader is present. The chosen device is recorded on `.device` and logged at `load()`. Falsified:
making `vulkan` fall back silently fails `test_resolve_device_vulkan_preference_needs_a_vulkan_loader`
(mutation A).

### DoD 7 — `tests/test_nemo_speech.py` hermetic, fake-driven, fails if the fake is simpler — **CHANGES REQUESTED**

Hermetic (tmp_path models, injected device records, injected library; the real-seam tests skip cleanly
when artifacts are absent). `FakeNemoSpeechLibrary` models a fresh handle per create, refuses a freed
handle, and makes create/recognize failures injectable — not simpler than the real lifecycle. The
16 kHz contract is pinned (`test_transcribe_rejects_a_non_16khz_buffer` fails when `_validate_buffer`
is a no-op).

**But the seam's audio payload is not pinned (F1).** See below. The fake *records*
`(handle, tuple(samples), sample_rate)` yet **no assertion ever reads the recorded samples for a
non-empty buffer**:

```
$ grep -n "recognized" tests/test_nemo_speech.py
403:    assert [handle for handle, _, _ in fake.recognized] == [1, 1]  # same recognizer
501:    assert fake.recognized[0][2] == SAMPLE_RATE             # only the rate
525:    assert fake.recognized == []                             # only the empty case
```

### DoD 8 — pytest green, mypy 0, docs link 0, README row updated — **PASS**

```
$ timeout 600 uv run pytest -q
1340 passed, 2 skipped in 8.35s
$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 70 source files
$ python3 <the §10 docs link check>
0 broken references
$ git status --short
(clean)
```

README ticket-58 row flipped to `in review`. The implementer's quoted counts (1340/2/70) trace
exactly to runs I reproduced.

### DoD 9 — no `numpy`/`torch`/`onnxruntime` — **PASS**

```
$ grep -rn "import numpy\|import torch\|onnxruntime" src/ tests/   -> none
$ grep -rn "numpy\|torch\|onnxruntime" pyproject.toml              -> none
```

The ABI path uses only `ctypes` + `array("f")`, verified against the real library (struct
`options.size == 72 == ctypes.sizeof`).

## Additional checks (not DoD items)

- **Import-time laziness** — `ctypes.CDLL` is called only in `CtypesNemoSpeechLibrary.__init__`.
  Mechanically confirmed: shimming `ctypes.CDLL` to raise, then importing `nemo_speech` and
  `nemo_speech_config`, succeeds (`imported both modules with CDLL shimmed to raise -> no import-time
  library load`).
- **Unknown-key warning wording** — one function (`_unknown_key_warning`); grep shows one sentence.
- **Rule 1 identifiers** — `grep -n '"nemo_speech"' src/` (excluding `NEMO_SPEECH_ENGINE`) → none;
  `GGUF_SUFFIX` derived from `model_store.NEMO_MODEL_FILE_NAME`, not spelled twice.
- **Boundaries** — `git diff main...HEAD --name-only` touches only `config.py`, the two new
  `transcribers/nemo_speech*.py`, the two test files, README and the ticket folder. Composition root,
  `pipeline.py`, `doctor.py`, `identifiers.py` untouched.
- **`model` = `.gguf` path (judgment call)** — the report §7.1 and the ticket both say only "model
  override"; the report never fixes the meaning. This engine has exactly one pinned model
  (`model_store.NEMO_MODEL_NAME`, no `parse_nemo_model_name`), unlike whisper.cpp's catalogue name, so
  a name key would be degenerate. Reading it as a path mirrors the adapter's `model_path_override`
  seam and is documented in the module docstring, the config docstring, and `001-comment.md`, with the
  deviation from whisper.cpp's `model`-as-name stated explicitly. **Reasonable and documented**; a
  reader who wanted a name key should say so in review — I do not.

## Findings

### F1 — BLOCKING: the float32 audio payload is never asserted; dropping it survives the whole suite

Mutation: `NemoSpeechTranscriber.transcribe` calls
`self._library.recognize(handle, buffer.samples, ...)`; changed `buffer.samples` → `[]` (all audio
dropped). Result:

```
$ timeout 300 uv run pytest tests/test_nemo_speech.py -q     # with payload mutation
.............................................ss.                         [100%]
46 passed, 2 skipped in 0.32s

$ GES2T_NEMO_REAL_SEAM=1 timeout 600 uv run pytest \
    tests/test_nemo_speech.py::test_real_library_recognizes_cached_audio -q     # with payload mutation
.                                                                        [100%]
1 passed in 0.15s
```

So neither the hermetic fake tests nor the real-seam decode can detect audio that never reaches the
library. (The real-seam test passes in 0.15 s rather than 2.34 s because the mutation short-circuits
`CtypesNemoSpeechLibrary.recognize` on `len(samples) == 0`, so `recognize_f32` is not even called.)
The fake records the samples but nothing reads them; a silent total-audio-loss bug ships green.

Fix (either is enough): assert the recorded payload in a fake test, e.g.
`assert fake.recognized[0][1] == tuple(_buffer([...]).samples)` and that its length equals the
buffer length; and/or make the real-seam test use a speech clip asserting a non-empty transcript
(whisper.cpp's `test_real_libwhisper_transcribes_cached_audio` does exactly this, so the silence-only
clip here is weaker than the pattern it claims to mirror).

### F2 — non-blocking: a test comment overstates what its assertion proves

`test_real_library_rejects_a_missing_model` comments *"The library's own last_error is surfaced, not
a generic message"* but only asserts the static prefix:

```
assert "nemo_speech_asr_create failed" in str(excinfo.value)
```

Mutation: replaced `{self.last_error()}` in the create failure message with a literal — the test still
passes (`1 passed in 0.16s`). The `last_error()` surfacing the ticket's Notes require is therefore
implemented but **not pinned by any test**, and this test is additionally opt-in
(`GES2T_NEMO_REAL_SEAM=1`), so by default it never runs. Fix: assert that a non-empty library message
is present (e.g. that the text after the prefix is non-empty), or drop the claim from the comment.

## Evidence — commands and real output

All commands run in `.worktrees/nemo-speech-batch-adapter`. Every source mutation was restored with
`git checkout`/`cp` from a backup and `.mypy_cache`/`__pycache__` removed after each probe;
`git diff --stat` was empty after every revert.

### Real C-ABI confirmation (read-only header; no download, no `sudo`)

Header actually present at
`~/.cache/ges2t-discovery/nemotron-3-5-asr-streaming-measurement/engine/nemo-speech-0.2.0-linux-x86_64-vulkan/include/nemo_speech/asr.h`
(the task/ticket-style path `engine/include/...` does **not** exist; the implementer's `001-comment.md`
names the correct nested path). Confirmed symbol lines:

```
201:nemo_speech_asr_create( ...
204:nemo_speech_asr_destroy( ...
209:nemo_speech_asr_recognize_f32( ...
197:nemo_speech_asr_recognition_options_default(void);
262:nemo_speech_asr_result_transcript( ...
282:nemo_speech_asr_result_language_count( ...
283:nemo_speech_asr_result_language_code( ...
286:nemo_speech_asr_result_destroy( ...
292:nemo_speech_asr_last_error(void);
293:nemo_speech_asr_version(void);
```

Struct-layout cross-check against the real library (not by reading):

```
version: nemo-speech-asr 0.2.0
library options.size: 72  our ctypes sizeof: 72
recognizer_config sizeof: 80
backend sizeof: 16 model sizeof: 24
```

`create`'s `nemo_speech_asr_status` is `c_int`, `out` `nemo_speech_asr_recognizer**`; the header's
"`gpu` `-1 = CPU`" comment backs `GPU_CPU = -1`; recognizer config fields are `size` + backend/model +
7 `c_void_p` subsystem pointers, matching the header order.

### Real seam (real `.so`, real 741 MB GGUF, CPU; no network)

Default suite behaviour:

```
$ uv run pytest tests/test_nemo_speech.py -v -k real
tests/test_nemo_speech.py::test_real_library_reports_its_version PASSED  [ 33%]
tests/test_nemo_speech.py::test_real_library_recognizes_cached_audio SKIPPED [ 66%]
tests/test_nemo_speech.py::test_real_library_rejects_a_missing_model SKIPPED [100%]
================= 1 passed, 2 skipped, 45 deselected in 0.06s ==================
```

The cheap `version()` check drives the real library and runs; the heavy decode and the
missing-model check skip cleanly (loud `pytest.skip` reason) without the env var. Opt-in decode run:

```
$ GES2T_NEMO_REAL_SEAM=1 uv run pytest \
    tests/test_nemo_speech.py::test_real_library_recognizes_cached_audio \
    tests/test_nemo_speech.py::test_real_library_rejects_a_missing_model -q
2 passed in 2.34s
```

The silence clip yields `result.text == ""`, matching `001-comment.md`'s claimed ticket-55 result and
the implementer's quoted `2 passed in 2.31s`.

### Falsifications (each mutated, watched to fail, reverted)

| # | mutation | result |
|---|---|---|
| A | `resolve_device` `vulkan` silently falls back to CPU | `test_resolve_device_vulkan_preference_needs_a_vulkan_loader` — `DID NOT RAISE NemoSpeechLoadError` |
| B | `from_mapping` drops unknown-key warnings | `test_window_seconds_produces_the_existing_unknown_key_warning`, `test_an_unknown_key_is_warned_and_ignored` — 2 failed |
| C | `_read_device` accepts anything silently | `test_every_device_spelling_is_accepted`, `test_an_unknown_device_names_the_valid_values` — 2 failed |
| D | `_read_chunk_seconds` `<= 0` guard removed | `test_chunk_seconds_rejects_zero_and_negative` — 1 failed |
| E | `load()` drops the model-presence check | `test_load_fails_when_the_model_is_missing` — `DID NOT RAISE NemoSpeechLoadError` |
| F | `stream()` no longer refuses | `test_stream_refuses_loudly` — `DID NOT RAISE NemoSpeechStreamingUnsupportedError` |
| — | **payload dropped** (`buffer.samples` → `[]`) | **survived — 46 passed, 2 skipped; real-seam 1 passed** (F1) |
| — | `last_error()` dropped from the create message | **survived — real-seam missing-model test 1 passed** (F2) |

### Guard-test change (`tests/test_whisper_backend_config.py`) — inspected hardest, **not weakened**

Baseline: `1 passed in 0.15s`. Mutation A (add a *new* engine to `KNOWN_ENGINES` only, not to
`BATCH_ONLY_ENGINES`):

```
>       assert set(declared) == set(config_module.KNOWN_ENGINES)
E       AssertionError: ...
E         Extra items in the right set:
E         'neweng'
tests/test_whisper_backend_config.py:371: AssertionError
1 failed in 0.11s
```

The finer case — a future **batch-only** engine added to the test's `declared` but not to
`BATCH_ONLY_ENGINES` — still fails the shipped set comprehension (simulated exactly):

```
declared: {'whistle': True, 'whisper_cpp': False, 'nemo_speech': False, 'neweng': False}
expected BATCH_ONLY (guard): {'whisper_cpp', 'neweng'}
actual BATCH_ONLY_ENGINES : {'whisper_cpp'}
guard assertion passes? -> False
future streaming engine passes guard? -> True
```

The exclusion is exactly `engine != NEMO_SPEECH_ENGINE`, plus two assertions
(`NEMO_SPEECH_ENGINE not in BATCH_ONLY_ENGINES`, `declared[NEMO_SPEECH_ENGINE] is False`) that pin the
transitional exception. The guard remains live for every other engine — the change was not a quiet
weakening.

## Could not verify

- **A pipeline-level "select `nemo_speech` and start"** (`_build_nemo_speech` in the composition
  root): the builder is deliberately ticket 60, so there is no runtime selection path on this branch.
  Verified only at the adapter boundary; this does not falsify the DoD wording, which the ticket's
  own Notes scope to ticket 60.
- **A real Vulkan decode.** I did not drive the engine in Vulkan mode (would require forcing a GPU
  path I cannot safely assert on this machine); the device rule was verified with injected
  `NemoDevice` records and the falsification above, not by a real GPU run.
- **That ticket 55's harness itself printed `""` for this clip.** I reran the *adapter* on the same
  silence WAV and reproduced `""`, but did not re-run ticket 55's harness; `001-comment.md`'s
  "matching ticket 55's silence result" is consistent with, but not independently reproduced from,
  the ticket-55 artifact.
- **The exact byte-size/`sha256` of the GGUF** — out of scope for this ticket (ticket 57 owns the
  pin); the file exists at the expected path and loads.

## Risks / follow-up

- **F1** is the one to fix before merge. Without it, the seam's most important invariant (audio
  actually reaches the decoder) has no failing test.
- **F2** should be fixed or reworded; a comment that claims proof it does not provide is a doc that
  lies about its own evidence.
- Weak-but-harmless: `test_load_records_the_load_time`'s `load_seconds >= 0.0` is tautological given
  `time.monotonic() - started` (the meaningful part is the `is not None`). Not worth a change on its
  own.
- No new ticket is required for the code; the follow-ups are test assertions in this branch.

## Safe to relay to the user?

**Yes** for the findings themselves — every claim above traces to a command whose real output is
quoted. **Not yet safe to merge or to present as "done"**: the verdict is `changes requested` because
of F1. The DoD checkboxes were ticked correctly for what they measure, but the ticked
"hermetic fake-driven test" item hides an unpinned seam payload.

## Commit hygiene

Every probe was reverted; `git diff --stat` and `git status --short` were empty after each revert and
before this artifact was written. No `src/` change from probing is present; no process was spawned
without a timeout; the worktree is left clean except for this review file.
