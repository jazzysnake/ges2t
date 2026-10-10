# whisper-engine-build — re-review of the `002-review.md` fixes (003)

- **Ticket:** [`ticket.md`](ticket.md)
- **Branch:** `feat/whisper-engine-build` — tip `3d51b990dbfaab6f376c5badd58c36ffbc1d1ad2`
- **Reviewed scope:** `git diff e99c3ce..HEAD` — commits `56ea030` (code + tests) and `3d51b99` (docs)
- **Supersedes the "nits" of:** [`002-review.md`](002-review.md) (verdict pass)
- **Reviewer:** ges2t reviewer (independent; implementation not touched)
- **Date:** 2026-10-10

## Verdict: **pass**

All four `002-review.md` follow-ups are fixed and each fix is **falsifiable** — I broke every new
guard and watched the corresponding test fail, then reverted to a byte-identical tree. The changes are
additive and contained: the pinned revision, the build recipe, the `$ORIGIN` flags and the manifest
are **byte-identical** to `e99c3ce`; only the `SubprocessRunner` contract (docstring + spawn-`OSError`
wrap + non-positive-timeout guard), tests, and docs changed.

Regression is green: **`990 passed`**, **`mypy: Success: no issues found in 57 source files`**, tree
clean, `src/` and `tests/` byte-identical to `HEAD` after every probe, no binary or probe file left.

## Answer: **safe to merge — yes**

The four fixes are correct, minimal, and each is covered by a test that I saw fail for the stated
reason. Nothing pinned or previously verified changed. Two residual notes below are cosmetic or
contract-only (both unreachable in the shipped flow); neither blocks the merge.

---

## 1. Scope — only the intended regions differ

```
$ git diff e99c3ce..HEAD --name-only
docs/project/whisper-cpp-backend/whisper-engine-build/001-comment.md
docs/project/whisper-cpp-backend/whisper-engine-build/003-comment.md
docs/project/whisper-cpp-backend/whisper-engine-build/ticket.md
src/ges2t/whisper_engine.py
tests/test_whisper_engine.py

$ git diff e99c3ce..HEAD --stat
 …/whisper-engine-build/001-comment.md |  40 +++-
 …/whisper-engine-build/003-comment.md | 250 +++++++++++++++++++++
 …/whisper-engine-build/ticket.md      |   4 +
 src/ges2t/whisper_engine.py           |  17 +-
 tests/test_whisper_engine.py          |  80 ++++++-
 5 files changed, 375 insertions(+), 16 deletions(-)
```

The **entire** `src/` diff is three hunks: the `SubprocessRunner` docstring, the
`timeout_seconds <= 0` guard, and the `except OSError` branch (shown in §3). Invariants extracted and
compared against `e99c3ce` are identical:

```
$ diff <(git show e99c3ce:src/ges2t/whisper_engine.py | grep -E 'WHISPER_CPP_(REVISION|TAG|URL|VERSION):|BUILD_TARGET:|LIBRARY_GLOBS|MANIFEST_NAME:|ENTRY_LIBRARY_NAME:') \
       <(grep -E 'WHISPER_CPP_(REVISION|TAG|URL|VERSION):|BUILD_TARGET:|LIBRARY_GLOBS|MANIFEST_NAME:|ENTRY_LIBRARY_NAME:' src/ges2t/whisper_engine.py)
IDENTICAL   # pinned revision d1be6fde…, v1.9.5, BUILD_TARGET=whisper, globs, manifest name

$ diff <(git show e99c3ce:src/ges2t/whisper_engine.py | grep -F -e DCMAKE -e -DWHISPER -e GGML_VULKAN -e '--build' -e '--target' -e clone) \
       <(grep -F -e DCMAKE -e -DWHISPER -e GGML_VULKAN -e '--build' -e '--target' -e clone src/ges2t/whisper_engine.py)
IDENTICAL   # -DCMAKE_BUILD_WITH_INSTALL_RPATH=ON, -DCMAKE_INSTALL_RPATH=$ORIGIN, clone argv, target

$ diff <(git show e99c3ce:src/ges2t/whisper_engine.py | sed -n '/^def _build_mapping/,/^def verify_build/p') \
       <(sed -n '/^def _build_mapping/,/^def verify_build/p' src/ges2t/whisper_engine.py)
IDENTICAL   # manifest writer / reader unchanged
```

