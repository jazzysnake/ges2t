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
- **Measured during scaffolding (ticket 1, 2026-10-09):** `cactus-needle`'s single dependency
  `huggingface-hub` transitively pulls a full network stack — `httpx2`, `httpcore2`, `anyio`,
  `h11`, `fsspec`, `filelock`, `hf-xet`, `tqdm`, `truststore`, `click`, `pyyaml`. We declare
  no HTTP client of our own, but a naive "grep the dependency tree for HTTP clients" audit
  would hit this chain and could not distinguish our code from a third party's.

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
- **The rule is scoped to code we write and dependencies we declare.** It constrains ges2t's
  own imports and behaviour, **not** the transitive dependency tree of a third-party backend.
  A network library arriving inside `cactus-needle`'s dependency closure is not a violation of
  this ADR; ges2t calling it outside acquisition would be. The obligation is therefore twofold:
  (a) our source performs network I/O in exactly one function, and (b) we never *invoke* a
  third-party fetch path at runtime — which is what ADR 0007's load-time assertion, the
  `NEEDLE_WHISTLE_WEIGHTS`/`NEEDLE3_LIB_PATH` overrides and `HF_HUB_OFFLINE=1` (ADR 0017)
  exist to guarantee.
- **The rule is auditable by reading.** Concretely: grep our own `src/` for HTTP/network
  clients (`urllib`, `httpx`, `requests`, `socket`) and confirm every hit is in
  `model_store`'s acquisition path; and confirm `pyproject.toml` declares no HTTP client.
  Do **not** attempt to audit the lockfile's transitive closure — it is third-party code we
  do not call. A future ticket that adds any other network call of our own must supersede
  this ADR.

## Consequences

- The user's privacy posture is a code property, not a runtime policy, which is what they
  asked for — no guardrails, no false failures, no startup delay.
- A reviewer can check compliance cheaply and without ambiguity: grep our own source, and
  read our own dependency list. Neither step requires reasoning about upstream packages.
- The distinction matters practically: `cactus-needle`'s transitive HTTP stack is unavoidable
  if we keep the integration path (ADR 0007), so a rule that banned it outright would
  contradict an accepted decision. Scoping the rule to our code keeps both ADRs true.
- Because we do not enforce offline-ness, a bug in the acquisition path could in principle
  be reached from elsewhere; the mitigation is structural (single call site, lazy imports)
  rather than defensive.
- `ges2t setup` is the only command a user needs to run with connectivity, which the README
  must state plainly, along with the fact that the model is pinned by checksum (ADR 0017).
