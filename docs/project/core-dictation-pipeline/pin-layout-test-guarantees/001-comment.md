# 001 — pin the three falsified layout-detection test guarantees

**Ticket:** `pin-layout-test-guarantees` · **Branch:** `feat/pin-layout-test-guarantees`

Scope: `tests/test_layout.py` only. **No production code changed** — after all three
falsification probes, `git diff --stat main -- src/` is empty and `git status` lists only
`tests/test_layout.py`, the ticket folder, and `docs/project/README.md` (row 27).

Counts: before `uv run pytest` **515 passed**, `tests/test_layout.py -q` **76 passed**,
`uv run mypy src/ges2t tests` **Success: no issues found in 34 source files**; after
`uv run pytest` **519 passed**, `tests/test_layout.py -q` **80 passed**, `mypy` **Success: no
issues found in 34 source files**. No test was deleted, skipped, or weakened: four were added,
two existing tests were strengthened, and two assertions that could never fail were replaced
with the claim they were standing in for.

## F1 — the non-zero-exit guards were only tested with empty stdout

**Claimed:** `detect_layout` treats a non-zero `busctl` exit as failure.

**Actually checked:** `test_detect_layout_returns_none_on_a_non_zero_exit` and
`test_detect_layout_returns_none_when_the_active_layout_call_fails` supplied `stdout=""` with
`returncode=1`. With empty stdout the parse-failure path returns `None` too, so deleting either
`if ... .returncode != 0: return None` guard left all 515 green (review mutations M2 and M10).

**Fix:** both tests now return the **real, valid body** (`REAL_LAYOUTS_LIST` / `REAL_GET_LAYOUT`)
with `returncode=1`, so only the returncode guard can produce `None`. The `getLayoutsList` test
also supplies a valid `getLayout` result, so a removed guard yields a complete `LayoutInfo`
rather than a `KeyError` from the runner map.

**Falsification (getLayoutsList guard, M10):** deleted
`if listed.returncode != 0: return None` from `detect_layout`:

```
$ uv run pytest "tests/test_layout.py::test_detect_layout_returns_none_on_a_non_zero_exit" -q
>       assert detect_layout(runner=runner) is None
E       AssertionError: assert LayoutInfo(layout='hu', variant='', display_name='Hungarian', index=0) is None
E        +  where LayoutInfo(layout='hu', variant='', display_name='Hungarian', index=0) = detect_layout(runner=<tests.test_layout.RecordingRunner object at 0x7f8a2c4f4ad0>)

tests/test_layout.py:228: AssertionError
1 failed in 0.10s
```

**Falsification (getLayout guard, M2):** deleted `if active.returncode != 0: return None`:

```
$ uv run pytest "tests/test_layout.py::test_detect_layout_returns_none_when_the_active_layout_call_fails" -q
>       assert detect_layout(runner=runner) is None
E       AssertionError: assert LayoutInfo(layout='hu', variant='', display_name='Hungarian', index=0) is None
E        +  where LayoutInfo(layout='hu', variant='', display_name='Hungarian', index=0) = detect_layout(runner=<tests.test_layout.RecordingRunner object at 0x7fc9d3abcad0>)

tests/test_layout.py:248: AssertionError
1 failed in 0.10s
```

Probe reverted with `git checkout -- src/ges2t/injectors/layout.py`; `git status --short src/`
was empty.

## F2 — the live-keymap guard was only tested with empty stdout, on the path the service types through

**Claimed:** `live_keymap_text` rejects a non-zero `xkbcli` exit, and `resolve_layout` therefore
falls through to detection.

**Actually checked:** `test_live_keymap_text_returns_none_on_a_non_zero_exit` used
`_completed("", returncode=1)`; `test_falls_back_to_detected_names_when_the_keymap_is_unavailable`
did the same inside `resolve_layout`. Empty stdout is rejected by the `text.strip() == ""` check
regardless of the exit code, so removing the exit guard left all 515 green (review mutation M3).

**Fix:** the `live_keymap_text` test now carries `REAL_KEYMAP` with `returncode=1`; a new
resolution-path test, `test_a_failing_keymap_exit_with_valid_stdout_falls_through_to_detection`,
does the same through `resolve_layout` and asserts `source is LayoutSource.DETECTED` and
`keymap_text is None`. This is the path that matters: the live keymap is what the service types
through, and a failing dump whose stdout happens to parse must not be used.

**Falsification (M3):** deleted `if completed.returncode != 0: return None` from
`live_keymap_text`:

```
$ uv run pytest "tests/test_layout.py::test_live_keymap_text_returns_none_on_a_non_zero_exit" \
    "tests/test_layout.py::test_a_failing_keymap_exit_with_valid_stdout_falls_through_to_detection" -q
FF                                                                       [100%]
>       assert live_keymap_text(runner=runner) is None
E       assert 'xkb_keymap {\n    xkb_keycodes "evdev_aliases(qwertz)" {\n        minimum = 8;\n' is None
...
>       assert resolved.source is LayoutSource.DETECTED
E       AssertionError: assert <LayoutSource.LIVE_KEYMAP: 'live keymap'> is <LayoutSource.DETECTED: 'detected'>

tests/test_layout.py:294: AssertionError
tests/test_layout.py:401: AssertionError
2 failed in 0.12s
```

Probe reverted; `git status --short src/` empty.

## F3 — the 2 s timeout was never enforced by a test

**Claimed:** both detectors bound their subprocess with `LAYOUT_COMMAND_TIMEOUT_SECONDS`.

