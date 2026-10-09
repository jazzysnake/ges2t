"""The shared vocabulary for the dictation pipeline (ADR 0010).

Every other module in ``ges2t`` is written against the Protocols and data
classes defined here: the transcriber, the audio source, the injector, and the
status UI. Later work substitutes test doubles for all four (ADR 0014), so this
module is deliberately tiny, dependency-free, and stable.

Import rule
-----------
This module imports **only the standard library**. It must never pull in a
backend (``cactus_needle``), a GUI (``gi``/``pystray``), or an audio library
(``numpy``/``sounddevice``); ``doctor`` and the config layer import it in bare
environments, and importing the vocabulary must not have side effects. The
adapters are the only modules allowed to import a backend library. This is
asserted in ``tests/test_interfaces.py`` by importing this module in a fresh
interpreter and inspecting ``sys.modules``.

Data rule
---------
"Data in, data out": no interface method takes or returns a raw ``dict``, and
multi-value results are frozen data classes with named fields rather than
positional tuples (ADR 0019). Engine JSON is parsed inside an adapter and
crosses this boundary as a typed class.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

__all__ = [
    "AudioBuffer",
    "AudioSource",
    "Injector",
    "ProgressCallback",
    "StatusUI",
    "Transcriber",
    "TranscriptionResult",
    "TranscriptionSegment",
]


#: An acquisition progress sink. Called with the completed fraction in
#: ``[0.0, 1.0]`` as a model or engine is downloaded. ``model_store`` and the
#: transcriber adapters share this exact signature.
ProgressCallback = Callable[[float], None]


@dataclass(frozen=True)
class AudioBuffer:
    """A block of captured audio.

    ``samples`` is a mono sequence of float32 values in ``[-1.0, 1.0]`` at
    ``sample_rate`` Hz. The canonical engine contract is 16 kHz mono float32
    (ADR 0005), but this class deliberately **carries** the sample rate without
    validating it: it is a value object, and the transcriber boundary is where
    the 16 kHz contract is enforced.
    """

    samples: Sequence[float]
    sample_rate: int


@dataclass(frozen=True)
class TranscriptionSegment:
    """One streaming result: a non-final partial or the final segment.

    ``pass_ms`` is the engine's own per-pass timing where it reports one
    (streaming exposes ``pass_ms``; it has no ``ttft_ms``/``decode_tps``), and
    is ``None`` when the engine does not report it (ADR 0011).
    """

    text: str
    language: str | None
    is_final: bool
    pass_ms: float | None


@dataclass(frozen=True)
class TranscriptionResult:
    """The result of a batch transcription.

    ``ttft_ms`` and ``decode_tps`` are passed through from the engine where it
    reports them and are ``None`` otherwise; they are never recomputed here
    (ADR 0005).
    """

    text: str
    language: str | None
    ttft_ms: float | None
    decode_tps: float | None


@runtime_checkable
class Transcriber(Protocol):
    """A speech-to-text backend.

    Callers depend on this Protocol, never on a concrete adapter; concrete
    classes are chosen in exactly one place, the composition root. ``name`` is
    the identifier matched against the ``transcriber.backend`` config value, and
    ``supports_streaming`` is a declared capability flag, not a runtime probe:
    ``False`` is a legitimate, first-class state, and callers branch on it
    rather than catching an exception (ADR 0010).
    """

    name: str
    supports_streaming: bool

    def ensure_model(self, progress: ProgressCallback | None = None) -> Path:
        """Download the model if missing, verify it, and return its local path.

        This is the only interface method that may touch the network. It is
        idempotent and safe to call repeatedly (ADR 0010, ADR 0017).
        """
        ...

    def load(self) -> None:
        """Load the model into memory, without acquiring it. Idempotent."""
        ...

    def transcribe(self, buffer: AudioBuffer) -> TranscriptionResult:
        """Transcribe a complete utterance in one call (the batch path)."""
        ...

    def stream(
        self, chunks: Iterable[AudioBuffer]
    ) -> Iterator[TranscriptionSegment]:
        """Transcribe an iterable of chunks, yielding partials then a final segment.

        The caller decides how many chunks to feed; windowing and commit logic
        live in the streaming module, not here (ADR 0011).
        """
        ...

    def close(self) -> None:
        """Release the process-global model."""
        ...


@runtime_checkable
class AudioSource(Protocol):
    """Microphone capture.

    ``chunks()`` yields roughly ``chunk_seconds`` of audio as it is captured and
    is used by the streaming path; ``stop()`` ends capture and returns
    **everything** captured, including audio already yielded, which is what the
    batch path consumes (ADR 0008).
    """

    def start(self) -> None:
        """Begin capture."""
        ...

    def chunks(self, chunk_seconds: float) -> Iterator[AudioBuffer]:
        """Yield captured audio in roughly ``chunk_seconds`` blocks."""
        ...

    def stop(self) -> AudioBuffer:
        """End capture, returning all audio captured in this recording."""
        ...


@runtime_checkable
class Injector(Protocol):
    """Delivers text to the focused window (ADR 0003)."""

    def inject(self, text: str) -> None:
        """Type ``text`` into the focused window and return once delivered."""
        ...


@runtime_checkable
class StatusUI(Protocol):
    """Display surface for service state (tray, notifications, partials).

    ``partial()`` is display-only live text and must never be injected
    (ADR 0011).
    """

    def idle(self) -> None:
        """Show the idle state."""
        ...

    def recording(self) -> None:
        """Show the recording state."""
        ...

    def transcribing(self) -> None:
        """Show the transcribing state."""
        ...

    def error(self, message: str) -> None:
        """Show an actionable error message."""
        ...

    def partial(self, text: str) -> None:
        """Show display-only live transcription text."""
        ...
