# Inject the engine's output verbatim, plus a configurable trailing space

- **Status:** accepted
- **Date:** 2026-10-09

## Context

The user was asked whether v1 should clean up transcriptions — punctuation restoration,
casing fixes, filler-word removal — and answered: **"post processing is not in scope for v1,
just input what the engine outputs."**

A practical wrinkle then appeared: the engine returns plain text with **no trailing space**,
so dictating two phrases in a row produces `helloworld` in the target field. The user was
asked to choose between injecting the raw string exactly, or appending a space, and answered:
**append the trailing space in this case**.

These two answers are in mild tension ("verbatim" vs "append something"), so the boundary
needs to be written down precisely rather than left to whoever implements injection.

## Decision

**The transcription pipeline is verbatim.** ges2t performs no rewriting of the engine's
text: no punctuation insertion, no capitalisation changes, no filler-word removal, no
profanity filtering, no whitespace normalisation *within* the text, no translation.

**A single, isolated exception:** the injector appends **one ASCII space** to the text it
types, when the text is non-empty and `ui.trailing_space` is true (default, ADR 0009).

Rules that keep this exception from metastasising:

- The trailing space is added **at the injection boundary, in one place**, and is the last
  thing that happens to the string. It is not applied to the stored/returned
  `TranscriptionResult`; the result object stays exactly as the engine returned it, so any
  future consumer (logging, history, tests) sees clean engine output.
- **Empty or whitespace-only transcriptions are never injected at all** — not even a lone
  space. Silence in, nothing out (ADR 0005 has no VAD, so this case is common and must be
  handled deliberately).
- The trailing-space behaviour is **configurable** and can be turned off exactly as it can
  be turned on, because it is a formatting preference, not a correctness fix.
- Anything resembling real post-processing is a **new ticket with its own ADR**, not an
  increment on this one. If it is ever built, it goes between the transcriber and the
  injector as an explicit, testable step — never scattered through either.

## Consequences

- Dictating two phrases in a row produces correctly separated words, which is the behaviour
  the user actually wanted from "verbatim".
- Transcripts keep the engine's own shortcomings (no punctuation unless the model emits it,
  arbitrary casing). The user accepted this for v1; it is a quality ceiling, not a bug.
- Because the exception is one line at one boundary and is off-by-default-able, it cannot
  quietly grow into an accidental text-processing layer.
- Tests can assert both halves precisely: the `TranscriptionResult` is unmodified, and the
  bytes handed to the `Injector` are the result plus exactly one space when enabled.
