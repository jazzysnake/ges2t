"""Newline-delimited JSON over a Unix domain socket (ADR 0015).

This module is the transport half of the service: both ends of the wire and the
protocol types that describe it. The service state machine lives in
:mod:`ges2t.service`; this module depends on that module's vocabulary
(:class:`~ges2t.service.ServiceState`, :class:`~ges2t.service.CommandResponse`)
because the message schema is the state machine's, not the other way round.

Wire format
-----------
One JSON object per line, UTF-8, ``\\n``-terminated. A request is::

    {"cmd": "toggle"}

and a response is::

    {"ok": true, "state": "recording", "error": null, "message": null}

Socket
------
The default path is ``$XDG_RUNTIME_DIR/ges2t.sock`` and the file is created with
mode ``0600`` (the runtime directory is already user-private; the socket holds no
secrets, but the mode is part of the contract). Only one service may own the
path: :meth:`ServiceServer.bind` refuses to start when something is already
listening, and removes a stale socket file (one nothing is listening on) before
rebinding.

Failure policy
--------------
The client helper :func:`send_command` uses bounded connect and read timeouts and
raises :class:`ServiceNotRunningError` with an actionable sentence rather than
ever hanging — a hotkey bound to ``ges2t toggle`` must fail fast (ADR 0004).
"""

from __future__ import annotations

import contextlib
import json
import os
import selectors
import signal
import socket
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Protocol, runtime_checkable

from ges2t.service import CommandResponse, ServiceState

__all__ = [
    "CommandHandler",
    "CommandRequest",
    "DEFAULT_CLIENT_TIMEOUT_SECONDS",
    "DEFAULT_SOCKET_NAME",
    "ProtocolError",
    "SOCKET_MODE",
    "ServiceAlreadyRunningError",
    "ServiceNotRunningError",
    "ServiceServer",
    "decode_request",
    "decode_response",
    "default_socket_path",
    "encode_request",
    "encode_response",
    "is_service_listening",
    "send_command",
    "signal_shutdown",
]

#: File name inside ``$XDG_RUNTIME_DIR``.
DEFAULT_SOCKET_NAME = "ges2t.sock"

#: Permission bits for the created socket file.
SOCKET_MODE = 0o600

#: Default bound for a CLI command's connect and read.
DEFAULT_CLIENT_TIMEOUT_SECONDS = 2.0

#: How long the server waits for a client to send its one request line.
_SERVER_READ_TIMEOUT_SECONDS = 2.0

#: Kernel listen backlog; the service answers clients sequentially.
_LISTEN_BACKLOG = 8

#: How long the single-instance probe waits for a connect to succeed.
_PROBE_TIMEOUT_SECONDS = 0.2


class ProtocolError(ValueError):
    """A message on the wire was not valid newline-delimited JSON for this protocol."""


class ServiceAlreadyRunningError(RuntimeError):
    """A second service tried to bind a socket that is already being served."""


class ServiceNotRunningError(RuntimeError):
    """The client could not reach a service, or the service did not answer in time."""


@dataclass(frozen=True)
class CommandRequest:
    """One request line: the verb the CLI wants the service to apply."""

    cmd: str


@runtime_checkable
class CommandHandler(Protocol):
    """The server's view of a service: current state plus command dispatch.

    :class:`ges2t.service.Service` satisfies this structurally; the Protocol
    exists so the transport depends on a seam rather than a concrete class.
    """

    @property
    def state(self) -> ServiceState:
        """The current state, for protocol-error responses."""
        ...

    def handle(self, command: str) -> CommandResponse:
        """Apply ``command`` and return the response to send back."""
        ...


# -- protocol codec -------------------------------------------------------


def default_socket_path() -> Path:
    """Return ``$XDG_RUNTIME_DIR/ges2t.sock`` (or a per-user fallback).

    The runtime directory is the right place for a per-session socket: it is
    private, it is cleared on logout, and its owner is the user. When
    ``XDG_RUNTIME_DIR`` is unset (an unusual session), a per-uid path under the
    system temporary directory is used instead of failing outright.
    """
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        return Path(runtime_dir) / DEFAULT_SOCKET_NAME
    return Path(tempfile.gettempdir()) / f"ges2t-{os.getuid()}.sock"


def encode_request(request: CommandRequest) -> bytes:
    """Encode ``request`` as one newline-terminated JSON line."""
    payload = {"cmd": request.cmd}
    return _encode_line(payload)


def decode_request(line: str) -> CommandRequest:
    """Parse one request line, raising :class:`ProtocolError` when it is not valid."""
    payload: object = _loads(line, "request")
    if not isinstance(payload, dict):
        raise ProtocolError("request must be a JSON object")
    cmd = payload.get("cmd")
    if not isinstance(cmd, str):
        raise ProtocolError("request must contain a string 'cmd' field")
    return CommandRequest(cmd=cmd)


