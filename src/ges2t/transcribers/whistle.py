"""The Whistle/Needle 3 batch transcriber adapter (ADR 0005, ADR 0007, ADR 0010).

This module is the **only** place in ges2t allowed to import the speech engine's
Python package (``needle``, installed by the ``cactus-needle`` distribution;
ADR 0010). Everything else depends on the
:class:`~ges2t.interfaces.Transcriber` Protocol, so swapping the engine stays a
one-module change (ADR 0006).

The batch path, in brief:

* :meth:`WhistleTranscriber.ensure_model` is the only path that may touch the
  network. It delegates to :mod:`ges2t.model_store`, which owns the pins, the
  checksums, and the atomic install (ADR 0017, ADR 0018).
* :meth:`WhistleTranscriber.load` writes
  :func:`ges2t.model_store.env_overrides` into the environment, then constructs
  the engine against the managed weights file. The engine is loaded
  **in-process** (ADR 0007); there is no subprocess hop. Immediately after
  construction it asserts that the weights file, and the shared library the
  engine actually opened, are the managed pinned ones -- so a wrong or missing
  override fails loudly here instead of silently fetching something else.
* :meth:`WhistleTranscriber.transcribe` enforces the engine's hard audio
  contract -- 16 kHz mono float32 in ``[-1, 1]``, at most 30 s per call -- and
  hands the samples to the engine as **raw little-endian float32 bytes**, never
  as a WAV path (re-parsing a WAV costs 0.036 s per 5 s and 0.218 s per 30 s of
  pure waste; ADR 0007). It returns the engine's ``text``, ``language``,
  ``ttft_ms`` and ``decode_tps`` unchanged: no stripping, no case folding, no
  trailing space (ADR 0012 -- the injector adds the space).

``numpy`` is deliberately **not** used: the conversion is the standard library's
``array('f', samples).tobytes()``.

The ``needle`` import is lazy and bound to the module global :data:`_needle`, so
importing this module has no backend side effects and the test suite can
substitute a fake with no engine installed (ADR 0014). :meth:`stream` is a
:class:`NotImplementedError` stub: the streaming adapter ticket owns that path
(ADR 0011), and implementing it here would make that ticket a rewrite.
"""

from __future__ import annotations

import os
import time
from array import array
from collections.abc import Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from ges2t import model_store
from ges2t.interfaces import (
    AudioBuffer,
    ProgressCallback,
    TranscriptionResult,
    TranscriptionSegment,
)
from ges2t.transcribers.whistle_config import WhistleConfig

__all__ = [
    "MAX_SECONDS",
    "SAMPLE_RATE",
    "WhistleInputError",
    "WhistleLoadError",
    "WhistleNotLoadedError",
    "WhistleTranscriber",
    "WhistleTranscriberError",
]

#: The engine's only accepted input rate: 16 kHz (ADR 0005).
SAMPLE_RATE: int = 16000

#: The engine's per-call ceiling in seconds; longer audio needs the streaming
#: path (ADR 0005, ADR 0011).
MAX_SECONDS: float = 30.0

#: The imported ``needle`` package, bound lazily by :func:`_import_needle` and
#: replaceable by tests. ``Any`` is deliberate: ``needle`` ships no type
#: information, so this is a genuine third-party boundary (ADR 0019).
_needle: Any = None


class WhistleTranscriberError(Exception):
    """Base class for every failure raised by this adapter."""


class WhistleNotLoadedError(WhistleTranscriberError):
    """``transcribe()`` was called before :meth:`WhistleTranscriber.load`."""


class WhistleInputError(WhistleTranscriberError):
    """The buffer violates the engine's hard audio contract (ADR 0005)."""


class WhistleLoadError(WhistleTranscriberError):
    """The engine loaded against artifacts that are not the managed pins (ADR 0007)."""


def _import_needle() -> Any:
    """Import ``needle`` on first use and cache it in :data:`_needle`.

    The import happens here rather than at module import time, so importing this
    module pulls in no backend (ADR 0010) and a test can assign a fake to
    :data:`_needle` and have it returned untouched.
    """
    global _needle
    if _needle is None:
        import needle

        _needle = needle
    return _needle


def _apply_env_overrides() -> None:
    """Write the managed artifact paths and telemetry opt-outs into ``os.environ``.

    This is the one place ges2t applies :func:`model_store.env_overrides` (ADR
    0007, ADR 0017). The backend reads ``NEEDLE_WHISTLE_WEIGHTS`` and
    ``NEEDLE3_LIB_PATH`` while loading, and ``HF_HUB_OFFLINE=1`` turns a wrong
    path into a loud local error instead of a silent download.
    """
    os.environ.update(model_store.env_overrides())


