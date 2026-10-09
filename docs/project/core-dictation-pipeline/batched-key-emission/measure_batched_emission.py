#!/usr/bin/env python3
"""Authorised, bounded batched-key-emission latency measurement (ticket 23).

**This script injects real keystrokes into whatever window has focus.** It exists
only for the ticket-23 measurement, which requires the user/orchestrator's
explicit authorisation. It refuses to run without ``--authorised``.

It opens **exactly one** scratch ``konsole`` window and makes **exactly one**
attempt (a sequence of delay values in that one window; no retries). Before
every injection it **positively confirms** that the scratch window is the KWin
active window -- by loading a temporary KWin declarative script that reports
``workspace.activeWindow`` -- and aborts without injecting if focus cannot be
confirmed.

The reader is a foreground ``head -c <n>`` (canonical mode off), never a
``timeout cat``: ``timeout`` puts the reader in its own process group, where a
read of the controlling tty is stopped by ``SIGTTIN`` and captures nothing.
``head -c`` reads exactly the payload length as the pty's foreground process and
then exits, so the window closes itself. The controller enforces the hard
30-second budget by killing the window if it lingers.

The payload is pre-flighted through the *real* mapping before anything is
injected and the script aborts if it contains Return, Escape, Tab, Control or
Meta. It never types a newline.

After the user has authorised the injection::

    cd /home/matee/Workspace/Personal/ges2t/.worktrees/batched-key-emission
    uv run python docs/project/core-dictation-pipeline/batched-key-emission/measure_batched_emission.py --authorised

``--self-test`` runs the same reader and focus mechanisms with **no GUI window
and no injection at all** (a Python-allocated pty and the KWin focus probe), so
the harness can be trusted before the one authorised attempt.
"""

from __future__ import annotations

import json
import os
import pty
import re
import secrets
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import FrameType

from ges2t.injectors.keymap import KeyEvent, KeySequence
from ges2t.injectors.ydotool import XkbKeySequenceSource, YdotoolInjector

#: ~125 characters / 20+ words -- the "<=20-word phrase" of ADR 0001.
PHRASE: str = (
    "the quick brown fox jumps over the lazy dog then quietly disappears "
    "into the very deep dark forest and never comes back again"
)

#: Delay values to try, in ms, in order. The default ydotool delay is 12; the
#: report names the lowest one that loses no characters.
DELAYS_MS: tuple[int, ...] = (12, 6, 0)

CAPTURE_DIR: Path = Path("/tmp/ges2t_measure_batched_emission")
SCRIPT_DIR: Path = Path("/tmp/ges2t_measure_kwin_scripts")
ATTEMPT_BUDGET_S: int = 30
PER_DELAY_CAPTURE_TIMEOUT_S: float = 10.0
READY_TIMEOUT_S: float = 8.0
FOCUS_PROBE_TIMEOUT_S: float = 5.0
FLUSH_SETTLE_S: float = 0.4

#: Keycodes that must never appear in the payload: Escape, Tab, Return,
#: Left/Right Control, Left/Right Meta. (Shift and RightAlt are legitimate
#: mapping output for `hu`.)
FORBIDDEN_KEYCODES: frozenset[int] = frozenset({1, 15, 28, 29, 97, 125, 126})

BUDGET_MS: float = 1000.0
KWIN_BUS_NAME: str = "org.kde.KWin"
KWIN_SCRIPTING_PATH: str = "/Scripting"
KWIN_SCRIPTING_INTERFACE: str = "org.kde.kwin.Scripting"


class _BudgetExceeded(Exception):
    """The hard per-attempt wall-clock budget was exceeded."""


@dataclass(frozen=True)
class FocusObservation:
    """What the KWin script saw for the scratch window and the active window."""

    target: str
    active: str
    match: bool

    @property
    def active_pid(self) -> int | None:
        """The active window's pid, parsed from ``class|pid|caption``."""
        parts = self.active.split("|")
        if len(parts) < 2 or not parts[1].isdigit():
            return None
        return int(parts[1])


