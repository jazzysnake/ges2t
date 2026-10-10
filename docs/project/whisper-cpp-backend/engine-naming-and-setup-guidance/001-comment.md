# engine-naming-and-setup-guidance — implementation notes

- **Branch:** `feat/engine-naming-and-setup-guidance`
- **Worktree:** `.worktrees/engine-naming-and-setup-guidance`
- **Status of this write-up:** in progress (written incrementally; see the commits)

## Baseline (before any change)

```
$ uv run pytest -q
1129 passed in 9.17s

$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 64 source files
```

The orchestrator's "known-good baseline" of `923 passed` is stale: `main` at
`df4738f` is **1129 passed**. The real baseline is recorded above and must not
regress.

## Scope and the decisions this ticket takes

The defect is **one thing, two spellings**: `ges2t setup whisper.cpp` names the
backend with the CLI token, but the config engine value is `whisper_cpp`, and a
user who copies the CLI token into `transcriber.engine` gets a rejection that
names only `whistle`. Second gap: a Vulkan runtime present with a missing dev
package silently produces a CPU-only engine build.

Decisions taken (and why):

1. **Canonical engine value `whisper_cpp`; `whisper.cpp` is an accepted alias,
   normalised at parse.** The alias is normalised in *both* places a spelling can
   enter: the `ges2t setup` backend token and the `transcriber.engine` config
   value. One normaliser (`config.normalize_engine`) is the single source of
   truth, so the two cannot drift.
2. **`[engine.<name>]` block names go through the same normaliser.** So
   `transcriber.engine = "whisper.cpp"` reads `[engine.whisper_cpp]`, and a block
   written with the alias spelling is read too. `[engine.whisper.cpp]` is TOML's
   *dotted* form, which parses as `{"whisper": {"cpp": {...}}}`; that one level is
   folded through the normaliser as well, so the block is **read**, not silently
   ignored. Two spellings of the same engine in one file are a hard error naming
   both (no silent precedence).
3. **`setup` prints the exact config line it cannot write.** ADR 0004/0009: ges2t
   never edits the user's config. `ges2t setup whisper.cpp …` prints
   `transcriber.engine = "whisper_cpp"` and names the resolved config path; the
   `needle` default says no line is needed.
4. **The rejection names the valid set and points at the right spelling** with a
   "did you mean" for a near-miss (`whisper`, `whisper-cpp`, `Whisper.CPP`).
5. **The GPU dev-package hint is a diagnostic, not an error.** When the *built*
   engine is CPU-only **and** the Vulkan runtime is present, `setup` prints the
   missing dev package(s) and the exact `pacman` line. No Vulkan runtime at all
   (no GPU) → no hint. Detection is reused from
   `whisper_engine.inspect_vulkan` / `check_prerequisites`; none is duplicated.

## Files

- `src/ges2t/config.py` — the normaliser, the alias map, block-name normalisation.
- `src/ges2t/setup_command.py` — the CLI token alias, the config-line guidance,
  the CPU-only-build hint.
- `src/ges2t/cli.py` — print the guidance and the hint after a successful setup.
- `README.md` — one spelling in the docs, the alias noted.
- tests: `tests/test_config.py`, `tests/test_setup_command.py`, `tests/test_cli.py`.

**Coordination note (ticket 46):** no shared file is edited. `src/ges2t/doctor.py`
is untouched; `src/ges2t/whisper_engine.py` is **read** (its public
`inspect_vulkan` / `check_prerequisites` / `prerequisites_for` / `PACMAN_PREFIX`
are reused) but **not modified**.

## Implementation (committed)

### `config.py` — the normaliser (single source of truth)

- `WHISTLE_ENGINE = "whistle"`, `WHISPER_CPP_ENGINE = "whisper_cpp"`,
  `KNOWN_ENGINES = (WHISTLE_ENGINE, WHISPER_CPP_ENGINE)`.
- `ENGINE_ALIASES = {"whisper.cpp": WHISPER_CPP_ENGINE}` and
  `normalize_engine(name) -> str`, which returns the canonical value and passes an
  unknown spelling through unchanged (so the caller can report it).
- `load_config` normalises `transcriber.engine` **before** checking membership, so
  `engine = "whisper.cpp"` and `engine = "whisper_cpp"` produce the identical
  `TranscriberConfig`.
- `_read_engine_blocks` keys every block by its canonical engine. A quoted alias
  block (`[engine."whisper.cpp"]`) and TOML's dotted `[engine.whisper.cpp]` both
  resolve to the `whisper_cpp` block (**read**, not ignored). Two spellings of one
  engine are a hard `ConfigError` naming both (no silent precedence).
- `_unknown_engine_error` names `KNOWN_ENGINES` and adds `did you mean '<canonical>'?`
  when `suggest_engine` folds the value onto a known spelling; for the whisper
  engine it also names the CLI token, e.g.:
  `transcriber.engine = 'whisper' is not a known engine; known engines: 'whistle',
  'whisper_cpp'; did you mean 'whisper_cpp'? ('whisper.cpp' is the spelling
  'ges2t setup' takes; the config value is 'whisper_cpp')`

### `setup_command.py`

- `SetupBackend` keeps the CLI token (`whisper.cpp`) as its value and gains an
  `engine` property (needle → `whistle`, whisper.cpp → `whisper_cpp`).
- `BACKEND_ALIASES = {"whisper_cpp": SetupBackend.WHISPER_CPP}`; `_parse_backend`
  accepts both tokens. `UnknownBackendError.valid` is
  `("needle", "whisper.cpp", "whisper_cpp")` plus a near-miss hint.
