# 002 — review: variant-aware engine rebuild (ticket 49)

- **Branch:** `feat/whisper-engine-variant-rebuild`, tip `b4d7536cdf50f54cd0b387d719a22a5abe3ed751`
- **Base:** `main` @ `ce8b4d7` (the merge of ticket 47 / `05603a2`)
- **Reviewed diff:** `git diff main...feat/whisper-engine-variant-rebuild` (three-dot)
- **Reviewer:** independent (did not write the change)

## Verdict: **pass**

The variant-aware policy is real, the guard rails are load-bearing, and every arm of the
definition of done is either mechanically falsified-and-recovered here or independently
reproduced. Non-blocking findings and test-strength gaps are listed under **Risks**.

The two DoD counts reproduce exactly:

```
$ timeout 300 uv run pytest -q
1216 passed in 8.16s

$ rm -rf .mypy_cache __pycache__ && timeout 300 uv run mypy src/ges2t tests
Success: no issues found in 64 source files
```

Baseline independently confirmed (not just arithmetically): I archived `05603a2` and ran its
suite with the branch's own venv and `PYTHONPATH` (no network):

```
$ git archive 05603a2 | tar -x -C /tmp/base56
$ PYTHONPATH=/tmp/base56/src .../.venv/bin/python -c "import ges2t; print(ges2t.__file__)"
/tmp/base56/src/ges2t/__init__.py
$ PYTHONPATH=/tmp/base56/src .../.venv/bin/python -m pytest --collect-only -q | tail -1
1194 tests collected in 2.74s
```

`git diff main...HEAD -- tests/ | grep -c '^+def test_'` = **22**, removed = **0**,
parametrize added = **0**; 1194 + 22 = 1216. ✔

---

## Per-criterion findings

### DoD 1 — variant-aware no-op ("no-op iff already as good as the machine can now build")

**Met.** `_satisfies(installed, desired) = installed is VULKAN or installed is desired`, with
`desired = _desired_variant(preference, fresh_probe)`.

Reproduced, not read. Neutering `_satisfies` both ways:

| Probe (mutation, then `git checkout --`) | Command | Result |
|---|---|---|
| `return True` (never rebuild) | `pytest -q tests/test_whisper_engine.py` | **2 failed**, 85 passed — `test_ensure_engine_upgrades_a_cpu_build_when_vulkan_becomes_available`, `test_ensure_engine_vulkan_preference_over_an_existing_cpu_build_errors` |
| `return False` (always rebuild) | same | **8 failed**, 79 passed — incl. keeps-vulkan / keeps-cpu / idempotent / cpu-preference / `force` |

The CPU→Vulkan upgrade arm:

```
$ pytest -q tests/test_whisper_engine.py::test_ensure_engine_upgrades_a_cpu_build_when_vulkan_becomes_available
1 passed
```
(and it is the test that failed under the `return True` probe — non-vacuous).

### DoD 2 — configured preference respected

**Met.** `gpu = "cpu"` → no upgrade; `gpu = "auto"` → upgrade; `gpu = "vulkan"` + cannot build →
typed error, never a silent CPU run.

| Preference / machine | Observed behaviour | Evidence |
|---|---|---|
| `cpu`, built CPU, Vulkan now available | **no rebuild** (`builds == 1`) | `test_ensure_engine_does_not_upgrade_a_cpu_build_when_the_preference_is_cpu` |
| `cpu`, fresh, Vulkan available | **builds CPU** | `test_ensure_engine_builds_cpu_for_a_cpu_preference_even_with_vulkan_available` |
| `cpu`, **built Vulkan**, Vulkan available | **no downgrade** (`builds == 1`) | `test_ensure_engine_does_not_downgrade_a_vulkan_build_under_a_cpu_preference` |
| `auto`, built CPU, Vulkan available | **rebuilds Vulkan** | DoD-1 table above |
| `vulkan`, fresh, no Vulkan | `PrerequisiteError` w/ `pacman` line, `builds == 0` | `test_ensure_engine_vulkan_preference_without_vulkan_is_a_reported_error` |
| `vulkan`, **existing CPU build**, no Vulkan | `PrerequisiteError`, no rebuild, engine byte-identical | see inline run below |