@dataclass(frozen=True)
class Attempt:
    """The outcome of one timed injection at one delay value."""

    delay_ms: int
    wall_ms: float
    per_char_ms: float
    captured: bytes
    expected: bytes
    focus: FocusObservation | None

    @property
    def byte_perfect(self) -> bool:
        """Whether every expected byte (and nothing else) was captured."""
        return self.captured == self.expected


def _alarm_handler(signum: int, frame: FrameType | None) -> None:
    del signum, frame
    raise _BudgetExceeded()


def _expected_payload() -> bytes:
    """The exact bytes the injector should type (phrase plus one trailing space)."""
    return (PHRASE + " ").encode("utf-8")


def _check_prerequisites() -> None:
    """Refuse to run when the scratch terminal, KWin or the daemon is unavailable."""
    for binary in ("konsole", "busctl", "journalctl"):
        if _which(binary) is None:
            raise SystemExit(f"{binary} is required for the measurement but is not on PATH.")
    if not Path("/run/user/%d" % os.getuid()).exists():
        raise SystemExit("no XDG runtime directory; is this a Wayland session?")
    socket = _ydotool_socket()
    if not socket.exists():
        raise SystemExit(
            f"the ydotoold socket {socket} does not exist; start it with "
            "'systemctl --user enable --now ydotool' before measuring."
        )


def _which(binary: str) -> str | None:
    """Locate ``binary`` on ``PATH``."""
    from shutil import which

    return which(binary)


def _ydotool_socket() -> Path:
    """The daemon socket ydotool will use (env override or the default)."""
    override = os.environ.get("YDOTOOL_SOCKET")
    if override:
        return Path(override)
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return Path(runtime) / ".ydotool_socket"


def _preflight() -> KeySequence:
    """Map the payload on the live layout and refuse to inject anything unsafe."""
    source = XkbKeySequenceSource("auto")
    sequence = source.sequence_for(PHRASE + " ")
    if sequence.unreachable:
        raise SystemExit(
            "the live layout cannot type: "
            + ", ".join(repr(char) for char in sequence.unreachable)
            + "; the measurement payload must be fully typeable."
        )
    forbidden = sorted({event.keycode for event in sequence.events} & FORBIDDEN_KEYCODES)
    if forbidden:
        raise SystemExit(
            "the pre-flight mapping contains forbidden keycodes "
            f"{forbidden} (Escape/Tab/Return/Control/Meta); refusing to inject."
        )
    _assert_balanced(sequence.events)
    return sequence


def _assert_balanced(events: tuple[KeyEvent, ...]) -> None:
    """Assert stack discipline on the pre-flight mapping (no held key survives)."""
    held: dict[int, int] = {}
    for event in events:
        held[event.keycode] = held.get(event.keycode, 0) + (1 if event.pressed else -1)
        if held[event.keycode] not in (0, 1):
            raise SystemExit(
                f"pre-flight mapping is not press/release balanced at key {event.keycode}"
            )
    if any(count != 0 for count in held.values()):
        raise SystemExit(f"pre-flight mapping leaves keys held: {held}")


# --- KWin focus verification --------------------------------------------------


