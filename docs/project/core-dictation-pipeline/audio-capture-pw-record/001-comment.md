# 001 — `pw-record` audio capture implementation notes

Ticket: `ticket.md` (`audio-capture-pw-record`) · Branch: `feat/audio-capture-pw-record`
Worktree: `.worktrees/audio-capture-pw-record`

## What was built

Exactly the three files the ticket allows:

- `src/ges2t/audio/__init__.py` — package marker (docstring only), so
  `ges2t.audio.pw_record` is importable.
- `src/ges2t/audio/pw_record.py` — `PwRecordAudioSource`, the concrete `AudioSource`.
- `tests/test_pw_record.py` — 26 tests, no microphone, no `pw-record` process.

No other module was created or modified. In particular there is no `pipeline.py`,
`streaming.py`, `config.py` change, or `interfaces.py` change; those belong to other
tickets. The module imports only `queue`, `subprocess`, `sys`, `threading`, `array`,
`collections.abc`, `typing`, and `ges2t.interfaces` — no `gi`, no `numpy`, no backend
(ADR 0010, ADR 0019).

## Verified `pw-record` invocation

`pw-record --help` on this machine (PipeWire/libpipewire **1.6.8**) documents exactly the
flags the ticket names:

```
      --target                          Set node target serial or name (default auto)
                                          0 means don't link
      --rate                            Sample rate (default 48000)
      --channels                        Number of channels (default 2)
      --format                          Sample format (default s16)
  -a, --raw                             RAW mode
```

and `--list-formats` lists `f32`. The verified invocation this module builds is

```
pw-record --rate 16000 --channels 1 --format f32 --raw --target <device> -
```

**`--raw` is required in addition to the three flags the ticket names.** Without it,
`pw-record -` wraps stdout in a container; a 1.5 s run began with a 24-byte AU header
(`64 6e 73 2e ...`, magic `dns.`) before the first sample. With `--raw`, a
`--sample-count 48000` run produced **exactly 192000 bytes = 48000 float32 samples =
3.000 s** at 16 kHz mono, with no header. `--target` is emitted only for a non-empty
device; the trailing `-` means "write to stdout", and this is exactly what the module's
argv test pins.

## Final public shapes

```python
PROGRAM: str = "pw-record"
SAMPLE_RATE: int = 16000
CHANNELS: int = 1
SAMPLE_FORMAT: str = "f32"

class PwRecordError(Exception): ...

class PwRecordProcess(Protocol):  # test seam; the subset of subprocess.Popen we use
    stdout: IO[bytes] | None
    stderr: IO[bytes] | None
    def wait(self, timeout: float | None = None) -> int: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...

SpawnPwRecord = Callable[[Sequence[str]], PwRecordProcess]

class PwRecordAudioSource:
    def __init__(self, device: str = "", *, spawn: SpawnPwRecord = _spawn_pw_record) -> None: ...
    def start(self) -> None: ...
    def chunks(self, chunk_seconds: float) -> Iterator[AudioBuffer]: ...
    def stop(self) -> AudioBuffer: ...
```

`start()`/`chunks()`/`stop()` match the `AudioSource` Protocol exactly and there are no
other public methods. `chunks()` is deliberately **not** a generator function: it
validates and raises eagerly, then returns `self._iter_chunks(...)`, so "before `start()`"
and "non-positive `chunk_seconds`" surface at the call site rather than on first `next()`
(the same pattern `FakeTranscriber.stream` uses).

## Manual real-microphone measurement (once, approved)

Real capture through `PwRecordAudioSource` on the default devices, all in memory; nothing
was written to disk and no audio file is in the tree.

3-second recording (`chunks(0.1)` consumed for 3.0 s wall, then `stop()`):

```
target samples          : 48000
captured samples        : 47418
captured seconds        : 2.9636
deviation from 3 x 16000: 1.21%     <- within the required 5 %
sample rate             : 16000
yielded by chunks()     : 46400
start->first chunk (ms) : 254.0
```

