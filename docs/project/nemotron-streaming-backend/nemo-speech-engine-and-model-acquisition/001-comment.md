# 57 — implementation comment

- **Branch:** `feat/nemo-speech-engine-and-model-acquisition`
- **Worktree:** `.worktrees/nemo-speech-engine-and-model-acquisition`
- **Status:** in progress (started 2026-10-14)

## Baseline (measured, before any change)

`uv run pytest -q` in the fresh worktree at `01c624e`:

```
1230 passed in 6.72s
```

**The orchestrator's stated baseline of `923 passed` is stale** — it does not match `main` at the
point this branch was cut. Recorded here rather than silently accepted; the number that must not
regress is **1230 passed**, and `uv run mypy src/ges2t tests` must stay at **0 errors**.

## Pins used (given by the ticket / ticket 55 — not re-derived)

| thing | value |
|---|---|
| engine archive URL | `https://github.com/NVIDIA/NeMo-Speech.cpp/releases/download/v0.2.0/nemo-speech-0.2.0-linux-x86_64-vulkan.tar.gz` |
| engine archive sha256 | `c8d0ed5b9ac5fb6d5c460015ba38ea217415c44edc1e6d49120646781009aee6` |
| engine archive size | `16,618,142` B |
| model URL | `https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b/resolve/1c8deaecc64b91f034d73e08dd8b64625eb3395d/nemotron-3.5-asr-streaming-0.6b.q8_0.gguf` |
| model sha256 | `a5c435f294eea8f88ce68dd27b8c3bfea7f777cb2fbba04fcd30eaa555f429ae` |
| model size | `741,548,352` B |
| model revision | `1c8deaecc64b91f034d73e08dd8b64625eb3395d` |

The archive URL and the revision-qualified model URL are the exact strings ticket 55's report records
(`docs/project/research/nemotron-3-5-asr-streaming-measurement/REPORT.md` §2), so the pins here are the
same artifacts that were measured, byte for byte.

## Extracted layout (read from ticket 55's scratch tree, read-only)

The extracted archive root is `nemo-speech-0.2.0-linux-x86_64-vulkan/` containing
`bin/nemo-speech`, `lib/libnemo_speech_asr_c.so` → `.so.1`, the `libggml*`/`libllama` family,
`include/nemo_speech/*.h`, and `share/{doc,licenses}`. The entry library the C-ABI adapter (ticket 58)
loads is `lib/libnemo_speech_asr_c.so`; the binary `bin/nemo-speech` is what `doctor` runs. This is the
"device detection" surface too: the archive is the **Vulkan** build with a CPU fallback, so acquisition
detects the Vulkan **loader** and reports Vulkan-vs-CPU; the engine enumerates GPUs itself at runtime.

## Plan

1. `identifiers.py` — add `NEMO_SPEECH_ENGINE = "nemo_speech"` (one spelling; the setup token and the
   engine value are the same string, so no second constant and no alias).
2. `model_store.py` — the GGUF pin (`ArtifactPin` + revision) and
   `nemo_model_path()`/`ensure_nemo_model()`, reusing `_ensure`/`verify`; expose the existing download
   implementation as a public seam so `nemo_engine.py` reuses it rather than adding a second urllib loop
   (the "no second networked path" guard test scans for exactly this).
3. `nemo_engine.py` (new) — archive pin, extraction into the data dir, `engine_dir()`,
   `verify_engine()`, device detection. **No build**, no `sudo`, no install.
4. `setup_command.py` + `cli.py` — the `nemo_speech` setup backend: acquire the archive, then the GGUF,
   verify both, print the config line. Hop-by-hop hash/size failure leaves nothing half-written.
5. Tests — hermetic (temp files + fake fetchers, never network): wrong-hash archive and wrong-size
   model both fail; the setup path with fakes; the real seam (a real `.tar.gz` round-trip through
   `tarfile`, no network).

Working log follows, appended as each step lands.
