# 001 — rebuild hardening: plan and result

Ticket 51. Ticket 49 passed review and recorded six **non-blocking** follow-ups (R1–R6) in its
`002-review.md`. This ticket closes them: the rebuild code is correct today, so this is test
coverage plus a little tidiness and two wording changes (`--rebuild` message, notice wording).

**No behaviour change** beyond the `--rebuild` message and the Vulkan notice wording: the rebuild
policy matrix and the staged-install safety are untouched.

## What I read first

- ticket `51` + `epic.md`
- ticket `49`'s `002-review.md` (the R1–R6 findings, verbatim) and `001-comment.md`
  (the policy matrix and the staging design)
- `src/ges2t/whisper_engine.py` (`ensure_engine`, `_desired_variant`, `_satisfies`,
  `_rebuild_reason`, `WhisperCppBuilder.build`, `_install_libraries`, `verify_build`),
  `src/ges2t/setup_command.py` (`run_setup`/`_run_whisper`), `src/ges2t/cli.py`
  (`_handle_setup`, the `--rebuild` flag) and their tests.

## Baseline (before any change)

```
$ timeout 600 uv run pytest -q
1228 passed in 10.77s

$ rm -rf .mypy_cache __pycache__ && timeout 600 uv run mypy src/ges2t tests
Success: no issues found in 64 source files
```

## R1–R6: fixed or declined

| # | Verdict | What changed |
|---|---|---|
| R1 | **Fixed** | New test `test_ensure_engine_vulkan_preference_without_vulkan_notices_nothing_first` |
| R2 | **Fixed** | Strengthened `test_a_failed_fresh_build_leaves_no_engine_verify_build_accepts` |
| R3 | **Fixed** | Removed the unreachable `_rebuild_reason` fallback (proof below) |
| R4 | **Fixed** | `ensure_engine(force=True)` now emits `rebuilding: forced by --rebuild` |
| R5 | **Fixed** | Notice wording is now the generic `the Vulkan dev packages` (reason below) |
| R6 | **Fixed** | New test `test_ensure_engine_forced_vulkan_preference_without_vulkan_still_errors` |

### R1 — pin the ordering (fixed)

`ensure_engine` keeps the explicit gate
`if preference is GpuPreference.VULKAN and not vulkan.available: raise` **before** the automatic
`_notify(_rebuild_reason())`. The gate duplicates the *error* (the following
`check_prerequisites(desired)` raises the same typed error) but is load-bearing for the *ordering*:
without it, an existing CPU build under `gpu = "vulkan"` prints a false
"the Vulkan dev packages are now available" line **before** the error.

The new test builds a CPU engine, then calls `ensure_engine(preference=VULKAN, notice=…)` on a
machine with no Vulkan and asserts the typed error **and** `messages == []`. Probe 1 below removes
the gate and the test fails with exactly the false notice ticket 49 measured.

### R2 — strengthen the weak test (fixed)

`test_a_failed_fresh_build_leaves_no_engine_verify_build_accepts` used
`FakeCommandRunner(produce_libraries=False)`: `_install_libraries` raises on the missing entry
library *before* it creates anything, so `verify_build(destination) is None` (also true of an empty
directory) passed even under the staging break. It is now a **Vulkan** build that *does* produce a
library set but no `libggml-vulkan`, and it asserts the destination is left **empty**
(`list(destination.iterdir()) == []`), plus no `.engine.staging-*` survives. Probe 2 below shows
that a failed build which copies straight into the install path leaves 13 orphan library files and
the test now fails; the old assertions would still have passed (no manifest ⇒ `verify_build` is
`None`).

### R3 — dead code (fixed: removed, it is unreachable)

The old fallback `f"rebuilding: {installed.variant.label} -> {desired.label}"` could not be
reached: `ensure_engine` only calls `_rebuild_reason` when `installed is not None and not
_satisfies(installed.variant, desired)`, and `BuildVariant` has exactly two members. Exhausting the
4 `(installed, desired)` pairs leaves exactly one non-satisfied pair:

```
$ uv run python - <<'PY'
from ges2t.whisper_engine import BuildVariant, _satisfies
members = list(BuildVariant)
print("BuildVariant members:", [m.value for m in members])
print("non-satisfied (installed, desired) pairs -- the only ones that reach _notify:")
for installed in members:
    for desired in members:
        if not _satisfies(installed, desired):
            print(f"  {installed.value} -> {desired.value}")
PY
BuildVariant members: ['cpu', 'vulkan']
non-satisfied (installed, desired) pairs -- the only ones that reach _notify:
  cpu -> vulkan
```

So the only reachable call is CPU → Vulkan, which the first branch handled. The fallback is gone;
`_rebuild_reason()` now takes no arguments and returns the one sentence, with a docstring stating
that a future policy adding a second reason must add its own sentence. **No test covers the removed
branch because no input can reach it** — that is the point.

### R4 — `--rebuild` says why (fixed)

`installed = None if force else verify_build(target)` meant a forced rebuild emitted no notice. A
forced rebuild now emits `rebuilding: forced by --rebuild` (new `_forced_rebuild_reason()`), so it
reads as forced rather than identical to the automatic variant-aware rebuild. The notice is emitted
**after** the explicit-Vulkan gate (same place as the automatic one), so it is never printed before
a `PrerequisiteError` either (see R6). The existing force test now also asserts the message.

### R5 — notice wording (fixed: generic, not `spirv-headers`)

