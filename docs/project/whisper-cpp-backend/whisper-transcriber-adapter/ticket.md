# whisper.cpp Transcriber adapter (in-process)

- **Priority:** P1
- **Status:** in review
- **Complexity:** M
- **Epic:** ../epic.md

## Current state / context

ges2t has one `Transcriber` implementation (Whistle/Needle) behind the ADR 0010 Protocol. This ticket
adds the second: **`ctypes` over `libwhisper.so`**, the same shape the Whistle adapter already uses for
`libneedle3.so`. The model is loaded **once** and kept resident — the user's explicit requirement, and
the reason the subprocess shape was rejected.

Measured properties that shape the adapter ([ADR 0021](../../../adr/0021-whisper-cpp-second-backend.md)):

- **`supports_streaming = False`**; do not implement `stream()`.
- `medium-q5_0` on Vulkan is ~1.7–3 s for 25 s of audio; CPU is ~6× real time (fallback only, and the
  CPU-only default model is `small-q5_0`).
- The 16 kHz mono float32 `AudioBuffer` contract **is** whisper's input contract — **no resampling**.
- The decode is **pinned**: `-nt` (no timestamps) and greedy. `-nt` changes the decode, and the
  measured accuracy depends on it.

## Definition of done

- [x] `WhisperCppTranscriber` implementing the `Transcriber` Protocol: `name = "whisper_cpp"`,
      `supports_streaming = False`, `ensure_model(progress)`, `load()`, `transcribe(buffer)`,
      `close()`.
- [x] **`load()` loads the model once**; the service calls it **at startup** (wired in
      `pipeline._TRANSCRIBER_BUILDERS` and exercised by `_startup`), and the model stays resident for
      the service lifetime. `load()`/`close()` are idempotent; a load failure raises a typed
      `WhisperCppLoadError` the service treats as fatal with a clear message.
- [x] Decode parameters pinned in the ctypes params: `no_timestamps` on, greedy
      (`best_of = beam_size = 1`), language and thread count configurable.
- [x] GPU/CPU device selection: use Vulkan when the built engine advertises a device, otherwise CPU;
      the choice is recorded (`Transcriber.device`) and logged, never a silent switch.
- [x] Engine timings whisper.cpp does not expose are `None` or supplied by ges2t's own clock, and this
      is documented — the same situation ADR 0011 records for the Whistle streaming path.
- [x] **Framework overhead measured and reported as a number** with its method: ~8.5 ms non-model for a
      25 s dictation (ADR 0001's 1 s budget), and the resident-model benefit (~41 ms tiny / ~113 ms
      medium one-time load, never paid per utterance) is stated — see `001-comment.md`.
- [x] Tests: a **fake library** drives the Protocol contract; **one test drives the real
      `libwhisper.so`** against a small cached model (skipped, not silently passed, when the library or
      model is absent — ADR 0014). Falsified the wiring (dropped the `-nt` parameter path and the model
      path) and watched the tests fail, then reverted.
- [x] `uv run pytest` and `uv run mypy src/ges2t tests` green; report counts.
- [x] `001-comment.md` records the exact ctypes calls, the device rule, the pinned decode, and the
      overhead numbers.

## Notes

- Ship **only** the in-process shape; no subprocess path. The research's subprocess numbers are
  evidence about whisper.cpp, not a design to copy.
- ~0.8–1 GB resident for `medium-q5_0`; on Vulkan the weights live in GPU-visible memory. No idle
  unload (the user accepted resident memory; a smaller model is the lever if it becomes a problem).
- **Do not** add a number/spelling normaliser.
- **No keystrokes, no clipboard, no windows, no microphone, no `sudo`, no service changes, no network.**

## Related tickets

- depends on: ../whisper-engine-build/ticket.md, ../whisper-model-store/ticket.md
- blocks: ../whisper-backend-config-and-validation/ticket.md, ../whisper-doctor/ticket.md
- related ADRs: ../../../adr/0010-transcriber-interface-design.md, ../../../adr/0011-streaming-sliding-window.md, ../../../adr/0019-code-style-typing-and-interfaces.md, ../../../adr/0021-whisper-cpp-second-backend.md
