# 002 — review: setup: backend and model arguments

- **Reviewer:** independent (did not write this code)
- **Branch:** `feat/setup-backend-and-model-arguments` @ `06b0a92`
- **Merge-base with `main`:** `27370b6` (`main` has since advanced with ticket 43)
- **Diff reviewed:** `git diff main...feat/setup-backend-and-model-arguments` (three-dot), per the ticket
- **Verdict:** **pass**

The command surface, the validation-before-work property, the GPU/CPU default rule, the
engine-then-model ordering, the partial-failure diagnostic, and the needle-default preservation are
all present and **mechanically falsifiable** — every load-bearing test was broken and watched to
fail, then reverted. The one unticked definition-of-done item (a real end-to-end
`ges2t setup whisper.cpp` build) is correctly unticked and user-owned: `cmake` is not installed and
this role may not install it.

---

## Definition of done — per criterion

| # | Criterion | Verdict | Evidence |
|---|---|---|---|
| 1 | `ges2t setup [backend] [model]`; no-args preserves needle + its default | **pass** | `plan_setup(None, None) == SetupPlan(NEEDLE, "whistle")`; `ensure_model()` default is `"whistle"` (below). |
| 2 | `ges2t setup whisper.cpp [model]` builds engine **and** fetches model, consistent on partial failure | **pass** | Order test + partial-failure test; reorder mutation fails 5 tests. |
| 3 | `ges2t setup needle [model]` accepted, needle model selectable, default `whistle` | **pass** | `plan_setup("needle", "whistle")`; `needle_model()` catalogue; skip-parse mutation fails. |
| 4 | Unknown backend / unknown model / unpublished quant rejected **before** build or download | **pass** | CLI runs + PATH `cmake`/`git` shim logged **no** invocations; data dir stayed empty; fakes' log `== []`. |
| 5 | GPU/CPU default rule, falsifiable (no Vulkan → `small-q8_0`) | **pass** | Inversion breaks 5 tests; joint probe shows the rule and `ensure_engine`'s variant agree on 4 probe shapes. |
| 6 | Idempotent; different model leaves old untouched | **pass** | Always-refetch mutation breaks 4 tests; collapsed-path mutation breaks the second-model test. |
| 7 | No `sudo` / installs / build-tool fetching; prerequisites checked and reported | **pass** | Real host: exact `sudo pacman -S base-devel cmake` line printed by the engine gate; no write. |
| 8 | Network only by setup, only for pinned clone + sha256 downloads (ADR 0018) | **pass** | ADR 0018 AST scan (live: an injected `import httpx` in `setup_command.py` fails it). |
| 9 | Tests drive a fake builder/downloader; each falsified | **pass** | All five falsifications below, in `src/`, reverted with empty `git diff --stat`. |
| 10 | `pytest` + `mypy src/ges2t tests` green; report counts | **pass** | `1049 passed in 6.83s`; `Success: no issues found in 59 source files`. |
| 11 | `001-comment.md` records CLI surface + default rule | **pass** | The comment's four "real runs" reproduced verbatim; the falsification counts are consistent 3-/2-/5-/3-test selections. |
| — | **End-to-end real whisper.cpp build** | **unticked (correct)** | `cmake` MISSING; user-owned and out of role. |

The branch adds **31** tests (18 in `tests/test_setup_command.py`, 7 in `tests/test_cli.py`, 6 in
`tests/test_model_store.py`), so `1049 = 1018 + 31`.

---

## Evidence — commands and real output

### Suite and types (branch tip, clean tree)

```
$ timeout 400 uv run --offline pytest -q
1049 passed in 6.83s
PYTEST_EXIT=0

$ rm -rf .mypy_cache __pycache__ && timeout 400 uv run --offline mypy src/ges2t tests
Success: no issues found in 59 source files
MYPY_EXIT=0
```

`pyproject.toml` `[tool.mypy]` has no `files`/`exclude`, and both `src/ges2t` and `tests` are passed
explicitly. **Falsified that `tests/` is actually in scope** — a deliberate annotation error injected
at the top of `tests/test_setup_command.py`:

```
$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
tests/test_setup_command.py:1: error: Incompatible types in assignment (expression has type "str", variable has type "int")  [assignment]
Found 1 error in 1 file (checked 59 source files)
```

### Falsification probes (each mutated in `src/`, watched fail, reverted)

**(1) Argument parsing — `_parse_backend` made to ignore its token:**

