# setup-backend-and-model-arguments — implementation notes

- **Branch:** `feat/setup-backend-and-model-arguments`
- **Worktree:** `.worktrees/setup-backend-and-model-arguments`
- **Status of this write-up:** in progress (written incrementally; see the commits)

## Baseline (before any change)

```
$ uv run pytest -q
1018 passed in 8.84s

$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 57 source files
```

The orchestrator's "known-good baseline" of `923 passed` is **stale**: `main` at
`27370b6` is `1018 passed`. The real baseline is recorded above and must not
regress.

## Scope

This ticket is the **`ges2t setup` command surface** only. The config-level backend
selection (`transcriber.engine`, `KNOWN_ENGINES`, the default rule *in config*, the strict
startup rejections) belongs to ticket 45 and is **not** implemented here.

Files this ticket touches — flagged for the ticket-45 serialization note:

- `src/ges2t/cli.py` — the `setup` parser and `_handle_setup` handler (ticket 45 does not
  name `cli.py` in its own definition of done, but flags it as a serialization risk).
- `src/ges2t/setup_command.py` — **new** module holding the backend/model parsing, the
  default-model rule and the orchestration. This is deliberately separate from
  `config.py`, so ticket 45's config work does not collide.
- `src/ges2t/model_store.py` — the needle/Whistle model *name* catalogue plus
  `ensure_model(name=...)`; ticket 45 does not touch `model_store.py`.

(Incremental notes continue below.)

## Design

### CLI surface

```
ges2t setup                      # needle + whistle (unchanged default)
ges2t setup needle               # same, explicit backend
ges2t setup needle <model>       # needle model selectable (only 'whistle' is pinned)
ges2t setup whisper.cpp          # build engine + default model (GPU/CPU rule)
ges2t setup whisper.cpp <model>  # build engine + fetch <model>
```

Parsing: two optional positionals on the `setup` subparser. `whisper.cpp` (not
`whisper`) is the backend token, matching the ticket and ADR 0021.

### Default-selection rule

When the whisper model is omitted, `ges2t.setup_command.default_whisper_model()`
returns `medium-q5_0` when `ges2t.whisper_engine.inspect_vulkan(system).available`
is true, else `small-q8_0`. It deliberately reuses the engine build's **own**
Vulkan detection, so the default model always matches the engine that would be
built: a machine with only the Vulkan runtime (no headers/`glslc`) gets a CPU
build *and* the CPU model. `small-q5_0` is not published upstream, which is why
the CPU default is `small-q8_0` (ADR 0021 §4).

### Validation before work

`plan_setup()` runs first and is pure: it resolves the backend
(`UnknownBackendError` naming `needle, whisper.cpp`), validates the model name
against the catalogue (`NeedleModelNameError` / `WhisperModelNameError`, both
naming the valid set), and only then applies the default rule. No build, no
download, no path is touched until `run_setup()`.

### Ordering and partial failure

- **whisper.cpp:** build the engine first, then acquire the model. The
  prerequisite gate (and its exact `pacman` line) therefore fires **before** the
  ~0.5 GB download, and a model failure leaves a complete engine (atomic install
  + `engine.json` manifest) with a `PartialSetupError` naming what is installed,
  what is missing, the cause, and the re-run. A build failure propagates
  unchanged, so the user sees the prerequisite message verbatim.
- **needle:** model then engine, as before this ticket.

### Idempotency

Each acquisition is idempotent at the `model_store`/`whisper_engine` level: a
present artifact of the pinned size is returned untouched (no download, no
hash), `ensure_engine` verifies the manifest and does nothing when the engine is
complete, and each whisper model installs into its **own**
`<data_dir>/models/whisper-cpp/<name>/` directory — so re-running with a
different model fetches the new one and leaves the old bytes alone
(`test_fetching_a_second_whisper_model_leaves_the_first_untouched`).

### Model-store change (flagged)

The needle model name is now **data**, not a literal: `model_store` gains a
`NeedleModel`/`NEEDLE_MODELS`/`NEEDLE_CATALOGUE`/`DEFAULT_NEEDLE_MODEL`
catalogue and `ensure_model(name=...)`, mirroring the whisper catalogue. The real
pins and paths are unchanged (`whistle` → `models/whistle/2.0.0/whistle.cact`),
and the whistle adapter's `ensure_model()` keeps its default, so ADR 0010 is
untouched. `main`'s `MODEL_PIN` patch in `tests/test_model_store.py::fake_pins`
was adapted to patch the catalogue (the acquisition now reads the catalogue); no
assertion was weakened.

## Test/verify counts

```
$ uv run pytest -q
1049 passed in 8.79s      # baseline 1018 + 31 new

$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 59 source files
```

## Falsification (each broken in `src/`, watched a test fail, reverted)

1. **Argument parsing** — `_parse_backend` made to ignore its token and always
   return `NEEDLE`:

   ```
   FAILED tests/test_setup_command.py::test_explicit_whisper_backend_and_model_is_accepted
   FAILED tests/test_setup_command.py::test_unknown_backend_is_typed_and_names_the_valid_set
   2 failed, 1 passed, 15 deselected in 0.27s
   ```

