"""Resolve the console script a desktop launcher must run (ADR 0004, ticket 40).

``ges2t toggle`` is **not on a KDE shortcut's ``PATH``**: ``uv sync`` installs
the console script beside the interpreter in ``.venv/bin/``, and a shortcut runs
with the session environment and no working directory, so neither the bare name
nor ``uv run`` starts it. The advice the tool prints must therefore name the
console script by its absolute path.

The running process *is* that script's Python entry point, so it can locate
itself: a virtualenv installs ``ges2t`` beside ``python`` and a system install
puts both in ``/usr/bin``, so :func:`resolve_console_script` looks next to
:data:`sys.executable`. Symlinks are deliberately **not** resolved, because
``.venv/bin/python`` is a symlink to the system interpreter and following it
would name the wrong script (``/usr/bin/ges2t``). The path is only returned when
it exists; a caller that gets ``None`` must fall back to the bare command rather
than print a plausible-looking path that will not work.

This module is deliberately tiny and imports only the standard library:
:mod:`ges2t.cli` imports it at parser-build time, and ``ges2t toggle`` must stay
cheap (ADR 0016).
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

__all__ = [
    "BARE_COMMAND",
    "CONSOLE_SCRIPT_NAME",
    "ConsoleScriptExists",
    "resolve_console_script",
]

#: The console script's name; both installers put it beside the interpreter.
CONSOLE_SCRIPT_NAME: str = "ges2t"

#: The fallback when no existing console script can be resolved. It is bindable
#: only where the script is on the launcher's ``PATH`` (a system install).
BARE_COMMAND: str = "ges2t"

#: Returns whether the candidate console script exists.
ConsoleScriptExists = Callable[[Path], bool]


def _is_file(path: Path) -> bool:
    """Return whether ``path`` is an existing regular file (the real probe)."""
    return path.is_file()


def resolve_console_script(
    *,
    executable: str | None = None,
    exists: ConsoleScriptExists | None = None,
) -> str | None:
    """Return the absolute path to this program's console script, or ``None``.

    ``executable`` defaults to :data:`sys.executable`; ``exists`` defaults to a
    ``Path.is_file`` check on the candidate. Both are injectable so tests cover
    the venv install, the system install, and the missing-script fallback
    without touching the real filesystem. The candidate keeps the interpreter's
    directory and is never symlink-resolved, so a virtualenv's ``python``
    symlink still yields that virtualenv's ``ges2t``.
    """
    interpreter = executable if executable is not None else sys.executable
    if not interpreter:
        return None
    candidate = Path(interpreter).with_name(CONSOLE_SCRIPT_NAME)
    if not candidate.is_absolute():
        candidate = candidate.absolute()
    check = exists if exists is not None else _is_file
    if not check(candidate):
        return None
    return str(candidate)
