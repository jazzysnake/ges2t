# whisper-engine-build — implementation notes

- **Branch:** `feat/whisper-engine-build`
- **Worktree:** `.worktrees/whisper-engine-build`
- **Date:** 2026-10-10
- **Status of this write-up:** final (ticket `in review`; the end-to-end build remains user-owned)

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

## Prerequisite package list (Arch `core`/`extra`; the ticket records them verified 2026-10-10)

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
cmake -S <src> -B <build> -DCMAKE_BUILD_TYPE=Release -DWHISPER_BUILD_TESTS=OFF \
      -DWHISPER_BUILD_EXAMPLES=OFF -DCMAKE_BUILD_WITH_INSTALL_RPATH=ON \
      -DCMAKE_INSTALL_RPATH='$ORIGIN'          # [+ -DGGML_VULKAN=ON] for the GPU build
cmake --build <build> --target whisper -j <jobs>
```

The `$ORIGIN` flags are this ticket's one addition to the research's configure
line; why they are needed is below.

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

### Relocatability: the one addition to the research's flags

The research only ever ran `whisper-cli` **from `build/bin/`**, where the libs are
found through CMake's absolute build-tree `RUNPATH` (`.../build/bin/`). Copying
that set anywhere else leaves `libwhisper.so` unable to find `libggml.so.0`. The
shipped builder therefore configures with

```
-DCMAKE_BUILD_WITH_INSTALL_RPATH=ON -DCMAKE_INSTALL_RPATH=$ORIGIN
```

which bakes a `$ORIGIN` `RUNPATH` into the produced libraries, so the installed
set is self-contained and loads by absolute path with `ctypes` and no
`LD_LIBRARY_PATH`. **Verified in scratch** with `uvx cmake` 4.4.4 (offline):

```
$ readelf -d build/bin/libwhisper.so.1.9.5 | grep -E 'RUNPATH|NEEDED.*ggml'
 0x0000000000000001 (NEEDED)  Shared library: [libggml.so.0]
 0x0000000000000001 (NEEDED)  Shared library: [libggml-cpu.so.0]
 0x0000000000000001 (NEEDED)  Shared library: [libggml-base.so.0]
 0x000000000000001d (RUNPATH) Library runpath: [$ORIGIN]

$ cp -a build/bin/lib{whisper,ggml}*.so* <other dir>   # relocated, build dir still exists
$ python3 -c 'import ctypes;l=ctypes.CDLL("<other dir>/libwhisper.so");l.whisper_version.restype=ctypes.c_char_p;print(l.whisper_version())'
whisper_version: b'1.9.5-dev'
```

The scratch build ran against the **already-present reference tree** as `-S`
(no clone, no network); the shipped builder clones the pinned tag instead. The
scratch build tree and the relocated copy were both removed afterwards
(`~/.cache/ges2t-verify-engine-build`).

Exact scratch configure/build commands (both exited 0):

```
uvx --offline cmake -S <ref-tree> -B build -DCMAKE_BUILD_TYPE=Release \
    -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_EXAMPLES=OFF \
    -DCMAKE_BUILD_WITH_INSTALL_RPATH=ON -DCMAKE_INSTALL_RPATH='$ORIGIN'