Independent inline run for the "even over an existing CPU build" case:

```
pre-existing build variant: cpu
PrerequisiteError raised. contains 'spirv-headers': True
contains 'sudo pacman -S': True
full message: cannot build the whisper.cpp engine (vulkan): missing Vulkan headers
(vulkan-headers); SPIR-V headers (spirv-headers); GLSL shader compiler (shaderc). |
Install the prerequisites, then re-run this command: |
    sudo pacman -S base-devel cmake vulkan-headers spirv-headers shaderc
builder.builds = 1 (expected 1: no CPU build attempted)
cpu engine byte-identical: True
```

Falsification of the silent-CPU guarantee (degrade `_desired_variant` so `vulkan` falls back to
the machine): the existing-build case then **returns the CPU build with no error** and
`test_ensure_engine_vulkan_preference_over_an_existing_cpu_build_errors` **fails** (`DID NOT
RAISE PrerequisiteError`). The guard is load-bearing.

The preference crosses in **exactly once** and via the **one** enum:

```
$ grep -rn GpuPreference src/ges2t
whisper_engine.py:1346/1348   <- build policy: `if preference is GpuPreference.CPU/VULKAN`
whisper_cpp.py:694/696        <- runtime device choice (resolve_device)
both `from ges2t.transcribers.whisper_cpp_config import ...`
```
`resolve_device` and `_desired_variant` have the same three-way shape over the same `StrEnum`,
so build policy and runtime cannot drift.

`ensure_engine` never reads a config file — its only path reads are the engine manifest
(`read_build` → `json.loads(path.read_text())` at 1006), and its only non-stdlib imports are
`default_data_dir` and `GpuPreference`:

```
$ grep -n "^from\|^import" src/ges2t/whisper_engine.py
... (stdlib) ...
from ges2t.model_store import default_data_dir
from ges2t.transcribers.whisper_cpp_config import GpuPreference
```

Falsifying the crossing: making `_gpu_preference` ignore the config (`return DEFAULT_GPU`) breaks
**3 tests**:

```
FAILED tests/test_setup_command.py::test_plan_setup_reads_the_gpu_preference_from_the_config
FAILED tests/test_setup_command.py::test_an_invalid_gpu_preference_is_a_setup_error
FAILED tests/test_cli.py::test_setup_reads_the_gpu_preference_from_the_config_file
3 failed, 110 passed
```

So `config.toml [engine.whisper_cpp] gpu → plan_setup → ensure_engine(preference=…)` is
instrumented end to end (`test_cli.py` drives `main(["setup", ...])` through `plan_setup` into a
`SetupDependencies` fake that records the preference).

### DoD 3 — `--rebuild` escape hatch

**Met, and justified in `001-comment.md`/CLI help.** It is wired
`ges2t setup --rebuild → run_setup(rebuild=) → _run_whisper(force=) → ensure_engine(force=)`.

It still honours the preference and the prerequisite gate (inline, real code):

```
G1 force+vulkan-pref+no-vulkan: PrerequisiteError: cannot build ... missing ... spirv-headers ...
   builder.builds = 0
G2 force+cpu-pref+vulkan-available: variant = cpu  requested = cpu  builds = 1
G3 force rebuilds an already-best build: 1 build (the forced one)
G4 no force no-op builds = 0
```

So `--rebuild` bypasses **only** the no-op; it cannot force a CPU run under `gpu="vulkan"` nor a
Vulkan run under `gpu="cpu"`.

### DoD 4 — safe replacement / no half-tree `verify_build` accepts

**Met.** `WhisperCppBuilder.build` assembles libraries + manifest in a sibling staging directory
(`_staging_directory`, `mkdtemp(..., dir=destination.parent)`), verifies it (`verify_build(staging)
is None → raise`), then `_replace_directory` renames the old tree aside and the new one in,
restoring on failure.

Falsified by the exact break the ticket names (install straight into the destination + move the
Vulkan check after the copy):

```
FAILED ...::test_a_successful_vulkan_rebuild_replaces_the_manifest_and_adds_the_library
FAILED ...::test_a_failed_rebuild_leaves_the_previous_build_intact
FAILED ...::test_builder_writes_a_manifest_read_back_by_read_build
... (16 failed, 71 passed)
```

