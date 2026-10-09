# 003 — Re-review of the sustained-energy silence gate (ticket 29)

Second reviewer pass on `feat/silence-gate` @ `55e8014` (rebuilt gate `d2dfb9c`,
streaming-test fix `c4b436d`, addendum `55e8014`), diff `git diff main...feat/silence-gate`.
Real adapter and real engine throughout; **engine calls counted** (a wrapper on the engine's
own `transcribe`, and on `_WhistleStreamingDecoder.process` for per-chunk streaming passes),
never inferred from the returned text. Buffers synthesised; **no microphone, no keystrokes, no
injection, no network, no `ges2t run`**. Probes in `/tmp/probe29/` only; every mutation
reverted byte-identically (sha256 checked), tree clean.

## Verdict

**The new frame-level sustained-energy gate genuinely fixes the reported symptom for the
modelled realistic toggle, on both the batch and streaming paths, with zero engine calls. It
does not regress the quiet-speech floor, the high-pass is genuinely analysis-only, and the
test suite is sound. I recommend merging the code.** What must not be claimed is *"silence is
solved"* — I agree with the implementer's own framing there.

Four documentation claims in the addendum are **not reproducible** and must be corrected
before the ticket is called done (none is a code regression, all are evidence accuracy):

1. **The engine-floor justification is false.** The addendum/`001` §2 says the engine "gives
   up" at −59/−65 dBFS so the gate "cannot remove anything Whistle itself would transcribe".
   Under the same gain-ladder method, the real engine transcribed `clip5` correctly and
   **deterministically** down to −99 dBFS and `jfk` at −79 dBFS. The gate therefore *does*
   drop audio the engine transcribes below gain 0.01. The floor is unchanged from the previous
   gate (so this is not a regression of this change), and the gain ladder preserves SNR so it
   does not simulate a real quiet speaker — but the stated justification for the floor is
   wrong, and the expensive-error cost is understated.
2. **Hum passes at ≈0.007, not 0.01.** `0.005` is gated; `0.007` (AC RMS 0.00495, ≈ −46 dBFS)
   already calls the engine and types `Thank you.` The advertised boundary is optimistic.
3. **A 40 ms transient already passes** at 180–500 Hz and for a decaying click envelope; the
   boundary is frequency/phase dependent because the filter's edge transient titrates a frame.
   The advertised "≈50–60 ms" margin is optimistic (real key clicks are 5–30 ms, so the
   practical margin survives).
4. **A5's "200 ms windows from clip5" are inflated ≈5×**, and its texts do not reproduce. All
   eight genuine windows do pass the gate (8/8 CALL), so the conclusion holds, but the
   evidence numbers are wrong.

Also **not reproduced**: the "engine floor is non-deterministic near −65 dBFS" caveat. Across
30 fresh processes (10 each at gain 0.02 / 0.01 / 0.005) and 20 in-process repeats, every call
returned the correct transcript. The gate's *decision* is deterministic (I confirm that half);
the engine wobble was not observable here.

## 1. The four realistic toggles are genuinely gated (engine-call counted)

Through the **real adapter and real engine**, synthesised `silence + clicks` (5 ms decaying
alternating clicks, the same generator as the shipped tests), batch `transcribe()` and
streaming `stream()`, with the engine calls counted:

| Input | before (`395a398`, whole-buffer) | now batch | engine calls (batch) | now streaming | engine passes (stream) |
|---|---|---|---|---|---|
| clicks both ends, amp 0.05, 1.2 s | `'Thank you.'` | `''` | **0** | `''` | **0** |
| clicks both ends, amp 0.02, 1.2 s | `'Thank you.'` | `''` | **0** | `''` | **0** |
| one 5 ms click, amp 0.05, 1.2 s | `'Thank you.'` | `''` | **0** | `''` | **0** |
| clicks both ends, amp 0.01, 2.0 s | `''` (was *below* 0.0005 already) | `''` | **0** | `''` | **0** |

The "before" column is my own run of the `395a398` module (loaded from git, sharing the loaded
engine): the first three called the engine and returned `'Thank you.'`; the fourth (AC RMS
0.000294) was already gated by the old whole-buffer floor, matching the addendum's `0.000294`
(so it is not evidence of improvement — the first three are). My measured whole-buffer AC RMS
matches the addendum exactly: 0.001896 / 0.000758 / 0.001341 / 0.000294, with per-frame runs
1 / 1 / 0 / 1. **All four return empty with zero engine calls on both paths. Claim confirmed.**