**Actually checked:** `test_live_keymap_text_uses_a_bounded_timeout` and
`test_detect_layout_passes_a_bounded_timeout_to_every_call` only inspected the value handed to
the **injected** runner. `_run_command` — the only place `subprocess.run` is called — was never
executed by a test, so `timeout=None` inside it left all 515 green (review mutation M4). The
"nothing can hang" requirement was therefore a claim, not a guarantee.

**Fix:** two tests exercise the real runner with a hermetic child that is the project's own
interpreter (no `xkbcli`/`busctl`, no session, nothing from `PATH`):

- `test_run_command_enforces_its_timeout_without_ignoring_it` calls
  `_run_command(<slow child>, 0.2)` and requires `subprocess.TimeoutExpired`. It is wrapped in a
  local `call_with_timeout` (worker daemon thread + `join(timeout)`, reused in shape from
  `tests/test_state_machine.py`), which raises `AssertionError` after 2 s if the bound is gone
  instead of hanging the suite on the 5 s child. A 0.2 s *injected* timeout proves the same thing
  the 2 s constant does without sleeping for it.
- `test_run_command_returns_the_child_result_when_it_finishes_in_time` proves a fast child is
  returned normally rather than killed.
- `test_the_documented_two_second_bound_is_pinned` pins
  `LAYOUT_COMMAND_TIMEOUT_SECONDS == 2.0`. Without it the existing
  `== LAYOUT_COMMAND_TIMEOUT_SECONDS` assertions are self-referential and would follow the
  constant anywhere.

**Falsification (M4):** set `timeout=None` in `_run_command`:

```
$ uv run pytest "tests/test_layout.py::test_run_command_enforces_its_timeout_without_ignoring_it" \
    "tests/test_layout.py::test_run_command_returns_the_child_result_when_it_finishes_in_time" \
    "tests/test_layout.py::test_live_keymap_text_uses_a_bounded_timeout" \
    "tests/test_layout.py::test_detect_layout_passes_a_bounded_timeout_to_every_call" -q
>           raise AssertionError(f"call did not return within {timeout:.1f}s")
E           AssertionError: call did not return within 2.0s

tests/test_layout.py:132: AssertionError
1 failed, 3 passed in 2.16s
```

The three listeners of the old value-passing assertions (2 old + the fast-child test) stayed
green in the same run — exactly the gap. The new test failed **in bounded time** (2.16 s
wall clock for the whole selection, 2.0 s of it the helper's bound). Probe reverted;
`git status --short src/` empty.

## Other assertions in `tests/test_layout.py` that could not fail

Found while auditing the file for this ticket (the failure class ticket 26 found in
`tests/test_cli.py`). Two were fixed as part of the strengthening; the rest are recorded, not
fixed, as the ticket asks.

**Fixed:**

- `test_blank_or_auto_configuration_means_detect_not_guess_us` asserted
  `"us" not in (resolved.layout or "")`. On the `LIVE_KEYMAP` branch `resolved.layout` is always
  `None`, so this compared `"us" not in ""`, which is always true — it could not fail. Replaced
  with `resolved.layout is None` and `resolved.keymap_text == REAL_KEYMAP` (the actual claim:
  no layout name was invented and the real keymap came back).
- `test_raises_when_no_source_can_determine_the_layout` asserted `"layout" in message`. The
  message's fixed prefix is "could not determine the keyboard layout", so this could not fail.
  Replaced with `"Set an explicit layout" in message` (the actionable fix hint) alongside the
  existing `"xkbcli dump-keymap-wayland"` / `"org.kde.keyboard"` pins.

**Recorded, not fixed:**

- **The timeout-value assertions were self-referential.**
  `assert runner.timeouts == [LAYOUT_COMMAND_TIMEOUT_SECONDS, ...]` compares against the same
  constant the code passes, so it cannot catch a *changed* constant — only a hardcoded different
  value. Closed for the literal by `test_the_documented_two_second_bound_is_pinned`.
- **`assert 0 < LAYOUT_COMMAND_TIMEOUT_SECONDS < 60`** is a near-tautology: true for every value
  a sane edit would pick. It only fails for an absurd bound. The literal pin above now covers
  the real guarantee.
- **Block-name assertions are self-referential.**
  `assert YDOTOOL_BLOCK_NAME in result.warnings[0]` and the `CLIPBOARD_BLOCK_NAME` equivalent
  build the expectation from the same constant the production warning is formatted with, so
  renaming the constant would rename both sides. The offending key name is pinned separately
  (`"'speed'"` / `"'typo'"`), so the warning *content* is still covered; only the block prefix
  is not.
- **Message-substring assertions that are structurally guaranteed.**
  `test_ydotool_config_invalid_key_delay_is_rejected` asserts
  `"key_delay_ms" in str(excinfo.value)`; given the raise happened, that key is hardcoded into
  every message the helper can produce. The `pytest.raises(InjectionConfigError)` is the real
  check; the substring adds nothing. Same shape for
  `test_ydotool_config_wrong_typed_strings_are_rejected`'s `"expected a string"` (structural, but
  it does at least pin the wording, unlike the block-name prefixes).
- **No self-iterating list test was found.** Unlike ticket 26's `FORBIDDEN_MODULES` /
  `SUBCOMMANDS`, no test in this file iterates a module-level list and asserts against that same
  list. Every `@pytest.mark.parametrize` list is consumed as inputs only.

## Verification

```
$ uv run pytest
519 passed in 1.14s
$ uv run pytest tests/test_layout.py -q
80 passed in 0.38s
$ uv run mypy src/ges2t tests
Success: no issues found in 34 source files
$ git status --short
 M tests/test_layout.py
$ git diff --stat main -- src/
(empty)
```

`src/ges2t/injectors/layout.py` is byte-identical to `main`; all three probes were reverted with
`git checkout -- src/ges2t/injectors/layout.py` before the final run.