`test_a_failed_rebuild_leaves_the_previous_build_intact` asserts the previous `libwhisper.so.1.9.5`
keeps its **inode** and `verify_build(destination) == original` — i.e. byte-for-byte untouched — plus
that no `.engine.staging-*` / `.engine.old` is left behind. Non-vacuous. `verify_build` itself
rejects a half-tree via `not build.libraries.ggml or not build.libraries.complete()`.

### DoD 5 — tests falsify the rule (each arm broken and reverted)

**Met.** All four arms reproduced above; every mutation was reverted with
`git checkout -- src/ges2t/whisper_engine.py` (or `setup_command.py`) and `git status --porcelain`
was empty after each. `git diff --stat` empty at the end.

### DoD 6 — pytest + mypy green, counts, no behaviour change beyond the policy

**Met.** 1216 passed / 64 files 0 errors (above). Scope is exactly the rebuild policy: the diff
touches `whisper_engine.py`, `setup_command.py`, `cli.py` and their tests; `src/ges2t/config.py`
and `src/ges2t/pipeline.py` are **untouched**:

```
$ git diff main...HEAD --name-only -- src/ges2t/config.py src/ges2t/pipeline.py
(empty)
$ uv run ruff check <6 touched files>  ->  All checks passed!
```

No binary or model blob is committed (`git diff --numstat` has no `-` entries).

### DoD 7 — `001-comment.md` records the policy and the `engine.json` fields read

**Met.** The comment states the matrix, the crossing point, the `engine.json` fields read
(`variant` for the decision; `entry_library`/`libraries` existence via `verify_build`), and
explicitly that the stored `vulkan` map is **not** trusted (re-probed live).

### DoD 8 — user-owned manual E2E

**Correctly left unticked.** The ticket forbids the agent from `sudo`/package installs; the
comment hands the exact steps to the user. I also could not run it (see could-not-verify).

---

## Falsification campaign (real output)

Every probe: mutate → `rm -rf .mypy_cache __pycache__` → `timeout … pytest -q …` →
`git checkout -- <file>` → `rm -rf .mypy_cache __pycache__`.

| # | Guard broken | Tests failed | Reverted clean |
|---|---|---|---|
| A | `_satisfies` → `return True` | 2 (`…upgrades_a_cpu_build…`, `…vulkan_preference_over_an_existing_cpu_build_errors`) | ✔ |
| B | `_satisfies` → `return False` | 8 (idempotency, keeps-vulkan ×2, keeps-cpu, cpu-pref, no-downgrade, force, broken-progress) | ✔ |
| C | `_desired_variant` ignores `CPU` | 2 (`…builds_cpu_for_a_cpu_preference…`, `…does_not_upgrade_…_preference_is_cpu`) | ✔ |
| D1 | remove the explicit `vulkan` gate | **0** (see risk R1) | ✔ |
| D2 | `vulkan` preference degrades to machine | 1 (`…over_an_existing_cpu_build_errors`) | ✔ |
| E | `vulkan` read from the **stored** manifest instead of a fresh probe | 1 (`…upgrades_a_cpu_build_when_vulkan_becomes_available`, `assert 1 == 2`) | ✔ |
| F | install straight to destination + Vulkan check after copy | 16 incl. `test_a_failed_rebuild_leaves_the_previous_build_intact` | ✔ |
| H | `_gpu_preference` ignores the config | 3 (2× setup_command, 1× cli) | ✔ |

Probe E is the mechanical proof of DoD-note 3 ("the machine is re-probed"): if the policy trusted
the stale `vulkan` map in `engine.json` (which the first, CPU build recorded as empty), the second
call with `spirv-headers` present would **not** upgrade and the upgrade test fails.

After the whole campaign: `git status --porcelain` empty, no mutated `src/`.

---

## Risks / recommended follow-ups (non-blocking)

