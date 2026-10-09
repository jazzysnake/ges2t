"""Layout-aware text -> keycode mapping, computed from a live XKB keymap (ADR 0020).

``ydotool type`` resolves text with a hardcoded US-position table and drops
non-ASCII, so it is unusable on the user's ``hu`` layout (``0`` -> ``ö``,
``-`` -> ``ü``, ``y`` -> ``z``; ADR 0003's 2026-10-09 amendment). This module
replaces that resolution with a **reverse lookup against the actual keymap**:
for every keycode, layout group and shift level it asks libxkbcommon which
keysym is produced, converts that keysym to a Unicode codepoint, and records the
keycode plus the *minimal* modifier mask that reaches it. Nothing here knows
about any particular layout -- the mapping is derived entirely from the keymap,
so any XKB layout works (the genericity test in ``tests/test_keymap.py`` pins
that down).

This module is pure mapping: it emits nothing and talks to no daemon. Ticket 23
takes the :class:`KeySequence` this produces and sends it through ``ydotool key``.

Keycode units (read this before wiring the emitter)
---------------------------------------------------
:attr:`KeyEvent.keycode` is a **Linux input-event code** (``/usr/include/linux/
input-event-codes.h``, i.e. evdev), because that is what ``ydotool key`` accepts
and what the downstream tickets name: ``KEY_RIGHTALT`` is ``100`` and the paste
chord is ``KEY_LEFTCTRL`` 29 + ``KEY_V`` 47 (see ``clipboard-paste-injector``).
libxkbcommon, however, speaks **XKB keycodes**, which are the Linux code plus
:data:`LINUX_KEYCODE_OFFSET` (``8``). This module converts once, at the boundary,
so callers never see an XKB keycode. The ticket's research notes (``AD11``=34,
``BKSL``=51) are XKB keycodes; the Linux codes this module emits for them are
26 and 43. ``KEY_RIGHTALT`` is XKB keycode 108 and Linux keycode 100 -- the
ticket's ``100`` is therefore consistent with this choice, not with XKB codes.

How a character is resolved
---------------------------
* Iterate ``min_keycode..max_keycode`` x layout group x shift level, read the
  keysyms with ``xkb_keymap_key_get_syms_by_level``, and compare their
  ``xkb_keysym_to_utf32`` codepoints with the target.
* For each match ask ``xkb_keymap_key_get_mods_for_level`` for the modifier
  masks that reach that level, and take the **minimal mask without ``Lock``**
  (fewest bits, then lowest value). Excluding ``Lock`` matters because the
  ``hu`` keymap remaps Caps (``caps:ctrl_modifier``) and Caps state changes the
  base level; pressing a lock is also never something this module should do.
* Map each set mask bit to a physical key via ``xkb_keymap_mod_get_name`` and
  the standard XKB *key names* (``Mod5`` -> ``RALT``), then
  ``xkb_keymap_key_by_name``; verify with a fresh :c:`xkb_state` that the chosen
  key really depresses that modifier and fall back to scanning the keymap if it
  does not. Keysyms with no Unicode value (dead keys, function keys) are
  ignored, and compose/dead-key *sequences* are deliberately not looked up in
  this ticket: only direct access is implemented. Dead keys are still emitted
  as ordinary key events (the application composes them).
* Among several keys/levels that produce the same codepoint, the lowest
  ``(modifier count, mask, level, layout, keycode)`` wins, which keeps ``0`` on
  the main row rather than the keypad.

Characters absent from the keymap are collected in :attr:`KeySequence.unreachable`
(deduplicated, in first-seen order) instead of being dropped or raising: ADR
0020's skip-and-warn rule. Multi-codepoint or zero-codepoint keysyms are treated
as unreachable rather than guessed.

Binding and memory
------------------
The binding is **ctypes** to ``libxkbcommon.so.0`` -- no new dependency, per
ADR 0020. The library is loaded lazily on first use and raises
:class:`XkbLibraryNotFoundError` with an actionable message if it is absent.
Opaque ``libxkbcommon`` pointers are declared ``c_void_p`` (never as integers
truncated to ``int``) and every owned handle is released: the keymap and context
are unref'd in :meth:`XkbKeymap.close`, and every temporary state is unref'd
inside the call that created it, so a long-running service does not leak.

A keymap is immutable once compiled, so the reverse map is cached **per
instance**. It is never cached globally: the active layout can change between
utterances, so ticket 24/23 must build a fresh :class:`XkbKeymap` per utterance
rather than reuse a stale one (ADR 0020).
"""

