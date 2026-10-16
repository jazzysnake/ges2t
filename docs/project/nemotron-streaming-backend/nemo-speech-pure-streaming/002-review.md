# 002 — review: Nemotron pure-streaming (ticket 59)

- **Ticket:** [ticket.md](ticket.md) — the mechanism ticket of Epic 6, ADR 0022.
- **Branch / worktree:** `feat/nemo-speech-pure-streaming` @ `.worktrees/nemo-speech-pure-streaming`
  (base `a6da1c7`, tip `eb7dfbd`).
- **Reviewer:** independent; authored none of this code. Only write is this file.
- **Range read:** three-dot `git diff main...HEAD`.

## Verdict

**pass.**

The central claim holds, mechanically falsified on every guard, and re-proven on the **real** engine
against the already-downloaded pinned archive + GGUF (no download, no sudo, no network, no mic). Two
risks are recorded (one of them the punctuation divergence — the project must own it), but neither
blocks this ticket's DoD.

## The central claim, and what would falsify it

**Claim (ADR 0022, report §9.1):** the NeMo-Speech.cpp cache-aware interim stream can be carried into
ges2t's committed-delta path with **no sliding window, no second pass, and no growing-buffer
re-decode**; the committed text is append-only and only the completed-word prefix is injected.

**What would falsify it (each is a real, runnable disproof):**

1. **A non-prefix interim.** The decoder's `_NemoSpeechDecoder.push` guard would raise
   `NemoSpeechAppendOnlyError`; the real-seam test's `committed.startswith(previous)` loop would fail.
   → *not observed* (guard falsified by mutation E; real-seam loop passes).
2. **A window flush / `finish()` more or less than once.** → *falsified*: mutation A (mid-loop flush)
   fails the driver test; the instrumented real engine reports `finishes=1`.
3. **A second pass over the audio (re-push / re-decode).** → *falsified*: instrumented real engine
   reports `pushes=32 == expected_groups=32`, `opens=1`, `closes=1`.
