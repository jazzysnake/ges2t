"""Tests for the in-process whisper.cpp transcriber (ticket 43, ADR 0021).

Two levels of test, deliberately:

* The **Protocol contract** is driven by :class:`FakeWhisperLibrary`, a fake that
  models the real library lifecycle (load one context, decode once, free) rather
  than a simplified one. It records the :class:`WhisperDecodeOptions` and the
  loaded model path, which is where the pinned ``-nt``/greedy decode and the
  override seam are observable without a native library.
* **One real-seam test** drives the actual ``libwhisper.so`` from the research
  scratch cache through :class:`CtypesWhisperLibrary`. It **skips cleanly** when
  the library, model or audio is absent -- never silently passes -- because the
  installed engine directory does not exist on a machine without ``cmake`` (ADR
  0014). Point it elsewhere with ``GES2T_WHISPER_LIBRARY``, ``GES2T_WHISPER_MODEL``
  and ``GES2T_WHISPER_AUDIO``.
"""

from __future__ import annotations

import os
import wave
from array import array
from collections.abc import Sequence
from pathlib import Path

import pytest

from ges2t.config import Config, TranscriberConfig
from ges2t.interfaces import AudioBuffer, Transcriber
from ges2t.pipeline import PipelineError
from tests.fakes import FakeAudioSource, FakeInjector
from ges2t.transcribers.whisper_cpp import (
    BEAM_SIZE,
    BEST_OF,
    NO_TIMESTAMPS,
    SAMPLE_RATE,
    CtypesWhisperLibrary,
    Device,
    SamplingStrategy,
    WhisperCppInputError,
    WhisperCppLoadError,
    WhisperCppNotLoadedError,
    WhisperCppStreamingUnsupportedError,
    WhisperCppTranscriber,
    WhisperDecodeOptions,
    WhisperFullParams,
    WhisperTranscript,
    apply_decode_options,
    resolve_threads,
)
from ges2t.transcribers.whisper_cpp_config import (
    GpuPreference,
    WhisperCppConfig,
)
from ges2t.whisper_engine import (
    EngineBuild,
    EngineLibraries,
    VulkanStatus,
    WHISPER_CPP_REVISION,
    WHISPER_CPP_TAG,
    WHISPER_CPP_VERSION,
)
from ges2t.whisper_engine import _write_manifest

# --- the fake library ---------------------------------------------------------


class FakeWhisperLibrary:
    """A :class:`~ges2t.transcribers.whisper_cpp.WhisperLibrary` that records calls.

    It mirrors the real lifecycle: :meth:`load_context` returns a fresh handle
    and records the model path and ``use_gpu``; :meth:`decode` records the handle,
    the samples and the :class:`WhisperDecodeOptions` so the pinned decode is
    assertable; :meth:`free_context` records the handle. ``raise_on_load`` and
    ``raise_on_decode`` inject failures without a native library.
    """

    def __init__(
        self,
        *,
        transcript: WhisperTranscript | None = None,
        raise_on_load: Exception | None = None,
        raise_on_decode: Exception | None = None,
    ) -> None:
        self.transcript = (
            transcript
            if transcript is not None
            else WhisperTranscript(text="hello world", language="en")
        )
        self.raise_on_load = raise_on_load
        self.raise_on_decode = raise_on_decode
        self.loaded: list[tuple[Path, bool]] = []
        self.freed: list[int] = []
        self.decoded: list[tuple[int, tuple[float, ...], WhisperDecodeOptions]] = []
        self._next_handle: int = 1

    def version(self) -> str:
        return "1.9.5-fake"

    def load_context(self, model_path: Path, *, use_gpu: bool) -> int:
        if self.raise_on_load is not None:
            raise self.raise_on_load
        handle = self._next_handle
        self._next_handle += 1
        self.loaded.append((Path(model_path), use_gpu))
        return handle

    def free_context(self, handle: int) -> None:
        self.freed.append(handle)

    def decode(
        self,
        handle: int,
        samples: Sequence[float],
        options: WhisperDecodeOptions,
    ) -> WhisperTranscript:
        if self.raise_on_decode is not None:
            raise self.raise_on_decode
        self.decoded.append((handle, tuple(samples), options))
        return self.transcript


