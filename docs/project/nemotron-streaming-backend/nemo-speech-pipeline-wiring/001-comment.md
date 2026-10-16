# 001-comment — ticket 60 (NeMo-Speech pipeline wiring): composition root + R1

Branch `feat/nemo-speech-pipeline-wiring` · worktree `.worktrees/nemo-speech-pipeline-wiring` ·
base `66f5235`.

## What this ticket is

Tickets 55–59 are merged. `nemo_speech` is selectable (58) and streams (59) but is **not in
`_TRANSCRIBER_BUILDERS`** and `_streaming_chunk_seconds` is still **Whistle-hardcoded**, so the engine
cannot run end to end yet. This ticket closes that gap and owns **R1** (the batch/stream text
divergence inherited from ticket 59's review).

Three jobs, per the ticket:

- **(A) composition root** — register `_build_nemo_speech`, generalise `_streaming_chunk_seconds`,
  dispatch artifact presence, end-to-end wiring test.
- **(B) R1** — measure offline `recognize_f32` vs streaming-drained-to-final on the reference clips
  (ticket 55's harness; no download, no sudo, no network, no mic), then decide from the numbers:
  unify `transcribe()` as a drain of `stream()`, or keep both and align their configuration.
- **(C) boundaries** — no change to `streaming_session.py`, `service.py`, `socket_proto.py`,
  `incremental_typing.py`; Whistle and whisper.cpp behaviour unchanged.

## Baseline (this worktree, before any change)

```
$ uv run pytest -q
1374 passed, 3 skipped in 12.64s
```

(A bare `uv sync --offline` after `uv venv --system-site-packages --python /usr/bin/python3` — no
network.)

<!-- appended incrementally as work proceeds -->

---

## (A) Composition-root wiring — implemented

| file | change |
|---|---|
| `src/ges2t/pipeline.py` | `_build_nemo_speech` added and registered in `_TRANSCRIBER_BUILDERS` (so `transcriber.engine = "nemo_speech"` builds the adapter, batch **and** streaming); `_streaming_chunk_seconds` **generalised** from a Whistle hardcoding to a per-engine resolver registry; `_nemo_missing_artifacts` + `_validate_nemo_config` added and dispatched in `_startup`, mirroring the whisper.cpp path. |
| `src/ges2t/transcribers/nemo_speech.py` | **R1 only** (below). |
| `tests/test_pipeline_streaming.py` | new: nemo builder dispatch + chunk default, the negative "does not read the whistle block" guards, the artifact-presence matrix, and the end-to-end wired streaming test. |
| `tests/test_nemo_speech.py` | the ticket-58 offline-path tests migrated to the streaming seam (the offline path is gone, R1). |
| `docs/.../nemo-speech-pipeline-wiring/measure_r1.py` | the R1 harness (additive; drives the real pinned scratch engine, no network/sudo/mic). |

### `_streaming_chunk_seconds` generalisation

`pipeline.py` now has `_whistle_chunk_seconds` / `_nemo_speech_chunk_seconds` /
`_default_chunk_seconds` and a `Mapping[str, Callable[[Config], float]]` registry keyed by the engine
name, the same shape as `_TRANSCRIBER_BUILDERS` / `_INJECTOR_BUILDERS`. `whistle` reads
`[engine.whistle] chunk_seconds` (unchanged); `nemo_speech` reads `[engine.nemo_speech] chunk_seconds`
(default **0.16**); an engine with no streaming chunk of its own (whisper.cpp, batch-only) falls back to
the pre-generalisation read of the selected engine's block through `WhistleConfig` — byte-for-byte the
old behaviour, and its value is discarded on the batch path. The signature stays
`(config: Config) -> float` (ADR 0019).

### Artifact presence

`_nemo_missing_artifacts` reports (a) the managed pinned GGUF missing/wrong-size, or a **caller-supplied**
`[engine.nemo_speech] model` path is not gated on our pin (mirroring whisper.cpp's block-level override),
and (b) `nemo_engine.verify_engine(engine_dir()) is None` (manifest missing, wrong pin, or a named file
gone). `_validate_nemo_config` turns those into a `PipelineError` with the hint
`ges2t setup nemo_speech`, called in `_startup` before any artifact is loaded. `_missing_artifacts`
(the Whistle check) stays whistle-specific and returns `[]` for nemo — asserted.

---

## (B) R1 — the batch/stream divergence: measured, then decided

### Method (real runs, this machine, no download / no network / no sudo / no mic)

Harness: `docs/project/nemotron-streaming-backend/nemo-speech-pipeline-wiring/measure_r1.py`, driving
the **already-downloaded** pinned scratch engine + GGUF
(`~/.cache/ges2t-discovery/nemotron-3-5-asr-streaming-measurement/…`) through the real adapter's
`transcribe()` (the offline `recognize_f32`, library default `enable_automatic_punctuation=False`) and
`stream()` (drained to its end-of-stream final, `enable_automatic_punctuation=True`), plus a raw
offline call with punctuation on (the "align" option). Same 16 kHz mono clips ticket 55 used
(`~/.cache/ges2t-discovery/whisper-cpp-provider/audio/`), same normalisation (ticket 32's `word_diff`,
reused by path). Both paths see **the same audio buffer**; `chunk_seconds = 0.16` for the stream.

Command (one for each device):

```
uv run python docs/project/nemotron-streaming-backend/nemo-speech-pipeline-wiring/measure_r1.py \
    --device auto --out /tmp/r1_auto.json      # resolved device = vulkan (RADV, 780M)
uv run python docs/project/nemotron-streaming-backend/nemo-speech-pipeline-wiring/measure_r1.py \
    --device cpu  --out /tmp/r1_cpu.json
```

### Numbers (word_diff WER vs the ticket-55 references)

**Vulkan** (load 0.05 s; the machine's `device = auto` default):

| clip | dur s | offline (punc off) | offline + punc | stream → final | stream segments |
|---|---:|---:|---:|---:|---:|
| jfk | 11.0 | 0.000 | 0.000 | 0.000 | 70 |
| quiet-short | 13.51 | 0.259 | 0.259 | 0.259 | 86 |
| quiet-long | 23.81 | 0.167 | 0.167 | 0.167 | 150 |
| commit (car) | 25.04 | 0.312 | 0.312 | 0.312 | 158 |
| **Σ / mean Δ(stream−offline)** | | **0.7385** | 0.7385 | **0.7385** | **+0.0000** |

**CPU** (the CPU/Vulkan confound the reports already record — transcripts are **not** device-identical):

| clip | dur s | offline (punc off) | offline + punc | stream → final |
|---|---:|---:|---:|---:|
| jfk | 11.0 | 0.000 | 0.000 | 0.000 |
| quiet-short | 13.51 | 0.296 | 0.296 | 0.259 |
| quiet-long | 23.81 | 0.194 | 0.194 | 0.167 |
| commit (car) | 25.04 | 0.234 | 0.234 | 0.328 |
| **Σ / mean Δ(stream−offline)** | | **0.7251** | 0.7251 | **0.7541** (mean **+0.0073**) |

Confounds stated: **one machine, one driver** (RADV on Radeon 780M); the two paths run on the **same
audio** with the **same** 0.16 s feed; input is the 16 kHz mono reference clips (**not** clipping —
they are the ticket-55 fixtures); CPU and Vulkan produce deterministic-but-different text, so the
numbers are per-device. The Vulkan numbers reproduce ticket 55's exactly (0.000 / 0.259 / 0.167 /
0.312). A streaming-vs-batch WER **difference** is a divergence number, not an accuracy claim; the
ground-truth reference is what says which path is closer.

### Decision: **unify** — `transcribe()` is now a drain of `stream()`

- On the machine's default device (**Vulkan**), the streaming-drain WER is **identical** to the offline
  WER on all four clips (Σ 0.7385 = 0.7385): the criterion "within noise of, or better than, offline"
  holds exactly.
- On **CPU** it is a wash, not a regression: streaming is better on two clips (−0.037, −0.028) and
  worse on one (+0.094 on the car clip, where the offline hears "restore" and the stream "really
  store"); the mean difference is **+0.0073**. Bidirectional and tiny → within noise.
- **The decisive point against the "align" option:** aligning `enable_automatic_punctuation`
  (offline+punc column) changes **nothing** in the WER (it is a case/punctuation change that the
  normalisation strips) and the offline decoder still differs from the streaming decoder **in words**
  on CPU. So alignment does **not** deliver "the same audio gives consistent text" — only a single
  decode path does. The measurement therefore rules the align branch out on its own terms.

The change (`nemo_speech.py`, tight): `transcribe()` feeds the whole buffer through `stream()` and
returns that path's end-of-stream final, so the batch session and the streaming session now share **one**
text-producing path and the divergence is gone by construction. The old second decoder
(`nemo_speech_asr_recognize_f32` with the library's lowercased/unpunctuated default) is **removed** —
`NemoSpeechTranscript`, the `recognize` Protocol method, `CtypesNemoSpeechLibrary.recognize` /
`_read_result`, and the `nemo_speech_asr_recognize_f32` binding — because leaving it would be dead code
(rule 10). The ticket-58 tests that pinned the offline seam are migrated to the streaming seam, not
deleted (test count unchanged +2).

**What would falsify the decision:** a real run where the streaming-drain WER is materially worse than
the offline WER on the **default** device. It was not observed (identical on Vulkan). The CPU car-clip
regression is recorded above as the weakest point.

### Effect on the user-visible contract

`transcribe()` now returns **capitalised, punctuated** text (the engine's streaming final) where it used
to return lowercased/unpunctuated text; the streaming path is unchanged. Ticket 62 (nemo-speech-docs)
owns stating this in the user-facing docs.

---

## Falsification (each: break → watch fail → revert → `sha256sum -c` OK)

Baseline `src/` hashes: `pipeline.py 055c44c7…`, `nemo_speech.py 684ebc7c…`.

| Mut. | Break | Test that failed (real output) |
|---|---|---|
| 1 | drop `NEMO_SPEECH_ENGINE: _build_nemo_speech` from `_TRANSCRIBER_BUILDERS` | `test_nemo_speech_is_dispatched_and_its_default_chunk_is_pinned` → `PipelineError: transcriber engine 'nemo_speech' is not handled by this build`; and the end-to-end test → same |
| 2 | hardcode `_streaming_chunk_seconds` back to `_whistle_chunk_seconds(config)` | `test_nemo_chunk_ignores_a_whistle_block_when_its_own_is_absent` → `assert 0.25 == 0.16`; `test_the_nemo_chunk_size_comes_from_its_own_block_not_the_whistle_one` → Failed |
| 3 | drop the final-text assignment in `transcribe()` (drain loses the final) | `test_transcribe_returns_text_and_omits_engine_timings` → `assert '' == 'the text'` |
| 4 | make `NemoSpeechTranscriber.load` non-idempotent (`if False:`) | `test_load_is_idempotent_and_the_recognizer_stays_resident` → `len(fake.created) == 3` ≠ 1 |
| 5 | `_nemo_missing_artifacts` returns `[]` | `test_nemo_missing_artifacts_reports_a_missing_model_and_engine`, `…detects_a_wrong_sized_managed_model`, `test_validate_nemo_config_refuses_missing_artifacts_with_the_setup_hint` → all Failed |
| 6 | remove the `_validate_nemo_config(config)` call from `_startup` | `test_run_service_reports_missing_nemo_artifacts_and_returns_nonzero` → the generic text appears instead of the `ges2t setup nemo_speech` hint |

After every mutation: `git checkout --`-equivalent restore from a `cp` snapshot + `sha256sum -c` → both
`OK`; `src/` byte-identical.

---

## Boundaries and verify

- **No change** to `streaming_session.py`, `service.py`, `socket_proto.py`, `incremental_typing.py`,
  `streaming.py`, `whistle.py`, `whisper_cpp.py` (`git diff --stat main...HEAD` shows only
  `pipeline.py`, `nemo_speech.py`, two test files, and this ticket's docs). Whistle and whisper.cpp are
  behaviourally unchanged. No new dependency; no `numpy`/`torch`.

```
$ uv run pytest -q
1386 passed, 3 skipped in 7.62s

$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 72 source files

$ <docs link check §10>
0 broken references

$ git status --short
(clean)
```

Real-seam (opt-in, the real `.so` + pinned GGUF, CPU): `GES2T_NEMO_REAL_SEAM=1 uv run pytest
tests/test_nemo_speech.py tests/test_nemo_speech_stream.py -q` → `69 passed` — the unified
`transcribe()` returns the reference word `dinner` on `clip5.wav` through the streaming path.
