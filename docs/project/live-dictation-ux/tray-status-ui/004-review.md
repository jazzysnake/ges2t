# 004 — Re-review: thread-start fallback (ticket 17, review 003 §4)

Reviewer: fourth pass, scoped **only** to `003-review.md` §4. Branch
`feat/tray-status-ui`, worktree `.worktrees/tray-status-ui`, HEAD `5298999`, fix
under review `cf8960a`. Rounds 1–3 findings (`001-comment.md`, `002-review.md`,
`003-review.md`) are treated as established and are not revisited.

## Verdict

**pass.** §4's blocking item is RESOLVED: the catch is local, the new test
falsifies, the 5 programming-error classes still raise, and the in-thread-body
angle is not a gap.

## 1. The catch is local (read the diff, not the prose)

`git diff 18746d6 HEAD --stat` touches only `001-comment.md`,
`src/ges2t/ui/tray.py` and `tests/test_tray_backend.py`. Inside `tray.py` the
fix is three things: the `TrayBackendError` docstring, the `start()` docstring,
and a `try/except RuntimeError` around **exactly** the single `thread.start()`
call (`src/ges2t/ui/tray.py:286`).

- **Wrapped:** `thread.start()` only.
- **Not wrapped:** `build_status_ui` (pipeline), `build_tray_status_ui`,
  `_create_icon`, `_apply_display`, and the thread *body* `_run_icon`.
- `git diff 18746d6 HEAD -- src/ges2t/pipeline.py` → **empty**. So
  `build_status_ui` and its `except TrayBackendError` /
  `except (ImportError, ModuleNotFoundError, OSError)` clause are
  **byte-identical** to the round-2/3 state; the clause was not re-broadened and
  round 2's finding is not re-opened.
- The clause is `except RuntimeError` (not bare `Exception`) over a single
  statement, so a `RuntimeError` raised anywhere else still propagates.

## 2. Falsification (reproduced, not accepted)

Removed the local catch (replaced the `try/except` with a bare
`thread.start()`), cleared `__pycache__` **and** `.mypy_cache`, ran the new test:

```
FAILED tests/test_tray_backend.py::test_build_status_ui_falls_back_when_the_tray_thread_cannot_start
E   RuntimeError: can't start new thread
1 failed in 0.24s   (exit 1)
```

Traceback, which names the whole chain:
`pipeline.py:222 build_status_ui` → `tray.py:424 build_tray_status_ui` →
`tray.py:286 start` → `_RefusingThread.start` raises. Without the local
conversion the `RuntimeError` **escapes `build_status_ui`** — so the test is
genuinely pinned to the fix, not to a string.

Restored with `git checkout`, re-cleared both caches, `sha256sum` on `tray.py`
= `48850620d1c1f243e1eb5449aaacaec354dbcbbb2c3db2309995802afcf967b7` **OK**,
restored run `1 passed in 0.18s`. Caches were cleared after **every** probe: a
same-second `cp`/checkout leaves a `.pyc` Python still accepts, so the
"restored" run would silently execute the mutation.

## 3. Nothing else regressed

- **Programming errors still raise, loudly:** the 5-class test
  `test_build_status_ui_propagates_programming_errors` passes 5/5 —
  `AttributeError`, `TypeError`, `NameError`, `AssertionError`, `KeyError`.
- **Environmental failures still fall back** to `NullStatusUI` + the two warning
  lines: `ImportError`, `ModuleNotFoundError`, `OSError` (3), the
  `TrayBackendError` assertion-failure test, and the new thread-start test.
- Focused run of the programming-error + environmental + new tests:
  `9 passed in 0.11s`. Whole `tests/test_tray_backend.py`: `18 passed`.

## 4. Angle — a failure *inside* the thread body

**Not a gap; the adversarial premise is false.** Traced and probed.

Path: `start()` → `threading.Thread(target=self._run_icon, ...)` →
`_run_icon` (`src/ges2t/ui/tray.py:367`):

```python
def _run_icon(self) -> None:
    icon = self._icon
    if icon is None:
        return
    try:
        icon.run()
    except Exception as exc:
        self._record_failure("tray loop", exc)
```

The entire body is inside `try/except Exception`; only the `self._icon` lookup
and the `is None` check sit outside, and neither can realistically raise.
`_record_failure` increments `_failures`, stores `_last_failure`, prints one
`TrayFailure(...).summary()` line to stderr once per distinct message, and
`failure_report()` returns `TrayFailure(count=…, last_message=…)`, which
`run_service` surfaces at shutdown through `_shutdown_status_ui`.

Probe (`/tmp/probe_inthread.py`; fake icon whose `run()` raises `OSError`, a
real `ges2t-tray` thread, `threading.excepthook` captured, no GUI and no real
icon):

```
stderr              : 'ges2t: the tray has failed 1 times: tray loop: failure inside the thread body\n'
threading.excepthook: 0 unhandled exception(s)
failure_count       : 1
last_failure        : tray loop: failure inside the thread body
failure_report      : TrayFailure(count=1, last_message='tray loop: failure inside the thread body')
```

So such a failure does **not** vanish into a stderr traceback with no count and
no report: it is counted, printed once, and returned by `failure_report` (and
surfaced at shutdown). The only ways out of the thread body are a
`BaseException` (`KeyboardInterrupt`/`SystemExit`) or a failure inside
`_record_failure` itself — both outside the environmental class this ticket
concerns and deliberately unswallowed. **Not a real gap; no follow-up needed.**

## 5. Mechanicals

- `uv run pytest -q` (displays unset) → **550 passed in 2.47s**, exit 0.
  (003 recorded 549; the +1 is the new thread-start test.)
- `rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests` →
  `Success: no issues found in 39 source files`, exit 0.
- **No test deleted/weakened:** `git diff 18746d6 HEAD -- tests/ | grep '^-' |
  grep 'def test_'` → NONE; `def test_` count in `test_tray_backend.py` 11 → 12
  (+1). Test change is purely additive (`_RefusingThread`, `_ThreadlessIcon`,
  one test function); no assertion removed or loosened.
- **No skip/xfail added:** `git diff 18746d6 HEAD | grep '^+' | grep -i
  'skip\|xfail'` matches only the word "skipped" in `001-comment.md` prose.
- **No config change:** no `pytest.ini`/`conftest.py` in the repo;
  `git diff 33413b3 HEAD -- pyproject.toml …` is empty; pytest reports only the
  `anyio` plugin and **pytest-timeout is not installed**.
- **Scope:** `18746d6..HEAD` is `001-comment.md`, `src/ges2t/ui/tray.py`,
  `tests/test_tray_backend.py` — nothing else.
- `git status` before committing: only `004-review.md` is new; every probe is
  reverted and the source hashes match HEAD (`5298999`).

## Verdict

**pass.** The fix is genuinely local (`thread.start()` only) and falsifiable
(the new test fails with `RuntimeError: can't start new thread` when the catch
is removed). `build_status_ui` is byte-identical to `18746d6`, so no
programming error is re-swallowed; the 5 programming-error classes still raise,
and the environmental cases still degrade to `NullStatusUI` + warning. The
in-thread-body case is already counted and reported, so no silently-failing
tray remains. Mergeable.

Not re-verified here, as established in rounds 1–3: the backend assertion
(`pystray.Icon.__module__ == "pystray._appindicator"`), the observability
mechanism and exit-path coverage, import discipline, Ayatana typelib
acceptance, and the user-owned manual desktop check (its DoD box stays
unticked).
