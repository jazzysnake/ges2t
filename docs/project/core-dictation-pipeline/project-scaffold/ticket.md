# Project scaffold: uv-managed Python project with a console script

- **Priority:** P0
- **Status:** in review
- **Complexity:** S
- **Epic:** ../epic.md

## Current state / context

The repository currently contains only `docs/` (19 ADRs and two `epic.md` files), a
`.gitignore`, and `.pi/`. There is no Python project at all: no `pyproject.toml`, no source
tree, no test runner. Every later ticket in this epic needs a place to put code and a command
to run tests, so this ticket is the first one and everything depends on it.

Environment facts: Arch Linux, Python **3.14.7** at `/usr/bin/python3`, `uv` 0.12.23
installed, GTK3 + PyGObject 3.56.3 installed **system-wide**, `gi` therefore only importable
from a venv created with system site packages.

Relevant decisions: ADR 0001 (Python, `src/` layout, `uv`), ADR 0013 (the venv must see the
system `gi`), ADR 0014 (pytest, run locally, no CI).

## Definition of done

- [x] `pyproject.toml` exists with: `name = "ges2t"`, `version = "0.1.0"`,
      `requires-python = ">=3.12"`, a `[project.scripts]` entry point
      `ges2t = "ges2t.cli:main"`, runtime dependencies `cactus-needle==3.1.3`, `pystray`,
      `pillow`, and a `[dependency-groups]` (or `[project.optional-dependencies]`) dev group
      containing `pytest` **and `mypy`**. No HTTP client dependency: downloads use the stdlib.
- [x] **`mypy` is configured and enforced** per ADR 0019: a `[tool.mypy]` section with
      `disallow_untyped_defs = true`, `no_implicit_optional = true`, and
      `ignore_missing_imports = true` for third-party libraries. `uv run mypy src/ges2t` must
      exit `0` (it will find nothing to complain about in the stub, which is the point).
      Add both commands to the README's Development section.
- [x] `src/ges2t/__init__.py` defines `__version__ = "0.1.0"`.
- [x] `src/ges2t/cli.py` exists as a **minimal stub only** — `main()` handling `--version`
      and `--help` — because the full surface is ticket `../cli-surface-and-commands/ticket.md`.
      This ticket must not define any subcommand.
- [x] `tests/test_smoke.py` asserts the version string is importable from the installed
      package (not from a relative path).
- [x] `README.md` exists with a one-paragraph description, the "Status: early MVP, not
      packaged for distribution" note (ADR 0000), and a **Development** section recording the
      exact commands below.
- [x] `uv.lock` is committed.
- [x] **The venv sees system site packages.** Running
      `uv run python -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk; print('ok')"`
      prints `ok`. Document in the README exactly which mechanism achieves this with the
      installed uv (e.g. `uv venv --system-site-packages --python /usr/bin/python3` or a
      `[tool.uv]` setting) — verify it empirically rather than assuming a flag exists.
- [x] `uv run ges2t --version` prints `0.1.0` and exits 0.
- [x] `uv run pytest` passes with exit code 0, and `uv run mypy src/ges2t` exits 0.

## Notes

- Add `from __future__ import annotations` and target modern typing; the codebase is
  3.12+ and uses `typing.Protocol` heavily (ADR 0010). **Every definition in the stub must be
  fully annotated, including `-> None`** — ADR 0019 makes this a project-wide rule, and this
  ticket is what makes it checkable.
- Include ADR 0019 in the README's Development section as the reason `mypy` is part of the
  test command.
- Do **not** add `numpy` as a dependency here. The probe measured that the transcription
  package does not need it (ADR 0007); if a later ticket wants it, that ticket adds it.
- Do not create `src/ges2t/{interfaces,config,model_store,service,doctor}.py`, `audio/`,
  `injectors/`, `transcribers/`, or `ui/` — those files belong to other tickets. Creating
  them here causes merge conflicts.
- No CI configuration (ADR 0014). No packaging metadata beyond what `uv` needs.
- `pystray` and `pillow` are declared now even though the tray is ticket
  `../../live-dictation-ux/tray-status-ui/ticket.md`, so the dependency set is fixed once.

## Related tickets

- depends on: none
- blocks: every other ticket in this epic
- related ADRs: ../../../adr/0001-python-implementation-language.md,
  ../../../adr/0013-tray-via-pystray-appindicator.md,
  ../../../adr/0014-testing-and-quality-bar.md,
  ../../../adr/0019-code-style-typing-and-interfaces.md
