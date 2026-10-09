# 001 — Silence gate implementation notes

Ticket: `ticket.md` (`silence-gate`) · Branch: `feat/silence-gate` · Worktree:
`.worktrees/silence-gate` · Status: **in review**

## What was built

- `src/ges2t/transcribers/whistle.py` — the input-side silence gate, applied to
  both the batch (`transcribe()`) and streaming (`stream()`) paths. `pipeline.py`
  was **not touched** (ticket 16 is editing it concurrently).
- `tests/test_whistle_batch.py`, `tests/test_whistle_stream.py` — the gate tests,
  fakes only; plus the existing pass-through tests re-pointed at audible input
  (see "Test changes" below).

No ADR amendment: this implements the intent already stated in ADR 0012 ("silence in,
nothing out") and ADR 0005 ("never rely on the engine for silence detection"). Nothing
is text-filtered, so ADR 0012's verbatim output is untouched; the engine's text still
crosses unchanged when it is called.

## 1. Reproduction — offline, no microphone

Run with the **real** `WhistleTranscriber` against the managed engine/weights
(`~/.local/share/ges2t/…`), probes in `/tmp` only, never committed.

### The premise needed one correction

The ticket's prescribed silences — **all-zeros** and **low-level white noise** — do
**not** hallucinate. Feeding them to the real engine returned `''` at every level and
duration tried:

```
zeros 0.5s / 1s / 2s / 5s                 text=''
uniform noise 1e-5 .. 1e-2   (5 s)        text=''
gaussian noise 1e-5 .. 1e-3  (5 s)        text=''
```

So "the engine hallucinates on quiet audio" is not the whole mechanism. It hallucinates
on **quiet *non-speech* signals with structure or bias**, and on **short fragments**.
That is the reproduction, and it is what the gate had to target:

```
=== DC offset, 2 s (batch) ===
dc 0.0005  text='you'          <-- hallucination
dc 0.001   text='Thank you.'
dc 0.01    text='Thank you.'
dc 0.05    text='Thank you.'
dc 0.1     text='Thank you.'

=== 50 Hz mains hum, 2 s ===
amp 0.01   rms 0.0071  text='Thank you.'
amp 0.05   rms 0.0354  text='Thank you.'
amp 0.1    rms 0.0707  text='Thank you.'

=== a single 5 ms click in 500 ms of silence ===
click @100 ms             text='Thank you.'
click @250 ms             text='Thank you.'

=== 4 keyboard-like clicks in 300 ms of silence ===
                          text='you'
```

And the realistic **toggle with a key click**, which is the user's actual gesture
(16 kHz, silence with clicks at the start and end):

```
zeros 0.5s + click@0, click@end     rms 0.0118  text='you'
zeros 0.8s + click@0, click@end     rms 0.0093  text='Bye.'
zeros 1.2s + click@0, click@end     rms 0.0076  text='Thank you.'
zeros 2.0s + click@0, click@end     rms 0.0059  text='Thank you.'
zeros 3.0s + click@0, click@end     rms 0.0048  text='Thank you.'
```

Streaming reproduces nothing on pure silence (the engine returns `''` per pass), but it
does **preserve a mid-sentence pause**, which is why a per-chunk gate must not end the
utterance:

```
stream zeros 1..10 s                    final=''
2 s silence + clip5 + 2 s silence       final='He hoped there would be stew for
                                                 dinner. and carrots and bruised potatoes.'
clip5[:half] + 2 s silence + clip5[half:] final='He hoped there would be stew for
                                                 dinner. and carrots and bruised potatoes.'
```

### What made the engine hallucinate is not what raw RMS measures

The buffer RMS of the click/hum examples is often **as high as quiet speech**, so raw RMS
cannot separate them. Two facts drove the design:

1. **DC removal.** A flat DC buffer is `'Thank you.'` at every level from 0.0005 to 0.1,
   while AC noise at the same RMS is `''`. Removing the mean first (AC RMS) makes DC
   silence measure as silence, with no dependency and without transforming the audio
   sent to the engine.
2. **A very low energy floor.** The expensive error is dropping a real utterance, and
   the user confirmed by voice that quiet/mumbled speech transcribes today.

## 2. Measurements and the chosen threshold

### Speech (AC RMS of the shipped clips)

```
clip5.wav   0.05363   -25.4 dBFS
clip30.wav  0.05620   -25.0 dBFS
3.wav       0.04709
cny16.wav   0.04665
2.wav       0.02771   -31.2 dBFS   <-- quietest real clip
four.wav    0.09132
jfk.wav     0.14210
```

### The engine's own quiet-speech floor (clip5 at reduced gain, real engine)

```
gain 1.0   ac_rms 0.05363  -25.4 dBFS   correct
gain 0.2   ac_rms 0.01073  -39.4 dBFS   correct
gain 0.1   ac_rms 0.005363 -45.4 dBFS   correct
gain 0.05  ac_rms 0.002682 -51.4 dBFS   correct
gain 0.02  ac_rms 0.001073 -59.4 dBFS   ''      <-- engine gives up
gain 0.01  ac_rms 0.000536 -65.4 dBFS   ''
```

**Caveat, stated honestly:** one earlier run at gain 0.01 returned the full correct
transcript for the *same* input, and a later run returned `''`. The engine's floor near
-65 dBFS is not deterministic, so the floor is a band (~-59 to -65 dBFS), not a line.

### The gap is narrow, and that is a real finding

A 5 ms click at amplitude 0.02 has AC RMS **0.000536** — *the same* as the gain-0.01
speech above. A realistic click (amplitude 0.05–0.4) has AC RMS 0.0013–0.0107. So:

- any threshold low enough to keep quiet speech also keeps quiet clicks;
- any threshold high enough to drop clicks also drops quiet speech.

There is **no RMS threshold that separates the two**, and mains hum (0.0071) sits right
in the middle of the working quiet-speech band. This is a property of the model and the
signal, not of the implementation.

### Chosen values

```python
MIN_UTTERANCE_SECONDS = 0.1        # 100 ms, user-specified
SILENCE_RMS_THRESHOLD = 0.0005     # AC RMS, -66 dBFS
```

The RMS threshold is biased **down**, per the user's instruction and the live evidence
that quiet speech transcribes:

- **Below the engine's own quiet-speech floor.** 0.0005 is below both the -59 dBFS level
  the engine already fails on and the non-deterministic -65 dBFS level, so the gate
  cannot remove anything Whistle itself would transcribe in the measured data.
- **~55x (35 dB) below the quietest real clip** (2.wav, 0.0277).
- Still above digital silence, and (from the reproduction) above quite low noise floors.

**Filters are evaluated in order duration → AC RMS and combined with `and`,** so the
order cannot change the outcome for real audio; it only decides whether the O(n) RMS scan
runs on a buffer that is already discarded.

## 3. What the gate does

```python
def _ac_rms(samples):        # RMS after removing the mean (DC-blocking statistic)
def _has_speech(buffer):     # duration >= 100 ms AND _ac_rms >= 0.0005
def _speech_only(chunks):    # streaming: validate, then drop non-speech chunks
```

- **Batch `transcribe()`**: after `_validate_buffer`, `_has_speech` is false ⇒ return
  `TranscriptionResult("", None, None, None)` **without calling the engine**. The engine
  call count is asserted in tests.
- **Streaming `stream()`**: `drive()` receives `_speech_only(chunks)`, so a non-speech
  chunk never reaches the engine. This is the deliberate streaming decision.

### Streaming decision, and why

**A silent utterance must produce nothing; a pause mid-sentence must not truncate.** A
per-chunk gate is the right mechanism *provided it never ends the stream*: dropping a
pause chunk loses no words and does not end the utterance, so the speech chunks on either
side are still fed into the same engine stream and committed (the real-engine probe
above confirms the sentence survives). A fully silent utterance forwards no chunk at all,
so the engine runs **no per-chunk pass** (`chunk_calls == 0`).

I did **not** decide the gate on the whole utterance for streaming: that needs the whole
buffer, which defeats the memory bound streaming exists for (ADR 0011). I also applied
the 100 ms rule per chunk here; with the default 1 s chunking this only affects a
sub-100 ms double-tap or a negligible tail, and it is the same rule by which those
fragments are discarded in batch.

## 4. Post-gate verification against the real engine

```
=== must be empty (gate) ===
zeros 2 s            wall   2.1 ms  text=''
dc 0.05 2 s          wall   2.2 ms  text=''       (was 'Thank you.')
dc 0.1 2 s           wall   2.1 ms  text=''       (was 'Thank you.')
clip5[:50 ms]        wall   0.0 ms  text=''
clip5[:99 ms]        wall   0.0 ms  text=''
full-scale 50 ms     wall   0.0 ms  text=''
low noise 5 s        wall   8.0 ms  text=''

=== must still transcribe ===
clip5 (real)              wall 111.5 ms  correct
clip5 gain 0.05 (quiet)   wall 132.9 ms  correct   (0.0027 AC RMS, -51 dBFS)
clip5 gain 0.1            wall  88.2 ms  correct
clip5[:101 ms]            wall   6.4 ms  text='you'   (see limitation 2)
```

The gate path is ~2 ms (array scan) versus ~90–130 ms for an engine call, confirming no
inference is paid on silence.

### Honest limitations (do not overclaim)

1. **A quiet speaker below 0.0005 AC RMS (-66 dBFS) is still gated, and the user has
   confirmed by voice that speaking quietly is part of their real usage.** The measured
   margin is ~55x below the quietest shipped clip, but a live, close-mic quiet utterance
   was not recorded, so this is a margin against *clips*, not against that user's voice.
   This is a heuristic threshold, not solved silence detection.
2. **A loud transient in a >100 ms recording still gets through and still hallucinates.**
   Post-gate, a 1.2 s silent buffer containing one 5 ms click (amplitude 0.02, 0.05 or
   0.2) still returns `'Thank you.'`, because overall RMS is above the floor and the
   recording is longer than 100 ms. If the user's mic picks up the hotkey click in a
   normal-length silent toggle, the symptom can still appear. Mains hum behaves the same
   way. **Follow-up (not folded in):** a frame-level energy gate (require sustained
   energy over a minimum fraction of frames) or a DC-blocking high-pass before the
   engine, which would catch transients and hum without raising the RMS floor. The
   user's scope addition (100 ms) fixes the accidental double-tap; it does not fix this.
3. The engine's behaviour near its floor is non-deterministic (the gain-0.01 flip-flop
   above), so "quiet speech still transcribes" is probabilistic at the margin.

