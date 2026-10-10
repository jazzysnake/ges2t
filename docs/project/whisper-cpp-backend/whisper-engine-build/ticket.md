# whisper.cpp engine build

- **Priority:** P1
- **Status:** in review
- **Complexity:** M
- **Epic:** ../epic.md

## Current state / context

The in-process shape (ADR 0021) needs `libwhisper.so` plus the `libggml*.so` libraries, built for
**this machine**. The provider research built them by hand from source and verified the result:
whisper.cpp `v1.9.5`, a CPU build and a `-DGGML_VULKAN=ON` build, both used successfully. The GPU
build is what makes `medium-q5_0` ~2 s instead of ~6× real time.

Per the user's decision, `ges2t setup` **builds** this, but **never installs packages**: it checks its
prerequisites and fails with the exact command. It also **never fetches build tooling**.

Prerequisites on Arch (all in `extra`/`core`, verified on this machine):

```
sudo pacman -S base-devel cmake vulkan-headers spirv-headers shaderc
```

(`cmake`, `spirv-headers`, `vulkan-headers`, `shaderc` are all packaged — verified 2026-10-10. The
Vulkan set is only needed for the **GPU** build; a CPU-only build needs just a C++ toolchain and
`cmake`.)

## Definition of done

- [ ] A builder that **clones whisper.cpp at a pinned revision/tag**, configures with `cmake`, builds
      `libwhisper.so` + the `libggml*.so` libraries, and installs them into
      `~/.local/share/ges2t/engine/whisper-cpp/<version>/` — mirroring Needle's
      `engine/<version>/libneedle.so` layout.

      **Left unticked on purpose: the end-to-end run is USER-OWNED.** The builder is implemented and
      the recipe is verified (a real `cmake` 4.4.4 configure/build of the `whisper` target in scratch,
      plus the real install step driven over that output and loaded with `ctypes` — see
      `001-comment.md`), but `cmake` is **not installed on this machine** and this role may not
      install it. User-owned steps, in order:

      ```
      sudo pacman -S base-devel cmake vulkan-headers spirv-headers shaderc
      uv run ges2t setup whisper.cpp small-q5_0   # once ticket 44 wires ensure_engine() into setup
      ```

      (To exercise the builder alone before ticket 44, call `ges2t.whisper_engine.ensure_engine()`.)
- [x] **Vulkan when the Vulkan runtime and dev headers are present; CPU-only otherwise.** The chosen
      feature set is recorded and surfaced (so `doctor` and the user can see which was built).
- [x] **Prerequisite detection**: check for a C++ compiler, `cmake`, and — for the GPU build —
      `vulkan-headers`, `spirv-headers` and a shader compiler. On a missing prerequisite, fail with a
      typed error naming **the exact package/command**. **Never install, never `sudo`, never fetch
      build tooling.**
- [x] **Idempotent**: re-running against an already-built engine verifies and does nothing else.
- [x] Network is used **only** to clone whisper.cpp at the pinned revision — nothing else (ADR 0018).
- [x] `tests/` cover the prerequisite check and the idempotent path with a **fake** builder (no real
      cmake in the suite). **Falsify**: simulate a missing `cmake` → the typed error listing it; watch
      the test fail without the check, revert.
- [x] `uv run pytest` and `uv run mypy src/ges2t tests` green; report counts.
- [x] `001-comment.md` records the pinned revision, the exact prerequisite package list, the
      configure/build commands, and the CPU-vs-Vulkan detection rule.

## Notes

- The research's real commands are the starting point — reuse them rather than inventing a build:
  `git clone --depth 1 …`, `uvx cmake -B build -DCMAKE_BUILD_TYPE=Release -DWHISPER_BUILD_TESTS=OFF`,
  `cmake --build build --target whisper-cli` (the library targets are what this ticket needs; the CLI
  is **not** used by the in-process shape).
- **Do not** require or install `whisper-cli`; the integration is `ctypes`, not a subprocess.
- **No keystrokes, no clipboard, no windows, no microphone, no `sudo`, no service changes.** No
  package installs.
- Review follow-ups from `002-review.md` (four non-blocking findings) are recorded in
  `003-comment.md`: the `001-comment.md` falsification-count correction, the `SubprocessRunner`
  spawn-`OSError` wrap plus a narrowed docstring, the now-falsifiable clone-timeout test, and the
  "only `vulkan/vulkan.h` missing" test.

## Related tickets

- blocks: ../whisper-transcriber-adapter/ticket.md, ../setup-backend-and-model-arguments/ticket.md,
  ../whisper-doctor/ticket.md
- related: ../../research/whisper-cpp-provider/REPORT.md §2.1 (the build) and §8 (Vulkan)
- related ADRs: ../../../adr/0018-no-runtime-network.md, ../../../adr/0006-needle3-closed-engine-accepted-risk.md
