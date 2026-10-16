"""The NeMo-Speech.cpp transcriber, driven in-process with ``ctypes`` (ADR 0022).

This module is the **only** place in ges2t allowed to load the NeMo-Speech.cpp
native library (``libnemo_speech_asr_c.so``). It reuses the shape ADR 0021
established for whisper.cpp: a :class:`~ges2t.interfaces.Transcriber`
implementation whose recognizer is created **once** by
:meth:`NemoSpeechTranscriber.load` and kept resident for the service lifetime.
There is no per-utterance subprocess, no Python, no ``torch`` and no ``numpy`` --
the C ABI takes a bare ``float`` buffer and returns a result handle. The model is
loaded once at service start.

What this adapter guarantees
----------------------------
* **Streams append-only (ADR 0022).** ``supports_streaming = True``.
  :meth:`stream` drives the C ABI's streaming API through the pure-streaming
  :func:`ges2t.streaming.drive_append_only`: no sliding window, no two-pass
  agreement rule. The adapter commits only the **completed-word prefix** of each
  engine interim and holds the trailing partial token ``pending`` (never
  injected); the commit boundary is the last space in the interim string, because
  interims carry **no word timestamps** (report §10.3).
* **The audio contract is 16 kHz mono float32.** A non-16 kHz buffer raises rather
  than being resampled silently. The engine *can* resample 8-96 kHz input (the C
  header says so), but ges2t's canonical rate is 16 kHz (ADR 0005) and the
  pipeline resamples before the transcriber; doing it here too would hide a
  pipeline bug.
* **The device is chosen, recorded and logged, never switched silently.** With
  ``device = auto`` Vulkan is used exactly when the host has a Vulkan loader,
  otherwise the CPU; ``device = vulkan`` on a machine without one is a hard error
  rather than a silent fallback (ADR 0020).
* **Engine timings are ``None``.** The C ABI exposes no processor rate or first
  token time; the result carries ``None`` for ``ttft_ms``/``decode_tps`` and the
  adapter records ges2t's **own** wall-clock decode time in
  :attr:`NemoSpeechTranscriber.last_transcribe_seconds`. This is the same
  situation ADR 0011 records for the Whistle streaming path.

How the library is substituted
-----------------------------
The adapter depends on the :class:`NemoSpeechLibrary` Protocol, not on the ctypes
class, so the suite drives the Protocol contract with a fake library and one test
drives the real ``libnemo_speech_asr_c.so`` through
:class:`CtypesNemoSpeechLibrary` (ADR 0014). Loading the native library happens
lazily inside :meth:`NemoSpeechTranscriber.load`, so importing this module has no
side effects.

The C ABI mirrored here
-----------------------
The structures below mirror ``include/nemo_speech/asr.h`` from the pinned v0.2.0
``linux-x86_64-vulkan`` engine archive. Every symbol used is confirmed against
that header (ticket 58's ``001-comment.md``): ``nemo_speech_asr_create`` /
``_destroy`` for the recognizer, ``nemo_speech_asr_recognition_options_default``
for the request options, ``nemo_speech_asr_recognize_f32`` for the batch decode,
``nemo_speech_asr_result_transcript`` / ``_result_language_count`` /
``_result_language_code`` / ``_result_destroy`` for the result, and
``nemo_speech_asr_last_error`` to surface the library's own message on failure.
Every subsystem struct starts with ``size_t size``, so the versioning contract is
"set ``size`` to ``sizeof`` what you built"; fields we do not set are left at the
linker's zero because the corresponding pointer is ``NULL``.
"""

from __future__ import annotations

import ctypes
import logging
import os
import time
from array import array
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

from ges2t import model_store, nemo_engine, streaming
from ges2t.identifiers import NEMO_SPEECH_ENGINE
from ges2t.interfaces import (
    AudioBuffer,
    ProgressCallback,
    TranscriptionResult,
    TranscriptionSegment,
)
from ges2t.streaming import StreamingPass
from ges2t.transcribers.nemo_speech_config import (
    NemoDevicePreference,
    NemoSpeechConfig,
)

__all__ = [
    "ENTRY_LIBRARY_NAME",
    "GPU_CPU",
    "GPU_DEVICE_INDEX",
    "SAMPLE_RATE",
    "STATUS_OK",
    "CtypesNemoSpeechLibrary",
    "Device",
    "NemoSpeechAppendOnlyError",
    "NemoSpeechDecodeError",
    "NemoSpeechError",
    "NemoSpeechInputError",
    "NemoSpeechLibrary",
    "NemoSpeechLoadError",
    "NemoSpeechNotLoadedError",
    "NemoSpeechStreamResult",
    "NemoSpeechTranscript",
    "NemoSpeechTranscriber",
]

_logger = logging.getLogger(__name__)

#: The engine's accepted input rate: 16 kHz mono float32, ges2t's canonical rate
#: (ADR 0005). The engine can resample other rates, but the transcriber boundary
#: enforces the pipeline's contract (see the module docstring).
SAMPLE_RATE: int = 16000

#: The library the adapter loads from an installed engine directory. Named once by
#: :mod:`ges2t.nemo_engine`, which owns the archive layout.
ENTRY_LIBRARY_NAME: str = nemo_engine.ENTRY_LIBRARY_NAME

# --- Status and device constants (the pinned C header) ------------------------

#: ``NEMO_SPEECH_ASR_OK`` from ``enum nemo_speech_asr_status`` (the pinned
#: v0.2.0 header). Any other status is a failure.
STATUS_OK: int = 0

#: ``nemo_speech_asr_backend_config.gpu`` sentinel for the CPU path: the header's
#: own comment is ``-1 = CPU``.
GPU_CPU: int = -1

