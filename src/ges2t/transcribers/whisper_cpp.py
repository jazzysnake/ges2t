"""The whisper.cpp transcriber, driven in-process with ``ctypes`` (ADR 0021).

This module is the **only** place in ges2t allowed to load the whisper.cpp native
library. It reuses the same shape the Whistle adapter uses for
``libneedle3.so``: a :class:`~ges2t.interfaces.Transcriber` implementation whose
model is loaded **once** by :meth:`WhisperCppTranscriber.load` and kept resident
for the service lifetime. There is no per-utterance subprocess and no reload.

Why ``ctypes`` over ``libwhisper.so`` (ADR 0021)
------------------------------------------------
The provider research measured the ``whisper-cli`` subprocess shape and rejected
it for ges2t: a per-call spawn reloads ~0.5 GB of weights. ges2t already loads a
native library in-process for Whistle, so the resident shape is reused and the
model is loaded once at service start.

What this adapter guarantees
----------------------------
* **Batch only.** ``supports_streaming = False``; :meth:`stream` refuses with a
  typed error rather than pretending. The streaming research measured whisper's
  partial-window output as unstable (boundary duplication, silence hallucination,
  worse WER), so there is deliberately no streaming path (ADR 0011, ADR 0021).
* **The audio contract is whisper's contract.** 16 kHz mono float32 in
  ``[-1, 1]`` is accepted **verbatim**; there is no resampling and no
  number/spelling normalisation (ADR 0021 explicitly forbids the latter). A
  non-16 kHz buffer raises rather than being resampled silently.
* **The decode is pinned in code.** ``-nt`` (``no_timestamps``) is a *decode*
  switch whose measured accuracy the research's headline depends on, and greedy
  is pinned (``strategy = WHISPER_SAMPLING_GREEDY``, ``greedy.best_of = 1``,
  ``beam_search.beam_size = 1``). It is never inherited from a CLI default (ADR
  0021 §5, ``004-addendum-nt-decode.md``).
* **The device is chosen, recorded and logged, never switched silently.** With
  ``gpu = auto`` Vulkan is used exactly when the installed engine advertises a
  Vulkan build, otherwise the CPU; ``gpu = vulkan`` on a CPU-only engine is a
  hard error rather than a silent fallback (ADR 0020).
* **Engine timings whisper.cpp does not expose are ``None``.** whisper.cpp
  reports neither ``ttft_ms`` nor ``decode_tps``; the result carries ``None``
  for both and the adapter records ges2t's **own** wall-clock decode time in
  :attr:`WhisperCppTranscriber.last_transcribe_seconds`. This is the same
  situation ADR 0011 records for the Whistle streaming path.

How the library is substituted
-----------------------------
The adapter depends on the :class:`WhisperLibrary` Protocol, not on the ctypes
class, so the suite drives the Protocol contract with a fake library and one
test drives the real ``libwhisper.so`` through
:class:`CtypesWhisperLibrary` (ADR 0014). Loading the native library happens
lazily inside :meth:`WhisperCppTranscriber.load`, so importing this module has no
side effects.
"""

from __future__ import annotations

import ctypes
import logging
import os
import time
from array import array
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

from ges2t import model_store, whisper_engine
from ges2t.interfaces import (
    AudioBuffer,
    ProgressCallback,
    TranscriptionResult,
    TranscriptionSegment,
)
from ges2t.transcribers.whisper_cpp_config import (
    GpuPreference,
    WhisperCppConfig,
    default_threads,
)

__all__ = [
    "BEAM_SIZE",
    "BEST_OF",
    "ENTRY_LIBRARY_NAME",
    "NO_TIMESTAMPS",
    "SAMPLE_RATE",
    "SAMPLING_GREEDY",
    "CtypesWhisperLibrary",
    "Device",
    "SamplingStrategy",
    "WhisperCppDecodeError",
    "WhisperCppError",
    "WhisperCppInputError",
    "WhisperCppLoadError",
    "WhisperCppNotLoadedError",
    "WhisperCppStreamingUnsupportedError",
    "WhisperCppTranscriber",
    "WhisperDecodeOptions",
    "WhisperFullParams",
    "WhisperLibrary",
    "WhisperTranscript",
    "apply_decode_options",
    "resolve_threads",
]

