"""Tests for the clipboard-paste injector (ticket 25, ADR 0003/0012/0020).

Nothing here writes the real clipboard, runs ``wl-copy``/``wl-paste``, or emits
a keystroke: the clipboard ``runner`` seam is replaced by
:class:`FakeClipboardRunner`. Writing to the clipboard destroys the user's
current selection, which is a real, externally visible side effect, so no test in
this repository is allowed to do it (ADR 0014).

The headline behaviours pinned here:

* the paste chord is sent as **raw Linux/evdev keycodes** -- ``ctrl+v`` is
  exactly ``29:1 47:1 47:0 29:0`` with no offset -- and never as text;
* transcript text never appears in any argv element, and the ``ydotool`` chord
  invocation reads **nothing** from stdin (the text is delivered to ``wl-copy``,
  which is the mechanism);
* ADR 0012's table is reused unchanged: empty/whitespace-only input spawns **no**
  process at all, and exactly one trailing space is appended when enabled;
* the read -> copy -> paste -> restore order holds, and a failing restore never
  fails the injection;
* every failure mode is a typed :class:`ClipboardError`.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Mapping, Sequence

import pytest

from ges2t.injectors.clipboard import (
    CLIPBOARD_RESTORE_DELAY_SECONDS,
    ClipboardError,
    ClipboardInjectionError,
    ClipboardKeyChordError,
    ClipboardNotFoundError,
    ClipboardPasteInjector,
    ClipboardRestoreWarning,
    ClipboardRunner,
    ClipboardRunnerCall,
    ClipboardSpawnError,
    ClipboardTimeoutError,
    KeyChord,
    WL_COPY_BINARY,
    WL_PASTE_BINARY,
    is_modifier_keycode,
)
from ges2t.injectors.layout import (
    DEFAULT_KEY_DELAY_MS,
    DEFAULT_PASTE_KEYS,
    ClipboardConfig,
)
from ges2t.injectors.ydotool import YDOTOOL_BINARY
from ges2t.interfaces import Injector

# Linux/evdev ``KEY_*`` codes the tests name directly. They are the unit
# ``ydotool key`` consumes (ticket 22's ruling: no XKB offset).
KEY_LEFTCTRL: int = 29
KEY_LEFTSHIFT: int = 42
KEY_V: int = 47


class FakeClipboardRunner:
    """A :class:`~ges2t.injectors.clipboard.ClipboardRunner` that runs nothing.

    Behaviour is configured per binary so a test can simulate exactly one
    failure: ``wl-paste`` (the read), ``wl-copy`` (the payload write and the
    restore), and ``ydotool`` (the chord). Every call is recorded, in order, in
    ``calls``. ``restore_*`` overrides the ``wl-copy`` result for the *deferred*
    (``delay_s is not None``) call only. ``raise_for`` makes a binary raise
    instead of returning -- ``FileNotFoundError`` proves the missing-executable
    path.
    """

    def __init__(
        self,
        *,
        paste_stdout: bytes = b"",
        paste_returncode: int = 0,
        copy_returncode: int = 0,
        copy_stderr: bytes = b"",
        restore_returncode: int = 0,
        restore_stderr: bytes = b"",
        restore_raise: Exception | None = None,
        ydotool_returncode: int = 0,
        ydotool_stderr: bytes = b"",
        raise_for: Mapping[str, Exception] | None = None,
    ) -> None:
        self.paste_stdout = paste_stdout
        self.paste_returncode = paste_returncode
        self.copy_returncode = copy_returncode
        self.copy_stderr = copy_stderr
        self.restore_returncode = restore_returncode
        self.restore_stderr = restore_stderr
        self.restore_raise = restore_raise
        self.ydotool_returncode = ydotool_returncode
        self.ydotool_stderr = ydotool_stderr
        self.raise_for = dict(raise_for or {})
        self.calls: list[ClipboardRunnerCall] = []

    def __call__(
        self,
        argv: Sequence[str],
        stdin_bytes: bytes,
        env: Mapping[str, str],
        *,
        delay_s: float | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(
            ClipboardRunnerCall(
                argv=tuple(argv), stdin=stdin_bytes, env=dict(env), delay_s=delay_s
            )
        )
        binary = argv[0]
        error = self.raise_for.get(binary)
        if error is not None:
            raise error
        if binary == WL_COPY_BINARY and delay_s is not None and self.restore_raise is not None:
            raise self.restore_raise
        if binary == WL_PASTE_BINARY:
            return self._completed(argv, self.paste_returncode, self.paste_stdout, b"")
        if binary == WL_COPY_BINARY:
            if delay_s is not None:
                return self._completed(
                    argv, self.restore_returncode, b"", self.restore_stderr
                )
            return self._completed(argv, self.copy_returncode, b"", self.copy_stderr)
        if binary == YDOTOOL_BINARY:
            return self._completed(
                argv, self.ydotool_returncode, b"", self.ydotool_stderr
            )
        return self._completed(argv, 0, b"", b"")

    @staticmethod
    def _completed(
        argv: Sequence[str], returncode: int, stdout: bytes, stderr: bytes
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            args=list(argv), returncode=returncode, stdout=stdout, stderr=stderr
        )

    # -- query helpers -------------------------------------------------------

    def binaries(self) -> list[str]:
        """The ``argv[0]`` of each recorded call, in order."""
        return [call.argv[0] for call in self.calls]

    def ydotool_calls(self) -> list[ClipboardRunnerCall]:
        """Only the chord invocations."""
        return [call for call in self.calls if call.argv[0] == YDOTOOL_BINARY]

    def copy_calls(self) -> list[ClipboardRunnerCall]:
        """Only the immediate ``wl-copy`` payload writes (not the restore)."""
        return [
            call
            for call in self.calls
            if call.argv[0] == WL_COPY_BINARY and call.delay_s is None
        ]

    def restore_calls(self) -> list[ClipboardRunnerCall]:
        """Only the deferred restore invocations."""
        return [call for call in self.calls if call.delay_s is not None]


def _injector(
    runner: FakeClipboardRunner,
    *,
    paste_keys: Sequence[str] = ("ctrl+v",),
    trailing_space: bool = True,
    key_delay_ms: int = DEFAULT_KEY_DELAY_MS,
    restore_delay_s: float = CLIPBOARD_RESTORE_DELAY_SECONDS,
) -> ClipboardPasteInjector:
    """Build the injector under test with the fake runner and no real clipboard."""
    return ClipboardPasteInjector(
        paste_keys=paste_keys,
        trailing_space=trailing_space,
        key_delay_ms=key_delay_ms,
        restore_delay_s=restore_delay_s,
        runner=runner,
    )


def _assert_runner_fits_protocol(runner: FakeClipboardRunner) -> ClipboardRunner:
    """Static proof that the double satisfies the documented runner signature."""
    return runner


# --- the paste chord: raw evdev keycodes, no offset ---------------------------


def test_ctrl_v_argv_is_the_literal_evdev_chord() -> None:
    runner = FakeClipboardRunner()
    injector = _injector(runner, paste_keys=("ctrl+v",), key_delay_ms=12)

    injector.inject("hi")

    # Literal tokens, not a comparison against a constant: KEY_LEFTCTRL is 29
    # and KEY_V is 47 in Linux/evdev, with NO XKB offset (ticket 22's ruling).
    assert runner.ydotool_calls()[0].argv == (
        "ydotool",
        "key",
        "-d",
        "12",
        "29:1",
        "47:1",
        "47:0",
        "29:0",
    )


def test_ctrl_shift_v_releases_modifiers_in_reverse_order() -> None:
    runner = FakeClipboardRunner()
    injector = _injector(runner, paste_keys=("ctrl+shift+v",), key_delay_ms=0)

    injector.inject("hi")

    # Ctrl and Shift pressed left-to-right, released right-to-left.
    assert runner.ydotool_calls()[0].argv == (
        "ydotool",
        "key",
        "-d",
        "0",
        "29:1",
        "42:1",
        "47:1",
        "47:0",
        "42:0",
        "29:0",
    )


def test_ydotool_type_is_never_invoked_and_chord_is_keycodes_only() -> None:
    runner = FakeClipboardRunner()
    _injector(runner).inject("hello")

    argv = runner.ydotool_calls()[0].argv
    assert argv[1] == "key"
    assert "type" not in argv
    # Everything after `ydotool key -d <ms>` is a keycode token, never text.
    assert all(re.fullmatch(r"\d+:[01]", token) for token in argv[4:])


def test_chord_stdin_is_empty() -> None:
    runner = FakeClipboardRunner()
    _injector(runner).inject("hello")

    assert runner.ydotool_calls()[0].stdin == b""


def test_chord_is_emitted_once_per_configured_chord() -> None:
    runner = FakeClipboardRunner()
    injector = _injector(runner, paste_keys=("ctrl+v", "ctrl+shift+v"))

    injector.inject("hi")

    assert [call.argv for call in runner.ydotool_calls()] == [
        ("ydotool", "key", "-d", str(DEFAULT_KEY_DELAY_MS), "29:1", "47:1", "47:0", "29:0"),
        (
            "ydotool",
            "key",
            "-d",
            str(DEFAULT_KEY_DELAY_MS),
            "29:1",
            "42:1",
            "47:1",
            "47:0",
            "42:0",
            "29:0",
        ),
    ]


# --- text never reaches argv; the payload goes to the clipboard ---------------


def test_text_is_never_an_argv_element() -> None:
    runner = FakeClipboardRunner()
    _injector(runner).inject("secretword")

    for call in runner.calls:
        assert "secretword" not in call.argv
        assert not any("secretword" in token for token in call.argv)


def test_payload_is_delivered_to_wl_copy_over_stdin() -> None:
    runner = FakeClipboardRunner()
    _injector(runner).inject("hello world")

    write = runner.copy_calls()[0]
    assert write.argv == ("wl-copy",)
    assert write.stdin == b"hello world "


def test_text_never_reaches_the_chord_stdin() -> None:
    runner = FakeClipboardRunner()
    _injector(runner).inject("secretword")

    # The only streams carrying the transcript are wl-copy's; ydotool reads
    # nothing, so the typing transport can never re-map the text.
    for call in runner.ydotool_calls():
        assert call.stdin == b""
        assert b"secretword" not in call.stdin


# --- ADR 0012: the shared text table, reused unchanged ------------------------


@pytest.mark.parametrize("text", ["", " ", "   ", "\t", "\n", " \t\n ", "\u00a0"])
def test_empty_or_whitespace_injects_nothing_and_copies_nothing(text: str) -> None:
    runner = FakeClipboardRunner()
    injector = _injector(runner)

    injector.inject(text)

    assert runner.calls == []
    assert injector.last_injection_ms is None


def test_trailing_space_appends_exactly_one_space() -> None:
    runner = FakeClipboardRunner()
    _injector(runner, trailing_space=True).inject("hello world")

    assert runner.copy_calls()[0].stdin == b"hello world "


def test_no_trailing_space_leaves_text_unchanged() -> None:
    runner = FakeClipboardRunner()
    _injector(runner, trailing_space=False).inject("hello world")

    assert runner.copy_calls()[0].stdin == b"hello world"


def test_text_already_ending_in_space_gets_a_second_one() -> None:
    runner = FakeClipboardRunner()
    _injector(runner, trailing_space=True).inject("done ")

    assert runner.copy_calls()[0].stdin == b"done  "


def test_non_ascii_text_is_delivered_verbatim() -> None:
    runner = FakeClipboardRunner()
    payload = "Gr\u00fc\u00dfe \u2013 caf\u00e9 \U0001f600"
    _injector(runner, trailing_space=False).inject(payload)

    assert runner.copy_calls()[0].stdin == payload.encode("utf-8")


# --- sequence: read -> copy -> paste -> restore -------------------------------


def test_delivery_order_is_read_copy_paste_restore() -> None:
    runner = FakeClipboardRunner(paste_stdout=b"previous")
    _injector(runner).inject("hi")

    assert runner.binaries() == [WL_PASTE_BINARY, WL_COPY_BINARY, YDOTOOL_BINARY, WL_COPY_BINARY]
    assert runner.restore_calls()[0].stdin == b"previous"


def test_clipboard_is_read_before_the_payload_overwrites_it() -> None:
    runner = FakeClipboardRunner(paste_stdout=b"old selection")
    _injector(runner).inject("hi")

    read_index = runner.binaries().index(WL_PASTE_BINARY)
    write_index = next(
        index
        for index, call in enumerate(runner.calls)
        if call.argv[0] == WL_COPY_BINARY and call.delay_s is None
    )
    assert read_index < write_index


def test_restore_uses_the_configured_bounded_delay() -> None:
    runner = FakeClipboardRunner(paste_stdout=b"previous")
    _injector(runner, restore_delay_s=0.25).inject("hi")

    assert runner.restore_calls()[0].delay_s == 0.25


def test_no_restore_when_the_previous_selection_is_unreadable() -> None:
    # An image or a password-manager selection cannot be read as text; that must
    # not fail the injection and must not trigger a bogus restore.
    runner = FakeClipboardRunner(paste_returncode=1)
    injector = _injector(runner)

    injector.inject("hi")

    assert runner.restore_calls() == []
    assert injector.last_injection_ms is not None


def test_no_restore_when_the_previous_selection_is_empty() -> None:
    runner = FakeClipboardRunner(paste_stdout=b"")
    _injector(runner).inject("hi")

    assert runner.restore_calls() == []


# --- the restore is best effort ----------------------------------------------


def test_failing_restore_does_not_fail_injection() -> None:
    runner = FakeClipboardRunner(paste_stdout=b"previous", restore_returncode=1,
                                 restore_stderr=b"wl-copy: no compositor")
    injector = _injector(runner)

    with pytest.warns(ClipboardRestoreWarning) as record:
        injector.inject("hi")

    assert injector.last_injection_ms is not None
    assert "previous clipboard" in str(record[0].message)
    assert "no compositor" in str(record[0].message)


def test_restore_spawn_failure_does_not_fail_injection() -> None:
    runner = FakeClipboardRunner(
        paste_stdout=b"previous", restore_raise=OSError("cannot fork")
    )
    injector = _injector(runner)

    with pytest.warns(ClipboardRestoreWarning):
        injector.inject("hi")

    assert injector.last_injection_ms is not None
    assert runner.restore_calls() != []


# --- delivery failures are typed and actionable -------------------------------


def test_missing_wl_copy_raises_not_found_with_guidance() -> None:
    runner = FakeClipboardRunner(raise_for={WL_COPY_BINARY: FileNotFoundError("wl-copy")})
    injector = _injector(runner)

    with pytest.raises(ClipboardNotFoundError) as excinfo:
        injector.inject("text")

    message = str(excinfo.value)
    assert "wl-copy" in message
    assert "PATH" in message
    assert "pacman" in message
    assert "ydotool" in message
    assert isinstance(excinfo.value, ClipboardError)


def test_missing_wl_paste_is_tolerated() -> None:
    runner = FakeClipboardRunner(raise_for={WL_PASTE_BINARY: FileNotFoundError("wl-paste")})
    injector = _injector(runner)

    injector.inject("text")

    assert runner.ydotool_calls()[0].argv[4:] == ("29:1", "47:1", "47:0", "29:0")
    assert runner.restore_calls() == []


def test_missing_ydotool_raises_not_found() -> None:
    runner = FakeClipboardRunner(raise_for={YDOTOOL_BINARY: FileNotFoundError("ydotool")})
    injector = _injector(runner)

    with pytest.raises(ClipboardNotFoundError) as excinfo:
        injector.inject("text")

    assert "ydotool" in str(excinfo.value)


def test_wl_copy_nonzero_exit_reports_stderr_and_does_not_swallow() -> None:
    runner = FakeClipboardRunner(copy_returncode=1, copy_stderr=b"no compositor\n")
    injector = _injector(runner)

    with pytest.raises(ClipboardInjectionError) as excinfo:
        injector.inject("text")

    error = excinfo.value
    assert error.binary == "wl-copy"
    assert error.returncode == 1
    assert "no compositor" in str(error)
    assert isinstance(error, ClipboardError)


def test_wl_copy_nonzero_with_empty_stderr_still_raises() -> None:
    runner = FakeClipboardRunner(copy_returncode=3)
    injector = _injector(runner)

    with pytest.raises(ClipboardInjectionError) as excinfo:
        injector.inject("text")

    assert "<empty>" in str(excinfo.value)


def test_ydotool_chord_nonzero_exit_reports_stderr() -> None:
    runner = FakeClipboardRunner(
        ydotool_returncode=2, ydotool_stderr=b"failed to connect socket\n"
    )
    injector = _injector(runner)

    with pytest.raises(ClipboardInjectionError) as excinfo:
        injector.inject("text")

    error = excinfo.value
    assert error.binary == "ydotool"
    assert error.returncode == 2
    assert "failed to connect socket" in str(error)


def test_timeout_maps_to_a_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeClipboardRunner(
        raise_for={WL_COPY_BINARY: subprocess.TimeoutExpired(cmd="wl-copy", timeout=5.0)}
    )
    injector = _injector(runner)

    with pytest.raises(ClipboardTimeoutError) as excinfo:
        injector.inject("text")

    assert "wl-copy" in str(excinfo.value)


def test_generic_spawn_error_maps_to_a_typed_error() -> None:
    runner = FakeClipboardRunner(raise_for={WL_COPY_BINARY: OSError("permission denied")})
    injector = _injector(runner)

    with pytest.raises(ClipboardSpawnError) as excinfo:
        injector.inject("text")

    assert "permission denied" in str(excinfo.value)


def test_delivery_records_a_wall_time() -> None:
    runner = FakeClipboardRunner()
    injector = _injector(runner)

    injector.inject("hi")

    assert injector.last_injection_ms is not None
    assert injector.last_injection_ms >= 0.0


# --- chord parsing ------------------------------------------------------------


def test_default_chord_is_ctrl_v() -> None:
    chord = KeyChord.parse(DEFAULT_PASTE_KEYS[0])

    assert chord.keycodes == (KEY_LEFTCTRL, KEY_V)
    assert chord.events[-1].pressed is False


@pytest.mark.parametrize("name,code", [("ctrl", 29), ("shift", 42), ("alt", 56),
                                        ("super", 125)])
def test_named_modifiers_resolve_to_evdev_codes(name: str, code: int) -> None:
    chord = KeyChord.parse(f"{name}+v")

    assert chord.keycodes == (code, KEY_V)
    assert is_modifier_keycode(code)


def test_insert_is_a_regular_key_not_a_modifier() -> None:
    chord = KeyChord.parse("ctrl+insert")

    assert chord.keycodes == (29, 110)
    assert not is_modifier_keycode(110)


def test_letter_and_digit_keys_resolve_without_offset() -> None:
    assert KeyChord.parse("ctrl+a").keycodes == (29, 30)
    assert KeyChord.parse("ctrl+z").keycodes == (29, 44)
    assert KeyChord.parse("ctrl+1").keycodes == (29, 2)
    assert KeyChord.parse("ctrl+0").keycodes == (29, 11)


def test_spelling_is_case_insensitive_and_accepts_variants() -> None:
    assert KeyChord.parse("CTRL+V").keycodes == (29, 47)
    assert KeyChord.parse("Control+Shift+V").keycodes == (29, 42, 47)
    assert KeyChord.parse("meta+v").keycodes == KeyChord.parse("super+v").keycodes


@pytest.mark.parametrize("spelling", ["", "ctrl+", "+v", "ctrl++v", "ctrl+ü"])
def test_malformed_chord_raises_actionable_error(spelling: str) -> None:
    with pytest.raises(ClipboardKeyChordError) as excinfo:
        KeyChord.parse(spelling)

    assert isinstance(excinfo.value, ClipboardError)
    message = str(excinfo.value)
    assert "paste key" in message


def test_unknown_key_name_error_mentions_terminals_need_ctrl_shift_v() -> None:
    with pytest.raises(ClipboardKeyChordError) as excinfo:
        KeyChord.parse("ctrl+foo")

    message = str(excinfo.value)
    assert "unknown key" in message
    assert "ctrl+shift+v" in message
    assert isinstance(excinfo.value, ValueError)


def test_chord_ending_with_a_modifier_raises() -> None:
    with pytest.raises(ClipboardKeyChordError) as excinfo:
        KeyChord.parse("ctrl+shift")

    assert "modifier" in str(excinfo.value)


def test_injector_rejects_a_malformed_paste_key_at_construction() -> None:
    with pytest.raises(ClipboardKeyChordError):
        _injector(FakeClipboardRunner(), paste_keys=("ctrl++v",))


# --- config: the ticket 24 contract, warnings surfaced ------------------------


def test_clipboard_config_defaults_when_the_block_is_absent() -> None:
    result = ClipboardConfig.from_mapping({})

    assert result.config.paste_keys == DEFAULT_PASTE_KEYS == ("ctrl+v",)
    assert result.warnings == []


def test_clipboard_config_reads_paste_keys() -> None:
    result = ClipboardConfig.from_mapping({"paste_keys": ["ctrl+shift+v"]})

    assert result.config.paste_keys == ("ctrl+shift+v",)


def test_injector_surfaces_config_warnings() -> None:
    result = ClipboardConfig.from_mapping({"paste_keys": ["ctrl+v"], "past_keys": ["x"]})
    injector = ClipboardPasteInjector(
        paste_keys=result.config.paste_keys, warnings=result.warnings, runner=FakeClipboardRunner()
    )

    assert injector.last_warnings == [
        "injection.clipboard: unknown key 'past_keys'"
    ]


# --- interface conformance and the seam ---------------------------------------


def test_injector_satisfies_the_injector_protocol() -> None:
    assert isinstance(_injector(FakeClipboardRunner()), Injector)


def test_the_fake_runner_is_accepted_as_a_clipboard_runner() -> None:
    # Guards the seam signature: the fake must remain callable where a
    # ClipboardRunner is expected (and nothing here writes a real clipboard).
    runner = _assert_runner_fits_protocol(FakeClipboardRunner())
    result = runner((WL_COPY_BINARY,), b"", {})
    assert result.returncode == 0


def test_the_evdev_keycode_unit_has_no_xkb_offset() -> None:
    # KEY_LEFTCTRL is 29 (XKB 37) and KEY_V is 47 (XKB 55); an off-by-eight
    # mutation must fail here.
    assert (KEY_LEFTCTRL, KEY_V) == (29, 47)
    assert KeyChord.parse("ctrl+v").keycodes == (29, 47)