# --- helpers ------------------------------------------------------------------


def _write_model(tmp_path: Path, name: str = "ggml-tiny-q8_0.bin") -> Path:
    """Create a stand-in model file (its bytes are never read by a fake library)."""
    model = tmp_path / name
    model.write_bytes(b"not a real ggml model")
    return model


def _write_engine(directory: Path, variant: str = "cpu") -> None:
    """Write a real ``engine.json`` manifest plus dummy libraries (via the engine module).

    The manifest is written by :func:`ges2t.whisper_engine._write_manifest`, the
    same writer the builder uses, so the adapter's ``verify_build`` reads exactly
    what a real install would carry.
    """
    from ges2t.whisper_engine import BuildVariant

    parsed_variant = BuildVariant(variant)
    directory.mkdir(parents=True, exist_ok=True)
    lib = directory / "libwhisper.so"
    lib.write_bytes(b"\x7fELF dummy")
    ggml = directory / "libggml.so"
    ggml.write_bytes(b"\x7fELF dummy")
    build = EngineBuild(
        version=WHISPER_CPP_VERSION,
        tag=WHISPER_CPP_TAG,
        revision=WHISPER_CPP_REVISION,
        variant=parsed_variant,
        directory=directory,
        libraries=EngineLibraries(libwhisper=lib, ggml=(ggml,)),
        vulkan=VulkanStatus(
            runtime=None, headers=None, spirv_headers=None, shader_compiler=None
        ),
        built_at="2026-10-10T00:00:00+00:00",
    )
    _write_manifest(build)


def _buffer(samples: Sequence[float], sample_rate: int = SAMPLE_RATE) -> AudioBuffer:
    """Build an :class:`AudioBuffer` with the given rate."""
    return AudioBuffer(samples=list(samples), sample_rate=sample_rate)


def _loaded(
    tmp_path: Path,
    *,
    config: WhisperCppConfig | None = None,
    library: FakeWhisperLibrary | None = None,
) -> tuple[WhisperCppTranscriber, FakeWhisperLibrary]:
    """Construct and load a transcriber over a stand-in model and a fake library."""
    fake = library if library is not None else FakeWhisperLibrary()
    transcriber = WhisperCppTranscriber(
        config if config is not None else WhisperCppConfig(),
        model_path_override=_write_model(tmp_path),
        library=fake,
    )
    transcriber.load()
    return transcriber, fake


# --- Protocol contract --------------------------------------------------------


def test_transcriber_satisfies_the_protocol_and_declares_batch_only() -> None:
    transcriber = WhisperCppTranscriber(WhisperCppConfig(), library=FakeWhisperLibrary())
    assert isinstance(transcriber, Transcriber)
    assert transcriber.name == "whisper_cpp"
    assert transcriber.supports_streaming is False


# --- pinned decode ------------------------------------------------------------


def test_apply_decode_options_pins_no_timestamps_and_greedy() -> None:
    params = WhisperFullParams()
    options = WhisperDecodeOptions(
        language="en",
        threads=7,
        no_timestamps=NO_TIMESTAMPS,
        strategy=SamplingStrategy.GREEDY,
        best_of=BEST_OF,
        beam_size=BEAM_SIZE,
    )
    apply_decode_options(params, options)
    assert params.n_threads == 7
    assert params.no_timestamps is True
    assert params.translate is False
    assert params.print_progress is False
    assert params.print_realtime is False
    assert params.print_timestamps is False
    assert params.greedy.best_of == 1
    assert params.beam_search.beam_size == 1


def test_the_pin_constants_are_the_measured_configuration() -> None:
    assert NO_TIMESTAMPS is True
    assert SamplingStrategy.GREEDY == 0
    assert BEST_OF == 1
    assert BEAM_SIZE == 1


