"""Microphone capture through ``pw-record`` (ADR 0008, ADR 0005).

:class:`PwRecordAudioSource` is the concrete
:class:`~ges2t.interfaces.AudioSource`: it spawns ``pw-record``, reads raw
float32 PCM off its stdout on a dedicated thread, and returns exactly the
engine's contract -- **16 kHz, mono, float32 in [-1, 1]** (ADR 0005) -- because
PipeWire itself does the resampling.

Verified invocation
-------------------
Checked against ``pw-record --help`` (and ``--list-formats``) on this machine,
PipeWire/libpipewire **1.6.8**, 2026-10-09::

    pw-record --rate 16000 --channels 1 --format f32 --raw --target <device> -

``--rate``, ``--channels`` and ``--format`` are all real flags on this build and
``f32`` is listed by ``--list-formats``. **``--raw`` is required alongside
them**: without it ``pw-record`` wraps stdout in a container (a 24-byte AU
header was observed before the first sample) and the bytes are not header-less
PCM. With ``--raw``, a ``--sample-count 48000`` run produced exactly 192000
bytes = 48000 float32 samples = 3.000 s at 16 kHz mono, so the four flags plus
the ``-`` stdout argument are the verified invocation. ``--target`` is added
only when a device is configured; the trailing ``-`` means "write to stdout".

Failure modes
-------------
``pw-record`` returns exit status **1 on every termination**, including a clean
SIGTERM stop and a normal ``--sample-count`` end, so the return code cannot be
used as a success signal. What *is* reliable is: a genuine failure (no PipeWire
session, unusable device) exits on its own, **before capturing any sample**, and
prints a diagnostic to stderr. That is the condition this module turns into a
:class:`PwRecordError`; ``stop()`` immediately after ``start()`` with an empty
stderr is a normal zero-sample recording, not an error (ADR 0005, ADR 0012).

Threading
---------
A PipeWire pipe that fills while nobody reads stalls the recorder, so stdout is
read on one dedicated thread from ``start()`` until EOF, and every block is
copied into a queue *and* into a recording-wide byte buffer. ``chunks()`` drains
the queue lazily; ``stop()`` asks the process to finish, joins the reader (which
drains stdout to EOF first), and returns the whole recording built from the byte
buffer. Stderr is tiny for ``pw-record`` and is read once, after stdout EOF,
on the same thread -- no second thread and no pipe-full deadlock.

This module imports no backend, no ``gi`` and no ``numpy``: only the stdlib and
the interface vocabulary (ADR 0010, ADR 0019).
"""

from __future__ import annotations

import queue
import subprocess
import sys
import threading
from array import array
from collections.abc import Callable, Iterator, Sequence
from typing import IO, Protocol

from ges2t.interfaces import AudioBuffer

__all__ = [
    "CHANNELS",
    "PROGRAM",
    "SAMPLE_FORMAT",
    "SAMPLE_RATE",
    "PwRecordAudioSource",
    "PwRecordError",
    "PwRecordProcess",
    "SpawnPwRecord",
]

#: The recorder binary; it lives in the ``pipewire`` package on Arch.
PROGRAM: str = "pw-record"

#: The engine's sample rate (ADR 0005); PipeWire resamples to it for us.
SAMPLE_RATE: int = 16000

#: Mono capture (ADR 0005).
CHANNELS: int = 1

#: The engine's sample format (ADR 0005). ``pw-record --format f32`` emits
#: native-endian IEEE-754 binary32.
SAMPLE_FORMAT: str = "f32"

#: Bytes per float32 sample. Derived from ``array`` rather than hard-coded so
#: the reader, the chunker and :func:`_to_samples` cannot disagree.
_SAMPLE_BYTES: int = array("f").itemsize

#: How much raw stdout to read per blocking read. 4096 bytes is 1024 samples,
#: i.e. 64 ms at 16 kHz -- smaller than PipeWire's default 100 ms quantum, so
#: the first block is delivered as soon as the first quantum arrives.
_READ_BLOCK_BYTES: int = 4096

