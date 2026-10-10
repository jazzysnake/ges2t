# review — setup guidance: one engine spelling, the config line, and the GPU dev-package hint

- **Ticket:** `docs/project/whisper-cpp-backend/engine-naming-and-setup-guidance/ticket.md`
- **Branch:** `feat/engine-naming-and-setup-guidance` (tip `583d79e`)
- **Reviewer:** independent verification (no implementation changes made)
- **Merge-base:** `df4738f` (`main` has since advanced to `02171eb`; the three-dot diff correctly
  resolves against the merge-base, so `main`'s later plan commits are excluded)

## Verdict

**`pass`** — every definition-of-done item verifies; all five claimed falsifications reproduce with
the exact numbers quoted in `001-comment.md`; `pytest` and `mypy` are green at the claimed counts.
No blocking findings.

## What shipped (three-dot diff, `git diff main...feat/engine-naming-and-setup-guidance`)

```
 README.md                                          |  35 +++-
 docs/project/README.md                             |   2 +-
 .../001-comment.md                                 | 214 +++++++++++++++++++++
 .../ticket.md                                      |  39 ++--
 src/ges2t/cli.py                                   |  28 ++-
 src/ges2t/config.py                                | 199 +++++++++++++++++--
 src/ges2t/setup_command.py                         | 200 ++++++++++++++++++-
 tests/test_cli.py                                  | 104 +++++++++-
 tests/test_config.py                               | 111 +++++++++++
 tests/test_setup_command.py                        | 134 ++++++++++++-
 10 files changed, 1008 insertions(+), 58 deletions(-)
```

`src/ges2t/whisper_engine.py` and `src/ges2t/doctor.py` are **byte-identical to `main`** (verified with
`git diff --quiet main...HEAD -- <file>` → `IDENTICAL` for both). No binary/model is committed
(`git diff main...HEAD --numstat` has no `-` entries).

## Required checks — real output

```
$ timeout 600 uv run pytest -q
1159 passed in 10.47s
PYTEST_EXIT=0

$ rm -rf .mypy_cache __pycache__ && timeout 600 uv run mypy src/ges2t tests
Success: no issues found in 64 source files
MYPY_EXIT=0
```

Baseline consistency: the branch is linear off the merge-base (`731e0b0^ == df4738f`), the diff adds
exactly 24 new `def test_` functions plus 6 `parametrize` expansions = **30** cases, and **no test
function is removed** (`git diff main...HEAD -- tests/ | grep '^-def test_'` → none). That reconciles
`1129 → 1159` arithmetically (the 1129 figure is the comment's claim about `main`; see
*could not verify*).

The `mypy` scope genuinely includes `tests/`: `[tool.mypy]` sets `disallow_untyped_defs = true`,
`no_implicit_optional = true`, `ignore_missing_imports = true` and **no `exclude`**, and the command
names `tests` explicitly. This is not a tests-blind scope.

## Per-criterion findings

### 1. One canonical value + forgiving alias — PASS

Evidence (real run, `uv run python`, no network):

```
normalize_engine('whisper.cpp') = whisper_cpp
normalize_engine('whisper_cpp') = whisper_cpp
normalize_engine('whistle')     = whistle
ENGINE_ALIASES                  = {'whisper.cpp': 'whisper_cpp'}
KNOWN_ENGINES                   = ('whistle', 'whisper_cpp')
plan_setup('whisper.cpp',None)  = SetupPlan(backend=<SetupBackend.WHISPER_CPP: 'whisper.cpp'>, model='medium-q5_0')
plan_setup('whisper_cpp',None)  = SetupPlan(backend=<SetupBackend.WHISPER_CPP: 'whisper.cpp'>, model='medium-q5_0')
```

The config value loads as canonical:

```
alias selector engine        = whisper_cpp
alias selector blocks        = {'whisper_cpp': {'model': 'medium-q5_0'}}
```

(`transcriber.engine = "whisper.cpp"` + `[engine.whisper_cpp]` in one file.) `SetupBackend` keeps the
CLI token `whisper.cpp` as its value and gains `.engine` (`needle → whistle`, `whisper.cpp →
whisper_cpp`).

### 2. Block resolution through the same normalisation — PASS

```
dotted block blocks  = {'whisper_cpp': {'model': 'medium-q5_0'}} warnings= []
quoted block blocks  = {'whisper_cpp': {'model': 'medium-q5_0'}} warnings= []
```

Both the quoted alias `[engine."whisper.cpp"]` and TOML's dotted `[engine.whisper.cpp]` (which parses
as `{"whisper": {"cpp": …}}`) are **read** as the `whisper_cpp` block. Both spellings at once is a hard
error naming both, reproduced with real TOML:

```
ERROR: /tmp/.../config.toml: engine blocks 'engine.whisper_cpp' and 'engine.whisper.cpp' are both spellings of the 'whisper_cpp' engine; keep one
```

