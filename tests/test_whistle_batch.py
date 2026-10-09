"""Tests for the Whistle batch adapter, :mod:`ges2t.transcribers.whistle`.

These tests need **no engine, no model, and no network**. ``needle`` is a lazy
module global (``whistle._needle``) that each test replaces with
:class:`FakeNeedleModule`; the fixture also poisons ``sys.modules['needle']`` so
that a stray real import would fail rather than quietly succeeding on a machine
where the package happens to be installed. The fake records the exact bytes it
was handed, which is how the raw-float32 conversion path is asserted (ADR 0007).

Covered here: the ``Transcriber`` Protocol, the float32 conversion, the 16 kHz
and 30 s validation, the before-``load()`` error, verbatim result pass-through,
the managed-path assertions, ``ensure_model`` delegation, and the eager
before-``load()`` failure of ``stream()`` (now implemented by the streaming
adapter, ADR 0010, ADR 0011, ADR 0012, ADR 0017, ADR 0019).
"""

from __future__ import annotations

import json
import math
import os
import random
import subprocess
import sys
from array import array
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ges2t import model_store
from ges2t.interfaces import (
    AudioBuffer,
    ProgressCallback,
    Transcriber,
    TranscriptionResult,
)
from ges2t.transcribers import whistle
from ges2t.transcribers.whistle import (
    SAMPLE_RATE,
    WhistleInputError,
    WhistleLoadError,
    WhistleNotLoadedError,
    WhistleTranscriber,
)
from ges2t.transcribers.whistle_config import WhistleConfig

#: Modules importing the adapter must never pull in: the engine backend (under
#: either of its names) and the array library we deliberately avoid (ADR 0007,
#: ADR 0010).
BANNED_MODULES: tuple[str, ...] = ("cactus_needle", "needle", "gi", "numpy")

#: A module the adapter imports on purpose; its presence proves the sys.modules
#: snapshot in the subprocess test is real rather than empty.
CONTROL_MODULE: str = "array"


# --- test doubles -------------------------------------------------------------


class FakeWhistleEngine:
    """A stand-in for ``needle.Whistle`` that records the audio it is handed."""

    def __init__(self, weights: str) -> None:
        self.weights = weights
        self.calls: list[tuple[bytes, str | None]] = []
        self.result: dict[str, Any] = {
            "text": "hallo welt",
            "language": "de",
            "ttft_ms": 123.5,
            "decode_tps": 198.0,
        }

    def transcribe(
        self,
        audio: bytes,
        language: str | None = None,
        keywords: object = None,
        word_timestamps: bool = False,
    ) -> dict[str, Any]:
        self.calls.append((audio, language))
        return self.result


class FakeLibrary:
    """A stand-in for the ctypes ``CDLL`` handle (only ``_name`` is read)."""

    def __init__(self, name: str) -> None:
        self._name = name


class FakeNeedleModule:
    """A stand-in for the top-level ``needle`` package.

    ``engine_path`` lets a test claim a different shared library was loaded, and
    ``weights_override`` lets it claim a different weights file was opened, so
    both halves of the ADR 0007 load-time assertion are reachable.
    """

    def __init__(
        self,
        *,
        engine_path: str | None = None,
        weights_override: str | None = None,
        result: Mapping[str, Any] | None = None,
    ) -> None:
        self._engine_path = (
            engine_path if engine_path is not None else str(model_store.engine_path())
        )
        self._weights_override = weights_override
        self.result: dict[str, Any] = (
            dict(result)
            if result is not None
            else {
                "text": "hallo welt",
                "language": "de",
                "ttft_ms": 123.5,
                "decode_tps": 198.0,
            }
        )
        self.created: list[FakeWhistleEngine] = []
        self.agent = SimpleNamespace(whistle=SimpleNamespace(_lib=self._lib))

    def _lib(self) -> FakeLibrary:
        return FakeLibrary(self._engine_path)

    def Whistle(self, weights: str) -> FakeWhistleEngine:
        reported = (
            self._weights_override if self._weights_override is not None else weights
        )
        engine = FakeWhistleEngine(reported)
        engine.result = self.result
        self.created.append(engine)
        return engine


# --- fixtures -----------------------------------------------------------------


