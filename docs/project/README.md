# ges2t — project board

**ges2t** (*good enough speech 2 text*) is a minimal, fully offline speech-to-text tool for
Linux that types what you say into whatever window has focus. Press a hotkey, speak, release:
Cactus Compute's 16 MB **Whistle** model runs locally and the text lands in the focused field.
No cloud, no account.

**Target:** usable as the user's daily driver on Arch Linux + KDE Plasma 6 on Wayland.
**Not** packaged or distributed in v1 — see [ADR 0000](../adr/0000-project-scope-and-non-goals.md).

## Status legend

`to do` · `in progress` · `in review` · `blocked` · `done` · `obsolete`

Branch convention: `feat/<ticket-slug>`, worktree at `.worktrees/<ticket-slug>`.

---

## Epic 1 — [Core dictation pipeline](core-dictation-pipeline/epic.md) · P0

A CLI-driven, batch-mode dictation path that works end to end. Exit test: `ges2t toggle` →
speak → `ges2t toggle` types the right text into a text editor and a terminal.

| # | Ticket | Size | Status | Depends on | Branch |
|---|---|---|---|---|---|
| 1 | [project-scaffold](core-dictation-pipeline/project-scaffold/ticket.md) — uv project, console script, pytest | S | done | — | feat/project-scaffold (merged) |
| 2 | [core-interfaces-and-fakes](core-dictation-pipeline/core-interfaces-and-fakes/ticket.md) — `Transcriber`/`AudioSource`/`Injector`/`StatusUI` + fakes | S | done | 1 | feat/core-interfaces-and-fakes (merged) |
| 3 | [config-loading](core-dictation-pipeline/config-loading/ticket.md) — generic TOML layer + engine selector | S | done | 1, 2 | feat/config-loading (merged) |
| 4 | [whistle-engine-config](core-dictation-pipeline/whistle-engine-config/ticket.md) — `[engine.whistle]` validation | S | done | 3 | feat/whistle-engine-config (merged) |
| 5 | [model-store-and-verification](core-dictation-pipeline/model-store-and-verification/ticket.md) — pinned download + sha256 | S | done | 2 | feat/model-store-and-verification (merged) |
| 6 | [audio-capture-pw-record](core-dictation-pipeline/audio-capture-pw-record/ticket.md) — PipeWire capture, drain on stop | M | done | 2, 3 | feat/audio-capture-pw-record (merged) |
| 7 | [ydotool-injector](core-dictation-pipeline/ydotool-injector/ticket.md) — uinput injection + trailing space | S | done | 2, 3 | feat/ydotool-injector (merged) |
| 8 | [whistle-transcriber-adapter-batch](core-dictation-pipeline/whistle-transcriber-adapter-batch/ticket.md) — batch `transcribe()` | M | done | 2, 4, 5 | feat/whistle-transcriber-adapter-batch (merged) |
| 9 | [service-state-machine-and-socket](core-dictation-pipeline/service-state-machine-and-socket/ticket.md) — states, single instance, socket | M | done | 2, 3 | feat/service-state-machine-and-socket (merged) |
| 10 | [cli-surface-and-commands](core-dictation-pipeline/cli-surface-and-commands/ticket.md) — 9 subcommands, exit codes | M | done | 3, 5, 9 | feat/cli-surface-and-commands (merged) |
| 11 | [dictation-pipeline-batch](core-dictation-pipeline/dictation-pipeline-batch/ticket.md) — composition root + E2E | M | done | 6, 7, 8, 9, 10 | feat/dictation-pipeline-batch (merged) |
| 12 | [doctor-command](core-dictation-pipeline/doctor-command/ticket.md) — diagnose every prerequisite | M | to do | 3, 5, 6, 7, 9, 10 | — |
| 20 | [type-check-whole-tree](core-dictation-pipeline/type-check-whole-tree/ticket.md) — fix the tests-not-type-checked gap | XS | done | 4 | feat/type-check-whole-tree (merged) |
| 21 | [status-ui-outside-lock](core-dictation-pipeline/status-ui-outside-lock/ticket.md) — fix the reproduced status_ui-under-lock deadlock | XS | done | 9 | feat/status-ui-outside-lock (merged) |
| 26 | [pin-cli-test-guarantees](core-dictation-pipeline/pin-cli-test-guarantees/ticket.md) — pin the two falsified CLI test guarantees | XS | done | 10 | feat/pin-cli-test-guarantees (merged) |
| 27 | [pin-layout-test-guarantees](core-dictation-pipeline/pin-layout-test-guarantees/ticket.md) — pin the three falsified layout-detection test guarantees | XS | done | 24 | feat/pin-layout-test-guarantees (merged) |

### Injection rework (added 2026-10-09 after the `hu` keymap discovery)

`ydotool type` is US-only and ~40 ms/char; see ADR 0020 and ADR 0003's amendment.

