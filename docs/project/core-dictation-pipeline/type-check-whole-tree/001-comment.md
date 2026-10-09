# 001 — Whole-tree type check: fix the one violation, report the second

Ticket: `ticket.md` (`type-check-whole-tree`) · Branch: `feat/type-check-whole-tree`
· Commit base: `664a575` (current `main`)

## Status: blocked

The violation this ticket was written for is fixed and verified. The enforcement command
does **not** exit 0, because running the wider check on current `main` surfaces a **second**
violation that this ticket's scope explicitly forbids me to touch. Details in "The second
error" below; the orchestrator must decide how to handle it.

## Exact cause of the original error

`_error_message(raw: Mapping[str, Any]) -> str` genuinely accepts a raw `[engine.whistle]`
TOML mapping — that is its whole job (it calls `WhistleConfig.from_mapping(raw)` and returns
the error message). Its parameter type is correct as written.

The broken link was the **caller**, in
`test_range_errors_name_the_key_the_value_and_the_allowed_range`:

```python
cases = [
    ({"chunk_seconds": 0.1}, "engine.whistle.chunk_seconds", "0.1", "0.25"),
    ({"window_seconds": 5, "chunk_seconds": 5}, ...),
    ({"window_seconds": 31}, "engine.whistle.window_seconds", "31.0", "at most 30"),
]
for raw, key, value, allowed in cases:
    message = _error_message(raw)
```

The three dict literals have different value types: `dict[str, float]`, `dict[str, int]`,
`dict[str, int]`. Because `dict` is invariant in its value type, mypy **joins** the three
tuple first-elements to `object` when it infers the list literal:

```
$ uv run mypy /tmp/reveal_check.py     # minimal reproduction of the inference
note: Revealed type is "list[tuple[object, str, str, str]]"
note: Revealed type is "object"        # <- `raw` in the for-loop
```

So `raw` was inferred as `object`, which is not assignable to `Mapping[str, Any]`. The
helper was right; the inferred type of the test's `cases` table was too wide.

## What I changed, and why it is the correct fix (not a suppression)

Annotated the literal with the type it actually has:

```python
cases: list[tuple[Mapping[str, Any], str, str, str]] = [
```

and added a four-line comment saying exactly why.

This is the correct fix, not a suppression, for these reasons:

- **It matches reality.** Every first element is a `Mapping[str, Any]`; the annotation states
  the caller's actual contract instead of letting mypy widen heterogeneous dicts to `object`.
  `Mapping[str, Any]` is also the type ADR 0019 explicitly blesses for the raw TOML boundary.
- **No `Any` is hidden.** `Any` appears only inside the mapping, where ADR 0019 permits it,
  and the helper already used it correctly. Nothing is cast, ignored, or widened.
- **No assertion is touched.** The `for` body and all four `assert`s are byte-for-byte
  unchanged. The test still sends the same three cases through `from_mapping` and still
  asserts each message names the key, the value, and the allowed range.