def test_transcribe_passes_the_pinned_decode_to_the_library(tmp_path: Path) -> None:
    transcriber, fake = _loaded(tmp_path)
    transcriber.transcribe(_buffer([0.0] * 1600))

    assert len(fake.decoded) == 1
    _, _, options = fake.decoded[0]
    assert options.no_timestamps is True
    assert options.strategy is SamplingStrategy.GREEDY
    assert options.best_of == 1
    assert options.beam_size == 1


def test_transcribe_forwards_language_and_threads(tmp_path: Path) -> None:
    config = WhisperCppConfig(language="de", threads=3)
    transcriber, fake = _loaded(tmp_path, config=config)
    transcriber.transcribe(_buffer([0.0] * 1600))

    options = fake.decoded[0][2]
    assert options.language == "de"
    assert options.threads == 3


def test_empty_language_is_auto_detect(tmp_path: Path) -> None:
    transcriber, fake = _loaded(tmp_path, config=WhisperCppConfig(language=""))
    transcriber.transcribe(_buffer([0.0] * 1600))
    assert fake.decoded[0][2].language is None


# --- model path / acquisition -------------------------------------------------


def test_load_uses_the_model_override(tmp_path: Path) -> None:
    model = _write_model(tmp_path)
    fake = FakeWhisperLibrary()
    transcriber = WhisperCppTranscriber(
        WhisperCppConfig(), model_path_override=model, library=fake
    )
    transcriber.load()
    assert fake.loaded == [(model, False)]


def test_ensure_model_returns_the_override_without_touching_the_store(
    tmp_path: Path,
) -> None:
    model = _write_model(tmp_path)
    transcriber = WhisperCppTranscriber(
        WhisperCppConfig(), model_path_override=model, library=FakeWhisperLibrary()
    )
    assert transcriber.ensure_model() == model


def test_ensure_model_delegates_to_the_model_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[tuple[str, object]] = []

    def fake_ensure(
        name: str, *, fetcher: object = None, progress: object = None
    ) -> Path:
        seen.append((name, progress))
        return tmp_path / f"ggml-{name}.bin"

    monkeypatch.setattr(
        "ges2t.transcribers.whisper_cpp.model_store.ensure_whisper_model",
        fake_ensure,
    )
    transcriber = WhisperCppTranscriber(
        WhisperCppConfig(model="medium-q5_0"), library=FakeWhisperLibrary()
    )

    def progress(fraction: float) -> None:
        pass

    result = transcriber.ensure_model(progress)
    assert result == tmp_path / "ggml-medium-q5_0.bin"
    assert seen == [("medium-q5_0", progress)]


# --- load / close idempotency and failures ------------------------------------


def test_load_is_idempotent_and_the_context_stays_resident(tmp_path: Path) -> None:
    transcriber, fake = _loaded(tmp_path)
    transcriber.load()
    transcriber.load()
    transcriber.transcribe(_buffer([0.0] * 1600))
    transcriber.transcribe(_buffer([0.0] * 1600))

    assert len(fake.loaded) == 1  # loaded once
    assert [handle for handle, _, _ in fake.decoded] == [1, 1]  # same resident context


def test_load_records_the_load_time_and_threads(tmp_path: Path) -> None:
    transcriber, _ = _loaded(tmp_path)
    assert transcriber.load_seconds is not None
    assert transcriber.load_seconds >= 0.0
    assert transcriber.threads >= 1


def test_load_fails_when_the_model_is_missing(tmp_path: Path) -> None:
    transcriber = WhisperCppTranscriber(
        WhisperCppConfig(),
        model_path_override=tmp_path / "missing.bin",
        library=FakeWhisperLibrary(),
    )
    with pytest.raises(WhisperCppLoadError) as excinfo:
        transcriber.load()
    message = str(excinfo.value)
    assert "missing.bin" in message
    assert "ges2t setup whisper.cpp" in message


def test_load_fails_when_the_library_is_missing(tmp_path: Path) -> None:
    model = _write_model(tmp_path)
    library = tmp_path / "libwhisper.so"
    transcriber = WhisperCppTranscriber(
        WhisperCppConfig(),
        model_path_override=model,
        library_path_override=library,
        engine_directory=tmp_path,
    )
    with pytest.raises(WhisperCppLoadError) as excinfo:
        transcriber.load()
    assert "libwhisper.so" in str(excinfo.value)


