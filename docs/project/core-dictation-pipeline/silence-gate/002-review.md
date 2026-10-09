# 002 — Review of the silence gate (ticket 29)

Reviewer pass on `feat/silence-gate` @ `a17e765`, diff `git diff main...feat/silence-gate`
(three dots). Real engine, synthesised buffers only; **no microphone, no keystrokes, no
injection, no network**. Probes in `/tmp` only. Tree clean, every probe reverted.

## Verdict

**The code is good. The premise correction is real and I reproduced it. The gate does
NOT fix the user's bug — the bug is probably merely narrowed, and I say so plainly below.
Do not merge this as "silence is fixed" and do not tick the ticket's DoD.**

- Implementation placement, ADRs, typing, tests, and mechanicals: all pass (details §3–§6).
- `001-comment.md` is unusually honest and its self-correction of the ticket's premise is
  correct: **all-zeros and low-level noise do NOT hallucinate**; DC, mains hum, and short
  transients do.
- The chosen threshold (0.0005 AC RMS) is biased so low that it cannot remove the user's
  dominant trigger (hotkey key clicks). The realistic silent toggle still hallucinates.
- The decisive evidence is the **user's own live re-test** (silence, double-tap, quiet
  speech), which is user-owned and has not happened. Nothing here should be called "fixed"
  until it passes.

## 1. Reproduction — the premise correction is real

Independent run of the **raw** engine (bypassing the gate) via the real
`WhistleTranscriber` against the managed weights (`load_seconds` 0.031 s). What the engine
actually returned:

| Input | Measured AC RMS | Engine text |
|---|---|---|
| all-zeros, 0.5 / 1 / 2 / 5 s | 0.000000 | `''` |
| uniform noise 1e-5…1e-2, 5 s | 6e-6 … 5.8e-3 | `''` |
| gaussian noise 1e-5…1e-3, 5 s | 1e-5 … 1e-3 | `''` |
| flat DC 0.0005 / 0.001 / 0.01 / 0.05 / 0.1, 2 s | 0.000000 | `'Thank you.'` (every level) |
| 50 Hz hum amp 0.01 / 0.05 / 0.1, 2 s | 0.0071 / 0.0354 / 0.0707 | `'Thank you.'` |
| one 5 ms click amp 0.02 @0.1 s in 0.5 s | 0.00141 | `'you'` |
| one 5 ms click amp 0.05 / 0.2 in 0.5 s | 0.00354 / 0.0141 | `'Thank you.'` |
| four clicks amp 0.3 in 0.3 s | 0.0548 | `'you'` |
| silence + click at both ends, 0.5 s | 0.0300 | `'you'` |
| silence + clicks both ends, 0.8 s | 0.0237 | `'Bye.'` |
| silence + clicks both ends, 1.2 s | 0.0194 | `'Thank you.'` |
| silence + clicks both ends, 2.0 s | 0.0150 | `'Thank you.'` |
| silence + clicks both ends, 3.0 s | 0.0122 | `''` |

**All-zeros and low-level white/gaussian noise do NOT hallucinate for me either.** The
implementer's correction is real and must stand. (One cosmetic difference: they recorded
`dc 0.0005 -> 'you'`; I got `'Thank you.'`. Both are hallucinations — conclusion unchanged.)

**Speech still works.** Every shipped clip transcribed correctly through the real engine:
`clip5.wav` AC RMS 0.053630, `clip30.wav` 0.056198, `2.wav` 0.027712 (quietest),
`3.wav` 0.047091, `cny16.wav` 0.046648, `four.wav` 0.091315, `jfk.wav` 0.142101 — all
correct, and all match the implementer's measured numbers. Nothing regenerated, nothing
committed.

## 2. RULING on the central question — the user's bug is NOT fixed, only narrowed

**Critical finding.** The RMS floor 0.0005 is ~7–60× below the AC RMS of a realistic
silent-toggle buffer that contains the hotkey's own key clicks, so the gate passes those
buffers and the engine still types `'Thank you.'`.

