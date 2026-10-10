# 001 — variant-aware build policy: the plan

Ticket 49. Re-running `ges2t setup` currently does nothing when a *valid* engine is already
installed (`ensure_engine()` short-circuits on `verify_build()`), so a CPU-only build made before
`spirv-headers` was installed can never become Vulkan without hand-deleting
`~/.local/share/ges2t/engine/whisper-cpp/<version>/`. This ticket makes the idempotency
**variant-aware** and honours the configured `gpu` preference.

## What I read first

- ticket `49` + `epic.md` + ADR `0021` (`§4` model rule, `§7` CPU/Vulkan detection, `§8` strict validation).
- merged ticket `42` (`whisper-engine-build`) and its `003-comment.md` (the build/detection rule),
  ticket `44` (`setup_command`), `45` (`whisper_cpp_config`), `46`/`47` (the shared GPU hint).
- `src/ges2t/whisper_engine.py`, `src/ges2t/setup_command.py`,
  `src/ges2t/transcribers/whisper_cpp_config.py`, `src/ges2t/pipeline.py`, `src/ges2t/cli.py`,
  `src/ges2t/doctor.py`, and their tests.

## The policy (decided)

Let `available` = the best variant this machine can build **now**:
`VULKAN` when `inspect_vulkan(probe).available`, else `CPU`. Let `installed` = `verify_build(target)`.

`gpu` preference (read from `[engine.whisper_cpp] gpu`, values `auto` / `cpu` / `vulkan` — the
existing `GpuPreference`):

| preference | desired variant | installed CPU + Vulkan now available | installed Vulkan | notes |
|---|---|---|---|---|
| `auto` (default) | `available` | **rebuild as Vulkan** | no-op | the user's bug |
| `cpu` | `CPU` | **no upgrade** (stays CPU) | no-op (never downgrade) | respects the preference |
| `vulkan` | `VULKAN` | rebuild as Vulkan | no-op | if Vulkan cannot be built → **reported error** |

The "no-op only when the installed build is already as good as the machine can now build" rule is:

```
no-op   when installed == VULKAN            (never downgrade a better build)
no-op   when installed == desired           (already what we would build)
rebuild otherwise                           (only CPU → VULKAN)
```

