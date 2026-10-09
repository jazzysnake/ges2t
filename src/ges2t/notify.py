"""Desktop notifications for actionable service errors (ADR 0013, ADR 0016).

:class:`NotifyingStatusUI` is a **pure decorator**: it wraps any
:class:`~ges2t.interfaces.StatusUI` (composition, not inheritance) and forwards
*every* member unchanged, so the tray and the no-op behave exactly as they did
before (ticket 17). On top of that it raises **one desktop notification per
distinct error message per suppression window**, and nothing else notifies:
``idle``/``recording``/``transcribing`` are tray states, and ``partial`` must
never reach a popup or the focused window (ADR 0011).

Why this exists
---------------
The service already routes failures to ``status_ui.error(message)`` (ADR 0015)
and the message is already actionable (ADR 0016). A tooltip is easy to miss
mid-dictation, and the first failure a user hits is the ``ydotoold`` daemon not
running; a popup is what turns that silent breakage into something they can act
on. The body is therefore the service's message **verbatim** -- re-wording it
would throw away the command that fixes the problem.

Why ``notify-send`` through ``subprocess``
-----------------------------------------
The project deliberately talks to the system through subprocesses rather than
Python bindings (``busctl``, ``xkbcli``) and avoids new runtime dependencies
(ADR 0001, ADR 0008, ADR 0013). ``notify-send`` is the freedesktop
Notification-Spec client already installed on the reference machine, so it is
the lightest option that matches the existing approach. The argv is an explicit
list and no shell is involved, so an error message containing quotes or ``;``
can never become a command (ADR 0016).

What stays fatal
----------------
Only failures **on the notification side** are contained: a missing
``notify-send``, no notification daemon, or a raising injected runner is logged
and swallowed, because a broken popup must never take the service down or hide
the error the popup was about (ADR 0013). An exception raised by the **wrapped**
``StatusUI`` is deliberately *not* caught here: it propagates unchanged so
``service.py``'s best-effort containment keeps owning it (ticket 17/21) and a
tray bug is not quietly converted into "the tray stopped updating".
"""

from __future__ import annotations

import logging
import subprocess
import time
from collections.abc import Callable, Sequence
from typing import Protocol

from ges2t.interfaces import LiveText, StatusUI
from ges2t.ui.status import DiagnosticStatusUI, StoppableStatusUI, TrayFailure

__all__ = [
    "SUPPRESSION_WINDOW_SECONDS",
    "NotifyingStatusUI",
    "NotificationError",
    "run_notify_send",
]

_logger = logging.getLogger(__name__)

#: How long an identical error message stays suppressed, in seconds. The ticket
#: fixes this as a module constant rather than a config key (ADR 0009): a user
#: hammering a bound key must get one popup, not a stack of identical ones.
SUPPRESSION_WINDOW_SECONDS: float = 5.0

#: The program every notification goes through (freedesktop notification spec).
_NOTIFY_SEND_PROGRAM: str = "notify-send"

#: The application name shown by the notification daemon, fixed so every ges2t
#: popup is attributable at a glance.
_APPLICATION_NAME: str = "ges2t"

#: The notification summary (the bold title). The *body* is the service's
#: actionable message, unchanged.
_SUMMARY: str = "ges2t error"

#: Errors break dictation, so the popup is urgent enough not to auto-expire
#: before it can be read. This is a one-constant change if it proves too loud.
_URGENCY: str = "critical"

#: A monotonic clock, injectable so the suppression window is tested by
#: advancing a fake rather than sleeping.
Clock = Callable[[], float]


class NotificationRunner(Protocol):
    """Delivers one notification and raises if it cannot (ADR 0019).

    The seam exists so tests never execute ``notify-send`` and never put a
    popup on the user's screen. The argument is the complete, explicit argv.
    """

    def __call__(self, argv: Sequence[str]) -> None:
        """Run ``argv`` and return once delivery was attempted."""
        ...


class NotificationError(RuntimeError):
    """``notify-send`` ran but could not deliver the notification.

    Raised by the default runner so a refused DBus call is logged with the
    daemon's own stderr instead of a bare exit status.
    """