def encode_response(response: CommandResponse) -> bytes:
    """Encode ``response`` as one newline-terminated JSON line."""
    payload = {
        "ok": response.ok,
        "state": response.state.value,
        "error": response.error,
        "message": response.message,
    }
    return _encode_line(payload)


def decode_response(line: str) -> CommandResponse:
    """Parse one response line, raising :class:`ProtocolError` when it is not valid."""
    payload: object = _loads(line, "response")
    if not isinstance(payload, dict):
        raise ProtocolError("response must be a JSON object")
    ok = payload.get("ok")
    state_name = payload.get("state")
    error = payload.get("error")
    message = payload.get("message")
    if not isinstance(ok, bool):
        raise ProtocolError("response field 'ok' must be a boolean")
    if not isinstance(state_name, str):
        raise ProtocolError("response field 'state' must be a string")
    try:
        state = ServiceState(state_name)
    except ValueError as exc:
        raise ProtocolError(f"unknown service state: {state_name!r}") from exc
    if error is not None and not isinstance(error, str):
        raise ProtocolError("response field 'error' must be a string or null")
    if message is not None and not isinstance(message, str):
        raise ProtocolError("response field 'message' must be a string or null")
    return CommandResponse(ok=ok, state=state, error=error, message=message)


def _encode_line(payload: Mapping[str, object]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


def _loads(line: str, kind: str) -> object:
    """Parse one JSON line, translating a syntax error into :class:`ProtocolError`."""
    try:
        return json.loads(line)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"{kind} is not valid JSON: {exc}") from exc


def _read_line(sock: socket.socket, *, deadline: float) -> str:
    """Read one newline-terminated line, returning ``""`` on immediate EOF.

    ``deadline`` is an absolute :func:`time.monotonic` value; each ``recv`` is
    bounded by the time remaining, so a silent peer cannot make the caller hang.
    """
    buffer = bytearray()
    while True:
        newline = buffer.find(b"\n")
        if newline >= 0:
            return buffer[:newline].decode("utf-8")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("timed out waiting for a newline-terminated message")
        sock.settimeout(remaining)
        try:
            chunk = sock.recv(4096)
        except TimeoutError:
            raise TimeoutError(
                "timed out waiting for a newline-terminated message"
            ) from None
        if not chunk:
            if not buffer:
                return ""
            return buffer.decode("utf-8")
        buffer.extend(chunk)


# -- server ---------------------------------------------------------------


class ServiceServer:
    """The listening end of the protocol.

    Lifecycle: :meth:`bind` creates the socket and enforces single instance,
    :meth:`serve_forever` accepts and answers connections until :meth:`stop`, and
    :meth:`close` releases the socket and unlinks the path. The server answers
    clients one at a time, which keeps the state machine single-threaded and lets
    each command's reply be determined before the next one is read.
    """

    def __init__(self, path: Path, handler: CommandHandler) -> None:
        self._path = path
        self._handler = handler
        self._socket: socket.socket | None = None
        self._stop_event = threading.Event()
        self._wake_read: int | None = None
        self._wake_write: int | None = None

    @property
    def path(self) -> Path:
        """The socket path this server owns."""
        return self._path

    def bind(self) -> None:
        """Create the socket, or raise if another service already owns the path.

        A path that exists but refuses connections is a stale file left by a
        crashed service; it is removed and rebound. A path that accepts a
        connection belongs to a live service, and refusing to start is the
        single-instance guarantee (ADR 0015).
        """
        if self._path.exists():
            if is_service_listening(self._path):
                raise ServiceAlreadyRunningError(
                    f"a ges2t service is already listening on {self._path}; "
                    "only one may run per session"
                )
            self._path.unlink()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.bind(str(self._path))
        except OSError:
            sock.close()
            raise
        os.chmod(self._path, SOCKET_MODE)
        sock.listen(_LISTEN_BACKLOG)
        sock.setblocking(False)
        self._socket = sock

    def serve_forever(self) -> None:
        """Answer connections until :meth:`stop` is called.

        A self-pipe registered with a selector wakes the loop on stop, so idle
        means a blocking ``select`` with no CPU and no polling (ADR 0015).
        """
        sock = self._socket
        if sock is None:
            raise RuntimeError("bind() must be called before serve_forever()")
        selector = selectors.DefaultSelector()
        wake_read, wake_write = os.pipe()
        os.set_blocking(wake_write, False)
        self._wake_read = wake_read
        self._wake_write = wake_write
        selector.register(sock, selectors.EVENT_READ, "listen")
        selector.register(wake_read, selectors.EVENT_READ, "wake")
        try:
            while not self._stop_event.is_set():
                for key, _ in selector.select():
                    if key.data == "wake":
                        try:
                            os.read(wake_read, 4096)
                        except BlockingIOError:
                            pass
                    else:
                        self._accept_pending(sock)
        finally:
            selector.close()
            os.close(wake_read)
            os.close(wake_write)
            self._wake_read = None
            self._wake_write = None

    def stop(self) -> None:
        """Ask :meth:`serve_forever` to return. Safe to call from a signal handler."""
        self._stop_event.set()
        wake_write = self._wake_write
        if wake_write is not None:
            try:
                os.write(wake_write, b"\x00")
            except (BlockingIOError, OSError):
                pass

    def close(self) -> None:
        """Stop serving, close the socket, and remove the socket file."""
        self.stop()
        sock = self._socket
        self._socket = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass

    def _accept_pending(self, sock: socket.socket) -> None:
        while True:
            try:
                conn, _ = sock.accept()
            except (BlockingIOError, OSError):
                return
            with conn:
                self._handle_connection(conn)

    def _handle_connection(self, conn: socket.socket) -> None:
        try:
            line = _read_line(
                conn, deadline=time.monotonic() + _SERVER_READ_TIMEOUT_SECONDS
            )
        except (TimeoutError, OSError):
            return
        if not line:
            return
        try:
            request = decode_request(line)
            response = self._handler.handle(request.cmd)
        except ProtocolError as exc:
            response = CommandResponse(
                ok=False,
                state=self._handler.state,
                error="bad-request",
                message=str(exc),
            )
        except Exception as exc:  # a handler bug must not kill the server
            response = CommandResponse(
                ok=False,
                state=self._handler.state,
                error="internal-error",
                message=str(exc),
            )
        try:
            conn.sendall(encode_response(response))
        except OSError:
            return