## 2. The expensive error — the quiet-speech floor has NOT moved

Methodology reproduced: `clip5.wav` scaled by a gain, through the **real adapter**. "Before" =
the `395a398` whole-buffer gate (AC RMS ≥ 0.0005); "after" = the sustained-energy gate. The
old gate is a pure predicate, so the old module's `_has_speech` reproduces its decision
exactly, sharing the loaded engine.

| gain | AC RMS | dBFS | old gate | new gate | engine text |
|---|---|---|---|---|---|
| 1.0 | 0.0536301 | −25.4 | keep | keep | correct |
| 0.1 | 0.0053630 | −45.4 | keep | keep | correct |
| 0.05 | 0.0026815 | −51.4 | keep | keep | correct |
| 0.02 | 0.0010726 | −59.4 | keep | keep | correct |
| 0.015 | 0.0008045 | −61.9 | keep | keep | correct |
| **0.01** | **0.0005363** | **−65.4** | **keep** | **keep** | **correct** |
| **0.005** | **0.0002682** | **−71.4** | **drop** | **drop** | not called |
| 0.002 | 0.0001073 | −79.4 | drop | drop | not called |

**Quietest gain that still transcribes: 0.01 (AC RMS 0.0005363, −65.4 dBFS) both before and
after; drops from gain 0.005 (0.0002682). The floor is unchanged. Claim confirmed.**

Two qualifications the orchestrator must see:

- Full (uncapped) active-frame run at gain 0.01 is **4** vs the 3-frame floor — one frame of
  margin, as claimed. At gain 0.015 it is 6. (`_longest_active_run` early-returns at 3, so the
  shipped function cannot itself report "4"; the margin is real but only visible to an uncapped
  computation. The addendum's "at 0.02 it is 6" does not reproduce — I measure 10 — a cosmetic
  evidence error.)
- **The engine itself transcribes far below the gate.** Calling the engine directly with the
  exact bytes the adapter would pass (bypassing only the gate), `clip5` transcribes correctly
  at gain 0.005, 0.003, 0.002, 0.001, 0.0005, 0.0002 (down to −99 dBFS), and `jfk` at 0.002, in
  fresh isolated processes. So the gate is not merely "rejecting what the engine would reject";
  it is a real speech-dropping threshold. The gain ladder preserves the clip's SNR, so this
  overstates the engine's ability on a *real* quiet talker with a fixed mic noise floor — but
  it does falsify the documented claim that the engine "gives up" at −59/−65 dBFS, which is the
  stated reason the floor is safe. The honest statement is: the floor is a genuine heuristic
  limit (unchanged by this change), **not** a level below which the engine has nothing to say.

All **seven** shipped clips still transcribe through the gate, engine called once each:
`clip5` (0.053630), `clip30` (0.056198), `2.wav` (0.027712), `3.wav` (0.047091), `cny16`
(0.046648), `four` (0.091315), `jfk` (0.142101) — texts correct. Nothing regenerated,
nothing committed.

**Short words.** Eight genuine 200 ms windows of `clip5` at 500–4000 ms all pass the gate
(8/8 engine calls, run ≥ 3). Note: the addendum's listed AC RMS values are exactly ≈5× mine
(e.g. it lists 0.074628 where the true window measures 0.014926), and its listed texts
(`Here.`, `Study.`, …) do not reproduce — mine are mostly `Thank you.` for these 200 ms
fragments. The pass/fail conclusion is unaffected, but the evidence is inaccurate.

**Boundary probe.** The run boundary is frequency- and phase-dependent, not the clean
"40 ms dropped / 60 ms kept" of the addendum. The shipped test's exact input (1000 Hz,
zero-phase cut) gives 40 ms → run 2 (dropped), 45 ms → run 3 (kept) — the test is right for
that input. But a 40 ms burst at 180/250/500 Hz gives **run 3 (kept)**, and a 40 ms decaying
click gives run 3. So the real transient boundary is ≈40 ms for voiced-band and click-shaped
signals, not 50–60 ms.

## 3. The high-pass is genuinely analysis-only — verified from the code path

Traced end to end. `_has_speech(buffer)` calls `_longest_active_run(buffer.samples, …)`, which
calls `_highpass(samples, sample_rate)`; `_highpass` **builds and returns a new `list[float]`**
and never assigns into `samples`. `_ac_rms` and the frame slices only read. The engine call is:

- batch: `engine.transcribe(_float32_bytes(buffer.samples), language=…)` — the **original**
  `buffer.samples`;
- streaming: `_speech_only(chunks)` yields the **original** `buffer` objects (`drive` passes
  them to `_WhistleStreamingDecoder.process`, which does `_float32_bytes(buffer.samples)`).

No filtered value can reach `_float32_bytes`; the filter's output is confined to the local
`filtered` list inside `_longest_active_run`.

**Falsification.** I mutated `transcribe()` so the engine receives
`_float32_bytes(_highpass(buffer.samples, buffer.sample_rate))` (the filter colouring the
engine's copy). `test_transcribe_hands_the_engine_raw_float32_bytes` **failed**
(`−0.26235976815223694` vs `−0.25`), confirming the byte-for-byte test would catch a leak.
Reverted byte-identically. The design claim holds.

## 4. Ruling on the honest-limits list

Verified against the real engine:

- **Hum partially fixed — as stated, but the number is optimistic.** `0.005` gated (`run 0`,
  text `''`); `0.01`, `0.02`, `0.05` call and hallucinate `Thank you.` — and so does `0.007`
  (AC RMS 0.00495, ≈ −46 dBFS), which is **below** the advertised 0.01 boundary. Raising the
  frame floor to 0.0025 would drop gain-0.015 speech (full run 2 at 0.0025, 4 at 0.002 —
  verified), which the engine still transcribes — so the implementer's decision not to raise
  it is correct. The remaining trigger is real; the boundary should say ≈0.007.
- **A sustained transient passes — as stated, but shorter than advertised.** A 40 ms
  voiced-band (180–500 Hz) burst and a 40 ms decaying click reach run 3; 5–30 ms clicks reach
  1–2. The margin exists but is 1 frame, not 2.
- **Margins are 1–2 frames — confirmed.** 5 ms click run 1 vs floor 3; gain-0.01 clip5 run 4
  vs 3; the filter costs 0 frames at gain 0.01 and 1 frame at gain 0.015 (verified with an
  identity-high-pass counterfactual).
- **"Silence is solved" would be an overclaim.** I agree, and the addendum already says so.
  The gate removes the modelled key-click toggle, DC, sub-100 ms, and *low-level* hum; it does
  not remove ≥0.007 hum, ≥40 ms transients, or a quiet speaker below −65 dBFS per-frame.

**Ruling: acceptable and honestly framed in direction; not blocking for the code. The four
evidence inaccuracies above should be corrected in `001-comment.md`/the addendum before the
ticket is marked done, and the ticket's own DoD (user live test) stays unticked.**

## 5. Falsification of the gate and the streaming-test re-check

Six mutations applied (per-run `timeout 150 uv run pytest -q`), caches cleared
(`__pycache__` under `src`/`tests` **and** `.mypy_cache`, `.pytest_cache`) before every run and
after every restore; `sha256sum -c` confirmed the pristine file
(`53f9bed9…89209b`) after each; `git diff --quiet` clean at the end.

| # | Mutation | Result |
|---|---|---|
| 1 | remove the sustained check (`_has_speech` returns `True` after duration) | **11 failed** (isolated click, both-ends, high-amp click, hum, run-below-min, 3 streaming) |
| 2 | `MIN_SPEECH_FRAMES = 1` | **6 failed** (DC offset, isolated click, both-ends, clicks-never-form-a-run, run-below-min, click chunk) |
| 3 | identity high-pass (remove the hum filter) | **1 failed** (`test_low_level_mains_hum_is_treated_as_silence`) |
| 4 | `SPEECH_FRAME_RMS_THRESHOLD = 0.05` | **6 failed**, incl. **`test_a_sustained_stream_chunk_passes_the_sustained_gate`** |
| 5 | `stream()` passes `chunks`, not `_speech_only(chunks)` | **4 failed** (silent utterance, silent chunk, 99 ms stream, click chunk) |
| 6 | filter colours the engine's copy | **1 failed** (`hands_the_engine_raw_float32_bytes`) |

**Streaming fix re-check (mutation 4).** The implementer changed
`test_a_sustained_stream_chunk_passes_the_sustained_gate` to feed a quiet 0.003 square wave
just above the 0.0015 floor. Re-applying the 0.05 frame floor **does** fail that test (along
with five batch tests). The fix is genuine: the streaming sustained test now pins the frame
floor instead of passing on a full-scale chunk.

**Naming defect fixed.** `test_silence_is_returned_as_an_empty_string` is gone; the replacement
`test_an_empty_engine_text_is_returned_unchanged` feeds audible `_audio(0.2)` and asserts the
engine's own `""` crosses unchanged. The name now matches what it does.

**No test weakened.** `git diff main...HEAD -- tests/` shows **0 removed `assert` lines**,
**0 added skips/xfails**, 1 removed `def test` (the rename) and 21 added. All round-1 input
changes (all-zero/sub-100 ms → audible `_audio`/`_chunk`) remain, and every assertion in those
tests is unchanged; 002's purpose-preservation audit still holds. New this round: the
just-above-floor test's input changed from `_audio(0.2, value=0.00075)` to
`_tone(0.2, amplitude=0.003·√2)`, consistent with the new criterion, assertion unchanged.
**One cosmetic defect:** that test's comment still says "1.5× the committed
`SPEECH_FRAME_RMS_THRESHOLD` (0.002)" — the constant is 0.0015 and `0.003` is 2×. The
assertion (`SPEECH_FRAME_RMS_THRESHOLD < frame_rms`) is correct; only the comment is stale.

## 6. Mechanicals, scope, ADR 0019

- `timeout 300 uv run pytest -q` → **644 passed in 2.83 s, exit 0** (branch baseline 624; +20).
- `rm -rf .mypy_cache __pycache__ && timeout 300 uv run mypy src/ges2t tests` → **Success: no
  issues found in 44 source files, exit 0**.
- Scope: only `src/ges2t/transcribers/whistle.py`, `tests/test_whistle_batch.py`,
  `tests/test_whistle_stream.py`, and the ticket's docs (`ticket.md`, `001-comment.md`,
  `002-review.md`, `docs/project/README.md`). `pipeline.py`, injection backends and the tray are
  **untouched**. **No audio/binary committed.** **No new dependency** (`pyproject.toml`/
  `uv.lock` untouched; the only new import is stdlib `math`).
