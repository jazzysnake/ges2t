"""Text injection by clipboard paste (ADR 0003 amended, ADR 0020, ticket 25).

This is the **second** ``Injector`` backend. It is a *configured alternative*
chosen with ``injection.backend = "clipboard"`` -- never an automatic fallback
(ADR 0003: silently switching mechanism changes behaviour the user did not ask
for, and hiding a failure is exactly what made the ``hu`` corruption invisible).

Two properties motivate it, both named in ADR 0020's amendment:

* it is **layout-independent and O(1)** -- one ``wl-copy`` plus one paste chord,
  tens of milliseconds regardless of transcript length, versus ``ydotool``'s
  per-character emission; and
* it is the **escape hatch for text the layout cannot type** -- an emoji, or a
  character absent from the layout and its compose tables. The ``ydotool``
  backend skips those with a warning; this backend delivers them.

The delivery sequence
---------------------
1. read what is currently on the clipboard with ``wl-paste`` (best effort: a
   text-type clipboard that cannot be read -- an image, a password-manager
   selection -- yields ``None`` rather than failing the injection);
2. write the assembled transcript with ``wl-copy`` **over stdin** (never as an
   argv element: text on a command line is visible to every process on the
   machine, and the ticket's rule is that text never reaches argv);
3. send the paste chord as **raw Linux/evdev keycodes** through ``ydotool key``
   -- the same transport as ticket 23, so the chord is layout-safe;
4. restore the previous clipboard after a short bounded delay, **best effort**:
   a failed restore warns and never fails the injection.

Text rules are not reimplemented here
-------------------------------------
:func:`~ges2t.injectors.ydotool.decide_injection` is imported and called
unchanged, because it is the single implementation of ADR 0012's table: empty or
whitespace-only text performs **no** clipboard write and **no** paste, non-empty
text gets exactly one appended ASCII space when ``trailing_space`` is true, and
the text is otherwise unmodified. One implementation, two emitters.

The paste chord
---------------
``[injection.clipboard] paste_keys`` defaults to ``["ctrl+shift+v"]``
(ticket 38) and accepts a list of chord spellings. Each name is resolved to the
**plain Linux input-event code** of the physical key: ``KEY_LEFTCTRL`` is 29 and
``KEY_V`` is 47, so ``ctrl+v`` is emitted exactly as

    ydotool key -d <ms> 29:1 47:1 47:0 29:0

presses left-to-right, releases right-to-left (``KEY_LEFTSHIFT`` is 42, so the
default ``ctrl+shift+v`` becomes ``29:1 42:1 47:1 47:0 42:0 29:0``).

Why ``ctrl+shift+v`` is the default, and its honest cost
--------------------------------------------------------
It is the **only chord with real evidence behind it**: the user's manual
verification of ticket 25 passed with it in every target they tried -- nvim, and
in their experience browsers and regular editors -- so one configuration covered
both an editor *and* a terminal (``ctrl+v`` does not paste in most terminals).
The cost is that this is a **different binding, not a superset**: an application
that leaves ``ctrl+shift+v`` unbound pastes **nothing**, so set
``paste_keys = ["ctrl+v"]`` instead. In most GUI toolkits ``ctrl+shift+v`` means
*paste as plain text*, which for dictation is desirable rather than a surprise --
no rich formatting is carried in. Do **not** list both chords as a fallback: the
injector emits **every** configured chord, so that would paste twice. This is
documented here, in the config reference, and in the error message for an
unknown key name rather than left as a mystery.

The runner seam
---------------
:class:`ClipboardRunner` is tiny and injectable so tests never touch the real
clipboard (ADR 0014). Writing to the clipboard **destroys the user's current
selection**, which is a real, externally visible side effect; no test in this
repository performs it.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import threading
import time
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from ges2t.injectors.layout import DEFAULT_KEY_DELAY_MS
from ges2t.injectors.ydotool import (
    KEY_DELAY_FLAG,
    KeyEvent,
    SkipInjection,
    YDOTOOL_BINARY,
    decide_injection,
)

__all__ = [
    "CLIPBOARD_RESTORE_DELAY_SECONDS",
    "ClipboardError",
    "ClipboardInjectionError",
    "ClipboardKeyChordError",
    "ClipboardNotFoundError",
    "ClipboardPasteInjector",
    "ClipboardRestoreWarning",
    "ClipboardRunner",
    "ClipboardRunnerCall",
    "ClipboardSpawnError",
    "ClipboardTimeoutError",
    "KeyChord",
    "WL_COPY_BINARY",
    "WL_PASTE_BINARY",
    "is_modifier_keycode",
]

#: Executables looked up on ``PATH``; the user's environment decides which ones
#: run, exactly as it does for ``ydotool``.
WL_COPY_BINARY: str = "wl-copy"
WL_PASTE_BINARY: str = "wl-paste"

#: Arguments for the clipboard write. ``wl-copy`` with no ``text...`` operand
#: reads its payload from **stdin**, which is what keeps transcript text out of
#: the process table.
WL_COPY_ARGUMENTS: tuple[str, ...] = ()

#: Arguments for the clipboard read. ``--no-newline`` stops ``wl-paste`` from
#: appending a trailing ``\n`` to text selections, so a restored selection is
#: byte-identical to the one that was read (``wl-copy`` does the same).
WL_PASTE_ARGUMENTS: tuple[str, ...] = ("--no-newline",)

#: Upper bound on one immediate ``wl-copy``/``wl-paste`` invocation. Both are
#: local and return in a few milliseconds; the bound exists so a wedged
#: compositor cannot hang dictation. The **restore** child is deliberately not
#: subject to it -- it sleeps by design (see below).
CLIPBOARD_COMMAND_TIMEOUT_SECONDS: float = 5.0

#: How long the restore is delayed after the payload was copied. The paste chord
#: is already sent when the restore is spawned, so this only has to cover the
#: target application's *round trip*: the compositor offering the selection, the
#: application requesting it, and the transfer completing. Too short and the
#: previous clipboard wins the race (the transcript is lost); the bounded sleep
#: plus keycode emission makes a lost paste vanishingly unlikely. A long
#: transcript must not be silently replaced by the previous clipboard, and the
#: service's command loop must never be blocked -- hence a detached child rather
#: than a sleep in the injection thread.
CLIPBOARD_RESTORE_DELAY_SECONDS: float = 0.4

#: The Linux input-event codes of the keys ``paste_keys`` spells. These are
#: **plain evdev codes with no XKB offset** -- the unit ``ydotool key`` consumes
#: and the unit :class:`~ges2t.injectors.keymap.KeyEvent` carries (ticket 22's
#: ruling, and the reason ``KEY_LEFTCTRL`` is 29 rather than 37).
_NAMED_KEY_CODES: Mapping[str, int] = {
    "ctrl": 29,
    "control": 29,
    "shift": 42,
    "alt": 56,
    "super": 125,
    "meta": 125,
    "insert": 110,
}

#: The real evdev letter block, keyed by the lower-case letter. The block is
#: **not** contiguous alphabetically -- it is ``QWERTYUIOP`` (16..25),
#: ``ASDFGHJKL`` (30..38), and ``ZXCVBNM`` (44..50). Resolving a letter with
#: ``30 + (ord(letter) - ord("a"))`` is wrong for 21 of the 26 letters and maps
#: ``q`` to ``c`` (16 -> 46) and ``r`` to ``v`` (19 -> 47): a config typo would
#: silently emit a *different*, potentially destructive chord. The table is the
#: single source of truth, and a letter absent from it is rejected loudly.
_LETTER_KEY_CODES: Mapping[str, int] = {
    "q": 16,
    "w": 17,
    "e": 18,
    "r": 19,
    "t": 20,
    "y": 21,
    "u": 22,
    "i": 23,
    "o": 24,
    "p": 25,
    "a": 30,
    "s": 31,
    "d": 32,
    "f": 33,
    "g": 34,
    "h": 35,
    "j": 36,
    "k": 37,
    "l": 38,
    "z": 44,
    "x": 45,
    "c": 46,
    "v": 47,
    "b": 48,
    "n": 49,
    "m": 50,
}

#: The set of codes above that are modifiers rather than the chord's final key.
_MODIFIER_KEY_CODES: frozenset[int] = frozenset({29, 42, 56, 125})

#: One chord segment: a letter, digit, or word. Anything else (an empty segment
#: from ``"ctrl++v"``, a stray symbol) is rejected with an actionable message.
_KEY_NAME_PATTERN = re.compile(r"[A-Za-z0-9_]+")

#: How a chord name is rendered in an error message so the user can see exactly
#: which modifier is being asked for.
_KNOWN_NAMES_HINT: str = (
    "recognised names: ctrl, shift, alt, super (also meta), insert, and a "
    "single letter or digit. Note that most terminals need 'ctrl+shift+v' "
    "rather than 'ctrl+v'."
)


class ClipboardError(Exception):
    """Base class for every clipboard-backend delivery failure.

    Callers catch this one type for any clipboard failure, exactly as they catch
    :class:`~ges2t.injectors.ydotool.YdotoolError` for the typing backend.
    """


class ClipboardNotFoundError(ClipboardError):
    """``wl-copy`` or ``wl-paste`` is not on ``PATH``."""

    def __init__(self, binary: str) -> None:
        self.binary = binary
        super().__init__(
            f"{binary} was not found on PATH, so ges2t cannot use the "
            "clipboard injection backend. Install the wl-clipboard package "
            "(Arch: 'sudo pacman -S wl-clipboard') and confirm '" + binary + "' "
            "runs in the same environment as ges2t, or set "
            'injection.backend = "ydotool" in ~/.config/ges2t/config.toml.'
        )


class ClipboardInjectionError(ClipboardError):
    """``wl-copy``/``wl-paste`` exited non-zero, so the paste may be incomplete."""

    def __init__(self, binary: str, returncode: int, stderr: str) -> None:
        self.binary = binary
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(
            f"{binary} exited with status {returncode}, so ges2t could not put "
            "the text on the clipboard (or read the previous selection); the "
            "paste may be incomplete. "
            f"stderr: {stderr or '<empty>'}"
        )


class ClipboardTimeoutError(ClipboardError):
    """``wl-copy``/``wl-paste`` did not finish within the bounded command timeout.

    A separate type rather than a ``returncode`` sentinel, because a timeout has
    no exit status to report (ADR 0019: distinguish failure with exceptions or a
    typed result, never with a sentinel the caller must special-case).
    """

    def __init__(self, binary: str, seconds: float) -> None:
        self.binary = binary
        self.seconds = seconds
        super().__init__(
            f"{binary} did not finish within {seconds:g}s, so ges2t gave up on "
            "the clipboard injection. A wedged Wayland compositor is the usual "
            "cause; check that the session is responsive."
        )


class ClipboardSpawnError(ClipboardError):
    """The clipboard command could not be started (permission, no session, ...)."""

    def __init__(self, binary: str, detail: str) -> None:
        self.binary = binary
        self.detail = detail
        super().__init__(
            f"{binary} could not be started, so ges2t cannot use the clipboard "
            f"injection backend: {detail or '<empty>'}"
        )


class ClipboardKeyChordError(ClipboardError, ValueError):
    """A ``[injection.clipboard] paste_keys`` entry is not a usable chord.

    The message names the offending entry, the unrecognised segment, and the
    recognised names -- including the fact that most terminals need
    ``ctrl+shift+v``.
    """


class ClipboardRestoreWarning(UserWarning):
    """Restoring the previous clipboard selection failed (best effort).

    The transcript was already pasted; only the user's previous clipboard
    content is affected. A restore must never fail the injection, so this is a
    warning rather than an error.
    """


@dataclass(frozen=True)
class ClipboardRunnerCall:
    """One recorded invocation of the clipboard runner seam.

    ``argv`` is the full command, ``stdin`` is the bytes handed to the child,
    and ``delay_s`` is the deferred-restore delay (``None`` for an immediate
    run). This class exists so a test double records a class rather than a
    positional tuple (ADR 0019).
    """

    argv: tuple[str, ...]
    stdin: bytes
    env: Mapping[str, str]
    delay_s: float | None


class ClipboardRunner(Protocol):
    """Runs one clipboard command and returns its completed process (ADR 0014).

    ``delay_s`` is ``None`` for an immediate run. A non-``None`` value asks for a
    **deferred** run: the implementation spawns a detached child and returns a
    synthetic zero-status result immediately, because the restore has to happen
    *after* the paste chord without blocking the service's command loop. A test
    double therefore records the deferred call without ever sleeping or touching
    the real clipboard.
    """

    def __call__(
        self,
        argv: Sequence[str],
        stdin_bytes: bytes,
        env: Mapping[str, str],
        *,
        delay_s: float | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        """Run ``argv`` now, or after ``delay_s`` seconds when it is not ``None``."""
        ...


def _run_command(
    argv: Sequence[str],
    stdin_bytes: bytes,
    env: Mapping[str, str],
    *,
    delay_s: float | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """The real :class:`ClipboardRunner`.

    An immediate run is a bounded, captured run through
    :func:`_run_immediate`: a missing executable raises
    :class:`FileNotFoundError` and a wedged compositor raises
    :class:`subprocess.TimeoutExpired`, both of which the caller classifies. A
    deferred run instead starts a **detached** ``sh -c 'sleep …; exec …'`` child
    that re-reads ``PATH`` itself, so a ``wl-copy`` that disappears between the
    payload write and the restore cannot fail the injection -- the child simply
    exits non-zero and the failure is discarded, as best-effort means.
    """
    environment = dict(env)
    if delay_s is None:
        return _run_immediate(argv, stdin_bytes, environment)
    script = "sleep " + format(delay_s, ".3f") + '; exec "$@"'
    try:
        proc = subprocess.Popen(
            ["sh", "-c", script, "sh", *argv],
            stdin=subprocess.PIPE,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        # The deferred run could not even start; the caller treats this as a
        # failed best-effort restore. Surface it as a synthetic non-zero status
        # rather than an exception, because nothing about the delivery failed.
        return subprocess.CompletedProcess(
            args=list(argv), returncode=1, stdout=b"", stderr=b"restore spawn failed"
        )
    # D1: the child sleeps before it ``exec``s, so ``stdin_bytes`` has to be fed
    # to the child's pipe and the write end closed, otherwise the eventual
    # ``wl-copy`` reads EOF and restores an **empty** selection. A daemon thread
    # does the write so a selection larger than the pipe buffer cannot block the
    # service's command loop while the child is still sleeping; the thread keeps
    # the ``Popen`` alive, so the pipe stays open until the bytes are in it.
    #
    # R2: only this deferred (restore) branch tolerates a failed thread start.
    # ``Thread.start`` raises ``RuntimeError`` under thread exhaustion, and the
    # payload has already been pasted by the time the restore runs, so the
    # failure has to stay best effort: a synthetic non-zero status that
    # ``_restore_clipboard`` turns into a ``ClipboardRestoreWarning`` rather than
    # an exception escaping ``inject()`` (ticket 17 shipped the opposite bug by
    # narrowing a restore ``except``). The ``except`` is deliberately scoped to
    # the thread start and **not** widened to ``Exception`` around the whole
    # runner: the payload write (``wl-copy``) and the paste chord keep their
    # typed fatal errors in ``_run``
    # (``ClipboardInjectionError``/``ClipboardNotFoundError``/
    # ``ClipboardTimeoutError``/``ClipboardSpawnError``). Swallowing those would
    # hide a broken paste (D3's lesson).
    try:
        _start_deferred_stdin(proc, stdin_bytes)
    except RuntimeError:
        return subprocess.CompletedProcess(
            args=list(argv),
            returncode=1,
            stdout=b"",
            stderr=b"restore thread could not be started",
        )
    return subprocess.CompletedProcess(
        args=list(argv), returncode=0, stdout=b"", stderr=b""
    )


def _run_immediate(
    argv: Sequence[str], stdin_bytes: bytes, environment: Mapping[str, str]
) -> subprocess.CompletedProcess[bytes]:
    """Run one immediate command without waiting on pipes a daemonising child holds.

    ``wl-copy`` **forks by default** and the forked child lives on to serve the
    selection. A ``capture_output=True`` run creates stdout/stderr **pipes** that
    the forked child inherits, and ``subprocess.run`` reads them to EOF -- which
    never arrives while the daemon holds a write end. Every real clipboard write
    therefore blocked for the full timeout and was reported as a failure even
    though the write had succeeded, so the paste chord never ran (ticket 36).

    Redirecting stdout and stderr to **regular files** removes the wait-on-EOF: a
    forked child may inherit the descriptors, but reading a file never depends on
    every writer closing it, so the bounded wait is only for the immediate
    process. Both streams are still captured from those files: stdout carries
    ``wl-paste``'s previous selection, and stderr still reaches ticket 25's typed
    error messages. ``subprocess.run``'s ``timeout`` is unchanged, so a genuinely
    wedged compositor still raises :class:`subprocess.TimeoutExpired`.
    """
    with (
        tempfile.TemporaryFile() as stdout_file,
        tempfile.TemporaryFile() as stderr_file,
    ):
        completed = subprocess.run(
            list(argv),
            input=stdin_bytes,
            env=dict(environment),
            stdout=stdout_file,
            stderr=stderr_file,
            check=False,
            timeout=CLIPBOARD_COMMAND_TIMEOUT_SECONDS,
        )
        stdout_file.seek(0)
        stderr_file.seek(0)
        return subprocess.CompletedProcess(
            args=list(argv),
            returncode=completed.returncode,
            stdout=stdout_file.read(),
            stderr=stderr_file.read(),
        )


def _start_deferred_stdin(proc: subprocess.Popen[bytes], payload: bytes) -> None:
    """Feed ``payload`` to a detached child's stdin and close it, off-thread."""
    threading.Thread(
        target=_write_stdin_and_close,
        args=(proc, payload),
        daemon=True,
    ).start()


