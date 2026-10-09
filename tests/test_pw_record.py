"""Tests for :mod:`ges2t.audio.pw_record` (ADR 0008).

These tests need **no microphone and no ``pw-record``**: the source's ``spawn``
seam is given a fake process whose stdout is a controllable sequence of blocks
and whose stderr, exit status, ``terminate()`` and ``wait()`` are scripted. That
covers the whole reader-thread / chunking / drain-on-stop machinery, and every
error branch, deterministically.
"""

from __future__ import annotations

import io
import threading
from array import array
from collections.abc import Callable, Iterator, Sequence
from typing import IO, cast

import pytest

from ges2t.audio.pw_record import (
    CHANNELS,
    PROGRAM,
    SAMPLE_FORMAT,
    SAMPLE_RATE,
    PwRecordAudioSource,
    PwRecordError,
    PwRecordProcess,
)
from ges2t.interfaces import AudioSource

#: ``pw-record``'s clean-but-nonzero exit status; a real SIGTERM stop returns
#: exactly this, so the tests must not mistake it for a failure.
PW_RECORD_STOP_STATUS = 1


# --- test doubles -------------------------------------------------------------


class FakeStdout:
    """A scripted stdout stand-in.

    ``blocks`` are returned in order; afterwards the stream either blocks
    forever (``block_at_end=True``, mimicking a live recorder) or reports EOF
    immediately (``block_at_end=False``, mimicking a recorder that has already
    exited). ``error`` makes reads raise after the blocks run out.
    """

    def __init__(
        self,
        blocks: Sequence[bytes] = (),
        *,
        block_at_end: bool = True,
        error: Exception | None = None,
    ) -> None:
        self._blocks = list(blocks)
        self._block_at_end = block_at_end
        self._error = error
        self._released = threading.Event()
        self.read_calls = 0

    def read(self, size: int = -1) -> bytes:
        self.read_calls += 1
        if self._blocks:
            return self._blocks.pop(0)
        if self._error is not None:
            raise self._error
        if self._block_at_end:
            self._released.wait()
        return b""

    def release(self) -> None:
        """Unblock a pending :meth:`read` and let it report EOF."""
        self._released.set()


class FakeProcess:
    """A fake :class:`~ges2t.audio.pw_record.PwRecordProcess`."""

    def __init__(
        self,
        stdout: FakeStdout | None = None,
        *,
        stderr: bytes = b"",
        returncode: int = 0,
    ) -> None:
        self._fake_stdout = stdout if stdout is not None else FakeStdout()
        self.stdout: IO[bytes] | None = cast(IO[bytes], self._fake_stdout)
        self.stderr: IO[bytes] | None = io.BytesIO(stderr)
        self._returncode = returncode
        self.terminate_calls = 0
        self.kill_calls = 0
        self.wait_calls = 0

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls += 1
        return self._returncode

    def terminate(self) -> None:
        self.terminate_calls += 1
        self._fake_stdout.release()

    def kill(self) -> None:
        self.kill_calls += 1
        self._fake_stdout.release()


class SpawnRecorder:
    """A ``spawn`` replacement that records the argv it was handed."""

    def __init__(self, factory: Callable[[], PwRecordProcess]) -> None:
        self.argv: list[str] | None = None
        self.call_count = 0
        self._factory = factory

    def __call__(self, argv: Sequence[str]) -> PwRecordProcess:
        self.call_count += 1
        self.argv = list(argv)
        return self._factory()


def _pcm(samples: Sequence[float]) -> bytes:
    """Encode float32 samples exactly as ``pw-record --format f32`` would."""
    return array("f", samples).tobytes()


def _constant_samples(count: int, value: float = 0.5) -> list[float]:
    return [value] * count


def _source_with(process: FakeProcess) -> tuple[PwRecordAudioSource, SpawnRecorder]:
    recorder = SpawnRecorder(lambda: process)
    return PwRecordAudioSource(spawn=recorder), recorder


# --- verified argv ------------------------------------------------------------


def test_default_argv_is_the_verified_pw_record_invocation() -> None:
    process = FakeProcess()
    source, recorder = _source_with(process)
    source.start()
    try:
        assert recorder.argv == [
            PROGRAM,
            "--rate",
            "16000",
            "--channels",
            "1",
            "--format",
            "f32",
            "--raw",
            "-",
        ]
        assert recorder.call_count == 1
    finally:
        source.stop()


def test_required_flags_are_exactly_the_adr_contract() -> None:
    process = FakeProcess()
    source, recorder = _source_with(process)
    source.start()
    try:
        assert recorder.argv is not None
        assert recorder.argv[recorder.argv.index("--rate") + 1] == str(SAMPLE_RATE)
        assert recorder.argv[recorder.argv.index("--channels") + 1] == str(CHANNELS)
        assert recorder.argv[recorder.argv.index("--format") + 1] == SAMPLE_FORMAT
        assert "--raw" in recorder.argv
    finally:
        source.stop()


