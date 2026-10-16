"""Tests for the NeMo-Speech.cpp **append-only streaming** adapter (ticket 59, ADR 0022).

The adapter under test is :meth:`NemoSpeechTranscriber.stream`, which drives the
pure-streaming :func:`ges2t.streaming.drive_append_only` through
:class:`~ges2t.transcribers.nemo_speech._NemoSpeechDecoder`. Everything here runs
against :class:`tests.test_nemo_speech.FakeNemoSpeechLibrary` -- the same fake the
batch tests use, extended to model the streaming lifecycle (open one stream per
recording, script one interim per push, deliver the final after ``finish``, and
refuse a freed handle) rather than a simplified one.

Covered here:

* the **commit policy** (ADR 0022 §5): only the completed-word prefix is
  committed, the trailing partial token stays ``pending`` and is never injected;
* the committed sequence is **cumulative and append-only**;
* the **actual float32 payload** reaches the library (mirroring ticket 58's F1
  fix), not merely that a call happened;
* one stream is opened and closed per recording, and ``finish`` runs exactly once;
* the append-only and endpointing-off guards fail loudly;
* one opt-in real-seam test drives the actual ``libnemo_speech_asr_c.so`` from the
  research scratch tree (``GES2T_NEMO_REAL_SEAM=1``); it **skips cleanly** when
  the tree is absent -- never silently passing (ADR 0014).
"""

from __future__ import annotations

import os
import wave
from array import array
from collections.abc import Generator, Sequence
from pathlib import Path

import pytest

from ges2t.interfaces import AudioBuffer, Transcriber
from ges2t.nemo_engine import NemoDevice
from ges2t.transcribers.nemo_speech import (
    ENTRY_LIBRARY_NAME,
    SAMPLE_RATE,
    Device,
    NemoSpeechAppendOnlyError,
    NemoSpeechDecodeError,
    NemoSpeechInputError,
    NemoSpeechNotLoadedError,
    NemoSpeechTranscriber,
    _committed_prefix,
)
from ges2t.transcribers.nemo_speech_config import NemoDevicePreference, NemoSpeechConfig
from tests.test_nemo_speech import FakeNemoSpeechLibrary

# --- helpers ------------------------------------------------------------------


def _write_model(tmp_path: Path) -> Path:
    """Create a stand-in GGUF file (its bytes are never read by a fake library)."""
    model = tmp_path / "model.gguf"
    model.write_bytes(b"not a real gguf model")
    return model


def _cpu_device() -> NemoDevice:
    """A host device record with no Vulkan loader."""
    return NemoDevice(vulkan_library=None)


def _buffer(samples: Sequence[float], sample_rate: int = SAMPLE_RATE) -> AudioBuffer:
    return AudioBuffer(samples=list(samples), sample_rate=sample_rate)


def _seconds(count: float, value: float = 0.1) -> AudioBuffer:
    """``count`` seconds of a small non-silent buffer at 16 kHz."""
    return _buffer([value] * round(count * SAMPLE_RATE))


def _loaded(
    tmp_path: Path,
    *,
    interims: Sequence[str] = (),
    final_text: str = "",
    language: str | None = None,
    mid_stream_final_at: int | None = None,
    suppress_final: bool = False,
    chunk_seconds: float = 1.0,
) -> tuple[NemoSpeechTranscriber, FakeNemoSpeechLibrary]:
    """Load a transcriber over a stand-in model and a scripted streaming fake."""
    fake = FakeNemoSpeechLibrary(
        interims=interims,
        final_text=final_text,
        stream_language=language,
        mid_stream_final_at=mid_stream_final_at,
        suppress_final=suppress_final,
    )
    transcriber = NemoSpeechTranscriber(
        NemoSpeechConfig(chunk_seconds=chunk_seconds),
        model_path_override=_write_model(tmp_path),
        library=fake,
        device_detection=_cpu_device(),
    )
    transcriber.load()
    return transcriber, fake


# --- the commit policy (ADR 0022 §5) ------------------------------------------


def test_the_trailing_partial_word_is_held_pending_and_never_committed(
    tmp_path: Path,
) -> None:
    transcriber, _ = _loaded(
        tmp_path,
        interims=("And so my fel", "And so my fellow", "And so my fellow Ame"),
        final_text="And so my fellow Americans",
    )

    segments = list(transcriber.stream([_seconds(3.0)]))

    partials = [segment for segment in segments if not segment.is_final]
    # The first interim commits the completed prefix "And so my"; "fel" is the
    # partial token and stays pending -- it is never in the committed text.
    assert partials[0].live.committed == "And so my"
    assert partials[0].live.pending == " fel"
    assert "fel" not in partials[0].live.committed