def _busctl_call(method: str, *args: str) -> str:
    """Call one ``org.kde.kwin.Scripting`` method and return its stdout."""
    result = subprocess.run(
        [
            "busctl",
            "--user",
            "call",
            KWIN_BUS_NAME,
            KWIN_SCRIPTING_PATH,
            KWIN_SCRIPTING_INTERFACE,
            method,
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=5.0,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"busctl {method} failed")
    return result.stdout.strip()


def _focus_script_source(nonce: str) -> str:
    """The KWin JS that reports the nonce window and whether it is active."""
    title = f"ges2t-measurement-{nonce}"
    return (
        "var nonce = " + json.dumps(title) + ";\n"
        "var wins = workspace.windowList ? workspace.windowList() : workspace.clientList();\n"
        "var target = null;\n"
        "for (var i = 0; i < wins.length; i++) {\n"
        "    if (wins[i].caption && wins[i].caption.indexOf(nonce) !== -1) { target = wins[i]; }\n"
        "}\n"
        "var active = workspace.activeWindow;\n"
        "var t = target ? (target.resourceClass + '|' + target.pid + '|' + target.caption) : 'none';\n"
        "var a = active ? (active.resourceClass + '|' + active.pid + '|' + active.caption) : 'none';\n"
        "print('GES2T_FOCUS_' + nonce + ' target=' + t + ' active=' + a + "
        "' match=' + ((target && active && target.pid === active.pid) ? 'yes' : 'no'));\n"
    )


def _observe_focus(nonce: str) -> FocusObservation | None:
    """Load a temporary KWin script and read the focused-window report.

    Returns ``None`` when the report could not be obtained at all (which must be
    treated as "not focused" and abort the injection).
    """
    SCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    script = SCRIPT_DIR / f"focus_{nonce}.js"
    title = f"ges2t-measurement-{nonce}"
    marker = f"GES2T_FOCUS_{title} "
    pattern = re.compile(
        re.escape(marker) + r"target=(.*?) active=(.*?) match=(yes|no)\s*$"
    )
    script.write_text(_focus_script_source(nonce), encoding="utf-8")
    try:
        _busctl_call("loadScript", "s", str(script))
        _busctl_call("start")
        deadline = time.monotonic() + FOCUS_PROBE_TIMEOUT_S
        while time.monotonic() < deadline:
            journal = subprocess.run(
                [
                    "journalctl",
                    "--user",
                    "-u",
                    "plasma-kwin_wayland.service",
                    "--since",
                    "1 minute ago",
                    "--no-pager",
                ],
                capture_output=True,
                text=True,
                check=False,
            ).stdout
            for line in journal.splitlines():
                match = pattern.search(line)
                if match is not None:
                    return FocusObservation(
                        target=match.group(1).strip(),
                        active=match.group(2).strip(),
                        match=match.group(3) == "yes",
                    )
            time.sleep(0.2)
        return None
    finally:
        try:
            _busctl_call("unloadScript", "s", str(script))
        except RuntimeError:
            pass
        script.unlink(missing_ok=True)


def _focus_confirmed(observation: FocusObservation | None, scratch_pid: int) -> bool:
    """Whether the report positively proves the scratch window holds focus."""
    if observation is None:
        return False
    if observation.match:
        return True
    # Fallback: the active window is the konsole process we launched.
    return observation.active_pid is not None and observation.active_pid == scratch_pid


# --- scratch window and capture ----------------------------------------------


def _scratch_command(nonce: str, payload_len: int, captures: dict[int, Path]) -> str:
    """The shell run inside the one scratch window, one ``head -c`` per delay.

    ``head`` is a plain foreground child (no ``timeout`` wrapper), so it is in
    the pty's foreground process group and actually reads the injected bytes.
    Each ``head`` exits once it has read the payload length, which lets the next
    one start; the last exit closes the window.
    """
    title = f"ges2t-measurement-{nonce}"
    lines = [
        "stty -icanon min 1 time 0",
        f"printf '\\033]0;{title}\\007'",
    ]
    lines.extend(f"head -c {payload_len} > {captures[delay]}" for delay in DELAYS_MS)
    return "; ".join(lines)


def _open_scratch(nonce: str, payload_len: int, captures: dict[int, Path]) -> subprocess.Popen[bytes]:
    """Open the one scratch window, in its own process so its pid is the window pid."""
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    for path in captures.values():
        path.unlink(missing_ok=True)
    return subprocess.Popen(
        [
            "konsole",
            "--nofork",
            "--hide-menubar",
            "--hide-tabbar",
            "-p",
            f"tabtitle=ges2t-measurement-{nonce}",
            "-e",
            "bash",
            "-c",
            _scratch_command(nonce, payload_len, captures),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _close_scratch(process: subprocess.Popen[bytes]) -> None:
    """Close the scratch window, killing it if it lingers."""
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3.0)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            print("warning: the scratch konsole did not exit", file=sys.stderr)


def _wait_for_file(path: Path, timeout_s: float) -> bool:
    """Wait until ``path`` exists (the shell reached that ``head``)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.05)
    return False


def _wait_for_size(path: Path, size: int, timeout_s: float) -> bool:
    """Wait until ``path`` holds at least ``size`` bytes."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if path.exists() and path.stat().st_size >= size:
            return True
        time.sleep(0.05)
    return False


# --- the measurement ----------------------------------------------------------


def _measure() -> list[Attempt]:
    """Open one window and time the payload at each delay, verifying focus each time."""
    payload_len = len(_expected_payload())
    expected = _expected_payload()
    nonce = secrets.token_hex(4)
    captures = {delay: CAPTURE_DIR / f"capture_d{delay}.txt" for delay in DELAYS_MS}
    attempts: list[Attempt] = []
    process = _open_scratch(nonce, payload_len, captures)
    signal.signal(signal.SIGALRM, _alarm_handler)
    signal.alarm(ATTEMPT_BUDGET_S)
    try:
        for delay in DELAYS_MS:
            capture = captures[delay]
            if not _wait_for_file(capture, READY_TIMEOUT_S):
                print(
                    f"delay {delay}: the scratch reader never started; stopping.",
                    file=sys.stderr,
                )
                break
            time.sleep(FLUSH_SETTLE_S)
            focus = _observe_focus(nonce)
            if not _focus_confirmed(focus, process.pid):
                print(
                    "FOCUS NOT CONFIRMED -- injecting nothing. "
                    f"observed active window: {focus.active if focus else '<no report>'}; "
                    f"scratch pid {process.pid}.",
                    file=sys.stderr,
                )
                break
            print(
                f"delay {delay}: focus confirmed (active={focus.active if focus else '?'})"
            )
            injector = YdotoolInjector(
                XkbKeySequenceSource("auto"),
                trailing_space=True,
                key_delay_ms=delay,
            )
            started = time.perf_counter()
            injector.inject(PHRASE)
            wall_ms = (time.perf_counter() - started) * 1000.0
            complete = _wait_for_size(capture, payload_len, PER_DELAY_CAPTURE_TIMEOUT_S)
            captured = capture.read_bytes() if capture.exists() else b""
            attempts.append(
                Attempt(
                    delay_ms=delay,
                    wall_ms=wall_ms,
                    per_char_ms=wall_ms / len(PHRASE),
                    captured=captured,
                    expected=expected,
                    focus=focus,
                )
            )
            if not complete:
                print(
                    f"delay {delay}: capture never reached {payload_len} bytes "
                    "(characters dropped); stopping the attempt.",
                    file=sys.stderr,
                )
                break
    except _BudgetExceeded:
        print("the 30-second attempt budget was exceeded; stopping.", file=sys.stderr)
    finally:
        signal.alarm(0)
        _close_scratch(process)
    return attempts


def _hexdump(label: str, data: bytes) -> None:
    """Print a hexdump plus the decoded text for one buffer."""
    print(f"  {label} ({len(data)} bytes):")
    if not data:
        print("    <empty>")
        return
    for offset in range(0, len(data), 16):
        chunk = data[offset : offset + 16]
        hex_part = " ".join(f"{byte:02x}" for byte in chunk)
        text_part = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in chunk)
        print(f"    {offset:06x}  {hex_part:<47}  {text_part}")


def _report(attempts: list[Attempt]) -> int:
    """Print the numbers, byte-perfect verdict, and the ADR 0001 budget verdict."""
    print()
    print("phrase:", repr(PHRASE))
    print(f"expected payload: {len(_expected_payload())} bytes (phrase + trailing space)")
    if not attempts:
        print("RESULT: no injection completed; the measurement is inconclusive.")
        return 2
    print()
    print(f"{'delay ms':>8}  {'wall ms':>9}  {'ms/char':>8}  {'bytes':>6}  byte-perfect")
    for attempt in attempts:
        print(
            f"{attempt.delay_ms:>8}  {attempt.wall_ms:>9.1f}  "
            f"{attempt.per_char_ms:>8.2f}  {len(attempt.captured):>6}  "
            f"{'YES' if attempt.byte_perfect else 'NO'}"
        )
    print()
    for attempt in attempts:
        print(f"--- delay {attempt.delay_ms} ms (focus={attempt.focus.active if attempt.focus else '?'}) ---")
        _hexdump("captured", attempt.captured)
        if not attempt.byte_perfect:
            _hexdump("expected", attempt.expected)
            print("  MISMATCH: characters were dropped or altered; do not flatter this number.")
    clean = [attempt for attempt in attempts if attempt.byte_perfect]
    print()
    if not clean:
        print("RESULT: no delay produced a byte-perfect capture; the measurement is")
        print("        inconclusive and the budget question is unanswered.")
        return 2
    best = min(clean, key=lambda attempt: attempt.delay_ms)
    print(
        f"RESULT: lowest clean delay = {best.delay_ms} ms; "
        f"wall {best.wall_ms:.1f} ms ({best.per_char_ms:.2f} ms/char), byte-perfect YES."
    )
    if best.wall_ms < BUDGET_MS:
        print(
            f"BUDGET: {best.wall_ms:.1f} ms < {BUDGET_MS:.0f} ms -- "
            "MEETS ADR 0001's sub-1-second framework budget."
        )
        return 0
    print(
        f"BUDGET: {best.wall_ms:.1f} ms >= {BUDGET_MS:.0f} ms -- "
        "DOES NOT MEET ADR 0001's sub-1-second framework budget."
    )
    print(
        "        Report this number; do not lower the delay until characters drop. "
        "The remaining option is typing committed text during dictation, which the "
        "orchestrator must decide (ADR 0011)."
    )
    return 1


# --- no-injection self-test ---------------------------------------------------


def _reader_self_test() -> bool:
    """Prove in a plain pty that a foreground ``head -c`` reads and ``timeout cat`` does not."""
    payload = b"the quick brown fox!"
    capture = CAPTURE_DIR / "reader_selftest.txt"
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)

    def run(command: str) -> bytes:
        capture.unlink(missing_ok=True)
        pid, master = pty.fork()
        if pid == 0:
            os.execvp("bash", ["bash", "-c", command])
        time.sleep(0.4)
        os.write(master, payload)
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            waited, _ = os.waitpid(pid, os.WNOHANG)
            if waited == pid:
                break
            if capture.exists() and capture.stat().st_size >= len(payload):
                time.sleep(0.2)
                break
            time.sleep(0.05)
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
        os.close(master)
        return capture.read_bytes() if capture.exists() else b""

    fixed = run(f"stty -icanon min 1 time 0; head -c {len(payload)} > {capture}")
    buggy = run(f"stty -icanon min 1 time 0; timeout 2 cat > {capture}")
    print(f"reader self-test: head -c captured {fixed!r} ({'MATCH' if fixed == payload else 'MISMATCH'})")
    print(f"reader self-test: timeout cat captured {buggy!r} ({len(buggy)} bytes; expected the SIGTTIN bug)")
    return fixed == payload


