# 002 — review: NeMo-Speech pipeline wiring (ticket 60)

- **Ticket:** [ticket.md](ticket.md) — composition root + R1 resolution, Epic 6, ADR 0022.
- **Branch / worktree:** `feat/nemo-speech-pipeline-wiring` @ `.worktrees/nemo-speech-pipeline-wiring`
  (base `66f5235`, tip `57caa4f`).
- **Reviewer:** independent; authored none of this code. Only write is this file.
- **Range read:** three-dot `git diff main...HEAD`.

## Verdict

**changes requested.**

The **unification decision and the source change pass on the merits**: the R1 numbers reproduce
exactly (both devices, independently re-run on the real pinned engine), the drain-based `transcribe()`
satisfies the `Transcriber` Protocol, the removed offline path is truly unreferenced, the wiring is
correct, and every shipped gate is green.

The blocking finding is **evidential, not behavioural**: the committed harness `measure_r1.py` **cannot
run** — it calls `library._read_result(...)`, a method that commit `3fdcec5` (the *same* commit that
added the harness) removed — and it uses `transcribe()` as its "offline" column even though
`transcribe()` is now the streaming drain. The comment (`001-comment.md`) names
`uv run python …/measure_r1.py …` as the command that produced each number; **that command does not
produce them** (code-quality rule 9: every stated number traces to a real run — the named command
must be the one that ran). Two further claims in `001-comment.md` are inaccurate. Fixing these is a
small docs/harness change; it does not touch the source.

## The central claim, and what would falsify it

**Claim (R1, ticket 60):** the offline `recognize_f32` decode and the streaming-drain decode give
**the same word-level result on the machine's default device (Vulkan)** (Σ WER Δ 0.0000), so
`transcribe()` can be reimplemented as a drain of `stream()`; on CPU the difference is a wash (mean
Δ ≈ +0.0073), and aligning `enable_automatic_punctuation` cannot reconcile the two paths because the
divergence is in **words**, not casing.

**What would falsify it:** a real run where the streaming-drain WER is materially worse than the
offline WER on the **default** device; or a case where setting the punctuation flag on the offline
call makes the offline and streaming text agree (which would make the "align" branch viable). Neither
was observed.

## R1 reproduction (independent, real pinned engine, no network/sudo/mic/download)

The committed `measure_r1.py` crashes (see the blocking finding). I reproduced the numbers with an
independent probe (`/tmp/r1_probe.py`, not committed) that **imports the harness's `CLIPS`, references
and ticket-32 `word_diff`** and drives the same real adapter's `stream()` plus a raw
`nemo_speech_asr_recognize_f32` call (punc off / punc on) over the same scratch engine + GGUF.

**Vulkan (`--device vulkan`):**

```
device=vulkan load_s=0.07 chunk=0.16
jfk          off=0.0000 off+punc=0.0000 stream=0.0000 off:stream_delta=+0.0000 | off==offp? True
quiet-short  off=0.2593 off+punc=0.2593 stream=0.2593 off:stream_delta=+0.0000 | off==offp? True
quiet-long   off=0.1667 off+punc=0.1667 stream=0.1667 off:stream_delta=+0.0000 | off==offp? True
commit       off=0.3125 off+punc=0.3125 stream=0.3125 off:stream_delta=+0.0000 | off==offp? True
SUM off=0.7384 off+punc=0.7384 stream=0.7384 mean_delta=+0.0000
```

**CPU (`--device cpu`):**

```
device=cpu load_s=0.04 chunk=0.16
jfk          off=0.0000 off+punc=0.0000 stream=0.0000 off:stream_delta=+0.0000 | off==offp? True
quiet-short  off=0.2963 off+punc=0.2963 stream=0.2593 off:stream_delta=-0.0370 | off==offp? True
quiet-long   off=0.1944 off+punc=0.1944 stream=0.1667 off:stream_delta=-0.0278 | off==offp? True
commit       off=0.2344 off+punc=0.2344 stream=0.3281 off:stream_delta=+0.0938 | off==offp? True
SUM off=0.7251 off+punc=0.7251 stream=0.7541 mean_delta=+0.0072
```