- **R1 — the explicit `vulkan`-preference gate is redundant for the *error* but load-bearing for the
  *notice*, and no test locks the ordering.** Removing
  `if preference is GpuPreference.VULKAN and not vulkan.available: raise` fails **zero** tests: the
  following `check_prerequisites(desired)` raises identically (D1). But without it, an existing CPU
  build under `gpu="vulkan"` emits a **misleading notice before the error** (measured):
  `notices emitted before the error: ['rebuilding: the Vulkan development packages are now available (was CPU-only)']`.
  Recommend a follow-up test: `gpu="vulkan"` + no Vulkan + existing build ⇒ `PrerequisiteError` **and
  no notice**. (Or delete the gate if the notice is judged harmless — but then the `_notify` ordering
  is the only thing standing between the user and the false line.)
- **R2 — one new test is weak.** `test_a_failed_fresh_build_leaves_no_engine_verify_build_accepts`
  keeps passing under the Probe-F staging break, because `_install_libraries` raises on the missing
  entry library *before* it ever creates the destination. It asserts `verify_build(...) is None`,
  which is also true for an empty directory. A stronger sibling would place a valid manifest + one
  named library and assert `verify_build` rejects it — though that is really a `verify_build` unit
  test, and `verify_build`'s `libraries.complete()` already covers it.
- **R3 — dead branch in `_rebuild_reason`.** The `f"rebuilding: {installed.variant.label} -> {desired.label}"`
  fallback is unreachable under the current policy (the only rebuild is CPU→VULKAN, which takes the
  first branch). Harmless, but it is a branch no test can reach.
- **R4 — `--rebuild` prints no "why".** `installed = None if force else verify_build(target)` means a
  forced rebuild emits no notice. Intended-looking, but a user forcing a rebuild gets no line. Minor
  UX; not part of the DoD.
- **R5 — notice wording.** The DoD's illustrative line names `spirv-headers`; the implementation says
  "the Vulkan development packages are now available (was CPU-only)". One line, says why, matches the
  ticket's *intent*; only the example's exact wording differs. No change requested.
- **R6 — `force` with `gpu="vulkan"` and Vulkan unavailable** is covered only by my inline run (G1),
  not by a committed test. Cheap regression guard to add.

## Could-not-verify

- **The real end-to-end** (install `spirv-headers` via `pacman`, real `git clone` + `cmake` build,
  then `"variant": "vulkan"` in `engine.json`). Requires `sudo`, package installs and the network —
  all forbidden to this reviewer. It is the ticket's user-owned, explicitly unticked item.
- **A real `WhisperCppBuilder` run against real `git`/`cmake`** (the staging swap and the
  `libggml-vulkan` failure over a real compiler tree). All builder tests inject `FakeCommandRunner`.
  The `SubprocessRunner` seam itself *is* exercised with **real children** (`sys.executable -c …`,
  `tests/test_whisper_engine.py:1110+`, bounded at 1–30 s), so the subprocess seam is not
  fake-only; it is the *builder composition* over that seam that is untested against a real tree.
- **`default_config_path()` with no config file at the real `$XDG` location** — I relied on the CLI
  test that runs `main(["setup", ...])` with no `--config` (it passes, so the no-config path is
  exercised), but I did not create/read a file under the user's real config directory.
- **No package was installed and no service touched** to test the upgrade path, by mandate.

## What would falsify the central claim

Central claim: *an existing CPU build is replaced by a Vulkan build exactly when the machine can now
build Vulkan and `[engine.whisper_cpp] gpu` permits it — and never otherwise; and a failed rebuild
leaves the previous engine untouched.*

Any one of these would falsify it (the first three are the mutations that **did** break the suite in
Probes A, B, E, F):

1. built `cpu` + Vulkan now available + `gpu=auto` ⇒ **no** rebuild (Probe A reverses this).
2. built `vulkan` ⇒ a new build is requested, or a CPU request is issued (Probe B reverses this).
3. a CPU engine is deleted/overwritten (new inode) after a rebuild that then fails (Probe F).
4. the second call does not upgrade after a dev package appears (Probe E).
5. `gpu=cpu` is not threaded through, so Vulkan is built for a CPU-pinned user (Probe H).
6. `gpu=vulkan` + no Vulkan yields a CPU build or a silent success (Probe D2).

None of these hold in the real code; each fails under the corresponding break.

## Safe to relay to the user?

