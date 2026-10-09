# 002 — Review: project scaffold

- **Ticket:** `ticket.md` (project-scaffold)
- **Branch:** `feat/project-scaffold`
- **Worktree:** `.worktrees/project-scaffold`
- **Reviewed commit:** `4ea749d` (`feat(scaffold): uv-managed Python project with console script,
  pytest and mypy`) plus the docs updates in the same commit.
- **Reviewer:** independent review run from the worktree above.

## Verdict

**pass**

Every Definition-of-done checkbox is genuinely satisfied by running the stated command. Scope
discipline holds: exactly the files the ticket lists are present, none of the forbidden files
exist, and `cli.py` defines no subcommand. ADR 0001, 0013, 0014, 0019 and 0000 are met. The
mypy enforcement was proven effective with a temporary unannotated probe that was fully
reverted (`git status` clean afterwards). The venv/system-site-packages mechanism was
re-verified empirically from a deleted `.venv`, including the documented repair path.

No blocking findings. Two non-blocking risks are recorded at the end.

## What was reviewed

Diff `main...HEAD` consists of exactly:

```
A  README.md
A  pyproject.toml
A  src/ges2t/__init__.py
A  src/ges2t/cli.py
A  tests/test_smoke.py
A  uv.lock
M  docs/project/README.md
M  docs/project/core-dictation-pipeline/project-scaffold/ticket.md
A  docs/project/core-dictation-pipeline/project-scaffold/001-comment.md
```

`git ls-files` confirms only `src/ges2t/__init__.py`, `src/ges2t/cli.py` and
`tests/test_smoke.py` exist under the package/test trees; there is no `interfaces.py`,
`config.py`, `model_store.py`, `service.py`, `doctor.py`, `pipeline.py`, `audio/`,
`injectors/`, `transcribers/` or `ui/`.

## Per-criterion findings (DoD, one by one)

### 1. `pyproject.toml` metadata and dependencies — PASS

Parsed `pyproject.toml` with `tomllib`:

```
name: ges2t
version: 0.1.0
requires-python: >=3.12
scripts: {'ges2t': 'ges2t.cli:main'}
dependencies: ['cactus-needle==3.1.3', 'pystray', 'pillow']
dependency-groups: {'dev': ['pytest', 'mypy']}
mypy cfg: {'python_version': '3.12', 'disallow_untyped_defs': True, 'no_implicit_optional': True, 'ignore_missing_imports': True}
```

`grep -nE "httpx|requests|urllib3|aiohttp|httpcore" pyproject.toml` -> no matches, so no HTTP
client is declared (the model download stays stdlib, as required).

### 2. `mypy` configured **and enforced** per ADR 0019 — PASS

Config present (see above). `uv run mypy src/ges2t` exits 0:

```
$ uv run mypy src/ges2t
Success: no issues found in 2 source files
mypy exit=0
```

**Enforcement proven, not merely asserted.** I temporarily added
`src/ges2t/_probe_untyped.py` containing an unannotated function and re-ran the check:

```
$ cat src/ges2t/_probe_untyped.py
def probe_untyped(x):
    return x

$ uv run mypy src/ges2t
src/ges2t/_probe_untyped.py:1: error: Function is missing a type annotation  [no-untyped-def]
Found 1 error in 1 file (checked 3 source files)
mypy probe exit=1
```

I then deleted the probe and re-ran:

```
$ uv run mypy src/ges2t
Success: no issues found in 2 source files
mypy clean exit=0

$ git status --porcelain
(end)
```

The probe is completely gone; the tree is clean. The `[tool.mypy]` section is therefore
effective, not decorative. The README's Development section records both
`uv run pytest` and `uv run mypy src/ges2t` (verified by reading `README.md`).

### 3. `src/ges2t/__init__.py` defines `__version__ = "0.1.0"` — PASS

```
"""ges2t — *good enough speech 2 text*: fully offline dictation for Linux."""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
```

### 4. `src/ges2t/cli.py` minimal stub, no subcommand — PASS

`cli.py` defines only `build_parser()` and `main()`; `add_subparsers`/`add_parser` do not
appear (the only textual match for "subcommand" is the docstring sentence stating none is
defined). Runtime confirmation that there is no subcommand surface:

```
$ uv run ges2t run
usage: ges2t [-h] [--version]
ges2t: error: unrecognized arguments: run
bogus exit=2
```

`uv run ges2t --help` exits 0 and lists only `-h/--help` and `--version`. `cli.py` imports no
`cactus_needle`, `gi` or `pystray`.

