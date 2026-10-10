"""``ges2t doctor``: diagnose every prerequisite so the user learns why ges2t is broken.

This module is the single place that answers "why isn't it working?" (ADR 0016),
and it is the project's main defence against silent breakage. ges2t has shipped
bugs that passed every test -- a hardcoded US keymap that corrupted every non-US
layout, and a tray that silently fell back to an invisible backend -- so doctor
is where those failure modes get *named*, before dictation produces wrong output.

Design rules, all of them load-bearing:

* **Safe at any time.** Doctor performs no network I/O, writes nothing to disk,
  and never mutates config or system state, so it is safe to run while the
  service is running (ADR 0016). Every probe is an *inspection*: a socket is
  connected to, a device's writability is checked, a binary is looked up on
  ``PATH``, ``pw-dump`` is read. Nothing is typed, no daemon command is sent, no
  service is changed, and no tray icon is created.
* **No side effects in imports.** ``gi``, ``pystray`` and ``needle`` are imported
  only inside the function that needs them, so doctor stays usable in a bare
  environment (ADR 0014). The tray check calls
  :func:`~ges2t.ui.tray.assert_appindicator_backend`, which imports modules only
  and never constructs an icon.
* **One result object per check.** Every check returns a frozen
  :class:`CheckResult` with a status of ``OK``/``WARN``/``FAIL``; a non-``OK``
  result must carry a non-empty, actionable fix line (ADR 0019).
* **Both config errors are unified here.** The generic loader raises
  :class:`~ges2t.config.ConfigError` (naming a file path) and the selected
  engine's adapter raises :class:`~ges2t.transcribers.whistle_config.WhistleConfigError`
  (naming a block key). They are deliberately distinct (ADR 0009) and are not
  given a shared base class; doctor is where they become one report.
* **A missing artifact is never a download.** Model/engine absence is a ``FAIL``
  whose fix names ``ges2t setup``, never an automatic acquisition (ADR 0017/0018).

**Every future ticket that adds a system prerequisite must add a check here.**
That is a standing instruction from ADR 0016 and a checklist item in the epic;
the checks below are not the last word.

Doctor is invoked through :func:`run_doctor`, which the CLI calls lazily with the
already-loaded :class:`~ges2t.config.Config`. It additionally takes a
:class:`DoctorOptions` carrying the resolved config path (so the generic
``ConfigError`` can be caught and reported rather than pre-empted by the CLI) and
the ``--verify`` flag. ``run_doctor(config)`` still works with defaults.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import os
import platform as _platform
import shutil
import socket
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import ModuleType

from ges2t import model_store
from ges2t.config import (
    Config,
    ConfigError,
    ConfigLoadResult,
    default_config_path,
    load_config,
)
from ges2t.console_script import BARE_COMMAND, resolve_console_script
from ges2t.injectors.keymap import XkbError, XkbKeymap
from ges2t.injectors.layout import (
    AUTO_LAYOUT,
    ClipboardConfig,
    ClipboardConfigResult,
    InjectionConfigError,
    LayoutResolutionError,
    LayoutSource,
    ResolvedLayout,
    YdotoolConfig,
    YdotoolConfigResult,
    resolve_layout,
)
from ges2t.model_store import ArtifactPin, UnsupportedPlatformError
from ges2t.socket_proto import default_socket_path, is_service_listening
from ges2t.transcribers.whistle_config import WhistleConfig, WhistleConfigError
from ges2t.ui.status import TrayFailure

__all__ = [
    "EXIT_FAILURE",
    "EXIT_SUCCESS",
    "CheckResult",
    "CheckStatus",
    "DoctorOptions",
    "DoctorProbes",
    "DoctorReport",
    "ArtifactTarget",
    "build_report",
    "check_architecture",
    "check_audio_device",
    "check_cactus_needle_importable",
    "check_clipboard_backend",
    "check_engine_present",
    "check_hotkey_bindings",
    "check_keyboard_layout",
    "check_model_present",
    "check_model_verified",
    "check_package_imports",
    "check_pipewire_session",
    "check_pw_record",
    "check_service_socket",
    "check_tray_backend",
    "check_tray_failures",
    "check_uinput",
    "check_versions",
    "check_ydotoold",
    "diagnose_config",
    "diagnose_engine_config",
    "diagnose_injection_config",
    "hotkey_fix",
    "kde_binding_section",
    "render_report",
    "run_doctor",
]

#: No ``FAIL``: the environment is usable (warnings are allowed).
EXIT_SUCCESS: int = 0

#: At least one check failed. The exact value is intentionally ``1`` and matches
#: the CLI's generic failure code (ADR 0016); the CLI returns it unchanged.
EXIT_FAILURE: int = 1

#: The one place a binary each probe looks up lives; kept as constants so a test
#: can name them without spelling the strings twice.
PW_RECORD_BINARY: str = "pw-record"
PW_DUMP_BINARY: str = "pw-dump"
PW_CLI_BINARY: str = "pw-cli"
WL_COPY_BINARY: str = "wl-copy"
YDOTOOL_SOCKET_NAME: str = ".ydotool_socket"

#: The uinput device ``ydotool`` writes to (ADR 0003).
UINPUT_PATH: Path = Path("/dev/uinput")

#: Timeout for the read-only PipeWire queries (``pw-dump``/``pw-cli``). Both are
#: local and fast; the bound exists so a wedged daemon cannot hang doctor.
PIPEWIRE_QUERY_TIMEOUT_SECONDS: float = 5.0

#: Bounded connect probe for a Unix socket (the ydotoold daemon, the service).
SOCKET_PROBE_TIMEOUT_SECONDS: float = 0.2

#: The hotkey is never grabbed by ges2t (ADR 0004).
HOTKEY_SUMMARY: str = "ges2t does not grab the hotkey itself; bind the command in KDE"

#: The bindable subcommands in recommendation order, each with its one-line
#: purpose. The command form is built from the resolved console script so the
#: printed advice is literally bindable (ticket 40).
_BINDABLE_COMMANDS: tuple[tuple[str, str], ...] = (
    ("toggle", "start or stop with one key (recommended default)"),
    ("ptt-start", "press-to-talk: bind to the key press"),
    ("ptt-stop", "press-to-talk: bind to the key release"),
    ("cancel", "abort a recording without transcribing"),
)


def hotkey_fix(*, console_script: str | None = None) -> str:
    """Return the always-WARN fix line: bind the *resolved* console script.

    ``console_script`` is the absolute path that
    :func:`ges2t.console_script.resolve_console_script` returned, or ``None``
    when it could not be resolved to an existing file; ``None`` (and the empty
    string) falls back to the bare command. A path that does not exist is never
    recommended, because a plausible-looking wrong path is worse than the
    honest generic one (ticket 40).
    """
    executable = console_script if console_script else BARE_COMMAND
    return (
        "System Settings -> Shortcuts -> Add -> Command or URL: bind "
        f"'{executable} toggle' (recommended), or '{executable} ptt-start' and "
        f"'{executable} ptt-stop', plus '{executable} cancel'"
    )


def kde_binding_section(*, console_script: str | None = None) -> str:
    """Render the copy-paste bindings, each as the resolved absolute command.

    Printed once at the end of every report (ADR 0004, ADR 0016). When the
    console script cannot be resolved, the bare command is named instead
    (ticket 40).
    """
    executable = console_script if console_script else BARE_COMMAND
    commands = tuple(f"{executable} {name}" for name, _ in _BINDABLE_COMMANDS)
    width = max(len(command) for command in commands)
    lines = [
        "Recommended KDE key bindings "
        "(System Settings -> Shortcuts -> Add -> Command or URL):",
        *(
            f"  {command:<{width}}  {purpose}"
            for command, (_, purpose) in zip(commands, _BINDABLE_COMMANDS)
        ),
        "ges2t never edits your compositor settings or your config file (ADR 0004).",
    ]
    return "\n".join(lines)


#: The package's own modules; importing one of these failing is a broken install.
_OWN_MODULES: tuple[str, ...] = (
    "ges2t",
    "ges2t.cli",
    "ges2t.config",
    "ges2t.console_script",
    "ges2t.interfaces",
    "ges2t.model_store",
    "ges2t.socket_proto",
    "ges2t.service",
    "ges2t.streaming",
    "ges2t.streaming_session",
    "ges2t.audio.pw_record",
    "ges2t.injectors.keymap",
    "ges2t.injectors.layout",
    "ges2t.injectors.ydotool",
    "ges2t.transcribers.whistle_config",
    "ges2t.ui.status",
    "ges2t.ui.tray",
    "ges2t.pipeline",
)

#: The keys a PipeWire object exposes that name a node. A configured
#: ``audio.device`` may be a node name, a description, a nickname, or a serial,
#: so all of them are accepted (ADR 0008's amendment).
_PW_NODE_NAME_KEYS: tuple[str, ...] = (
    "node.name",
    "node.description",
    "node.nick",
    "device.name",
    "device.description",
)


# --- result objects (ADR 0019: classes, not tuples) ---------------------------


class CheckStatus(StrEnum):
    """The three outcomes a doctor check can have."""

    OK = "OK"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass(frozen=True)
class CheckResult:
    """One line of the report: a status, a one-line summary, and a fix when needed.

    A non-``OK`` result **must** carry a non-empty fix naming an exact command or
    setting; :meth:`__post_init__` refuses the alternative, so a failure can
    never be printed without telling the user what to do (ticket DoD).
    """

    name: str
    status: CheckStatus
    summary: str
    fix: str | None = None

    def __post_init__(self) -> None:
        if self.status is not CheckStatus.OK:
            if self.fix is None or self.fix.strip() == "":
                raise ValueError(
                    f"check {self.name!r} is {self.status} and must carry a fix line"
                )


def _ok(name: str, summary: str) -> CheckResult:
    """Build an ``OK`` result (no fix line required)."""
    return CheckResult(name=name, status=CheckStatus.OK, summary=summary)


def _warn(name: str, summary: str, fix: str) -> CheckResult:
    """Build a ``WARN`` result with its fix line."""
    return CheckResult(name=name, status=CheckStatus.WARN, summary=summary, fix=fix)


def _fail(name: str, summary: str, fix: str) -> CheckResult:
    """Build a ``FAIL`` result with its fix line."""
    return CheckResult(name=name, status=CheckStatus.FAIL, summary=summary, fix=fix)


@dataclass(frozen=True)
class DoctorReport:
    """Every check's result, in report order, plus the derived exit code."""

    results: tuple[CheckResult, ...]
    #: The resolved absolute path to this process's console script, or ``None``
    #: for the bare-command fallback. It makes the printed bindings bindable.
    console_script: str | None = None

    @property
    def failures(self) -> tuple[CheckResult, ...]:
        """The ``FAIL`` results, re-listed at the end of the report."""
        return tuple(result for result in self.results if result.status is CheckStatus.FAIL)

    @property
    def warnings(self) -> tuple[CheckResult, ...]:
        """The ``WARN`` results (allowed; they do not change the exit code)."""
        return tuple(result for result in self.results if result.status is CheckStatus.WARN)

    @property
    def successes(self) -> tuple[CheckResult, ...]:
        """The ``OK`` results."""
        return tuple(result for result in self.results if result.status is CheckStatus.OK)

    @property
    def exit_code(self) -> int:
        """``0`` when nothing failed, non-zero as soon as one check fails."""
        return EXIT_FAILURE if self.failures else EXIT_SUCCESS