**Findings:**

- **(a) Vulkan Σ WER identical across the four clips: CONFIRMED.** Per-clip offline / offline+punc /
  stream are 0.0000 / 0.2593 / 0.1667 / 0.3125; Δ(stream−offline) = **0.0000** on every clip. The
  comment's per-clip values match to 3 dp; its Σ `0.7385` is the sum of the *rounded* per-clip values
  (my exact Σ is 0.7384 — a rounding artefact, not a discrepancy; Δ reproduces exactly).
- **(b) CPU a wash: CONFIRMED.** Σ offline 0.7251 vs Σ stream 0.7541; mean Δ **+0.0072** (comment
  +0.0073, same value within rounding); stream better on two clips (−0.037, −0.028), worse on the car
  clip (+0.0938 ≈ +0.094).
- **(c) "changing the punctuation flag does not change the offline/streaming divergence": CONFIRMED.**
  On all four clips on both devices `off == offp` (True) — the flag changes casing/punctuation only,
  which `word_diff` strips, so the WER is byte-for-byte unchanged. On CPU the offline+punc text still
  differs from the streaming text in **words** (e.g. `restore` vs `really store`, `tails` vs `tiles`,
  `Diploma` vs `Diplommer`), so aligning the flag cannot reconcile the two paths — exactly the
  comment's decisive point.

The numbers are genuine: they were not fabricated (they reproduce to 4 dp on a re-run). Only the
named command in the doc does not.

## Blocking finding: the committed harness does not run

```
$ uv run python docs/project/nemotron-streaming-backend/nemo-speech-pipeline-wiring/measure_r1.py --device auto --out /tmp/r1_auto_review.json
[asr] model=.nemotron-3.5-asr-streaming-0.6b.q8_0.gguf head=rnnt backend=Vulkan0
[asr] mode=streaming head=rnnt left=56 center=1 right=1 attention=58 encoder-frames step=160ms
device=vulkan load_s=0.05
Traceback (most recent call last):
  File ".../measure_r1.py", line 287, in <module>
    raise SystemExit(main())
  File ".../measure_r1.py", line 265, in main
    result = measure_clip(...)
  File ".../measure_r1.py", line 210, in measure_clip
    offline_punc = offline_with_punctuation(library, handle, buffer)
  File ".../measure_r1.py", line 162, in offline_with_punctuation
    return library._read_result(result).text  # noqa: SLF001
AttributeError: 'CtypesNemoSpeechLibrary' object has no attribute '_read_result'. Did you mean: 'next_result'?
real	0m4.299s
```

Two independent defects in the harness:

1. **`library._read_result` no longer exists.** `measure_r1.py` was **added in `3fdcec5`**, and
   `3fdcec5` is also the commit that removed `_read_result`:
   ```
   $ git show 3fdcec5 --stat
    .../nemo-speech-pipeline-wiring/measure_r1.py      | 287 +++++++++++++++++++++
    src/ges2t/transcribers/nemo_speech.py              | 154 +++--------
   $ for c in 4b2a238 3fdcec5 57caa4f; do echo -n "$c: "; git show $c:src/ges2t/transcribers/nemo_speech.py | grep -c _read_result; done
   4b2a238: 2
   3fdcec5: 0
   57caa4f: 0
   ```
   It was **never runnable at any committed revision.** The implementer must have run an *uncommitted*
   pre-unification version (the numbers are real), but no committed command reproduces them.
2. **The "offline" column is not offline.** `measure_clip` computes
   `offline = transcriber.transcribe(buffer).text`, and `transcribe()` is now the drain of `stream()`.
   At the tip the harness's `offline` and `stream` columns would be the *same* decode — it would mask
   the very divergence it measures. The harness needs to call the raw `recognize_f32` itself for the
   offline column (it removed the binding that would let it).

**Required change:** make `measure_r1.py` runnable at the tip — declare the
`nemo_speech_asr_recognize_f32` argtypes in the harness and read the result with the still-present
`nemo_speech_asr_result_transcript` binding (the raw-ABI route this review used), for both the punc
off and punc on columns. Then re-run it and paste the real output in the comment. Or delete the
harness and state the probe honestly. (Docs-only; no `src/` change.)

