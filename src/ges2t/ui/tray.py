"""The real tray status UI, via ``pystray``'s AppIndicator backend (ADR 0013).

Why this module is unusually careful
------------------------------------
On the reference machine ``pystray`` 0.19.5 selects its XEmbed (``_xorg``)
backend whenever ``gi`` is not importable, and Plasma 6.7.5 no longer displays
XEmbed icons. The result is a process that looks healthy and an icon that never
appears. ADR 0013 exists to stop that, so :func:`assert_appindicator_backend`
proves the selected backend is ``appindicator`` **before** an icon is created
and raises :class:`TrayBackendError` otherwise. The composition root turns that
error into a printed warning and a :class:`~ges2t.ui.status.NullStatusUI`, so a
missing tray never makes ges2t unusable and nothing is reachable only through
the tray.

``gi`` and ``pystray`` are imported **inside functions**, never at module import
time, so the service, the CLI and the headless tests all work with no GTK present
(ADR 0014).

Failure visibility (the observability requirement)
--------------------------------------------------
:mod:`ges2t.service` contains every :class:`~ges2t.interfaces.StatusUI`
callback exception, deliberately, so a broken display can never wedge the state
machine in ``TRANSCRIBING``. That safety net would otherwise make a tray that
raises on every call disappear silently, which is the same failure class ADR
0013 exists to prevent. :class:`TrayStatusUI` therefore catches its own callback
failures, records them (:meth:`failure_report`), and prints the first occurrence
of each distinct failure to stderr. :func:`ges2t.pipeline.build_status_ui` and
``run_service`` surface the record through
:class:`~ges2t.ui.status.DiagnosticStatusUI` at shutdown as well.
"""

from __future__ import annotations

import os
import signal
import sys
import threading
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ges2t.service import Command, CommandResponse
from ges2t.ui.status import StatusState, TrayFailure

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage

__all__ = [
    "TrayBackendError",
    "TrayStatusUI",
    "assert_appindicator_backend",
    "build_tray_status_ui",
]

#: The tray icon's stable name; the tooltip prefixes it.
_TITLE = "ges2t"

#: The exact module a visible, AppIndicator-backed ``pystray`` icon comes from.
_APPINDICATOR_MODULE = "pystray._appindicator"

#: The typelibs ``pystray._appindicator`` accepts, in its own preference order.
_APPINDICATOR_TYPELIBS = ("AppIndicator3", "AyatanaAppIndicator3")

#: The Arch packages the AppIndicator backend needs (ADR 0013).
_REQUIRED_PACKAGES = "'python-gobject', 'gtk3' and 'libappindicator'"

#: How long :meth:`TrayStatusUI.stop` waits for the icon thread to finish.
_STOP_JOIN_SECONDS = 2.0

#: One RGBA colour per display state, used for the in-code generated icon.
_STATE_COLOURS: Mapping[StatusState, tuple[int, int, int, int]] = {
    StatusState.IDLE: (96, 108, 122, 255),
    StatusState.RECORDING: (211, 47, 47, 255),
    StatusState.TRANSCRIBING: (245, 158, 11, 255),
    StatusState.ERROR: (136, 14, 79, 255),
}

#: The generated icon is 22 px square, the AppIndicator convention on Plasma.
_ICON_SIZE = 22

#: A status UI command sender: the signature of ``ges2t.socket_proto.send_command``
#: once its socket path is bound. The return value is discarded by the tray.
CommandSender = Callable[[str], CommandResponse]

#: A request to shut the service process down, used by the tray's Quit item.
ShutdownRequest = Callable[[], None]


class TrayBackendError(RuntimeError):
    """``pystray`` would not select the visible AppIndicator backend (ADR 0013).

    Raising this is the whole point: it is better to have no tray than a tray
    that silently never appears on Plasma 6.
    """


def _backend_error(reason: str) -> TrayBackendError:
    """Build the actionable refusal message (the three packages and the venv)."""
    return TrayBackendError(
        "refusing to start a tray that Plasma 6 cannot display: "
        f"{reason}. Install the Arch packages {_REQUIRED_PACKAGES} and create "
        "the virtualenv with 'uv venv --system-site-packages --python "
        "/usr/bin/python3' so 'gi' is importable, then run 'ges2t doctor' "
        "(ADR 0013)."
    )