_logger = logging.getLogger(__name__)

#: The engine's only accepted input rate: 16 kHz mono float32 (ADR 0021). This is
#: whisper.cpp's own input contract, so no resampling happens anywhere on this path.
SAMPLE_RATE: int = 16000

#: The library the adapter loads from an installed engine directory.
ENTRY_LIBRARY_NAME: str = whisper_engine.ENTRY_LIBRARY_NAME

# --- Pinned decode (ADR 0021 §5, the ``-nt`` addendum) ------------------------

#: ``whisper_full_params.no_timestamps``. ``-nt`` is a **decode** switch, not an
#: output-format one: the same model on the same clip scored 0.0625 with it and
#: 0.1562 without it, so the accurate decode is pinned here, never inherited
#: from a default. ges2t types text and has no use for timestamps.
NO_TIMESTAMPS: bool = True

#: ``whisper_full_params.greedy.best_of``. Greedy decoding is pinned; beam search
#: was measured no better and slower.
BEST_OF: int = 1

#: ``whisper_full_params.beam_search.beam_size``. Set even though the greedy
#: strategy is selected, so the two knobs cannot drift apart.
BEAM_SIZE: int = 1

#: ``whisper_full_params.strategy``. ``WHISPER_SAMPLING_GREEDY`` is enum value 0
#: in the pinned v1.9.5 header.
SAMPLING_GREEDY: int = 0


class SamplingStrategy(IntEnum):
    """``enum whisper_sampling_strategy`` from the pinned v1.9.5 header."""

    GREEDY = 0
    BEAM_SEARCH = 1


class Device(StrEnum):
    """Which compute backend the loaded engine uses (ADR 0021)."""

    CPU = "cpu"
    VULKAN = "vulkan"


# --- Errors -------------------------------------------------------------------


class WhisperCppError(Exception):
    """Base class for every failure raised by this adapter."""


class WhisperCppLoadError(WhisperCppError):
    """The engine could not be loaded; the service treats this as fatal.

    Covers a missing library or model, an unusable library, and a failed
    ``whisper_init_from_file_with_params`` (a corrupt or unreadable model). The
    message names the exact artifact and the fix so ``run_service`` can print it
    verbatim.
    """


class WhisperCppNotLoadedError(WhisperCppError):
    """``transcribe()`` was called before :meth:`WhisperCppTranscriber.load`."""


class WhisperCppInputError(WhisperCppError):
    """The buffer violates whisper.cpp's hard audio contract (ADR 0021)."""


class WhisperCppDecodeError(WhisperCppError):
    """``whisper_full`` returned a non-zero status for this buffer."""


class WhisperCppStreamingUnsupportedError(WhisperCppError):
    """``stream()`` was called on a batch-only backend (ADR 0010, ADR 0021).

    ``supports_streaming = False`` is a declared capability, not a runtime probe:
    callers branch on the flag and never reach this. The method exists only
    because the Protocol declares it, and it fails loudly rather than returning
    an empty iterator that would look like "no speech".
    """


# --- Values that cross the library seam (ADR 0019) ----------------------------


@dataclass(frozen=True)
class WhisperDecodeOptions:
    """Everything one ``whisper_full`` call needs besides the context and audio.

    A class rather than positional arguments, so the pinned decode flags cannot
    be confused with the configurable ones (ADR 0019). The adapter builds this
    with the pinned values; a fake library records it, which is how the pin is
    observable in a test without the native library.
    """

    language: str | None
    threads: int
    no_timestamps: bool
    strategy: SamplingStrategy
    best_of: int
    beam_size: int


@dataclass(frozen=True)
class WhisperTranscript:
    """What one decoded whisper.cpp call produced: the text and the language.

    Engine timings are deliberately **not** here: whisper.cpp does not expose
    ``ttft_ms``/``decode_tps``, so there is nothing to carry (ADR 0021).
    """

    text: str
    language: str | None


# --- The library seam (ADR 0010/0014/0019) ------------------------------------


