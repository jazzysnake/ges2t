# Audio capture via `pw-record`, behind an `AudioSource` interface

- **Status:** accepted
- **Date:** 2026-10-09

## Context

The engine needs **16 kHz, mono, float32** (ADR 0005). Two capture strategies were offered
with their trade-offs, and the user asked for the pros and cons before deciding, then chose
the recommendation.

**`pw-record` subprocess**

- Pro: already installed (`/usr/bin/pw-record`); PipeWire is active on this machine.
- Pro: **zero Python dependencies**, which matters because the project targets Python 3.14
  where third-party wheels are still thin (ADR 0001).
- Pro: `pw-record --rate 16000 --channels 1 --format f32 -` makes **PipeWire itself** do the
  resampling, so we receive exactly the engine's native contract with no DSP on our side.
- Con: one subprocess per recording; PCM must be read off stdout.
- Con: PipeWire-specific (acceptable: Wayland-only target, ADR 0002).

**`sounddevice` (PortAudio via `cffi`)**

- Pro: in-process callback streaming; convenient for a level meter or a future engine with
  true incremental decoding.
- Con: adds a `cffi` + PortAudio binary dependency; wheel availability on Python 3.14 is a
  moving target (verified resolvable today at `sounddevice==0.5.6`, but it is a real supply
  risk).
- Con: needs its own resampling to 16 kHz, and PortAudio must route through
  pipewire-alsa/pulse.

## Decision

Use **`pw-record` as a subprocess**, wrapped in a single `PwRecordAudioSource`, behind an
`AudioSource` interface.

- The interface yields an `AudioBuffer` of **16 kHz mono float32** plus its sample rate, so
  downstream code never resamples and never guesses the format.
- Recording start/stop is explicit and mapped onto the service's state machine (ADR 0004),
  and stopping must **not** discard already-captured audio: a `SIGTERM`/stdin-close shutdown
  can truncate a PipeWire stream, so the stop path must drain stdout to EOF and only then
  treat the buffer as complete.
- Device selection is a **config value** (ADR 0009) passed as the PipeWire target; empty
  means the system default. The user explicitly required configurable input device.
- Process spawn and first-buffer latency are part of the framework budget (ADR 0001) and
  must be measured and reported, not assumed.
- `sounddevice` is not installed, not imported, and not a fallback. If richer in-process
  audio is ever needed, it becomes a second `AudioSource`.

## Consequences

- The capture path is a subprocess handle plus a reader thread, so tests use a **fake
  `AudioSource`** (or a synthetic WAV) and never touch a real microphone (ADR 0014).
- Because PipeWire resamples for us, `numpy` is only needed to hold float32 samples and
  compute levels, not to resample. That keeps a heavy DSP dependency out of the critical
  path, though `numpy` is still expected as a transitive dependency of the audio/model
  stack.
- Failure modes to handle explicitly: no PipeWire session, `pw-record` exiting immediately,
  and a recording stopped before any audio arrives (zero-length buffer → do not call the
  engine).
- **Amended 2026-10-09 (measured during ticket 6):** "unknown device target" was originally on
  that list and has been **removed**. `--target` is only a `node.target` *hint* to PipeWire: an
  unresolved target makes `pw-record` silently fall back to the default node, exit normally,
  and print nothing on stderr, and **no `pw-record` flag makes it fatal**. Detecting it needs
  device enumeration (`pw-dump`/`wpctl`) and is therefore new scope that this ADR does not
  authorise. Accepted behaviour is PipeWire's documented fallback: a full recording from the
  default device, which still honours the intent of the rule (never a silent short or empty
  buffer). The user-facing safety net moves to `ges2t doctor`, which validates the *configured*
  device against the session's node list (ADR 0016).
- We accept a hard dependency on PipeWire, which is consistent with a Wayland-only,
  KDE-on-Arch target and would need revisiting only if the project ever targets non-PipeWire
  systems (out of scope, ADR 0000).