**Start-to-first-sample latency, measured three times** by wrapping the real stdout pipe
and timestamping the first non-empty read (the reader's first 4096-byte block, which is
1024 samples):

| trial | first stdout read | first 0.1 s chunk from `chunks()` |
|---|---|---|
| 1 | 117.9 ms | 182.2 ms |
| 2 | 99.7 ms | 163.8 ms |
| 3 | 103.8 ms | 167.8 ms |

So the capture path contributes roughly **100–120 ms** to first sample and **~165–180 ms**
to the first streamed chunk, driven by PipeWire's default 100 ms quantum
(`--latency` defaults to `100ms`). That is comfortably inside the project's sub-1-second
framework budget (ADR 0001). If it ever needs trimming, `--latency 20ms` is a real flag on
this build; it was deliberately **not** added, to keep the verified invocation minimal.

**Drain-on-stop does not truncate.** With the real recorder, consuming `chunks(0.1)` and
then stopping after the wall-clock target always returned a `stop()` total at least as
large as everything `chunks()` had already yielded:

```
wall=3.0s consumed=46400 stop_total=47418
wall=5.0s consumed=80000 stop_total=81210   (stop() returned *more* than 5 s of audio)
```

`pw-record` writes its full stderr diagnostic and exits within milliseconds of `SIGTERM`,
and the reader drains stdout to EOF before the buffer is finalised.

## Threading and drain design

`start()` spawns the process and starts one daemon reader thread. The reader loops on
`stdout.read(4096)` (1024 samples, 64 ms at 16 kHz) and, for every non-empty block, copies
it into a per-recording `bytearray` **and** puts it on a `queue.Queue`. That is what keeps a
PipeWire pipe from filling and stalling the recorder when nobody is consuming chunks.

`chunks(chunk_seconds)` returns a generator that blocks on that queue, accumulates bytes,
and yields an `AudioBuffer` as soon as one chunk (`round(chunk_seconds * 16000)` samples)
is available — it never waits for the recording to end, so the streaming path gets its
partials on time (ADR 0011). At EOF it yields the final short remainder too, so the sum of
the chunks equals `stop()`'s total when the iterator is fully consumed. Bytes are trimmed
to a whole number of float32 samples identically in both paths.

`stop()` sets a "stop requested" flag, sends `SIGTERM`, joins the reader (which reads
stdout to EOF and then stderr, and reaps the process with `wait()`), and only then builds
the returned buffer from the recording-wide bytearray — so it contains everything, not just
the not-yet-yielded tail. If `SIGTERM` were ever ignored it escalates to `SIGKILL` after a
5 s join, and raises if the reader is still blocked. A fresh `_Capture` object per
recording means the source is reusable across hotkey presses, and an older `chunks()`
generator can never read a newer recording's bytes.

Stderr is read **after stdout EOF, on the same thread**. That is safe for `pw-record`: its
stderr is tiny, so it cannot fill the pipe and block the process, and by stdout EOF the
process is exiting and the diagnostic is complete. A second thread (or `selectors`) was
avoided as unnecessary complexity.

## Failure modes and the real error text

`pw-record` exit status is **not** a success signal: a clean `SIGTERM` stop, an immediate
abort, and a genuine connection failure all returned **1** on this machine, while a
`--sample-count`-terminated recording that produced exactly 48000 samples also returned 1.
The module therefore keys the "failed before audio" decision off what is actually
reliable — *the recorder exited on its own before any sample, and either said something on
stderr or was not stopped by us* — and keeps the return code only as text in the message.

Verified against the real recorder:

- **No PipeWire session** (`XDG_RUNTIME_DIR=/tmp/nonexistent-pw`, surfaced through both
  `chunks()` and `stop()`):

  ```
  pw-record exited before capturing any audio (exit status 1); stderr: error:
  pw_context_connect() failed: Host is down. Check that a PipeWire session is running
  (`pw-cli info 0`) and that the system default input device exists and is not in use
  (`wpctl status`).
  ```

  The **stderr text is in the message**, as the ticket requires.