#: ``nemo_speech_asr_backend_config.gpu`` for the Vulkan/GPU path: device index 0.
#: The ticket-55 measurement harness used exactly this value
#: (``gpu = -1 if device == "cpu" else 0``).
GPU_DEVICE_INDEX: int = 0


class Device(StrEnum):
    """Which compute backend the loaded engine uses (ADR 0022)."""

    CPU = "cpu"
    VULKAN = "vulkan"


# --- Errors -------------------------------------------------------------------


class NemoSpeechError(Exception):
    """Base class for every failure raised by this adapter."""


class NemoSpeechLoadError(NemoSpeechError):
    """The engine could not be loaded; the service treats this as fatal.

    Covers a missing library or model, an unusable library, a failed
    ``nemo_speech_asr_create`` (a corrupt or unreadable model), and a
    ``device = vulkan`` request on a machine with no Vulkan loader. The message
    names the exact artifact and the fix so ``run_service`` can print it verbatim.
    """


class NemoSpeechNotLoadedError(NemoSpeechError):
    """``transcribe()`` was called before :meth:`NemoSpeechTranscriber.load`."""


class NemoSpeechInputError(NemoSpeechError):
    """The buffer violates the 16 kHz mono audio contract (ADR 0005)."""


class NemoSpeechDecodeError(NemoSpeechError):
    """A C-ABI call returned a non-zero status (recognize or a stream call)."""


class NemoSpeechAppendOnlyError(NemoSpeechDecodeError):
    """An interim did not extend the previously committed prefix (ADR 0022).

    The pure-streaming decision rests on the interims being **append-only** (ticket
    55 measured it). If an interim ever rewrites an earlier token, the delta the
    adapter computes from the string prefix would silently drop the revision, so
    the decoder stops and reports instead -- the loud failure report §9.1(2)
    expects. A subclass of :class:`NemoSpeechDecodeError` so existing handlers
    still catch it.
    """


# --- Values that cross the library seam (ADR 0019) ----------------------------


@dataclass(frozen=True)
class NemoSpeechTranscript:
    """What one ``nemo_speech_asr_recognize_f32`` call produced: text and language.

    Engine timings are deliberately **not** here: the C ABI exposes none, so there
    is nothing to carry (the adapter records its own wall time instead).
    """

    text: str
    language: str | None


@dataclass(frozen=True)
class NemoSpeechStreamResult:
    """One result the streaming ABI returned (ADR 0019, ADR 0022).

    ``is_final`` is the engine's end-of-stream flag (interims carry ``False``);
    ``language`` is the language the result reports, or ``None`` when it reports
    none. The ABI's ``audio_processed`` watermark is deliberately **not** carried:
    nothing in the commit path reads it (JSONL measurement is ticket 55's job),
    and a populated field no code reads is dead weight.
    """

    transcript: str
    is_final: bool
    language: str | None


# --- The library seam (ADR 0010/0014/0019) ------------------------------------


@runtime_checkable
class NemoSpeechLibrary(Protocol):
    """The NeMo-Speech.cpp ASR library as the adapter uses it.

    :class:`CtypesNemoSpeechLibrary` is the real implementation; the test suite
    substitutes a fake at this Protocol. It is deliberately coarse (create one
    recognizer, decode once, free) so the fake models the same lifecycle the real
    library has -- an opaque handle, a create that can fail, a decode that can
    fail, and an explicit free -- not a simplified one.
    """

    def version(self) -> str:
        """Return the library's version string."""
        ...

    def create_recognizer(self, model_path: Path, *, use_gpu: bool) -> int:
        """Load ``model_path`` and return an opaque recognizer handle.

        ``use_gpu`` selects the Vulkan path (``gpu`` device 0) when true and the
        CPU path (``gpu = -1``) otherwise. A failure to create raises
        :class:`NemoSpeechLoadError`, naming the library's own ``last_error``.
        """
        ...

    def free_recognizer(self, handle: int) -> None:
        """Release a recognizer previously returned by :meth:`create_recognizer`."""
        ...

    def recognize(
        self,
        handle: int,
        samples: Sequence[float],
        *,
        sample_rate: int,
    ) -> NemoSpeechTranscript:
        """Decode one complete utterance of mono float32 samples at ``sample_rate``.

        A non-zero status raises :class:`NemoSpeechDecodeError`; a failure to
        convert the samples raises :class:`NemoSpeechInputError`.
        """
        ...

    # -- streaming (ADR 0022): one stream per recording, polled on this thread --

    def open_stream(self, handle: int) -> int:
        """Start a streaming recognition and return its opaque stream handle.

        ``handle`` is the recognizer from :meth:`create_recognizer`. The stream has
        no callback: the caller advances it with :meth:`push_audio`, polls it with
        :meth:`next_result`, ends it with :meth:`finish_stream`, and releases it
        with :meth:`close_stream` (report §4.1). A failure raises
        :class:`NemoSpeechDecodeError`.
        """
        ...

    def push_audio(
        self, stream: int, samples: Sequence[float], *, sample_rate: int
    ) -> None:
        """Buffer ``samples`` into ``stream``; **no decode** happens here.

        The rate cannot change within a stream. Decoding is driven by
        :meth:`next_result`. A failure raises :class:`NemoSpeechDecodeError`.
        """
        ...

    def next_result(self, stream: int) -> NemoSpeechStreamResult | None:
        """Pull **one** result, or ``None`` when nothing is ready yet.

        Called in a loop until it returns ``None``; a single push may yield zero,
        one, or several results, so the caller keeps every one (report §4.1). A
        failure raises :class:`NemoSpeechDecodeError`.
        """
        ...

    def finish_stream(self, stream: int) -> None:
        """End input on ``stream``: flush the tail so the final can be pulled.

        The end-of-stream result (``is_final=True``) is then returned by
        :meth:`next_result`. A failure raises :class:`NemoSpeechDecodeError`.
        """
        ...

    def close_stream(self, stream: int) -> None:
        """Release a stream handle previously returned by :meth:`open_stream`."""
        ...


