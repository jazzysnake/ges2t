# Accepted risk: the Needle 3 engine is a closed binary

- **Status:** accepted
- **Date:** 2026-10-09

## Context

The research pass tried to answer "what are we actually depending on?" and found an
asymmetry the user should decide on knowingly rather than discover later.

What is open:

- The **model weights** are Apache-2.0 on Hugging Face.
- The **`cactus-needle` Python package** is Apache-2.0 on PyPI.

What is not:

- The **native engine** — `libneedle3.so` — is shipped as a **stripped x86-64 ELF binary**.
  The C++ source is not published. The public `cactus-compute/needle` repository contains
  only Python, `llms.txt`, and `pyproject.toml`; there is no CMake and no source build path.
- The Apache-2.0 declaration covers the binaries legally, but the shipped license is an
  unmodified template (`Copyright [yyyy]`, no `NOTICE`), which suggests the label was applied
  rather than deliberately authored. **This is unverifiable, and flagged as such.**

The Apache-2.0 label does permit commercial use and binary redistribution. It does **not**
compel source disclosure, so this is legal but **unauditable and unpatchable**: we cannot
read the code, cannot fix a bug in it, cannot port it, and cannot fully audit what it does.
The engine links only `libc`/`libm`/`pthread`/`libdl` and, per its own README, reads no
environment variables — so a direct-`ctypes` integration has no network surface. The
telemetry that exists lives in the *Python package*, and is opt-out (ADR 0007).

The user was asked directly whether this is acceptable for v1 and answered **accept**.

## Decision

Accept the closed-engine risk for v1, with the following mitigations as conditions of that
acceptance:

1. **Record it.** This ADR is the record; the README's dependencies section must name the
   engine as a closed binary and link to it.
2. **Pin it.** The engine artifact is pinned by version (the wheel that carries
   `libneedle3.so`) alongside the model sha256 (ADR 0005), so an upstream silent change
   cannot silently change our behaviour.
3. **Contain it.** Nothing outside the single Whistle adapter (ADR 0007, ADR 0010) may
   import the engine. If we ever drop it, the blast radius is one module plus a dependency
   pin.
4. **Do not build on undocumented behaviour.** Anything discovered by probing (streaming
   accumulation semantics, ADR 0011) is written down as a *measurement*, not treated as a
   contract, because there is no source or spec to appeal to.
5. **Telemetry stays off.** `NEEDLE_TELEMETRY=0` and `DO_NOT_TRACK=1` are set by ges2t
   unconditionally (ADR 0007), so the opt-out does not depend on the user remembering.

## Consequences

- v1 ships a dependency we cannot debug at the source level. Any engine bug is either
  worked around in our adapter or escalated as a blocker.
- The escape hatch is real and cheap because the interface already exists: `whisper.cpp`
  (MIT) or a CTranslate2 path can be implemented as a second `Transcriber` without touching
  the service, injection, audio, or UI layers.
- If the user ever wants to sell or redistribute ges2t, this ADR is the first thing a
  diligence pass will ask about. The answer is "Apache-2.0 declared, binary-only, weights
  and Python wrapper verifiably Apache-2.0".
- We do **not** commit to fine-tuning Whistle: published fine-tuning tooling targets Needle
  text models, and no Whistle-specific recipe was found. That capability is unverified and
  out of scope for v1 (ADR 0000).