@runtime_checkable
class WhisperLibrary(Protocol):
    """The whisper.cpp library as the adapter uses it.

    :class:`CtypesWhisperLibrary` is the real implementation; the test suite
    substitutes a fake at this Protocol. It is deliberately coarse (load one
    context, decode once, free) so the fake models the same lifecycle the real
    library has, not a simplified one.
    """

    def version(self) -> str:
        """Return the library's version string."""
        ...

    def load_context(self, model_path: Path, *, use_gpu: bool) -> int:
        """Load ``model_path`` and return an opaque context handle.

        ``use_gpu`` selects the GPU (Vulkan) path when true. A failure to load
        raises :class:`WhisperCppLoadError`.
        """
        ...

    def free_context(self, handle: int) -> None:
        """Release a context previously returned by :meth:`load_context`."""
        ...

    def decode(
        self,
        handle: int,
        samples: Sequence[float],
        options: WhisperDecodeOptions,
    ) -> WhisperTranscript:
        """Decode already-validated 16 kHz mono float32 samples."""
        ...


# --- ctypes mirror of the pinned v1.9.5 C ABI ---------------------------------
#
# ``whisper_full_params`` is passed **by value** to ``whisper_full`` and returned
# **by value** by ``whisper_full_default_params``, so ctypes must know its exact
# layout. The structures below mirror ``include/whisper.h`` at the pinned
# revision (v1.9.5, commit d1be6fde11ac6e0407606b4e42fe72d34add8037). The layout
# was checked against the real header with ``offsetof``: sizeof is 304 bytes and
# the offsets of ``no_timestamps`` (22), ``n_threads`` (4), ``language`` (104),
# ``greedy.best_of`` (144) and ``beam_search.beam_size`` (148) all match. See
# this ticket's ``001-comment.md`` for the transcript.
#
# Function pointers are declared ``c_void_p``: they are only ever NULL on this
# path, and a pointer is one machine word regardless of signature, so the exact
# callback typedefs do not affect the layout.


class _WhisperAheads(ctypes.Structure):
    """``struct whisper_aheads``."""

    _fields_ = [
        ("n_heads", ctypes.c_size_t),
        ("heads", ctypes.c_void_p),
    ]


class WhisperContextParams(ctypes.Structure):
    """``struct whisper_context_params`` (v1.9.5)."""

    _fields_ = [
        ("use_gpu", ctypes.c_bool),
        ("flash_attn", ctypes.c_bool),
        ("gpu_device", ctypes.c_int),
        ("dtw_token_timestamps", ctypes.c_bool),
        ("dtw_aheads_preset", ctypes.c_int),
        ("dtw_n_top", ctypes.c_int),
        ("dtw_aheads", _WhisperAheads),
        ("dtw_mem_size", ctypes.c_size_t),
    ]


class _GreedyParams(ctypes.Structure):
    """The anonymous ``greedy`` member of ``whisper_full_params``."""

    _fields_ = [("best_of", ctypes.c_int)]


class _BeamSearchParams(ctypes.Structure):
    """The anonymous ``beam_search`` member of ``whisper_full_params``."""

    _fields_ = [
        ("beam_size", ctypes.c_int),
        ("patience", ctypes.c_float),
    ]


class WhisperVadParams(ctypes.Structure):
    """``struct whisper_vad_params`` (v1.9.5)."""

    _fields_ = [
        ("threshold", ctypes.c_float),
        ("min_speech_duration_ms", ctypes.c_int),
        ("min_silence_duration_ms", ctypes.c_int),
        ("max_speech_duration_s", ctypes.c_float),
        ("speech_pad_ms", ctypes.c_int),
        ("samples_overlap", ctypes.c_float),
    ]