uvx --offline cmake --build build --target whisper -j 16
```

Produced in `build/bin/` (CPU variant, every entry either a real file or a
symlink to one in the same directory):

```
libwhisper.so -> libwhisper.so.1 -> libwhisper.so.1.9.5
libggml.so -> libggml.so.0 -> libggml.so.0.26.0
libggml-base.so -> libggml-base.so.0 -> libggml-base.so.0.26.0
libggml-cpu.so  -> libggml-cpu.so.0  -> libggml-cpu.so.0.26.0
```

`whisper-cli` is **not** built (the `whisper` target does not depend on it) and
is **not** installed — the integration is in-process `ctypes` (ADR 0021).

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
`engine.json` manifest (revision, tag, variant, library file names, timestamp,
and the detected Vulkan pieces) so `doctor` and the user can see what was built —
and *why that variant* — without re-probing the machine.

## User-owned steps (left unticked — `cmake` may not be installed here)

`cmake` is absent and installing it needs `sudo`, which this role is forbidden
from running. The end-to-end build/full `setup` verification is therefore
**USER-OWNED**:

```
sudo pacman -S base-devel cmake vulkan-headers spirv-headers shaderc
```

then, once the ticket's `ensure_engine()` is wired into `ges2t setup` (ticket 44 owns
that wiring, so `ges2t setup` does not build the engine yet):

```
uv run ges2t setup whisper.cpp small-q5_0
```

To exercise the builder alone before ticket 44 lands (network: the pinned clone;
writes only under `~/.local/share/ges2t/` and `~/.cache/ges2t/`):

```
uv run python -c 'from ges2t.whisper_engine import ensure_engine; print(ensure_engine().summary)'
```

## What was built

`src/ges2t/whisper_engine.py` — the engine builder, with the Protocol seams ADR 0019
requires (`SystemProbe`, `CommandRunner`, `EngineBuilder`), and
`tests/test_whisper_engine.py` (63 tests, all with fakes for the build path: no
real `git`/`cmake`/compiler and no network in the suite; four tests drive the real
`SubprocessRunner` with a trivial bounded `python -c` to pin its failure modes).

Public surface:

- `ensure_engine(...) -> EngineBuild` — idempotency check, CPU-vs-Vulkan choice,
  prerequisite gate, then the build. The module's composition point.
- `check_prerequisites(variant, system) -> PrerequisiteReport`,
  `prerequisites_for(variant)`, `inspect_vulkan(system) -> VulkanStatus`.
- `WhisperCppBuilder` (`EngineBuilder`) — clone, configure, build the `whisper`
  target, install the library set, write the manifest.
- `read_build(directory)` / `verify_build(directory)` — the recorded build.
- `engine_dir(version)` = `<data_dir>/engine/whisper-cpp/v1.9.5/`;
  `engine.json` records tag, revision, variant and the library file names.

## Real-machine prerequisite detection (this machine, 2026-10-10)

A probe script against the real machine (`ensure_engine` uses exactly this, since
it defaults every seam to the real implementation) prints:

```
$ uv run python - <<'PY'
from ges2t.whisper_engine import HostSystemProbe, engine_dir, inspect_vulkan
status = inspect_vulkan(HostSystemProbe())
print("vulkan runtime :", status.runtime)
print("vulkan headers :", status.headers)
print("spirv headers  :", status.spirv_headers)
print("shader compiler:", status.shader_compiler)
print("available      :", status.available)
print("summary        :", status.summary())
print("engine_dir()   :", engine_dir())
PY
vulkan runtime : /usr/lib/libvulkan.so
vulkan headers : /usr/include/vulkan/vulkan.h
spirv headers  : None
shader compiler: /usr/bin/glslc
available      : False
summary        : CPU-only: SPIR-V headers (spirv-headers)
engine_dir()   : /home/matee/.local/share/ges2t/engine/whisper-cpp/v1.9.5
```

That is the same verdict as the raw `pacman -Q` above (`spirv-headers` and `cmake`
absent) — the rule picks the CPU variant here, and would pick Vulkan on a machine
with all four pieces.

`ensure_engine()` with the real probe on this machine (no builder injected; a
throwaway directory, so it cannot touch the user's data dir):

```
PrerequisiteError:
cannot build the whisper.cpp engine (cpu): missing cmake (cmake).
Install the prerequisites, then re-run this command:
    sudo pacman -S base-devel cmake
```

Nothing was cloned, compiled or installed: the check runs **before** the builder
and the builder is never constructed.

## The install step, driven over the real CMake output

The scratch build's `build/bin/` was run through the module's own install step
(`_install_libraries`) into a fake engine directory, then loaded with `ctypes`:

```
installed: ['libggml-base.so', 'libggml-base.so.0', 'libggml-base.so.0.26.0',
            'libggml-cpu.so', 'libggml-cpu.so.0', 'libggml-cpu.so.0.26.0',
            'libggml.so', 'libggml.so.0', 'libggml.so.0.26.0',
            'libwhisper.so', 'libwhisper.so.1', 'libwhisper.so.1.9.5']
