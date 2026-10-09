"""Composition root: the one place concrete implementations are chosen (ADR 0010).

Every other module in ges2t is written against the Protocols in
:mod:`ges2t.interfaces` and the :class:`~ges2t.service.DictationSession` seam.
This module is the single exception: it is the *only* code allowed to import a
backend adapter, ``pw-record``, or the ``ydotool`` injector and bind them
together (ADR 0010, ADR 0019). Keeping every concrete choice here is what makes
the abstraction real rather than decorative.

What it builds
--------------
* :func:`build_transcriber` — maps ``config.transcriber.engine`` to a
  :class:`~ges2t.transcribers.whistle.WhistleTranscriber`. The
  ``[engine.<name>]`` block is fetched with ``.get(..., {})``: the generic
  config layer deliberately never synthesises an engine block, so an absent
  ``[engine.whistle]`` is the *normal* case and must mean "all defaults", never a
  ``KeyError`` (ADR 0009; ticket 4's hand-off note).
* :func:`build_audio_source` — a :class:`~ges2t.audio.pw_record.PwRecordAudioSource`
  for the configured device.
* :func:`build_injector` — a :class:`~ges2t.injectors.ydotool.YdotoolInjector` or a
  :class:`~ges2t.injectors.clipboard.ClipboardPasteInjector`, chosen by
  ``injection.backend``. The ``ydotool`` backend carries ``ui.trailing_space``
  (ADR 0012) and the ``[injection.ydotool]`` layout/key-delay settings (ADR
  0009, ADR 0020) and emits one batched ``ydotool key`` invocation, never
  ``ydotool type`` (ADR 0003's amendment). The clipboard backend carries the
  ``[injection.clipboard]`` ``paste_keys`` and delivers the text through
  ``wl-copy`` plus one raw-keycode paste chord (ticket 25). An unknown backend
  value raises rather than silently choosing one (ADR 0003, ADR 0020).
* :class:`BatchDictationSession` — the batch ``DictationSession``: record the
  whole utterance, transcribe once, return the text.
* :class:`~ges2t.streaming_session.StreamingDictationSession` — the windowed
  streaming session (ADR 0011). :func:`_session_factory` owns the one
  batch-vs-streaming selection step: streaming is chosen when
  ``streaming.enabled`` and ``Transcriber.supports_streaming`` are both true,
  the batch path otherwise. The commit window stays the adapter's property and
  only the feed granularity reaches the session.
* :func:`build_status_ui` — the tray when ``ui.tray = true`` and the
  AppIndicator backend is present, the documented no-op otherwise; a failed tray
  degrades to the no-op with a printed warning (ADR 0013).
* :func:`run_service` — the ``ges2t run`` entry point: verify the artifacts are
  already present **without downloading** (ADR 0017, ADR 0018), warm the model
  once, build the service, and serve the socket until ``SIGINT``/``SIGTERM``.
  With ``ui.notifications = true`` the chosen status UI is wrapped in
  :class:`~ges2t.notify.NotifyingStatusUI` first (ticket 18).

Startup never touches the network (ADR 0018). Acquisition lives exclusively in
:mod:`ges2t.model_store` and is reached from ``ges2t setup``, never from this
path — ``run_service`` calls neither ``ensure_model()`` nor ``ensure_engine()``,
and that is asserted in ``tests/test_pipeline.py``.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path

from ges2t import model_store
from ges2t.audio.pw_record import PwRecordAudioSource
from ges2t.config import Config
from ges2t.injectors.clipboard import ClipboardPasteInjector
from ges2t.injectors.layout import ClipboardConfig, YdotoolConfig
from ges2t.injectors.ydotool import XkbKeySequenceSource, YdotoolInjector
from ges2t.interfaces import AudioSource, Injector, LiveText, StatusUI, Transcriber
from ges2t.notify import NotifyingStatusUI
from ges2t.service import CommandResponse, DictationSession, Service
from ges2t.socket_proto import (
    ServiceAlreadyRunningError,
    default_socket_path,
    send_command,
)
from ges2t.streaming_session import StreamingDictationSession
from ges2t.transcribers.whistle import WhistleTranscriber
from ges2t.transcribers.whistle_config import WhistleConfig
from ges2t.ui.status import (
    DiagnosticStatusUI,
    NullStatusUI,
    StoppableStatusUI,
)
from ges2t.ui.tray import TrayBackendError, build_tray_status_ui

__all__ = [
    "BatchDictationSession",
    "PipelineError",
    "build_audio_source",
    "build_injector",
    "build_status_ui",
    "build_transcriber",
    "run_service",
]

#: Process exit code for a documented startup failure. Mirrors
#: :data:`ges2t.cli.EXIT_FAILURE` without importing the CLI into the
#: implementation (the dependency runs CLI -> pipeline, never the reverse).
_EXIT_SUCCESS: int = 0
_EXIT_FAILURE: int = 1

#: The pipeline's logger. The session-selection decision is logged at DEBUG so
#: a user can tell whether streaming is active without a debugger attached
#: (ticket 16); nothing else here logs.
_logger = logging.getLogger(__name__)

#: The two session kinds :func:`_select_session_kind` can return. Plain strings
#: rather than an ``Enum`` because the value is only compared internally and
#: never crosses a boundary.
_SESSION_STREAMING: str = "streaming"
_SESSION_BATCH: str = "batch"


class PipelineError(Exception):
    """A startup failure that already carries an actionable message.

    ``hint`` is an optional second line naming the exact fix; ``run_service``
    prints it to stderr and returns non-zero instead of raising to the top level.
    """

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint


class BatchDictationSession:
    """The batch :class:`~ges2t.service.DictationSession` (ADR 0005, ADR 0011).

    Record the whole utterance, then transcribe it once when
    :meth:`finish` is called. There are no partials: the batch adapter can only
    transcribe complete buffers, so :meth:`partials` yields nothing rather than
    inventing live text (ADR 0011).

    Both collaborators are injected as Protocols, so the session is exercised in
    tests with fakes and never touches a microphone or the engine directly
    (ADR 0010, ADR 0014). A single audio source is shared by every session the
    factory creates; that is safe because the service guarantees at most one
    recording at a time (``PwRecordAudioSource.start`` refuses a second).
    """

    def __init__(self, transcriber: Transcriber, audio_source: AudioSource) -> None:
        self._transcriber = transcriber
        self._audio_source = audio_source

    def start(self) -> None:
        """Begin capturing audio; returns promptly and transcribes nothing."""
        self._audio_source.start()

    def partials(self) -> Iterator[LiveText]:
        """Yield nothing: the batch path produces no partial text (ADR 0011)."""
        return iter(())

    def finish(self) -> str:
        """Stop capture and return the transcript.

        A recording that captured **zero samples** returns ``""`` and does not
        call the transcriber: silence in, nothing out (ADR 0012). Otherwise the
        buffer is handed to the transcriber and its text is returned unchanged;
        the trailing space belongs to the injector (ADR 0012).
        """
        buffer = self._audio_source.stop()
        if len(buffer.samples) == 0:
            return ""
        return self._transcriber.transcribe(buffer).text

    def cancel(self) -> None:
        """Stop capture and discard the audio without transcribing."""
        self._audio_source.stop()


# -- composition ----------------------------------------------------------
# Small, named builders, one per collaborator. Ticket 16 adds a second session
# implementation and a selection rule; it extends the session factory below
# rather than growing ``run_service`` into a blob.


def _build_whistle(config: Config) -> Transcriber:
    """Build the Whistle batch transcriber from its ``[engine.whistle]`` block."""
    block = config.engine_blocks.get(config.transcriber.engine, {})
    whistle_config = WhistleConfig.from_mapping(block).config
    return WhistleTranscriber(
        whistle_config,
        model_path_override=_model_path_override(config),
    )


#: The engine identifiers :func:`build_transcriber` can build, mapped to their
#: builder. The key is matched against ``config.transcriber.engine`` and the
#: mapping is the factory's single source of truth: an engine absent here gets a
#: clear "not handled by this build" error rather than a silent fallback.
_TRANSCRIBER_BUILDERS: Mapping[str, Callable[[Config], Transcriber]] = {
    "whistle": _build_whistle,
}


def build_transcriber(config: Config) -> Transcriber:
    """Choose and construct the configured :class:`~ges2t.interfaces.Transcriber`.

    Only ``"whistle"`` is handled in v1. The engine's own ``[engine.whistle]``
    block is selected with ``.get(..., {})``: the generic layer never
    synthesises an engine block, so its absence is normal and means "all
    defaults" (ADR 0009). An unhandled engine raises :class:`PipelineError` with
    the list of engines this build can construct.
    """
    engine = config.transcriber.engine
    builder = _TRANSCRIBER_BUILDERS.get(engine)
    if builder is None:
        known = ", ".join(repr(name) for name in sorted(_TRANSCRIBER_BUILDERS))
        raise PipelineError(
            f"transcriber engine {engine!r} is not handled by this build; "
            f"handled engines: {known}"
        )
    return builder(config)


def build_audio_source(config: Config) -> AudioSource:
    """Construct the ``pw-record`` audio source for ``config.audio.device`` (ADR 0008)."""
    return PwRecordAudioSource(config.audio.device)


def _build_ydotool_injector(config: Config) -> Injector:
    """Build the batched ``ydotool`` injector (ADR 0003, ADR 0012, ADR 0020).

    ``[injection.ydotool]`` supplies the layout and ``key_delay_ms``; an absent
    block means all defaults (``.get(..., {})``, ADR 0009). The
    ``XkbKeySequenceSource`` holds only the layout names: it rebuilds its keymap
    per utterance, so a layout switch is picked up (ADR 0020).
    """
    settings = YdotoolConfig.from_mapping(config.injection_blocks.get("ydotool", {}))
    return YdotoolInjector(
        XkbKeySequenceSource(settings.config.layout, settings.config.variant),
        trailing_space=config.ui.trailing_space,
        key_delay_ms=settings.config.key_delay_ms,
    )


def _build_clipboard_injector(config: Config) -> Injector:
    """Build the clipboard-paste injector (ticket 25, ADR 0003 amended).

    ``[injection.clipboard]`` supplies ``paste_keys`` (default ``["ctrl+v"]``;
    most terminals need ``ctrl+shift+v``); an absent block means all defaults
    (``.get(..., {})``, ADR 0009). The block's unknown-key warnings are carried
    on the injector's ``last_warnings`` for ``ges2t doctor`` to surface instead
    of being swallowed.
    """
    settings = ClipboardConfig.from_mapping(
        config.injection_blocks.get("clipboard", {})
    )
    return ClipboardPasteInjector(
        paste_keys=settings.config.paste_keys,
        trailing_space=config.ui.trailing_space,
        warnings=settings.warnings,
    )


#: The ``injection.backend`` identifiers :func:`build_injector` can build, mapped
#: to their builder. The key is matched against ``config.injection.backend`` and
#: this mapping is the factory's single source of truth: an unknown backend gets
#: a clear error naming the handled ones rather than a silent default -- a silent
#: mechanism switch is exactly what ADR 0003/0020 forbid.
_INJECTOR_BUILDERS: Mapping[str, Callable[[Config], Injector]] = {
    "ydotool": _build_ydotool_injector,
    "clipboard": _build_clipboard_injector,
}


def build_injector(config: Config) -> Injector:
    """Choose and construct the configured :class:`~ges2t.interfaces.Injector`.

    The backend is an explicit configuration choice (``injection.backend``,
    default ``"ydotool"``), never an automatic fallback: a failure in one
    backend must not silently switch to the other (ADR 0003, ADR 0020). An
    unhandled backend value raises :class:`PipelineError` naming the backends
    this build can construct, so a typo fails at startup rather than by quietly
    choosing something the user did not ask for. The selected backend's
    ``[injection.<name>]`` block is read with ``.get(..., {})``: the generic
    layer never synthesises one, so its absence is the normal case and means all
    defaults, never a ``KeyError`` (ADR 0009).
    """
    backend = config.injection.backend
    builder = _INJECTOR_BUILDERS.get(backend)
    if builder is None:
        known = ", ".join(repr(name) for name in sorted(_INJECTOR_BUILDERS))
        raise PipelineError(
            f"injection backend {backend!r} is not handled by this build; "
            f"handled backends: {known}"
        )
    return builder(config)


def _send_command_to_service(command: str) -> CommandResponse:
    """Send one verb to this session's service socket (ADR 0015, ADR 0016).

    The tray's menu actions go through the same client the CLI uses rather than
    reimplementing service logic; this binds the default socket path once.
    """
    return send_command(default_socket_path(), command)


def build_status_ui(config: Config) -> StatusUI:
    """Choose the status display: the tray, or the documented no-op (ADR 0013).

    With ``ui.tray = false`` this is :class:`NullStatusUI`. Otherwise it asserts
    the AppIndicator backend and starts the tray. An *environmental* failure to
    build a visible tray — the backend assertion, a missing ``gi``/``pystray``,
    or ``pystray``/GTK refusing to construct the icon for want of a display —
    prints an actionable warning and degrades to :class:`NullStatusUI`, because
    a missing tray must never make ges2t unusable and nothing may be reachable
    only through the tray (ADR 0013). A programming error is **not** swallowed:
    it propagates so a bug fails loudly instead of quietly disabling the tray.
    """
    if not config.ui.tray:
        return NullStatusUI()
    try:
        return build_tray_status_ui(_send_command_to_service)
    except TrayBackendError as exc:
        _print_tray_warning(str(exc))
        return NullStatusUI()
    except (ImportError, ModuleNotFoundError, OSError) as exc:
        # Only an *environmental* failure may fall back: ``gi``/``pystray``/PIL
        # missing, or no display/DBus (review 002, blocking finding). A
        # programming error (``AttributeError``, ``TypeError``, ...) must
        # propagate and fail loudly rather than ship as "the tray sometimes
        # does not appear" (ADR 0013). ``TrayBackendError`` is handled above so
        # its exact, actionable message is preserved.
        _print_tray_warning(f"the tray could not be created ({exc})")
        return NullStatusUI()


def _print_tray_warning(reason: str) -> None:
    """Print the tray fallback and why, without stopping the service (ADR 0013)."""
    print(f"ges2t: {reason}", file=sys.stderr)
    print(
        "  the tray is disabled; ges2t remains fully usable from the CLI",
        file=sys.stderr,
    )


def _apply_notifications(config: Config, ui: StatusUI) -> StatusUI:
    """Wrap the status UI for desktop notifications when enabled (ticket 18).

    ``ui.notifications = false`` returns the bare UI untouched, so the tray is
    passed straight to the service and nothing on the notification path is
    constructed at all. The wrapper forwards every call and only adds a popup
    on ``error`` (ADR 0013, ADR 0016).
    """
    if not config.ui.notifications:
        return ui
    return NotifyingStatusUI(ui)


def _shutdown_status_ui(ui: StatusUI) -> None:
    """Stop a status UI that owns a background resource and report its failures.

    Only the tray implements either Protocol; the no-op and test UIs are left
    alone. The failure report is the in-process half of the observability
    requirement — the tray also prints each distinct failure once as it happens.
    """
    if isinstance(ui, StoppableStatusUI):
        ui.stop()
    if isinstance(ui, DiagnosticStatusUI):
        report = ui.failure_report()
        if report is not None:
            print(f"ges2t: {report.summary()}", file=sys.stderr)


def _streaming_chunk_seconds(config: Config) -> float:
    """Return the streaming feed granularity from ``[engine.whistle]`` (ADR 0011).

    The block is read exactly as :func:`_build_whistle` reads it: ``.get(...,
    {})``, because an absent block is the normal case and means all defaults
    (ADR 0009). This is the **chunk** size only; the commit window is the
    transcriber adapter's property (:class:`WhistleConfig`, applied inside
    ``WhistleTranscriber.stream``), so it is deliberately not returned here.
    """
    block = config.engine_blocks.get(config.transcriber.engine, {})
    return WhistleConfig.from_mapping(block).config.chunk_seconds


def _select_session_kind(config: Config, transcriber: Transcriber) -> str:
    """Return the session to build, and log why (ADR 0010, ADR 0011).

    Streaming is the default whenever the engine declares the capability and
    the user has not switched it off; the batch path runs otherwise. The
    decision is a capability flag (``Transcriber.supports_streaming``), never an
    exception or a runtime probe, and it is derived rather than configured: the
    only switch is ``streaming.enabled`` (ticket 16).
    """
    if not config.streaming.enabled:
        reason = "streaming.enabled = false"
        kind = _SESSION_BATCH
    elif not transcriber.supports_streaming:
        reason = f"transcriber {transcriber.name!r} does not support streaming"
        kind = _SESSION_BATCH
    else:
        reason = (
            f"streaming.enabled = true and transcriber {transcriber.name!r} "
            "supports streaming"
        )
        kind = _SESSION_STREAMING
    _logger.debug("using the %s dictation session (%s)", kind, reason)
    return kind


def _session_factory(
    config: Config, transcriber: Transcriber, audio_source: AudioSource
) -> Callable[[], DictationSession]:
    """Return the service's session factory: one session per recording (ADR 0011).

    This is the one place the batch-vs-streaming selection happens. Ticket 11
    deliberately left the choice out so this ticket owns it; the public
    ``run_service`` signature is unchanged and ``Service`` never learns which
    implementation it gets (ADR 0010, ADR 0015). The commit window is threaded
    to the adapter by :func:`_build_whistle` and the feed granularity to the
    session here, because they are different owners' properties.
    """
    kind = _select_session_kind(config, transcriber)
    chunk_seconds = _streaming_chunk_seconds(config)

    def create() -> DictationSession:
        if kind == _SESSION_STREAMING:
            return StreamingDictationSession(
                audio_source, transcriber, chunk_seconds=chunk_seconds
            )
        return BatchDictationSession(transcriber, audio_source)

    return create


def _build_service(
    config: Config,
    transcriber: Transcriber,
    audio_source: AudioSource,
    injector: Injector,
    status_ui: StatusUI,
) -> Service:
    """Construct the service with the composition root's choices."""
    return Service(
        _session_factory(config, transcriber, audio_source),
        injector,
        status_ui,
        default_socket_path(),
    )


