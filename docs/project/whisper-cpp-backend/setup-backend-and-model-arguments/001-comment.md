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

