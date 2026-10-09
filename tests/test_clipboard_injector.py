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

import os
import re
import signal
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from ges2t.injectors import clipboard as clipboard_module
from ges2t.injectors.clipboard import (
    CLIPBOARD_COMMAND_TIMEOUT_SECONDS,
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
    _run_command,
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


def test_restore_thread_start_failure_does_not_fail_injection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R2: a ``Thread.start`` failure is best effort, never an injection failure.

    ``threading.Thread.start`` raises ``RuntimeError`` ("can't start new
    thread") when the process is out of threads. The restore runs *after* the
    payload has been pasted, so that failure must degrade to a
    ``ClipboardRestoreWarning`` rather than escaping ``inject()`` (ticket 17
    shipped the opposite bug by narrowing a restore ``except``). The deferred
    restore is routed through the real ``_run_command``/``_start_deferred_stdin``
    seam -- payload write and chord stay on the fake -- so the real
    ``Thread.start`` is what fails. The restore child is ``true`` (reads no
    stdin, exits on its own), so no process is left behind when the writer
    thread never starts.
    """

    def failing_start(self: threading.Thread) -> None:
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(threading.Thread, "start", failing_start)

    runner = DelegatingRestoreRunner(target=None)
    injector = ClipboardPasteInjector(
        paste_keys=("ctrl+v",),
        trailing_space=True,
        restore_delay_s=0.0,
        runner=runner,
    )

    with pytest.warns(ClipboardRestoreWarning) as record:
        injector.inject("hi")

    assert injector.last_injection_ms is not None
    # The transcript still reached wl-copy (with ADR 0012's trailing space) ...
    payload_calls = [
        call
        for call in runner.calls
        if call.argv[0] == WL_COPY_BINARY and call.delay_s is None
    ]
    assert [call.stdin for call in payload_calls] == [b"hi "]
    # ... and the paste chord was still emitted ...
    assert [call for call in runner.calls if call.argv[0] == YDOTOOL_BINARY] != []
    # ... and the only consequence is a recorded restore warning (a GC'd
    # still-running child may also emit a ResourceWarning, so select ours).
    restore_warnings = [
        item
        for item in record
        if issubclass(item.category, ClipboardRestoreWarning)
    ]
    assert restore_warnings != []
    assert "previous clipboard" in str(restore_warnings[0].message)


# --- the real deferred runner seam: stdin actually reaches the child (D1) -----


class DelegatingRestoreRunner:
    """A fake runner that hands the deferred restore to the *real* seam.

    Every call is recorded and answered synthetically except the deferred
    ``wl-copy`` restore, which is retargeted to the harmless ``tee`` and run by
    the real :func:`_run_command`. The plain fake recorded ``stdin=previous``
    while the real deferred branch dropped it (D1), so this pins the injector ->
    real-seam contract without touching the clipboard.
    """

    def __init__(self, target: Path | None) -> None:
        # ``target is None`` keeps the *real* deferred branch but points it at a
        # command (``true``) that never reads stdin, so the R2 test spawns no
        # child that could block when the writer thread never starts.
        self._target = target
        self._fake = FakeClipboardRunner(paste_stdout=b"previous")
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
        if delay_s is not None and argv[0] == WL_COPY_BINARY:
            destination = (
                ("tee", str(self._target))
                if self._target is not None
                else ("true",)
            )
            return _run_command(destination, stdin_bytes, env, delay_s=delay_s)
        return self._fake(argv, stdin_bytes, env, delay_s=delay_s)


def _wait_for_bytes(target: Path, expected: bytes) -> None:
    """Poll for a detached child to finish writing ``expected`` to ``target``."""
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if target.exists() and target.read_bytes() == expected:
            return
        time.sleep(0.02)


def test_real_deferred_runner_delivers_stdin_bytes(tmp_path: Path) -> None:
    """The REAL ``_run_command`` deferred branch must feed the child's stdin.

    The old runner kept the bytes in a dead assignment and let the ``Popen``
    object go out of scope, so the child read EOF and restored an **empty**
    selection. This exercises the real branch with ``tee`` writing only under
    ``tmp_path`` -- no clipboard, no keystroke -- so the fake and the real seam
    cannot disagree again.
    """
    target = tmp_path / "restore.bin"
    payload = b"PREVIOUS-SELECTION"

    completed = _run_command(("tee", str(target)), payload, dict(os.environ), delay_s=0.05)

    assert completed.returncode == 0
    _wait_for_bytes(target, payload)
    assert target.exists(), "the detached child never wrote its output"
    assert target.read_bytes() == payload


def test_restore_bytes_reach_the_real_deferred_child(tmp_path: Path) -> None:
    """The injector's restore bytes traverse the real deferred runner (D1 E2E)."""
    target = tmp_path / "restore-e2e.bin"
    runner = DelegatingRestoreRunner(target)
    injector = ClipboardPasteInjector(
        paste_keys=("ctrl+v",), trailing_space=True, runner=runner
    )

    injector.inject("hi")

    _wait_for_bytes(target, b"previous")
    assert target.exists()
    assert target.read_bytes() == b"previous"


# --- the real immediate runner seam: a forking child must not stall it (36) ---


def _kill_pid_file(pid_file: Path) -> None:
    """Kill the harmless background ``sleep`` a forking-child test left behind."""
    try:
        pid = int(pid_file.read_text().strip())
    except (OSError, ValueError):
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def test_real_forking_child_does_not_block_the_immediate_path(
    tmp_path: Path,
) -> None:
    """The REAL immediate ``_run_command`` must not wait on a forked child's pipes.

    ``wl-copy`` forks by default and the forked child lives on to serve the
    selection, inheriting whatever stdout/stderr descriptors the runner handed
    it. With ``capture_output=True`` those are **pipes**, and ``subprocess.run``
    reads them to EOF -- which never arrives while the daemon holds a write end,
    so the run blocked for the whole timeout and every successful write was
    reported as a failure before the paste chord (ticket 36). This drives the
    real seam with a harmless ``sh`` stand-in that forks a long-lived ``sleep``
    the way ``wl-copy`` does, writes a marker under ``tmp_path``, emits stderr and
    exits non-zero. Nothing here touches the clipboard. Against the pre-fix
    ``capture_output=True`` the call raises ``subprocess.TimeoutExpired``; the
    fixed redirect completes at once and still captures stderr.
    """
    marker = tmp_path / "forked-child-marker.txt"
    pid_file = tmp_path / "forked-child.pid"
    script = (
        f"sleep 30 & echo $! > {pid_file}; "
        f"printf 'wrote-and-forked' > {marker}; "
        "echo 'no compositor' >&2; exit 7"
    )

    started = time.monotonic()
    try:
        completed = _run_command(("sh", "-c", script), b"", dict(os.environ))
        elapsed = time.monotonic() - started
    finally:
        _kill_pid_file(pid_file)

    assert elapsed < CLIPBOARD_COMMAND_TIMEOUT_SECONDS / 2, (
        "the immediate path waited on a pipe the forked child still holds"
    )
    assert completed.returncode == 7
    assert b"no compositor" in completed.stderr, (
        "stderr must still be captured and reach the typed error message"
    )
    assert marker.read_text() == "wrote-and-forked"


def test_real_immediate_runner_still_captures_stdout() -> None:
    """The fix must not cost stdout: ``wl-paste``'s previous selection needs it."""
    completed = _run_command(("sh", "-c", "printf PREVIOUS"), b"", dict(os.environ))

    assert completed.returncode == 0
    assert completed.stdout == b"PREVIOUS"
    assert completed.stderr == b""


def test_real_immediate_runner_still_bounds_a_wedged_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The file redirect must not remove the bound: a genuine hang still times out."""
    monkeypatch.setattr(
        clipboard_module, "CLIPBOARD_COMMAND_TIMEOUT_SECONDS", 0.2
    )

    with pytest.raises(subprocess.TimeoutExpired) as excinfo:
        _run_command(("sh", "-c", "sleep 30"), b"", dict(os.environ))

    assert excinfo.value.timeout == 0.2


def test_real_immediate_runner_delivers_a_non_empty_payload_to_stdin(
    tmp_path: Path,
) -> None:
    """The REAL immediate ``_run_command`` must feed a non-empty payload to stdin.

    ``_run_immediate`` hands the payload to ``subprocess.run(input=...)``, and
    that is the only route transcript text takes to ``wl-copy``: ADR 0020's rule
    is that text never reaches argv, so nothing but the child's stdin carries
    it. Every other clipboard test substitutes :class:`FakeClipboardRunner`, so
    the fakes answer before the bytes matter and the real seam never sees a
    payload -- mutating ``input=stdin_bytes`` to ``input=b""`` left the whole
    877-test suite green (ticket 36 review section 11, M4). That is the same
    blind spot that hid ticket 25's D1 and ticket 36 itself.

    ``cat`` redirects stdin to a file under ``tmp_path``; no clipboard binary
    and no keystroke is involved, so the file can be compared byte-for-byte. The
    payload is deliberately multi-byte UTF-8, so a text-mode or re-encoding
    defect cannot pass either.
    """
    target = tmp_path / "immediate-stdin.bin"
    payload = "naïve café — 日本語 🎤\n".encode()

    completed = _run_command(
        ("sh", "-c", 'cat > "$1"', "sh", str(target)),
        payload,
        dict(os.environ),
    )

    assert completed.returncode == 0
    assert len(payload) > 0, "the payload under test must be non-empty"
    assert any(byte > 0x7F for byte in payload), "payload must be multi-byte"
    assert target.read_bytes() == payload, "the child did not receive stdin bytes"


def test_real_immediate_runner_captures_a_non_empty_stdout_payload(
    tmp_path: Path,
) -> None:
    """The REAL immediate runner must capture stdout written to a regular file.

    The same immediate path serves ``wl-paste``, whose **stdout** is the previous
    selection the deferred restore later writes back. Ticket 36 replaced the
    stdout pipe with a ``tempfile.TemporaryFile``, so that capture path is now
    "a child writes a regular file, the runner reads it back" and has to be
    pinned end to end, not just for an empty stream.

    A real ``cat`` prints a file under ``tmp_path`` -- a stand-in for the
    previous selection -- and the runner must return those bytes exactly,
    multi-byte included. No clipboard binary and no keystroke is involved.
    """
    source = tmp_path / "previous-selection.bin"
    previous = "old selection — 選択 🎤".encode()
    source.write_bytes(previous)

    completed = _run_command(("cat", str(source)), b"", dict(os.environ))

    assert completed.returncode == 0
    assert len(previous) > 0, "the payload under test must be non-empty"
    assert any(byte > 0x7F for byte in previous), "payload must be multi-byte"
    assert completed.stdout == previous, "stdout bytes were not captured exactly"
    assert completed.stderr == b""


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


def test_wl_paste_timeout_is_tolerated_like_a_missing_binary() -> None:
    """D3: a wedged-compositor read timeout is best effort, not a fatal error.

    The read only enables the courtesy restore. Letting a raw
    ``subprocess.TimeoutExpired`` escape would fail an injection whose payload
    write and paste are still viable, so the timeout is treated exactly like a
    missing ``wl-paste`` (``return None``).
    """
    runner = FakeClipboardRunner(
        raise_for={
            WL_PASTE_BINARY: subprocess.TimeoutExpired(cmd="wl-paste", timeout=5.0)
        }
    )
    injector = _injector(runner)

    # If the timeout escaped untyped this call would raise, failing the test.
    injector.inject("text")

    assert runner.ydotool_calls()[0].argv[4:] == ("29:1", "47:1", "47:0", "29:0")
    assert runner.restore_calls() == []
    assert injector.last_injection_ms is not None


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


#: The real evdev codes of the whole letter block, written out independently of
#: the module under test so a mutation cannot keep this table in sync with the
#: implementation. The block is QWERTYUIOP / ASDFGHJKL / ZXCVBNM, not
#: a..z contiguous.
_EVDEV_LETTER_CODES: Mapping[str, int] = {
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


@pytest.mark.parametrize("letter,code", sorted(_EVDEV_LETTER_CODES.items()))
def test_every_letter_resolves_to_its_real_evdev_code(letter: str, code: int) -> None:
    """D2: the whole 26-letter block, not just the five hardcoded ones."""
    assert KeyChord.parse(f"ctrl+{letter}").keycodes == (KEY_LEFTCTRL, code)


def test_no_letter_silently_resolves_to_a_different_key() -> None:
    """D2's damaging case: a typo'd ctrl+r must never become ctrl+v."""
    assert KeyChord.parse("ctrl+q").keycodes == (29, 16)
    assert KeyChord.parse("ctrl+r").keycodes == (29, 19)
    assert KeyChord.parse("ctrl+r").keycodes != KeyChord.parse("ctrl+v").keycodes


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