**Yes.** This is not a measurement/timing artifact — the claims are structural (policy matrix,
idempotency, error typing) and each was falsified-and-recovered. The only numbers relayed are test
counts (1216 / 1194 / 64), independently reproduced.

---

## Merge-collision assessment — ticket 50 (`feat/streaming-default-for-batch-engines`)

Ticket 50's tip moved during this review (was `6833989`, now `00a6d0d`); the assessment below is
against the **current** tip `00a6d0df71c943a71d4be7fcad87e603f9f65a74`. Both branches share base
`ce8b4d7`, so a sequential merge into `main` is the same 3-way merge.

Mechanical simulation (`git merge-tree --write-tree`):

```
$ git merge-tree --write-tree --name-only \
    feat/whisper-engine-variant-rebuild feat/streaming-default-for-batch-engines
e64da1c5fb4f5a8726634161da299f0925719bbf
docs/project/README.md

Auto-merging docs/project/README.md
CONFLICT (content): Merge conflict in docs/project/README.md
Auto-merging src/ges2t/setup_command.py
Auto-merging tests/test_setup_command.py
```

**Which regions each branch changes in `src/ges2t/setup_command.py`:**

- **Ticket 49** (`git diff ce8b4d7...…`, +77/−8): module docstring; the import block
  (`Config`; `BLOCK_NAME`/`DEFAULT_GPU`/`GpuPreference`/`WhisperCppConfig`/`WhisperCppConfigError`;
  `NoticeCallback`); `__all__`; the `SetupPlan` dataclass (new `preference` field);
  the `WhisperEngineInstaller` Protocol signature; `plan_setup` (new `config` kwarg + the
  `_gpu_preference(config)` call); **new** `_gpu_preference()`; `run_setup` (new `rebuild`/`notice`);
  `_run_whisper` (new params, forwarded to the engine).
- **Ticket 50** (`git diff ce8b4d7...…`, +3/−3): **`config_guidance()` only** — the whisper.cpp
  batch-only wording ("no `enabled = false` line needed; `streaming.enabled = true` is refused").

**Verdict: automatic for the Python; one mechanical conflict.**

- `src/ges2t/setup_command.py` — **auto-merges**. The two branches touch disjoint functions
  (`plan_setup`/`run_setup`/`_run_whisper`/`_gpu_preference` vs `config_guidance`), ~40 lines apart,
  no shared hunk.
- `tests/test_setup_command.py` — **auto-merges**.
- `docs/project/README.md` — **conflicts**. The conflict is the adjacent rows 49 and 50 of the
  status table; each branch edited the other's row-neighbour. Resolution is **purely mechanical**:
  keep row 49 = `in review | feat/whisper-engine-variant-rebuild` and row 50 =
  `in review | feat/streaming-default-for-batch-engines`.

I ran the **merged tree** (both branches) end to end to prove the auto-merge is semantically
coherent, not just textually clean:

```
$ git worktree add --detach /tmp/mergecheck feat/whisper-engine-variant-rebuild
$ git merge --no-commit --no-ff feat/streaming-default-for-batch-engines   # only README conflicts
$ grep -n "_gpu_preference\|streaming.enabled = true would be refused" src/ges2t/setup_command.py
403:def _gpu_preference(config: Config | None) -> GpuPreference:
458:            "needed; setting streaming.enabled = true would be refused at startup.",
$ PYTHONPATH=/tmp/mergecheck/src .../.venv/bin/python -m pytest -q
1228 passed in 11.33s
```

1228 = 1216 (ticket 49) + 12 (ticket 50's added tests); both features present and the merged suite
is green. **Resolution needed: only the README.md table rows, mechanical.** The merge worktree and
archives were removed; `git worktree list` is back to the four expected worktrees.

---

## Reviewer hygiene / machine safety attestation

- No implementation files were edited; the only write is this artifact.
- Every mutation probe was reverted; final `git status --porcelain` is empty and no `src/` file is
  modified (`git diff --stat` empty).
- No microphone, clipboard, keystroke, window, notification, `sudo`, package install, service
  change or network access was performed. The base-suite reproduction used a local `git archive`
  and the existing venv only.
- Probe scratch (`/tmp/base56`, `/tmp/mergecheck` worktree) removed; `__pycache__`/`.mypy_cache`
  cleared.
