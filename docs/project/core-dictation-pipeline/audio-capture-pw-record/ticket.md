# Audio capture via pw-record

- **Priority:** P0
- **Status:** done
- **Complexity:** M
- **Epic:** ../epic.md

## Current state / context

There is no audio capture. ADR 0008 chose `pw-record` as a subprocess over `sounddevice`,
for three reasons that matter to this ticket: it is already installed, it adds no Python
dependency (Python 3.14 wheels are thin), and `pw-record --rate 16000 --channels 1 --format
f32` makes **PipeWire itself resample**, so we receive exactly the engine's contract —
16 kHz mono float32 — with no DSP on our side.

The `AudioSource` protocol this must satisfy is fixed by
`../core-interfaces-and-fakes/ticket.md`: `start()`, `chunks(chunk_seconds)`, and
`stop() -> AudioBuffer`.

Environment facts: PipeWire is active, `/usr/bin/pw-record` exists, and the input device must
be **configurable** (explicit user requirement, ADR 0009).

Relevant decisions: ADR 0008 (capture strategy and failure modes), ADR 0005 (audio contract),
ADR 0011 (1 s chunks feed the streaming path).

## Definition of done

- [x] `src/ges2t/audio/__init__.py` exists (may be empty).
- [x] `src/ges2t/audio/pw_record.py` defines `class PwRecordAudioSource` satisfying
      `AudioSource`:
      - `__init__(self, device: str = "")` — `device` is a PipeWire target passed as
        `--target`; empty means the system default;
      - `start() -> None` spawns `pw-record` with `--rate 16000 --channels 1 --format f32`,
        writing to stdout. Verified against `pw-record --help` on this machine; the built
        invocation is `pw-record --rate 16000 --channels 1 --format f32 --raw --target
        <device> -`, recorded in `001-comment.md` (`--raw` was **required** in addition to
        the three flags: without it stdout carries a container header);
      - `chunks(chunk_seconds: float) -> Iterator[AudioBuffer]` yields roughly
        `chunk_seconds` of audio as it arrives, reading raw float32 bytes off stdout and
        converting with `array('f')`;
      - `stop() -> AudioBuffer` ends capture, **drains stdout to EOF**, and returns
        everything captured in this recording — including audio already yielded by
        `chunks()`.
- [x] **Stop must not truncate.** A `SIGTERM`/early-close path can lose buffered audio, so the
      stop path must let the process exit cleanly and read what it produced before
      finalising (ADR 0008). The returned buffer's sample count must match the sum of the
      chunks yielded.
- [x] Failure modes each raise a clear, actionable error instead of returning a short or
      empty buffer silently:
      - no PipeWire session / `pw-record` missing from `PATH`;
      - ~~an unknown `--target` device~~ — **REMOVED 2026-10-09; no longer a requirement.**
        Measured: `--target` is only a PipeWire `node.target` *hint*, so an unresolved target
        silently falls back to the default node, exits normally and prints nothing on stderr,
        and no `pw-record` flag makes it fatal. Detecting it needs `pw-dump`/`wpctl`
        enumeration — new scope no ADR authorises. Accepted: an unresolved target yields a
        **full recording from the default device**, which still satisfies this bullet's real
        purpose (never a silent short or empty buffer). Recorded as an amendment in ADR 0008;
        the user-facing safety net is the `ges2t doctor` check that the *configured* device
        exists in the session's node list.
      - `pw-record` exiting non-zero before any audio — the error must include its **stderr**;
      - `stop()` before `start()`, or `start()` twice.
- [x] A recording that captured **zero samples** returns an empty `AudioBuffer` rather than
      raising; the caller decides not to call the engine (ADR 0005, ADR 0012).
- [x] `tests/test_pw_record.py` runs with **no microphone**: it injects a fake process
      (a small object with `stdout`, `stderr`, `wait()`, `terminate()`) and asserts the
      constructed argv includes `--rate 16000 --channels 1 --format f32`, that a configured
      device becomes `--target <device>`, that chunk conversion yields the expected sample
      counts, that drain-on-stop returns the full buffer, and each error branch above,
      including that stderr text reaches the raised error.
- [x] `uv run pytest tests/test_pw_record.py` passes and `uv run pytest` stays green.
- [x] **Manual, once**, reported in `001-comment.md`: a real 3-second recording produces a
      buffer whose sample count is within 5 % of `3 × 16000` (47418 samples, 1.21 %), and
      the measured `start()`-to-first-sample latency is reported as a number (100–120 ms to
      the first stdout block, 164–182 ms to the first 0.1 s chunk).

- [x] `uv run mypy src/ges2t` exits 0: every definition fully annotated and every
      signature interface-typed — ADR 0019.

## Blocker

One DoD sub-bullet cannot be satisfied as written: an unknown `--target` is not a
`pw-record` failure on this machine (it falls back to the default device, stderr empty),
so no error can be raised without adding an unauthorised device-enumeration dependency.
See `001-comment.md` → "The blocker" for the measurement and the recommended one-line
resolution. Everything else is implemented, tested, and verified.

## Notes

- Use `subprocess.Popen` with an explicit argument list, never a shell.
- Read stdout on a **dedicated thread**, because a PipeWire pipe that fills while nobody
  reads will stall the recorder. `stop()` must join that thread before returning.
- Do not import the engine, `gi`, or `numpy` here. `array('f')` from the stdlib is enough;
  the probe confirmed the transcription package accepts raw float32 bytes (ADR 0007).
- Keep `chunks()` lazy — it must not buffer the whole recording before yielding the first
  chunk, or the streaming path gets no partials until release.
- `pw-record` writes nothing to stdout until audio starts; a `stop()` immediately after
  `start()` is therefore a normal zero-sample case, not an error.

## Related tickets

- depends on: ../core-interfaces-and-fakes/ticket.md, ../config-loading/ticket.md
- blocks: ../dictation-pipeline-batch/ticket.md,
  ../../live-dictation-ux/streaming-session/ticket.md
- related ADRs: ../../../adr/0008-audio-capture-via-pw-record.md,
  ../../../adr/0005-stt-backend-cactus-whistle.md
