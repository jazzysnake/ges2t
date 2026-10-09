"""Console entry point for ges2t: the user-facing command surface (ADR 0016).

This module is the whole of the ``ges2t`` command line. It is deliberately a
thin layer:

* ``run`` and ``doctor`` do not implement anything; they lazily import their
  implementation module (``ges2t.pipeline``, ``ges2t.doctor``) and call its
  entry point. Missing modules produce an actionable internal error, never a
  traceback.
* ``toggle``, ``ptt-start``, ``ptt-stop``, ``cancel`` and ``status`` are thin
  clients over the Unix socket (ADR 0015): they connect, send one verb, and
  exit. They never import the engine, a GUI toolkit, or the pipeline.
* ``setup`` and ``models`` are standalone and never require the service.

Two rules drive the shape of the imports:

* **Lazy imports.** Importing this module must not import ``needle``, ``gi``,
  ``pystray``, ``ges2t.pipeline`` or ``ges2t.doctor``, because ``ges2t toggle``
  is bound to a key and must stay fast (ADR 0016). Everything heavy is imported
  inside the handler that needs it.
* **Exit codes are behaviour.** A compositor binding and a human both need to
  distinguish success from "the service is not running" from "the engine
  failed", so those are named constants rather than arbitrary 1s (ADR 0016).

No subcommand writes the user's configuration or compositor settings.
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys
import traceback
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ges2t import __version__
from ges2t.service import Command, CommandResponse, ServiceState
from ges2t.socket_proto import ServiceNotRunningError

if TYPE_CHECKING:
    from ges2t.config import Config
    from ges2t.model_store import ArtifactPin

__all__ = [
    "EXIT_ENGINE_FAILURE",
    "EXIT_FAILURE",
    "EXIT_INTERRUPTED",
    "EXIT_SERVICE_NOT_RUNNING",
    "EXIT_SUCCESS",
    "KDE_SHORTCUT_HINT",
    "build_parser",
    "main",
]

PROGRAM_NAME = "ges2t"
DESCRIPTION = "Fully offline dictation that types into the focused window."

#: Everything worked.
EXIT_SUCCESS = 0

#: A documented, generic failure (bad configuration, acquisition failure, ...).
EXIT_FAILURE = 1

#: The service is not running, or its socket is absent (ADR 0016).
EXIT_SERVICE_NOT_RUNNING = 2

#: The engine or the injector failed, so the service reported ``error``.
EXIT_ENGINE_FAILURE = 3

#: The command was interrupted (128 + SIGINT), matching shell convention.
EXIT_INTERRUPTED = 130

#: When set (to anything non-empty), failures print a full traceback.
DEBUG_ENVIRONMENT_VARIABLE = "GES2T_DEBUG"

#: The one true way to bind a command to a key (ADR 0004). It appears in the
#: help text of every command that is meant to be bound to a key.
KDE_SHORTCUT_HINT = (
    "Binding (KDE): System Settings -> Shortcuts -> Add -> Command or URL, then\n"
    "enter the ges2t command shown above. True press-and-hold (ptt-start/ptt-stop)\n"
    "works only when the compositor can fire separate key-press and key-release\n"
    "commands; if it cannot, bind 'ges2t toggle' instead. ges2t never edits your\n"
    "compositor settings or your config file."
)

#: Shown at the bottom of the top-level ``--help``.
_TOP_LEVEL_EPILOG = (
    "Key-bound commands ('toggle', 'ptt-start', 'ptt-stop', 'cancel') are bound in\n"
    "KDE -> System Settings -> Shortcuts -> Add -> Command or URL. Run\n"
    "'ges2t <command> --help' for the details of a single command."
)

#: The wire verbs for the thin clients, keyed by subcommand name. Using the
#: service's own enum keeps the CLI and the state machine from drifting apart.
_THIN_CLIENT_COMMANDS: Mapping[str, Command] = {
    "toggle": Command.TOGGLE,
    "ptt-start": Command.PTT_START,
    "ptt-stop": Command.PTT_STOP,
    "cancel": Command.CANCEL,
    "status": Command.STATUS,
}

#: The artifact statuses ``ges2t models`` can print.
_STATUS_PRESENT = "present"
_STATUS_MISSING = "missing"
_STATUS_WRONG_SIZE = "wrong-size"
_STATUS_VERIFIED = "verified"
_STATUS_CORRUPT = "corrupt"
_STATUS_UNSUPPORTED = "unsupported"


class CliError(Exception):
    """A user-facing failure printed as a sentence, never as a traceback.

    ``exit_code`` is the documented process status (ADR 0016) and ``hint`` is an
    optional second line naming the exact fix, because a bound hotkey failure
    has to tell the user what to do.
    """

    def __init__(
        self,
        message: str,
        *,
        exit_code: int = EXIT_FAILURE,
        hint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.hint = hint


class MissingImplementationError(CliError):
    """A subcommand's lazily imported implementation is absent from this build.

    ``run`` and ``doctor`` import their bodies from ``ges2t.pipeline`` and
    ``ges2t.doctor``; those tickets are later than this one, so the imports can
    legitimately fail and must still produce a clean message naming the target.
    """

    def __init__(self, target: str, cause: ImportError | AttributeError) -> None:
        super().__init__(
            f"{target} is not available in this build ({cause})",
            hint=(
                "this command's implementation is not part of this build yet; "
                "reinstall ges2t from a complete release"
            ),
        )


@dataclass(frozen=True)
class _ArtifactListing:
    """One row of ``ges2t models``: what is pinned, where, and how it looks."""

    name: str
    version: str
    path: Path
    pin: ArtifactPin | None
    note: str | None = None


class _ProgressPrinter:
    """A progress callback that prints a labelled percentage once per whole percent."""

    def __init__(self, label: str) -> None:
        self._label = label
        self._last_percent = -1

    def __call__(self, fraction: float) -> None:
        percent = int(round(fraction * 100))
        if percent == self._last_percent:
            return
        self._last_percent = percent
        print(f"  {self._label}: {percent}%")


Handler = Callable[[argparse.Namespace], int]


# -- argument parser ------------------------------------------------------


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    """Add the cross-cutting ``--config`` and ``--debug`` options to ``parser`` (ADR 0009).

    Both use ``SUPPRESS`` as their default: subparsers copy their whole
    namespace over the parent's, so a plain ``False``/``None`` default would
    clobber a top-level ``--debug`` passed before the subcommand name.
    """
    parser.add_argument(
        "--config",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help="configuration file to read (default: $XDG_CONFIG_HOME/ges2t/config.toml)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=argparse.SUPPRESS,
        help="print a full traceback on failure (also honours GES2T_DEBUG)",
    )


def _common_parent() -> argparse.ArgumentParser:
    """Return the parent parser that gives every subcommand ``--config``/``--debug``."""
    parent = argparse.ArgumentParser(add_help=False)
    _add_common_options(parent)
    return parent


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for the full ges2t command surface (ADR 0016)."""
    parser = argparse.ArgumentParser(
        prog=PROGRAM_NAME,
        description=DESCRIPTION,
        epilog=_TOP_LEVEL_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=__version__,
        help="print the ges2t version and exit",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="print a full traceback on failure (also honours GES2T_DEBUG)",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND", title="commands")
    common = _common_parent()

    subparsers.add_parser(
        "run",
        parents=[common],
        help="run the service and status UI (starts the long-lived process)",
        description=(
            "Run the long-lived ges2t service: it owns the engine, the audio\n"
            "source, the state machine, the tray/status UI and the Unix socket\n"
            "(ADR 0015). Exactly one may run per session."
        ),
        epilog="Autostart it from a systemd user unit; v1 does not install one for you.",
    )
    subparsers.add_parser(
        "toggle",
        parents=[common],
        help="start recording if idle, else stop and transcribe (default key binding)",
        description=(
            "Start recording if the service is idle, or stop recording and\n"
            "transcribe if it is recording. This is the recommended default key\n"
            "binding (ADR 0004)."
        ),
        epilog=KDE_SHORTCUT_HINT,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers.add_parser(
        "ptt-start",
        parents=[common],
        help="start recording (press-to-talk, key press)",
        description="Begin a press-to-talk recording.",
        epilog=KDE_SHORTCUT_HINT,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers.add_parser(
        "ptt-stop",
        parents=[common],
        help="stop recording and transcribe (press-to-talk, key release)",
        description="End a press-to-talk recording and transcribe it.",
        epilog=KDE_SHORTCUT_HINT,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers.add_parser(
        "cancel",
        parents=[common],
        help="abort the current recording without transcribing",
        description="Abort the current recording; nothing is transcribed or typed.",
        epilog=KDE_SHORTCUT_HINT,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers.add_parser(
        "status",
        parents=[common],
        help="print the service state (idle, recording, transcribing or error)",
        description="Print the service's current state to stdout and exit 0.",
    )
    subparsers.add_parser(
        "setup",
        parents=[common],
        help="download and verify the pinned model and engine (the only network use)",
        description=(
            "Acquire and verify the pinned Whistle weights and Needle 3 engine\n"
            "(ADR 0017). This is the only ges2t command that uses the network;\n"
            "everything afterwards runs offline. Safe to re-run: valid artifacts\n"
            "are left alone."
        ),
    )
    subparsers.add_parser(
        "doctor",
        parents=[common],
        help="diagnose the environment and print the recommended key bindings",
        description=(
            "Check the model and engine, ydotoold and uinput access, the tray\n"
            "backend, the configuration (including unknown keys), the service and\n"
            "socket, and pw-record. Exits non-zero when something is broken."
        ),
        epilog=KDE_SHORTCUT_HINT,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    models = subparsers.add_parser(
        "models",
        parents=[common],
        help="list the pinned models and whether they are present or verified",
        description=(
            "List the pinned Whistle weights and Needle 3 engine with their\n"
            "present/size status. Pass --verify to hash each artifact against its\n"
            "pinned sha256 (slower, but conclusive)."
        ),
    )
    models.add_argument(
        "--verify",
        action="store_true",
        help="hash every artifact against its pinned sha256 and byte size",
    )

    return parser


# -- shared plumbing ------------------------------------------------------


def _debug_enabled(args: argparse.Namespace) -> bool:
    """Return whether a traceback should be shown for this invocation."""
    return bool(getattr(args, "debug", False)) or bool(os.environ.get(DEBUG_ENVIRONMENT_VARIABLE))


def _print_cli_error(error: CliError) -> None:
    """Print an actionable failure sentence and, when present, the fix."""
    print(f"{PROGRAM_NAME}: {error}", file=sys.stderr)
    if error.hint is not None:
        print(f"  hint: {error.hint}", file=sys.stderr)


def _print_unexpected(exc: Exception) -> None:
    """Print an unexpected failure without leaking a traceback by default."""
    print(f"{PROGRAM_NAME}: unexpected failure: {exc}", file=sys.stderr)
    print(
        "  re-run with '--debug' for the full traceback and report it as a bug",
        file=sys.stderr,
    )


def _send_command(command: str) -> CommandResponse:
    """Send one command to the running service. This is the test seam for the client.

    The real socket client is imported here, not at module import time, so that
    importing :mod:`ges2t.cli` stays cheap for every subcommand.
    """
    from ges2t.socket_proto import default_socket_path, send_command

    return send_command(default_socket_path(), command)


def _send_request(command: Command) -> CommandResponse:
    """Send ``command`` and translate an absent service into exit code 2 (ADR 0016)."""
    try:
        return _send_command(command.value)
    except ServiceNotRunningError as exc:
        raise CliError(str(exc), exit_code=EXIT_SERVICE_NOT_RUNNING) from exc


def _load_entry_point(module_name: str, attribute: str) -> Callable[[Config], int]:
    """Lazily import ``module_name.attribute`` or raise a clean internal error.

    The target is deliberately a string so that nothing is imported until the
    subcommand that needs it actually runs (ADR 0016), and so a build missing
    the module names itself instead of raising a bare ``ImportError``.
    """
    target = f"{module_name}.{attribute}"
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise MissingImplementationError(target, exc) from exc
    try:
        function = getattr(module, attribute)
    except AttributeError as exc:
        raise MissingImplementationError(target, exc) from exc
    return cast("Callable[[Config], int]", function)


def _load_config(args: argparse.Namespace) -> Config:
    """Resolve ``--config`` and load the configuration, turning errors into a sentence."""
    from ges2t.config import ConfigError, load_config, resolve_config_path

    resolved = resolve_config_path(getattr(args, "config", None))
    try:
        return load_config(resolved).config
    except ConfigError as exc:
        raise CliError(
            f"invalid configuration: {exc}",
            hint="fix the file named above, or pass a different one with '--config <path>'",
        ) from exc


# -- handlers -------------------------------------------------------------


def _handle_run(args: argparse.Namespace) -> int:
    """Run the long-lived service (ticket: dictation-pipeline-batch, ADR 0015)."""
    run_service = _load_entry_point("ges2t.pipeline", "run_service")
    config = _load_config(args)
    return run_service(config)


def _handle_doctor(args: argparse.Namespace) -> int:
    """Run the environment diagnosis (ticket: doctor-command, ADR 0016)."""
    run_doctor = _load_entry_point("ges2t.doctor", "run_doctor")
    config = _load_config(args)
    return run_doctor(config)


def _handle_thin_client(args: argparse.Namespace) -> int:
    """Send one verb to the service and map its response to a documented exit code."""
    command = _THIN_CLIENT_COMMANDS[args.command]
    response = _send_request(command)
    if response.ok:
        return EXIT_SUCCESS
    message = response.message or f"the service refused the {command.value!r} command"
    if response.state is ServiceState.ERROR:
        raise CliError(message, exit_code=EXIT_ENGINE_FAILURE)
    raise CliError(message)


def _handle_status(args: argparse.Namespace) -> int:
    """Print the service's current state; ``error`` is a state, not a failure."""
    response = _send_request(Command.STATUS)
    print(response.state.value)
    return EXIT_SUCCESS


def _handle_setup(args: argparse.Namespace) -> int:
    """Acquire and verify the pinned model and engine (ADR 0017)."""
    from ges2t import model_store

    print("Acquiring the pinned Whistle weights and Needle 3 engine.")
    print("This is the only ges2t command that uses the network; the rest runs offline.")
    try:
        model = model_store.ensure_model(
            progress=_ProgressPrinter(model_store.MODEL_PIN.name)
        )
        engine = model_store.ensure_engine(
            progress=_ProgressPrinter(model_store.ENGINE_PIN.name)
        )
    except (model_store.ModelStoreError, OSError) as exc:
        raise CliError(
            f"setup failed: {exc}",
            hint=(
                "check your network connection and the paths above, then re-run 'ges2t setup'"
            ),
        ) from exc
    print(f"Model:  {model}")
    print(f"Engine: {engine}")
    print("Setup complete.")
    return EXIT_SUCCESS


def _handle_models(args: argparse.Namespace) -> int:
    """List the pinned artifacts with their present/verified status (ADR 0017)."""
    from ges2t import model_store

    verify = bool(getattr(args, "verify", False))
    listings: list[_ArtifactListing] = [
        _ArtifactListing(
            name=model_store.MODEL_PIN.name,
            version=model_store.MODEL_VERSION,
            path=model_store.model_path(),
            pin=model_store.MODEL_PIN,
        )
    ]
    try:
        engine_pin = model_store.engine_pin()
    except model_store.UnsupportedPlatformError as exc:
        listings.append(
            _ArtifactListing(
                name="libneedle.so",
                version=model_store.ENGINE_VERSION,
                path=model_store.engine_path(),
                pin=None,
                note=str(exc),
            )
        )
    else:
        listings.append(
            _ArtifactListing(
                name=engine_pin.name,
                version=model_store.ENGINE_VERSION,
                path=model_store.engine_path(),
                pin=engine_pin,
            )
        )

    print(f"{'artifact':<14} {'version':<8} {'status':<11} path")
    failures = 0
    for listing in listings:
        pin = listing.pin
        if pin is None:
            status = _STATUS_UNSUPPORTED
        elif not listing.path.is_file():
            status = _STATUS_MISSING
        elif listing.path.stat().st_size != pin.size:
            status = _STATUS_WRONG_SIZE
        elif verify:
            status = (
                _STATUS_VERIFIED
                if model_store.verify(listing.path, pin)
                else _STATUS_CORRUPT
            )
        else:
            status = _STATUS_PRESENT
        print(f"{listing.name:<14} {listing.version:<8} {status:<11} {listing.path}")
        if listing.note is not None:
            print(f"  note: {listing.note}")
        if verify and status != _STATUS_VERIFIED:
            failures += 1

    if verify and failures:
        sys.stdout.flush()
        print(
            f"{PROGRAM_NAME}: {failures} artifact(s) did not verify; run 'ges2t setup'",
            file=sys.stderr,
        )
        return EXIT_FAILURE
    return EXIT_SUCCESS


_HANDLERS: Mapping[str, Handler] = {
    "run": _handle_run,
    "toggle": _handle_thin_client,
    "ptt-start": _handle_thin_client,
    "ptt-stop": _handle_thin_client,
    "cancel": _handle_thin_client,
    "status": _handle_status,
    "setup": _handle_setup,
    "doctor": _handle_doctor,
    "models": _handle_models,
}


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ges2t command line and return the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    command = getattr(args, "command", None)
    if command is None:
        parser.print_help()
        return EXIT_SUCCESS

    handler = _HANDLERS.get(command)
    if handler is None:
        parser.error(f"unknown command: {command!r}")

    debug = _debug_enabled(args)
    try:
        return handler(args)
    except CliError as exc:
        _print_cli_error(exc)
        if debug:
            traceback.print_exc()
        return exc.exit_code
    except KeyboardInterrupt:
        print(f"{PROGRAM_NAME}: interrupted", file=sys.stderr)
        return EXIT_INTERRUPTED
    except Exception as exc:  # top-level guard: report, never leak a traceback
        if debug:
            traceback.print_exc()
        else:
            _print_unexpected(exc)
        return EXIT_FAILURE
