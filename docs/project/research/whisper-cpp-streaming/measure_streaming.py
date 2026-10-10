#!/usr/bin/env python3
"""Simulate whisper.cpp streaming with ges2t's commit rule (discovery harness).

This is a **diagnostic**, not part of the shipped package. It is deliberately outside
``src/ges2t`` so ``uv run pytest`` stays headless and engine-free (ADR 0014).

The question
------------
ges2t's Whistle streaming path commits "the words two consecutive passes agree on"
(ADR 0011 / ``needle.h``). whisper.cpp has no native streaming, so this harness replays
a recorded clip as a stream and applies the same **N=2 consecutive-passes-agree** rule
across passes. It answers whether whisper.cpp's partial-window output is stable enough to
use that rule, on the Vulkan/GPU path, and at what window/hop/model/latency/duty.

The commit rule, mirrored
-------------------------
ges2t's rule (``src/ges2t/streaming.py`` + ADR 0011): ``process()`` returns the words
**this pass committed** -- "the words two consecutive passes agree on" (N = **2**) --
which ``drive()`` appends to an append-only transcript; ``flush()`` at a window boundary
commits the unconfirmed tail and resets the stream. There is no vote count or threshold
in ges2t: the agreement is exactly two passes.

Two schemes are simulated
-------------------------
``cap`` (the faithful mirror, default)
    Exactly ges2t's windowing: a buffer starts empty at a reset point ``R``, grows by
    ``H`` seconds per pass, and once the audio fed since the reset reaches ``W`` the
    engine's ``stop()`` is modelled by committing the whole tail and resetting ``R``.
    Consecutive passes therefore see the **same growing buffer**, so their transcripts are
    prefix-comparable and the agreed prefix is the plain longest-common-prefix of the two
    pass transcripts -- the literal "two consecutive passes agree" rule, with no alignment
    assumption.

``slide`` (the literal "last W seconds")
    A fixed sliding window of the last ``W`` seconds, advancing ``H`` seconds per pass.
    A fixed window rewrites its own head each hop, so agreement is measured over the audio
    the two windows share, using the token timestamps from ``-ojf``; the agreed prefix is
    the positional longest common prefix of the two overlap word sequences. Included as a
    stability probe because the window's left edge is where whisper rewrites.

No 30 s padding
---------------
Whisper's encoder always runs over a 30 s mel segment, so the CLI pads internally; the
harness never pads the WAV. Physically appending silence would only add decoder work and
re-risk the project's silence-hallucination bug (REPORT.md section 9).

Usage
-----
    uv run python measure_streaming.py --combo --scheme cap \
        --model <path.bin> --model-name medium-q5_0 --backend vulkan \
        --window 5 --hop 2 --audio <clip.wav> --label L --reference-key commit \
        --passes-out data/passes/L.jsonl --summary-out data/summary.jsonl

``run_matrix.sh`` drives the whole matrix, one bounded ``whisper-cli`` run per process.
Everything heavy (sliced WAVs, per-pass JSON) lives in the scratch dir; the repo gains
only the curated JSONL.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
import time
import wave
from array import array
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

# --------------------------------------------------------------------------------------
# Scratch and reference clips (never committed; scratch lives in ~/.cache)
# --------------------------------------------------------------------------------------

DISCOVERY: Path = Path.home() / ".cache/ges2t-discovery" / "whisper-cpp-provider"
SCRATCH: Path = Path.home() / ".cache" / "ges2t-discovery" / "whisper-cpp-streaming-scratch"
MODELS: Path = DISCOVERY / "models"
AUDIO: Path = DISCOVERY / "audio"
VULKAN_CLI: Path = DISCOVERY / "whisper.cpp" / "build-vulkan" / "bin" / "whisper-cli"
CPU_CLI: Path = DISCOVERY / "whisper.cpp" / "build" / "bin" / "whisper-cli"

#: ticket 32's harness, imported by path so scoring matches the merged report exactly.
TICKET32_HARNESS: Path = (
    Path(__file__).resolve().parents[2]
    / "live-dictation-ux"
    / "commit-cadence-diagnostic"
    / "measure_commit_cadence.py"
)

#: Reference passages, exactly as the merged whisper.cpp report used them.
COMMIT_REFERENCE: str = (
    "Good morning. Last night's storm knocked out power to roughly four thousand homes "
    "across the northern valley, and crews expect to restore most of them by Friday "
    "afternoon. Officials say the river crested at 3.2 metres just after midnight, well "
    "below the flood barrier. Is that reading correct? Two low-lying roads remain closed "
    "tonight, and the ferry service resumes at half past six."
)
JFK_REFERENCE: str = (
    "And so my fellow Americans, ask not what your country can do for you, "
    "ask what you can do for your country."
)
QUIET_LONG_REFERENCE: str = (
    "Hi Sam, quick update on the kitchen order. The tiles arrive Tuesday morning, and the "
    "invoice came to four hundred and twenty euros, a bit more than we budgeted, so check "
    "whether their quote included delivery. The plumber can only start on the fourteenth, "
    "which means the painting may slip by a week. I'll confirm the exact measurements for "
    "the north wall tonight. Let me know if that still works for you."
)
#: clip30 has no human reference checked in (ticket 32 used a per-clip engine batch file
#: that is not in the repo). ``None`` means the final WER is reported against the one-shot
#: batch whisper transcript of the same clip, i.e. it is a *divergence*, not accuracy.
REFERENCES: dict[str, str | None] = {
    "commit": COMMIT_REFERENCE,
    "jfk": JFK_REFERENCE,
    "quiet-long": QUIET_LONG_REFERENCE,
    "clip30": None,
}


def load_ticket32() -> Any:
    """Import ticket 32's harness by path and return it (for word_diff / tokenise)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("ges2t_ticket32_measure", TICKET32_HARNESS)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot import ticket 32's harness at {TICKET32_HARNESS}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------------------
