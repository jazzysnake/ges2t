# 002 — Review of the speech-band recalibration (ticket 31)

Reviewer pass on `feat/speech-gate-recalibration` @ `38068c9`, diff
`git diff main...feat/speech-gate-recalibration`. Probes in `/tmp/probe31/` only;
**fresh real device captures** via the production `PwRecordAudioSource`, the
**real Cactus-Needle engine** for the call-count and cost measurements, fake
engines only for the test-suite mutations. Every mutation reverted
byte-identically (sha256 `bfdf1f83…a00bfbd7` before and after every probe;
tree clean apart from this file).

## Verdict

**The mechanism is sound and — for the first time in this ticket — the
calibration is genuinely grounded in real device audio, not synthetic signals.**
I reproduced the separation with my own fresh captures and it is **wider** than
the ticket claims. The analysis-only property holds and is falsified. No test was
weakened; the suite is green; scope is clean.

**But the quiet-speech cost is understated for the user's actual environment.**
The ticket's headline cost ("keeps gain ≥ 0.4 where the engine reads to 0.1") was
measured against a **0.043-RMS** LF floor. The floor I measured **today is
0.28–0.35 RMS** (the user reports being in a car with the window open; the
broadband 20–40 Hz drift is exactly wind/road rumble). With that floor the gate
keeps nothing below **gain ≈ 4.0 (speech RMS ≈ 0.21)** while the **engine still
produces a near-correct transcript at gain 0.5 (RMS 0.027)** — the gate is ~8×
more conservative than the engine, in RMS, for speech over the real noise floor.
That is the single most important number in this review.

**Recommendation:** the code is mergeable on its own evidence, but **do not merge
on the strength of the cost analysis as written** — it describes a floor 6–8×
quieter than the one currently present. The noisy-environment trade is real and
cannot be settled without the user's live test in both a quiet room and a noisy
one. Given the user reports silence is not a regular input and normal speech works
well, **pausing the merge pending that test is the right call.**

## 1. Measured separation — my own numbers

5 **fresh** real idle recordings via `PwRecordAudioSource` (default source,
volume 0.16), short and `/tmp`-only; plus the 7 shipped clips in
`/tmp/whinspect/`, scored with the branch's own functions. The shipped function
early-returns at 3, so I also report an uncapped run.

| input | dur s | AC RMS | run (uncapped) | **ratio > 200 Hz** | old gate | new gate |
|---|---|---|---|---|---|---|
| idle_0 | 0.084 (under floor) | 0.0189 | 3 | 0.305 | reject | reject |
| idle_1 | 0.169 | 0.3496 | 8 | 0.0031 | keep | **reject** |
| idle_2 | 0.169 | 0.2232 | 8 | 0.0056 | keep | **reject** |
| idle_3 | 1.470 | 0.2811 | 73 | 0.0061 | keep | **reject** |
| idle_4 | 2.473 | 0.2917 | 123 | 0.0042 | keep | **reject** |
| clip5 | 5.000 | 0.0536 | 44 | 0.798 | keep | keep |
| clip30 | 30.000 | 0.0562 | 328 | 0.879 | keep | keep |
| 2.wav | 3.275 | 0.0277 | 59 | 0.762 | keep | keep |
| 3.wav | 6.625 | 0.0471 | 63 | 0.813 | keep | keep |
| cny16 | 10.435 | 0.0466 | 51 | 0.827 | keep | keep |
| four | 6.576 | 0.0913 | 327 | 0.924 | keep | keep |
| jfk | 11.000 | 0.1421 | 547 | 0.980 | keep | keep |

**Idle (≥ 100 ms) 0.0031–0.0061; speech 0.762–0.980.** Against
`SPEECH_BAND_RATIO = 0.15`: **24.6× headroom below** (worst idle 0.0061) and
**5.1× above** (quietest clip 0.762). The claim is confirmed and, on the
rejection side, **more conservative than the ticket states** (it reported a
worst realistic idle of 0.036 → 4.2×). The threshold is not in danger from the
idle population.

Note: my idle RMS (0.22–0.35) is at/above the top of the ticket's measured
range (0.036–0.26) — consistent with the louder environment. The ratio is
unaffected by level, so this does not weaken the rejection.

## 2. Target cases — engine-call counted

Through the **real adapter and real engine**, wrapping `engine.transcribe` with a
counter (call count, never inferred from text):

