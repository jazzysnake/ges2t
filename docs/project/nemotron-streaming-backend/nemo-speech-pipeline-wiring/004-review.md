# 004 — re-review: NeMo-Speech pipeline wiring (ticket 60)

- **Ticket:** [ticket.md](ticket.md) — composition root + R1 resolution, Epic 6, ADR 0022.
- **Branch / worktree:** `feat/nemo-speech-pipeline-wiring` @ `.worktrees/nemo-speech-pipeline-wiring`
  (base `66f5235`, prior review tip `8ca6641`, re-review tip `a4776d9`).
- **Reviewer:** independent; authored none of this code. Only write is this file.
- **Range read:** three-dot `git diff main...HEAD`.

## Verdict

**pass.**

The prior review (`002-review.md`) returned **changes requested** with all findings confined to the
evidence artifact and its harness (the R1 decision and every `src/` change had passed). This re-review
confirms all three findings are fixed, re-runs every gate, re-runs the harness on the real pinned
engine for both devices, and re-verifies the `src/` tree is byte-identical to the reviewed tip.

- **F1 fixed.** `measure_r1.py` now declares its **own** raw-ABI `RawOfflineDecode` binding for
  `nemo_speech_asr_recognize_f32` (nothing re-added to `src/`; the argtype block is a faithful copy of
  the removed production binding). It **runs** for both devices, exit 0, and reproduces the documented
  numbers exactly. Its two columns are genuinely distinct decoders: a live mutation that collapses the
  "offline" column onto the drain makes the CPU divergence it measures **disappear**.
- **F2 fixed.** The delta is stated as **+12** (1374 → 1386); `test_pipeline_streaming.py` 22 → 34,
  `test_nemo_speech.py` 49 → 49 with a byte-identical test-name set. Confirmed.
- **F3 confirmed.** Base, suite totals, mypy file count, the `src/` diff file set, and the removed
  offline path being unreferenced all check out against the tree.

No blocking finding remains. One nit below (a rounding artefact), non-blocking.

## The central claim, and what would falsify it

**Claim (R1, ticket 60):** the offline `recognize_f32` decode and the streaming-drain decode give
**the same word-level result on the machine's default device (Vulkan)** (Σ WER Δ 0.0000), so
`transcribe()` can be reimplemented as a drain of `stream()`; on CPU the difference is a wash (mean
Δ ≈ +0.0072); and aligning `enable_automatic_punctuation` cannot reconcile the two paths where they
differ because the divergence is in **words**, not casing.

**What would falsify it:** a real run where the streaming-drain WER is materially worse than the
offline WER on the **default** device; or a case where setting the punctuation flag on the offline
call makes the offline and streaming text agree in words. **Neither was observed** (see the harness
run below).

## F1 — the harness runs and the columns are distinct

### It runs (both devices, real pinned engine, no download/network/sudo/mic), exit 0

```
$ uv run python docs/project/nemotron-streaming-backend/nemo-speech-pipeline-wiring/measure_r1.py --device auto --out /tmp/r1_auto_004.json
[asr] model=.nemotron-3.5-asr-streaming-0.6b.q8_0.gguf head=rnnt backend=Vulkan0
[asr] mode=streaming head=rnnt left=56 center=1 right=1 attention=58 encoder-frames step=160ms
device=vulkan load_s=0.09
jfk          wers off=0.000 off+punc=0.000 stream=0.000 segments=70
quiet-short  wers off=0.259 off+punc=0.259 stream=0.259 segments=86
quiet-long   wers off=0.167 off+punc=0.167 stream=0.167 segments=150
commit       wers off=0.312 off+punc=0.312 stream=0.312 segments=158
wrote /tmp/r1_auto_004.json
real	0m33.456s        EXIT=0
```

