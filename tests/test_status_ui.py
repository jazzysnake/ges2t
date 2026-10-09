"""Tests for the status-display vocabulary and the tray's failure recording.

No test here imports ``gi`` or ``pystray``, creates a tray icon, or needs a
display: the tray's icon is injected as a fake, so the observability behaviour
(the part ticket 17 requires) is exercised on any machine (ADR 0014). The
backend assertion itself is covered in ``tests/test_tray_backend.py``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable

import pytest

from ges2t.interfaces import LiveText, StatusUI
from ges2t.service import CommandResponse, ServiceState
from ges2t.ui.status import (
    DiagnosticStatusUI,
    NullStatusUI,
    StatusState,
    StoppableStatusUI,
    TrayFailure,
)
from ges2t.ui.tray import CommandSender, TrayIcon, TrayStatusUI

# --- NullStatusUI and the shared enum ----------------------------------------


def test_null_status_ui_is_a_status_ui_and_stays_silent(
    capsys: pytest.CaptureFixture[str],
) -> None:
    ui = NullStatusUI()

    assert isinstance(ui, StatusUI)
    ui.idle()
    ui.recording()
    ui.transcribing()
    ui.partial(LiveText(committed="display", pending="-only"))

    assert capsys.readouterr().err == ""


def test_null_status_ui_error_writes_to_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    NullStatusUI().error("the engine rejected the audio")

    assert "the engine rejected the audio" in capsys.readouterr().err


def test_status_state_mirrors_the_service_state_values() -> None:
    # The tooltip and the wire protocol must not spell the same state differently.
    assert {state.value for state in StatusState} == {
        state.value for state in ServiceState
    }


def _gui_modules_after_importing(module_name: str) -> dict[str, bool]:
    """Import ``module_name`` in a fresh isolated interpreter and report sys.modules."""
    script = (
        "import json, sys\n"
        f"import {module_name}\n"
        f"names = {json.dumps(list(_GUI_MODULES))}\n"
        "print(json.dumps({name: name in sys.modules for name in names}))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-c", script],
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    present: dict[str, bool] = json.loads(completed.stdout.strip().splitlines()[-1])
    return present


#: The GUI and image libraries the tray may use only *inside* its methods, never
#: at import time (ADR 0014).
_GUI_MODULES = ("gi", "pystray", "PIL")


@pytest.mark.parametrize(
    "module_name", ["ges2t.cli", "ges2t.pipeline", "ges2t.ui.tray"]
)
def test_importing_a_gui_free_module_does_not_pull_in_gtk_or_pystray(
    module_name: str,
) -> None:
    present = _gui_modules_after_importing(module_name)

    for module in _GUI_MODULES:
        assert present[module] is False, f"{module} was imported by {module_name}"


# --- helpers -----------------------------------------------------------------


def _sender(calls: list[str]) -> CommandSender:
    def send(command: str) -> CommandResponse:
        calls.append(command)
        return CommandResponse(ok=True, state=ServiceState.IDLE)

    return send


class _FakeIcon:
    """An injected tray icon that records the calls :class:`TrayStatusUI` makes.

    ``fail=True`` makes every real display operation raise, which is the
    injected failure the observability requirement is tested with.
    """

    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail
        self._title = ""
        self.icon: object = None
        self.menu_updates = 0
        self.run_calls = 0
        self.stop_calls = 0

    @property
    def title(self) -> str:
        return self._title

    @title.setter
    def title(self, value: str) -> None:
        if self._fail:
            raise RuntimeError("no display available")
        self._title = value

    def run(self, setup: Callable[[TrayIcon], None] | None = None) -> None:
        self.run_calls += 1

    def stop(self) -> None:
        self.stop_calls += 1

    def update_menu(self) -> None:
        self.menu_updates += 1
        if self._fail:
            raise RuntimeError("no display available")


# --- TrayStatusUI mapping ----------------------------------------------------


def test_tray_status_ui_satisfies_the_status_and_lifecycle_protocols() -> None:
    ui = TrayStatusUI(_sender([]), icon=_FakeIcon())

    assert isinstance(ui, StatusUI)
    assert isinstance(ui, StoppableStatusUI)
    assert isinstance(ui, DiagnosticStatusUI)


def test_tray_maps_every_state_onto_the_tooltip() -> None:
    icon = _FakeIcon()
    ui = TrayStatusUI(_sender([]), icon=icon)

    ui.idle()
    assert icon.title == "ges2t: idle"
    ui.recording()
    assert icon.title == "ges2t: recording"
    ui.transcribing()
    assert icon.title == "ges2t: transcribing"
    ui.error("the engine is not loaded")
    assert "error" in icon.title
    assert "the engine is not loaded" in icon.title

    assert ui.failure_count == 0
    assert ui.failure_report() is None


def test_tray_partial_updates_the_tooltip_without_typing() -> None:
    icon = _FakeIcon()
    ui = TrayStatusUI(_sender([]), icon=icon)

    ui.recording()
    ui.partial(LiveText(committed="the quick brown", pending=""))

    assert "the quick brown" in icon.title
    # partial() has no injector and no typing side effect at all (ADR 0011):
    # the only observable change is the displayed tooltip.


def test_tray_partial_renders_committed_plus_pending_byte_for_byte() -> None:
    """(c) at the UI layer: the tooltip is unchanged by the committed split."""
    icon = _FakeIcon()
    ui = TrayStatusUI(_sender([]), icon=icon)

    ui.recording()
    ui.partial(LiveText(committed="the quick ", pending="brown fox"))

    # Exactly the merged string the pre-split pipeline passed as a bare str.
    assert icon.title == "ges2t: recording\nthe quick brown fox"
    # The committed half is available to the UI; the tray still shows the whole
    # live string and types nothing (ADR 0011).
    assert ui.failure_count == 0


def test_tray_start_runs_the_injected_icon_on_its_own_thread() -> None:
    icon = _FakeIcon()
    ui = TrayStatusUI(_sender([]), icon=icon)

    ui.start()

    thread = ui._thread
    assert thread is not None
    assert thread.daemon is True
    thread.join(timeout=1.0)
    assert icon.run_calls == 1
    assert icon.title == "ges2t: idle"


def test_tray_stop_stops_the_icon() -> None:
    icon = _FakeIcon()
    ui = TrayStatusUI(_sender([]), icon=icon)

    ui.stop()

    assert icon.stop_calls == 1


# --- observability (required by ticket 17) -----------------------------------


def test_tray_records_and_exposes_injected_failures(
    capsys: pytest.CaptureFixture[str],
) -> None:
    ui = TrayStatusUI(_sender([]), icon=_FakeIcon(fail=True))

    ui.recording()
    ui.transcribing()

    assert ui.failure_count == 2
    assert ui.last_failure is not None
    assert "no display available" in ui.last_failure
    report = ui.failure_report()
    assert report is not None
    assert report == TrayFailure(count=2, last_message=ui.last_failure)
    assert report.summary() == f"the tray has failed 2 times: {ui.last_failure}"

    # It is not silent: the first occurrence of the failure is printed.
    assert "the tray has failed 1 times" in capsys.readouterr().err


def test_tray_failure_count_keeps_climbing_but_only_prints_new_messages(
    capsys: pytest.CaptureFixture[str],
) -> None:
    ui = TrayStatusUI(_sender([]), icon=_FakeIcon(fail=True))

    for _ in range(5):
        ui.idle()

    assert ui.failure_count == 5
    # Five identical failures produce exactly one stderr line, not five.
    stderr = capsys.readouterr().err
    assert stderr.count("the tray has failed") == 1


def test_tray_menu_actions_use_the_socket_client_and_shutdown_request() -> None:
    calls: list[str] = []
    shutdowns: list[str] = []
    ui = TrayStatusUI(
        _sender(calls),
        request_shutdown=lambda: shutdowns.append("quit"),
        icon=_FakeIcon(),
    )

    ui._on_toggle(object(), object())
    ui._on_cancel(object(), object())
    ui._on_quit(object(), object())

    assert calls == ["toggle", "cancel"]
    assert shutdowns == ["quit"]
    assert ui.failure_count == 0


def test_tray_records_a_menu_command_failure_instead_of_raising() -> None:
    def failing_sender(command: str) -> CommandResponse:
        raise RuntimeError("service not running")

    ui = TrayStatusUI(failing_sender, icon=_FakeIcon())

    ui._on_toggle(object(), object())

    assert ui.failure_count == 1
    assert ui.failure_report() is not None
    assert "toggle command" in (ui.last_failure or "")
