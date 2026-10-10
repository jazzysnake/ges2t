# whisper-engine-build — independent review (002)

- **Ticket:** [`ticket.md`](ticket.md)
- **Branch:** `feat/whisper-engine-build` — tip `5a47651d5dadfc0e39f5c633c2fac0c679cc7ddf`
- **Worktree:** `.worktrees/whisper-engine-build`
- **Diff range reviewed:** `git diff main...feat/whisper-engine-build` (three-dot)
- **Reviewer:** ges2t reviewer (independent; implementation not touched)
- **Date:** 2026-10-10

## Verdict: **pass**

Every definition-of-done clause except the deliberately user-owned end-to-end run is implemented and
verified mechanically. I re-ran the suite and `mypy`, and I **falsified** each load-bearing behaviour
(dropped the guard, watched the test fail, reverted). I also drove the **shipped** `WhisperCppBuilder`
end-to-end offline (real `git`, real `cmake` via the offline `uvx` wrapper) and confirmed the
`$ORIGIN` relocatability fix on **real CMake output**, including a discriminating load with the build
tree deleted.

Two documentation nits and two low-severity robustness notes are recorded below; none is blocking.

The one unticked box (the end-to-end build) is **honest**: `cmake` is genuinely absent on this machine
and may not be installed by this role. I verified the recipe it points at as far as is possible
without the network clone URL and the system `cmake`; the residual user-owned step is small and stated.

---

## Per-criterion findings

| # | Definition-of-done item | Verdict | Basis |
|---|---|---|---|
| 1 | Builder clones pinned revision, configures with `cmake`, builds `libwhisper.so` + `libggml*.so`, installs into `…/engine/whisper-cpp/<version>/` | **pass, user-owned end-to-end** | Shipped builder driven end-to-end (probe below): real clone at `v1.9.5` → HEAD `d1be6fd…`, real cmake configure/build of the `whisper` target, real `_install_libraries` + `engine.json`, `ctypes` load `b'1.9.5-dev'`. `engine_dir()` = `…/engine/whisper-cpp/v1.9.5`. The GitHub clone URL and system `cmake` are the user-owned substitutions (box left unticked correctly). |
| 2 | Vulkan when runtime+dev headers present; CPU-only otherwise; chosen feature set recorded/surfaced | **pass** | Exercised all **four** conditions independently (probe): each missing piece → `available=False`, `ensure_engine` picks `cpu`, **no error**. All four present → `vulkan`. `engine.json` records variant + the four probes. |
| 3 | Prerequisite detection (compiler, `cmake`, Vulkan dev pkgs); typed error naming exact package/command; never install/sudo/fetch | **pass** | Real-machine `ensure_engine` raises `PrerequisiteError` naming `cmake (cmake)` and the exact line `sudo pacman -S base-devel cmake`, **before** the builder is constructed (no clone/compile/network). Missing `git` gets its own `BuildFailedError` (`sudo pacman -S git`). Module imports only stdlib; grep shows no HTTP client, no `os.system`, no package-manager call. |
| 4 | Idempotent: re-run verifies and does nothing else | **pass** | Second `ensure_engine()` against a verified build does not call the builder (`builds == 1`); falsified by removing the short-circuit (`assert 2 == 1`). Rebuilds on deleted library or corrupt manifest (tested). |
| 5 | Network only to clone the pinned revision (ADR 0018) | **pass** | Exactly two `git` invocations, both the pin (clone + `rev-parse HEAD`); falsified by injecting a third git call (test failed). The pinned tree has no `FetchContent`/`ExternalProject`/download in the top-level build files, and the e2e build completed with `uvx --offline cmake` — no other network. |
| 6 | `tests/` cover prerequisite + idempotent path with a fake builder; missing-`cmake` falsification | **pass** | 63 new tests, all fakes; the required missing-`cmake` falsification reproduced (see Probes). |
| 7 | `uv run pytest` and `uv run mypy src/ges2t tests` green; report counts | **pass** | `986 passed in 7.49s` (exit 0); `Success: no issues found in 57 source files` (exit 0). Baseline 923 confirmed (`--ignore` the new file) → 923 + 63 = 986. |
| 8 | `001-comment.md` records revision, package list, configure/build commands, CPU-vs-Vulkan rule | **pass (2 nits)** | All present and correct; revision verified against the reference tree. Nit A: falsification #2's "three tests FAILED" is six. Nit B: the `SubprocessRunner` "every failure mode becomes `BuildFailedError`" docstring overstates (see Risks). |
| — | Do not require/install `whisper-cli` (in-process `ctypes`) | **pass** | `--target whisper` builds no CLI; probe output shows `bin/whisper-cli` absent. |
| — | No keystrokes/clipboard/windows/mic/sudo/service changes; no package installs | **pass** | Static: no such call in `src/`. Behavioural: the prerequisite path only reads the filesystem and `PATH`. |