def test_configured_device_becomes_target() -> None:
    process = FakeProcess()
    recorder = SpawnRecorder(lambda: process)
    source = PwRecordAudioSource(device="alsa_input.usb-mic", spawn=recorder)
    source.start()
    try:
        assert recorder.argv is not None
        assert recorder.argv[recorder.argv.index("--target") + 1] == "alsa_input.usb-mic"
        assert recorder.argv[-1] == "-"
    finally:
        source.stop()


def test_empty_device_emits_no_target_argument() -> None:
    process = FakeProcess()
    source, recorder = _source_with(process)
    source.start()
    try:
        assert recorder.argv is not None
        assert "--target" not in recorder.argv
    finally:
        source.stop()


def test_argv_is_an_explicit_list_not_a_shell_string() -> None:
    process = FakeProcess()
    source, recorder = _source_with(process)
    source.start()
    try:
        assert isinstance(recorder.argv, list)
        assert all(isinstance(argument, str) for argument in recorder.argv)
    finally:
        source.stop()


# --- chunking and laziness ----------------------------------------------------


def test_chunk_conversion_yields_the_expected_sample_counts() -> None:
    blocks = [_pcm(_constant_samples(SAMPLE_RATE // 10)) for _ in range(10)]
    process = FakeProcess(FakeStdout(blocks, block_at_end=False))
    source, _ = _source_with(process)
    source.start()

    chunks = list(source.chunks(0.1))

    assert [len(chunk.samples) for chunk in chunks] == [1600] * 10
    assert all(chunk.sample_rate == SAMPLE_RATE for chunk in chunks)
    assert chunks[0].samples[0] == pytest.approx(0.5)


def test_final_short_remainder_is_still_yielded() -> None:
    total_samples = SAMPLE_RATE + 300
    process = FakeProcess(
        FakeStdout([_pcm(_constant_samples(total_samples))], block_at_end=False)
    )
    source, _ = _source_with(process)
    source.start()

    lengths = [len(chunk.samples) for chunk in source.chunks(1.0)]
    buffer = source.stop()

    assert lengths == [SAMPLE_RATE, 300]
    assert len(buffer.samples) == total_samples


def test_chunks_is_lazy_and_yields_before_the_recording_ends() -> None:
    process = FakeProcess(
        FakeStdout([_pcm(_constant_samples(SAMPLE_RATE // 10))], block_at_end=True)
    )
    source, _ = _source_with(process)
    source.start()

    iterator = source.chunks(0.1)
    first = next(iterator)

    assert len(first.samples) == SAMPLE_RATE // 10
    assert process.terminate_calls == 0, "the recorder must not have been stopped"
    buffer = source.stop()
    assert len(buffer.samples) == SAMPLE_RATE // 10


def test_reader_thread_is_not_the_calling_thread() -> None:
    process = FakeProcess(FakeStdout([_pcm([0.0])], block_at_end=False))
    source, _ = _source_with(process)
    source.start()
    capture = source._capture  # noqa: SLF001 - asserting the threading design
    assert capture is not None
    assert capture.thread is not None
    assert capture.thread is not threading.current_thread()
    assert capture.thread.daemon is True
    source.stop()


# --- drain on stop ------------------------------------------------------------


def test_drain_on_stop_returns_the_full_buffer_including_yielded_audio() -> None:
    total_samples = SAMPLE_RATE * 2
    process = FakeProcess(
        FakeStdout([_pcm(_constant_samples(total_samples))], block_at_end=False)
    )
    source, _ = _source_with(process)
    source.start()

    chunks = list(source.chunks(0.25))
    buffer = source.stop()

    assert len(buffer.samples) == total_samples
    assert sum(len(chunk.samples) for chunk in chunks) == len(buffer.samples)
    assert buffer.sample_rate == SAMPLE_RATE


def test_stop_returns_everything_even_when_chunks_was_abandoned_early() -> None:
    total_samples = SAMPLE_RATE
    process = FakeProcess(
        FakeStdout([_pcm(_constant_samples(total_samples))], block_at_end=True)
    )
    source, _ = _source_with(process)
    source.start()

    iterator = source.chunks(0.1)
    taken = [next(iterator) for _ in range(3)]
    buffer = source.stop()

    assert sum(len(chunk.samples) for chunk in taken) == 3 * 1600
    assert len(buffer.samples) == total_samples


def test_stop_drains_what_the_recorder_wrote_after_chunking_stopped() -> None:
    total_samples = SAMPLE_RATE
    process = FakeProcess(
        FakeStdout([_pcm(_constant_samples(total_samples))], block_at_end=True)
    )
    source, _ = _source_with(process)
    source.start()

    # ``stop()`` terminates the recorder and *then* reads stdout to EOF, so the
    # bytes already in the pipe are still counted.
    buffer = source.stop()

    assert len(buffer.samples) == total_samples
    assert process.terminate_calls == 1


# --- zero samples is normal ---------------------------------------------------


def test_stop_immediately_after_start_returns_an_empty_buffer() -> None:
    process = FakeProcess(
        FakeStdout((), block_at_end=True),
        stderr=b"",
        returncode=PW_RECORD_STOP_STATUS,
    )
    source, _ = _source_with(process)

    source.start()
    buffer = source.stop()

    assert len(buffer.samples) == 0
    assert buffer.sample_rate == SAMPLE_RATE


# --- failure modes ------------------------------------------------------------


def test_missing_binary_raises_an_actionable_error() -> None:
    def missing(argv: Sequence[str]) -> PwRecordProcess:
        raise FileNotFoundError(2, "No such file or directory", argv[0])

    source = PwRecordAudioSource(spawn=missing)
    with pytest.raises(PwRecordError) as excinfo:
        source.start()

    assert PROGRAM in str(excinfo.value)
    assert "PATH" in str(excinfo.value)


def test_startup_failure_before_audio_surfaces_stderr_through_stop() -> None:
    stderr = b"error: pw_context_connect() failed: Host is down"
    process = FakeProcess(
        FakeStdout((), block_at_end=True),
        stderr=stderr,
        returncode=1,
    )
    source, _ = _source_with(process)
    source.start()

    with pytest.raises(PwRecordError) as excinfo:
        source.stop()

    assert "Host is down" in str(excinfo.value)
    assert "PipeWire session" in str(excinfo.value)


def test_startup_failure_before_audio_surfaces_stderr_through_chunks() -> None:
    stderr = b"error: no node available for target 'definitely-not-a-mic'"
    process = FakeProcess(
        FakeStdout((), block_at_end=False),
        stderr=stderr,
        returncode=1,
    )
    source, _ = _source_with(process)
    source.start()

    with pytest.raises(PwRecordError) as excinfo:
        next(source.chunks(0.5))

    assert "definitely-not-a-mic" in str(excinfo.value)
    assert "no node available" in str(excinfo.value)


def test_failure_message_names_the_configured_device() -> None:
    process = FakeProcess(
        FakeStdout((), block_at_end=True),
        stderr=b"error: target not found",
        returncode=1,
    )
    recorder = SpawnRecorder(lambda: process)
    source = PwRecordAudioSource(device="my-usb-mic", spawn=recorder)
    source.start()

    with pytest.raises(PwRecordError) as excinfo:
        source.stop()

    assert "my-usb-mic" in str(excinfo.value)


def test_stdout_read_error_is_raised_not_swallowed() -> None:
    process = FakeProcess(
        FakeStdout((), block_at_end=False, error=OSError("device disconnected"))
    )
    source, _ = _source_with(process)
    source.start()

    with pytest.raises(PwRecordError) as excinfo:
        source.stop()

    assert "device disconnected" in str(excinfo.value)


def test_start_twice_raises() -> None:
    process = FakeProcess()
    source, _ = _source_with(process)
    source.start()
    try:
        with pytest.raises(PwRecordError) as excinfo:
            source.start()
        assert "start()" in str(excinfo.value)
    finally:
        source.stop()


def test_stop_before_start_raises() -> None:
    process = FakeProcess()
    source, _ = _source_with(process)
    with pytest.raises(PwRecordError) as excinfo:
        source.stop()
    assert "before start()" in str(excinfo.value)


def test_stop_twice_raises() -> None:
    process = FakeProcess()
    source, _ = _source_with(process)
    source.start()
    source.stop()
    with pytest.raises(PwRecordError):
        source.stop()


def test_chunks_before_start_raises() -> None:
    process = FakeProcess()
    source, _ = _source_with(process)
    with pytest.raises(PwRecordError) as excinfo:
        source.chunks(1.0)
    assert "before start()" in str(excinfo.value)


def test_non_positive_chunk_seconds_is_rejected() -> None:
    process = FakeProcess()
    source, _ = _source_with(process)
    source.start()
    try:
        with pytest.raises(ValueError):
            source.chunks(0.0)
        with pytest.raises(ValueError):
            source.chunks(-1.0)
    finally:
        source.stop()


# --- the interface and reuse --------------------------------------------------


def test_source_satisfies_the_audio_source_protocol() -> None:
    source = PwRecordAudioSource()
    assert isinstance(source, AudioSource)


def test_source_is_reusable_across_recordings() -> None:
    recordings = [
        FakeProcess(FakeStdout([_pcm([0.1] * 100)], block_at_end=False)),
        FakeProcess(FakeStdout([_pcm([0.2] * 200)], block_at_end=False)),
    ]
    iterator: Iterator[PwRecordProcess] = iter(recordings)
    recorder = SpawnRecorder(lambda: next(iterator))
    source = PwRecordAudioSource(spawn=recorder)

    source.start()
    first = source.stop()
    source.start()
    second = source.stop()

    assert len(first.samples) == 100
    assert len(second.samples) == 200
    assert recorder.call_count == 2
    assert first.samples[0] == pytest.approx(0.1)
    assert second.samples[0] == pytest.approx(0.2)


def test_sample_rate_and_format_are_the_engine_contract() -> None:
    assert SAMPLE_RATE == 16000
    assert CHANNELS == 1
    assert SAMPLE_FORMAT == "f32"
    assert PROGRAM == "pw-record"
