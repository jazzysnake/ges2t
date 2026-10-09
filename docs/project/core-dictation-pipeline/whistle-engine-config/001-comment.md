# 001 — Whistle engine-specific config implementation notes

Ticket: `ticket.md` (`whistle-engine-config`) · Branch: `feat/whistle-engine-config`

## What was built

Exactly the three files the ticket allows:

- `src/ges2t/transcribers/__init__.py` — package marker (docstring only), so
  `ges2t.transcribers.whistle_config` is importable.
- `src/ges2t/transcribers/whistle_config.py` — the `[engine.whistle]` validator.
- `tests/test_whistle_config.py` — 63 tests.

No other module was created. In particular there is **no** `whistle.py`,
`interfaces.py`, `model_store.py`, `service.py`, or `streaming.py`; those belong to
other tickets. `src/ges2t/config.py` was **not modified** — it is merged and owned by
ticket 3. The module imports only `math`, `collections.abc`, `dataclasses`, and
`typing`, so it is importable and testable with no engine and no third-party package
(ADR 0010). Nothing imports `cactus_needle`.

## Final public shapes

```python
BLOCK_NAME: str = "engine.whistle"          # message prefix
DEFAULT_LANGUAGE: str = ""
DEFAULT_WINDOW_SECONDS: float = 5.0
DEFAULT_CHUNK_SECONDS: float = 1.0
MIN_CHUNK_SECONDS: float = 0.25
MAX_WINDOW_SECONDS: float = 30.0
LATENCY_KNEE_SECONDS: float = 6.0
KNOWN_KEYS: frozenset[str] = frozenset({"language", "window_seconds", "chunk_seconds"})

class WhistleConfigError(Exception): ...

@dataclass(frozen=True)
class WhistleConfig:
    language: str = ""
    window_seconds: float = 5.0
    chunk_seconds: float = 1.0

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> WhistleConfigResult: ...

@dataclass(frozen=True)
class WhistleConfigResult:
    config: WhistleConfig
    warnings: list[str]
```

`from_mapping` returns the frozen `WhistleConfigResult`, never a tuple (ADR 0019).
`raw` is the only `Any` in any signature — the genuine untyped boundary ADR 0019
permits; it is converted to a typed `WhistleConfig` immediately. `from_mapping` is a
`classmethod` so a subclass would still get its own type back; `cls` is inferred by
mypy and needs no annotation.

## Exact error and warning texts (real output)

Errors, all raised as `WhistleConfigError`, each naming the key, the offending value,
and the allowed type or range:

```
engine.whistle.language: expected a string, got int (5)
engine.whistle.window_seconds: expected a number (int or float, not bool), got str ('five')
engine.whistle.window_seconds: expected a number (int or float, not bool), got bool (True)
engine.whistle.chunk_seconds = 0.1 is too small: chunk_seconds must be at least 0.25 seconds
engine.whistle.chunk_seconds = 5.0 is invalid: chunk_seconds must be less than window_seconds = 5.0 seconds
engine.whistle.window_seconds = 31.0 is too large: window_seconds must be at most 30 seconds (the engine's per-call limit)
engine.whistle.window_seconds = nan is invalid: expected a finite number of seconds
```

Warnings, returned in `WhistleConfigResult.warnings` (never raised, mirroring the
generic layer's warn-don't-raise behaviour):

```
engine.whistle: unknown key 'tray'
engine.whistle: unknown key 'model_path'
engine.whistle.window_seconds = 8.0 is too large for the 1 s latency budget: measured per-chunk latency crosses 1 s above about 6 seconds of buffered audio, so this knowingly trades latency for context
```

The messages carry no file path, because `from_mapping` receives only the raw block and
the ticket fixes its one-argument signature. The dotted `engine.whistle.<key>` prefix is
the location; callers that have the resolved config path can prefix it when surfacing a
`WhistleConfigError` through `doctor`.

## The int-vs-float TOML trap

`tomllib` parses `window_seconds = 5` as a Python `int`, not a `float`, so a naive
`isinstance(value, float)` would reject the ADR's own documented example. The numeric
reader therefore:

1. accepts `int` **or** `float`;
2. rejects `bool` first, explicitly (`isinstance(value, bool) or not isinstance(value, (int, float))`),
   because `isinstance(True, int)` is `True` and `tray = true` must not silently become
   `1.0`; and
3. normalises to `float(value)` before any range check.

Three tests lock this down, the strongest being
`test_real_toml_parses_the_window_as_int_and_is_still_accepted`, which parses real TOML
with `tomllib`, asserts `isinstance(block["window_seconds"], int)`, and only then calls
`from_mapping`. `test_toml_integer_is_accepted_as_a_float_with_no_warnings` pins the
ticket's exact case (`window_seconds = 5`, `chunk_seconds = 1` → no warnings), and
`test_integer_and_float_spellings_produce_the_same_config` proves `5` and `5.0` agree.
`test_booleans_are_rejected_as_numbers` pins the `bool` exclusion for both keys.

## `from_mapping`'s contract for the missing block

The generic layer **never synthesizes** an `[engine.whistle]` block (ticket 3 review,
hand-off note 1): an absent `[engine.whistle]` and an empty one are indistinguishable at
the boundary. `from_mapping({})` therefore returns all defaults with `warnings == []`,
and that is the normal "no `[engine.whistle]` section" case, not an error.

Consumers must select the block themselves:

```python
block = config.engine_blocks.get(config.transcriber.engine, {})
result = WhistleConfig.from_mapping(block)
```

`dictation-pipeline-batch`'s ticket spells this as `config.engine_blocks["whistle"]`,
which is equivalent only while `whistle` is the selected engine; `.get(..., {})` is the
form that also survives a missing or unselected block. The module does **not** read the
generic `Config` and must not: it receives only the raw mapping.

## Judgement calls (the orchestrator should know these)

1. **A dedicated `WhistleConfigError` is introduced.** The ticket names neither an
   exception type nor a base class. I did **not** reuse `ges2t.config.ConfigError`: that
   class's own contract says "every message names the offending file", which an
   engine-block validator cannot honour (it never sees a path), and it would couple the
   transcribers package to the generic layer. `WhistleConfigError` subclasses `Exception`
   exactly as `ConfigError` does. If `doctor`/CLI wants one catch-all type, catching both
   is a two-name `except`; if the orchestrator prefers a shared base, that is a small,
   deliberate change to make now rather than at merge time.
2. **Non-finite numbers are rejected.** TOML explicitly supports `inf` and `nan` as
   float literals. `window_seconds = nan` would silently pass every range comparison
   (`nan > 30` is `False`, `nan >= window` is `False`), so a `math.isfinite` guard was
   added with its own message and tests. This is beyond the ticket's literal list but
   prevents a real silent-corruption hole; `inf` would otherwise be caught by the `> 30`
   rule, `nan` would not.
3. **Errors are raised on the first violation**, like the generic layer, rather than
   collected. The result class holds only `config` and `warnings` per the ticket, so
   collecting errors would need a fourth field the ticket forbids. Unknown-key warnings
   are still gathered before validation, but if a later range check raises they are not
   returned (the exception replaces the result).
4. **`model_path` inside `[engine.whistle]` is an unknown-key warning**, not a valid key.
   It is generic (`transcriber.model_path`, ADR 0009); this is pinned by
   `test_model_path_in_the_engine_block_is_an_unknown_key_warning`.
5. **`KNOWN_KEYS` and the range/default constants are module-level and public** so
   `doctor`, tests, and the future adapter do not re-hardcode `0.25`/`6`/`30`.
   `BLOCK_NAME` is public for the same reason.
6. **No `sum`/aggregate validation**: `chunk_seconds` is checked against `window_seconds`
   after both are parsed, so a wrong-typed `window_seconds` is reported before any
   cross-field check runs.

## Deviations from the ticket, stated loudly

