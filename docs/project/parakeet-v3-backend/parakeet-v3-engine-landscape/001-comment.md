# 001-comment — ticket 53 (Parakeet v3 engine landscape): gate decision for ticket 54

**Verdict: PROCEED — gate ticket 54 open. Do not close it obsolete.** A candidate engine exists,
supports Vulkan on this PC, and adds no forbidden dependency. But the epic must carry one escalated
nuance the user will care about (see "Escalate").

Report: [`../../research/parakeet-v3-engine-landscape/REPORT.md`](../../research/parakeet-v3-engine-landscape/REPORT.md)

## The two or three facts that decide it

1. **An engine that runs v3 on Vulkan this PC exists, officially.** `NVIDIA/NeMo-Speech.cpp`
   (Apache-2.0, C++/ggml) executes `nvidia/parakeet-tdt-0.6b-v3` from the **official in-repo q8_0
   GGUF** (sha256 `e3880d0a…`, 713,975,456 B) and publishes a **prebuilt
   `linux-x86_64-vulkan` binary** (16,618,142 B, sha256 `c8d0ed5b…`). No Python, no `torch`, **no
   `numpy`**. `vulkaninfo` confirms RADV is live (`AMD Radeon 780M / RADV PHOENIX`, Mesa 26.2.3).
2. **Vulkan is architectural, and only ggml has it.** The tensor-runner engines (ONNX Runtime,
   sherpa-onnx, torch/NeMo/Transformers) have **no Vulkan path** — ONNX Runtime's EP list contains no
   Vulkan EP, and an ONNX import would also mean trusting a **third-party re-export** (NVIDIA ships no
   ONNX for v3). So the choice collapses to ggml engines: NeMo-Speech.cpp (recommended), or the
   community `mudler/parakeet.cpp` (MIT) / `CrispASR` (MIT) as fallbacks. **No `sudo`/package is
   needed** if ticket 54 uses the prebuilt archive.
3. **Parakeet v3 is batch-only in every engine.** It is an offline transducer; NeMo-Speech.cpp's
   recognizer throws *"this transducer encoder is offline-only and cannot serve StreamingRecognize"*,
   and its CLI rejects `--stream`/`--live` for it. **So v3 alone does not fix the user's stated pain**
   (no incremental text). The fix is a *sibling* model on the same engine:
   `nvidia/nemotron-3.5-asr-streaming-0.6b` (cache-aware streaming, OpenMDW-1.1, 40 locales incl.
   `hu-HU`).

## Escalate to the user before ticket 54 commits to a model

- **The motivation is incremental text, and v3 cannot deliver it.** A third backend that is still
  batch-only may be worth it for Hungarian / multilingual *accuracy*, but it is **not** the UX fix.
  The user should choose:
  - **(A) Parakeet v3, batch-only** — best accuracy, Hungarian FLEURS **15.72**; reproduces
    whisper.cpp's batch behaviour; CC-BY-4.0.
  - **(B) Nemotron 3.5 ASR Streaming, true incremental** — cache-aware streaming, 80–160–320–560–1120 ms
    chunks; Hungarian is only **broad-coverage** (FLEURS ≈ 29–33, ~2× v3's error rate); OpenMDW-1.1.
  - **(C) both** — v3 for accuracy, the streaming model for live typing; two model pins, same engine.
- **Licence note:** v3 weights are **CC-BY-4.0** (attribution required in the README); the streaming
  sibling is **OpenMDW-1.1**. The engine is Apache-2.0 (+ ggml MIT).
- **No `sudo` is required** for the recommended path (prebuilt Vulkan binary). Only a *from-source*
  build would need `sudo pacman -S sentencepiece` (cmake/ninja/glslc/spirv-headers already present).
- **Residual unknown (ticket 54 must settle):** the prebuilt Vulkan binary is expected to offload the
  **offline** v3 graph, but `src/asr/recognizer.cpp` hints offline-Vulkan parity was still being
  hardened. Confirm with `nemo-speech doctor` + a CPU-vs-Vulkan timing + `check_backend_coverage`, and
  byte-compare the transcripts. If the GPU is not actually used, the fallback is CPU or parakeet.cpp.

## Ticket 54 plan (condensed; full plan in report §7)

Download the prebuilt Vulkan tarball and the pinned GGUF into scratch
(`~/.cache/ges2t-discovery/parakeet-v3-engine-landscape/`), verify both sha256s, then measure CPU vs
Vulkan (cold/warm latency, RTF, peak RSS) and accuracy (std + `word_diff` WER) on the project's
existing reference clips — byte-comparing CPU vs Vulkan output. If option (B)/(C) is chosen, add the
committed-delta stability run modelled on the whisper-streaming report.

**Safe to relay?** Yes for the engine/Vulkan/dependency findings (documentary + device inventory).
**Not yet** for any *measured* Vulkan/accuracy number — none was produced here; ticket 54 produces
them.
