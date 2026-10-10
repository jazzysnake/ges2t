"""Tests for locating the bindable console script (ticket 40).

The resolver must name a path that **exists**: a virtualenv's ``ges2t`` sits
beside its interpreter, a system install's sits in ``/usr/bin``, and anything
else falls back to the bare command. Every test injects the interpreter path and
the existence probe, so none of them depends on where the test itself runs.
"""

from __future__ import annotations

from pathlib import Path

from ges2t.console_script import (
    BARE_COMMAND,
    CONSOLE_SCRIPT_NAME,
    ConsoleScriptExists,
    resolve_console_script,
)


def _only(expected: str) -> ConsoleScriptExists:
    """Return an ``exists`` probe that accepts exactly ``expected``."""
    target = Path(expected)

    def exists(path: Path) -> bool:
        return path == target

    return exists


def test_resolves_the_console_script_beside_a_virtualenv_interpreter() -> None:
    resolved = resolve_console_script(
        executable="/home/u/proj/.venv/bin/python",
        exists=_only("/home/u/proj/.venv/bin/ges2t"),
    )

    assert resolved == "/home/u/proj/.venv/bin/ges2t"


def test_resolves_a_system_install_script_beside_usr_bin_python() -> None:
    resolved = resolve_console_script(
        executable="/usr/bin/python3",
        exists=_only("/usr/bin/ges2t"),
    )

    assert resolved == "/usr/bin/ges2t"


def test_returns_none_when_the_candidate_does_not_exist() -> None:
    assert (
        resolve_console_script(
            executable="/home/u/proj/.venv/bin/python", exists=lambda path: False
        )
        is None
    )


def test_returns_none_for_an_empty_interpreter() -> None:
    assert resolve_console_script(executable="", exists=lambda path: True) is None


def test_does_not_follow_the_virtualenv_python_symlink() -> None:
    """The venv's ``python`` points at the system one; ``ges2t`` must not follow.

    ``.venv/bin/python`` is a symlink to ``/usr/bin/python3``; resolving it
    would name ``/usr/bin/ges2t`` -- the wrong script. The candidate must stay
    in ``.venv/bin``.
    """
    seen: list[Path] = []

    def exists(path: Path) -> bool:
        seen.append(path)
        return True

    resolved = resolve_console_script(
        executable="/home/u/proj/.venv/bin/python", exists=exists
    )

    assert resolved == "/home/u/proj/.venv/bin/ges2t"
    assert seen == [Path("/home/u/proj/.venv/bin/ges2t")]


def test_a_relative_interpreter_still_yields_an_absolute_path() -> None:
    resolved = resolve_console_script(executable="python3", exists=lambda path: True)

    assert resolved is not None
    assert Path(resolved).is_absolute()


def test_constants_name_the_bare_fallback() -> None:
    assert CONSOLE_SCRIPT_NAME == "ges2t"
    assert BARE_COMMAND == "ges2t"
