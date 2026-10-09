"""Text injection through the ``ydotool`` virtual keyboard (ADR 0003, ADR 0020).

Three rules live here and nowhere else:

* **Text assembly** (ADR 0012) -- :func:`decide_injection` is the single
  implementation of the trailing-space table: empty or whitespace-only text is
  never typed at all, non-empty text is typed with exactly one appended ASCII
  space when ``trailing_space`` is true, and is otherwise passed through
  byte-for-byte. There is no stripping, case folding, or internal whitespace
  collapsing.
* **Layout-aware mapping** -- :class:`XkbKeySequenceSource` turns the assembled
  text into a :class:`~ges2t.injectors.keymap.KeySequence` against the *live*
  keyboard layout (ADR 0020). It rebuilds its keymap for **every** utterance,
  because a compiled keymap caches its reverse map and a process-lifetime one
  would pin a stale layout when the user switches layout.
* **Batched emission** -- :class:`YdotoolInjector` renders the precomputed
  sequence as **one** ``ydotool key -d <ms> KEYCODE:PRESSED ...`` invocation per
  injection. ``ydotool type`` is deliberately **never** called: its hardcoded
  US-position table corrupts every character whose position differs on a non-US
  layout and it sleeps twice per character (ADR 0003's amendment). Every failure
  mode still becomes a typed exception with an actionable message, and there is
  no clipboard or ``wtype`` fallback (ADR 0003).

Verified invocation
-------------------
``man ydotool`` (2024-07-14) documents the subcommand as::

    key [-d,--key-delay <ms>] [<KEYCODE:PRESSED> ...]

with "where :1 for pressed means the key is down and then :0 means the key is
released" and "See '/usr/include/linux/input-event-codes.h' for available key
codes (KEY_*)". :class:`~ges2t.injectors.keymap.KeyEvent.keycode` is exactly such
a Linux/evdev ``KEY_*`` code, so it is rendered **straight** into the argv token
with no offset (ticket 22's ruling, confirmed by a byte-exact round trip). The
invocation this module actually uses is therefore::

    ydotool key -d <key_delay_ms> <KEYCODE>:1 <KEYCODE>:0 ...

``YDOTOOL_SOCKET`` is inherited from the environment when set -- that is how
``ydotool`` itself selects a non-default daemon socket -- and the daemon default
is used otherwise. The executable is resolved from ``PATH``.
"""

from __future__ import annotations

import os
import subprocess
import time
import warnings
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ges2t.injectors.keymap import KeyEvent, KeySequence, XkbError, XkbKeymap
from ges2t.injectors.layout import (
    AUTO_LAYOUT,
    DEFAULT_KEY_DELAY_MS,
    LayoutCommandRunner,
    LayoutResolutionError,
    LayoutSource,
    ResolvedLayout,
    resolve_layout,
)

__all__ = [
    "CommandRunner",
    "InjectionDecision",
    "KEY_ARGUMENTS",
    "KEY_DELAY_FLAG",
    "KeySequenceSource",
    "SkipInjection",
    "TypeText",
    "XkbKeySequenceSource",
    "YDOTOOL_BINARY",
    "YdotoolDaemonUnreachableError",
    "YdotoolError",
    "YdotoolInjectionError",
    "YdotoolInjector",
    "YdotoolKeymapError",
    "YdotoolNotFoundError",
    "YdotoolTextUnreachableError",
    "YdotoolUnreachableWarning",
    "decide_injection",
]

#: Executable looked up on ``PATH``. Never an absolute path: the user's
#: environment decides which ``ydotool`` runs.
YDOTOOL_BINARY: str = "ydotool"

#: Arguments after the executable. ``key`` takes an explicit event list, so the
#: whole utterance is one spawn (ADR 0003's amendment).
KEY_ARGUMENTS: tuple[str, ...] = ("key",)

#: The short form of ``--key-delay``; the man page documents both.
KEY_DELAY_FLAG: str = "-d"

#: The runner seam. It is deliberately tiny -- ``argv``, the bytes for the
#: child's stdin, and the environment -- so a test double is a few lines and
#: never touches a real daemon (ADR 0014). ``ydotool key`` reads nothing from
#: stdin, so the bytes are always empty; the parameter is kept because the seam
#: is shared with the clipboard backend's shape.
CommandRunner = Callable[
    [Sequence[str], bytes, Mapping[str, str]], subprocess.CompletedProcess[bytes]
]


class YdotoolError(Exception):
    """Base class for every ``ydotool`` delivery failure (ADR 0003)."""


class YdotoolNotFoundError(YdotoolError):
    """``ydotool`` is not on ``PATH``."""