#: How long ``stop()`` waits for the recorder to exit after ``SIGTERM`` and,
#: if that fails, after ``SIGKILL``. Generous: a clean ``pw-record`` exits in
#: milliseconds.
_JOIN_TIMEOUT_SECONDS: float = 5.0

#: The queue sentinel that marks "stdout reached EOF"; ``None`` cannot be a
#: data block, which is always non-empty ``bytes``.
_EOF: None = None


class PwRecordError(Exception):
    """Capture failed, or the source was used out of order.

    Every message is actionable: it names ``pw-record``, the configured device
    where relevant, and the stderr text the recorder produced. ``doctor`` and
    the state machine print these verbatim (ADR 0008).
    """


class PwRecordProcess(Protocol):
    """The subset of :class:`subprocess.Popen` a capture run needs.

    Declared as a Protocol so tests can inject a fake process with no
    microphone and no subprocess (ADR 0014). ``stdout``/``stderr`` are
    ``None`` only if the caller did not request a pipe; this module always
    requests both.
    """

    stdout: IO[bytes] | None
    stderr: IO[bytes] | None

    def wait(self, timeout: float | None = None) -> int:
        """Block until the process exits and return its status."""
        ...

    def terminate(self) -> None:
        """Ask the process to exit (``SIGTERM``)."""
        ...

    def kill(self) -> None:
        """Force the process to exit (``SIGKILL``)."""
        ...


#: How a source starts a recorder. The default spawns the real binary with an
#: explicit argument list and two pipes; tests pass a fake.
SpawnPwRecord = Callable[[Sequence[str]], PwRecordProcess]


def _spawn_pw_record(argv: Sequence[str]) -> PwRecordProcess:
    """Spawn ``pw-record`` with an explicit argument list, never a shell."""
    return subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


class _Capture:
    """Per-recording mutable state shared by the reader thread and the caller.

    A fresh instance is created by :meth:`PwRecordAudioSource.start` for every
    recording, so a previous recording's generator can never observe the next
    recording's data, and the source is reusable across hotkey presses.
    """

    def __init__(
        self,
        process: PwRecordProcess,
        stdout: IO[bytes],
        stderr: IO[bytes],
        device: str,
    ) -> None:
        self.process = process
        self.stdout = stdout
        self.stderr = stderr
        self.device = device
        self.queue: queue.Queue[bytes | None] = queue.Queue()
        self.captured = bytearray()
        self.stderr_bytes: bytes = b""
        self.returncode: int | None = None
        self.reader_error: BaseException | None = None
        self.failure: PwRecordError | None = None
        #: Set by ``stop()`` before it signals the process. A zero-sample
        #: recording is normal when the caller asked for it and stderr is
        #: empty; otherwise it is a failure.
        self.stop_requested = False
        self.thread: threading.Thread | None = None


def _to_samples(data: bytes) -> array[float]:
    """Convert raw native-endian float32 bytes to samples, dropping any remainder.

    ``array('f')`` is the stdlib's view of the machine's native float32, which
    is what ``pw-record --format f32`` writes. A trailing run shorter than one
    sample (possible only if a pipe delivered an unaligned final block) is
    dropped so ``frombytes`` cannot raise; both :meth:`chunks` and :meth:`stop`
    drop it identically, keeping their sample counts equal.
    """
    usable = len(data) - (len(data) % _SAMPLE_BYTES)
    samples: array[float] = array("f")
    if usable == 0:
        return samples
    samples.frombytes(data[:usable])
    if sys.byteorder == "big":
        # PipeWire writes little-endian f32; array('f') is native.
        samples.byteswap()
    return samples


def _reader_failure(capture: _Capture) -> PwRecordError:
    """Build the error for an I/O failure while reading the recorder's stdout."""
    return PwRecordError(
        f"failed while reading {PROGRAM} stdout after "
        f"{len(capture.captured) // _SAMPLE_BYTES} samples: {capture.reader_error}"
    )