from __future__ import annotations

import ctypes
import ctypes.util
from dataclasses import dataclass
from typing import Final

__all__ = [
    "KeyEvent",
    "KeySequence",
    "LINUX_KEYCODE_OFFSET",
    "XkbError",
    "XkbKeymap",
    "XkbKeymapClosedError",
    "XkbKeymapCompileError",
    "XkbLibraryNotFoundError",
]

#: ``xkb_keycode_t`` = Linux/evdev input-event code + this offset. The reverse
#: lookup is done in XKB keycodes (what libxkbcommon reports); :class:`KeyEvent`
#: carries Linux keycodes (what ``ydotool key`` accepts), so this is subtracted
#: once when a resolution is recorded.
LINUX_KEYCODE_OFFSET: Final[int] = 8

#: ``enum xkb_context_flags`` / ``enum xkb_keymap_compile_flags``: no flags.
_XKB_NO_FLAGS: Final[int] = 0
#: ``enum xkb_keymap_format``: the format ``xkbcli dump-keymap-wayland`` emits.
_XKB_KEYMAP_FORMAT_TEXT_V1: Final[int] = 1
#: ``XKB_KEYMAP_USE_ORIGINAL_FORMAT``: serialize a keymap in its source format.
_XKB_KEYMAP_USE_ORIGINAL_FORMAT: Final[int] = -1
#: ``enum xkb_key_direction``.
_XKB_KEY_DOWN: Final[int] = 1
#: ``enum xkb_state_component``: the mods currently held down.
_XKB_STATE_MODS_DEPRESSED: Final[int] = 1 << 0
#: ``XKB_MOD_INVALID``: ``xkb_keymap_mod_get_index`` returns this for a name the
#: keymap does not define.
_XKB_MOD_INVALID: Final[int] = 0xFFFFFFFF
#: ``XKB_KEYCODE_INVALID``: ``xkb_keymap_key_by_name`` returns this on no match.
_XKB_KEYCODE_INVALID: Final[int] = 0xFFFFFFFF
#: Upper bound on modifier masks ``xkb_keymap_key_get_mods_for_level`` may
#: return for one level; it grows the buffer if this is ever saturated.
_MOD_MASK_BUFFER_START: Final[int] = 16
_MOD_MASK_BUFFER_LIMIT: Final[int] = 256

#: Absolute candidates for the shared object, tried in order. ``.so.0`` is the
#: stable soname and the one KWin links against; ``.so`` covers dev installs.
_LIBRARY_SONAMES: Final[tuple[str, ...]] = ("libxkbcommon.so.0", "libxkbcommon.so")

#: Standard XKB **key names** (physical positions, not layout symbols) for the
#: canonical modifier that activates each XKB modifier name. This is *not* a
#: layout table: ``LFSH``/``RALT``/... exist in every pc-derived keycodes
#: section, and the mapping is verified against the keymap before use, with a
#: full scan as fallback. libxkbcommon exposes no public "modifier -> key" API,
#: so the name is the only portable handle we have.
_PREFERRED_MODIFIER_KEY_NAMES: Final[dict[str, str]] = {
    "Shift": "LFSH",
    "Lock": "CAPS",
    "Control": "LCTL",
    "Mod1": "LALT",
    "Mod2": "NMLK",
    "Mod4": "LWIN",
    "Mod5": "RALT",
}


class XkbError(Exception):
    """Base class for every failure raised by this module."""


class XkbLibraryNotFoundError(XkbError):
    """``libxkbcommon.so.0`` could not be loaded (ADR 0020)."""


class XkbKeymapCompileError(XkbError):
    """The keymap string or RMLVO names did not compile to a keymap."""


