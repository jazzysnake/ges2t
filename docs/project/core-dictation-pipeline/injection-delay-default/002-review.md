# 002 — Review: set the injection key-delay default from the measurement

- **Ticket:** [ticket.md](ticket.md)
- **Branch:** `feat/injection-delay-default`
- **Worktree:** `.worktrees/injection-delay-default`
- **Review base:** `main` (`git diff main...feat/injection-delay-default`, three dots)
- **HEAD:** `56138c2` (docs); code `d52fe33`
- **Status:** review complete

## Verdict

**Approve.** The default is genuinely consumed end to end, the pin is falsifiable
(5 tests fail on mutation of the constant), the change is value-only, the scope is
exactly the four source/test files plus the ticket's own docs and one board row,
and no historical record was rewritten. The one honest limitation is the pty
caveat, which is stated plainly and correctly.

## 1. The default is actually used — end-to-end argv evidence

Driven through the **real** config path (`load_config` on a real TOML file with no
`[injection.ydotool]` block → `pipeline.build_injector` → `YdotoolInjector`), with
the delivery seam replaced by a fake runner and the mapping seam by a canned
sequence:

```
injection_blocks: {}
has ydotool block: False
injector.key_delay_ms: 0
argv: ('ydotool', 'key', '-d', '0', '30:1', '30:0', '57:1', '57:0')
"0" in argv: True
"12" in argv: False
E2E DEFAULT CONSUMED: OK
```

The argv literally contains `-d 0` and does **not** contain `12`. The constant is
not inert. `build_injector` reads it via
`settings.config.key_delay_ms` (`src/ges2t/pipeline.py:223`) into the emitter,
which renders `str(self.key_delay_ms)` as the `-d` token
(`src/ges2t/injectors/ydotool.py:445-461`).

## 2. Falsification reproduced

`DEFAULT_KEY_DELAY_MS: int = 0` was mutated to `5`, caches cleared
(`.mypy_cache`, all `__pycache__`), and the three touched test files run:

```
FAILED tests/test_layout.py::test_ydotool_config_defaults_match_the_adr_shape
FAILED tests/test_pipeline.py::test_build_injector_defaults_when_the_ydotool_block_is_absent
FAILED tests/test_ydotool_emitter.py::test_real_us_keymap_passes_linux_keycode_through_without_offset
FAILED tests/test_ydotool_emitter.py::test_default_key_delay_is_the_measured_zero_ms
FAILED tests/test_ydotool_emitter.py::test_unreachable_characters_are_skipped_with_a_warning
5 failed, 140 passed
```

This matches the implementer's `001-comment.md` exactly. The tests assert the
**literal** `0` (`injector.key_delay_ms == 0`, `argv ... "-d", "0"`), not
`X == X`; the layout test's `DEFAULT_KEY_DELAY_MS == 0` chain also anchors the
constant to a literal. Restored with `git checkout --` and caches cleared again:

```
145 passed in 1.03s
$ git status --porcelain
(empty)
```

The `5` mutation was verified to have taken effect before the run, so the failure
is real and not a stale-bytecode artifact.

## 3. Value-only, absent-block, override, scope

- **Value-only.** The `layout.py` diff changes the comment and
  `DEFAULT_KEY_DELAY_MS: int = 12` → `= 0`. Name, type, `__all__` entry, position
  and every consumer are unchanged. `key_delay_ms`'s type/meaning
  (`YdotoolKeySequenceSource` → `ydotool key -d`) is untouched.
- **Absent block = all defaults.** `build_injector` uses
  `config.injection_blocks.get("ydotool", {})` and `_read_key_delay_ms` returns
  `DEFAULT_KEY_DELAY_MS` when the key is absent, so there is no `KeyError`. Real
  TOML parse with no `[injection.ydotool]` block → `0`.
- **Explicit override still works.** Real TOML parse with
  `[injection.ydotool] key_delay_ms = 7` → `7`; `key_delay_ms = 0` → `0`.
  The remaining `12` literals in `tests/test_layout.py` are the explicit-override
  case (line 540/544), the invalid-float case (`12.5`), and the integral-float
  parse case (`12.0` → `12`) — none is a default statement.
- **`ydotool.py` untouched.** `git diff main...feat/injection-delay-default --
  src/ges2t/injectors/ydotool.py` is empty.
- **Scope.** Changed files are exactly `src/ges2t/injectors/layout.py`, the three
  test files, the ticket's `ticket.md`/`001-comment.md`, and the one board row in
  `docs/project/README.md` (status `to do` → `in review`, branch added).

## 4. Comment honesty and the pty caveat

Every claim in the constant's comment and in `001-comment.md` checks out against
ticket 23's measurement record:

- **Measured basis.** `0` → 47.9 ms, byte-perfect, 125-char phrase; `12` → 3100.7
  ms (3.1 s); `6` → 1595.4 ms (1.6 s). Accurate.
- **Why no margin.** Only 0/6/12 were measured, and 6/12 both miss ADR 0001's
  sub-1-second budget, so a 2 ms value would be an extrapolation from the linear
  `delay × 2 × 126` model, not evidence. Accurate and correctly framed.
- **pty caveat.** Ticket 23's capture target was a scratch `konsole` running a
  foreground `head -c` — i.e. a **pty**, not a real editor. The constant's comment
  says so ("The measured capture target was a pty, not a real editor") and
  `001-comment.md` is blunter ("`0` is not proven universally drop-proof"). The
  caveat is stated plainly enough that `0` cannot reasonably be read as
  "verified drop-proof in every application".
- **Raising `key_delay_ms` is the fix if an app drops characters.** The comment
  says exactly this and notes the knob stays a documented override. Accurate.
- **Citation.** The constant cites
  `docs/project/core-dictation-pipeline/batched-key-emission/001-comment.md`, which
  exists. Accurate.

No prose elsewhere states the old `12` as the live default. The only remaining
`12` statements live in point-in-time records (ticket 23/24 `001-comment.md`,
ticket 23 `002-review.md`) and the measurement artifact, all correctly left alone.

## 5. Mechanicals

```
$ uv run pytest -q
567 passed in 2.72s

$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 37 source files
```

Baseline and after are both 567 tests / 37 files — no test deleted, skipped or
weakened; one test was renamed (`..._is_twelve_ms` →
`..._is_the_measured_zero_ms`) with its assertions updated, not removed.

ADR 0019 in the changed test code: the renamed test keeps its `-> None`
annotation; no positional tuples were introduced (the tuples are compared expected
argv values, not constructed data records).

Historical records confirmed untouched (`git diff` vs `main` empty):
`batched-key-emission/001-comment.md`, `batched-key-emission/002-review.md`,
`layout-detection-and-config/001-comment.md`. Saying "12" was correct when
written; no penalty.

## What I could not verify

- **The measurement itself.** No keystrokes, `ydotool`, pty, window or GUI were
  used; the 47.9 / 1595.4 / 3100.7 ms numbers and byte-perfection are taken from
  ticket 23's record. I did **not** re-measure, by design and by constraint.
- **Real-editor drop behaviour.** Untouched: the pty caveat means `0` is not
  proven drop-proof in a real application; that is inherent to the measurement,
  not a defect in this ticket.
- **`uv run pytest` on `main`.** I confirmed 567/37 on the branch, as required; I
  did not check out `main` to re-derive the baseline, so the "baseline 567" figure
  is accepted from `001-comment.md` plus the branch result.
