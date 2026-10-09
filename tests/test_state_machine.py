"""The service state machine: the full transition table plus ADR 0004's races.

Every test here uses only the fakes in :mod:`tests.fakes` for the
:class:`~ges2t.interfaces.Injector` and :class:`~ges2t.interfaces.StatusUI`, and
a hand-written :class:`FakeDictationSession` for the session seam (there is no
session fake in ``tests/fakes.py`` because ticket 9 publishes the Protocol).
There is no microphone, engine, subprocess, or ``/dev/uinput`` anywhere.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

import pytest

from ges2t.service import (
    CommandResponse,
    DictationSession,
    Service,
    ServiceState,
)
from tests.fakes import FakeInjector, FakeStatusUI

_T = TypeVar("_T")


# -- session double and helpers ------------------------------------------


class FakeDictationSession:
    """A scripted :class:`~ges2t.service.DictationSession`.

    ``finish()`` can block on an event so a test can hold the service in
    ``TRANSCRIBING`` and prove the socket loop is not blocked by transcription.
    """

    def __init__(
        self,
        *,
        text: str = "hello world",
        partial_texts: Sequence[str] = (),
        start_error: Exception | None = None,
        finish_error: Exception | None = None,
        cancel_error: Exception | None = None,
        block_finish: bool = False,
    ) -> None:
        self.text = text
        self.partial_texts = list(partial_texts)
        self.start_error = start_error
        self.finish_error = finish_error
        self.cancel_error = cancel_error
        self.block_finish = block_finish
        self.calls: list[str] = []
        self.finish_started = threading.Event()
        self.release_finish = threading.Event()

    def start(self) -> None:
        self.calls.append("start")
        if self.start_error is not None:
            raise self.start_error

    def partials(self) -> Iterator[str]:
        self.calls.append("partials")
        yield from self.partial_texts

    def finish(self) -> str:
        self.calls.append("finish")
        self.finish_started.set()
        if self.block_finish and not self.release_finish.wait(timeout=5.0):
            raise AssertionError("finish() was never released by the test")
        if self.finish_error is not None:
            raise self.finish_error
        return self.text

    def cancel(self) -> None:
        self.calls.append("cancel")
        if self.cancel_error is not None:
            raise self.cancel_error


class SessionScript:
    """A ``session_factory`` that returns sessions in order, repeating the last.

    ``created`` records every session the service asked for, which is how tests
    assert that a queued toggle starts **exactly one** new recording.
    """

    def __init__(self, sessions: Sequence[FakeDictationSession]) -> None:
        if not sessions:
            raise ValueError("SessionScript needs at least one session")
        self._sessions = list(sessions)
        self.created: list[FakeDictationSession] = []

    def __call__(self) -> FakeDictationSession:
        if len(self._sessions) > 1:
            session = self._sessions.pop(0)
        else:
            session = self._sessions[0]
        self.created.append(session)
        return session


def wait_for(
    predicate: Callable[[], bool],
    *,
    timeout: float = 2.0,
    description: str = "condition",
) -> None:
    """Poll ``predicate`` until it is true, failing instead of hanging."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.001)
    raise AssertionError(f"{description} was not met within {timeout:.1f}s")


def call_with_timeout(fn: Callable[[], _T], *, timeout: float) -> _T:
    """Run ``fn`` on a worker thread and fail if it has not returned in time.

    This is how the "the socket loop is not blocked" claim is tested: if the
    service held its lock across ``finish()``, this call would never return.
    """
    results: list[_T] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            results.append(fn())
        except BaseException as exc:  # re-raised on the main thread
            errors.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    if thread.is_alive():
        raise AssertionError(f"call did not return within {timeout:.1f}s")
    if errors:
        raise errors[0]
    return results[0]


