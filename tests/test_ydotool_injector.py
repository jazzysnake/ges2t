"""Tests for the ``ydotool`` injector (ADR 0003, ADR 0012, ADR 0014).

Nothing here runs ``ydotool`` or touches ``/dev/uinput``: the injector's
``runner`` seam is replaced by :class:`FakeRunner`. The ADR 0012 text-assembly
table is exercised directly through :func:`decide_injection` *and* end to end
through :meth:`YdotoolInjector.inject`, because the ticket makes it the primary
unit-test target.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import pytest

from ges2t.injectors.ydotool import (
    CommandRunner,
    SkipInjection,
    TYPE_ARGUMENTS,
    TypeText,
    YDOTOOL_BINARY,
    YdotoolDaemonUnreachableError,
    YdotoolError,
    YdotoolInjectionError,
    YdotoolInjector,
    YdotoolNotFoundError,
    decide_injection,
)
from ges2t.interfaces import Injector


@dataclass(frozen=True)
class RunnerCall:
    """One recorded invocation of the runner seam."""

    argv: tuple[str, ...]
    stdin: bytes
    env: Mapping[str, str]


class FakeRunner:
    """A :data:`~ges2t.injectors.ydotool.CommandRunner` that runs nothing.

    Set ``error`` to raise instead of returning, or ``returncode``/``stderr`` to
    simulate a finished process. Every call is recorded in ``calls`` in order.
    """

    def __init__(
        self,
        *,
        returncode: int = 0,
        stderr: bytes = b"",
        stdout: bytes = b"",
        error: Exception | None = None,
    ) -> None:
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout
        self.error = error
        self.calls: list[RunnerCall] = []

    def __call__(
        self, argv: Sequence[str], stdin_bytes: bytes, env: Mapping[str, str]
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(
            RunnerCall(argv=tuple(argv), stdin=stdin_bytes, env=dict(env))
        )
        if self.error is not None:
            raise self.error
        return subprocess.CompletedProcess(
            args=list(argv),
            returncode=self.returncode,
            stdout=self.stdout,
            stderr=self.stderr,
        )


def _assert_runner_fits_protocol(runner: FakeRunner) -> CommandRunner:
    """Static proof that the double satisfies the documented runner signature."""
    return runner


# --- the ADR 0012 text-assembly table, tested directly ------------------------


def test_empty_text_decides_to_skip() -> None:
    decision = decide_injection("", trailing_space=True)
    assert isinstance(decision, SkipInjection)


@pytest.mark.parametrize("text", [" ", "   ", "\t", "\n", " \t\n ", "\u00a0"])
def test_whitespace_only_text_decides_to_skip(text: str) -> None:
    assert isinstance(decide_injection(text, trailing_space=True), SkipInjection)
    assert isinstance(decide_injection(text, trailing_space=False), SkipInjection)


def test_trailing_space_appends_exactly_one_space() -> None:
    decision = decide_injection("hello world", trailing_space=True)
    assert isinstance(decision, TypeText)
    assert decision.payload == "hello world "


def test_no_trailing_space_leaves_text_unchanged() -> None:
    decision = decide_injection("hello world", trailing_space=False)
    assert isinstance(decision, TypeText)
    assert decision.payload == "hello world"


def test_assembly_never_strips_leading_or_trailing_whitespace() -> None:
    text = "  padded  "
    decision = decide_injection(text, trailing_space=False)
    assert isinstance(decision, TypeText)
    assert decision.payload == "  padded  "


def test_assembly_never_collapses_internal_whitespace() -> None:
    text = "two   spaces\tand\ttabs"
    decision = decide_injection(text, trailing_space=False)
    assert isinstance(decision, TypeText)
    assert decision.payload == "two   spaces\tand\ttabs"


def test_assembly_never_changes_case_or_punctuation() -> None:
    text = "Mixed CASE, punctuation?!"
    decision = decide_injection(text, trailing_space=False)
    assert isinstance(decision, TypeText)
    assert decision.payload == "Mixed CASE, punctuation?!"


def test_assembly_preserves_non_ascii_text() -> None:
    text = "Gr\u00fc\u00dfe \u2013 caf\u00e9 \U0001f600"
    decision = decide_injection(text, trailing_space=False)
    assert isinstance(decision, TypeText)
    assert decision.payload == text


def test_text_already_ending_in_space_gets_a_second_one() -> None:
    # "append exactly one space", not "normalise to one space" (ADR 0012).
    decision = decide_injection("done ", trailing_space=True)
    assert isinstance(decision, TypeText)
    assert decision.payload == "done  "


def test_decision_types_are_frozen() -> None:
    from dataclasses import FrozenInstanceError

    decision = decide_injection("hi", trailing_space=False)
    assert isinstance(decision, TypeText)
    with pytest.raises(FrozenInstanceError):
        decision.payload = "changed"  # type: ignore[misc]


# --- inject() delivery through the runner seam --------------------------------


def test_injector_satisfies_the_injector_protocol() -> None:
    injector = YdotoolInjector(runner=FakeRunner())
    assert isinstance(injector, Injector)


def test_inject_writes_payload_to_stdin_not_argv() -> None:
    runner = FakeRunner()
    injector = YdotoolInjector(trailing_space=True, runner=runner)

    injector.inject("write this down")

    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert call.argv == (YDOTOOL_BINARY, *TYPE_ARGUMENTS)
    assert call.argv == ("ydotool", "type", "--file", "-")
    assert call.stdin == b"write this down "
    # The text must never appear in argv (that is the whole point of --file -).
    assert all("write this down" not in argument for argument in call.argv)


def test_inject_without_trailing_space_sends_text_unchanged() -> None:
    runner = FakeRunner()
    injector = YdotoolInjector(trailing_space=False, runner=runner)

    injector.inject("unchanged")

    assert runner.calls[0].stdin == b"unchanged"


def test_inject_encodes_non_ascii_payload_as_utf8() -> None:
    runner = FakeRunner()
    injector = YdotoolInjector(trailing_space=False, runner=runner)

    injector.inject("Gr\u00fc\u00dfe")

    assert runner.calls[0].stdin == "Gr\u00fc\u00dfe".encode("utf-8")


@pytest.mark.parametrize("text", ["", "   ", "\t\n ", "\u00a0"])
def test_whitespace_only_inject_performs_no_call(text: str) -> None:
    runner = FakeRunner()
    injector = YdotoolInjector(runner=runner)

    injector.inject(text)

    assert runner.calls == []


def test_successful_inject_records_a_positive_duration() -> None:
    injector = YdotoolInjector(runner=FakeRunner())
    injector.inject("timed")
    assert injector.last_injection_ms is not None
    assert injector.last_injection_ms >= 0.0


def test_skipped_inject_records_no_duration() -> None:
    injector = YdotoolInjector(runner=FakeRunner())
    injector.inject("   ")
    assert injector.last_injection_ms is None


def test_single_runner_call_for_a_long_payload() -> None:
    runner = FakeRunner()
    injector = YdotoolInjector(runner=runner)

    injector.inject("word " * 500)

    assert len(runner.calls) == 1


# --- environment: YDOTOOL_SOCKET ---------------------------------------------


def test_ydotool_socket_is_passed_through_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YDOTOOL_SOCKET", "/tmp/custom.sock")
    runner = FakeRunner()

    YdotoolInjector(runner=runner).inject("sock")

    assert runner.calls[0].env["YDOTOOL_SOCKET"] == "/tmp/custom.sock"


def test_ydotool_socket_is_absent_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YDOTOOL_SOCKET", raising=False)
    runner = FakeRunner()

    YdotoolInjector(runner=runner).inject("sock")

    assert "YDOTOOL_SOCKET" not in runner.calls[0].env


# --- failure modes ------------------------------------------------------------


def test_missing_binary_raises_not_found_with_path_guidance() -> None:
    runner = FakeRunner(error=FileNotFoundError("ydotool"))
    injector = YdotoolInjector(runner=runner)

    with pytest.raises(YdotoolNotFoundError) as excinfo:
        injector.inject("text")

    message = str(excinfo.value)
    assert "PATH" in message
    assert "ydotool" in message
    assert "pacman" in message
    assert isinstance(excinfo.value, YdotoolError)


@pytest.mark.parametrize(
    "stderr",
    [
        b"failed to connect socket `/run/user/1000/.ydotool_socket': No such file or directory\nPlease check if ydotoold is running.\n",
        b"failed to connect socket: Connection refused\n",
        b"Please check if ydotoold is running.\n",
    ],
)
def test_daemon_unreachable_raises_actionable_error(stderr: bytes) -> None:
    runner = FakeRunner(returncode=2, stderr=stderr)
    injector = YdotoolInjector(runner=runner)

    with pytest.raises(YdotoolDaemonUnreachableError) as excinfo:
        injector.inject("text")

    message = str(excinfo.value)
    assert "ydotoold" in message
    assert "systemctl --user enable --now ydotool" in message
    assert "/dev/uinput" in message
    assert "input" in message
    assert "fallback" in message
    assert isinstance(excinfo.value, YdotoolError)


def test_nonzero_exit_is_reported_and_not_swallowed() -> None:
    runner = FakeRunner(returncode=1, stderr=b"uinput write failed\n")
    injector = YdotoolInjector(runner=runner)

    with pytest.raises(YdotoolInjectionError) as excinfo:
        injector.inject("partial text")

    error = excinfo.value
    assert error.returncode == 1
    assert error.stderr == "uinput write failed"
    assert "1" in str(error)
    assert "uinput write failed" in str(error)
    assert "incomplete" in str(error)


def test_nonzero_exit_with_empty_stderr_still_raises() -> None:
    runner = FakeRunner(returncode=3, stderr=b"")
    injector = YdotoolInjector(runner=runner)

    with pytest.raises(YdotoolInjectionError) as excinfo:
        injector.inject("text")

    assert "<empty>" in str(excinfo.value)


def test_error_priority_daemon_message_wins_over_generic() -> None:
    runner = FakeRunner(
        returncode=2, stderr=b"failed to connect socket: No such file or directory"
    )
    injector = YdotoolInjector(runner=runner)

    with pytest.raises(YdotoolDaemonUnreachableError):
        injector.inject("text")


def test_the_fake_runner_is_accepted_as_a_command_runner() -> None:
    # Guards the seam signature: FakeRunner must remain callable where a
    # CommandRunner is expected (and nothing here runs ``ydotool``).
    runner = _assert_runner_fits_protocol(FakeRunner())
    runner(("ydotool", "type", "--file", "-"), b"x", {})