class YdotoolDaemonUnreachableError(YdotoolError):
    """``ydotool`` ran but could not reach the ``ydotoold`` daemon (ADR 0003)."""


class YdotoolInjectionError(YdotoolError):
    """``ydotool`` exited non-zero, so delivery may be incomplete.

    This is always raised on a non-zero exit -- including one that happens
    *after* some characters were already typed. A partial delivery is a failure
    the caller (and eventually the user) must see, never a quiet no-op (ADR
    0003).
    """

    def __init__(self, returncode: int, stderr: str) -> None:
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(
            f"ydotool exited with status {returncode} after being asked to type; "
            "any text already delivered to the focused window may be incomplete. "
            f"stderr: {stderr or '<empty>'}"
        )


class YdotoolKeymapError(YdotoolError):
    """The active keyboard layout could not be resolved or compiled (ADR 0020).

    Raised instead of leaking an :class:`~ges2t.injectors.keymap.XkbError` or a
    :class:`~ges2t.injectors.layout.LayoutResolutionError`, so callers can keep
    catching :class:`YdotoolError` for every injection failure.
    """


class YdotoolTextUnreachableError(YdotoolError):
    """**Every** character was unreachable on the active layout (ADR 0020).

    A partial miss is a warning (the reachable characters are still typed); a
    total miss is an error, because silently typing nothing is exactly the
    failure mode this whole workstream exists to remove.
    """

    def __init__(self, unreachable: Sequence[str]) -> None:
        self.unreachable = tuple(unreachable)
        characters = ", ".join(repr(char) for char in self.unreachable) or "<none>"
        super().__init__(
            "none of the characters in the transcription can be typed on the "
            f"active keyboard layout ({characters}), so nothing was injected. "
            'Set injection.backend = "clipboard" to paste text the layout '
            "cannot type."
        )


class YdotoolUnreachableWarning(UserWarning):
    """Some (not all) characters were skipped because the layout cannot type them.

    The reachable characters are still delivered in the same batch; the skipped
    ones are reported so the user can see why text is missing, rather than
    getting silently corrupted output (ADR 0020).
    """


@runtime_checkable
class KeySequenceSource(Protocol):
    """Maps assembled text to the key events that type it (ADR 0020).

    This is the seam between ticket 22's mapping and this emitter. An
    implementation must reflect the **current** keyboard layout per call: a
    compiled ``XkbKeymap`` caches its reverse map, so a source that reused one
    across utterances would pin a stale layout after a layout switch.
    """

    def sequence_for(self, text: str) -> KeySequence:
        """Return the events for ``text`` plus the characters the layout lacks."""
        ...


@dataclass(frozen=True)
class TypeText:
    """The exact string to map to key events (ADR 0012)."""

    payload: str


@dataclass(frozen=True)
class SkipInjection:
    """Nothing is typed; the transcription was empty or whitespace-only (ADR 0012)."""

    reason: str


#: The result of text assembly: either something to map or an explicit
#: "type nothing" decision. A typed union rather than a ``str | None`` sentinel
#: so callers cannot confuse "no text" with "empty payload" (ADR 0019).
InjectionDecision = TypeText | SkipInjection

#: stderr fragments ``ydotool`` emits when it cannot talk to the daemon. The
#: current machine produces "failed to connect socket ... No such file or
#: directory"; "Connection refused" is the other common shape.
_SOCKET_FAILURE_MARKERS: tuple[str, ...] = (
    "failed to connect socket",
    "connection refused",
    "ydotoold is running",
    "no such file or directory",
)


def decide_injection(text: str, *, trailing_space: bool) -> InjectionDecision:
    """Apply the ADR 0012 text-assembly table. This is its only implementation.

    The table, in order:

    * ``text`` empty or whitespace-only -> :class:`SkipInjection`; the caller
      must not spawn a subprocess at all.
    * non-empty and ``trailing_space`` true -> ``text + " "``, exactly one
      ASCII space appended.
    * non-empty and ``trailing_space`` false -> ``text`` unchanged.

    ``text`` is otherwise never modified: no ``str.strip()``, no case change,
    and no collapsing of whitespace inside the string. A text that already ends
    in a space therefore gets a second one, because the rule is "append one
    space", not "normalise to one space".
    """
    if text.strip() == "":
        return SkipInjection(reason="transcription was empty or whitespace-only")
    if trailing_space:
        return TypeText(payload=text + " ")
    return TypeText(payload=text)


def _subprocess_environment() -> dict[str, str]:
    """Return the environment for the child process.

    ``ydotool`` reads ``YDOTOOL_SOCKET`` itself, so honouring it is simply a
    matter of inheriting ``os.environ``; when it is unset the daemon default
    (``$XDG_RUNTIME_DIR/.ydotool_socket``) applies.
    """
    return dict(os.environ)