- ADR 0019: `_highpass(samples: Sequence[float], sample_rate: int) -> list[float]`,
  `_longest_active_run(...) -> int`, `_has_speech(buffer: AudioBuffer) -> bool`,
  `_speech_only(Iterable[AudioBuffer]) -> Iterator[AudioBuffer]`; new test helpers annotated
  including `-> None`; no new dataclasses or positional tuples. Passes `mypy` under
  `disallow_untyped_defs`.

## What I could NOT verify

- **The user's live re-test — the decisive evidence — has not happened.** Toggle-on/off
  without speaking, accidental double-tap, and quiet/mumbled speech are user-owned (§A9). This
  review models the buffer; it cannot measure the user's actual microphone.
- The user's real silent-toggle AC RMS, their hotkey click amplitude/duration, and whether
  their buffer contains key clicks at all. I modelled clicks at 0.01–0.4 and 5–80 ms.
- Whether a *real* quiet speaker (fixed mic noise floor, not a scaled clean clip) is
  transcribable below the gate. The gain ladder preserves SNR, so it cannot answer this; it is
  the user's live counter-test that will.
- The engine's reported non-determinism near −65 dBFS: I could not reproduce it (30 fresh
  processes, all deterministic success). If the user's live quiet speech returns nothing while
  my scaled clip transcribes, that would be the real evidence that the floor is unsafe.
- Full streaming E2E at non-default window/chunk sizes (I used the defaults; the per-chunk
  split-word limitation in A8(7) is untested here).

## Bottom line for the orchestrator

**Merge the code**: the reported key-click toggle is genuinely gated on both paths with zero
engine calls, the quiet-speech floor is unchanged, the high-pass cannot colour a transcript,
the streaming test fix is real, and no test was weakened. **Do not merge it as "silence is
solved"** and do not tick the ticket's DoD: the user's live test is outstanding, and four
addendum numbers/claims (engine floor, hum ≥0.01, transient ≥60 ms, A5's 5×-inflated windows)
are inaccurate and should be corrected first. The engine-floor correction matters most because
it is the stated justification for the gate's safety, and it is wrong: the gate drops audio the
engine demonstrably transcribes, so the quiet-speaker risk is real rather than theoretical.
