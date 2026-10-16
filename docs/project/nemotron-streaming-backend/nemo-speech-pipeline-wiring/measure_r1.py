#!/usr/bin/env python3
"""R1 divergence harness (ticket 60; a measurement probe, not shipped code).

Compares, on **the same audio and the same normalisation**, three decodes of the
pinned NeMo-Speech.cpp engine:

* ``offline``   -- ``nemo_speech_asr_recognize_f32`` with the library default
  options (``enable_automatic_punctuation=False``) -- exactly what
  :meth:`NemoSpeechTranscriber.transcribe` runs today;
* ``offline+punc`` -- the same offline call with
  ``enable_automatic_punctuation=True`` -- the "align the two paths" option;
* ``stream``    -- the ``stream()`` path (``enable_automatic_punctuation=True``)
  drained to its end-of-stream final.

WER is ticket 32's ``word_diff`` (reused by path), i.e. lowercase + punctuation
stripped, so it is blind to the punctuation/casing difference and measures only
whether the two paths heard different words.

No download, no network, no ``sudo``, no microphone: it drives the pinned scratch
engine + GGUF already on this machine, over the reference clips.

Usage::

    uv run python docs/project/nemotron-streaming-backend/nemo-speech-pipeline-wiring/measure_r1.py \
        --device auto
"""

from __future__ import annotations

import argparse
import ctypes
import importlib.util
import json
import sys
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ges2t.interfaces import AudioBuffer
from ges2t.transcribers.nemo_speech import (
    STATUS_OK,
    CtypesNemoSpeechLibrary,
    NemoSpeechTranscriber,
    _NemoRecognitionOptions,
)
from ges2t.transcribers.nemo_speech_config import (
    NemoDevicePreference,
    NemoSpeechConfig,
)

#: The scratch engine + GGUF ticket 55 downloaded and verified (never re-fetched).
SCRATCH = Path.home() / ".cache" / "ges2t-discovery" / "nemotron-3-5-asr-streaming-measurement"
REAL_ENGINE = SCRATCH / "engine" / "nemo-speech-0.2.0-linux-x86_64-vulkan"
REAL_LIBRARY = REAL_ENGINE / "lib" / "libnemo_speech_asr_c.so"
REAL_MODEL = SCRATCH / "models" / "nemotron-3.5-asr-streaming-0.6b.q8_0.gguf"

#: The reference clips ticket 55 scored, with the ground-truth passages from
#: ``docs/project/research/whisper-cpp-provider/measure_whisper_cpp.py`` (REFERENCES).
AUDIO_DIR = Path.home() / ".cache" / "ges2t-discovery" / "whisper-cpp-provider" / "audio"
JFK_REFERENCE = (
    "And so my fellow Americans, ask not what your country can do for you, "
    "ask what you can do for your country."
)
QUIET_SHORT_REFERENCE = (
    "Hi Sam, the tiles arrive Tuesday. The invoice came to four hundred and twenty euros, "
    "so check whether their quote included delivery. I'll send the measurements tonight."
)
QUIET_LONG_REFERENCE = (
    "Hi Sam, quick update on the kitchen order. The tiles arrive Tuesday morning, and the "
    "invoice came to four hundred and twenty euros, a bit more than we budgeted, so check "
    "whether their quote included delivery. The plumber can only start on the fourteenth, "
    "which means the painting may slip by a week. I'll confirm the exact measurements for "
    "the north wall tonight. Let me know if that still works for you."
)
COMMIT_REFERENCE = (
    "Good morning. Last night's storm knocked out power to roughly four thousand homes "
    "across the northern valley, and crews expect to restore most of them by Friday "
    "afternoon. Officials say the river crested at 3.2 metres just after midnight, well "
    "below the flood barrier. Is that reading correct? Two low-lying roads remain closed "
    "tonight, and the ferry service resumes at half past six."
)

#: ``(label, clip, reference)`` for every clip ticket 55 had a reference for.
CLIPS: tuple[tuple[str, Path, str], ...] = (
    ("jfk", AUDIO_DIR / "jfk.wav", JFK_REFERENCE),
    ("quiet-short", AUDIO_DIR / "quiet-short.wav", QUIET_SHORT_REFERENCE),
    ("quiet-long", AUDIO_DIR / "quiet-long.wav", QUIET_LONG_REFERENCE),
    (
        "commit",
        AUDIO_DIR / "ges2t-commit-cadence-capture-20261009-195914.wav",
        COMMIT_REFERENCE,
    ),
)

#: Ticket 32's harness, reused by path for ``word_diff`` (same normalisation).
TICKET32_HARNESS = (
    Path(__file__).resolve().parents[2]
    / "live-dictation-ux"
    / "commit-cadence-diagnostic"
    / "measure_commit_cadence.py"
)


class WordDiff(Protocol):
    """The shape of ticket 32's ``word_diff`` result this harness reads."""

    @property
    def wer(self) -> float: ...


class Ticket32(Protocol):
    """The part of ticket 32's harness this harness reuses."""

    def word_diff(self, reference: str, hypothesis: str) -> WordDiff: ...


def load_ticket32() -> Ticket32:
    """Import ticket 32's harness by path and return its typed surface."""
    spec = importlib.util.spec_from_file_location("ges2t_r1_ticket32", TICKET32_HARNESS)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {TICKET32_HARNESS}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module  # type: ignore[no-any-return]