| # | Ticket | Size | Status | Depends on | Branch |
|---|---|---|---|---|---|
| 22 | [xkb-keycode-mapping](core-dictation-pipeline/xkb-keycode-mapping/ticket.md) — libxkbcommon reverse lookup, generic over layouts | M | done | 2 | feat/xkb-keycode-mapping (merged) |
| 24 | [layout-detection-and-config](core-dictation-pipeline/layout-detection-and-config/ticket.md) — live keymap, KWin D-Bus, injection config blocks | S | done | 3 | feat/layout-detection-and-config (merged) |
| 23 | [batched-key-emission](core-dictation-pipeline/batched-key-emission/ticket.md) — one `ydotool key` batch + the latency measurement | M | done | 7, 22, 24 | feat/batched-key-emission (merged) |
| 25 | [clipboard-paste-injector](core-dictation-pipeline/clipboard-paste-injector/ticket.md) — `wl-copy` + paste chord as a configured alternative | S | to do | 23, 24 | — |
| 28 | [injection-delay-default](core-dictation-pipeline/injection-delay-default/ticket.md) — set `key_delay_ms` from ticket 23's measurement (12 → 0) | XS | done | 23, 24 | feat/injection-delay-default (merged) |

## Epic 2 — [Live dictation UX](live-dictation-ux/epic.md) · P1

Streaming as the default path, a tray that shows state, actionable notifications, and
documentation that takes a fresh machine to a working dictation.

| # | Ticket | Size | Status | Depends on | Branch |
|---|---|---|---|---|---|
| 13 | [streaming-window-engine](live-dictation-ux/streaming-window-engine/ticket.md) — generic 5 s windowed-commit driver | M | done | 2 | feat/streaming-window-engine (merged) |
| 14 | [whistle-streaming-adapter](live-dictation-ux/whistle-streaming-adapter/ticket.md) — engine decoder + `stream()` | M | done | 8, 13 | feat/whistle-streaming-adapter (merged) |
| 15 | [streaming-session](live-dictation-ux/streaming-session/ticket.md) — `DictationSession` over capture + window | M | done | 9, 14 | feat/streaming-session (merged) |
| 16 | [streaming-wiring-and-e2e](live-dictation-ux/streaming-wiring-and-e2e/ticket.md) — selection rule + >30 s E2E | S | to do | 11, 15 | — |
| 17 | [tray-status-ui](live-dictation-ux/tray-status-ui/ticket.md) — pystray AppIndicator + backend assertion | M | done | 10, 11, 21 | feat/tray-status-ui (merged) |
| 18 | [notifications-and-cancel](live-dictation-ux/notifications-and-cancel/ticket.md) — `notify-send` on failure | S | to do | 16, 17 | — |
| 19 | [user-documentation-and-setup-guide](live-dictation-ux/user-documentation-and-setup-guide/ticket.md) — README + setup guide + unit file | M | to do | 10, 11, 12, 16, 17, 18 | — |

---

## Dispatch order

Dependencies are respected by working the board in this order; tickets in the same row are
independent and may run in parallel (they touch disjoint files).

| Wave | Tickets |
|---|---|
| 0 | 20 (done), 21 (corrective) |
| 1 | 1 | 
| 2 | 2, 3 |
| 3 | 4, 5, 6, 7, 13 |
| 4 | 8, 9 |
| 5 | 10 |
| 6 | 11, 12 |
| 7 | 14 |
| 8 | 15 |
| 9 | 16, 17 |
| 10 | 18 |
| 11 | 19 |
| 12 | 22, 24 (injection rework; disjoint files) |
| 13 | 23 |
| 14 | 25 |

Serialization is also required wherever two tickets touch the same file: `10 → 11/12`
(`cli.py`, `doctor.py`), `11 → 16` (`pipeline.py`), `17 → 18` (`pipeline.py`).

## Decisions

All 20 decision records live in [`adr/`](../adr/). Start with
[ADR 0000 (scope)](../adr/0000-project-scope-and-non-goals.md) and
[ADR 0005 (the Whistle/Needle 3 backend)](../adr/0005-stt-backend-cactus-whistle.md).

**Every ticket's definition of done includes `uv run mypy src/ges2t tests` in addition to
`uv run pytest`** — full annotations, interface-typed signatures, and classes instead of
tuples, per [ADR 0019](../adr/0019-code-style-typing-and-interfaces.md). The command covers
**tests as well as `src/`**: ADR 0019 was amended on 2026-10-09 after ticket 7's review
found that a `src/ges2t`-only check had let a real violation through ticket 4's review
([ticket 20](core-dictation-pipeline/type-check-whole-tree/ticket.md) fixes it).

## Known accepted risks

- The Needle 3 engine is a **closed binary** — Apache-2.0 declared, but unauditable and
  unpatchable. Accepted knowingly, with a documented escape hatch to whisper.cpp/ggml.
  See [ADR 0006](../adr/0006-needle3-closed-engine-accepted-risk.md).
- `ges2t` cannot work without `ydotoold` running and `/dev/uinput` access; both are manual
  setup steps. See [ADR 0003](../adr/0003-text-injection-via-ydotool.md).
- The tray needs system packages (`python-gobject`, `gtk3`, `libappindicator`) and a venv
  created with `--system-site-packages`; a pip-only tray silently fails on Plasma 6.
  See [ADR 0013](../adr/0013-tray-via-pystray-appindicator.md).