# -- artifact presence (no network) ---------------------------------------


def _model_path_override(config: Config) -> Path | None:
    """Return the configured weights path, or ``None`` for the managed pin.

    ``config.transcriber.model_path`` is honoured only when it is non-empty; the
    engine path always comes from the model store and cannot be overridden.
    """
    configured = config.transcriber.model_path
    if configured:
        return Path(configured)
    return None


def _weights_path(config: Config) -> Path:
    """Return the weights file this run will load: the override or the managed pin."""
    override = _model_path_override(config)
    if override is not None:
        return override
    return model_store.model_path()


def _present(path: Path, *, expected_size: int | None) -> bool:
    """Whether ``path`` is a file of the expected byte size.

    Existence plus size is exactly the startup-path definition of "present"
    (ADR 0017): a full sha256 is deferred to ``ges2t setup`` and ``ges2t
    models --verify`` and must never sit here. ``expected_size`` is ``None`` for
    a caller-supplied ``model_path`` override, which has no pin of ours.
    """
    if not path.is_file():
        return False
    return expected_size is None or path.stat().st_size == expected_size


def _missing_artifacts(config: Config) -> list[str]:
    """Return one problem sentence per artifact that is missing or the wrong size.

    Empty means both artifacts are present and this run may proceed offline.
    The engine pin is resolved for the running platform, so an unsupported
    platform is reported as such instead of as a spurious missing file.
    """
    problems: list[str] = []

    weights = _weights_path(config)
    expected_weights_size = (
        None if _model_path_override(config) is not None else model_store.MODEL_PIN.size
    )
    if not _present(weights, expected_size=expected_weights_size):
        problems.append(f"the Whistle weights are missing or incomplete at {weights}")

    try:
        engine_pin = model_store.engine_pin()
    except model_store.UnsupportedPlatformError as exc:
        problems.append(str(exc))
    else:
        engine = model_store.engine_path()
        if not _present(engine, expected_size=engine_pin.size):
            problems.append(f"the Needle 3 engine is missing or incomplete at {engine}")

    return problems


