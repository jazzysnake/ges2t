"""Desktop notifications layered over a ``StatusUI`` (ticket 18, ADR 0013, ADR 0016).

Every test injects a **fake runner**: no ``notify-send`` process is started and
no popup reaches the user's screen. The suppression window is exercised with an
injected clock, never by sleeping.
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Sequence

import pytest

from ges2t import notify, pipeline
from ges2t.config import Config, UIConfig
from ges2t.injectors.ydotool import _daemon_unreachable_message
from ges2t.interfaces import LiveText, StatusUI
from ges2t.notify import (
    SUPPRESSION_WINDOW_SECONDS,
    NotificationError,
    NotifyingStatusUI,
)
from ges2t.ui.status import DiagnosticStatusUI, StoppableStatusUI, TrayFailure
from tests.fakes import FakeStatusUI

# Literal advances, deliberately NOT derived from SUPPRESSION_WINDOW_SECONDS: a
# test that computes its own clock from the constant under test moves its own
# goalposts when that constant is mutated (the stale-constant trap).
_SHORT = 1.0
_PAST_THE_WINDOW = 30.0


class _RecordingRunner:
    """A ``NotificationRunner`` that records argv instead of running anything."""

    def __init__(self) -> None:
        self.argvs: list[list[str]] = []

    def __call__(self, argv: Sequence[str]) -> None:
        self.argvs.append(list(argv))


class _RaisingRunner:
    """A runner that fails the way a missing binary or a dead daemon would."""

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    def __call__(self, argv: Sequence[str]) -> None:
        self.calls += 1
        raise self.error


class _FakeClock:
    """An injectable monotonic clock; advance it instead of sleeping."""

    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _LifecycleStatusUI:
    """A ``StatusUI`` that also implements the stop/diagnostic protocols."""

    def __init__(self) -> None:
        self.stop_calls = 0

    def idle(self) -> None:
        """No-op."""

    def recording(self) -> None:
        """No-op."""

    def transcribing(self) -> None:
        """No-op."""

    def error(self, message: str) -> None:
        """No-op."""

    def partial(self, live: LiveText) -> None:
        """No-op."""

    def stop(self) -> None:
        """Record the stop."""
        self.stop_calls += 1

    def failure_report(self) -> TrayFailure | None:
        """Report one recorded failure."""
        return TrayFailure(count=2, last_message="state update: no display")


class _RaisingStatusUI:
    """A ``StatusUI`` whose ``error`` raises, like a display that broke."""

    def __init__(self, error: Exception) -> None:
        self.raise_error = error
        self.errors: list[str] = []

    def idle(self) -> None:
        """No-op."""

    def recording(self) -> None:
        """No-op."""

    def transcribing(self) -> None:
        """No-op."""

    def partial(self, live: LiveText) -> None:
        """No-op."""

    def error(self, message: str) -> None:
        """Record the message, then raise."""
        self.errors.append(message)
        raise self.raise_error


# --- pure forwarding (ticket 17 must not regress) -----------------------------


def test_every_status_ui_call_forwards_to_the_wrapped_ui() -> None:
    wrapped = FakeStatusUI()
    runner = _RecordingRunner()
    ui = NotifyingStatusUI(wrapped, runner=runner)

    ui.idle()
    ui.recording()
    ui.transcribing()
    ui.partial(LiveText(committed="live words", pending=""))
    ui.error("the engine failed")

    assert wrapped.calls == ["idle", "recording", "transcribing", "partial", "error"]
    assert [live.display for live in wrapped.partials] == ["live words"]
    assert wrapped.errors == ["the engine failed"]
    assert len(runner.argvs) == 1


def test_state_calls_and_partials_never_notify() -> None:
    runner = _RecordingRunner()
    ui = NotifyingStatusUI(FakeStatusUI(), runner=runner)

    ui.idle()
    ui.recording()
    ui.transcribing()
    ui.partial(LiveText(committed="partial text must not pop up", pending=""))

    assert runner.argvs == []


def test_the_wrapper_satisfies_the_status_ui_protocol() -> None:
    ui = NotifyingStatusUI(FakeStatusUI(), runner=_RecordingRunner())

    assert isinstance(ui, StatusUI)


# --- the notification itself --------------------------------------------------


def test_error_sends_one_notification_with_the_message_verbatim() -> None:
    runner = _RecordingRunner()
    message = "transcription failed: the engine worker exited"
    ui = NotifyingStatusUI(FakeStatusUI(), runner=runner)

    ui.error(message)

    assert len(runner.argvs) == 1
    assert runner.argvs[0][-1] == message


def test_the_notification_uses_the_fixed_application_name_and_an_explicit_argv() -> None:
    runner = _RecordingRunner()
    ui = NotifyingStatusUI(FakeStatusUI(), runner=runner)

    ui.error("boom")

    argv = runner.argvs[0]
    assert argv[0] == "notify-send"
    assert "--app-name=ges2t" in argv
    assert argv[-2] == "ges2t error"
    assert argv[-1] == "boom"


def test_the_notification_body_names_the_ydotoold_fix() -> None:
    # The actionable text is the service's own message (ticket 7, ADR 0016),
    # taken from its real generator so this cannot drift into a self-consistent
    # test-only string. The service prefixes the exception with this verb.
    failure = _daemon_unreachable_message(
        "failed to connect socket: No such file or directory"
    )
    message = f"injection failed: {failure}"
    runner = _RecordingRunner()
    ui = NotifyingStatusUI(FakeStatusUI(), runner=runner)

    ui.error(message)

    body = runner.argvs[0][-1]
    assert body == message
    assert failure in body
    assert "systemctl --user enable --now ydotool" in body
    assert "/dev/uinput" in body


# --- duplicate suppression (one popup per message per window) -----------------


def test_the_suppression_window_is_a_few_seconds() -> None:
    # The DoD fixes the window as a module constant of "a few seconds"; this
    # pins its intent so a mutation to 0 (no suppression) or effectively
    # infinite (never re-notify) is caught directly.
    assert 0.0 < SUPPRESSION_WINDOW_SECONDS <= 30.0


def test_repeated_identical_errors_send_one_notification() -> None:
    runner = _RecordingRunner()
    clock = _FakeClock()
    ui = NotifyingStatusUI(FakeStatusUI(), runner=runner, clock=clock)

    for _ in range(5):
        ui.error("ydotoold is not reachable")
        clock.advance(_SHORT)

    assert len(runner.argvs) == 1


def test_distinct_errors_each_send_a_notification() -> None:
    runner = _RecordingRunner()
    ui = NotifyingStatusUI(FakeStatusUI(), runner=runner)

    ui.error("first failure")
    ui.error("second failure")

    assert [argv[-1] for argv in runner.argvs] == ["first failure", "second failure"]


def test_interleaved_identical_errors_inside_the_window_stay_suppressed() -> None:
    # The rule is keyed on the message text, not "remember only the last one":
    # a repeat of A inside the window stays suppressed even though B happened in
    # between, while B itself still got its own popup.
    runner = _RecordingRunner()
    clock = _FakeClock()
    ui = NotifyingStatusUI(FakeStatusUI(), runner=runner, clock=clock)

    ui.error("A")
    clock.advance(_SHORT)
    ui.error("B")
    clock.advance(_SHORT)
    ui.error("A")

    assert [argv[-1] for argv in runner.argvs] == ["A", "B"]


def test_a_repeat_inside_the_window_stays_suppressed() -> None:
    runner = _RecordingRunner()
    clock = _FakeClock()
    ui = NotifyingStatusUI(FakeStatusUI(), runner=runner, clock=clock)

    ui.error("the daemon is down")
    clock.advance(_SHORT)
    ui.error("the daemon is down")

    assert len(runner.argvs) == 1


def test_the_same_error_notifies_again_after_the_window() -> None:
    runner = _RecordingRunner()
    clock = _FakeClock()
    ui = NotifyingStatusUI(FakeStatusUI(), runner=runner, clock=clock)

    ui.error("the daemon is down")
    clock.advance(_PAST_THE_WINDOW)
    ui.error("the daemon is down")

    assert len(runner.argvs) == 2


# --- notification failures are contained, wrapped failures are not ------------


def test_a_missing_notify_send_is_swallowed_and_the_wrapped_ui_still_runs() -> None:
    wrapped = FakeStatusUI()
    runner = _RaisingRunner(FileNotFoundError("notify-send"))
    ui = NotifyingStatusUI(wrapped, runner=runner)

    ui.error("the real error")

    assert wrapped.calls == ["error"]
    assert wrapped.errors == ["the real error"]
    assert runner.calls == 1


def test_a_failing_notify_send_is_logged_and_contained(
    caplog: pytest.LogCaptureFixture,
) -> None:
    wrapped = FakeStatusUI()
    runner = _RaisingRunner(NotificationError("no notification daemon"))
    ui = NotifyingStatusUI(wrapped, runner=runner)

    with caplog.at_level(logging.WARNING, logger="ges2t.notify"):
        ui.error("the real error")

    assert wrapped.errors == ["the real error"]
    assert any(
        "could not send the desktop notification" in record.getMessage()
        for record in caplog.records
    )


def test_a_raising_wrapped_ui_is_not_masked_and_the_popup_is_still_attempted() -> None:
    # The popup is attempted first, so a broken tray still cannot hide the
    # error; the wrapped exception then propagates for service.py to contain.
    runner = _RecordingRunner()
    broken = _RaisingStatusUI(RuntimeError("the tray exploded"))
    ui = NotifyingStatusUI(broken, runner=runner)

    with pytest.raises(RuntimeError, match="the tray exploded"):
        ui.error("ydotoold is not reachable")

    assert broken.errors == ["ydotoold is not reachable"]
    assert runner.argvs
    assert runner.argvs[0][-1] == "ydotoold is not reachable"


# --- the default runner uses an explicit argv and no shell --------------------


def test_the_default_runner_passes_an_explicit_argument_list_without_a_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(
        argv: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(notify.subprocess, "run", fake_run)

    notify.run_notify_send(("notify-send", "--app-name=ges2t", "ges2t error", "boom"))

    argv, kwargs = calls[0]
    assert argv == ["notify-send", "--app-name=ges2t", "ges2t error", "boom"]
    assert kwargs == {"capture_output": True, "check": False}
    assert "shell" not in kwargs


def test_the_default_runner_raises_notification_error_on_a_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(
        argv: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            argv, 1, b"", b"Failed to connect to session bus"
        )

    monkeypatch.setattr(notify.subprocess, "run", fake_run)

    with pytest.raises(NotificationError, match="Failed to connect to session bus"):
        notify.run_notify_send(("notify-send", "ges2t error", "boom"))


# --- lifecycle forwarding (ticket 17's shutdown must keep working) ------------


def test_stop_and_failure_report_forward_to_a_wrapped_lifecycle_ui() -> None:
    wrapped = _LifecycleStatusUI()
    ui = NotifyingStatusUI(wrapped, runner=_RecordingRunner())

    ui.stop()
    report = ui.failure_report()

    assert wrapped.stop_calls == 1
    assert report == TrayFailure(count=2, last_message="state update: no display")


def test_stop_and_failure_report_are_safe_for_a_bare_wrapped_ui() -> None:
    ui = NotifyingStatusUI(FakeStatusUI(), runner=_RecordingRunner())

    # The wrapper is still a lifecycle UI for run_service's shutdown, but the
    # bare wrapped UI has nothing to stop and no failures to report.
    assert isinstance(ui, StoppableStatusUI)
    assert isinstance(ui, DiagnosticStatusUI)
    ui.stop()
    assert ui.failure_report() is None


# --- run_service wiring -------------------------------------------------------


def _capture_startup(
    monkeypatch: pytest.MonkeyPatch, seen: list[StatusUI]
) -> None:
    """Make ``_startup`` record the status UI it was handed, then stop."""

    def capture(config: Config, status_ui: StatusUI) -> StatusUI:
        seen.append(status_ui)
        raise pipeline.PipelineError("stop before serving")

    monkeypatch.setattr(pipeline, "_startup", capture)


def test_run_service_does_not_wrap_when_notifications_are_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = FakeStatusUI()
    seen: list[StatusUI] = []
    monkeypatch.setattr(pipeline, "build_status_ui", lambda config: sentinel)
    _capture_startup(monkeypatch, seen)

    result = pipeline.run_service(Config(ui=UIConfig(notifications=False)))

    assert result != 0
    assert seen == [sentinel]


def test_run_service_wraps_when_notifications_are_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = FakeStatusUI()
    seen: list[StatusUI] = []
    monkeypatch.setattr(pipeline, "build_status_ui", lambda config: sentinel)
    _capture_startup(monkeypatch, seen)

    result = pipeline.run_service(Config(ui=UIConfig(notifications=True)))

    assert result != 0
    assert len(seen) == 1
    assert isinstance(seen[0], NotifyingStatusUI)
    assert seen[0].wrapped is sentinel


def test_run_service_wraps_an_injected_status_ui_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = FakeStatusUI()
    seen: list[StatusUI] = []
    _capture_startup(monkeypatch, seen)

    result = pipeline.run_service(Config(), status_ui=sentinel)

    assert result != 0
    assert len(seen) == 1
    assert isinstance(seen[0], NotifyingStatusUI)
    assert seen[0].wrapped is sentinel
