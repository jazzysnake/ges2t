# Core interfaces and test fakes

- **Priority:** P0
- **Status:** to do
- **Complexity:** S
- **Epic:** ../epic.md

## Current state / context

`project-scaffold` gives us a package and a test runner but no shared vocabulary. Every
remaining ticket needs the same three seams — a transcriber, an audio source, and an
injector — and the whole test strategy depends on being able to substitute fakes for them so
tests run with **no microphone, no `/dev/uinput`, and no model** (ADR 0014).

This ticket defines that vocabulary once, and nothing else. Read ADR 0010 in full before
starting: the interface shape, the "data in, data out" rule, the capability-flag rule, and
the "adapters are the only modules allowed to import a backend library" rule all come from
there.

Relevant decisions: ADR 0010 (interfaces and data classes), ADR 0014 (fakes are
first-class artifacts).

## Definition of done

- [ ] `src/ges2t/interfaces.py` defines, with no import of any backend, GUI, or audio
      library:
      - `AudioBuffer` — frozen dataclass: `samples` (float32 mono sequence), `sample_rate:
        int`. Validates `sample_rate == 16000` is *not* its job; it just carries the value.
      - `TranscriptionSegment` — dataclass: `text: str`, `language: str | None`,
        `is_final: bool`, `pass_ms: float | None` (engine-reported where available).
      - `TranscriptionResult` — dataclass: `text: str`, `language: str | None`,
        `ttft_ms: float | None`, `decode_tps: float | None`.
      - `Transcriber` — `runtime_checkable` Protocol with: `name: str`;
        `supports_streaming: bool`; `ensure_model(progress=None) -> Path`;
        `load() -> None`; `transcribe(buffer: AudioBuffer) -> TranscriptionResult`;
        `stream(chunks: Iterable[AudioBuffer]) -> Iterator[TranscriptionSegment]`;
        `close() -> None`.
      - `AudioSource` — `runtime_checkable` Protocol with exactly: `start() -> None`,
        `chunks(chunk_seconds: float) -> Iterator[AudioBuffer]` (yields ~`chunk_seconds` of
        audio as it is captured; used by the streaming path), and `stop() -> AudioBuffer`
        (ends capture and returns **everything** captured, including already-yielded audio —
        the batch path uses this). Do not add a callback variant.
      - `Injector` — `runtime_checkable` Protocol: `inject(text: str) -> None`.
      - `StatusUI` — `runtime_checkable` Protocol: state transitions `idle()`,
        `recording()`, `transcribing()`, `error(message: str)`, and `partial(text: str)`
        (display-only live text, ADR 0011).
- [ ] `tests/fakes.py` provides `FakeAudioSource`, `FakeTranscriber` (constructible as
      streaming-capable **or** not), `FakeInjector`, and `FakeStatusUI`. Each records what it
      was asked to do in a list a test can assert on, and `FakeTranscriber` can be
      configured to raise on demand. These are shared fixtures for all later tickets; later
      tickets must import them rather than redefining them.
- [ ] `tests/test_interfaces.py` asserts:
      - each fake satisfies its Protocol via `isinstance(fake, Transcriber)` etc.;
      - `FakeTranscriber(supports_streaming=False)` reports `False` and one constructed
        streaming-capable reports `True`;
      - `import ges2t.interfaces` leaves `cactus_needle`, `gi`, and `numpy` **absent** from
        `sys.modules` (assert on the freshly-imported state, e.g. via a subprocess or
        `importlib` + `sys.modules` snapshot).
- [ ] `uv run pytest tests/test_interfaces.py` passes, and `uv run pytest` stays green.

## Notes

- Types only where they are load-bearing: this module must be importable in a bare Python
  environment, because `doctor` and the config layer will import it.
- Do not add methods you cannot name a caller for. In particular do not add a `warmup()`,
  a `language` property, or an async variant — ADR 0010 explains why inventing surface here
  is a liability, and a future async-only engine is an interface-breaking change that needs
  its own ADR.
- `stream()` returning an `Iterator` means the *caller* decides how many chunks to feed.
  Do not put the windowing logic here; that is
  `../../live-dictation-ux/streaming-window-engine/ticket.md`.
- Naming matters for later tickets: use exactly the names above so
  `config`, `model_store`, `pipeline`, `service`, `doctor`, and the streaming module can be
  written against this file without further negotiation.

## Related tickets

- depends on: ../project-scaffold/ticket.md
- blocks: ../config-loading/ticket.md, ../model-store-and-verification/ticket.md,
  ../audio-capture-pw-record/ticket.md, ../ydotool-injector/ticket.md,
  ../../live-dictation-ux/streaming-window-engine/ticket.md
- related ADRs: ../../../adr/0010-transcriber-interface-design.md,
  ../../../adr/0014-testing-and-quality-bar.md
