"""Tests for the layout-aware text -> keycode mapping (ADR 0020, ADR 0019, ADR 0014).

Everything here compiles keymaps **from names** through libxkbcommon, so the
suite needs neither ``xkbcli`` nor a live session, and no ``ydotool`` or daemon
is touched. The mapping is pure data in / data out.

The units matter: :attr:`~ges2t.injectors.keymap.KeyEvent.keycode` is a Linux
input-event code (what ``ydotool key`` accepts), while the research notes in the
ticket quote XKB keycodes (``AD11``=34, ``BKSL``=51). The two differ by
:data:`~ges2t.injectors.keymap.LINUX_KEYCODE_OFFSET`.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import FrozenInstanceError

import pytest

from ges2t.injectors import keymap as keymap_module
from ges2t.injectors.keymap import (
    LINUX_KEYCODE_OFFSET,
    KeyEvent,
    KeySequence,
    XkbKeymap,
    XkbKeymapClosedError,
    XkbKeymapCompileError,
    XkbLibraryNotFoundError,
)

#: Linux input-event codes (``KEY_*``) used below.
LINUX_KEY_5 = 6
LINUX_KEY_A = 30
LINUX_KEY_B = 48
LINUX_KEY_LEFTCTRL = 29
LINUX_KEY_LEFTSHIFT = 42
LINUX_KEY_CAPS = 58
LINUX_KEY_RIGHTALT = 100

#: XKB keycodes from the research notes, for the direct Hungarian letters.
XKB_KEY_AD11 = 34
XKB_KEY_BKSL = 51


@pytest.fixture(scope="session")
def us_keymap() -> Iterator[XkbKeymap]:
    keymap = XkbKeymap.from_names("us")
    try:
        yield keymap
    finally:
        keymap.close()


@pytest.fixture(scope="session")
def hu_keymap() -> Iterator[XkbKeymap]:
    keymap = XkbKeymap.from_names("hu")
    try:
        yield keymap
    finally:
        keymap.close()


def codes(sequence: KeySequence) -> list[tuple[int, bool]]:
    """The ``(keycode, pressed)`` pairs of a sequence, for exact assertions."""
    return [(event.keycode, event.pressed) for event in sequence.events]


def only_char(sequence: KeySequence) -> int:
    """The Linux keycode of text that maps to exactly one unmodified key."""
    assert sequence.unreachable == ()
    assert len(sequence.events) == 2
    press, release = sequence.events
    assert press.pressed is True
    assert release.pressed is False
    assert press.keycode == release.keycode
    return press.keycode


# --- data classes (ADR 0019) --------------------------------------------------


def test_key_event_is_a_frozen_named_class() -> None:
    event = KeyEvent(keycode=LINUX_KEY_LEFTSHIFT, pressed=True)
    assert event.keycode == LINUX_KEY_LEFTSHIFT
    assert event.pressed is True
    with pytest.raises(FrozenInstanceError):
        event.keycode = 1  # type: ignore[misc]


def test_key_sequence_is_a_frozen_named_class(us_keymap: XkbKeymap) -> None:
    sequence = us_keymap.sequence_for("a")
    assert isinstance(sequence, KeySequence)
    with pytest.raises(FrozenInstanceError):
        sequence.unreachable = ()  # type: ignore[misc]


# --- the genericity test (the whole point of ADR 0020) -----------------------


def test_same_character_resolves_differently_on_hu_and_us(
    us_keymap: XkbKeymap, hu_keymap: XkbKeymap
) -> None:
    """GENERICITY: the mapping is derived from the keymap, not hardcoded.

    A hardcoded US (or any per-layout) table would give the same answer for
    both layouts, so this assertion is what would fail if someone reintroduced
    one. ``0`` is the ticket's confirmed corruption: on ``hu`` the US ``AE10``
    position produces ``ö``, and the correct ``0`` lives on ``TLDE``.
    """
    us_zero = only_char(us_keymap.sequence_for("0"))
    hu_zero = only_char(hu_keymap.sequence_for("0"))

    assert us_zero != hu_zero
    assert us_zero == 11  # XKB AE10 (19) - offset
    assert hu_zero == 41  # XKB TLDE (49) - offset


def test_hu_zero_does_not_use_the_key_that_produces_o_umlaut(
    hu_keymap: XkbKeymap,
) -> None:
    """The confirmed ``0`` -> ``ö`` corruption is fixed by construction."""
    zero = only_char(hu_keymap.sequence_for("0"))
    o_umlaut = only_char(hu_keymap.sequence_for("ö"))

    # On hu, XKB AE10 (19) produces 'ö'; ydotool used to send that for '0'.
    assert o_umlaut == 11
    assert zero != o_umlaut
    assert zero == 41


# --- Hungarian specifics ------------------------------------------------------


@pytest.mark.parametrize(
    ("character", "xkb_keycode"),
    [("ő", XKB_KEY_AD11), ("ű", XKB_KEY_BKSL)],
)
def test_hu_double_acute_letters_are_direct_and_unmodified(
    hu_keymap: XkbKeymap, character: str, xkb_keycode: int
) -> None:
    sequence = hu_keymap.sequence_for(character)
    linux_keycode = xkb_keycode - LINUX_KEYCODE_OFFSET

    assert sequence.unreachable == ()
    assert codes(sequence) == [
        (linux_keycode, True),
        (linux_keycode, False),
    ]
    # The research quotes XKB keycodes (AD11=34, BKSL=51); the events carry the
    # Linux codes (26, 43), because that is what ydotool key consumes.
    assert xkb_keycode == linux_keycode + LINUX_KEYCODE_OFFSET


def test_hu_level3_character_uses_right_alt(hu_keymap: XkbKeymap) -> None:
    sequence = hu_keymap.sequence_for("„")
    linux_keycode = 32 - LINUX_KEYCODE_OFFSET  # XKB AD09

    assert sequence.unreachable == ()
    assert LINUX_KEY_RIGHTALT in [event.keycode for event in sequence.events]
    assert codes(sequence) == [
        (LINUX_KEY_RIGHTALT, True),
        (linux_keycode, True),
        (linux_keycode, False),
        (LINUX_KEY_RIGHTALT, False),
    ]


# --- modifier ordering --------------------------------------------------------


def test_shifted_character_presses_shift_before_and_releases_after(
    us_keymap: XkbKeymap,
) -> None:
    sequence = us_keymap.sequence_for("A")
    assert codes(sequence) == [
        (LINUX_KEY_LEFTSHIFT, True),
        (LINUX_KEY_A, True),
        (LINUX_KEY_A, False),
        (LINUX_KEY_LEFTSHIFT, False),
    ]


def test_two_modifiers_are_released_in_reverse_order(hu_keymap: XkbKeymap) -> None:
    # '°' on hu is Shift+Mod5 (AltGr) on XKB AE05; xkbcli confirms
    # "[ Shift Mod5 LevelThree ]".
    sequence = hu_keymap.sequence_for("°")
    linux_keycode = 14 - LINUX_KEYCODE_OFFSET  # XKB AE05, Linux KEY_5

    assert codes(sequence) == [
        (LINUX_KEY_LEFTSHIFT, True),
        (LINUX_KEY_RIGHTALT, True),
        (linux_keycode, True),
        (linux_keycode, False),
        (LINUX_KEY_RIGHTALT, False),
        (LINUX_KEY_LEFTSHIFT, False),
    ]


def test_hu_double_acute_is_not_mistaken_for_a_shifted_key(
    hu_keymap: XkbKeymap,
) -> None:
    # On hu these letters are reachable with no modifier *and* with
    # Shift+Lock; the module must pick the modifier-free level (and never Lock).
    sequence = hu_keymap.sequence_for("y")
    assert len(sequence.events) == 2
    assert [event.keycode for event in sequence.events] == [44, 44]


# --- unreachable characters ---------------------------------------------------


def test_unreachable_character_is_reported_and_emits_nothing(
    us_keymap: XkbKeymap,
) -> None:
    sequence = us_keymap.sequence_for("😀")
    assert sequence.unreachable == ("😀",)
    assert sequence.events == ()


def test_unreachable_characters_are_deduplicated_in_first_seen_order(
    us_keymap: XkbKeymap,
) -> None:
    sequence = us_keymap.sequence_for("a😀b🎉a😀")
    assert sequence.unreachable == ("😀", "🎉")
    # The reachable characters still produce their events, in order.
    assert [keycode for keycode, pressed in codes(sequence) if pressed] == [
        LINUX_KEY_A,
        LINUX_KEY_B,
        LINUX_KEY_A,
    ]


def test_empty_text_produces_an_empty_sequence(us_keymap: XkbKeymap) -> None:
    assert us_keymap.sequence_for("") == KeySequence(events=(), unreachable=())


# --- ASCII sanity and layout-option handling ---------------------------------


def test_every_printable_ascii_character_resolves_on_us(us_keymap: XkbKeymap) -> None:
    printable = "".join(chr(codepoint) for codepoint in range(0x20, 0x7F))
    sequence = us_keymap.sequence_for(printable)
    assert sequence.unreachable == ()
    assert sequence.events


def test_caps_remap_option_never_emits_the_caps_key() -> None:
    # The live hu keymap remaps Caps (caps:ctrl_modifier). Choosing the mask
    # without Lock keeps that option from leaking into the emitted events.
    with XkbKeymap.from_names("hu", options="caps:ctrl_modifier") as keymap:
        assert only_char(keymap.sequence_for("y")) == 44
        assert only_char(keymap.sequence_for("0")) == 41
        sequence = keymap.sequence_for("Ay")
        assert LINUX_KEY_CAPS not in [event.keycode for event in sequence.events]
        assert LINUX_KEY_LEFTSHIFT in [event.keycode for event in sequence.events]


# --- both entry points and the error paths ------------------------------------


def test_keymap_can_be_reloaded_from_its_serialized_string(
    us_keymap: XkbKeymap,
) -> None:
    reloaded = XkbKeymap.from_string(us_keymap.to_string())
    try:
        assert reloaded.sequence_for("Hello, world! 0") == us_keymap.sequence_for(
            "Hello, world! 0"
        )
    finally:
        reloaded.close()


def test_invalid_keymap_string_raises_compile_error() -> None:
    with pytest.raises(XkbKeymapCompileError):
        XkbKeymap.from_string("this is definitely not an xkb keymap")


def test_unknown_layout_raises_compile_error() -> None:
    with pytest.raises(XkbKeymapCompileError):
        XkbKeymap.from_names("definitely-not-a-real-layout-xyz")


def test_using_a_closed_keymap_raises_and_close_is_idempotent() -> None:
    keymap = XkbKeymap.from_names("us")
    keymap.close()
    with pytest.raises(XkbKeymapClosedError):
        keymap.sequence_for("a")
    keymap.close()  # idempotent
    with pytest.raises(XkbKeymapClosedError):
        keymap.to_string()


class _BrokenCtypes:
    """Stand-in for the ``ctypes`` module when libxkbcommon cannot be loaded."""

    class util:  # noqa: N801 - mirrors ctypes.util
        @staticmethod
        def find_library(name: str) -> None:
            return None

    @staticmethod
    def CDLL(name: str) -> object:
        raise OSError(f"cannot load {name}")


def test_missing_library_raises_an_actionable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(keymap_module, "ctypes", _BrokenCtypes)
    with pytest.raises(XkbLibraryNotFoundError) as excinfo:
        keymap_module._load_library()
    message = str(excinfo.value)
    assert "libxkbcommon" in message
    assert "pacman" in message


def test_linux_keycode_offset_is_the_xkb_to_evdev_delta() -> None:
    # The offset is the whole reason KeyEvent.keycode is ydotool-compatible;
    # pin it so a reader cannot miss which unit the events carry.
    assert LINUX_KEYCODE_OFFSET == 8
    assert LINUX_KEY_RIGHTALT == 108 - LINUX_KEYCODE_OFFSET
