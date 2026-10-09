# 001 — pin the two falsified CLI test guarantees

**Ticket:** `pin-cli-test-guarantees` · **Branch:** `feat/pin-cli-test-guarantees`

Scope: `tests/test_cli.py` only. **No production code changed** — `git diff -- src/ges2t/cli.py`
is empty after both probes were reverted. The underlying behaviour was already correct, so this
is purely closing test-strength gaps, the same class as ticket 20's `mypy`-scope gap.

Counts: before `uv run pytest` **407 passed**, `tests/test_cli.py -q` **60 passed**; after
`uv run pytest` **409 passed**, `tests/test_cli.py -q` **62 passed**. No test was deleted,
skipped, or weakened: two were added, and three path assertions were strengthened from
constants to literals.

## F1 — the laziness test only caught runtime imports

**Claimed:** `ges2t toggle` imports neither the engine nor a GUI toolkit (ADR 0016).

**Actually checked:** the test deleted the five forbidden names from `sys.modules` and then
called `main(["toggle"])`. `tests/test_cli.py` imports `cli` at collection time, so a top-level
`import pystray` had already run and been deleted; Python does not re-execute a module-level
import statement, so only an import *inside a handler* was visible.

**Fix:** added `test_importing_the_cli_does_not_load_the_engine_gui_or_later_tickets`, which
runs `import ges2t.cli` in a fresh subprocess and inspects `sys.modules` in that interpreter.
The existing runtime test is kept beside it, because the subprocess check cannot see a runtime
import. The helper costs ~100 ms and touches nothing external.

**Falsification:** added a top-level `import pystray` to `src/ges2t/cli.py`:

```
$ uv run pytest "tests/test_cli.py::test_importing_the_cli_does_not_load_the_engine_gui_or_later_tickets" -q
F                                                                        [100%]
=================================== FAILURES ===================================
_____ test_importing_the_cli_does_not_load_the_engine_gui_or_later_tickets _____

    def test_importing_the_cli_does_not_load_the_engine_gui_or_later_tickets() -> None:
>       assert _forbidden_modules_after_importing_cli_in_a_subprocess() == []
E       AssertionError: assert ['gi', 'pystray'] == []
E
E         Left contains 2 more items, first extra item: 'gi'
E         Use -v to get more diff

tests/test_cli.py:432: AssertionError
=========================== short test summary info ============================
FAILED tests/test_cli.py::test_importing_the_cli_does_not_load_the_engine_gui_or_later_tickets
1 failed, 2 warnings in 0.68s
```

(`pystray` pulls in `gi` on this machine, so both are reported.) Probe reverted with
`git checkout -- src/ges2t/cli.py`; `git status` shows no `cli.py` change.

## F2 — the exit-code tests compared against their own constants

**Claimed:** the service-absent path returns exactly `2` and the engine/injection-failure path
exactly `3` (ADR 0016 documents both as user-facing API for compositor bindings and scripts).

**Actually checked:** `main([...]) == EXIT_SERVICE_NOT_RUNNING` / `== EXIT_ENGINE_FAILURE`.
Renumbering the constant renumbered the expectation with it, so the literal was never pinned.

**Fix:**

- the two service-absent assertions (`toggle`, `status`) now assert `== 2`, and the
  engine-failure assertion now asserts `== 3`, each with a comment that ADR 0016 documents the
  value as user-facing API;
- added `test_documented_exit_code_literals_are_pinned`, which pins `EXIT_SUCCESS == 0`,
  `EXIT_FAILURE == 1`, `EXIT_SERVICE_NOT_RUNNING == 2`, `EXIT_ENGINE_FAILURE == 3` and
  `EXIT_INTERRUPTED == 130` independently of the call sites.

**Falsification:** set `EXIT_SERVICE_NOT_RUNNING = 7` in `src/ges2t/cli.py`:

```
$ uv run pytest "tests/test_cli.py::test_documented_exit_code_literals_are_pinned" -q
F                                                                        [100%]
=================================== FAILURES ===================================
________________ test_documented_exit_code_literals_are_pinned _________________

    def test_documented_exit_code_literals_are_pinned() -> None:
        """ADR 0016 documents these exit codes as user-facing API.
        ...
        assert EXIT_SUCCESS == 0
        assert EXIT_FAILURE == 1
>       assert EXIT_SERVICE_NOT_RUNNING == 2
E       assert 7 == 2

tests/test_cli.py:281: AssertionError
=========================== short test summary info ============================
FAILED tests/test_cli.py::test_documented_exit_code_literals_are_pinned - ass...
1 failed in 0.07s
```

```
$ uv run pytest "tests/test_cli.py::test_toggle_without_a_service_exits_two" "tests/test_cli.py::test_status_without_a_service_exits_two" -q
F...                                                                     [100%]
...
>       assert main(["toggle"]) == 2
E       AssertionError: assert 7 == 2
E        +  where 7 = main(['toggle'])
tests/test_cli.py:315: AssertionError
...
>       assert main(["status"]) == 2
E       AssertionError: assert 7 == 2
E        +  where 7 = main(['status'])
tests/test_cli.py:371: AssertionError
=========================== short test summary info ============================
FAILED tests/test_cli.py::test_toggle_without_a_service_exits_two - Assertion...
FAILED tests/test_cli.py::test_status_without_a_service_exits_two - Assertion...
3 failed in 0.14s
```

Probe reverted; `git status` shows no `cli.py` change.

## Other assertions whose truth depends on the thing they assert against

Same defect class as F2, noted but deliberately not fixed (out of scope for this ticket):

- **`FORBIDDEN_MODULES` is self-referential.** Both laziness tests iterate the tuple and assert
  against it, so emptying the tuple (or dropping `pystray` from it) makes them vacuous. The new
  subprocess test pins the import-time *behaviour* but is only as strong as the tuple's
  contents; a future ticket could assert the tuple equals the documented forbidden set.
- **`SUBCOMMANDS` and `KEY_BOUND_COMMANDS` are self-referential.** `test_top_level_help_lists_every_subcommand`
  and `test_each_subcommand_parses_and_accepts_config` iterate the same tuple they check, so
  removing a name from the tuple silently removes it from the check. ADR 0016 fixes the nine
  subcommands as user-facing API, so pinning the tuple's contents would close this.
- **Every `== EXIT_SUCCESS` / `== EXIT_FAILURE` assertion** in the suite compared against a
  constant. The new literal-pinning test covers `0` and `1` globally, so a renumbered
  `EXIT_FAILURE` now fails even for the many path tests that still compare against the constant.

## Verification

```
$ uv run pytest
409 passed in 0.94s
$ uv run pytest tests/test_cli.py -q
62 passed in 0.23s
$ uv run mypy src/ges2t tests
Success: no issues found in 30 source files
```

`git status` after both reverts lists only `tests/test_cli.py`, the ticket folder, and
`docs/project/README.md` (row 26) — no `src/` change.