symlink libwhisper.so -> libwhisper.so.1
verify_build: True
summary: whisper.cpp v1.9.5 (CPU-only) at .../engine
ctypes whisper_version(): b'1.9.5-dev'
```

So the install half of the builder is verified against **real** CMake output, not
only against the test fake's output. The scratch tree was removed afterwards.

Failure modes are typed, never raw: `SubprocessRunner` turns a non-zero exit into
`BuildFailedError` naming the command and the tail of its stderr, a timeout into
one naming the budget, and a missing executable into one naming it (tests:
`test_subprocess_runner_wraps_*`). `git` is **not** in the prerequisite report —
the ticket fixes that report's package list to the toolchain, `cmake` and the
Vulkan dev packages, and `base-devel` does not ship `git` — so a missing `git` is
caught in `WhisperCppBuilder._clone` with its own actionable message
(`sudo pacman -S git`), deliberately rather than by widening the ADR's package
line.

## Falsification (each watched to fail, then reverted)

All six reversions were `git checkout -- src/ges2t/whisper_engine.py`, run with
everything committed first so the revert was exact; the tree was green again
immediately after each (`63 passed`, `git status` clean), and all six were
re-run against the final committed code.

1. **Drop the prerequisite gate in `ensure_engine`** (the `raise` becomes `_ =
   report`) →
   `test_ensure_engine_fails_before_building_when_a_prerequisite_is_missing`
   FAILED: `DID NOT RAISE PrerequisiteError`.
2. **Drop `cmake` from `prerequisites_for(CPU)`** → three tests FAILED:
   `test_cpu_requirement_set_is_the_base_devel_and_cmake_line`,
   `test_missing_cmake_is_reported_with_its_package_and_command`,
   `test_ensure_engine_fails_before_building_when_a_prerequisite_is_missing`
   (the last with `DID NOT RAISE PrerequisiteError`). This is the ticket's
   required "missing `cmake`" falsification.
3. **Skip the idempotency short-circuit** (`if installed is not None:` →
   `if False:`) →
   `test_ensure_engine_is_idempotent_and_builds_only_once` (`assert 2 == 1`) and
   `test_ensure_engine_keeps_an_existing_vulkan_build_without_rebuilding` FAILED.
4. **Drop `-DCMAKE_BUILD_WITH_INSTALL_RPATH=ON -DCMAKE_INSTALL_RPATH=$ORIGIN`**
   → `test_builder_configures_a_release_library_only_origin_rpath_build` FAILED.
5. **Drop the `git` guard in `_clone`** →
   `test_builder_raises_a_typed_error_when_git_is_absent` FAILED:
   `DID NOT RAISE BuildFailedError`.
6. **Neuter `SubprocessRunner`'s `CalledProcessError` wrapping** (re-raise the
   raw error) → `test_subprocess_runner_wraps_a_nonzero_exit_with_stderr` FAILED
   with the untyped `CalledProcessError` escaping.

## Verify

Run from the worktree (`986 = 923 baseline + 63 new`):

```
$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 57 source files
$ uv run pytest -q
986 passed in 6.42s
```

Baseline before this ticket: 923 passed. This ticket adds 63 tests
(`tests/test_whisper_engine.py`); none deleted, skipped or weakened. The two
commands were re-run after every edit above and after the status/README/comment
updates, with the same result and a clean `git status`.

## Cleanup

- Scratch build tree `~/.cache/ges2t-verify-engine-build` (clone source, CMake
  build tree, relocated copy, install probe) removed; nothing built was ever put
  in the repo, and `git status` is clean apart from the committed work.
- No process was left running; `uvx`, `cmake` and `python3` probe runs all
  exited.
- No network was used by this ticket's work: the recipe was verified with
  `uvx --offline cmake` against the already-present reference tree, and the
  shipped builder's `git clone` was only ever exercised through the fake runner.