class XkbKeymapClosedError(XkbError):
    """The keymap was used after :meth:`XkbKeymap.close` released it."""


@dataclass(frozen=True)
class KeyEvent:
    """One press or release of one key.

    ``keycode`` is a **Linux input-event code** (``ydotool key``'s unit), and
    ``pressed`` is ``True`` for a press and ``False`` for a release. Frozen, and
    a class rather than a ``(keycode, pressed)`` tuple, per ADR 0019.
    """

    keycode: int
    pressed: bool


@dataclass(frozen=True)
class KeySequence:
    """The events that type some text, plus what the keymap cannot type.

    ``events`` is fully expanded and ordered: for each character the required
    modifiers are pressed first, then the key is pressed and released, then the
    modifiers are released in **reverse** order. ``unreachable`` holds the
    distinct characters the keymap could not produce, in first-seen order, so
    the caller can warn instead of emitting garbage (ADR 0020). A frozen class
    rather than a tuple, per ADR 0019.
    """

    events: tuple[KeyEvent, ...]
    unreachable: tuple[str, ...]


class _RuleNames(ctypes.Structure):
    """The ``struct xkb_rule_names`` RMLVO argument (all NUL-terminated strings)."""

    _fields_ = [
        ("rules", ctypes.c_char_p),
        ("model", ctypes.c_char_p),
        ("layout", ctypes.c_char_p),
        ("variant", ctypes.c_char_p),
        ("options", ctypes.c_char_p),
    ]


@dataclass(frozen=True)
class _Resolution:
    """The winning keycode+modifiers for one codepoint, with its selection rank."""

    keycode: int
    modifier_keycodes: tuple[int, ...]
    rank: tuple[int, int, int, int, int]


_library: ctypes.CDLL | None = None


def _load_library() -> ctypes.CDLL:
    """Load ``libxkbcommon`` by soname, or raise an actionable error.

    Deliberately separate from :func:`_libxkbcommon` so the failure path is
    directly testable without a real missing library.
    """
    errors: list[str] = []
    for soname in _LIBRARY_SONAMES:
        try:
            return ctypes.CDLL(soname)
        except OSError as exc:  # pragma: no cover - exercised via the fake below
            errors.append(f"{soname}: {exc}")
    found = ctypes.util.find_library("xkbcommon")
    if found:
        try:
            return ctypes.CDLL(found)
        except OSError as exc:  # pragma: no cover - exercised via the fake below
            errors.append(f"{found}: {exc}")
    detail = "; ".join(errors) if errors else "no candidate soname resolved"
    raise XkbLibraryNotFoundError(
        "libxkbcommon.so.0 could not be loaded, so ges2t cannot map text to "
        "keycodes on this layout. It is part of any KDE/Wayland system because "
        "KWin itself uses it; install it with 'sudo pacman -S libxkbcommon' "
        "(Arch) or the equivalent for your distribution. Detail: " + detail
    )