```
FAILED tests/test_setup_command.py::test_explicit_whisper_backend_and_model_is_accepted
FAILED tests/test_setup_command.py::test_unknown_backend_is_typed_and_names_the_valid_set
FAILED tests/test_setup_command.py::test_omitted_whisper_model_uses_the_cpu_default_without_vulkan
FAILED tests/test_setup_command.py::test_omitted_whisper_model_uses_the_gpu_default_with_vulkan
FAILED tests/test_cli.py::test_setup_rejects_an_unknown_backend_before_acquiring
FAILED tests/test_cli.py::test_setup_whisper_cpp_builds_the_engine_then_fetches_the_model
... (11 failed, 79 passed)
```

**(2) Model-name validation — whisper branch carries the raw token:**

```
FAILED tests/test_setup_command.py::test_unpublished_whisper_quant_is_typed_and_names_the_valid_set
FAILED tests/test_setup_command.py::test_unknown_whisper_size_is_rejected
FAILED tests/test_cli.py::test_setup_rejects_an_unpublished_whisper_model_before_acquiring
3 failed, 87 passed
```

The `test_cli.py` failure output **proves the "before any build/download" claim is the thing the test
guards** — with the parse skipped, `small-q5_0` ran to completion:

```
Setting up the whisper.cpp backend and model small-q5_0.
Engine: whisper.cpp v1.9.5 (CPU-only) at .../engine/v1.9.5
Model:  .../ggml.bin
Setup complete.
```

Needle validation is separately falsifiable — replacing `model_store.needle_model(model).name` with
the raw token gives `DID NOT RAISE NeedleModelNameError` (1 failed).

**(3) GPU/CPU default rule — inverted:**

```
FAILED tests/test_setup_command.py::test_default_model_is_small_q8_0_without_vulkan
FAILED tests/test_setup_command.py::test_default_model_is_medium_q5_0_with_vulkan
FAILED tests/test_setup_command.py::test_omitted_whisper_model_uses_the_cpu_default_without_vulkan
FAILED tests/test_setup_command.py::test_omitted_whisper_model_uses_the_gpu_default_with_vulkan
FAILED tests/test_setup_command.py::test_partial_vulkan_selects_the_cpu_default
5 failed, 13 passed
```

**The rule reuses the engine build's own Vulkan detection** (not a re-implementation). Two pieces of
mechanical evidence:

```
$ uv run python -c "from ges2t import setup_command, whisper_engine; \
  print(setup_command.inspect_vulkan is whisper_engine.inspect_vulkan, \
        setup_command.ensure_engine is whisper_engine.ensure_engine)"
True True
```

and a joint probe that drives the **real** `ensure_engine` (fake builder, so no network/build) with
the same probe passed to the model rule:

```
cpu                            model=small-q8_0   engine_variant=cpu     agree=True
vulkan                         model=medium-q5_0  engine_variant=vulkan  agree=True
partial-vulkan(runtime only)   model=small-q8_0   engine_variant=cpu     agree=True
partial-vulkan(no spirv)       model=small-q8_0   engine_variant=cpu     agree=True
```

Diverging from that detection (a naive runtime-only check) is caught by exactly one test:

```
FAILED tests/test_setup_command.py::test_partial_vulkan_selects_the_cpu_default
1 failed, 17 passed
```

`small-q5_0` is rejected because it is genuinely not in the catalogue:
`valid models: tiny-q8_0, base-q8_0, small-q8_0, medium-q5_0, medium-q8_0, large-v3-turbo-q5_0,
large-v3-turbo-q8_0` (no `small-q5_0`).

**(4) Partial-failure ordering — model fetched before engine:**

```
FAILED tests/test_setup_command.py::test_whisper_run_builds_the_engine_then_acquires_the_model
FAILED tests/test_setup_command.py::test_whisper_engine_failure_propagates_and_never_acquires_a_model
FAILED tests/test_setup_command.py::test_whisper_model_failure_after_the_build_is_typed_and_names_state
FAILED tests/test_cli.py::test_setup_whisper_cpp_builds_the_engine_then_fetches_the_model
FAILED tests/test_cli.py::test_setup_whisper_cpp_reports_a_partial_failure
5 failed, 85 passed
```

**(5) Idempotence — `_present_with_pinned_size` made to always return `False`:**

```
FAILED tests/test_model_store.py::test_present_file_of_pinned_size_is_returned_without_fetching
FAILED tests/test_model_store.py::test_present_artifact_reports_complete
FAILED tests/test_model_store.py::test_engine_present_file_is_not_refetched
FAILED tests/test_model_store.py::test_ensure_whisper_model_is_idempotent
4 failed, 59 passed
```

"(5b) different model leaves the old untouched (all whisper paths collapsed to one file):"

```
FAILED tests/test_model_store.py::test_whisper_model_path_uses_the_epic_layout
FAILED tests/test_model_store.py::test_fetching_a_second_whisper_model_leaves_the_first_untouched
2 failed, 61 passed
```

