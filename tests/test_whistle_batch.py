"""Tests for the Whistle batch adapter, :mod:`ges2t.transcribers.whistle`.

These tests need **no engine, no model, and no network**. ``needle`` is a lazy
module global (``whistle._needle``) that each test replaces with
:class:`FakeNeedleModule`; the fixture also poisons ``sys.modules['needle']`` so
that a stray real import would fail rather than quietly succeeding on a machine
where the package happens to be installed. The fake records the exact bytes it
was handed, which is how the raw-float32 conversion path is asserted (ADR 0007).

Covered here: the ``Transcriber`` Protocol, the float32 conversion, the 16 kHz
and 30 s validation, the before-``load()`` error, verbatim result pass-through,
the managed-path assertions, ``ensure_model`` delegation, and the deliberate
``stream()`` stub (ADR 0010, ADR 0011, ADR 0012, ADR 0017, ADR 0019).
"""

from __future__ import annotations

import json
import os
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
    samples = [0.0, 0.5, -0.25, 1.0, -1.0]

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

    adapter.transcribe(AudioBuffer(samples=[0.0], sample_rate=SAMPLE_RATE))

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
    exactly_30 = [0.0] * (SAMPLE_RATE * 30)

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

    result = transcriber.transcribe(AudioBuffer(samples=[0.0], sample_rate=SAMPLE_RATE))

    assert result == TranscriptionResult(
        text="  Hallo WELT ",
        language="de",
        ttft_ms=12.5,
        decode_tps=199.25,
    )


def test_silence_is_returned_as_an_empty_string(
    transcriber: WhistleTranscriber, fake_needle: FakeNeedleModule
) -> None:
    fake_needle.result = {
        "text": "",
        "language": None,
        "ttft_ms": 1.0,
        "decode_tps": 0.0,
    }
    transcriber.load()

    result = transcriber.transcribe(AudioBuffer(samples=[0.0], sample_rate=SAMPLE_RATE))

    assert result.text == ""
    assert result.language is None


def test_missing_timings_become_none(
    transcriber: WhistleTranscriber, fake_needle: FakeNeedleModule
) -> None:
    fake_needle.result = {"text": "x"}
    transcriber.load()

    result = transcriber.transcribe(AudioBuffer(samples=[0.0], sample_rate=SAMPLE_RATE))

    assert result == TranscriptionResult(
        text="x", language=None, ttft_ms=None, decode_tps=None
    )


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


def test_stream_is_a_not_implemented_stub(transcriber: WhistleTranscriber) -> None:
    with pytest.raises(NotImplementedError):
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
