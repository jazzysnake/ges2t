#!/usr/bin/env python3
"""NeMo-Speech.cpp C-ABI streaming harness (discovery; not shipped code).

Drives ``libnemo_speech_asr_c`` directly with ``ctypes`` over the ASR streaming
API (``nemo_speech_asr_streaming_recognize`` / ``stream_push_f32`` /
``stream_next`` / ``stream_finish``) so that **every interim result** the engine
emits is recorded, with its ``audio_processed`` watermark and ``is_final`` flag.
The file CLI (``nemo-speech transcribe --stream``) prints only the final
transcript, so the C ABI is the only way to see the partial stream that ges2t's
committed-delta contract depends on.

No numpy: the WAV is read with stdlib ``wave``/``array`` and converted to
float32 in a loop. No network, no microphone.

Usage
-----
    python3 measure_nemo_streaming.py \
        --lib <dir>/lib/libnemo_speech_asr_c.so.1 \
        --model <path>.q8_0.gguf --audio clip.wav \
        --device vulkan --chunk-ms 160 --interim \
        --partials-out data/partials/L.jsonl --summary-out data/stream_summary.jsonl \
        --label L --timeout 300
"""

from __future__ import annotations

import argparse
import ctypes
import json
import sys
import time
import wave
from array import array
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

# --------------------------------------------------------------------------------------
# ctypes ABI mirror of include/nemo_speech/asr.h
# --------------------------------------------------------------------------------------

c_size_t = ctypes.c_size_t
c_int32 = ctypes.c_int32
c_float = ctypes.c_float
c_double = ctypes.c_double
c_bool = ctypes.c_bool
c_char_p = ctypes.c_char_p


class BackendConfig(ctypes.Structure):
    _fields_ = [("size", c_size_t), ("gpu", c_int32)]


class ModelConfig(ctypes.Structure):
    _fields_ = [("size", c_size_t), ("path", c_char_p), ("name", c_char_p)]


class StreamingConfig(ctypes.Structure):
    _fields_ = [
        ("size", c_size_t),
        ("chunk_size", c_float),
        ("ctc_left_padding", c_float),
        ("ctc_right_padding", c_float),
        ("rnnt_right_context", c_int32),
    ]


class DecoderConfig(ctypes.Structure):
    _fields_ = [
        ("size", c_size_t),
        ("kind", ctypes.c_int),
        ("flashlight_lm", c_char_p),
        ("flashlight_lexicon", c_char_p),
        ("flashlight_tokenizer", c_char_p),
        ("beam_size", c_int32),
        ("beam_size_token", c_int32),
        ("beam_threshold", c_double),
        ("lm_weight", c_double),
        ("word_insertion_score", c_double),
        ("max_boost", c_double),
    ]


class VadConfig(ctypes.Structure):
    _fields_ = [
        ("size", c_size_t),
        ("model_path", c_char_p),
        ("enable_masking", c_bool),
        ("onset", c_float),
        ("offset", c_float),
    ]


class EndpointingConfig(ctypes.Structure):
    _fields_ = [
        ("size", c_size_t),
        ("enable", c_bool),
        ("vad_based", c_bool),
        ("stop_history_eou_ms", c_int32),
    ]


class PostprocConfig(ctypes.Structure):
    _fields_ = [
        ("size", c_size_t),
        ("profanity_list_path", c_char_p),
        ("itn_model_dir", c_char_p),
        ("pnc_model_path", c_char_p),
    ]


class DiarConfig(ctypes.Structure):
    _fields_ = [
        ("size", c_size_t),
        ("model_path", c_char_p),
        ("chunk_frames", c_int32),
        ("right_context_frames", c_int32),
        ("left_context_frames", c_int32),
        ("fifo_frames", c_int32),
        ("spkcache_frames", c_int32),
        ("update_period_frames", c_int32),
    ]


class BatchingConfig(ctypes.Structure):
    _fields_ = [
        ("size", c_size_t),
        ("enable", c_bool),
        ("max_batch_size", c_int32),
        ("max_queue_delay_us", c_int32),
        ("max_queue_depth", c_int32),
        ("ingress_cohort_delay_us", c_int32),
        ("state_arena_slots", c_int32),
    ]


