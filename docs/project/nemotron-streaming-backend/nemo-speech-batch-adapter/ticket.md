# Nemotron batch adapter (selectable engine, batch-only for now)

- **Priority:** P1
- **Status:** done
- **Complexity:** M
- **Epic:** ../epic.md

## Current state / context

Ticket 57 made the NeMo-Speech.cpp engine and the Nemotron GGUF **acquirable** ([57](../nemo-speech-engine-and-model-acquisition/ticket.md)).
This ticket makes `nemo_speech` a **selectable engine with a working batch path**, so the backend is
usable (and testable) before the streaming mechanism lands in ticket 59. It mirrors the whisper.cpp
adapter exactly: a `Transcriber` behind a `ctypes` **library Protocol**, so the seam is testable with a
fake library while the real implementation loads `libnemo_speech_asr_c`.

Relevant code to mirror, not reinvent: `src/ges2t/transcribers/whisper_cpp.py` (the `ctypes` Protocol
seam, `whisper_cpp.py:223`), `src/ges2t/transcribers/whisper_cpp_config.py` (an engine config block,
ADR 0009), `src/ges2t/config.py` (`KNOWN_ENGINES` at `:80`, `BATCH_ONLY_ENGINES` at `:100`, the
selection gate at `:399`), and `src/ges2t/interfaces.py` (the `Transcriber` Protocol, ADR 0010).

Read [the design report §7.1 and §3.3](../../research/nemo-pure-streaming-integration/REPORT.md) and
[ADR 0022](../../../adr/0022-nemotron-streaming-pure-streaming-backend.md) first.

**C-ABI symbols** (from the installed header — confirm every one against the real header in the
acquired engine dir before binding it): `nemo_speech_asr_create`, `nemo_speech_asr_destroy`,
`nemo_speech_asr_recognize_f32` (offline/batch), `nemo_speech_asr_last_error`.

## Definition of done

- [x] `src/ges2t/config.py` adds `NEMO_SPEECH_ENGINE` (`identifiers.py`) to **`KNOWN_ENGINES`**
      (`config.py:80`) so `transcriber.engine = "nemo_speech"` is a valid config (the gate at `:399`).
      It is **not** added to `BATCH_ONLY_ENGINES` (`:100`) — the engine streams, so an **omitted**
      `streaming.enabled` must resolve to `True` (ticket 50's engine-aware default), and an explicit
      `true` must **not** be rejected.
- [x] `src/ges2t/transcribers/nemo_speech_config.py` (new) validates `[engine.nemo_speech]`:
      `device` (`"auto"` | `"vulkan"` | `"cpu"`, default `"auto"`), `chunk_seconds` (default **0.16**),
      and a model override. A `window_seconds` key produces the **existing unknown-key warning** (it
      has no effect for this engine — ADR 0022 requires it say so rather than silently accept it).
- [x] `src/ges2t/transcribers/nemo_speech.py` (new) defines a narrow **`NemoSpeechLibrary` Protocol**
      plus the `ctypes` implementation, and **`NemoSpeechTranscriber`** implementing the `Transcriber`
      Protocol with: `ensure_model` (→ ticket 57's store), `load` (recognizer resident from service
      start), `transcribe` (offline `recognize_f32`), `close`.
- [x] The adapter is the **only** module allowed to touch the native library (ADR 0010). It reports
      `name = NEMO_SPEECH_ENGINE` and, **for this ticket**, `supports_streaming = False` (ticket 59
      flips it).
- [x] Selecting `nemo_speech` with an acquired model and a built engine **starts and transcribes** a
      batch utterance; startup validation rejects the engine when the model is not acquired or the
      library is not present (mirroring the whisper.cpp rules).
- [x] Device selection is **never silent** (ADR 0020): the adapter reports the device it actually used.
- [x] `tests/test_nemo_speech.py` (new) is **hermetic** — it drives a fake `NemoSpeechLibrary` (the
      pattern in `tests/test_whisper_cpp.py`), failing if the fake models a simpler contract than the
      real library.
- [x] `uv run pytest` green, `uv run mypy src/ges2t tests` 0 errors; docs link check 0; README row updated.
- [x] **No `numpy`, `torch`, or `onnxruntime`** enters the project or any dependency.

## Notes

- **Batch-first is deliberate.** Shipping a correct batch adapter before the streaming driver keeps
  each ticket small and gives ticket 59 a tested foundation.
- The config block is engine-specific (ADR 0009); do not generalise shared config.
- The `ctypes` binding must not block on import and must surface `nemo_speech_asr_last_error` in
  failures.
- Do **not** add the builder to the composition root here — that is ticket 60.

## Related tickets

- depends on: [../nemo-speech-engine-and-model-acquisition/ticket.md](../nemo-speech-engine-and-model-acquisition/ticket.md) (57)
- blocks: [../nemo-speech-pure-streaming/ticket.md](../nemo-speech-pure-streaming/ticket.md) (59) ·
  [../nemo-speech-doctor/ticket.md](../nemo-speech-doctor/ticket.md) (61)
- related ADRs: [ADR 0009](../../../adr/0009-configuration-toml.md) ·
  [ADR 0010](../../../adr/0010-transcriber-interface-design.md) ·
  [ADR 0019](../../../adr/0019-code-style-typing-and-interfaces.md) ·
  [ADR 0020](../../../adr/0020-layout-aware-keycode-mapping.md) ·
  [ADR 0021](../../../adr/0021-whisper-cpp-second-backend.md) ·
  [ADR 0022](../../../adr/0022-nemotron-streaming-pure-streaming-backend.md)