- The ticket's signature and behaviour are implemented as written. The two additions
  beyond its literal text are the dedicated `WhistleConfigError` type (judgement call 1)
  and the non-finite rejection (judgement call 2). Neither changes any required
  behaviour: every listed rule has its own test, and the required
  `window_seconds = 5` / `chunk_seconds = 1` case still returns **no warnings**.
- The ticket says "any string is accepted" for `language`; the tests pin empty, `en`,
  `de`, `es`, `nl`, and a deliberately invented `zz-invented` tag, i.e. no allowed-list
  is hard-coded.

## What the next ticket (`whistle-transcriber-adapter-batch`) must know

- Import `WhistleConfig` (and `WhistleConfigError` if it wants to translate it) from
  `ges2t.transcribers.whistle_config`; construct via `from_mapping`, not directly, if it
  needs validation. `WhistleTranscriber.__init__(self, config: WhistleConfig, ...)`
  receives an already-validated `WhistleConfig`, so it can trust the numbers.
- `config.language` is passed through to the engine unchanged; `""` means auto-detect.
- `config.window_seconds` / `config.chunk_seconds` are ordinary `float`s and are always
  present (defaults applied), so the adapter never sees a `None`.
- `from_mapping` does **not** know the config file path and does not warn about the
  selected engine; selection is the caller's job via
  `config.engine_blocks.get(config.transcriber.engine, {})`.

## Verification (real output)

```
$ uv run pytest
platform linux -- Python 3.14.7, pytest-9.1.1, pluggy-1.6.0
rootdir: /home/matee/Workspace/Personal/ges2t/.worktrees/whistle-engine-config
configfile: pyproject.toml
plugins: anyio-4.15.1
collected 99 items

tests/test_config.py .................................                   [ 33%]
tests/test_smoke.py ...                                                  [ 36%]
tests/test_whistle_config.py ........................................... [ 79%]
....................                                                     [100%]

============================== 99 passed in 0.32s ==============================

$ uv run pytest tests/test_whistle_config.py -q
...............................................................          [100%]
63 passed in 0.16s

$ uv run mypy src/ges2t
Success: no issues found in 5 source files
```

### Test coverage map

| Ticket bullet | Test(s) |
|---|---|
| empty mapping → all defaults | `test_empty_mapping_yields_all_defaults`, `test_default_values_match_the_adr_schema` |
| frozen result class, not a tuple | `test_result_is_a_typed_frozen_dataclass`, `test_config_is_frozen` |
| unknown key → warning and ignored | `test_unknown_key_is_a_warning_and_is_ignored`, `test_unknown_keys_are_reported_in_insertion_order`, `test_model_path_in_the_engine_block_is_an_unknown_key_warning` |
| `language` any string | `test_language_accepts_any_string` |
| `language` wrong type → error | `test_language_wrong_type_is_rejected` |
| `chunk_seconds < 0.25` → error | `test_chunk_below_the_minimum_is_rejected`, `test_chunk_exactly_at_the_minimum_is_accepted` |
| `chunk_seconds >= window_seconds` → error | `test_chunk_at_or_above_window_is_rejected` |
| `window_seconds > 30` → error | `test_window_above_the_engine_limit_is_rejected`, `test_window_exactly_at_the_engine_limit_is_accepted_with_a_warning` |
| `window_seconds > 6` → warning | `test_window_above_the_latency_knee_warns_but_does_not_raise`, `test_window_at_or_below_the_latency_knee_does_not_warn` |
| wrong-typed values | `test_wrong_typed_numbers_are_rejected` |
| `True`/`False` rejected as numbers | `test_booleans_are_rejected_as_numbers` |
| `window_seconds = 5`, `chunk_seconds = 1` → no warnings | `test_toml_integer_is_accepted_as_a_float_with_no_warnings` |
| int-vs-float TOML trap handled | `test_real_toml_parses_the_window_as_int_and_is_still_accepted`, `test_integer_and_float_spellings_produce_the_same_config` |
| error names key, value and range | `test_range_errors_name_the_key_the_value_and_the_allowed_range` |
| non-finite guard (added) | `test_non_finite_numbers_are_rejected` |
