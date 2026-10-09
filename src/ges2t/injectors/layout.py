"""Layout detection and injector configuration for the injection backends.

This module answers "which keymap is the user actually on" (ADR 0020) and owns
the injector-specific configuration blocks the batched emitter and the clipboard
injector consume. Both concerns live in the injectors package because they are
settings and capabilities of an injector, and the ticket that owns them owns the
whole `layout-detection-and-config` scope; this is the module named by its
definition of done.

Live keymap and active layout
-----------------------------
The ground-truth keymap comes from the compositor itself::

    xkbcli dump-keymap-wayland

returns the *live* keymap **including options** -- on the reference machine
``pc_hu_inet(evdev)_capslock(ctrl_modifier)``, which carries the user's
``caps:ctrl_modifier`` KDE option. Compiling that string means the computed
mapping cannot drift from what KWin will do, because it is literally KWin's
keymap.

The active layout group is reported over D-Bus by ``kwin_wayland`` under
``org.kde.keyboard``, object ``/Layouts``, interface ``org.kde.KeyboardLayouts``.
Both facts were verified against the live session (ADR 0020), and both tools are
plain subprocesses -- ``xkbcli`` from libxkbcommon and ``busctl`` from systemd --
so **no D-Bus client dependency is added** (ADR 0001's stdlib-first posture).

Resolution order
----------------
:func:`resolve_layout` implements the order explicitly and never guesses ``us``:

1. an **explicit configured** ``layout`` (anything other than ``"auto"``) wins
   outright; ``variant`` travels with it and nothing is probed;
2. otherwise the **live keymap** from the compositor, used verbatim;
3. otherwise the detected **layout/variant names** from KDE's layout service;
4. otherwise :class:`LayoutResolutionError`, naming each thing that could not be
   read and how to fix it.

``layout = "auto"`` means "use the live keymap", never "guess from ``$LANG`` or
``localectl``".

Injector block configuration
----------------------------
The generic layer (:mod:`ges2t.config`) passes every ``[injection.<name>]`` table
through **uninterpreted**; this module is the selected backend's half of that
contract, exactly as :mod:`ges2t.transcribers.whistle_config` is for the whistle
engine. :class:`YdotoolConfig` validates ``[injection.ydotool]`` and
:class:`ClipboardConfig` validates ``[injection.clipboard]``; both follow
``whistle_config.py``'s shape -- a frozen dataclass, a ``from_mapping`` class
method returning the config plus a list of unknown-key warnings, and a hard
error only for a wrong-typed value. The generic layer preserves blocks for the
backend that is not selected, so switching backends needs no edit.

Nothing here touches a real session: both detectors take an injected ``runner``
whose default is the only place ``subprocess`` is called.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

__all__ = [
    "AUTO_LAYOUT",
    "BUSCTL_BINARY",
    "CLIPBOARD_BLOCK_NAME",
    "DEFAULT_KEY_DELAY_MS",
    "DEFAULT_PASTE_KEYS",
    "DUMP_KEYMAP_ARGUMENTS",
    "KEYBOARD_INTERFACE",
    "KEYBOARD_OBJECT_PATH",
    "KEYBOARD_SERVICE",
    "LAYOUT_COMMAND_TIMEOUT_SECONDS",
    "XKBCLI_BINARY",
    "YDOTOOL_BLOCK_NAME",
    "ClipboardConfig",
    "ClipboardConfigResult",
    "InjectionConfigError",
    "LayoutCommandRunner",
    "LayoutEntry",
    "LayoutInfo",
    "LayoutParseError",
    "LayoutResolutionError",
    "LayoutSource",
    "ResolvedLayout",
    "YdotoolConfig",
    "YdotoolConfigResult",
    "detect_layout",
    "live_keymap_text",
    "parse_active_layout_index",
    "parse_layouts_list",
    "resolve_layout",
]

# --- external commands --------------------------------------------------------

#: Executable looked up on ``PATH`` that dumps the compositor's live keymap.
XKBCLI_BINARY: str = "xkbcli"

#: Arguments after ``xkbcli``. ``dump-keymap-wayland`` asks the compositor over
#: the Wayland connection and prints the keymap it is actually using.
DUMP_KEYMAP_ARGUMENTS: tuple[str, ...] = ("dump-keymap-wayland",)

#: Executable looked up on ``PATH`` that speaks D-Bus without a Python binding.
BUSCTL_BINARY: str = "busctl"

#: The KWin keyboard-layout service and its one relevant object/interface.
KEYBOARD_SERVICE: str = "org.kde.keyboard"
KEYBOARD_OBJECT_PATH: str = "/Layouts"
KEYBOARD_INTERFACE: str = "org.kde.KeyboardLayouts"

#: Upper bound on either external command. Both are local and return in a few
#: milliseconds; the bound exists so a wedged compositor or D-Bus daemon cannot
#: hang dictation. On expiry the call is treated as a failure and ``None`` is
#: returned, never raised.
LAYOUT_COMMAND_TIMEOUT_SECONDS: float = 2.0

#: The "use the live keymap" sentinel for ``[injection.ydotool] layout``.
AUTO_LAYOUT: str = "auto"

# Runner seam. It takes the argv and the timeout, and returns the captured
# CompletedProcess -- tiny enough that a test double is three lines and never
# starts a process or opens a D-Bus connection (ADR 0014).
LayoutCommandRunner = Callable[[Sequence[str], float], subprocess.CompletedProcess[bytes]]


def _run_command(
    argv: Sequence[str], timeout: float
) -> subprocess.CompletedProcess[bytes]:
    """The real :data:`LayoutCommandRunner`: run ``argv`` with a timeout and capture output.

    ``check=False`` because the caller classifies the exit status. A missing
    executable raises :class:`FileNotFoundError`, a wedged child raises
    :class:`subprocess.TimeoutExpired`; both are caught by the callers.
    """
    return subprocess.run(
        list(argv),
        capture_output=True,
        check=False,
        timeout=timeout,
    )


# --- errors -------------------------------------------------------------------


class LayoutParseError(ValueError):
    """``busctl`` printed something this parser does not understand."""


class LayoutResolutionError(Exception):
    """No layout could be determined from any source in the resolution order.

    The message names every source that was tried, because the failure the user
    must fix is a configuration or environment one, not a programming one.
    """


# --- layout data --------------------------------------------------------------


@dataclass(frozen=True)
class LayoutEntry:
    """One row of ``getLayoutsList()``: a layout, its variant, and its display name."""

    layout: str
    variant: str
    display_name: str


@dataclass(frozen=True)
class LayoutInfo:
    """The active layout, as reported by KDE's ``org.kde.KeyboardLayouts``.

    ``index`` is the active position in the full ``getLayoutsList()`` result;
    ``layout``/``variant``/``display_name`` are that entry's fields. The list
    itself is not kept because the mapping is recomputed per utterance
    (ADR 0020), so a stale cached list would only invite drift.
    """

    layout: str
    variant: str
    display_name: str
    index: int


class LayoutSource(StrEnum):
    """Which level of the resolution order produced a :class:`ResolvedLayout`."""

    CONFIGURED = "configured"
    LIVE_KEYMAP = "live keymap"
    DETECTED = "detected"


@dataclass(frozen=True)
class ResolvedLayout:
    """The chosen layout plus where it came from (ADR 0020).

    Exactly one payload is populated for a given ``source``:

    * :attr:`LayoutSource.CONFIGURED` -- ``layout`` and ``variant`` are the
      explicit configured strings; ``keymap_text`` is ``None``.
    * :attr:`LayoutSource.LIVE_KEYMAP` -- ``keymap_text`` is the compositor's
      live keymap and must be compiled; the name fields are ``None``.
    * :attr:`LayoutSource.DETECTED` -- ``layout``/``variant``/``display_name``
      name the active KDE layout; ``keymap_text`` is ``None`` and the caller
      builds a keymap from the names.

    ``source`` is an enum rather than a string so the emitter can branch on it
    without parseable text (ADR 0019).
    """

    source: LayoutSource
    layout: str | None
    variant: str | None
    display_name: str | None
    # The keymap is large and would swamp a log line; keep it out of repr().
    keymap_text: str | None = field(repr=False)


# --- parsing ``busctl`` output ------------------------------------------------


#: C escapes ``busctl`` may emit inside a quoted string. Anything else after a
#: backslash is taken literally, which is the least surprising guess and keeps a
#: round-trip of a stray backslash from raising.
_ESCAPES: Mapping[str, str] = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    '"': '"',
    "\\": "\\",
}


def _tokenize(output: str) -> list[str]:
    """Split ``busctl`` output into bare and quoted tokens.

    The format is whitespace-separated: a signature, a count, then the values.
    Strings are double-quoted and C-escaped, so the display name
    ``"English (US)"`` survives intact. An unterminated quote is a parse error.
    """
    tokens: list[str] = []
    index = 0
    length = len(output)
    while index < length:
        char = output[index]
        if char.isspace():
            index += 1
            continue
        if char == '"':
            value, index = _read_quoted(output, index + 1)
            tokens.append(value)
            continue
        start = index
        while index < length and not output[index].isspace():
            index += 1
        tokens.append(output[start:index])
    return tokens


def _read_quoted(output: str, index: int) -> tuple[str, int]:
    """Read one double-quoted, C-escaped string starting after its opening quote."""
    chars: list[str] = []
    length = len(output)
    while index < length:
        char = output[index]
        if char == '"':
            return "".join(chars), index + 1
        if char == "\\" and index + 1 < length:
            escape = output[index + 1]
            chars.append(_ESCAPES.get(escape, escape))
            index += 2
            continue
        chars.append(char)
        index += 1
    raise LayoutParseError("busctl output ended inside a quoted string")


def parse_layouts_list(output: str) -> tuple[LayoutEntry, ...]:
    """Parse ``getLayoutsList() -> a(sss)`` output into its entries.

    ``busctl`` renders an array as ``a(sss) <count> "l" "v" "d" ...``, flattening
    each tuple into three consecutive strings. Fewer or more strings than
    ``3 * count`` is a :class:`LayoutParseError`, never a silent truncation.
    """
    tokens = _tokenize(output)
    if len(tokens) < 2:
        raise LayoutParseError(f"expected a signature and a count, got {tokens!r}")
    if tokens[0] != "a(sss)":
        raise LayoutParseError(f"expected signature 'a(sss)', got {tokens[0]!r}")
    try:
        count = int(tokens[1])
    except ValueError as exc:
        raise LayoutParseError(f"expected an integer count, got {tokens[1]!r}") from exc
    values = tokens[2:]
    if len(values) != count * 3:
        raise LayoutParseError(
            f"declared {count} layouts but found {len(values)} strings"
        )
    return tuple(
        LayoutEntry(
            layout=values[index],
            variant=values[index + 1],
            display_name=values[index + 2],
        )
        for index in range(0, len(values), 3)
    )


def parse_active_layout_index(output: str) -> int:
    """Parse ``getLayout() -> u`` output into the active index."""
    tokens = _tokenize(output)
    if len(tokens) != 2:
        raise LayoutParseError(f"expected a signature and one value, got {tokens!r}")
    if tokens[0] != "u":
        raise LayoutParseError(f"expected signature 'u', got {tokens[0]!r}")
    try:
        index = int(tokens[1])
    except ValueError as exc:
        raise LayoutParseError(f"expected an integer index, got {tokens[1]!r}") from exc
    if index < 0:
        raise LayoutParseError(f"layout index cannot be negative, got {index}")
    return index


# --- detection ----------------------------------------------------------------


def live_keymap_text(*, runner: LayoutCommandRunner | None = None) -> str | None:
    """Return the compositor's live keymap, or ``None`` if it cannot be obtained.

    Shells out to ``xkbcli dump-keymap-wayland`` with a bounded timeout. Every
    failure mode -- the binary missing, no Wayland session, a non-zero exit, an
    empty result, or a timeout -- returns ``None`` so the caller can fall back to
    the detected layout names. This function never raises for a missing binary
    and never hangs.
    """
    run = runner if runner is not None else _run_command
    argv: tuple[str, ...] = (XKBCLI_BINARY, *DUMP_KEYMAP_ARGUMENTS)
    try:
        completed = run(argv, LAYOUT_COMMAND_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    text = completed.stdout.decode("utf-8", errors="replace")
    if text.strip() == "":
        return None
    return text


def detect_layout(*, runner: LayoutCommandRunner | None = None) -> LayoutInfo | None:
    """Return the active KDE layout, or ``None`` on any failure.

    Calls ``busctl --user call org.kde.keyboard /Layouts org.kde.KeyboardLayouts
    getLayoutsList`` and ``getLayout`` (both verified live, ADR 0020) and selects
    the entry at the reported active index. A missing service (a non-KDE
    compositor), a non-zero exit, unparseable output, an out-of-range index, or a
    timeout all return ``None`` -- this stays compositor-agnostic (ADR 0002)
    without pretending to test other compositors.
    """
    run = runner if runner is not None else _run_command
    try:
        listed = run(_busctl_argv("getLayoutsList"), LAYOUT_COMMAND_TIMEOUT_SECONDS)
        if listed.returncode != 0:
            return None
        entries = parse_layouts_list(
            listed.stdout.decode("utf-8", errors="replace")
        )
        if not entries:
            return None
        active = run(_busctl_argv("getLayout"), LAYOUT_COMMAND_TIMEOUT_SECONDS)
        if active.returncode != 0:
            return None
        index = parse_active_layout_index(active.stdout.decode("utf-8", errors="replace"))
    except (OSError, subprocess.SubprocessError, LayoutParseError):
        return None
    if not 0 <= index < len(entries):
        return None
    entry = entries[index]
    return LayoutInfo(
        layout=entry.layout,
        variant=entry.variant,
        display_name=entry.display_name,
        index=index,
    )


def _busctl_argv(method: str) -> tuple[str, ...]:
    """Build the read-only ``busctl --user call`` argv for one layout method."""
    return (
        BUSCTL_BINARY,
        "--user",
        "call",
        KEYBOARD_SERVICE,
        KEYBOARD_OBJECT_PATH,
        KEYBOARD_INTERFACE,
        method,
    )


# --- resolution ---------------------------------------------------------------


def resolve_layout(
    *,
    configured_layout: str = AUTO_LAYOUT,
    configured_variant: str = "",
    runner: LayoutCommandRunner | None = None,
) -> ResolvedLayout:
    """Choose a layout by the ADR 0020 order and say where it came from.

    1. An explicit ``configured_layout`` (not ``"auto"``, not blank) wins without
       probing anything, so a user who sets ``layout = "hu"`` gets ``hu`` even if
       detection says otherwise.
    2. Otherwise the live compositor keymap, used verbatim because it is KWin's
       own keymap including options.
    3. Otherwise the detected layout/variant names from KDE's layout service.
    4. Otherwise :class:`LayoutResolutionError`.

    A blank or ``"auto"`` configuration deliberately means "detect", never
    "default to ``us``": the only way to get ``us`` is to configure it.
    """
    run = runner if runner is not None else _run_command

    explicit = configured_layout.strip()
    if explicit != "" and explicit != AUTO_LAYOUT:
        return ResolvedLayout(
            source=LayoutSource.CONFIGURED,
            layout=configured_layout,
            variant=configured_variant,
            display_name=None,
            keymap_text=None,
        )

    keymap = live_keymap_text(runner=run)
    if keymap is not None:
        return ResolvedLayout(
            source=LayoutSource.LIVE_KEYMAP,
            layout=None,
            variant=None,
            display_name=None,
            keymap_text=keymap,
        )

    info = detect_layout(runner=run)
    if info is not None:
        return ResolvedLayout(
            source=LayoutSource.DETECTED,
            layout=info.layout,
            variant=info.variant,
            display_name=info.display_name,
            keymap_text=None,
        )

    raise LayoutResolutionError(
        "could not determine the keyboard layout: [injection.ydotool] layout is "
        "'auto', the live keymap could not be read (xkbcli dump-keymap-wayland "
        "failed or timed out), and KDE's org.kde.keyboard layout service did not "
        "report one. Set an explicit layout, for example "
        '[injection.ydotool] layout = "hu" with variant = "", or run ges2t '
        "under KDE Plasma on Wayland."
    )


# --- injector block configuration ---------------------------------------------

# Following ``ges2t.transcribers.whistle_config``: the generic layer hands the
# selected ``[injection.<name>]`` table over as a raw mapping, and this is where
# that mapping becomes a typed config with defaults and unknown-key warnings.

#: Dotted block names, used to prefix every warning and error so ``doctor``
#: points at the exact key the user must edit.
YDOTOOL_BLOCK_NAME: str = "injection.ydotool"
CLIPBOARD_BLOCK_NAME: str = "injection.clipboard"

#: Default delay between emitted key events, in milliseconds. ``ydotool key``'s
#: own default is 12 ms (``man ydotool``). Ticket 23 measures the batched path
#: and may revise this after the real numbers; it is a single constant so the
#: revision touches one line.
DEFAULT_KEY_DELAY_MS: int = 12

#: Default paste chord for the clipboard backend (ticket 25). A tuple because a
#: future default may add a fallback chord; today it is exactly one.
DEFAULT_PASTE_KEYS: tuple[str, ...] = ("ctrl+v",)

#: Keys ``[injection.ydotool]`` understands; everything else warns.
YDOTOOL_KNOWN_KEYS: frozenset[str] = frozenset({"layout", "variant", "key_delay_ms"})

#: Keys ``[injection.clipboard]`` understands; everything else warns.
CLIPBOARD_KNOWN_KEYS: frozenset[str] = frozenset({"paste_keys"})


class InjectionConfigError(Exception):
    """An ``[injection.<name>]`` block has a wrong-typed or out-of-range value.

    Every message names the offending key, the received value, and the allowed
    type or range. Unknown keys are not raised; they are returned as warnings,
    mirroring the generic layer and the engine adapters (ADR 0009).
    """


@dataclass(frozen=True)
class YdotoolConfig:
    """Validated ``[injection.ydotool]`` settings (ADR 0009, ADR 0020).

    ``layout`` defaults to :data:`AUTO_LAYOUT`, which means "use the live
    keymap"; any other value is an explicit layout the emitter must honour.
    ``variant`` is only meaningful with an explicit layout. ``key_delay_ms`` is
    passed to ``ydotool key -d``.
    """

    layout: str = AUTO_LAYOUT
    variant: str = ""
    key_delay_ms: int = DEFAULT_KEY_DELAY_MS

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> YdotoolConfigResult:
        """Validate a raw ``[injection.ydotool]`` mapping.

        An absent block (which the generic layer never synthesizes) yields all
        defaults. Unknown keys are collected as warnings and ignored; a
        wrong-typed value raises :class:`InjectionConfigError`.
        """
        warnings: list[str] = [
            f"{YDOTOOL_BLOCK_NAME}: unknown key {key!r}"
            for key in raw
            if key not in YDOTOOL_KNOWN_KEYS
        ]
        layout = _read_block_str(raw, "layout", AUTO_LAYOUT, YDOTOOL_BLOCK_NAME)
        variant = _read_block_str(raw, "variant", "", YDOTOOL_BLOCK_NAME)
        key_delay_ms = _read_key_delay_ms(raw)
        return YdotoolConfigResult(
            config=cls(layout=layout, variant=variant, key_delay_ms=key_delay_ms),
            warnings=warnings,
        )


@dataclass(frozen=True)
class YdotoolConfigResult:
    """The validated ``[injection.ydotool]`` config plus its non-fatal warnings."""

    config: YdotoolConfig
    warnings: list[str]


@dataclass(frozen=True)
class ClipboardConfig:
    """Validated ``[injection.clipboard]`` settings (ticket 25).

    ``paste_keys`` is a sequence of key-chord spellings, defaulting to
    ``("ctrl+v",)``. It is a tuple so the frozen config cannot be mutated
    through a shared list.
    """

    paste_keys: tuple[str, ...] = DEFAULT_PASTE_KEYS

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> ClipboardConfigResult:
        """Validate a raw ``[injection.clipboard]`` mapping.

        An absent block yields the default chord. Unknown keys warn; a
        non-list, an empty list, or a non-string element raises
        :class:`InjectionConfigError`.
        """
        warnings: list[str] = [
            f"{CLIPBOARD_BLOCK_NAME}: unknown key {key!r}"
            for key in raw
            if key not in CLIPBOARD_KNOWN_KEYS
        ]
        paste_keys = _read_paste_keys(raw)
        return ClipboardConfigResult(
            config=cls(paste_keys=paste_keys),
            warnings=warnings,
        )


@dataclass(frozen=True)
class ClipboardConfigResult:
    """The validated ``[injection.clipboard]`` config plus its non-fatal warnings."""

    config: ClipboardConfig
    warnings: list[str]


def _read_block_str(
    raw: Mapping[str, Any], key: str, default: str, block_name: str
) -> str:
    """Return a string key from an injection block, or its default."""
    if key not in raw:
        return default
    value = raw[key]
    if not isinstance(value, str):
        raise InjectionConfigError(
            f"{block_name}.{key}: expected a string, got {_describe_value(value)}"
        )
    return value


def _read_key_delay_ms(raw: Mapping[str, Any]) -> int:
    """Return ``key_delay_ms`` as a non-negative ``int`` (TOML ``int`` or integral ``float``)."""
    key = "key_delay_ms"
    if key not in raw:
        return DEFAULT_KEY_DELAY_MS
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InjectionConfigError(
            f"{YDOTOOL_BLOCK_NAME}.{key}: expected a millisecond count (int), "
            f"got {_describe_value(value)}"
        )
    if isinstance(value, float) and not value.is_integer():
        raise InjectionConfigError(
            f"{YDOTOOL_BLOCK_NAME}.{key}: expected a whole number of milliseconds, "
            f"got {_describe_value(value)}"
        )
    delay = int(value)
    if delay < 0:
        raise InjectionConfigError(
            f"{YDOTOOL_BLOCK_NAME}.{key} = {delay} is invalid: must be zero or more"
        )
    return delay


def _read_paste_keys(raw: Mapping[str, Any]) -> tuple[str, ...]:
    """Return ``paste_keys`` as a non-empty tuple of non-empty strings."""
    key = "paste_keys"
    if key not in raw:
        return DEFAULT_PASTE_KEYS
    value = raw[key]
    if not isinstance(value, (list, tuple)):
        raise InjectionConfigError(
            f"{CLIPBOARD_BLOCK_NAME}.{key}: expected an array of strings, "
            f"got {_describe_value(value)}"
        )
    keys: list[str] = []
    for element in value:
        if not isinstance(element, str):
            raise InjectionConfigError(
                f"{CLIPBOARD_BLOCK_NAME}.{key}: expected an array of strings, "
                f"got an element of type {_describe_value(element)}"
            )
        if element.strip() == "":
            raise InjectionConfigError(
                f"{CLIPBOARD_BLOCK_NAME}.{key}: an empty key chord is not valid"
            )
        keys.append(element)
    if not keys:
        raise InjectionConfigError(
            f"{CLIPBOARD_BLOCK_NAME}.{key} must contain at least one key chord "
            '(the default is ["ctrl+v"])'
        )
    return tuple(keys)


def _describe_value(value: object) -> str:
    """Render a value for an error message as its type name, plus the value when scalar."""
    type_name = type(value).__name__
    if isinstance(value, (bool, int, float, str)):
        return f"{type_name} ({value!r})"
    return type_name
