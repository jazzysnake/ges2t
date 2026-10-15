# 004 — re-review (ticket 58, NeMo-Speech batch adapter) — addressing F1/F2

Fresh independent re-review of the fix at **`bd66596`** (parent `938dc57`, the review commit that
carried `002-review.md`). Branch `feat/nemo-speech-batch-adapter`, worktree
`.worktrees/nemo-speech-batch-adapter`. Base diffed with **three dots**
(`git diff main...feat/nemo-speech-batch-adapter`). The reviewer did not write the code under review
and did not modify it; the only write is this artifact. Every probe was reverted and checked
byte-identical before the next one.

## Verdict

**pass.**

`002-review.md` raised two findings: **F1** (blocking — the forwarded float32 payload was unpinned;
dropping it survived the whole hermetic suite *and* the opt-in real-seam decode) and **F2**
(non-blocking — a test comment claimed the library's own `last_error` was surfaced while only a static
prefix was asserted). The fix commit `bd66596` changes **only** `tests/test_nemo_speech.py` and adds
`003-comment.md`; **no `src/` byte changed** (`git diff 938dc57 bd66596 --name-only -- src/` is empty).
I re-falsified both: the new hermetic test fails when the payload is dropped, the real-seam decode
fails when the payload is dropped, and the F2 assertion fails when `{self.last_error()}` is removed
from the message. Full suite, `mypy`, the docs link check and `git status` are clean. Both findings
are genuinely closed.

## Scope of the fix (verified, not trusted)

```
$ git diff --stat 938dc57 bd66596
 .../nemo-speech-batch-adapter/003-comment.md       | 131 ++++++++++++++++++++
 tests/test_nemo_speech.py                          |  60 ++++++++--
 2 files changed, 183 insertions(+), 8 deletions(-)

$ git diff 938dc57 bd66596 --name-only -- src/      # (empty output)
$ git diff f163ade bd66596  --name-only -- src/     # (empty output)
$ git diff 938dc57 bd66596 --name-only -- tests/test_whisper_backend_config.py   # (empty output)
```

So the fix is **test-only**: the adapter, config, validators, `KNOWN_ENGINES`/`BATCH_ONLY_ENGINES`
and the whisper-backend guard test are byte-identical to what `002-review.md` passed.

## Per-criterion findings (Definition of done)

The fix touches only the test file, so DoD items 1–6, 8, 9 are unchanged from `002-review.md`
(which passed them). I re-ran the load-bearing parts of each on the fixed tip rather than relying on
the previous verdict.

### DoD 1 — `NEMO_SPEECH_ENGINE` in `KNOWN_ENGINES`, not in `BATCH_ONLY_ENGINES` — **PASS**

```
NEMO_SPEECH_ENGINE = 'nemo_speech'
in KNOWN_ENGINES: True | ('whistle', 'whisper_cpp', 'nemo_speech')
in BATCH_ONLY_ENGINES: False | frozenset({'whisper_cpp'})
omitted        -> engine: nemo_speech | enabled: True  | warnings: []
explicit_true  -> engine: nemo_speech | enabled: True  | warnings: []
explicit_false -> engine: nemo_speech | enabled: False | warnings: []
```

An omitted `streaming.enabled` resolves to `True`, an explicit `true` is not rejected, and the engine
is not in `BATCH_ONLY_ENGINES`.

### DoD 2 — `nemo_speech_config.py` validates `device`/`chunk_seconds`/`model`; `window_seconds` warns — **PASS**

```
window_seconds warnings: ["engine.nemo_speech: unknown key 'window_seconds'"]
defaults: NemoSpeechConfig(device=<NemoDevicePreference.AUTO: 'auto'>, chunk_seconds=0.16, model=None)
rejected {'device': 'gpu'} -> engine.nemo_speech.device = 'gpu' is invalid: valid values are auto, cpu, vulkan
rejected {'chunk_seconds': 0} -> ... chunk_seconds must be greater than 0
rejected {'chunk_seconds': -0.1} -> ... chunk_seconds must be greater than 0
rejected {'chunk_seconds': nan} -> ... expected a finite number of seconds
rejected {'model': 'medium-q5_0'} -> ... expected a path to a .gguf model file (the managed model is used when the key is omitted)
```

