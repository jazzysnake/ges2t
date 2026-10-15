"""Tests for the NeMo-Speech.cpp batch transcriber (ticket 58, ADR 0022).

Two levels of test, deliberately:

* The **Protocol contract** is driven by :class:`FakeNemoSpeechLibrary`, a fake
  that models the real library lifecycle (create one recognizer per load, decode
  against a live handle, free it, and fail loudly on a freed or failing handle)
  rather than a simplified one. It records the model path and ``use_gpu`` and the
  samples and sample rate, which is where the device rule, the model override and
  the 16 kHz contract are observable without a native library.
* **One real-seam test** drives the actual ``libnemo_speech_asr_c.so`` from the
  research scratch tree through :class:`CtypesNemoSpeechLibrary`. It **skips
  cleanly** when the library is absent -- never silently passes (ADR 0014). The
  heavier full-decode real-seam test is opt-in via ``GES2T_NEMO_REAL_SEAM=1`` so
  the default suite stays offline and does not load ~1 GB of weights on a run of
  ``pytest``.

The ``[engine.nemo_speech]`` config block is exercised here too, because the
ticket scopes its test file to ``tests/test_nemo_speech.py``.
"""

from __future__ import annotations

import os
import wave
from array import array
from collections.abc import Sequence
from pathlib import Path

import pytest

from ges2t.config import load_config
from ges2t.interfaces import AudioBuffer, Transcriber
from ges2t.nemo_engine import NemoDevice
from ges2t.transcribers.nemo_speech import (
    ENTRY_LIBRARY_NAME,
    GPU_CPU,
    GPU_DEVICE_INDEX,
    SAMPLE_RATE,
    STATUS_OK,
    CtypesNemoSpeechLibrary,
    Device,
    NemoSpeechDecodeError,
    NemoSpeechInputError,
    NemoSpeechLibrary,
    NemoSpeechLoadError,
    NemoSpeechNotLoadedError,
    NemoSpeechStreamingUnsupportedError,
    NemoSpeechTranscript,
    NemoSpeechTranscriber,
)
from ges2t.transcribers.nemo_speech_config import (
    BLOCK_NAME,
    DEFAULT_CHUNK_SECONDS,
    DEFAULT_DEVICE,
    DEFAULT_MODEL,
    GGUF_SUFFIX,
    KNOWN_KEYS,
    NemoDevicePreference,
    NemoSpeechConfig,
    NemoSpeechConfigError,
    NemoSpeechConfigResult,
)

# --- the fake library ---------------------------------------------------------


class FakeNemoSpeechLibrary:
    """A :class:`NemoSpeechLibrary` that records calls and models the lifecycle.

    It is deliberately not simpler than the real library: :meth:`create_recognizer`
    returns a **fresh** handle each call (so a close-then-load observes a new
    recognizer), :meth:`recognize` refuses a handle that has been freed (the real
    library would fault), and create/recognize failures are injectable.
    """

    def __init__(
        self,
        *,
        transcript: NemoSpeechTranscript | None = None,
        raise_on_create: Exception | None = None,
        raise_on_recognize: Exception | None = None,
    ) -> None:
        self.transcript = (
            transcript
            if transcript is not None
            else NemoSpeechTranscript(text="hello world", language="en")
        )
        self.raise_on_create = raise_on_create
        self.raise_on_recognize = raise_on_recognize
        self.created: list[tuple[Path, bool]] = []
        self.freed: list[int] = []
        self.recognized: list[tuple[int, tuple[float, ...], int]] = []
        self._next_handle: int = 1
        self._live: set[int] = set()

    def version(self) -> str:
        return "0.2.0-fake"

    def create_recognizer(self, model_path: Path, *, use_gpu: bool) -> int:
        if self.raise_on_create is not None:
            raise self.raise_on_create
        handle = self._next_handle
        self._next_handle += 1
        self._live.add(handle)
        self.created.append((Path(model_path), use_gpu))
        return handle

    def free_recognizer(self, handle: int) -> None:
        self._live.discard(handle)
        self.freed.append(handle)

    def recognize(
        self,
        handle: int,
        samples: Sequence[float],
        *,
        sample_rate: int,
    ) -> NemoSpeechTranscript:
        if handle not in self._live:
            raise NemoSpeechDecodeError(f"recognizer {handle} has been freed")
        if self.raise_on_recognize is not None:
            raise self.raise_on_recognize
        self.recognized.append((handle, tuple(samples), sample_rate))
        return self.transcript


