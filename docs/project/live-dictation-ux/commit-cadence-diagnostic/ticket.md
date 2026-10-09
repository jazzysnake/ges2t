# Commit-cadence diagnostic (the gate for incremental typing)

- **Priority:** P1
- **Status:** to do
- **Complexity:** S
- **Epic:** ../epic.md

## Current state / context

ADR 0011 says nothing is injected until release. The user has asked for text to appear **as they
speak**, and they identified the safe part correctly: the words the engine **commits** — those two
consecutive passes agree on — are appended to the transcript and never revised. In
`src/ges2t/streaming.py`:

```python
transcript += pass_.committed      # append-only
```

Because `transcript` is built purely by `+=`, **every intermediate value of it is a literal prefix of
the final transcript**. That is a property of the fold, not of the engine.

**But it holds only if the engine's committed deltas are themselves append-only.** The engine is
closed-source (ADR 0006), so the "returns only the words it committed" claim is currently the ADR's
assertion, **never measured on real speech**.

This ticket is the gate. It makes **no behaviour change** and writes **no injection code**. Its whole
job is to produce the numbers that decide whether the feature is safe, cheap, or dead — before any of
it is built.

## Definition of done

A measurement harness that drives the **real** engine over **real recorded speech** through the
shipped `drive()` / streaming adapter, and reports:

- [ ] **The append-only property, over every intermediate segment.** For each utterance, assert that
      every emitted value of `transcript` is a **prefix** of the final transcript, and report pass/fail
      per utterance plus a total. **This is the load-bearing safety property.** If it fails, say so
      plainly: the feature is unsafe and ticket 34 is cancelled.
- [ ] **Commit cadence.** How often does `pass_.committed` become non-empty, and how many characters
      does each commit carry? Report a distribution (median / p90 / max) and the typical gap **in
      seconds between commits**. This number decides how bad a focus slip actually is.
- [ ] **`pending` size.** How many characters typically sit uncommitted, and for how long? This is the
      observable lag between speaking and text appearing.
- [ ] **Any observed restatement.** If the engine ever returns a committed string that revises earlier
      text, capture the concrete before/after example **verbatim**. This is the case that kills the
      design; record it rather than smoothing it over.
- [ ] Run on the shipped clips **plus at least one fresh real-device recording long enough to cross a
      window flush** — the 5 s window means the `flush()` path must be exercised, and the flush is
      where a restatement is most likely. See the lesson in
      ../../core-dictation-pipeline/silence-gate/ticket.md: **"verified against constructed inputs" is
      not "works on real inputs"**. Synthetic signals are not evidence here.
- [ ] **The capture step is user-owned and self-announcing.** The harness must be a script the **user**
      runs in their own terminal — the agent must **not** record the microphone itself, because it has
      no way to tell the user when to start. The script must make the start unambiguous:
      print the device it will use, wait for the user to press **Enter**, show a **3-2-1 countdown**,
      then print an unmistakable **`● RECORDING — speak now`** cue with a visible seconds counter, and
      print **`■ stopped`** at the end. It must also write its full report to a file, not only stdout,
      so the user can hand the numbers back without copy-pasting from a terminal.
- [ ] **A recommendation with the numbers behind it** — proceed / proceed with a named caveat / stop —
      including the injection cadence the measurements support.

## Notes

- **Note on which input exercises what.** `clip30.wav` is 30 s, so it already crosses the 5 s window
  **six** times — the flush path is exercised by the clips alone. The fresh capture is for **realism**,
  not for reaching the flush: it is the only input that carries this device's own noise floor and the
  user's own speech, which is the whole lesson of the silence saga.
- **Ask before recording.** Any fresh capture uses the user's microphone; that is a separate permission
  from wanting the feature. State the duration up front.
- **Never commit audio.** `/tmp` only.
- Keep the harness out of the shipped package: a `docs/.../measure_*.py` script, or a test explicitly
  marked as requiring the engine and a device. `uv run pytest` must stay headless and engine-free
  (ADR 0014).
- This is a measurement, not a redesign. If the engine restates, **stop and report**.
- One decision is already taken and must not be re-litigated: the clipboard backend is
  **incompatible** with streaming, because incremental paste would rewrite the user's clipboard on
  every commit.

## Related tickets

- depends on: ../streaming-window-engine/ticket.md, ../whistle-streaming-adapter/ticket.md,
  ../streaming-session/ticket.md, ../streaming-wiring-and-e2e/ticket.md (all merged)
- blocks: ../committed-delta-interface/ticket.md, ../incremental-typing/ticket.md
- related ADRs: ../../../adr/0011-streaming-sliding-window.md,
  ../../../adr/0006-needle3-closed-engine-accepted-risk.md,
  ../../../adr/0010-transcriber-interface-design.md