### DoD 3 — Protocol + ctypes impl + `NemoSpeechTranscriber` methods — **PASS** (unchanged; real round-trip re-run below)

### DoD 4 — adapter is the only module touching the native library; `name`/`supports_streaming` — **PASS**

`ctypes.CDLL` for the ASR library is called only in `nemo_speech.py:325`; the other `ctypes.CDLL` sites
are the unrelated whisper.cpp and xkb keymap modules. `nemo_engine.py` names the file
(`ENTRY_LIBRARY_NAME = "libnemo_speech_asr_c.so"`) but does not load it — see the residual note below.
`name` comes from `NEMO_SPEECH_ENGINE`; the only `"nemo_speech"` literal in `src/` is its definition
in `identifiers.py`.

### DoD 5 — selection starts+transcribes; startup validation rejects missing model/library — **PASS (adapter layer)**

Unchanged; the composition root is ticket 60 by the ticket's own Notes. The real `load()`→
`transcribe()`→`recognize_f32` round-trip is exercised below (real transcript, not silence).

### DoD 6 — device selection never silent (ADR 0020) — **PASS**

`test_resolve_device_vulkan_preference_needs_a_vulkan_loader` re-run green on `bd66596`; the source is
byte-identical to the version `002-review.md` falsified (mutation A).

### DoD 7 — `tests/test_nemo_speech.py` hermetic, fake-driven, fails if the fake is simpler — **PASS (was the blocking gap)**

This is the item the fix repaired. F1's two halves are both now real and both falsified:

* **Hermetic.** New `test_transcribe_forwards_the_actual_audio_payload` asserts the payload the fake
  recorded: `forwarded == tuple(samples)`, `len(forwarded) == len(samples)`, non-empty (`assert
  forwarded`), plus the transcript. Mutation `buffer.samples → []` **fails** it (evidence below).
* **Real seam.** `test_real_library_recognizes_cached_audio` now defaults to the **speech** clip
  `clip5.wav` and asserts a **word** (`"dinner"`) is in the lowercased transcript, instead of the
  silence clip's `""`. This mirrors `tests/test_whisper_cpp.py:696-698`
  (`_REAL_AUDIO = .../audio/clip5.wav`, `assert "dinner" in text`). The same payload mutation makes it
  fail (empty transcript), so it can no longer pass with audio that never reached the decoder.

The heavy real-seam tests stay **skipped by default** (loud `pytest.skip` reason); only the cheap
`version()` check runs when the `.so` is present.

### DoD 8 — pytest green, mypy 0, docs link 0, README row updated — **PASS**

```
$ timeout 600 uv run pytest -q
1341 passed, 2 skipped in 7.27s

$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 70 source files

$ python3 <the §10 docs link check>
0 broken references

$ git status --short      # (empty)
```

Suite moved 1340 → 1341 (one new hermetic test), matching `003-comment.md`. README row 58 is
`in review` (`docs/project/README.md:163`); `ticket.md` status is `in review`.

### DoD 9 — no `numpy`/`torch`/`onnxruntime` — **PASS** (unchanged; the fix adds no import)

## Findings

**F1 — CLOSED.** The float32 payload is now pinned in both the hermetic and real-seam tests, and each
was re-falsified to fail under the `buffer.samples → []` mutation.

**F2 — CLOSED.** `test_real_library_rejects_a_missing_model` now reads the library's own
`last_error()` back and requires it to appear in the raised message
(`assert surfaced and surfaced in message`), replacing the comment that overclaimed. Re-falsified: the
assertion fails when `{self.last_error()}` is dropped from the create-failure message.

No new blocking findings.

## Evidence — commands and real output

All commands run in `.worktrees/nemo-speech-batch-adapter`. After every probe: `git checkout --` the
mutated file, `rm -rf .mypy_cache __pycache__`, `cmp` against a `/tmp` backup, and `git diff --stat`
(empty) before the next probe.

### Baseline (tip `bd66596`, clean tree)

```
$ timeout 600 uv run pytest -q
1341 passed, 2 skipped in 9.07s
$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 70 source files
$ python3 <§10 link check>
0 broken references
```

