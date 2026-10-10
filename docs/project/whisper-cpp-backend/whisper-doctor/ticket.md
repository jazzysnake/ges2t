# whisper backend doctor checks

- **Priority:** P1
- **Status:** to do
- **Complexity:** S
- **Epic:** ../epic.md

## Current state / context

`ges2t doctor` runs 22 checks (ADR 0016) and is the user's single diagnostic surface. The whisper
backend adds three new prerequisites that are currently invisible: the **`whisper-cli` binary**, the
**configured model file**, and **Vulkan/GPU availability** (which determines whether `medium q5_0` is
~2 s or ~6× real time). A user whose whisper setup is broken should learn it from `doctor`, not from a
silent failure at dictation time.

## Definition of done

- [ ] A `whisper-cli` check: present and executable at the resolved path, with an actionable fix when
      missing (build instructions or the Arch package, and that `whisper.cpp` is built outside the repo).
- [ ] A **model** check: the configured model file is present, and with the existing `--verify` intent,
      hash-verified against its pinned sha256; name the exact file and expected size.
- [ ] A **GPU/Vulkan** check: report whether a Vulkan device is available and which one; when not,
      report a **WARN** stating the CPU cost (~6× real time) rather than a failure.
- [ ] Each check is only meaningful when the whisper backend is selected; with Whistle selected, they
      are `OK`/skipped rather than false failures.
- [ ] Tests falsify each check: rename the binary → FAIL; point at a corrupt model → FAIL; simulate no
      Vulkan → WARN. Watch each fail, revert.
- [ ] `doctor`'s existing 22 checks are untouched; total count and the two by-design WARNs still hold.
- [ ] `uv run pytest` and `uv run mypy src/ges2t tests` green; report counts.
- [ ] `001-comment.md` records each new check and its message.

## Notes

- Follow the existing check shape exactly (ADR 0016's surface, ticket 12's implementation); do not
  invent a second diagnostic style.
- **No keystrokes, no clipboard, no windows, no microphone, no `sudo`, no service changes, no network.**
  Do not install anything; a missing binary is a diagnosis, not something to fix by installing.

## Related tickets

- depends on: ../whisper-transcriber-adapter/ticket.md, ../whisper-model-store/ticket.md
- related ADRs: ../../../adr/0016-cli-surface.md, ../../../adr/0013-tray-via-pystray-appindicator.md