def _write_stdin_and_close(proc: subprocess.Popen[bytes], payload: bytes) -> None:
    """Write ``payload`` to ``proc.stdin`` and close it (best effort)."""
    stream = proc.stdin
    if stream is None:  # pragma: no cover - deferred runs always use PIPE
        return
    try:
        stream.write(payload)
        stream.flush()
    except (BrokenPipeError, OSError):
        # The child died before reading (for example the compositor vanished).
        # A restore is best effort, so a lost write is not an error.
        pass
    finally:
        try:
            stream.close()
        except OSError:  # pragma: no cover - close is idempotent/best effort
            pass


def _clipboard_environment() -> dict[str, str]:
    """The environment for a clipboard child.

    ``wl-copy``/``wl-paste`` read ``WAYLAND_DISPLAY``/``XDG_RUNTIME_DIR`` from
    the environment, so inheriting ``os.environ`` is what makes them reach the
    session ges2t itself runs in.
    """
    return dict(os.environ)


def is_modifier_keycode(keycode: int) -> bool:
    """Whether ``keycode`` is one of the chord's modifier keys."""
    return keycode in _MODIFIER_KEY_CODES


@dataclass(frozen=True)
class KeyChord:
    """A parsed paste chord as Linux/evdev key events (ADR 0019, ticket 25).

    ``events`` is the full press-then-release sequence: modifiers pressed first
    in the order the user wrote them, the final key pressed and released, then
    everything released in **reverse** order. ``name`` is the original spelling,
    kept for error messages and for ``ydotool key`` troubleshooting.
    """

    name: str
    events: tuple[KeyEvent, ...]

    @property
    def keycodes(self) -> tuple[int, ...]:
        """The distinct keycodes the chord presses, in press order."""
        return tuple(event.keycode for event in self.events if event.pressed)

    @classmethod
    def parse(cls, spelling: str) -> KeyChord:
        """Resolve a ``"ctrl+shift+v"``-style spelling into key events.

        Raises :class:`ClipboardKeyChordError` for an empty chord, an empty
        segment, an unknown name, or a modifier used as the final key, so a
        typo in the config is a loud, actionable failure rather than a chord
        that silently does nothing.
        """
        segments = [segment.strip().lower() for segment in spelling.split("+")]
        if any(segment == "" for segment in segments):
            raise ClipboardKeyChordError(
                f"paste key {spelling!r} is not valid: a chord is '+'-separated "
                f"names with no empty segment (for example 'ctrl+shift+v'); "
                f"{_KNOWN_NAMES_HINT}"
            )
        codes: list[int] = []
        for segment in segments:
            if _KEY_NAME_PATTERN.fullmatch(segment) is None:
                raise ClipboardKeyChordError(
                    f"paste key {spelling!r} is not valid: {segment!r} is not a "
                    f"key name; {_KNOWN_NAMES_HINT}"
                )
            code = _resolve_code(segment)
            if code is None:
                raise ClipboardKeyChordError(
                    f"paste key {spelling!r} names an unknown key {segment!r}; "
                    f"{_KNOWN_NAMES_HINT}"
                )
            codes.append(code)
        if is_modifier_keycode(codes[-1]):
            raise ClipboardKeyChordError(
                f"paste key {spelling!r} ends with the modifier {segments[-1]!r}; "
                "a chord must end with the key that is pressed, for example "
                "'ctrl+v' or 'ctrl+shift+v'"
            )
        presses = tuple(
            KeyEvent(keycode=code, pressed=True) for code in codes
        )
        releases = tuple(
            KeyEvent(keycode=code, pressed=False) for code in reversed(codes)
        )
        return cls(name=spelling, events=presses + releases)


