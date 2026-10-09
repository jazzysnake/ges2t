"""Console entry point for ges2t.

This module is deliberately a minimal stub: it handles only ``--version`` and
``--help``. The full command surface (``run``, ``toggle``, ``setup``, ``doctor``,
...) belongs to the ``cli-surface-and-commands`` ticket (ADR 0016), so no
subcommand is defined here.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from ges2t import __version__

PROGRAM_NAME = "ges2t"
DESCRIPTION = "Fully offline dictation that types into the focused window."


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for the ges2t command line."""
    parser = argparse.ArgumentParser(prog=PROGRAM_NAME, description=DESCRIPTION)
    parser.add_argument(
        "--version",
        action="version",
        version=__version__,
        help="print the ges2t version and exit",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ges2t command line and return the process exit code."""
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0