class RecognizerConfig(ctypes.Structure):
    _fields_ = [
        ("size", c_size_t),
        ("backend", ctypes.POINTER(BackendConfig)),
        ("model", ctypes.POINTER(ModelConfig)),
        ("streaming", ctypes.POINTER(StreamingConfig)),
        ("decoder", ctypes.POINTER(DecoderConfig)),
        ("vad", ctypes.POINTER(VadConfig)),
        ("endpointing", ctypes.POINTER(EndpointingConfig)),
        ("postproc", ctypes.POINTER(PostprocConfig)),
        ("diar", ctypes.POINTER(DiarConfig)),
        ("batching", ctypes.POINTER(BatchingConfig)),
    ]


class SpeechContext(ctypes.Structure):
    _fields_ = [
        ("size", c_size_t),
        ("phrases", ctypes.POINTER(c_char_p)),
        ("phrase_count", c_size_t),
        ("boost", c_float),
    ]


class RecognitionOptions(ctypes.Structure):
    _fields_ = [
        ("size", c_size_t),
        ("request_id", c_char_p),
        ("language_code", c_char_p),
        ("interim_results", c_bool),
        ("enable_word_time_offsets", c_bool),
        ("enable_automatic_punctuation", c_bool),
        ("verbatim_transcripts", c_bool),
        ("profanity_filter", c_bool),
        ("stop_history_eou_ms", c_int32),
        ("speech_contexts", ctypes.POINTER(SpeechContext)),
        ("speech_context_count", c_size_t),
        ("max_alternatives", c_int32),
        ("enable_speaker_diarization", c_bool),
        ("max_speaker_count", c_int32),
    ]


STATUS = {
    0: "OK",
    1: "ERROR_INVALID_ARGUMENT",
    2: "ERROR_OUT_OF_MEMORY",
    3: "ERROR_RUNTIME",
    4: "ERROR_CANCELLED",
}


def _bind(lib: ctypes.CDLL) -> None:
    lib.nemo_speech_asr_create.argtypes = [
        ctypes.POINTER(RecognizerConfig),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    lib.nemo_speech_asr_create.restype = ctypes.c_int
    lib.nemo_speech_asr_destroy.argtypes = [ctypes.c_void_p]
    lib.nemo_speech_asr_recognition_options_default.restype = RecognitionOptions
    lib.nemo_speech_asr_recognition_options_default.argtypes = []
    lib.nemo_speech_asr_streaming_recognize.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(RecognitionOptions),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    lib.nemo_speech_asr_streaming_recognize.restype = ctypes.c_int
    lib.nemo_speech_asr_stream_push_f32.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(c_float),
        c_size_t,
        c_int32,
    ]
    lib.nemo_speech_asr_stream_push_f32.restype = ctypes.c_int
    lib.nemo_speech_asr_stream_next.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    lib.nemo_speech_asr_stream_next.restype = ctypes.c_int
    lib.nemo_speech_asr_stream_finish.argtypes = [ctypes.c_void_p]
    lib.nemo_speech_asr_stream_finish.restype = ctypes.c_int
    lib.nemo_speech_asr_stream_close.argtypes = [ctypes.c_void_p]
    lib.nemo_speech_asr_result_transcript.argtypes = [ctypes.c_void_p, c_size_t]
    lib.nemo_speech_asr_result_transcript.restype = c_char_p
    lib.nemo_speech_asr_result_is_final.argtypes = [ctypes.c_void_p]
    lib.nemo_speech_asr_result_is_final.restype = c_bool
    lib.nemo_speech_asr_result_audio_processed.argtypes = [ctypes.c_void_p]
    lib.nemo_speech_asr_result_audio_processed.restype = c_float
    lib.nemo_speech_asr_result_word_count.argtypes = [ctypes.c_void_p, c_size_t]
    lib.nemo_speech_asr_result_word_count.restype = c_size_t
    lib.nemo_speech_asr_result_word_text.argtypes = [ctypes.c_void_p, c_size_t, c_size_t]
    lib.nemo_speech_asr_result_word_text.restype = c_char_p
    lib.nemo_speech_asr_result_word_start_time.argtypes = [ctypes.c_void_p, c_size_t, c_size_t]
    lib.nemo_speech_asr_result_word_start_time.restype = c_int32
    lib.nemo_speech_asr_result_word_end_time.argtypes = [ctypes.c_void_p, c_size_t, c_size_t]
    lib.nemo_speech_asr_result_word_end_time.restype = c_int32
    lib.nemo_speech_asr_result_destroy.argtypes = [ctypes.c_void_p]
    lib.nemo_speech_asr_version.restype = c_char_p
    lib.nemo_speech_asr_last_error.restype = c_char_p