def _require_artifacts(config: Config) -> None:
    """Raise :class:`PipelineError` unless both pinned artifacts are already present."""
    problems = _missing_artifacts(config)
    if problems:
        raise PipelineError(
            "; ".join(problems),
            hint=(
                "run 'ges2t setup' to download and verify the pinned model and "
                "engine (the only ges2t command that uses the network)"
            ),
        )


# -- entry point ----------------------------------------------------------


def _startup(config: Config, status_ui: StatusUI) -> Service:
    """Do everything up to (but not including) serving the socket.

    Order matters and is asserted in the tests: check the artifacts are present
    locally, build the collaborators, apply the managed environment overrides
    (ADR 0007), **load the model once** so it stays warm for the process
    lifetime (ADR 0015), then construct the service.
    """
    _require_artifacts(config)
    transcriber = build_transcriber(config)
    audio_source = build_audio_source(config)
    injector = build_injector(config)
    # The adapter also applies these in load(); doing it here as well is
    # idempotent and keeps the contract visible at the composition root (ADR 0017).
    os.environ.update(model_store.env_overrides())
    transcriber.load()
    return _build_service(config, transcriber, audio_source, injector, status_ui)


def run_service(config: Config, status_ui: StatusUI | None = None) -> int:
    """Run the long-lived service and return the process exit code (ADR 0015).

    ``status_ui`` defaults to the configured display: the tray when
    ``ui.tray = true`` and the AppIndicator backend is present, the documented
    no-op otherwise (ADR 0013). An explicitly injected UI wins, which is the
    seam the headless tests use. With ``ui.notifications = true`` (the default)
    the chosen UI is wrapped in :class:`~ges2t.notify.NotifyingStatusUI` so an
    ``error`` also raises a desktop notification; with it off the bare UI is
    used and the notification path is not constructed. The model is loaded
    exactly once at startup, the missing-artifact check never downloads, and the
    service runs until ``SIGINT``/``SIGTERM`` before returning ``0``.
    """
    base_ui = status_ui if status_ui is not None else build_status_ui(config)
    ui = _apply_notifications(config, base_ui)
    try:
        service = _startup(config, ui)
    except PipelineError as exc:
        _shutdown_status_ui(ui)
        _print_failure(exc)
        return _EXIT_FAILURE
    except Exception as exc:  # startup failure: report, never traceback at the top level
        _shutdown_status_ui(ui)
        print(f"ges2t: could not start the service: {exc}", file=sys.stderr)
        print(
            "  hint: run 'ges2t setup' to (re)install the pinned model and "
            "engine, then try again",
            file=sys.stderr,
        )
        return _EXIT_FAILURE

    try:
        service.serve()
    except ServiceAlreadyRunningError as exc:
        print(f"ges2t: {exc}", file=sys.stderr)
        return _EXIT_FAILURE
    except OSError as exc:
        print(f"ges2t: could not serve on {service.socket_path}: {exc}", file=sys.stderr)
        return _EXIT_FAILURE
    finally:
        _shutdown_status_ui(ui)
    return _EXIT_SUCCESS


def _print_failure(error: PipelineError) -> None:
    """Print a startup failure sentence and, when present, its fix."""
    print(f"ges2t: {error}", file=sys.stderr)
    if error.hint is not None:
        print(f"  hint: {error.hint}", file=sys.stderr)