# --- ctypes mirror of the pinned v0.2.0 C ABI ---------------------------------
#
# ``nemo_speech_asr_recognizer_config`` and ``nemo_speech_asr_recognition_options``
# are both passed by pointer and start with ``size_t size``; ctypes must know the
# exact layout to write ``size`` and to point at the right structs. The fields are
# declared in the header's order. The unused subsystem pointers in the recognizer
# config are declared ``c_void_p`` (one machine word each, exactly like the typed
# pointers they stand in for) and left ``NULL``, which the C header documents as
# "use the library's default".


class _NemoBackendConfig(ctypes.Structure):
    """``struct nemo_speech_asr_backend_config``."""

    _fields_ = [
        ("size", ctypes.c_size_t),
        ("gpu", ctypes.c_int32),
    ]


class _NemoModelConfig(ctypes.Structure):
    """``struct nemo_speech_asr_model_config``."""

    _fields_ = [
        ("size", ctypes.c_size_t),
        ("path", ctypes.c_char_p),
        ("name", ctypes.c_char_p),
    ]


class _NemoRecognizerConfig(ctypes.Structure):
    """``struct nemo_speech_asr_recognizer_config``: size + 9 subsystem pointers."""

    _fields_ = [
        ("size", ctypes.c_size_t),
        ("backend", ctypes.POINTER(_NemoBackendConfig)),
        ("model", ctypes.POINTER(_NemoModelConfig)),
        ("streaming", ctypes.c_void_p),
        ("decoder", ctypes.c_void_p),
        ("vad", ctypes.c_void_p),
        ("endpointing", ctypes.c_void_p),
        ("postproc", ctypes.c_void_p),
        ("diar", ctypes.c_void_p),
        ("batching", ctypes.c_void_p),
    ]


class _NemoSpeechContext(ctypes.Structure):
    """``struct nemo_speech_asr_speech_context`` (referenced by the options struct)."""

    _fields_ = [
        ("size", ctypes.c_size_t),
        ("phrases", ctypes.POINTER(ctypes.c_char_p)),
        ("phrase_count", ctypes.c_size_t),
        ("boost", ctypes.c_float),
    ]


class _NemoRecognitionOptions(ctypes.Structure):
    """``struct nemo_speech_asr_recognition_options``, in header order."""

    _fields_ = [
        ("size", ctypes.c_size_t),
        ("request_id", ctypes.c_char_p),
        ("language_code", ctypes.c_char_p),
        ("interim_results", ctypes.c_bool),
        ("enable_word_time_offsets", ctypes.c_bool),
        ("enable_automatic_punctuation", ctypes.c_bool),
        ("verbatim_transcripts", ctypes.c_bool),
        ("profanity_filter", ctypes.c_bool),
        ("stop_history_eou_ms", ctypes.c_int32),
        ("speech_contexts", ctypes.POINTER(_NemoSpeechContext)),
        ("speech_context_count", ctypes.c_size_t),
        ("max_alternatives", ctypes.c_int32),
        ("enable_speaker_diarization", ctypes.c_bool),
        ("max_speaker_count", ctypes.c_int32),
    ]


