"""Text injection through the ``ydotool`` virtual keyboard (ADR 0003).

Two rules live here and nowhere else:

* **Text assembly** (ADR 0012) -- :func:`decide_injection` is the single
  implementation of the trailing-space table: empty or whitespace-only text is
  never typed at all, non-empty text is typed with exactly one appended ASCII
  space when ``trailing_space`` is true, and is otherwise passed through
  byte-for-byte. There is no stripping, case folding, or internal whitespace
  collapsing.
* **Delivery** -- :class:`YdotoolInjector` spawns ``ydotool`` once per call,
  hands it the payload on **stdin**, and turns every failure mode into a typed
  exception with an actionable message. There is no clipboard or ``wtype``
  fallback and no retry with a different mechanism: a broken injector fails
  loudly (ADR 0003).

Verified invocation
-------------------
``ydotool --help`` lists the ``type`` command along with ``click``, ``key``,
``mousemove``, ``debug``, and ``bakers``. ``man ydotool`` (2024-07-14) documents
the subcommand as::

    type [-D,--next-delay <ms>] [-d,--key-delay <ms>] [-f,--file <filepath>]
    "text"

with "The filepath may also be '-' to read from stdin." The invocation this
module actually uses is therefore::

    ydotool type --file -

and the payload is written to the child's stdin, never passed as an argv
element, so a long transcript cannot hit ``ARG_MAX`` and does not appear in the
process table. ``YDOTOOL_SOCKET`` is inherited from the environment when set --
that is how ``ydotool`` itself selects a non-default daemon socket -- and the
daemon default is used otherwise. The executable is resolved from ``PATH``.
"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

__all__ = [
    "CommandRunner",
    "InjectionDecision",
    "SkipInjection",
    "TYPE_ARGUMENTS",
    "TypeText",
    "YDOTOOL_BINARY",
    "YdotoolDaemonUnreachableError",
    "YdotoolError",
    "YdotoolInjectionError",
    "YdotoolInjector",
    "YdotoolNotFoundError",
    "decide_injection",
]

#: Executable looked up on ``PATH``. Never an absolute path: the user's
#: environment decides which ``ydotool`` runs.
YDOTOOL_BINARY: str = "ydotool"

#: Arguments after the executable. ``--file -`` is the stdin form verified
#: against ``man ydotool`` (see the module docstring).
TYPE_ARGUMENTS: tuple[str, ...] = ("type", "--file", "-")

#: The runner seam. It is deliberately tiny -- ``argv``, the bytes for the
#: child's stdin, and the environment -- so a test double is a few lines and
#: never touches a real daemon (ADR 0014). The default is
#: :func:`_run_command`.
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


@dataclass(frozen=True)
class TypeText:
    """The exact string to hand to the daemon on stdin (ADR 0012)."""

    payload: str


@dataclass(frozen=True)
class SkipInjection:
    """Nothing is typed; the transcription was empty or whitespace-only (ADR 0012)."""

    reason: str


#: The result of text assembly: either something to type or an explicit
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


class YdotoolInjector:
    """An :class:`~ges2t.interfaces.Injector` backed by ``ydotool`` (ADR 0003).

    ``trailing_space`` is the ADR 0012 preference (``ui.trailing_space``,
    default true). ``runner`` is the injectable seam: production leaves it as
    :func:`_run_command`, tests pass a fake so no daemon or ``/dev/uinput``
    access is needed. ``last_injection_ms`` records the wall time of the most
    recent non-skipped delivery -- the per-call cost ADR 0003 requires be
    measurable -- and is ``None`` before any such call or after a skip.
    """

    def __init__(
        self,
        trailing_space: bool = True,
        runner: CommandRunner | None = None,
    ) -> None:
        self.trailing_space = trailing_space
        self._runner: CommandRunner = runner if runner is not None else _run_command
        self.last_injection_ms: float | None = None

    def inject(self, text: str) -> None:
        """Type ``text`` into the focused window and return once delivered.

        Whitespace-only text is a no-op that spawns no process (ADR 0012);
        every other outcome either completes or raises a :class:`YdotoolError`.
        """
        decision = decide_injection(text, trailing_space=self.trailing_space)
        if isinstance(decision, SkipInjection):
            self.last_injection_ms = None
            return
        self._deliver(decision.payload)

    def _deliver(self, payload: str) -> None:
        """Run ``ydotool type --file -`` once with ``payload`` on stdin."""
        argv: tuple[str, ...] = (YDOTOOL_BINARY, *TYPE_ARGUMENTS)
        started = time.perf_counter()
        try:
            completed = self._runner(
                argv, payload.encode("utf-8"), _subprocess_environment()
            )
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