---

## Evidence — commands and real output

### 1. Suite and type check (from the worktree, caches cleared)

```
$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 57 source files            # MYPY_EXIT=0

$ timeout 600 uv run pytest -q
986 passed in 7.49s                                    # PYTEST_EXIT=0 (not 124: process exits)

$ uv run pytest -q --ignore=tests/test_whisper_engine.py
923 passed in 6.95s                                    # baseline, matches the comment

$ uv run pytest tests/test_whisper_engine.py --collect-only -q | tail -1
63 tests collected in 0.07s
```

mypy scope actually reaches `tests/` (ADR 0019's documented trap) — I appended a deliberate type
error to `tests/test_whisper_engine.py` and it was caught, then reverted:

```
tests/test_whisper_engine.py:1089: error: Incompatible return value type (got "str", expected "int")  [return-value]
Found 1 error in 1 file (checked 57 source files)
```

### 2. Falsifications — each mutation watched to fail, then `git checkout --` + cache clear

All six probes below were run against the committed tip; `git status` was clean before and after each.

| probe | mutation | observed failure |
|---|---|---|
| 1 | drop `cmake` from `prerequisites_for` | `6 failed, 57 passed` — incl. `test_cpu_requirement_set_is_the_base_devel_and_cmake_line`, `test_missing_cmake_is_reported_with_its_package_and_command`, `test_ensure_engine_fails_before_building_when_a_prerequisite_is_missing` |
| 2 | `raise PrerequisiteError(report)` → `_ = report` in `ensure_engine` | `DID NOT RAISE PrerequisiteError` (`test_ensure_engine_fails_before_building_when_a_prerequisite_is_missing`) |
| 3 | `if installed is not None:` → `if False:` (idempotency) | `assert 2 == 1`; `test_ensure_engine_is_idempotent_and_builds_only_once` and `test_ensure_engine_keeps_an_existing_vulkan_build_without_rebuilding` FAILED |
| 4 | `if head != request.revision:` → `if False:` | `DID NOT RAISE BuildFailedError` (`test_builder_refuses_a_revision_that_does_not_match_the_pin`) |
| 5 | remove the `git` guard in `_clone` | `DID NOT RAISE BuildFailedError` (`test_builder_raises_a_typed_error_when_git_is_absent`) |
| 6 | `SubprocessRunner`: re-raise raw `CalledProcessError` | untyped `subprocess.CalledProcessError` escaped; `test_subprocess_runner_wraps_a_nonzero_exit_with_stderr` FAILED |
| 7 | typo the `-DCMAKE_BUILD_WITH_INSTALL_RPATH` flag + no-op the Vulkan-library check | `test_builder_configures_a_release_library_only_origin_rpath_build` and `test_builder_raises_when_a_vulkan_build_produced_no_vulkan_library` both FAILED |
| 8 | inject an extra `git --version` call in `_clone` | `assert 3 == 2` — `test_the_only_git_commands_are_the_pinned_clone_and_its_revision_check` FAILED |

Probe 1 detail (the ticket's required falsification):

```
E  AssertionError: assert 'cmake' in 'cannot build the whisper.cpp engine (cpu): missing .\n…\n    sudo pacman -S base-devel'
FAILED tests/test_whisper_engine.py::test_cpu_requirement_set_is_the_base_devel_and_cmake_line
FAILED tests/test_whisper_engine.py::test_vulkan_requirement_set_is_the_epic_package_line
FAILED tests/test_whisper_engine.py::test_missing_cmake_is_reported_with_its_package_and_command
FAILED tests/test_whisper_engine.py::test_prerequisite_kinds_route_to_the_matching_probe
FAILED tests/test_whisper_engine.py::test_ensure_engine_fails_before_building_when_a_prerequisite_is_missing
FAILED tests/test_whisper_engine.py::test_builder_raises_prerequisite_error_when_cmake_is_absent
6 failed, 57 passed in 1.76s
```

### 3. CPU-vs-Vulkan rule — each of the four pieces independently (probe, not a test)

A hand-written `SystemProbe` (each piece removable) driven through the **real** `ensure_engine`:

```
missing=runtime  available=False variant=cpu error=None
missing=headers  available=False variant=cpu error=None
missing=spirv    available=False variant=cpu error=None
missing=shader   available=False variant=cpu error=None
all four present  available=True  variant=vulkan
OK: all four pieces are individually required; any missing -> CPU-only, no error.
```

(Gap in the shipped suite: there is no dedicated test for "only `vulkan/vulkan.h` missing"; the three
other pieces each have one. My probe covers it; recommend a sibling test — low priority.)

### 4. Real-machine detection and the typed prerequisite error (reproduced exactly)

```
$ uv run python - <<'PY'   # inspect_vulkan(HostSystemProbe()) …
vulkan runtime : /usr/lib/libvulkan.so
vulkan headers : /usr/include/vulkan/vulkan.h
spirv headers  : None
shader compiler: /usr/bin/glslc
available      : False
summary        : CPU-only: SPIR-V headers (spirv-headers)
engine_dir()   : /home/matee/.local/share/ges2t/engine/whisper-cpp/v1.9.5

$ uv run python -c 'ensure_engine(directory=<throwaway>)'
PrerequisiteError:
cannot build the whisper.cpp engine (cpu): missing cmake (cmake).
Install the prerequisites, then re-run this command:
    sudo pacman -S base-devel cmake
```

Machine facts confirmed: `command -v cmake` → missing; `g++/cc/c++/make/git/glslc` present;
`/usr/include/vulkan/vulkan.h` present; `/usr/include/spirv/unified1/spirv.h` **absent**;
`/usr/lib/libvulkan.so*` present. The CPU-only verdict is therefore the correct one here.

### 5. `$ORIGIN` relocatability — verified on real CMake output, discriminatingly

`uvx --offline cmake` 4.4.4 was available, so I ran the **shipped configure/build flags** against the
existing reference tree (no clone, no network):

```
$ uvx --offline cmake -S <ref>/whisper.cpp -B /tmp/ges2t-review-cmake-build -DCMAKE_BUILD_TYPE=Release \
      -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_EXAMPLES=OFF \
      -DCMAKE_BUILD_WITH_INSTALL_RPATH=ON -DCMAKE_INSTALL_RPATH='$ORIGIN'
-- ggml version: 0.26.0
-- ggml commit:  d1be6fd
-- Configuring done … Build files have been written …
CONFIGURE_EXIT=0

$ uvx --offline cmake --build /tmp/ges2t-review-cmake-build --target whisper -j 16
[100%] Linking CXX shared library ../bin/libwhisper.so
[100%] Built target whisper
BUILD_EXIT=0
$ ls /tmp/ges2t-review-cmake-build/bin/whisper-cli   # the CLI is NOT built
ls: cannot access '…/bin/whisper-cli': No such file or directory

$ readelf -d bin/libwhisper.so.1.9.5 | grep -E 'RUNPATH|NEEDED.*ggml'
 (NEEDED)  Shared library: [libggml.so.0]
 (NEEDED)  Shared library: [libggml-cpu.so.0]
 (NEEDED)  Shared library: [libggml-base.so.0]
 (RUNPATH) Library runpath: [$ORIGIN]
```
`libggml.so.0.26.0` and `libggml-cpu.so.0.26.0` likewise carry `RUNPATH [$ORIGIN]`.

Contrast: the research's reference build (absolute RUNPATH) — the premise of the fix:
```
$ readelf -d <ref>/build/bin/libwhisper.so.1.9.5 | grep RUNPATH
 (RUNPATH) Library runpath: [/home/matee/.cache/ges2t-discovery/whisper-cpp-provider/whisper.cpp/build/bin:]
```

Discriminating relocatability test (copy the set out, **hide the build dir**, load by absolute path):
```
$ cp -a bin/libwhisper.so* bin/libggml*.so* /tmp/ges2t-review-relocated
$ mv /tmp/ges2t-review-cmake-build/bin /tmp/ges2t-review-cmake-build/bin.hidden
$ python3 -c 'import ctypes; l=ctypes.CDLL("/tmp/ges2t-review-relocated/libwhisper.so"); l.whisper_version.restype=ctypes.c_char_p; print(l.whisper_version())'
whisper_version(): b'1.9.5-dev'
```
With the only alternative resolution path removed, the relocated set loads: the fix is real.

### 6. The shipped builder, driven end-to-end (offline)

Real `git`, real `SubprocessRunner`, real configure/build/install/manifest. Stated substitutions: the
clone URL is a local `file://` path to the same reference tree, and `cmake` is a wrapper around
`uvx --offline cmake` (system `cmake` is not installed).

```
BUILD OK: whisper.cpp v1.9.5 (CPU-only) at /tmp/review-e2e/engine
libraries complete: True
manifest read_build tags: cpu d1be6fde11ac6e0407606b4e42fe72d34add8037
verify_build: True
progress: [0.0, 0.2, 0.3, 0.9, 1.0]
ctypes whisper_version(): b'1.9.5-dev'      # loaded AFTER the scratch tree was deleted on return
scratch empty: True
wrong revision -> BuildFailedError: whisper.cpp v1.9.5 resolved to d1be6fde11ac6e0407606b4e42fe72d34add8037, but ges2t pins deadbeef…; refusing to build an unpinned revision
names the real head: True
manifest present after failure: False
```
The `ctypes` load succeeded only because the scratch tree (clone + build) is deleted before `build()`
returns — so this is a second, independent confirmation of `$ORIGIN`.

Also driving the shipped `_install_libraries` over real CMake output:
```
installed: [libggml-base.so … libggml.so.0.26.0, libwhisper.so]
libwhisper is symlink: True   complete: True   verify_build: True   ctypes whisper_version(): b'1.9.5-dev'
```

### 7. Revision pin and prerequisite basis confirmed against the reference tree

```
$ git -C <ref>/whisper.cpp log -1 --format='%H'; git describe --tags
d1be6fde11ac6e0407606b4e42fe72d34add8037  (grafted, HEAD -> master, tag: v1.9.5)
v1.9.5
$ grep -rn find_package <ref>/ggml/src/ggml-vulkan/CMakeLists.txt
9:find_package(Vulkan COMPONENTS glslc REQUIRED)
14:find_package(SPIRV-Headers CONFIG REQUIRED)
$ grep -n 'target_link_libraries(whisper' <ref>/src/CMakeLists.txt
236:target_link_libraries(whisper PUBLIC ggml Threads::Threads)
```
So the four Vulkan pieces map to real requirements and building `whisper` transitively builds `ggml`.

### 8. Repository hygiene / ADR 0019 style

```
$ git diff --name-status main...feat/whisper-engine-build
M docs/project/README.md
A docs/project/whisper-cpp-backend/whisper-engine-build/001-comment.md
M docs/project/whisper-cpp-backend/whisper-engine-build/ticket.md
A src/ges2t/whisper_engine.py
A tests/test_whisper_engine.py
$ git ls-tree -r --name-only feat/whisper-engine-build | grep -iE '\.(so|bin|o|a|pyc)$'   # none
$ git status --short                                                                     # clean
```
No binary/build artefact is committed; no existing test file was modified or deleted (purely
additive). ADR 0019: `mypy` with `disallow_untyped_defs`/`no_implicit_optional` passes over
`src`+`tests`; no bare `Any`, no `type: ignore`; the three seams (`CommandRunner`, `SystemProbe`,
`EngineBuilder`) are `@runtime_checkable` Protocols and appear as parameter types; the six records
are `@dataclass(frozen=True)`; tuples appear only for homogeneous sequences.

The reference tree and worktree were left unchanged (both `git status` clean), and every probe
directory I created was removed.

---

## Risks (none blocking)

1. **Doc nit A — understated falsification count.** `001-comment.md` §Falsification says dropping
   `cmake` failed "three tests"; the actual mutation fails **six** (table above). The claim's
   direction is right; the number is wrong. `cmake` cannot be dropped "from `prerequisites_for(CPU)`"
   alone because it lives in the shared `common` tuple, so the stated mutation is not quite the one
   that was run. Fix the comment, not the code. *(recommend a follow-up comment edit)*
2. **Doc nit B — overstated `SubprocessRunner` contract.** The class docstring says "every failure
   mode becomes a `BuildFailedError`". `subprocess.run` can also raise `PermissionError` (e.g. a
   non-executable `argv[0]`), which escapes raw:
   ```
   $ python -c 'SubprocessRunner().run(["/etc/passwd"], …)'
   a non-executable file: RAW PermissionError: [Errno 13] Permission denied: '/etc/passwd'
   ```
   Not reachable through the shipped path (`argv[0]` is always a path from `shutil.which` or `"git"`
   after a guard), so this is not a defect in behaviour — only the docstring is broader than the code.
3. **Missing per-piece test.** The suite tests "runtime missing", "spirv missing", "shader missing",
   and "all missing", but not "only `vulkan/vulkan.h` missing". My probe covers it; a one-line test
   would close the gap.
4. **Vulkan build not run here.** Only the CPU recipe was rebuilt; `-DGGML_VULKAN=ON` is exercised
   only by the fake runner (and the detection rule). `spirv-headers` is absent on this machine.
5. **`test_builder_uses_a_bounded_clone_timeout` is near-tautological** (compares the recorded value
   to `CLONE_TIMEOUT_SECONDS`), though `test_builder_bounds_every_command` (`timeout > 0`) backstops it.

## Recommended follow-up tickets

- (cosmetic, same folder) Correct the count in `001-comment.md` §Falsification #2 to six, and quote
  the actual mutation (`common` tuple).
- (tests) Add `test_vulkan_absent_when_the_headers_are_missing`; optionally tighten the
  `SubprocessRunner` docstring or wrap `PermissionError`/`OSError` as `BuildFailedError`.

---

## Could not verify (each reason is itself true)

- **The real end-to-end user-owned build**: `sudo pacman -S … cmake …` was not run (forbidden: no
  `sudo`, no package installs), so `cmake` remains absent from `PATH`; the shipped `ensure_engine()`
  therefore cannot run its build step against the system toolchain here.
- **The network `git clone` from GitHub**: no network is permitted; I substituted a local `file://`
  clone of the same repository at the same tag/HEAD. The clone argv, the tag→HEAD pin, and the
  guard were exercised for real; the transport was local.
- **System `cmake`**: substituted with `uvx --offline cmake` 4.4.4 behind a thin wrapper. The
  configure/build flags are the shipped ones and the artefacts verified are real.
- **The Vulkan build (`-DGGML_VULKAN=ON`) end-to-end**: not built (needs `spirv-headers`, which is
  absent and may not be installed); the reference tree already has a `build-vulkan` with the
  research's absolute RUNPATH, which I inspected but did not rebuild with `$ORIGIN`.
- **`ges2t setup whisper.cpp …` and `ges2t doctor`**: the wiring is owned by tickets 44/45; only
  `whisper_engine.ensure_engine()` exists at this tip.

## What would falsify the central claim

The central claim is: *"the shipped builder clones the pinned `v1.9.5`, builds `libwhisper.so` +
`libggml*.so` with the pinned flags, installs a self-contained set under the engine dir, and is
idempotent and prerequisite-gated."* It would be falsified by any of:

- the end-to-end build producing no `libwhisper.so`, or a set that fails to load after the build
  tree is gone (would falsify `$ORIGIN`); *(I tested this and it held)*
- `git clone --branch v1.9.5` resolving to a different commit than `d1be6fd…` and **not** raising
  (would falsify the pin); *(falsified the guard and confirmed the pin)*
- dropping `cmake` from the required set leaving the suite green (would falsify the prerequisite
  gate); *(dropping it fails six tests)*
- a second `ensure_engine()` invoking the builder again; *(removing the short-circuit fails the test)*
- any network access beyond the clone (e.g. `FetchContent` during configure). *(none present; the
  offline configure/build completed)*

A user-run `uv run python -c 'from ges2t.whisper_engine import ensure_engine; ensure_engine()'` on a
machine with `cmake` installed is the remaining proof that the GitHub transport itself works; every
other link was reproduced here.

## Safe to relay to the user? — **yes**

The core mechanic (pinned revision, CPU-vs-Vulkan rule, prerequisite gate, idempotency, install +
`$ORIGIN` load) is verified with real commands and watched-to-fail falsifications. Relay with the
caveat that the first DoD box stays user-owned: the user must install `cmake` (and the Vulkan dev
packages for a GPU build) and run the build once; I did not and could not perform that step.
