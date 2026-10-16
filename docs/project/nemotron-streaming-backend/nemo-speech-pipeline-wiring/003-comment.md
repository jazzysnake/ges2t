# 003-comment — ticket 60: response to review 002 (F1/F2/F3)

- **Ticket:** [ticket.md](ticket.md) — composition root + R1 resolution, Epic 6, ADR 0022.
- **Branch / worktree:** `feat/nemo-speech-pipeline-wiring` @ `.worktrees/nemo-speech-pipeline-wiring`
  (base `66f5235`, review tip `8ca6641`).
- **Scope:** **docs + the harness only.** No change to `src/ges2t/` or `tests/` — the review confirmed
  the R1 decision and every `src/` change are correct, so this pass fixes only the evidence artifact
  (`001-comment.md`) and its harness (`measure_r1.py`).

The review (`002-review.md`) returned **changes requested** with all findings in the evidence/docs, not
the source. This file records each fix and its evidence.

## `src/` and `tests/` are untouched — hash evidence

The two changed source files still hash to the exact baseline the comment records (and the review's
falsification matrix reverted to):

```
$ sha256sum src/ges2t/pipeline.py src/ges2t/transcribers/nemo_speech.py
055c44c734917e7628db0b38b02789607492abad4738971563d1a8194378825a  src/ges2t/pipeline.py
684ebc7cde99cb3b6eb4cdc976fad33cef3c6f26456ad60b31c7ed10e72b06ee  src/ges2t/transcribers/nemo_speech.py
```

Both match the recorded prefixes `pipeline.py 055c44c7…`, `nemo_speech.py 684ebc7c…`. The only files
this pass touches are the three documentation artifacts; the final branch state is:

```
$ git diff --name-only 8ca6641..HEAD
docs/project/nemotron-streaming-backend/nemo-speech-pipeline-wiring/001-comment.md
docs/project/nemotron-streaming-backend/nemo-speech-pipeline-wiring/003-comment.md
docs/project/nemotron-streaming-backend/nemo-speech-pipeline-wiring/measure_r1.py
$ git diff --name-only 8ca6641..HEAD -- src/ tests/
(empty)
$ git status --short
(empty)
```

<!-- appended incrementally as work proceeds -->

## F1 (blocking) — `measure_r1.py` could not run; it now genuinely measures offline vs streaming

### What the review found

The committed harness had two independent defects, both confirmed by re-running it at the tip:

1. **It called a method the same commit deleted.** `measure_r1.py` was added in `3fdcec5`, and
   `3fdcec5` is also the commit that removed `CtypesNemoSpeechLibrary._read_result`:
   ```
   $ git show 3fdcec5 --stat | grep -E 'measure_r1|nemo_speech.py'
    .../nemo-speech-pipeline-wiring/measure_r1.py      | 287 +++++++++++++++++++++
    src/ges2t/transcribers/nemo_speech.py              | 154 +++--------
   $ for c in 4b2a238 3fdcec5 8ca6641; do echo -n "$c: "; git show $c:src/ges2t/transcribers/nemo_speech.py | grep -c _read_result; done
   4b2a238: 2
   3fdcec5: 0
   8ca6641: 0
   ```
   So it was **never runnable at any committed revision**, and the command `001-comment.md` named did
   not produce the stated numbers (code-quality rule 9). Reproduced:
   ```
   $ uv run python docs/project/nemotron-streaming-backend/nemo-speech-pipeline-wiring/measure_r1.py --device auto --out /tmp/r1_auto_fresh.json
   ...
   device=vulkan load_s=0.05
   Traceback (most recent call last):
     File ".../measure_r1.py", line 265, in main
       result = measure_clip(...)
     File ".../measure_r1.py", line 210, in measure_clip
       offline_punc = offline_with_punctuation(library, handle, buffer)
     File ".../measure_r1.py", line 162, in offline_with_punctuation
       return library._read_result(result).text  # noqa: SLF001
   AttributeError: 'CtypesNemoSpeechLibrary' object has no attribute '_read_result'. Did you mean: 'next_result'?
   ```