class WhisperFullParams(ctypes.Structure):
    """``struct whisper_full_params`` (v1.9.5) -- 304 bytes, in declaration order."""

    _fields_ = [
        ("strategy", ctypes.c_int),
        ("n_threads", ctypes.c_int),
        ("n_max_text_ctx", ctypes.c_int),
        ("offset_ms", ctypes.c_int),
        ("duration_ms", ctypes.c_int),
        ("translate", ctypes.c_bool),
        ("no_context", ctypes.c_bool),
        ("no_timestamps", ctypes.c_bool),
        ("single_segment", ctypes.c_bool),
        ("print_special", ctypes.c_bool),
        ("print_progress", ctypes.c_bool),
        ("print_realtime", ctypes.c_bool),
        ("print_timestamps", ctypes.c_bool),
        ("token_timestamps", ctypes.c_bool),
        ("thold_pt", ctypes.c_float),
        ("thold_ptsum", ctypes.c_float),
        ("max_len", ctypes.c_int),
        ("split_on_word", ctypes.c_bool),
        ("max_tokens", ctypes.c_int),
        ("debug_mode", ctypes.c_bool),
        ("audio_ctx", ctypes.c_int),
        ("tdrz_enable", ctypes.c_bool),
        ("suppress_regex", ctypes.c_char_p),
        ("initial_prompt", ctypes.c_char_p),
        ("carry_initial_prompt", ctypes.c_bool),
        ("prompt_tokens", ctypes.c_void_p),
        ("prompt_n_tokens", ctypes.c_int),
        ("language", ctypes.c_char_p),
        ("detect_language", ctypes.c_bool),
        ("suppress_blank", ctypes.c_bool),
        ("suppress_nst", ctypes.c_bool),
        ("temperature", ctypes.c_float),
        ("max_initial_ts", ctypes.c_float),
        ("length_penalty", ctypes.c_float),
        ("temperature_inc", ctypes.c_float),
        ("entropy_thold", ctypes.c_float),
        ("logprob_thold", ctypes.c_float),
        ("no_speech_thold", ctypes.c_float),
        ("greedy", _GreedyParams),
        ("beam_search", _BeamSearchParams),
        ("new_segment_callback", ctypes.c_void_p),
        ("new_segment_callback_user_data", ctypes.c_void_p),
        ("progress_callback", ctypes.c_void_p),
        ("progress_callback_user_data", ctypes.c_void_p),
        ("encoder_begin_callback", ctypes.c_void_p),
        ("encoder_begin_callback_user_data", ctypes.c_void_p),
        ("abort_callback", ctypes.c_void_p),
        ("abort_callback_user_data", ctypes.c_void_p),
        ("logits_filter_callback", ctypes.c_void_p),
        ("logits_filter_callback_user_data", ctypes.c_void_p),
        ("grammar_rules", ctypes.c_void_p),
        ("n_grammar_rules", ctypes.c_size_t),
        ("i_start_rule", ctypes.c_size_t),
        ("grammar_penalty", ctypes.c_float),
        ("vad", ctypes.c_bool),
        ("vad_model_path", ctypes.c_char_p),
        ("vad_params", WhisperVadParams),
    ]


def apply_decode_options(
    params: WhisperFullParams, options: WhisperDecodeOptions
) -> None:
    """Overwrite the fields ges2t owns on ``params``; leave whisper's defaults alone.

    Every other field keeps whatever ``whisper_full_default_params`` produced, so
    only the decode contract ges2t pins and the two configured knobs are set. A
    module-level, side-effect-free function so a test can assert the mapping on a
    bare :class:`WhisperFullParams` without a loaded library.
    """
    params.n_threads = options.threads
    params.translate = False
    params.no_timestamps = options.no_timestamps
    params.print_special = False
    params.print_progress = False
    params.print_realtime = False
    params.print_timestamps = False
    params.greedy.best_of = options.best_of
    params.beam_search.beam_size = options.beam_size