- **The alternative was worse.** Narrowing at the call site with `assert isinstance(raw,
  Mapping)` would also work, but it would add a runtime assertion for a condition the type
  checker already knows is true, and it would only narrow to `Mapping[Any, Any]`. Typing the
  caller's table is strictly more precise and keeps the test body about the behaviour, not
  about mypy. (The ticket's note allowed either; this is the "helper genuinely accepts a raw
  mapping → fix the caller" branch.)

## The second error (out of scope — do not attribute to this ticket)

Current `main` (`664a575`, after ticket 5 merged) produces **two** errors, not the one the
ticket recorded at `665f7bb`. The extra one comes from `tests/test_model_store.py`, which did
not exist at the ticket's snapshot commit:

```
tests/test_model_store.py:349: error: "bool" is invalid as return type for "__exit__" that always returns False  [exit-return]
tests/test_model_store.py:349: note: Use "typing.Literal[False]" as the return type or change it to "None"
tests/test_model_store.py:349: note: If return type of "__exit__" implies that it may return True, the context manager may swallow exceptions
```

The offending line is `FakeResponse.__exit__(self, *exc: object) -> bool: return False` in
ticket 5's test double. mypy is right that `-> bool` is too wide: a context manager whose
`__exit__` always returns `False` should be annotated `-> Literal[False]` (or `-> None`). This
is a real, correct mypy finding in test code — exactly the class of latent violation this
ticket exists to expose. It is **not** fixed here because the instructions and the ticket both
say: touch only `tests/test_whistle_config.py`, report additional violations, do not expand
scope.

Consequence: `uv run mypy src/ges2t tests` still exits **1**, on this unrelated file. The
ticket's DoD line "`uv run mypy src/ges2t tests` exits 0" cannot be met without editing
`tests/test_model_store.py`, so the ticket is `blocked` rather than `in review`.

## Before / after (real command output)

### Before the change

```
$ uv run mypy src/ges2t tests
tests/test_whistle_config.py:247: error: Argument 1 to "_error_message" has incompatible type "object"; expected "Mapping[str, Any]"  [arg-type]
tests/test_model_store.py:349: error: "bool" is invalid as return type for "__exit__" that always returns False  [exit-return]
tests/test_model_store.py:349: note: Use "typing.Literal[False]" as the return type or change it to "None"
tests/test_model_store.py:349: note: If return type of "__exit__" implies that it may return True, the context manager may swallow exceptions
Found 2 errors in 2 files (checked 17 source files)
```

### After the change

```
$ uv run mypy src/ges2t tests
tests/test_model_store.py:349: error: "bool" is invalid as return type for "__exit__" that always returns False  [exit-return]
tests/test_model_store.py:349: note: Use "typing.Literal[False]" as the return type or change it to "None"
tests/test_model_store.py:349: note: If return type of "__exit__" implies that it may return True, the context manager may swallow exceptions
Found 1 error in 1 file (checked 17 source files)
```

The `tests/test_whistle_config.py:247` line is gone because the types now match — not because
anything was silenced. Proof in isolation:

```
$ uv run mypy tests/test_whistle_config.py
Success: no issues found in 1 source file
```

### Unchanged requirements

```
$ uv run mypy src/ges2t
Success: no issues found in 9 source files

$ uv run pytest
collected 181 items
...
============================= 181 passed in 0.56s ==============================

$ uv run pytest tests/test_whistle_config.py -q
63 passed in 0.09s
```

## Test counts (before → after)

| Scope | Before | After |
|---|---|---|
| `uv run pytest` | 181 passed | 181 passed |
| `tests/test_whistle_config.py` | 63 passed | 63 passed |

Identical. No test was added, deleted, skipped, or weakened, so coverage of the range-error
messaging behaviour is unchanged.

## Diff

```diff
--- a/tests/test_whistle_config.py
+++ b/tests/test_whistle_config.py
@@ -233,7 +233,11 @@ def test_window_above_the_engine_limit_is_rejected(window: float) -> None:
 
 
 def test_range_errors_name_the_key_the_value_and_the_allowed_range() -> None:
-    cases = [
+    # Each first element is a raw `[engine.whistle]` TOML mapping, which is exactly
+    # what `_error_message` takes. The explicit annotation is the real fix, not a
+    # suppression: mypy joins the three differently-valued dicts in this literal to
+    # `object`, so without it `raw` would be `object` and no longer match the helper.
+    cases: list[tuple[Mapping[str, Any], str, str, str]] = [
         ({"chunk_seconds": 0.1}, "engine.whistle.chunk_seconds", "0.1", "0.25"),
         (
             {"window_seconds": 5, "chunk_seconds": 5},
```

`Mapping` and `Any` were already imported by the file; no import changes were needed.

## Other latent type problems

Only the one above (`tests/test_model_store.py:349`, `[exit-return]`). Within
`tests/test_whistle_config.py`, the wider check now reports nothing else. No production code
under `src/ges2t/` is implicated — `uv run mypy src/ges2t` is green, and nothing about this
change required touching `src/`.

## What the orchestrator must know

1. The line-247 fix is complete, correct, and verified; the ticket's own corrective work is
   done.
2. The ticket is marked **blocked**, not `in review`, only because the amended enforcement
   command cannot reach exit 0 while `tests/test_model_store.py:349` is unfixed, and this
   ticket's scope forbids fixing it. That violation was introduced by ticket 5 (merged after
   the ticket's `665f7bb` snapshot), not by this work.
3. To actually satisfy the ticket's headline DoD, someone must either (a) widen this ticket's
   scope to change `FakeResponse.__exit__` to `-> Literal[False]` in
   `tests/test_model_store.py`, or (b) open a follow-up ticket for it. The change is a
   one-line annotation with no behaviour change, but it belongs to ticket 5's file and is not
   mine to make under the stated rules.