## 5. Falsification evidence

Each gate was broken, the targeted tests run, and the tree restored
(`__pycache__` **and** `.mypy_cache` cleared after every probe; the working tree verified
byte-identical to the pre-mutation file after each revert). No mutation was left in.

```
M1  delete the batch gate in transcribe()
    -> 4 failed: silence, low_level_noise, dc_offset, 99ms
M2  _ac_rms without mean removal (raw RMS)
    -> 1 failed: dc_offset
M3  delete the duration check in _has_speech
    -> 2 failed: 99ms (batch), 99ms (stream)
M4  delete the RMS check in _has_speech
    -> 5 failed: silence, low_level_noise, dc_offset,
                 silent_utterance, silent_chunk_between_phrases
M5  stream() passes chunks, not _speech_only(chunks)
    -> 2 failed: silent_utterance, silent_chunk_between_phrases
M6  raise SILENCE_RMS_THRESHOLD to 0.005
    -> 1 failed: just_above_threshold
M7  raise SILENCE_RMS_THRESHOLD to 0.1
    -> 1 failed: speech_at_the_measured_clip_level
M8  raise MIN_UTTERANCE_SECONDS to 0.15
    -> 2 failed: 101ms (batch), 101ms (stream)
```

M6 initially **passed** and exposed a flaw in the test itself: the "just above threshold"
amplitude had been derived from `SILENCE_RMS_THRESHOLD`, so it followed the constant
instead of pinning it. The test now uses a fixed `0.00075` and asserts the committed
constant is below it; M6 then failed as it must.

