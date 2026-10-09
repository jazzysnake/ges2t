"""The ges2t service: state machine, session seam, and socket host (ADR 0015).

This module is the long-lived process's core. It owns the one piece of state
that everything else observes (the tray, the CLI, the pipeline) and it is the
only thing that mutates it. It is deliberately written against interfaces only:
a :class:`DictationSession` arrives from a factory, an
:class:`~ges2t.interfaces.Injector` and a :class:`~ges2t.interfaces.StatusUI`
are injected, and the socket path is a parameter. The service never imports a
backend, a GUI toolkit, ``subprocess``, or an audio library, which is what makes
its tests honest (ADR 0010, ADR 0014).

Threading
---------
One :class:`threading.Lock` guards **state transitions only**, and **no
collaborator is ever called while it is held**. ``session.finish()`` runs on a
short-lived worker thread started by ``toggle``/``ptt-stop``, so the socket
loop answers the next command immediately; creating/starting a session and
``session.cancel()`` are deferred past the locked section; and ``status_ui`` is
invoked from :meth:`Service._transition_scope` *after* the lock is released.
Holding the lock across transcription is the classic deadlock in this design
(ADR 0015), and a ``StatusUI`` such as the tray (ADR 0013) that reads
``service.state`` back would deadlock on the non-reentrant lock if it were
called under it. The tests assert both the loop's responsiveness and the
no-collaborator-under-lock rule.

Partial text from ``session.partials()`` is forwarded to
``status_ui.partial()`` on its own thread. It is display-only and is never
injected (ADR 0011).
"""

from __future__ import annotations

import contextlib
import functools
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

from ges2t.interfaces import Injector, StatusUI

__all__ = [
    "Command",
    "CommandResponse",
    "DictationSession",
    "Service",
    "ServiceState",
]

#: How long :meth:`Service.shutdown` waits for the transcription worker and the
#: partials forwarder to stop before giving up. Shutdown must not hang forever
#: on a wedged engine.
_SHUTDOWN_JOIN_TIMEOUT_SECONDS = 5.0


class ServiceState(StrEnum):
    """The closed set of service states (ADR 0015).

    The member values are exactly the strings that cross the socket, so the
    wire protocol and the state machine cannot disagree about spelling.
    """

    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    ERROR = "error"


class Command(StrEnum):
    """The verbs the service accepts over the socket (ADR 0004, ADR 0016).

    The strings are part of the CLI's user-facing contract: they are what the
    user binds to keys, so they are fixed here as a closed enum rather than
    sprinkled as literals.
    """

    TOGGLE = "toggle"
    PTT_START = "ptt-start"
    PTT_STOP = "ptt-stop"
    CANCEL = "cancel"
    STATUS = "status"


@dataclass(frozen=True)
class CommandResponse:
    """The service's answer to one command.

    This is the response body of the newline-delimited JSON protocol; the codec
    that turns it into bytes lives in :mod:`ges2t.socket_proto`. ``error`` is a
    stable machine-readable code (``not-recording``, ``busy``, ...) and
    ``message`` is the human sentence the CLI prints.
    """

    ok: bool
    state: ServiceState
    error: str | None = None
    message: str | None = None


@runtime_checkable
class DictationSession(Protocol):
    """One recording/transcription session — the seam later tickets implement.

    The service treats a session as an opaque object with a small lifecycle and
    never touches audio, the engine, or injection directly:

    * :meth:`start` begins capture; it must return promptly (it may start a
      background thread, but it must not transcribe).
    * :meth:`partials` yields display-only live text while recording and may
      yield nothing at all; it is forwarded to ``status_ui.partial()`` and is
      never injected (ADR 0011).
    * :meth:`finish` is blocking and returns the final text; the service calls it
      on a worker thread so the socket loop is never blocked.
    * :meth:`cancel` abandons the recording without transcribing.
    """

    def start(self) -> None:
        """Begin capturing audio."""
        ...

    def partials(self) -> Iterator[str]:
        """Yield display-only live text as it advances."""
        ...

    def finish(self) -> str:
        """Stop capture and return the final transcript (blocking)."""
        ...

    def cancel(self) -> None:
        """Abandon the recording without transcribing."""
        ...


