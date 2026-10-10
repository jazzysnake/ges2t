# review (redo) — dedupe the GPU-path hint onto one source of truth

- **Ticket:** `docs/project/whisper-cpp-backend/engine-naming-and-setup-guidance/ticket.md`
- **Branch:** `feat/engine-naming-and-setup-guidance` (tip `f367d80`)
- **Scope of this review:** the **dedupe** only — merge `7ee36eb`, dedupe `2aaa11f`, comment `f367d80`.
  This supersedes the `002-review.md` verification of the alias/block-resolution/guidance deliverables,
  which are re-checked here for regression.
- **Merge-base:** `18855e7` (== `main`; the three-dot diff resolves against `main` directly)

## Verdict

**`pass`** — the GPU-path sentence has one definition, `setup` and `doctor` both render it through it,
and the single-source property is pinned by equality tests that fail when broken. No regression to the
ticket's deliverables. `pytest` `1194 passed`, `mypy` `0 / 64`.

## 1. One source of truth — PASS

**`setup_command.py` no longer contains the hint.** Grep for the removed symbols returns nothing (only
a docstring pointer to the shared helper):

```
$ grep -rn "cpu_only_build_advice\|CpuOnlyBuildAdvice\|_unique_packages\|_HOST_BUILD_PACKAGES" src/ges2t/setup_command.py
(none)
$ grep -n "check_prerequisites\|HostSystemProbe\|Prerequisite\|PACMAN_PREFIX\|BuildVariant" src/ges2t/setup_command.py
(none)
```

`setup_command.__all__` no longer exports them, and the module imports only the engine pieces it still
needs (`DEFAULT_WHISPER_MODEL_CPU/GPU`, `EngineBuild`, `SystemProbe`, `default_whisper_model`,
`ensure_engine`). The `PACMAN_PREFIX` / `PACKAGE_*` hits that remain are all in `whisper_engine.py`
(the source of truth), not in `setup_command.py`.

**`whisper_engine.vulkan_dev_package_hint(status)` is the wording** — the only place the sentence is
built:

```python
def vulkan_dev_package_hint(status: VulkanStatus) -> str | None:
    if status.runtime is None or status.available:
        return None
    packages = status.missing_packages
    return (
        "a Vulkan runtime is present, but the GPU build is missing "
        f"{', '.join(packages)}, so the engine builds CPU-only; install them with "
        f"'{PACMAN_PREFIX} {' '.join(packages)}' and rebuild with "
        "'ges2t setup whisper.cpp <model>' to enable the GPU path"
    )
```

A grep for the distinctive phrases (`engine builds CPU-only`, `GPU build is missing`,
`enable the GPU path`) finds them **only** in `whisper_engine.py` (plus doctor's *summary* line, a
different field — see §2).

**`cpu_only_build_hint(build, *, system=None)` adds only a variant gate + re-probe** and returns the
shared sentence verbatim — no wording, no package list, no formatting:

```python
    if build.variant is not BuildVariant.CPU:
        return None
    probe = system if system is not None else HostSystemProbe()
    return vulkan_dev_package_hint(inspect_vulkan(probe))
```

`cli.py` now imports it from `ges2t.whisper_engine` and prints the returned string unchanged.

## 2. `doctor.py` byte-identical to `main` — PASS

```
$ git diff main...HEAD -- src/ges2t/doctor.py
(empty)
$ git diff main..HEAD -- src/ges2t/doctor.py
(empty)
```

It still renders the shared sentence as the fix (its own `summary` line is a different field):

```
src/ges2t/doctor.py:971:        hint = whisper_engine.vulkan_dev_package_hint(vulkan)
```

Real run, both sides agree:

```
doctor status : WARN
doctor fix    : a Vulkan runtime is present, but the GPU build is missing spirv-headers, so the engine builds CPU-only; install them with 'sudo pacman -S spirv-headers' and rebuild with 'ges2t setup whisper.cpp <model>' to enable the GPU path
shared sentence: a Vulkan runtime is present, but the GPU build is missing spirv-headers, so the engine builds CPU-only; install them with 'sudo pacman -S spirv-headers' and rebuild with 'ges2t setup whisper.cpp <model>' to enable the GPU path
doctor fix == shared sentence: True
```