Measured through the **gated** adapter (batch path, real engine), silence + realistic
clicks at both ends:

- amp 0.05, 1.2 s → AC RMS **0.00323** (6.5× threshold) → `'Thank you.'`
- amp 0.02, 1.2 s → AC RMS **0.00129** (2.6×) → `'Thank you.'`
- amp 0.01, 2.0 s → AC RMS **0.00050** (at threshold) → `'Thank you.'`
- amp 0.005 → AC RMS 0.00032 (below) → `''` (the only case gated)
- one 5 ms click amp 0.05 in 1.2 s → AC RMS **0.00228** → `'Thank you.'`

**Arithmetic.** For the both-ends gesture the statistic scales linearly with click
amplitude: AC RMS at amp 1.0 is 0.064550, so to fall to 0.0005 the click peak must be
**0.0005 / 0.064550 ≈ 0.0077**. A real key click reaching a desk mic peaks far above that;
the implementer's own "realistic click (0.05–0.4)" is 6–50× too loud to gate. Independently,
50 Hz hum at amp 0.01 already measures 0.0071 (14× threshold) and hallucinates — and mean
removal does not touch it, because hum is **AC**, not DC. **The only realistic inputs the
gate removes are ones that never hallucinated (digital silence, sub-noise-floor hiss) plus
flat DC and sub-100 ms recordings.** The reported trigger — a ~1–3 s silent toggle whose
only content is key clicks and possibly hum/DC — passes the gate and still types
`'Thank you.'`.

The **streaming** path behaves the same (real engine, through `stream()`): five all-zero
1 s chunks → `''` (0.014 s wall = flush only, no per-chunk pass), but **1.2 s of silence
containing one 5 ms click → `'Thank you.'`**. It also preserves a genuine mid-sentence
pause (first half of clip5 + 2 s silence + second half transcribed with both halves
present). So the streaming decision is correct, but it does not save the user either.

**Recommendation.**
- **Blocking** for marking ticket 29 done and for any statement that the user's bug is
  fixed. The decisive evidence is the **user's live re-test (§8)** — silence, double-tap,
  quiet speech — which has not run. Given the numbers above, I expect the silence case to
  still type something.
- The change itself is still worth keeping as a **partial**: it correctly handles DC and
  sub-100 ms, never pays inference on digital silence, and is well tested. But it must not
  be described as the fix for the reported symptom.
- If (as expected) the live re-test fails, the correct next mechanism is the implementer's
  own suggestion, and it should be folded into this ticket / an immediate follow-up rather
  than deferred: a **frame-level / sustained-energy gate** (energy above threshold across a
  minimum fraction or run of consecutive frames, so an isolated transient or steady hum is
  not "speech") **and/or a DC-blocking high-pass** before the engine. Both act on the
  signal, so ADR 0012's verbatim-text rule is untouched.

## 3. Falsification of the gate and the threshold

- **Statistic is genuinely AC.** `_ac_rms` subtracts `math.fsum(samples)/count` before the
  sum of squares. Independent recomputation: DC 0.05 → `_ac_rms` 0.0 but raw RMS 0.05;
  50 Hz tone unaffected (0.0707 both). Mutation F (use raw RMS) fails the DC test, proving
  mean removal is exactly what makes DC safe. **Confirmed.**
- **Threshold and margin.** Real-engine clip measurements match the claim: quietest real
  clip 0.027712, margin **55.4×** (34.9 dB) below threshold — the implementer's "~55×"
  is right. Threshold −66.0 dBFS. The gate is below the engine's own quiet-speech floor
  (their gain ladder), so it cannot remove anything the engine transcribes *in the measured
  data* — though it is nowhere near high enough to remove clicks/hum (§2).
- **Duration gate tested in isolation.** `test_a_99ms_full_scale_burst_is_dropped_by_the_duration_gate`
  feeds a **full-scale** square wave, so the RMS gate passes and only duration decides.
  99 ms = 1584 samples < 1600 → dropped; 101 ms = 1616 ≥ 1600 → kept. Mutation C
  (MIN 0.05) and Mutation G (MIN 0.0, i.e. duration check removed) each fail exactly the
  99 ms tests in batch and streaming. **Confirmed.**