# WAV I/O (stdlib wave only -- no numpy)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Wav:
    """A 16 kHz mono 16-bit WAV: the samples and the frame rate."""

    samples: array
    rate: int

    @property
    def seconds(self) -> float:
        """Duration in seconds."""
        return len(self.samples) / self.rate


def read_wav(path: Path) -> Wav:
    """Read a 16 kHz mono 16-bit PCM WAV into an ``array('h')``."""
    with wave.open(str(path), "rb") as handle:
        if handle.getnchannels() != 1 or handle.getsampwidth() != 2:
            raise ValueError(f"{path} is not 1ch/16-bit PCM")
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    samples = array("h")
    samples.frombytes(frames)
    return Wav(samples=samples, rate=rate)


def write_wav(path: Path, wav: Wav) -> None:
    """Write ``wav`` as 16 kHz mono 16-bit PCM under ``path``."""
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(wav.rate)
        handle.writeframes(wav.samples.tobytes())


def slice_wav(wav: Wav, start_s: float, end_s: float) -> Wav:
    """Return ``wav``'s samples in ``[start_s, end_s)`` (clamped to the clip)."""
    start = max(0, round(start_s * wav.rate))
    end = min(len(wav.samples), round(end_s * wav.rate))
    return Wav(samples=wav.samples[start:end], rate=wav.rate)


# --------------------------------------------------------------------------------------
# The CLI run and its token-timestamp parsing
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CliResult:
    """One ``whisper-cli`` invocation."""

    transcript: str
    words: tuple[tuple[str, float], ...]
    wall_s: float
    exit_code: int