After every probe: `git checkout -- <file> && rm -rf .mypy_cache __pycache__` and
`git diff --stat` empty; no `FALSIFICATION` marker remains in `src/`.

### Scope — `config.py` untouched; shipped default unchanged

```
$ git diff main...HEAD -- src/ges2t/config.py      # (empty)
$ git diff 27370b6..HEAD -- src/ges2t/config.py | wc -l
0

$ XDG_DATA_HOME=$(mktemp -d) uv run python -c "..."
ensure_model default name    = 'whistle'
DEFAULT_NEEDLE_MODEL         = 'whistle'
model_path()                 = <tmp>/ges2t/models/whistle/2.0.0/whistle.cact
needle_model('whistle').path() = <tmp>/ges2t/models/whistle/2.0.0/whistle.cact
env NEEDLE_WHISTLE_WEIGHTS   = <tmp>/ges2t/models/whistle/2.0.0/whistle.cact
```

The whistle adapter still calls `model_store.ensure_model(progress=progress)` with no name
(`src/ges2t/transcribers/whistle.py:541`), so the shipped default still resolves `whistle`.
The only config-level work (`KNOWN_ENGINES`/`transcriber.engine`/default rule *in config*) is ticket
45's and is absent here, as the ticket requires.

### Real-host runs (temp `XDG_DATA_HOME`, `uv run --offline`, no build, no network)

```
$ ges2t setup --help
usage: ges2t setup [-h] [--config PATH] [--debug] [BACKEND] [MODEL]
...
$ ges2t setup bogus                                          # exit=1
ges2t: setup: unknown backend 'bogus'; valid backends: needle, whisper.cpp
  hint: run 'ges2t setup --help' to list the accepted backends and models
$ ges2t setup whisper.cpp small-q5_0                         # exit=1
ges2t: setup: unknown whisper.cpp model 'small-q5_0': 'q5_0' is not published upstream for size 'small';
 valid models: tiny-q8_0, base-q8_0, small-q8_0, medium-q5_0, medium-q8_0, large-v3-turbo-q5_0, large-v3-turbo-q8_0
$ ges2t setup needle dragon                                  # exit=1
ges2t: setup: unknown needle model 'dragon'; valid models: whistle
$ ges2t setup whisper.cpp                                    # exit=1
Setting up the whisper.cpp backend and model small-q8_0.
This is the only ges2t command that uses the network; the rest runs offline.
ges2t: setup failed: cannot build the whisper.cpp engine (cpu): missing cmake (cmake).
Install the prerequisites, then re-run this command:
    sudo pacman -S base-devel cmake
  hint: check the message above, then re-run 'ges2t setup'
```

These match `001-comment.md` exactly. The `sudo pacman -S base-devel cmake` line is owned and
produced by `whisper_engine.PrerequisiteError`; it is **not** duplicated in `setup_command.py`
(`grep` finds no `pacman -S base-devel` there).

The real host resolves **CPU** here — accurately described in the comment: `inspect_vulkan` returns
`available: False` because **spirv-headers** is absent (runtime, vulkan headers and `glslc` are all
present), so the omitted whisper model correctly becomes `small-q8_0`:

```
available: False
runtime  : /usr/lib/libvulkan.so
headers  : /usr/include/vulkan/vulkan.h
spirv    : None
shaderc  : /usr/bin/glslc
summary  : CPU-only: SPIR-V headers (spirv-headers)
```

**Before-any-work property proven directly.** With a `PATH` shim placing logging `cmake`/`git`
stand-ins ahead of the real PATH, all three invalid invocations (`bogus`, `whisper.cpp small-q5_0`,
`needle dragon`) and `whisper.cpp colossal-q8_0` exited 1 with the typed errors above and the shim log
was **empty**, and the temp data dir contained **no** files:

```
=== shim invocation log (expect empty) ===
(no invocations)
=== data dir (expect no models) ===
(end)
```

### Hygiene

```
$ git status --porcelain          # (empty)
$ git diff --stat                 # (empty)
$ git diff --name-only --diff-filter=A main...HEAD | xargs ls -la
... 001-comment.md  src/ges2t/setup_command.py  tests/test_setup_command.py   # text only
```

No `.so`/`.bin`/`.cact`/`.whl`/`ggml` file is added by the diff; no `sudo`, no installs, no service
changes; the only network-capable path is still acquisition, and no probe used the network.

---

## Risks (non-blocking) and recommended follow-ups

