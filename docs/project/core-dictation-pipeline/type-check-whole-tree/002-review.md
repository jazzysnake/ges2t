# 002 — Review: whole-tree type check, tests included

- **Ticket:** `ticket.md` (type-check-whole-tree)
- **Branch:** `feat/type-check-whole-tree`
- **Worktree:** `.worktrees/type-check-whole-tree`
- **Reviewed commits:** `4debbb9` (annotate the range-error case table) and `bdc9bc9`
  (annotate `FakeResponse.__exit__` as `Literal[False]`) — HEAD `bdc9bc9`
- **Base:** `main` at `664a575` (`board: ticket 5 (model-store-and-verification) is done`);
  `git merge-base HEAD main` = `664a575` exactly.
- **Reviewer:** independent review run from the worktree above. Every command below was
  re-run by me; no claim in `001-comment.md` was trusted. The worktree was clean before the
  review, every probe was reverted completely, and `git status --porcelain` is empty after.

## Verdict

**pass**

No blocking findings. The headline requirement — `uv run mypy src/ges2t tests` exits 0 —
genuinely holds (17 source files, 9 under `src/ges2t` and 8 under `tests/`, i.e. every Python
file in both trees). Both changes are real type annotations, not `cast`s, `# type: ignore`s,
widened parameters, or weakened assertions: reverting each annotation re-introduces exactly
the error it removes, and the wide check (not the narrow one) is what catches each. Test
counts are unchanged (181 / 63 / 29) and the diffs add only a comment and an annotation line
to each test file. No file under `src/ges2t/` was touched. Scope is exactly the two permitted
test files plus the ticket's own docs, and the only `docs/project/README.md` change is ticket
20's own row.

Minor, non-blocking documentation nits are listed at the end (a "four asserts" miscount in
`001-comment.md`; two pre-existing, untouched `# type: ignore[misc]` lines).

## 1. Headline DoD — PASS

```
$ uv run mypy src/ges2t tests
Success: no issues found in 17 source files
exit=0

$ uv run mypy src/ges2t
Success: no issues found in 9 source files
exit=0
```

17 source files is exactly the number of `.py` files under the two trees, and there is no
`exclude` or `files` setting in `pyproject.toml` (the `[tool.mypy]` block sets only
`python_version`, `disallow_untyped_defs`, `no_implicit_optional`, `ignore_missing_imports`),
so the wide check covers every Python file in `src/` and `tests/`.

## 2. The fixes are real, not suppressions — PASS

### `tests/test_whistle_config.py` (commit `4debbb9`)

The helper and its only relevant callee confirm the stated reasoning:

```
tests/test_whistle_config.py:31:def _error_message(raw: Mapping[str, Any]) -> str:
src/ges2t/transcribers/whistle_config.py:95:    def from_mapping(cls, raw: Mapping[str, Any]) -> WhistleConfigResult:
```

`_error_message` forwards straight to `WhistleConfig.from_mapping`, whose parameter is also
`Mapping[str, Any]`; the raw `[engine.whistle]` TOML boundary is `Mapping[str, Any]` by design.
So the helper was already correct and the caller's table was the defect. The three first
elements are `dict[str, float]`, `dict[str, int]`, `dict[str, int]`; because `dict` is
invariant, mypy joins them to `object`, so the loop variable `raw` was `object`. The fix
annotates the caller's literal — `cases: list[tuple[Mapping[str, Any], str, str, str]] = [` —
which is the actual type of the table, adds no `Any` outside the permitted TOML boundary, and
touches nothing else.

The diff is confined to the annotation plus a four-line comment; the `for` body and its three
`assert` statements are byte-for-byte identical to the base:

```
$ git show 664a575:tests/test_whistle_config.py | sed -n '235,250p'
...
    for raw, key, value, allowed in cases:
        message = _error_message(raw)
        assert key in message, message
        assert value in message, message
        assert allowed in message, message
```

There are **three** assert statements (plus the `_error_message` call), all unchanged; there is
no `cast`, no `type: ignore`, and no deleted or weakened assertion anywhere in the change.
(Pre-existing `# type: ignore[misc]` lines at 68 and 74 of this file are untouched and not
part of this diff — see minor observations.)

### `tests/test_model_store.py` (commit `bdc9bc9`)

`FakeResponse.__exit__` changed only its declared return type, `-> bool` → `-> Literal[False]`,
with `from typing import Literal` added; the body is still `return False`:

```
tests/test_model_store.py:356:    def __exit__(self, *exc: object) -> Literal[False]:
tests/test_model_store.py:357:        return False
```

The parameter `*exc: object` is unchanged, so the signature was not widened.

The reasoning is accurate. In `_download`, the response is used as a context manager:

```
src/ges2t/model_store.py:423:    with urllib.request.urlopen(request) as response, destination.open("wb") as handle:
```

A `__exit__` that returns a truthy `True` suppresses the exception, so `-> bool` admits a
contract under which a download failure could be swallowed and the atomic-install cleanup
never see it. `Literal[False]` states precisely that this double always returns `False` and
never suppresses, which is what the test relies on. The claim that `None` "would mean
rewriting the body, since returning a value from a `-> None` method is itself an error" is
also accurate: `def f() -> None: return False` is a mypy error (`No return value expected`),
so `-> None` would require changing `return False` to `return`/`return None`. `Literal[False]`
is strictly more precise than `bool` and preserves the existing body exactly.

Judgement on `None`: both `Literal[False]` and `None` satisfy mypy's note, and both mean "does
not suppress". `None` is arguably the more conventional spelling for a no-op `__exit__`, but it
is not *better*: `Literal[False]` is at least as precise (it pins the exact returned value) and
is the minimal, zero-body-churn change. The choice is well-reasoned and the accompanying
comment is accurate.