## 3. The single-source property is genuinely pinned — PASS

**The equality holds** (real run, `cpu_only_build_hint` vs the shared function on the same probe):

```
shared sentence     : a Vulkan runtime is present, but the GPU build is missing spirv-headers, so the engine builds CPU-only; install them with 'sudo pacman -S spirv-headers' and rebuild with 'ges2t setup whisper.cpp <model>' to enable the GPU path
cpu_only_build_hint == shared : True
no runtime (CPU build)      : None
vulkan build                : None
dev piece since installed   : None
```

**D1 — reword setup's wrapper** (`return hint + " (reworded)"`), then:

```
$ pytest -q tests/test_whisper_engine.py tests/test_setup_command.py tests/test_cli.py -k "cpu_only or shared or hint"
FAILED tests/test_whisper_engine.py::test_cpu_only_build_hint_is_exactly_the_shared_sentence
FAILED tests/test_setup_command.py::test_cpu_only_hint_is_the_shared_sentence_itself
FAILED tests/test_setup_command.py::test_cpu_only_hint_names_every_missing_dev_package
3 failed, 15 passed, 160 deselected
```

Matches `003-comment.md` exactly. Reverted with `git checkout --` + cache clear; sha256 matched.

**D2 — make `doctor` build its own fix string.** Two mutations, both reverted:

- *Still-plausible fix* (self-built string that happens to keep the `pacman` line) →
  `1 failed, 2 passed, 118 deselected` (only `test_vulkan_fix_is_the_same_sentence_setup_prints`).
- *Fully reworded fix* (no `pacman` line) →
  `3 failed, 118 deselected`
  (`test_vulkan_warns_with_the_missing_dev_package_when_a_gpu_is_present`,
  `test_vulkan_names_only_spirv_headers_when_that_is_the_missing_piece`,
  `test_vulkan_fix_is_the_same_sentence_setup_prints`).

The `003-comment.md` claim of `3 failed` is reproducible with the strong mutation; the weaker
plausible-string mutation fails only the equality test. Either way **the property is pinned** — a
rewording on either side breaks a test. (See residual risk R1 for the one nuance.)

Pristine state restored after every probe: `sha256sum -c` matched and `git diff --stat` was empty.

## 4. No regression to the ticket's deliverables — PASS

Real runs, `uv run python` / `uv run ges2t`:

```
normalize_engine('whisper.cpp') = whisper_cpp
plan_setup('whisper.cpp',None)  = SetupPlan(backend=<SetupBackend.WHISPER_CPP: 'whisper.cpp'>, model='medium-q5_0')
plan_setup('whisper_cpp',None)  = SetupPlan(backend=<SetupBackend.WHISPER_CPP: 'whisper.cpp'>, model='medium-q5_0')
alias selector engine/blocks  = whisper_cpp {'whisper_cpp': {'model': 'm'}}
dotted block blocks           = {'whisper_cpp': {'model': 'm'}}
both-spellings ERROR          = .../config.toml: engine blocks 'engine.whisper_cpp' and 'engine.whisper.cpp' are both spellings of the 'whisper_cpp' engine; keep one
unknown-engine ERROR          = .../config.toml: transcriber.engine = 'whisper' is not a known engine; known engines: 'whistle', 'whisper_cpp'; did you mean 'whisper_cpp'? ('whisper.cpp' is the spelling 'ges2t setup' takes; the config value is 'whisper_cpp')
unknown-backend ERROR         = unknown backend 'whisper'; valid backends: needle, whisper.cpp, whisper_cpp; did you mean 'whisper_cpp'? | valid= ('needle', 'whisper.cpp', 'whisper_cpp')
```

```
$ timeout 60 uv run ges2t setup whisper
ges2t: setup: unknown backend 'whisper'; valid backends: needle, whisper.cpp, whisper_cpp; did you mean 'whisper_cpp'?
  hint: run 'ges2t setup --help' to list the accepted backends and models
EXIT=1
```

Real `cli.main` printed guidance and the **three-way** GPU hint:

```
########## whisper.cpp, runtime present, spirv missing ##########
Setup complete.
Add this line to /tmp/.../c.toml to select the engine (ges2t never edits it for you):

    transcriber.engine = "whisper_cpp"

whisper.cpp is batch-only, so also set, under [streaming]:

    enabled = false

a Vulkan runtime is present, but the GPU build is missing spirv-headers, so the engine builds CPU-only; install them with 'sudo pacman -S spirv-headers' and rebuild with 'ges2t setup whisper.cpp <model>' to enable the GPU path
exit=0

########## whisper.cpp, NO runtime ##########   -> no hint line, exit=0
########## whisper.cpp, Vulkan build ##########  -> no hint line, exit=0
########## needle (default) ##########
Setup complete.
The default engine is 'whistle', so no line is needed in /tmp/.../c.toml.
exit=0
```

An empty-probe run also confirms `cpu_only_build_hint` returns `None` for CPU build + no runtime, and
`None` for a Vulkan build — the diagnostic is still stdout, exit `0`, and silent when it should be.

## 5. Regression / hygiene — PASS

```
$ timeout 600 uv run pytest -q
1194 passed in 11.91s
PYTEST_EXIT=0

$ rm -rf .mypy_cache __pycache__ && timeout 600 uv run mypy src/ges2t tests
Success: no issues found in 64 source files
MYPY_EXIT=0
```

`git status --porcelain` is clean; `HEAD` is still the implementer's tip `f367d80` (no `src/` mutation
was committed); probe files removed; no stray processes. No binary/model in the diff. `whisper_engine.py`
gained only `cpu_only_build_hint` + its `__all__` entry (24 lines) relative to `main`; `doctor.py` is
byte-identical.

**Test-count arithmetic:** `main` (`18855e7`) = 1158; the branch adds 30 `def test_` functions and 6
`parametrize` expansions vs `main`, with **0 removed** (`git diff main...HEAD -- tests/ | grep -c
'^-def test_'` = 0) → 1194. The `003-comment.md` "1188 after the merge, +6 dedupe" is the same
history read the other way (1159 pre-merge + 29 ticket-46 + 6 dedupe = 1194).

## Residual risks

- **R1 — the CLI test uses `shared in out`, not equality.** `test_cli.py::test_setup_whisper_cpp_prints_the_cpu_only_hint_when_a_gpu_lost_the_build` asserts the shared sentence is a **substring** of stdout, so an append-only rewording of `cpu_only_build_hint` would pass it. The equality tests in `test_whisper_engine.py` / `test_setup_command.py` do catch that, so the invariant is still pinned; tightening the CLI assertion to equality would remove the last gap. Minor.
- **R2 — the shared sentence is coupled to `whisper_engine.py`.** That is the intended home (next to `inspect_vulkan`), but it means the one sentence is now load-bearing for both `setup` and `doctor`; a future edit to it changes both outputs by design.
- **R3 — the `[streaming] enabled = false` printed line remains** by design; `main` already carries a ticket-50 plan to make it redundant. Keep the printed text in sync when ticket 50 lands.
- **R4 — `doctor` still has its own `summary` wording** for the same fact (`"a Vulkan runtime is present at …, but the GPU build is missing …"`). That is a different field (the check summary, not the fix) and was already the case on `main`; only the `fix` is the shared sentence.

## Could not verify

- The real whisper.cpp build / a live GPU hint on the user's machine — `cmake` is absent and the build is user-owned; exercised through fakes and the real `vulkan_dev_package_hint` / `inspect_vulkan`.
- A live dictation — user-owned per the epic.
- Each reason is itself true: no build was attempted, `cmake` is not installed, and no dictation was run.

## What would falsify the single-source claim

It would be falsified if (a) `setup_command.py` again built any part of the sentence, (b)
`cpu_only_build_hint` returned anything other than `vulkan_dev_package_hint(inspect_vulkan(probe))`
(or a non-`None` for a Vulkan build / no runtime), (c) `doctor`'s `fix` stopped equalling the shared
function, or (d) the equality tests did not fail when either side was reworded. All four held under
direct probing.

## Safe to merge?

**Yes.** The dedupe removes the duplicate wording, both `setup` and `doctor` render the one shared
sentence, the property is pinned by equality tests that fail when broken, the ticket's own deliverables
are intact, and the suite/mypy are green with a clean tree. The only residual is a test-strength nit
(R1), which does not affect the shipped behaviour.