def _resolve_code(segment: str) -> int | None:
    """Resolve one chord segment to a Linux/evdev keycode, or ``None``.

    Named keys win. A single letter is looked up in :data:`_LETTER_KEY_CODES`,
    the real evdev letter block (``QWERTYUIOP``, ``ASDFGHJKL``, ``ZXCVBNM``),
    never with arithmetic on the alphabet: the block is not contiguous, so the
    old ``30 + (ord(upper) - ord("A"))`` silently mapped ``ctrl+q`` to
    ``ctrl+c`` and ``ctrl+r`` to ``ctrl+v`` (D2). A digit uses the genuinely
    contiguous ``KEY_1``..``KEY_0`` row (codes 2..11).
    """
    named = _NAMED_KEY_CODES.get(segment)
    if named is not None:
        return named
    if len(segment) == 1:
        letter = _LETTER_KEY_CODES.get(segment)
        if letter is not None:
            return letter
        if "1" <= segment <= "9":
            return 2 + (ord(segment) - ord("1"))
        if segment == "0":
            return 11
    return None


class ClipboardPasteInjector:
    """An :class:`~ges2t.interfaces.Injector` backed by ``wl-copy`` + paste (ticket 25).

    ``paste_keys`` comes from ``[injection.clipboard]`` (ticket 24 owns the
    config plumbing) and defaults to ``["ctrl+shift+v"]`` (ticket 38) -- the
    only chord with manual-verification evidence, and one that pastes in both
    an editor and a terminal. The honest caveat: an application that does not
    bind ``ctrl+shift+v`` pastes **nothing**, so set
    ``paste_keys = ["ctrl+v"]`` there; in most GUI toolkits ``ctrl+shift+v``
    means *paste as plain text*, which for dictation is desirable. Do **not**
    list both chords -- the injector emits every configured chord and would
    paste twice. ``trailing_space`` is the ADR 0012 preference
    (``ui.trailing_space``). ``key_delay_ms`` becomes ``ydotool key -d`` for the
    chord, reusing ticket 23's transport so the chord is layout-safe.
    ``restore_delay_s`` is the bounded delay before the previous selection is put
    back. ``runner`` is the injectable clipboard seam; production leaves it as
    the real ``subprocess`` runner and tests inject a fake so no real clipboard
    is ever written.

    ``last_injection_ms`` mirrors :class:`~ges2t.injectors.ydotool.YdotoolInjector`:
    the wall time of the most recent successful delivery, ``None`` before any
    call, after a skip, and after a failure. ``last_warnings`` carries the
    config warnings ``ClipboardConfig.from_mapping`` produced, so ``ges2t
    doctor`` can surface them instead of silently swallowing them.
    """

    def __init__(
        self,
        *,
        paste_keys: Sequence[str] = ("ctrl+shift+v",),
        trailing_space: bool = True,
        key_delay_ms: int = DEFAULT_KEY_DELAY_MS,
        restore_delay_s: float = CLIPBOARD_RESTORE_DELAY_SECONDS,
        runner: ClipboardRunner | None = None,
        warnings: Sequence[str] = (),
    ) -> None:
        self.paste_keys = list(paste_keys)
        self.trailing_space = trailing_space
        self.key_delay_ms = key_delay_ms
        self.restore_delay_s = restore_delay_s
        self.last_warnings = list(warnings)
        self._chords = tuple(KeyChord.parse(spelling) for spelling in paste_keys)
        self._runner: ClipboardRunner = runner if runner is not None else _run_command
        self.last_injection_ms: float | None = None

    def inject(self, text: str) -> None:
        """Put ``text`` on the clipboard, paste it, and restore the previous selection.

        Whitespace-only text is a no-op that spawns **no** process at all -- no
        read, no write, no paste (ADR 0012: silence in, nothing out). Every other
        outcome either completes or raises a :class:`ClipboardError`.
        """
        decision = decide_injection(text, trailing_space=self.trailing_space)
        if isinstance(decision, SkipInjection):
            self.last_injection_ms = None
            return
        self._deliver(decision.payload)

    def _deliver(self, payload: str) -> None:
        """Run the read -> copy -> paste -> restore sequence for ``payload``."""
        environment = _clipboard_environment()
        started = time.perf_counter()
        previous = self._read_clipboard(environment)
        self._write_payload(payload, environment)
        self._send_paste_chord(environment)
        self.last_injection_ms = (time.perf_counter() - started) * 1000.0
        if previous:
            self._restore_clipboard(previous, environment)

    def _write_payload(self, payload: str, environment: Mapping[str, str]) -> None:
        """Copy ``payload`` to the clipboard, over stdin, never through argv."""
        argv = (WL_COPY_BINARY, *WL_COPY_ARGUMENTS)
        completed = self._run(
            WL_COPY_BINARY, argv, payload.encode("utf-8"), environment
        )
        if completed.returncode != 0:
            raise ClipboardInjectionError(
                WL_COPY_BINARY,
                completed.returncode,
                _decode_stderr(completed.stderr),
            )

    def _read_clipboard(self, environment: Mapping[str, str]) -> bytes | None:
        """Return the current clipboard bytes, tolerating an unreadable selection.

        A clipboard offering an image, or a password manager's sensitive
        selection, cannot be read as text and must not fail the injection -- the
        transcript is what matters. ``None`` means "there was nothing restorable".
        """
        argv = (WL_PASTE_BINARY, *WL_PASTE_ARGUMENTS)
        try:
            completed = self._runner(argv, b"", environment)
        except FileNotFoundError:
            # A missing wl-paste is not fatal: injection needs wl-copy and the
            # chord. It only costs the courtesy of restoring the old selection.
            return None
        except subprocess.TimeoutExpired:
            # D3: a wedged compositor is the same "nothing restorable" case as a
            # missing wl-paste. The read is a courtesy; failing the transcript
            # write and paste because the *previous* selection could not be read
            # would break the injection for the wrong reason. Tolerated here, so
            # the read never escapes as a raw TimeoutExpired; the payload write
            # and the chord still go through the typed timeout path in ``_run``.
            return None
        if completed.returncode != 0 or not completed.stdout:
            return None
        return bytes(completed.stdout)

    def _send_paste_chord(self, environment: Mapping[str, str]) -> None:
        """Emit every configured paste chord as raw keycodes via ``ydotool key``."""
        for chord in self._chords:
            argv = self._build_key_argv(chord)
            completed = self._run(YDOTOOL_BINARY, argv, b"", environment)
            if completed.returncode != 0:
                stderr = _decode_stderr(completed.stderr)
                raise ClipboardInjectionError(
                    YDOTOOL_BINARY, completed.returncode, stderr
                )

    def _build_key_argv(self, chord: KeyChord) -> tuple[str, ...]:
        """Render ``chord`` as ``ydotool key -d <ms> KEYCODE:PRESSED ...``.

        ``KeyEvent.keycode`` is already a Linux/evdev code, so it is rendered
        with **no** offset -- the same ruling ticket 23's emitter follows.
        """
        tokens = tuple(
            f"{event.keycode}:{1 if event.pressed else 0}" for event in chord.events
        )
        return (
            YDOTOOL_BINARY,
            "key",
            KEY_DELAY_FLAG,
            str(self.key_delay_ms),
            *tokens,
        )

    def _restore_clipboard(
        self, previous: bytes, environment: Mapping[str, str]
    ) -> None:
        """Put ``previous`` back after a bounded delay, best effort.

        A failure here never fails the injection -- the transcript was already
        pasted -- so it is reported as a :class:`ClipboardRestoreWarning` and
        swallowed. The delay is delegated to the runner (a detached child in
        production), so waiting never blocks the service's command loop and the
        previous selection can never overwrite the payload before the target
        application has read it.
        """
        argv = (WL_COPY_BINARY, *WL_COPY_ARGUMENTS)
        try:
            completed = self._runner(
                argv, previous, environment, delay_s=self.restore_delay_s
            )
        except (OSError, subprocess.SubprocessError) as exc:
            warnings.warn(
                _restore_warning(f"could not start the restore: {exc}"),
                ClipboardRestoreWarning,
                stacklevel=2,
            )
            return
        if completed.returncode != 0:
            warnings.warn(
                _restore_warning(
                    f"{WL_COPY_BINARY} exited with status {completed.returncode}: "
                    f"{_decode_stderr(completed.stderr) or '<empty>'}"
                ),
                ClipboardRestoreWarning,
                stacklevel=2,
            )

    def _run(
        self,
        binary: str,
        argv: Sequence[str],
        stdin_bytes: bytes,
        environment: Mapping[str, str],
    ) -> subprocess.CompletedProcess[bytes]:
        """Call the runner once, translating a spawn failure into a typed error.

        A missing ``wl-paste`` is tolerated by :meth:`_read_clipboard`, and a
        missing ``wl-copy`` during a *restore* is best effort; only the payload
        write and the chord delivery reach this method's fatal branches.
        """
        try:
            return self._runner(argv, stdin_bytes, environment)
        except FileNotFoundError as exc:
            raise ClipboardNotFoundError(binary) from exc
        except subprocess.TimeoutExpired as exc:
            raise ClipboardTimeoutError(
                binary, CLIPBOARD_COMMAND_TIMEOUT_SECONDS
            ) from exc
        except OSError as exc:
            raise ClipboardSpawnError(binary, str(exc)) from exc


def _restore_warning(detail: str) -> str:
    """Build the best-effort restore warning."""
    return (
        "ges2t: the transcription was pasted, but the previous clipboard "
        f"selection could not be restored ({detail}). The clipboard now holds "
        "the transcription."
    )


def _decode_stderr(stderr: bytes | None) -> str:
    """Decode a child's stderr for inclusion in an error message."""
    if not stderr:
        return ""
    return bytes(stderr).decode("utf-8", errors="replace").strip()
