# 001 — Scaffold implementation notes

Ticket: `ticket.md` (project-scaffold) · Branch: `feat/project-scaffold`

## The venv / system-site-packages mechanism (verified)

**Mechanism:** create the venv once, explicitly, before the first sync:

```bash
uv venv --system-site-packages --python /usr/bin/python3
uv sync
```

There is **no `[tool.uv]` setting** for this in uv 0.12.23, which is what the ticket asked me
to check. Evidence:

- `uv venv --help` exposes `--system-site-packages`; `uv sync --help` exposes no venv/system
  flag, and `uv sync --show-settings` has no `system_site_packages` field (only a
  `python_preference: Managed`).
- The uv settings reference (`https://docs.astral.sh/uv/reference/settings/`) contains no
  `system-site-packages` option.
- The upstream feature request for exactly this — "Allow setting virtual environment options
  in `pyproject.toml`" (astral-sh/uv#5737) — is still **open** (checked via the GitHub API).

**Why it survives `uv sync`:** uv reuses an existing `.venv` whose interpreter satisfies
`requires-python = ">=3.12"` instead of recreating it, so `include-system-site-packages = true`
in `.venv/pyvenv.cfg` is preserved. Verified empirically: after
`uv venv --system-site-packages --python /usr/bin/python3`, three further `uv sync` runs and
several `uv run` / `uv lock` invocations left `include-system-site-packages = true` intact,
and the `gi` check still printed `ok`.

**Two footguns the README documents, both verified:**

1. `uv sync` alone on a machine with no `.venv` creates the venv with
   `include-system-site-packages = false`, and picks uv's **managed CPython 3.13.16** rather
   than `/usr/bin/python3` (observed in the very first `uv sync` here), so `gi` is not
   importable. Hence the explicit `--python /usr/bin/python3` and the ordering "venv first,
   then sync".
2. Running `uv venv --system-site-packages` on an existing venv fails without `--clear` /
   `--allow-existing`; the documented repair is
   `uv venv --clear --system-site-packages --python /usr/bin/python3 && uv sync`.

## Resolved versions (from `uv.lock`)

| Package | Resolved |
|---|---|
| `cactus-needle` | 3.1.3 (as pinned) |
| `pystray` | 0.19.5 |
| `pillow` | 12.3.0 |
| `pytest` | 9.1.1 |
| `mypy` | 2.4.0 |

Other direct/notable transitive versions: `python-xlib` 0.33, `six` 1.17.0,
`huggingface-hub` 2.2.0. Python: `/usr/bin/python3` = 3.14.7.

## Surprising findings the orchestrator should know

1. **`cactus-needle` pulls a network stack transitively.** Its only dependency is
   `huggingface-hub` 2.2.0, which brings `httpx2`, `httpcore2`, `anyio`, `h11`, `fsspec`,
   `filelock`, `hf-xet`, `tqdm`, `truststore`, `click`, `pyyaml`.
   - We declare **no** HTTP client ourselves (ticket requirement met: model downloads in
     ticket 5 use the stdlib).
   - But this is directly relevant to ADR 0018's acceptance criterion "grep for the project's
     network clients returns hits only in the acquisition path": any grep for
     `httpx`/`huggingface`/`requests` in the dependency tree will hit this transitive chain.
     ADR 0018 / the doctor ticket may need to state that the rule constrains *our* declared
     dependencies and imports, not `cactus-needle`'s transitive tree. Flagging it now rather
     than silently reinterpreting the ADR.
2. **`mypy` is 2.4.0**, not 1.x. `[tool.mypy]` with `python_version`, `disallow_untyped_defs`,
   `no_implicit_optional`, `ignore_missing_imports` is accepted with no deprecation warning,
   and the config is proven effective: an unannotated probe function in `src/ges2t` makes
   `uv run mypy src/ges2t` fail with `[no-untyped-def]` (exit 1). The probe was deleted.
3. `numpy` is **not** in the tree at all — consistent with ADR 0007 and the ticket's "do not
   add numpy".
4. `uv.lock` resolved against the default PyPI index; all wheels used are pure Python, so no
   build toolchain was needed.

## Files created (exactly the ticket's list, nothing else)

- `pyproject.toml` — project metadata, `ges2t = "ges2t.cli:main"` script, runtime deps
  (`cactus-needle==3.1.3`, `pystray`, `pillow`), dev group (`pytest`, `mypy`), hatchling
  build backend, `[tool.mypy]`.
- `src/ges2t/__init__.py` — `__version__ = "0.1.0"`.
- `src/ges2t/cli.py` — minimal stub: `build_parser()` + `main()` handling only
  `--version` / `--help`. No subcommand.
- `tests/test_smoke.py` — version importable from the installed package, version matches the
  installed distribution metadata, `--version` prints `0.1.0` and exits 0.
- `README.md`, `uv.lock`.

No `interfaces.py`, `config.py`, `model_store.py`, `service.py`, `doctor.py`, `pipeline.py`,
`audio/`, `injectors/`, `transcribers/`, or `ui/` — those are other tickets' files.

## Verification output

```
$ uv venv --system-site-packages --python /usr/bin/python3
Using CPython 3.14.7 interpreter at: /usr/bin/python3

$ uv sync
Resolved 35 packages in 3ms
Checked 30 packages in 1ms
# .venv/pyvenv.cfg: version_info = 3.14.7, include-system-site-packages = true

$ uv run python -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk; print('ok')"
ok

$ uv run ges2t --version
0.1.0                      # exit 0

$ uv run pytest -q
3 passed in 0.06s          # exit 0

$ uv run mypy src/ges2t
Success: no issues found in 2 source files   # exit 0
```

## Notes for the next tickets

- `tests/test_smoke.py` is intentionally tiny; the `Fake*` doubles from ADR 0014 belong to
  ticket 2 (`core-interfaces-and-fakes`).
- The console script is installed editable, so `uv run ges2t ...` exercises the real entry
  point — ticket 10 only has to grow `cli.py`.
- Nothing here encodes the tray; ticket 13 just has to rely on `gi` being importable, which
  the README's `uv venv` step guarantees.