def run_notify_send(argv: Sequence[str]) -> None:
    """Default :class:`NotificationRunner`: one ``notify-send`` subprocess.

    ``subprocess.run`` is called with an argument list and without ``shell``,
    so the message is passed as data and can never be interpreted as a command
    (ADR 0016). A non-zero exit -- the shape ``notify-send`` uses when there is
    no notification daemon, for example over SSH -- becomes a
    :class:`NotificationError`; a missing binary surfaces as the
    ``FileNotFoundError`` :mod:`subprocess` raises. Both are contained by
    :class:`NotifyingStatusUI`.
    """
    completed = subprocess.run(
        list(argv),
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        if not detail:
            detail = f"exit status {completed.returncode}"
        raise NotificationError(f"notify-send failed: {detail}")


def _notification_argv(message: str) -> list[str]:
    """Build the explicit ``notify-send`` argument list (no shell, no re-wording)."""
    return [
        _NOTIFY_SEND_PROGRAM,
        f"--app-name={_APPLICATION_NAME}",
        f"--urgency={_URGENCY}",
        _SUMMARY,
        message,
    ]


class NotifyingStatusUI:
    """A ``StatusUI`` that forwards every call and notifies on ``error`` (ADR 0013).

    Composition, not inheritance: :attr:`wrapped` receives every call unchanged,
    so wrapping a :class:`~ges2t.ui.tray.TrayStatusUI` cannot alter the tray's
    behaviour (ticket 17). ``stop`` and ``failure_report`` are forwarded too,
    when the wrapped UI implements the matching lifecycle protocol, so
    ``run_service``'s shutdown still stops the tray and reports its failures --
    a wrapper that hid those would silently regress ticket 17's observability.

    The dedup rule is part of the contract: **at most one notification per
    distinct message per** :data:`SUPPRESSION_WINDOW_SECONDS`. Distinct messages
    each notify immediately, and the *same* message notifies again only after
    the window has passed. Suppression is keyed on the message text (never on a
    state), so two different errors both get their own popup.
    """

    def __init__(
        self,
        wrapped: StatusUI,
        *,
        runner: NotificationRunner = run_notify_send,
        clock: Clock = time.monotonic,
    ) -> None:
        self._wrapped = wrapped
        self._runner = runner
        self._clock = clock
        self._last_notified: dict[str, float] = {}

    @property
    def wrapped(self) -> StatusUI:
        """The ``StatusUI`` every call is forwarded to (the tray or the no-op)."""
        return self._wrapped

    # -- StatusUI: pure forwarding (ticket 17) ----------------------------

    def idle(self) -> None:
        """Forward the idle state; never notifies."""
        self._wrapped.idle()

    def recording(self) -> None:
        """Forward the recording state; never notifies."""
        self._wrapped.recording()

    def transcribing(self) -> None:
        """Forward the transcribing state; never notifies."""
        self._wrapped.transcribing()

    def partial(self, live: LiveText) -> None:
        """Forward display-only live text; never notifies (ADR 0011)."""
        self._wrapped.partial(live)

    def error(self, message: str) -> None:
        """Notify once (best-effort), then forward the message unchanged.

        Notifying first means the popup still goes out when the wrapped display
        is the thing that is broken. The forwarded call is deliberately outside
        the notification-side ``try``: an exception from the wrapped UI must
        reach ``service.py``'s containment unchanged, never be masked here.
        """
        self._notify(message)
        self._wrapped.error(message)

    # -- lifecycle protocols the composition root also uses ---------------

    def stop(self) -> None:
        """Forward to a wrapped stoppable UI, if any (ticket 17's shutdown)."""
        if isinstance(self._wrapped, StoppableStatusUI):
            self._wrapped.stop()

    def failure_report(self) -> TrayFailure | None:
        """Forward a wrapped diagnostic UI's failure report, if any."""
        if isinstance(self._wrapped, DiagnosticStatusUI):
            return self._wrapped.failure_report()
        return None

    # -- notification side: best-effort, never raises ---------------------

    def _notify(self, message: str) -> None:
        """Send the message unless it was sent inside the suppression window."""
        now = self._clock()
        last = self._last_notified.get(message)
        if last is not None and now - last < SUPPRESSION_WINDOW_SECONDS:
            return
        # Record the attempt *before* running it, so a notifier that keeps
        # failing is not retried in a burst either.
        self._last_notified[message] = now
        self._prune(now)
        try:
            self._runner(_notification_argv(message))
        except Exception as exc:  # deliberately broad: the popup path must never raise
            _logger.warning(
                "could not send the desktop notification for %r: %s", message, exc
            )

    def _prune(self, now: float) -> None:
        """Forget messages older than the window so the map stays bounded."""
        for message, sent_at in list(self._last_notified.items()):
            if now - sent_at >= SUPPRESSION_WINDOW_SECONDS:
                del self._last_notified[message]
