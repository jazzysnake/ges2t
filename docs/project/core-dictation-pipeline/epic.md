# Epic: Core dictation pipeline

- **Priority:** P0
- **Status:** to do
- **Epic owner:** orchestrator

## Goal

Make dictation work end to end **from the command line**: a recording can be started and
stopped with `ges2t toggle`, the audio is transcribed locally by Cactus Whistle, and the
resulting text is typed into whatever window currently has focus.

When this epic is done, the user can run `ges2t run` in a terminal, switch to any other
application, run `ges2t toggle` from a second terminal, speak, run `ges2t toggle` again, and
watch the words appear in the focused text field.

## Why it matters

This is the entire product, minus polish. Everything the user asked for — local model, no
cloud, text into any textbox or terminal, hotkey-driven, abstracted model runtime — is
exercised by this path. The tray, the streaming partials, and the pretty documentation all
sit on top of it, and none of them can be validated until this works.

It is deliberately scoped to the **batch** transcription path so that the riskiest unknowns
(the engine actually running, `ydotool` actually typing, PipeWire actually capturing) are
retired before streaming complicates the loop.

## Deliverable

A CLI-driven, batch-mode dictation tool that works on the reference machine.

## Acceptance criteria

These are the epic's own criteria, verified by the orchestrator after the last ticket merges:

- [ ] `uv run pytest` passes with the full unit suite green, all tests headless (no mic, no
      `/dev/uinput`, no model, no network) — ADR 0014.
- [ ] `uv run ges2t doctor` reports a healthy environment on the reference machine, and
      reports an actionable, specific diagnosis for each of: missing model, `ydotoold` not
      running, no `/dev/uinput` access, `pw-record` missing, invalid config.
- [ ] `uv run ges2t setup` downloads `whistle.cact`, verifies sha256
      `b6e02f048568ac5d01a2042556c658061e699acbc0aa2a1439f52f3d461dffeb` and size
      `16,919,407` bytes, and is idempotent on a second run.
- [ ] **End-to-end, manually verified on the reference machine:** with `ges2t run` active,
      `ges2t toggle` → speak → `ges2t toggle` types the correct text into a focused text
      editor, and separately into a focused terminal.
- [ ] **End-to-end, manually verified:** dictating twice in a row produces two space-
      separated words, and dictating in silence types nothing at all — ADR 0012.
- [ ] **Framework latency under 1 second**, measured and reported as a number with its
      method: the non-model part of a dictation round trip (socket call, capture stop and
      drain, buffer handoff, injection of a ≤20-word phrase) excludes engine processing time
      — ADR 0001.
- [ ] A recording stopped before any audio arrived, a dead engine worker, and a failed
      injection each produce a visible error and leave the service usable — ADR 0015.
- [ ] No code outside the acquisition path performs network I/O: grepping **our own `src/`**
      for network clients returns hits only in `model_store`'s acquisition path, and
      `pyproject.toml` declares no HTTP client. (The third-party transitive tree of
      `cactus-needle` is explicitly out of scope for this check — ADR 0018.)
- [ ] Every ticket's `docs/project/README.md` row is `done` and the merged diffs match the
      ADR decisions they implement.

## Out of scope for this epic

Streaming/partial transcription, the tray icon, notifications and cancel-on-key, the KDE
shortcut documentation, and the finished README — all of those live in
`../live-dictation-ux/epic.md`.

## Decisions this epic implements

- ADR 0000 — project scope and non-goals
- ADR 0001 — Python implementation language
- ADR 0002 — Wayland-only, KDE reference target
- ADR 0003 — text injection via ydotool/uinput
- ADR 0004 — global hotkey via CLI command + service socket
- ADR 0005 — backend is Cactus Whistle on Needle 3
- ADR 0006 — accepted risk: closed engine binary
- ADR 0007 — integrate via the `cactus-needle` package
- ADR 0008 — audio capture via `pw-record`
- ADR 0009 — TOML configuration
- ADR 0010 — `Transcriber`/`AudioSource`/`Injector` interfaces
- ADR 0012 — verbatim output plus trailing space
- ADR 0014 — testing and quality bar
- ADR 0015 — single long-lived service process
- ADR 0016 — CLI surface
- ADR 0017 — model acquisition via `ges2t setup`
- ADR 0018 — no runtime network
- ADR 0019 — full type annotations, interface-typed signatures, classes over tuples