- **`pw-record` missing from `PATH`** — the spawn raises `FileNotFoundError`, which
  `start()` converts to a `PwRecordError` naming `pw-record`, the missing `pipewire`
  package, and `PATH`.

- **`start()` twice**, **`stop()` before `start()` / twice**, **`chunks()` before
  `start()`** — each raises `PwRecordError` with an actionable message.

- **Zero samples** (`stop()` immediately after `start()`) returns an empty
  `AudioBuffer(samples=array('f'), sample_rate=16000)` and does **not** raise, including
  when the recorder's exit status is the usual 1. Verified on the real microphone.

- **Read error** on stdout is raised, never swallowed, even if some samples were captured.

## The blocker: an unknown `--target` is not a `pw-record` failure

The ticket's DoD lists "an unknown `--target` device" as a failure mode that must raise.
**On this machine it is not one, and it cannot be made one through `pw-record`.** Verified:
a 1.5 s capture with `device="definitely-not-a-real-device-xyz"` produced

```
unknown target -> no error, samples: 22159 ( 1.385 s )
```

i.e. `pw-record` exits 0-when-signalled like normal, prints **nothing** on stderr, and
silently falls back to the default input node. The same happened with a nonexistent numeric
serial (`--target 999999`). `--target` sets the `node.target` property, and the session
manager's policy is to fall back to the default when the target does not resolve; there is
no `pw-record` flag to make an unresolved target fatal (the documented `0` value means
"don't link", which yields silence, not an error).

Every other listed failure mode *is* handled and tested; only this one cannot be detected
without an extra device-enumeration step (`pw-dump`/`wpctl` parsing), which no ADR
authorises and which would add a second external dependency to the capture path. No
improvising was done: the recorder's real failure path (no session, unusable device) raises
with stderr, and the fallback is documented here.

**Recommended resolution (one line, no code change):** accept the fallback as documented
PipeWire behaviour and drop that sub-bullet, because the bullet's stated purpose — never
return a short or empty buffer silently — is met: with an unresolved target the user gets a
*full* recording from the default mic, and the only symptom is that it came from the wrong
device. If the project instead wants a hard error, that is a new ticket for device
enumeration, not a change to this module.

Consequently `ticket.md` is set to **`blocked`** with this single item open; everything
else in the Definition of Done is implemented, tested, and verified below. Flip it to
`in review` once the orchestrator accepts the recommendation.

## Judgement calls (the orchestrator should know these)

1. **`--raw` added to the verified invocation.** Without it the first 24 bytes are an AU
   header and every sample is offset; the ticket's three flags alone are not a usable
   invocation. Recorded here and in the module docstring.
2. **One `PwRecordError` type for all branches.** The ticket names neither a type nor a
   hierarchy. A single exception keeps the caller's `except` simple; `ValueError` is used
   only for a non-positive `chunk_seconds`, which is a programmer error, not a capture
   failure.
3. **`__init__` gained one keyword-only parameter, `spawn: SpawnPwRecord`.** The required
   signature `__init__(self, device: str = "")` is unchanged for callers; `spawn` defaults
   to a function that runs the real `subprocess.Popen` with an explicit argument list. It
   exists so tests can inject a fake process (ADR 0014) with **no monkeypatching of
   `subprocess`** and no microphone. This is the only addition beyond the ticket's literal
   signature, and it adds no public method.
4. **The source is reusable.** `stop()` clears the per-recording state and `start()` may be
   called again, which the hotkey→speak→hotkey service needs; `start()` twice *during a
   recording* still raises.
5. **A `stop()` on a recording that already self-terminated with audio does not raise** —
   the captured audio is returned rather than discarded, because losing real speech to a
   device hiccup would be worse than a partial result.
6. **No `--latency` flag.** Default 100 ms measured fine against the budget; leaving it out
   keeps the argv minimal and matches what the ADR describes.
