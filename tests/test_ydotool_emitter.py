"""Tests for the batched ``ydotool key`` emitter (ADR 0003, ADR 0012, ADR 0020).

Nothing here runs ``ydotool``, touches ``/dev/uinput``, or needs a compositor:
the delivery ``runner`` seam is replaced by :class:`FakeRunner` and the mapping
seam by :class:`FakeKeySequenceSource`. A handful of tests compile a real
``us``/``hu`` keymap through ``libxkbcommon`` to pin the actual keycodes, which
is headless (ADR 0014).

The headline behaviours pinned here:

* the argv is ``ydotool key -d <ms> KEYCODE:1 KEYCODE:0 ...`` -- **one spawn**
  per injection, and ``ydotool type`` is never invoked;
* the assembled text is never an argv element;
* every batch is press/release balanced and contains no Return/Esc when the
  text had no newline;
* unreachable characters are skipped with a warning; an all-unreachable
  transcription raises instead of typing nothing.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import pytest

from ges2t.injectors.keymap import KeyEvent, KeySequence, XkbKeymap
from ges2t.injectors.layout import LayoutCommandRunner
from ges2t.injectors.ydotool import (
    CommandRunner,
    KEY_ARGUMENTS,
    KEY_DELAY_FLAG,
    KeySequenceSource,
    XkbKeySequenceSource,
    YDOTOOL_BINARY,
    YdotoolDaemonUnreachableError,
    YdotoolError,
    YdotoolInjectionError,
    YdotoolInjector,
    YdotoolKeymapError,
    YdotoolNotFoundError,
    YdotoolTextUnreachableError,
    YdotoolUnreachableWarning,
)
from ges2t.interfaces import Injector

# Linux/evdev ``KEY_*`` codes the tests name directly (they are what `ydotool
# key` consumes, ticket 22's ruling).
KEY_A: int = 30
KEY_SPACE: int = 57
KEY_LEFTCTRL: int = 29
KEY_RIGHTALT: int = 100
KEY_RETURN: int = 28
KEY_ESC: int = 1


@dataclass(frozen=True)
class RunnerCall:
    """One recorded invocation of the delivery runner seam."""

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
        self.calls.append(RunnerCall(argv=tuple(argv), stdin=stdin_bytes, env=dict(env)))
        if self.error is not None:
            raise self.error
        return subprocess.CompletedProcess(
            args=list(argv),
            returncode=self.returncode,
            stdout=self.stdout,
            stderr=self.stderr,
        )


class FakeKeySequenceSource:
    """A :class:`~ges2t.injectors.ydotool.KeySequenceSource` returning a canned sequence."""

    def __init__(self, sequence: KeySequence) -> None:
        self.sequence = sequence
        self.texts: list[str] = []

    def sequence_for(self, text: str) -> KeySequence:
        self.texts.append(text)
        return self.sequence


class FakeLayoutRunner:
    """A :data:`~ges2t.injectors.layout.LayoutCommandRunner` for layout detection.

    ``xkbcli dump-keymap-wayland`` succeeds with ``keymap_text`` when it is set;
    every other call (``busctl``) fails, so :func:`resolve_layout` uses the live
    keymap branch. ``calls`` records each argv so a test can prove the keymap is
    re-read per utterance.
    """

    def __init__(self, *, keymap_text: str | None = None) -> None:
        self.keymap_text = keymap_text
        self.calls: list[tuple[str, ...]] = []

    def __call__(
        self, argv: Sequence[str], timeout: float
    ) -> subprocess.CompletedProcess[bytes]:
        del timeout
        self.calls.append(tuple(argv))
        if argv[0] == "xkbcli" and self.keymap_text is not None:
            return subprocess.CompletedProcess(
                args=list(argv), returncode=0, stdout=self.keymap_text.encode(), stderr=b""
            )
        return subprocess.CompletedProcess(
            args=list(argv), returncode=1, stdout=b"", stderr=b"unavailable"
        )


def _sequence(*events: tuple[int, bool], unreachable: tuple[str, ...] = ()) -> KeySequence:
    """Build a :class:`KeySequence` from ``(keycode, pressed)`` pairs."""
    return KeySequence(
        events=tuple(KeyEvent(keycode=code, pressed=pressed) for code, pressed in events),
        unreachable=unreachable,
    )


def _keycode_tokens(argv: Sequence[str]) -> list[tuple[int, int]]:
    """Return ``(keycode, pressed)`` for every token of the form ``K:P``."""
    pairs: list[tuple[int, int]] = []
    for token in argv:
        match = re.fullmatch(r"(\d+):([01])", token)
        if match is not None:
            pairs.append((int(match.group(1)), int(match.group(2))))
    return pairs


def _assert_press_release_balanced(argv: Sequence[str]) -> None:
    """Assert stack discipline: every press has a later release, never a double hold."""
    held: dict[int, int] = {}
    for keycode, pressed in _keycode_tokens(argv):
        held[keycode] = held.get(keycode, 0) + (1 if pressed else -1)
        assert held[keycode] in (0, 1), f"key {keycode} unbalanced"
    assert all(count == 0 for count in held.values()), f"keys left held: {held}"


def _assert_runner_fits_protocol(runner: FakeRunner) -> CommandRunner:
    """Static proof that the double satisfies the documented runner signature."""
    return runner


def _assert_layout_runner_fits_protocol(runner: FakeLayoutRunner) -> LayoutCommandRunner:
    """Static proof that the layout double satisfies the detection seam."""
    return runner


# --- argv shape and batching --------------------------------------------------


def test_argv_is_key_with_delay_then_ordered_tokens() -> None:
    source = FakeKeySequenceSource(
        _sequence((KEY_RIGHTALT, True), (24, True), (24, False), (KEY_RIGHTALT, False))
    )
    runner = FakeRunner()
    injector = YdotoolInjector(
        source, trailing_space=False, key_delay_ms=7, runner=runner
    )

    injector.inject("x")

    assert runner.calls[0].argv == (
        YDOTOOL_BINARY,
        "key",
        "-d",
        "7",
        "100:1",
        "24:1",
        "24:0",
        "100:0",
    )
    assert runner.calls[0].stdin == b""


def test_real_us_keymap_passes_linux_keycode_through_without_offset() -> None:
    runner = FakeRunner()
    injector = YdotoolInjector(
        XkbKeySequenceSource("us"), trailing_space=False, runner=runner
    )

    injector.inject("a")

    # KEY_A is Linux 30 (XKB 38); a ydotool type payload would have been "a".
    assert runner.calls[0].argv == (YDOTOOL_BINARY, "key", "-d", "0", "30:1", "30:0")


def test_ydotool_type_is_never_invoked() -> None:
    runner = FakeRunner()
    injector = YdotoolInjector(
        XkbKeySequenceSource("us"), trailing_space=False, runner=runner
    )

    injector.inject("hello")

    argv = runner.calls[0].argv
    assert argv[1] == "key"
    assert "type" not in argv


def test_text_is_never_an_argv_element() -> None:
    runner = FakeRunner()
    injector = YdotoolInjector(
        XkbKeySequenceSource("us"), trailing_space=False, runner=runner
    )

    injector.inject("secretword")

    argv = runner.calls[0].argv
    assert "secretword" not in argv
    # Everything after `ydotool key -d <ms>` is a keycode token, not text.
    assert all(re.fullmatch(r"\d+:[01]", token) for token in argv[4:])


def test_one_injection_is_one_spawn() -> None:
    events = tuple(
        (code, pressed)
        for code in range(KEY_A, KEY_A + 200)
        for pressed in (True, False)
    )
    runner = FakeRunner()
    injector = YdotoolInjector(
        FakeKeySequenceSource(_sequence(*events)), trailing_space=False, runner=runner
    )

    injector.inject("a" * 200)

    assert len(runner.calls) == 1
    assert len(runner.calls[0].argv) == 4 + len(events)


def test_default_key_delay_is_the_measured_zero_ms() -> None:
    # 0 ms comes from ticket 23's measurement (`001-comment.md`): 0/6/12 ms were
    # captured byte-perfect and only 0 fits ADR 0001's budget. Do not "tidy"
    # this to a margin.
    runner = FakeRunner()
    injector = YdotoolInjector(
        FakeKeySequenceSource(_sequence((KEY_A, True), (KEY_A, False))), runner=runner
    )

    injector.inject("a")

    assert runner.calls[0].argv[1:4] == ("key", KEY_DELAY_FLAG, "0")


# --- ADR 0012 text rules at the emitter boundary -----------------------------


def test_trailing_space_is_mapped_into_the_batch() -> None:
    runner = FakeRunner()
    injector = YdotoolInjector(
        XkbKeySequenceSource("us"), trailing_space=True, runner=runner
    )

    injector.inject("a")

    assert runner.calls[0].argv[-2:] == ("57:1", "57:0")  # KEY_SPACE


def test_no_trailing_space_emits_no_space_key() -> None:
    runner = FakeRunner()
    injector = YdotoolInjector(
        XkbKeySequenceSource("us"), trailing_space=False, runner=runner
    )

    injector.inject("a")

    assert "57:1" not in runner.calls[0].argv


@pytest.mark.parametrize("text", ["", "   ", "\t\n ", "\u00a0"])
def test_empty_or_whitespace_inject_spawns_nothing_and_maps_nothing(text: str) -> None:
    runner = FakeRunner()
    source = FakeKeySequenceSource(_sequence((KEY_A, True), (KEY_A, False)))
    injector = YdotoolInjector(source, runner=runner)

    injector.inject(text)

    assert runner.calls == []
    assert source.texts == []
    assert injector.last_injection_ms is None


def test_source_receives_the_assembled_payload_once_per_injection() -> None:
    source = FakeKeySequenceSource(_sequence((KEY_A, True), (KEY_A, False)))
    injector = YdotoolInjector(source, trailing_space=True, runner=FakeRunner())

    injector.inject("one")
    injector.inject("two")

    assert source.texts == ["one ", "two "]


# --- no spurious modifier, Return, or Escape ----------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "hello world",
        "the quick brown fox jumps over the lazy dog",
        "abc 123 plus punctuation and spaces",
    ],
)
def test_newline_free_text_emits_no_return_or_escape(text: str) -> None:
    runner = FakeRunner()
    injector = YdotoolInjector(
        XkbKeySequenceSource("us"), trailing_space=True, runner=runner
    )

    injector.inject(text)

    argv = runner.calls[0].argv
    for token in (f"{KEY_RETURN}:1", f"{KEY_RETURN}:0", f"{KEY_ESC}:1", f"{KEY_ESC}:0"):
        assert token not in argv


def test_plain_text_uses_no_control_or_altgr_modifier() -> None:
    runner = FakeRunner()
    injector = YdotoolInjector(
        XkbKeySequenceSource("us"), trailing_space=True, runner=runner
    )

    injector.inject("hello world")

    argv = runner.calls[0].argv
    assert f"{KEY_LEFTCTRL}:1" not in argv
    assert f"{KEY_RIGHTALT}:1" not in argv


@pytest.mark.parametrize("layout,text", [("us", "hello world"), ("hu", "\u00f6\u00fc\u00f3\u0151\u00fa\u0171")])
def test_every_emitted_batch_is_press_release_balanced(layout: str, text: str) -> None:
    runner = FakeRunner()
    injector = YdotoolInjector(
        XkbKeySequenceSource(layout), trailing_space=True, runner=runner
    )

    injector.inject(text)

    _assert_press_release_balanced(runner.calls[0].argv)


# --- unreachable characters ---------------------------------------------------


def test_unreachable_characters_are_skipped_with_a_warning() -> None:
    source = FakeKeySequenceSource(
        _sequence((KEY_A, True), (KEY_A, False), unreachable=("\U0001f600",))
    )
    runner = FakeRunner()
    injector = YdotoolInjector(source, trailing_space=False, runner=runner)

    with pytest.warns(YdotoolUnreachableWarning) as record:
        injector.inject("a\U0001f600")

    assert len(runner.calls) == 1
    assert runner.calls[0].argv == (YDOTOOL_BINARY, "key", "-d", "0", "30:1", "30:0")
    assert "\U0001f600" in str(record[0].message)


def test_all_unreachable_raises_and_spawns_nothing() -> None:
    source = FakeKeySequenceSource(
        _sequence(unreachable=("\U0001f600", "\U0001f602"))
    )
    runner = FakeRunner()
    injector = YdotoolInjector(source, trailing_space=False, runner=runner)

    with pytest.raises(YdotoolTextUnreachableError) as excinfo:
        injector.inject("\U0001f600\U0001f602")

    assert runner.calls == []
    assert injector.last_injection_ms is None
    message = str(excinfo.value)
    assert "\U0001f600" in message
    assert "nothing was injected" in message
    assert isinstance(excinfo.value, YdotoolError)


def test_partial_unreachable_still_records_a_delivery_time() -> None:
    source = FakeKeySequenceSource(
        _sequence((KEY_A, True), (KEY_A, False), unreachable=("\U0001f600",))
    )
    injector = YdotoolInjector(source, trailing_space=False, runner=FakeRunner())

    with pytest.warns(YdotoolUnreachableWarning):
        injector.inject("a\U0001f600")

    assert injector.last_injection_ms is not None
    assert injector.last_injection_ms >= 0.0


# --- the XkbKeySequenceSource (mapping seam) ----------------------------------


def test_xkb_source_satisfies_the_key_sequence_source_protocol() -> None:
    assert isinstance(XkbKeySequenceSource("us"), KeySequenceSource)


def test_explicit_layout_needs_no_layout_detection() -> None:
    def exploding_runner(
        argv: Sequence[str], timeout: float
    ) -> subprocess.CompletedProcess[bytes]:
        raise AssertionError(f"layout detection must not run, got {argv!r} ({timeout})")

    source = XkbKeySequenceSource("us", runner=exploding_runner)

    sequence = source.sequence_for("a")

    assert [(event.keycode, event.pressed) for event in sequence.events] == [
        (KEY_A, True),
        (KEY_A, False),
    ]
    assert sequence.unreachable == ()


def test_live_keymap_branch_compiles_the_compositor_keymap() -> None:
    keymap_text = XkbKeymap.from_names("us").to_string()
    runner = FakeLayoutRunner(keymap_text=keymap_text)
    source = XkbKeySequenceSource("auto", runner=runner)

    sequence = source.sequence_for("a")

    assert [(event.keycode, event.pressed) for event in sequence.events] == [
        (KEY_A, True),
        (KEY_A, False),
    ]


def test_xkb_source_rebuilds_the_keymap_per_utterance() -> None:
    keymap_text = XkbKeymap.from_names("us").to_string()
    runner = FakeLayoutRunner(keymap_text=keymap_text)
    source = XkbKeySequenceSource("auto", runner=runner)

    first = source.sequence_for("a")
    second = source.sequence_for("a")

    assert first == second
    dumps = [call for call in runner.calls if call[0] == "xkbcli"]
    assert len(dumps) == 2, f"the keymap must be re-read per utterance: {runner.calls}"


def test_unresolvable_layout_raises_a_typed_keymap_error() -> None:
    runner = FakeLayoutRunner(keymap_text=None)
    source = XkbKeySequenceSource("auto", runner=runner)

    with pytest.raises(YdotoolKeymapError) as excinfo:
        source.sequence_for("a")

    assert isinstance(excinfo.value, YdotoolError)
    assert "layout" in str(excinfo.value).lower()


def test_emitter_translates_keymap_failure_to_a_ydotool_error() -> None:
    runner = FakeLayoutRunner(keymap_text=None)
    injector = YdotoolInjector(
        XkbKeySequenceSource("auto", runner=runner), runner=FakeRunner()
    )

    with pytest.raises(YdotoolError):
        injector.inject("a")


# --- delivery failures (ticket 7's contract, preserved) -----------------------


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
    injector = YdotoolInjector(_canned_source(), runner=runner)

    with pytest.raises(YdotoolDaemonUnreachableError) as excinfo:
        injector.inject("text")

    message = str(excinfo.value)
    assert "ydotoold" in message
    assert "systemctl --user enable --now ydotool" in message
    assert "/dev/uinput" in message
    assert "input" in message
    assert "fallback" in message
    assert isinstance(excinfo.value, YdotoolError)


def test_missing_binary_raises_not_found_with_path_guidance() -> None:
    runner = FakeRunner(error=FileNotFoundError("ydotool"))
    injector = YdotoolInjector(_canned_source(), runner=runner)

    with pytest.raises(YdotoolNotFoundError) as excinfo:
        injector.inject("text")

    message = str(excinfo.value)
    assert "PATH" in message
    assert "ydotool" in message
    assert "pacman" in message
    assert isinstance(excinfo.value, YdotoolError)


def test_nonzero_exit_is_reported_and_not_swallowed() -> None:
    runner = FakeRunner(returncode=1, stderr=b"uinput write failed\n")
    injector = YdotoolInjector(_canned_source(), runner=runner)

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
    injector = YdotoolInjector(_canned_source(), runner=runner)

    with pytest.raises(YdotoolInjectionError) as excinfo:
        injector.inject("text")

    assert "<empty>" in str(excinfo.value)


def test_error_priority_daemon_message_wins_over_generic() -> None:
    runner = FakeRunner(
        returncode=2, stderr=b"failed to connect socket: No such file or directory"
    )
    injector = YdotoolInjector(_canned_source(), runner=runner)

    with pytest.raises(YdotoolDaemonUnreachableError):
        injector.inject("text")


def _canned_source() -> FakeKeySequenceSource:
    """A source that maps any text to ``a``, for delivery-failure tests."""
    return FakeKeySequenceSource(_sequence((KEY_A, True), (KEY_A, False)))


# --- environment and interface conformance ------------------------------------


def test_ydotool_socket_is_passed_through_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YDOTOOL_SOCKET", "/tmp/custom.sock")
    runner = FakeRunner()

    YdotoolInjector(_canned_source(), runner=runner).inject("sock")

    assert runner.calls[0].env["YDOTOOL_SOCKET"] == "/tmp/custom.sock"


def test_ydotool_socket_is_absent_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YDOTOOL_SOCKET", raising=False)
    runner = FakeRunner()

    YdotoolInjector(_canned_source(), runner=runner).inject("sock")

    assert "YDOTOOL_SOCKET" not in runner.calls[0].env


def test_injector_satisfies_the_injector_protocol() -> None:
    assert isinstance(YdotoolInjector(_canned_source(), runner=FakeRunner()), Injector)


def test_skip_records_no_delivery_time() -> None:
    injector = YdotoolInjector(_canned_source(), runner=FakeRunner())

    injector.inject("   ")

    assert injector.last_injection_ms is None


def test_the_fake_runner_is_accepted_as_a_command_runner() -> None:
    # Guards the seam signature: FakeRunner must remain callable where a
    # CommandRunner is expected (and nothing here runs ``ydotool``).
    runner = _assert_runner_fits_protocol(FakeRunner())
    runner((YDOTOOL_BINARY, *KEY_ARGUMENTS), b"", {})


def test_the_fake_layout_runner_is_accepted_as_a_layout_runner() -> None:
    source = _assert_layout_runner_fits_protocol(FakeLayoutRunner())
    result = source(("xkbcli", "dump-keymap-wayland"), 2.0)
    assert result.returncode == 1
