# Testing and quality bar for v1

- **Status:** accepted
- **Date:** 2026-10-09

## Context

The user was asked what must be tested versus what may be verified by hand, and confirmed:
unit tests for config parsing, the transcriber interface, and the hotkey/injection state
machine; **manual** verification acceptable for real microphone capture, real `ydotool`
injection, and the tray; **no CI** in v1.

The interfaces decision (ADR 0010) exists partly to make this possible: the model, the mic,
and `/dev/uinput` are all substitutable, so the interesting logic can be tested headlessly on
a machine with none of them.

The framework latency budget (ADR 0001) also needs an enforcement point. Some of it is pure
unit-testable logic; the rest is a measurement obligation on integration tickets.

## Decision

**Automated tests: `pytest`, run locally via `uv run pytest`. No CI configuration is added
in v1, and no coverage threshold is enforced.**

Required automated coverage — these are the things a reviewer must be able to re-run:

1. **Config** (ADR 0009): defaults when the file is absent; every key parsed; invalid TOML,
   unknown keys, and wrong-typed values produce the specified loud/warning behaviour; the
   `--config` override is honoured.
2. **Service state machine** (ADR 0004, ADR 0015): the full transition table including the
   adversarial cases — duplicate `toggle`, `ptt-stop` without `ptt-start`, `ptt-start` while
   already transcribing, `cancel` during recording, and engine/injector failure paths.
   Tested against fakes only.
3. **Injection text assembly** (ADR 0012): exactly one trailing space when enabled, none when
   disabled, and **nothing at all** for empty/whitespace-only transcriptions.
4. **Transcriber selection and capability branching** (ADR 0010): the factory maps config to
   implementation; a fake with `supports_streaming = False` takes the batch path and one with
   `True` takes the streaming path, with no exception-driven control flow.
5. **Model verification** (ADR 0017): checksum and size mismatch must fail; the downloader is
   faked so the test performs no network I/O.
6. **CLI surface** (ADR 0016): argument parsing, and the socket client's behaviour when the
   service is absent (clear error, non-zero exit, no hang).

**Test doubles are first-class artifacts**, not ad-hoc mocks: `FakeAudioSource`,
`FakeTranscriber` (in both streaming and non-streaming flavours), `FakeInjector`, and
`FakeStatusUI`, each recording what it was given. They live in `tests/` and later tickets
import them rather than redefining them.

**Manual verification is explicitly allowed** for, and limited to: real microphone capture
through PipeWire, real `ydotool` typing into real applications, tray visibility and
state changes on Plasma 6, the KDE shortcut binding, and the end-to-end dictation
acceptance test. Each of these must be written down as a **reproducible checklist** with the
exact commands in the README or the ticket, so it is verification rather than vibes.

**Every ticket's definition of done names the exact command that must pass**, and review
re-runs it (per the review process). "Tests pass" is not a criterion; `uv run pytest
tests/foo` is.

## Consequences

- No CI means no automated regression safety net beyond what a reviewer runs. Accepted for a
  personal daily-driver MVP on one machine (ADR 0000).
- Manual checks are only as good as their checklist. Reviewers must actually run them for the
  tickets that claim them, and a ticket that says "manually verified" without a written
  procedure has not met its definition of done.
- The design is pushed toward testable seams, which is also what makes the engine
  replaceable (ADR 0006) — the testability and the abstraction requirement point the same
  way.
- Performance claims are treated as measurements with a stated method, never as adjectives:
  any ticket asserting latency must say how it was measured and report the number, because
  the <1 s framework budget is otherwise unfalsifiable.