class CtypesNemoSpeechLibrary:
    """The real :class:`NemoSpeechLibrary`: ``ctypes`` bindings over the C ABI.

    Constructing this class loads the native library (``ctypes.CDLL``); importing
    the module does not. Only :meth:`NemoSpeechTranscriber.load` constructs it, so
    a fake library can be substituted with no native library present.
    """

    def __init__(self, library_path: Path) -> None:
        self.library_path = Path(library_path)
        self._lib = ctypes.CDLL(str(self.library_path))
        self._bind()

    def _bind(self) -> None:
        """Declare every function's argument and return types once.

        ctypes defaults to ``int`` for every parameter and return value, which
        would truncate the 64-bit recognizer/result pointers and misread the
        by-pointer structs, so each symbol used is declared explicitly.
        """
        lib = self._lib

        lib.nemo_speech_asr_version.restype = ctypes.c_char_p
        lib.nemo_speech_asr_version.argtypes = []

        lib.nemo_speech_asr_last_error.restype = ctypes.c_char_p
        lib.nemo_speech_asr_last_error.argtypes = []

        lib.nemo_speech_asr_create.restype = ctypes.c_int
        lib.nemo_speech_asr_create.argtypes = [
            ctypes.POINTER(_NemoRecognizerConfig),
            ctypes.POINTER(ctypes.c_void_p),
        ]

        lib.nemo_speech_asr_destroy.restype = None
        lib.nemo_speech_asr_destroy.argtypes = [ctypes.c_void_p]

        lib.nemo_speech_asr_recognition_options_default.restype = (
            _NemoRecognitionOptions
        )
        lib.nemo_speech_asr_recognition_options_default.argtypes = []

        lib.nemo_speech_asr_recognize_f32.restype = ctypes.c_int
        lib.nemo_speech_asr_recognize_f32.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_NemoRecognitionOptions),
            ctypes.POINTER(ctypes.c_float),
            ctypes.c_size_t,
            ctypes.c_int32,
            ctypes.POINTER(ctypes.c_void_p),
        ]

        lib.nemo_speech_asr_result_transcript.restype = ctypes.c_char_p
        lib.nemo_speech_asr_result_transcript.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
        ]

        lib.nemo_speech_asr_result_language_count.restype = ctypes.c_size_t
        lib.nemo_speech_asr_result_language_count.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
        ]

        lib.nemo_speech_asr_result_language_code.restype = ctypes.c_char_p
        lib.nemo_speech_asr_result_language_code.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_size_t,
        ]

        lib.nemo_speech_asr_result_destroy.restype = None
        lib.nemo_speech_asr_result_destroy.argtypes = [ctypes.c_void_p]

        # Streaming (ADR 0022). Every symbol is confirmed against the pinned v0.2.0
        # header (include/nemo_speech/asr.h, the streaming section). There is no
        # callback in the ABI: `stream_next` is the only reader, polled on the
        # calling thread (report §4.1).
        lib.nemo_speech_asr_streaming_recognize.restype = ctypes.c_int
        lib.nemo_speech_asr_streaming_recognize.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_NemoRecognitionOptions),
            ctypes.POINTER(ctypes.c_void_p),
        ]

        lib.nemo_speech_asr_stream_push_f32.restype = ctypes.c_int
        lib.nemo_speech_asr_stream_push_f32.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_float),
            ctypes.c_size_t,
            ctypes.c_int32,
        ]

        lib.nemo_speech_asr_stream_next.restype = ctypes.c_int
        lib.nemo_speech_asr_stream_next.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]

        lib.nemo_speech_asr_stream_finish.restype = ctypes.c_int
        lib.nemo_speech_asr_stream_finish.argtypes = [ctypes.c_void_p]

        lib.nemo_speech_asr_stream_close.restype = None
        lib.nemo_speech_asr_stream_close.argtypes = [ctypes.c_void_p]

        lib.nemo_speech_asr_result_is_final.restype = ctypes.c_bool
        lib.nemo_speech_asr_result_is_final.argtypes = [ctypes.c_void_p]

    def last_error(self) -> str:
        """Return the library's thread-local last error message, or ``""``."""
        raw = self._lib.nemo_speech_asr_last_error()
        return raw.decode("utf-8", "replace") if raw else ""

    def version(self) -> str:
        """Return the library's version string (``nemo_speech_asr_version()``)."""
        raw = self._lib.nemo_speech_asr_version()
        return raw.decode("utf-8", "replace") if raw else "<unknown>"

    def create_recognizer(self, model_path: Path, *, use_gpu: bool) -> int:
        """Create the recognizer with ``nemo_speech_asr_create``.

        ``use_gpu`` chooses ``gpu = 0`` (Vulkan) or ``-1`` (CPU). Only the
        ``backend`` and ``model`` substructs are supplied; every other pointer is
        ``NULL`` so the library uses its own defaults. A NULL handle or a non-zero
        status raises :class:`NemoSpeechLoadError` carrying the library's own
        ``last_error``.
        """
        backend = _NemoBackendConfig(
            size=ctypes.sizeof(_NemoBackendConfig),
            gpu=GPU_DEVICE_INDEX if use_gpu else GPU_CPU,
        )
        model = _NemoModelConfig(
            size=ctypes.sizeof(_NemoModelConfig),
            path=os.fsencode(str(model_path)),
            name=None,
        )
        config = _NemoRecognizerConfig(
            size=ctypes.sizeof(_NemoRecognizerConfig),
            backend=ctypes.pointer(backend),
            model=ctypes.pointer(model),
            streaming=None,
            decoder=None,
            vad=None,
            endpointing=None,
            postproc=None,
            diar=None,
            batching=None,
        )
        recognizer = ctypes.c_void_p()
        status = self._lib.nemo_speech_asr_create(
            ctypes.byref(config), ctypes.byref(recognizer)
        )
        if status != STATUS_OK or not recognizer.value:
            raise NemoSpeechLoadError(
                f"nemo_speech_asr_create failed with status {status} for the model "
                f"at {model_path}: {self.last_error()} (run 'ges2t setup "
                f"{NEMO_SPEECH_ENGINE}' to acquire the pinned model)"
            )
        return int(recognizer.value)

    def free_recognizer(self, handle: int) -> None:
        """Release the recognizer with ``nemo_speech_asr_destroy``."""
        self._lib.nemo_speech_asr_destroy(ctypes.c_void_p(handle))

    def recognize(
        self,
        handle: int,
        samples: Sequence[float],
        *,
        sample_rate: int,
    ) -> NemoSpeechTranscript:
        """Run ``nemo_speech_asr_recognize_f32`` and read back the top hypothesis.

        The request options come from the library's own default factory
        (``nemo_speech_asr_recognition_options_default``) with ``interim_results``
        forced off: this is the offline/batch call, and the library's defaults are
        the honest starting point rather than a hand-built option set.
        """
        if len(samples) == 0:
            return NemoSpeechTranscript(text="", language=None)

        options = self._lib.nemo_speech_asr_recognition_options_default()
        options.size = ctypes.sizeof(_NemoRecognitionOptions)
        options.interim_results = False
        options.language_code = None

        # 16 kHz mono float32 is the declared input; the array is kept alive for
        # the duration of the call and handed over as a raw pointer.
        raw = array("f", samples)
        buffer = (ctypes.c_float * len(raw)).from_buffer(raw)
        result = ctypes.c_void_p()
        status = self._lib.nemo_speech_asr_recognize_f32(
            ctypes.c_void_p(handle),
            ctypes.byref(options),
            buffer,
            len(raw),
            sample_rate,
            ctypes.byref(result),
        )
        if status != STATUS_OK:
            raise NemoSpeechDecodeError(
                f"nemo_speech_asr_recognize_f32 failed with status {status} for "
                f"{len(raw)} samples at {sample_rate} Hz: {self.last_error()}"
            )
        try:
            return self._read_result(result)
        finally:
            if result.value:
                self._lib.nemo_speech_asr_result_destroy(result)

    def _read_result(self, result: ctypes.c_void_p) -> NemoSpeechTranscript:
        """Read the top hypothesis's transcript and language from a result handle."""
        if not result.value:
            return NemoSpeechTranscript(text="", language=None)
        raw_text = self._lib.nemo_speech_asr_result_transcript(result, 0)
        text = raw_text.decode("utf-8", "replace") if raw_text else ""
        return NemoSpeechTranscript(
            text=text,
            language=self._first_language(result),
        )

    def _first_language(self, result: ctypes.c_void_p) -> str | None:
        """Return the first language code the result carries, or ``None``."""
        count = self._lib.nemo_speech_asr_result_language_count(result, 0)
        if count == 0:
            return None
        raw = self._lib.nemo_speech_asr_result_language_code(result, 0, 0)
        return raw.decode("utf-8", "replace") if raw else None

    # -- streaming (ADR 0022) ---------------------------------------------------

    def open_stream(self, handle: int) -> int:
        """Start a stream with ``nemo_speech_asr_streaming_recognize``.

        The request options come from the library's own default factory with
        ``interim_results`` forced **on** -- this is the streaming call and the
        interims are the whole point (ADR 0022) -- and ``enable_automatic_punctuation``
        forced **on**. The second flag is not cosmetic: with the library default
        (off) the engine lowercases and strips the punctuation from the *final*
        while the interims stay capitalised and punctuated, so the final does not
        extend the last interim and the append-only contract breaks (measured on
        this machine, CPU + the pinned GGUF: with it off ``final.startswith(last
        interim)`` is ``False``, with it on ``True``). Ticket 55 measured the
        append-only property under exactly this flag (its harness sets it), so on
        is the **measured** configuration, not a substitution.

        ``enable_word_time_offsets`` is left off: the driver never reads word
        times (report §10.3), and the probe shows it does not affect the text.
        Endpointing is a recognizer property and stays at its default (off), so
        exactly one ``is_final`` is emitted after :meth:`finish_stream` (report
        §5.4).
        """
        options = self._lib.nemo_speech_asr_recognition_options_default()
        options.size = ctypes.sizeof(_NemoRecognitionOptions)
        options.interim_results = True
        options.enable_automatic_punctuation = True
        options.language_code = None

        stream = ctypes.c_void_p()
        status = self._lib.nemo_speech_asr_streaming_recognize(
            ctypes.c_void_p(handle), ctypes.byref(options), ctypes.byref(stream)
        )
        if status != STATUS_OK or not stream.value:
            raise NemoSpeechDecodeError(
                f"nemo_speech_asr_streaming_recognize failed with status {status}: "
                f"{self.last_error()}"
            )
        return int(stream.value)

    def push_audio(
        self, stream: int, samples: Sequence[float], *, sample_rate: int
    ) -> None:
        """Buffer ``samples`` with ``nemo_speech_asr_stream_push_f32`` (no decode)."""
        if len(samples) == 0:
            return
        raw = array("f", samples)
        buffer = (ctypes.c_float * len(raw)).from_buffer(raw)
        status = self._lib.nemo_speech_asr_stream_push_f32(
            ctypes.c_void_p(stream), buffer, len(raw), sample_rate
        )
        if status != STATUS_OK:
            raise NemoSpeechDecodeError(
                f"nemo_speech_asr_stream_push_f32 failed with status {status} for "
                f"{len(raw)} samples at {sample_rate} Hz: {self.last_error()}"
            )

    def next_result(self, stream: int) -> NemoSpeechStreamResult | None:
        """Pull one result with ``nemo_speech_asr_stream_next``, or ``None``.

        A NULL out-handle is the ABI's "need more audio" signal, not an error. A
        non-NULL result is read on this thread -- ``last_error`` is thread-local,
        so the message must be read here, at the point of failure, never later
        elsewhere (report §4.3) -- and destroyed before returning.
        """
        out = ctypes.c_void_p()
        status = self._lib.nemo_speech_asr_stream_next(
            ctypes.c_void_p(stream), ctypes.byref(out)
        )
        if status != STATUS_OK:
            raise NemoSpeechDecodeError(
                f"nemo_speech_asr_stream_next failed with status {status}: "
                f"{self.last_error()}"
            )
        if not out.value:
            return None
        try:
            raw_text = self._lib.nemo_speech_asr_result_transcript(out, 0)
            transcript = raw_text.decode("utf-8", "replace") if raw_text else ""
            return NemoSpeechStreamResult(
                transcript=transcript,
                is_final=bool(self._lib.nemo_speech_asr_result_is_final(out)),
                language=self._first_language(out),
            )
        finally:
            self._lib.nemo_speech_asr_result_destroy(out)

    def finish_stream(self, stream: int) -> None:
        """Flush the tail with ``nemo_speech_asr_stream_finish``."""
        status = self._lib.nemo_speech_asr_stream_finish(ctypes.c_void_p(stream))
        if status != STATUS_OK:
            raise NemoSpeechDecodeError(
                f"nemo_speech_asr_stream_finish failed with status {status}: "
                f"{self.last_error()}"
            )

    def close_stream(self, stream: int) -> None:
        """Release a stream handle with ``nemo_speech_asr_stream_close``."""
        self._lib.nemo_speech_asr_stream_close(ctypes.c_void_p(stream))


