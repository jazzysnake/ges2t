"""The socket protocol: codec, server lifecycle, and client failure modes.

These tests use a real Unix socket in ``tmp_path`` and short timeouts, so the
suite stays fast and never touches ``$XDG_RUNTIME_DIR`` or a live service.
"""

from __future__ import annotations

import contextlib
import socket
import stat
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from ges2t.service import CommandResponse, ServiceState
from ges2t.socket_proto import (
    DEFAULT_SOCKET_NAME,
    SOCKET_MODE,
    CommandHandler,
    CommandRequest,
    ProtocolError,
    ServiceAlreadyRunningError,
    ServiceNotRunningError,
    ServiceServer,
    decode_request,
    decode_response,
    default_socket_path,
    encode_request,
    encode_response,
    is_service_listening,
    send_command,
)


class _EchoHandler:
    """A minimal :class:`CommandHandler` for transport tests."""

    def __init__(self) -> None:
        self.commands: list[str] = []
        self._state = ServiceState.IDLE

    @property
    def state(self) -> ServiceState:
        return self._state

    def handle(self, command: str) -> CommandResponse:
        self.commands.append(command)
        if command == "toggle":
            self._state = ServiceState.RECORDING
        elif command == "cancel":
            self._state = ServiceState.IDLE
        return CommandResponse(
            ok=True, state=self._state, message=f"handled {command}"
        )


@contextlib.contextmanager
def running_server(
    path: Path, handler: CommandHandler | None = None
) -> Iterator[ServiceServer]:
    server = ServiceServer(path, handler if handler is not None else _EchoHandler())
    server.bind()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.stop()
        thread.join(timeout=2.0)
        server.close()


# -- codec ---------------------------------------------------------------


@pytest.mark.parametrize(
    "command", ["toggle", "ptt-start", "ptt-stop", "cancel", "status"]
)
def test_request_round_trip(command: str) -> None:
    encoded = encode_request(CommandRequest(cmd=command))
    assert encoded.endswith(b"\n")
    assert decode_request(encoded.decode("utf-8").strip()) == CommandRequest(
        cmd=command
    )


@pytest.mark.parametrize(
    "response",
    [
        CommandResponse(ok=True, state=ServiceState.IDLE),
        CommandResponse(
            ok=True, state=ServiceState.RECORDING, message="queued recording"
        ),
        CommandResponse(
            ok=False,
            state=ServiceState.ERROR,
            error="injection-failed",
            message="could not type the text",
        ),
    ],
)
def test_response_round_trip(response: CommandResponse) -> None:
    encoded = encode_response(response)
    assert encoded.endswith(b"\n")
    assert decode_response(encoded.decode("utf-8").strip()) == response


@pytest.mark.parametrize(
    "line",
    [
        "[]",
        "{not json}",
        '{"state": "idle"}',
        '{"cmd": 3}',
    ],
)
def test_decode_request_rejects_malformed_lines(line: str) -> None:
    with pytest.raises(ProtocolError):
        decode_request(line)


@pytest.mark.parametrize(
    "line",
    [
        "[]",
        '{"ok": "yes", "state": "idle", "error": null, "message": null}',
        '{"ok": true, "state": "levitating", "error": null, "message": null}',
        '{"ok": true, "state": "idle", "error": 5, "message": null}',
        '{"ok": true, "state": "idle", "error": null, "message": 5}',
    ],
)
def test_decode_response_rejects_malformed_lines(line: str) -> None:
    with pytest.raises(ProtocolError):
        decode_response(line)


def test_default_socket_path_uses_xdg_runtime_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert default_socket_path() == tmp_path / DEFAULT_SOCKET_NAME


# -- server --------------------------------------------------------------


def test_bind_creates_the_socket_with_mode_0600(tmp_path: Path) -> None:
    path = tmp_path / "ges2t.sock"
    server = ServiceServer(path, _EchoHandler())
    try:
        server.bind()
        assert path.exists()
        assert stat.S_IMODE(path.stat().st_mode) == SOCKET_MODE
        assert SOCKET_MODE == 0o600
    finally:
        server.close()


def test_close_removes_the_socket_file(tmp_path: Path) -> None:
    path = tmp_path / "ges2t.sock"
    server = ServiceServer(path, _EchoHandler())
    server.bind()
    assert path.exists()
    server.close()
    assert not path.exists()


def test_round_trip_over_a_real_socket(tmp_path: Path) -> None:
    path = tmp_path / "ges2t.sock"
    handler = _EchoHandler()
    with running_server(path, handler):
        first = send_command(path, "status", timeout=1.0)
        assert first.ok is True
        assert first.state is ServiceState.IDLE
        assert first.message == "handled status"

        second = send_command(path, "toggle", timeout=1.0)
        assert second.state is ServiceState.RECORDING

        third = send_command(path, "status", timeout=1.0)
        assert third.state is ServiceState.RECORDING
        assert handler.commands == ["status", "toggle", "status"]


def test_stale_socket_is_removed_and_rebound(tmp_path: Path) -> None:
    path = tmp_path / "ges2t.sock"
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(path))
    stale.close()  # the file survives, but nothing is listening on it
    assert path.exists()
    assert is_service_listening(path) is False

    server = ServiceServer(path, _EchoHandler())
    try:
        server.bind()
        assert path.exists()
        assert stat.S_IMODE(path.stat().st_mode) == SOCKET_MODE
    finally:
        server.close()


def test_second_service_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "ges2t.sock"
    first = ServiceServer(path, _EchoHandler())
    first.bind()
    try:
        second = ServiceServer(path, _EchoHandler())
        with pytest.raises(ServiceAlreadyRunningError) as caught:
            second.bind()
        assert "already listening" in str(caught.value)
        assert path.exists()
    finally:
        first.close()


def test_malformed_request_gets_a_bad_request_response(tmp_path: Path) -> None:
    path = tmp_path / "ges2t.sock"
    with running_server(path):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(1.0)
            sock.connect(str(path))
            sock.sendall(b"this is not json\n")
            data = b""
            while b"\n" not in data:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
    line = data.split(b"\n", 1)[0].decode("utf-8")
    response = decode_response(line)
    assert response.ok is False
    assert response.error == "bad-request"
    assert response.state is ServiceState.IDLE


def test_is_service_listening_detects_a_live_socket(tmp_path: Path) -> None:
    path = tmp_path / "ges2t.sock"
    with running_server(path):
        assert is_service_listening(path) is True
    assert is_service_listening(path) is False


def test_probe_does_not_confuse_a_missing_file(tmp_path: Path) -> None:
    assert is_service_listening(tmp_path / "absent.sock") is False


# -- client --------------------------------------------------------------


def test_client_reports_missing_socket(tmp_path: Path) -> None:
    with pytest.raises(ServiceNotRunningError) as caught:
        send_command(tmp_path / "absent.sock", "status", timeout=0.5)
    assert "not running" in str(caught.value)
    assert "ges2t run" in str(caught.value)


def test_client_reports_a_stale_socket(tmp_path: Path) -> None:
    path = tmp_path / "ges2t.sock"
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(path))
    stale.close()
    with pytest.raises(ServiceNotRunningError):
        send_command(path, "status", timeout=0.5)


def test_client_never_hangs_on_a_silent_peer(tmp_path: Path) -> None:
    path = tmp_path / "ges2t.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen(1)
    try:
        started = time.monotonic()
        with pytest.raises(ServiceNotRunningError):
            send_command(path, "status", timeout=0.2)
        assert time.monotonic() - started < 1.0
    finally:
        listener.close()