## 2. Doc overclaim A — corrected, and the six reproduced

`001-comment.md` §Falsification #2 now names the actual mutation (delete the `cmake` `Prerequisite`
from the shared `common` tuple) and the measured **six**. Reproduced on the current tip by deleting
that block and running the whole file:

```
FAILED tests/test_whisper_engine.py::test_cpu_requirement_set_is_the_base_devel_and_cmake_line
FAILED tests/test_whisper_engine.py::test_vulkan_requirement_set_is_the_epic_package_line
FAILED tests/test_whisper_engine.py::test_missing_cmake_is_reported_with_its_package_and_command
FAILED tests/test_whisper_engine.py::test_prerequisite_kinds_route_to_the_matching_probe
FAILED tests/test_whisper_engine.py::test_ensure_engine_fails_before_building_when_a_prerequisite_is_missing
FAILED tests/test_whisper_engine.py::test_builder_raises_prerequisite_error_when_cmake_is_absent
6 failed, 61 passed in 1.71s
```

The identical **six tests** fail. (The pass-count line is `61`, not the `57` quoted in the documents,
because they were added with the four new tests; the 63-test reviewed tip reads `6 failed, 57 passed`,
which is what the implementer re-measured. See residual note R1.) Reverted; tree clean.

## 3. Doc overclaim B and its code fix — falsified

New test passes:
```
$ uv run pytest tests/test_whisper_engine.py -q -k "non_executable or non_positive_timeout"
2 passed in 0.26s
```

The docstring now names **exactly** the wrapped set (grep for the old phrase finds it only in the
review/comment artifacts describing the *old* text, never in `src/`):

```
Exactly the failures a spawned command can produce are turned into a
BuildFailedError …: a non-zero exit, a timeout, and an OSError from the
spawn itself (a missing or non-executable argv[0]). A non-positive
timeout_seconds is rejected outright as a caller bug …
```

Deleting the `except OSError` branch makes the raw `PermissionError` escape and the test fail:

```
E  PermissionError: [Errno 13] Permission denied: '/tmp/pytest-of-matee/…/not-executable'
FAILED tests/test_whisper_engine.py::test_subprocess_runner_wraps_a_non_executable_executable
1 failed in 0.41s
```

Reverted. Branch order is correct (`FileNotFoundError` before its `OSError` base, so the missing-git
message is unchanged):
```
634:        except subprocess.CalledProcessError as exc:
640:        except subprocess.TimeoutExpired as exc:
644:        except FileNotFoundError as exc:
646:        except OSError as exc:
```

## 4. The clone-timeout test can now fail — both mutations

Wrong budget (clone handed the compile budget):
```
$ git diff: timeout_seconds=CLONE_TIMEOUT_SECONDS → WHISPER_CPP_BUILD_TIMEOUT_SECONDS (first occurrence)
E       assert 3600.0 == 600.0
FAILED tests/test_whisper_engine.py::test_builder_passes_the_clone_timeout_into_the_clone_command
1 failed in 0.29s
```

Guard removed (`if timeout_seconds <= 0:`) → `test_subprocess_runner_rejects_a_non_positive_timeout`
fails with the immediately-timing-out command the guard exists to prevent:
```
E  ges2t.whisper_engine.BuildFailedError: command timed out after 0s: …python -c print('never runs')
FAILED tests/test_whisper_engine.py::test_subprocess_runner_rejects_a_non_positive_timeout
1 failed in 0.41s
```

Both reverted. The near-tautology is genuinely gone: the renamed test now fails on a wrong budget,
and the positivity guard is separately falsified.

