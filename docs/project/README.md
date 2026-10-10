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
| 12 | [doctor-command](core-dictation-pipeline/doctor-command/ticket.md) — diagnose every prerequisite | M | done | 3, 5, 6, 7, 9, 10 | feat/doctor-command (merged) |
| 29 | [silence-gate](core-dictation-pipeline/silence-gate/ticket.md) — silence must type nothing (the model hallucinates `thank you` on quiet audio) | S | done | 11 | feat/silence-gate (merged) |
| 31 | [speech-gate-recalibration](core-dictation-pipeline/speech-gate-recalibration/ticket.md) — recalibrate the speech gate on real device idle audio (it is as loud as speech) | M | blocked | 29 | feat/speech-gate-recalibration (parked: implemented and reviewed, awaiting the user's live test) |
| 30 | [tray-failure-diagnostics](core-dictation-pipeline/tray-failure-diagnostics/ticket.md) — a read-only socket verb so `doctor`/`status` can see the tray's failure record (amends ADR 0016) | S | to do | 12, 17 | — |
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
| 25 | [clipboard-paste-injector](core-dictation-pipeline/clipboard-paste-injector/ticket.md) — `wl-copy` + paste chord as a configured alternative | S | done | 23, 24 | feat/clipboard-paste-injector (merged 2646212; **manual gate PASSED** after 36 — the user verified paste + clipboard restore with `paste_keys = ["ctrl+shift+v"]`, see `008-comment.md`) |
| 28 | [injection-delay-default](core-dictation-pipeline/injection-delay-default/ticket.md) — set `key_delay_ms` from ticket 23's measurement (12 → 0) | XS | done | 23, 24 | feat/injection-delay-default (merged) |
| 36 | [clipboard-injection-timeout](core-dictation-pipeline/clipboard-injection-timeout/ticket.md) — `wl-copy` forks by default, so the immediate path blocked on its inherited stdout/stderr pipes and reported failure after a successful write (corrective for 25; found by the user's manual run) | S | done | 25 | feat/clipboard-injection-timeout (merged ddeb278) |
| 37 | [pin-clipboard-real-seam-payload](core-dictation-pipeline/pin-clipboard-real-seam-payload/ticket.md) — pin that a non-empty payload actually reaches the child's stdin in the real immediate runner (mutating it to `b""` left all 877 tests green) | XS | done | 36 | feat/pin-clipboard-real-seam-payload (merged 53d3c79) |
| 38 | [clipboard-paste-keys-default](core-dictation-pipeline/clipboard-paste-keys-default/ticket.md) — default `paste_keys` `ctrl+v` → `ctrl+shift+v` (the shipped default had been exercised by nobody; the caveat that an unbound `ctrl+shift+v` pastes nothing is documented with the default) | XS | done | 25 | feat/clipboard-paste-keys-default (merged 47431eb) |
| 40 | [bindable-hotkey-hint](core-dictation-pipeline/bindable-hotkey-hint/ticket.md) — the binding advice `doctor`/`cli` print (`ges2t toggle`) is not on a shortcut's `PATH`; both now resolve and print the console script's absolute path, falling back to the bare command only when it cannot be resolved to an existing file | XS | done | 12, 19 | feat/bindable-hotkey-hint (merged 3658ca0) |

## Epic 2 — [Live dictation UX](live-dictation-ux/epic.md) · P1

Streaming as the default path, a tray that shows state, actionable notifications, and
documentation that takes a fresh machine to a working dictation.

| # | Ticket | Size | Status | Depends on | Branch |
|---|---|---|---|---|---|
| 13 | [streaming-window-engine](live-dictation-ux/streaming-window-engine/ticket.md) — generic 5 s windowed-commit driver | M | done | 2 | feat/streaming-window-engine (merged) |
| 14 | [whistle-streaming-adapter](live-dictation-ux/whistle-streaming-adapter/ticket.md) — engine decoder + `stream()` | M | done | 8, 13 | feat/whistle-streaming-adapter (merged) |
| 15 | [streaming-session](live-dictation-ux/streaming-session/ticket.md) — `DictationSession` over capture + window | M | done | 9, 14 | feat/streaming-session (merged) |
| 16 | [streaming-wiring-and-e2e](live-dictation-ux/streaming-wiring-and-e2e/ticket.md) — selection rule + >30 s E2E | S | done | 11, 15 | feat/streaming-wiring-and-e2e (merged) |
| 17 | [tray-status-ui](live-dictation-ux/tray-status-ui/ticket.md) — pystray AppIndicator + backend assertion | M | done | 10, 11, 21 | feat/tray-status-ui (merged) |
| 18 | [notifications-and-cancel](live-dictation-ux/notifications-and-cancel/ticket.md) — `notify-send` on failure | S | done | 16, 17 | feat/notifications-and-cancel (merged) |
| 19 | [user-documentation-and-setup-guide](live-dictation-ux/user-documentation-and-setup-guide/ticket.md) — README + setup guide + unit file | M | done | 10, 11, 12, 16, 17, 18 | feat/user-documentation-and-setup-guide (merged fcef28a) |
| 32 | [commit-cadence-diagnostic](live-dictation-ux/commit-cadence-diagnostic/ticket.md) — measure commit cadence and prove the committed deltas are new text (the gate for 34) | S | done | 13, 14, 15, 16 | feat/commit-cadence-diagnostic (merged 1356f88) |
| 33 | [committed-delta-interface](live-dictation-ux/committed-delta-interface/ticket.md) — carry committed text separately from the pending tail | S | done | 32 | feat/committed-delta-interface (merged 78f87fc) |
| 34 | [incremental-typing](live-dictation-ux/incremental-typing/ticket.md) — type committed words as they are committed (**amends ADR 0011**, sign-off given) | M | done | 32, 33 | feat/incremental-typing (merged 5eeca4d) |
| 35 | [streaming-vs-batch-quality](live-dictation-ux/streaming-vs-batch-quality/ticket.md) — measure streaming vs batch **accuracy against ground truth** (gates nothing, blocks nothing) | M | to do | — | — |
| 39 | [accuracy-levers](live-dictation-ux/accuracy-levers/ticket.md) — measure which input-side lever (LF high-pass, gain/clipping, streaming-vs-batch) actually moves accuracy, **before** building a filter | M | done | — | feat/accuracy-levers (merged ba4f234) |

---

## Epic 3 — [whisper.cpp backend](whisper-cpp-backend/epic.md) · P1

A second, **selectable** `Transcriber` on whisper.cpp, integrated **in-process** (`ctypes`, model
resident from service start) and **batch-only**, supporting the full ggml model range and defaulting to
`medium-q5_0` on GPU devices and `small-q8_0` on CPU-only ones. The **default engine stays
`whistle`** (the Whistle model on the Needle 3 engine), so shipped live/incremental typing is unchanged; choosing whisper trades it for
accuracy — **~4.5× fewer errors than Whistle** with `-nt` (~1.8× on the CLI default; see the
[`-nt` addendum](research/whisper-cpp-provider/004-addendum-nt-decode.md)) at ~1.7–3 s per 25 s
utterance on the Radeon 780M. Evidence:
[provider research](research/whisper-cpp-provider/REPORT.md) ·
[streaming research](research/whisper-cpp-streaming/REPORT.md) · **proposed ADR 0021** (needs sign-off).

| # | Ticket | Size | Status | Depends on | Branch |
|---|---|---|---|---|---|
| 41 | [whisper-model-store](whisper-cpp-backend/whisper-model-store/ticket.md) — catalogue + pinned sha256/size acquisition of ggml models from HF | S | done | — | feat/whisper-model-store (merged 7da409e) |
| 42 | [whisper-engine-build](whisper-cpp-backend/whisper-engine-build/ticket.md) — build `libwhisper.so` + ggml (CPU/Vulkan); prerequisites checked, never installed | M | done | — | feat/whisper-engine-build (merged 3478b78) |
| 43 | [whisper-transcriber-adapter](whisper-cpp-backend/whisper-transcriber-adapter/ticket.md) — in-process `ctypes` `Transcriber`, model resident from startup, `supports_streaming = False` | M | done | 41, 42 | feat/whisper-transcriber-adapter (merged 6e0a61d) |
| 44 | [setup-backend-and-model-arguments](whisper-cpp-backend/setup-backend-and-model-arguments/ticket.md) — `ges2t setup [backend] [model]`, model argument ported to needle | M | done | 41, 42 | feat/setup-backend-and-model-arguments (merged 89a62ef) |
| 45 | [whisper-backend-config-and-validation](whisper-cpp-backend/whisper-backend-config-and-validation/ticket.md) — config, GPU/CPU default rule, **strict startup validation** (carries ADR 0021) | S | done | 43 | feat/whisper-backend-config-and-validation (merged 9c0ae6d) |
| 46 | [whisper-doctor](whisper-cpp-backend/whisper-doctor/ticket.md) — engine library / model / Vulkan checks | S | to do | 41, 42, 43 | — |
| 47 | [engine-naming-and-setup-guidance](whisper-cpp-backend/engine-naming-and-setup-guidance/ticket.md) — one canonical engine spelling + a `whisper.cpp` alias; `setup` prints the config line, and names a missing Vulkan dev package when a GPU is present | S | to do | 45 | — |
| 49 | [whisper-engine-variant-rebuild](whisper-cpp-backend/whisper-engine-variant-rebuild/ticket.md) — re-running `setup` upgrades a CPU-only build to Vulkan once the dev package is installed (variant-aware idempotency) | S | to do | 42 | — |
| 50 | [streaming-default-for-batch-engines](whisper-cpp-backend/streaming-default-for-batch-engines/ticket.md) — selecting a batch-only engine must not require an explicit `streaming.enabled = false`; only an explicit `true` errors | S | to do | 45 | — |

---

## Epic 4 — [Code quality](code-quality/epic.md) · P2

Remove the drift class behind the `whisper.cpp` / `whisper_cpp` trap: the project's **identifier
strings** get **one definition** and are compared through named constants, never as bare literals in
conditionals. A guard test fails when a new bare identifier literal appears in `src/`.

| # | Ticket | Size | Status | Depends on | Branch |
|---|---|---|---|---|---|
| 48 | [centralize-identifier-literals](code-quality/centralize-identifier-literals/ticket.md) — one definition per identifier string, compared via constants (`src/` only; tests keep literals) | M | to do | — | — |

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
| 15 | 32 (the incremental-typing **gate**: its numbers may cancel 33 and 34) |
| 16 | 33 |
| 17 | 34 |
| 18 | 35 (independent: **nothing depends on it and it gates nothing**) |
| 19 | 36 (corrective: the clipboard write timeout) |
| 20 | 37 (pin the real-seam payload delivery) |
| 21 | 38 (the `paste_keys` default) · 34 (incremental typing; disjoint files apart from a `pipeline.py` docstring) |
| 22 | 19 (docs) · 39 (accuracy levers; disjoint files) |
| 23 | 40 (corrective: the hotkey advice the tool prints) |
| 24 | 41, 42 (whisper foundation: model store + engine build; disjoint files) |
| 25 | 43, 44 (adapter · setup command; disjoint files) |
| 26 | 45 (config + startup validation) · 46 (doctor) |
| 27 | 47 (engine naming + setup guidance; depends on 45) |
| 28 | 48 (identifier constants; independent) |
| 29 | 46 · 47 · 49 (doctor · setup guidance · variant rebuild) |
| 30 | 50 (engine-aware streaming default) |

Serialization is also required wherever two tickets touch the same file: `10 → 11/12`
(`cli.py`, `doctor.py`), `11 → 16` (`pipeline.py`), `17 → 18` (`pipeline.py`).

## Decisions

All 22 decision records live in [`adr/`](../adr/). Start with
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
