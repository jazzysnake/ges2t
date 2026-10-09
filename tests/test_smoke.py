"""Smoke tests for the scaffold: the installed package and its console script."""

from __future__ import annotations

import importlib.metadata

import pytest

import ges2t
from ges2t.cli import main


def test_version_constant_is_importable_from_installed_package() -> None:
    assert ges2t.__version__ == "0.1.0"


def test_version_matches_installed_distribution_metadata() -> None:
    assert importlib.metadata.version("ges2t") == ges2t.__version__


def test_version_flag_prints_version_and_exits_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == ges2t.__version__