def is_service_listening(path: Path, *, timeout: float = _PROBE_TIMEOUT_SECONDS) -> bool:
    """Return whether something accepts connections at ``path``.

    Used by :meth:`ServiceServer.bind` (stale-socket detection) and by ``doctor``
    (a socket that exists but refuses connections is a stale service).
    """
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(timeout)
            probe.connect(str(path))
    except OSError:
        return False
    return True


SignalHandler = Callable[[int, FrameType | None], object] | int | None


@contextlib.contextmanager
def signal_shutdown(
    server: ServiceServer,
    *,
    signals: Sequence[int] = (signal.SIGINT, signal.SIGTERM),
) -> Iterator[None]:
    """Stop ``server`` cleanly when SIGINT/SIGTERM arrives.

    Signal handlers may only be installed from the main thread; when the context
    manager is entered from another thread it does nothing rather than raising.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def handle(signum: int, frame: FrameType | None) -> None:
        server.stop()

    installed: dict[int, SignalHandler] = {}
    try:
        for signum in signals:
            installed[signum] = signal.signal(signum, handle)
        yield
    finally:
        for signum, previous in installed.items():
            signal.signal(signum, previous)


# -- client ---------------------------------------------------------------


def send_command(
    path: Path,
    command: str,
    *,
    timeout: float = DEFAULT_CLIENT_TIMEOUT_SECONDS,
) -> CommandResponse:
    """Send one command to a running service and return its response.

    Bounded on both the connect and the read. Any transport failure, including a
    timeout, becomes a :class:`ServiceNotRunningError` with a sentence the CLI
    can print verbatim, so a bound hotkey fails fast instead of hanging.
    """
    request = CommandRequest(cmd=command)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            try:
                sock.connect(str(path))
            except OSError as exc:
                raise ServiceNotRunningError(
                    _not_running_message(path, exc)
                ) from exc
            try:
                sock.sendall(encode_request(request))
                line = _read_line(sock, deadline=time.monotonic() + timeout)
            except TimeoutError as exc:
                raise ServiceNotRunningError(
                    f"the ges2t service at {path} did not answer within "
                    f"{timeout:.1f}s; it may be stuck"
                ) from exc
            except OSError as exc:
                raise ServiceNotRunningError(
                    _not_running_message(path, exc)
                ) from exc
    except ServiceNotRunningError:
        raise
    except OSError as exc:
        raise ServiceNotRunningError(_not_running_message(path, exc)) from exc
    if not line:
        raise ServiceNotRunningError(
            f"the ges2t service at {path} closed the connection without answering"
        )
    return decode_response(line)


def _not_running_message(path: Path, exc: OSError) -> str:
    return (
        f"the ges2t service is not running (no answer from {path}: {exc}); "
        "start it with 'ges2t run'"
    )