def test_committed_advances_only_when_a_later_word_confirms_the_held_one(
    tmp_path: Path,
) -> None:
    transcriber, _ = _loaded(
        tmp_path,
        interims=("And so my fel", "And so my fellow", "And so my fellow Ame"),
        final_text="And so my fellow Americans",
    )

    segments = list(transcriber.stream([_seconds(3.0)]))
    partials = [segment for segment in segments if not segment.is_final]

    assert [segment.live.committed for segment in partials] == [
        "And so my",
        "And so my",
        "And so my fellow",
    ]
    assert [segment.live.pending for segment in partials] == [" fel", " fellow", " Ame"]


def test_committed_is_cumulative_and_append_only(tmp_path: Path) -> None:
    transcriber, _ = _loaded(
        tmp_path,
        interims=("a", "a b", "a b c", "a b c d"),
        final_text="a b c d e",
    )

    segments = list(transcriber.stream([_seconds(4.0)]))

    previous = ""
    for segment in segments:
        assert segment.live.committed.startswith(previous)
        previous = segment.live.committed
    assert segments[-1].live.committed == "a b c d e"


def test_pending_is_never_in_the_final_transcript(tmp_path: Path) -> None:
    transcriber, _ = _loaded(
        tmp_path,
        interims=("And so my fel", "And so my fellow Ame"),
        final_text="And so my fellow Americans",
    )

    segments = list(transcriber.stream([_seconds(2.0)]))
    final = segments[-1]

    assert final.is_final is True
    assert final.live.pending == ""
    assert final.live.committed == final.text == "And so my fellow Americans"


def test_exactly_one_final_segment(tmp_path: Path) -> None:
    transcriber, _ = _loaded(
        tmp_path,
        interims=("a", "a b", "a b c"),
        final_text="a b c d",
    )

    segments = list(transcriber.stream([_seconds(3.0)]))

    assert [segment.is_final for segment in segments] == [False, False, False, True]


def test_a_single_word_interim_commits_nothing(tmp_path: Path) -> None:
    transcriber, _ = _loaded(tmp_path, interims=("And",), final_text="And more")

    segments = list(transcriber.stream([_seconds(1.0)]))

    partial = segments[0]
    assert partial.live.committed == ""
    assert partial.live.pending == "And"


# --- the string word boundary (no word timestamps on interims) ---------------


def test_committed_prefix_is_the_last_completed_word() -> None:
    assert _committed_prefix("And so my fel") == "And so my"
    assert _committed_prefix("And so my fellow") == "And so my"
    assert _committed_prefix("And so my fellow Ame") == "And so my fellow"


def test_committed_prefix_of_a_lone_partial_is_empty() -> None:
    assert _committed_prefix("And") == ""
    assert _committed_prefix("") == ""


def test_a_single_word_interim_pins_the_string_not_a_timestamp(tmp_path: Path) -> None:
    # The final carries word times; the interims do not (report §10.3). The rule
    # works from the string alone, so a fake that reports no times still yields
    # the right boundary -- this test would fail if the rule reached for a time.
    transcriber, _ = _loaded(tmp_path, interims=("one two thr",), final_text="one two three")
    segments = list(transcriber.stream([_seconds(1.0)]))
    assert segments[0].live.committed == "one two"


# --- the real seam: the audio payload reaches the library ---------------------


def test_the_actual_audio_payload_reaches_the_library(tmp_path: Path) -> None:
    transcriber, fake = _loaded(tmp_path, interims=("hi",), final_text="hi there")
    samples = [0.0, 0.25, -0.5, 1.0, -1.0, 0.125] * 500  # 0.1875 s -> one group

    list(transcriber.stream([_buffer(samples)]))

    assert len(fake.pushed) == 1
    _, forwarded, rate = fake.pushed[0]
    assert forwarded == tuple(samples)
    assert len(forwarded) == len(samples)
    assert rate == SAMPLE_RATE


def test_the_configured_chunk_seconds_drive_the_push_granularity(tmp_path: Path) -> None:
    transcriber, fake = _loaded(
        tmp_path, interims=("a", "a b"), final_text="a b c", chunk_seconds=0.5
    )

    list(transcriber.stream([_seconds(1.0)]))

    assert len(fake.pushed) == 2
    assert [len(payload) for _, payload, _ in fake.pushed] == [
        round(0.5 * SAMPLE_RATE)
    ] * 2


# --- the stream lifecycle -----------------------------------------------------


def test_one_stream_is_opened_and_closed_per_recording(tmp_path: Path) -> None:
    transcriber, fake = _loaded(tmp_path, interims=("a",), final_text="a b")

    list(transcriber.stream([_seconds(2.0)]))

    assert len(fake.streams_opened) == 1
    assert fake.streams_closed == fake.streams_opened
    assert len(fake.streams_finished) == 1


def test_the_stream_is_closed_when_the_generator_is_abandoned(tmp_path: Path) -> None:
    transcriber, fake = _loaded(tmp_path, interims=("a", "a b"), final_text="a b c")

    iterator = transcriber.stream([_seconds(1.0)])
    assert isinstance(iterator, Generator)  # the concrete stream() is a generator
    next(iterator)  # open the stream and push the first group
    iterator.close()  # cancel: abandon before finish

    assert len(fake.streams_opened) == 1
    assert fake.streams_closed == fake.streams_opened
    assert fake.streams_finished == []