- **Composition is outcome-neutral.** The gates are `duration AND ac_rms`, duration first.
  Both are pure predicates over the same immutable buffer with no side effects, and both
  must hold; short-circuit order only decides whether the O(n) scan runs (an efficiency
  choice). Mutation D (swap the two checks) leaves **635 passed**. **Confirmed neutral.**
  The genuinely load-bearing ordering is *validation before the gate* (Mutation A: gate
  first breaks two `WhistleInputError` tests. Mutation E: gating before validation inside
  `_speech_only` breaks `test_engine_call_failure_does_not_leave_a_usable_stream`). The
  implementation gets that right.
- **Streaming.** Mid-sentence pause preserved (unit test + real-engine probe; transcript
  intact, not truncated). Fully silent utterance makes **no per-chunk engine pass**:
  `chunk_calls == 0` in the unit test, and 0.014 s wall with `''` on the real engine
  (one flush tail only). Mutation H (drop `_speech_only`) fails `silent_utterance` and
  `silent_chunk_between_phrases`. **Confirmed.**

## 4. Per-test verdict on the input change (§4 of the brief) — NO test was weakened

The one place a fix can hide a regression. Changed-input tests, individually:

| Test | Change | Verdict |
|---|---|---|
| `test_transcribe_hands_the_engine_raw_float32_bytes` | `[...]*400` (audible) | **Preserved, strengthened** — the same distinctive values ×400; still pins byte length and the float32 round trip. |
| `test_transcribe_passes_the_configured_language_through` | `[0.0]` → `_audio(0.2)` | **Preserved** — asserts language passthrough only. |
| `test_accepts_a_buffer_of_exactly_30_seconds` | `[0.0]*480000` → `[0.5,-0.5]*240000` | **Preserved** — still exactly 480000 samples; the boundary is what is tested. |
| `test_result_is_passed_through_unchanged` | `[0.0]` → `_audio(0.2)` | **Preserved** — full `TranscriptionResult` equality unchanged. |
| `test_silence_is_returned_as_an_empty_string` | `[0.0]` → `_audio(0.2)` | **No coverage lost, but the name is now false.** The original intent (a genuinely silent buffer reaching the engine and returning `''`) is now unreachable *by design* and is superseded by the stronger `test_silence_returns_empty_without_calling_the_engine` (which asserts no engine call). What remains is "engine-returned `''` is passed through", correctly asserted. This is a **naming defect, not a weakening**; rename to e.g. `test_an_empty_engine_text_is_returned_unchanged`. |
| `test_missing_timings_become_none` | `[0.0]` → `_audio(0.2)` | **Preserved** — missing fields → `None`. |
| `test_process_passes_raw_float32_bytes` (stream) | `[...]*400` | **Preserved, strengthened.** |
| `_chunk()` default (affects ~17 stream tests) | constant 0.0 → square wave 0.5 | **Preserved** — none of those tests asserted the chunk's DC-ness or silence value; they exercise window/commit mapping, language, pending, flush. The all-zero streaming input is now covered by the dedicated gate tests. |

**The key question — did changing the input quietly remove the "a degenerate buffer still
reaches the engine" coverage?** Only `test_silence_is_returned_as_an_empty_string` had a
degenerate input, and its purpose was result pass-through, not engine-call. The *new* tests
assert the replacement behaviour explicitly (engine **not** called). So the discarded
coverage is exactly the behaviour this ticket intends to change — intentional, not silent.
**Blocker on §4: none.**

Also note `test_engine_call_failure_does_not_leave_a_usable_stream` was deliberately *not*
changed (still feeds a silent 8 kHz `[0.0]`) and is what pins validate-before-gate in
`_speech_only` (Mutation E). Good.

## 5. My mutations (per-run `timeout 150 uv run pytest -q`, caches cleared, byte-identical restore)

