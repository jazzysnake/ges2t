# 002 — Review: config loading (generic TOML layer, engine selector, engine blocks)

- **Ticket:** `ticket.md` (config-loading)
- **Branch:** `feat/config-loading`
- **Worktree:** `.worktrees/config-loading`
- **Reviewed commit:** `306b232b246671b92abecb450f04a8072f702dd1` — `config: generic TOML layer, engine selector, verbatim engine blocks`
- **Base:** `main` at `5edf284` (`board: ticket 1 (project-scaffold) is done`)
- **Reviewer:** independent review run from the worktree above. I ran the commands myself; no
  checkbox was trusted.

## Verdict

**pass**

Every Definition-of-done item is genuinely satisfied when re-run from the worktree. The
generic/engine boundary — the point of the ticket — is real: I proved by introspection and by
experiment that no engine knob leaks into any generic dataclass, and that `[engine.*]` tables
(including nested tables and lists, and blocks for unselected engines) cross into
`engine_blocks` verbatim and unread. `ConfigLoadResult` is a frozen dataclass, not a tuple.
`uv run pytest tests/test_config.py`, `uv run pytest` and `uv run mypy src/ges2t` all pass, and
mypy enforcement was proven effective with a temporary unannotated probe that was fully
reverted (`git status` clean). Scope discipline holds: exactly `src/ges2t/config.py` and
`tests/test_config.py` were added as source, no forbidden module exists, `config.py` imports
only stdlib, and no TOML-writing dependency was added.

No blocking findings. There are minor, non-blocking observations and one
`whistle-engine-config` hand-off note at the end.

## What was reviewed

`git diff --name-status main...HEAD` is exactly:

```
M  docs/project/README.md
A  docs/project/core-dictation-pipeline/config-loading/001-comment.md
M  docs/project/core-dictation-pipeline/config-loading/ticket.md
A  src/ges2t/config.py
A  tests/test_config.py
```

`git diff --stat main...HEAD -- pyproject.toml uv.lock` is empty: no dependency was added. The
full diff is `412` lines of `config.py` and `368` lines of `test_config.py`.

## Critical check: the generic/engine boundary is genuinely respected

**Introspection — no engine knob in any generic dataclass.** I walked
`dataclasses.fields(...)` for every public class and searched for `window_seconds`,
`chunk_seconds`, `language`:

```
TranscriberConfig: ['engine', 'model_path']
HotkeyConfig: ['mode', 'key']
AudioConfig: ['device']
InjectionConfig: ['backend']
UIConfig: ['tray', 'notifications', 'trailing_space']
StreamingConfig: ['enabled']
Config: ['transcriber', 'hotkey', 'audio', 'injection', 'ui', 'streaming', 'engine_blocks']
ConfigLoadResult: ['config', 'warnings']
OK: no window_seconds/chunk_seconds/language in any generic dataclass
```

`grep -n "window_seconds\|chunk_seconds\|language" src/ges2t/config.py` returns **no matches**
— the strings are not even present in the generic module.

**Verbatim round-trip, nested tables and lists included.** A config with
`[engine.whistle]`, `[engine.something_else]`, nested anonymous tables, a list of tables and a
dotted sub-table produced:

```
engine_blocks: {'whistle': {'language': 'en', 'window_seconds': 5, 'chunk_seconds': 1,
'nested': {'a': 1, 'b': [1, 2, 3]}, 'list_of_tables': [{'x': 1}, {'x': 2}]},
'something_else': {'whatever': 'kept', 'flag': True, 'deep': {'inner': {'k': 'v'}}}}
```

and the file-level nested form:

```
{'whistle': {'language': 'de',
             'post': {'normalize': True, 'tags': ['a', 'b']},
             'deep': {'inner': {'x': 1}}}}
```

No warnings were emitted, and `transcriber.engine` stayed `"whistle"`.

**Unselected engines are preserved, not errors.** With `engine = "whistle"` and only

```toml
[engine.whisper_cpp]
model = "ggml.bin"
```

`load_config` returned `warnings == []` and `engine_blocks == {"whisper_cpp": {"model": "ggml.bin"}}`.

**The layer never reads block keys.** A block containing keys that shadow generic sections and
a malformed generic value —

```toml
[engine.whistle]
transcriber = { engine = "not-a-real-engine" }
hotkey = { mode = "nonsense" }
ui = "not a table"
```

— loaded with `warnings == []`, `transcriber.engine == "whistle"` (from the real selector),
`hotkey.mode == "ptt"`, and `engine_blocks["whistle"]["ui"] == "not a table"`. Nothing inside
the block was validated, converted, or warned about. The only shape check is structural
(`[engine]` must be a table and each `engine.<name>` must be a table), which the declared
output type `Mapping[str, Mapping[str, Any]]` requires; this is the implementer's judgement
call #1 and I accept it.