- **5/5 fresh real idle captures rejected, engine calls 0 each.** The two 0.169 s
  captures are exactly the `start(); sleep(0.2); stop()` minimal case — both
  gated, 0 calls.
- **7/7 shipped clips reach the engine exactly once**, correct text.
- **Streaming composition**: `idle_3` split into 1 s chunks gives
  `_has_speech = [False, False]` and `list(_speech_only(chunks)) == []` — no chunk
  reaches the decoder.
- **Ticket-29 regressions**: 99 ms full-scale dropped (duration), 101 ms kept;
  isolated click, clicks at both ends (amp 0.02/0.05/0.4) and low-level 50 Hz hum
  all rejected; a 180 Hz sustained tone still passes. Direct probes: real idle +
  isolated click (start or end) still rejected (ratio ≤ 0.0073); click-only
  rejected by the sustained gate. The gates compose correctly.
- **The old failure mode, on real car idle**: calling the engine directly on the
  idle captures (which the *old* gate keeps) hallucinates `'you'`, `'you'`,
  `"I'm not going to do anything."`, `"¿Qué estás buscando?"` — i.e. the old gate
  types garbage on every silent toggle in this environment.

## 3. Why a steeper/higher high-pass alone was rejected — re-measured

I implemented the same RBJ second-order Butterworth high-pass at corners
150/200/250/300/400/600/800 Hz and counted the longest run of 20 ms frames whose
AC RMS clears `SPEECH_FRAME_RMS_THRESHOLD = 0.0015`:

| capture | 150 Hz | 200 | 300 | 400 | 800 Hz |
|---|---|---|---|---|---|
| idle_1 | 8 | 8 | 8 | 8 | 8 |
| idle_2 | 8 | 8 | 8 | 8 | 8 |
| idle_3 | 73 | 73 | 73 | 73 | 73 |
| idle_4 | 123 | 123 | 123 | 123 | 123 |

The run is **unchanged up to an 800 Hz corner** — the noise is broadband and loud
enough that every frame still clears the level floor. The implementer's claim
("12–122 frames at 300 Hz, 16 at 400 Hz") is **confirmed, and in my environment
understated**. Level filtering cannot separate these populations; the ratio (a
spectral cue) is the right choice.

## 4. Level-invariance — confirmed

The ratio is unchanged by overall gain, so it adds no loudness requirement of its
own:
- shipped clips at gains 4.0 / 1.0 / 0.1 / 0.01 / 0.001: clip5 `0.798` (all),
  2.wav `0.7619`, jfk `0.9796`, four `0.9239` — constant to 4 dp.
- a synthetic LF floor at amplitudes 0.04 / 0.08 / 0.15 / 0.3 all score
  `0.04412`.

## 5. Warm-up — a real necessity, with a honest residual

The first 20 ms is excluded and the second-order state is seeded from the first
sample. Probes:

- **Necessity**: with the warm-up removed, short spike-opening idle reads
  `0.446 / 0.443 / 0.383 / 0.229 / 0.122` at 0.08 / 0.10 / 0.12 / 0.20 / 0.50 s —
  i.e. **without it, a 0.1–0.2 s spike-opening idle would clear 0.15 and be sent
  to the engine**. With it, the same buffers read `0.042–0.054`.
- **A single-sample full-scale spike at sample 0 is fully absorbed**: forcing
  `samples[0] = 1.0` on real idle prefixes at 0.105–1.0 s changes the ratio not
  at all (0.0047–0.0072), because only the excluded first 20 ms is affected.
- **Residual**: my real 0.084 s capture `idle_0`, whose start is a sustained
  clipped burst (not a one-sample spike), still scores **0.305** with the warm-up
  (4.03 without). It is kept out solely by the **duration floor**. A real 0.105 s
  prefix scores 0.0066, so the residual looks confined to sub-100 ms captures and
  the duration gate independently covers them. The warm-up is genuine engineering,
  not a fudge, but it is not a complete guarantee and the duration floor does real
  work for the shortest bad case.

## 6. The noisy-environment trade — the headline

Offline-only: real car idle captures tiled to 5 s, added to `clip5` at a gain
ladder. `noise_rms ≈ 0.277` (0.350 for idle_1).