def assert_appindicator_backend() -> None:
    """Prove ``pystray`` selected the AppIndicator backend, or raise.

    Two checks, in order, so the failure is named precisely:

    1. ``gi.require_version`` for ``AppIndicator3`` (or, when only the Ayatana
       fork is installed, ``AyatanaAppIndicator3``) — proves ``python-gobject``
       and an ``libappindicator`` typelib are visible to this interpreter. The
       two namespaces mirror ``pystray._appindicator``'s own fallback, so a
       working Ayatana-only system is accepted rather than falsely refused
       (review 002, non-blocking finding). A venv created without
       ``--system-site-packages`` fails here with
       ``ModuleNotFoundError: No module named 'gi'``.
    2. ``pystray.Icon``'s defining module — ``pystray`` resolves its backend at
       import time by trying ``_appindicator``, then ``_gtk``, then ``_xorg``,
       and the class it exposes records which one won. This is the check the
       earlier probe used: it resolves to ``pystray._xorg`` when ``gi`` is
       absent, and the assertion refuses that rather than showing nothing.

    :raises TrayBackendError: when either check fails.
    """
    try:
        import gi
    except Exception as exc:
        raise _backend_error(f"'gi' could not be imported ({exc})") from exc

    for namespace in _APPINDICATOR_TYPELIBS:
        try:
            gi.require_version(namespace, "0.1")
        except ValueError:
            continue
        break
    else:
        raise _backend_error(
            "'gi' has neither the AppIndicator3 nor the AyatanaAppIndicator3 typelib"
        )

    try:
        import pystray
    except Exception as exc:
        raise _backend_error(f"pystray could not be imported ({exc})") from exc

    selected = getattr(pystray.Icon, "__module__", None)
    if selected != _APPINDICATOR_MODULE:
        raise _backend_error(
            f"pystray selected the {selected or 'unknown'!r} backend instead of "
            "the AppIndicator one"
        )


@runtime_checkable
class TrayIcon(Protocol):
    """The slice of ``pystray.Icon`` :class:`TrayStatusUI` drives.

    Declared as a Protocol so the failure-handling tests can inject a broken
    icon and prove the failures are recorded, without a display or a real tray
    (ADR 0019).
    """

    title: str
    icon: object

    def run(self, setup: Callable[["TrayIcon"], None] | None = None) -> None:
        """Run the icon's event loop until :meth:`stop` is called."""
        ...

    def stop(self) -> None:
        """Stop the event loop."""
        ...

    def update_menu(self) -> None:
        """Refresh the native menu after a state change."""
        ...


def _signal_process_shutdown() -> None:
    """Ask the service process to exit the way ``SIGTERM`` does (ADR 0015).

    The tray runs inside the service process (ADR 0015) and the socket protocol
    deliberately has no ``quit`` verb, so Quit raises the signal the service
    already handles cleanly. Quit is a convenience: Ctrl-C and the CLI remain
    the primary paths, and nothing is reachable only through the tray (ADR 0013).
    """
    os.kill(os.getpid(), signal.SIGTERM)