## Per-criterion findings (DoD, one by one)

### 1. Frozen generic dataclasses + top-level `Config` — PASS

`typing.get_type_hints` and `__dataclass_params__.frozen` confirm:

```
TranscriberConfig: frozen=True hints={'engine': str, 'model_path': str}
HotkeyConfig:      frozen=True hints={'mode': str, 'key': str}
AudioConfig:       frozen=True hints={'device': str}
InjectionConfig:   frozen=True hints={'backend': str}
UIConfig:          frozen=True hints={'tray': bool, 'notifications': bool, 'trailing_space': bool}
StreamingConfig:   frozen=True hints={'enabled': bool}
Config:            frozen=True hints={..., 'engine_blocks': Mapping[str, Mapping[str, Any]]}
```

Defaults match ADR 0009: `engine="whistle"`, `model_path=""`, `mode="ptt"`, `key=""`,
`device=""`, `backend="ydotool"`, `tray/notifications/trailing_space=True`, `enabled=True`,
`engine_blocks={}`. `default_config()` returns `Config()`.

### 2. `load_config(path: Path | None = None) -> ConfigLoadResult`, frozen result, all rules — PASS

`inspect.signature` gives `load_config(path: 'Path | None' = None) -> 'ConfigLoadResult'`, and
`ConfigLoadResult` is `@dataclass(frozen=True)` with `config: Config`, `warnings: list[str]`.
All seven ADR rules were reproduced independently — see the next section.

### 3. Known engine names are one module-level tuple — PASS

`KNOWN_ENGINES: tuple[str, ...] = ("whistle",)`; `DEFAULT_ENGINE = KNOWN_ENGINES[0]`; the
unknown-engine error is formatted from `KNOWN_ENGINES` (`", ".join(repr(n) for n in KNOWN_ENGINES)`),
so message and factory share one source. `KNOWN_HOTKEY_MODES = ("ptt", "toggle")` is likewise a
single tuple. The extra public constants (`DEFAULT_ENGINE`, `KNOWN_HOTKEY_MODES`,
`INJECTION_DEFAULT_BACKEND`, `AUDIO_DEFAULT_DEVICE`) are additive and harmless.

### 4. `--config` / XDG resolution helper — PASS

`resolve_config_path(flag: str | Path | None) -> Path` exists and is exported. Behaviour,
verified directly:

```
resolve(None) with XDG set: /tmp/xdgtest/ges2t/config.toml
resolve('/custom/x.toml'): /custom/x.toml
resolve(Path('/custom/x.toml')): /custom/x.toml
resolve(None) no XDG: /tmp/hometest/.config/ges2t/config.toml
resolve(None) empty XDG: /tmp/hometest/.config/ges2t/config.toml
```

`load_config(None)` uses the same default. The helper does no existence check, which matches
the ticket's "the caller decides whether absence is fatal". (See risk 2 about relative
`XDG_CONFIG_HOME`.)

### 5. `tests/test_config.py` coverage — PASS (with two weak-but-redundant tests, below)