## Second finding: inaccurate claims in `001-comment.md`

- **Test count.** `001-comment.md` (R1 section) says *"the ticket-58 tests that pinned the offline seam
  are migrated to the streaming seam, not deleted (test count unchanged +2)"*. Measured:
  ```
  $ for rev in main HEAD; do for f in tests/test_pipeline_streaming.py tests/test_nemo_speech.py; do \
        echo -n "$rev $f: "; git show $rev:$f | grep -c '^def test_'; done; done
  main tests/test_pipeline_streaming.py: 22
  main tests/test_nemo_speech.py: 49
  HEAD tests/test_pipeline_streaming.py: 34
  HEAD tests/test_nemo_speech.py: 49
  ```
  The nemo-speech file is **unchanged at 49** tests (its test-name set is byte-identical:
  `diff` of the `def test_` name sets → empty); the +12 tests are the new wiring tests in
  `test_pipeline_streaming.py`. The suite total is 1374 → 1386, i.e. **+12, not +2**. The migration
  claim itself is true (no test was deleted or weakened; `raise_on_recognize` was dead code in
  `main`, never used by a test), but "+2" is wrong.
- **Method description.** `001-comment.md` says the harness drives *"the real adapter's `transcribe()`
  (the offline `recognize_f32`…)"*. At the committed state `transcribe()` is the drain, and the
  offline column must be a raw `recognize_f32` call. The prose describes the pre-unification adapter.

## Per-criterion findings (Definition of done)

| # | DoD item | Verdict | Evidence |
|---|---|---|---|
| 1 | `_build_nemo_speech` added + registered in `_TRANSCRIBER_BUILDERS`; engine runs batch **and** streaming | **pass** | `pipeline.py` diff adds the builder and the `NEMO_SPEECH_ENGINE` registry entry. Mutation 1 (drop it) → `test_nemo_speech_is_dispatched…` + the E2E test fail with `PipelineError: transcriber engine 'nemo_speech' is not handled by this build`. |
| 2 | `_streaming_chunk_seconds` generalised; Whistle unchanged; Nemo default 0.16; interface-typed | **pass** | Resolver registry `_STREAMING_CHUNK_SECONDS`; `nemo_speech_config.DEFAULT_CHUNK_SECONDS = 0.16`; `whistle_config.DEFAULT_CHUNK_SECONDS = 1.0`. Mutation 2 (hardcode to `_whistle_chunk_seconds`) → `assert 0.25 == 0.5` + `test_nemo_chunk_ignores_a_whistle_block…` fail. `test_defaults_come_from_whistle_config_when_the_block_is_absent` body is **byte-identical** to `main` (sha256 `a1563da3…`). `mypy` clean. |
| 3 | Artifact presence dispatched; missing model/archive reported with the `ges2t setup nemo_speech` hint | **pass** | `_nemo_missing_artifacts` + `_validate_nemo_config` dispatched in `_startup`. Mutation 4 (`_nemo_missing_artifacts` → `[]`) fails 3 tests; mutation 5 (drop `_validate_nemo_config` from `_startup`) fails `test_run_service_reports_missing_nemo_artifacts_and_returns_nonzero` (generic hint appears instead of `ges2t setup nemo_speech`). |
| 4 | `test_pipeline_streaming.py:244` still passes for `whistle` + new Nemo case | **pass** | The `whistle` test is unchanged (above); the new `test_the_nemo_chunk_size_comes_from_its_own_block_not_the_whistle_one` and `test_nemo_speech_is_dispatched…` pin the Nemo chunk + builder. |
| 5 | End-to-end wiring test proves selection from config + streaming output (committed-delta path intact) | **pass** | `test_end_to_end_nemo_streaming_types_the_committed_deltas_and_the_final` drives `_build_service` through the real `pipeline._session_factory`; asserts `streams_opened==1`, audio pushed, `typed == final + " "`, `injected == []`, partial display. Mutation 1 also fails it. |
| 6 | No change to `streaming_session.py`, `service.py`, `socket_proto.py`, `incremental_typing.py`; Whistle/whisper behaviour unchanged | **pass** | `git diff --name-only main...HEAD -- src/` → only `src/ges2t/pipeline.py` and `src/ges2t/transcribers/nemo_speech.py`. All four boundary files + `streaming.py`/`whistle.py`/`whisper_cpp.py` untouched. |
| 7 | R1: **measure** offline vs streaming-drain, record the numbers | **partial** | The numbers are real and reproduce (above). But the committed command named in the doc **does not run** and the described offline column is wrong — see the blocking finding. |
| 8 | R1: **decide from evidence** — unify as a drain if within noise | **pass** | Vulkan Δ = 0.0000 → the DoD's own criterion is met. `transcribe()` now drains `stream()`; the offline `recognize_f32` path (`NemoSpeechTranscript`, `NemoSpeechLibrary.recognize`, `CtypesNemoSpeechLibrary.recognize`/`_read_result`, the binding) is removed and **unreferenced** (`grep` across `src/`+`tests/` → none, except docstrings). |
| 9 | `pytest` green, `mypy` 0, docs link check 0, README row updated | **pass** | `1386 passed, 3 skipped`; `Success: no issues found in 72 source files`; `0 broken references`; README row → `in review`. |

