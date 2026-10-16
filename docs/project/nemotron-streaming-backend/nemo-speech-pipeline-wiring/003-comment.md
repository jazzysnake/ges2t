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

Both match the recorded prefixes `pipeline.py 055c44c7…`, `nemo_speech.py 684ebc7c…`. Only these files
are modified in this pass:

```
$ git status --short
 M docs/project/nemotron-streaming-backend/nemo-speech-pipeline-wiring/001-comment.md
 M docs/project/nemotron-streaming-backend/nemo-speech-pipeline-wiring/measure_r1.py
?? docs/project/nemotron-streaming-backend/nemo-speech-pipeline-wiring/003-comment.md
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
