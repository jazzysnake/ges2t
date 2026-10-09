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
* :func:`build_injector` — a :class:`~ges2t.injectors.ydotool.YdotoolInjector`
  carrying ``ui.trailing_space`` (ADR 0012) and the ``[injection.ydotool]``
  layout/key-delay settings (ADR 0009, ADR 0020); it emits one batched
  ``ydotool key`` invocation and never calls ``ydotool type`` (ADR 0003's
  amendment).
* :class:`BatchDictationSession` — the batch ``DictationSession``: record the
  whole utterance, transcribe once, return the text. Streaming is a separate
  session implementation and is deliberately absent here (ticket 16).
* :func:`run_service` — the ``ges2t run`` entry point: verify the artifacts are
  already present **without downloading** (ADR 0017, ADR 0018), warm the model
  once, build the service, and serve the socket until ``SIGINT``/``SIGTERM``.

Startup never touches the network (ADR 0018). Acquisition lives exclusively in
:mod:`ges2t.model_store` and is reached from ``ges2t setup``, never from this
path — ``run_service`` calls neither ``ensure_model()`` nor ``ensure_engine()``,
and that is asserted in ``tests/test_pipeline.py``.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path

from ges2t import model_store
from ges2t.audio.pw_record import PwRecordAudioSource
from ges2t.config import Config
from ges2t.injectors.layout import YdotoolConfig
from ges2t.injectors.ydotool import XkbKeySequenceSource, YdotoolInjector
from ges2t.interfaces import AudioSource, Injector, StatusUI, Transcriber
from ges2t.service import DictationSession, Service
from ges2t.socket_proto import ServiceAlreadyRunningError, default_socket_path
from ges2t.transcribers.whistle import WhistleTranscriber
from ges2t.transcribers.whistle_config import WhistleConfig

__all__ = [
    "BatchDictationSession",
    "NullStatusUI",
    "PipelineError",
    "build_audio_source",
    "build_injector",
    "build_transcriber",
    "run_service",
]

#: Process exit code for a documented startup failure. Mirrors
#: :data:`ges2t.cli.EXIT_FAILURE` without importing the CLI into the
#: implementation (the dependency runs CLI -> pipeline, never the reverse).
_EXIT_SUCCESS: int = 0
_EXIT_FAILURE: int = 1


class PipelineError(Exception):
    """A startup failure that already carries an actionable message.

    ``hint`` is an optional second line naming the exact fix; ``run_service``
    prints it to stderr and returns non-zero instead of raising to the top level.
    """

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint


class NullStatusUI:
    """The documented no-op :class:`~ges2t.interfaces.StatusUI` (epic 2 adds the tray).

    ``idle``/``recording``/``transcribing``/``partial`` do nothing: there is no
    display surface yet. ``error`` is the deliberate exception — it writes the
    message to stderr, because otherwise a runtime failure (for example the
    adapter rejecting a window longer than the engine's 30 s ceiling, ADR 0005)
    would be invisible until the tray/notifications land. When epic 2 injects the
    real tray in the same place, this class is simply not constructed.
    """

    def idle(self) -> None:
        """Show the idle state (no-op)."""

    def recording(self) -> None:
        """Show the recording state (no-op)."""

    def transcribing(self) -> None:
        """Show the transcribing state (no-op)."""

    def error(self, message: str) -> None:
        """Report a failure on stderr until a real status UI exists."""
        print(f"ges2t: {message}", file=sys.stderr)

    def partial(self, text: str) -> None:
        """Show display-only live text (no-op; batch has no partials, ADR 0011)."""


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

    def partials(self) -> Iterator[str]:
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


def build_injector(config: Config) -> Injector:
    """Construct the batched ``ydotool`` injector (ADR 0003, ADR 0012, ADR 0020).

    ``[injection.ydotool]`` supplies the layout and ``key_delay_ms``; an absent
    block means all defaults (``.get(..., {})``, ADR 0009), and the config's
    unknown-key warnings are left for ``ges2t doctor`` to surface. The
    ``XkbKeySequenceSource`` holds only the layout names: it rebuilds its keymap
    per utterance, so a layout switch is picked up (ADR 0020). v1 has exactly
    one injector backend; ``injection.backend`` is preserved in the generic
    config for the clipboard paste injector that a later ticket adds.
    """
    settings = YdotoolConfig.from_mapping(config.injection_blocks.get("ydotool", {}))
    return YdotoolInjector(
        XkbKeySequenceSource(settings.config.layout, settings.config.variant),
        trailing_space=config.ui.trailing_space,
        key_delay_ms=settings.config.key_delay_ms,
    )


def _session_factory(
    transcriber: Transcriber, audio_source: AudioSource
) -> Callable[[], DictationSession]:
    """Return the service's session factory: one batch session per recording.

    This is the seam ticket 16 extends with the streaming/batch selection rule;
    ``Service`` already accepts the factory and never learns which implementation
    it gets (ADR 0010, ADR 0015).
    """

    def create() -> DictationSession:
        return BatchDictationSession(transcriber, audio_source)

    return create


def _build_service(
    transcriber: Transcriber,
    audio_source: AudioSource,
    injector: Injector,
    status_ui: StatusUI,
) -> Service:
    """Construct the service with the composition root's choices."""
    return Service(
        _session_factory(transcriber, audio_source),
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
    return _build_service(transcriber, audio_source, injector, status_ui)


def run_service(config: Config, status_ui: StatusUI | None = None) -> int:
    """Run the long-lived service and return the process exit code (ADR 0015).

    ``status_ui`` defaults to the documented no-op; epic 2 injects the real tray
    here without changing this signature. The model is loaded exactly once at
    startup, the missing-artifact check never downloads, and the service runs
    until ``SIGINT``/``SIGTERM`` before returning ``0``.
    """
    ui = status_ui if status_ui is not None else NullStatusUI()
    try:
        service = _startup(config, ui)
    except PipelineError as exc:
        _print_failure(exc)
        return _EXIT_FAILURE
    except Exception as exc:  # startup failure: report, never traceback at the top level
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
    return _EXIT_SUCCESS


def _print_failure(error: PipelineError) -> None:
    """Print a startup failure sentence and, when present, its fix."""
    print(f"ges2t: {error}", file=sys.stderr)
    if error.hint is not None:
        print(f"  hint: {error.hint}", file=sys.stderr)