def _resolved(path: Path) -> Path:
    """Return an absolute, symlink-resolved form for path comparison."""
    return path.expanduser().resolve()


def _float32_bytes(samples: Sequence[float]) -> bytes:
    """Return ``samples`` as raw little-endian float32 bytes.

    The engine accepts ``bytes`` only as **raw** little-endian float32; int16
    bytes are silently misread as garbage, and a WAV path pays a re-parse we do
    not need (ADR 0007). ``array('f', ...).tobytes()`` is native-endian, which
    is little-endian on the pinned x86-64 target. No numpy.
    """
    return array("f", samples).tobytes()


def _validate_buffer(buffer: AudioBuffer) -> None:
    """Raise :class:`WhistleInputError` unless the buffer honours the engine contract.

    Both checks are hard limits, not hints: a wrong rate is not resampled and an
    over-long buffer is not truncated, because a silent transformation would
    make the transcription quietly wrong (ADR 0005).
    """
    if buffer.sample_rate != SAMPLE_RATE:
        raise WhistleInputError(
            f"the Whistle engine requires {SAMPLE_RATE} Hz mono audio, but the "
            f"buffer is {buffer.sample_rate} Hz; resample before the transcriber "
            "rather than expecting it to do so silently (ADR 0005)"
        )
    duration_seconds = len(buffer.samples) / SAMPLE_RATE
    if duration_seconds > MAX_SECONDS:
        raise WhistleInputError(
            f"the Whistle engine accepts at most {MAX_SECONDS:g} s of audio per "
            f"call, but the buffer is {duration_seconds:.3f} s "
            f"({len(buffer.samples)} samples at {SAMPLE_RATE} Hz); use the "
            "streaming path for longer audio, never silent truncation "
            "(ADR 0005, ADR 0011)"
        )


def _transcription_from_engine(raw: Mapping[str, Any]) -> TranscriptionResult:
    """Convert the engine's JSON object into the interface's frozen result.

    ``text`` crosses **unchanged** (ADR 0012); the engine's own timings are
    passed through rather than recomputed, and are ``None`` when the engine did
    not report them (ADR 0005).
    """
    language = raw.get("language")
    ttft_ms = raw.get("ttft_ms")
    decode_tps = raw.get("decode_tps")
    return TranscriptionResult(
        text=raw["text"],
        language=None if language is None else str(language),
        ttft_ms=None if ttft_ms is None else float(ttft_ms),
        decode_tps=None if decode_tps is None else float(decode_tps),
    )


def _loaded_engine_path(needle_module: Any) -> Path | None:
    """Return the path of the shared library the engine actually opened.

    ``needle`` exposes no public accessor for this, so the ctypes handle it
    caches is read directly; ``CDLL._name`` is the path it was opened with. This
    is the "engine it is using" half of the ADR 0007 load-time assertion.
    ``None`` means the package's internals no longer expose the path, and the
    assertion fails closed.
    """
    try:
        library = needle_module.agent.whistle._lib()
    except (AttributeError, OSError):
        return None
    name = getattr(library, "_name", None)
    if not isinstance(name, str) or not name:
        return None
    return Path(name)


