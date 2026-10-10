# whisper-cli subprocess runner

- **Priority:** P1
- **Status:** to do
- **Complexity:** S
- **Epic:** ../epic.md

## Current state / context

The research measured whisper.cpp through its **`whisper-cli`** binary, which is the integration
shape recommended first: no native library loaded into the service, crashes contained, and the whole
stack open (ADR 0006's escape hatch). The exact invocation that produced the measured transcripts is:

```
whisper-cli -m <model.bin> -f <clip.wav> -l <lang> -t <threads> -bs 1 -bo 1 -nt -np
```

`-nt` is **not** output-only — it changes the decode, and the 0.062 headline depends on it (see
[../../research/whisper-cpp-provider/004-addendum-nt-decode.md](../../research/whisper-cpp-provider/004-addendum-nt-decode.md)).
Greedy (`-bs 1 -bo 1`) is the measured choice; beam search buys nothing at 7× the cost.

There is no runner yet. The seam must be bounded and testable — this project has twice shipped a
green suite over a broken real subprocess seam.

## Definition of done

- [ ] A runner that invokes `whisper-cli` with the argv above (binary path, model path, WAV path,
      language, threads **configurable**; `-bs 1 -bo 1 -nt -np` **fixed**), returns the transcript, and
      runs under a **timeout**.
- [ ] The 16 kHz mono s16 `AudioBuffer` is written to a temp WAV for the CLI; the temp file is
      removed in all paths.
- [ ] Typed, specific errors (not bare `CalledProcessError`) for: binary missing, non-zero exit
      (with stderr), timeout, and empty/whitespace stdout.
- [ ] **A test drives a real child process** — the project's hard rule: a fake that models a simpler
      contract than the real binary is not a test of the seam. Assert the payload and argv reach a
      real child (a harmless stand-in under `tmp_path`); do **not** shell out.
- [ ] Tests falsify each error path (rename the binary → the typed error; force a non-zero exit; force
      a timeout). Watch each fail, revert.
- [ ] No network, no shell-injection surface (argument list, never `shell=True`).
- [ ] `uv run pytest` and `uv run mypy src/ges2t tests` green; report counts.
- [ ] `001-comment.md` records the argv, the `-nt` rationale, and the real-child evidence.

## Notes

- Model load happens on **every** call (subprocess) — measured at ~0.8 s for `medium q5_0`; that cost
  belongs to the overhead measurement ticket, not here.
- **No keystrokes, no clipboard, no windows, no microphone, no `sudo`, no service changes.**
- Keep it a pure, injectable seam: it takes a binary path and argv inputs and returns text, so the
  adapter and tests can both drive it.

## Related tickets

- depends on: —
- blocks: ../whisper-transcriber-adapter/ticket.md
- related ADRs: ../../../adr/0010-transcriber-interface-design.md, ../../../adr/0014-testing-and-quality-bar.md