| speech gain | speech RMS | SNR | ratio | **new gate** | **engine (direct)** |
|---|---|---|---|---|---|
| 0.05 | 0.0027 | −40 dB | 0.006 | drop | not tested |
| 0.1 | 0.0054 | −34 dB | 0.007 | drop | `"Well, I'm working with you."` (wrong) |
| 0.3 | 0.016 | −25 dB | 0.009 | drop | `"…stew for dinner to turnips…"` (errors: *stupid dinner to turn up*) |
| 0.5 | 0.027 | −20 dB | 0.014 | drop | `"…stew for dinner to turnips and carrots…"` (one inserted word) |
| 1.0 | 0.054 | −14 dB | 0.035 | drop | `"…dinner, turnips and carrots and cruis…"` (near-correct) |
| 2.0 | 0.107 | −8 dB | 0.110 | drop | near-correct |
| 4.0 | 0.215 | −2 dB | 0.304 | **keep** | correct |
| 8.0 | 0.429 | +4 dB | 0.566 | keep | correct |

**The gate needs speech RMS ≥ ~0.47 × the noise RMS (≈ −6.5 dB SNR) to keep, so
the keep-boundary tracks the noise floor.** Against the ticket's own 0.043-RMS
floor I reproduced its table almost exactly (gain 0.4 → 0.193 keep; gain 0.3 →
0.136 drop) — that table is correct *for that floor*. But against the floor I
measure today (0.28–0.35 RMS) the keep-boundary moves to **gain ≈ 4.0**, while
the **engine returns a comprehensible, near-correct transcript on that same mix
at gain 0.5–2.0 (RMS 0.027–0.107)** and a recognisable attempt at 0.3. So in the
noisy environment the gate discards speech the engine can still read, by a factor
of ~8 in RMS.

**Which failure mode is worse for a noisy-place user?**
- *Old gate*: noise alone passes → the engine hallucinates and **types garbage on
  every silent toggle** (`"I'm not going to do anything."`, `"¿Qué estás buscando?"`).
- *New gate*: noise alone is rejected, but **quiet speech over the noise is also
  rejected** — nothing is typed, and the user has to repeat louder.

Typing garbage is arguably worse (it corrupts the user's text and must be
deleted), and the new gate is a clear improvement on the reported symptom. But
the user says they **sometimes speak quietly**, and in a car the crossover
(RMS ≈ 0.13 for a 0.28-RMS floor) is plausibly above a quiet speaker's level.
**Whether the user's real quiet speech clears it is not determinable from offline
data** — it depends on their mic distance and volume in the moment. Their live
test in a noisy environment is the decisive evidence, and the honest ruling is:
*the trade is real and quantified, but not settled.*

This is a **finding**, not a blocker: the ticket reports the cost as "gain ≥ 0.4"
without noting that this boundary is *relative to the noise floor* and moves with
it. The report should say the floor it used (0.043 RMS) and that a louder
environment raises the absolute speech level required — which is exactly the
user's situation.

## 7. The origin of the LF drift — reframed

The ticket correctly says the origin is unknown and requires no hardware change.
New information: the user is **in a car with the window open**, and my captures
(0.28–0.35 RMS, dominant energy 20–40 Hz, broadband, drifting) look exactly like
**wind buffeting / road rumble**. This is now the strongest candidate for the
"device idle" — i.e. the population may be **environmental noise, not a device
artefact**. It is **not proven** (I did not compare against a quiet-room capture
or a stationary car), and I did not reproduce the car environment deliberately.
The consequence is important: the *failure is environment-dependent*, and the
noisy environment is precisely where the user dictates. This should be recorded
in the ticket as a strong, unproven hypothesis.

## 8. Falsification

### Analysis-only property (ADR 0012)

The engine receives the original bytes; the filter is for the gate only. I broke
it deliberately on both paths:

| mutation | test | result |
|---|---|---|
| batch `transcribe()` sends `_speech_band_highpass(...)` to the engine | `test_transcribe_hands_the_engine_raw_float32_bytes` | **1 failed** |
| streaming `process()` feeds `_float32_bytes(_speech_band_highpass(...))` | `test_process_passes_raw_float32_bytes` | **1 failed** |

Both caught by the byte-for-byte tests. Restored, sha256 verified. The property
holds.

### My own three mutations (per-run `timeout 200`, caches cleared before and after, `git checkout` restore, sha256 verified)

