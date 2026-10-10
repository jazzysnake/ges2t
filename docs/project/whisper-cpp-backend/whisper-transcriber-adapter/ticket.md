# whisper.cpp Transcriber adapter

- **Priority:** P1
- **Status:** to do
- **Complexity:** M
- **Epic:** ../epic.md

## Current state / context

ges2t has one `Transcriber` implementation (the Whistle/Needle adapter) behind the ADR 0010 Protocol.
This ticket adds the second, over the runner (`../whisper-cli-runner/ticket.md`) and the model
catalogue (`../whisper-model-store/ticket.md`), following the Whistle adapter's shape exactly so the
pipeline, service, and selection rule need no changes.

Measured properties that shape the adapter:

- **`supports_streaming = False`** — the streaming discovery found whisper's partial-window output
  unstable (seam duplication, flush-dominated commits, cut-off/silence hallucination, 2.3–4.8× worse
  WER). Do not implement `stream()`.
- `medium q5_0` on Vulkan is ~1.7–3 s for 25 s of audio; **CPU is ~6× real time** and exists only as
  a fallback.
- The 16 kHz mono float32 `AudioBuffer` contract is whisper's own input contract — **no resampling**.

## Definition of done

- [ ] `WhisperCppTranscriber` implementing the `Transcriber` Protocol: `name = "whisper_cpp"`,
      `supports_streaming = False`, `ensure_model(progress)`, `load()`, `transcribe(buffer)`,
      `close()`.
- [ ] GPU/CPU selection: use Vulkan when the build and device are available, otherwise CPU, and say
      which was chosen (a log line); the choice is explicit, never a silent fallback that changes
      accuracy expectations without notice.
- [ ] `transcribe()` returns a `TranscriptionResult` with the text; engine timing fields are `None`
      or supplied by ges2t's own clock (whisper.cpp does not expose them), and this is documented —
      the same situation ADR 0011 records for the Whistle streaming path.
- [ ] Typed errors propagate from the runner; `load()` and `close()` are idempotent.
- [ ] **Tests**: a fake runner drives the Protocol contract; **at least one test drives the real
      `whisper-cli`** against a small cached model (skipped, not silently passed, when the binary or
      model is absent — ADR 0014). Falsify the adapter's wiring (e.g. drop the `-nt` flag path, or the
      model path) and watch a test fail, then revert.
- [ ] **No `src/ges2t/` file common with the model-store and runner tickets is edited here beyond the
      adapter's own module**; if a shared file must change, say so in the ticket's comment.
- [ ] `uv run pytest` and `uv run mypy src/ges2t tests` green; report counts.
- [ ] `001-comment.md` records the exact argv, the GPU/CPU rule, and the real-seam evidence.

## Notes

- Model stays loaded only for the process's lifetime **only in the `ctypes` shape**; the subprocess
  shape reloads per call (~0.8 s). Ship the **subprocess** shape first (smallest change, what was
  measured); the resident-model trade is measured in `../whisper-framework-overhead/ticket.md`.
- **Do not** add a number/spelling normaliser — out of scope project-wide.
- **No keystrokes, no clipboard, no windows, no microphone, no `sudo`, no service changes, no network.**

## Related tickets

- depends on: ../whisper-cli-runner/ticket.md, ../whisper-model-store/ticket.md
- blocks: ../whisper-backend-config-and-default/ticket.md, ../whisper-framework-overhead/ticket.md
- related ADRs: ../../../adr/0010-transcriber-interface-design.md, ../../../adr/0011-streaming-sliding-window.md, ../../../adr/0019-code-style-typing-and-interfaces.md
