# 002 — Review: Whistle engine-specific config block

Ticket: `ticket.md` (`whistle-engine-config`) · Branch: `feat/whistle-engine-config` ·
Commit reviewed: `e3039ef` · Reviewer artefact, not application code.

## Verdict

**pass**

Every Definition-of-Done bullet was reproduced from scratch (not read off the test
file), the two documented deviations are sound, the TOML int-vs-float trap is genuinely
handled, `uv run mypy src/ges2t` exits 0 and is demonstrably enforcing, and the scope is
exactly the three files the ticket permits. No blocking findings.

## Method

All commands were run in the worktree at commit `e3039ef` with the real `uv`
environment. The int-vs-float and validation checks were re-derived by writing an
independent probe script under `/tmp` (never in the repo), writing a real TOML file to a
temp path, parsing it with `tomllib`, and feeding the resulting mapping through
`WhistleConfig.from_mapping`. The mypy-effectiveness probe was created and then deleted;
`git status --porcelain` was empty afterwards.

## Per-criterion findings

### 1. TOML int-vs-float trap — PASS (real experiment)

Real TOML written to a temp path and parsed with `tomllib`:

```
parsed block: {'language': 'en', 'window_seconds': 5, 'chunk_seconds': 1}
types: {'language': 'str', 'window_seconds': 'int', 'chunk_seconds': 'int'}
result.config: WhistleConfig(language='en', window_seconds=5.0, chunk_seconds=1.0)
window type/value: float 5.0
chunk type/value: float 1.0
warnings: []
PASS: no error, no warnings, normalised to 5.0/1.0
```

`window_seconds = 5` and `chunk_seconds = 1` arrive as Python **`int`**, are accepted,
and become `5.0`/`1.0` floats with **zero warnings**. `bool` is rejected before the
`int` path is reached:

```
WhistleConfigError: engine.whistle.window_seconds: expected a number (int or float, not bool), got bool (True)
WhistleConfigError: engine.whistle.chunk_seconds: expected a number (int or float, not bool), got bool (False)
```

`isinstance(True, int)` is `True`, so the explicit `isinstance(value, bool)` short-circuit
in `_read_seconds` is load-bearing, not decorative. Correct.

### 2. Every validation rule — PASS (all reproduced by me)

| Rule | My observed result |
|---|---|
| `language` string, any string, no allowed-list | `zz-invented` and `klingon` both accepted, `warnings == []` |
| `language` non-string | `5`, `None`, `['en']`, `{'a': 1}`, `True` all rejected as `engine.whistle.language: expected a string, got <type> (value)` |
| `chunk_seconds < 0.25` | `0.24` → `...chunk_seconds = 0.24 is too small: ... must be at least 0.25 seconds` |
| `chunk_seconds >= window_seconds` | `window=5, chunk=5` → `...chunk_seconds = 5.0 is invalid: chunk_seconds must be less than window_seconds = 5.0 seconds` |
| `window_seconds > 30` | `30.0001` → `...window_seconds = 30.0001 is too large: ... must be at most 30 seconds (the engine's per-call limit)` |
| `window_seconds > 6` | warning, **not** an error; `6.0001`, `8.0`, `30.0` all return a config plus one latency warning |
| `window_seconds == 6` | accepted, `warnings == []` |
| `window_seconds == 30` | accepted **with** the latency warning |
| wrong types | `"five"`, `None`, `[5]` rejected with key, value and expectation |
| exact required case `window=5`/`chunk=1` | accepted, `warnings == []` |

Boundary semantics match ADR 0011/0005 precisely: `>=`, `<`, `> 30`, `> 6`.

### 3. Absent block, unknown keys, `model_path` — PASS

`from_mapping({})` (the normal "no `[engine.whistle]` section" path, since the generic
layer never synthesizes the block) returns
`WhistleConfig(language='', window_seconds=5.0, chunk_seconds=1.0)` with `warnings == []`,
and the return type is `WhistleConfigResult`.

Unknown keys warn and are ignored, in insertion order:

```
warnings: ["engine.whistle: unknown key 'tray'", "engine.whistle: unknown key 'model_path'"]
config: WhistleConfig(language='', window_seconds=5.0, chunk_seconds=1.0)
```

`model_path` inside the engine block is correctly treated as unknown (generic per ADR
0009). I also confirmed the input mapping is not mutated by `from_mapping`.

### 4. The two documented deviations — both ACCEPTED