```
$ uv run python docs/project/nemotron-streaming-backend/nemo-speech-pipeline-wiring/measure_r1.py --device cpu --out /tmp/r1_cpu_004.json
[asr] model=.nemotron-3.5-asr-streaming-0.6b.q8_0.gguf head=rnnt backend=CPU
[asr] mode=offline head=rnnt attention-left=56 attention-right=3 encoder-frames
[asr] mode=streaming head=rnnt left=56 center=1 right=1 attention=58 encoder-frames step=160ms
device=cpu load_s=0.07
jfk          wers off=0.000 off+punc=0.000 stream=0.000 segments=70
quiet-short  wers off=0.296 off+punc=0.296 stream=0.259 segments=86
quiet-long   wers off=0.194 off+punc=0.194 stream=0.167 segments=150
commit       wers off=0.234 off+punc=0.234 stream=0.328 segments=158
wrote /tmp/r1_cpu_004.json
real	0m39.841s        EXIT=0
```

### It reproduces the documented numbers (4-dp values from the JSON the run wrote)

```
--- auto ---
jfk          off=0.0000 off+punc=0.0000 stream=0.0000 delta=+0.0000 segs=70
quiet-short  off=0.2593 off+punc=0.2593 stream=0.2593 delta=+0.0000 segs=86
quiet-long   off=0.1667 off+punc=0.1667 stream=0.1667 delta=+0.0000 segs=150
commit       off=0.3125 off+punc=0.3125 stream=0.3125 delta=+0.0000 segs=158
SUM off=0.7385 offp=0.7385 stream=0.7385 mean_delta=+0.0000

--- cpu ---
jfk          off=0.0000 off+punc=0.0000 stream=0.0000 delta=+0.0000 segs=70
quiet-short  off=0.2963 off+punc=0.2963 stream=0.2593 delta=-0.0370 segs=86
quiet-long   off=0.1944 off+punc=0.1944 stream=0.1667 delta=-0.0277 segs=150
commit       off=0.2344 off+punc=0.2344 stream=0.3281 delta=+0.0937 segs=158
SUM off=0.7251 offp=0.7251 stream=0.7541 mean_delta=+0.0072
```

**Vulkan Δ 0.0000 on all four clips (CONFIRMED).** CPU mean Δ **+0.0072**, car **+0.0938/0.0937**
(CONFIRMED). The exact CPU mean is `(0.7541 − 0.7251)/4 = 0.00723`. The task's stated `car ≈ +0.094`
and `mean ≈ +0.0072` both hold.

### The columns are genuinely distinct (not collapsed)

- The `offline` text is **lowercased/unpunctuated** (`and so my fellow americans ask not …`) while the
  `stream` text is **capitalised/punctuated** (`And so my fellow Americans …`) — two different decoders.
- On CPU the WERs **differ** per clip (e.g. quiet-short offline 0.296 vs stream 0.259), which a
  collapsed column could not show. On Vulkan they coincide *after* normalisation, which is the point.
- **Decisive-align measurement**, `word_diff(offline+punc, stream_final)` using ticket 32's
  `word_diff` (re-run here, real output):
  ```
  === auto: word_diff(offline+punc, stream_final) ===
  jfk          wer=0.0000    quiet-short  wer=0.0000
  quiet-long   wer=0.0000    commit       wer=0.0000
  === cpu: word_diff(offline+punc, stream_final) ===
  jfk          wer=0.0000    quiet-short  wer=0.1852
  quiet-long   wer=0.1014    commit       wer=0.1061
  ```
  Word-identical on Vulkan (Δ 0.0000), **not** on CPU — exactly `003-comment.md`'s claim
  (0.1852 / 0.1014 / 0.1061).

### Falsification: the column is live (rule 6)

I mutated a **copy** of the harness so its "offline" column became
`transcriber.transcribe(buffer).text` (the drain — the very collapse F1 was about), ran it on CPU, and
the divergence it exists to measure **disappeared**:

```
$ uv run python .../measure_r1_probe004.py --device cpu --out /tmp/r1_cpu_collapsed.json
quiet-short  wers off=0.259 off+punc=0.259 stream=0.259 segments=86     # was 0.296
quiet-long   wers off=0.167 off+punc=0.167 stream=0.167 segments=150     # was 0.194
commit       wers off=0.328 off+punc=0.328 stream=0.328 segments=158     # was 0.234
EXIT=0
```