@pytest.fixture()
def fake_needle(monkeypatch: pytest.MonkeyPatch) -> FakeNeedleModule:
    """Replace the lazy engine import with a fake and block a real import."""
    fake = FakeNeedleModule()
    monkeypatch.setattr(whistle, "_needle", fake)
    # Any accidental ``import needle`` raises instead of loading the installed
    # package, so these tests prove the fake is the only engine in play.
    monkeypatch.setitem(sys.modules, "needle", None)
    return fake


@pytest.fixture()
def transcriber(fake_needle: FakeNeedleModule) -> WhistleTranscriber:
    """A default-config adapter whose engine import is the fake."""
    return WhistleTranscriber(WhistleConfig())


def _modules_present_after_importing_adapter(names: tuple[str, ...]) -> dict[str, bool]:
    """Import the adapter in a fresh interpreter and report ``sys.modules``.

    ``-I`` isolates the interpreter from user site-packages and ``PYTHON*``
    environment variables, so the answer reflects only what the import pulled in
    (mirroring the equivalent check in ``tests/test_interfaces.py``).
    """
    script = (
        "import json, sys\n"
        "import ges2t.transcribers.whistle\n"
        f"names = {json.dumps(list(names))}\n"
        "print(json.dumps({name: name in sys.modules for name in names}))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-c", script],
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    present: dict[str, bool] = json.loads(completed.stdout.strip().splitlines()[-1])
    return present


def _audio(seconds: float, value: float = 0.5) -> AudioBuffer:
    """An audible square wave, so the buffer passes the silence gate.

    Pass-through tests need the engine to actually be called; an all-zero (or
    too-short) buffer is now gated as silence before the engine sees it.
    """
    count = int(seconds * SAMPLE_RATE)
    return AudioBuffer(
        samples=[value if index % 2 == 0 else -value for index in range(count)],
        sample_rate=SAMPLE_RATE,
    )


def _tone(seconds: float, *, frequency: float = 1000.0, amplitude: float = 0.05) -> AudioBuffer:
    """A steady tone above the 150 Hz analysis high-pass (a voiced-vowel proxy)."""
    return AudioBuffer(
        samples=[
            amplitude * math.sin(2.0 * math.pi * frequency * index / SAMPLE_RATE)
            for index in range(int(seconds * SAMPLE_RATE))
        ],
        sample_rate=SAMPLE_RATE,
    )


def _silence_with_tone(
    seconds: float,
    start_ms: float,
    tone_ms: float,
    *,
    frequency: float = 1000.0,
    amplitude: float = 0.05,
) -> AudioBuffer:
    """A tone burst inside silence, so only the *run* length varies."""
    samples = [0.0] * int(seconds * SAMPLE_RATE)
    start = int(start_ms * SAMPLE_RATE / 1000)
    for index in range(int(tone_ms * SAMPLE_RATE / 1000)):
        if start + index >= len(samples):
            break
        samples[start + index] = amplitude * math.sin(
            2.0 * math.pi * frequency * index / SAMPLE_RATE
        )
    return AudioBuffer(samples=samples, sample_rate=SAMPLE_RATE)


def _silence_with_clicks(
    seconds: float, positions_ms: Sequence[float], amplitude: float, width_ms: float = 5.0
) -> AudioBuffer:
    """Decaying clicks in otherwise digital silence -- a hotkey toggle with no speech."""
    samples = [0.0] * int(seconds * SAMPLE_RATE)
    width = max(1, int(width_ms * SAMPLE_RATE / 1000))
    for at_ms in positions_ms:
        base = int(at_ms * SAMPLE_RATE / 1000)
        for k in range(width):
            if base + k >= len(samples):
                break
            samples[base + k] = amplitude * math.exp(-k / (width / 3.0)) * (
                1 if k % 2 == 0 else -1
            )
    return AudioBuffer(samples=samples, sample_rate=SAMPLE_RATE)


# --- interface conformance ----------------------------------------------------


def test_satisfies_the_transcriber_protocol() -> None:
    assert isinstance(WhistleTranscriber(WhistleConfig()), Transcriber)


def test_name_and_streaming_capability() -> None:
    adapter = WhistleTranscriber(WhistleConfig())

    assert adapter.name == "whistle"
    assert adapter.supports_streaming is True


def test_importing_the_adapter_does_not_pull_in_the_engine() -> None:
    present = _modules_present_after_importing_adapter(
        (*BANNED_MODULES, CONTROL_MODULE)
    )

    assert present[CONTROL_MODULE] is True, "the sys.modules snapshot was empty"
    for module in BANNED_MODULES:
        assert present[module] is False, f"{module} was imported by the adapter"