class CtypesWhisperLibrary:
    """The real :class:`WhisperLibrary`: ``ctypes`` bindings over ``libwhisper.so``.

    Constructing this class loads the native library (``ctypes.CDLL``); importing
    the module does not. Only :meth:`WhisperCppTranscriber.load` constructs it, so
    a fake library can be substituted with no native library present.
    """

    def __init__(self, library_path: Path) -> None:
        self.library_path = Path(library_path)
        self._lib = ctypes.CDLL(str(self.library_path))
        self._bind()

    def _bind(self) -> None:
        """Declare every function's argument and return types once.

        ctypes defaults to ``int`` for every parameter and return value, which
        would truncate the 64-bit context pointer and read the by-value structs
        wrongly, so each symbol used must be declared explicitly.
        """
        lib = self._lib
        lib.whisper_version.restype = ctypes.c_char_p
        lib.whisper_version.argtypes = []

        lib.whisper_context_default_params.restype = WhisperContextParams
        lib.whisper_context_default_params.argtypes = []

        lib.whisper_init_from_file_with_params.restype = ctypes.c_void_p
        lib.whisper_init_from_file_with_params.argtypes = [
            ctypes.c_char_p,
            WhisperContextParams,
        ]

        lib.whisper_free.restype = None
        lib.whisper_free.argtypes = [ctypes.c_void_p]

        lib.whisper_full_default_params.restype = WhisperFullParams
        lib.whisper_full_default_params.argtypes = [ctypes.c_int]

        lib.whisper_full.restype = ctypes.c_int
        lib.whisper_full.argtypes = [
            ctypes.c_void_p,
            WhisperFullParams,
            ctypes.POINTER(ctypes.c_float),
            ctypes.c_int,
        ]

        lib.whisper_full_n_segments.restype = ctypes.c_int
        lib.whisper_full_n_segments.argtypes = [ctypes.c_void_p]

        lib.whisper_full_get_segment_text.restype = ctypes.c_char_p
        lib.whisper_full_get_segment_text.argtypes = [ctypes.c_void_p, ctypes.c_int]

        lib.whisper_full_lang_id.restype = ctypes.c_int
        lib.whisper_full_lang_id.argtypes = [ctypes.c_void_p]

        lib.whisper_lang_str.restype = ctypes.c_char_p
        lib.whisper_lang_str.argtypes = [ctypes.c_int]

    def version(self) -> str:
        """Return the library's version string (``whisper_version()``)."""
        raw = self._lib.whisper_version()
        return raw.decode("utf-8", "replace") if raw else "<unknown>"

    def load_context(self, model_path: Path, *, use_gpu: bool) -> int:
        """Load the model with ``whisper_init_from_file_with_params``.

        ``use_gpu`` is written into ``whisper_context_params.use_gpu``; the
        default context parameters (flash attention on, GPU device 0) are kept.
        A NULL return means whisper.cpp could not load the model, which is a
        :class:`WhisperCppLoadError`.
        """
        cparams = self._lib.whisper_context_default_params()
        cparams.use_gpu = use_gpu
        handle = self._lib.whisper_init_from_file_with_params(
            os.fsencode(str(model_path)), cparams
        )
        if not handle:
            raise WhisperCppLoadError(
                f"whisper.cpp could not load the model at {model_path}; the file "
                "may be missing, truncated or not a ggml model (run 'ges2t "
                "setup whisper.cpp <model>')"
            )
        return int(handle)

    def free_context(self, handle: int) -> None:
        """Release the context with ``whisper_free``."""
        self._lib.whisper_free(ctypes.c_void_p(handle))

    def decode(
        self,
        handle: int,
        samples: Sequence[float],
        options: WhisperDecodeOptions,
    ) -> WhisperTranscript:
        """Run ``whisper_full`` and join the segments, per the pinned decode."""
        if len(samples) == 0:
            return WhisperTranscript(text="", language=None)

        params = self._lib.whisper_full_default_params(int(options.strategy))
        apply_decode_options(params, options)
        # ``whisper_full_params.language`` is a borrowed ``const char *``; the
        # bytes object must outlive the call, so it is held in a local.
        params.language = options.language.encode("utf-8") if options.language else None

        # 16 kHz mono float32 is whisper's own sample format; the array is kept
        # alive for the duration of the call and handed over as a raw pointer.
        raw = array("f", samples)
        buffer = (ctypes.c_float * len(raw)).from_buffer(raw)
        status = self._lib.whisper_full(
            ctypes.c_void_p(handle), params, buffer, len(raw)
        )
        if status != 0:
            raise WhisperCppDecodeError(
                f"whisper_full failed with status {status} for {len(samples)} "
                "samples at 16 kHz"
            )

        return WhisperTranscript(
            text=self._segments_text(handle),
            language=self._detected_language(handle),
        )

    def _segments_text(self, handle: int) -> str:
        """Join every segment ``whisper_full`` produced, in order."""
        count = self._lib.whisper_full_n_segments(ctypes.c_void_p(handle))
        pieces: list[str] = []
        for index in range(count):
            raw = self._lib.whisper_full_get_segment_text(
                ctypes.c_void_p(handle), index
            )
            if raw:
                pieces.append(raw.decode("utf-8", "replace"))
        return "".join(pieces)

    def _detected_language(self, handle: int) -> str | None:
        """Return the language whisper.cpp reported, or ``None`` when unknown."""
        lang_id = self._lib.whisper_full_lang_id(ctypes.c_void_p(handle))
        if lang_id < 0:
            return None
        raw = self._lib.whisper_lang_str(lang_id)
        return raw.decode("utf-8", "replace") if raw else None