def test_a_library_load_failure_propagates_and_leaves_the_adapter_unloaded(
    tmp_path: Path,
) -> None:
    fake = FakeWhisperLibrary(
        raise_on_load=WhisperCppLoadError("corrupt model file")
    )
    transcriber = WhisperCppTranscriber(
        WhisperCppConfig(),
        model_path_override=_write_model(tmp_path),
        library=fake,
    )
    with pytest.raises(WhisperCppLoadError):
        transcriber.load()
    assert transcriber.device is None
    assert transcriber._handle is None  # noqa: SLF001 - asserting the internal state


def test_close_releases_the_context_and_is_idempotent(tmp_path: Path) -> None:
    transcriber, fake = _loaded(tmp_path)
    transcriber.close()
    transcriber.close()
    assert fake.freed == [1]


def test_close_then_load_reloads(tmp_path: Path) -> None:
    transcriber, fake = _loaded(tmp_path)
    transcriber.close()
    transcriber.load()
    assert len(fake.loaded) == 2


# --- transcribe contract ------------------------------------------------------


def test_transcribe_returns_text_and_omits_engine_timings(tmp_path: Path) -> None:
    fake = FakeWhisperLibrary(
        transcript=WhisperTranscript(text="the text", language="en")
    )
    transcriber, _ = _loaded(tmp_path, library=fake)
    result = transcriber.transcribe(_buffer([0.0] * 1600))
    assert result.text == "the text"
    assert result.language == "en"
    assert result.ttft_ms is None
    assert result.decode_tps is None
    assert transcriber.last_transcribe_seconds is not None


def test_transcribe_before_load_is_a_typed_error(tmp_path: Path) -> None:
    transcriber = WhisperCppTranscriber(
        WhisperCppConfig(),
        model_path_override=_write_model(tmp_path),
        library=FakeWhisperLibrary(),
    )
    with pytest.raises(WhisperCppNotLoadedError):
        transcriber.transcribe(_buffer([0.0] * 1600))


def test_transcribe_rejects_a_non_16khz_buffer(tmp_path: Path) -> None:
    transcriber, _ = _loaded(tmp_path)
    with pytest.raises(WhisperCppInputError) as excinfo:
        transcriber.transcribe(_buffer([0.0] * 1600, sample_rate=48000))
    assert "48000" in str(excinfo.value)


def test_transcribe_of_empty_audio_does_not_decode(tmp_path: Path) -> None:
    transcriber, fake = _loaded(tmp_path)
    result = transcriber.transcribe(_buffer([]))
    assert result.text == ""
    assert fake.decoded == []


def test_stream_refuses_loudly(tmp_path: Path) -> None:
    transcriber, _ = _loaded(tmp_path)
    with pytest.raises(WhisperCppStreamingUnsupportedError):
        transcriber.stream([_buffer([0.0] * 160)])


# --- device rule --------------------------------------------------------------


def test_resolve_device_auto_uses_cpu_without_an_engine(tmp_path: Path) -> None:
    transcriber = WhisperCppTranscriber(
        WhisperCppConfig(),
        model_path_override=_write_model(tmp_path),
        library=FakeWhisperLibrary(),
        engine_directory=tmp_path / "no-engine",
    )
    assert transcriber.resolve_device() is Device.CPU


def test_resolve_device_auto_uses_vulkan_for_a_vulkan_engine(tmp_path: Path) -> None:
    engine = tmp_path / "engine"
    _write_engine(engine, "vulkan")
    transcriber = WhisperCppTranscriber(
        WhisperCppConfig(),
        library=FakeWhisperLibrary(),
        engine_directory=engine,
    )
    assert transcriber.resolve_device() is Device.VULKAN