**Decision: keep it generic.** The DoD's example names `spirv-headers`, but the notice cannot
truthfully name one package: the machine may have been missing any subset of `vulkan-headers`,
`spirv-headers` and `shaderc`, and the installed build's manifest records their *presence at build
time*, not a single cause. Naming `spirv-headers` would be a false claim whenever the piece that
appeared was `vulkan-headers` or `shaderc`. For consistency with the shared
`vulkan_dev_package_hint` (whose whole purpose is "the GPU path is one package set away"), the
notice now uses the project's established short term for that set — **"the Vulkan dev packages"**,
the same term `cli.py` ("install the Vulkan dev packages") and `doctor.py` ("Vulkan runtime and dev
packages") use — instead of the previous "Vulkan development packages".

Message: `rebuilding: the Vulkan dev packages are now available (was CPU-only)`.

### R6 — the missing case (fixed)

New `test_ensure_engine_forced_vulkan_preference_without_vulkan_still_errors`: an existing CPU
build, `preference=VULKAN`, `force=True`, no Vulkan ⇒ typed `PrerequisiteError` naming the exact
`pacman` line, `builder.builds` unchanged, and **no forced notice before the error** (mirroring
R1). Probe 4 shows that without the gate plus the preference handling, this call silently builds
CPU (`DID NOT RAISE`).

## Falsification (every probe: mutate `src/`, run the targeted test, `git checkout --`, confirm clean)

All six probes were run from the worktree; `git status --porcelain` was empty after each revert.

| Probe | Guard broken | Test(s) run | Result |
|---|---|---|---|
| 1 | remove the explicit `vulkan` gate in `ensure_engine` | R1, R6, and the two pre-existing `vulkan`-preference tests | **R1 + R6 fail**; the two pre-existing error tests **pass** (gate redundant for the error) |
| 2 | install libraries straight into the destination + move the Vulkan check after the copy | `test_a_failed_fresh_build_leaves_no_engine_verify_build_accepts` | **fails**: 13 orphan library files left at the destination |
| 3 | drop the `if force: _notify(_forced_rebuild_reason())` | `test_ensure_engine_force_rebuilds_an_already_best_build` | **fails**: `[] != ['rebuilding: forced by --rebuild']` |
| 4 | remove the gate **and** make `_desired_variant` ignore the `vulkan` pin | `test_ensure_engine_forced_vulkan_preference_without_vulkan_still_errors` | **fails**: `DID NOT RAISE PrerequisiteError` (silent CPU build) |
| 5 | revert the notice wording to "Vulkan development packages" | `test_ensure_engine_upgrades_a_cpu_build_when_vulkan_becomes_available` | **fails** on the exact message |
| 6 | drop `engine_directory=tmp_path` from the model-override test | `test_load_uses_the_model_override` | **fails** in this environment (real Vulkan engine installed — see below) |

### Probe 1 output (R1 false notice, verbatim)

```
>       assert messages == []
E       AssertionError: assert ['rebuilding:...as CPU-only)'] == []
E         Left contains one more item: 'rebuilding: the Vulkan dev packages are now available (was CPU-only)'
```

and for R6:

```
>       assert messages == []
E       AssertionError: assert ['rebuilding:...by --rebuild'] == []
E         Left contains one more item: 'rebuilding: forced by --rebuild'
```

### Probe 2 output (R2, verbatim)

```
>       assert list(destination.iterdir()) == []
E       AssertionError: assert [PosixPath('/...gml.so'), ...] == []
E         Left contains 12 more items, first extra item: PosixPath('.../engine/libwhisper.so.1.9.5')
```

### Probe 4 output (R6, verbatim)

```
>       with pytest.raises(PrerequisiteError) as caught:
E       Failed: DID NOT RAISE PrerequisiteError
```

## Unrelated environmental finding (not caused by this ticket)

During this work the **user's real whisper.cpp engine was installed** at the default XDG data dir
(`~/.local/share/ges2t/engine/whisper-cpp/v1.9.5/engine.json`, `"variant": "vulkan"`, real 46 MB
`libggml-vulkan.so`), built 18:36–18:38 — outside any command in this ticket. That exposed one
**non-hermetic test**: `test_load_uses_the_model_override` constructed
`WhisperCppTranscriber(WhisperCppConfig(), …)` with the *default* engine directory, so `gpu = auto`
consulted the real store, resolved Vulkan, and asserted `use_gpu=True` where the test expected
`False`. It fails identically on clean `main` (no branch changes):

```
$ cd <main worktree> && uv run pytest -q tests/test_whisper_cpp.py::test_load_uses_the_model_override
FAILED tests/test_whisper_cpp.py::test_load_uses_the_model_override
```

Since this ticket is test hardening and the DoD requires green, I made that one test hermetic by
pinning `engine_directory=tmp_path` (exactly what its sibling load tests already do), and proved
the pin load-bearing in Probe 6. The user's engine was **not** touched, moved or deleted.

## Final verification (plain commands, real output)

```
$ timeout 600 uv run pytest -q
1230 passed in 8.95s

$ rm -rf .mypy_cache __pycache__ && timeout 600 uv run mypy src/ges2t tests
Success: no issues found in 64 source files
```

`1228 → 1230` is the two new tests (R1, R6); R2 strengthens an existing test, R4 extends an existing
one, and the wording/R3 changes add none. No `src/ges2t/` behaviour changed beyond
`_rebuild_reason` (dead branch removed), the `--rebuild` notice, and the notice wording; the
rebuild policy matrix and the staged-install safety are untouched.