# --- helpers ------------------------------------------------------------------


def _write_model(tmp_path: Path, name: str = "model.gguf") -> Path:
    """Create a stand-in GGUF file (its bytes are never read by a fake library)."""
    model = tmp_path / name
    model.write_bytes(b"not a real gguf model")
    return model


def _cpu_device() -> NemoDevice:
    """A host device record with no Vulkan loader."""
    return NemoDevice(vulkan_library=None)


def _vulkan_device() -> NemoDevice:
    """A host device record with a Vulkan loader."""
    return NemoDevice(vulkan_library=Path("/usr/lib/libvulkan.so.1"))


def _buffer(samples: Sequence[float], sample_rate: int = SAMPLE_RATE) -> AudioBuffer:
    """Build an :class:`AudioBuffer` with the given rate."""
    return AudioBuffer(samples=list(samples), sample_rate=sample_rate)


def _loaded(
    tmp_path: Path,
    *,
    config: NemoSpeechConfig | None = None,
    library: FakeNemoSpeechLibrary | None = None,
    device: NemoDevice | None = None,
) -> tuple[NemoSpeechTranscriber, FakeNemoSpeechLibrary]:
    """Construct and load a transcriber over a stand-in model and a fake library."""
    fake = library if library is not None else FakeNemoSpeechLibrary()
    transcriber = NemoSpeechTranscriber(
        config if config is not None else NemoSpeechConfig(),
        model_path_override=_write_model(tmp_path),
        library=fake,
        device_detection=device if device is not None else _cpu_device(),
    )
    transcriber.load()
    return transcriber, fake


# --- the config block: defaults -----------------------------------------------


def test_an_absent_block_yields_all_defaults() -> None:
    result = NemoSpeechConfig.from_mapping({})
    assert isinstance(result, NemoSpeechConfigResult)
    assert result.config == NemoSpeechConfig()
    assert result.config.device is NemoDevicePreference.AUTO
    assert result.config.chunk_seconds == 0.16
    assert result.config.model is None
    assert result.warnings == []


def test_default_values_match_the_documented_schema() -> None:
    config = NemoSpeechConfig()
    assert config.device is DEFAULT_DEVICE is NemoDevicePreference.AUTO
    assert config.chunk_seconds == DEFAULT_CHUNK_SECONDS == 0.16
    assert config.model == DEFAULT_MODEL is None
    assert BLOCK_NAME == "engine.nemo_speech"
    assert KNOWN_KEYS == frozenset({"device", "chunk_seconds", "model"})
    assert GGUF_SUFFIX == ".gguf"


def test_every_device_spelling_is_accepted() -> None:
    for value in ("auto", "vulkan", "cpu"):
        result = NemoSpeechConfig.from_mapping({"device": value})
        assert result.config.device is NemoDevicePreference(value)


def test_a_non_string_device_is_rejected() -> None:
    with pytest.raises(NemoSpeechConfigError) as excinfo:
        NemoSpeechConfig.from_mapping({"device": 5})
    assert "device" in str(excinfo.value)
    assert "expected a string" in str(excinfo.value)


def test_an_unknown_device_names_the_valid_values() -> None:
    with pytest.raises(NemoSpeechConfigError) as excinfo:
        NemoSpeechConfig.from_mapping({"device": "gpu"})
    message = str(excinfo.value)
    assert "'gpu'" in message
    assert "auto" in message and "vulkan" in message and "cpu" in message


def test_chunk_seconds_accepts_int_and_normalises_to_float() -> None:
    result = NemoSpeechConfig.from_mapping({"chunk_seconds": 1})
    assert result.config.chunk_seconds == 1.0
    assert isinstance(result.config.chunk_seconds, float)


