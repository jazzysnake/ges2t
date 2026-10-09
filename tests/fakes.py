"""First-class test doubles for the ``ges2t.interfaces`` Protocols (ADR 0014).

These are shared fixtures, not ad-hoc mocks: later tickets import them rather
than redefining them, so their API is deliberately small, explicit, and
predictable.

Import them as::

    from tests.fakes import (
        FakeAudioSource,
        FakeInjector,
        FakeStatusUI,
        FakeTranscriber,
    )

Conventions common to every fake:

* ``calls`` is a list of method-name strings recording calls in order, so a test
  can assert the exact call sequence.
* Method arguments are additionally recorded in a per-method typed list
  (``transcribed``, ``injected``, ``partials``, ...) when the test needs them.
* Every fake accepts a ``raise_on_<method>`` keyword and exposes the same name
  as a settable attribute, so a test can make any operation raise on demand.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path

from ges2t.interfaces import (
    AudioBuffer,
    ProgressCallback,
    TranscriptionResult,
    TranscriptionSegment,
)

__all__ = ["FakeAudioSource", "FakeInjector", "FakeStatusUI", "FakeTranscriber"]


def _concatenate(buffers: Sequence[AudioBuffer], *, sample_rate: int) -> AudioBuffer:
    """Join buffers end to end, returning one buffer at ``sample_rate``."""
    samples: list[float] = []
    for buffer in buffers:
        samples.extend(buffer.samples)
    return AudioBuffer(samples=samples, sample_rate=sample_rate)


class FakeAudioSource:
    """An in-memory :class:`~ges2t.interfaces.AudioSource`.

    ``buffers`` are yielded by :meth:`chunks` in order. :meth:`stop` returns
    ``stop_buffer`` when one was supplied, otherwise every configured buffer
    concatenated, so it mirrors the real source's "everything captured" contract.
    """

    def __init__(
        self,
        buffers: Sequence[AudioBuffer] = (),
        *,
        sample_rate: int = 16000,
        stop_buffer: AudioBuffer | None = None,
        raise_on_start: Exception | None = None,
        raise_on_chunks: Exception | None = None,
        raise_on_stop: Exception | None = None,
    ) -> None:
        self.sample_rate = sample_rate
        self.buffers: list[AudioBuffer] = list(buffers)
        self.stop_buffer = stop_buffer
        self.raise_on_start = raise_on_start
        self.raise_on_chunks = raise_on_chunks
        self.raise_on_stop = raise_on_stop
        self.calls: list[str] = []
        self.chunk_seconds_seen: list[float] = []
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.calls.append("start")
        if self.raise_on_start is not None:
            raise self.raise_on_start
        self.started = True

    def chunks(self, chunk_seconds: float) -> Iterator[AudioBuffer]:
        self.calls.append("chunks")
        self.chunk_seconds_seen.append(chunk_seconds)
        if self.raise_on_chunks is not None:
            raise self.raise_on_chunks
        yield from self.buffers

    def stop(self) -> AudioBuffer:
        self.calls.append("stop")
        if self.raise_on_stop is not None:
            raise self.raise_on_stop
        self.stopped = True
        if self.stop_buffer is not None:
            return self.stop_buffer
        return _concatenate(self.buffers, sample_rate=self.sample_rate)


class FakeTranscriber:
    """An in-memory :class:`~ges2t.interfaces.Transcriber`.

    Constructible as streaming-capable or not via ``supports_streaming``.
    :meth:`transcribe` returns ``result``. :meth:`stream` yields ``segments``
    when given, otherwise a single final segment built from ``result``.

    Any of ``raise_on_ensure_model``, ``raise_on_load``, ``raise_on_transcribe``
    or ``raise_on_stream`` makes the matching method raise the supplied
    exception; the attributes are public and may be set after construction.
    """

    def __init__(
        self,
        *,
        name: str = "fake",
        supports_streaming: bool = True,
        result: TranscriptionResult | None = None,
        segments: Sequence[TranscriptionSegment] | None = None,
        model_path: Path | None = None,
        raise_on_ensure_model: Exception | None = None,
        raise_on_load: Exception | None = None,
        raise_on_transcribe: Exception | None = None,
        raise_on_stream: Exception | None = None,
    ) -> None:
        self.name = name
        self.supports_streaming = supports_streaming
        self.result = (
            result
            if result is not None
            else TranscriptionResult(
                text="", language=None, ttft_ms=None, decode_tps=None
            )
        )
        self.segments = list(segments) if segments is not None else None
        self.model_path = model_path if model_path is not None else Path("/fake/model.cact")
        self.raise_on_ensure_model = raise_on_ensure_model
        self.raise_on_load = raise_on_load
        self.raise_on_transcribe = raise_on_transcribe
        self.raise_on_stream = raise_on_stream
        self.calls: list[str] = []
        self.ensure_model_progress: list[ProgressCallback | None] = []
        self.transcribed: list[AudioBuffer] = []
        self.streamed: list[list[AudioBuffer]] = []
        self.loaded = False
        self.closed = False

    def ensure_model(self, progress: ProgressCallback | None = None) -> Path:
        self.calls.append("ensure_model")
        self.ensure_model_progress.append(progress)
        if self.raise_on_ensure_model is not None:
            raise self.raise_on_ensure_model
        if progress is not None:
            progress(1.0)
        return self.model_path

    def load(self) -> None:
        self.calls.append("load")
        if self.raise_on_load is not None:
            raise self.raise_on_load
        self.loaded = True

    def transcribe(self, buffer: AudioBuffer) -> TranscriptionResult:
        self.calls.append("transcribe")
        if self.raise_on_transcribe is not None:
            raise self.raise_on_transcribe
        self.transcribed.append(buffer)
        return self.result

    def stream(self, chunks: Iterable[AudioBuffer]) -> Iterator[TranscriptionSegment]:
        # Deliberately not a generator function: the call is recorded and any
        # configured error is raised eagerly, then a ready iterator is returned.
        self.calls.append("stream")
        if self.raise_on_stream is not None:
            raise self.raise_on_stream
        materialised = list(chunks)
        self.streamed.append(materialised)
        if self.segments is not None:
            return iter(self.segments)
        return iter(
            [
                TranscriptionSegment(
                    text=self.result.text,
                    language=self.result.language,
                    is_final=True,
                    pass_ms=None,
                )
            ]
        )

    def close(self) -> None:
        self.calls.append("close")
        self.closed = True


class FakeInjector:
    """An in-memory :class:`~ges2t.interfaces.Injector`.

    ``injected`` holds the successful payloads in order. Set ``error`` (or pass
    it to the constructor) to make :meth:`inject` raise instead of recording.
    """

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[str] = []
        self.injected: list[str] = []

    def inject(self, text: str) -> None:
        self.calls.append("inject")
        if self.error is not None:
            raise self.error
        self.injected.append(text)


class FakeStatusUI:
    """An in-memory :class:`~ges2t.interfaces.StatusUI`.

    ``calls`` records the transition sequence (``"idle"``, ``"recording"``,
    ``"transcribing"``, ``"error"``, ``"partial"``); ``errors`` and ``partials``
    hold the corresponding arguments.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.errors: list[str] = []
        self.partials: list[str] = []
        self.idle_calls = 0
        self.recording_calls = 0
        self.transcribing_calls = 0

    def idle(self) -> None:
        self.calls.append("idle")
        self.idle_calls += 1

    def recording(self) -> None:
        self.calls.append("recording")
        self.recording_calls += 1

    def transcribing(self) -> None:
        self.calls.append("transcribing")
        self.transcribing_calls += 1

    def error(self, message: str) -> None:
        self.calls.append("error")
        self.errors.append(message)

    def partial(self, text: str) -> None:
        self.calls.append("partial")
        self.partials.append(text)