A `gpu = "vulkan"` preference that cannot be satisfied (Vulkan dev pieces missing) raises the
existing typed `PrerequisiteError` — with the exact `pacman` line — even when a CPU build exists;
it is a reported error, never a silent CPU run (matches the runtime rule in
`whisper_cpp.resolve_device`, ADR 0020's no-silent-substitution).

## Where the preference crosses into `ensure_engine`

`ensure_engine()` gains a keyword parameter `preference: GpuPreference` (default `AUTO`). The enum
is the **single** `gpu` vocabulary (`ges2t.transcribers.whisper_cpp_config.GpuPreference`), so the
build policy and the runtime device choice cannot drift. The composition is:

```
config.toml [engine.whisper_cpp] gpu
  -> Ges2t setup loads it (new: setup now reads config)
  -> WhisperCppConfig.from_mapping -> GpuPreference   (the existing validator)
  -> SetupPlan.preference
  -> run_setup -> ensure_engine(preference=...)
```

`ges2t setup` is the only place the config value is read; `ensure_engine` never opens a file.

## Safety and the escape hatch

- **Safe replacement.** `WhisperCppBuilder` stages the library set + manifest in a sibling temp
  directory (same filesystem), verifies it, then swaps the directory into place. A failed rebuild
  leaves the previous engine untouched, and a half-written tree is never at `engine.json`'s path,
  so `verify_build` cannot accept it.
- **`--rebuild`.** Added, because the ticket's complaint is precisely "the only path is to delete
  the directory by hand": `ensure_engine(force=True)` / `ges2t setup --rebuild` bypasses the
  no-op for an already-best build while still honouring the preference and the prerequisite gate.

## `engine.json` fields read by the policy

Only `variant` (the stored `BuildVariant`) decides the rebuild; `verify_build` already reads the
manifest and checks the `entry_library` + every `libraries` entry still exist. The live machine is
probed fresh (`inspect_vulkan`) — never trusted from the stored `vulkan` map — so a dev package
installed since the build is seen.

## Implementation (what changed)

**`src/ges2t/whisper_engine.py`** — the policy lives here.

- `ensure_engine()` gains `preference: GpuPreference = AUTO`, `force: bool = False`,
  `notice: NoticeCallback | None = None`. `GpuPreference` is imported from
  `ges2t.transcribers.whisper_cpp_config` — the **one** `gpu` vocabulary, so the build
  policy and the runtime device choice cannot drift (the module docstring now records this
  single, deliberate dependency; it still never reads a config file).
- New helpers `_desired_variant()`, `_satisfies()`, `_rebuild_reason()`, `_notify()`.
  `_satisfies(installed, desired)` is `installed is VULKAN or installed is desired`, which is
  exactly "already as good as the machine can build" and "never downgrade".
- A `VULKAN` preference with no Vulkan raises `PrerequisiteError(check_prerequisites(VULKAN))`
  **before** any rebuild/no-op, so it errors over an existing CPU build too.
- `NoticeCallback = Callable[[str], None]` added next to `ProgressCallback`.
- `WhisperCppBuilder` now assembles the library set **and manifest** in a sibling staging
  directory, verifies it, and swaps the directory into place (`_staging_directory`,
  `_replace_directory`, `_remove_tree`), restoring the old tree on any failure. The Vulkan
  "no `libggml-vulkan.so`" check moved **before** any copy.
- The rebuild reason, one line: `rebuilding: the Vulkan development packages are now
  available (was CPU-only)`.

**`src/ges2t/setup_command.py`** — the preference crossing point.

- `SetupPlan` gains `preference: GpuPreference = DEFAULT_GPU`.
- `plan_setup(..., config: Config | None = None)` reads `[engine.whisper_cpp] gpu` through the
  backend's own `WhisperCppConfig.from_mapping` validator. The needle backend does not read it.
- `run_setup(..., rebuild=False, notice=None)` and `_run_whisper` forward `preference`,
  `force`, `notice` to the engine builder; the `WhisperEngineInstaller` Protocol matches.

**`src/ges2t/cli.py`** — `ges2t setup` now loads the config (`_load_config`, so `--config`
works) and passes it to `plan_setup`; adds `--rebuild`; prints each rebuild notice.

**Tests** added: `tests/test_whisper_engine.py` (the matrix + a real-builder safe-replacement
pair), `tests/test_setup_command.py` (preference reading / plumbing / `--rebuild`), and
`tests/test_cli.py` (the `--rebuild` flag, reading `gpu` from a config file, printing the
notice).

## `engine.json` fields the policy reads

`verify_build()` → `read_build()` reads `variant`, `entry_library` and `libraries`; the policy
compares the stored `variant` against the freshly probed machine. Nothing else in the manifest
is trusted — in particular the stored `vulkan` paths are **not** used, so a dev package
installed after the build is seen. `verify_build` also requires every named library to still
exist, so a hand-deleted file still triggers a rebuild.

## Verification (real output)

```
$ uv run pytest -q
1216 passed in 7.80s

$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 64 source files
```

**Baseline note.** The task text cited `923 passed`; `main` (`ce8b4d7`, this branch's base)
actually collects **1194** tests. This branch adds **22** and runs **1216**, all passing — no
regression (`uv run pytest --collect-only -q` on `main`: `1194 tests collected`).

`uv run ruff check` on the six touched files: `All checks passed!` (the repo is not
`ruff format`-clean on `main`, so I matched the surrounding style and ran no reformat).

## Falsification (each break reverted with `git checkout --`)

| # | Break applied | Tests that failed |
|---|---|---|
| 1 | `_satisfies` → `return True` (never rebuild) | `test_ensure_engine_upgrades_a_cpu_build_when_vulkan_becomes_available` |
| 2 | `_satisfies` → `return False` (always rebuild) | `test_ensure_engine_keeps_a_vulkan_build_when_vulkan_is_available`, `test_ensure_engine_keeps_an_existing_vulkan_build_without_rebuilding` |
| 3 | same as 2 | `test_ensure_engine_keeps_a_cpu_build_when_only_cpu_is_available`, `test_ensure_engine_is_idempotent_and_builds_only_once` |
| 4 | `_desired_variant` ignores `CPU` preference | `test_ensure_engine_does_not_upgrade_a_cpu_build_when_the_preference_is_cpu`, `test_ensure_engine_builds_cpu_for_a_cpu_preference_even_with_vulkan_available` |
| 5 | install straight into the destination + Vulkan check after the copy | `test_a_failed_rebuild_leaves_the_previous_build_intact` |

After every revert: `git diff --stat` empty.

## `--rebuild`

Added. The ticket's whole complaint is "the only path is to delete the directory by hand"; the
variant rule makes the common upgrade automatic, but an explicit force is still the honest fix
for "rebuild the already-best engine" (a suspicious install, a new compiler). It honours both
the preference and the prerequisite gate — it only bypasses the no-op.

## User-owned end-to-end (handed over, NOT done here)

The agent may not install packages or `sudo`. On the reference machine:

```
sudo pacman -S spirv-headers          # the piece the user was missing
uv run ges2t setup whisper.cpp medium-q5_0
cat ~/.local/share/ges2t/engine/whisper-cpp/v1.9.5/engine.json   # expect "variant": "vulkan"
```

The expected setup stdout line is
`rebuilding: the Vulkan development packages are now available (was CPU-only)`.
Set `[engine.whisper_cpp] gpu = "cpu"` first and the rebuild must **not** happen.