| # | mutation | result |
|---|---|---|
| M1 | `SPEECH_BAND_RATIO = 0.0` (ratio gate can never reject) | **4 failed** — both batch idle tests, the separation test, the spike test, the streaming idle test |
| M2 | warm-up drops the **tail** instead of the head (`samples[: len - warmup]`) | **1 failed** — `test_a_short_capture_that_opens_on_a_spike_is_still_not_speech` |
| M3 | `SPEECH_BAND_HZ = 8000` (corner above the speech band) | **25 failed** across both files |

The new tests pin the guarded behaviour. I did not re-run the implementer's other
five mutations (ratio ignored, 25 Hz cutoff, threshold 0.99, level-dependent
band), but the two analysis-only ones I re-proved are the load-bearing ADR-0012
cases.

## 9. Mechanicals, scope, framing

- `timeout 300 uv run pytest -q` → **773 passed in 4.77 s, exit 0**. The ticket's
  stated `main` baseline is 767; the diff adds exactly **6** test functions, so
  767 + 6 = 773 is consistent. (I did not re-run `main` in a second worktree to
  avoid touching the tree.)
- `rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests` → **Success:
  no issues found in 49 source files, exit 0**. ADR 0019 annotations present on
  the new helpers (`-> AudioBuffer`, `-> None`); the new filter functions are
  fully annotated.
- **No test deleted, skipped or weakened**: `git diff main...HEAD -- tests/`
  removes **0 assertions**, adds **0 skips/xfails**, and the only removed lines
  are two stale comment lines ("1.5× the committed `SPEECH_FRAME_RMS_THRESHOLD`
  (0.002)") corrected to 2× / 0.0015. 6 test functions added.
- **Scope**: exactly `src/ges2t/transcribers/whistle.py`, the two whistle test
  files, `docs/project/README.md`, and the ticket's docs. `pipeline.py`,
  injection backends, tray untouched.
- **No audio committed**, `/tmp`-only; **no new dependency** (stdlib `math`
  only); `pyproject.toml`/`uv.lock` untouched; no `numpy`.
- **Framing**: `001-comment.md` explicitly disclaims "silence is solved"; the LF
  origin is stated as unknown; no hardware change is required of the user. That
  framing is honest, with the one gap in §6 (the cost table does not say the
  boundary is noise-relative).

## 10. What I could not verify

- **The user's live test — the decisive evidence — has not happened** (silence,
  double-tap, quiet speech, normal dictation, in **both** a quiet room and a
  noisy one). This review models the buffer; it cannot measure the user's
  microphone, their mic distance, or their real speech level over the real noise.
- **The user's own 22 idle captures / their `idle_7` at 0.043 RMS.** I used my own
  5 fresh captures plus a synthetic 0.043-RMS floor. My fresh idle is 6–8× louder
  than the floor in the ticket's cost table, which is why the cost differs.
- **The physical origin of the LF drift.** Wind/road rumble is a strong candidate
  given the car-with-window-open report; it is not proven.
- **Whether real (not clip+idle) quiet speech over the car floor behaves like this
  synthetic mix.** `clip5` is a clean studio clip; a real recording carries its
  own noise and the engine's behaviour may differ. The engine results at each gain
  were a single run each — ticket 29 noted possible engine non-determinism near
  the floor, so I do not over-claim determinism here.
- **The implementer's other five mutations** (I re-ran M1–M3 plus the two
  analysis-only probes, not all eight).
- Full streaming end-to-end at non-default window/chunk sizes (out of scope).

## Bottom line for the orchestrator

- **The calibration is real, not constructed.** I independently measured the
  separation on fresh device audio: idle 0.003–0.006 vs speech 0.762–0.980
  against a 0.15 threshold — 24.6× / 5.1× margins. Idle is gated with **zero
  engine calls**, the minimal case is gated, ticket-29 cases hold, the
  analysis-only property is falsified-and-true, and no test was weakened.
- **The one thing to fix before any merge decision: the cost story is calibrated
  to a 0.043-RMS floor, but the user's current floor is 0.28–0.35 RMS.** In that
  environment the gate requires speech RMS ≥ ~0.21 to keep while the engine still
  returns near-correct text at ~0.027. The trade is defensible but must be stated as
  noise-relative, and the noisy-environment case needs the user's live test.
- **The user has asked to pause the merge**; that is consistent with the evidence
  — the benefit (silence) is not a regular input for them, and the cost may bite
  in exactly the environment they dictate in. Do not merge as "silence solved",
  do not tick the DoD, and record the wind/road-rumble hypothesis.