def _validate_buffer(buffer: AudioBuffer) -> None:
    """Raise :class:`NemoSpeechInputError` unless the buffer is 16 kHz mono.

    NeMo-Speech.cpp's C ABI would resample 8-96 kHz input itself, but ges2t's
    canonical rate is 16 kHz (ADR 0005) and the pipeline guarantees it. Accepting
    another rate here would silently produce a different decode from what the rest
    of the pipeline assumes, so it is a hard error rather than a quiet resample.
    """
    if buffer.sample_rate != SAMPLE_RATE:
        raise NemoSpeechInputError(
            f"nemo_speech requires {SAMPLE_RATE} Hz mono audio, but the buffer is "
            f"{buffer.sample_rate} Hz; resample before the transcriber rather than "
            "expecting it to do so silently (ADR 0005)"
        )


def _committed_prefix(interim: str) -> str:
    """The longest prefix of ``interim`` that ends at a completed word boundary.

    Interims are plain space-separated tokens and carry **no word timestamps**
    (only the final does -- report §10.3), so the boundary is the last space: the
    text before it is a completed word, the trailing whitespace-delimited token
    may still be extended by the next interim and is held pending (ADR 0022 §5).
    Ticket 55 measured 1,841 interims with no leading or trailing whitespace, so
    the ``rstrip`` is belt-and-braces; an interim with no space has no completed
    word and commits nothing.
    """
    stripped = interim.rstrip()
    cut = stripped.rfind(" ")
    if cut < 0:
        return ""
    return stripped[:cut]