# --------------------------------------------------------------------------------------
# WAV I/O (stdlib only -- no numpy)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Wav:
    samples: array
    rate: int

    @property
    def seconds(self) -> float:
        return len(self.samples) / self.rate


def read_wav(path: Path) -> Wav:
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    if width != 2:
        raise ValueError(f"{path} is not 16-bit PCM")
    raw = array("h")
    raw.frombytes(frames)
    if channels == 2:
        mono = array("h", [0]) * (len(raw) // 2)
        for i in range(len(mono)):
            mono[i] = (raw[2 * i] + raw[2 * i + 1]) // 2
        raw = mono
    elif channels != 1:
        raise ValueError(f"{path} has {channels} channels")
    return Wav(samples=raw, rate=rate)


def to_f32(samples: Sequence[int]) -> array:
    out = array("f", [0.0]) * len(samples)
    for i, value in enumerate(samples):
        out[i] = float(value) / 32768.0
    return out


# --------------------------------------------------------------------------------------
# Driving the stream
# --------------------------------------------------------------------------------------


@dataclass
class Partial:
    index: int
    kind: str  # "interim" | "final"
    wall_s: float
    audio_processed_s: float
    transcript: str
    words: list[str] = field(default_factory=list)
    word_times: list[tuple[str, int, int]] = field(default_factory=list)
    chunk_index: int = -1


@dataclass
class RunResult:
    label: str
    device: str
    chunk_ms: int
    interim: bool
    audio: str
    audio_s: float
    load_wall_s: float
    stream_wall_s: float
    peak_rss_kb: int
    vram_bytes: int | None
    partials: list[Partial] = field(default_factory=list)
    final_text: str = ""
    push_walls: list[float] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    version: str = ""


def peak_rss_kb() -> int:
    import resource

    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


def vram_used_bytes() -> int | None:
    """Global amdgpu VRAM used, from sysfs, or None if unavailable."""
    from glob import glob

    for path in glob("/sys/class/drm/card*/device/mem_info_vram_used"):
        try:
            return int(Path(path).read_text().strip())
        except OSError:
            continue
    return None


def drive(
    *,
    lib: ctypes.CDLL,
    model: Path,
    wav: Wav,
    device: str,
    chunk_ms: int,
    interim: bool,
    label: str,
    audio_name: str,
    wall_timeout_s: float,
    chunk_sec: float = 0.16,
    rnnt_right_context: int = -1,
    pace: bool = False,
) -> RunResult:
    gpu = -1 if device == "cpu" else 0
    backend = BackendConfig(size=ctypes.sizeof(BackendConfig), gpu=gpu)
    model_cfg = ModelConfig(
        size=ctypes.sizeof(ModelConfig),
        path=str(model).encode(),
        name=None,
    )
    streaming = StreamingConfig(
        size=ctypes.sizeof(StreamingConfig),
        chunk_size=chunk_sec,
        ctc_left_padding=0.0,
        ctc_right_padding=0.0,
        rnnt_right_context=rnnt_right_context,
    )
    cfg = RecognizerConfig(
        size=ctypes.sizeof(RecognizerConfig),
        backend=ctypes.pointer(backend),
        model=ctypes.pointer(model_cfg),
        streaming=ctypes.pointer(streaming),
        decoder=None,
        vad=None,
        endpointing=None,
        postproc=None,
        diar=None,
        batching=None,
    )
    result = RunResult(
        label=label,
        device=device,
        chunk_ms=chunk_ms,
        interim=interim,
        audio=audio_name,
        audio_s=wav.seconds,
        load_wall_s=0.0,
        stream_wall_s=0.0,
        peak_rss_kb=0,
        vram_bytes=None,
        version=(lib.nemo_speech_asr_version() or b"").decode(),
    )
    recognizer = ctypes.c_void_p()
    vram_before = vram_used_bytes()
    t0 = time.monotonic()
    status = lib.nemo_speech_asr_create(ctypes.byref(cfg), ctypes.byref(recognizer))
    result.load_wall_s = time.monotonic() - t0
    if status != 0 or not recognizer.value:
        err = (lib.nemo_speech_asr_last_error() or b"").decode()
        raise RuntimeError(f"create failed status={STATUS.get(status, status)}: {err}")

    opts = lib.nemo_speech_asr_recognition_options_default()
    opts.size = ctypes.sizeof(RecognitionOptions)
    opts.interim_results = bool(interim)
    opts.enable_word_time_offsets = True
    opts.enable_automatic_punctuation = True
    stream = ctypes.c_void_p()
    try:
        status = lib.nemo_speech_asr_streaming_recognize(
            recognizer, ctypes.byref(opts), ctypes.byref(stream)
        )
        if status != 0 or not stream.value:
            err = (lib.nemo_speech_asr_last_error() or b"").decode()
            raise RuntimeError(f"streaming_recognize failed: {err}")

        chunk_samples = max(1, round(wav.rate * chunk_ms / 1000.0))
        start = time.monotonic()
        pace_start = start
        audio_pushed_s = 0.0
        index = 0
        chunk_index = 0
        pos = 0
        while pos < len(wav.samples):
            if time.monotonic() - start > wall_timeout_s:
                result.errors.append("wall timeout in feed loop")
                break
            piece = to_f32(wav.samples[pos : pos + chunk_samples])
            pos += chunk_samples
            audio_pushed_s += len(piece) / wav.rate
            buf = (c_float * len(piece)).from_buffer(piece)
            tpush = time.monotonic()
            status = lib.nemo_speech_asr_stream_push_f32(
                stream, buf, len(piece), wav.rate
            )
            if status != 0:
                result.errors.append(
                    f"push status={STATUS.get(status, status)}: "
                    f"{(lib.nemo_speech_asr_last_error() or b'').decode()}"
                )
                break
            _collect(lib, stream, result, chunk_index, start)
            result.push_walls.append(time.monotonic() - tpush)
            chunk_index += 1
            if pace:
                behind = (pace_start + audio_pushed_s) - time.monotonic()
                if behind > 0:
                    time.sleep(behind)

        status = lib.nemo_speech_asr_stream_finish(stream)
        if status != 0:
            result.errors.append(f"finish status={STATUS.get(status, status)}")
        # Drain everything after finish (the end-of-stream final).
        _collect(lib, stream, result, chunk_index, start, drain=True)
        result.stream_wall_s = time.monotonic() - start
        vram_after = vram_used_bytes()
        if vram_before is not None and vram_after is not None:
            result.vram_bytes = max(0, vram_after - vram_before)
    finally:
        lib.nemo_speech_asr_stream_close(stream)
        lib.nemo_speech_asr_destroy(recognizer)
    result.peak_rss_kb = peak_rss_kb()
    finals = [p for p in result.partials if p.kind == "final"]
    result.final_text = finals[-1].transcript if finals else (
        result.partials[-1].transcript if result.partials else ""
    )
    return result


def _collect(
    lib: ctypes.CDLL,
    stream: ctypes.c_void_p,
    result: RunResult,
    chunk_index: int,
    start: float,
    drain: bool = False,
) -> None:
    while True:
        out = ctypes.c_void_p()
        status = lib.nemo_speech_asr_stream_next(stream, ctypes.byref(out))
        if status != 0:
            result.errors.append(f"next status={STATUS.get(status, status)}")
            return
        if not out.value:
            return
        try:
            is_final = bool(lib.nemo_speech_asr_result_is_final(out))
            transcript = (lib.nemo_speech_asr_result_transcript(out, 0) or b"").decode()
            audio_processed = float(lib.nemo_speech_asr_result_audio_processed(out))
            n_words = lib.nemo_speech_asr_result_word_count(out, 0)
            words: list[str] = []
            times: list[tuple[str, int, int]] = []
            for i in range(n_words):
                wt = (lib.nemo_speech_asr_result_word_text(out, 0, i) or b"").decode()
                words.append(wt)
                times.append(
                    (
                        wt,
                        int(lib.nemo_speech_asr_result_word_start_time(out, 0, i)),
                        int(lib.nemo_speech_asr_result_word_end_time(out, 0, i)),
                    )
                )
            result.partials.append(
                Partial(
                    index=len(result.partials),
                    kind="final" if is_final else "interim",
                    wall_s=time.monotonic() - start,
                    audio_processed_s=audio_processed,
                    transcript=transcript,
                    words=words,
                    word_times=times,
                    chunk_index=chunk_index,
                )
            )
        finally:
            lib.nemo_speech_asr_result_destroy(out)


# --------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lib", required=True, type=Path)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--device", default="vulkan", choices=["vulkan", "cpu"])
    parser.add_argument("--chunk-ms", type=int, default=160)
    parser.add_argument("--chunk-sec", type=float, default=0.16)
    parser.add_argument("--rnnt-right-context", type=int, default=-1)
    parser.add_argument("--interim", action="store_true")
    parser.add_argument("--pace", action="store_true", help="feed at real time")
    parser.add_argument("--label", default="run")
    parser.add_argument("--partials-out", type=Path)
    parser.add_argument("--summary-out", type=Path)
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args(argv)

    lib = ctypes.CDLL(str(args.lib))
    _bind(lib)
    wav = read_wav(args.audio)
    t0 = time.monotonic()
    result = drive(
        lib=lib,
        model=args.model,
        wav=wav,
        device=args.device,
        chunk_ms=args.chunk_ms,
        interim=args.interim,
        label=args.label,
        audio_name=args.audio.name,
        wall_timeout_s=args.timeout,
        chunk_sec=args.chunk_sec,
        rnnt_right_context=args.rnnt_right_context,
        pace=args.pace,
    )
    total_wall = time.monotonic() - t0

    if args.partials_out:
        args.partials_out.parent.mkdir(parents=True, exist_ok=True)
        with open(args.partials_out, "w", encoding="utf-8") as handle:
            for p in result.partials:
                handle.write(
                    json.dumps(
                        {
                            "label": result.label,
                            "index": p.index,
                            "kind": p.kind,
                            "chunk_index": p.chunk_index,
                            "wall_s": round(p.wall_s, 4),
                            "audio_processed_s": round(p.audio_processed_s, 3),
                            "transcript": p.transcript,
                            "words": p.words,
                            "word_times": p.word_times,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
    interims = [p for p in result.partials if p.kind == "interim"]
    finals = [p for p in result.partials if p.kind == "final"]
    push = result.push_walls
    steady = push[3:] if len(push) > 3 else push
    first_interim = interims[0] if interims else None
    summary: dict[str, Any] = {
        "label": result.label,
        "version": result.version,
        "device": result.device,
        "chunk_ms": result.chunk_ms,
        "chunk_sec": args.chunk_sec,
        "rnnt_right_context": args.rnnt_right_context,
        "interim_requested": result.interim,
        "paced": args.pace,
        "audio": result.audio,
        "audio_s": round(result.audio_s, 3),
        "load_wall_s": round(result.load_wall_s, 3),
        "stream_wall_s": round(result.stream_wall_s, 3),
        "total_wall_s": round(total_wall, 3),
        "rtf": round(result.audio_s / result.stream_wall_s, 3) if result.stream_wall_s else None,
        "peak_rss_mb": round(result.peak_rss_kb / 1024.0, 1),
        "vram_bytes": result.vram_bytes,
        "vram_mb": round(result.vram_bytes / 1e6, 1) if result.vram_bytes is not None else None,
        "n_partials": len(result.partials),
        "n_interims": len(interims),
        "n_finals": len(finals),
        "push_n": len(push),
        "push_first_ms": round(1000.0 * push[0], 3) if push else None,
        "push_ms_mean": round(1000.0 * sum(push) / len(push), 3) if push else None,
        "push_ms_p50": round(1000.0 * sorted(push)[len(push) // 2], 3) if push else None,
        "push_ms_p90": round(
            1000.0 * sorted(push)[min(len(push) - 1, int(0.9 * len(push)))], 3
        )
        if push
        else None,
        "push_ms_max": round(1000.0 * max(push), 3) if push else None,
        "steady_n": len(steady),
        "steady_ms_mean": round(1000.0 * sum(steady) / len(steady), 3) if steady else None,
        "steady_ms_p50": round(1000.0 * sorted(steady)[len(steady) // 2], 3) if steady else None,
        "steady_ms_p90": round(
            1000.0 * sorted(steady)[min(len(steady) - 1, int(0.9 * len(steady)))], 3
        )
        if steady
        else None,
        "steady_ms_max": round(1000.0 * max(steady), 3) if steady else None,
        "first_interim_wall_s": round(first_interim.wall_s, 3) if first_interim else None,
        "first_interim_audio_s": round(first_interim.audio_processed_s, 3)
        if first_interim
        else None,
        "final_text": result.final_text,
        "errors": result.errors,
    }
    if args.summary_out:
        args.summary_out.parent.mkdir(parents=True, exist_ok=True)
        with open(args.summary_out, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(summary, ensure_ascii=False) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not result.errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