def test_resolve_device_auto_uses_cpu_for_a_cpu_engine(tmp_path: Path) -> None:
    engine = tmp_path / "engine"
    _write_engine(engine, "cpu")
    transcriber = WhisperCppTranscriber(
        WhisperCppConfig(),
        library=FakeWhisperLibrary(),
        engine_directory=engine,
    )
    assert transcriber.resolve_device() is Device.CPU


def test_resolve_device_cpu_preference_wins_over_a_vulkan_engine(
    tmp_path: Path,
) -> None:
    engine = tmp_path / "engine"
    _write_engine(engine, "vulkan")
    transcriber = WhisperCppTranscriber(
        WhisperCppConfig(gpu=GpuPreference.CPU),
        library=FakeWhisperLibrary(),
        engine_directory=engine,
    )
    assert transcriber.resolve_device() is Device.CPU


def test_resolve_device_vulkan_preference_needs_a_vulkan_engine(
    tmp_path: Path,
) -> None:
    engine = tmp_path / "engine"
    _write_engine(engine, "cpu")
    transcriber = WhisperCppTranscriber(
        WhisperCppConfig(gpu=GpuPreference.VULKAN),
        library=FakeWhisperLibrary(),
        engine_directory=engine,
    )
    with pytest.raises(WhisperCppLoadError) as excinfo:
        transcriber.resolve_device()
    assert "CPU-only" in str(excinfo.value)


def test_load_records_the_chosen_device(tmp_path: Path) -> None:
    engine = tmp_path / "engine"
    _write_engine(engine, "vulkan")
    fake = FakeWhisperLibrary()
    transcriber = WhisperCppTranscriber(
        WhisperCppConfig(),
        model_path_override=_write_model(tmp_path),
        library=fake,
        engine_directory=engine,
    )
    transcriber.load()
    assert transcriber.device is Device.VULKAN
    assert fake.loaded[0][1] is True  # use_gpu was requested


def test_resolve_threads_defaults_to_the_machine() -> None:
    assert resolve_threads(None) >= 1
    assert resolve_threads(5) == 5


# --- pipeline wiring (composition root) ---------------------------------------


def test_build_transcriber_returns_whisper_cpp_for_its_engine() -> None:
    from ges2t import pipeline

    config = Config(transcriber=TranscriberConfig(engine="whisper_cpp"))
    transcriber = pipeline.build_transcriber(config)
    assert isinstance(transcriber, WhisperCppTranscriber)
    assert transcriber.name == "whisper_cpp"
    assert transcriber.supports_streaming is False


def test_build_transcriber_reads_the_whisper_block() -> None:
    from ges2t import pipeline

    config = Config(
        transcriber=TranscriberConfig(engine="whisper_cpp"),
        engine_blocks={"whisper_cpp": {"model": "medium-q5_0", "threads": 3}},
    )
    transcriber = pipeline.build_transcriber(config)
    assert isinstance(transcriber, WhisperCppTranscriber)
    assert transcriber.config.model == "medium-q5_0"
    assert transcriber.threads == 3


def test_build_transcriber_rejects_an_invalid_whisper_block() -> None:
    from ges2t import pipeline
    from ges2t.transcribers.whisper_cpp_config import WhisperCppConfigError

    config = Config(
        transcriber=TranscriberConfig(engine="whisper_cpp"),
        engine_blocks={"whisper_cpp": {"model": "small-q5_0"}},
    )
    with pytest.raises(WhisperCppConfigError):
        pipeline.build_transcriber(config)


def test_missing_artifacts_defers_a_whisper_engine_to_the_adapter() -> None:
    from ges2t import pipeline

    config = Config(transcriber=TranscriberConfig(engine="whisper_cpp"))
    assert pipeline._missing_artifacts(config) == []  # noqa: SLF001


