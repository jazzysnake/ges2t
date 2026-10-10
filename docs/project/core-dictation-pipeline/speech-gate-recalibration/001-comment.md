# 001 — Recalibrating the speech gate on the real device's idle audio

Ticket: `ticket.md` (`speech-gate-recalibration`, ticket 31) · Branch:
`feat/speech-gate-recalibration` · Worktree:
`.worktrees/speech-gate-recalibration` · Status: **in review**

## What changed

`src/ges2t/transcribers/whistle.py` only. `_has_speech` now requires **three**
conditions, all input-side and all on the gate's analysis copy (ADR 0012):

1. duration ≥ 100 ms (unchanged);
2. a run of ≥ `MIN_SPEECH_FRAMES` (3) consecutive 20 ms frames whose AC RMS
   clears `SPEECH_FRAME_RMS_THRESHOLD` (0.0015) after the existing first-order
   150 Hz high-pass (unchanged);
3. **new** — the buffer's AC energy above `SPEECH_BAND_HZ` (200 Hz, a
   second-order Butterworth high-pass) is at least `SPEECH_BAND_RATIO` (0.15)
   of its total AC energy.

The engine still receives the **original bytes**; the new filter is only used to
compute the ratio. `tests/test_whistle_batch.py` and
`tests/test_whistle_stream.py` add six gate tests (one synthetic device-idle
model per path, the separation criterion, gain-invariance, the quiet-band floor,
and a short capture that opens on a spike).

## The measurement — real data, not synthetic

The previous fix was calibrated on digital silence, synthesised clicks and a
50 Hz tone. This round captured the **real** device first, with the production
`PwRecordAudioSource` (`alsa_input … ALC287`, source volume 0.16), all audio in
`/tmp`, never committed:

* **idle** — 22 real "toggle on, stay silent, toggle off" recordings: 8 saved
  (0.084–2.452 s) plus 8 more (0.169–1.470 s) plus 6 fresh minimal toggles
  (`start(); sleep(0.2); stop()`, 0.084–0.169 s);
* **speech** — the 7 shipped clips in `/tmp/whinspect/`: `clip5`, `clip30`,
  `2.wav`, `3.wav`, `cny16`, `four`, `jfk`.

| Population | count | AC RMS | per-frame run (150 Hz 1st-order) | **energy fraction > 200 Hz** |
|---|---|---|---|---|
| real idle | 21 (≥100 ms) | 0.022–0.20 | 3–123 | **0.001–0.065** (≥100 ms max **0.036**) |
| the 8 saved captures | 8 | 0.022–0.20 | 1–123 | **0.002–0.063** |
| the 6 fresh toggles | 6 | 0.049–0.15 | 1–3 (early return) | **0.003–0.065** |
| shipped speech clips | 7 | 0.028–0.14 | ≥3 | **0.762–0.980** |

*Spectral detail.* The idle signal is a **broadband low-frequency rumble that
drifts**: a 0.5 s window of the 2.45 s capture peaks at 33–37 Hz, and a 2 s
spectrum puts 42 % of its power at 30–40 Hz, 15 % at 20–30 Hz, 13 % at 70–80 Hz
— almost nothing above 150 Hz. Speech lives at 300–3000 Hz (58–91 % of clip
energy). The 50 Hz hum is 0.001–0.008, **not** the cause.

*Why the old gate passed it.* The level gate is doing exactly what it was
designed to do — the idle **is** sustained energy. The failure is spectral, not
level.

*Why a steeper/higher high-pass alone was rejected.* A second-order 200 Hz
high-pass leaves 3–123 active frames for idle (threshold 0.0015); even a
400 Hz second-order corner left 16 consecutive frames on the 2.45 s capture.
Raising the level threshold high enough to reject the idle (≈0.025) would drop
every quiet-speech frame (the quietest clip frames are ≈0.0003). Level cannot
separate the populations; the **ratio is the discriminating cue**.

## The chosen criterion and its margin

`SPEECH_BAND_RATIO = 0.15` on the energy above `SPEECH_BAND_HZ = 200 Hz`
(second-order Butterworth, state seeded from the first sample; the first 20 ms
excluded from numerator and denominator to drop the filter's start-up ringing
on short, spike-opening captures).

* idle margin: worst real idle scores 0.065 → **2.3× below** the threshold; the
  realistic (≥100 ms) maximum is 0.036 → **4.2× below**;
* speech margin: the quietest shipped clip scores 0.762 → **5.1× above**;
* 200 Hz (not 250–300 Hz) is chosen so a voiced fundamental near 180–200 Hz
  keeps margin too: a pure 180 Hz tone scores 0.39, a pure 200 Hz tone 0.50;
* the criterion is **level-invariant**: scaling a clip does not change its
  ratio (verified at gains 1.0 and 0.02), so it adds no level requirement of its
  own.

## Quiet speech — the expensive error — before vs after

Clean `clip5` through the **real engine** and the **real gate** (gain ladder,
the ticket-29 methodology; "transcribes" = engine called and text correct):

