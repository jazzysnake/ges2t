# 002 — review: pin the CLI test guarantees

**Ticket:** `pin-cli-test-guarantees` · **Branch:** `feat/pin-cli-test-guarantees` · **HEAD:** `99ff8fb`
**Reviewer:** adversarial review against the ticket's Definition of Done, `001-comment.md`, the
ticket-10 findings F1/F2, and ADRs 0014/0016/0019. Every claim below was reproduced by hand.
Probes were applied to `src/ges2t/cli.py` (and two untracked stub modules) and completely
reverted; the worktree `git status` is clean at the end.

`main` advanced several times during this review (it is now well past the branch's merge-base
`16d1996`; tip was `add5089` while writing). Counts in this review were reproduced against the
branch's own merge-base, and the merge-conflict analysis uses the current `main`.

## Verdict: **pass** — no blocking findings.

Both falsified gaps are closed, both closures were reproduced, no production code changed, no
test was deleted or weakened, and mypy is clean. Two residual items, neither blocking: a
self-referential-tuple gap (F2's class, disclosed and deliberately out of scope) that deserves a
small follow-up, and a broader-than-advertised board hunk that the orchestrator must resolve on
merge.

---

## 1. No production code changed — PASS (hard gate)

```
$ git diff main -- src/ges2t/cli.py
(empty)

$ git diff --name-only 16d1996..HEAD          # branch's own changes
docs/project/README.md
docs/project/core-dictation-pipeline/pin-cli-test-guarantees/001-comment.md
docs/project/core-dictation-pipeline/pin-cli-test-guarantees/ticket.md
tests/test_cli.py

$ git diff --stat 16d1996..HEAD -- src/
(empty)

$ git diff --stat HEAD -- src/     # nothing dirty, no untracked src files
(empty)

$ git status --short
(clean)
```

The only code file is `tests/test_cli.py`. `main` itself has not touched `src/ges2t/cli.py`
since the merge-base either, so the comparison against `main` agrees. No F1/F2 repair required a
production change, so neither is a real defect — confirmed.

## 2. Falsifications — both reproduced, plus the extra imports

### F1 — top-level `import pystray` in `cli.py`

```
$ uv run pytest "tests/test_cli.py::test_importing_the_cli_does_not_load_the_engine_gui_or_later_tickets" -q
>       assert _forbidden_modules_after_importing_cli_in_a_subprocess() == []
E       AssertionError: assert ['gi', 'pystray'] == []
tests/test_cli.py:432: AssertionError
1 failed in 0.67s
```

Exactly as the implementer reported (`pystray` pulls in `gi` on this machine). The **old runtime
test still passes** with that probe in place, which is the point of keeping both:

```
$ uv run pytest "tests/test_cli.py::test_toggle_does_not_import_the_engine_gui_or_later_tickets" -q
1 passed
```

### F1 — imports the report did not claim

| Probe (top-level in `cli.py`) | Result |
|---|---|
| `import needle` | `AssertionError: assert ['needle'] == []` — **caught** |
| `import ges2t.pipeline` (module absent today) | collection error: `ModuleNotFoundError: No module named 'ges2t.pipeline'` — **caught**, but as a collection error, not the clean assertion |
| `import ges2t.pipeline` with a stub module present | `AssertionError: assert ['ges2t.pipeline'] == []` — **caught** |
| `import ges2t.doctor` with a stub module present | `AssertionError: assert ['ges2t.doctor'] == []` — **caught** |

The `ges2t.pipeline`/`ges2t.doctor` distinction matters only for the failure *mode*: because the
modules do not exist yet, a top-level import fails at collection before the assertion runs. Once
tickets 11/12 land the modules, a top-level import is caught by the assertion proper. Either way
the suite goes red, which is what the guarantee requires. I created the stubs only to prove the
assertion path and deleted them.

### F2 — `EXIT_SERVICE_NOT_RUNNING = 7`

```
$ uv run pytest "tests/test_cli.py::test_documented_exit_code_literals_are_pinned" -q
>       assert EXIT_SERVICE_NOT_RUNNING == 2
E       assert 7 == 2
tests/test_cli.py:281: AssertionError

$ uv run pytest "tests/test_cli.py::test_toggle_without_a_service_exits_two" \
                "tests/test_cli.py::test_status_without_a_service_exits_two" -q
E       AssertionError: assert 7 == 2   # main(["toggle"])  (tests/test_cli.py:315)
E       AssertionError: assert 7 == 2   # main(["status"])  (tests/test_cli.py:371)
2 failed

$ uv run pytest tests/test_cli.py -q      # whole file with the probe
3 failed, 59 passed
```

Exactly the implementer's three failures. I also mutated the other direction the report did not:

```
$ # EXIT_ENGINE_FAILURE = 42
$ uv run pytest "tests/test_cli.py::test_documented_exit_code_literals_are_pinned" \
                "tests/test_cli.py::test_engine_failure_exits_three" -q
E       assert 42 == 3   # literal test, tests/test_cli.py:282
E       AssertionError: assert 42 == 3   # main(["toggle"])  (test_engine_failure_exits_three)
2 failed, 60 passed
```

### The decisive before/after (both constants mutated to `7` and `42`)

```
# pre-fix tests (tests/test_cli.py from 16d1996), mutated cli.py
60 passed

# post-fix tests (HEAD), same mutated cli.py
4 failed, 58 passed
```

That is F2 exactly: the old suite was blind to renumbered constants; the new suite is not.

All probes reverted with `git checkout -- src/ges2t/cli.py`; final `git status` clean.

## 3. The tests are genuinely stronger, not merely added — PASS

- **F1 runs in a fresh subprocess.** `_forbidden_modules_after_importing_cli_in_a_subprocess`
  executes `import ges2t.cli` under `sys.executable -c` and inspects `sys.modules` there. This is
  the only way to observe import-time dependencies, because the test module imports `cli` at
  collection time. Confirmed the subprocess resolves this worktree's source, not a stale copy:
  `subprocess imports: .../pin-cli-test-guarantees/src/ges2t/cli.py rc 0`.
- **The old runtime test was kept.** Removing it would lose the runtime-import check. Reproduced
  the complementarity: a runtime `import pystray` inside `_handle_thin_client` fails the old test
  (`assert ['gi', 'pystray'] == []`) and *passes* the new subprocess test; a top-level import does
  the reverse. Neither test subsumes the other, so keeping both is correct.
- **F2 pins literals.** `test_documented_exit_code_literals_are_pinned` asserts
  `0/1/2/3/130` for the five constants with an ADR 0016 rationale in the docstring, and the two
  service-absent path tests plus the engine-failure path assert `2`/`3` at the call sites.
  Independent of the constants: mutating either constant fails the literal test.
- **Nothing deleted, skipped, or weakened.** `git diff main..HEAD -- tests/test_cli.py` is purely
  additive except for three assertions changed *from* the constant *to* the literal (strictly
  stronger) and three added imports. No `skip`/`xfail` anywhere in the file.
- **Counts.** `tests/test_cli.py` from `16d1996` collects and passes **60**; HEAD collects
  **62**; full suite **409** (was 407). Consistent with two added test functions and no removed
  parameterizations.
- **Cost.** `--durations` puts the new subprocess test at **0.14 s**; the whole CLI file runs in
  **0.52 s** and the full suite in **1.84 s**. Still fast.

## 4. Are the two fixes sufficient? Residual self-reference — real gap, non-blocking

The implementer disclosed that `FORBIDDEN_MODULES` and `SUBCOMMANDS`/`KEY_BOUND_COMMANDS` are
iterated by the same tests that assert against them. I reproduced both.

**`FORBIDDEN_MODULES` (the more serious one).** With a top-level `import needle` in `cli.py`, and
`"needle"` removed from the tuple in a copy of the test file (repo untouched):

```
modified copy, subprocess laziness test : 1 passed     <-- gap
real test file,    subprocess laziness test : 1 failed  (assert ['needle'] == [])
```

So deleting one name from the tuple silently makes the whole laziness guarantee blind to that
import, and **nothing else fails**. That is exactly F2's shape — the constant is both the spec and
the instrumentation — and the laziness guarantee is the entire point of the subprocess test.

**`SUBCOMMANDS`/`KEY_BOUND_COMMANDS` (lower impact).** Removing `"models"` from `SUBCOMMANDS` in a
copy drops three parametrized cases (62 → 59) and everything passes, including
`test_top_level_help_lists_every_subcommand`. Literal coverage of `models`/`setup`/`doctor`
elsewhere means the real behaviour still has tests, so this one degrades the *help-listing*
guarantee rather than the whole surface.

**Verdict:** a real gap of the same class, worth a small follow-up — but **not blocking this
ticket**. Rationale: (a) the ticket's Notes explicitly say "note (do not necessarily fix) any
other assertion whose truth depends on the constant it asserts against" and "Do not widen this
into a test-suite overhaul"; (b) the underlying behaviour is correct and unchanged; (c) the
sibling ticket 27 (`pin-layout-test-guarantees`) is the natural home, or a one-assertion
follow-up: pin the tuples to literals, e.g.
`assert set(FORBIDDEN_MODULES) == {"needle", "gi", "pystray", "ges2t.pipeline", "ges2t.doctor"}`
and the nine subcommand names. It is cheap enough that the orchestrator could also add it to this
branch before merging if a same-class gap is considered unacceptable to ship.

## 5. Test honesty and subprocess robustness — PASS

- The new tests exercise the behaviour and fail when it is removed (section 2, plus the runtime
  complement in section 3).
- The helper uses `sys.executable` (the same venv interpreter running pytest), `capture_output`,
  and `assert completed.returncode == 0, completed.stderr`, so an import failure is loud rather
  than silently returning an empty list.
- `subprocess.run` waits for the child and the child is a short `-c` import: no leaked process.
  Confirmed after running the suite: `pgrep -af ges2t` shows only the unrelated tmux session and
  the reviewer's own shell; no `ges2t*.sock` anywhere under `/tmp`.
- **Nit (non-blocking):** the call has no `timeout=`. A local module import cannot hang today (no
  network, no service, no I/O), so the suite is effectively bounded; a `timeout=` would make that
  guarantee explicit rather than implied. Not worth changing now.
- The helper returns every non-empty stdout line; because the child prints only matching
  forbidden names, that is exactly the present-modules list. A stray `print` at import time would
  cause a false failure, which is arguably a feature (it flags import-time stdout pollution).

## 6. ADR 0019 and mypy — PASS

```
$ uv run mypy src/ges2t tests
Success: no issues found in 30 source files
```

All new code is annotated: the helper returns `list[str]`, the new test returns `-> None`. No
positional-tuple return, no bare `Any`, no implicit `Optional` in the added lines. The config
bites:

```
$ printf 'def probe(x):\n    return x\n' > tests/_review_mypy_probe.py
$ uv run mypy src/ges2t tests
tests/_review_mypy_probe.py:1: error: Function is missing a type annotation  [no-untyped-def]
Found 1 error in 1 file (checked 31 source files)
$ rm tests/_review_mypy_probe.py && uv run mypy src/ges2t tests
Success: no issues found in 30 source files
```

Probe removed; `[tool.mypy]` sets `disallow_untyped_defs = true` and `no_implicit_optional = true`.

## 7. Scope and hygiene — PASS with one board note

- Code file: `tests/test_cli.py` only. Other branch files are the ticket's own docs.
- No conflict markers in `tests/` or `src/` (the `=====` grep hits are pytest separators inside
  `001-comment.md`).
- No stray processes or sockets (section 5); all probes reverted; worktree clean.
- **Implementer's disclosure verified.** The main worktree is clean, its `tests/test_cli.py`
  matches `main`'s HEAD (`git diff --stat HEAD -- tests/test_cli.py` empty), and it does **not**
  contain `test_importing_the_cli_does_not_load...`. The accidental edit was genuinely restored.
- **Board note (non-blocking).** The branch's `docs/project/README.md` hunk is wider than its own
  row: relative to the branch's merge-base it changes rows **10** (`in review` → `done`) and
  **21** (`to do` → `in review`) as well as adding row **26**. Row 10 now matches `main`, but row
  21 does not — `main` already has ticket 21 as `done … (merged)`. This is a merge hazard, not a
  code problem; the orchestrator must resolve it as described below.

## Merge conflicts the orchestrator must resolve

`git merge-tree` of the branch into current `main` shows three conflict sites plus one clean
merge; `tests/test_cli.py` merges cleanly (`result 82af35b`).

1. **`docs/project/README.md` — changed in both.** Conflict on rows 21 and 26:
   - `main`: row 21 `done | (merged)`, row 26 `to do | —`
   - branch: row 21 `in review | feat/status-ui-outside-lock`, row 26 `in review | feat/pin-cli-test-guarantees`
   - **Resolution:** keep `main`'s row 21 (`done`, `(merged)`) and take the branch's row 26
     (`in review`, `feat/pin-cli-test-guarantees`). Do **not** take the branch's row 21 — it is
     stale. (Row 10 agrees on both sides already.)
2. **`docs/project/core-dictation-pipeline/pin-cli-test-guarantees/ticket.md` — ADD/ADD.** The
   file does not exist at the merge-base; `main` added it via `195d1d3` with the original `to do`
   / unchecked content, and the branch added it with `in review` / checked boxes.
   - **Resolution:** take the branch's version (status `in review`, boxes `[x]`); `main`'s copy is
     the untouched template, so there is no main-side content to preserve.
3. **`001-comment.md`** is branch-only ("added in remote") — no conflict.
4. `docs/project/orchestrator-state.md` is `main`-only — merges in cleanly.

This is the add/add conflict the implementer warned about, and it is exactly as described.

## Risks / follow-ups for the orchestrator

- **Follow-up (P3):** pin `FORBIDDEN_MODULES` and `SUBCOMMANDS`/`KEY_BOUND_COMMANDS` contents to
  literals (section 4). Same class as F2; two or three assertions; natural home is ticket 27 or a
  small sibling. Not required to merge this branch.
- **Merge resolution** as above; the branch's README row 21 must not win.
- The new subprocess test is the pattern the sibling ticket 27 will want for the layout guards.

## Could not verify

- That a *successful* top-level `import ges2t.pipeline` / `ges2t.doctor` is caught by the
  assertion was verified with temporary stub modules (since the real modules are tickets 11/12);
  with the modules absent it is caught as a collection error instead. Both are red.
- `main` moved several times during the review (concurrent merges); the conflict analysis reflects
  `main` at the time of writing and should be re-checked at merge time.