def run_cli(
    *,
    cli: Path,
    model: Path,
    wav_path: Path,
    out_base: Path,
    timeout_s: float,
    timestamps: bool,
    duration_s: float,
) -> CliResult:
    """Run ``whisper-cli`` once.

    With ``timestamps`` the CLI is run ``-ojf`` and the token timestamps give each word
    its real source time. Without it the CLI is run ``-nt -np`` -- the **exact** decode the
    merged whisper.cpp report used -- and the words are spread uniformly across the buffer
    so the append-only bookkeeping and the latency proxy still work. ``-nt`` masks
    whisper's timestamp tokens and therefore changes the greedy decode (car-clip batch
    word_diff WER 0.062 with ``-nt`` vs 0.156 without), so the accuracy numbers must use
    ``-nt`` to be comparable to the merged report.

    The run is bounded by ``timeout_s``; on expiry the child is killed and reaped so no
    process outlives this call.
    """
    import subprocess

    argv = [
        str(cli),
        "-m", str(model),
        "-f", str(wav_path),
        "-l", "en",
        "-t", "16",
        "-bs", "1",
        "-bo", "1",
        "-np",
    ]
    if timestamps:
        argv += ["-ojf", "-of", str(out_base)]
    else:
        argv += ["-nt"]
    start = time.monotonic()
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE if not timestamps else subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    timed_out = False
    try:
        stdout, _ = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        stdout, _ = proc.communicate()
    wall = time.monotonic() - start
    if not timestamps:
        transcript = (stdout or b"").decode("utf-8", "replace").strip()
        return CliResult(
            transcript=transcript,
            words=synthesise_words(transcript, duration_s),
            wall_s=wall,
            exit_code=proc.returncode if proc.returncode is not None else -1,
        )
    json_path = out_base.with_suffix(".json")
    if not json_path.exists():
        raise RuntimeError(
            f"whisper-cli produced no JSON at {json_path} "
            f"(exit={proc.returncode}, timed_out={timed_out})"
        )
    data = json.loads(json_path.read_text(encoding="utf-8"))
    json_path.unlink(missing_ok=True)
    segments = data.get("transcription", [])
    transcript = "".join(segment.get("text", "") for segment in segments).strip()
    tokens = [token for segment in segments for token in segment.get("tokens", [])]
    return CliResult(
        transcript=transcript,
        words=reconstruct_words(tokens),
        wall_s=wall,
        exit_code=proc.returncode if proc.returncode is not None else -1,
    )


def synthesise_words(transcript: str, duration_s: float) -> tuple[tuple[str, float], ...]:
    """Normalise ``transcript`` and spread its words uniformly over ``duration_s``.

    Used only when the ``-nt`` decode is chosen and no token timestamps exist. The times
    are a monotone proxy, not measurements; they order the words and give a bounded
    latency estimate.
    """
    t32 = load_ticket32()
    words, _ = t32.tokenise(transcript)
    total = max(1, len(words))
    return tuple((word, duration_s * (index + 0.5) / total) for index, word in enumerate(words))