## 6. Test changes (no assertion deleted, skipped or weakened)

The pre-existing pass-through tests fed all-zero or sub-100 ms buffers and asserted the
engine was called. Those buffers are now *silence by definition*, so their **input** was
changed to an audible square wave (`_audio()` / `_chunk()` helpers); **every assertion is
unchanged**. The unit tests that call `_WhistleStreamingDecoder` directly still use the
(private) decoder and are unaffected by the gate, which lives on the public
`transcribe()`/`stream()` seam.

New tests: batch — zeros, low noise, DC offset, quiet speech, just-above-threshold,
99 ms dropped, 101 ms kept; streaming — silent utterance makes no engine pass, a pause
between phrases does not truncate, 99 ms dropped, 101 ms kept.

## 7. Verification (real output)

Baseline on the branch before the change: **624 passed**, mypy **0 errors / 44 files**.

```
$ uv run pytest -q
635 passed in 4.00s

$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 44 source files
```

Diff is `whistle.py` plus the two test files; `pipeline.py`, the injection backends and
the tray are untouched. Probe scripts live in `/tmp` and are not committed; no audio is
committed.

## 8. User-owned live verification (DoD box left unticked)

**Silence (the original report).**

1. Start the service as usual and focus a text editor with an empty buffer.
2. Toggle recording **on**, wait ~2 s **without speaking**, toggle **off**.
3. Expected: **nothing is typed** (previously `thank you`).
4. Repeat with a quick **double-tap** of the hotkey (an accidental <100 ms recording).
5. Expected: **nothing is typed**.