4. **A final that does not extend the committed prefix** (the engine's `punc=False` default does this).
   → *falsified*: mutation G drops `enable_automatic_punctuation` and the real-seam test fails.
5. **The engine call under the service lock.** → boundary files unchanged (`git diff --name-only`),
   the call site (`streaming_session.py:268`) is unmodified; not re-probed here (see could-not-verify).

## Per-criterion findings (Definition of done)

| # | DoD item | Verdict | Evidence |
|---|---|---|---|
| 1 | Additive `AppendOnlyDecoder` + `drive_append_only`; `drive`/`StreamingDecoder` keep signatures + behaviour | **pass** | AST compare: every pre-existing top-level def (`drive`, `_drive`, `StreamingDecoder`, `StreamingPass`, `_group_chunks`, `_target_samples`) is byte-identical to `main`; the *only* removed line in `src/ges2t/streaming.py` is the old `__all__`. |
| 2 | `_NemoSpeechDecoder` drives `stream_push_f32 → drain stream_next → stream_finish` → `StreamingPass` | **pass** | Code read + instrumented real probe: `opens=1 pushes=32 nexts=61 finishes=1 closes=1`, `finals=1`. |
| 3 | `stream()` uses `drive_append_only`; `supports_streaming = True` | **pass** | `nemo_speech.py:1118–1134`; `test_supports_streaming_is_true_and_it_is_a_transcriber`; `test_transcriber_satisfies_the_protocol_and_declares_streaming`. |
| 4 | Commit only completed-word prefix; partial token pending, never injected; committed append-only; `CommittedTyper` unchanged | **pass** | Mutations A/B/C each fail the specific test and revert cleanly; `incremental_typing.py` unchanged vs `main`. |
| 5 | Boundary is string-based (interims have no word timestamps) | **pass** | `_committed_prefix` uses `rstrip()` + `rfind(" ")`; `test_committed_prefix_is_the_last_completed_word`; report §10.3. |
| 6 | `streaming_session.py`, `service.py`, `socket_proto.py`, `incremental_typing.py` unchanged | **pass** | all four `UNCHANGED` vs `main` (plus `pipeline.py`). |
| 7 | New `tests/test_streaming_append_only.py` + `tests/test_nemo_speech_stream.py` | **pass** | 33 pass + 1 cleanly skipped; every guard falsified (table below). |
| 8 | `tests/test_whistle_stream.py` (incl. `test_drive_is_the_windowing_implementation`) still passes | **pass** | `1 passed`; full suite green. |
| 9 | `pytest` green, `mypy` 0, docs link check 0, README row updated | **pass** | `1374 passed, 3 skipped`; `Success: no issues found in 72 source files`; `0 broken references`; README row → `in review`. |

## Falsification matrix (break → watch fail → revert → hash)

Baseline: `streaming.py a4bbbc27…`, `nemo_speech.py 7cf31348…` (the comment's `6cbd1dab…` predates the
final `audio_processed`-drop commit; harmless, the guards are unaffected). After **every** mutation:
`git checkout --` + `rm -rf .mypy_cache __pycache__` + `sha256sum -c` → both `OK`.

| Mut. | Break | Test that failed (real output) |
|---|---|---|
| A | mid-loop `decoder.finish()` (a window flush) | `test_finish_is_called_exactly_once_at_the_very_end` → `AssertionError: assert ['push','finish',…] == ['push',…]` |
| B | `transcript += committed + pending` | `test_pending_never_reaches_the_transcript` → `assert False` (`POISON` in committed) |
| C | `_committed_prefix` returns the whole interim | `test_committed_prefix_is_the_last_completed_word` + `test_the_trailing_partial_word_is_held_pending_and_never_committed` → `assert 'And so my fel' == 'And so my'` |
| E | remove the append-only guard (`if False:`) | `test_the_decoder_raises_when_an_interim_revises_earlier_text` → Failed |
| F | remove the mid-stream-final guard | `test_a_mid_stream_final_is_rejected` → Failed |
| H | remove the missing-final guard | `test_a_finish_without_an_end_of_stream_final_is_rejected` → Failed |
| I | remove the abandoned-generator `close()` finally | `test_the_stream_is_closed_when_the_generator_is_abandoned` → `AssertionError` |
| G | `enable_automatic_punctuation = False` (real engine) | `test_real_streaming_engine_appends_without_a_window` → `AssertionError: … 'he hoped … potatoes'.startswith('He hoped … bruised')` False |

Every new guard is a test **seen to fail** (code-quality rule 6).

## Independent punctuation probe (the decision the project lives with)

I re-ran the raw C ABI myself (CPU, pinned v0.2.0 + GGUF, `clip5.wav`, `/tmp` only):

```
=== punc=False word_ts=False interims=27 finals=1 ===
  last interim : 'He hoped there would be stew for dinner, turnips and carrots and bruised po'
  final        : 'he hoped there would be stew for dinner turnips and carrots and bruised potatoes'
  final.startswith(last_interim)? False
=== punc=True word_ts=False interims=27 finals=1 ===
  last interim : 'He hoped there would be stew for dinner, turnips and carrots and bruised po'
  final        : 'He hoped there would be stew for dinner, turnips and carrots and bruised potatoes'
  final.startswith(last_interim)? True
=== punc=True word_ts=True interims=27 finals=1 ===
  (identical text to punc=True word_ts=False)
=== batch transcribe() (library default, punc off) ===
  'he hoped there would be stew for dinner turnips and carrots and bruised potatoes'
```

**Findings, judged independently:**

- `enable_automatic_punctuation=True` is **exactly the config ticket 55 measured under** — its harness
  `measure_nemo_streaming.py:401` sets `opts.enable_automatic_punctuation = True`. So it is the
  **measured** configuration, not a substitution (ADR 0020 does not apply).
- The flag **does not touch the interims** (the last interim is byte-identical on/off across 27
  interims). It only changes the **final**, restoring the append-only relationship. So it is not
  masking a "revision"; the append-only property is genuine.
- `enable_word_time_offsets` makes no text difference on this clip (both True/False identical), so
  leaving it off is sound.
- **The real cost, disclosed by the implementer and confirmed here:** the *batch* path
  (`transcribe()` → `recognize_f32` with the library default) returns **lowercased, unpunctuated** text,
  while `stream()` returns **capitalised, punctuated** text. That is a real `transcribe()`/`stream()`
  divergence for the same audio.

**DoD judgement:** item 4 requires *committed text is append-only and never revised*. That is met —
precisely **because** of the flag (without it, `CommittedTyper.finish` would raise
`IncrementalTypingError`). The DoD does not require `stream() == transcribe()`. So the DoD **is** met;
the divergence is a genuine design consequence that the epic must own (see Risks R1), not a hidden
defect in this ticket.

## Real-seam tests

- Default suite: `tests/test_nemo_speech_stream.py` → `19 passed, 1 skipped`, skip reason
  `the full real stream loads ~1 GB of weights; set GES2T_NEMO_REAL_SEAM=1` — **skips cleanly**.
- `GES2T_NEMO_REAL_SEAM=1 uv run pytest tests/test_nemo_speech.py tests/test_nemo_speech_stream.py -q`
  → `69 passed`. The streaming real-seam test drives the real `.so` on real speech, asserts the
  reference word (`dinner`), the append-only committed sequence, and that a trailing partial was held
  pending.
- The payload-reaches-the-library assertion is the hermetic `test_the_actual_audio_payload_reaches_the_library`
  (`fake.pushed` payload equality), matching ticket 58's F1 pattern; the real-seam half proves the
  *engine* consumed the audio by producing the right word. The fake models the real streaming
  lifecycle (open/push/next/finish/close, freed-handle refusal, NULL-when-empty) and is driven both
  ways — rule 7 satisfied.
- Extra (mine): an instrumented wrapper around the **real** library counted the ABI calls:
  `opens=1 finishes=1 closes=1 pushes=32 nexts=61 segments=33 finals=1 expected_groups=32`
  → `ALL REAL-ENGINE INVARIANTS HOLD`.

## Boundaries, guard-test change, hygiene

- `git diff --name-only main...HEAD` → only README, ticket artifacts, `streaming.py`, `nemo_speech.py`,
  and three test files. `streaming_session.py`, `service.py`, `socket_proto.py`,
  `incremental_typing.py`, `pipeline.py` are all **UNCHANGED**.
- **No `numpy`/`torch`/`onnxruntime`**: `grep` in `src/ges2t` → none; `pyproject.toml`/`uv.lock` untouched.
- `tests/test_whisper_backend_config.py::test_batch_only_engines_agree_with_the_transcriber_capability`
  was **not weakened** — the ticket-58 `nemo_speech` exclusion was *removed* and the assertion flipped
  from `declared[NEMO_SPEECH_ENGINE] is False` to `is True`; the guard now covers every engine with no
  exception. (Ticket 58's exact failure mode was not repeated.)
- No new waits/threads/timeouts in the streaming code (`grep` → none); the only unbounded join is the
  pre-existing session `worker.join()` (report §4.3.2, inherited).
- `uv run pytest -q` completed (not exit 124): no leaked non-daemon thread. `git status` clean.

## Risks and follow-ups

- **R1 (own it).** `transcribe()` returns lowercased/unpunctuated text; `stream()` returns capitalised/
  punctuated text. Recommend a follow-up on **ticket 60 (wiring)** (and/or 62 docs) to decide one
  spelling for the engine — either set `enable_automatic_punctuation=True` on the batch path too, or
  document the divergence as intentional. Not a ticket-59 blocker.
- **R2 (minor doc).** `001-comment.md:78` still lists `audio_processed_s` as a field of
  `NemoSpeechStreamResult`; the final commit removed it. The comment's "Final cleanup" section
  corrects this, but line 78 is stale. Non-blocking.
- **R3 (minor).** `drive_append_only` does not itself assert `final_text` extends the committed prefix;
  that is enforced one layer down by `CommittedTyper._extend` (loud `IncrementalTypingError`) and pinned
  for the real engine by the real-seam test. Acceptable; note it for the wiring ticket.
- **R4 (pre-existing).** The unbounded session `worker.join()` on `finish()` is inherited from ADR 0011,
  unchanged.

## Could not verify

- **Vulkan path.** I exercised CPU only (`device=CPU`), matching the implementer's measurement and the
  real-seam test. ADR 0022 already states CPU/Vulkan transcripts are *not* byte-identical, so the
  append-only property is per-device; I did not re-measure it on Vulkan.
- **Service-lock / threading end-to-end.** I confirmed the boundary files are unchanged and read the
  call site, but did not run a live capture session (no microphone — forbidden). The claim rests on
  `streaming_session.py:268` being unmodified.
- **`test_real_streaming_engine_appends_without_a_window` is one clip, one device.** Its `any(pending)`
  assertion could in principle be clip-sensitive; it passes here.
- **Other audio clips / Hungarian.** Not measured (ADR 0022 already records Hungarian as
  published-FLEURS only).

## What would falsify the central claim (for the record)

A single real-engine run whose interim is **not** a prefix extension (the guard raises, the real-seam
loop fails), or a `pushes` count exceeding the regrouped-chunk count (a second pass), or a final that
does not extend the committed prefix. None was observed across the hermetic tests, the instrumented
real probe, and the real-seam test.

## Safe to relay to the user?

**Yes — with the caveat stated.** The append-only pure-streaming mechanism is real and independently
verified on the actual engine; the additive claim is proven.
Do **not** relay it as a win on accuracy: ADR 0022's limits stand (weaker than whisper `medium` on
noisy real-device audio; Hungarian published-FLEURS only). And relay the `stream()` vs `transcribe()`
punctuation divergence as an open decision for ticket 60, not as a resolved one.

---

*Evidence commands run from `.worktrees/nemo-speech-pure-streaming`; all mutations reverted and
`src/` verified byte-identical (`sha256sum -c`); `git status` clean; no microphone, network, sudo, or
window touched.*