## Falsification matrix (break → watch fail → revert → hash)

Baseline `src/` hashes: `pipeline.py 055c44c7…`, `nemo_speech.py 684ebc7c…` (matching the comment).
After **every** mutation: `git checkout --` + `rm -rf .mypy_cache __pycache__` + `sha256sum -c` → both
`OK`.

| Mut. | Break | Test that failed (real output) | Reverted |
|---|---|---|---|
| 1 | drop `NEMO_SPEECH_ENGINE: _build_nemo_speech` from `_TRANSCRIBER_BUILDERS` | `test_nemo_speech_is_dispatched_and_its_default_chunk_is_pinned` + E2E → `PipelineError: transcriber engine 'nemo_speech' is not handled by this build; handled engines: 'whisper_cpp', 'whistle'` (2 failed) | OK |
| 2 | hardcode `_streaming_chunk_seconds` to `_whistle_chunk_seconds(config)` | `test_the_nemo_chunk_size_comes_from_its_own_block_not_the_whistle_one` → `AssertionError: assert 0.25 == 0.5`; `test_nemo_chunk_ignores_a_whistle_block…` failed (2 failed) | OK |
| 3 | remove `final_text = segment.live.committed` in `transcribe()` | `test_transcribe_returns_text_and_omits_engine_timings` → `AssertionError: assert '' == 'the text'` | OK |
| 4 | `_nemo_missing_artifacts` returns `[]` | `…_reports_a_missing_model_and_engine`, `…_detects_a_wrong_sized_managed_model`, `test_validate_nemo_config_refuses_missing_artifacts_with_the_setup_hint` → all failed (3 failed) | OK |
| 5 | remove `_validate_nemo_config(config)` from `_startup` | `test_run_service_reports_missing_nemo_artifacts_and_returns_nonzero` → `AssertionError` (generic hint `run 'ges2t setup'` appears; the `ges2t setup nemo_speech` hint is absent) | OK |

Every new guard tested is a test **seen to fail** (rule 6). Five of the comment's six claimed
mutations were re-run; all reproduce.

## Behaviour change is declared