@dataclass(frozen=True)
class ArtifactTarget:
    """Where one pinned artifact is expected, and what its size should be.

    ``pin`` is ``None`` when there is no ges2t pin for this target -- a
    user-supplied ``transcriber.model_path`` or a platform with no engine
    artifact -- in which case only existence can be checked.
    """

    path: Path
    expected_size: int | None
    pin: ArtifactPin | None


# --- probe types (ADR 0019: interface-typed signatures) -----------------------

#: Imports a module by name; the real default is :func:`importlib.import_module`.
PackageImporter = Callable[[str], ModuleType]
#: Reads an installed distribution's version.
VersionReader = Callable[[str], str]
#: Returns the PipeWire node names/serials, or ``None`` when they cannot be read.
NodeListProbe = Callable[[], frozenset[str] | None]
#: Returns whether a PipeWire session is reachable (read-only, no recording).
SessionProbe = Callable[[], bool]
#: Resolves a command name on ``PATH`` to its path, or ``None``.
BinaryLookup = Callable[[str], str | None]
#: Returns whether the Unix socket at the path accepts a connection.
SocketProbe = Callable[[Path], bool]
#: Calls the tray backend assertion; must import modules only, never build an icon.
TrayAssertion = Callable[[], None]
#: Returns the tray's recorded runtime failures, or ``None`` when there are none.
TrayFailureProbe = Callable[[], TrayFailure | None]
#: Resolves a layout by the ADR 0020 order, with an optional configured value.
LayoutResolver = Callable[[str, str], ResolvedLayout]
#: Compiles a resolved layout, proving libxkbcommon and the XKB data work.
LayoutCompiler = Callable[[ResolvedLayout], None]
#: Resolves this process's own console script to an absolute path, or ``None``.
ConsoleScriptResolver = Callable[[], str | None]
#: A config loader; injected so tests never read the user's real file.
ConfigLoader = Callable[[], ConfigLoadResult]
#: A callable returning a :class:`CheckResult`.
Check = Callable[[], CheckResult]


