# 002 — review: pin the layout detection test guarantees

**Ticket:** `pin-layout-test-guarantees` · **Branch:** `feat/pin-layout-test-guarantees` · **HEAD:** `911db7b`
**Reviewer:** adversarial re-verification. Every claim in `001-comment.md` was treated as a claim and
independently reproduced on this worktree. All probes were applied to `src/ges2t/injectors/layout.py`
and reverted with `git checkout -- src/ges2t/injectors/layout.py`; the tree is clean and `layout.py` is
byte-identical to `main` after every probe.

## Verdict: **pass** — no blocking findings.

The hard gate holds (`src/` diff empty). All three falsifications reproduce exactly as reported, the new
tests are genuinely bounded, both "repaired" assertions are genuinely falsifiable, the "recorded, not
fixed" list is honest, no test was deleted/skipped/weakened, the counts match, and `mypy` is green. Two
non-blocking precision notes are recorded at the end.

---

## Hard gate — `src/` must be empty

```
$ git diff main...HEAD -- src/
(empty)
$ git diff main...HEAD --stat
 docs/project/README.md                             |   2 +-
 .../pin-layout-test-guarantees/001-comment.md      | 199 +++++++++++++++++++++
 .../pin-layout-test-guarantees/ticket.md           |   2 +-
 tests/test_layout.py                               | 116 +++++++++++-
 4 files changed, 310 insertions(+), 9 deletions(-)
```

`git status` is clean, and `git diff --stat main -- src/ges2t/injectors/layout.py` is empty, so the
implementation is untouched. No F1–F3 is a production defect; F1–F3 stay test-strength gaps, as the
ticket framed them.

## F1 — the non-zero-exit guards (both detectors), reproduced

**Mutation F1a — delete `if listed.returncode != 0: return None` from `detect_layout`:**

```
$ uv run pytest "tests/test_layout.py::test_detect_layout_returns_none_on_a_non_zero_exit" -q
>       assert detect_layout(runner=runner) is None
E       AssertionError: assert LayoutInfo(layout='hu', variant='', display_name='Hungarian', index=0) is None
tests/test_layout.py:228: AssertionError
1 failed in 0.10s
```

**Mutation F1b — delete `if active.returncode != 0: return None` from `detect_layout`:**

```
$ uv run pytest "tests/test_layout.py::test_detect_layout_returns_none_when_the_active_layout_call_fails" -q
>       assert detect_layout(runner=runner) is None
E       AssertionError: assert LayoutInfo(layout='hu', variant='', display_name='Hungarian', index=0) is None
tests/test_layout.py:248: AssertionError
1 failed in 0.10s
```

Both reproduce. Each test now feeds valid `busctl` bodies (`REAL_LAYOUTS_LIST`, `REAL_GET_LAYOUT`) with
`returncode=1`, so the parse path would succeed and only the exit guard can produce `None`.

## F2 — the live-keymap non-zero-exit guard, reproduced

**Mutation — delete `if completed.returncode != 0: return None` from `live_keymap_text`:**

```
$ uv run pytest \
    "tests/test_layout.py::test_live_keymap_text_returns_none_on_a_non_zero_exit" \
    "tests/test_layout.py::test_a_failing_keymap_exit_with_valid_stdout_falls_through_to_detection" -q
>       assert live_keymap_text(runner=runner) is None
E       assert 'xkb_keymap {\n    xkb_keycodes "evdev_aliases(qwertz)" {\n        minimum = 8;\n' is None
...
>       assert resolved.source is LayoutSource.DETECTED
E       AssertionError: assert <LayoutSource.LIVE_KEYMAP: 'live keymap'> is <LayoutSource.DETECTED: 'detected'>
tests/test_layout.py:401: AssertionError
2 failed in 0.12s
```

Both the isolated test and the new resolution-path test fail. F2 is pinned on the path the service
actually types through, not only in isolation.

## F3 — the real timeout, reproduced (both halves)

**Mutation — set `timeout=None` inside `_run_command`:**

```
$ uv run pytest \
    "tests/test_layout.py::test_run_command_enforces_its_timeout_without_ignoring_it" \
    "tests/test_layout.py::test_run_command_returns_the_child_result_when_it_finishes_in_time" \
    "tests/test_layout.py::test_live_keymap_text_uses_a_bounded_timeout" \
    "tests/test_layout.py::test_detect_layout_passes_a_bounded_timeout_to_every_call" -q
>           raise AssertionError(f"call did not return within {timeout:.1f}s")
E           AssertionError: call did not return within 2.0s
tests/test_layout.py:132: AssertionError
1 failed, 3 passed in 2.14s
```

This reproduces the reported contrast exactly. The single failure is the new enforcement test; the three
tests that stayed green are the two **old value-passing timeout assertions**
(`test_live_keymap_text_uses_a_bounded_timeout`, `test_detect_layout_passes_a_bounded_timeout_to_every_call`)
plus the new fast-child test. That is the evidence that the old timeout tests could not fail: with
`subprocess.run(timeout=None)` they still pass, because they only inspect the float handed to the
injected runner. The new test fails and fails loudly.