def _focus_self_test() -> bool:
    """Prove the KWin focus probe reports the currently active window."""
    nonce = secrets.token_hex(4)
    observation = _observe_focus(nonce)
    if observation is None:
        print("focus self-test: no report from the KWin script (FAIL)")
        return False
    print(f"focus self-test: active window = {observation.active}")
    return True


def _self_test() -> int:
    """Run the harness checks that open no window and inject nothing."""
    print("=== ticket 23 measurement harness self-test (no injection, no window) ===")
    reader_ok = _reader_self_test()
    focus_ok = _focus_self_test()
    print(f"self-test: reader={'OK' if reader_ok else 'FAIL'} focus={'OK' if focus_ok else 'FAIL'}")
    return 0 if reader_ok and focus_ok else 1


def main(argv: list[str]) -> int:
    """Dispatch: ``--self-test``, or ``--authorised`` for the one real attempt."""
    if "--self-test" in argv:
        return _self_test()
    if "--authorised" not in argv:
        print(
            "Refusing to inject: pass --authorised only after the user has "
            "explicitly authorised the ticket-23 measurement.\n"
            "This injects real keystrokes into the focused window. Use "
            "--self-test for the no-injection harness check.",
            file=sys.stderr,
        )
        return 2
    _check_prerequisites()
    sequence = _preflight()
    print(
        f"pre-flight OK: {len(sequence.events)} events, "
        f"{len(sequence.unreachable)} unreachable characters."
    )
    print("One scratch konsole window will open and briefly take focus. Do not touch anything.")
    attempts = _measure()
    return _report(attempts)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
