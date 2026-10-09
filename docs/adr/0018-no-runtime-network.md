# No runtime network: the only online operation is model acquisition

- **Status:** accepted
- **Date:** 2026-10-09

## Context

ges2t is a **fully offline** speech-to-text tool: that is the whole point of running a local
model. The user was asked how strictly to enforce this and gave an unusually crisp answer:
**"we do not need to guardrail offline-ness in any way, just write code so that it only
checks online for model downloads."**

That is a design constraint, not a policy to enforce at runtime. The context that makes it
non-trivial:

- The chosen integration path, `cactus-needle`, **auto-downloads the engine wheel and
  possibly the model on first use** (ADR 0007). Left alone, it would touch the network
  during dictation.
- Model acquisition is owned by the transcriber interface (ADR 0010) and exposed through
  `ges2t setup`, which the user chose as the *only* acquisition path — there is no automatic
  first-run download (ADR 0017).
- Telemetry in the Python package is opt-out (ADR 0006), so "do nothing" is the wrong default.

## Decision

**Network access is confined to one function, and only one: `Transcriber.ensure_model()`
(ADR 0010), invoked from `ges2t setup` and, where explicitly requested, `ges2t models`.**

Implementation rules:

- **No other code path may perform network I/O.** No update checks, no crash reporting, no
  analytics, no remote config, no version pings, no "is there a newer model" on startup.
- **Telemetry is disabled unconditionally at process start** — `NEEDLE_TELEMETRY=0` and
  `DO_NOT_TRACK=1` are set by ges2t before the backend package is imported (ADR 0007), in
  one place, for every subcommand. Not dependent on the user's shell or environment.
- **The auto-download behaviour of the backend package must be defused**, not merely
  avoided by convention: by the time the service loads the model, the engine and model must
  already be present locally, because the user ran `ges2t setup` (ADR 0017). A missing model
  at startup is an actionable error, never an unexpected download.
- **Offline operation is not detected, blocked, or warned about.** We do not add a network
  guard, a sandbox, a firewall check, or an offline mode flag. If the machine has no
  network, everything except `ges2t setup` simply works.
- **The rule is auditable by reading.** Because there is exactly one network-touching
  function, "does ges2t talk to the internet at runtime?" is answerable by reading that one
  function and grepping for HTTP clients elsewhere. A future ticket that adds any other
  network call must supersede this ADR.

## Consequences

- The user's privacy posture is a code property, not a runtime policy, which is what they
  asked for — no guardrails, no false failures, no startup delay.
- A reviewer can check compliance cheaply: grep the tree for network clients and confirm the
  hits are in the acquisition path only. This is a legitimate review criterion.
- Because we do not enforce offline-ness, a bug in the acquisition path could in principle
  be reached from elsewhere; the mitigation is structural (single call site, lazy imports)
  rather than defensive.
- `ges2t setup` is the only command a user needs to run with connectivity, which the README
  must state plainly, along with the fact that the model is pinned by checksum (ADR 0017).
