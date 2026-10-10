# whisper.cpp model catalogue and pinned acquisition

- **Priority:** P1
- **Status:** to do
- **Complexity:** S
- **Epic:** ../epic.md

## Current state / context

`src/ges2t/model_store.py` acquires exactly one artefact — the Whistle `.cact` — pinned by sha256 and
size, into `~/.local/share/ges2t/models/whistle/2.0.0/whistle.cact`, with no runtime network outside
this path (ADR 0018). whisper.cpp models are a **family** (sizes × quants) fetched from the canonical
`ggerganov/whisper.cpp` Hugging Face repo as `ggml-<name>.bin`.

Facts the research established (do not re-derive):

- **Upstream publishes `q8_0` for every size**, but **`q5_0` only for `medium` and `large-v3-turbo`**.
  `tiny`/`base`/`small` publish `q5_1`/`q8_0` only. **Do not ship a quantiser**; support only what is
  downloadable.
- WER is quantisation-invariant between `q5_0`/`q8_0`; **never `f16`** (slower, bigger, no gain).
- The default model is **`medium q5_0`** (canonical, sha256 known).

Pins measured and verified on this machine are listed in
[../../research/whisper-cpp-provider/REPORT.md](../../research/whisper-cpp-provider/REPORT.md) §11.4;
re-verify each against the live host before pinning.

## Definition of done

- [ ] A **catalogue** of downloadable whisper.cpp models (size × quant as published), each entry
      carrying its file name, byte size, sha256, and the canonical download URL.
- [ ] Download + **sha256 and size verification**, into
      `~/.local/share/ges2t/models/whisper-cpp/<name>/ggml-<name>.bin`; idempotent (a second run
      re-verifies and does nothing else); typed errors for a bad hash, a truncated file, and a
      network failure.
- [ ] The default entry is `medium q5_0`; the catalogue **does not** offer a `q5_0` for
      `tiny`/`base`/`small`.
- [ ] **No runtime network outside this acquisition path** (ADR 0018), checked the same way the
      existing store is.
- [ ] `tests/` cover the catalogue shape, a successful verify, and a **failing** verify (flip one
      byte → typed error). **Falsify each**: break the check, watch the test fail, revert.
- [ ] `uv run pytest` and `uv run mypy src/ges2t tests` green (ADR 0019 covers `tests/`); report
      before/after counts; no test deleted, skipped or weakened.
- [ ] `001-comment.md` records the catalogue, the pins with their provenance, and the upstream
      publishing facts.

## Notes

- Reuse the existing store's shape rather than inventing a second one; the pinned-artefact pattern
  already exists.
- **No keystrokes, no clipboard, no windows, no microphone, no `sudo`, no service changes.** Network
  is allowed **only** for the model downloads this ticket owns, and must not be exercised in tests
  (use a fake downloader).
- Keep it small: this ticket is acquisition and verification only — no adapter, no config wiring.

## Related tickets

- blocks: ../whisper-transcriber-adapter/ticket.md
- related ADRs: ../../../adr/0017-model-acquisition-via-setup.md, ../../../adr/0018-no-runtime-network.md