def _read_distribution_version(name: str) -> str:
    """Return the installed distribution's version (no import of its modules)."""
    return importlib.metadata.version(name)


def _unix_socket_reachable(path: Path, *, socket_type: int = socket.SOCK_STREAM) -> bool:
    """Return whether ``path`` is a Unix socket of ``socket_type`` that accepts a connection.

    A connection is opened and immediately closed; no protocol message is sent.
    A missing file, a plain file, a refusal, or a socket of the wrong type all
    mean "not reachable". The socket type matters: the ges2t service is a
    ``SOCK_STREAM`` socket while ``ydotoold`` is a ``SOCK_DGRAM`` one, and
    connecting to the wrong type fails with ``Protocol wrong type for socket``.
    """
    try:
        with socket.socket(socket.AF_UNIX, socket_type) as probe:
            probe.settimeout(SOCKET_PROBE_TIMEOUT_SECONDS)
            probe.connect(str(path))
    except OSError:
        return False
    return True


def _ydotoold_reachable(path: Path) -> bool:
    """Return whether the ``ydotoold`` daemon at ``path`` accepts a connection.

    ``ydotoold`` listens on a ``SOCK_DGRAM`` Unix socket, so a ``SOCK_STREAM``
    probe would report a running daemon as unreachable; this is the probe the
    evidence forced (measured on the reference machine).
    """
    return _unix_socket_reachable(path, socket_type=socket.SOCK_DGRAM)


def _ydotoold_socket_path() -> Path:
    """Return the daemon socket: ``$YDOTOOL_SOCKET`` or the runtime-dir default.

    ``ydotool`` itself reads ``YDOTOOL_SOCKET``, so honouring it means a daemon
    started by hand on a non-default socket is recognised (ADR 0003).
    """
    override = os.environ.get("YDOTOOL_SOCKET")
    if override:
        return Path(override)
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / YDOTOOL_SOCKET_NAME
    return Path("/run/user") / str(os.getuid()) / YDOTOOL_SOCKET_NAME


def _uinput_writable(path: Path) -> bool:
    """Return whether this process may write ``path`` (``/dev/uinput``).

    ``os.access`` is a pure inspection of the permission bits and ACLs; nothing
    is opened, so no virtual input device is ever created.
    """
    return path.exists() and os.access(path, os.W_OK)