def _run_command(
    argv: Sequence[str], stdin_bytes: bytes, env: Mapping[str, str]
) -> subprocess.CompletedProcess[bytes]:
    """The real :data:`CommandRunner`: run ``argv`` and capture its output.

    ``check=False`` because the caller classifies the exit status; a missing
    executable raises :class:`FileNotFoundError`, which the caller translates.
    """
    return subprocess.run(
        list(argv),
        input=stdin_bytes,
        env=dict(env),
        capture_output=True,
        check=False,
    )


def _looks_like_daemon_failure(stderr: str) -> bool:
    """Whether ``stderr`` indicates ``ydotoold`` was unreachable."""
    lowered = stderr.lower()
    return any(marker in lowered for marker in _SOCKET_FAILURE_MARKERS)


def _daemon_unreachable_message(stderr: str) -> str:
    """Build the actionable "start the daemon" message (ADR 0003, ADR 0016)."""
    detail = stderr.strip() or "<no output>"
    return (
        "ydotoold is not reachable, so ges2t cannot type into the focused "
        "window (ADR 0003 has no clipboard fallback). Start and enable the "
        "daemon with 'systemctl --user enable --now ydotool', and make sure it "
        "may write /dev/uinput: your user needs the 'input' group or ydotool's "
        "udev rule, which is why /dev/uinput is mode crw-rw---- root:input "
        "(a re-login picks up a group change). ydotoold said: " + detail
    )


def _unreachable_warning(unreachable: Sequence[str]) -> str:
    """Build the "characters skipped" warning text (ADR 0020)."""
    characters = ", ".join(repr(char) for char in unreachable)
    return (
        "ges2t: skipping characters the active keyboard layout cannot type: "
        f"{characters}. Set injection.backend = \"clipboard\" to paste text the "
        "layout cannot type."
    )


class XkbKeySequenceSource:
    """A :class:`KeySequenceSource` backed by a fresh ``XkbKeymap`` per call (ADR 0020).

    ``layout`` is the configured ``[injection.ydotool]`` value: ``"auto"`` (the
    default) resolves the compositor's **live** keymap, and any other value is an
    explicit layout. ``variant`` travels with an explicit layout.

    The keymap is rebuilt for **every** :meth:`sequence_for` call -- including a
    fresh layout resolution, because the active group can change between
    utterances -- and closed immediately afterwards, so the reverse map is never
    reused across utterances and no keymap leaks in the long-lived service.

    ``runner`` is the layout-detection subprocess seam from
    :mod:`ges2t.injectors.layout`; production leaves it as the real
    ``subprocess`` runner, tests inject a fake so no compositor is needed.
    """

    def __init__(
        self,
        layout: str = AUTO_LAYOUT,
        variant: str = "",
        *,
        runner: LayoutCommandRunner | None = None,
    ) -> None:
        self._layout = layout
        self._variant = variant
        self._runner = runner

    def sequence_for(self, text: str) -> KeySequence:
        """Resolve the current layout, compile a fresh keymap, and map ``text``."""
        resolved = self._resolve()
        keymap = self._compile(resolved)
        try:
            return keymap.sequence_for(text)
        finally:
            keymap.close()

    def _resolve(self) -> ResolvedLayout:
        """Run the ADR 0020 resolution order, translating failure to a typed error."""
        try:
            return resolve_layout(
                configured_layout=self._layout,
                configured_variant=self._variant,
                runner=self._runner,
            )
        except LayoutResolutionError as exc:
            raise YdotoolKeymapError(
                "could not determine the active keyboard layout, so ges2t "
                f"cannot compute key events: {exc}"
            ) from exc

    def _compile(self, resolved: ResolvedLayout) -> XkbKeymap:
        """Compile the resolved layout into a keymap, or raise a typed error."""
        try:
            if resolved.source is LayoutSource.LIVE_KEYMAP and resolved.keymap_text:
                return XkbKeymap.from_string(resolved.keymap_text)
            if resolved.layout is not None:
                return XkbKeymap.from_names(resolved.layout, resolved.variant or "")
        except XkbError as exc:
            raise YdotoolKeymapError(
                f"the active keyboard layout could not be compiled: {exc}"
            ) from exc
        raise YdotoolKeymapError(
            "the active keyboard layout could not be resolved to a keymap "
            f"(source {resolved.source.value!r}); set [injection.ydotool] "
            "layout explicitly."
        )