### Falsification — reverting each annotation makes the wide check red again

Probe 1 — reverted `cases:` back to a bare `cases =` and ran the wide check:

```
tests/test_whistle_config.py:251: error: Argument 1 to "_error_message" has incompatible type "object"; expected "Mapping[str, Any]"  [arg-type]
Found 1 error in 1 file (checked 17 source files)
exit=1
```

Same error, same code as the ticket recorded (line 251 rather than 247 only because the added
comment shifted the line number). Restored with `git checkout -- tests/test_whistle_config.py`.

Probe 2 — reverted `-> Literal[False]` back to `-> bool` and ran both checks:

```
$ uv run mypy src/ges2t tests
tests/test_model_store.py:356: error: "bool" is invalid as return type for "__exit__" that always returns False  [exit-return]
tests/test_model_store.py:356: note: Use "typing.Literal[False]" as the return type or change it to "None"
tests/test_model_store.py:356: note: If return type of "__exit__" implies that it may return True, the context manager may swallow exceptions
Found 1 error in 1 file (checked 17 source files)
exit=1

$ uv run mypy src/ges2t
Success: no issues found in 9 source files
exit=0
```

The error returns identically, and the narrow `src/ges2t`-only command stays green — direct
proof that the widened check is what catches this class of test-code violation. Restored with
`git checkout -- tests/test_model_store.py`; `git status --porcelain` is empty.

## 3. Coverage preserved — PASS

```
$ uv run pytest -q
181 passed in 0.60s
$ uv run pytest tests/test_whistle_config.py -q
63 passed in 0.09s
$ uv run pytest tests/test_model_store.py -q
29 passed in 0.12s
```

Counts match the claimed before/after exactly (181 total; 63 and 29 per file). Inspection of
both diffs — not just the counts — confirms no test was added, deleted, skipped, or weakened:
`tests/test_whistle_config.py` changes only the `cases` line and inserts a four-line comment;
`tests/test_model_store.py` changes only the import, the `__exit__` annotation, and inserts a
six-line comment. No `@pytest.mark.skip`, `xfail`, or removed/edited assertion appears in the
diff.

## 4. No production-code change — PASS

`git diff --name-only 664a575..HEAD` is exactly:

```
docs/project/README.md
docs/project/core-dictation-pipeline/type-check-whole-tree/001-comment.md
docs/project/core-dictation-pipeline/type-check-whole-tree/ticket.md
tests/test_model_store.py
tests/test_whistle_config.py
```

Nothing under `src/ges2t/` appears. The error was a test-code inference defect, not a real bug
papered over, so no blocking finding here.

## 5. No scope creep — PASS

Exactly the two permitted test files (`tests/test_whistle_config.py`,
`tests/test_model_store.py`) plus the ticket's own docs (`ticket.md`, `001-comment.md`). The
only `docs/project/README.md` change is ticket 20's own row:

```
-| 20 | [type-check-whole-tree](...) | XS | to do | 4 | — |
+| 20 | [type-check-whole-tree](...) | XS | in review | 4 | feat/type-check-whole-tree |
```

No other ticket's row changed. `pyproject.toml`, `uv.lock`, `src/`, and all other test files
are absent from the diff.

## 6. Nothing left red — PASS

The wide check reports no issues at all. Both originally-observed violations
(`tests/test_whistle_config.py` `[arg-type]` and `tests/test_model_store.py` `[exit-return]`)
are gone and no new one appeared. As noted in §1, the check covers all 17 Python files in
`src/` and `tests/` with no exclusions, so coverage of the rule matches the rule's scope.

## 7. Board, links, conflict markers — PASS

- `ticket.md` Status is `in review`; `docs/project/README.md` row 20 is `in review` with branch
  `feat/type-check-whole-tree`.
- Relative targets referenced by the ticket all resolve on disk: `../epic.md`,
  `../../../adr/0019-code-style-typing-and-interfaces.md`,
  `../../../adr/0014-testing-and-quality-bar.md`, `../whistle-engine-config/ticket.md`, and
  `001-comment.md`.
- No conflict markers anywhere in the working tree (a scan of all `*.py`/`*.md` excluding
  `.git/` and `.venv/` returns none).

## Minor, non-blocking observations

1. `001-comment.md` says "The `for` body and all **four** `assert`s are byte-for-byte
   unchanged" (line 62). The test contains **three** `assert` statements (the fourth thing in
   the loop is the `_error_message` call). The substantive claim is true — nothing in the body
   changed — but the count in the comment is wrong. Doc-only nit; no code impact.
2. `tests/test_whistle_config.py` lines 68 and 74 carry pre-existing `# type: ignore[misc]`
   annotations. They predate this change, are untouched by the diff, and are not in scope; the
   wide check accepts them. Noted only for completeness.

## What I could not verify

Nothing material. The ticket needs no network or external artifact; every DoD claim is
reproducible locally and was re-run here. I did not independently re-derive all 17 files' type
correctness by hand — I rely on mypy's own reporting for that — but the file-count check
confirms the command's scope matches the tree, and both falsification probes confirm the
command genuinely reacts to the test files.

## Statement on whether the fixes are genuine rather than suppressions

Both are genuine annotations. Neither introduces a `cast`, a `# type: ignore`, a widened
parameter, or a weakened/deleted assertion; each states a type the value already has
(`Mapping[str, Any]` for the TOML mapping the helper and `from_mapping` declare, and
`Literal[False]` for a `__exit__` whose body is literally `return False`). Each annotation is
load-bearing: reverting it re-introduces the exact mypy error the wide check was written to
catch, while the narrow `src/ges2t`-only command stays green. The two changes are the minimal
real fixes, not silencers.
