# ges2t — *good enough speech 2 text*

ges2t is a minimal, fully offline speech-to-text tool for Linux: press a hotkey, speak, and
the transcription is typed into whatever window currently has focus. Transcription runs
locally with Cactus Compute's 16 MB **Whistle** model on the **Needle 3** engine — no cloud,
no account, no text leaving the machine. The reference target is Arch Linux + KDE Plasma 6 on
Wayland; v1 exists to be usable as a daily driver on that machine.

> **Status: early MVP, not packaged for distribution.**
> Scope and non-goals are fixed by [ADR 0000](docs/adr/0000-project-scope-and-non-goals.md):
> no AUR/Flatpak/AppImage package, no
> Windows/macOS support, no CI, no distribution to third parties. Setup steps are documented
> system prerequisites rather than packaged ones.

## Development

### Prerequisites (Arch Linux, reference machine)

- **Python 3.12+** — developed and verified on the system interpreter `/usr/bin/python3`
  (3.14.7).
- **[uv](https://docs.astral.sh/uv/)** (verified with 0.12.23) for the environment and the
  lockfile.
- **GTK3 + PyGObject, system-wide**, plus `libappindicator` — `python-gobject` (3.56.3),
  `gtk3` (3.24.52), `libappindicator` (12.10.1). These are only needed by the tray (ticket
  `tray-status-ui`), but the virtualenv must be created with system site packages from the
  start: `pystray` silently falls back to an invisible XEmbed backend when `gi` is missing,
  and Plasma 6 has no XEmbed systray —
  [ADR 0013](docs/adr/0013-tray-via-pystray-appindicator.md).

### Environment: the venv must see the system `gi`

`uv 0.12.23` has **no `[tool.uv]` setting that persists `--system-site-packages`**
(`uv venv`'s flag is the only surface; [uv#5737](https://github.com/astral-sh/uv/issues/5737)
is still open). The verified mechanism is therefore to create the venv explicitly, once,
**before** the first `uv sync`, and then let `uv sync` manage it:

```bash
# Once, from the repository root. Use the *system* interpreter: uv otherwise prefers its
# own managed CPython, whose site-packages do not contain gi.
uv venv --system-site-packages --python /usr/bin/python3

# Then, as usual:
uv sync
```

This survives `uv sync`, `uv run` and `uv lock` because uv reuses an existing `.venv` whose
interpreter satisfies `requires-python`; it does not recreate it. It is still worth knowing
that `uv sync` on a machine with **no** `.venv` creates a plain one, so a fresh clone must run
the `uv venv` command first. To repair a venv that was created without it:

```bash
uv venv --clear --system-site-packages --python /usr/bin/python3 && uv sync
```

Verify it:

```bash
uv run python -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk; print('ok')"
# expected output: ok
```

### Commands

```bash
uv sync                                # create/update the environment from uv.lock
uv run ges2t --version                 # prints 0.1.0
uv run pytest                          # unit tests (ADR 0014: local only, no CI)
uv run mypy src/ges2t                  # static typing check
```

`mypy` is part of the test command on purpose: types are mandatory and enforced, per
[ADR 0019](docs/adr/0019-code-style-typing-and-interfaces.md) — every function is fully
annotated (including `-> None`), signatures depend on the `typing.Protocol` interfaces rather
than concrete classes, and multi-value returns are dataclasses rather than tuples. Tests are
fully annotated too. The configuration lives in `[tool.mypy]` in `pyproject.toml`
(`disallow_untyped_defs`, `no_implicit_optional`, `ignore_missing_imports`).

## Layout

```
src/ges2t/          # the package (src layout, ADR 0001)
tests/              # pytest suite, headless only (ADR 0014)
docs/adr/           # 20 decision records
docs/project/       # epics, tickets, and the project board
```