class WhistleTranscriber:
    """A :class:`~ges2t.interfaces.Transcriber` for Whistle on the Needle 3 engine.

    ``config`` carries the engine-specific ``[engine.whistle]`` settings (ADR
    0009); ``config.language`` is passed to the engine verbatim, with the empty
    string meaning auto-detect. ``model_path_override`` names a specific weights
    file instead of the managed one -- a seam for tests and for a caller that has
    taken ownership of its own artifact.

    ``load_seconds`` records how long the most recent successful :meth:`load`
    took, or is ``None`` before the first load. The engine is process-global and
    not thread-safe (ADR 0005): this class owns one instance and makes one call
    at a time; no locking is provided because concurrent use is not supported in
    the first place (ADR 0015).
    """

    def __init__(
        self,
        config: WhistleConfig,
        model_path_override: Path | None = None,
    ) -> None:
        self.name: str = "whistle"
        self.supports_streaming: bool = True
        self.config = config
        self._model_path_override: Path | None = (
            Path(model_path_override) if model_path_override is not None else None
        )
        self._engine: Any = None
        self.load_seconds: float | None = None

    def _weights_path(self) -> Path:
        """The weights file this transcriber will load: the override or the managed pin."""
        if self._model_path_override is not None:
            return self._model_path_override
        return model_store.model_path()

    def ensure_model(self, progress: ProgressCallback | None = None) -> Path:
        """Ensure the managed engine and Whistle weights exist; download nothing else.

        Delegates to :mod:`ges2t.model_store`, the only module allowed to touch
        the network (ADR 0018). When ``model_path_override`` was supplied the
        caller owns the weights file, so only the engine is ensured; otherwise
        both artifacts are. ``progress`` is the interface's fraction callback and
        is forwarded to each acquisition unchanged.
        """
        if self._model_path_override is not None:
            model_store.ensure_engine(progress=progress)
            return self._model_path_override
        model = model_store.ensure_model(progress=progress)
        model_store.ensure_engine(progress=progress)
        return model

    def load(self) -> None:
        """Load the pinned engine and weights; idempotent.

        Applies the managed environment overrides first, then constructs
        ``needle.Whistle`` from the managed weights path, and finally asserts
        that the weights file and the shared library the engine actually opened
        are the managed pins. Any mismatch raises :class:`WhistleLoadError`
        rather than letting the backend fetch something else (ADR 0007).
        """
        if self._engine is not None:
            return
        _apply_env_overrides()
        needle_module = _import_needle()
        weights_path = self._weights_path()
        started = time.monotonic()
        engine = needle_module.Whistle(weights=str(weights_path))
        self.load_seconds = time.monotonic() - started
        self._assert_managed_artifacts(
            needle_module, engine, weights_path=weights_path
        )
        self._engine = engine

    def _assert_managed_artifacts(
        self,
        needle_module: Any,
        engine: Any,
        *,
        weights_path: Path,
    ) -> None:
        """Assert the loaded weights and engine library are the managed paths (ADR 0007)."""
        expected_weights = _resolved(weights_path)
        actual_weights = _resolved(Path(str(engine.weights)))
        if actual_weights != expected_weights:
            raise WhistleLoadError(
                "the Whistle engine loaded weights from "
                f"{actual_weights}, but ges2t pinned {expected_weights}: refusing "
                "to run against unmanaged weights (ADR 0007). Run 'ges2t setup' "
                "and remove any stale NEEDLE_WHISTLE_WEIGHTS override."
            )
        expected_engine = _resolved(model_store.engine_path())
        actual_engine = _loaded_engine_path(needle_module)
        if actual_engine is None or _resolved(actual_engine) != expected_engine:
            shown = actual_engine if actual_engine is not None else "<unknown>"
            raise WhistleLoadError(
                f"the Whistle engine loaded {shown}, but ges2t pinned "
                f"{expected_engine}: refusing to run against an unmanaged engine "
                "(ADR 0007). Run 'ges2t setup' and remove any stale "
                "NEEDLE3_LIB_PATH override."
            )

    def transcribe(self, buffer: AudioBuffer) -> TranscriptionResult:
        """Transcribe one complete utterance (the batch path).

        Validates the engine's hard audio contract first: 16 kHz and at most
        30 s per call. A violation raises :class:`WhistleInputError` naming the
        limit; it never truncates silently (ADR 0005). The samples are handed to
        the engine as raw float32 bytes, and its ``text``/``language``/
        ``ttft_ms``/``decode_tps`` are returned unchanged (ADR 0007, ADR 0012).
        """
        engine = self._require_loaded()
        _validate_buffer(buffer)
        raw_result = engine.transcribe(
            _float32_bytes(buffer.samples),
            language=self.config.language,
        )
        return _transcription_from_engine(raw_result)

    def _require_loaded(self) -> Any:
        """Return the loaded engine or raise a clear "call load() first" error."""
        if self._engine is None:
            raise WhistleNotLoadedError(
                "WhistleTranscriber.transcribe() was called before load(); call "
                "ensure_model() and load() first (ADR 0010)"
            )
        return self._engine

    def stream(
        self, chunks: Iterable[AudioBuffer]
    ) -> Iterator[TranscriptionSegment]:
        """Not implemented here: the streaming adapter ticket owns this path.

        ``supports_streaming`` reports the engine's capability, but implementing
        the streaming loop in the batch adapter would make the dedicated
        streaming ticket a rewrite rather than an addition (ADR 0011).
        """
        raise NotImplementedError(
            "WhistleTranscriber streaming is implemented by the streaming "
            "adapter ticket (ADR 0011); only the batch transcribe() path exists "
            "here"
        )

    def close(self) -> None:
        """Drop ges2t's reference to the process-global model.

        The engine exposes no unload entry point, so this cannot free it; it
        makes the adapter safe to reuse and signals that the model is no longer
        owned (ADR 0005, ADR 0015).
        """
        self._engine = None