The committed harness does **not** show this collapse; its offline column is therefore a real call to
the offline decoder. (The probe copy was deleted; `git status` clean.)

### Nothing re-added to `src/`

```
$ grep -rn 'recognize_f32\|_read_result\|NemoSpeechTranscript' src/ tests/
src/ges2t/transcribers/nemo_speech.py:61:The offline ``nemo_speech_asr_recognize_f32`` call is deliberately **not** mirrored:
src/ges2t/transcribers/nemo_speech.py:996:        (``nemo_speech_asr_recognize_f32`` under the library's lowercased, unpunctuated
```

Both hits are **docstrings**; no binding, no `_read_result`, no `NemoSpeechTranscript` type in
`src/` or `tests/`. The harness's binding is `measure_r1.py`'s own, against the `ctypes.CDLL` the
adapter already loaded — and its `argtypes` are exactly the removed production binding's (compared
against `git show 66f5235:src/ges2t/transcribers/nemo_speech.py`, lines 424–432), so the offline column
is the same ABI the pre-ticket-60 batch path used.

## F2 — the test delta is +12, not +2

```
$ for rev in 66f5235 a4776d9; do for f in tests/test_pipeline_streaming.py tests/test_nemo_speech.py; do \
      echo -n "$rev $f: "; git show $rev:$f | grep -c '^def test_'; done; done
66f5235 tests/test_pipeline_streaming.py: 22
66f5235 tests/test_nemo_speech.py: 49
a4776d9 tests/test_pipeline_streaming.py: 34
a4776d9 tests/test_nemo_speech.py: 49

$ diff <(git show 66f5235:tests/test_nemo_speech.py | grep '^def test_' | sort) \
       <(git show a4776d9:tests/test_nemo_speech.py | grep '^def test_' | sort) && echo IDENTICAL
IDENTICAL
```

The 12 new names in `test_pipeline_streaming.py` are exactly the list in `003-comment.md`. Suite
1374 → 1386 = **+12**. **Confirmed.**

## F3 — every other number/claim re-checked against the tree

| claim | command | result |
|---|---|---|
| base `66f5235` | `git merge-base main HEAD` → `66f52351f8b9…` | **confirmed** |
| final `1386 passed, 3 skipped` | `uv run pytest -q` → `1386 passed, 3 skipped in 10.20s` (exit 0) | **confirmed** |
| `mypy … 72 source files` | `rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests` → `Success: no issues found in 72 source files` | **confirmed** |
| docs link check `0 broken references` | §10 script → `0 broken references` | **confirmed** |
| `src/` diff is only `pipeline.py` + `nemo_speech.py` | `git diff --name-only main...a4776d9 -- src/` | **confirmed** |
| removed offline path is unreferenced | `grep … src/ tests/` → docstrings only | **confirmed** |
| Nemo default `0.16`, Whistle `1.0` | `DEFAULT_CHUNK_SECONDS` in the two config modules | **confirmed** |
| only production `transcribe()` caller is `pipeline.py:188` | `grep -rn '\.transcribe(' src/` | **confirmed** |
| no `numpy`/`torch` in `src/` | `grep` → none | **confirmed** |
| real-seam `69 passed` | `GES2T_NEMO_REAL_SEAM=1 … -q` → `69 passed` (0 skipped) | **confirmed** |
| README row → `in review`; REPORT addendum additive | diffs | **confirmed** (25 `+`, 0 removed lines) |

**Nit (non-blocking):** `001-comment.md`'s CPU table row for quiet-long shows Δ `−0.0278`; recomputing
from the 4-dp values printed by the harness gives `−0.0277` (the exact value is `−0.02778`, which
rounds either way depending on tie-breaking). The full-precision Σ and the mean (`+0.0072`) are
correct. This is a rounding-presentation artefact, not a wrong number.

## `src/` and `tests/` untouched by the fix