- `config_guidance(plan, *, path=None) -> str` — the exact line
  `transcriber.engine = "whisper_cpp"` and the resolved config path, plus the
  batch-only `enabled = false` note; the needle path says no line is needed.
- `cpu_only_build_advice(build, *, system=None) -> CpuOnlyBuildAdvice | None` and
  `cpu_only_build_hint(...) -> str | None` — the diagnostic. Gate: the *built*
  engine is `BuildVariant.CPU` **and** `build.vulkan.runtime is not None` (a GPU is
  present). No runtime at all → `None` (no hint). Missing packages come from
  `whisper_engine.check_prerequisites(VULKAN, probe)` minus the host-build
  packages (`base-devel`, `cmake`); detection is not duplicated.

### `cli.py`

`_handle_setup` prints `config_guidance(...)` after `Setup complete.` and, for the
whisper path, `cpu_only_build_hint(outcome.whisper_engine)` — **exit code
unchanged** (a diagnostic, not an error). The `setup --help` text names the alias
and the printed line.

## Verification (real output)

```
$ uv run pytest -q
1159 passed in 8.23s

$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 64 source files
```

Baseline was `1129 passed` / 0 errors; this ticket adds 30 tests and regresses
nothing.

## Falsification (each change removed, the test watched failing, reverted)

| # | What was removed | Command | Result |
|---|---|---|---|
| F1 | the `transcriber.engine` **value** normalisation | `pytest tests/test_config.py -k "alias or canonical_whisper"` | `2 failed, 4 passed` (`test_config_accepts_whisper_dot_cpp_as_an_alias`, `test_alias_selector_reads_the_canonical_engine_block`) |
| F2 | the **block-name** normalisation | `pytest tests/test_config.py -k "alias or dotted or quoted"` | `2 failed, 3 passed` (quoted and dotted alias block tests) |
| F3 | the "did you mean" **suggestion** | `pytest tests/test_config.py -k "did_you_mean or points_at_the_right or looks_like"` | `2 failed` |
| F4 | the **CPU-only hint** | `pytest tests/test_setup_command.py tests/test_cli.py -k "cpu_only or hint"` | `2 failed, 7 passed` |
| F5 | the printed **config line** | `pytest tests/test_cli.py -k "config_engine_line or no_line"` | `1 failed` |

Every probe was reverted with `git checkout -- <file>`; `git diff --stat` was
empty before the next probe and before every commit.

## Real behaviour (run from the worktree, no network, no machine side effects)

`normalize_engine` / `suggest_engine` / `load_config` alias + block resolution:

```
normalize_engine('whisper.cpp') = whisper_cpp
suggest_engine('whisper')      = whisper_cpp
suggest_engine('Whisper.CPP')  = whisper_cpp
suggest_engine('sphinx')       = None
plan_setup('whisper.cpp', ..)  = SetupPlan(backend=<SetupBackend.WHISPER_CPP: 'whisper.cpp'>, model='medium-q5_0')
plan_setup('whisper_cpp', ..)  = SetupPlan(backend=<SetupBackend.WHISPER_CPP: 'whisper.cpp'>, model='medium-q5_0')
config engine value           = 'whisper_cpp'
engine_blocks resolved        = {'whisper_cpp': {'model': 'medium-q5_0'}}
```

(`transcriber.engine = "whisper.cpp"` + `[engine.whisper_cpp]` in one file.)

`ges2t setup whisper.cpp tiny-q8_0 --config <tmp>` **with the real `cli.main`, fakes
for the acqusition steps only** (no network, no `cmake`):

```
Setting up the whisper.cpp backend and model tiny-q8_0.
This is the only ges2t command that uses the network; the rest runs offline.
Engine: whisper.cpp v1.9.5 (CPU-only) at /tmp/.../engine/v1.9.5
Model:  /tmp/.../ggml.bin
Setup complete.
Add this line to /tmp/.../config.toml to select the engine (ges2t never edits it for you):

    transcriber.engine = "whisper_cpp"

whisper.cpp is batch-only, so also set, under [streaming]:

    enabled = false
exit=0
```

Unknown backend (real `ges2t` entry point, `plan_setup` rejects before any build):

```
$ uv run ges2t setup whisper
ges2t: setup: unknown backend 'whisper'; valid backends: needle, whisper.cpp, whisper_cpp; did you mean 'whisper_cpp'?
  hint: run 'ges2t setup --help' to list the accepted backends and models
exit=1
```

Unknown **config** engine:

```
transcriber.engine = 'whisper' is not a known engine; known engines: 'whistle',
'whisper_cpp'; did you mean 'whisper_cpp'? ('whisper.cpp' is the spelling
'ges2t setup' takes; the config value is 'whisper_cpp')
```

CPU-only hint, GPU present with `spirv-headers` missing:

```
Note: the Vulkan runtime is present, but the engine was built CPU-only
because these Vulkan development packages are missing: SPIR-V headers (spirv-headers).
Install them, then re-run 'ges2t setup whisper.cpp' for a Vulkan (GPU) build:
    sudo pacman -S spirv-headers
```

Same `EngineBuild` with no Vulkan runtime at all → `cpu_only_build_hint(...)` is
`None` (no hint), and a Vulkan build → `None`.

## Out of scope / handed back

- No `sudo`, no package installs, no network, no microphone, no clipboard, no
  window, no notification, no service change. Nothing was recorded and no audio
  was committed.
- The real whisper.cpp build and a live dictation remain **user-owned** and were
  not performed here; the hint path is exercised through fakes and a real
  `EngineBuild` record only.