def resolve_threads(configured: int | None) -> int:
    """Resolve ``configured`` to a positive thread count, defaulting to the machine."""
    if configured is None:
        return default_threads()
    return configured


def _validate_buffer(buffer: AudioBuffer) -> None:
    """Raise :class:`WhisperCppInputError` unless the buffer is 16 kHz mono.

    whisper.cpp's input contract **is** ges2t's :class:`AudioBuffer` contract at
    16 kHz mono float32, so nothing is resampled. A wrong rate is a hard error;
    silently resampling would make the transcription quietly different from what
    the rest of the pipeline assumes (ADR 0021).

    Unlike Whistle there is no per-call duration cap: ``whisper_full`` handles
    arbitrarily long audio by splitting it internally at 30 s, and slicing the
    audio ourselves is exactly the accuracy loss the streaming research measured.
    """
    if buffer.sample_rate != SAMPLE_RATE:
        raise WhisperCppInputError(
            f"whisper.cpp requires {SAMPLE_RATE} Hz mono audio, but the buffer is "
            f"{buffer.sample_rate} Hz; resample before the transcriber rather than "
            "expecting it to do so silently (ADR 0021)"
        )


class WhisperCppTranscriber:
    """A :class:`~ges2t.interfaces.Transcriber` for whisper.cpp, in-process.

    ``config`` carries the ``[engine.whisper_cpp]`` settings (ADR 0009). The
    adapter can be pointed at artifacts outside the managed data directory for
    tests and for a caller that owns its own build:

    * ``model_path_override`` -- a specific ``ggml-*.bin`` instead of the managed
      one, so a test can use the research scratch cache.
    * ``library_path_override`` -- a specific ``libwhisper.so`` instead of the
      installed engine's.
    * ``engine_directory`` -- where to look for the installed engine manifest
      (defaults to :func:`ges2t.whisper_engine.engine_dir`), which decides the
      CPU-vs-Vulkan device under ``gpu = auto``.
    * ``library`` -- an already-constructed :class:`WhisperLibrary` (the test
      seam). When given, no native library is loaded.

    The model is process-global and not thread-safe (ADR 0021): this class owns
    one context and makes one call at a time; concurrent use is unsupported.
    """

    def __init__(
        self,
        config: WhisperCppConfig,
        *,
        model_path_override: Path | None = None,
        library_path_override: Path | None = None,
        engine_directory: Path | None = None,
        library: WhisperLibrary | None = None,
    ) -> None:
        self.name: str = "whisper_cpp"
        self.supports_streaming: bool = False
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
            else whisper_engine.engine_dir()
        )
        self._library: WhisperLibrary | None = library
        self._handle: int | None = None
        self.threads: int = resolve_threads(config.threads)
        #: The device chosen at the last successful :meth:`load`, or ``None``
        #: before the first load. Recorded so the choice is observable (ADR 0021).
        self.device: Device | None = None
        #: How long the last successful :meth:`load` took, in seconds.
        self.load_seconds: float | None = None
        #: ges2t's own wall-clock decode time for the last :meth:`transcribe`,
        #: in seconds. whisper.cpp exposes no engine timing to pass through.
        self.last_transcribe_seconds: float | None = None
        #: The resolved model name. ``None`` until resolved; the composition root
        #: normally resolves an omitted ``[engine.whisper_cpp] model`` before
        #: constructing this adapter (ticket 45), so this fallback only matters
        #: for a directly-constructed adapter.
        self._resolved_model: str | None = config.model

    # -- locations ----------------------------------------------------------

    def _model_name(self) -> str:
        """Return the model name, resolving the device-aware default when omitted.

        An omitted ``model`` (``config.model is None``) is resolved through
        :func:`ges2t.whisper_engine.default_whisper_model`, the same
        single source of truth ``ges2t setup`` uses, so the loaded model matches
        the one ``setup`` would fetch. The result is cached (ADR 0021 §4).
        """
        if self._resolved_model is None:
            self._resolved_model = whisper_engine.default_whisper_model()
        return self._resolved_model

    def _model_path(self) -> Path:
        """The model file this transcriber will load: the override or the managed pin."""
        if self._model_path_override is not None:
            return self._model_path_override
        return model_store.whisper_model_path(self._model_name())

    def _library_path(self) -> Path:
        """The ``libwhisper.so`` path: the override or the installed engine's."""
        if self._library_path_override is not None:
            return self._library_path_override
        return self._engine_directory / ENTRY_LIBRARY_NAME

    def _engine_is_vulkan(self) -> bool:
        """Whether the installed engine advertises a Vulkan build (its manifest).

        A missing or incomplete installation reads back as ``None`` and is treated
        as CPU; the engine build itself only chose Vulkan when the Vulkan runtime
        and dev headers were present (ADR 0021 §7).
        """
        build = whisper_engine.verify_build(self._engine_directory)
        return (
            build is not None
            and build.variant is whisper_engine.BuildVariant.VULKAN
        )

    def resolve_device(self) -> Device:
        """Decide CPU vs Vulkan from the config preference and the built engine.

        * ``cpu`` -> CPU.
        * ``vulkan`` -> Vulkan, or a :class:`WhisperCppLoadError` when the
          installed engine is CPU-only: a user who asked for the GPU is never
          silently moved to the CPU (ADR 0020).
        * ``auto`` -> Vulkan exactly when the built engine advertises a Vulkan
          device, CPU otherwise.
        """
        preference = self.config.gpu
        if preference is GpuPreference.CPU:
            return Device.CPU
        if preference is GpuPreference.VULKAN:
            if not self._engine_is_vulkan():
                raise WhisperCppLoadError(
                    "engine.whisper_cpp.gpu = 'vulkan' but the installed whisper.cpp "
                    f"engine at {self._engine_directory} is CPU-only; rebuild it with "
                    "the Vulkan dev packages present (run 'ges2t setup whisper.cpp') "
                    "or set gpu = 'cpu'"
                )
            return Device.VULKAN
        return Device.VULKAN if self._engine_is_vulkan() else Device.CPU

    # -- Transcriber Protocol ----------------------------------------------

    def ensure_model(self, progress: ProgressCallback | None = None) -> Path:
        """Ensure the configured whisper.cpp model exists locally; return its path.

        Delegates to :mod:`ges2t.model_store`, the only module allowed to touch
        the network (ADR 0018). An omitted ``model`` is resolved through the
        device-aware rule first (ADR 0021 §4). When ``model_path_override`` was
        supplied the caller owns the file, so it is returned untouched and
        nothing is fetched.
        """
        if self._model_path_override is not None:
            return self._model_path_override
        return model_store.ensure_whisper_model(
            self._model_name(), progress=progress
        )

    def load(self) -> None:
        """Load the model once and keep it resident; idempotent.

        Resolves the device, opens the engine library if one was not injected,
        loads the model with ``whisper_init_from_file_with_params``, and records
        the device, the resolved thread count and the load time. Any failure
        raises :class:`WhisperCppLoadError`, which the service treats as fatal.
        Calling this again after a successful load is a no-op.
        """
        if self._handle is not None:
            return

        model_path = self._model_path()
        if not model_path.is_file():
            raise WhisperCppLoadError(
                f"the whisper.cpp model is missing at {model_path}; run 'ges2t "
                f"setup whisper.cpp {self._model_name()}' to download it (the only "
                "ges2t command that uses the network)"
            )

        device = self.resolve_device()
        library = self._library
        if library is None:
            library_path = self._library_path()
            if not library_path.is_file():
                raise WhisperCppLoadError(
                    f"the whisper.cpp engine library is missing at {library_path}; "
                    "build it with 'ges2t setup whisper.cpp <model>' (it requires "
                    "a C++ toolchain and cmake, which ges2t never installs)"
                )
            library = self._open_library(library_path)

        started = time.monotonic()
        handle = library.load_context(model_path, use_gpu=device is Device.VULKAN)
        self.load_seconds = time.monotonic() - started
        self._library = library
        self._handle = handle
        self.device = device
        _logger.info(
            "whisper.cpp loaded: device=%s threads=%d model=%s in %.3fs",
            device.value,
            self.threads,
            model_path,
            self.load_seconds,
        )

    def _open_library(self, library_path: Path) -> WhisperLibrary:
        """Open the native library, turning a load failure into a typed error."""
        try:
            return CtypesWhisperLibrary(library_path)
        except OSError as exc:
            raise WhisperCppLoadError(
                f"could not load the whisper.cpp engine library at {library_path}: "
                f"{exc}. Rebuild it with 'ges2t setup whisper.cpp <model>'."
            ) from exc

    def transcribe(self, buffer: AudioBuffer) -> TranscriptionResult:
        """Transcribe one complete utterance on the batch path (ADR 0021).

        Validates the 16 kHz contract, then decodes with the pinned ``-nt`` /
        greedy parameters. The result carries the engine's text and language
        unchanged and ``None`` for ``ttft_ms``/``decode_tps`` (whisper.cpp does
        not expose them); ges2t's own decode wall time is recorded on
        :attr:`last_transcribe_seconds` instead (ADR 0011's timing situation).
        """
        handle = self._require_loaded()
        _validate_buffer(buffer)
        if len(buffer.samples) == 0:
            return TranscriptionResult(
                text="", language=None, ttft_ms=None, decode_tps=None
            )
        assert self._library is not None  # _require_loaded guarantees this
        options = WhisperDecodeOptions(
            language=self.config.language or None,
            threads=self.threads,
            no_timestamps=NO_TIMESTAMPS,
            strategy=SamplingStrategy.GREEDY,
            best_of=BEST_OF,
            beam_size=BEAM_SIZE,
        )
        started = time.monotonic()
        transcript = self._library.decode(handle, buffer.samples, options)
        self.last_transcribe_seconds = time.monotonic() - started
        return TranscriptionResult(
            text=transcript.text,
            language=transcript.language,
            ttft_ms=None,
            decode_tps=None,
        )

    def _require_loaded(self) -> int:
        """Return the context handle or raise a clear "call load() first" error."""
        if self._handle is None:
            raise WhisperCppNotLoadedError(
                "WhisperCppTranscriber.transcribe() was called before load(); call "
                "ensure_model() and load() first (ADR 0010)"
            )
        return self._handle

    def stream(
        self, chunks: Iterable[AudioBuffer]
    ) -> Iterator[TranscriptionSegment]:
        """Refuse: whisper.cpp is batch-only in ges2t (ADR 0021, ADR 0011).

        ``supports_streaming = False`` is the contract; callers branch on it and
        never call this. Reaching here is a wiring bug, so it raises rather than
        yielding nothing (which would be indistinguishable from "no speech").
        """
        raise WhisperCppStreamingUnsupportedError(
            "whisper_cpp does not support streaming: whisper's partial-window "
            "output is unstable and the backend is batch-only (ADR 0021). Branch "
            "on Transcriber.supports_streaming and use the batch path."
        )

    def close(self) -> None:
        """Release the resident model with ``whisper_free``; idempotent.

        Unlike Whistle (whose engine exposes no unload entry point), whisper.cpp
        has ``whisper_free``, so the context is actually released and the adapter
        becomes loadable again by calling :meth:`load`.
        """
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        self.device = None
        if self._library is not None:
            self._library.free_context(handle)