def reconstruct_words(tokens: Sequence[dict[str, Any]]) -> tuple[tuple[str, float], ...]:
    """Rebuild normalised ``(word, absolute_second)`` pairs from whisper tokens.

    whisper's tokens are not whitespace words: ``night`` + ``'s`` are two tokens but one
    word, and a punctuation token sticks to the word before it. A new word starts when a
    token's text begins with a space; other tokens append to the current word. The word is
    normalised with ticket 32's ``_normalise_token`` (so ``night's`` -> ``nights``) and
    takes the first sub-token's timestamp. Special tokens -- ``[_BEG_]``, ``[_TT_...]``
    and the bracketed non-speech markers ``[BLANK_AUDIO]``/``[MUSIC]``/``[SOUND]`` -- are
    dropped, so pure silence produces no words. Cross-checked against
    ``tokenise(transcript)`` by ``--verify-tokens``.
    """
    t32 = load_ticket32()
    words: list[tuple[str, float]] = []
    raw_buffers: list[tuple[str, float]] = []
    buffer: str | None = None
    start_s = 0.0
    for token in tokens:
        text = str(token.get("text", ""))
        stripped = text.strip()
        if stripped.startswith("[_") and stripped.endswith("]"):
            # [_BEG_] / [_TT_...]: whisper.cpp's own out-of-band tokens.
            continue
        offset_s = float(token.get("offsets", {}).get("from", 0)) / 1000.0
        if buffer is None:
            buffer, start_s = text, offset_s
        elif text.startswith(" "):
            raw_buffers.append((buffer, start_s))
            buffer, start_s = text, offset_s
        else:
            buffer += text
    if buffer is not None:
        raw_buffers.append((buffer, start_s))
    for raw, start_s in raw_buffers:
        stripped = raw.strip()
        if stripped.startswith("[_") and stripped.endswith("]"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            # [BLANK_AUDIO] / [MUSIC] / [SOUND]: a non-speech marker, not a word.
            continue
        for word in t32._normalise_token(raw):  # noqa: SLF001
            words.append((word, start_s))
    return tuple(words)


# --------------------------------------------------------------------------------------
# The commit rule
# --------------------------------------------------------------------------------------


def longest_common_prefix(a: Sequence[str], b: Sequence[str]) -> int:
    """Length of the longest common prefix of ``a`` and ``b``."""
    limit = min(len(a), len(b))
    for index in range(limit):
        if a[index] != b[index]:
            return index
    return limit


def longest_suffix_prefix(a: Sequence[str], b: Sequence[str]) -> int:
    """Longest suffix of ``a`` that is also a prefix of ``b`` (0 if none).

    A fixed sliding window rewrites its own head, so the shared audio appears as the
    *tail* of the previous window and the *head* of the current one; a plain prefix match
    would read the shift as total disagreement. This finds the shift.
    """
    best = 0
    for start in range(len(a) + 1):
        length = len(a) - start
        if length == 0 or length > len(b):
            continue
        if list(a[start:]) == list(b[:length]):
            best = max(best, length)
    return best


def sequence_overlap(a: Sequence[str], b: Sequence[str]) -> tuple[int, int]:
    """Return ``(matched, total)`` words under a best alignment (difflib).

    Used only for the stability metric: how many words of the shared region two passes
    render identically, regardless of a small positional shift. ``total`` is the longer
    sequence's length.
    """
    matcher = difflib.SequenceMatcher(a=list(a), b=list(b), autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    return matched, max(len(a), len(b))


@dataclass
class PassRecord:
    """One window pass: what it saw, said, and committed."""

    index: int
    scheme: str
    window_start_s: float
    window_end_s: float
    buffer_s: float
    wall_s: float
    transcript: str
    words: tuple[tuple[str, float], ...]
    overlap_prev_words: tuple[str, ...]
    overlap_cur_words: tuple[str, ...]
    agreed: int
    committed_new: tuple[str, ...]
    commit_audio_s: float | None
    flush_tail_words: tuple[str, ...]
    flushed: bool
    committed_total_words: int
    pending_words: int
    stability_matched: int
    stability_total: int
    reproduces_committed: bool


@dataclass
class ComboResult:
    """Everything measured for one (model, backend, scheme, W, H, clip) combination."""

    label: str
    model: str
    backend: str
    scheme: str
    window_s: float
    hop_s: float
    audio: str
    audio_s: float
    passes: list[PassRecord] = field(default_factory=list)
    committed_words: list[str] = field(default_factory=list)
    committed_until_s: float = 0.0
    window_committed_floor: int = 0
    window_committed_len: int = 0
    errors: list[str] = field(default_factory=list)


def _commit_new(
    result: ComboResult, pairs: Sequence[tuple[str, float]]
) -> tuple[tuple[str, ...], float | None]:
    """Append ``pairs`` beyond the committed watermark; return the new words and time."""
    new_words: list[str] = []
    commit_audio_s: float | None = None
    for word, sec in pairs:
        if sec > result.committed_until_s + 1e-6:
            new_words.append(word)
            result.committed_until_s = max(result.committed_until_s, sec)
            commit_audio_s = sec
    result.committed_words.extend(new_words)
    return tuple(new_words), commit_audio_s


def _pass_end_times(duration: float, hop_s: float, scheme: str, window_s: float) -> list[tuple[float, float, float]]:
    """Return the ``(start, end, buffer)`` of every pass for one scheme."""
    if scheme == "cap":
        schedule: list[tuple[float, float, float]] = []
        reset = 0.0
        fed = 0.0
        while True:
            fed += hop_s
            end = min(reset + fed, duration)
            buffer = end - reset
            schedule.append((reset, end, buffer))
            if end >= duration - 1e-9:
                break
            if fed >= window_s:
                reset = end
                fed = 0.0
        return schedule
    # slide: a fixed window of the last W seconds, advancing by H.
    schedule = []
    t = hop_s
    while t < duration - 1e-9:
        schedule.append((max(0.0, t - window_s), round(t, 6), t - max(0.0, t - window_s)))
        t += hop_s
    if not schedule or duration - schedule[-1][1] >= 0.5:
        start = max(0.0, duration - window_s)
        schedule.append((start, round(duration, 6), duration - start))
    return schedule


def simulate(
    *,
    cli: Path,
    model: Path,
    wav: Wav,
    window_s: float,
    hop_s: float,
    scheme: str,
    label: str,
    model_name: str,
    backend: str,
    audio_name: str,
    timeout_s: float,
    scratch: Path,
    timestamps: bool = True,
) -> ComboResult:
    """Replay ``wav`` as a stream and apply the N=2 agreement rule across passes."""
    duration = wav.seconds
    schedule = _pass_end_times(duration, hop_s, scheme, window_s)

    scratch.mkdir(parents=True, exist_ok=True)
    result = ComboResult(
        label=label,
        model=model_name,
        backend=backend,
        scheme=scheme,
        window_s=window_s,
        hop_s=hop_s,
        audio=audio_name,
        audio_s=duration,
    )
    prev_words: tuple[tuple[str, float], ...] = ()
    prev_end = 0.0
    for index, (start, end, _buffer) in enumerate(schedule):
        wav_path = scratch / f"pass_{index:04d}.wav"
        out_base = scratch / f"pass_{index:04d}"
        write_wav(wav_path, slice_wav(wav, start, end))
        try:
            cli_result = run_cli(
                cli=cli,
                model=model,
                wav_path=wav_path,
                out_base=out_base,
                timeout_s=timeout_s,
                timestamps=timestamps,
                duration_s=end - start,
            )
        except RuntimeError as exc:
            result.errors.append(f"pass {index}: {exc}")
            break
        finally:
            wav_path.unlink(missing_ok=True)

        abs_words = tuple((word, start + sec) for word, sec in cli_result.words)
        prior_committed = list(result.committed_words[result.window_committed_floor :])

        if scheme == "cap":
            # Consecutive passes see the same growing buffer -> plain LCP. The committed
            # prefix is a *count* within the window, so a word cannot be committed twice
            # because its estimated time drifted.
            prev_seq = tuple(word for word, _ in prev_words)
            cur_seq = tuple(word for word, _ in abs_words)
            agreed = longest_common_prefix(prev_seq, cur_seq) if prev_words else 0
            overlap_prev = prev_seq
            overlap_cur = cur_seq
            committed_new = ()
            commit_audio_s = None
            if agreed > result.window_committed_len:
                new_words = cur_seq[result.window_committed_len : agreed]
                result.committed_words.extend(new_words)
                if timestamps and new_words:
                    commit_audio_s = abs_words[agreed - 1][1]
                result.window_committed_len = agreed
                committed_new = tuple(new_words)
            flush_tail: tuple[str, ...] = ()
            flushed = False
            full_buffer = end - start
            if full_buffer >= window_s - 1e-9 or end >= duration - 1e-9:
                flushed = True
                tail = cur_seq[result.window_committed_len :]
                result.committed_words.extend(tail)
                if timestamps and tail:
                    commit_audio_s = abs_words[-1][1]
                flush_tail = tuple(tail)
                result.window_committed_len = 0
                prev_words = ()
                prev_end = end
                result.window_committed_floor = len(result.committed_words)
            else:
                prev_words = abs_words
                prev_end = end
            pending_words = max(0, len(cur_seq) - result.window_committed_len)
        else:
            # Fixed window: compare only over the shared audio (all of the previous
            # window, the part of the current window that is not newer than prev_end). The
            # shared words appear as the previous window's tail and the current window's
            # head, so the agreed run is the longest suffix/prefix match.
            overlap_prev = tuple(word for word, _ in prev_words)
            cur_pairs = tuple(
                (word, sec) for word, sec in abs_words if sec <= prev_end + 1e-6
            )
            overlap_cur = tuple(word for word, _ in cur_pairs)
            agreed = longest_suffix_prefix(overlap_prev, overlap_cur) if prev_words else 0
            committed_new, commit_audio_s = _commit_new(result, cur_pairs[:agreed])
            flush_tail = ()
            flushed = False
            prev_words = abs_words
            prev_end = end
            full_buffer = end - start
            pending_words = sum(
                1 for _, sec in abs_words if sec > result.committed_until_s + 1e-6
            )

        stability_matched, stability_total = sequence_overlap(overlap_prev, overlap_cur)
        cur_seq_full = tuple(word for word, _ in abs_words)
        reproduces_committed = (
            list(cur_seq_full[: len(prior_committed)]) == prior_committed
            if prior_committed
            else True
        )
        result.passes.append(
            PassRecord(
                index=index,
                scheme=scheme,
                window_start_s=start,
                window_end_s=end,
                buffer_s=full_buffer,
                wall_s=cli_result.wall_s,
                transcript=cli_result.transcript,
                words=abs_words,
                overlap_prev_words=overlap_prev,
                overlap_cur_words=overlap_cur,
                agreed=agreed,
                committed_new=committed_new,
                commit_audio_s=commit_audio_s,
                flush_tail_words=flush_tail,
                flushed=flushed,
                committed_total_words=len(result.committed_words),
                pending_words=pending_words,
                stability_matched=stability_matched,
                stability_total=stability_total,
                reproduces_committed=reproduces_committed,
            )
        )
    return result


# --------------------------------------------------------------------------------------
# Summarising one combination
# --------------------------------------------------------------------------------------


def percentile(values: Sequence[float], fraction: float) -> float:
    """Linear-interpolated percentile of ``values`` (``values`` must be non-empty)."""
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def summarise(
    result: ComboResult,
    *,
    reference: str | None,
    batch_reference: str | None,
    reference_key: str | None,
) -> dict[str, Any]:
    """Reduce one combination to the row the report renders (all numbers from the run)."""
    t32 = load_ticket32()
    committed = " ".join(result.committed_words)
    passes = result.passes
    total_wall = sum(p.wall_s for p in passes)

    committing = [p for p in passes if p.committed_new or p.flush_tail_words]
    first_commit_audio = committing[0].window_end_s if committing else None
    first_commit_wall = (
        sum(p.wall_s for p in passes[: committing[0].index + 1]) if committing else None
    )
    latencies = [
        p.window_end_s - p.commit_audio_s
        for p in committing
        if p.commit_audio_s is not None
    ]

    pairs = [p for p in passes if p.index > 0]
    overlap_pairs = [p for p in pairs if p.overlap_prev_words]
    pairs_with_overlap = len(overlap_pairs)
    word_match = sum(p.agreed for p in overlap_pairs)
    word_total = sum(max(len(p.overlap_prev_words), len(p.overlap_cur_words)) for p in overlap_pairs)
    stab_match = sum(p.stability_matched for p in overlap_pairs)
    stab_total = sum(p.stability_total for p in overlap_pairs)
    rewritten = [
        (p.index, p.overlap_prev_words, p.overlap_cur_words)
        for p in overlap_pairs
        if p.overlap_prev_words != p.overlap_cur_words and p.overlap_cur_words
    ]

    pending = [p.pending_words for p in passes]

    wer_worddiff = None
    wer_std = None
    ins = sub = deletion = ref_words = None
    if reference is not None:
        diff = t32.word_diff(reference, committed)
        wer_worddiff = diff.wer
        ins, sub, deletion, ref_words = (
            diff.insertions,
            diff.substitutions,
            diff.deletions,
            diff.reference_words,
        )
        ref_seq, _ = t32.tokenise(reference)
        hyp_seq, _ = t32.tokenise(committed)
        wer_std = _std_wer(ref_seq, hyp_seq)
    divergence = None
    if batch_reference is not None:
        divergence = t32.word_diff(batch_reference, committed).wer

    return {
        "label": result.label,
        "model": result.model,
        "backend": result.backend,
        "scheme": result.scheme,
        "window_s": result.window_s,
        "hop_s": result.hop_s,
        "audio": result.audio,
        "audio_s": round(result.audio_s, 3),
        "passes": len(passes),
        "total_wall_s": round(total_wall, 3),
        "duty": round(total_wall / result.audio_s, 4) if result.audio_s else None,
        "first_commit_audio_s": first_commit_audio,
        "first_commit_wall_s": round(first_commit_wall, 3) if first_commit_wall else None,
        "commits": len(committing),
    "windows": sum(1 for p in passes if p.flushed),
    "flush_tail_words": sum(len(p.flush_tail_words) for p in passes),
        "commit_latency_median_s": round(percentile(latencies, 0.5), 3) if latencies else None,
        "commit_latency_p90_s": round(percentile(latencies, 0.9), 3) if latencies else None,
        "commit_latency_max_s": round(max(latencies), 3) if latencies else None,
        "pairs_with_overlap": pairs_with_overlap,
        "word_agreement": round(word_match / word_total, 4) if word_total else None,
        "stability": round(stab_match / stab_total, 4) if stab_total else None,
        "rewritten_pairs": len(rewritten),
        "regressed_passes": sum(
            1 for p in pairs if p.overlap_prev_words and not p.reproduces_committed
        ),
        "committed_words": len(result.committed_words),
        "pending_max_words": max(pending) if pending else 0,
        "reference_key": reference_key,
        "wer_worddiff": round(wer_worddiff, 4) if wer_worddiff is not None else None,
        "wer_std": round(wer_std, 4) if wer_std is not None else None,
        "worddiff_sub": sub,
        "worddiff_ins": ins,
        "worddiff_del": deletion,
        "reference_words": ref_words,
        "batch_divergence_worddiff": round(divergence, 4) if divergence is not None else None,
        "committed_text": committed,
        "errors": result.errors,
    }


def _std_wer(reference_words: Sequence[str], hyp_words: Sequence[str]) -> float:
    """Standard word edit distance / reference length (matches the merged report)."""
    n, m = len(reference_words), len(hyp_words)
    if n == 0:
        return 0.0
    previous = list(range(m + 1))
    for i in range(1, n + 1):
        current = [i] + [0] * m
        for j in range(1, m + 1):
            cost = 0 if reference_words[i - 1] == hyp_words[j - 1] else 1
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
        previous = current
    return previous[m] / n


# --------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------


def verify_tokens(cli: Path, model: Path, wav_path: Path) -> int:
    """Assert the token reconstruction matches ticket 32's ``tokenise`` on a real clip."""
    import subprocess

    out_base = SCRATCH / "verify"
    out_base.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [str(cli), "-m", str(model), "-f", str(wav_path), "-l", "en", "-t", "16",
         "-bs", "1", "-bo", "1", "-np", "-ojf", "-of", str(out_base)],
        capture_output=True, timeout=300, check=False,
    )
    data = json.loads(out_base.with_suffix(".json").read_text(encoding="utf-8"))
    out_base.with_suffix(".json").unlink(missing_ok=True)
    segments = data.get("transcription", [])
    transcript = "".join(s.get("text", "") for s in segments).strip()
    tokens = [t for s in segments for t in s.get("tokens", [])]
    rebuilt = [w for w, _ in reconstruct_words(tokens)]
    t32 = load_ticket32()
    expected, _ = t32.tokenise(transcript)
    ok = rebuilt == expected
    print(f"verify-tokens: rebuilt={len(rebuilt)} expected={len(expected)} MATCH={ok}")
    if not ok:
        print("rebuilt :", rebuilt)
        print("expected:", expected)
    return 0 if ok else 1


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments, run one combination, append the raw and summary rows."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--model-name", default=None)
    parser.add_argument("--backend", default="vulkan", choices=["cpu", "vulkan"])
    parser.add_argument("--scheme", default="cap", choices=["cap", "slide"])
    parser.add_argument("--window", type=float)
    parser.add_argument("--hop", type=float)
    parser.add_argument("--audio", type=Path)
    parser.add_argument("--label", default=None)
    parser.add_argument("--reference-key", default=None)
    parser.add_argument("--batch-reference-key", default=None)
    parser.add_argument("--passes-out", type=Path)
    parser.add_argument("--summary-out", type=Path)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--verify-tokens", action="store_true")
    parser.add_argument(
        "--no-timestamps",
        action="store_true",
        help="use the -nt decode (merged-report comparable) instead of -ojf timestamps",
    )
    args = parser.parse_args(argv)

    cli = VULKAN_CLI if args.backend == "vulkan" else CPU_CLI
    if args.verify_tokens:
        return verify_tokens(cli, args.model, args.audio)

    wav = read_wav(args.audio)
    reference = REFERENCES.get(args.reference_key) if args.reference_key else None
    batch_reference = (
        REFERENCES.get(args.batch_reference_key) if args.batch_reference_key else None
    )

    result = simulate(
        cli=cli,
        model=args.model,
        wav=wav,
        window_s=args.window,
        hop_s=args.hop,
        scheme=args.scheme,
        label=args.label,
        model_name=args.model_name,
        backend=args.backend,
        audio_name=args.audio.name,
        timeout_s=args.timeout,
        scratch=SCRATCH / args.label,
        timestamps=not args.no_timestamps,
    )

    args.passes_out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.passes_out, "w", encoding="utf-8") as handle:
        for record in result.passes:
            handle.write(
                json.dumps(
                    {
                        "index": record.index,
                        "scheme": record.scheme,
                        "window_start_s": round(record.window_start_s, 3),
                        "window_end_s": round(record.window_end_s, 3),
                        "buffer_s": round(record.buffer_s, 3),
                        "wall_s": round(record.wall_s, 3),
                        "transcript": record.transcript,
                        "n_words": len(record.words),
                        "overlap_prev_all": list(record.overlap_prev_words),
                        "overlap_cur_all": list(record.overlap_cur_words),
                        "agreed": record.agreed,
                        "committed_new": list(record.committed_new),
                        "commit_audio_s": record.commit_audio_s,
                        "flush_tail": list(record.flush_tail_words),
                        "flushed": record.flushed,
                        "committed_total_words": record.committed_total_words,
                        "pending_words": record.pending_words,
                        "stability_matched": record.stability_matched,
                        "stability_total": record.stability_total,
                        "reproduces_committed": record.reproduces_committed,
                    }
                )
            )
            handle.write("\n")

    summary = summarise(
        result,
        reference=reference,
        batch_reference=batch_reference,
        reference_key=args.reference_key,
    )
    with open(args.summary_out, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(summary) + "\n")

    print(
        f"{args.label:46s} passes={summary['passes']:3d} "
        f"wall={summary['total_wall_s']:8.2f}s duty={summary['duty']} "
        f"first={summary['first_commit_audio_s']} lat={summary['commit_latency_median_s']} "
        f"agree={summary['word_agreement']} stab={summary['stability']} "
        f"wer={summary['wer_worddiff']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