### F1 hermetic — re-falsified (`buffer.samples` → `[]` in `transcribe`)

Mutation applied with a byte-checked Python `str.replace` on
`src/ges2t/transcribers/nemo_speech.py` (`handle, buffer.samples, sample_rate=buffer.sample_rate` →
`handle, [], sample_rate=buffer.sample_rate`):

```
$ uv run pytest "tests/test_nemo_speech.py::test_transcribe_forwards_the_actual_audio_payload" -q
        _, forwarded, _ = fake.recognized[0]
>       assert forwarded == tuple(samples)
E       assert () == (0.0, 0.25, -..., -1.0, 0.125)
E         Right contains 6 more items, first extra item: 0.0
tests/test_nemo_speech.py:515: AssertionError
1 failed in 0.07s

$ uv run pytest tests/test_nemo_speech.py -q         # same mutation, whole file
1 failed, 46 passed, 2 skipped in 0.31s
```

Revert verified byte-identical:

```
$ git checkout -- src/ges2t/transcribers/nemo_speech.py && cmp /tmp/nemo_speech.py.bak src/ges2t/transcribers/nemo_speech.py
cmp: identical
$ sha256sum src/ges2t/transcribers/nemo_speech.py
319582d66a45ffe60a8444f6f8b8d669374ba05da71e48bbd3cfc51362231e83  src/ges2t/transcribers/nemo_speech.py
$ git diff --stat      # (empty)
```

### F1 real seam — skipped by default, and re-falsified under the same mutation

```
$ uv run pytest tests/test_nemo_speech.py -v -k real          # no env var
tests/test_nemo_speech.py::test_real_library_reports_its_version PASSED  [ 33%]
tests/test_nemo_speech.py::test_real_library_recognizes_cached_audio SKIPPED [ 66%]
tests/test_nemo_speech.py::test_real_library_rejects_a_missing_model SKIPPED [100%]
================= 1 passed, 2 skipped, 46 deselected in 0.06s ==================
```

Real decode, opt-in, CPU, already-downloaded scratch engine+model+clip — no download, no `sudo`, no
network; `2 passed in 0.81s`. Direct read of the transcript produced on `clip5.wav` (read-only
`/tmp` probe through the same adapter, not a test edit):

```
clip: clip5.wav ch/width/rate/frames: 1 2 16000 80000 len(samples): 80000
load s: 0.076 device: cpu
decode s: 1.216
TRANSCRIPT: 'he hoped there would be stew for dinner turnips and carrots and bruised potatoes'
language: en-US ttft: None tps: None
```

The same payload mutation, real decode:

```
$ GES2T_NEMO_REAL_SEAM=1 uv run pytest tests/test_nemo_speech.py::test_real_library_recognizes_cached_audio -q
>       assert text.strip(), "the real library produced no text on real speech"
E       AssertionError: the real library produced no text on real speech
E       assert ''
tests/test_nemo_speech.py:704: AssertionError
1 failed in 0.25s
```

Revert verified byte-identical (`cmp`/`sha256sum`/`git diff --stat` empty, as above).

### F2 — re-falsified (`{self.last_error()}` dropped from the create message)

Mutation: `f"at {model_path}: {self.last_error()} (run 'ges2t setup "` →
`f"at {model_path}: (run 'ges2t setup "`.

```
$ GES2T_NEMO_REAL_SEAM=1 uv run pytest tests/test_nemo_speech.py::test_real_library_rejects_a_missing_model -q
        surfaced = library.last_error()
        assert surfaced, "the failed create recorded no last_error to check against"
>       assert surfaced in message, (...)
E       AssertionError: the library's own last_error 'Failed to load GGML file: /nonexistent/model.gguf' was not surfaced in
        "nemo_speech_asr_create failed with status 3 for the model at /nonexistent/model.gguf: (run 'ges2t setup nemo_speech' to acquire the pinned model)"
tests/test_nemo_speech.py:731: AssertionError
1 failed in 0.10s
```

The `assert surfaced` guard makes the test non-vacuous: `last_error()` really returns the library's
string after the failed create. Revert verified byte-identical; probes deleted.

