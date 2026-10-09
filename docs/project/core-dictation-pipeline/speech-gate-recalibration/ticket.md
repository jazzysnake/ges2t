# Recalibrate the speech gate on real device idle audio

- **Priority:** P0
- **Status:** to do
- **Complexity:** M
- **Epic:** ../epic.md

## Current state / context

Ticket `../silence-gate/ticket.md` added a frame-level sustained-energy gate so the model would stop
hallucinating `Thank you.` on silent toggles. **It did not fix the user's machine**, and the cause is
neither of the two things anyone assumed. A dedicated diagnostic run measured it:

**The user's "silence" is not silent.** Using the production `PwRecordAudioSource`, six real
"toggle on, stay quiet, toggle off" captures measured:

| Property | Measured | Reference |
|---|---|---|
| Duration | 318 – 2473 ms | floor is 100 ms, so always above it |
| Raw **AC RMS** | **0.036 – 0.26** | the shipped speech clips are **0.047 – 0.14** |
| Per-**20 ms-frame** RMS, after the gate's 150 Hz high-pass | **0.003 – 0.011** | threshold `SPEECH_FRAME_RMS_THRESHOLD = 0.0015` → **2–7× over** |
| Longest run of consecutive active frames | **12, 20, 32, 32, 58, 123** | `MIN_SPEECH_FRAMES = 3` |
| 50 Hz hum amplitude | 0.001 – 0.008 (one 0.016 at startup) | **not the culprit** |
| Dominant energy | a **broadband ~20–25 Hz oscillation/drift, amplitude ≈0.04** | — |

So `_has_speech` returns `True` 6/6 and `_speech_only` keeps the chunk 6/6 — **the gate is working as
designed and is being handed something that genuinely resembles sustained speech energy.** The
recording is as loud as speech, just at far lower frequencies. The first-order 150 Hz high-pass leaves
0.00826 → ×2–×3 of the threshold, so it does not save it.

**Ruled out by the diagnostic (do not re-investigate):**

- **Not a stale service** — no `ges2t` process was running, the package is installed editable, and a
  fresh process on current `main` still reproduces it.
- **Not mains hum** — 50 Hz is 0.001–0.008, far below the LF drift's ≈0.04.
- **Not the user's `command output` hypothesis** — the string was traced through the real `Service` →
  streaming session → engine → injector (`partial 'Thank you.'` → `final 'Thank you.'` →
  `injector.inject('Thank you.')`), `transcribe()` on the saved buffer returned it 5/5, `needle` contains
  no `Thank` and prints nothing, no subprocess-output-as-text path exists, and the device is a real
  `alsa_input`, **not** a monitor. It **is** the model hallucinating.

**Minimal reproduction:** `start()`; stay silent ≈0.2 s; `stop()` → a ≈169 ms buffer, AC RMS ≈0.1, longest
run 8 → `_has_speech` True → the engine returns `'you'`/`'Thank you.'`.

**The root cause of the miss is calibration:** the gate's thresholds were derived from **synthetic**
inputs (digital silence, synthesised clicks, a 50 Hz tone). It was never checked against the **actual
idle audio of the real device**, which is where the failure lives.

Relevant decisions: ADR 0012 (the fix stays **input-side** — never text post-processing), ADR 0005,
ADR 0011, ADR 0019.

## Definition of done

- [ ] **Recalibrate against real data, and say what data.** Capture, on the reference machine,
      (a) several real **idle/silent** recordings via the production audio source, and (b) the real
      **speech** side (the shipped clips in `/tmp/whinspect/`; never commit audio). Derive the criterion
      from **measured separation between those two populations**, not from synthetic signals and not
      from intuition. Report the distributions and the margin.
- [ ] **Choose a mechanism that can actually separate them.** The idle signal is a ≈20–25 Hz
      oscillation; speech lives higher. Candidates: a **steeper and/or higher-cutoff high-pass**
      (a first-order 150 Hz filter demonstrably does not suffice), a **speech-band energy ratio**
      (e.g. energy in roughly 300–3000 Hz relative to total — the idle drift has essentially none),
      or a combination. **Justify the choice from the measurements.**
- [ ] **⚠️ Do not eat real speech — especially quiet speech.** This is the expensive error, and it has
      already caused one round of rework. Male speech fundamentals sit around 85–180 Hz, so a
      high-pass pushed too high *attenuates real speech*; verify with real clips that quiet speech
      still transcribes, and report the **quietest gain that still transcribes, before and after**.
      If the floor moves, quantify it.