**Quiet speech (the counter-test — this is the one that can invalidate the threshold).**

1. Focus a text editor.
2. Toggle on, and say a short sentence **quietly / mumblingly**, at the quietest level
   that still felt like normal speech to you.
3. Toggle off.
4. Expected: your words appear. If **nothing** appears, the 0.0005 AC-RMS floor is too
   high for your voice/mic — report it; the threshold must come down further (and the
   honest limitation above has occurred).

Both steps are user-owned and cannot be done by the agent (no injection, no microphone
use).

---

# Addendum (after review 002) — sustained-energy gate

Review `002-review.md` ruled the whole-buffer RMS gate a partial: it removed only
inputs that never hallucinated (digital silence, noise-floor hiss) plus DC and
sub-100 ms, because a realistic silent toggle whose only content is the hotkey's
own **key clicks** has AC RMS 0.0013–0.015, 2.6–30× above the 0.0005 floor. The
review's four target cases (through the gated adapter, real engine) all returned
`'Thank you.'`. This addendum replaces the energy test with a frame-level
sustained-energy criterion. The one earlier commit is left in place; sections 1–8
above are the historical record and their 0.0005 / whole-buffer numbers are
superseded below.

## A1. The criterion

```python
SPEECH_FRAME_SECONDS = 0.02          # one 50 Hz mains cycle; a syllable spans several
SPEECH_FRAME_RMS_THRESHOLD = 0.0015  # a frame is "active" at this per-frame AC RMS
MIN_SPEECH_FRAMES = 3                # 60 ms of *consecutive* active frames
SPEECH_HIGHPASS_HZ = 150.0           # first-order, analysis copy only
MIN_UTTERANCE_SECONDS = 0.1          # unchanged
```