class YdotoolInjector:
    """An :class:`~ges2t.interfaces.Injector` backed by ``ydotool`` (ADR 0003).

    ``key_sequence_source`` maps the assembled text to Linux ``KEY_*`` events;
    production passes :class:`XkbKeySequenceSource`, tests pass a fake so no
    ``libxkbcommon`` or compositor is needed. ``trailing_space`` is the ADR 0012
    preference (``ui.trailing_space``, default true). ``key_delay_ms`` becomes
    ``ydotool key -d`` and comes from ``[injection.ydotool]`` (ADR 0009).
    ``runner`` is the injectable delivery seam.

    ``last_injection_ms`` records the wall time of the most recent successful
    delivery -- the per-call cost ADR 0003 requires be measurable -- and is
    ``None`` before any such call, after a skip, and after an all-unreachable
    failure.
    """

    def __init__(
        self,
        key_sequence_source: KeySequenceSource,
        *,
        trailing_space: bool = True,
        key_delay_ms: int = DEFAULT_KEY_DELAY_MS,
        runner: CommandRunner | None = None,
    ) -> None:
        self.trailing_space = trailing_space
        self.key_delay_ms = key_delay_ms
        self._source = key_sequence_source
        self._runner: CommandRunner = runner if runner is not None else _run_command
        self.last_injection_ms: float | None = None

    def inject(self, text: str) -> None:
        """Type ``text`` into the focused window and return once delivered.

        Whitespace-only text is a no-op that spawns no process *and maps
        nothing* (ADR 0012); the mapping runs once per non-empty utterance
        (ADR 0020). Every other outcome either completes or raises a
        :class:`YdotoolError`.
        """
        decision = decide_injection(text, trailing_space=self.trailing_space)
        if isinstance(decision, SkipInjection):
            self.last_injection_ms = None
            return
        self._deliver(self._source.sequence_for(decision.payload))

    def type_text(self, text: str) -> None:
        """Type ``text`` verbatim, applying no trailing-space rule (ticket 34).

        This is the raw primitive the streaming path needs: it types the exact
        payload and adds nothing, so a caller that types the engine's committed
        prefix in pieces can apply ADR 0012's single trailing space once, at the
        end, by assembling the final document itself. An empty payload is a
        no-op that maps nothing and spawns nothing; a payload consisting only of
        whitespace (for example the trailing space) **is** typed, because it is
        part of the document and ADR 0012's "never inject whitespace-only" rule
        is a whole-utterance rule applied by the caller.
        """
        if text == "":
            return
        self._deliver(self._source.sequence_for(text))

    def _deliver(self, sequence: KeySequence) -> None:
        """Emit ``sequence`` in a single ``ydotool key`` invocation."""
        if not sequence.events:
            # The payload was non-empty, so an empty event list means every
            # character was unreachable: refuse loudly rather than type nothing.
            self.last_injection_ms = None
            raise YdotoolTextUnreachableError(sequence.unreachable)
        if sequence.unreachable:
            warnings.warn(
                _unreachable_warning(sequence.unreachable),
                YdotoolUnreachableWarning,
                stacklevel=2,
            )
        argv = self._build_argv(sequence.events)
        started = time.perf_counter()
        try:
            completed = self._runner(argv, b"", _subprocess_environment())
        except FileNotFoundError as exc:
            raise YdotoolNotFoundError(
                "ydotool was not found on PATH, so ges2t cannot type. Install "
                "the ydotool package (Arch: 'sudo pacman -S ydotool') and "
                "confirm 'ydotool' runs in the same environment as ges2t."
            ) from exc
        self.last_injection_ms = (time.perf_counter() - started) * 1000.0

        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        if completed.returncode != 0:
            if _looks_like_daemon_failure(stderr):
                raise YdotoolDaemonUnreachableError(
                    _daemon_unreachable_message(stderr)
                )
            raise YdotoolInjectionError(completed.returncode, stderr)

    def _build_argv(self, events: Sequence[KeyEvent]) -> tuple[str, ...]:
        """Render the event list as ``ydotool key -d <ms> KEYCODE:PRESSED ...``.

        ``KeyEvent.keycode`` is already a Linux/evdev ``KEY_*`` code, so it is
        rendered with **no** offset (ticket 22's ruling). ``pressed`` becomes
        ``1``/``0`` exactly as the man page's ``KEYCODE:PRESSED`` token.
        """
        tokens = tuple(
            f"{event.keycode}:{1 if event.pressed else 0}" for event in events
        )
        return (
            YDOTOOL_BINARY,
            *KEY_ARGUMENTS,
            KEY_DELAY_FLAG,
            str(self.key_delay_ms),
            *tokens,
        )