2. **Its "offline" column was not offline.** `measure_clip` set
   `offline = transcriber.transcribe(buffer).text`, and after this ticket `transcribe()` **is** the
   `stream()` drain — so the two columns would have collapsed onto the same decode and masked the very
   divergence the harness exists to measure.

### The fix (harness only — nothing re-added to `src/`)

The production offline binding was correctly removed as dead code, so the harness now carries its
**own** raw-ABI binding instead: a `RawOfflineDecode` class (added to `measure_r1.py`) that takes the
`ctypes.CDLL` the adapter already loaded and declares the offline symbols itself —
`nemo_speech_asr_recognize_f32` (with its full argtypes), `nemo_speech_asr_result_transcript` and
`nemo_speech_asr_result_destroy`. It is called twice (punctuation off / on) to produce the two offline
columns; the recognizer handle is the adapter's own, so all three decodes run on one `.so` instance and
the **same audio buffer**. `transcribe()` is no longer used as a column. The harness is compile-clean:

```
$ uv run python -m py_compile docs/project/nemotron-streaming-backend/nemo-speech-pipeline-wiring/measure_r1.py
PY_COMPILE_OK
```

### Real command + output (re-run 2026-10-16; exit 0)

```
$ uv run python docs/project/nemotron-streaming-backend/nemo-speech-pipeline-wiring/measure_r1.py --device auto --out /tmp/r1_auto.json
[asr] model=.nemotron-3.5-asr-streaming-0.6b.q8_0.gguf head=rnnt backend=Vulkan0
[asr] mode=streaming head=rnnt left=56 center=1 right=1 attention=58 encoder-frames step=160ms
device=vulkan load_s=0.09
jfk          wers off=0.000 off+punc=0.000 stream=0.000 segments=70
quiet-short  wers off=0.259 off+punc=0.259 stream=0.259 segments=86
quiet-long   wers off=0.167 off+punc=0.167 stream=0.167 segments=150
commit       wers off=0.312 off+punc=0.312 stream=0.312 segments=158
wrote /tmp/r1_auto.json

$ uv run python docs/project/nemotron-streaming-backend/nemo-speech-pipeline-wiring/measure_r1.py --device cpu --out /tmp/r1_cpu.json
[asr] model=.nemotron-3.5-asr-streaming-0.6b.q8_0.gguf head=rnnt backend=CPU
[asr] mode=offline head=rnnt attention-left=56 attention-right=3 encoder-frames
[asr] mode=streaming head=rnnt left=56 center=1 right=1 attention=58 encoder-frames step=160ms
device=cpu load_s=0.07
jfk          wers off=0.000 off+punc=0.000 stream=0.000 segments=70
quiet-short  wers off=0.296 off+punc=0.296 stream=0.259 segments=86
quiet-long   wers off=0.194 off+punc=0.194 stream=0.167 segments=150
commit       wers off=0.234 off+punc=0.234 stream=0.328 segments=158
wrote /tmp/r1_cpu.json
```

**Reproduces the documented numbers exactly.** Vulkan per-clip offline / offline+punc / stream =
0.000 / 0.259 / 0.167 / 0.312 with Δ(stream − offline) = **+0.0000** on every clip. CPU:
Σ offline 0.7251, Σ stream 0.7541, mean Δ **+0.0072**; better on two clips (−0.037, −0.028), worse on
the car clip (+0.094). That is the same result the review's independent probe got — so the *numbers*
were always genuine; only the named command was not.

Exact 4-dp values from the JSON the run wrote (`--out`):

| device | clip | offline | offline+punc | stream | Δ(stream−offline) |
|---|---|---:|---:|---:|---:|
| vulkan | jfk | 0.0000 | 0.0000 | 0.0000 | +0.0000 |
| vulkan | quiet-short | 0.2593 | 0.2593 | 0.2593 | +0.0000 |
| vulkan | quiet-long | 0.1667 | 0.1667 | 0.1667 | +0.0000 |
| vulkan | commit (car) | 0.3125 | 0.3125 | 0.3125 | +0.0000 |
| cpu | jfk | 0.0000 | 0.0000 | 0.0000 | +0.0000 |
| cpu | quiet-short | 0.2963 | 0.2963 | 0.2593 | −0.0370 |
| cpu | quiet-long | 0.1944 | 0.1944 | 0.1667 | −0.0278 |
| cpu | commit (car) | 0.2344 | 0.2344 | 0.3281 | +0.0938 |

