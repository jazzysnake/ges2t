# 001 — Config loading implementation notes

Ticket: `ticket.md` (config-loading) · Branch: `feat/config-loading`

## What was built

Exactly two files, as the ticket requires:

- `src/ges2t/config.py` — the generic layer (ADR 0009).
- `tests/test_config.py` — 33 tests covering every rule in the definition of done.

No `interfaces.py`, `model_store.py`, `service.py`, `doctor.py`, `pipeline.py`, `audio/`,
`injectors/`, `transcribers/`, or `ui/` was created, and `config.py` imports nothing from
`ges2t` at all (in particular not `interfaces.py`, which another agent owns in parallel).
Its only imports are `os`, `tomllib`, `collections.abc`, `dataclasses`, `pathlib`,
`typing`.

## Final public shapes

```python
KNOWN_ENGINES: tuple[str, ...] = ("whistle",)
DEFAULT_ENGINE: str = KNOWN_ENGINES[0]          # "whistle"
KNOWN_HOTKEY_MODES: tuple[str, ...] = ("ptt", "toggle")
INJECTION_DEFAULT_BACKEND: str = "ydotool"
AUDIO_DEFAULT_DEVICE: str = ""

@dataclass(frozen=True)
class TranscriberConfig:  engine: str = DEFAULT_ENGINE; model_path: str = ""
@dataclass(frozen=True)
class HotkeyConfig:       mode: str = "ptt"; key: str = ""
@dataclass(frozen=True)
class AudioConfig:        device: str = ""
@dataclass(frozen=True)
class InjectionConfig:    backend: str = "ydotool"
@dataclass(frozen=True)
class UIConfig:           tray: bool = True; notifications: bool = True; trailing_space: bool = True
@dataclass(frozen=True)
class StreamingConfig:    enabled: bool = True

@dataclass(frozen=True)
class Config:
    transcriber: TranscriberConfig
    hotkey: HotkeyConfig
    audio: AudioConfig
    injection: InjectionConfig
    ui: UIConfig
    streaming: StreamingConfig
    engine_blocks: Mapping[str, Mapping[str, Any]]

@dataclass(frozen=True)
class ConfigLoadResult:
    config: Config
    warnings: list[str]

def load_config(path: Path | None = None) -> ConfigLoadResult: ...
def default_config() -> Config: ...
def default_config_path() -> Path: ...
def resolve_config_path(flag: str | Path | None) -> Path: ...
class ConfigError(Exception): ...
```

Every section dataclass carries the ADR's default value as its field default, so
`default_config()` is `return Config()` and defaults have exactly one home. The dataclasses
are frozen; `ConfigLoadResult` returns a typed field instead of the tuple the ticket text
originally specified (ADR 0019 correction).

`KNOWN_ENGINES` is the single source of truth: `DEFAULT_ENGINE` is derived from it
(`KNOWN_ENGINES[0]`), and the unknown-engine error message is generated from the same tuple,
so the factory and the message cannot drift.

## `[engine.*]` boundary — what the generic layer does and does not do

- Every `[engine.<name>]` table is copied into `engine_blocks` **verbatim**, nested tables,
  lists and scalars included, with no key inspection. `[engine.whistle]` and
  `[engine.something_else]` both survive; blocks for engines that are not selected are
  preserved and are **not** errors (ADR 0009).
- Keys inside any `[engine.*]` block never produce warnings. That is the adapter's job
  (`whistle-engine-config`), and a test (`test_engine_block_contents_are_not_interpreted_or_warned_about`)
  locks it in.
- The generic layer does not contain `window_seconds`, `chunk_seconds`, or `language`.
  `[streaming].enabled` is the only streaming key it knows.

## XDG default resolution

```python
xdg = os.environ.get("XDG_CONFIG_HOME")
base = Path(xdg) if xdg else Path.home() / ".config"
return base / "ges2t" / "config.toml"
```

`resolve_config_path(None)` returns this default; a supplied flag is returned as `Path(flag)`
with no existence check (the caller decides whether absence is fatal). `load_config(None)`
uses the same default, so the CLI can either pass `resolve_config_path(args.config)` or call
`load_config()` directly.

Per the XDG spec, an **empty** `XDG_CONFIG_HOME` is treated as unset and falls back to
`~/.config`; this is tested (`test_empty_xdg_config_home_falls_back_to_home`).

## Error and warning messages (real output)

```
<form>: invalid TOML: Expected ']' at the end of a table declaration (at line 1, column 4)
<form>: key 'ui.tray': expected bool, got str ('yes')
<form>: key 'transcriber.model_path': expected str, got int (5)
<form>: key 'ui': expected table, got bool (True)
<form>: transcriber.engine = 'whisper_cpp' is not a known engine; known engines: 'whistle'
<form>: hotkey.mode = 'hold' is not supported; expected 'ptt' and 'toggle'
warning: "<form>: unknown key 'ui.tray_icon'"
```

The `<form>` is the resolved config path. Warnings are returned in file order and contain the
same file prefix, so `doctor` can print them as-is. `ConfigError` is the only exception this
module raises; `tomllib.TOMLDecodeError` is chained (`from exc`), so a debug flag can still
show the original traceback.

## Judgement calls (the orchestrator should know these)