def _bind(lib: ctypes.CDLL) -> None:
    """Declare the prototypes this module uses.

    Opaque struct pointers are ``c_void_p`` and every function that takes one has
    explicit ``argtypes``; omitting them lets ctypes truncate a 64-bit pointer to
    an ``int`` and segfault.
    """
    lib.xkb_context_new.restype = ctypes.c_void_p
    lib.xkb_context_new.argtypes = [ctypes.c_int]
    lib.xkb_context_unref.restype = None
    lib.xkb_context_unref.argtypes = [ctypes.c_void_p]

    lib.xkb_keymap_new_from_names.restype = ctypes.c_void_p
    lib.xkb_keymap_new_from_names.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_RuleNames),
        ctypes.c_int,
    ]
    lib.xkb_keymap_new_from_string.restype = ctypes.c_void_p
    lib.xkb_keymap_new_from_string.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_int,
    ]
    lib.xkb_keymap_unref.restype = None
    lib.xkb_keymap_unref.argtypes = [ctypes.c_void_p]
    lib.xkb_keymap_get_as_string.restype = ctypes.c_void_p
    lib.xkb_keymap_get_as_string.argtypes = [ctypes.c_void_p, ctypes.c_int]

    lib.xkb_keymap_min_keycode.restype = ctypes.c_uint32
    lib.xkb_keymap_min_keycode.argtypes = [ctypes.c_void_p]
    lib.xkb_keymap_max_keycode.restype = ctypes.c_uint32
    lib.xkb_keymap_max_keycode.argtypes = [ctypes.c_void_p]
    lib.xkb_keymap_num_mods.restype = ctypes.c_uint32
    lib.xkb_keymap_num_mods.argtypes = [ctypes.c_void_p]
    lib.xkb_keymap_num_layouts.restype = ctypes.c_uint32
    lib.xkb_keymap_num_layouts.argtypes = [ctypes.c_void_p]
    lib.xkb_keymap_num_levels_for_key.restype = ctypes.c_uint32
    lib.xkb_keymap_num_levels_for_key.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
    ]
    lib.xkb_keymap_key_get_syms_by_level.restype = ctypes.c_int
    lib.xkb_keymap_key_get_syms_by_level.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.POINTER(ctypes.c_uint32)),
    ]
    lib.xkb_keymap_key_get_mods_for_level.restype = ctypes.c_size_t
    lib.xkb_keymap_key_get_mods_for_level.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_size_t,
    ]
    lib.xkb_keymap_mod_get_name.restype = ctypes.c_char_p
    lib.xkb_keymap_mod_get_name.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    lib.xkb_keymap_mod_get_index.restype = ctypes.c_uint32
    lib.xkb_keymap_mod_get_index.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lib.xkb_keymap_key_by_name.restype = ctypes.c_uint32
    lib.xkb_keymap_key_by_name.argtypes = [ctypes.c_void_p, ctypes.c_char_p]

    lib.xkb_keysym_to_utf32.restype = ctypes.c_uint32
    lib.xkb_keysym_to_utf32.argtypes = [ctypes.c_uint32]

    lib.xkb_state_new.restype = ctypes.c_void_p
    lib.xkb_state_new.argtypes = [ctypes.c_void_p]
    lib.xkb_state_unref.restype = None
    lib.xkb_state_unref.argtypes = [ctypes.c_void_p]
    lib.xkb_state_update_key.restype = ctypes.c_int
    lib.xkb_state_update_key.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int]
    lib.xkb_state_serialize_mods.restype = ctypes.c_uint32
    lib.xkb_state_serialize_mods.argtypes = [ctypes.c_void_p, ctypes.c_int]


def _libxkbcommon() -> ctypes.CDLL:
    """Return the lazily-loaded, prototype-bound ``libxkbcommon`` handle."""
    global _library
    if _library is None:
        lib = _load_library()
        _bind(lib)
        _library = lib
    return _library


def _libc_free(pointer: int) -> None:
    """Free a string libxkbcommon allocated with ``malloc`` (its documented contract)."""
    libc = ctypes.CDLL(None)
    libc.free.restype = None
    libc.free.argtypes = [ctypes.c_void_p]
    libc.free(ctypes.c_void_p(pointer))