### 5. `tests/test_smoke.py` — PASS

The test imports the installed `ges2t` package (absolute import, not a relative path) and
asserts `ges2t.__version__ == "0.1.0"`, cross-checks
`importlib.metadata.version("ges2t") == ges2t.__version__`, and asserts
`main(["--version"])` raises `SystemExit(0)` printing `0.1.0`. All test functions are
annotated with `-> None` including the fixture parameter
(`capsys: pytest.CaptureFixture[str]`).

### 6. `README.md` — PASS

`README.md` exists with a one-paragraph description, the exact statement
"Status: early MVP, not packaged for distribution" plus the ADR 0000 non-goals, a Development
section with prerequisites, the venv mechanism, and the four exact commands
(`uv sync`, `uv run ges2t --version`, `uv run pytest`, `uv run mypy src/ges2t`). It names
ADR 0019 as the reason `mypy` is part of the check and links the relevant ADRs. No packaging
or distribution claim is made (consistent with ADR 0000).

### 7. `uv.lock` committed — PASS

`git ls-files` includes `uv.lock`; `uv lock --check` exits 0:

```
$ uv lock --check
Resolved 35 packages in 1ms
lock check exit=0
```

`cactus-needle` resolves to exactly `3.1.3`; `requires-python = ">=3.12"` in the lock.

### 8. Venv sees system site packages (ADR 0013) — PASS, mechanism demonstrated

The pre-existing `.venv` had `include-system-site-packages = true`. Because I was asked to
verify the mechanism rather than trust it, I **deleted `.venv` and reproduced the README's
documented sequence from a fresh-clone state**:

```
### STEP A: documented fresh-clone sequence
$ uv venv --system-site-packages --python /usr/bin/python3
Using CPython 3.14.7 interpreter at: /usr/bin/python3
Creating virtual environment at: .venv
$ uv sync
Installed 30 packages in 63ms
$ cat .venv/pyvenv.cfg
home = /usr/bin
implementation = CPython
uv = 0.12.23
version_info = 3.14.7
include-system-site-packages = true
prompt = project-scaffold
$ uv run python -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk; print('ok')"
ok
```

I also reproduced the documented footgun and the documented repair:

```
### STEP B: uv sync alone on a fresh clone
$ uv sync
Using CPython 3.13.16
Creating virtual environment at: .venv
$ cat .venv/pyvenv.cfg
version_info = 3.13
include-system-site-packages = false
$ uv run python -c "import gi; ..."
ModuleNotFoundError: No module named 'gi'   # confirms the README's warning

### STEP C: documented repair
$ uv venv --clear --system-site-packages --python /usr/bin/python3
$ uv sync
$ cat .venv/pyvenv.cfg
version_info = 3.14.7
include-system-site-packages = true
$ uv run python -c "import gi; ... print('ok')"
ok

### STEP D: survives further sync/run
$ uv sync
$ uv run python -c "import sys; print(sys.version)"
3.14.7 (main, Aug 10 2026, ...)
$ grep include-system-site-packages .venv/pyvenv.cfg
include-system-site-packages = true
$ uv run python -c "import gi; ... print('ok-after-sync2')"
ok-after-sync2
```

Conclusion on the `001-comment.md` claim: the assertion "uv reuses an existing `.venv`" is
**demonstrated behaviorally**, not merely asserted — a subsequent `uv sync` leaves
`include-system-site-packages = true` and `version_info = 3.14.7` untouched while the gi
import still succeeds. The README's instruction is correct and in the right order (create the
venv before the first sync; repair with `--clear`). The `[tool.uv]`-has-no-such-setting claim
is also supported: `uv venv --help` exposes `--system-site-packages`, `uv sync --show-settings`
has no `system_site_packages` field, and `uv sync --help` has no such flag.

Note: the review's destructive step rebuilt the (git-ignored) `.venv`; it was left in the
working 3.14.7 + system-site-packages state. `git status --porcelain` is empty, so no tracked
artifact was affected.

### 9. `uv run ges2t --version` prints `0.1.0`, exit 0 — PASS

```
$ uv run ges2t --version
0.1.0
version exit=0
```

This invokes the real installed console script (`[project.scripts]`), not `python -m`.

### 10. `uv run pytest` passes, `uv run mypy src/ges2t` exits 0 — PASS

```
$ uv run pytest -q
...                                                                      [100%]
3 passed in 0.05s
pytest exit=0

$ uv run mypy src/ges2t
Success: no issues found in 2 source files
mypy exit=0
```