## 5. The headers-missing test is real — falsified

Dropping `self.headers` from `VulkanStatus.available` makes it fail:
```
E  AssertionError: assert not True
E   +  where True = VulkanStatus(runtime=…, headers=None, spirv_headers=…, shader_compiler=…).available
FAILED tests/test_whisper_engine.py::test_vulkan_absent_when_the_headers_are_missing
1 failed in 0.28s
```
Reverted. All four pieces now have their own test, and all pass:
```
$ grep -n "def test_vulkan_absent_when" tests/test_whisper_engine.py
410:def test_vulkan_absent_when_spirv_headers_are_missing() -> None:
421:def test_vulkan_absent_when_the_runtime_is_missing() -> None:
431:def test_vulkan_absent_when_the_shader_compiler_is_missing() -> None:
442:def test_vulkan_absent_when_the_headers_are_missing(tmp_path: Path) -> None:
$ uv run pytest tests/test_whisper_engine.py -q -k vulkan_absent_when
4 passed, 63 deselected in 0.23s
```
(No test was deleted or skipped: the only removed `def test` line is the rename
`test_builder_uses_a_bounded_clone_timeout` → `test_builder_passes_the_clone_timeout_into_the_clone_command`.
File count 63 → **67**.)

## 6. Regression and hygiene

```
$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 57 source files        # MYPY_EXIT=0

$ timeout 600 uv run pytest -q
990 passed in 8.35s                                # PYTEST_EXIT=0

$ git diff --exit-code -- src/ tests/ && echo IDENTICAL
IDENTICAL                                          # byte-identical to HEAD after all probes
$ git status --short ; git diff --stat
                                                   # both empty (clean)
$ git ls-tree -r --name-only HEAD | grep -iE '\.(so|bin|o|a|pyc)$' || echo "no binaries tracked"
no binaries tracked
```

Every mutation above was reverted with `git checkout -- src/ges2t/whisper_engine.py`, and
`rm -rf .mypy_cache __pycache__` was run after each probe. No probe file remains; the reference tree
was not touched.

---

## Residual risks / notes (none blocking)

- **R1 — stale pass-count in the quoted falsification.** `001-comment.md` §Falsification #2 and
  `003-comment.md` §F1 quote `6 failed, 57 passed`. That number is a real measurement **at the
  63-test reviewed tip `e99c3ce`**; on the current 67-test tree the same six tests fail and the
  summary reads `6 failed, 61 passed`. The *set* of six is unchanged, so the claim is true, but a
  reader re-running it now will see a different pass count. Cosmetic; optionally re-quote or footnote.
- **R2 — the new non-positive-timeout guard raises `ValueError`, not `BuildFailedError`.** Correct as
  a caller-bug signal but outside the `WhisperEngineError` hierarchy, so a future caller catching only
  `BuildFailedError` would not catch it. Unreachable in the shipped flow (every call site passes a
  positive module constant), so this is a contract note, not a defect.
- **R3 — the spawn-`OSError` path stays unreachable in the shipped flow.** `argv[0]` is always a
  `shutil.which` result or `"git"` after the guard, so this fix makes the documented contract match
  what the code *can* raise rather than fixing an observed failure. That is the right shape; recorded
  so the claim is not read as a reachable-bug fix.

## What would falsify the central claim of this round

The claim is: *"the four follow-ups are fixed, each new guard is load-bearing, and nothing pinned
changed."* It would be falsified by any of: an invariant diff showing a changed revision/flag/manifest
(it did not); a new test that passes with its guard deleted (all failed when broken); a stale
`src/`/`tests/` tree after probing (byte-identical); or a red suite/mypy (green).

## Safe to merge? — **yes**

Recommend merging `feat/whisper-engine-build` into `main`. The one user-owned item from
`002-review.md` is unchanged and still honestly recorded: the end-to-end build requires the user to
install `cmake` (and the Vulkan dev packages for a GPU build) and run it once.