```
$ git diff 8ca6641 a4776d9 --name-only
docs/project/nemotron-streaming-backend/nemo-speech-pipeline-wiring/001-comment.md
docs/project/nemotron-streaming-backend/nemo-speech-pipeline-wiring/003-comment.md
docs/project/nemotron-streaming-backend/nemo-speech-pipeline-wiring/measure_r1.py

$ git diff 8ca6641 a4776d9 --name-only -- src/ tests/
(empty)

$ for c in 08a6ab4 eeb6449 15e29fe a4776d9; do git show --stat --oneline $c; done   # only those 3 docs files
```

Merged-code hashes are unchanged from the reviewed tip (and from the recorded baseline):

```
$ for f in src/ges2t/pipeline.py src/ges2t/transcribers/nemo_speech.py; do echo -n "$f: "; git show a4776d9:$f | sha256sum; done
src/ges2t/pipeline.py: 055c44c734917e7628db0b38b02789607492abad4738971563d1a8194378825a  -
src/ges2t/transcribers/nemo_speech.py: 684ebc7cde99cb3b6eb4cdc976fad33cef3c6f26456ad60b31c7ed10e72b06ee  -
```

Both match the recorded prefixes and the `8ca6641` hashes exactly. `git status --porcelain
--untracked-files=all` shows only this review file — the transient F3 mutation (mut #4) was fully
reverted, and my own mutation probes were reverted (`diff` against a baseline snapshot →
`IDENTICAL_TO_BASELINE`).

## Overall conclusion still holds (wiring intact, boundaries unchanged)

- `_build_nemo_speech` is defined (`pipeline.py:250`) and registered in `_TRANSCRIBER_BUILDERS`
  (`:277`); `_streaming_chunk_seconds` dispatches through `_STREAMING_CHUNK_SECONDS` (`:482`).
- Boundary files are unchanged vs `main`: `streaming_session.py`, `service.py`, `socket_proto.py`,
  `incremental_typing.py`, `streaming.py`, `whistle.py`, `whisper_cpp.py` all diff-empty.
- The offline decoder is gone: `_read_result` count at base `66f5235` = 2, at `a4776d9` = 0.

### Falsification matrix (this re-review)

Baseline `src/` hashes as above. After each mutation: `git checkout --` + `rm -rf .mypy_cache
__pycache__ …` + `sha256sum` → **OK**.

| Mut. | Break | Test that failed (real output) | Reverted |
|---|---|---|---|
| A | drop `NEMO_SPEECH_ENGINE: _build_nemo_speech` from `_TRANSCRIBER_BUILDERS` | `test_nemo_speech_is_dispatched_and_its_default_chunk_is_pinned` + `test_end_to_end_nemo_streaming_types_the_committed_deltas_and_the_final` → `PipelineError: transcriber engine 'nemo_speech' is not handled by this build; handled engines: 'whisper_cpp', 'whistle'` (2 failed) | OK (`055c44c7…`) |
| B | hardcode the Nemo chunk resolver to `_whistle_chunk_seconds` | `test_the_nemo_chunk_size_comes_from_its_own_block_not_the_whistle_one` + `test_nemo_chunk_ignores_a_whistle_block_when_its_own_is_absent` → `assert 0.25 == 0.16` (2 failed) | OK (`055c44c7…`) |
| C | collapse the harness offline column onto `transcribe()` (probe copy) | the CPU divergence vanishes (quiet-short 0.296 → 0.259) — proving the committed column is live | probe copy deleted |

Every new guard I broke was a test **seen to fail**; the harness column was **seen to be live**.

## Per-criterion findings (Definition of done)

| # | DoD item | Verdict | Evidence |
|---|---|---|---|
| 1 | `_build_nemo_speech` added + registered; runs batch **and** streaming | **pass** | `pipeline.py:250/277`; mutation A fails the dispatch + E2E tests with the "not handled" error |
| 2 | `_streaming_chunk_seconds` generalised; Whistle unchanged; Nemo default 0.16; interface-typed | **pass** | `_STREAMING_CHUNK_SECONDS` registry; `DEFAULT_CHUNK_SECONDS` 1.0 (whistle) / 0.16 (nemo); mutation B fails `assert 0.25 == 0.16`; mypy clean |
| 3 | Artifact presence dispatched for the engine | **pass** | `_nemo_missing_artifacts` + `_validate_nemo_config`; prior review's mutations 4/5 reproduced; real `test_run_service_…` present |
| 4 | `test_pipeline_streaming.py:244` unchanged for whistle + new Nemo case | **pass** | prior review verified byte-identical; new Nemo chunk/dispatch tests present and live (mutation B) |
| 5 | End-to-end wiring test (fake library, committed-delta path intact) | **pass** | `test_end_to_end_nemo_streaming_types_the_committed_deltas_and_the_final` (fails under mutation A) |
| 6 | No change to the four boundary files; Whistle/whisper unchanged | **pass** | all boundary diffs empty vs `main` |
| 7 | R1: **measure** offline vs streaming-drain; record numbers | **pass** | harness runs both devices, exit 0, reproduces; columns distinct (probe C) |
| 8 | R1: **decide from evidence** — unify as a drain | **pass** | Vulkan Δ 0.0000 ×4; `off+punc` vs `stream` 0.0000 Vulkan vs 0.1852/0.1014/0.1061 CPU → align cannot reconcile; offline path removed & unreferenced |
| 9 | `pytest` green, `mypy` 0, link check 0, README row | **pass** | `1386 passed, 3 skipped`; `72 source files`; `0 broken references`; README row `in review` |

## Risks and follow-ups

- **R1 residual (accepted, disclosed).** On CPU the unified `transcribe()` is slightly worse than the
  old offline output on the car clip (+0.0938). The default device (Vulkan) is Δ 0.0000. Within the
  ticket's "wash" tolerance.
- **R2 (pre-existing, carried from ticket 59).** The unbounded session `worker.join()` is unchanged by
  this ticket — out of scope here, worth its own ticket.
- **No new risk from the fixes:** the fix range touches only docs + the harness; the merged `src/` is
  byte-identical to the reviewed tip.

## Could not verify

- **The harness's provenance run.** The implementer's original probe is not recoverable from history;
  I verified the **numbers** by independent re-run at the tip (they match to 4 dp) and verified the
  committed harness is live and faithful. The claim that *this committed* harness produced them is now
  **true** (it ran here).
- **Other machines/devices/clips.** One machine, one driver (RADV on Radeon 780M), the four ticket-55
  clips only.
- **A live microphone session.** Forbidden; the E2E test drives a fake audio source through the real
  session/service code.
- **Ticket 55's own harness** was not re-run; only its reference clips and ticket-32 `word_diff` were
  reused.
- **The `+2`→`+12` internals.** I confirmed the counts and the identical nemo test-name set; I did not
  re-read every migrated test body (the prior review did).

## Safe to merge?

**Yes — safe to merge.** The two blocking findings (F1 unrunnable harness, F2 wrong count) are fixed
and re-verified against the real pinned engine; F3's claims all re-check; `src/` and `tests/` are
byte-identical to the reviewed-and-passed tip; `pytest` (`1386 passed, 3 skipped`, exit 0), `mypy`
(`72 source files`, 0 errors), and the docs link check (`0 broken references`) are green; `git status`
is clean apart from this file. The R1 unification decision stands on a measurement that reproduces,
including the negative (align) branch. Relay it as a **resolved decision**, not an accuracy win
(ADR 0022's CPU/Vulkan confound and the car-clip residual stand).

---

*Evidence commands run from `.worktrees/nemo-speech-pipeline-wiring`; all mutations reverted and `src/`
verified byte-identical (`sha256sum` + snapshot `diff`); `git status` clean except this file; the
harness ran against the already-downloaded scratch engine + GGUF and the ticket-55 reference clips; no
microphone, network, `sudo`, window, notification, keystroke, or clipboard touched.*