def _decide_failure(capture: _Capture) -> PwRecordError | None:
    """Decide whether EOF before any audio was a failure, or a normal empty stop.

    Returns ``None`` when audio arrived (whatever the exit status -- we keep
    what we captured rather than discarding it), or when a zero-sample recording
    was explicitly stopped and the recorder said nothing on stderr. Otherwise
    the recorder exited on its own before producing audio and we surface its
    stderr, because that is the whole diagnostic value of the failure.
    """
    if capture.reader_error is not None:
        return _reader_failure(capture)
    if capture.captured:
        return None
    stderr_text = capture.stderr_bytes.decode("utf-8", "replace").strip()
    if capture.stop_requested and not stderr_text:
        return None

    status = "unknown" if capture.returncode is None else str(capture.returncode)
    device_hint = (
        f"the configured input device {capture.device!r}"
        if capture.device
        else "the system default input device"
    )
    detail = f"stderr: {stderr_text}" if stderr_text else "stderr was empty"
    return PwRecordError(
        f"{PROGRAM} exited before capturing any audio (exit status {status}); {detail}. "
        "Check that a PipeWire session is running (`pw-cli info 0`) and that "
        f"{device_hint} exists and is not in use (`wpctl status`)."
    )


class PwRecordAudioSource:
    """An :class:`~ges2t.interfaces.AudioSource` backed by ``pw-record``.

    ``device`` is a PipeWire target name or serial, passed as ``--target``;
    the empty string (the default) means the system default input, and no
    ``--target`` argument is emitted. ``spawn`` is a test seam: it defaults to
    spawning the real binary and may be replaced by a fake process factory.
    """

    def __init__(
        self,
        device: str = "",
        *,
        spawn: SpawnPwRecord = _spawn_pw_record,
    ) -> None:
        self._device = device
        self._spawn = spawn
        self._capture: _Capture | None = None

    def start(self) -> None:
        """Spawn ``pw-record`` and begin reading its stdout.

        The recorder is verified to be useful lazily: a missing binary raises
        here, but a recorder that fails after spawning (no session, bad device)
        is only detected once its stdout ends, in :meth:`chunks` or
        :meth:`stop`, because detecting it earlier would mean guessing how long
        a healthy recorder takes to open its device.
        """
        if self._capture is not None:
            raise PwRecordError(
                f"a {PROGRAM} capture is already running: start() was called twice. "
                "Call stop() to finish the current recording before starting the next."
            )

        argv = self._build_argv()
        try:
            process = self._spawn(argv)
        except FileNotFoundError as exc:
            raise PwRecordError(
                f"{PROGRAM!r} was not found on PATH: ges2t captures audio exclusively "
                f"through {PROGRAM}, so install the PipeWire tools (Arch package "
                "'pipewire') and make sure it is on PATH"
            ) from exc
        except OSError as exc:
            raise PwRecordError(f"failed to start {PROGRAM}: {exc}") from exc

        stdout = process.stdout
        stderr = process.stderr
        if stdout is None or stderr is None:
            process.kill()
            raise PwRecordError(
                f"failed to start {PROGRAM}: the capture pipe was not created, so no "
                "audio can be read from it"
            )

        capture = _Capture(process, stdout, stderr, self._device)
        reader = threading.Thread(
            target=self._read_stdout,
            args=(capture,),
            name="pw-record-reader",
            daemon=True,
        )
        capture.thread = reader
        self._capture = capture
        reader.start()

    def chunks(self, chunk_seconds: float) -> Iterator[AudioBuffer]:
        """Yield roughly ``chunk_seconds`` of audio as it arrives.

        Lazy by construction: the generator blocks on the reader thread's queue
        and returns as soon as one chunk has accumulated, without waiting for
        the recording to end. Callers that abandon the iterator early still get
        the unyielded audio from :meth:`stop`, because the full recording is
        buffered independently of this queue.
        """
        capture = self._capture
        if capture is None:
            raise PwRecordError(
                f"chunks() was called before start(): no {PROGRAM} capture is running"
            )
        if chunk_seconds <= 0:
            raise ValueError(
                f"chunk_seconds must be greater than zero, got {chunk_seconds!r}"
            )
        target_samples = max(1, round(chunk_seconds * SAMPLE_RATE))
        return self._iter_chunks(target_samples, capture)

    def stop(self) -> AudioBuffer:
        """End capture, drain stdout to EOF, and return the whole recording.

        The buffer includes audio already yielded by :meth:`chunks`, so the
        batch path never re-reads and never loses a sample. ``pw-record`` is
        stopped with ``SIGTERM`` first -- it is not killed while it still has
        buffered audio to flush -- and the reader is joined, so all bytes the
        recorder wrote are read before the buffer is treated as complete.
        """
        capture = self._capture
        if capture is None:
            raise PwRecordError(
                f"stop() was called before start(), or after this recording was already "
                f"stopped: there is no running {PROGRAM} capture to stop"
            )

        capture.stop_requested = True
        try:
            capture.process.terminate()
        except OSError as exc:
            capture.reader_error = capture.reader_error or exc

        thread = capture.thread
        if thread is not None:
            thread.join(timeout=_JOIN_TIMEOUT_SECONDS)
            if thread.is_alive():
                try:
                    capture.process.kill()
                except OSError as exc:
                    capture.reader_error = capture.reader_error or exc
                thread.join(timeout=_JOIN_TIMEOUT_SECONDS)
            if thread.is_alive():
                raise PwRecordError(
                    f"{PROGRAM} did not exit after SIGTERM and SIGKILL, so its stdout "
                    "reader is still blocked and this recording cannot be finalised"
                )

        self._capture = None

        if capture.failure is not None:
            raise capture.failure
        if capture.reader_error is not None:
            raise _reader_failure(capture) from capture.reader_error
        return AudioBuffer(
            samples=_to_samples(bytes(capture.captured)),
            sample_rate=SAMPLE_RATE,
        )

    def _build_argv(self) -> list[str]:
        """Build the verified ``pw-record`` argument list (see the module docstring)."""
        argv = [
            PROGRAM,
            "--rate",
            str(SAMPLE_RATE),
            "--channels",
            str(CHANNELS),
            "--format",
            SAMPLE_FORMAT,
            "--raw",
        ]
        if self._device:
            argv.extend(["--target", self._device])
        argv.append("-")
        return argv

    def _read_stdout(self, capture: _Capture) -> None:
        """Reader-thread body: copy stdout into the queue and the recording buffer.

        Runs until stdout reaches EOF, then reads stderr and reaps the process
        (both are complete once stdout has closed, and ``pw-record``'s stderr is
        far smaller than a pipe buffer, so there is no fill-and-block hazard).
        The ``EOF`` sentinel is always queued, even on an unexpected error, so a
        consumer blocked in :meth:`chunks` can never hang.
        """
        try:
            try:
                while True:
                    block = capture.stdout.read(_READ_BLOCK_BYTES)
                    if not block:
                        break
                    capture.captured.extend(block)
                    capture.queue.put(block)
            except (OSError, ValueError) as exc:
                capture.reader_error = exc
        finally:
            try:
                capture.stderr_bytes = capture.stderr.read()
            except (OSError, ValueError) as exc:
                capture.reader_error = capture.reader_error or exc
            try:
                capture.returncode = capture.process.wait()
            except OSError as exc:
                capture.reader_error = capture.reader_error or exc
            capture.failure = _decide_failure(capture)
            capture.queue.put(_EOF)

    def _iter_chunks(
        self, target_samples: int, capture: _Capture
    ) -> Iterator[AudioBuffer]:
        """Yield fixed-size chunks from ``capture``'s queue, then any remainder."""
        target_bytes = target_samples * _SAMPLE_BYTES
        pending = bytearray()
        while True:
            block = capture.queue.get()
            if block is _EOF:
                break
            pending.extend(block)
            while len(pending) >= target_bytes:
                yield AudioBuffer(
                    samples=_to_samples(bytes(pending[:target_bytes])),
                    sample_rate=SAMPLE_RATE,
                )
                del pending[:target_bytes]

        if capture.failure is not None:
            raise capture.failure
        if len(pending) >= _SAMPLE_BYTES:
            yield AudioBuffer(
                samples=_to_samples(bytes(pending)),
                sample_rate=SAMPLE_RATE,
            )
