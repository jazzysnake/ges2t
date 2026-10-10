"""Tests for locating the bindable console script (ticket 40).

The resolver must name a path that **exists**: a virtualenv's ``ges2t`` sits
beside its interpreter, a system install's sits in ``/usr/bin``, and anything
else falls back to the bare command. Most tests inject the interpreter path and
the existence probe; the symlink and real-probe tests build a throwaway tree in
``tmp_path`` so they exercise the real ``_is_file`` probe.
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


def test_does_not_follow_the_virtualenv_python_symlink(tmp_path: Path) -> None:
    """A real venv ``python`` symlink must not lead the resolver to the system.

    Built on the real filesystem with the default ``_is_file`` probe: a venv
    ``ges2t``, a ``python`` symlink pointing at ``usr/bin/python3``, and a
    *different* ``usr/bin/ges2t``. Following the symlink would name the system
    script, which is the wrong side; the resolver must keep the venv one.
    """
    venv_bin = tmp_path / "venv" / "bin"
    system_bin = tmp_path / "usr" / "bin"
    venv_bin.mkdir(parents=True)
    system_bin.mkdir(parents=True)
    system_python = system_bin / "python3"
    system_python.write_text("#!/bin/sh\n")
    system_script = system_bin / "ges2t"
    system_script.write_text("#!/bin/sh\n")
    venv_script = venv_bin / "ges2t"
    venv_script.write_text("#!/bin/sh\n")
    venv_python = venv_bin / "python"
    venv_python.symlink_to(system_python)

    assert venv_python.is_symlink()
    assert venv_python.resolve() == system_python  # the symlink really escapes

    resolved = resolve_console_script(executable=str(venv_python))

    assert resolved == str(venv_script)
    assert resolved != str(system_script)


def test_returns_none_when_a_real_candidate_does_not_exist(tmp_path: Path) -> None:
    """The real ``_is_file`` probe rejects a candidate that is not on disk.

    The other missing-candidate test injects ``exists``; this one leaves the
    default probe in place, so a resolver that skipped the check would name a
    path to a file that does not exist.
    """
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text("#!/bin/sh\n")

    assert resolve_console_script(executable=str(venv_bin / "python")) is None


def test_a_relative_interpreter_still_yields_an_absolute_path() -> None:
    resolved = resolve_console_script(executable="python3", exists=lambda path: True)

    assert resolved is not None
    assert Path(resolved).is_absolute()


def test_constants_name_the_bare_fallback() -> None:
    assert CONSOLE_SCRIPT_NAME == "ges2t"
    assert BARE_COMMAND == "ges2t"