- [ ] **The whole point: the real idle captures must be gated.** The six measured captures above (or
      fresh equivalents measured the same way) must all be rejected, with the **engine not called** —
      assert the call count, not just the empty text. Reproduce the minimal case (`≈0.2 s` silent
      toggle) and confirm it is gated.
- [ ] **Keep the transient and duration behaviour:** an isolated click and a sub-100 ms recording must
      still be gated, and the gates must compose sensibly. Do not regress ticket 29's verified cases.
- [ ] Keep it **input-side** (ADR 0012) and dependency-free (no numpy). The engine must still receive
      the **original bytes** — any filter you add is for the gate's analysis only (verify the existing
      analysis-only property still holds; `003-review.md` on ticket 29 shows how it was proven).
- [ ] **Falsify every new test** — break the guarded behaviour, watch it fail, revert completely.
- [ ] `uv run pytest` and **`uv run mypy src/ges2t tests`** green. Report before/after counts; no test
      deleted, skipped or weakened.
- [ ] `001-comment.md` records: the captured distributions, the chosen criterion and margin, the
      quiet-speech floor before/after, the falsification evidence, and an explicit statement of what
      this does **not** solve. **Do not claim silence is solved** — ticket 29's review already showed
      that framing to be an overclaim, and this ticket exists because the previous "fix" was.

## Notes

- **The device may itself be improvable** — a ≈20–25 Hz idle oscillation at that amplitude is unusual
  and could be an acoustic rumble (fan, traffic, desk vibration) or an ALSA/PipeWire artefact. Record
  what you can determine about its physical origin, but **do not require the user to change hardware**:
  ges2t must be robust on the device as it is.
- **No keystrokes, no clipboard, no windows, no `sudo`, no service changes, no network.** The audio
  captures are user-authorised for this diagnosis only; keep them short, keep them in `/tmp`, and
  **never commit audio**. The microphone is live during a capture, so keep the window brief.
- The user's live confirmation (silence, double-tap, quiet speech, and a normal dictation) remains
  **user-owned**; provide exact steps and leave the box unticked.

## Status: PAUSED by the user

The user asked to pause this work:

> *"I am sitting in a car right now with the window open, so I think we can postpone this. we dont
> need to get stuck on this, since absolute silence is not a regular input and the model works kinda
> well for regular input."*

**That is a reasonable call, and it also supplied the missing piece of the diagnosis.**

1. **The measured "idle" may be environmental noise, not a device floor.** A car with the window
   open produces precisely the measured signature — broadband ≈20–25 Hz rumble at amplitude ≈0.04
   (wind and road noise). So the idle population this ticket calibrated against is plausibly
   **wind/road rumble**, which means the previous diagnostic's "could not determine the physical
   origin" has a strong candidate, and the failure is **environment-dependent** rather than a property
   of silence. In a quiet room the original amplitude gate probably worked all along.
2. **The cost may bite in exactly the noisy case.** The measured quiet-speech cost (the gate keeps
   gain ≥ 0.4 with the real floor present, where the engine reaches 0.1) was measured *with that LF
   noise floor present* — i.e. in the kind of environment where the user actually dictates. If real
   speech over the measured noise floor does not clear `SPEECH_BAND_RATIO ≥ 0.15`, this gate would
   **drop real dictation**, which is worse than the bug it fixes.
3. **Absolute silence was never a requirement.** ADR 0000's bar is "good enough", and the user judges
   it met for normal speech. Chasing silence indefinitely is not serving v1.

**Decision: the branch is PARKED, not merged.** The implementation and its evidence stay on
`feat/speech-gate-recalibration`; the ticket stays `in review`. Before any merge, the user should test
in **both** environments:

| Environment | What to check |
|---|---|
| **Quiet room** | silence → nothing; double-tap → nothing; quiet speech still transcribes |
| **Noisy (car, window open)** | **does normal speech still transcribe?** — the question that decides whether this gate is an improvement or a regression |

If speech is dropped in the noisy case, the two honest options are: lower `SPEECH_BAND_RATIO`
(accepting more hallucination on noise), or address the noise **at the input** (a PipeWire high-pass,
a different capture device, or disabling AGC/ALC), which would remove the root cause and let the gate
be gentler. **Neither is to be built speculatively.**

**Do not reopen this without the user's real-world result.** Ticket 29 (the amplitude-based gate) is
merged and is what ships today; this ticket exists to improve the noisy-input case, not to fix a
regression.