def test_startup_builds_and_loads_the_whisper_transcriber_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from ges2t import pipeline
    from ges2t.ui.status import NullStatusUI

    fake = FakeWhisperLibrary()
    transcriber = WhisperCppTranscriber(
        WhisperCppConfig(model="tiny-q8_0"),
        model_path_override=_write_model(tmp_path),
        library=fake,
    )
    monkeypatch.setattr(pipeline, "build_transcriber", lambda config: transcriber)
    monkeypatch.setattr(pipeline, "build_audio_source", lambda config: FakeAudioSource())
    monkeypatch.setattr(pipeline, "build_injector", lambda config: FakeInjector())

    config = Config(transcriber=TranscriberConfig(engine="whisper_cpp"))
    service = pipeline._startup(config, NullStatusUI())  # noqa: SLF001

    assert service is not None
    assert len(fake.loaded) == 1  # loaded exactly once, at startup


def test_a_whisper_load_failure_is_fatal_with_a_clear_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from ges2t import pipeline
    from ges2t.ui.status import NullStatusUI

    class _FailingWhisper(WhisperCppTranscriber):
        def load(self) -> None:
            raise WhisperCppLoadError("the whisper.cpp engine library is missing at /nope/libwhisper.so")

    failing = _FailingWhisper(WhisperCppConfig())
    monkeypatch.setattr(pipeline, "build_transcriber", lambda config: failing)
    monkeypatch.setattr(pipeline, "build_audio_source", lambda config: FakeAudioSource())
    monkeypatch.setattr(pipeline, "build_injector", lambda config: FakeInjector())

    config = Config(transcriber=TranscriberConfig(engine="whisper_cpp"))
    result = pipeline.run_service(config, status_ui=NullStatusUI())

    assert result != 0
    stderr = capsys.readouterr().err
    assert "could not start the service" in stderr
    assert "libwhisper.so" in stderr


def test_build_transcriber_still_rejects_an_unknown_engine() -> None:
    from ges2t import pipeline

    config = Config(transcriber=TranscriberConfig(engine="nope"))
    with pytest.raises(PipelineError):
        pipeline.build_transcriber(config)


# --- the real seam (skipped cleanly when the scratch cache is absent) ---------

_SCRATCH = Path.home() / ".cache" / "ges2t-discovery" / "whisper-cpp-provider"
_REAL_LIBRARY = Path(
    os.environ.get(
        "GES2T_WHISPER_LIBRARY",
        str(_SCRATCH / "whisper.cpp" / "build" / "bin" / "libwhisper.so"),
    )
)
_REAL_MODEL = Path(
    os.environ.get(
        "GES2T_WHISPER_MODEL",
        str(_SCRATCH / "models" / "ggml-tiny-q5_0.bin"),
    )
)
_REAL_AUDIO = Path(
    os.environ.get(
        "GES2T_WHISPER_AUDIO",
        str(_SCRATCH / "audio" / "clip5.wav"),
    )
)


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


def test_real_libwhisper_transcribes_cached_audio() -> None:
    missing = [
        str(path)
        for path in (_REAL_LIBRARY, _REAL_MODEL, _REAL_AUDIO)
        if not path.is_file()
    ]
    if missing:
        pytest.skip(
            "the whisper.cpp real-seam artifacts are absent (the installed engine "
            "is user-owned and needs cmake): " + ", ".join(missing)
        )

    transcriber = WhisperCppTranscriber(
        WhisperCppConfig(model="tiny-q5_0", language="en"),
        model_path_override=_REAL_MODEL,
        library_path_override=_REAL_LIBRARY,
        engine_directory=_REAL_LIBRARY.parent.parent,  # no engine.json -> CPU
    )
    transcriber.load()
    assert transcriber.device is Device.CPU

    result = transcriber.transcribe(_read_wav_mono_16k(_REAL_AUDIO))

    text = result.text.lower()
    assert text.strip(), "the real library produced no text on real speech"
    assert "dinner" in text, f"expected the reference word; got {result.text!r}"
    assert result.ttft_ms is None and result.decode_tps is None
    assert result.language == "en"

    transcriber.close()


def test_real_libwhisper_reports_its_version() -> None:
    if not _REAL_LIBRARY.is_file():
        pytest.skip(f"the whisper.cpp library is absent at {_REAL_LIBRARY}")
    library = CtypesWhisperLibrary(_REAL_LIBRARY)
    assert library.version() != "<unknown>"