(`test_the_documented_two_second_bound_is_pinned` also stays green under `timeout=None` by design — it
targets constant drift, verified separately below.)

## Are the new tests bounded? Yes

`test_run_command_enforces_its_timeout_without_ignoring_it` wraps the real call in the local
`call_with_timeout(...)` helper (daemon worker thread + `thread.join(timeout)`; raises
`AssertionError` if the thread is still alive). The injected bound is **0.2 s**, the child sleeps **5 s**,
and the helper's own bound is **2.0 s**. So:

- correct code: `subprocess.run(timeout=0.2)` raises `TimeoutExpired` in ~0.20 s (measured);
- regression (`timeout=None`): the helper raises `AssertionError` at 2.0 s instead of blocking on the
  5 s child. Re-run under the mutation: `1 failed, 3 passed in 2.14s`.

The whole `tests/test_layout.py` still runs in **0.39 s** (`--durations=5` shows the 0.20 s enforcement
test as the only measurable one), so a regression cannot hang the suite. The other new real-runner test
(`..._returns_the_child_result_when_it_finishes_in_time`) uses an immediately-exiting child, so leaving it
unwrapped cannot hang even under the regression.

## The two "repaired" assertions are genuinely falsifiable

**1. `"us" not in (resolved.layout or "")` → `resolved.layout is None` + `resolved.keymap_text == REAL_KEYMAP`**

The old expression on the `LIVE_KEYMAP` path compared `"us" not in ""` (the branch hardcodes
`layout=None`), so it was constant-true. I mutated the branch to return `keymap_text=None` and ran both
the new test and the old expression:

```
$ uv run pytest "tests/test_layout.py::test_blank_or_auto_configuration_means_detect_not_guess_us" -q
>       assert resolved.keymap_text == REAL_KEYMAP
E       assert None == 'xkb_keymap {\n    xkb_keycodes "evdev_aliases(qwertz)" {\n        minimum = 8;\n'
tests/test_layout.py:431: AssertionError
3 failed in 0.14s

layout= None keymap_text= None
OLD assertion  "us" not in (resolved.layout or "")  -> True
NEW assertion  resolved.layout is None              -> True
NEW assertion  resolved.keymap_text == REAL_KEYMAP  -> False
```

So on the exact regression the old assertion misses, the new one fails. I also mutated the branch to
invent `layout="us"`, which fails `resolved.layout is None` (`3 failed`). Both new assertions are
falsifiable; the old one was not covering the keymap at all.

**2. `"layout" in message` → `"Set an explicit layout" in message` + `"'auto'" in message`**

The old assertion was guaranteed by the fixed prefix "could not determine the keyboard layout". I
replaced the message with one that keeps the prefix, `xkbcli dump-keymap-wayland`, and
`org.kde.keyboard` but drops the hint and the sentinel:

```
$ uv run pytest "tests/test_layout.py::test_raises_when_no_source_can_determine_the_layout" -q
>       assert "Set an explicit layout" in message
E       assert 'Set an explicit layout' in "could not determine the keyboard layout: xkbcli dump-keymap-wayland failed, and KDE's org.kde.keyboard layout service did not report one"
tests/test_layout.py:447: AssertionError
1 failed in 0.10s
```

I then kept the hint but dropped `'auto'`, and the last assertion failed:

```
>       assert "'auto'" in message
E       assert "'auto'" in '... Set an explicit layout.'
tests/test_layout.py:448: AssertionError
```

The old `"layout" in message` would have passed in both cases (the prefix contains "layout"). Both new
assertions are falsifiable and pin the actionable content.

## The "recorded, not fixed" list is honest (spot-checked)

**Self-referential `runner.timeouts == [LAYOUT_COMMAND_TIMEOUT_SECONDS, ...]` — confirmed.** I changed the
constant to `30.0`:

```
$ uv run pytest ... test_detect_layout_passes_a_bounded_timeout_to_every_call \
                   test_live_keymap_text_uses_a_bounded_timeout \
                   test_the_documented_two_second_bound_is_pinned -q
>       assert LAYOUT_COMMAND_TIMEOUT_SECONDS == 2.0
E       assert 30.0 == 2.0
tests/test_layout.py:273: AssertionError
1 failed, 2 passed in 0.10s
```

Both value-passing assertions followed the constant anywhere. The implementer's claim is accurate, and
`test_the_documented_two_second_bound_is_pinned` is what actually closes the constant-drift hole.

**`0 < LAYOUT_COMMAND_TIMEOUT_SECONDS < 60` is a near-tautology — confirmed.** With the constant at 30.0
the assertion still passes; it is true for any sane edit (`0 < 0.5 < 60` → True, `0 < 30.0 < 60` → True)
and only fails an absurd bound (`0 < 120 < 60` → False). Accurate.

**Block-name substring checks are self-referential — confirmed.** I renamed
`YDOTOOL_BLOCK_NAME` → `"foo.bar"` and `CLIPBOARD_BLOCK_NAME` → `"baz.qux"`. The warning and the
expectation are both built from the same constant, so everything stayed green:

```
$ uv run pytest test_ydotool_config_unknown_key_warns_and_is_ignored \
                   test_clipboard_config_unknown_key_warns \
                   test_ydotool_config_wrong_typed_strings_are_rejected -q
10 passed in 0.07s
```

**`"key_delay_ms" in str(excinfo.value)` — honest, with one precision nuance.** I dropped the key from
one rejection message and the assertion fired (`2 failed, 2 passed`), so it is not *logically*
unfalsifiable — it pins the wording. The implementer's actual wording ("given the raise happened, that
key is hardcoded into every message the helper can produce … the substring adds nothing") is correct:
it cannot catch a validation-logic error, only a message change. Same for `"expected a string"`, which the
implementer already distinguishes as the one that "at least pins the wording". Fair.

I found no self-iterating list test in the file (`grep -nE '^\s*for .* in '` → none), matching the
implementer's note.

**Judgement:** the list is honest. Each weakness is a real reduction in what the assertion can detect,
and the tightenings that were made (literal constant pin; `resolved.layout is None` + `keymap_text`;
the actionable message text) cover the gaps that matter.

## Counts, scope, and no deletion / skipping / weakening

```
HEAD:  $ uv run pytest -q                       -> 519 passed in 1.15s
       $ uv run pytest tests/test_layout.py -q  -> 80 passed in 0.38s
base 33413b3 (branch parent): full suite -> 515 passed in 2.86s; test_layout.py -> 76 passed in 0.12s
```

All four claimed numbers reproduce (519/515 and 80/76), a **+4** delta in both, matching four added test
functions. `diff` of test-function names shows **four added, none removed**; the only removed lines in
`tests/test_layout.py` are two import lines and five `_completed("", returncode=1)` / vacuous-assertion
lines, every one replaced by a strictly stronger form. No `pytest.mark.skip`/`xfail`/`skipif` was added.

Accurate diff figures: `tests/test_layout.py` is **+109 / −7**. The task brief's "+116 / −9" conflates the
file's total churn (`--stat` shows 116 changed lines) with the branch-wide deletion count (9 across all
four files, 2 of them in docs). Nothing substantive turns on this.

Scope is exactly `tests/test_layout.py` + the ticket folder + its board row:

```
docs/project/README.md                                                        (row 27: to do -> in review, branch named)
docs/project/core-dictation-pipeline/pin-layout-test-guarantees/001-comment.md
docs/project/core-dictation-pipeline/pin-layout-test-guarantees/ticket.md    (status: to do -> in review)
tests/test_layout.py
```

## Hermeticity, ADR 0019 and `mypy`

- No real `xkbcli`/`busctl` is executed: those strings appear only as `RecordingRunner` outputs, expected
  argv, or message fixtures. The only real processes are the two new tests' children, which are the
  project's own `sys.executable` running `time.sleep(5)` / `raise SystemExit(0)` — no `PATH` lookup, no
  `xkbcli`/`busctl`, no D-Bus, no Wayland session.
- ADR 0019: the new helper and tests are fully annotated (`call: Callable[[], _T]`, `*, timeout: float`,
  `-> _T`, inner `run() -> None`, test functions `-> None`), `_SLOW_CHILD_ARGV: tuple[str, ...]` is a
  homogeneous sequence, and `functools.partial` is used instead of a positional tuple.
- `rm -rf .mypy_cache` then `uv run mypy src/ges2t tests` → `Success: no issues found in 34 source files`
  (re-run after all probes with a fresh cache).

## Merge note for the orchestrator

`main` has advanced past the branch's base (`33413b3` → `46c7122`) with tickets 22 and 23. Since the
merge-base, `main` changed `src/ges2t/injectors/keymap.py` and `tests/test_keymap.py` and docs — **not**
`layout.py` or `test_layout.py`. There is no file overlap on this ticket's subject, so the merge should
be conflict-free there; the only likely conflict is the `docs/project/README.md` board row (ticket 23
also edited the board). Rebasing onto current `main` before merging is the safe order.

## Non-blocking observations

1. **Module docstring is now slightly inaccurate.** `tests/test_layout.py` still says "Every test injects
   a runner (or calls the pure parsers directly), so no test starts `xkbcli` …". The two new `_run_command`
   tests do start a real (interpreter-only) subprocess and do not inject a runner. The literal safety
   claims — no `xkbcli`, no D-Bus, no live session — remain true; only the "every test injects a runner"
   clause is stale. Worth a one-line docstring update, not a blocker.
2. **Precision, not a defect:** the `"key_delay_ms"` / `"expected a string"` substrings are falsifiable by
   a message change (see above), so "could not fail" is stronger than warranted if read literally. The
   implementer's recorded reasoning does not overclaim; it says the substring adds nothing beyond the
   `pytest.raises`, which is right.

## Could not verify (and why)

- No real KDE multi-layout session or live `xkbcli`/`busctl` call was made; all detection tests keep
  using the injected runner, as required, and the real-session behaviour is out of scope for a
  test-hardening ticket (review 002 already verified it by hand).
- The exact "before" total of 515 was measured on a throwaway worktree at the branch's parent `33413b3`
  (515 full / 76 layout), then removed; it is not the same as today's `main` tip, which now includes
  tickets 22 and 23.