def test_stream_before_load_fails_eagerly(tmp_path: Path) -> None:
    transcriber = NemoSpeechTranscriber(
        NemoSpeechConfig(),
        model_path_override=_write_model(tmp_path),
        library=FakeNemoSpeechLibrary(),
    )

    with pytest.raises(NemoSpeechNotLoadedError):
        transcriber.stream([_seconds(1.0)])


def test_stream_rejects_a_non_16khz_buffer(tmp_path: Path) -> None:
    transcriber, _ = _loaded(tmp_path, final_text="x")

    with pytest.raises(NemoSpeechInputError, match="16000"):
        list(transcriber.stream([_buffer([0.0] * 100, sample_rate=48000)]))


def test_supports_streaming_is_true_and_it_is_a_transcriber(tmp_path: Path) -> None:
    transcriber, _ = _loaded(tmp_path)

    assert isinstance(transcriber, Transcriber)
    assert transcriber.supports_streaming is True


# --- the guards fail loudly ---------------------------------------------------


def test_the_decoder_raises_when_an_interim_revises_earlier_text(tmp_path: Path) -> None:
    # Interims are append-only (ticket 55); a revision would make the string
    # prefix delta wrong, so the decoder stops instead of silently dropping it.
    transcriber, _ = _loaded(
        tmp_path, interims=("a b c", "a x c"), final_text="a x c"
    )

    with pytest.raises(NemoSpeechAppendOnlyError):
        list(transcriber.stream([_seconds(2.0)]))


def test_a_mid_stream_final_is_rejected(tmp_path: Path) -> None:
    # Endpointing is off (report §5.4); an is_final mid-stream means it was
    # switched on, and v1 has no reopen rule, so the decoder fails loudly.
    transcriber, _ = _loaded(
        tmp_path,
        interims=("a", "a b"),
        final_text="a b c",
        mid_stream_final_at=0,
    )

    with pytest.raises(NemoSpeechDecodeError, match="is_final"):
        list(transcriber.stream([_seconds(2.0)]))


def test_a_finish_without_an_end_of_stream_final_is_rejected(tmp_path: Path) -> None:
    # The ABI guarantees the final after finish; a stream that ends without one is
    # a broken contract, so the decoder fails loudly instead of promoting the last
    # interim (which would inject an unconfirmed partial).
    transcriber, _ = _loaded(
        tmp_path, interims=("a",), final_text="a b", suppress_final=True
    )

    with pytest.raises(NemoSpeechDecodeError, match="end-of-stream final"):
        list(transcriber.stream([_seconds(1.0)]))


# --- the real seam (skipped cleanly when the scratch tree is absent) -----------

_SCRATCH = (
    Path.home() / ".cache" / "ges2t-discovery" / "nemotron-3-5-asr-streaming-measurement"
)
_REAL_ENGINE = _SCRATCH / "engine" / "nemo-speech-0.2.0-linux-x86_64-vulkan"
_REAL_LIBRARY = Path(
    os.environ.get("GES2T_NEMO_LIBRARY", str(_REAL_ENGINE / "lib" / ENTRY_LIBRARY_NAME))
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
#: v0.2.0 engine + GGUF); the same clip ticket 58's batch real-seam test uses.
_REAL_SPEECH_WORD: str = "dinner"


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
    reason="the full real stream loads ~1 GB of weights; set GES2T_NEMO_REAL_SEAM=1",
)
def test_real_streaming_engine_appends_without_a_window() -> None:
    missing = [
        str(path) for path in (_REAL_LIBRARY, _REAL_MODEL, _REAL_AUDIO) if not path.is_file()
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
    try:
        segments = list(transcriber.stream([_read_wav_mono_16k(_REAL_AUDIO)]))
        final = segments[-1]
        assert final.is_final is True
        assert final.live.pending == ""
        text = final.text.lower()
        assert text.strip(), "the real engine produced no text on real speech"
        assert _REAL_SPEECH_WORD in text, (
            f"expected the reference word {_REAL_SPEECH_WORD!r}; got {final.text!r}"
        )
        # The committed sequence is append-only and reaches the final transcript.
        # This is where the `enable_automatic_punctuation` request option is
        # pinned: with it off the engine lowercases/strips the final while the
        # interims stay capitalised, so the final does not extend them and this
        # loop fails (measured; report §4.1's harness sets the flag).
        previous = ""
        for segment in segments:
            assert segment.live.committed.startswith(previous)
            previous = segment.live.committed
        assert previous == final.text
        # At least one interim held a trailing partial word pending (the whole
        # point of the commit rule) -- otherwise this engine would not need it.
        assert any(segment.live.pending for segment in segments)
    finally:
        transcriber.close()
