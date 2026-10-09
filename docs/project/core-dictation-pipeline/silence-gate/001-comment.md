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