| # | Mutation | Result |
|---|---|---|
| A | run the gate **before** `_validate_buffer` in `transcribe()` | **2 failed** (`not_16khz`, `longer_than_30_seconds`) |
| B | `SILENCE_RMS_THRESHOLD = 0.0` | **5 failed** (silence, low noise, DC, silent utterance, silent chunk) |
| C | `MIN_UTTERANCE_SECONDS = 0.05` | **2 failed** (99 ms batch + stream) |
| D | swap duration/RMS order inside `_has_speech` | **635 passed** — outcome-neutral, as claimed |
| E | validate **after** the gate in `_speech_only` | **1 failed** (`engine_call_failure...`) — validate-first is load-bearing |
| F | `_ac_rms` without mean removal (raw RMS) | **1 failed** (DC) — AC-ness is load-bearing |
| G | `MIN_UTTERANCE_SECONDS = 0.0` (duration check removed) | **2 failed** (99 ms batch + stream) |
| H | `stream()` passes `chunks`, not `_speech_only(chunks)` | **3 failed** (silent utterance, silent chunk, 99 ms stream) |

All caught except D, which is the deliberate order-neutrality check and *should* pass.
After every run: `find src tests -name __pycache__ -exec rm`, `rm -rf .mypy_cache`, `cp`
back the pristine `whistle.py`, `git diff --quiet` — sha256 restored to
`1ea5092…defa37`, tree clean.

## 6. Mechanicals

- `timeout 300 uv run pytest -q` → **635 passed in 3.88 s, exit 0**. Baseline 624 → **+11**,
  matching the 11 added `def test_` functions; `git diff` shows **0 removed test defs and 0
  removed assertions/skips**.
- `rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests` → **Success: no issues
  found in 44 source files, exit 0**.
- ADR 0019: all new functions annotated including `-> None` on test helpers;
  `_ac_rms(Sequence[float]) -> float`, `_has_speech(AudioBuffer) -> bool`,
  `_speech_only(Iterable[AudioBuffer]) -> Iterator[AudioBuffer]`; no new positional tuples.
- **No new dependency**: `pyproject.toml` / `uv.lock` untouched; no `numpy`/`gi` import in
  the adapter; `BANNED_MODULES` test still green.
- **Scope**: diff is only `src/ges2t/transcribers/whistle.py`, the two whistle test files,
  and the ticket's own docs (`ticket.md`, `001-comment.md`, `docs/project/README.md`).
  `pipeline.py`, injection backends and tray are **untouched**. **No audio file committed.**

## 7. What I could NOT verify

- **The user's live re-test — the decisive evidence — has not happened.** Silence,
  accidental double-tap, and quiet/mumbled speech are user-owned (§8 of `001-comment.md`).
  This review models the buffer; it cannot measure the user's actual mic.
- The user's real silent-toggle AC RMS and whether their buffer actually contains key
  clicks/hum. I modelled clicks at 0.005–0.4; I did not capture their mic (forbidden).
- The engine's non-deterministic behaviour near its floor (the reported −59/−65 dBFS
  flip-flop). I did not re-run the gain ladder exhaustively; my clip results were
  deterministic on this run.
- I did not re-run `main`'s 624-test baseline (would need a fresh environment/tree); the
  +11/−0 delta is verified structurally from the diff, not by executing `main`.
- Which path the user's install actually uses (batch vs streaming). Both were shown to
  hallucinate on a click inside silence, so the ruling holds either way.
- Full streaming E2E at non-default window/chunk sizes (I used the defaults).

## Bottom line for the orchestrator

Merge-worthy as a **partial input-validation improvement** (DC + duration, no inference on
digital silence), with the `test_silence_is_returned_as_an_empty_string` rename as a nit.
**Do not merge it as the fix for the user's bug**: the arithmetic and the real-engine probes
say a silent toggle containing a key click still types `'Thank you.'`, on both paths. Hold
the ticket open; require the user's live re-test; and plan the frame-level/sustained-energy
gate (plus DC-blocking high-pass) as the actual fix.