class _NemoSpeechDecoder:
    """The append-only decoder behind :meth:`NemoSpeechTranscriber.stream` (ADR 0022).

    It drives one C-ABI stream handle: :meth:`push` feeds a group and polls the
    interims, :meth:`finish` ends the stream and returns the whole final text. It
    keeps only the cumulative committed **string** and the latest interim string --
    no growing audio buffer and no second pass; the caches that grow live inside
    the engine (report §9.1(3)). The stream is opened lazily on the first push and
    released by :meth:`finish` or :meth:`close`; :meth:`close` is idempotent, so an
    abandoned generator cannot leak the handle.

    The commit rule is ADR 0022 §5: report the delta up to the completed-word
    prefix of the latest interim and hold the trailing token pending. It is
    **string-based** because interims carry no word timestamps (report §10.3).
    """

    def __init__(self, library: NemoSpeechLibrary, handle: int) -> None:
        self._library = library
        self._handle = handle
        self._stream: int | None = None
        self._committed = ""
        self._interim = ""

    def push(self, buffer: AudioBuffer) -> StreamingPass:
        """Feed one group and return the newly committed delta and the pending tail."""
        _validate_buffer(buffer)
        stream = self._ensure_stream()
        self._library.push_audio(
            stream, buffer.samples, sample_rate=buffer.sample_rate
        )
        language = self._pump_interims(stream)
        target = _committed_prefix(self._interim)
        if not target.startswith(self._committed):
            raise NemoSpeechAppendOnlyError(
                "the nemo_speech engine revised an earlier word: the interim "
                f"{self._interim!r} does not extend the committed prefix "
                f"{self._committed!r}, so the append-only contract (ADR 0022) is "
                "broken and the pure-streaming delta cannot be computed"
            )
        delta = target[len(self._committed) :]
        self._committed = target
        return StreamingPass(
            committed=delta,
            pending=self._interim[len(target) :],
            language=language,
        )

    def finish(self) -> str:
        """End input and return the whole final transcript; call exactly once.

        With no audio ever pushed there is no stream to end and the empty string
        is the whole transcript -- the same empty final the silence case yields.
        """
        stream = self._stream
        if stream is None:
            return ""
        try:
            self._library.finish_stream(stream)
            return self._drain_final(stream)
        finally:
            self.close()

    def close(self) -> None:
        """Release the stream handle; idempotent and safe after :meth:`finish`."""
        stream = self._stream
        if stream is None:
            return
        self._stream = None
        self._library.close_stream(stream)

    def _ensure_stream(self) -> int:
        """Open the stream on first use and return its handle."""
        if self._stream is None:
            self._stream = self._library.open_stream(self._handle)
        return self._stream

    def _pump_interims(self, stream: int) -> str | None:
        """Drain every ready interim; keep the latest transcript and return its language.

        A single push may yield zero, one, or several results, so every one is read
        (report §4.1). A mid-stream ``is_final`` is impossible with endpointing off
        (report §5.4); if one ever appears the driver has no rule for it, so it fails
        loudly rather than folding half an utterance in as a continuation.
        """
        language: str | None = None
        while True:
            result = self._library.next_result(stream)
            if result is None:
                break
            if result.is_final:
                raise NemoSpeechDecodeError(
                    "the nemo_speech engine emitted an is_final result in the middle "
                    "of the stream; endpointing must stay off for this engine and "
                    "v1 has no reopen rule for a mid-stream final (ADR 0022, report "
                    "§5.4)"
                )
            self._interim = result.transcript
            if result.language is not None:
                language = result.language
        return language

    def _drain_final(self, stream: int) -> str:
        """Drain the post-``finish`` results and return the end-of-stream final.

        The ABI guarantees the final arrives here; a stream that ends without one
        is a broken contract, so it fails loudly instead of silently promoting the
        last interim (which would inject an unconfirmed partial).
        """
        final_text: str | None = None
        while True:
            result = self._library.next_result(stream)
            if result is None:
                break
            if result.is_final:
                final_text = result.transcript
            else:
                self._interim = result.transcript
        if final_text is None:
            raise NemoSpeechDecodeError(
                "the nemo_speech engine did not return an end-of-stream final after "
                "nemo_speech_asr_stream_finish"
            )
        return final_text


