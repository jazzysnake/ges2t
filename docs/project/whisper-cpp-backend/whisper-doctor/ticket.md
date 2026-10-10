# whisper backend doctor checks

- **Priority:** P1
- **Status:** in review
- **Complexity:** S
- **Epic:** ../epic.md

## Current state / context

`ges2t doctor` runs 22 checks (ADR 0016) and is the user's single diagnostic surface. The whisper
backend adds prerequisites that are currently invisible: the **built engine libraries**, the
**configured model**, and **Vulkan availability** (which determines whether `medium-q5_0` runs in ~1.7 s
or ~19 s for the 25 s clip, and which default model applies). A user whose whisper setup is broken should learn it
from `doctor`, not from a startup failure they cannot interpret.

## Definition of done

- [x] A **built-engine** check: the whisper `libwhisper.so` + `libggml*.so` are present at the resolved
      data-dir path, and it reports whether the build is **CPU-only or Vulkan**.
- [x] A **model** check: the configured model is present, and with the existing `--verify` intent,
      hash-verified against its pinned sha256; name the exact file and expected size.
- [x] A **GPU/Vulkan** check: report whether a Vulkan device is available and which one; when not,
      a **WARN** stating the CPU cost (`medium-q5_0` ~19 s, still faster than realtime; RTF 1.30) and
      that the default model is `small-q8_0`.
- [x] When `transcriber.engine = "whisper_cpp"` and `streaming.enabled = true`, the checks explain
      the **startup rejection** (this config will not start) rather than reporting a healthy-but-refused
      state.
- [x] With `needle` selected, the whisper checks are `OK`/skipped rather than false failures.
- [x] **No model-vs-GPU sizing check** (explicitly out of scope per the user).
- [x] `doctor`'s existing 22 checks are untouched; the total count and the two by-design WARNs still
      hold.
- [x] Tests falsify each new check: rename the library → FAIL; corrupt the model → FAIL; simulate no
      Vulkan → WARN. Watch each fail, revert.
- [x] `uv run pytest` and `uv run mypy src/ges2t tests` green; report counts.
- [x] `001-comment.md` records each new check and its exact message.

## Notes

- Follow the existing check shape exactly (ADR 0016's surface, ticket 12's implementation); do not
  invent a second diagnostic style.
- **No keystrokes, no clipboard, no windows, no microphone, no `sudo`, no service changes, no network.**
  A missing library is a diagnosis, not something to fix by building or installing here.

## Related tickets

- depends on: ../whisper-transcriber-adapter/ticket.md, ../whisper-engine-build/ticket.md,
  ../whisper-model-store/ticket.md
- related ADRs: ../../../adr/0016-cli-surface.md, ../../../adr/0013-tray-via-pystray-appindicator.md