@dataclass(frozen=True)
class _Decision:
    """A handler's answer plus an optional side effect to run outside the lock.

    ``state`` is the state to report to the client. It is captured while the
    lock is held so a fast background worker cannot race the response (for
    example, a ``finish()`` that returns instantly must not make a
    ``toggle``→``transcribing`` reply read back as ``idle``).
    """

    ok: bool
    state: ServiceState
    error: str | None = None
    message: str | None = None
    #: A side effect to run after the lock is released. It may return a
    #: replacement decision (a deferred start that fails turns an optimistic
    #: ``RECORDING`` into ``ERROR``); ``None`` means the decision stands.
    after: Callable[[], _Decision | None] | None = None


class Service:
    """The dictation state machine and socket command handler.

    Transition table (the specification, reproduced from the ticket so the code
    and the table cannot drift)::

        state         | toggle                  | ptt-start             | ptt-stop                | cancel                  | status
        --------------+-------------------------+-----------------------+-------------------------+-------------------------+-------------
        IDLE          | -> RECORDING            | -> RECORDING          | error not-recording     | error not-recording     | idle
        RECORDING     | -> TRANSCRIBING         | error already-recording | -> TRANSCRIBING       | -> IDLE                 | recording
        TRANSCRIBING  | -> RECORDING (queued)   | error busy            | error busy              | error busy              | transcribing
        ERROR         | -> IDLE                 | -> IDLE               | error not-recording     | -> IDLE                 | error

    Two details the table leaves implicit and this implementation fixes:

    * ``status`` never changes state and is always ``ok``; it reports the state
      the service is in right now.
    * The ``TRANSCRIBING`` row's ``toggle`` is *queued*: the service records the
      request and starts a fresh recording as soon as the in-flight ``finish()``
      returns. The immediate reply reports ``transcribing`` (the honest current
      state) with a ``message`` saying the recording is queued, because the
      state only becomes ``RECORDING`` once the queued session actually starts.

    Failures (a raising ``session_factory``/``start``/``finish`` or a raising
    ``injector``) move the service to ``ERROR``, call ``status_ui.error(...)``,
    and leave it ready for the next command. Empty or whitespace-only final text
    is never handed to the injector at all and returns to ``IDLE`` with no error
    (ADR 0012).
    """

    def __init__(
        self,
        session_factory: Callable[[], DictationSession],
        injector: Injector,
        status_ui: StatusUI,
        socket_path: Path,
    ) -> None:
        self._session_factory = session_factory
        self._injector = injector
        self._status_ui = status_ui
        self._socket_path = socket_path
        self._lock = threading.Lock()
        self._pending_display: list[Callable[[], None]] | None = None
        self._state = ServiceState.IDLE
        self._session: DictationSession | None = None
        self._pending_start = False
        self._worker: threading.Thread | None = None
        self._partials_thread: threading.Thread | None = None
        self._partials_stop: threading.Event | None = None

    @property
    def state(self) -> ServiceState:
        """The current state, read under the lock."""
        with self._lock:
            return self._state

    @property
    def socket_path(self) -> Path:
        """The Unix socket this service is (or will be) listening on."""
        return self._socket_path

    # -- command handling -------------------------------------------------

    def handle(self, command: str) -> CommandResponse:
        """Apply one command and return the response to send to the client.

        State transitions happen under the lock; every collaborator call — the
        ``status_ui`` display, creating/starting a session, ``session.cancel()``,
        and the transcription worker — is deferred until after the lock is
        released (ticket 21).
        """
        with self._transition_scope():
            try:
                decision = self._dispatch_locked(command)
            except Exception as exc:  # defensive: no command may kill the loop
                message = f"internal error handling {command!r}: {exc}"
                self._set_state(ServiceState.ERROR, message)
                decision = _Decision(
                    ok=False,
                    state=ServiceState.ERROR,
                    error="internal-error",
                    message=message,
                )
        if decision.after is not None:
            replacement = decision.after()
            if replacement is not None:
                decision = replacement
        return CommandResponse(
            ok=decision.ok,
            state=decision.state,
            error=decision.error,
            message=decision.message,
        )

    def _dispatch_locked(self, command: str) -> _Decision:
        try:
            verb = Command(command)
        except ValueError:
            return self._reject("unknown-command", f"unknown command: {command!r}")

        if verb is Command.STATUS:
            return _Decision(ok=True, state=self._state)

        state = self._state
        if state is ServiceState.IDLE:
            if verb is Command.TOGGLE or verb is Command.PTT_START:
                return self._begin_start_locked()
            return self._reject("not-recording", "the service is not recording")

        if state is ServiceState.RECORDING:
            if verb is Command.TOGGLE or verb is Command.PTT_STOP:
                return self._begin_transcription_locked()
            if verb is Command.PTT_START:
                return self._reject("already-recording", "already recording")
            if verb is Command.CANCEL:
                return self._cancel_locked()

        if state is ServiceState.TRANSCRIBING:
            if verb is Command.TOGGLE:
                return self._queue_start_locked()
            return self._reject("busy", "still transcribing the previous recording")

        if state is ServiceState.ERROR:
            if verb is Command.PTT_STOP:
                return self._reject("not-recording", "the service is not recording")
            self._set_state(ServiceState.IDLE)
            return _Decision(ok=True, state=ServiceState.IDLE)

        return self._reject("unknown-command", f"unknown command: {command!r}")

    # -- transitions ------------------------------------------------------
    # The ``*_locked`` helpers run with the lock held and never touch a
    # collaborator; :meth:`_commit_start` runs without it.

    def _reject(self, code: str, message: str) -> _Decision:
        return _Decision(ok=False, state=self._state, error=code, message=message)

    def _begin_start_locked(self) -> _Decision:
        """Reserve a start; the session is created and started after the lock.

        Creating the session and calling ``session.start()`` are collaborator
        calls, so they run in :meth:`_commit_start` (the decision's ``after``),
        never while the lock is held.
        """
        return _Decision(
            ok=True, state=ServiceState.RECORDING, after=self._commit_start
        )

    def _commit_start(self) -> _Decision:
        """Create and start a session without the lock, then commit under it."""
        try:
            session = self._session_factory()
            session.start()
        except Exception as exc:
            message = f"could not start recording: {exc}"
            with self._transition_scope():
                self._set_state(ServiceState.ERROR, message)
            return _Decision(
                ok=False,
                state=ServiceState.ERROR,
                error="start-failed",
                message=message,
            )
        with self._transition_scope():
            self._session = session
            self._pending_start = False
            self._set_state(ServiceState.RECORDING)
            self._start_partials_locked(session)
        return _Decision(ok=True, state=ServiceState.RECORDING)

    def _begin_transcription_locked(self) -> _Decision:
        session = self._session
        if session is None:
            message = "recording state without a session"
            self._set_state(ServiceState.ERROR, message)
            return _Decision(
                ok=False,
                state=ServiceState.ERROR,
                error="internal-error",
                message=message,
            )
        self._session = None
        self._stop_partials_locked()
        self._set_state(ServiceState.TRANSCRIBING)
        worker = threading.Thread(
            target=self._run_finish,
            args=(session,),
            name="ges2t-finish",
            daemon=True,
        )
        self._worker = worker

        def after() -> None:
            worker.start()

        return _Decision(ok=True, state=ServiceState.TRANSCRIBING, after=after)

    def _cancel_locked(self) -> _Decision:
        session = self._session
        if session is None:
            message = "recording state without a session"
            self._set_state(ServiceState.ERROR, message)
            return _Decision(
                ok=False,
                state=ServiceState.ERROR,
                error="internal-error",
                message=message,
            )
        self._session = None
        self._stop_partials_locked()
        self._set_state(ServiceState.IDLE)

        def after() -> None:
            try:
                session.cancel()
            except Exception as exc:
                self._fail(f"could not cancel the recording: {exc}")

        return _Decision(ok=True, state=ServiceState.IDLE, after=after)

    def _queue_start_locked(self) -> _Decision:
        self._pending_start = True
        return _Decision(
            ok=True,
            state=ServiceState.TRANSCRIBING,
            message="recording queued for after transcription",
        )

    # -- background work --------------------------------------------------

    def _run_finish(self, session: DictationSession) -> None:
        """Call ``session.finish()`` off the socket loop and deliver the text."""
        try:
            text = session.finish()
        except Exception as exc:
            self._fail(f"transcription failed: {exc}")
            return
        try:
            if text.strip():
                self._injector.inject(text)
        except Exception as exc:
            self._fail(f"injection failed: {exc}")
            return
        self._complete_transcription()

    def _complete_transcription(self) -> None:
        with self._transition_scope():
            pending = self._pending_start
            self._pending_start = False
            if not pending:
                self._set_state(ServiceState.IDLE)
        if pending:
            # A toggle arrived while transcribing: honour it now, once, and
            # never stack a second recording on top of an in-flight one. The
            # session is created and started with the lock released.
            self._commit_start()

    def _fail(self, message: str) -> None:
        with self._transition_scope():
            self._session = None
            self._pending_start = False
            self._stop_partials_locked()
            self._set_state(ServiceState.ERROR, message)

    @contextlib.contextmanager
    def _transition_scope(self) -> Iterator[None]:
        """Hold the state lock and run queued ``status_ui`` calls after it.

        :meth:`_set_state` only records the new state and queues the matching
        :class:`~ges2t.interfaces.StatusUI` call here; the scope invokes it once
        the lock is released. That is what lets a tray read ``service.state``
        from inside a status callback without deadlocking on the non-reentrant
        lock (ticket 21).
        """
        display: list[Callable[[], None]] = []
        try:
            with self._lock:
                self._pending_display = display
                try:
                    yield
                finally:
                    self._pending_display = None
        finally:
            for action in display:
                action()

    def _set_state(self, state: ServiceState, error_message: str | None = None) -> None:
        """Record ``state`` and queue its display call; never calls ``status_ui``.

        Must be called with the lock held inside a :meth:`_transition_scope`.
        """
        pending = self._pending_display
        assert pending is not None, "_set_state called outside a transition scope"
        self._state = state
        if state is ServiceState.IDLE:
            pending.append(self._status_ui.idle)
        elif state is ServiceState.RECORDING:
            pending.append(self._status_ui.recording)
        elif state is ServiceState.TRANSCRIBING:
            pending.append(self._status_ui.transcribing)
        else:
            message = error_message if error_message is not None else "unknown error"
            pending.append(functools.partial(self._status_ui.error, message))

    def _start_partials_locked(self, session: DictationSession) -> None:
        stop = threading.Event()
        self._partials_stop = stop

        def forward() -> None:
            try:
                for text in session.partials():
                    if stop.is_set():
                        break
                    self._status_ui.partial(text)
            except Exception:
                # Partials are display-only; a failing iterator must never
                # disturb the recording or the state machine.
                return

        thread = threading.Thread(
            target=forward, name="ges2t-partials", daemon=True
        )
        self._partials_thread = thread
        thread.start()

    def _stop_partials_locked(self) -> None:
        stop = self._partials_stop
        if stop is not None:
            stop.set()
            self._partials_stop = None

    # -- process lifecycle ------------------------------------------------

    def shutdown(self) -> None:
        """Cancel any in-progress session and stop the background threads."""
        with self._transition_scope():
            session = self._session
            self._session = None
            self._pending_start = False
            self._stop_partials_locked()
            worker = self._worker
            partials = self._partials_thread
            self._set_state(ServiceState.IDLE)
        if session is not None:
            try:
                session.cancel()
            except Exception:
                pass
        if worker is not None and worker.is_alive():
            worker.join(timeout=_SHUTDOWN_JOIN_TIMEOUT_SECONDS)
        if partials is not None and partials.is_alive():
            partials.join(timeout=_SHUTDOWN_JOIN_TIMEOUT_SECONDS)

    def serve(self) -> None:
        """Bind the socket, serve commands until a signal, then clean up.

        The import of :mod:`ges2t.socket_proto` is local so that ``socket_proto``
        can depend on this module's vocabulary without a module-level cycle.
        """
        from ges2t.socket_proto import ServiceServer, signal_shutdown

        server = ServiceServer(self._socket_path, self)
        server.bind()
        try:
            with signal_shutdown(server):
                server.serve_forever()
        finally:
            self.shutdown()
            server.close()