Decision is documented (README §"The whisper.cpp backend (opt-in)": *"Only that spelling is
documented; a `[engine.whisper.cpp]` block is read as the same block, and writing both spellings is an
error"*). The `engine_blocks` key is now canonical, and the consumers (`pipeline.py`,
`doctor.py`, `transcriber.engine`) all look the block up by `config.transcriber.engine`, which is
normalised identically — no key/selector drift.

### 3. `setup` prints the exact config line — PASS

Full real-`cli.main` output with fakes for acquisition only (no network, no `cmake`):

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

Needle says no line is needed:

```
Setup complete.
The default engine is 'whistle', so no line is needed in /tmp/.../config.toml.
exit=0
```

The quoted "real report" in `001-comment.md` matches this output byte-for-byte (modulo the tmp paths).
The `--config` path is resolved through `resolve_config_path`.

### 4. Rejection names the valid set and points at the right spelling — PASS

Real console script, no build started (validated in `plan_setup` before any work):

```
$ timeout 60 uv run ges2t setup whisper
ges2t: setup: unknown backend 'whisper'; valid backends: needle, whisper.cpp, whisper_cpp; did you mean 'whisper_cpp'?
  hint: run 'ges2t setup --help' to list the accepted backends and models
EXIT=1
```

Unknown config engine (real `load_config`):

```
/tmp/.../config.toml: transcriber.engine = 'whisper' is not a known engine; known engines: 'whistle', 'whisper_cpp'; did you mean 'whisper_cpp'? ('whisper.cpp' is the spelling 'ges2t setup' takes; the config value is 'whisper_cpp')
```

A value resembling nothing (`sphinx`) names the valid set but adds no "did you mean" (asserted and
reproduced).

### 5. Docs show one spelling plus the alias — PASS

`README.md` uses `whisper_cpp` as the config value throughout and notes `whisper.cpp` as the accepted
alias (§"The whisper.cpp backend (opt-in)" and §"Engines"). The only `transcriber.engine =
"whisper.cpp"` occurrences in `README.md`/`docs/` outside the ticket/comment narrative are none — the
grep hits are all in this ticket's own `ticket.md`/`001-comment.md` describing the defect.

### 6. Tests, each falsified — PASS (all five reproduced)

Each mutation applied, the targeted tests run, then `git checkout -- <file>` and
`rm -rf .mypy_cache __pycache__`:

| # | Mutation | Command | Real result |
|---|---|---|---|
| F1 | removed the `transcriber.engine` value normalisation | `pytest tests/test_config.py -k "alias or canonical_whisper"` | `2 failed, 4 passed` |
| F2 | disabled the block-name normalisation (`resolved = None`) | `pytest tests/test_config.py -k "alias or dotted or quoted"` | `2 failed, 3 passed` |
| F3 | removed the "did you mean" suggestion | `pytest tests/test_config.py -k "did_you_mean or points_at_the_right or looks_like"` | `2 failed` |
| F4 | disabled `cpu_only_build_hint` (`return None`) | `pytest tests/test_setup_command.py tests/test_cli.py -k "cpu_only or hint"` | `2 failed, 7 passed` |
| F5 | removed the `print(config_guidance(...))` | `pytest tests/test_cli.py -k "config_engine_line or no_line"` | `1 failed` |

These are exactly the numbers `001-comment.md` reports. **The tests are not vacuous.**

Two additional mutations I ran, beyond the claimed set, to probe the guards the ticket calls
load-bearing:

```
# duplicate-spelling check disabled (if resolved.canonical in blocks: -> if False:)
FAILED tests/test_config.py::test_two_spellings_of_one_engine_block_are_rejected
1 failed, 49 deselected

# no-GPU gate removed (build.vulkan.runtime is None check dropped)
FAILED tests/test_setup_command.py::test_cpu_only_hint_is_silent_when_there_is_no_vulkan_runtime
1 failed, 4 passed
```

Both guards are genuinely exercised. After every probe, `sha256sum -c` against a pristine snapshot
matched and `git diff --stat` was empty; the final tree is `git status --porcelain` clean.

### 7. GPU dev-package hint — PASS (all three cases exercised through the real CLI)

- **GPU present, `spirv-headers` missing, CPU build** → prints the diagnostic, **exit 0**:

```
Note: the Vulkan runtime is present, but the engine was built CPU-only
because these Vulkan development packages are missing: SPIR-V headers (spirv-headers).
Install them, then re-run 'ges2t setup whisper.cpp' for a Vulkan (GPU) build:
    sudo pacman -S spirv-headers
exit=0
```

- **No Vulkan runtime** → prints **nothing** (`cpu_only_build_hint` → `None`; the `(CPU-only)`
  engine summary is still shown by the pre-existing `Engine:` line).
- **Vulkan build (full dev set)** → prints **nothing**.