1. **Structural validation of the `engine` table is generic.** The generic layer requires
   that `[engine]` is a table and that each `engine.<name>` value is a table, because its
   declared output type is `Mapping[str, Mapping[str, Any]]`. It still never reads the keys
   *inside* a block. So `engine = "whistle"` or `[engine]\nwhistle = "x"` is a hard
   `ConfigError`, not a pass-through. I read "the generic layer knows the existence of
   `[engine.<name>]` tables" (ADR 0009) as covering their shape.
2. **Unknown top-level keys are warnings too**, not only unknown keys inside the six
   generic sections. `nonsense = 1` or a stray `[ui_extra]` table is a typo just as much as
   `[ui] tray_icon`, and ADR 0009 says unknown keys are "tolerated but surfaced". The warning
   is `unknown key 'nonsense'` / `unknown key 'ui_extra'`.
3. **`engine_blocks` is a declared `Mapping` but not wrapped in `MappingProxyType`.** The
   dataclasses are frozen in the ticket's sense (no field rebinding); `warnings` is likewise a
   plain `list`. Adapting the outer mapping to a read-only proxy would add ceremony without
   the ticket asking for deep immutability. If the reviewer wants it, it is a two-line change.
4. **`bool` is checked with `isinstance(value, bool)`**, so `tray = 1` is rejected even
   though `int` is `bool`'s supertype in Python. A test pins this.
5. **`resolve_config_path` accepts `str | Path | None`** rather than only `str`, so tests and
   callers can pass either; argparse will hand it a `str`.
6. **Module-level public constants beyond `KNOWN_ENGINES`** (`DEFAULT_ENGINE`,
   `KNOWN_HOTKEY_MODES`, `INJECTION_DEFAULT_BACKEND`, `AUDIO_DEFAULT_DEVICE`) exist so call
   sites (the factory, the future CLI help text) do not re-hardcode `"ydotool"`/`"ptt"`. The
   ticket only mandated `KNOWN_ENGINES`; these are additive and cheap.

## Surprises / notes for the orchestrator

- Nothing surprising in `tomllib`: it returns plain `dict`/`list`/scalars, preserves file
  order, and reports line/column in `TOMLDecodeError`. Python here is `/usr/bin/python3`
  3.14.7, `tomllib` is stdlib.
- `load_config()` with no argument reads the *real* user config, so the tests that exercise
  the XDG default monkeypatch `XDG_CONFIG_HOME`/`HOME`; no test touches the developer's
  `~/.config/ges2t/config.toml`.
- The one consumer-facing contract worth restating: **`transcriber.engine` is validated by
  the generic layer, but its `[engine.<name>]` block is not.** `whistle-engine-config` should
  fetch it with `config.engine_blocks.get(config.transcriber.engine, {})` and own the
  defaults/warnings for that mapping. The generic layer deliberately does not pre-select or
  copy it, to keep "pass through uninterpreted" literal.
- `DEFAULT_ENGINE` is derived as `KNOWN_ENGINES[0]`, so reordering that tuple changes the
  default. If an engine is ever prepended, that is the line to look at.

## Verification (real output)

```
$ uv run pytest
platform linux -- Python 3.14.7, pytest-9.1.1, pluggy-1.6.0
rootdir: /home/matee/Workspace/Personal/ges2t/.worktrees/config-loading
configfile: pyproject.toml
plugins: anyio-4.15.1
collected 36 items

tests/test_config.py .................................                   [ 91%]
tests/test_smoke.py ...                                                  [100%]

============================== 36 passed in 0.15s ==============================

$ uv run pytest tests/test_config.py -q
.................................                                        [100%]
33 passed in 0.19s

$ uv run mypy src/ges2t
Success: no issues found in 3 source files
```

### Test coverage map

| Ticket bullet | Test(s) |
|---|---|
| absent file → defaults | `test_missing_file_yields_all_defaults`, `test_empty_file_yields_all_defaults`, `test_default_values_match_the_adr_schema` |
| invalid TOML → error naming file + problem | `test_invalid_toml_error_names_file_and_parse_problem` |
| wrong type → file, key, expected, received | `test_wrong_typed_*` (bool, int-for-bool, str, every section) |
| unknown generic key → warning | `test_unknown_key_in_a_generic_section_is_a_warning`, `..._top_level_...`, `..._section_...`, `test_unknown_keys_are_reported_in_file_order` |
| all `[engine.*]` verbatim, unselected preserved | `test_engine_blocks_survive_verbatim_including_unselected_engines`, `test_unselected_engine_block_alone_is_not_an_error`, `test_engine_block_contents_are_not_interpreted_or_warned_about` |
| unknown engine → error listing known names | `test_unknown_engine_name_lists_the_known_names`, `test_engine_constants_are_consistent` |
| `hotkey.mode` only ptt/toggle | `test_hotkey_mode_rejects_anything_but_ptt_and_toggle`, `test_hotkey_mode_accepts_ptt_and_toggle` |
| `--config` override + XDG default | `test_resolve_config_path_honours_the_flag_over_xdg`, `test_config_flag_override_is_honoured_by_load_config`, `test_resolve_config_path_uses_xdg_config_home`, `test_resolve_config_path_falls_back_to_home_config`, `test_empty_xdg_config_home_falls_back_to_home` |
| frozen/typed result classes | `test_load_result_is_a_typed_frozen_dataclass`, `test_config_dataclasses_are_frozen` |
