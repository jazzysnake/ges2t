# Nemotron engine and model acquisition

- **Priority:** P1
- **Status:** done
- **Complexity:** M
- **Epic:** ../epic.md

## Current state / context

Epic 6 adds **`nemo_speech`**, an opt-in `Transcriber` on the **`NVIDIA/NeMo-Speech.cpp`** engine
running **`nvidia/nemotron-3.5-asr-streaming-0.6b`**. Nothing about that engine or model exists in the
project yet: there is no identifier, no engine archive handling, no model pin, and no `setup` path.
This ticket establishes **acquisition only** — the pieces every later ticket builds on.

The template is the whisper.cpp precedent, which this mirrors one-for-one:
`src/ges2t/whisper_engine.py` (engine archive handling), `src/ges2t/model_store.py` (pinned model
acquisition, ADR 0017), `src/ges2t/identifiers.py` (one definition per identifier — Epic 4's rule), and
`src/ges2t/setup_command.py` + `src/ges2t/cli.py` (the only networked path, ADR 0018).

**Facts already established (ticket 55 / the design report §7.1) — do not re-derive:**

| thing | value |
|---|---|
| engine release | `NVIDIA/NeMo-Speech.cpp` **v0.2.0**, prebuilt, Apache-2.0 |
| engine archive | `nemo-speech-0.2.0-linux-x86_64-vulkan.tar.gz` — **16,618,142 B**, sha256 **`c8d0ed5b9ac5fb6d5c460015ba38ea217415c44edc1e6d49120646781009aee6`** |
| model | `nemotron-3.5-asr-streaming-0.6b.q8_0.gguf` — **741,548,352 B**, sha256 **`a5c435f294eea8f88ce68dd27b8c3bfea7f777cb2fbba04fcd30eaa555f429ae`**, revision **`1c8deaecc64b91f034d73e08dd8b64625eb3395d`** |
| licence | engine Apache-2.0 (ggml MIT); model **OpenMDW-1.1** |
| build | **none** — the archive is prebuilt; `setup` must not run `cmake` or `sudo` |

Read [the design report §7.1](../../research/nemo-pure-streaming-integration/REPORT.md) and
[ADR 0022](../../../adr/0022-nemotron-streaming-pure-streaming-backend.md) before starting.

## Definition of done

- [x] `src/ges2t/identifiers.py` defines **`NEMO_SPEECH_ENGINE = "nemo_speech"`** — one definition,
      used by comparison, never a bare literal (Epic 4 rule; the guard test must stay green). The
      setup token *is* this value: no second constant, no alias.
- [x] `src/ges2t/nemo_engine.py` (new) mirrors `whisper_engine.py`: the archive pin (URL, sha256,
      size), extraction into the ges2t data dir, `engine_dir()`, `verify_engine()`, and device
      detection. **It never builds anything and never installs a package.**
- [x] `src/ges2t/model_store.py` gains the GGUF pin (sha256 + size + revision) and
      `nemo_model_path()` / `ensure_nemo_model()`, reusing the existing `_ensure`/`verify` helpers.
- [x] `ges2t setup nemo_speech` (via `setup_command.py` + `cli.py`) acquires the engine archive and
      the GGUF, verifies both hashes, and prints the config line the user must add. A hash or size
      mismatch is a hard failure that leaves nothing half-written.
- [x] `setup` **requires and never installs** its prerequisites and never runs `sudo`; the prebuilt
      archive path needs **no** build tooling (`ninja`/`sentencepiece` are absent on this machine and
      are never named — asserted by a static AST guard test).
- [x] **No runtime network** (ADR 0018): acquisition happens only here; `ensure_*` is the only
      networked call. `nemo_engine.py` imports no HTTP client (the "network stays in `model_store`"
      guard test stays green).
- [x] Hermetic tests for the pins/verification (a wrong-hash archive and a wrong-size model both
      fail); `uv run pytest` green and `uv run mypy src/ges2t tests` 0 errors (ADR 0019).
- [x] Docs link check 0 broken references; `docs/project/README.md` row for this ticket updated.

Evidence, the falsification table for every new guard, and the one user-owned gate that was **not**
run (the real ~758 MB download — the network is not mine to use) are in [`001-comment.md`](001-comment.md).

## Notes

- **Version pinning is the point.** Every artifact is pinned by content hash *and* byte size, exactly
  as ADR 0017 requires; a moving `latest` URL is never used.
- The model revision pin (`1c8deae…`) is the revision the engine's own index pins; ticket 55 verified
  its sha256.
- Keep the network surface in `setup_command.py`/`model_store.py` only — do not add network calls
  anywhere else.
- Scratch/experiment files from earlier discovery are **not** part of this ticket; the repo gains no
  binary, weight, or audio.
- **Not run here:** the real end-to-end download (`ges2t setup nemo_speech`) — the network is
  user-owned and was not confirmed. The pins are asserted as data against ticket 55's report and the
  acquisition/extraction logic is covered hermetically (fake fetcher + a real gzip tar); the exact
  commands for the gate are in `001-comment.md`.

## Related tickets

- depends on: [../nemo-pure-streaming-integration/ticket.md](../nemo-pure-streaming-integration/ticket.md) (ticket 56 — done)
- blocks: [../nemo-speech-batch-adapter/ticket.md](../nemo-speech-batch-adapter/ticket.md) (58) ·
  [../nemo-speech-pipeline-wiring/ticket.md](../nemo-speech-pipeline-wiring/ticket.md) (60) ·
  [../nemo-speech-doctor/ticket.md](../nemo-speech-doctor/ticket.md) (61)
- related ADRs: [ADR 0017](../../../adr/0017-model-acquisition-via-setup.md) ·
  [ADR 0018](../../../adr/0018-no-runtime-network.md) ·
  [ADR 0021](../../../adr/0021-whisper-cpp-second-backend.md) ·
  [ADR 0022](../../../adr/0022-nemotron-streaming-pure-streaming-backend.md)