| gain | AC RMS | run | ratio | before | after | engine text |
|---|---|---|---|---|---|---|
| 1.0 | 0.0536 | 3 | 0.798 | keep | keep | correct |
| 0.05 | 0.00268 | 3 | 0.798 | keep | keep | correct |
| 0.015 | 0.00080 | 3 | 0.798 | keep | keep | correct |
| **0.01** | 0.000536 | 3 | 0.798 | **keep** | **keep** | **correct** |
| 0.009 | 0.000483 | 0 | 0.798 | drop | drop | correct |
| 0.005 | 0.000268 | 0 | 0.798 | drop | drop | correct |

**Quietest clean gain that still transcribes: 0.01 before and 0.01 after — the
floor does not move.** The engine itself still transcribes at gain 0.002
(0.000107), so the floor is the gate's pre-existing level threshold, not the
engine's. All 7 shipped clips still reach the engine exactly once and transcribe
correctly.

**The real cost, stated plainly.** When the *actual idle floor* is added to a
quiet speech segment (real `idle_7` + `clip5`, real engine), the new gate is
more conservative than the engine:

| speech gain in the mix | ratio | after gate | engine text |
|---|---|---|---|
| 1.0 | 0.506 | keep | correct |
| 0.5 | 0.252 | keep | correct |
| **0.4** | 0.186 | **keep** | correct |
| 0.3 | 0.121 | **drop** | correct |
| 0.1 | 0.027 | drop | correct |
| 0.05 | 0.017 | drop | degraded |

The current (level-only) gate keeps every one of these. So this change **is a
real narrowing for speech quieter than the device's own LF floor** — roughly
below −6 dB SNR against a 0.043-RMS rumble. Normal-volume speech (the clips are
0.028–0.14 RMS) is 5–20× that floor and is unaffected. This is the honest price
of stopping the hallucination; the user should be told, and it is the first
thing to reconsider if their live quiet-speech test regresses.

## The target cases — engine-call counted

Through the **real adapter**, with a counting wrapper on the real engine's
`transcribe`:

* the 8 saved real idle captures and the 6 fresh minimal toggles: **14/14
  rejected, 0 engine calls** (asserted by call count, not just empty text);
* the 7 real clips: **7/7 reach the engine exactly once**, correct text;
* the three idle regression tests pin the same behaviour with a fake engine and
  a synthetic idle model whose ratio (~0.05) and sustained run (≥3) match the
  measured population.

## Falsification

Mutations applied to `src/ges2t/transcribers/whistle.py`, each followed by a
full `pytest` of the two gate test files with `__pycache__`, `.pytest_cache`
and `.mypy_cache` cleared before and after, and `git checkout` to restore
(sha256 `7631626d…5f97e5` verified after every probe, tree clean):

| # | mutation | result |
|---|---|---|
| A | `_has_speech` ignores the ratio (`return True`) | 3 failed — the 2 batch idle tests + the streaming idle test |
| B | band = total (identity filter, ratio ≡ 1) | 4 failed |
| C | `SPEECH_BAND_HZ = 25` (below the idle) | 4 failed |
| D | return absolute band RMS, not the ratio | 9 failed (incl. gain-invariance + quiet-band tests) |
| E | `SPEECH_BAND_WARMUP_SECONDS = 0` | 1 failed — the spike-opening capture test |
| F | `SPEECH_BAND_RATIO = 0.99` | 3 failed |
| G | batch `transcribe()` sends the filtered copy to the engine | 1 failed — `test_transcribe_hands_the_engine_raw_float32_bytes` |
| H | streaming decoder sends the filtered copy | 1 failed — `test_process_passes_raw_float32_bytes` |

G and H are the ADR-0012 analysis-only proof: colouring the engine's copy fails
the byte-for-byte tests, so the filter cannot leak.

## What this does **not** solve

* **It does not claim "silence is solved."** Ticket 29's review showed that
  framing to be an overclaim, and this ticket exists because the previous fix
  was overclaimed.
* It does not make the gate as capable as the engine for speech buried in the
  device's LF floor: below roughly −6 dB SNR against the measured 0.043-RMS
  rumble the ratio cannot tell speech from rumble, and those buffers are
  dropped (engine could still read some). Quantified above.
* It does not fix the LF idle signal itself, and its **physical origin is still
  unknown**: it is a real broadband rumble (30–40 Hz dominant, drifting) at the
  ADC, consistent with acoustic fan/desk/vibration pickup *or* an ALC287
  analog-front-end artefact. I did not change hardware, gain, or service
  settings, and no test requires any of that — ges2t is robust on the device as
  it is.
* It does not change the engine path, `pipeline.py`, the injection backends,
  the tray, or any text handling; nothing is post-processed.
* The **user's live re-test** (silence, double-tap, quiet speech, normal
  dictation) remains user-owned and is **not** ticked.

## Verification

* Baseline `main`: **767 passed**, 49 files.
* Branch: `timeout 300 uv run pytest -q` → **773 passed, exit 0** (6 new tests;
  none deleted, skipped or weakened — the only edits to existing tests are input
  helpers already changed by ticket 29 and one stale comment corrected from
  "(0.002)" to "(0.0015)").
* `rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests` → **Success:
  no issues found in 49 source files**.
* No new dependency (stdlib `math` only), no audio committed.
