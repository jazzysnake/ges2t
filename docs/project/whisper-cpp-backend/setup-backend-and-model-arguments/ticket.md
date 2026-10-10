# setup: backend and model arguments

- **Priority:** P1
- **Status:** in review
- **Complexity:** M
- **Epic:** ../epic.md

## Current state / context

Today `ges2t setup` acquires the Whistle model (ADR 0017) as the **only networked path** (ADR 0018).
The user wants setup to become **backend-aware and model-parameterised**:

- `ges2t setup` — unchanged default: set up `needle` and its default model (whistle).
- `ges2t setup whisper.cpp <model>` — **build** the whisper engine (from
  `../whisper-engine-build/ticket.md`) and **fetch** the named model (from
  `../whisper-model-store/ticket.md`).
- the **same model-argument behaviour ported to the needle engine**: `ges2t setup needle <model>`
  accepted; **the needle model is selectable**, defaulting to `whistle`.

When the model is omitted for whisper, the default is the ADR 0021 rule: **`medium-q5_0` if a Vulkan
device is available, `small-q8_0` otherwise** (upstream publishes no `small-q5_0`).

## Definition of done

- [x] CLI: `ges2t setup [backend] [model]`, with the no-argument case preserving today's behaviour
      (needle + its default model).
- [x] `ges2t setup whisper.cpp [model]` runs the engine build **and** the model acquisition, in an
      order that leaves a consistent state on partial failure (a built engine with no model must be
      diagnosable, not corrupt).
- [x] `ges2t setup needle [model]` accepted — the **needle model is selectable** (default
      `whistle`), not hardcoded.
- [x] **Backend and model names validated** with typed, actionable errors: an unknown backend, an
      unknown model name, or a model/quant upstream does not publish is rejected **before** any build
      or download starts.
- [x] The **GPU/CPU default-model rule** implemented and used when the model is omitted; it is
      falsifiable (simulate no Vulkan → `small-q8_0`).
- [x] **Idempotent**: a second run verifies and does nothing; re-running with a different model
      fetches the new one without disturbing the old.
- [x] **No `sudo`, no package installs, no fetching of build tooling** — prerequisites are checked and
      reported (the engine-build ticket owns that check).
- [x] Network is used **only** by setup, only for the pinned clone and the sha256-pinned model
      downloads (ADR 0018).
- [x] Tests drive a **fake** builder/downloader: the argument parsing, the model-name validation, the
      GPU/CPU default rule, and the partial-failure ordering. Falsify each.
- [x] `uv run pytest` and `uv run mypy src/ges2t tests` green; report counts.
- [x] `001-comment.md` records the final CLI surface and the default-selection rule.

## End-to-end (user-owned, unticked)

- [ ] Real `ges2t setup whisper.cpp` completes on the reference machine.
      **`cmake` is not installed** here and this role may not install it, so this
      cannot run in-repo. Exact steps are handed over in `001-comment.md`; the
      honest failure the user sees first is the engine-build prerequisite gate
      (`sudo pacman -S base-devel cmake ...`).
      Note: use `small-q8_0`, not ticket 42's `small-q5_0`, which upstream does
      not publish and setup now rejects with a typed error.

## Notes

- Keep the existing `setup` UX (progress, idempotency, "this is the only networked command") — extend
  it, do not replace it.
- **No keystrokes, no clipboard, no windows, no microphone, no `sudo`, no service changes.** Network
  only for the pinned clone and the model downloads.
- If this ticket and `../whisper-backend-config-and-validation/ticket.md` both touch `cli.py` or the
  config module, **serialize them** — do not run them in parallel.

## Related tickets

- depends on: ../whisper-engine-build/ticket.md, ../whisper-model-store/ticket.md
- related ADRs: ../../../adr/0017-model-acquisition-via-setup.md, ../../../adr/0018-no-runtime-network.md, ../../../adr/0016-cli-surface.md