def shutdown_with_timeout(service: Service, *, timeout: float = 1.0) -> None:
    """Call ``shutdown`` on a daemon thread, failing the suite if it wedges.

    A deadlock test that fails by hanging CI is worse than no test: when the
    bug is present the command thread is wedged on the state lock, so an
    unbounded ``shutdown()`` would block on the same lock. Bounding it keeps
    the failure a clean assertion.
    """
    thread = threading.Thread(target=service.shutdown, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    if thread.is_alive():
        raise AssertionError(f"shutdown did not return within {timeout:.1f}s")


@dataclass
class _Scenario:
    service: Service
    session: FakeDictationSession
    injector: FakeInjector
    status_ui: FakeStatusUI


def _scenario(
    tmp_path: Path,
    session: FakeDictationSession,
    *,
    injector: FakeInjector | None = None,
) -> _Scenario:
    real_injector = injector if injector is not None else FakeInjector()
    status_ui = FakeStatusUI()
    service = Service(
        SessionScript([session]),
        real_injector,
        status_ui,
        tmp_path / "ges2t.sock",
    )
    return _Scenario(service, session, real_injector, status_ui)


def prepare(tmp_path: Path, target: ServiceState) -> _Scenario:
    """Drive a fresh service into ``target`` using only public commands."""
    injector = FakeInjector()
    status_ui = FakeStatusUI()
    if target is ServiceState.ERROR:
        failing = FakeDictationSession(start_error=RuntimeError("boom"))
        service = Service(
            SessionScript([failing, FakeDictationSession(block_finish=True)]),
            injector,
            status_ui,
            tmp_path / "ges2t.sock",
        )
        assert service.handle("toggle").state is ServiceState.ERROR
        session = failing
    else:
        session = FakeDictationSession(block_finish=True)
        service = Service(
            SessionScript([session]), injector, status_ui, tmp_path / "ges2t.sock"
        )
        if target is ServiceState.RECORDING:
            service.handle("toggle")
        elif target is ServiceState.TRANSCRIBING:
            service.handle("toggle")
            service.handle("toggle")
            assert session.finish_started.wait(timeout=2.0)
    assert service.state is target
    return _Scenario(service, session, injector, status_ui)


def cleanup(scenario: _Scenario) -> None:
    """Release any blocked ``finish()`` and shut the service down."""
    scenario.session.release_finish.set()
    try:
        wait_for(
            lambda: scenario.service.state is not ServiceState.TRANSCRIBING,
            timeout=2.0,
            description="transcription to finish",
        )
    except AssertionError:
        pass
    scenario.service.shutdown()


# -- the full transition table ---------------------------------------

_TABLE: list[tuple[ServiceState, str, bool, ServiceState, str | None]] = [
    (ServiceState.IDLE, "toggle", True, ServiceState.RECORDING, None),
    (ServiceState.IDLE, "ptt-start", True, ServiceState.RECORDING, None),
    (ServiceState.IDLE, "ptt-stop", False, ServiceState.IDLE, "not-recording"),
    (ServiceState.IDLE, "cancel", False, ServiceState.IDLE, "not-recording"),
    (ServiceState.IDLE, "status", True, ServiceState.IDLE, None),
    (ServiceState.RECORDING, "toggle", True, ServiceState.TRANSCRIBING, None),
    (
        ServiceState.RECORDING,
        "ptt-start",
        False,
        ServiceState.RECORDING,
        "already-recording",
    ),
    (ServiceState.RECORDING, "ptt-stop", True, ServiceState.TRANSCRIBING, None),
    (ServiceState.RECORDING, "cancel", True, ServiceState.IDLE, None),
    (ServiceState.RECORDING, "status", True, ServiceState.RECORDING, None),
    # A toggle while transcribing is queued; the honest current state is
    # reported, and the restart happens once finish() returns.
    (ServiceState.TRANSCRIBING, "toggle", True, ServiceState.TRANSCRIBING, None),
    (ServiceState.TRANSCRIBING, "ptt-start", False, ServiceState.TRANSCRIBING, "busy"),
    (ServiceState.TRANSCRIBING, "ptt-stop", False, ServiceState.TRANSCRIBING, "busy"),
    (ServiceState.TRANSCRIBING, "cancel", False, ServiceState.TRANSCRIBING, "busy"),
    (ServiceState.TRANSCRIBING, "status", True, ServiceState.TRANSCRIBING, None),
    (ServiceState.ERROR, "toggle", True, ServiceState.IDLE, None),
    (ServiceState.ERROR, "ptt-start", True, ServiceState.IDLE, None),
    (ServiceState.ERROR, "ptt-stop", False, ServiceState.ERROR, "not-recording"),
    (ServiceState.ERROR, "cancel", True, ServiceState.IDLE, None),
    (ServiceState.ERROR, "status", True, ServiceState.ERROR, None),
]


@pytest.mark.parametrize(
    "initial, command, expected_ok, expected_state, expected_error",
    _TABLE,
    ids=[f"{state.value}+{command}" for state, command, _, _, _ in _TABLE],
)
def test_transition_table(
    initial: ServiceState,
    command: str,
    expected_ok: bool,
    expected_state: ServiceState,
    expected_error: str | None,
    tmp_path: Path,
) -> None:
    scenario = prepare(tmp_path, initial)
    try:
        response = scenario.service.handle(command)
        assert response.ok is expected_ok
        assert response.state is expected_state
        assert response.error == expected_error
        assert scenario.service.state is expected_state
    finally:
        cleanup(scenario)


# -- never block the socket loop -------------------------------------


def test_toggle_from_recording_returns_before_finish_completes(
    tmp_path: Path,
) -> None:
    scenario = prepare(tmp_path, ServiceState.RECORDING)
    try:
        response = call_with_timeout(
            lambda: scenario.service.handle("toggle"), timeout=1.0
        )
        assert response.state is ServiceState.TRANSCRIBING
        assert scenario.session.finish_started.wait(timeout=1.0)
        assert not scenario.session.release_finish.is_set()
    finally:
        cleanup(scenario)


def test_commands_are_answered_while_transcription_is_in_flight(
    tmp_path: Path,
) -> None:
    scenario = prepare(tmp_path, ServiceState.TRANSCRIBING)
    try:
        response = call_with_timeout(
            lambda: scenario.service.handle("status"), timeout=1.0
        )
        assert response.ok is True
        assert response.state is ServiceState.TRANSCRIBING
        # finish() is genuinely still running (and the lock is not held).
        assert scenario.session.finish_started.is_set()
        assert not scenario.session.release_finish.is_set()
    finally:
        cleanup(scenario)


# -- no collaborator is called under the state lock (ticket 21) ------
#
# This is the defect ticket 9's review reproduced: the tray is a ``StatusUI``
# whose whole job is showing the current state, so reading ``service.state``
# back from a status callback is the natural implementation. Called under the
# non-reentrant lock it deadlocks, wedging the socket loop so the hotkey stops
# responding with no error. These tests run each command on a bounded worker
# thread, so the bug returns as a loud failure instead of a hung suite.


class StateReadingStatusUI:
    """A :class:`~ges2t.interfaces.StatusUI` that reads state on every call."""

    def __init__(self) -> None:
        self.service: Service | None = None
        self.states: list[ServiceState] = []

    def _read(self) -> None:
        assert self.service is not None, "status UI used before it was wired"
        self.states.append(self.service.state)

    def idle(self) -> None:
        self._read()

    def recording(self) -> None:
        self._read()

    def transcribing(self) -> None:
        self._read()

    def error(self, message: str) -> None:
        self._read()

    def partial(self, text: str) -> None:
        self._read()


class StateReadingSession(FakeDictationSession):
    """A session whose methods also read ``Service.state`` back."""

    service: Service | None = None

    def _read(self) -> None:
        assert self.service is not None, "session used before it was wired"
        self.service.state

    def start(self) -> None:
        self._read()
        super().start()

    def finish(self) -> str:
        self._read()
        return super().finish()

    def cancel(self) -> None:
        self._read()
        super().cancel()


def _wired_service(
    tmp_path: Path,
    session: FakeDictationSession,
    status_ui: StateReadingStatusUI,
) -> Service:
    service = Service(
        SessionScript([session]),
        FakeInjector(),
        status_ui,
        tmp_path / "ges2t.sock",
    )
    status_ui.service = service
    if isinstance(session, StateReadingSession):
        session.service = service
    return service


def test_status_ui_reading_state_back_does_not_deadlock(tmp_path: Path) -> None:
    status_ui = StateReadingStatusUI()
    session = FakeDictationSession(text="hello", block_finish=True)
    service = _wired_service(tmp_path, session, status_ui)
    try:
        # IDLE -> RECORDING: status_ui.recording() reads state.
        first = call_with_timeout(lambda: service.handle("toggle"), timeout=1.5)
        assert first.state is ServiceState.RECORDING
        # RECORDING -> TRANSCRIBING: status_ui.transcribing() reads state.
        second = call_with_timeout(lambda: service.handle("toggle"), timeout=1.5)
        assert second.state is ServiceState.TRANSCRIBING
        # TRANSCRIBING -> IDLE on the finish worker: status_ui.idle() reads
        # state. If that ran under the lock the worker would never publish
        # IDLE, so the bounded wait below fails instead of hanging.
        session.release_finish.set()
        wait_for(
            lambda: status_ui.states[-1:] == [ServiceState.IDLE],
            timeout=1.5,
            description="status UI to observe the idle transition",
        )
        assert status_ui.states == [
            ServiceState.RECORDING,
            ServiceState.TRANSCRIBING,
            ServiceState.IDLE,
        ]
    finally:
        session.release_finish.set()
        shutdown_with_timeout(service)


def test_status_ui_reading_state_back_on_error_does_not_deadlock(
    tmp_path: Path,
) -> None:
    status_ui = StateReadingStatusUI()
    failing = FakeDictationSession(start_error=RuntimeError("no audio device"))
    service = _wired_service(tmp_path, failing, status_ui)
    try:
        # IDLE -> ERROR: status_ui.error() reads state under the same lock
        # ``_set_state`` used to hold.
        response = call_with_timeout(lambda: service.handle("toggle"), timeout=1.5)
        assert response.state is ServiceState.ERROR
        assert status_ui.states == [ServiceState.ERROR]
    finally:
        shutdown_with_timeout(service)


def test_session_reading_state_back_does_not_deadlock(tmp_path: Path) -> None:
    status_ui = StateReadingStatusUI()
    session = StateReadingSession(text="")
    service = _wired_service(tmp_path, session, status_ui)
    try:
        # ``session.start()`` runs on the command path and must not be called
        # while the state lock is held.
        response = call_with_timeout(lambda: service.handle("toggle"), timeout=1.5)
        assert response.state is ServiceState.RECORDING
    finally:
        shutdown_with_timeout(service)


# -- a raising StatusUI must not abort a transition (B1) -------------
#
# The display drain runs in ``_transition_scope``'s ``finally``, which sits
# outside ``handle``'s defensive ``try/except``. Display is best-effort and a
# raising callback must never change the state machine's outcome nor skip the
# deferred side effect that runs after the scope (``worker.start()``,
# ``session.cancel()``, ``shutdown``). Otherwise ``RECORDING + toggle`` with a
# raising ``status_ui.transcribing`` leaves the service wedged in
# ``TRANSCRIBING`` answering ``busy`` forever -- the same "hotkey stops
# responding with no error" class this ticket exists to prevent. Every command
# below runs on a bounded worker thread so a regression fails loudly instead of
# hanging the suite.


class RaisingStatusUI(FakeStatusUI):
    """A :class:`~ges2t.interfaces.StatusUI` that raises on chosen callbacks.

    The real call is recorded first (``super()``), then the configured
    callbacks raise ``RuntimeError``; ``raised`` names the callbacks that did
    raise, so a test can prove the display failure was actually exercised.
    """

    def __init__(self, *, raise_on: Sequence[str] = ()) -> None:
        super().__init__()
        self._raise_on = set(raise_on)
        self.raised: list[str] = []

    def _maybe_raise(self, name: str) -> None:
        if name in self._raise_on:
            self.raised.append(name)
            raise RuntimeError(f"status_ui.{name} exploded")

    def idle(self) -> None:
        super().idle()
        self._maybe_raise("idle")

    def recording(self) -> None:
        super().recording()
        self._maybe_raise("recording")

    def transcribing(self) -> None:
        super().transcribing()
        self._maybe_raise("transcribing")

    def error(self, message: str) -> None:
        super().error(message)
        self._maybe_raise("error")

    def partial(self, text: str) -> None:
        super().partial(text)
        self._maybe_raise("partial")


def _raising_service(
    tmp_path: Path, session: FakeDictationSession, *, raise_on: Sequence[str]
) -> tuple[Service, RaisingStatusUI]:
    status_ui = RaisingStatusUI(raise_on=raise_on)
    service = Service(
        SessionScript([session]),
        FakeInjector(),
        status_ui,
        tmp_path / "ges2t.sock",
    )
    return service, status_ui


def test_raising_status_ui_transcribing_does_not_wedge_the_service(
    tmp_path: Path,
) -> None:
    session = FakeDictationSession(text="")
    service, status_ui = _raising_service(
        tmp_path, session, raise_on=["transcribing"]
    )
    try:
        assert service.handle("toggle").state is ServiceState.RECORDING
        # RECORDING -> TRANSCRIBING: status_ui.transcribing() raises while the
        # deferred worker.start() is still pending.
        response = call_with_timeout(lambda: service.handle("toggle"), timeout=1.5)
        assert response.ok is True
        assert response.state is ServiceState.TRANSCRIBING
        assert status_ui.raised == ["transcribing"]
        # The deferred side effect still ran: finish() was actually called.
        wait_for(
            lambda: "finish" in session.calls,
            timeout=1.5,
            description="the finish worker to start",
        )
        wait_for(
            lambda: service.state is ServiceState.IDLE,
            timeout=1.5,
            description="the service to return to idle",
        )
        # ... and it is not wedged in TRANSCRIBING answering 'busy' forever.
        follow = call_with_timeout(
            lambda: service.handle("status"), timeout=1.5
        )
        assert follow.ok is True
        assert follow.state is ServiceState.IDLE
        assert follow.error != "busy"
    finally:
        shutdown_with_timeout(service)


def test_raising_status_ui_recording_does_not_change_a_start(
    tmp_path: Path,
) -> None:
    session = FakeDictationSession(text="")
    service, status_ui = _raising_service(tmp_path, session, raise_on=["recording"])
    try:
        response = call_with_timeout(lambda: service.handle("toggle"), timeout=1.5)
        assert response.ok is True
        assert response.state is ServiceState.RECORDING
        assert service.state is ServiceState.RECORDING
        assert session.calls.count("start") == 1
        assert status_ui.raised == ["recording"]
    finally:
        shutdown_with_timeout(service)


def test_raising_status_ui_error_does_not_mask_a_start_failure(
    tmp_path: Path,
) -> None:
    failing = FakeDictationSession(start_error=RuntimeError("no audio device"))
    service, status_ui = _raising_service(tmp_path, failing, raise_on=["error"])
    try:
        response = call_with_timeout(lambda: service.handle("toggle"), timeout=1.5)
        assert response.ok is False
        assert response.state is ServiceState.ERROR
        assert response.error == "start-failed"
        assert status_ui.raised == ["error"]
        assert service.state is ServiceState.ERROR
    finally:
        shutdown_with_timeout(service)


def test_raising_status_ui_idle_still_cancels_the_session(tmp_path: Path) -> None:
    session = FakeDictationSession(text="must not be typed")
    service, status_ui = _raising_service(tmp_path, session, raise_on=["idle"])
    try:
        assert service.handle("toggle").state is ServiceState.RECORDING
        response = call_with_timeout(lambda: service.handle("cancel"), timeout=1.5)
        assert response.ok is True
        assert response.state is ServiceState.IDLE
        assert status_ui.raised == ["idle"]
        assert "cancel" in session.calls
        assert service.state is ServiceState.IDLE
    finally:
        shutdown_with_timeout(service)


def test_raising_status_ui_does_not_skip_shutdown_cleanup(tmp_path: Path) -> None:
    session = FakeDictationSession(text="")
    service, status_ui = _raising_service(tmp_path, session, raise_on=["idle"])
    assert service.handle("toggle").state is ServiceState.RECORDING
    # shutdown() must return rather than raise, and must still cancel the
    # session and join the background threads.
    call_with_timeout(service.shutdown, timeout=1.5)
    assert status_ui.raised == ["idle"]
    assert "cancel" in session.calls
    assert service.state is ServiceState.IDLE
    worker = service._worker
    partials = service._partials_thread
    assert worker is None or not worker.is_alive()
    assert partials is None or not partials.is_alive()


# -- rapid / out-of-order commands (ADR 0004) ------------------------


def test_duplicate_toggle_never_stacks_recordings(tmp_path: Path) -> None:
    session = FakeDictationSession(text="", block_finish=True)
    script = SessionScript([session])
    injector = FakeInjector()
    status_ui = FakeStatusUI()
    service = Service(script, injector, status_ui, tmp_path / "ges2t.sock")
    try:
        assert service.handle("toggle").state is ServiceState.RECORDING
        assert service.handle("toggle").state is ServiceState.TRANSCRIBING
        assert service.handle("toggle").state is ServiceState.TRANSCRIBING
        assert len(script.created) == 1
        session.release_finish.set()
        wait_for(lambda: service.state is ServiceState.RECORDING)
        assert len(script.created) == 2
    finally:
        session.release_finish.set()
        service.shutdown()


def test_toggle_during_transcribing_queues_exactly_one_restart(
    tmp_path: Path,
) -> None:
    first = FakeDictationSession(text="first", block_finish=True)
    second = FakeDictationSession(text="second")
    script = SessionScript([first, second])
    injector = FakeInjector()
    status_ui = FakeStatusUI()
    service = Service(script, injector, status_ui, tmp_path / "ges2t.sock")
    try:
        service.handle("toggle")
        service.handle("toggle")
        queued = service.handle("toggle")
        assert queued.ok is True
        assert queued.state is ServiceState.TRANSCRIBING
        assert queued.message is not None
        first.release_finish.set()
        wait_for(lambda: service.state is ServiceState.RECORDING)
        assert second.calls.count("start") == 1
        assert injector.injected == ["first"]
    finally:
        first.release_finish.set()
        service.shutdown()


def test_ptt_stop_without_start_is_rejected(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path, FakeDictationSession())
    try:
        response = scenario.service.handle("ptt-stop")
        assert response.ok is False
        assert response.error == "not-recording"
        assert response.state is ServiceState.IDLE
        assert scenario.session.calls == []
        assert scenario.injector.calls == []
    finally:
        cleanup(scenario)


def test_ptt_start_while_recording_is_rejected(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path, FakeDictationSession(block_finish=True))
    try:
        scenario.service.handle("ptt-start")
        response = scenario.service.handle("ptt-start")
        assert response.ok is False
        assert response.error == "already-recording"
        assert scenario.session.calls.count("start") == 1
    finally:
        cleanup(scenario)


def test_ptt_start_while_transcribing_is_busy(tmp_path: Path) -> None:
    scenario = prepare(tmp_path, ServiceState.TRANSCRIBING)
    try:
        assert scenario.service.handle("ptt-start").error == "busy"
        assert scenario.service.handle("ptt-stop").error == "busy"
        assert scenario.service.handle("cancel").error == "busy"
        assert scenario.service.state is ServiceState.TRANSCRIBING
    finally:
        cleanup(scenario)


def test_cancel_during_recording_injects_nothing(tmp_path: Path) -> None:
    scenario = _scenario(
        tmp_path, FakeDictationSession(text="must not be typed")
    )
    try:
        scenario.service.handle("toggle")
        response = scenario.service.handle("cancel")
        assert response.ok is True
        assert response.state is ServiceState.IDLE
        assert "cancel" in scenario.session.calls
        assert scenario.injector.calls == []
        assert scenario.service.state is ServiceState.IDLE
    finally:
        cleanup(scenario)


def test_cancel_while_idle_is_rejected(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path, FakeDictationSession())
    try:
        response = scenario.service.handle("cancel")
        assert response.ok is False
        assert response.error == "not-recording"
        assert response.state is ServiceState.IDLE
    finally:
        cleanup(scenario)


def test_unknown_command_is_rejected_without_changing_state(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path, FakeDictationSession())
    try:
        response = scenario.service.handle("frobnicate")
        assert response.ok is False
        assert response.error == "unknown-command"
        assert response.state is ServiceState.IDLE
        assert scenario.service.state is ServiceState.IDLE
    finally:
        cleanup(scenario)


# -- failures do not kill the service ---------------------------------


def test_start_raising_moves_to_error_then_recovers(tmp_path: Path) -> None:
    failing = FakeDictationSession(start_error=RuntimeError("no audio device"))
    good = FakeDictationSession(text="after recovery")
    script = SessionScript([failing, good])
    injector = FakeInjector()
    status_ui = FakeStatusUI()
    service = Service(script, injector, status_ui, tmp_path / "ges2t.sock")
    try:
        response = service.handle("toggle")
        assert response.ok is False
        assert response.error == "start-failed"
        assert service.state is ServiceState.ERROR
        assert status_ui.errors
        # ERROR + toggle clears the error; the next toggle records again.
        assert service.handle("toggle").state is ServiceState.IDLE
        assert service.handle("toggle").state is ServiceState.RECORDING
        assert good.calls.count("start") == 1
    finally:
        service.shutdown()


def test_finish_raising_moves_to_error_then_recovers(tmp_path: Path) -> None:
    failing = FakeDictationSession(finish_error=RuntimeError("engine exploded"))
    script = SessionScript([failing, FakeDictationSession(text="ok")])
    injector = FakeInjector()
    status_ui = FakeStatusUI()
    service = Service(script, injector, status_ui, tmp_path / "ges2t.sock")
    try:
        service.handle("toggle")
        service.handle("toggle")
        wait_for(lambda: service.state is ServiceState.ERROR)
        assert injector.calls == []
        assert status_ui.errors
        assert service.handle("cancel").state is ServiceState.IDLE
    finally:
        service.shutdown()


def test_inject_raising_moves_to_error_then_recovers(tmp_path: Path) -> None:
    injector = FakeInjector(error=RuntimeError("ydotoold is down"))
    scenario = _scenario(
        tmp_path, FakeDictationSession(text="typed nowhere"), injector=injector
    )
    try:
        scenario.service.handle("toggle")
        scenario.service.handle("toggle")
        wait_for(lambda: scenario.service.state is ServiceState.ERROR)
        assert scenario.status_ui.errors
        assert scenario.service.handle("cancel").state is ServiceState.IDLE
        assert scenario.service.state is ServiceState.IDLE
    finally:
        cleanup(scenario)


def test_error_then_successful_command(tmp_path: Path) -> None:
    scenario = prepare(tmp_path, ServiceState.ERROR)
    try:
        assert scenario.service.handle("cancel").state is ServiceState.IDLE
        assert scenario.service.handle("toggle").state is ServiceState.RECORDING
        assert scenario.service.state is ServiceState.RECORDING
    finally:
        cleanup(scenario)


# -- empty text (ADR 0012) -------------------------------------------


@pytest.mark.parametrize("text", ["", "   ", "\t\n", "\u00a0"])
def test_empty_finish_text_is_never_injected(text: str, tmp_path: Path) -> None:
    scenario = _scenario(tmp_path, FakeDictationSession(text=text))
    try:
        scenario.service.handle("toggle")
        scenario.service.handle("toggle")
        wait_for(lambda: scenario.service.state is ServiceState.IDLE)
        assert scenario.injector.calls == []
        assert scenario.status_ui.errors == []
        assert scenario.status_ui.calls[-1] == "idle"
    finally:
        cleanup(scenario)


def test_injection_receives_the_text_verbatim(tmp_path: Path) -> None:
    scenario = _scenario(
        tmp_path, FakeDictationSession(text="  hello world  ")
    )
    try:
        scenario.service.handle("toggle")
        scenario.service.handle("toggle")
        wait_for(lambda: scenario.injector.injected == ["  hello world  "])
        assert scenario.service.state is ServiceState.IDLE
    finally:
        cleanup(scenario)


# -- status UI and partials (ADR 0011) -------------------------------


def test_status_ui_tracks_the_transition_sequence(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path, FakeDictationSession(text=""))
    try:
        scenario.service.handle("toggle")
        scenario.service.handle("toggle")
        wait_for(lambda: scenario.service.state is ServiceState.IDLE)
        assert scenario.status_ui.calls == ["recording", "transcribing", "idle"]
    finally:
        cleanup(scenario)


def test_partials_are_forwarded_but_never_injected(tmp_path: Path) -> None:
    scenario = _scenario(
        tmp_path, FakeDictationSession(text="hello", partial_texts=["Hel", "hell"])
    )
    try:
        scenario.service.handle("toggle")
        wait_for(lambda: scenario.status_ui.partials == ["Hel", "hell"])
        assert scenario.injector.calls == []
        scenario.service.handle("toggle")
        wait_for(lambda: scenario.injector.injected == ["hello"])
        assert scenario.status_ui.partials == ["Hel", "hell"]
    finally:
        cleanup(scenario)


def test_status_reports_the_current_state(tmp_path: Path) -> None:
    scenario = prepare(tmp_path, ServiceState.RECORDING)
    try:
        response = scenario.service.handle("status")
        assert response.ok is True
        assert response.state is ServiceState.RECORDING
        assert response.error is None
        assert scenario.injector.calls == []
    finally:
        cleanup(scenario)


def test_session_protocol_is_satisfied_by_the_fake() -> None:
    session: DictationSession = FakeDictationSession()
    assert isinstance(session, DictationSession)


def test_response_is_a_frozen_dataclass() -> None:
    response = CommandResponse(ok=True, state=ServiceState.IDLE)
    assert response.error is None
    with pytest.raises(Exception):
        response.ok = False  # type: ignore[misc]
