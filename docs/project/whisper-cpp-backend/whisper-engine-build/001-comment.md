# whisper-engine-build — implementation notes

- **Branch:** `feat/whisper-engine-build`
- **Worktree:** `.worktrees/whisper-engine-build`
- **Date:** 2026-10-10
- **Status of this write-up:** in progress (written incrementally)

## Pinned engine revision

| fact | value |
|---|---|
| upstream | `https://github.com/ggml-org/whisper.cpp.git` |
| tag | `v1.9.5` |
| commit (pinned) | `d1be6fde11ac6e0407606b4e42fe72d34add8037` |
| ggml version | `0.26.0` (from the produced `libggml.so.0.26.0`) |
| install directory | `~/.local/share/ges2t/engine/whisper-cpp/v1.9.5/` |

The revision is the one the provider research actually built and measured
([`REPORT.md` §2.1](../../research/whisper-cpp-provider/REPORT.md)); the local
reference tree at `~/.cache/ges2t-discovery/whisper-cpp-provider/whisper.cpp`
confirms `git log -1 --format=%H` and `git describe --tags` for it:

```
$ git -C ~/.cache/ges2t-discovery/whisper-cpp-provider/whisper.cpp log -1 --format='%H %d'; git describe --tags
d1be6fde11ac6e0407606b4e42fe72d34add8037  (grafted, HEAD -> master, tag: v1.9.5, ...)
v1.9.5
```

## Prerequisite package list (Arch, all verified in the repos 2026-10-10)

```
sudo pacman -S base-devel cmake vulkan-headers spirv-headers shaderc
```

- **CPU-only build:** `base-devel` (C/C++ compiler + `make`) and `cmake`.
  Exact CPU line: `sudo pacman -S base-devel cmake`.
- **GPU (Vulkan) build:** the CPU line plus `vulkan-headers`, `spirv-headers`,
  `shaderc` (provides `glslc`).

Presence on *this* machine (checked, not installed — this is the "what is
missing" evidence the ticket asked for):

```
$ command -v cmake    -> MISSING
$ command -v g++      -> /usr/bin/g++
$ pacman -Q vulkan-headers spirv-headers shaderc vulkan-icd-loader cmake base-devel
vulkan-headers 1:1.4.363.0-1
shaderc 2026.3-1
vulkan-icd-loader 1.4.357.0-1
base-devel 1-2
error: package 'spirv-headers' was not found
error: package 'cmake' was not found
```

`glslc` is present (`/usr/bin/glslc`), `/usr/include/vulkan/vulkan.h` is present,
`/usr/lib/libvulkan.so.1` is present, `/usr/include/spirv/` is **absent**. So
`cmake` and `spirv-headers` are missing, and the build recipe cannot run here —
see "user-owned steps" below.

## Build recipe (from the research, reused rather than invented)

The research built the CLI (`build/bin/whisper-cli`) because its harness was a
subprocess. This ticket needs the **library targets**, so the shipped builder
builds the `whisper` CMake target (which pulls the `ggml` libraries with it):

```
git clone --depth 1 --branch v1.9.5 https://github.com/ggml-org/whisper.cpp.git <src>
cmake -S <src> -B <build> -DCMAKE_BUILD_TYPE=Release -DWHISPER_BUILD_TESTS=OFF
cmake --build <build> --target whisper -j <jobs>
```

Produced by that target, in `<build>/bin/`:

```
libwhisper.so -> libwhisper.so.1 -> libwhisper.so.1.9.5
libggml.so    -> libggml.so.0    -> libggml.so.0.26.0
libggml-base.so -> libggml-base.so.0 -> libggml-base.so.0.26.0
libggml-cpu.so  -> libggml-cpu.so.0  -> libggml-cpu.so.0.26.0
libggml-vulkan.so -> libggml-vulkan.so.0 -> libggml-vulkan.so.0.26.0   (Vulkan build only)
```

and for the Vulkan build only:

```
-DGGML_VULKAN=ON
```

The research's Vulkan build additionally needed `SPIRV-Headers` for
`find_package(SPIRV-Headers)` in `ggml/src/ggml-vulkan`; it was staged in scratch
and pointed at with `-DCMAKE_PREFIX_PATH=.../spirv-headers-prefix`. In the
shipped builder the package is a **prerequisite** (`spirv-headers`) and is found
through CMake's normal system paths — no prefix trick, no staged tree.

## CPU-vs-Vulkan detection rule (ADR 0021)

A Vulkan build is chosen **only when all four** are detected, otherwise the build
is CPU-only and no error is raised:

| piece | probe | Arch package |
|---|---|---|
| Vulkan loader | `libvulkan.so*` in a system library directory | `vulkan-icd-loader` (runtime) |
| Vulkan headers | `vulkan/vulkan.h` on a system include path | `vulkan-headers` |
| SPIR-V headers | `spirv/unified1/spirv.h` on a system include path | `spirv-headers` |
| shader compiler | `glslc` on `PATH` | `shaderc` |

The chosen variant is written into the installed directory as a small
`engine.json` manifest (revision, tag, variant, library names, timestamp) so
`doctor` and the user can see what was built without re-deriving it.

## User-owned steps (left unticked — `cmake` may not be installed here)

`cmake` is absent and installing it needs `sudo`, which this role is forbidden
from running. The end-to-end build/full `setup` verification is therefore
**USER-OWNED**:

```
sudo pacman -S base-devel cmake vulkan-headers spirv-headers shaderc
```

then, once the ticket's `ensure_engine()` is wired into `ges2t setup` (ticket 44):

```
uv run ges2t setup whisper.cpp small-q5_0
```

(Or, to exercise the builder alone before ticket 44 lands, the scratch
verification command recorded in §"Verify" below.)

## Verify

Run from the worktree:

```
uv run pytest -q
rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
```

_(results appended below as they are produced.)_