def _pipewire_session_reachable() -> bool:
    """Return whether a read-only ``pw-cli info 0`` reaches a PipeWire session.

    This is the mitigation for ticket 6's ~50 ms race, where ``stop()`` right
    after ``start()`` can swallow a genuine "no session" failure. It is a
    standalone probe, not a recording, so it neither needs nor opens a device.
    """
    try:
        completed = subprocess.run(
            [PW_CLI_BINARY, "info", "0"],
            capture_output=True,
            check=False,
            timeout=PIPEWIRE_QUERY_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _pw_dump_node_names() -> frozenset[str] | None:
    """Read PipeWire's node names, descriptions and serials, or ``None``.

    ``pw-dump`` prints a JSON array of session objects; a device is "present" if
    the configured value equals any node name, description, nickname, object id
    or ``object.serial``. A missing binary, a non-zero exit, a timeout or
    unparseable output all return ``None`` so the caller can distinguish "the
    device is absent" from "the session could not be queried" (ADR 0008).
    """
    try:
        completed = subprocess.run(
            [PW_DUMP_BINARY],
            capture_output=True,
            check=False,
            timeout=PIPEWIRE_QUERY_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    try:
        objects = json.loads(completed.stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None
    if not isinstance(objects, list):
        return None

    names: set[str] = set()
    for obj in objects:
        if not isinstance(obj, dict):
            continue
        object_id = obj.get("id")
        if isinstance(object_id, int):
            names.add(str(object_id))
        info = obj.get("info")
        props = info.get("props") if isinstance(info, dict) else None
        if not isinstance(props, dict):
            continue
        for key in _PW_NODE_NAME_KEYS:
            value = props.get(key)
            if isinstance(value, str):
                names.add(value)
        serial = props.get("object.serial")
        if isinstance(serial, (int, str)):
            names.add(str(serial))
    return frozenset(names)


def _defuse_backend_network() -> None:
    """Set ges2t's telemetry/offline overrides before the backend is imported.

    Process-local only: nothing is written to disk. Doctor imports ``needle`` to
    prove it is importable, and the same environment ``WhistleTranscriber.load``
    applies keeps a future package version's import-time telemetry from firing;
    ``HF_HUB_OFFLINE=1`` makes a stray hub access a loud local error instead of a
    download (ADR 0007, ADR 0018).
    """
    os.environ.update(model_store.env_overrides())


def _default_tray_assertion() -> None:
    """Import and run the AppIndicator assertion. It imports modules, no icon.

    Imported here, not at module load, so importing :mod:`ges2t.doctor` never
    touches ``gi`` or ``pystray`` (ADR 0014).
    """
    from ges2t.ui.tray import assert_appindicator_backend

    assert_appindicator_backend()


def _default_layout_resolver(layout: str, variant: str) -> ResolvedLayout:
    """Resolve the configured layout by the ADR 0020 order."""
    return resolve_layout(configured_layout=layout, configured_variant=variant)


def _compile_resolved_layout(resolved: ResolvedLayout) -> None:
    """Compile a resolved layout, proving libxkbcommon and the XKB data work.

    Raises :class:`~ges2t.injectors.keymap.XkbError` on any failure; the caller
    turns it into a ``FAIL`` with an install hint. The keymap is closed
    immediately, so doctor leaks nothing.
    """
    if resolved.source is LayoutSource.LIVE_KEYMAP and resolved.keymap_text:
        keymap = XkbKeymap.from_string(resolved.keymap_text)
    elif resolved.layout is not None:
        keymap = XkbKeymap.from_names(resolved.layout, resolved.variant or "")
    else:
        raise XkbError(
            f"the {resolved.source.value} resolution carries neither a keymap "
            "string nor layout names, so no keymap can be compiled"
        )
    keymap.close()


@dataclass(frozen=True)
class DoctorProbes:
    """Every external probe doctor uses, as one injectable bundle (ADR 0014).

    The defaults are the real inspectors; tests build a ``DoctorProbes`` with
    fakes so no real session, daemon, display, subprocess or data directory is
    touched. Passing this to :class:`DoctorOptions` is how an integration test
    drives the whole report without monkeypatching.
    """

    importer: PackageImporter = importlib.import_module
    version_reader: VersionReader = _read_distribution_version
    node_probe: NodeListProbe = _pw_dump_node_names
    session_probe: SessionProbe = _pipewire_session_reachable
    binary_lookup: BinaryLookup = shutil.which
    ydotoold_probe: SocketProbe = _ydotoold_reachable
    uinput_probe: SocketProbe = _uinput_writable
    tray_assertion: TrayAssertion = _default_tray_assertion
    layout_resolver: LayoutResolver = _default_layout_resolver
    layout_compiler: LayoutCompiler = _compile_resolved_layout
    console_script: ConsoleScriptResolver = resolve_console_script
    listening_probe: SocketProbe = is_service_listening
    tray_failure_probe: TrayFailureProbe | None = None


@dataclass(frozen=True)
class DoctorOptions:
    """Everything :func:`run_doctor` needs beyond the loaded config.

    ``config_path`` is where the generic configuration is (re-)loaded from, so
    a :class:`~ges2t.config.ConfigError` becomes a report line instead of being
    pre-empted by the CLI. ``verify`` forces the full sha256 model check.
    ``probes`` is the test seam; ``None`` means the real inspectors.
    """

    config_path: Path
    verify: bool = False
    probes: DoctorProbes | None = None


# --- individual checks --------------------------------------------------------


def check_package_imports(*, importer: PackageImporter | None = None) -> CheckResult:
    """Every one of ges2t's own modules must import; a failure is a broken install."""
    name = "Package imports"
    do_import = importer if importer is not None else importlib.import_module
    failed: list[str] = []
    for module_name in _OWN_MODULES:
        try:
            do_import(module_name)
        except Exception as exc:  # a broken module must not abort the diagnosis
            failed.append(f"{module_name} ({type(exc).__name__}: {exc})")
    if failed:
        return _fail(
            name,
            "could not import: " + "; ".join(failed),
            "reinstall ges2t with 'uv sync' (or 'pip install --force-reinstall ges2t') "
            "and re-run 'ges2t doctor'",
        )
    return _ok(
        name,
        f"all {len(_OWN_MODULES)} package modules import (Python {_platform.python_version()})",
    )


def check_architecture(
    *,
    platform_key: str = model_store.PLATFORM_KEY,
    supported_platforms: frozenset[str] = model_store.SUPPORTED_ENGINE_PLATFORMS,
) -> CheckResult:
    """The running platform must have a pinned engine artifact (ADR 0017)."""
    name = "Architecture"
    if platform_key not in supported_platforms:
        pinned = ", ".join(sorted(supported_platforms))
        return _fail(
            name,
            f"no engine artifact is pinned for this platform ({platform_key})",
            f"a pinned engine exists only for: {pinned}; run on one of them or "
            "extend SUPPORTED_ENGINE_PLATFORMS with a matching pin",
        )
    return _ok(name, f"{platform_key} has a pinned engine artifact")


def _model_target(config: Config) -> ArtifactTarget:
    """Resolve the weights file this run will load: the override or the pinned path."""
    override = config.transcriber.model_path
    if override:
        return ArtifactTarget(path=Path(override), expected_size=None, pin=None)
    return ArtifactTarget(
        path=model_store.model_path(),
        expected_size=model_store.MODEL_PIN.size,
        pin=model_store.MODEL_PIN,
    )


def _engine_target() -> ArtifactTarget:
    """Resolve the engine path and its pin, tolerating an unsupported platform."""
    try:
        pin = model_store.engine_pin()
    except UnsupportedPlatformError:
        return ArtifactTarget(path=model_store.engine_path(), expected_size=None, pin=None)
    return ArtifactTarget(path=model_store.engine_path(), expected_size=pin.size, pin=pin)


def _check_artifact_present(
    name: str, filename: str, target: ArtifactTarget, *, setup_fix: str
) -> CheckResult:
    """Shared body of the model/engine presence checks (existence + byte size)."""
    if not target.path.is_file():
        return _fail(name, f"{filename} is missing at {target.path}", setup_fix)
    actual_size = target.path.stat().st_size
    if target.expected_size is not None and actual_size != target.expected_size:
        return _fail(
            name,
            f"{filename} at {target.path} is {actual_size} bytes, expected "
            f"{target.expected_size} bytes",
            setup_fix,
        )
    return _ok(name, f"{filename} is present at {target.path} ({actual_size} bytes)")


def check_model_present(
    config: Config, *, target: ArtifactTarget | None = None
) -> CheckResult:
    """The Whistle weights must be present and the pinned size (ADR 0017).

    Absence is a ``FAIL`` naming ``ges2t setup``, never an automatic download
    (ADR 0017/0018). A configured ``transcriber.model_path`` has no pin of ours,
    so only its existence is checked.
    """
    resolved = target if target is not None else _model_target(config)
    return _check_artifact_present(
        "Model present",
        "whistle.cact",
        resolved,
        setup_fix="run 'ges2t setup' to download and verify the pinned model",
    )


def check_model_verified(
    config: Config, *, verify: bool = False, target: ArtifactTarget | None = None
) -> CheckResult:
    """Hash the model against its pin when ``--verify`` is set (ADR 0017).

    Without ``--verify`` this reports ``OK`` with an explicit "not requested"
    summary: doctor still runs cheaply by default.
    """
    name = "Model verified"
    if not verify:
        return _ok(
            name,
            "not requested; pass --verify to hash the model against its pinned sha256",
        )
    resolved = target if target is not None else _model_target(config)
    if resolved.pin is None:
        return _ok(
            name,
            f"skipped: {resolved.path} is a configured model_path with no ges2t pin "
            "to verify against",
        )
    if not resolved.path.is_file():
        return _fail(
            name,
            f"cannot verify: {resolved.path} is missing",
            "run 'ges2t setup' to download the pinned model",
        )
    if not model_store.verify(resolved.path, resolved.pin):
        return _fail(
            name,
            f"the model at {resolved.path} does not match the pinned sha256 "
            f"{resolved.pin.sha256}",
            "run 'ges2t setup' to re-download the pinned model",
        )
    return _ok(name, f"the model at {resolved.path} matches the pinned sha256")


def check_engine_present(*, target: ArtifactTarget | None = None) -> CheckResult:
    """The Needle 3 engine must be present and the pinned size (ADR 0017)."""
    resolved = target if target is not None else _engine_target()
    if resolved.pin is None:
        pinned = ", ".join(sorted(model_store.SUPPORTED_ENGINE_PLATFORMS))
        return _fail(
            "Engine present",
            f"no engine artifact is pinned for this platform ({model_store.PLATFORM_KEY})",
            f"a pinned engine exists only for: {pinned}",
        )
    return _check_artifact_present(
        "Engine present",
        "libneedle.so",
        resolved,
        setup_fix="run 'ges2t setup' to download and verify the pinned engine",
    )


def check_cactus_needle_importable(
    *,
    importer: PackageImporter | None = None,
    defuse: Callable[[], None] | None = None,
) -> CheckResult:
    """The ``needle`` package (from the ``cactus-needle`` distribution) must import."""
    name = "cactus-needle importable"
    do_import = importer if importer is not None else importlib.import_module
    apply_defusing = defuse if defuse is not None else _defuse_backend_network
    apply_defusing()
    try:
        module = do_import("needle")
    except Exception as exc:
        return _fail(
            name,
            f"the 'needle' package could not be imported ({type(exc).__name__}: {exc})",
            "run 'uv sync' to install the pinned cactus-needle package",
        )
    return _ok(name, f"'{getattr(module, '__name__', 'needle')}' imports")


def check_versions(
    *,
    version_reader: VersionReader | None = None,
    pinned_version: str = model_store.PINNED_CACTUS_NEEDLE_VERSION,
    engine_version: str = model_store.ENGINE_VERSION,
) -> CheckResult:
    """Report the installed ``cactus-needle`` and engine versions; WARN on skew.

    The streaming decoder is coupled to the pinned package's exact generator
    shape (ticket 14's risk R1), so an installed version that differs from the
    pin is a warning the user should see. The engine ``.so`` version is reported
    alongside because ADR 0017 pins it separately.
    """
    name = "Engine/package versions"
    read_version = version_reader if version_reader is not None else _read_distribution_version
    try:
        installed = read_version("cactus-needle")
    except importlib.metadata.PackageNotFoundError:
        return _warn(
            name,
            f"cactus-needle is not installed; pinned {pinned_version}, engine {engine_version}",
            f"run 'uv sync' to install cactus-needle=={pinned_version}",
        )
    if installed != pinned_version:
        return _warn(
            name,
            f"cactus-needle {installed} is installed but {pinned_version} is pinned; "
            f"engine {engine_version}. The streaming decoder is coupled to the "
            "pinned package's generator shape, so a version change can break it",
            f"restore the pin with 'uv sync' (pinned {pinned_version}, installed "
            f"{installed}), or update the pin and re-run the full test suite",
        )
    return _ok(
        name,
        f"cactus-needle {installed} matches the pin {pinned_version}; engine {engine_version}",
    )


def check_audio_device(config: Config, *, node_probe: NodeListProbe | None = None) -> CheckResult:
    """The configured ``audio.device`` should appear in the session's node list.

    ``pw-record --target`` is only a hint: an unresolved target silently falls
    back to the default microphone (ADR 0008's amendment), so a typo cannot be
    detected in the audio source. An absent-but-plausible device is therefore a
    ``WARN`` (dictation still works, from the wrong mic); a syntactically
    impossible device name is a ``FAIL``.
    """
    name = "Audio device"
    device = config.audio.device
    if device == "":
        return _ok(name, "audio.device is empty: the system default input is used")
    if device.strip() == "" or any(char in device for char in ("\x00", "\n", "\r")):
        return _fail(
            name,
            f"audio.device = {device!r} is not a valid PipeWire node name",
            "set [audio] device to a real node name (list them with 'pw-dump' or "
            "'wpctl status'), or leave it empty for the system default",
        )
    probe = node_probe if node_probe is not None else _pw_dump_node_names
    nodes = probe()
    if nodes is None:
        return _warn(
            name,
            f"could not enumerate PipeWire nodes to check audio.device = {device!r}",
            "ensure PipeWire is running and list nodes with 'pw-dump' or 'wpctl status'",
        )
    if device in nodes:
        return _ok(name, f"audio.device = {device!r} is present in the session")
    return _warn(
        name,
        f"audio.device = {device!r} is not in the session's node list; pw-record "
        "would silently fall back to the default microphone",
        "list the available devices with 'wpctl status' or 'pw-dump', then set "
        "[audio] device to one of them",
    )


def check_pw_record(*, lookup: BinaryLookup | None = None) -> CheckResult:
    """``pw-record`` must be on ``PATH`` (ADR 0008)."""
    name = "pw-record present"
    find = lookup if lookup is not None else shutil.which
    found = find(PW_RECORD_BINARY)
    if found is None:
        return _fail(
            name,
            f"'{PW_RECORD_BINARY}' is not on PATH",
            "install the PipeWire tools (Arch: 'sudo pacman -S pipewire') and make "
            "sure they are on PATH",
        )
    return _ok(name, f"'{PW_RECORD_BINARY}' is at {found}")


def check_pipewire_session(*, probe: SessionProbe | None = None) -> CheckResult:
    """A PipeWire session must be reachable, or capture cannot start (ADR 0008).

    This is the unconditional, once-per-run mitigation for ticket 6's measured
    zero-sample race: a broken session is caught here before dictation rather
    than surfacing as a normal empty recording.
    """
    name = "PipeWire session"
    reachable = probe if probe is not None else _pipewire_session_reachable
    if reachable():
        return _ok(name, "a PipeWire session is reachable")
    return _fail(
        name,
        "no PipeWire session is reachable",
        "check the session with 'pw-cli info 0' (or list nodes with 'pw-dump'); "
        "ensure PipeWire is running in this login session",
    )


def check_ydotoold(*, socket_path: Path | None = None, probe: SocketProbe | None = None) -> CheckResult:
    """The ``ydotoold`` daemon must be reachable at its socket (ADR 0003).

    The probe is the socket, not ``systemctl``, because a daemon started by hand
    is equally valid; ``systemctl`` appears only in the fix.
    """
    name = "ydotoold"
    path = socket_path if socket_path is not None else _ydotoold_socket_path()
    reachable = probe if probe is not None else _ydotoold_reachable
    if reachable(path):
        return _ok(name, f"the ydotoold daemon is reachable at {path}")
    return _fail(
        name,
        f"the ydotoold daemon is not reachable at {path}",
        "enable and start the ydotoold daemon with 'systemctl --user enable --now "
        "ydotool' (the systemd unit is ydotool.service), or start 'ydotoold' by "
        "hand; make sure it can write /dev/uinput",
    )


def check_uinput(*, path: Path = UINPUT_PATH, probe: SocketProbe | None = None) -> CheckResult:
    """This process must be able to write ``/dev/uinput`` (ADR 0003)."""
    name = "/dev/uinput access"
    writable = probe if probe is not None else _uinput_writable
    if writable(path):
        return _ok(name, f"{path} is writable by this user")
    return _fail(
        name,
        f"{path} is not writable by this user",
        "add your user to the 'input' group ('sudo usermod -aG input \"$USER\"') or "
        "install ydotool's udev rule, then log out and back in so the group "
        "membership takes effect",
    )


def check_tray_backend(*, assertion: TrayAssertion | None = None) -> CheckResult:
    """Prove the tray backend is the visible AppIndicator one (ADR 0013).

    This calls the assertion function, which imports modules only. It never
    creates an icon, opens a window, or touches a display.
    """
    name = "Tray backend"
    assert_backend = assertion if assertion is not None else _default_tray_assertion
    try:
        assert_backend()
    except Exception as exc:
        return _fail(
            name,
            f"the AppIndicator tray backend is not available ({exc})",
            "install the Arch packages 'python-gobject', 'gtk3' and "
            "'libappindicator', and create the virtualenv with 'uv venv "
            "--system-site-packages --python /usr/bin/python3'",
        )
    return _ok(name, "pystray selected the visible AppIndicator backend")


def check_tray_failures(*, failure_report: TrayFailureProbe | None = None) -> CheckResult:
    """Surface a tray that has recorded runtime failures (ADR 0013, ticket 17).

    ``service.py`` contains every ``StatusUI`` callback exception, so a tray that
    raises on every call simply stops updating with no signal. ``TrayStatusUI``
    records its own failures; when a provider is injected this check turns that
    record into a line -- ``OK`` when nothing failed, ``FAIL`` when the record
    exists.

    **With no provider this is a ``WARN``, never ``OK``.** The record genuinely
    lives in the running service process and doctor neither writes nor opens a
    socket to it, so an ``OK`` line here would claim health doctor cannot
    observe; the summary says so and the fix names where the failures do surface
    (the service's stderr and its shutdown summary). A check that cannot fail is
    worse than no check, because it reads as verification.
    """
    name = "Tray failures"
    if failure_report is None:
        return _warn(
            name,
            "tray failures cannot be determined from here: the record lives in the "
            "running service process, and doctor does not connect to it or write a file",
            "watch the service's stderr for 'ges2t: the tray has failed ...' lines and "
            "its shutdown summary, or run 'ges2t run' in a terminal to see them",
        )
    report = failure_report()
    if report is None:
        return _ok(name, "the tray has recorded no failures")
    return _fail(
        name,
        report.summary(),
        "restart 'ges2t run' and read the tray messages; a tray that keeps "
        "failing will not update",
    )


def check_service_socket(
    *, path: Path | None = None, listening: SocketProbe | None = None
) -> CheckResult:
    """A socket that exists but refuses connections is a stale service (ADR 0015)."""
    name = "Service socket"
    resolved = path if path is not None else default_socket_path()
    is_listening = listening if listening is not None else is_service_listening
    if not resolved.exists():
        return _ok(
            name,
            f"no socket at {resolved}; the service is not running (start it with 'ges2t run')",
        )
    if is_listening(resolved):
        return _ok(name, f"the service is listening at {resolved}")
    return _fail(
        name,
        f"a socket exists at {resolved} but refuses connections (stale)",
        "stop any old process and restart the service with 'ges2t run'; a stale "
        "socket file is removed on the next start",
    )


def _describe_layout(resolved: ResolvedLayout) -> str:
    """Render which source won and what it names, for the layout line."""
    if resolved.source is LayoutSource.LIVE_KEYMAP:
        return "resolved from the live compositor keymap"
    if resolved.source is LayoutSource.CONFIGURED:
        return (
            f"resolved from configuration: layout={resolved.layout!r} "
            f"variant={resolved.variant or ''!r}"
        )
    return (
        f"resolved by detection: layout={resolved.layout!r} "
        f"variant={resolved.variant or ''!r} ({resolved.display_name or 'unnamed'})"
    )


def check_keyboard_layout(
    *,
    layout: str = AUTO_LAYOUT,
    variant: str = "",
    resolver: LayoutResolver | None = None,
    compiler: LayoutCompiler | None = None,
    required: bool = True,
) -> CheckResult:
    """Resolve the active layout, record its source, and prove it compiles.

    The order is configuration -> live keymap -> detection (ADR 0020), and a
    layout that cannot be resolved must be learned here, before dictation
    silently types the wrong characters. ``required`` is ``False`` when the
    clipboard backend is selected (typing does not depend on the layout), so an
    unresolvable layout is then a warning, not a failure.
    """
    name = "Keyboard layout"
    resolve = resolver if resolver is not None else _default_layout_resolver
    compile_keymap = compiler if compiler is not None else _compile_resolved_layout
    try:
        resolved = resolve(layout, variant)
    except LayoutResolutionError as exc:
        summary = f"the keyboard layout could not be resolved: {exc}"
        fix = (
            'set [injection.ydotool] layout explicitly (for example layout = "hu"), '
            "or run ges2t under KDE Plasma on Wayland"
        )
        return _fail(name, summary, fix) if required else _warn(name, summary, fix)
    summary = _describe_layout(resolved)
    try:
        compile_keymap(resolved)
    except XkbError as exc:
        detail = f"the layout could not be compiled: {exc}"
        fix = (
            "install libxkbcommon and its XKB data (Arch: 'sudo pacman -S "
            "libxkbcommon xkeyboard-config')"
        )
        return _fail(name, detail, fix) if required else _warn(name, detail, fix)
    return _ok(name, f"{summary}; libxkbcommon compiled it")


def check_clipboard_backend(*, backend: str, lookup: BinaryLookup | None = None) -> CheckResult:
    """``wl-copy`` must exist when ``injection.backend = "clipboard"`` (ADR 0020)."""
    name = "Clipboard backend"
    if backend != "clipboard":
        return _ok(
            name,
            f"not selected (injection.backend = {backend!r}); 'wl-copy' is not required",
        )
    find = lookup if lookup is not None else shutil.which
    found = find(WL_COPY_BINARY)
    if found is None:
        return _fail(
            name,
            "injection.backend = 'clipboard' but 'wl-copy' is not on PATH",
            "install wl-clipboard (Arch: 'sudo pacman -S wl-clipboard') or set "
            "injection.backend = 'ydotool'",
        )
    return _ok(name, f"injection.backend = 'clipboard' and 'wl-copy' is at {found}")


def check_hotkey_bindings(*, console_script: str | None = None) -> CheckResult:
    """The hotkey lives in the compositor, so this is always a warning (ADR 0004).

    ``console_script`` is the resolved absolute path to this program's console
    script, or ``None`` when it could not be resolved to an existing file; the
    fix then falls back to the bare command (ticket 40).
    """
    return _warn(
        "Hotkey bindings",
        HOTKEY_SUMMARY,
        hotkey_fix(console_script=console_script),
    )


# --- diagnosis of the configuration blocks ------------------------------------


@dataclass(frozen=True)
class ConfigDiagnosis:
    """The generic config's validity plus its warnings."""

    validity: CheckResult
    generic_warnings: tuple[str, ...]


@dataclass(frozen=True)
class BlockDiagnosis:
    """One engine/injection block's validity plus its warnings."""

    validity: CheckResult
    warnings: tuple[str, ...]


def diagnose_config(*, loader: ConfigLoader) -> ConfigDiagnosis:
    """Load the generic config, turning :class:`ConfigError` into a report line.

    The loader is injected so tests never read the user's real file. A missing
    file is not an error (ADR 0009); a TOML or type error names the file and key
    in the :class:`ConfigError` message, which is printed verbatim.
    """
    try:
        loaded = loader()
    except ConfigError as exc:
        return ConfigDiagnosis(
            validity=_fail(
                "Config valid",
                f"invalid configuration: {exc}",
                "fix the file named above, or pass a different one with '--config <path>'",
            ),
            generic_warnings=(),
        )
    except Exception as exc:  # a probe bug must not abort the whole report
        return ConfigDiagnosis(
            validity=_fail(
                "Config valid",
                f"the check crashed: {type(exc).__name__}: {exc}",
                "this is a ges2t bug; re-run with 'GES2T_DEBUG=1' and report it",
            ),
            generic_warnings=(),
        )
    return ConfigDiagnosis(
        validity=_ok(
            "Config valid",
            "the configuration parsed and every generic value has the right type",
        ),
        generic_warnings=tuple(loaded.warnings),
    )


def diagnose_engine_config(config: Config) -> BlockDiagnosis:
    """Validate the selected engine's block, catching ``WhistleConfigError``.

    The engine block is handed to the selected adapter, exactly as the pipeline
    does; the adapter's distinct error type (which names a block key) is caught
    here and unified with the generic report (ADR 0009).
    """
    engine = config.transcriber.engine
    if engine != "whistle":
        return BlockDiagnosis(
            validity=_ok(
                "Engine config valid",
                f"engine {engine!r} has no engine-block validator in this build",
            ),
            warnings=(),
        )
    block = config.engine_blocks.get(engine, {})
    try:
        result = WhistleConfig.from_mapping(block)
    except WhistleConfigError as exc:
        return BlockDiagnosis(
            validity=_fail(
                "Engine config valid",
                str(exc),
                f"correct the value named above in the [engine.whistle] block ({exc})",
            ),
            warnings=(),
        )
    except Exception as exc:
        return BlockDiagnosis(
            validity=_fail(
                "Engine config valid",
                f"the check crashed: {type(exc).__name__}: {exc}",
                "this is a ges2t bug; re-run with 'GES2T_DEBUG=1' and report it",
            ),
            warnings=(),
        )
    return BlockDiagnosis(
        validity=_ok("Engine config valid", "[engine.whistle] is valid"),
        warnings=tuple(result.warnings),
    )


def diagnose_injection_config(config: Config) -> BlockDiagnosis:
    """Validate the selected injector's block, catching ``InjectionConfigError``."""
    backend = config.injection.backend
    block = config.injection_blocks.get(backend, {})
    result: YdotoolConfigResult | ClipboardConfigResult
    try:
        if backend == "ydotool":
            result = YdotoolConfig.from_mapping(block)
        elif backend == "clipboard":
            result = ClipboardConfig.from_mapping(block)
        else:
            return BlockDiagnosis(
                validity=_ok(
                    "Injection config valid",
                    f"injection backend {backend!r} has no block validator in this build",
                ),
                warnings=(),
            )
    except InjectionConfigError as exc:
        return BlockDiagnosis(
            validity=_fail(
                "Injection config valid",
                str(exc),
                f"correct the value named above in the [injection.{backend}] block ({exc})",
            ),
            warnings=(),
        )
    except Exception as exc:
        return BlockDiagnosis(
            validity=_fail(
                "Injection config valid",
                f"the check crashed: {type(exc).__name__}: {exc}",
                "this is a ges2t bug; re-run with 'GES2T_DEBUG=1' and report it",
            ),
            warnings=(),
        )
    return BlockDiagnosis(
        validity=_ok("Injection config valid", f"[injection.{backend}] is valid"),
        warnings=tuple(result.warnings),
    )


def _warnings_result(warnings: Sequence[str]) -> CheckResult:
    """Fold every unknown-key warning (generic, engine and injection) into one line."""
    if not warnings:
        return _ok("Config warnings", "no unknown keys were found")
    return _warn(
        "Config warnings",
        f"{len(warnings)} unknown key(s) were ignored: " + "; ".join(warnings),
        "remove or correct the unknown keys listed above; they are ignored but "
        "are usually typos",
    )


# --- report assembly ----------------------------------------------------------


def _guard(name: str, check: Check) -> CheckResult:
    """Run one check; an unexpected crash becomes a ``FAIL``, never a traceback."""
    try:
        return check()
    except Exception as exc:
        return _fail(
            name,
            f"the check crashed: {type(exc).__name__}: {exc}",
            "this is a ges2t bug; re-run with 'GES2T_DEBUG=1' and report it",
        )


def _ydotool_settings(config: Config) -> YdotoolConfig:
    """Return the ``[injection.ydotool]`` settings, defaulting when they are invalid.

    The injection-config check reports an invalid block; the layout check still
    needs *something*, so it falls back to the documented defaults.
    """
    try:
        return YdotoolConfig.from_mapping(config.injection_blocks.get("ydotool", {})).config
    except InjectionConfigError:
        return YdotoolConfig()


def build_report(config: Config, options: DoctorOptions) -> DoctorReport:
    """Run every check and collect the results, in report order.

    ``config`` is the already-loaded configuration the CLI passes; the generic
    loader is re-run from ``options.config_path`` so a :class:`ConfigError` is
    reported here rather than pre-empted. Every probe comes from
    ``options.probes`` and is injectable, so the tests touch no real system.
    """
    probes = options.probes if options.probes is not None else DoctorProbes()

    config_diagnosis = diagnose_config(loader=lambda: load_config(options.config_path))
    engine_diagnosis = diagnose_engine_config(config)
    injection_diagnosis = diagnose_injection_config(config)
    ydotool = _ydotool_settings(config)
    model_target = _model_target(config)
    engine_target = _engine_target()
    console_script = probes.console_script()

    results: list[CheckResult] = [
        _guard(
            "Package imports",
            lambda: check_package_imports(importer=probes.importer),
        ),
        _guard(
            "Architecture",
            lambda: check_architecture(),
        ),
        _guard(
            "Model present",
            lambda: check_model_present(config, target=model_target),
        ),
        _guard(
            "Model verified",
            lambda: check_model_verified(config, verify=options.verify, target=model_target),
        ),
        _guard(
            "Engine present",
            lambda: check_engine_present(target=engine_target),
        ),
        _guard(
            "cactus-needle importable",
            lambda: check_cactus_needle_importable(importer=probes.importer),
        ),
        _guard(
            "Engine/package versions",
            lambda: check_versions(version_reader=probes.version_reader),
        ),
        config_diagnosis.validity,
        engine_diagnosis.validity,
        injection_diagnosis.validity,
        _warnings_result(
            (*config_diagnosis.generic_warnings, *engine_diagnosis.warnings, *injection_diagnosis.warnings)
        ),
        _guard(
            "Audio device",
            lambda: check_audio_device(config, node_probe=probes.node_probe),
        ),
        _guard(
            "pw-record present",
            lambda: check_pw_record(lookup=probes.binary_lookup),
        ),
        _guard(
            "PipeWire session",
            lambda: check_pipewire_session(probe=probes.session_probe),
        ),
        _guard(
            "ydotoold",
            lambda: check_ydotoold(probe=probes.ydotoold_probe),
        ),
        _guard(
            "/dev/uinput access",
            lambda: check_uinput(probe=probes.uinput_probe),
        ),
        _guard(
            "Tray backend",
            lambda: check_tray_backend(assertion=probes.tray_assertion),
        ),
        _guard(
            "Tray failures",
            lambda: check_tray_failures(failure_report=probes.tray_failure_probe),
        ),
        _guard(
            "Service socket",
            lambda: check_service_socket(listening=probes.listening_probe),
        ),
        _guard(
            "Keyboard layout",
            lambda: check_keyboard_layout(
                layout=ydotool.layout,
                variant=ydotool.variant,
                resolver=probes.layout_resolver,
                compiler=probes.layout_compiler,
                required=config.injection.backend == "ydotool",
            ),
        ),
        _guard(
            "Clipboard backend",
            lambda: check_clipboard_backend(
                backend=config.injection.backend, lookup=probes.binary_lookup
            ),
        ),
        check_hotkey_bindings(console_script=console_script),
    ]
    return DoctorReport(results=tuple(results), console_script=console_script)


def render_report(report: DoctorReport) -> str:
    """Render the human-readable report: one line per check, then the failures.

    ``FAIL`` entries are listed again at the end with their fix lines so the
    user does not have to scroll, and the recommended KDE bindings are printed
    once for copying.
    """
    width = max((len(result.name) for result in report.results), default=0)
    lines: list[str] = ["ges2t doctor", ""]
    for result in report.results:
        lines.append(f"{result.name:<{width}}  {result.status.value:<4}  {result.summary}")
        if result.status is not CheckStatus.OK and result.fix:
            lines.append(f"{'':<{width}}        fix: {result.fix}")
    lines.append("")
    lines.append(
        f"Summary: {len(report.results)} checks, {len(report.successes)} ok, "
        f"{len(report.warnings)} warning(s), {len(report.failures)} failure(s)."
    )
    failures = report.failures
    if failures:
        lines.append("")
        lines.append("Failures (listed again with their fixes):")
        for result in failures:
            lines.append(f"  {result.name}: {result.summary}")
            lines.append(f"    fix: {result.fix}")
    lines.append("")
    lines.append(kde_binding_section(console_script=report.console_script))
    return "\n".join(lines) + "\n"


def run_doctor(config: Config, options: DoctorOptions | None = None) -> int:
    """Diagnose the environment and return the documented exit code (ADR 0016).

    Prints one line per check plus a summary, then returns :data:`EXIT_SUCCESS`
    when no check failed (warnings are allowed) and :data:`EXIT_FAILURE`
    otherwise. ``options`` defaults to the default config path and the real
    probes, so ``run_doctor(config)`` is the stable one-argument entry point the
    CLI contract names.
    """
    resolved = options if options is not None else DoctorOptions(config_path=default_config_path())
    report = build_report(config, resolved)
    print(render_report(report), end="")
    return report.exit_code