**(a) Dedicated `WhistleConfigError`.** The reasoning is sound. `ConfigError`'s own
docstring contracts that *"Every message names the offending file and key"*, and
`from_mapping` is a one-argument boundary that never sees a path — the ticket fixes that
signature. Reusing `ConfigError` would either violate its documented contract or force a
synthetic path into every engine-block message, and it would make the `transcribers`
package import the generic config layer, working against the ADR 0009 boundary. A local
exception whose messages carry the dotted `engine.whistle.<key>` location instead is the
right call, and it matches the "warn-don't-raise"/typed-boundary style of ticket 3.

*Doctor/CLI impact:* real but small and already anticipated. `doctor-command`'s Config
validity row must catch **both** `ConfigError` and `WhistleConfigError` (two names in one
`except`), otherwise a bad `[engine.whistle]` value would escape as an unhandled
traceback. Nothing in the current CLI catches either yet (`cli.py` is a `--version`/`--help`
stub), so choosing the exception name now is the cheapest moment to make this choice
rather than at merge time. I would not block on it; I would record it as a hard hand-off
to `doctor-command`. See Risks.

**(b) Rejecting non-finite floats.** Correct and tested, and it closes a real silent hole.
TOML permits `inf`/`nan`. For `window_seconds = nan` **every** comparison is `False`
(`nan > 30`, `nan >= window`, `nan > 6`), so without the guard it would have been accepted
as a config that no downstream range check could catch. I reproduced the rejection for
`nan`, `inf`, `-inf` on **both** keys through the shared `_read_seconds` helper:

```
engine.whistle.window_seconds = nan is invalid: expected a finite number of seconds
engine.whistle.chunk_seconds = nan is invalid: expected a finite number of seconds
```

The behaviour is right and the `nan` case specifically is pinned by
`test_non_finite_numbers_are_rejected`. One minor coverage gap (non-blocking): that test
parametrises only `window_seconds`, so the `chunk_seconds` non-finite path is verified by
code-sharing rather than by its own case. I verified it works; a future refactor that
split the helper could lose it silently.

### 5. ADR 0019 compliance — PASS

- `from_mapping(...) -> WhistleConfigResult`, a frozen dataclass; no positional tuple is
  returned anywhere (`_read_language -> str`, `_read_seconds -> float`,
  `_validate_ranges -> None`).
- `Any` appears **only** as `Mapping[str, Any]` at the genuine raw-TOML boundary; it is
  converted to a typed `WhistleConfig` immediately. No bare `Any` in any return type.
- No implicit `Optional`; all annotations present including `-> None`.
- `uv run mypy src/ges2t` → `Success: no issues found in 5 source files`, exit 0.
- Effectiveness demonstrated: I added `src/ges2t/_reviewer_probe.py` containing an
  unannotated function, and mypy exited 1 with
  `error: Function is missing a type annotation  [no-untyped-def]`. I then deleted the
  probe and `git status --porcelain` was **empty** — no probe left behind.

### 6. Scope — PASS

`git diff --name-status` against the branch base shows only:

```
M  docs/project/README.md
M  docs/project/core-dictation-pipeline/whistle-engine-config/ticket.md
A  docs/project/core-dictation-pipeline/whistle-engine-config/001-comment.md
A  src/ges2t/transcribers/__init__.py
A  src/ges2t/transcribers/whistle_config.py
A  tests/test_whistle_config.py
```

- `src/ges2t/config.py` is **not** in the diff — untouched.
- None of `interfaces.py`, `model_store.py`, `service.py`, `doctor.py`, `pipeline.py`,
  `audio/`, `injectors/`, `ui/` exists.
- `whistle_config.py` imports only `math`, `collections.abc`, `dataclasses`, `typing`;
  it does **not** import `ges2t.interfaces` or `cactus_needle`, so it is importable with no
  backend present (ADR 0010).

### 7. Tests are honest and headless — PASS

- 63 tests collected; `uv run pytest tests/test_whistle_config.py -q` → `63 passed in
  0.09s`; full suite `99 passed in 0.22s`. Fast and headless (no engine, no I/O beyond a
  temp-free `tomllib.loads` on a literal string).
- The tests exercise real behaviour, not constants: every range/warn/error test calls
  `from_mapping` and asserts the returned config, the raised type and the message
  contents. `test_result_is_a_typed_frozen_dataclass` asserts assignment raises
  `FrozenInstanceError`; `test_real_toml_parses_the_window_as_int_and_is_still_accepted`
  asserts the parsed value really is an `int` before validating (it tests the trap, not the
  fix).