# --- the batch path -----------------------------------------------------------


def test_transcribe_before_load_raises_a_clear_error() -> None:
    adapter = WhistleTranscriber(WhistleConfig())

    with pytest.raises(WhistleNotLoadedError, match="load"):
        adapter.transcribe(AudioBuffer(samples=[0.0], sample_rate=SAMPLE_RATE))


def test_transcribe_hands_the_engine_raw_float32_bytes(
    transcriber: WhistleTranscriber, fake_needle: FakeNeedleModule
) -> None:
    transcriber.load()
    # Audible (>= 100 ms, above the RMS floor) so the gate forwards it; the
    # values stay distinctive so the float32 conversion is pinned exactly.
    samples = [0.0, 0.5, -0.25, 1.0, -1.0] * 400

    result = transcriber.transcribe(
        AudioBuffer(samples=samples, sample_rate=SAMPLE_RATE)
    )

    assert len(fake_needle.created[-1].calls) == 1
    audio, language = fake_needle.created[-1].calls[0]
    assert isinstance(audio, bytes)
    assert len(audio) == len(samples) * 4
    recovered = array("f")
    recovered.frombytes(audio)
    assert list(recovered) == pytest.approx(samples)
    # The empty config language is passed through verbatim; the engine treats it
    # as auto-detect (ADR 0009).
    assert language == ""
    assert result.text == "hallo welt"


def test_transcribe_passes_the_configured_language_through(
    fake_needle: FakeNeedleModule,
) -> None:
    adapter = WhistleTranscriber(WhistleConfig(language="de"))
    adapter.load()

    adapter.transcribe(_audio(0.2))

    assert fake_needle.created[-1].calls[0][1] == "de"


def test_rejects_a_buffer_that_is_not_16khz(
    transcriber: WhistleTranscriber, fake_needle: FakeNeedleModule
) -> None:
    transcriber.load()

    with pytest.raises(WhistleInputError, match="16000"):
        transcriber.transcribe(AudioBuffer(samples=[0.0], sample_rate=8000))

    assert fake_needle.created[-1].calls == []


def test_rejects_a_buffer_longer_than_30_seconds(
    transcriber: WhistleTranscriber, fake_needle: FakeNeedleModule
) -> None:
    transcriber.load()
    too_long = [0.0] * (SAMPLE_RATE * 30 + 1)

    with pytest.raises(WhistleInputError, match="30"):
        transcriber.transcribe(AudioBuffer(samples=too_long, sample_rate=SAMPLE_RATE))

    assert fake_needle.created[-1].calls == []


def test_accepts_a_buffer_of_exactly_30_seconds(
    transcriber: WhistleTranscriber, fake_needle: FakeNeedleModule
) -> None:
    transcriber.load()
    exactly_30 = [0.5, -0.5] * (SAMPLE_RATE * 15)

    result = transcriber.transcribe(
        AudioBuffer(samples=exactly_30, sample_rate=SAMPLE_RATE)
    )

    assert len(fake_needle.created[-1].calls) == 1
    assert result.text == "hallo welt"


def test_result_is_passed_through_unchanged(
    transcriber: WhistleTranscriber, fake_needle: FakeNeedleModule
) -> None:
    fake_needle.result = {
        "text": "  Hallo WELT ",
        "language": "de",
        "ttft_ms": 12.5,
        "decode_tps": 199.25,
    }
    transcriber.load()

    result = transcriber.transcribe(_audio(0.2))

    assert result == TranscriptionResult(
        text="  Hallo WELT ",
        language="de",
        ttft_ms=12.5,
        decode_tps=199.25,
    )


def test_an_empty_engine_text_is_returned_unchanged(
    transcriber: WhistleTranscriber, fake_needle: FakeNeedleModule
) -> None:
    # The gate now intercepts digital silence before the engine, so this pins
    # the other half: when the engine itself returns "", it crosses unchanged.
    fake_needle.result = {
        "text": "",
        "language": None,
        "ttft_ms": 1.0,
        "decode_tps": 0.0,
    }
    transcriber.load()

    result = transcriber.transcribe(_audio(0.2))

    assert result.text == ""
    assert result.language is None