`transcribe()` now returns **capitalised, punctuated** text where ticket 58 returned
lowercased/unpunctuated (verified by the raw probe: e.g. `and so my fellow americans…` vs
`And so my fellow Americans…`). It is documented in `001-comment.md` ("Effect on the user-visible
contract"), the REPORT §12 addendum, and the docstring; ticket 62 owns the user-facing docs. No code
path depended on the old casing: the only production caller is `BatchDictationSession`
(`pipeline.py:188`, `…transcribe(buffer).text`) which injects the text verbatim; `grep` found no
lowercasing of a `transcribe()` result in `src/`. The real-seam test lowercases only to search for the
reference word. **Declared and low-risk.**

## Scope / regression

- **Report edit is an addendum.** `git diff main...HEAD -- REPORT.md` introduces only `+` lines (a new
  `## 12. Addendum` at the end); no reviewed content is rewritten. `grep '^-[^-]'` → no removals.
- **No `numpy`/`torch`** in `src/`; `pyproject.toml`/`uv.lock` unchanged.
- **No unbounded waits introduced**; `uv run pytest -q` exited 0 (not 124). No new threads/sockets in
  the streaming code (boundary unchanged).
- `git status` after all probes: only this review file untracked; no modified `src/`.

## Real seam

`GES2T_NEMO_REAL_SEAM=1 uv run pytest tests/test_nemo_speech.py tests/test_nemo_speech_stream.py -q`
→ `69 passed in 4.63s`. The two real-engine tests (`test_real_library_recognizes_cached_audio` →
`transcribe()` on the real `.so`+GGUF asserting the reference word `dinner` and `None` timings;
`test_real_streaming_engine_appends_without_a_window` → the append-only committed sequence) **run**
(not skip) against the pinned scratch engine. The unified `transcribe()` therefore is proven on the
real child/library, satisfying rule 7 for the seam.

## Risks and follow-ups

- **B1 (blocking, docs).** `measure_r1.py` is unrunnable and its method description is stale; the
  command named in `001-comment.md` does not produce the stated numbers. Fix the harness (raw-ABI
  offline column) or delete it, and re-run/paste.
- **B2 (minor, docs).** "+2" test count is wrong (+12; nemo file unchanged at 49).
- **R1 residual (accepted).** The CPU car-clip regression (+0.094) is recorded as the weakest point;
  the default device (Vulkan) is Δ 0.0000. On CPU, `transcribe()` (now the stream) is slightly worse
  than the old offline output on that one clip. This is within the ticket's "wash" tolerance and is
  disclosed.
- **R2 (pre-existing, carried from ticket 59).** The unbounded session `worker.join()` is unchanged.
- **R3 (design).** `_default_chunk_seconds` parses the batch-only engine's block through
  `WhistleConfig`; reachable only for `whisper_cpp` and its value is discarded (the batch path never
  consumes it). Behaviour is byte-for-byte the pre-generalisation read; no action needed, but it is
  the one place a `WhistleConfig` parse can still be triggered by a non-Whistle block.

## Could not verify

- **The committed harness's provenance run.** I cannot recover the exact uncommitted probe the
  implementer ran; I can only confirm the *numbers* by independent re-run (they match to 4 dp). The
  claim that the committed `measure_r1.py` produced them is **false** as committed.
- **Other machines/clips/Hungarian.** The measurement is one machine, one driver (RADV, 780M); the
  four ticket-55 clips only. ADR 0022 already scopes Hungarian to published-FLEURS.
- **A live end-to-end mic session.** Forbidden (no microphone); the E2E test drives a fake audio
  source through the real session/service code.
- **Ticket 55's harness itself** (`measure_nemo_streaming.py`) was not re-run; only its reference clips
  and ticket-32 `word_diff` were reused.

## What would falsify the central claim (for the record)

A real run where the streaming-drain WER is materially worse than the offline WER on the **default**
device; or a punctuation-flag setting that makes the offline and streaming text agree in words on a
device where they currently differ. Not observed: Vulkan Δ = 0.0000 on all four clips, and on CPU
`off+punc` still differs from `stream` in words.

## Safe to relay to the user?

**Yes for the decision — with the caveat.** The unification of `transcribe()` onto `stream()` is
justified by a measurement that reproduces (Vulkan WER-identical, CPU a wash), the offline decode path
is genuinely gone, and the wiring works end to end. Relay it as a resolved decision, **not** as an
accuracy win (ADR 0022's limits stand).

**Do not relay the ticket as fully clean:** the committed evidence harness does not run, and two
claims in the comment are inaccurate. These are the required changes before merge. The source change
and the R1 decision themselves pass.

---

*Evidence commands run from `.worktrees/nemo-speech-pipeline-wiring`; all mutations reverted and
`src/` verified byte-identical (`sha256sum -c` OK); `git status` clean except this file; no
microphone, network, `sudo`, or window touched.*