1. **Setup-level idempotence for whisper is established transitively, not directly.** `run_setup`
   calls the idempotent `ensure_engine`/`ensure_whisper_model`, and those are tested idempotent, but
   there is no test that a *second* `ges2t setup whisper.cpp` re-verifies and refetches nothing end
   to end. A small `run_setup`-twice test (fake installers counting calls twice) would close it.
2. **The default model and the engine variant are derived from two separate probes.**
   `plan_setup` → `default_whisper_model(system)` and `run_setup` → `ensure_engine(system=None)` each
   build their own `HostSystemProbe`. They call the same `inspect_vulkan`, so they agree (my joint
   probe confirms for four shapes), but the agreement is not asserted by a shipped test and a
   mid-run environment change is a theoretical TOCTOU. Low impact.
3. **`ges2t setup` (needle path) now imports `ges2t.whisper_engine` eagerly** through
   `setup_command`. `setup` is not a key-bound command, so ADR 0016's lazy-import rule for
   `toggle`/`ptt`/`cancel`/`status` is not violated; noted only for completeness.
4. **Ticket 42's `ticket.md` still shows the stale `ges2t setup whisper.cpp small-q5_0` example.**
   This ticket documents the correction in `001-comment.md` and now rejects `small-q5_0` with a typed
   error, but does not edit the done ticket 42. Cosmetic; the correction is discoverable.
5. **Ticket-45 serialization.** This ticket edits `cli.py` (the `setup` parser/handler) and
   `model_store.py`; `config.py` is untouched. Per the ticket's own note, serialize ticket 45 if any
   overlap is found at its review — none is present in `config.py` now.

No risk above blocks the merge.

---

## Could-not-verify (each reason is itself true)

- **The successful end-to-end `ges2t setup whisper.cpp`** (real pinned clone, real `cmake` build of
  `libwhisper.so`+ggml, real ~0.5 GB sha256-pinned download, then a load). `cmake` is **not
  installed** on this host (`command -v cmake` → MISSING) and this review role may neither install
  packages nor use the network; the real build is user-owned. This is the honest unticked DoD item.
- **An independent recomputation of the 1018-test baseline at `27370b6`.** I ran the branch tip
  (`1049 passed`) and confirmed the 31 added tests; I did not check out the merge-base to re-run its
  suite. The supplied baseline is consistent (`1049 − 31 = 1018`).
- **Re-verification of the whisper.cpp sha256/size pins against the live Hugging Face host.** Requires
  network; those pins were verified in ticket 41's review and are unchanged here.
- **The real engine build's `EngineBuilder`/subprocess seam.** Ticket 44's tests drive the
  `ensure_engine`-level seam with fakes by design; the real `WhisperCppBuilder` and its real child
  process are covered by ticket 42's suite, which this branch does not retest.

## What would falsify the central claim

The central claim is: *`ges2t setup [backend] [model]` validates backend and model names before any
build or download; omitting the whisper model picks `medium-q5_0` iff the engine would be built with
Vulkan, else `small-q8_0`; the whisper path builds the engine before downloading the model and leaves
a complete engine with a typed `PartialSetupError` on model failure; re-runs are idempotent; and the
needle default is unchanged.* Any one of these would falsify it:

- running `ges2t setup whisper.cpp small-q5_0` and seeing a build or download start (it does not — shim
  log empty, data dir empty);
- `default_whisper_model` returning `medium-q5_0` on a CPU-only probe, or disagreeing with the variant
  `ensure_engine` chooses for the same probe (the joint probe shows agreement on all four shapes);
- a whisper path where the model is fetched before the engine (the reorder mutation fails 5 tests,
  including the typed `PartialSetupError` test);
- `git diff main...HEAD -- src/ges2t/config.py` being non-empty, or `ensure_model()` resolving anything
  other than `whistle` by default;
- a second acquisition re-downloading (the always-refetch and collapsed-path mutations fail 4 and 2
  tests respectively);
- the ADR 0018 scan passing with a network client added to `setup_command.py` (an injected
  `import httpx` fails it).

## Safe to relay to the user?

**Yes**, with one explicit caveat. The CLI surface, validation-before-work, the GPU/CPU default rule,
the engine-before-model ordering, the `PartialSetupError` diagnostic, idempotence, and the unchanged
needle default are all reproduced mechanically, and every `001-comment.md` real-run output and
falsification count is consistent with a fresh run. The unticked end-to-end whisper.cpp build is
genuinely user-owned: on this host the user must first
`sudo pacman -S base-devel cmake` (plus `vulkan-headers spirv-headers shaderc` for a GPU build) and
then run `uv run ges2t setup whisper.cpp small-q8_0` (or `small-q8_0`/`medium-q5_0` as appropriate) —
using `small-q8_0`, never ticket 42's stale `small-q5_0`.