class XkbKeymap:
    """A compiled XKB keymap with a text -> key-event reverse lookup (ADR 0020).

    Build one with :meth:`from_names` (tests, RMLVO names) or
    :meth:`from_string` (production: the composer's live keymap). A single
    instance reflects the keymap it was compiled from; build a **new** instance
    per utterance if the active layout can change between utterances.

    Use it as a context manager or call :meth:`close` to release the underlying
    ``libxkbcommon`` handles.
    """

    def __init__(self, context: int, keymap: int) -> None:
        self._context = context
        self._keymap = keymap
        self._lib = _libxkbcommon()
        self._resolutions: dict[int, _Resolution] | None = None
        self._mask_keycodes: dict[int, tuple[int, ...] | None] = {}
        self._lock_index: int | None = self._mod_index("Lock")
        self._num_mods: int = 0
        self._min_keycode: int = 0
        self._max_keycode: int = 0
        self._num_layouts: int = 0
        self._read_geometry()

    @classmethod
    def from_string(cls, keymap: str) -> XkbKeymap:
        """Compile a keymap from a complete XKB keymap string.

        This is the production entry point (ADR 0020): ticket 24 passes the live
        compositor keymap from ``xkbcli dump-keymap-wayland``, so options like
        ``caps:ctrl_modifier`` are honoured rather than guessed.
        """
        lib = _libxkbcommon()
        context = lib.xkb_context_new(_XKB_NO_FLAGS)
        if not context:
            raise XkbError("libxkbcommon could not allocate an xkb_context.")
        handle = lib.xkb_keymap_new_from_string(
            context,
            keymap.encode("utf-8"),
            _XKB_KEYMAP_FORMAT_TEXT_V1,
            _XKB_NO_FLAGS,
        )
        if not handle:
            lib.xkb_context_unref(context)
            raise XkbKeymapCompileError(
                "libxkbcommon could not compile the supplied keymap string. "
                "Pass a complete XKB keymap (as emitted by "
                "'xkbcli dump-keymap-wayland'), not a fragment."
            )
        return cls(context, handle)

    @classmethod
    def from_names(
        cls,
        layout: str,
        variant: str = "",
        *,
        rules: str | None = None,
        model: str | None = None,
        options: str | None = None,
    ) -> XkbKeymap:
        """Compile a keymap from RMLVO names (``xkb_keymap_new_from_names``).

        The tests use this so they need no ``xkbcli`` and no live session. When
        an RMLVO field is ``None`` libxkbcommon falls back to its default (or the
        matching ``XKB_DEFAULT_*`` environment variable).
        """
        lib = _libxkbcommon()
        context = lib.xkb_context_new(_XKB_NO_FLAGS)
        if not context:
            raise XkbError("libxkbcommon could not allocate an xkb_context.")
        names = _RuleNames(
            rules=rules.encode("utf-8") if rules is not None else None,
            model=model.encode("utf-8") if model is not None else None,
            layout=layout.encode("utf-8"),
            variant=variant.encode("utf-8"),
            options=options.encode("utf-8") if options is not None else None,
        )
        handle = lib.xkb_keymap_new_from_names(
            context, ctypes.byref(names), _XKB_NO_FLAGS
        )
        if not handle:
            lib.xkb_context_unref(context)
            raise XkbKeymapCompileError(
                f"libxkbcommon could not compile the RMLVO names "
                f"layout={layout!r} variant={variant!r} "
                f"options={options!r}. Check the layout name and that the XKB "
                "data files (xkeyboard-config) are installed."
            )
        return cls(context, handle)

    # -- lifecycle -----------------------------------------------------------

    def close(self) -> None:
        """Release the keymap and context. Idempotent; safe to call twice."""
        keymap, self._keymap = self._keymap, 0
        context, self._context = self._context, 0
        self._resolutions = None
        self._mask_keycodes = {}
        if keymap:
            self._lib.xkb_keymap_unref(keymap)
        if context:
            self._lib.xkb_context_unref(context)

    def __enter__(self) -> XkbKeymap:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def __del__(self) -> None:
        # Never raise from a finalizer (it may run during interpreter teardown).
        try:
            self.close()
        except Exception:  # noqa: BLE001 - a finalizer must swallow everything
            pass

    # -- public mapping API --------------------------------------------------

    def sequence_for(self, text: str) -> KeySequence:
        """Map ``text`` to the key events that type it on this keymap.

        Modifiers are pressed before the key and released after, in reverse
        order. Characters with no direct keymap access are collected in
        :attr:`KeySequence.unreachable` -- never dropped, never raised (ADR
        0020). Raises :class:`XkbKeymapClosedError` if the keymap was closed.
        """
        if not self._keymap:
            raise XkbKeymapClosedError(
                "This XkbKeymap was closed; compile a new one for the current "
                "layout before mapping text (ADR 0020 maps per utterance)."
            )
        resolutions = self._resolution_map()
        events: list[KeyEvent] = []
        unreachable: list[str] = []
        seen_unreachable: set[str] = set()
        for char in text:
            resolution = resolutions.get(ord(char))
            if resolution is None:
                if char not in seen_unreachable:
                    seen_unreachable.add(char)
                    unreachable.append(char)
                continue
            for modifier in resolution.modifier_keycodes:
                events.append(KeyEvent(keycode=modifier, pressed=True))
            events.append(KeyEvent(keycode=resolution.keycode, pressed=True))
            events.append(KeyEvent(keycode=resolution.keycode, pressed=False))
            for modifier in reversed(resolution.modifier_keycodes):
                events.append(KeyEvent(keycode=modifier, pressed=False))
        return KeySequence(events=tuple(events), unreachable=tuple(unreachable))

    # -- geometry ------------------------------------------------------------

    def to_string(self) -> str:
        """Serialize this keymap back to XKB text (round-trip and debugging).

        The returned string can be handed back to :meth:`from_string`, which is
        what makes the ``from_string`` entry point testable without ``xkbcli``.
        """
        if not self._keymap:
            raise XkbKeymapClosedError(
                "This XkbKeymap was closed; compile a new one before serializing."
            )
        pointer = self._lib.xkb_keymap_get_as_string(
            self._keymap, _XKB_KEYMAP_USE_ORIGINAL_FORMAT
        )
        if not pointer:
            raise XkbError("libxkbcommon could not serialize the keymap.")
        try:
            return ctypes.string_at(pointer).decode("utf-8", errors="replace")
        finally:
            _libc_free(int(pointer))

    def _read_geometry(self) -> None:
        lib = self._lib
        self._min_keycode = int(lib.xkb_keymap_min_keycode(self._keymap))
        self._max_keycode = int(lib.xkb_keymap_max_keycode(self._keymap))
        self._num_layouts = int(lib.xkb_keymap_num_layouts(self._keymap))
        self._num_mods = int(lib.xkb_keymap_num_mods(self._keymap))

    def _mod_index(self, name: str) -> int | None:
        index = int(self._lib.xkb_keymap_mod_get_index(self._keymap, name.encode()))
        if index == _XKB_MOD_INVALID:
            return None
        return index

    def _mod_name(self, index: int) -> str | None:
        raw = self._lib.xkb_keymap_mod_get_name(self._keymap, index)
        if raw is None:
            return None
        return raw.decode("utf-8", errors="replace")

    # -- low-level libxkbcommon helpers --------------------------------------

    def _syms_for_level(self, key: int, layout: int, level: int) -> tuple[int, ...]:
        syms = ctypes.POINTER(ctypes.c_uint32)()
        count = int(
            self._lib.xkb_keymap_key_get_syms_by_level(
                self._keymap, key, layout, level, ctypes.byref(syms)
            )
        )
        if count <= 0 or not syms:
            return ()
        return tuple(int(syms[i]) for i in range(count))

    def _mods_for_level(self, key: int, layout: int, level: int) -> tuple[int, ...]:
        size = _MOD_MASK_BUFFER_START
        while True:
            buffer = (ctypes.c_uint32 * size)()
            count = int(
                self._lib.xkb_keymap_key_get_mods_for_level(
                    self._keymap, key, layout, level, buffer, size
                )
            )
            if count < size or size >= _MOD_MASK_BUFFER_LIMIT:
                return tuple(int(buffer[i]) for i in range(count))
            size *= 2

    def _choose_mask(self, masks: tuple[int, ...]) -> int | None:
        """The minimal mask that reaches a level, never using ``Lock``.

        Returns ``None`` if every mask needs ``Lock``: the module must not press
        Caps, so such a level is treated as inaccessible (ADR 0020).
        """
        lock_bit = 1 << self._lock_index if self._lock_index is not None else 0
        candidates = [mask for mask in masks if not (mask & lock_bit)]
        if not candidates:
            return None
        return min(candidates, key=lambda mask: (mask.bit_count(), mask))

    def _state_depressed(self, keys: tuple[int, ...]) -> int:
        """Depress ``keys`` on a fresh state and return the resulting mod mask."""
        lib = self._lib
        state = lib.xkb_state_new(self._keymap)
        if not state:
            return 0
        try:
            for key in keys:
                lib.xkb_state_update_key(state, key, _XKB_KEY_DOWN)
            return int(
                lib.xkb_state_serialize_mods(state, _XKB_STATE_MODS_DEPRESSED)
            )
        finally:
            lib.xkb_state_unref(state)

    def _key_produces_mod(self, key: int, mod_index: int) -> bool:
        return bool(self._state_depressed((key,)) & (1 << mod_index))

    def _scan_key_for_mod(self, mod_index: int) -> int | None:
        """Lowest keycode that depresses ``mod_index`` (exact match preferred)."""
        exact: int | None = None
        partial: int | None = None
        for key in range(self._min_keycode, self._max_keycode + 1):
            depressed = self._state_depressed((key,))
            if not depressed & (1 << mod_index):
                continue
            if depressed == 1 << mod_index:
                return key
            if partial is None:
                partial = key
        return exact if exact is not None else partial

    def _physical_key_for_mod(self, name: str, mod_index: int) -> int | None:
        """The physical XKB keycode that activates modifier ``name``/``index``.

        Prefers the standard XKB key name for the modifier, verifies it with a
        real state, and falls back to scanning the keymap otherwise.
        """
        preferred = _PREFERRED_MODIFIER_KEY_NAMES.get(name)
        if preferred is not None:
            key = int(
                self._lib.xkb_keymap_key_by_name(self._keymap, preferred.encode())
            )
            if key != _XKB_KEYCODE_INVALID and self._key_produces_mod(key, mod_index):
                return key
        return self._scan_key_for_mod(mod_index)

    def _modifier_keycodes(self, mask: int) -> tuple[int, ...] | None:
        """Physical Linux keycodes for the set bits of ``mask``, or ``None``.

        Cached per mask because levels share the handful of real masks. Bits are
        visited in ascending modifier-index order (so ``Shift`` before ``Mod5``);
        the final mask is verified against a real state before being trusted.
        """
        cached = self._mask_keycodes.get(mask)
        if mask in self._mask_keycodes:
            return cached
        result = self._resolve_modifier_keycodes(mask)
        self._mask_keycodes[mask] = result
        return result

    def _resolve_modifier_keycodes(self, mask: int) -> tuple[int, ...] | None:
        keys: list[int] = []
        seen: set[int] = set()
        for mod_index in range(self._num_mods):
            if not mask & (1 << mod_index):
                continue
            name = self._mod_name(mod_index)
            if name is None:
                return None
            key = self._physical_key_for_mod(name, mod_index)
            if key is None:
                return None
            if key not in seen:
                seen.add(key)
                keys.append(key)
        if not keys:
            return ()
        combined = self._state_depressed(tuple(keys))
        if combined & mask != mask:
            return None
        return tuple(key - LINUX_KEYCODE_OFFSET for key in keys)

    # -- reverse-map construction -------------------------------------------

    def _resolution_map(self) -> dict[int, _Resolution]:
        if self._resolutions is None:
            self._resolutions = self._build_resolutions()
        return self._resolutions

    def _build_resolutions(self) -> dict[int, _Resolution]:
        best: dict[int, _Resolution] = {}
        for layout in range(self._num_layouts):
            for key in range(self._min_keycode, self._max_keycode + 1):
                levels = int(
                    self._lib.xkb_keymap_num_levels_for_key(
                        self._keymap, key, layout
                    )
                )
                for level in range(levels):
                    syms = self._syms_for_level(key, layout, level)
                    if not syms:
                        continue
                    mask = self._choose_mask(
                        self._mods_for_level(key, layout, level)
                    )
                    if mask is None:
                        continue
                    modifiers = self._modifier_keycodes(mask)
                    if modifiers is None:
                        continue
                    keycode = key - LINUX_KEYCODE_OFFSET
                    if keycode <= 0:
                        continue
                    rank = (mask.bit_count(), mask, level, layout, keycode)
                    resolution = _Resolution(
                        keycode=keycode,
                        modifier_keycodes=modifiers,
                        rank=rank,
                    )
                    for sym in syms:
                        codepoint = int(self._lib.xkb_keysym_to_utf32(sym))
                        if codepoint == 0:
                            continue
                        existing = best.get(codepoint)
                        if existing is None or rank < existing.rank:
                            best[codepoint] = resolution
        return best