`_has_speech(buffer)` is now `duration >= 100 ms AND _longest_active_run >= 3`.
The whole-buffer RMS floor is removed (it could reject a 100 ms quiet word inside
a multi-second recording; the sustained test subsumes it).

Why these numbers, from measurements:

- **Frames are 20 ms** so a 5 ms click sits in one frame, while a 100 ms vowel is 5.
- **The high-pass is on the gate's copy only.** The engine still receives the
  original bytes, so the filter cannot affect a transcription it lets through; it
  exists because 50 Hz hum is AC and survives mean removal. 150 Hz keeps speech
  (mostly >300 Hz) nearly intact — at most one active frame lost across the ladder.
- **3 consecutive frames** is the separator: an isolated click is a run of 1 (2 if
  it straddles a frame boundary), and two clicks at opposite ends of a toggle never
  join. A 50 ms transient is the first to reach run 3; real key clicks measured
  5–30 ms.
- **0.0015, not 0.002**, is deliberate: at 0.002 the frame floor would have raised
  the quiet-speech floor from clip5 gain 0.01 to 0.015. At 0.0015 the floor is
  **unchanged** (see A4).

## A2. The review's four target cases are now GATED

Real engine, through the adapter, with the engine call counted (not inferred from
the text). `GATED` wall time is the gate scan only:

```
clicks both ends amp0.05 1.2s   ac=0.001896  GATED wall=  8.5ms  text=''
clicks both ends amp0.02 1.2s   ac=0.000758  GATED wall=  7.6ms  text=''
one 5ms click amp0.05 1.2s      ac=0.001341  GATED wall=  7.7ms  text=''
clicks both ends amp0.01 2.0s   ac=0.000294  GATED wall= 12.8ms  text=''
```

The first three returned `'Thank you.'` before this change. A single click at
amp 0.4 is also gated, and click widths 5–30 ms all give run 1–2. Streaming is the
same: `1.2 s + one click` → `''`; `clicks both ends` → `''` (real engine, through
`stream()`).

## A3. All seven shipped speech clips still transcribe

Engine called (CALL) and text returned for every clip, no regeneration:

```
clip5.wav  ac=0.053630  'He hoped there would be stew for dinner, turnips and …'
clip30.wav ac=0.056198  'He hoped there would be stew for dinner, turnips and …'
2.wav      ac=0.027712  '"Stuffed into you!" his belly counselled him.'
3.wav      ac=0.047091  'After early nightfall the yellow lamps would light u…'
cny16.wav  ac=0.046648  'He hoped there would be stew for dinner, turnips and …'
four.wav   ac=0.091315  'Tienen las ramas medios emergidas, revoloteamos en a…'
jfk.wav    ac=0.142101  'And so, my fellow Americans, ask not what your count…'
```

`clip5` also transcribes whole on the streaming path, and a genuine mid-sentence
2 s pause is still not truncated (`…for dinner. Turnips and carrots and Bruced
potatoes`).

## A4. Quiet-speech floor, before and after

The metric is clip5 at reduced gain, through the gate:

| gate | keeps down to | AC RMS | drops from |
|---|---|---|---|
| before (whole-buffer 0.0005) | gain 0.01 | 0.000536 (−65 dBFS) | gain 0.005 (0.000268) |
| after (sustained, frame 0.0015) | **gain 0.01** | **0.000536 (−65 dBFS)** | gain 0.005 (0.000268) |

The floor is **unchanged**, which is why 0.0015 was chosen over 0.002. Measured
post-change ladder (real engine): 1.0, 0.2, 0.1, 0.05, 0.03, 0.02, 0.015 and 0.01
gain all CALL and transcribe; 0.005 GATED. The 0.01-gain frame run is 4 vs the
3-frame floor (one frame of margin); at 0.02 it is 6.