def test_missing_timings_become_none(
    transcriber: WhistleTranscriber, fake_needle: FakeNeedleModule
) -> None:
    fake_needle.result = {"text": "x"}
    transcriber.load()

    result = transcriber.transcribe(_audio(0.2))

    assert result == TranscriptionResult(
        text="x", language=None, ttft_ms=None, decode_tps=None
    )


# --- the silence gate ---------------------------------------------------------


def test_silence_returns_empty_without_calling_the_engine(
    transcriber: WhistleTranscriber, fake_needle: FakeNeedleModule
) -> None:
    transcriber.load()

    result = transcriber.transcribe(
        AudioBuffer(samples=[0.0] * (SAMPLE_RATE * 2), sample_rate=SAMPLE_RATE)
    )

    assert result == TranscriptionResult(
        text="", language=None, ttft_ms=None, decode_tps=None
    )
    assert fake_needle.created[-1].calls == []


def test_low_level_noise_returns_empty_without_calling_the_engine(
    transcriber: WhistleTranscriber, fake_needle: FakeNeedleModule
) -> None:
    rng = random.Random(29)
    samples = [rng.uniform(-1e-4, 1e-4) for _ in range(SAMPLE_RATE * 2)]
    transcriber.load()

    result = transcriber.transcribe(
        AudioBuffer(samples=samples, sample_rate=SAMPLE_RATE)
    )

    assert result.text == ""
    assert fake_needle.created[-1].calls == []


def test_a_constant_dc_offset_is_treated_as_silence(
    transcriber: WhistleTranscriber, fake_needle: FakeNeedleModule
) -> None:
    # The engine hallucinates "Thank you." on a flat DC buffer at every level
    # tested, while raw RMS would call this buffer loud. Removing the mean is
    # what makes the decision correct.
    transcriber.load()

    result = transcriber.transcribe(
        AudioBuffer(samples=[0.05] * (SAMPLE_RATE * 2), sample_rate=SAMPLE_RATE)
    )

    assert result.text == ""
    assert fake_needle.created[-1].calls == []