def _stream_append_only(
    library: NemoSpeechLibrary,
    handle: int,
    chunks: Iterable[AudioBuffer],
    *,
    chunk_seconds: float,
) -> Iterator[TranscriptionSegment]:
    """Drive the append-only decoder, guaranteeing the native stream is closed.

    :func:`ges2t.streaming.drive_append_only` calls ``decoder.finish()`` exactly
    once at end of input; the ``finally`` here releases the stream handle if the
    consumer abandons the generator first (a cancelled recording), so a native
    stream and its encoder caches never leak (report §4.3).
    """
    decoder = _NemoSpeechDecoder(library, handle)
    try:
        yield from streaming.drive_append_only(
            decoder, chunks, chunk_seconds=chunk_seconds
        )
    finally:
        decoder.close()


class NemoSpeechTranscriber:
    """A :class:`~ges2t.interfaces.Transcriber` for NeMo-Speech.cpp, in-process.

    ``config`` carries the ``[engine.nemo_speech]`` settings (ADR 0009). The
    adapter can be pointed at artifacts outside the managed data directory for
    tests and for a caller that owns its own build:

    * ``model_path_override`` -- a specific ``.gguf`` instead of the managed one,
      so a test can use the research scratch cache.
    * ``library_path_override`` -- a specific ``libnemo_speech_asr_c.so`` instead
      of the installed engine's.
    * ``engine_directory`` -- where to look for the installed engine (defaults to
      :func:`ges2t.nemo_engine.engine_dir`).
    * ``device_detection`` -- the resolved host device record
      (:class:`ges2t.nemo_engine.NemoDevice`), so a test pins Vulkan-present vs
      Vulkan-absent without probing the machine.
    * ``library`` -- an already-constructed :class:`NemoSpeechLibrary` (the test
      seam). When given, no native library is loaded.

    The recognizer is process-global and not thread-safe (ADR 0021's shape): this
    class owns one recognizer and makes one call at a time; concurrent use is
    unsupported.
    """

    def __init__(
        self,
        config: NemoSpeechConfig,
        *,
        model_path_override: Path | None = None,
        library_path_override: Path | None = None,
        engine_directory: Path | None = None,
        device_detection: nemo_engine.NemoDevice | None = None,
        library: NemoSpeechLibrary | None = None,
    ) -> None:
        self.name: str = NEMO_SPEECH_ENGINE
        #: The engine emits append-only interims, so it streams (ADR 0022).
        self.supports_streaming: bool = True
        self.config = config
        self._model_path_override: Path | None = (
            Path(model_path_override) if model_path_override is not None else None
        )
        self._library_path_override: Path | None = (
            Path(library_path_override)
            if library_path_override is not None
            else None
        )
        self._engine_directory: Path = (
            Path(engine_directory)
            if engine_directory is not None
            else nemo_engine.engine_dir()
        )
        self._device_detection: nemo_engine.NemoDevice | None = device_detection
        self._library: NemoSpeechLibrary | None = library
        self._handle: int | None = None
        #: The device chosen at the last successful :meth:`load`, or ``None``
        #: before the first load. Recorded so the choice is observable (ADR 0020).
        self.device: Device | None = None
        #: How long the last successful :meth:`load` took, in seconds.
        self.load_seconds: float | None = None
        #: ges2t's own wall-clock decode time for the last :meth:`transcribe`,
        #: in seconds. The C ABI exposes no engine timing to pass through.
        self.last_transcribe_seconds: float | None = None

    # -- locations ----------------------------------------------------------

    def _model_path(self) -> Path:
        """The GGUF this transcriber will load.

        Precedence: the constructor override, then a configured
        ``[engine.nemo_speech] model`` path, then the managed pinned model.
        """
        if self._model_path_override is not None:
            return self._model_path_override
        if self.config.model is not None:
            return Path(self.config.model)
        return model_store.nemo_model_path()

    def _library_path(self) -> Path:
        """The engine library path: the override or the installed engine's."""
        if self._library_path_override is not None:
            return self._library_path_override
        return self._engine_directory / nemo_engine.ENTRY_LIBRARY_RELATIVE_PATH

    def _detection(self) -> nemo_engine.NemoDevice:
        """Return the host device record: the injected one, or a fresh probe."""
        if self._device_detection is None:
            self._device_detection = nemo_engine.inspect_device()
        return self._device_detection

    def resolve_device(self) -> Device:
        """Decide CPU vs Vulkan from the config preference and the host.

        * ``cpu`` -> CPU.
        * ``vulkan`` -> Vulkan, or a :class:`NemoSpeechLoadError` when the host
          has no Vulkan loader: a user who asked for the GPU is never silently
          moved to the CPU (ADR 0020).
        * ``auto`` -> Vulkan exactly when a Vulkan loader is present, CPU otherwise.
        """
        preference = self.config.device
        if preference is NemoDevicePreference.CPU:
            return Device.CPU
        available = self._detection().vulkan_available
        if preference is NemoDevicePreference.VULKAN:
            if not available:
                raise NemoSpeechLoadError(
                    f"engine.{NEMO_SPEECH_ENGINE}.device = 'vulkan' but no Vulkan "
                    "loader was found on this machine; install a Vulkan runtime "
                    "(ges2t never installs it) or set device = 'cpu'"
                )
            return Device.VULKAN
        return Device.VULKAN if available else Device.CPU

    # -- Transcriber Protocol ----------------------------------------------

    def ensure_model(self, progress: ProgressCallback | None = None) -> Path:
        """Ensure the pinned NeMo-Speech GGUF exists locally; return its path.

        Delegates to :mod:`ges2t.model_store`, the only module allowed to touch
        the network (ADR 0018). When ``model_path_override`` was supplied the
        caller owns the file, so it is returned untouched and nothing is fetched.
        """
        if self._model_path_override is not None or self.config.model is not None:
            # A caller-supplied file (constructor override or the configured
            # ``model`` path) is the caller's own; it is not acquired through the
            # store.
            return self._model_path()
        return model_store.ensure_nemo_model(progress=progress)

    def load(self) -> None:
        """Load the recognizer once and keep it resident; idempotent.

        Resolves the device, opens the engine library if one was not injected,
        creates the recognizer, and records the device and load time. Any failure
        raises :class:`NemoSpeechLoadError`, which the service treats as fatal.
        Calling this again after a successful load is a no-op.
        """
        if self._handle is not None:
            return

        model_path = self._model_path()
        if not model_path.is_file():
            raise NemoSpeechLoadError(
                f"the nemo_speech model is missing at {model_path}; run 'ges2t "
                f"setup {NEMO_SPEECH_ENGINE}' to download it (the only ges2t "
                "command that uses the network)"
            )

        device = self.resolve_device()
        library = self._library
        if library is None:
            library_path = self._library_path()
            if not library_path.is_file():
                raise NemoSpeechLoadError(
                    f"the nemo_speech engine library is missing at {library_path}; "
                    f"run 'ges2t setup {NEMO_SPEECH_ENGINE}' to acquire the pinned "
                    "engine archive"
                )
            library = self._open_library(library_path)

        started = time.monotonic()
        handle = library.create_recognizer(model_path, use_gpu=device is Device.VULKAN)
        self.load_seconds = time.monotonic() - started
        self._library = library
        self._handle = handle
        self.device = device
        _logger.info(
            "nemo_speech loaded: device=%s model=%s in %.3fs",
            device.value,
            model_path,
            self.load_seconds,
        )

    def _open_library(self, library_path: Path) -> NemoSpeechLibrary:
        """Open the native library, turning a load failure into a typed error."""
        try:
            return CtypesNemoSpeechLibrary(library_path)
        except OSError as exc:
            raise NemoSpeechLoadError(
                f"could not load the nemo_speech engine library at {library_path}: "
                f"{exc}. Re-acquire it with 'ges2t setup {NEMO_SPEECH_ENGINE}'."
            ) from exc

    def transcribe(self, buffer: AudioBuffer) -> TranscriptionResult:
        """Transcribe one complete utterance on the batch path (ADR 0022).

        Validates the 16 kHz contract, then decodes with
        ``nemo_speech_asr_recognize_f32``. The result carries the engine's text
        and language unchanged and ``None`` for ``ttft_ms``/``decode_tps`` (the C
        ABI exposes neither); ges2t's own decode wall time is recorded on
        :attr:`last_transcribe_seconds` instead.
        """
        handle = self._require_loaded()
        _validate_buffer(buffer)
        if len(buffer.samples) == 0:
            return TranscriptionResult(
                text="", language=None, ttft_ms=None, decode_tps=None
            )
        assert self._library is not None  # _require_loaded guarantees this
        started = time.monotonic()
        transcript = self._library.recognize(
            handle, buffer.samples, sample_rate=buffer.sample_rate
        )
        self.last_transcribe_seconds = time.monotonic() - started
        return TranscriptionResult(
            text=transcript.text,
            language=transcript.language,
            ttft_ms=None,
            decode_tps=None,
        )

    def _require_loaded(self) -> int:
        """Return the recognizer handle or raise a clear "call load() first" error."""
        if self._handle is None:
            raise NemoSpeechNotLoadedError(
                "NemoSpeechTranscriber was used before load(); call ensure_model() "
                "and load() first (ADR 0010)"
            )
        return self._handle

    def stream(
        self, chunks: Iterable[AudioBuffer]
    ) -> Iterator[TranscriptionSegment]:
        """Transcribe a live chunk iterable on the append-only streaming path (ADR 0022).

        The commit fold is :func:`ges2t.streaming.drive_append_only` -- the sibling
        of the Whistle path's :func:`ges2t.streaming.drive` with **no window and no
        flush**. The adapter supplies only the engine-facing
        :class:`_NemoSpeechDecoder`, which commits the completed-word prefix of each
        interim and holds the trailing token ``pending`` (never injected);
        ``finish()`` is called exactly once at end of input and returns the whole
        transcript, which the existing :class:`~ges2t.incremental_typing.CommittedTyper`
        then types with its watermark and single trailing space (tickets 32-34).

        The silence gate is deliberately absent: ticket 55 measured that the engine
        emits zero interims and an empty final on digital silence, so a second
        energy gate would re-derive a Whistle-specific fix for a defect this engine
        does not have (report §5.4).

        The call fails eagerly with :class:`NemoSpeechNotLoadedError` when
        :meth:`load` has not run, rather than at first iteration, so a caller does
        not start capturing audio for a stream that cannot run.
        """
        handle = self._require_loaded()
        assert self._library is not None  # _require_loaded guarantees this
        return _stream_append_only(
            self._library,
            handle,
            chunks,
            chunk_seconds=self.config.chunk_seconds,
        )

    def close(self) -> None:
        """Release the resident recognizer with ``nemo_speech_asr_destroy``; idempotent.

        Unlike Whistle (whose engine exposes no unload entry point), this engine
        has a destroy call, so the recognizer is actually released and the adapter
        becomes loadable again by calling :meth:`load`.
        """
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        self.device = None
        if self._library is not None:
            self._library.free_recognizer(handle)