## ADR compliance

- **ADR 0019 (typing/interfaces):** `build_parser() -> argparse.ArgumentParser` and
  `main(argv: Sequence[str] | None = None) -> int` are fully annotated, including `-> None` on
  all test functions. `grep -rnE "-> *(tuple|Tuple|Any)|: *Any|Optional" src tests` returns no
  matches: no positional-tuple returns, no bare `Any`, no implicit `Optional`. No interfaces
  exist yet in this ticket, so interface-typed signatures are not applicable here. Enforcement
  is real (probe above).
- **ADR 0001:** `requires-python = ">=3.12"`, `src/ges2t/` layout, `uv` + `uv.lock`, console
  script; no HTTP client declared. `grep -c 'name = "numpy"' uv.lock` = 0 and no `numpy`
  dependency/import exists anywhere in the tree (only prose mentions in tickets/ADRs).
- **ADR 0013:** venv with `--system-site-packages` makes `gi` + `Gtk 3.0` importable; verified
  above on `/usr/bin/python3` 3.14.7. System packages the README lists are present:
  `python-gobject 3.56.3-1`, `gtk3 1:3.24.52-1`, `libappindicator 12.10.1-2`.
- **ADR 0014:** no CI added (no `.github/`, no workflow YAML anywhere); tests are headless and
  fast (3 passed in 0.05s, no mic/network/model).
- **ADR 0000:** README states "not packaged for distribution" and no AUR/Flatpak/AppImage/
  Windows/macOS/CI claims are made.

## Scope discipline

- Forbidden files: none present (verified with `find src tests -type f -name '*.py'`).
- `cli.py` is a stub with no subcommands (verified at runtime).
- No CI configuration added.

## Regressions / docs integrity

The `main...HEAD` docs diff touches only the board row, the ticket's status/checkboxes, and
the new `001-comment.md`; `git diff --name-only main...HEAD -- docs/` lists exactly those three
paths. No ADR or other ticket/epic file was modified. The board row reads
`in review` / `feat/project-scaffold` and the ticket status reads `in review`, which is the
correct state while this review is pending (flip to `done` is the orchestrator's step).

## Path sanity

I extracted every relative Markdown link from the files this ticket touched (`README.md`,
`docs/project/README.md`, `ticket.md`, `001-comment.md`) and resolved each against both the
file's directory and the repo root. Result: **32 links checked, 0 broken.** The README's claim
of `docs/adr/` holding 20 decision records is correct (`ls docs/adr/*.md | wc -l` = 20).

## Risks / recommended follow-up tickets

1. **Transitive network stack (already flagged in `001-comment.md`).** `cactus-needle` pulls
   `huggingface-hub` 2.2.0, which brings `httpx2`, `httpcore2`, `h11`, `anyio`, `fsspec`,
   `hf-xet`, `truststore`, etc. This ticket declares no HTTP client (correct), but the epic's
   ADR 0018 acceptance criterion ("grep for the project's network clients returns hits only in
   the acquisition path") will trip over this transitive chain unless ADR 0018 / the
   `doctor-command` ticket state that the rule constrains *our* declared dependencies and
   imports, not `cactus-needle`'s tree. Recommend a follow-up wording fix before the epic's
   final ADR-0018 grep, as the implementer requested.
2. **The venv mechanism is a documented manual step, not enforced by the project config.**
   `uv sync` on a fresh clone silently produces a venv without `gi` (Step B above). Later
   tickets that import `gi` (`tray-status-ui`) or rely on the tray should fail loudly with a
   clear message rather than relying on the README. ADR 0013 already mandates a backend
   assertion and a `doctor` check; ensure the `tray-status-ui`/`doctor` tickets test the
   `ModuleNotFoundError: gi` case too.

## Could not verify

- The external claim that uv's upstream feature request **astral-sh/uv#5737** is still open.
  No network check was performed as part of this review; the local uv 0.12.23 behavior (no
  persisted `system-site-packages` setting) was verified directly and is what matters.
- The exact internal reason uv "reuses" the venv. The *behavior* (config and interpreter
  preserved across `uv sync`/`uv run`) is empirically demonstrated above; the mechanism is
  inferred from that behavior plus `uv sync --show-settings`/`--help`, not from uv source.
- Anything requiring the tray, model, microphone or `ydotool`: those are later tickets and out
  of scope for this scaffold ticket.
- The implementer's four original `uv sync`/`uv run` runs and the GitHub API check recorded in
  `001-comment.md`; I re-ran equivalent commands myself, and all reproduced.