- I did not find a test that would still pass with the behaviour removed. The strongest
  candidate — `test_default_values_match_the_adr_schema` — pins literal `0.25`/`30.0`/`6.0`
  and the `KNOWN_KEYS` set, so it fails if a constant drifts.

### 8. Board / ticket status and links — PASS (one process nit)

- `docs/project/README.md` row 4 is `in review` with branch `feat/whistle-engine-config`;
  the ticket header is `Status: in review`. Correct.
- All relative links in `ticket.md` and `001-comment.md` resolve:
  `../config-loading/ticket.md`, `../whistle-transcriber-adapter-batch/ticket.md`,
  `../../live-dictation-ux/whistle-streaming-adapter/ticket.md`,
  `../../live-dictation-ux/streaming-window-engine/ticket.md`, `../epic.md`, and the three
  ADRs.
- Nit: the ticket's DoD boxes are all ticked while the status is still `in review`. That is
  the implementer's self-assessment and does not affect this review, but the project's own
  `config-loading` review flagged the opposite convention; the orchestrator may want one
  consistent rule for when boxes are ticked.

## Evidence — commands and real output

```
$ git rev-parse HEAD
e3039eff56e297346022f84b7e9ddb9df0f4b72d

$ uv run mypy src/ges2t
Success: no issues found in 5 source files          # exit 0

$ uv run pytest -q
99 passed in 0.22s

$ uv run pytest tests/test_whistle_config.py --collect-only -q | tail -1
63 tests collected in 0.02s

# mypy effectiveness probe (created, run, deleted)
$ uv run mypy src/ges2t        # with src/ges2t/_reviewer_probe.py present
src/ges2t/_reviewer_probe.py:1: error: Function is missing a type annotation  [no-untyped-def]
Found 1 error in 1 file (checked 6 source files)    # exit 1
$ git status --porcelain                            # after rm
(empty)
```

The independent `/tmp` probe's full output is summarised in sections 1–3 above;
the key line is `types: {'window_seconds': 'int', 'chunk_seconds': 'int'}` followed by
`warnings: []` and normalisation to `5.0`/`1.0`.

## Risks / follow-ups

1. **`doctor-command` (ticket 12) — must catch two exception types.** Its "Config valid"
   check has to catch `WhistleConfigError` in addition to `ConfigError`, and its "Config
   warnings" row must call `WhistleConfig.from_mapping` on the **selected** engine block
   (not just the generic sections) to surface the unknown-key and latency warnings, which
   the generic layer never sees. Assigning `doctor` to import `WhistleConfigError` and
   `WhistleConfig` closes the only integration seam this ticket opens. Non-blocking for
   this ticket, but it must not be forgotten.
2. **`whistle-transcriber-adapter-batch` (ticket 8) — block selection.** The adapter must
   fetch its block with `config.engine_blocks.get(config.transcriber.engine, {})`, never
   `config.engine_blocks["whistle"]`; the generic layer does not synthesize the block, so
   its ticket's bracket form is correct only while `whistle` is selected and would
   `KeyError` on a config with no `[engine.whistle]` section. `from_mapping({})` returning
   defaults makes the `.get(..., {})` form safe. `WhistleConfigError` should be translated
   to whichever user-facing error the adapter/CLI uses.
3. **Non-blocking gap:** `test_non_finite_numbers_are_rejected` covers only
   `window_seconds`; add `chunk_seconds` to that parametrisation when this file is next
   touched (behaviour is already correct — I verified it).
4. **Non-blocking observation:** a `window_seconds` of `0` or negative produces a message
   about `chunk_seconds` (the cross-field rule fires first, since the default `chunk` is
   not `<` it) rather than a message saying the window is too small. The ticket defines no
   minimum window, and the config is still rejected, so this is acceptable; only worth a
   clearer message if a future ticket adds a window minimum.

## Not verified / out of scope

- I did not run the code through a real `ges2t doctor` or CLI, because neither exists yet
  (this ticket is not allowed to create them). The two exception types are not yet consumed
  anywhere.
- I did not evaluate the downstream streaming semantics that consume these numbers
  (`streaming-window-engine`); this ticket correctly validates numbers only.
- I confirmed relative links resolve as files but did not fetch any external URL.
