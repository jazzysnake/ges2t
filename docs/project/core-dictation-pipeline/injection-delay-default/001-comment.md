# 001 — Implementation comment: set the injection key-delay default from the measurement

- **Ticket:** [ticket.md](ticket.md)
- **Branch:** `feat/injection-delay-default`
- **Worktree:** `.worktrees/injection-delay-default`
- **Status:** in review

## The change

`DEFAULT_KEY_DELAY_MS` in `src/ges2t/injectors/layout.py` went from the provisional
`12` (ticket 24, `ydotool key`'s own default) to the measured `0`. This is a
**value change only**: the constant keeps its name, type and every consumer, and
the emission mechanism in `ydotool.py` was not touched (ticket 23 is merged and
done).

Files changed:

- `src/ges2t/injectors/layout.py` — the constant and its comment.
- `tests/test_layout.py` — the defaults test now pins `0` (with a comment saying
  the value is measured) and no longer asserts `12`.
- `tests/test_pipeline.py` — the absent-block `build_injector` test pins `0`.
- `tests/test_ydotool_emitter.py` — `test_default_key_delay_is_twelve_ms` was
  renamed to `test_default_key_delay_is_the_measured_zero_ms`, and two incidental
  argv assertions that used the default switched `"12"` → `"0"`.

## Measurement basis (ticket 23, `../batched-key-emission/001-comment.md`)

Ticket 23 replaced `ydotool type` with one batched `ydotool key` and measured a
125-character phrase, byte-compared against the capture:

| delay ms | wall ms | ms/char | byte-perfect | ADR 0001 budget (< 1 s) |
|---:|---:|---:|:--:|:--:|
| 12 | 3100.7 | 24.81 | yes | **no** |
| 6 | 1595.4 | 12.76 | yes | **no** |
| **0** | **47.9** | **0.38** | **yes** | **yes** |

The cost is linear — `delay × 2 × 126` events, because `ydotool key` sleeps once
per event, down *and* up — so every non-zero delay reintroduces 252 sleeps. With
the default at 12 the product shipped **over its own budget** (3.1 s per phrase)
despite the implementation being able to do 47.9 ms.

**Chosen value: `0`.** Only `0`, `6` and `12` were measured, and `6`/`12` are both
over budget, so `0` is the only **measured** in-budget value. Picking, say,
`2 ms` would ship an extrapolation from the linear model — arithmetic, not
evidence. The knob stays a **documented user override**, and raising
`key_delay_ms` is the intended fix if a specific application ever drops
characters; that reasoning is recorded in the constant's comment.

**Caveat.** The measurement's capture target was a **pty** (`head -c`), not a real
editor, so `0` is not proven universally drop-proof. That is exactly why the knob
stays documented and configurable; it is not a reason to guess a margin.

## User override and absent block

Nothing about the override changed. `[injection.ydotool] key_delay_ms` is still
read by `YdotoolConfig.from_mapping` / `_read_key_delay_ms`, still rejects
negatives and wrong types, and `build_injector` still uses
`config.injection_blocks.get("ydotool", {})`, so an absent block is "all defaults"
and never a `KeyError`.

Confirmed with a real TOML parse (a file with no `[injection.ydotool]` block):

```
raw injection_blocks: {'clipboard': {'paste_keys': ['ctrl+v']}}
warnings: []
has 'ydotool' block: False
injector: YdotoolInjector
key_delay_ms: 0 | trailing_space: True
source layout/variant: auto ''
```

## Real verify output

```
$ uv run pytest -q
567 passed in 2.78s

$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 37 source files
```

Baseline on `main`: **567 passed, 37 files**. After: **567 passed, 37 files** —
no test deleted, skipped or weakened (the count is unchanged because the existing
pins were updated, not removed, and one test was renamed).

## Falsification evidence

The default is pinned to the literal `0`, so changing the constant must fail the
tests. `DEFAULT_KEY_DELAY_MS: int = 0` was temporarily mutated to `5`:

```
$ uv run pytest -q tests/test_layout.py tests/test_pipeline.py tests/test_ydotool_emitter.py
FAILED tests/test_layout.py::test_ydotool_config_defaults_match_the_adr_shape
FAILED tests/test_pipeline.py::test_build_injector_defaults_when_the_ydotool_block_is_absent
FAILED tests/test_ydotool_emitter.py::test_real_us_keymap_passes_linux_keycode_through_without_offset
FAILED tests/test_ydotool_emitter.py::test_default_key_delay_is_the_measured_zero_ms
FAILED tests/test_ydotool_emitter.py::test_unreachable_characters_are_skipped_with_a_warning
5 failed, 140 passed in 0.89s
```

The constant was then restored (`git checkout -- src/ges2t/injectors/layout.py`)
and the same selection went green (`145 passed in 0.78s`).

## Anything observed but not explained

Nothing anomalous. No re-measurement and no keystrokes were emitted for this
ticket: it works entirely from the recorded numbers in ticket 23's
`001-comment.md`, per the ticket's hard constraints. No window, service, network,
clipboard or `sudo` was involved.

## Note for the orchestrator

- The measurement scripts under `docs/` were not moved and are not packaged.
- Historical records (ticket 23's `001-comment.md` and `002-review.md`, ticket
  24's `001-comment.md`) still quote the old `12` default; they are point-in-time
  records and were deliberately left untouched. No shipped prose or config example
  outside those records stated the default.
- User documentation for `key_delay_ms` is still owned by ticket 19
  (`../../live-dictation-ux/user-documentation-and-setup-guide/ticket.md`); none
  was written here.