The diagnostic is on **stdout with exit `0`** — a diagnostic, not an error. Detection is reused from
`whisper_engine.check_prerequisites` / `inspect_vulkan`; `base-devel`/`cmake` are excluded from the
advice because their absence would have failed the build outright. The gate
(`variant is CPU and build.vulkan.runtime is not None`) is consistent with `ensure_engine`, which
selects Vulkan iff `inspect_vulkan(probe).available` — so "CPU build with a runtime present" really
does imply a missing *dev* piece. No user-facing way to force a CPU build while a GPU is present
exists, so this is not a false positive.

### 8. `pytest` + `mypy` green, counts reported, no behaviour change — PASS

`1159 passed` / `0 errors, 64 files`, exit codes `0` (not `124`: the process exits; no leaked
non-daemon thread). The non-test diff's 31 deleted lines are all docstrings/help text/imports and the
old inline unknown-engine check replaced by `_unknown_engine_error` — reviewed line by line, nothing
behavioural is dropped. The default engine is still `whistle` (`KNOWN_ENGINES[0]`,
`DEFAULT_ENGINE = KNOWN_ENGINES[0]`), and the pre-existing 1129 cases still pass.

## Risks / follow-ups (non-blocking)

- **Ticket 50 will touch this printed text.** `main` already carries `02171eb plan: ticket 50 also
  updates ticket 47's printed [streaming] guidance (redundant once the default follows the engine)`.
  The `enabled = false` line printed here will become redundant; keep it in sync rather than treating
  this review as freezing the string.
- **Two spellings of an *unknown* engine block still collide silently** (`blocks[name] = block`,
  last wins). Unknown blocks are ignored per ADR 0009, so this is out of scope, but a duplicate-name
  warning for unknown blocks would be a cheap consistency follow-up. Not required by the DoD.
- `suggest_engine` is a heuristic and resolves ambiguous prefixes (e.g. `whis`) to `whisper_cpp`
  because the alias is checked first. It is guidance only and never substitutes a value (ADR 0020),
  so this is acceptable.

## Could not verify

- **The real whisper.cpp build and a live GPU hint on the user's machine.** `cmake` is not installed
  here and the build is user-owned; the hint path is exercised only through fakes, a synthetic
  `EngineBuild`, and the real `check_prerequisites`.
- **A live dictation / end-to-end GPU run.** User-owned per the epic; out of scope for this review.
- **The `1129` baseline number on `main` was not independently re-run** (I did not check out or run
  `main`'s suite). It is corroborated by the branch being linear off `df4738f`, by exactly 30 added
  test cases, and by zero removed test functions — but the literal `1129 passed` is the comment's
  claim, not a run I performed.
- **The `whisper_engine`/`doctor` "untouched" claim beyond `main` equality.** I verified byte
  equality against `main`'s merge-base content; I did not diff against an independent copy.

Each of these reasons is itself true: no real build was attempted, `cmake` is absent, and I never ran
`main`'s suite.

## What would falsify the central claim

The central claim is: *`whisper_cpp` is the one canonical engine value, `whisper.cpp` is a forgiving
alias across both the CLI and the config layer, the `[engine.*]` block resolves through the same
normaliser, and `setup` prints honest guidance plus a non-fatal GPU dev-package hint.*

It would be falsified by any of:

1. `normalize_engine("whisper.cpp") != "whisper_cpp"`, or the alias accepted by one of {config,
   `setup`} but not the other (F1/F4-style mutation).
2. A `[engine.whisper.cpp]` (dotted or quoted) block silently ignored instead of read (F2-style
   mutation), or both spellings accepted with silent precedence instead of a named hard error.
3. The unknown-engine/backend message not naming both `whistle` and `whisper_cpp` (or omitting the
   CLI-token pointer).
4. `setup` not printing `transcriber.engine = "whisper_cpp"`, printing the wrong spelling, or the
   needle path not saying no line is needed (F5-style mutation).
5. The hint printing (a) when no Vulkan runtime exists, or (b) as an error / non-zero exit, or (c)
   naming `base-devel`/`cmake`, or (d) on a Vulkan build.
6. `pytest` < 1159, any failing test, or `mypy` non-zero.

All six held under direct probing.

## Safe to relay to the user?

**Yes.** The user-facing output is truthful and verified end-to-end: the printed
`transcriber.engine = "whisper_cpp"` line is exactly what `load_config` accepts (confirmed by
loading a config with it), the `enabled = false` advice matches the startup rejection rule from
ticket 45, and the GPU hint is a diagnostic that leaves the exit code at `0`, prints only when a
runtime is present and a dev package is missing, and names the exact `pacman` line. The only caveat
is that ticket 50 plans to change the `[streaming]` advice; relay the output as it is today, not as a
frozen contract.

## Machine safety

No microphone, clipboard, keystrokes, windows, notifications, `sudo`, package installs, service
changes or network access were used. Fakes stood in for every acquisition step; the only real
subprocesses were `uv run pytest`, `uv run mypy` and `uv run ges2t setup whisper` (which rejects in
`plan_setup` before any build). Probe files under `/tmp/rev47` were removed; `git status` is clean.