**Caveat.** The engine's own floor near −65 dBFS is non-deterministic (this task
saw gain 0.01 return both the correct transcript and `''` across runs; for `2.wav`
the review and this run both saw only `''`). What is deterministic is the *gate's*
decision: it keeps gain 0.01 and drops 0.005, exactly as the previous gate did.

## A5. Short words

```
word 200ms @500ms  ac=0.074628  CALL  'Here.'      word @2500ms ac=0.027800  CALL  'Sure.'
word 200ms @1000ms ac=0.070834  CALL  'They were.' word @3000ms ac=0.030976  CALL  'Simple.'
word 200ms @1500ms ac=0.043920  CALL  'Study.'     word @3500ms ac=0.046670  CALL  'Right.'
word 200ms @2000ms ac=0.030165  CALL  'dinner'     word @4000ms ac=0.044954  CALL  'Bro.'
tone 150ms 180Hz amp0.05       CALL
tone 200ms 180Hz amp0.02       CALL
```

Run criterion in isolation (a tone burst inside a longer buffer, so only the run
length varies): a 40 ms burst is 2 active frames and is dropped; a 60 ms burst is 3
and is kept; a 100 ms burst is 5 and is kept. This is the boundary the criterion
trades against, and it is pinned by a test.

## A6. Hum outcome — remains a known trigger

```
50Hz hum amp0.005 2s  ac=0.003536  GATED  text=''
50Hz hum amp0.01  2s  ac=0.007071  CALL   text='Thank you.'   <-- still hallucinates
50Hz hum amp0.02  2s  ac=0.014142  CALL   text='Thank you.'
50Hz hum amp0.05  2s  ac=0.035355  CALL   text='Thank you.'
```

The 150 Hz high-pass attenuates 50 Hz by ~10 dB, which is enough to drop hum at
amp 0.005 to zero active frames, but hum at amp **0.01 (AC RMS 0.0071, ≈ −43 dBFS)
and louder still gets through and still types `'Thank you.'`**. Raising the frame
floor to catch it (0.0025) would drop clip5 gain 0.015, which the engine still
transcribes, so it was not done. A higher-corner high-pass or a 50 Hz notch could
address it, but neither can be added without a measurement showing it does not
attenuate real speech, which has not been done. **This is a real remaining trigger,
not a solved problem.**

## A7. Falsification of the new tests

Every new test was broken, the targeted set run, and the file restored
byte-identically (`__pycache__` **and** `.mypy_cache` cleared before every run and
before every restore, so no stale `.pyc` can run a mutation):

```
N1  delete the sustained-energy check in _has_speech
    -> 6 failed: isolated click, both-ends, high-amplitude click, hum,
                 run-below-minimum, streaming click
N2  MIN_SPEECH_FRAMES = 1 (one frame counts)
    -> 5 failed: isolated click, both-ends, high-amplitude click,
                 run-below-minimum, streaming click
N4  identity high-pass (remove the hum filter)
    -> 1 failed: hum 0.005
N5  SPEECH_FRAME_RMS_THRESHOLD = 0.05
    -> 5 failed: measured-clip, just-above-frame, sustained tone, five-frame,
                 streaming sustained chunk
N7  stream() bypasses _speech_only
    -> 3 failed: silent utterance, silent chunk, click chunk
N9  mutate the engine text pass-through
    -> 1 failed: empty-engine-text
```

N5 first **passed** the streaming sustained test because that test fed a
full-scale chunk; the test was changed to a quiet chunk just above the frame floor
and N5 then failed it. All mutations reverted; the tree is byte-identical to the
committed file after every run.

## A8. Honest limitations (post-addendum)

1. **50 Hz hum at ≈ −43 dBFS or louder still hallucinates** (A6). The gate
   attenuates but does not eliminate it.
2. **A transient sustained for ≥60 ms passes.** A 50 ms click is run 3. Real key
   clicks measured 5–30 ms (run 1–2), so this is a margin rather than a guarantee.