33 tests collected. I mapped each DoD bullet to tests and re-ran them. All bullets are
exercised: absent file → defaults; empty file → defaults; unknown generic key → warning;
unknown top-level/section key → warning (implementer's judgement call #2); unknown engine name
→ error listing known names; `[engine.whistle]` + `[engine.something_else]` verbatim; bad type
in every section; `hotkey.mode` accept/reject; XDG default and override; frozen result.

### 6. `uv run pytest tests/test_config.py` and `uv run pytest` — PASS

```
$ uv run pytest tests/test_config.py -q
.................................                                        [100%]
33 passed in 0.09s

$ uv run pytest -q
....................................                                     [100%]
36 passed in 0.14s
```

Headless and fast: only `tmp_path` files and `monkeypatch`; no mic, model, `/dev/uinput`, or
network (ADR 0014). Python here is 3.14.7.

### 7. `uv run mypy src/ges2t` exits 0, enforcement real — PASS

```
$ uv run mypy src/ges2t
Success: no issues found in 3 source files
mypy exit=0
```

Enforcement probe (temporary, then fully reverted):

```
$ cat src/ges2t/_review_probe.py
def untyped_probe(x):
    return x

$ uv run mypy src/ges2t
src/ges2t/_review_probe.py:1: error: Function is missing a type annotation  [no-untyped-def]
Found 1 error in 1 file (checked 4 source files)
mypy exit: 1

$ rm src/ges2t/_review_probe.py   # probe reverted
$ uv run mypy src/ges2t
Success: no issues found in 3 source files
mypy exit: 0
$ git status --porcelain
(empty)
```

Every `def` in `config.py` and `test_config.py` carries a return annotation, including the
`-> None` on `_warn_unknown_keys` and every test function. `Any` appears only in
`Mapping[str, Any]` for raw `tomllib` output — the genuine untyped boundary ADR 0019 permits —
and nowhere else. There is no implicit `Optional` (`load_config(path: Path | None = None)`,
`resolve_config_path(flag: str | Path | None)` are explicit), and no positional-tuple return
value: `grep -n "tuple\[" src/ges2t/config.py` matches only the two module constants, not a
return.

## ADR 0009 rule-by-rule reproduction (run by me, exact output)

```
missing file -> all defaults, not error:
  ConfigLoadResult(config=Config(transcriber=TranscriberConfig(engine='whistle', model_path=''),
  ...), warnings=[])                                    # and no exception

invalid TOML:
  /tmp/.../config.toml: invalid TOML: Expected ']' at the end of a table declaration (at line 1, column 4)

wrong-typed value:
  /tmp/.../config.toml: key 'ui.tray': expected bool, got str ('yes')
  /tmp/.../config.toml: key 'transcriber.model_path': expected str, got int (5)
  /tmp/.../config.toml: key 'ui.tray': expected bool, got int (1)          # bool is strict

unknown generic key -> warning returned, not raised:
  warnings: ["/tmp/.../config.toml: unknown key 'ui.tray_icon'"]
  config.ui: UIConfig(tray=False, notifications=True, trailing_space=True)

unknown engine name:
  /tmp/.../config.toml: transcriber.engine = 'whisper_cpp' is not a known engine; known engines: 'whistle'

hotkey.mode:
  'ptt':    ACCEPTED -> 'ptt'
  'toggle': ACCEPTED -> 'toggle'
  'hold':   REJECTED -> ...: hotkey.mode = 'hold' is not supported; expected 'ptt' and 'toggle'
  'PTT':    REJECTED -> ...: hotkey.mode = 'PTT' is not supported; expected 'ptt' and 'toggle'

--config / XDG:  (see DoD 4 above)
```

An unreadable file and a directory path both produce a `ConfigError` naming the file
(`...: cannot read configuration: [Errno 13] Permission denied: ...`), so a caller never has to
catch a bare `OSError`.

## Error-message quality (two quoted verbatim)

```
/tmp/.../config.toml: key 'ui.tray': expected bool, got str ('yes')
```

Judgement: **good.** It is a full, actionable sentence naming the resolved file, the dotted
key, the expected type, and the received type *and value*. A user can go straight to
`[ui] tray` and fix it. Received type alone would have been terse; the value (`'yes'`) is the
useful part, and it is included.

```
/tmp/.../config.toml: transcriber.engine = 'whisper_cpp' is not a known engine; known engines: 'whistle'
```

Judgement: **good.** It names the file, echoes the offending value, and — critically — lists
the accepted names from the same tuple the factory will use, so the message cannot drift from
the implementation. It is one sentence with no code/symbol leakage.

The warning form, `...: unknown key 'ui.tray_icon'`, is deliberately terser because it is
non-fatal and surfaces in `doctor`; it still carries the file and dotted key. `doctor` can
print `ConfigLoadResult.warnings` verbatim.

## Test honesty

The tests exercise behaviour, not internals: they write real TOML to `tmp_path`, call
`load_config`, and assert the resulting `Config`/`ConfigLoadResult` or the raised `ConfigError`
message text. The negative tests would fail if the behaviour were removed — e.g.
`test_engine_block_contents_are_not_interpreted_or_warned_about` pins `warnings == []` and the
raw block contents, so an implementation that validated engine keys would fail it; the
verbatim test compares the full nested structure. The frozen tests use real
`dataclasses.FrozenInstanceError`.

Two non-blocking weaknesses (redundant, not deceptive):

- `test_missing_default_path_uses_the_xdg_location` asserts only that defaults come back; it
  would still pass if `load_config()` ignored `XDG_CONFIG_HOME` entirely, because a missing
  file always yields defaults. The actual XDG resolution is genuinely covered by
  `test_resolve_config_path_uses_xdg_config_home` and
  `test_resolve_config_path_falls_back_to_home_config` (which assert the path), so the
  behaviour is verified — the named test just doesn't test its own name.
- `test_wrong_typed_key_in_every_section_is_rejected` only asserts `"expected" in message`;
  the per-section type specifics are covered by the dedicated bool/str/int tests.

I did not mutation-test (that would require editing implementation code, which this review must
not do); the above is by reading and execution.

## Scope discipline — PASS

`git ls-files '*.py'` lists only `src/ges2t/__init__.py`, `src/ges2t/cli.py`,
`src/ges2t/config.py`, `tests/test_config.py`, `tests/test_smoke.py`. No `interfaces.py`,
`model_store.py`, `service.py`, `doctor.py`, `pipeline.py`, `audio/`, `injectors/`,
`transcribers/`, `ui/`, and no `tests/fakes.py`.

`config.py` imports only `os`, `tomllib`, `collections.abc`, `dataclasses`, `pathlib`,
`typing` — nothing from `ges2t` and no third-party package. `tomllib` is used in exactly one
place in `src/` (`config.py`, in `_read_toml`); no other module opens the config file.
`pyproject.toml`/`uv.lock` are unchanged by this branch, so no TOML-writing dependency was
added.

## Board / ticket status and links

The board row reads status `in review`, branch `feat/config-loading`; the ticket header reads
`Status: in review`. Both are the correct pre-merge state. I resolved every relative Markdown
link in the three touched docs files (`docs/project/README.md`, `ticket.md`,
`001-comment.md`): **29 links, 0 broken**.

One hygiene note: the ticket's Definition-of-done checkboxes are all still `- [ ]`, whereas
`project-scaffold`'s were ticked `- [x]` by its implementer before review. This does not
change the verdict (I ran every command) but the boxes should be ticked when the ticket is
flipped to `done`, or they will read as an unfinished checklist.

## Risks / recommended follow-up tickets

1. **Hand-off to `whistle-engine-config` (important).** The adapter must select its own block
   with `config.engine_blocks.get(config.transcriber.engine)` and must **not** assume the key
   exists: the generic layer never synthesizes a block, so an absent `[engine.whistle]` and an
   empty `[engine.whistle]` both arrive as "not present" (`.get(..., {})`). It owns defaults,
   type checks, unknown-key warnings, and range validation for `language`, `window_seconds`,
   `chunk_seconds`. `transcriber.engine` is already validated against `KNOWN_ENGINES`, but the
   block itself is completely unvalidated.
2. **TOML integer/float trap for the adapter.** `[engine.whistle] window_seconds = 5` parses
   to Python `int`, not `float`; `WhistleConfig` declares `window_seconds: float = 5.0`. If
   `from_mapping` checks `isinstance(value, float)` it will reject the ADR's own example. It
   should accept numeric int-or-float (excluding `bool`, which is an `int` subclass).
3. **`engine_blocks` is a plain mutable dict of raw dicts, and `warnings` is a mutable list**
   inside a frozen dataclass. ADR 0019 specifies `warnings: list[str]`, so this is compliant,
   and the implementer flagged the missing `MappingProxyType` as a deliberate call. Treat the
   mapping as read-only in consumers; wrap it only if the project wants deep immutability.
   Non-blocking.
4. **Relative `XDG_CONFIG_HOME` is not rejected.** `XDG_CONFIG_HOME=relative/dir` yields
   `relative/dir/ges2t/config.toml`. The XDG base-directory spec says relative paths must be
   ignored; the implementation special-cases only the empty string. Low severity, no ticket
   requirement. Optional follow-up: validate `Path(xdg).is_absolute()` and fall back to
   `~/.config` otherwise, with a test.
5. **Unknown *top-level* keys are also warned about** (`nonsense = 1`, `[ui_extra]`), which is
   broader than the DoD's "unknown keys in the generic sections". It is consistent with ADR
   0009's "unknown keys are tolerated but surfaced" and I consider it correct, but `doctor`
   should be aware it may see warnings for keys that are neither a known section nor inside
   one. Non-blocking.
6. **Ticket 2 (`core-interfaces-and-fakes`) is still `to do`** although the board lists it as a
   dependency of this ticket. `config.py` is import-independent (it imports nothing from
   `ges2t`), so merging it before ticket 2 cannot break at import time, and the files are
   disjoint. Merge ordering is the orchestrator's call.

## What I could not verify

- **End-to-end CLI `--config` plumbing** is not exercised: `cli.py` is still the scaffold stub
  and does not call `resolve_config_path`/`load_config` yet; that wiring belongs to
  `cli-surface-and-commands`. I verified the helper in isolation only.
- **Mutation testing** of the suite (deliberately breaking behaviours to see which tests fail)
  was not performed, because it would require editing implementation/test code, which this
  review is not allowed to do. Test honesty above is established by inspection plus execution.
- I did not inspect or use the sibling `core-interfaces-and-fakes` worktree, per instruction.