def test_chunk_seconds_accepts_float() -> None:
    assert (
        NemoSpeechConfig.from_mapping({"chunk_seconds": 0.16}).config.chunk_seconds
        == 0.16
    )


def test_chunk_seconds_rejects_a_bool() -> None:
    with pytest.raises(NemoSpeechConfigError) as excinfo:
        NemoSpeechConfig.from_mapping({"chunk_seconds": True})
    assert "chunk_seconds" in str(excinfo.value)


def test_chunk_seconds_rejects_a_non_number() -> None:
    with pytest.raises(NemoSpeechConfigError):
        NemoSpeechConfig.from_mapping({"chunk_seconds": "0.16"})


def test_chunk_seconds_rejects_zero_and_negative() -> None:
    for value in (0, -0.1):
        with pytest.raises(NemoSpeechConfigError) as excinfo:
            NemoSpeechConfig.from_mapping({"chunk_seconds": value})
        assert "greater than 0" in str(excinfo.value)


def test_chunk_seconds_rejects_infinity() -> None:
    with pytest.raises(NemoSpeechConfigError) as excinfo:
        NemoSpeechConfig.from_mapping({"chunk_seconds": float("inf")})
    assert "finite" in str(excinfo.value)


def test_a_model_path_is_accepted() -> None:
    result = NemoSpeechConfig.from_mapping({"model": "/models/custom.gguf"})
    assert result.config.model == "/models/custom.gguf"


def test_a_non_gguf_model_value_is_rejected() -> None:
    with pytest.raises(NemoSpeechConfigError) as excinfo:
        NemoSpeechConfig.from_mapping({"model": "medium-q5_0"})
    assert ".gguf" in str(excinfo.value)


def test_a_non_string_model_value_is_rejected() -> None:
    with pytest.raises(NemoSpeechConfigError) as excinfo:
        NemoSpeechConfig.from_mapping({"model": 3})
    assert "expected a string path" in str(excinfo.value)


def test_window_seconds_produces_the_existing_unknown_key_warning() -> None:
    # ADR 0022: window_seconds has no effect for this engine, and the block must
    # say so rather than silently accept it. It is not a key of this block, so the
    # ordinary unknown-key warning fires -- one wording, no special case.
    result = NemoSpeechConfig.from_mapping({"window_seconds": 5.0})
    assert result.warnings == ["engine.nemo_speech: unknown key 'window_seconds'"]
    # ...and it does not change the accepted config.
    assert result.config == NemoSpeechConfig()


def test_an_unknown_key_is_warned_and_ignored() -> None:
    result = NemoSpeechConfig.from_mapping({"threads": 4})
    assert result.warnings == ["engine.nemo_speech: unknown key 'threads'"]
    assert result.config == NemoSpeechConfig()


# --- the generic selector accepts the engine ----------------------------------