def test_speech_at_the_measured_clip_level_still_transcribes(
    transcriber: WhistleTranscriber, fake_needle: FakeNeedleModule
) -> None:
    # The shipped clips measure 0.0536 (clip5) and 0.0562 (clip30) AC RMS; a
    # voiced 0.05 tone stands in for them, with no engine and no audio file.
    samples = [
        0.05 * math.sin(2.0 * math.pi * 200.0 * index / SAMPLE_RATE)
        for index in range(SAMPLE_RATE // 2)
    ]
    transcriber.load()

    result = transcriber.transcribe(
        AudioBuffer(samples=samples, sample_rate=SAMPLE_RATE)
    )

    assert len(fake_needle.created[-1].calls) == 1
    assert result.text == "hallo welt"


def test_a_buffer_just_above_the_frame_floor_is_not_dropped(
    transcriber: WhistleTranscriber, fake_needle: FakeNeedleModule
) -> None:
    # A 1 kHz tone is above the 150 Hz analysis high-pass, so its per-frame AC
    # RMS is amplitude/sqrt(2). A fixed 0.003 is 1.5x the committed
    # SPEECH_FRAME_RMS_THRESHOLD (0.002); using the constant here would make the
    # test follow any threshold change instead of pinning it.
    frame_rms = 0.003
    assert whistle.SPEECH_FRAME_RMS_THRESHOLD < frame_rms
    transcriber.load()

    transcriber.transcribe(
        _tone(0.2, amplitude=frame_rms * math.sqrt(2.0))
    )

    assert len(fake_needle.created[-1].calls) == 1


def test_a_99ms_full_scale_burst_is_dropped_by_the_duration_gate(
    transcriber: WhistleTranscriber, fake_needle: FakeNeedleModule
) -> None:
    # Full scale, so only the duration decides: the RMS gate would pass it.
    transcriber.load()

    result = transcriber.transcribe(_audio(0.099, value=1.0))

    assert result.text == ""
    assert fake_needle.created[-1].calls == []


def test_a_101ms_full_scale_burst_is_not_dropped(
    transcriber: WhistleTranscriber, fake_needle: FakeNeedleModule
) -> None:
    transcriber.load()

    transcriber.transcribe(_audio(0.101, value=1.0))

    assert len(fake_needle.created[-1].calls) == 1


def test_an_isolated_click_is_not_speech(
    transcriber: WhistleTranscriber, fake_needle: FakeNeedleModule
) -> None:
    # The user's failure mode: a hotkey click in otherwise silent audio. One
    # 20 ms frame is active at most, which is below MIN_SPEECH_FRAMES.
    transcriber.load()

    result = transcriber.transcribe(_silence_with_clicks(1.2, [10.0], 0.05))

    assert result.text == ""
    assert fake_needle.created[-1].calls == []


def test_clicks_at_both_ends_of_a_silent_toggle_are_not_speech(
    transcriber: WhistleTranscriber, fake_needle: FakeNeedleModule
) -> None:
    # The review's four realistic-toggle cases. Two isolated transients must not
    # add up to a run.
    transcriber.load()

    for amplitude in (0.05, 0.02):
        result = transcriber.transcribe(
            _silence_with_clicks(1.2, [0.0, 1180.0], amplitude)
        )
        assert result.text == "", f"amplitude {amplitude} was not gated"
    assert fake_needle.created[-1].calls == []


def test_clicks_never_form_a_run_even_at_high_amplitude(
    transcriber: WhistleTranscriber, fake_needle: FakeNeedleModule
) -> None:
    transcriber.load()

    transcriber.transcribe(_silence_with_clicks(1.2, [0.0, 1180.0], 0.4))

    assert fake_needle.created[-1].calls == []


def test_low_level_mains_hum_is_treated_as_silence(
    transcriber: WhistleTranscriber, fake_needle: FakeNeedleModule
) -> None:
    # Hum is AC, so mean removal alone does not touch it; the 150 Hz analysis
    # high-pass is what makes this measure as silence.
    samples = [
        0.005 * math.sin(2.0 * math.pi * 50.0 * index / SAMPLE_RATE)
        for index in range(2 * SAMPLE_RATE)
    ]
    transcriber.load()

    result = transcriber.transcribe(
        AudioBuffer(samples=samples, sample_rate=SAMPLE_RATE)
    )

    assert result.text == ""
    assert fake_needle.created[-1].calls == []


def test_a_sustained_tone_passes_the_sustained_gate(
    transcriber: WhistleTranscriber, fake_needle: FakeNeedleModule
) -> None:
    # A 150 ms voiced-vowel proxy: 7 active frames, well above the 3-frame floor.
    transcriber.load()

    transcriber.transcribe(_tone(0.15, frequency=180.0))

    assert len(fake_needle.created[-1].calls) == 1


def test_a_run_below_the_minimum_is_dropped_and_at_the_minimum_is_kept(
    transcriber: WhistleTranscriber, fake_needle: FakeNeedleModule
) -> None:
    # A 100 ms tone burst at the frame boundary is exactly 5 frames. A 40 ms
    # burst is 2 frames (below MIN_SPEECH_FRAMES), a 60 ms burst is 3 (at the
    # floor); both sit inside a longer buffer so only the run length varies.
    transcriber.load()

    transcriber.transcribe(_silence_with_tone(0.5, 100.0, 40.0))
    assert fake_needle.created[-1].calls == []

    transcriber.transcribe(_silence_with_tone(0.5, 100.0, 60.0))
    assert len(fake_needle.created[-1].calls) == 1


def test_a_five_frame_burst_is_kept(
    transcriber: WhistleTranscriber, fake_needle: FakeNeedleModule
) -> None:
    transcriber.load()

    transcriber.transcribe(_silence_with_tone(0.5, 100.0, 100.0))

    assert len(fake_needle.created[-1].calls) == 1


# --- loading ------------------------------------------------------------------


def test_load_is_idempotent(
    transcriber: WhistleTranscriber, fake_needle: FakeNeedleModule
) -> None:
    transcriber.load()
    first_load_seconds = transcriber.load_seconds
    transcriber.load()

    assert len(fake_needle.created) == 1
    assert transcriber.load_seconds == first_load_seconds
    assert first_load_seconds is not None


def test_load_uses_the_managed_weights_path(
    transcriber: WhistleTranscriber, fake_needle: FakeNeedleModule
) -> None:
    transcriber.load()

    assert fake_needle.created[-1].weights == str(model_store.model_path())


def test_load_uses_a_model_path_override(
    fake_needle: FakeNeedleModule,
) -> None:
    override = Path("/custom/whistle.cact")
    adapter = WhistleTranscriber(WhistleConfig(), model_path_override=override)

    adapter.load()

    assert fake_needle.created[-1].weights == str(override)


def test_load_rejects_unmanaged_weights(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeNeedleModule(weights_override="/tmp/unmanaged/whistle.cact")
    monkeypatch.setattr(whistle, "_needle", fake)
    adapter = WhistleTranscriber(WhistleConfig())

    with pytest.raises(WhistleLoadError, match="weights"):
        adapter.load()


def test_load_rejects_an_unmanaged_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeNeedleModule(engine_path="/tmp/unmanaged/libneedle.so")
    monkeypatch.setattr(whistle, "_needle", fake)
    adapter = WhistleTranscriber(WhistleConfig())

    with pytest.raises(WhistleLoadError, match="engine"):
        adapter.load()


def test_load_applies_the_managed_environment_overrides(
    monkeypatch: pytest.MonkeyPatch, transcriber: WhistleTranscriber
) -> None:
    for key in model_store.env_overrides():
        monkeypatch.delenv(key, raising=False)

    transcriber.load()

    for key, value in model_store.env_overrides().items():
        assert os.environ[key] == value


def test_close_returns_the_adapter_to_the_unloaded_state(
    transcriber: WhistleTranscriber, fake_needle: FakeNeedleModule
) -> None:
    transcriber.load()
    transcriber.close()

    with pytest.raises(WhistleNotLoadedError):
        transcriber.transcribe(AudioBuffer(samples=[0.0], sample_rate=SAMPLE_RATE))


def test_stream_before_load_fails_eagerly(transcriber: WhistleTranscriber) -> None:
    # The batch ticket's ``stream()`` was a NotImplementedError stub; the
    # streaming adapter ticket replaced the body (ADR 0011). What is still worth
    # guarding here is that the error is raised at call time, not on iteration:
    # a caller must not start capturing audio for a stream that cannot run.
    with pytest.raises(WhistleNotLoadedError):
        transcriber.stream([])


# --- acquisition --------------------------------------------------------------


def test_ensure_model_delegates_to_the_model_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ProgressCallback | None]] = []

    def fake_ensure_model(
        *, fetcher: object = None, progress: ProgressCallback | None = None
    ) -> Path:
        calls.append(("model", progress))
        return Path("/managed/whistle.cact")

    def fake_ensure_engine(
        *, fetcher: object = None, progress: ProgressCallback | None = None
    ) -> Path:
        calls.append(("engine", progress))
        return Path("/managed/libneedle.so")

    monkeypatch.setattr(model_store, "ensure_model", fake_ensure_model)
    monkeypatch.setattr(model_store, "ensure_engine", fake_ensure_engine)
    adapter = WhistleTranscriber(WhistleConfig())
    seen: list[float] = []
    callback: ProgressCallback = seen.append

    result = adapter.ensure_model(callback)

    assert result == Path("/managed/whistle.cact")
    assert [name for name, _ in calls] == ["model", "engine"]
    assert calls[0][1] is callback
    assert calls[1][1] is callback


