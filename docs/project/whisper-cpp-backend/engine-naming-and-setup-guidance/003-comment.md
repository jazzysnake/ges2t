# 003 — dedupe: one GPU-path hint, shared by `setup` and `doctor`

- **Branch:** `feat/engine-naming-and-setup-guidance`
- **Worktree:** `.worktrees/engine-naming-and-setup-guidance`
- **Trigger:** follow-up after `002-review.md` (pass); ticket 46 (`4b798bf`) added a shared
  `vulkan_dev_package_hint` to `whisper_engine.py`, and this branch had its own copy in
  `setup_command.py` — two sources for one user-facing message.
- **Status of this write-up:** in progress (written incrementally; see the commits)

## What moved where

`main` was merged first (`7ee36eb`), resolving the `docs/project/README.md` conflict row-wise:
rows 41–46 keep `main`'s values (46 `done … merged 4b798bf`), rows 48/49/50 and Epic 4 come from
`main`, and row 47 stays `in review` on `feat/engine-naming-and-setup-guidance`.

The single wording now lives only in `src/ges2t/whisper_engine.py`:

- `VulkanStatus.missing_packages` (ticket 46) — the dev packages not found (runtime excluded).
- `vulkan_dev_package_hint(status: VulkanStatus) -> str | None` (ticket 46) — **the** sentence:
  *"a Vulkan runtime is present, but the GPU build is missing `<pkgs>`, so the engine builds
  CPU-only; install them with 'sudo pacman -S `<pkgs>`' and rebuild with 'ges2t setup whisper.cpp
  <model>' to enable the GPU path"*; `None` with no runtime and when every dev piece is present.
- `cpu_only_build_hint(build: EngineBuild, *, system=None) -> str | None` (**new**) — the
  `ges2t setup` side. It adds only the **variant gate** (`build.variant is CPU`) and re-probes
  with `inspect_vulkan`, then returns `vulkan_dev_package_hint(...)` **verbatim**. No wording, no
  package list, no formatting of its own. Added to the module `__all__`.

Removed from `src/ges2t/setup_command.py` (the duplicate):

- `CpuOnlyBuildAdvice`, `cpu_only_build_advice`, `cpu_only_build_hint`,
  `_unique_packages`, `_HOST_BUILD_PACKAGES`;
- the now-unused imports `PACMAN_PREFIX`, `PACKAGE_CMAKE`, `PACKAGE_TOOLCHAIN`, `BuildVariant`,
  `HostSystemProbe`, `Prerequisite`, `check_prerequisites`.

`src/ges2t/cli.py` now imports `cpu_only_build_hint` from `ges2t.whisper_engine` (it is no longer
a `setup_command` symbol). `doctor.py` is **unchanged** — it already rendered
`whisper_engine.vulkan_dev_package_hint` as its fix (ticket 46).

`setup_command.py` keeps `config_guidance` only; its docstring now points at the shared helper.

## Behaviour preserved

- **`ges2t setup`**: the alias, the block resolution and the printed
  `transcriber.engine = "whisper_cpp"` line are untouched, **including** the
  `[streaming] enabled = false` line (ticket 50 will remove the need for it, not this ticket).
- The GPU diagnostic is still **stdout, exit 0**, fires only when a runtime is present and a dev
  piece is missing, is **silent** with no runtime and for a Vulkan build, and names the missing
  package plus the exact `pacman` line.
- **`ges2t doctor`**: its three-way Vulkan check (no runtime / runtime-but-missing-dev /
  Vulkan built) is untouched; its fix is the same shared sentence.

## Single-source property, pinned by tests

For the same probe, three assertions tie the two commands to the one function:

- `tests/test_whisper_engine.py::test_cpu_only_build_hint_is_exactly_the_shared_sentence`
  — `cpu_only_build_hint(build, system=probe) == vulkan_dev_package_hint(inspect_vulkan(probe))`.
- `tests/test_setup_command.py::test_cpu_only_hint_is_the_shared_sentence_itself` — the same
  equality from the `setup_command` consumer's perspective; plus the every-missing-package case
  asserts equality too.
- `tests/test_doctor.py::test_vulkan_fix_is_the_same_sentence_setup_prints`
  — `check_vulkan(...).fix == vulkan_dev_package_hint(inspect_vulkan(probe))`.
- `tests/test_cli.py::test_setup_whisper_cpp_prints_the_cpu_only_hint_when_a_gpu_lost_the_build`
  — the exact shared sentence appears in the real `cli.main` stdout.

Real run proving both sides agree:

```
doctor fix    : a Vulkan runtime is present, but the GPU build is missing spirv-headers, so the engine builds CPU-only; install them with 'sudo pacman -S spirv-headers' and rebuild with 'ges2t setup whisper.cpp <model>' to enable the GPU path
doctor fix == shared sentence: True
cpu_only_build_hint(cpu_build, probe) == vulkan_dev_package_hint(inspect_vulkan(probe)): True
```

## Verification (real output)

```
$ uv run pytest -q
1194 passed in 8.37s

$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 64 source files
```

`1188` immediately after the merge (ticket 46 added 29 tests to the 1159 baseline); the dedupe
adds 6 net test cases. No test was removed or weakened:
`git diff -- tests/ | grep '^-def test_'` is empty on this branch.

## Falsification (single-source pin, each reverted)

| # | What was reworded | Command | Result |
|---|---|---|---|
| D1 | `setup`'s wrapper returns the shared sentence plus `" (reworded)"` | `pytest tests/test_whisper_engine.py tests/test_setup_command.py tests/test_cli.py -k "cpu_only or shared or hint"` | `3 failed, 15 passed` |
| D2 | `doctor`'s fix built its own string instead of the shared helper | `pytest tests/test_doctor.py -k "shared or same_sentence or missing_dev_package or only_spirv"` | `3 failed, 118 deselected` |

Both reverted with `git checkout -- <file>`; `git diff --stat` was empty before the final run
and the tree is clean.