def test_the_generic_config_accepts_nemo_speech(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[transcriber]\nengine = "nemo_speech"\n', encoding="utf-8")
    config = load_config(path).config
    assert config.transcriber.engine == "nemo_speech"
    # A streaming engine: an omitted streaming.enabled resolves to True, so no
    # [streaming] block is required (ticket 50's engine-aware default).
    assert config.streaming.enabled is True


def test_an_explicit_streaming_true_is_not_refused_for_nemo_speech(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        '[transcriber]\nengine = "nemo_speech"\n\n[streaming]\nenabled = true\n',
        encoding="utf-8",
    )
    assert load_config(path).config.streaming.enabled is True


# --- Protocol contract --------------------------------------------------------


def test_transcriber_satisfies_the_protocol_and_declares_batch_only_for_now() -> None:
    transcriber = NemoSpeechTranscriber(
        NemoSpeechConfig(), library=FakeNemoSpeechLibrary()
    )
    assert isinstance(transcriber, Transcriber)
    assert transcriber.name == "nemo_speech"
    # Ticket 58: the engine is batch-only until ticket 59 flips this.
    assert transcriber.supports_streaming is False


def test_the_fake_satisfies_the_library_protocol() -> None:
    assert isinstance(FakeNemoSpeechLibrary(), NemoSpeechLibrary)


# --- model path / acquisition -------------------------------------------------


def test_load_uses_the_model_override(tmp_path: Path) -> None:
    model = _write_model(tmp_path)
    fake = FakeNemoSpeechLibrary()
    transcriber = NemoSpeechTranscriber(
        NemoSpeechConfig(),
        model_path_override=model,
        library=fake,
        device_detection=_cpu_device(),
    )
    transcriber.load()
    assert fake.created == [(model, False)]


def test_a_configured_model_path_is_loaded(tmp_path: Path) -> None:
    model = _write_model(tmp_path, "custom.gguf")
    fake = FakeNemoSpeechLibrary()
    transcriber = NemoSpeechTranscriber(
        NemoSpeechConfig(model=str(model)),
        library=fake,
        device_detection=_cpu_device(),
    )
    transcriber.load()
    assert fake.created == [(model, False)]
    assert transcriber.ensure_model() == model


def test_ensure_model_returns_the_override_without_touching_the_store(
    tmp_path: Path,
) -> None:
    model = _write_model(tmp_path)
    transcriber = NemoSpeechTranscriber(
        NemoSpeechConfig(),
        model_path_override=model,
        library=FakeNemoSpeechLibrary(),
    )
    assert transcriber.ensure_model() == model


def test_ensure_model_delegates_to_the_model_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[object] = []

    def fake_ensure(*, fetcher: object = None, progress: object = None) -> Path:
        seen.append(progress)
        return tmp_path / "model.gguf"

    monkeypatch.setattr(
        "ges2t.transcribers.nemo_speech.model_store.ensure_nemo_model",
        fake_ensure,
    )
    transcriber = NemoSpeechTranscriber(
        NemoSpeechConfig(), library=FakeNemoSpeechLibrary()
    )

    def progress(fraction: float) -> None:
        pass

    assert transcriber.ensure_model(progress) == tmp_path / "model.gguf"
    assert seen == [progress]


# --- load / close idempotency and failures ------------------------------------


def test_load_is_idempotent_and_the_recognizer_stays_resident(tmp_path: Path) -> None:
    transcriber, fake = _loaded(tmp_path)
    transcriber.load()
    transcriber.load()
    transcriber.transcribe(_buffer([0.0] * 1600))
    transcriber.transcribe(_buffer([0.0] * 1600))

    assert len(fake.created) == 1  # created once
    assert [handle for handle, _, _ in fake.recognized] == [1, 1]  # same recognizer


def test_load_records_the_load_time(tmp_path: Path) -> None:
    transcriber, _ = _loaded(tmp_path)
    assert transcriber.load_seconds is not None
    assert transcriber.load_seconds >= 0.0


def test_load_fails_when_the_model_is_missing(tmp_path: Path) -> None:
    transcriber = NemoSpeechTranscriber(
        NemoSpeechConfig(),
        model_path_override=tmp_path / "missing.gguf",
        library=FakeNemoSpeechLibrary(),
    )
    with pytest.raises(NemoSpeechLoadError) as excinfo:
        transcriber.load()
    message = str(excinfo.value)
    assert "missing.gguf" in message
    assert "ges2t setup nemo_speech" in message


def test_load_fails_when_the_library_is_missing(tmp_path: Path) -> None:
    model = _write_model(tmp_path)
    library = tmp_path / ENTRY_LIBRARY_NAME
    transcriber = NemoSpeechTranscriber(
        NemoSpeechConfig(),
        model_path_override=model,
        library_path_override=library,
        engine_directory=tmp_path,
        device_detection=_cpu_device(),
    )
    with pytest.raises(NemoSpeechLoadError) as excinfo:
        transcriber.load()
    assert ENTRY_LIBRARY_NAME in str(excinfo.value)


def test_a_create_failure_propagates_and_leaves_the_adapter_unloaded(
    tmp_path: Path,
) -> None:
    fake = FakeNemoSpeechLibrary(
        raise_on_create=NemoSpeechLoadError("corrupt model file")
    )
    transcriber = NemoSpeechTranscriber(
        NemoSpeechConfig(),
        model_path_override=_write_model(tmp_path),
        library=fake,
        device_detection=_cpu_device(),
    )
    with pytest.raises(NemoSpeechLoadError):
        transcriber.load()
    assert transcriber.device is None
    assert transcriber._handle is None  # noqa: SLF001 - asserting the internal state


def test_close_releases_the_recognizer_and_is_idempotent(tmp_path: Path) -> None:
    transcriber, fake = _loaded(tmp_path)
    transcriber.close()
    transcriber.close()
    assert fake.freed == [1]


def test_close_then_load_creates_a_new_recognizer(tmp_path: Path) -> None:
    transcriber, fake = _loaded(tmp_path)
    transcriber.close()
    transcriber.load()
    assert len(fake.created) == 2


def test_a_freed_recognizer_cannot_be_used(tmp_path: Path) -> None:
    # The fake must not be simpler than the real library: reusing a freed handle
    # is a fault, and the adapter never does it (it clears its own handle).
    fake = FakeNemoSpeechLibrary()
    handle = fake.create_recognizer(_write_model(tmp_path), use_gpu=False)
    fake.free_recognizer(handle)
    with pytest.raises(NemoSpeechDecodeError):
        fake.recognize(handle, [0.0], sample_rate=SAMPLE_RATE)


# --- transcribe contract ------------------------------------------------------


def test_transcribe_returns_text_and_omits_engine_timings(tmp_path: Path) -> None:
    fake = FakeNemoSpeechLibrary(
        transcript=NemoSpeechTranscript(text="the text", language="en")
    )
    transcriber, _ = _loaded(tmp_path, library=fake)
    result = transcriber.transcribe(_buffer([0.0] * 1600))
    assert result.text == "the text"
    assert result.language == "en"
    assert result.ttft_ms is None
    assert result.decode_tps is None
    assert transcriber.last_transcribe_seconds is not None


def test_transcribe_forwards_the_sample_rate(tmp_path: Path) -> None:
    transcriber, fake = _loaded(tmp_path)
    transcriber.transcribe(_buffer([0.0] * 1600))
    assert fake.recognized[0][2] == SAMPLE_RATE


def test_transcribe_forwards_the_actual_audio_payload(tmp_path: Path) -> None:
    # The seam's central data contract: the *actual* float32 samples reach the
    # library -- correct values, correct length, non-empty -- not merely that
    # ``recognize`` was called. A mutation that forwards an empty or substituted
    # buffer must fail here, not sail through green (ADR 0014).
    transcriber, fake = _loaded(tmp_path)
    samples = [0.0, 0.25, -0.5, 1.0, -1.0, 0.125]
    result = transcriber.transcribe(_buffer(samples))

    assert len(fake.recognized) == 1
    _, forwarded, _ = fake.recognized[0]
    assert forwarded == tuple(samples)
    assert len(forwarded) == len(samples)
    assert forwarded  # the payload is not dropped
    assert result.text == "hello world"


def test_transcribe_before_load_is_a_typed_error(tmp_path: Path) -> None:
    transcriber = NemoSpeechTranscriber(
        NemoSpeechConfig(),
        model_path_override=_write_model(tmp_path),
        library=FakeNemoSpeechLibrary(),
    )
    with pytest.raises(NemoSpeechNotLoadedError):
        transcriber.transcribe(_buffer([0.0] * 1600))


def test_transcribe_rejects_a_non_16khz_buffer(tmp_path: Path) -> None:
    transcriber, _ = _loaded(tmp_path)
    with pytest.raises(NemoSpeechInputError) as excinfo:
        transcriber.transcribe(_buffer([0.0] * 1600, sample_rate=48000))
    assert "48000" in str(excinfo.value)


def test_transcribe_of_empty_audio_does_not_decode(tmp_path: Path) -> None:
    transcriber, fake = _loaded(tmp_path)
    result = transcriber.transcribe(_buffer([]))
    assert result.text == ""
    assert fake.recognized == []


def test_stream_refuses_loudly(tmp_path: Path) -> None:
    transcriber, _ = _loaded(tmp_path)
    with pytest.raises(NemoSpeechStreamingUnsupportedError):
        transcriber.stream([_buffer([0.0] * 160)])


# --- device rule --------------------------------------------------------------


def test_resolve_device_auto_uses_cpu_without_vulkan(tmp_path: Path) -> None:
    transcriber = NemoSpeechTranscriber(
        NemoSpeechConfig(),
        library=FakeNemoSpeechLibrary(),
        device_detection=_cpu_device(),
    )
    assert transcriber.resolve_device() is Device.CPU


def test_resolve_device_auto_uses_vulkan_when_available(tmp_path: Path) -> None:
    transcriber = NemoSpeechTranscriber(
        NemoSpeechConfig(),
        library=FakeNemoSpeechLibrary(),
        device_detection=_vulkan_device(),
    )
    assert transcriber.resolve_device() is Device.VULKAN


def test_resolve_device_cpu_preference_wins_over_vulkan(tmp_path: Path) -> None:
    transcriber = NemoSpeechTranscriber(
        NemoSpeechConfig(device=NemoDevicePreference.CPU),
        library=FakeNemoSpeechLibrary(),
        device_detection=_vulkan_device(),
    )
    assert transcriber.resolve_device() is Device.CPU


def test_resolve_device_vulkan_preference_needs_a_vulkan_loader(
    tmp_path: Path,
) -> None:
    transcriber = NemoSpeechTranscriber(
        NemoSpeechConfig(device=NemoDevicePreference.VULKAN),
        library=FakeNemoSpeechLibrary(),
        device_detection=_cpu_device(),
    )
    with pytest.raises(NemoSpeechLoadError) as excinfo:
        transcriber.resolve_device()
    assert "no Vulkan loader" in str(excinfo.value)


def test_load_records_the_chosen_device_and_requests_the_gpu(tmp_path: Path) -> None:
    fake = FakeNemoSpeechLibrary()
    transcriber = NemoSpeechTranscriber(
        NemoSpeechConfig(),
        model_path_override=_write_model(tmp_path),
        library=fake,
        device_detection=_vulkan_device(),
    )
    transcriber.load()
    assert transcriber.device is Device.VULKAN
    assert fake.created[0][1] is True  # use_gpu was requested


def test_close_clears_the_recorded_device(tmp_path: Path) -> None:
    transcriber, _ = _loaded(tmp_path)
    assert transcriber.device is Device.CPU
    transcriber.close()
    assert transcriber.device is None


# --- the real seam (skipped cleanly when the scratch tree is absent) -----------

_SCRATCH = Path.home() / ".cache" / "ges2t-discovery" / "nemotron-3-5-asr-streaming-measurement"
_REAL_ENGINE = _SCRATCH / "engine" / "nemo-speech-0.2.0-linux-x86_64-vulkan"
_REAL_LIBRARY = Path(
    os.environ.get(
        "GES2T_NEMO_LIBRARY",
        str(_REAL_ENGINE / "lib" / ENTRY_LIBRARY_NAME),
    )
)
_REAL_MODEL = Path(
    os.environ.get(
        "GES2T_NEMO_MODEL",
        str(_SCRATCH / "models" / "nemotron-3.5-asr-streaming-0.6b.q8_0.gguf"),
    )
)
_REAL_AUDIO = Path(
    os.environ.get(
        "GES2T_NEMO_AUDIO",
        str(
            Path.home()
            / ".cache"
            / "ges2t-discovery"
            / "whisper-cpp-provider"
            / "audio"
            / "clip5.wav"
        ),
    )
)

#: A word the real engine produces for ``clip5.wav`` (measured, CPU, the pinned
#: v0.2.0 engine + GGUF). This is the same reference clip and word whisper.cpp's
#: ``test_real_libwhisper_transcribes_cached_audio`` asserts, so the real-seam
#: tests mirror each other.
_REAL_SPEECH_WORD: str = "dinner"


def test_real_library_reports_its_version() -> None:
    if not _REAL_LIBRARY.is_file():
        pytest.skip(f"the nemo_speech library is absent at {_REAL_LIBRARY}")
    library = CtypesNemoSpeechLibrary(_REAL_LIBRARY)
    assert library.version() != "<unknown>"


def _read_wav_mono_16k(path: Path) -> AudioBuffer:
    """Read a 16 kHz mono 16-bit WAV into the interface's :class:`AudioBuffer`."""
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    assert (channels, width, rate) == (1, 2, SAMPLE_RATE), (
        f"{path}: the real-seam test needs 16 kHz mono 16-bit, got "
        f"{rate} Hz, {channels} ch, {width * 8}-bit"
    )
    raw = array("h")
    raw.frombytes(frames)
    return AudioBuffer(samples=[value / 32768.0 for value in raw], sample_rate=rate)


@pytest.mark.skipif(
    os.environ.get("GES2T_NEMO_REAL_SEAM") != "1",
    reason="the full real decode loads ~1 GB of weights; set GES2T_NEMO_REAL_SEAM=1",
)
def test_real_library_recognizes_cached_audio() -> None:
    missing = [
        str(path)
        for path in (_REAL_LIBRARY, _REAL_MODEL, _REAL_AUDIO)
        if not path.is_file()
    ]
    if missing:
        pytest.skip(
            "the nemo_speech real-seam artifacts are absent: " + ", ".join(missing)
        )

    transcriber = NemoSpeechTranscriber(
        NemoSpeechConfig(device=NemoDevicePreference.CPU),
        model_path_override=_REAL_MODEL,
        library_path_override=_REAL_LIBRARY,
        device_detection=_cpu_device(),
    )
    transcriber.load()
    assert transcriber.device is Device.CPU

    result = transcriber.transcribe(_read_wav_mono_16k(_REAL_AUDIO))

    # A *speech* clip (the whisper.cpp reference clip), so a real decode produces
    # words: audio that never reached the decoder would yield "". This is the
    # real-seam half of review finding F1 (ADR 0014).
    text = result.text.lower()
    assert text.strip(), "the real library produced no text on real speech"
    assert _REAL_SPEECH_WORD in text, (
        f"expected the reference word {_REAL_SPEECH_WORD!r}; got {result.text!r}"
    )
    assert result.ttft_ms is None and result.decode_tps is None
    transcriber.close()


@pytest.mark.skipif(
    os.environ.get("GES2T_NEMO_REAL_SEAM") != "1",
    reason="the real create initialises the compute backend; set GES2T_NEMO_REAL_SEAM=1",
)
def test_real_library_rejects_a_missing_model() -> None:
    if not _REAL_LIBRARY.is_file():
        pytest.skip(f"the nemo_speech library is absent at {_REAL_LIBRARY}")
    library = CtypesNemoSpeechLibrary(_REAL_LIBRARY)
    missing_model = Path("/nonexistent/model.gguf")
    with pytest.raises(NemoSpeechLoadError) as excinfo:
        library.create_recognizer(missing_model, use_gpu=False)
    message = str(excinfo.value)
    assert "nemo_speech_asr_create failed" in message
    # The library's own last_error is surfaced, not a generic message: read it
    # back from the same library and require it to appear in the exception. This
    # fails if {self.last_error()} is dropped from the create failure message
    # (review finding F2), without hardcoding the library's wording.
    surfaced = library.last_error()
    assert surfaced, "the failed create recorded no last_error to check against"
    assert surfaced in message, (
        f"the library's own last_error {surfaced!r} was not surfaced in {message!r}"
    )


def test_status_ok_constant_matches_the_header() -> None:
    assert STATUS_OK == 0
    assert GPU_CPU == -1
    assert GPU_DEVICE_INDEX == 0
