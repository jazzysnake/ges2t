# whisper-engine-build — 003: the four `002-review.md` follow-ups

- **Ticket:** [`ticket.md`](ticket.md)
- **Branch:** `feat/whisper-engine-build`
- **Worktree:** `.worktrees/whisper-engine-build`
- **Based on:** `002-review.md` (verdict pass, four non-blocking findings)
- **Date:** 2026-10-10
- **Status after this artifact:** ticket stays `in review`

`002-review.md` raised four non-blocking items, all of the same class the review
applied to ticket 41: **a document that overclaims**, and **a check that cannot
fail for the reason it names**. This artifact records each fix and its evidence.
Nothing pinned or verified changed: the revision, the build recipe, the `$ORIGIN`
flags, the manifest and every verified behaviour are untouched. No dependency was
added, and the suite still performs no network I/O.

| finding (§Risks) | fix | evidence |
|---|---|---|
| 1 — doc overclaim A (falsification count "three") | corrected `001-comment.md` §Falsification #2 to the measured six, and to the actual mutation | §F1 below (`6 failed, 57 passed`) |
| 2 — doc overclaim B (`SubprocessRunner` "every failure mode") | wrapped `OSError` in code **and** narrowed the docstring to the exact wrapped set | §F2 below (raw `PermissionError` removed) |
| 5 — near-tautological clone-timeout test | reshaped so it can fail for a wrong budget **and** for a non-positive one; renamed | §F3 below (`assert 3600.0 == 600.0`) |
| 3 — missing per-piece test | added "only `vulkan/vulkan.h` absent" → CPU-only, **no error**, via the real `ensure_engine` | §F4 below (`available` became `True` when the check was mutated) |

Every mutation below was applied to the **committed** tree and reverted with
`git checkout -- src/ges2t/whisper_engine.py`; `git status` was clean before and
after each (the four fixes were committed first, precisely so the revert was
exact — the pre-review run lost uncommitted hardening to a `git checkout`).

## F1 — doc overclaim A: the missing-`cmake` falsification fails six, not three

The claim in `001-comment.md` was wrong in two ways: the number (**six**, not
three) and the mutation (it cannot be "from `prerequisites_for(CPU)`" alone,
because the `cmake` `Prerequisite` lives in the shared `common` tuple that both
variants build on).

Re-measured on the reviewed tip, deleting the `cmake` block from the `common`
tuple and running the **whole file** with no `-k` filter — my earlier figure of
three came from a `-k` expression that deselected half the failures:

```
$ python3 - <<'PY'
from pathlib import Path
p = Path("src/ges2t/whisper_engine.py")
t = p.read_text()
old = """        Prerequisite(
            name="cmake",
            kind=PrerequisiteKind.EXECUTABLE,
            probe="cmake",
            package=PACKAGE_CMAKE,
        ),
    )
"""
assert old in t, "cmake Prerequisite block not found"
p.write_text(t.replace(old, "    )\n"))
PY
$ uv run pytest tests/test_whisper_engine.py -q
FAILED tests/test_whisper_engine.py::test_cpu_requirement_set_is_the_base_devel_and_cmake_line
FAILED tests/test_whisper_engine.py::test_vulkan_requirement_set_is_the_epic_package_line
FAILED tests/test_whisper_engine.py::test_missing_cmake_is_reported_with_its_package_and_command
FAILED tests/test_whisper_engine.py::test_prerequisite_kinds_route_to_the_matching_probe
FAILED tests/test_whisper_engine.py::test_ensure_engine_fails_before_building_when_a_prerequisite_is_missing
FAILED tests/test_whisper_engine.py::test_builder_raises_prerequisite_error_when_cmake_is_absent
6 failed, 57 passed in 1.60s
$ git checkout -- src/ges2t/whisper_engine.py     # reverted, tree clean
```

The observed failure also shows *why* the mutation must be described as
shared-tuple, not CPU-only: with `cmake` gone from both variants the typed error
degenerates to `cannot build the whisper.cpp engine (cpu): missing .` with
`sudo pacman -S base-devel`, which is exactly what
`test_missing_cmake_is_reported_with_its_package_and_command` catches.

`001-comment.md` §Falsification #2 now states the six tests, the shared-tuple
mutation, and that the earlier number came from a narrower `-k` run.

## F2 — doc overclaim B: the `SubprocessRunner` contract

The reviewer's reproduction was right: a present but non-executable `argv[0]`
raised a raw `PermissionError`, which the old docstring's "every failure mode
becomes a `BuildFailedError`" did not honour. Fixed **in code**, so the claim can
be made true rather than deleted:

- `except OSError as exc:` → `BuildFailedError(f"cannot run {argv[0]}: {exc}")`,
  covering a non-executable file, a directory, and anything else the spawn itself
  can raise. `FileNotFoundError` keeps its own "command not found" branch (it is an
  `OSError` subclass, so it stays first).
- The docstring now names exactly the wrapped set — non-zero exit, timeout, spawn
  `OSError` — instead of "every failure mode".
- The `timeout_seconds <= 0` case, which the old docstring also glossed, is now
  **rejected** (see F3).

New test `test_subprocess_runner_wraps_a_non_executable_executable` writes a
non-executable file and runs it:

```
$ uv run pytest tests/test_whisper_engine.py -q -k non_executable
1 passed
```

The docstring is only as strong as that test — deleting the handler makes it fail
with the untyped error the reviewer saw:

```
$ python3 - <<'PY'
from pathlib import Path
p = Path("src/ges2t/whisper_engine.py")
t = p.read_text()
old = """        except OSError as exc:
            raise BuildFailedError(f"cannot run {argv[0]}: {exc}") from exc
"""
assert old in t
p.write_text(t.replace(old, ""))
PY
$ uv run pytest tests/test_whisper_engine.py -q -k non_executable
E                   PermissionError: [Errno 13] Permission denied: '.../not-executable'
FAILED tests/test_whisper_engine.py::test_subprocess_runner_wraps_a_non_executable_executable
1 failed, 66 deselected in 0.26s
$ git checkout -- src/ges2t/whisper_engine.py     # reverted, tree clean
```

Behavioural note: this path is unreachable in the shipped flow (`argv[0]` is
always a `shutil.which` result or `"git"` after the guard), so nothing verified
changed — the fix is that the contract now matches what the code can raise.

## F3 — the clone-timeout test can now fail

`002-review.md` §Risks 5: `test_builder_uses_a_bounded_clone_timeout` only compared
the recorded value against `CLONE_TIMEOUT_SECONDS`, while the "bounded" claim was
backstopped by the separate `timeout > 0` test — it could not fail for the reason
its name implied.

The test is renamed `test_builder_passes_the_clone_timeout_into_the_clone_command`
and now asserts three things:

1. the clone invocation actually received `CLONE_TIMEOUT_SECONDS` (the propagation
   the name promises);
2. that budget is **positive and finite** (so it fails if the constant is ever
   `0`, `nan` or `inf`);
3. `CLONE_TIMEOUT_SECONDS != WHISPER_CPP_BUILD_TIMEOUT_SECONDS` — the copy/paste
   shape, where the clone is handed the much longer compile budget.

The "bounded" property itself is now structural, not asserted:
`SubprocessRunner.run` **rejects a non-positive timeout** with `ValueError`,
because `subprocess.run(timeout=0)` raises `TimeoutExpired` on the spot — an
accidental `0` would silently turn every command into a failure. New tests:
`test_subprocess_runner_rejects_a_non_positive_timeout` and
`test_subprocess_runner_has_a_positive_default_timeout`.

Wrong-budget mutation (the first clone timeout in `_clone` becomes the build
budget):

```
$ python3 - <<'PY'
from pathlib import Path
p = Path("src/ges2t/whisper_engine.py")
t = p.read_text()
marker = "    def _clone(self, request: BuildRequest, source: Path) -> None:"
head, tail = t.split(marker, 1)
assert tail.count("timeout_seconds=CLONE_TIMEOUT_SECONDS") == 2
tail = tail.replace(
    "timeout_seconds=CLONE_TIMEOUT_SECONDS",
    "timeout_seconds=WHISPER_CPP_BUILD_TIMEOUT_SECONDS",
    1,
)
p.write_text(head + marker + tail)
PY
$ uv run pytest tests/test_whisper_engine.py -q -k clone_timeout
E       assert 3600.0 == 600.0
FAILED tests/test_whisper_engine.py::test_builder_passes_the_clone_timeout_into_the_clone_command
1 failed, 66 deselected in 0.20s
$ git checkout -- src/ges2t/whisper_engine.py     # reverted, tree clean
```

And the guard's own falsification (delete the guard):

```
$ python3 - <<'PY'
from pathlib import Path
p = Path("src/ges2t/whisper_engine.py")
t = p.read_text()
old = """        if timeout_seconds <= 0:
            raise ValueError(
                f"a command must have a positive timeout, not {timeout_seconds}"
            )
"""
assert old in t
p.write_text(t.replace(old, ""))
PY
$ uv run pytest tests/test_whisper_engine.py -q -k non_positive_timeout
E           ges2t.whisper_engine.BuildFailedError: command timed out after 0s: ... -c print('never runs')
FAILED tests/test_whisper_engine.py::test_subprocess_runner_rejects_a_non_positive_timeout
1 failed, 66 deselected in 0.28s
$ git checkout -- src/ges2t/whisper_engine.py     # reverted, tree clean
```

## F4 — the missing per-piece Vulkan test

`002-review.md` §3 noted the suite covered "runtime missing", "spirv missing",
"shader missing" and "all missing", but not "only `vulkan/vulkan.h` missing".
Added `test_vulkan_absent_when_the_headers_are_missing`, which asserts the record
**and** the orchestration the ticket cares about: `available is False`, the missing
name is `vulkan-headers`, and the real `ensure_engine` picks `BuildVariant.CPU` and
builds — **no error**.

Falsified by making the check unable to notice the headers:

```
$ python3 - <<'PY'
from pathlib import Path
p = Path("src/ges2t/whisper_engine.py")
t = p.read_text()
old = """        return None not in (
            self.runtime,
            self.headers,
            self.spirv_headers,
            self.shader_compiler,
        )"""
new = """        return None not in (
            self.runtime,
            self.spirv_headers,
            self.shader_compiler,
        )"""
assert old in t
p.write_text(t.replace(old, new))
PY
$ uv run pytest tests/test_whisper_engine.py -q -k headers_are_missing
E        +  where True = VulkanStatus(runtime=..., headers=None, spirv_headers=..., shader_compiler=...).available
FAILED tests/test_whisper_engine.py::test_vulkan_absent_when_the_headers_are_missing
1 failed, 1 passed, 65 deselected in 0.15s
$ git checkout -- src/ges2t/whisper_engine.py     # reverted, tree clean
```

(`-k headers_are_missing` matches two tests — the existing
`test_vulkan_absent_when_spirv_headers_are_missing` contains the same substring —
which is why that run reports `1 failed, 1 passed`.)

## Verify (after all four fixes)

```
$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 57 source files

$ uv run pytest -q
990 passed in 6.53s
```

Baseline 923 + 63 tests from the first round + 4 new review-follow-up tests
(`headers_are_missing`, `non_executable`, `non_positive_timeout`,
`positive_default_timeout`) = **990**. No test was deleted, skipped or weakened;
the one changed test was renamed and strengthened.