2. **Model-name validation** — the whisper branch made to carry the raw token
   instead of `parse_whisper_model_name(model).name`:

   ```
   FAILED tests/test_setup_command.py::test_unpublished_whisper_quant_is_typed_and_names_the_valid_set
   FAILED tests/test_setup_command.py::test_unknown_whisper_size_is_rejected
   2 failed, 16 deselected in 0.13s
   ```

3. **GPU/CPU default rule** — the Vulkan test inverted:

   ```
   FAILED tests/test_setup_command.py::test_default_model_is_small_q8_0_without_vulkan
   FAILED tests/test_setup_command.py::test_default_model_is_medium_q5_0_with_vulkan
   FAILED tests/test_setup_command.py::test_omitted_whisper_model_uses_the_cpu_default_without_vulkan
   FAILED tests/test_setup_command.py::test_omitted_whisper_model_uses_the_gpu_default_with_vulkan
   FAILED tests/test_setup_command.py::test_partial_vulkan_selects_the_cpu_default
   5 failed, 13 deselected in 0.16s
   ```

4. **Partial-failure ordering** — the whisper path reordered to fetch the model
   before the engine:

   ```
   FAILED tests/test_setup_command.py::test_whisper_run_builds_the_engine_then_acquires_the_model
   FAILED tests/test_setup_command.py::test_whisper_model_failure_after_the_build_is_typed_and_names_state
   2 failed, 1 passed, 15 deselected in 0.16s
   ```

   After each revert `git diff --stat` was empty and no `FALSIFICATION` marker
   remained in `src/`.

## Real runs (arguments only; no build, no network)

`cmake` is not installed, so the whisper.cpp build cannot run end-to-end on this
machine — that step is **user-owned** (see below). The argument/validation
surface and the honest prerequisite failure were exercised on the real host with
`XDG_DATA_HOME` pointed at a temporary directory (so the user's real data dir was
not touched):

```
$ uv run ges2t setup --help
usage: ges2t setup [-h] [--config PATH] [--debug] [BACKEND] [MODEL]
...

$ uv run ges2t setup bogus
ges2t: setup: unknown backend 'bogus'; valid backends: needle, whisper.cpp
  hint: run 'ges2t setup --help' to list the accepted backends and models
exit=1

$ uv run ges2t setup whisper.cpp small-q5_0
ges2t: setup: unknown whisper.cpp model 'small-q5_0': 'q5_0' is not published upstream for size 'small'; valid models: tiny-q8_0, base-q8_0, small-q8_0, medium-q5_0, medium-q8_0, large-v3-turbo-q5_0, large-v3-turbo-q8_0
exit=1

$ uv run ges2t setup needle dragon
ges2t: setup: unknown needle model 'dragon'; valid models: whistle
exit=1

$ XDG_DATA_HOME=$(mktemp -d) uv run ges2t setup whisper.cpp
Setting up the whisper.cpp backend and model small-q8_0.
This is the only ges2t command that uses the network; the rest runs offline.
ges2t: setup failed: cannot build the whisper.cpp engine (cpu): missing cmake (cmake).
Install the prerequisites, then re-run this command:
    sudo pacman -S base-devel cmake
  hint: check the message above, then re-run 'ges2t setup'
exit=1
```

The last run is the honest failure and two pieces of real evidence at once: the
real host's `inspect_vulkan` resolves to the **CPU** path here, so the omitted
model correctly became `small-q8_0`, and the prerequisite gate's exact `pacman`
line is printed verbatim (the engine-build ticket owns that message; nothing is
duplicated or weakened). A small `sys.stdout.flush()` was added before the work
so this banner appears above the stderr failure.

## User-owned steps left unticked (end-to-end whisper.cpp setup)

`cmake` is **not installed** and this role may not install it, so a real
`ges2t setup whisper.cpp` cannot complete here. The end-to-end checkbox stays
unticked. On this machine:

```
sudo pacman -S base-devel cmake
uv run ges2t setup whisper.cpp small-q8_0    # add the Vulkan packages first for a GPU build
```

For a Vulkan build, install `sudo pacman -S base-devel cmake vulkan-headers
spirv-headers shaderc`, then `uv run ges2t setup whisper.cpp` (the real host
currently resolves Vulkan as unavailable, hence the CPU default above).

**Correction to ticket 42's hand-over:** its example uses
`ges2t setup whisper.cpp small-q5_0`, but upstream publishes **no** `small-q5_0`
(ADR 0021 §4), and this ticket's validation now rejects it with the typed error
shown above. The correct command is `small-q8_0` (or `medium-q5_0` on a Vulkan
build).

## Ticket-45 collision note

This ticket edits `src/ges2t/cli.py` (the `setup` parser + `_handle_setup`) and
touches `src/ges2t/model_store.py`. Ticket 45's definition of done names neither,
but it does name `cli.py`/the config module as a serialization risk; the setup
vocabulary was kept out of `config.py` (`KNOWN_ENGINES`/`transcriber.engine` are
untouched) precisely so ticket 45 does not collide. Serialize if change overlap
is found at review.

