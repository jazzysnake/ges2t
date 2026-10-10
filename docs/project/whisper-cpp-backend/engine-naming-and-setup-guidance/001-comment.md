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