7. **After a failed `stop()` the per-recording state is cleared**, so the source must be
   `start()`ed again before reuse and a later `stop()` reports "before start()" rather than
   replaying the old failure. This keeps the state machine honest.

## What later tickets must know

- Construct `PwRecordAudioSource(device=config.audio.device)` — `""` means the system
  default and emits no `--target`.
- `chunks(1.0)` (the `[engine.whistle]` default) yields lazily; consume it until the user
  releases the hotkey, then call `stop()` once and feed the returned `AudioBuffer` to the
  batch path or use it as the final window. Everything already yielded is included in
  `stop()`, so nothing is double-counted or lost.
- `AudioBuffer.sample_rate` is always `16000`; samples are float32 in roughly `[-1, 1]`
  (PipeWire does not clamp, so a hot mic can exceed it — the measured first samples reached
  `-1.05`).
- `stop()` returning zero samples is normal and means "do not call the engine" (ADR 0005,
  ADR 0012); it is not an error.
- Catch `ges2t.audio.pw_record.PwRecordError` for capture failures; its message is already
  user-facing and names the device and PipeWire stderr.

## Verification (real output)

```
$ uv run pytest -q
........................................................................ [ 50%]
......................................................................   [100%]
142 passed in 0.45s

$ uv run pytest tests/test_pw_record.py -q
..........................                                               [100%]
26 passed in 0.06s

$ uv run mypy src/ges2t
Success: no issues found in 8 source files
```

`uv run mypy tests/test_pw_record.py` is also clean (`Success: no issues found in 1 source
file`), so the tests satisfy ADR 0019 too.

### Test coverage map

| Ticket bullet | Test(s) |
|---|---|
| argv has `--rate 16000 --channels 1 --format f32` (+ verified `--raw`) | `test_default_argv_is_the_verified_pw_record_invocation`, `test_required_flags_are_exactly_the_adr_contract` |
| configured device → `--target <device>` | `test_configured_device_becomes_target` |
| empty device → no `--target` | `test_empty_device_emits_no_target_argument` |
| explicit argument list, not a shell | `test_argv_is_an_explicit_list_not_a_shell_string` |
| chunk conversion sample counts | `test_chunk_conversion_yields_the_expected_sample_counts`, `test_final_short_remainder_is_still_yielded` |
| `chunks()` is lazy | `test_chunks_is_lazy_and_yields_before_the_recording_ends` |
| dedicated reader thread | `test_reader_thread_is_not_the_calling_thread` |
| drain-on-stop returns the full buffer incl. yielded audio | `test_drain_on_stop_returns_the_full_buffer_including_yielded_audio` |
| stop returns everything even when `chunks()` was abandoned | `test_stop_returns_everything_even_when_chunks_was_abandoned_early` |
| stop drains bytes written after chunking stopped | `test_stop_drains_what_the_recorder_wrote_after_chunking_stopped` |
| zero samples → empty buffer, no raise | `test_stop_immediately_after_start_returns_an_empty_buffer`, `test_sample_rate_and_format_are_the_engine_contract` |
| `pw-record` missing from `PATH` | `test_missing_binary_raises_an_actionable_error` |
| failure before audio includes stderr (via `stop()` and `chunks()`) | `test_startup_failure_before_audio_surfaces_stderr_through_stop`, `test_startup_failure_before_audio_surfaces_stderr_through_chunks` |
| failure names the configured device | `test_failure_message_names_the_configured_device` |
| stdout read error is raised | `test_stdout_read_error_is_raised_not_swallowed` |
| `start()` twice | `test_start_twice_raises` |
| `stop()` before `start()` / twice | `test_stop_before_start_raises`, `test_stop_twice_raises` |
| `chunks()` before `start()` | `test_chunks_before_start_raises` |
| non-positive `chunk_seconds` | `test_non_positive_chunk_seconds_is_rejected` |
| satisfies `AudioSource` | `test_source_satisfies_the_audio_source_protocol` |
| reusable across recordings | `test_source_is_reusable_across_recordings` |
| unknown `--target` | **not testable** — see "The blocker" above |
