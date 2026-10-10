# whisper.cpp model catalogue and pinned acquisition

- **Priority:** P1
- **Status:** to do
- **Complexity:** S
- **Epic:** ../epic.md

## Current state / context

`model_store` acquires exactly one artefact — the Whistle `.cact` — pinned by sha256 and size, into
`~/.local/share/ges2t/models/whistle/2.0.0/`, with no runtime network outside this path (ADR 0018).
whisper.cpp models are a **family** (size × quant) fetched from the canonical
`ggerganov/whisper.cpp` Hugging Face repo as `ggml-<size>-<quant>.bin`.

Facts the research established — **do not re-derive**:

- **Upstream publishes `q8_0` for every size**, but **`q5_0` only for `medium` and `large-v3-turbo`**.
  `tiny`/`base`/`small` publish `q5_1`/`q8_0` only. **Do not ship a quantiser.**
- WER is quantisation-invariant between `q5_0`/`q8_0`; **never `f16`**.
- Verified pins (size + sha256) are in
  [../../research/whisper-cpp-provider/REPORT.md](../../research/whisper-cpp-provider/REPORT.md) §11.4;
  re-verify each against the live host before pinning.

## Definition of done

- [ ] A **catalogue** of downloadable models — each entry: name (`<size>-<quant>`), file name, byte
      size, sha256, canonical URL — covering only what upstream actually publishes.
- [ ] A **name parser**: `medium-q5_0` → (size `medium`, quant `q5_0`); unknown size or quant → a typed
      error naming the valid set.
- [ ] Download + **sha256 and size verification** into
      `~/.local/share/ges2t/models/whisper-cpp/<name>/ggml-<name>.bin`; **idempotent** (a second run
      re-verifies and does nothing else); typed errors for a bad hash, a truncated file, and a network
      failure.
- [ ] **No runtime network outside this acquisition path** (ADR 0018), checked the same way the
      existing store is.
- [ ] `tests/` cover the catalogue shape, the name parser (good and rejected), a successful verify, and
      a **failing** verify (flip one byte → typed error). **Falsify each**: break the check, watch the
      test fail, revert.
- [ ] `uv run pytest` and `uv run mypy src/ges2t tests` green; report before/after counts; no test
      deleted, skipped or weakened.
- [ ] `001-comment.md` records the catalogue, the pins with provenance, and the upstream publishing
      facts.

## Notes

- Reuse the existing store's shape; it is already the pinned-artefact pattern. **Do not** add a second
  divergent one.
- The default-model rule (GPU → `medium-q5_0`, CPU → `small-q5_0`) is a **selection** concern and lives
  with config, not here; this ticket only knows the catalogue and names.
- **No keystrokes, no clipboard, no windows, no microphone, no `sudo`, no service changes.** Network
  only for the model downloads this ticket owns, and never in tests (fake the downloader).

## Related tickets

- blocks: ../whisper-transcriber-adapter/ticket.md, ../setup-backend-and-model-arguments/ticket.md,
  ../whisper-doctor/ticket.md
- related ADRs: ../../../adr/0017-model-acquisition-via-setup.md, ../../../adr/0018-no-runtime-network.md