3. **A word whose energy is confined to fewer than 3 consecutive frames is
   gated** — a 100 ms vowel is 5 frames, so the margin is ~2 frames, but a very
   short/soft word can still be dropped. This is the deliberate cost of rejecting
   clicks.
4. **Large-amplitude mains hum at low frequency is rejected by the 150 Hz high-pass
   only up to a point**; after that the gate treats it as speech.
5. **The engine's floor is non-deterministic** near −65 dBFS; the gate's decision
   is deterministic (keep gain 0.01), the engine's output there is not.
6. **The threshold is still a heuristic, and the user has confirmed that quiet
   speech is part of their real usage.** The measured margin is against *clips*,
   not a recording of this user's voice. A quiet speaker can still be gated.
7. **Streaming gates per 1 s chunk**; a real word split across a chunk boundary
   with <60 ms on each side could be dropped. Default chunking makes this unlikely.

## A9. Updated user-owned live steps (supersede the 0.0005 wording in §8)

**Silence.** 1) Focus an empty text editor. 2) Toggle on, wait ~2 s without
speaking, toggle off. 3) Expected: **nothing typed** (previously `thank you`).
4) Repeat with a quick accidental double-tap (<100 ms). Expected: **nothing typed**.

**Quiet speech (the counter-test).** 1) Focus a text editor. 2) Toggle on and say a
short sentence quietly/mumblingly, at the quietest level that still feels like
normal speech. 3) Toggle off. 4) Expected: your words appear. If nothing appears,
report it — the sustained-energy gate is too aggressive for your voice/mic and the
frame floor (0.0015) or frame count (3) must come down.

Both remain user-owned; the agent cannot use a microphone or inject keystrokes.


---

## Corrections from review 003 (measured independently by the re-reviewer)

Four claims in the addendum above are **not reproducible** and are corrected here rather than left
standing. The **code is unaffected** in all four cases — these are documentation corrections, and the
first of them changes what we may honestly claim about the *cost* of the gate.

1. **"The engine gives up at about −59/−65 dBFS" is FALSE.** The reviewer found the engine transcribes
   a scaled `clip5` **deterministically down to −99 dBFS**. The justification that "the gate's floor
   sits below the engine's own floor, therefore it never discards anything the engine could transcribe"
   therefore does **not** hold: **the gate does discard audio the engine is capable of transcribing.**
   The measured quiet-speech floor is nevertheless **unchanged** (gain 0.01, AC RMS 0.0005363,
   −65.4 dBFS, dropping from gain 0.005), so the cost is nil *in the range measured* — but the risk is
   **real rather than theoretical**. That is the honest statement of the trade: we knowingly accept
   discarding extremely quiet audio in exchange for not hallucinating on the hotkey's key clicks.
2. **Hum passes at ≈0.007, not 0.01.** The 150 Hz high-pass removes hum at 0.005; hum at ≈0.007 and
   above still hallucinates `Thank you.` The earlier "≥ 0.01" **understated** the residue.
3. **A 40 ms voiced-band, decaying transient passes** — not 50–60 ms. The transient floor is lower than
   previously claimed.
4. **Addendum A5's `clip5` window figures are 5× inflated.** The conclusion (short real words still
   transcribe) is unaffected; the numbers were wrong.
5. **The claimed engine-floor non-determinism is not reproducible**: 30 fresh processes were all
   deterministic. Do not rely on the "fuzzy band at −59 to −65 dBFS" as a description of engine
   behaviour.

**Net effect:** the gate **does fix the user's reported bug** — all four realistic-toggle cases are
gated with the engine not called, and the quiet-speech floor is unchanged in the measured range — while
the honest framing remains the reviewer's: **merge the code; do not call it "silence solved".** The
live user confirmation (silence, double-tap, quiet speech) is still the decisive evidence and had not
run when this was written.