def test_ensure_model_with_override_skips_the_weights_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_ensure_model(
        *, fetcher: object = None, progress: ProgressCallback | None = None
    ) -> Path:
        calls.append("model")
        return Path("/managed/whistle.cact")

    def fake_ensure_engine(
        *, fetcher: object = None, progress: ProgressCallback | None = None
    ) -> Path:
        calls.append("engine")
        return Path("/managed/libneedle.so")

    monkeypatch.setattr(model_store, "ensure_model", fake_ensure_model)
    monkeypatch.setattr(model_store, "ensure_engine", fake_ensure_engine)
    override = Path("/custom/whistle.cact")
    adapter = WhistleTranscriber(WhistleConfig(), model_path_override=override)

    result = adapter.ensure_model()

    assert result == override
    assert calls == ["engine"]


# --- conversion helper --------------------------------------------------------


def test_float32_bytes_matches_the_stdlib_array_encoding() -> None:
    samples: Sequence[float] = [0.0, 0.5, -0.25, 1.0, -1.0]

    assert whistle._float32_bytes(samples) == array("f", samples).tobytes()


def test_load_fails_closed_when_the_engine_path_is_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeNeedleModule()
    del fake.agent
    monkeypatch.setattr(whistle, "_needle", fake)
    adapter = WhistleTranscriber(WhistleConfig())

    with pytest.raises(WhistleLoadError, match="unknown"):
        adapter.load()
