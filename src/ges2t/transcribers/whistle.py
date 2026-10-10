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

The streaming path is implemented by :class:`WhistleTranscriber.stream`, which
delegates the windowing to :func:`ges2t.streaming.drive` and supplies a private
:class:`_WhistleStreamingDecoder`. The decoder steps ``needle.Whistle.stream``
incrementally: one package-level generator per window, fed one chunk per
``process()`` call and ended at the window boundary so the generator's own
``stop()`` commits the tail (ADR 0011). The engine returns bare words, so the
decoder owns the inter-delta whitespace that ``drive()`` concatenates verbatim.

The ``needle`` import is lazy and bound to the module global :data:`_needle`, so
importing this module has no backend side effects and the test suite can
substitute a fake with no engine installed (ADR 0014).
"""

from __future__ import annotations

import math
import os
import time
from array import array
from collections import deque
from collections.abc import Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from ges2t import model_store, streaming
from ges2t.identifiers import WHISTLE_ENGINE
from ges2t.interfaces import (
    AudioBuffer,
    ProgressCallback,
    TranscriptionResult,
    TranscriptionSegment,
)
from ges2t.streaming import StreamingPass
from ges2t.transcribers.whistle_config import WhistleConfig

__all__ = [
    "MAX_SECONDS",
    "MIN_SPEECH_FRAMES",
    "MIN_UTTERANCE_SECONDS",
    "SAMPLE_RATE",
    "SPEECH_FRAME_RMS_THRESHOLD",
    "SPEECH_FRAME_SECONDS",
    "SPEECH_HIGHPASS_HZ",
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

#: A recording shorter than this is treated as "no speech": an accidental
#: hotkey double-tap captures a click, not an utterance. User-specified at
#: 100 ms; real utterances are orders of magnitude longer, so this cannot drop
#: one. Not configurable -- a constant is the right size for one number.
MIN_UTTERANCE_SECONDS: float = 0.1

#: Frame length for the sustained-energy gate. 20 ms is one 50 Hz mains cycle,
#: so a hum's per-frame energy is steady rather than beating with the frame
#: boundary; it is also short enough that a syllable spans several frames.
SPEECH_FRAME_SECONDS: float = 0.02

#: A frame counts as *active* when its AC RMS reaches this. Measured on the
#: target populations (addendum to ``001-comment.md``): a 5 ms key click gives
#: one active frame at every level that hallucinated; quiet speech that the real
#: engine still transcribes (clip5 at 0.01 gain) gives a run of 4; the
#: near-silent opening of clip5 gives no active frame; 0.005-amplitude 50 Hz hum
#: gives none after the high-pass. Deliberately low -- it sits at the quietest
#: gain the previous gate kept, so the sustained rule does not narrow the
#: quiet-speech floor.
SPEECH_FRAME_RMS_THRESHOLD: float = 0.0015

#: How many consecutive active frames are needed before the buffer is speech.
#: 3 frames = 60 ms. A single 5-30 ms transient never reaches it (measured:
#: run 1 at 5-10 ms, 2 at 20-30 ms); a 100 ms vowel is 5 frames and the
#: quietest 150 ms word tested is 3-4. A transient sustained for >=60 ms would
#: still pass -- recorded as a limitation, because the alternative (a longer
#: run) drops short real words.
MIN_SPEECH_FRAMES: int = 3

#: First-order high-pass corner applied to the *gate's* copy of the samples
#: (never to the audio handed to the engine). 50 Hz mains hum is AC, so mean
#: removal does not touch it; this attenuates it ~10 dB while leaving speech
#: (mostly >300 Hz) nearly intact. Measured: it drops hum at 0.005 and 0.01 to
#: zero active frames while costing real speech at most one frame.
SPEECH_HIGHPASS_HZ: float = 150.0


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


def _ac_rms(samples: Sequence[float]) -> float:
    """Return the RMS of ``samples`` **after removing the DC component**.

    A constant offset is a capture artefact, not sound: the engine hallucinated
    "Thank you." on a flat DC buffer at every level tested, from 0.0005 to 0.1,
    while AC noise at the same or higher level produced nothing. Subtracting the
    mean first makes that case measurable as the silence it is. ``math.fsum``
    keeps the sum accurate for the long buffers (up to 30 s) this runs on.
    """
    count = len(samples)
    if count == 0:
        return 0.0
    mean = math.fsum(samples) / count
    return math.sqrt(math.fsum((value - mean) ** 2 for value in samples) / count)


def _highpass(samples: Sequence[float], sample_rate: int) -> list[float]:
    """Return a first-order high-pass copy of ``samples`` at 150 Hz.

    Applied to the gate's **analysis** copy only -- the engine still receives the
    original bytes. That is the point: the filter exists to stop 50 Hz mains hum
    (which is AC, so :func:`_ac_rms` cannot remove it) from counting as speech,
    and keeping it out of the engine path means it cannot colour a transcription
    it lets through. ``y[n] = a * (y[n-1] + x[n] - x[n-1])``.
    """
    alpha = 1.0 / (1.0 + 2.0 * math.pi * SPEECH_HIGHPASS_HZ / sample_rate)
    filtered: list[float] = []
    previous_x = 0.0
    previous_y = 0.0
    for value in samples:
        current_y = alpha * (previous_y + value - previous_x)
        filtered.append(current_y)
        previous_x = value
        previous_y = current_y
    return filtered


def _longest_active_run(samples: Sequence[float], sample_rate: int) -> int:
    """Longest run of consecutive 20 ms frames whose AC RMS reaches the floor.

    Runs the high-pass first, then scans non-overlapping frames. Returns as soon
    as the run reaches :data:`MIN_SPEECH_FRAMES`, so the common speech case does
    not walk the whole buffer. A buffer shorter than one frame gives 0.
    """
    frame_size = max(1, round(SPEECH_FRAME_SECONDS * sample_rate))
    filtered = _highpass(samples, sample_rate)
    run = 0
    for start in range(0, len(filtered) - frame_size + 1, frame_size):
        frame = filtered[start : start + frame_size]
        if _ac_rms(frame) >= SPEECH_FRAME_RMS_THRESHOLD:
            run += 1
            if run >= MIN_SPEECH_FRAMES:
                return run
        else:
            run = 0
    return run


def _has_speech(buffer: AudioBuffer) -> bool:
    """Decide whether ``buffer`` contains anything worth transcribing.

    Two independent gates, both of which must pass:

    1. **Duration** -- a buffer shorter than :data:`MIN_UTTERANCE_SECONDS` is an
       accidental double-tap, not an utterance. Checked first because it is
       O(1).
    2. **Sustained energy** -- :func:`_longest_active_run` must reach
       :data:`MIN_SPEECH_FRAMES`. This is what an isolated transient fails: a
       5 ms key click occupies one frame, while even a short word occupies
       several consecutive frames. The threshold is deliberately low so that
       quiet speech is not caught by the same rule.

    The gates are combined with ``and``, so the evaluation order cannot change
    the outcome for real audio; it only decides how much work runs on a
    discarded buffer.
    """
    if len(buffer.samples) < MIN_UTTERANCE_SECONDS * buffer.sample_rate:
        return False
    return _longest_active_run(buffer.samples, buffer.sample_rate) >= MIN_SPEECH_FRAMES


def _speech_only(chunks: Iterable[AudioBuffer]) -> Iterator[AudioBuffer]:
    """Yield only the chunks whose audio passes :func:`_has_speech`.

    This is the streaming half of the gate. The engine contract is validated
    before the gate so that an invalid buffer still raises
    :class:`WhistleInputError` instead of being quietly filtered as silence;
    :meth:`_WhistleStreamingDecoder.process` validates again, which is
    idempotent. Dropping a chunk never ends the stream, so a pause between two
    spoken phrases is skipped without truncating the utterance.
    """
    for buffer in chunks:
        _validate_buffer(buffer)
        if _has_speech(buffer):
            yield buffer


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


def _stream_text(raw: Mapping[str, Any], key: str) -> str:
    """Return one of the engine's string fields, or ``""`` when it is absent.

    The streaming engine returns bare words (the package's own reader joins
    them with a space), so the adapter owns the separators: this helper must
    **not** add whitespace.
    """
    value = raw.get(key)
    return "" if value is None else str(value)


def _stream_language(raw: Mapping[str, Any]) -> str | None:
    """Return the language the engine reported for one streaming pass, if any."""
    value = raw.get("language")
    return None if value is None else str(value)


class _ChunkFeeder(Iterator[bytes]):
    """The pull-side iterator handed to one ``needle`` stream generator.

    ``needle.Whistle.stream()`` consumes an iterable and owns the engine's
    ``stop()`` call: it pulls one chunk per ``next()`` and issues ``stop()`` as
    soon as the iterable is exhausted. The adapter must choose when that
    happens -- at a window boundary, not at end of utterance -- so it feeds
    exactly one chunk through this object per
    :meth:`_WhistleStreamingDecoder.process` and ends the iterable in
    :meth:`_WhistleStreamingDecoder.flush`.

    A pull with nothing queued and no end signalled is a programming error, not
    end of input: it raises rather than silently terminating the engine stream
    early and losing audio.
    """

    def __init__(self) -> None:
        self._pending: deque[bytes] = deque()
        self._ended: bool = False

    def feed(self, chunk: bytes) -> None:
        """Queue the next chunk of raw float32 bytes for the generator to pull."""
        self._pending.append(chunk)

    def end(self) -> None:
        """Mark the input exhausted so the generator pulls its terminal ``stop()``."""
        self._ended = True

    def __iter__(self) -> _ChunkFeeder:
        return self

    def __next__(self) -> bytes:
        if self._pending:
            return self._pending.popleft()
        if self._ended:
            raise StopIteration
        raise WhistleTranscriberError(
            "the needle stream generator pulled a chunk that was never fed; the "
            "streaming decoder must feed exactly one chunk per process() call"
        )


class _WhistleStreamingDecoder:
    """Adapts ``needle.Whistle.stream`` to the generic :class:`streaming.StreamingDecoder`.

    One package-level generator is alive per window. :meth:`process` pushes the
    buffer into the generator's feeder and advances the generator once, so the
    generator's ``process`` call and its yielded dict line up one-to-one.
    :meth:`flush` ends the feeder, which makes the generator exhaust its input
    and issue the engine's ``stop()``; the resulting tail dict is the window's
    committed tail. A fresh generator is opened immediately afterwards, so the
    next window starts on a reset engine stream (ADR 0011).

    The engine returns bare words, while :func:`ges2t.streaming.drive`
    concatenates every delta **verbatim**, so this class owns the inter-delta
    whitespace: a non-empty delta that continues a non-empty transcript gets a
    single leading space. ``pending`` is display-only and is never used to
    reconstruct the transcript.

    ``engine`` is the already-:meth:`load` -ed ``needle.Whistle`` handle; the
    decoder never imports ``needle`` itself and never creates a second engine
    (ADR 0005, ADR 0010). ``language`` is ``WhistleConfig.language`` passed
    through verbatim; the empty string lets the engine auto-detect.
    """

    def __init__(self, engine: Any, language: str) -> None:
        self._engine: Any = engine
        self._language: str = language
        self._emitted: bool = False
        self._feeder: _ChunkFeeder = _ChunkFeeder()
        self._generator: Iterator[Mapping[str, Any]] = self._engine.stream(
            self._feeder, language=self._language
        )

    def _open_stream(self) -> None:
        """Start a fresh engine stream for the next window."""
        self._feeder = _ChunkFeeder()
        self._generator = self._engine.stream(
            self._feeder, language=self._language
        )

    def _format(self, text: str) -> str:
        """Return a concatenation-safe delta: prepend a space only for a continuation."""
        if not text:
            return ""
        if self._emitted:
            return " " + text
        self._emitted = True
        return text

    def process(self, buffer: AudioBuffer) -> StreamingPass:
        """Append ``buffer`` to the current window and return this pass's commit.

        The buffer is handed to the engine as raw float32 bytes, never a WAV
        path (ADR 0007). ``committed`` carries its own leading space when it
        continues the transcript; ``pending`` is display-only and is prefixed
        the same way so live text does not run words together (ADR 0011).
        """
        _validate_buffer(buffer)
        self._feeder.feed(_float32_bytes(buffer.samples))
        raw = next(self._generator)
        committed = self._format(_stream_text(raw, "text"))
        pending = _stream_text(raw, "pending")
        if pending and self._emitted:
            pending = " " + pending
        return StreamingPass(
            committed=committed,
            pending=pending,
            language=_stream_language(raw),
        )

    def flush(self) -> str:
        """Commit the window's tail, reset the engine stream, return the tail text.

        Ending the feeder makes the package generator exhaust its input and
        call ``needle_stream_transcribe_stop``, which commits the unconfirmed
        tail and resets the engine. The generator is then exhausted and a fresh
        one is opened, so the next :meth:`process` starts a new stream.
        """
        self._feeder.end()
        raw = next(self._generator)
        tail = self._format(_stream_text(raw, "text"))
        try:
            next(self._generator)
        except StopIteration:
            pass
        self._open_stream()
        return tail


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
        self.name: str = WHISTLE_ENGINE
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
        limit; it never truncates silently (ADR 0005). A recording the gate
        judges to contain no speech (:func:`_has_speech`: shorter than 100 ms,
        or no run of consecutive active frames) returns an empty result
        **without calling the engine at all** -- inference on silence is both
        meaningless and billed, and the engine hallucinates phrases like
        "Thank you." on key clicks, DC offset and mains hum. Samples that pass
        the gate are handed to the engine as raw float32 bytes, and its
        ``text``/``language``/``ttft_ms``/``decode_tps`` are returned unchanged
        (ADR 0007, ADR 0012).
        """
        engine = self._require_loaded()
        _validate_buffer(buffer)
        if not _has_speech(buffer):
            return TranscriptionResult(text="", language=None, ttft_ms=None, decode_tps=None)
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
        """Transcribe a live chunk iterable on the windowed streaming path (ADR 0011).

        The window/commit algorithm is :func:`ges2t.streaming.drive`: the
        adapter only supplies the engine-facing decoder, while ``drive`` groups
        chunks, flushes at the configured window boundary, accumulates the
        committed deltas, and yields display-only partials plus one final
        segment. The knobs come from ``[engine.whistle]``
        (``window_seconds``/``chunk_seconds``) and are validated there, not
        re-implemented here.

        The silence gate is applied **per incoming chunk** by
        :func:`_speech_only`: a chunk that is not speech (the same
        duration + sustained-energy test as the batch path) is never forwarded
        to ``drive`` and therefore never reaches the engine. This is correct for
        streaming because silence carries no words -- skipping a pause
        *mid-sentence* loses nothing and, crucially, does **not** end the
        utterance; the speech chunks on either side of it are still fed into the
        same stream and committed normally. A fully silent utterance forwards no
        chunk at all, so the engine performs no per-chunk pass. A chunk that is
        only a key click fails the sustained-energy test and is dropped just like
        a silent one. The minimum-duration rule is worth applying per chunk here
        too: the default 1 s chunking makes a sub-100 ms buffer either a whole
        double-tap or a negligible tail. The alternative -- deciding the gate on
        the whole utterance -- would require buffering all audio and defeat the
        memory bound streaming exists for (ADR 0011).

        The call fails eagerly with :class:`WhistleNotLoadedError` when
        :meth:`load` has not run, rather than at first iteration, so a caller
        does not start capturing audio for a stream that cannot run.
        """
        engine = self._require_loaded()
        decoder = _WhistleStreamingDecoder(engine, self.config.language)
        return streaming.drive(
            decoder,
            _speech_only(chunks),
            window_seconds=self.config.window_seconds,
            chunk_seconds=self.config.chunk_seconds,
        )

    def close(self) -> None:
        """Drop ges2t's reference to the process-global model.

        The engine exposes no unload entry point, so this cannot free it; it
        makes the adapter safe to reuse and signals that the model is no longer
        owned (ADR 0005, ADR 0015).
        """
        self._engine = None
