"""Tests for the shared interfaces and the shared test doubles.

Covers ADR 0010 (the Protocol vocabulary and the "importing it pulls in no
backend" rule), ADR 0014 (fakes are first-class artifacts), and ADR 0019 (frozen
data classes instead of tuples).
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import FrozenInstanceError

import pytest

from ges2t.interfaces import (
    AudioBuffer,
    AudioSource,
    Injector,
    LiveText,
    StatusUI,
    Transcriber,
    TranscriptionResult,
    TranscriptionSegment,
)
from tests.fakes import FakeAudioSource, FakeInjector, FakeStatusUI, FakeTranscriber

#: Modules that ``ges2t.interfaces`` must never cause a caller to import: the
#: engine backend, the GUI toolkit, and the audio array library (ADR 0010).
#:
#: NOTE: the ``cactus-needle`` distribution (version 3.1.3) installs its
#: top-level package as ``needle``, not ``cactus_needle`` -- confirmed by
#: ``cactus_needle-3.1.3.dist-info/top_level.txt`` and by ADR 0007's own
#: references to ``needle.Needle``/``needle/_worker.py``. Both names are listed
#: so the assertion keeps its meaning whichever name a later ticket imports.
BANNED_MODULES = ("cactus_needle", "needle", "gi", "numpy")

#: A module ``interfaces`` imports on purpose; its presence proves the
#: ``sys.modules`` snapshot is real rather than empty.
CONTROL_MODULE = "dataclasses"


def _modules_present_after_importing_interfaces(
    names: tuple[str, ...],
) -> dict[str, bool]:
    """Import ``ges2t.interfaces`` in a fresh interpreter and report sys.modules.

    ``-I`` isolates the interpreter from user site-packages and ``PYTHON*``
    environment variables, so the answer reflects only what the import itself
    pulled in.
    """
    script = (
        "import json, sys\n"
        "import ges2t.interfaces\n"
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


def test_importing_interfaces_does_not_pull_in_banned_modules() -> None:
    present = _modules_present_after_importing_interfaces(
        (*BANNED_MODULES, CONTROL_MODULE)
    )

    assert present[CONTROL_MODULE] is True, "the sys.modules snapshot was empty"
    for module in BANNED_MODULES:
        assert present[module] is False, f"{module} was imported by ges2t.interfaces"


def test_fake_audio_source_satisfies_protocol() -> None:
    assert isinstance(FakeAudioSource(), AudioSource)


def test_fake_transcriber_satisfies_protocol() -> None:
    assert isinstance(FakeTranscriber(), Transcriber)


def test_fake_injector_satisfies_protocol() -> None:
    assert isinstance(FakeInjector(), Injector)


def test_fake_status_ui_satisfies_protocol() -> None:
    assert isinstance(FakeStatusUI(), StatusUI)


def test_transcriber_streaming_capability_is_visible() -> None:
    assert FakeTranscriber(supports_streaming=False).supports_streaming is False
    assert FakeTranscriber(supports_streaming=True).supports_streaming is True
    assert FakeTranscriber().supports_streaming is True


def test_audio_buffer_carries_any_sample_rate_without_validating() -> None:
    buffer = AudioBuffer(samples=[0.0, 1.0, -1.0], sample_rate=8000)

    assert list(buffer.samples) == [0.0, 1.0, -1.0]
    assert buffer.sample_rate == 8000


def test_audio_buffer_is_frozen() -> None:
    buffer = AudioBuffer(samples=[0.0], sample_rate=16000)

    with pytest.raises(FrozenInstanceError):
        buffer.sample_rate = 48000  # type: ignore[misc]


def test_transcription_classes_use_named_fields() -> None:
    result = TranscriptionResult(
        text="hello", language="en", ttft_ms=12.5, decode_tps=199.0
    )
    segment = TranscriptionSegment(
        live=LiveText(committed="hel", pending=""),
        language="en",
        is_final=False,
        pass_ms=39.0,
    )

    assert result.text == "hello"
    assert result.ttft_ms == 12.5
    assert segment.live.committed == "hel"
    assert segment.text == "hel"
    assert segment.is_final is False
    assert segment.pass_ms == 39.0


def test_live_text_keeps_committed_and_pending_apart() -> None:
    live = LiveText(committed="hello ", pending="world")

    assert live.committed == "hello "
    assert live.pending == "world"
    assert live.display == "hello world"


def test_live_text_is_frozen() -> None:
    live = LiveText(committed="a", pending="b")

    with pytest.raises(FrozenInstanceError):
        live.committed = "c"  # type: ignore[misc]


def test_fake_audio_source_stop_returns_everything_captured() -> None:
    first = AudioBuffer(samples=[0.0, 0.5], sample_rate=16000)
    second = AudioBuffer(samples=[-0.5], sample_rate=16000)
    source = FakeAudioSource([first, second])

    source.start()
    yielded = list(source.chunks(1.0))
    stopped = source.stop()

    assert yielded == [first, second]
    assert list(stopped.samples) == [0.0, 0.5, -0.5]
    assert stopped.sample_rate == 16000
    assert source.calls == ["start", "chunks", "stop"]
    assert source.chunk_seconds_seen == [1.0]
    assert (source.started, source.stopped) == (True, True)


def test_fake_audio_source_raises_on_demand() -> None:
    source = FakeAudioSource(raise_on_start=RuntimeError("no device"))

    with pytest.raises(RuntimeError, match="no device"):
        source.start()
    assert source.calls == ["start"]
    assert source.started is False


def test_fake_transcriber_records_calls_and_can_raise() -> None:
    buffer = AudioBuffer(samples=[0.1], sample_rate=16000)
    transcriber = FakeTranscriber(supports_streaming=False)

    assert transcriber.transcribe(buffer) == transcriber.result
    assert transcriber.transcribed == [buffer]
    assert transcriber.calls == ["transcribe"]

    transcriber.raise_on_transcribe = RuntimeError("engine exploded")
    with pytest.raises(RuntimeError, match="engine exploded"):
        transcriber.transcribe(buffer)
    assert transcriber.transcribed == [buffer]


def test_fake_transcriber_ensure_model_reports_progress_and_records_it() -> None:
    seen: list[float] = []
    transcriber = FakeTranscriber()

    path = transcriber.ensure_model(seen.append)

    assert path == transcriber.model_path
    assert seen == [1.0]
    assert transcriber.calls == ["ensure_model"]


def test_fake_transcriber_stream_yields_configured_segments() -> None:
    segments = [
        TranscriptionSegment(
            live=LiveText(committed="hel", pending=""),
            language="en",
            is_final=False,
            pass_ms=12.0,
        ),
        TranscriptionSegment(
            live=LiveText(committed="hello", pending=""),
            language="en",
            is_final=True,
            pass_ms=13.0,
        ),
    ]
    transcriber = FakeTranscriber(segments=segments)
    chunks = [AudioBuffer(samples=[0.0], sample_rate=16000)]

    assert list(transcriber.stream(chunks)) == segments
    assert transcriber.streamed == [chunks]
    assert transcriber.calls == ["stream"]


def test_fake_transcriber_stream_defaults_to_one_final_segment() -> None:
    transcriber = FakeTranscriber(
        result=TranscriptionResult(
            text="hello", language="en", ttft_ms=1.0, decode_tps=2.0
        )
    )

    segments = list(transcriber.stream([]))

    assert len(segments) == 1
    assert segments[0] == TranscriptionSegment(
        live=LiveText(committed="hello", pending=""),
        language="en",
        is_final=True,
        pass_ms=None,
    )


def test_fake_injector_records_and_can_raise() -> None:
    injector = FakeInjector()

    injector.inject("hello ")

    assert injector.injected == ["hello "]
    assert injector.calls == ["inject"]

    injector.error = RuntimeError("ydotoold not running")
    with pytest.raises(RuntimeError, match="ydotoold"):
        injector.inject("again")
    assert injector.injected == ["hello "]


def test_fake_status_ui_records_transitions_and_payloads() -> None:
    ui = FakeStatusUI()

    ui.recording()
    ui.partial(LiveText(committed="hello", pending=""))
    ui.partial(LiveText(committed="hello", pending=" world"))
    ui.transcribing()
    ui.error("engine failed")
    ui.idle()

    assert ui.calls == [
        "recording",
        "partial",
        "partial",
        "transcribing",
        "error",
        "idle",
    ]
    assert [live.display for live in ui.partials] == ["hello", "hello world"]
    assert ui.errors == ["engine failed"]
    assert ui.idle_calls == 1
    assert ui.recording_calls == 1
    assert ui.transcribing_calls == 1