### C-ABI (real installed header, read-only)

```
201: ... nemo_speech_asr_create(
204: ... void nemo_speech_asr_destroy(nemo_speech_asr_recognizer* recognizer);
209: ... nemo_speech_asr_status nemo_speech_asr_recognize_f32(
292: ... const char* nemo_speech_asr_last_error(void);
```

### Guard test and device rule re-run green

```
$ uv run pytest \
    tests/test_whisper_backend_config.py::test_batch_only_engines_agree_with_the_transcriber_capability \
    tests/test_nemo_speech.py::test_resolve_device_vulkan_preference_needs_a_vulkan_loader \
    tests/test_nemo_speech.py::test_transcribe_forwards_the_actual_audio_payload -v
3 passed in 0.11s
```

## Residual notes (non-blocking)

- **`002-review.md`'s prose nit (not the code).** `002`'s DoD-4 evidence says
  `grep -rn "libnemo_speech_asr_c" src/` finds "only nemo_speech.py". Literally it also appears in
  `src/ges2t/nemo_engine.py:160` as `ENTRY_LIBRARY_NAME = "libnemo_speech_asr_c.so"` (ticket 57's
  acquisition module — it names the file, it does not load it). The meaningful claim (only
  `nemo_speech.py` loads the library) holds; I record the discrepancy for honesty, not as a defect.
- **F2's guard is opt-in only.** The `last_error` surfacing is exercised only under
  `GES2T_NEMO_REAL_SEAM=1` (it must touch the real library); plain `pytest` never runs it. That is the
  correct seam test (ADR 0014 rule 7) — a hermetic fake cannot prove ctypes surfaces the C string — but
  it means a future regression to the message would be caught only by an opt-in run. Acceptable.
- **Redundant-but-harmless assertions** in the new test: `len(forwarded) == len(samples)` and `assert
  forwarded` are implied by `forwarded == tuple(samples)`. They read as belt-and-braces, not mistakes.
- `test_load_records_the_load_time`'s `load_seconds >= 0.0` remains tautological (`002` flagged it);
  out of scope for this fix and not worth a change.

## Could not verify

- **A pipeline-level "select `nemo_speech` and start"** — the composition-root builder is ticket 60 by
  the ticket's own Notes, so no runtime selection path exists on this branch. Verified at the adapter
  boundary only.
- **A real Vulkan decode** — not driven (would require forcing a GPU path this machine cannot be
  safely asserted on). The device rule is verified with injected `NemoDevice` records plus the
  unchanged falsification from `002`.
- **Ticket 55's harness output for the silence clip** — not re-run; `002` noted the same. Not needed
  now, since the real-seam test no longer depends on the silence result.
- **Stability of `"dinner"` beyond two runs.** I observed the reference word in two independent real
  decodes (the opt-in pytest run and the direct probe) on this clip/engine/GGUF; I did not run a
  repetition sweep, so I cannot state a flake rate for a non-deterministic engine. The clip and word
  are exactly those `tests/test_whisper_cpp.py` already relies on.

## Risks / follow-up

- None blocking. Ticket 59 (streaming) will flip `supports_streaming` and must update the guard test's
  transitional `NEMO_SPEECH_ENGINE` exception — the test names that explicitly, so it will be forced.
- No follow-up ticket required.

## Safe to relay to the user?

**Yes** — every claim above traces to a command whose real output is quoted, and both findings were
re-falsified rather than read.

## Safe to merge?

**Yes.** Verdict `pass`; the fix is test-only (`src/` byte-identical), F1 and F2 are closed and
independently falsified, the full suite is `1341 passed, 2 skipped`, `mypy` is clean, the docs link
check is `0 broken references`, and `git status` is clean. I did **not** merge, push, or flip the
ticket to `done`.

## Commit hygiene

Every probe was reverted; `cmp`/`sha256sum` confirmed the `src/` file byte-identical to the backup and
`git diff --stat` was empty after each; `/tmp` backups were deleted; `git status --short` was clean
before this artifact was written. No `src/` change from probing is present; no process was spawned
without a timeout; the worktree is left clean except for this review file.
