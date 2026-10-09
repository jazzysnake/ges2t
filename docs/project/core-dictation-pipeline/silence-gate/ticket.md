# Silence must produce nothing (the model hallucinates on quiet audio)

- **Priority:** P0
- **Status:** done
- **Complexity:** S
- **Epic:** ../epic.md

## Current state / context

Found by the **user's manual verification** — the same carve-out that caught the `hu` keymap corruption,
and for the same reason: no test could see it.

**Symptom.** Toggle recording on and off **without speaking**, and ges2t types **`thank you`** instead
of nothing. Every other step of the epic's end-to-end test passed.

**Mechanism (verified by reading the code, not assumed).**

- **There is no silence / amplitude / RMS / VAD handling anywhere in `src/`** (grepped for all of it).
- `BatchDictationSession.finish()` returns `""` only when **zero samples** were captured
  (`../dictation-pipeline-batch/ticket.md`). A live microphone during silence captures *plenty* of
  samples — they are merely quiet — so that rule never fires.
- `src/ges2t/service.py` guards `if text.strip():`, so genuinely empty text is never injected
  (ADR 0012). But the model returns **`"thank you"`**, which is not empty, so the guard passes it
  through.

So **Whistle hallucinates a plausible short phrase on near-silent audio** — a known behaviour of small
speech models — and nothing in the pipeline ever asks *"did anyone actually speak?"*.

**Why the fix is on the audio side, not the text side.** Filtering the text (blacklisting `thank you`)
would (a) violate **ADR 0012**'s "verbatim output, no post-processing except one trailing space", and
(b) be fragile — it would be a different phrase next time. Deciding that there is **no speech to
transcribe** is an **input-side** judgement, and it is what ADR 0012 already intends; `pipeline.py`
even quotes the intent in a docstring: *"silence in, nothing out"*. The intent was correct — the
implementation simply assumed zero-samples was the only way to detect silence. **This ticket does not
need an ADR amendment; it implements existing intent.**

## Definition of done

- [x] **Reproduce it first, offline and without a microphone.** Feed a **silent buffer** (all-zeros,
      and separately a low-level noise floor) to the real transcriber and record what it returns.
      Then feed the **real speech clips** from `/tmp/whinspect/` (`clip5.wav`, `clip30.wav` — never
      commit audio; regenerate with `pw-record` if `/tmp` has been reaped). Report both verbatim in
      `001-comment.md`. If silence does **not** reproduce the hallucination, **stop** and report —
      the premise would be wrong and that is a far more interesting finding.
- [x] **Measure, then choose the threshold from data — do not guess.** Record the **RMS / dBFS** of
      (a) a captured silent recording and (b) the speech clips, and pick a threshold with a **stated
      margin** below the quietest real speech. The threshold's purpose is to distinguish *no speech*
      from *quiet speech*, and the **expensive error is the opposite one**: dropping a real utterance
      is far worse than occasionally transcribing a hallucination. Choose a conservative value and
      record the measured distributions and the margin in `001-comment.md`.
- [x] **Implement the gate.** When the recording contains no speech, return **`""` without calling the
      model at all** (not merely discarding the model's output — do not pay for inference we know is
      meaningless). Prefer placing it where **both** the batch and streaming paths benefit, i.e. in the
      transcriber adapter (`src/ges2t/transcribers/whistle.py`) rather than in one session. **Note:**
      `src/ges2t/pipeline.py` is being edited concurrently by ticket
      `../../live-dictation-ux/streaming-wiring-and-e2e/ticket.md` — avoid touching it if you can; if
      the design genuinely requires a change there, say so and keep it minimal.
- [x] **Minimum recording duration — 100 ms, at the user's direction.** A recording shorter than
      **100 ms** is treated as no-speech: return an empty result **without calling the model**.
      **Note:** this had *already* been implemented in round 1 as `MIN_UTTERANCE_SECONDS = 0.1`, so the
      user's request was satisfied before it was made — and `003-review.md` verified it is tested **in
      isolation** from the energy gate (a 99 ms buffer is dropped while a 101 ms full-scale buffer is
      kept), which is what stops the test passing for the wrong reason. Rationale: an accidental
      double-tap produces a very short recording the model can still hallucinate into a phrase, and an
      energy gate alone does not catch it because a key click is **loud**. No config plumbing; a
      constant is right at this size.
- [x] **Consider the streaming path explicitly.** A per-chunk gate would misfire on the natural pauses
      *within* an utterance, so think about what the gate should mean for `stream()` — a silent
      utterance must produce nothing, but a pause mid-sentence must not truncate. State what you
      decided and why.
- [x] **Add the minimum-recording-duration gate (user scope addition).** A recording shorter than
      **100 ms** counts as "no speech": return `""` without calling the model, exactly like the RMS
      gate. `100 ms` is the user's specified value, held in the named constant
      `MIN_UTTERANCE_SECONDS` with no config plumbing. Tested **in isolation** from the RMS gate
      (a full-scale 99 ms burst is dropped, a 101 ms burst is not), and falsified by removing the
      duration check.
- [x] **Tests** (fakes only, no mic, no real model): a silent buffer yields `""` and the model is
      **never called** (assert the call count, not just the return value); a low-level noise floor
      likewise; the real speech clips still transcribe; and a buffer **just above** the threshold is
      **not** dropped. Falsify each — break the gate, watch the test fail, revert completely.
- [x] `uv run pytest` and **`uv run mypy src/ges2t tests`** green (ADR 0019 covers `tests/`). Report
      before/after counts; no test deleted, skipped or weakened.
- [x] `001-comment.md` records the reproduction, the measured distributions, the chosen threshold and
      margin, the streaming decision, and the honest limitation that a threshold is a heuristic — an
      unusually quiet speaker could still be gated. **Do not claim silence is solved in general.**
- [ ] **User-owned live confirmation.** Toggle on and off without speaking (and with an accidental
      double-tap) and see **nothing** typed; then repeat with quiet/mumbled speech and see the words
      typed. Exact steps are in `001-comment.md` §8. The agent cannot do this (no microphone, no
      injection).

## Notes

- **Do NOT inject keystrokes, do not write to the clipboard, no windows, no `sudo`, no network.** The
  gate is verified offline against buffers and the shipped clips.
- The user's live confirmation ("toggle on and off without speaking, and see nothing typed") is a
  **user-owned** step; leave that DoD box unticked and provide the exact steps.
- Do not touch `ydotool`/`clipboard` injection or the tray — this is about *what* is transcribed, not
  how it is typed.
- The **minimum-duration** rule was originally listed as an adjacent idea, but the user promoted it
  to scope at **100 ms** after the brief: an accidental double-tap captures a *loud* click that the
  RMS gate cannot catch. It is user-specified and is implemented as a constant, not config.