The align argument is measured too: scoring `offline+punc` against the `stream` final with the same
`word_diff` gives WER 0.0000 on **all four** Vulkan clips (word-identical) but **0.1852 / 0.1014 /
0.1061** on the CPU quiet-short / quiet-long / car clips — punctuation alignment does not reconcile the
two paths where they actually differ, because the difference is in **words** (`restore` vs `really
store`, `tails` vs `tiles`, `diploma` vs `Diplommer`).

Confounds (unchanged): one machine, one driver (RADV on Radeon 780M); all three decodes see the same
16 kHz mono reference clips (the ticket-55 fixtures, **not** clipping); same feed (0.16 s) for the
stream; CPU and Vulkan give deterministic-but-different text, so the numbers are per-device. A
streaming-vs-batch WER **difference** is a divergence number, not an accuracy claim.

### Stale method prose fixed

`001-comment.md`'s method paragraph said the harness drove "the real adapter's `transcribe()` (the
offline `recognize_f32`…)". That described the **pre-unification** adapter. It now says the offline
columns come from the harness's own `RawOfflineDecode` raw-ABI binding and that `transcribe()` is
deliberately *not* a column (because it is now the `stream()` drain). The same correction was made in
the harness's own module docstring.

## F2 — the test-count claim: **+12**, not “+2”

`001-comment.md` said the migrated tests left the count “unchanged +2”. Measured against the true
branch base (`66f5235`, identical to `main` for these files) and the tip:

```
$ for rev in 66f5235 8ca6641; do for f in tests/test_pipeline_streaming.py tests/test_nemo_speech.py; do \
      echo -n "$rev $f: "; git show $rev:$f | grep -c '^def test_'; done; done
66f5235 tests/test_pipeline_streaming.py: 22
66f5235 tests/test_nemo_speech.py: 49
8ca6641 tests/test_pipeline_streaming.py: 34
8ca6641 tests/test_nemo_speech.py: 49

$ diff <(git show 66f5235:tests/test_nemo_speech.py | grep '^def test_' | sort) \
       <(git show 8ca6641:tests/test_nemo_speech.py | grep '^def test_' | sort) && echo IDENTICAL
IDENTICAL
```

The nemo-speech file keeps the **same 49 test names** (its *bodies* were rewritten onto the streaming
seam, which is the migration claim, and it holds); the suite grows **1374 → 1386 = +12**, and all
12 are the new wiring tests in `test_pipeline_streaming.py` (22 → 34):

```
test_end_to_end_nemo_streaming_types_the_committed_deltas_and_the_final
test_nemo_chunk_ignores_a_whistle_block_when_its_own_is_absent
test_nemo_missing_artifacts_accepts_a_present_managed_model_and_engine
test_nemo_missing_artifacts_detects_a_wrong_sized_managed_model
test_nemo_missing_artifacts_reports_a_missing_model_and_engine
test_nemo_missing_artifacts_skips_the_pin_for_a_caller_supplied_model
test_nemo_speech_is_dispatched_and_its_default_chunk_is_pinned
test_run_service_reports_missing_nemo_artifacts_and_returns_nonzero
test_the_generic_missing_artifacts_check_is_whistle_specific_for_nemo
test_the_nemo_chunk_size_comes_from_its_own_block_not_the_whistle_one
test_validate_nemo_config_is_a_no_op_for_another_engine
test_validate_nemo_config_refuses_missing_artifacts_with_the_setup_hint
```

`001-comment.md` now says **+12** (1374 → 1386; nemo file unchanged at 49); the baseline suite total
is the review's measurement (`1374`) corroborated by `1386` at the tip minus the 12 new tests.

## F3 — every other number/claim, re-checked against the final tree