def read_wav_mono_16k(path: Path) -> AudioBuffer:
    """Read a 16-bit PCM mono WAV at 16 kHz into an :class:`AudioBuffer`."""
    with wave.open(str(path), "rb") as handle:
        if handle.getnchannels() != 1 or handle.getframerate() != 16000:
            raise ValueError(f"{path} is not mono 16 kHz")
        frames = handle.readframes(handle.getnframes())
    samples = array("h")
    samples.frombytes(frames)
    return AudioBuffer(samples=[s / 32768.0 for s in samples], sample_rate=16000)


def offline_with_punctuation(library: CtypesNemoSpeechLibrary, handle: int, buffer: AudioBuffer) -> str:
    """Run ``recognize_f32`` with ``enable_automatic_punctuation=True`` (the align option)."""
    options = library._lib.nemo_speech_asr_recognition_options_default()  # noqa: SLF001
    options.size = ctypes.sizeof(_NemoRecognitionOptions)
    options.interim_results = False
    options.enable_automatic_punctuation = True
    options.language_code = None
    raw = array("f", buffer.samples)
    c_buffer = (ctypes.c_float * len(raw)).from_buffer(raw)
    result = ctypes.c_void_p()
    status = library._lib.nemo_speech_asr_recognize_f32(  # noqa: SLF001
        ctypes.c_void_p(handle),
        ctypes.byref(options),
        c_buffer,
        len(raw),
        buffer.sample_rate,
        ctypes.byref(result),
    )
    if status != STATUS_OK:
        raise RuntimeError(f"recognize_f32 (punc on) failed with status {status}")
    try:
        return library._read_result(result).text  # noqa: SLF001
    finally:
        if result.value:
            library._lib.nemo_speech_asr_result_destroy(result)  # noqa: SLF001


@dataclass(frozen=True)
class ClipResult:
    """One clip's three decodes and their WERs against the ground truth."""

    label: str
    duration_s: float
    offline: str
    offline_punc: str
    stream_final: str
    stream_last_committed: str
    stream_segments: int
    offline_wer: float
    offline_punc_wer: float
    stream_wer: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "duration_s": round(self.duration_s, 3),
            "offline": self.offline,
            "offline_punc": self.offline_punc,
            "stream_final": self.stream_final,
            "stream_last_committed": self.stream_last_committed,
            "stream_segments": self.stream_segments,
            "offline_wer": round(self.offline_wer, 4),
            "offline_punc_wer": round(self.offline_punc_wer, 4),
            "stream_wer": round(self.stream_wer, 4),
        }


def measure_clip(
    transcriber: NemoSpeechTranscriber,
    library: CtypesNemoSpeechLibrary,
    handle: int,
    ticket32: Ticket32,
    label: str,
    path: Path,
    reference: str,
) -> ClipResult:
    """Run the three decodes for one clip and score each against the reference."""
    buffer = read_wav_mono_16k(path)
    offline = transcriber.transcribe(buffer).text
    offline_punc = offline_with_punctuation(library, handle, buffer)
    segments = list(transcriber.stream([buffer]))
    final = segments[-1]
    if not final.is_final:
        raise RuntimeError(f"{label}: the streaming path yielded no final segment")
    stream_final = final.live.committed
    last_committed = segments[-2].live.committed if len(segments) >= 2 else ""
    duration_s = len(buffer.samples) / buffer.sample_rate
    return ClipResult(
        label=label,
        duration_s=duration_s,
        offline=offline,
        offline_punc=offline_punc,
        stream_final=stream_final,
        stream_last_committed=last_committed,
        stream_segments=len(segments),
        offline_wer=ticket32.word_diff(reference, offline).wer,
        offline_punc_wer=ticket32.word_diff(reference, offline_punc).wer,
        stream_wer=ticket32.word_diff(reference, stream_final).wer,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--device", choices=["auto", "cpu", "vulkan"], default="auto"
    )
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    for path in (REAL_LIBRARY, REAL_MODEL):
        if not path.is_file():
            print(f"MISSING {path}", file=sys.stderr)
            return 2

    config = NemoSpeechConfig(device=NemoDevicePreference(args.device))
    transcriber = NemoSpeechTranscriber(
        config,
        model_path_override=REAL_MODEL,
        library_path_override=REAL_LIBRARY,
    )
    transcriber.load()
    assert transcriber.device is not None
    library = transcriber._library  # noqa: SLF001
    handle = transcriber._handle  # noqa: SLF001
    assert isinstance(library, CtypesNemoSpeechLibrary)
    assert handle is not None
    print(f"device={transcriber.device.value} load_s={transcriber.load_seconds:.2f}")

    ticket32 = load_ticket32()
    results: list[ClipResult] = []
    for label, path, reference in CLIPS:
        if not path.is_file():
            print(f"SKIP {label}: {path} absent", file=sys.stderr)
            continue
        result = measure_clip(
            transcriber, library, handle, ticket32, label, path, reference
        )
        results.append(result)
        print(
            f"{label:12s} wers off={result.offline_wer:.3f} "
            f"off+punc={result.offline_punc_wer:.3f} stream={result.stream_wer:.3f} "
            f"segments={result.stream_segments}"
        )

    transcriber.close()

    payload = [r.as_dict() for r in results]
    if args.out is not None:
        args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        print(f"wrote {args.out}")
    else:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