def _state_image(state: StatusState) -> PILImage:
    """Generate the tiny state icon in code; no icon-theme dependency (ADR 0013)."""
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (_ICON_SIZE, _ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((2, 2, _ICON_SIZE - 3, _ICON_SIZE - 3), fill=_STATE_COLOURS[state])
    return image


class TrayStatusUI:
    """The ``StatusUI`` implementation backed by a ``pystray`` AppIndicator icon.

    State callbacks only mutate the display (:meth:`idle`/:meth:`recording`/
    :meth:`transcribing`/:meth:`error`/:meth:`partial`); they never call the
    service back, so the state machine can never block on the tray (ADR 0015).
    Every icon operation is wrapped so a failing tray records and reports the
    failure instead of disappearing silently.

    The icon's event loop runs on its own daemon thread, started by
    :meth:`start`; :meth:`stop` ends it cleanly.
    """

    def __init__(
        self,
        send_command: CommandSender,
        *,
        request_shutdown: ShutdownRequest | None = None,
        icon: TrayIcon | None = None,
    ) -> None:
        self._send_command = send_command
        self._request_shutdown: ShutdownRequest = (
            request_shutdown if request_shutdown is not None else _signal_process_shutdown
        )
        #: ``icon`` is injected by the failure tests; production builds it in
        #: :meth:`start` after the backend assertion.
        self._icon = icon
        self._state = StatusState.IDLE
        self._partial = ""
        self._last_error: str | None = None
        self._failures = 0
        self._last_failure: str | None = None
        self._printed_failure: str | None = None
        self._thread: threading.Thread | None = None
        self._stopped = False

    # -- diagnostics ------------------------------------------------------

    @property
    def failure_count(self) -> int:
        """How many of this UI's own callbacks have raised so far."""
        return self._failures

    @property
    def last_failure(self) -> str | None:
        """The action and exception of the most recent failure, or ``None``."""
        return self._last_failure

    def failure_report(self) -> TrayFailure | None:
        """Return the recorded failures for ``doctor``/``status``, or ``None``.

        Implements :class:`~ges2t.ui.status.DiagnosticStatusUI`.
        """
        if self._failures == 0 or self._last_failure is None:
            return None
        return TrayFailure(count=self._failures, last_message=self._last_failure)

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        """Assert the backend, create the icon, and run it on its own thread.

        The assertion runs **before** the icon is created, so an invisible
        backend is refused rather than merely looking healthy (ADR 0013).

        :raises TrayBackendError: when the selected backend is not AppIndicator.
        """
        if self._thread is not None or self._stopped:
            return
        icon = self._icon
        if icon is None:
            assert_appindicator_backend()
            icon = self._create_icon()
            self._icon = icon
        self._apply_display()
        thread = threading.Thread(target=self._run_icon, name="ges2t-tray", daemon=True)
        self._thread = thread
        thread.start()

    def stop(self) -> None:
        """Stop the icon thread so the service can exit cleanly.

        Also implements :class:`~ges2t.ui.status.StoppableStatusUI`.
        """
        self._stopped = True
        icon = self._icon
        if icon is None:
            return
        self._guard("stop", icon.stop)
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=_STOP_JOIN_SECONDS)

    # -- StatusUI ---------------------------------------------------------

    def idle(self) -> None:
        """Show the idle state."""
        self._set_state(StatusState.IDLE)

    def recording(self) -> None:
        """Show the recording state."""
        self._set_state(StatusState.RECORDING)

    def transcribing(self) -> None:
        """Show the transcribing state."""
        self._set_state(StatusState.TRANSCRIBING)

    def error(self, message: str) -> None:
        """Show the error state with the actionable message in the tooltip."""
        self._last_error = message
        self._set_state(StatusState.ERROR)

    def partial(self, text: str) -> None:
        """Show display-only live text in the tooltip; it is **never** typed (ADR 0011)."""
        self._partial = text
        self._apply_display()

    # -- internals --------------------------------------------------------

    def _set_state(self, state: StatusState) -> None:
        self._state = state
        self._partial = ""
        self._apply_display()

    def _tooltip(self) -> str:
        if self._state is StatusState.ERROR:
            base = f"{_TITLE}: error - {self._last_error or 'unknown error'}"
        else:
            base = f"{_TITLE}: {self._state.value}"
        if self._partial:
            return f"{base}\n{self._partial}"
        return base

    def _apply_display(self) -> None:
        """Push the current state, tooltip and partial onto the icon, guarded."""
        icon = self._icon
        if icon is None:
            return
        image = _state_image(self._state)
        tooltip = self._tooltip()

        def update() -> None:
            icon.icon = image
            icon.title = tooltip
            icon.update_menu()

        self._guard("state update", update)

    def _run_icon(self) -> None:
        icon = self._icon
        if icon is None:
            return
        try:
            icon.run()
        except Exception as exc:
            self._record_failure("tray loop", exc)

    def _create_icon(self) -> TrayIcon:
        """Build the ``pystray`` menu and icon (only called after the assertion)."""
        import pystray

        menu = pystray.Menu(
            pystray.MenuItem("Toggle dictation", self._on_toggle, default=True),
            pystray.MenuItem("Cancel", self._on_cancel),
            pystray.MenuItem("Quit", self._on_quit),
        )
        return pystray.Icon(
            _TITLE,
            icon=_state_image(self._state),
            title=self._tooltip(),
            menu=menu,
        )

    def _on_toggle(self, icon: object, item: object) -> None:
        self._send(Command.TOGGLE)

    def _on_cancel(self, icon: object, item: object) -> None:
        self._send(Command.CANCEL)

    def _on_quit(self, icon: object, item: object) -> None:
        self._guard("quit", self._request_shutdown)

    def _send(self, command: Command) -> None:
        """Send one verb over the service socket, guarded like every display call."""

        def call() -> None:
            self._send_command(command.value)

        self._guard(f"{command.value} command", call)

    def _guard(self, action: str, operation: Callable[[], None]) -> None:
        """Run ``operation``; on failure record it rather than letting it escape."""
        try:
            operation()
        except Exception as exc:
            self._record_failure(action, exc)

    def _record_failure(self, action: str, exc: BaseException) -> None:
        """Count a failure, keep the last message, and print each distinct one once."""
        self._failures += 1
        message = f"{action}: {exc}"
        self._last_failure = message
        if message != self._printed_failure:
            self._printed_failure = message
            print(f"ges2t: {TrayFailure(self._failures, message).summary()}", file=sys.stderr)


def build_tray_status_ui(send_command: CommandSender) -> TrayStatusUI:
    """Construct and start the AppIndicator tray, or raise :class:`TrayBackendError`.

    This is the composition root's single entry point into the tray. Building is
    separated from construction so tests can exercise :class:`TrayStatusUI` with
    an injected icon and no display.
    """
    ui = TrayStatusUI(send_command)
    ui.start()
    return ui