| claim in `001-comment.md` | re-checked at the tip | result |
|---|---|---|
| base `66f5235` | `git merge-base main HEAD` → `66f52351f8b9…` | **confirmed** |
| baseline `1374 passed, 3 skipped` | review measured it; `1386` at tip − 12 new | **confirmed** |
| final `1386 passed, 3 skipped` | `uv run pytest -q` (below) | **confirmed** |
| `mypy … 72 source files` | `uv run mypy src/ges2t tests` (below) | **confirmed** |
| docs link check `0 broken references` | §10 script (below) | **confirmed** |
| diff touches only `pipeline.py` + `nemo_speech.py` under `src/` | `git diff --name-only main...HEAD -- src/` | **confirmed** |
| the removed offline path is unreferenced | `grep -rn 'recognize_f32\|_read_result\|NemoSpeechTranscript' src/ tests/` → **docstrings only** | **confirmed** |
| only production `transcribe()` caller is `pipeline.py:188` | `grep -rn '\.transcribe(' src/` → `pipeline.py:188` | **confirmed** |
| Nemo default `0.16`, Whistle `1.0` | `DEFAULT_CHUNK_SECONDS` in the two config modules | **confirmed** |
| no `numpy`/`torch` in `src/` | `grep` → none | **confirmed** |
| real-seam `69 passed` | `GES2T_NEMO_REAL_SEAM=1 … -q` (below) | **confirmed** |
| Vulkan Δ 0.0000 / CPU mean +0.0073 | harness re-run → mean **+0.0072** (0.0290 ÷ 4) | **corrected** (rounding) |
| “test count unchanged +2” | see F2 | **corrected** to **+12** |
| falsification table (6 mutations) | review re-ran 5; this pass re-ran #4 (`load` idempotency) | **all 6 confirmed** |
| “ticket 55 measured 1,841 interims”, “Vulkan reproduces ticket 55 exactly” | ticket 55's own record; not re-runnable here without the ticket-55 harness | **not re-verified** (cited from 55) |

The mean-Δ correction: exact CPU mean is `(0.7541 − 0.7251) / 4 = 0.0072338 → +0.0072`; the old
`+0.0073` was a rounding drift. Every other number reproduced unchanged (both devices), so the
corrected tables in `001-comment.md` are the harness's real output.

Mutation #4 was the one the review did not re-run; this pass did (transient mutate → revert → hash):

```
$ uv run pytest tests/test_nemo_speech.py::test_load_is_idempotent_and_the_recognizer_stays_resident -q
>       assert len(fake.created) == 1  # created once
E       AssertionError: assert 3 == 1
FAILED tests/test_nemo_speech.py::test_load_is_idempotent_and_the_recognizer_stays_resident
1 failed in 0.08s

$ sha256sum src/ges2t/transcribers/nemo_speech.py   # after revert
684ebc7cde99cb3b6eb4cdc976fad33cef3c6f26456ad60b31c7ed10e72b06ee  src/ges2t/transcribers/nemo_speech.py
$ git diff --stat -- src/ tests/
(empty)
```

Exactly the comment's claimed `len(fake.created) == 3 ≠ 1`, and the source is byte-identical again.

## Verify gates (re-run after the fixes)

```
$ uv run pytest -q
1386 passed, 3 skipped in 6.72s

$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 72 source files

$ <docs link check §10>
0 broken references
```

`1386 passed` matches the comment's final count (the runtime differs run-to-run; the count does not).
The link check covers the new `003-comment.md` and the edited `001-comment.md`. `git status --short` is
empty after the commits.

## Status

The ticket stays **in review** on `feat/nemo-speech-pipeline-wiring`; the review asked for changes, so
this branch is submitted again for re-review. No merge, no push. `src/` and `tests/` are byte-identical
to the reviewed tip `8ca6641` (`pipeline.py 055c44c7…`, `nemo_speech.py 684ebc7c…`).

## Boundaries honoured

- No `src/ges2t/` change, no `tests/` change (only a transient, fully-reverted mutation for F3 above).
- No download, no network, no `sudo`, no microphone: only the already-downloaded scratch engine + GGUF
  and the ticket-55 reference clips were driven.
- No window, notification, keystroke or clipboard touched; no service started or stopped.
