"""Status-display vocabulary shared by every ``StatusUI`` (ADR 0013, ADR 0014).

This module is deliberately stdlib-only. The no-op UI, the display-state enum
and the diagnostic records live here so the concrete tray in
:mod:`ges2t.ui.tray` can import them without importing ``gi`` or ``pystray``,
and so the composition root can talk about "a stoppable/diagnostic status UI"
without naming a concrete class (ADR 0019).

``NullStatusUI`` used to live in :mod:`ges2t.pipeline`; it was moved here by
ticket ``live-dictation-ux/tray-status-ui`` so both the batch composition root
and the tray share one definition.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from ges2t.interfaces import LiveText

__all__ = [
    "DiagnosticStatusUI",
    "NullStatusUI",
    "StoppableStatusUI",
    "StatusState",
    "TrayFailure",
]


class StatusState(StrEnum):
    """The state a status display can be in (the display-side mirror of the service).

    The member values are deliberately identical to
    :class:`ges2t.service.ServiceState` so the tray tooltip and the wire state
    cannot spell the same state differently. ``tests/test_status_ui.py`` pins
    that equality so the two enums cannot drift.
    """

    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    ERROR = "error"


@dataclass(frozen=True)
class TrayFailure:
    """The recorded evidence that a ``StatusUI`` callback raised.

    ``count`` is the number of failures since the UI was constructed and
    ``last_message`` is the action and exception of the most recent one. This
    is the value ``ges2t doctor`` and ``ges2t status`` need to surface the
    otherwise-invisible "the tray broke and stopped updating" state (ADR 0013).
    """

    count: int
    last_message: str

    def summary(self) -> str:
        """Return the human sentence a diagnostic command prints."""
        return f"the tray has failed {self.count} times: {self.last_message}"


class NullStatusUI:
    """The documented no-op :class:`~ges2t.interfaces.StatusUI`.

    ``idle``/``recording``/``transcribing``/``partial`` do nothing: there is no
    display surface. ``error`` is the deliberate exception — it writes the
    message to stderr, because otherwise a runtime failure (for example the
    adapter rejecting a window longer than the engine's 30 s ceiling, ADR 0005)
    would be invisible until the tray or notifications land. This is also the
    fallback when the tray backend assertion fails (ADR 0013), and it is what
    keeps ges2t fully usable with no tray at all.
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

    def partial(self, live: LiveText) -> None:
        """Show display-only live text (no-op; never typed, ADR 0011)."""


@runtime_checkable
class StoppableStatusUI(Protocol):
    """A :class:`~ges2t.interfaces.StatusUI` that owns a resource to release.

    Only the tray implements this today (its icon thread). The composition root
    asks with ``isinstance`` rather than importing the concrete class, so a
    user-injected or no-op UI is left alone (ADR 0019).
    """

    def stop(self) -> None:
        """Release the background resource so the process can exit cleanly."""
        ...


@runtime_checkable
class DiagnosticStatusUI(Protocol):
    """A status UI that can report its own failures (ADR 0013).

    ``service.py`` contains every ``StatusUI`` callback exception as a safety
    net so a broken display can never wedge the state machine. That is the right
    policy for *safety* but it makes a silently-degraded tray invisible, so the
    tray records its failures itself and exposes them through this method for
    ``ges2t doctor``/``ges2t status`` to report.
    """

    def failure_report(self) -> TrayFailure | None:
        """Return the recorded failures, or ``None`` when every callback worked."""
        ...
