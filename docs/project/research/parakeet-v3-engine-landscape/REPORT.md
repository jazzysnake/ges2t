# Parakeet v3 engine landscape — which engine runs `nvidia/parakeet-tdt-0.6b-v3`, does it use Vulkan on this PC, and can it deliver incremental text?

- **Ticket / question:** ticket 53 of Epic 5. Which engine best supports **NVIDIA Parakeet TDT
  0.6B v3**; how hard is it to integrate; would it use **Vulkan GPU on this PC** (AMD Radeon 780M,
  RDNA3, RADV); and — the user's real motivation — can any Parakeet engine deliver **stable
  incremental (committed) text** that whisper.cpp cannot?
- **Branch:** `feat/parakeet-v3-engine-landscape`
- **Worktree:** `.worktrees/parakeet-v3-engine-landscape`
- **Date:** 2026-10-14
- **Status:** literature + metadata discovery complete (no engine built or run; that is ticket 54)
- **Scratch (outside the repo, never committed):** `~/.cache/ges2t-discovery/parakeet-v3-engine-landscape/`
- **Raw evidence:** this report's inline command outputs; [`data/sources.md`](data/sources.md) lists
  every URL rounded up. No engine binary, model, or audio is committed.

> **Method.** This is the literature half. Every claim below carries either a **source URL** or a
> **local command with its real output**. Where a verdict can only be settled by building and running
> the engine on this machine, it is marked **ticket 54** and stated as *not yet run* — never as a
> measurement. The reference machine was re-verified read-only: `vulkaninfo`, `nproc`, `free -g`,
> Python version (see §7). No engine was downloaded, built, or executed for this report.

---

## 1. Answer (headline)

**Recommended engine: `NVIDIA/NeMo-Speech.cpp`** — NVIDIA's own lightweight C++ runtime, built on
`llama.cpp`/`ggml`, Apache-2.0, which executes the model from the **official `q8_0` GGUF that NVIDIA
ships inside the `nvidia/parakeet-tdt-0.6b-v3` repo itself**, and publishes a **prebuilt
`linux-x86_64-vulkan` binary** (16,618,142 B). It needs **no Python, no `torch`, no `numpy`** at
inference time.

- **Vulkan on this PC: yes — expected.** NeMo-Speech.cpp ships an official Vulkan build for
  `linux-x86_64`, exposes `--device vulkan:0`, and has a `doctor` command that lists compiled
  backends and detected devices. ggml's Vulkan backend runs on RADV, which `vulkaninfo` confirms is
  the driver here (`AMD Radeon 780M Graphics (RADV PHOENIX)`, `driverName = radv`, Mesa 26.2.3). This
  is a documentation + device-inventory verdict; **ticket 54 confirms the on-device result** (device
  line + a CPU-vs-Vulkan timing).
- **Incremental text: not from Parakeet v3 itself.** Parakeet TDT v3 is an **offline (non-streaming)
  transducer**, and every engine that runs it says so: NeMo-Speech.cpp rejects `--stream`/`--live`
  for it and its recognizer throws *"this transducer encoder is offline-only and cannot serve
  StreamingRecognize"* (`src/asr/recognizer.cpp:224`); parakeet.cpp's `--stream` is documented only
  for the EOU / Nemotron streaming models. **So Parakeet v3 reproduces whisper.cpp's batch-only
  limitation, not the fix for it.**
- **But the incremental UX is achievable — with a streaming *sibling* model on the *same* engine.**
  `nvidia/nemotron-3.5-asr-streaming-0.6b` is a **cache-aware streaming** FastConformer-RNNT
  (configurable 80/160/320/560/1120 ms chunks) that NeMo-Speech.cpp runs in true streaming mode
  (`--stream`, `--live`, realtime WebSocket) and is in fact NeMo-Speech.cpp's **default ASR model**.
  It covers 40 locales **including Hungarian (`hu-HU`)**. Caveat: its Hungarian accuracy is
  **broad-coverage** tier (FLEURS WER ≈ 29–33) versus Parakeet v3's Hungarian **FLEURS WER 15.72**, so
  incremental Hungarian costs roughly half the accuracy.

**Bottom line for the user:** Parakeet v3 can be added on this PC, with Vulkan, at no new Python
dependency — but it will still be batch-only. If the point is *seeing text as you speak*, the engine
is the right choice and the *model* should be the streaming sibling, and that is a decision the
orchestrator must put to the user (see §8 and the gate in `001-comment.md`).

---

## 2. Model identity — `nvidia/parakeet-tdt-0.6b-v3`

All facts from the Hugging Face API and file tree (fetched 2026-10-14), plus NVIDIA's model card.

- **Repo:** `https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3`
- **Revision / commit (`sha`):** `541d1f99c6b0c3cd0b11a95167540bb8edefd82b` (HF API `sha`;
  `lastModified` 2026-08-05). NVIDIA's own NeMo-Speech.cpp index pins the **same** revision (see §3.1).
- **Licence:** **CC-BY-4.0** (`cardData.license`, and the card's "GOVERNING TERMS" section).
  Commercial and non-commercial use permitted with attribution.
- **Parameters:** **0.6 B** (600 million), stated in the card and implied by the safetensors size.
- **Architecture:** **FastConformer encoder + TDT (Token-and-Duration Transducer) decoder.** From
  `config.json`: 24 encoder layers, hidden 1024, 8 heads, 128 mel bins, subsampling factor 8;
  TDT decoder 2 layers, hidden 640, `durations [0,1,2,3,4]`, `vocab_size 8193`,
  `architectures: ["ParakeetForTDT"]`. The card calls it "FastConformer-TDT".

### 2.1 Published artifacts and byte sizes (HF tree, `?recursive=true&blobs=true`)

| file | bytes | LFS sha256 |
|---|---:|---|
| `model.safetensors` (fp32 weights) | 2,508,311,120 | `3a2026366188c8c68598edbbff92f8d11590a08e0ae2e6775544e7b07d6a5e11` |
| `parakeet-tdt-0.6b-v3.nemo` (NeMo archive) | 2,509,332,480 | `3cbdc85877e668ca7b82d0d56770eb1fac76691f55d6b97545e8d61ca588d10d` |
| **`parakeet-tdt-0.6b-v3.q8_0.gguf`** | **713,975,456** | `e3880d0aaaaf2c308ea2c35016b2b895c423eb3fda924c1b463d1c19b7f4d32e` |
| `tokenizer.json` | 1,159,960 | (not pinned here) |
| `config.json` | 1,153 | |
| `processor_config.json` | 392 | |
| `generation_config.json` | 289 | |

Total repo size ≈ 5.73 GB (dominated by the fp32 safetensors and the `.nemo` archive, which carry the
same weights twice). **The single artifact the recommended engine uses is the 681 MiB `q8_0` GGUF.**

### 2.2 Supported languages — **Hungarian: confirmed**

The card lists **25 European languages**, verbatim:
Bulgarian (bg), Croatian (hr), Czech (cs), Danish (da), Dutch (nl), English (en), Estonian (et),
Finnish (fi), French (fr), German (de), Greek (el), **Hungarian (hu)**, Italian (it), Latvian (lv),
Lithuanian (lt), Maltese (mt), Polish (pl), Portuguese (pt), Romanian (ro), Slovak (sk),
Slovenian (sl), Spanish (es), Swedish (sv), Russian (ru), Ukrainian (uk).

`hu` is present in HF `cardData.language`, and the model card's own `model-index` reports
**FLEURS `hu_hu` Test WER = 15.72** — the best Hungarian number of any model considered here. Language
is **auto-detected** ("transcribes it without requiring additional prompting").

### 2.3 Official ONNX export — **none**

The `nvidia/parakeet-tdt-0.6b-v3` repo publishes **no ONNX file**; the 12 repo files are the
safetensors, the `.nemo`, the **GGUF**, the tokenizer/config files, `README.md`, `plots/asr.png`, and
`.eval_results/`. NVIDIA's three documented runtimes are **NeMo-Speech.cpp (GGUF)**, **NVIDIA NeMo
(`.nemo`)**, and **HF Transformers (safetensors)** — no ONNX among them. Community ONNX exports
abound (searched via the HF API; e.g. `istupakov/parakeet-tdt-0.6b-v3-onnx`, `rleo/…-onnx`,
`myned-ai/…-greek`) and are the base of sherpa-onnx's `csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3`
family, but **none is NVIDIA-published or NVIDIA-pinned**. This matters for §3.4: an ONNX path means
trusting a third-party re-export.

---

## 3. Engine inventory — every runtime that can execute *this* model

Searched: NVIDIA's model card (its three officially documented paths), the Hugging Face API
(`?search=parakeet-tdt-0.6b-v3`, 100 repos), and GitHub (`parakeet.cpp`, `CrispASR`, `parakeet ggml`).
Only runtimes that load the **v3 weights** are listed. "**numpy?**" is answered against the project's
rule that `numpy` must stay absent.

### 3.1 `NVIDIA/NeMo-Speech.cpp` — **the recommended engine**

- **Upstream:** `https://github.com/NVIDIA/NeMo-Speech.cpp` (Apache-2.0; `created_at` 2026-07-15,
  `pushed_at` 2026-10-13, 178 stars, language C++). The model card names it as the "Run locally"
  path and shows exactly `hf download nvidia/parakeet-tdt-0.6b-v3 parakeet-tdt-0.6b-v3.q8_0.gguf`
  then `nemo-speech transcribe … --model ….gguf`.
- **Pinned version / revision:** latest tagged release **`v0.2.0`** (2026-10-02) plus a **`nightly`**
  channel (2026-10-12). Its in-tree model index `models/index.json` pins
  `nvidia/parakeet-tdt-0.6b-v3` at revision `541d1f99c6b0c3cd0b11a95167540bb8edefd82b`, licence
  `CC-BY-4.0`, artifact `parakeet-tdt-0.6b-v3.q8_0.gguf`, size `713975456`, sha256
  `e3880d0aaaaf2c308ea2c35016b2b895c423eb3fda924c1b463d1c19b7f4d32e` — **byte-identical to the HF
  artifact in §2.1** (size matches exactly; sha256 matches the HF LFS oid).
- **Integration language / linkage:** **C++**, built on `llama.cpp`/`ggml` (git submodule). It exports
  a **C ABI** (`libnemo_speech_asr_c`, per `docs/build.md` "Build outputs") — so a ges2t adapter can
  use `ctypes` in-process, exactly like the whisper.cpp backend, **or** shell out to the
  `nemo-speech` CLI, **or** run `nemo-speech serve` (HTTP / realtime WebSocket). Release archives
  include stable C headers and an exported CMake package (`find_package(NeMoSpeech REQUIRED
  COMPONENTS ASR)`).
- **Transitive dependencies:** at **inference** time, none beyond the system C/C++ runtime and the
  Vulkan loader/driver — the archives are self-contained ("Conversion tools are not included in the
  native binary archives; the installed runtime itself does not require Python"). Model download uses
  the system `curl`. **No Python, no `torch`, no `numpy`, no CUDA, no `onnxruntime`.** Build-time
  (only if building from source) needs CMake ≥ 3.26, Ninja, a C/C++17 compiler, **SentencePiece
  development files**, and — for the Vulkan backend — Vulkan headers + loader dev files, `glslc`,
  SPIR-V headers.
- **Licence:** NVIDIA-authored code **Apache-2.0**; third-party components keep their terms
  (`THIRD_PARTY_NOTICES.md`; `llama.cpp`/`ggml` are MIT). The **model weights are CC-BY-4.0**.
- **CPU vs GPU:** **both.** Backends compiled per preset: `cpu`, `cuda`, `metal`, **`vulkan`**
  (`CMakePresets.json` lists `vulkan-asr`, `vulkan-diar`, `vulkan-tts`, `vulkan-nmt`, `vulkan-speech`,
  `vulkan-server`). Device selection is automatic and overridable with `--device`/`--backend`
  (`cpu`, `cuda:0`, `metal`, `vulkan:0`); `nemo-speech doctor` prints the compiled backends and
  detected devices.
- **Streaming:** **yes for cache-aware streaming models; no for Parakeet TDT v3.** See §5.
- **Prebuilt Linux x86_64 Vulkan artifact (v0.2.0):**
  `nemo-speech-0.2.0-linux-x86_64-vulkan.tar.gz`, **16,618,142 B**, sha256
  `c8d0ed5b9ac5fb6d5c460015ba38ea217415c44edc1e6d49120646781009aee6` (from the release's
  `SHA256SUMS`). A matching `…-vulkan.zip` exists for Windows.

### 3.2 NVIDIA NeMo toolkit (Python) — the `.nemo` path

- **Upstream:** `https://github.com/NVIDIA-NeMo/NeMo`; card: `pip install -U nemo_toolkit['asr']`.
- **Linkage:** Python, `nemo_asr.models.ASRModel.from_pretrained("nvidia/parakeet-tdt-0.6b-v3")`.
- **Dependencies:** `torch` + `nemo_toolkit[asr]`, which pulls **`numpy`** and a large scientific stack
  (torchaudio, lightning, etc.). GPU is **CUDA** (and, unofficially, ROCm torch builds) — **torch has
  no Vulkan backend**. This is a hard `numpy`+`torch` dependency: a direct violation of the project's
  "numpy deliberately absent" rule and a very large install. **Not viable** for ges2t as-is.
- **Streaming:** NeMo documents a *chunked/buffered* streaming script
  (`examples/asr/asr_chunked_inference/rnnt/speech_to_text_streaming_infer_rnnt.py`, left/right
  context, e.g. `chunk_secs=2`, `left_context_secs=10`). That is **overlapping re-decode**, not the
  cache-aware streaming of the Nemotron sibling, and it is the regime the whisper-streaming report
  already showed to be unstable. Kept here only to complete the inventory.

### 3.3 HF Transformers — `ParakeetForTDT` / `AutoModelForTDT`

- **Upstream:** `https://github.com/huggingface/transformers`; card says it needs `pip install
  git+https://github.com/huggingface/transformers` **until Parakeet TDT is in a release**. `config.json`
  `transformers_version` is `5.6.0.dev0`.
- **Linkage:** Python; `AutoModelForTDT` + `AutoProcessor`, `torch`.
- **Dependencies:** `torch`, **`numpy`**, `datasets`/`soundfile` for audio in the examples. GPU is
  torch CUDA/ROCm — **no Vulkan**. Same `numpy`/`torch` problem as §3.2. **Not viable** for ges2t.

### 3.4 ONNX Runtime (all Linux execution providers)

- **Upstream:** `https://onnxruntime.ai/docs/execution-providers/` (docs fetched 2026-10-14; PyPI
  `onnxruntime` **1.31.0**, MIT).
- **Model source:** **not NVIDIA-published** — you take a community ONNX export of v3 (§2.3).
- **Linkage:** C/C++ API, or Python (`onnxruntime`) / C# / Java. C/C++ avoids Python.
- **Dependencies:** the **Python** package declares `numpy>=1.21.6` (plus `flatbuffers`, `packaging`,
  `protobuf`) — **Python integration forces `numpy` into the project.** The C/C++ API does not.
- **Execution providers available** (from the docs nav): CUDA, TensorRT / TensorRT-RTX, OpenVINO,
  oneDNN, DirectML (Windows), QNN, NNAPI, CoreML, XNNPACK, **ROCm**, **MIGraphX**, Vitis AI, Azure,
  WebGPU (web only), and community EPs (ACL, ArmNN, TVM, RKNPU, CANN). **There is no Vulkan EP.**
- **Verdict for this PC:** **no Vulkan.** AMD GPU paths are **ROCm** and **MIGraphX**, both of which
  need a ROCm install (not present) and have patchy support for the 780M (gfx1103, an iGPU) — see §4.
  Fallback is CPU (slow) or a ROCm/MIGraphX experiment.

### 3.5 sherpa-onnx (next-gen Kaldi + ONNX Runtime)

- **Upstream:** `https://github.com/k2-fsa/sherpa-onnx` (Apache-2.0, 15,270 stars, `pushed_at`
  2026-10-14). PyPI `sherpa-onnx` **1.13.8** → depends only on `sherpa-onnx-core` (1.13.8,
  `manylinux2014_x86_64` wheel 10,642,497 B, **no declared `requires_dist`** — so the Python path
  does not itself require `numpy`).
- **Model source:** its own ONNX exports
  (`csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3`, `…-v3-int8`, `…-v3-fp16`), i.e. a third-party
  re-export of v3.
- **Linkage:** C API (`sherpa-onnx/c-api/c-api.h`), C++ API, or Python; the `nemo-parakeet-c-api.c`
  example loads `encoder.int8.onnx` + `decoder.int8.onnx` + `joiner.int8.onnx` + `tokens.txt`.
- **Dependencies:** `onnxruntime` (bundled in the wheel / built in). **No Vulkan anywhere** — a grep
  of the repo tree finds **0** `vulkan` paths and 9 `cuda` paths.
- **Streaming:** v3 is loaded as an **`OfflineRecognizer`** (see the C API example). sherpa-onnx ships
  a `parakeet-tdt-simulate-streaming-microphone-cxx-api.cc` example, but reading its loop shows the
  mechanism is **VAD-segmented re-decode**: accumulate audio, run Silero VAD, and on each VAD segment
  create a *new* `OfflineStream` and decode the whole segment (`recognizer.Decode`); while speaking it
  re-decodes the whole growing buffer every ~0.2 s. That is the same "re-decode a growing window"
  shape as ges2t's Whistle path — **not** stable, append-only commits.
- **Verdict for this PC:** **no Vulkan** (onnxruntime); CPU works; CUDA if it were an NVIDIA box.

### 3.6 ggml-based Parakeet ports (`parakeet.cpp`, CrispASR) — CPU + **Vulkan**

These are the only *other* engines that can plausibly use Vulkan on this PC, and the ticket asked
specifically whether equivalents to `parakeet.cpp`/`CrispASR` exist. **They do.**

**`mudler/parakeet.cpp`** — `https://github.com/mudler/parakeet.cpp` (MIT, C++17, ggml, 841 stars,
`pushed_at` 2026-10-14). "A C++17/ggml port of NVIDIA's NeMo Parakeet models … runs on CPU and on GPU
backends, reads self-contained GGUF files, and **needs no Python at inference time**." Supports
`parakeet-tdt-0.6b-v3` explicitly ("25 European languages"). Publishes a flat, exception-free **C
API** (`include/parakeet_capi.h`, ABI 10) for `dlopen`/FFI. Device selection:
`PARAKEET_DEVICE=cpu`, or `CUDA0`/`Vulkan1`; "Ops that a backend lacks run on the CPU." `--stream`
is documented for the **EOU and Nemotron streaming** models — e.g.
`parakeet_realtime_eou_120m-v1` and `nemotron-3.5-asr-streaming-0.6b`. Own GGUF quants live in
`mudler/parakeet-cpp-gguf` (f16, q8_0, q6_k, q5_k, q4_k). Licences per model tracked in
`docs/licenses.md`.

**`CrispStrobe/CrispASR`** — `https://github.com/CrispStrobe/CrispASR` (MIT, a fork of whisper.cpp,
750 stars, `pushed_at` 2026-10-14). A unified ggml speech engine with **119 backends**; the parakeet
backend covers `nvidia/parakeet-tdt-0.6b-v3` ("FastConformer + TDT, 25 EU, CC-BY-4.0"). Ships
**`crispasr-linux-x86_64-vulkan.tar.gz`** prebuilt, CLI + HTTP server + C-ABI + language bindings.
Its own docs note the `-vulkan` build "requires the matching GPU driver and does not fall back to
CPU".

Both are community projects (not NVIDIA-published), so they add an audit/trust surface that
NeMo-Speech.cpp does not — but both are MIT and open, and both are real Vulkan options. The ticket's
rule "do not assume none exists" is answered: **two exist.**

### 3.7 Everything else that runs v3 (noted, not recommended)

`mlx-community/…` and other **MLX** repos (Apple Silicon only); many **CoreML** exports and
`NexaAI/…-npu` / `…-ane` (Apple/Qualcomm); `larryliu0820/…-executorch` (mobile); `FluidInference/…-ov`
(**OpenVINO**, Intel/CPU); `soniqo/…-LiteRT-INT8` (mobile); `rleo/…-onnx` (`parakeet-rs`, Rust + ORT);
`istupakov/…-onnx` (`onnx-asr`); `handy-computer/…-gguf` (tagged `transcribe.cpp`, another ggml
runtime); `Cactus-Compute/parakeet-tdt-0.6b-v3` (tagged `apple-npu`). None adds a Vulkan path beyond
§3.6, and none is NVIDIA-pinned.

---

## 4. Vulkan verdict for **this PC** (AMD Radeon 780M / RDNA3, RADV, Arch)

Local device check (read-only, run for this report):

```
$ vulkaninfo 2>&1 | grep -E 'deviceName|driverName|apiVersion'
	apiVersion        = 1.4.354 (4211042)
	deviceName        = AMD Radeon 780M Graphics (RADV PHOENIX)
	driverName        = radv
	driverInfo        = Mesa 26.2.3-arch1.1
```

So Vulkan 1.4 is live on RADV. Per engine:

| engine | Vulkan on this PC? | evidence |
|---|---|---|
| **NeMo-Speech.cpp** | **Yes (expected; ticket 54 confirms)** | official `nemo-speech-0.2.0-linux-x86_64-vulkan.tar.gz` (16,618,142 B, sha256 `c8d0ed5b…`); `vulkan-*` build presets; `--device vulkan:0`; `doctor` lists backends; `tools/check_backend_coverage.cpp` exists to prove no op falls back to CPU |
| `mudler/parakeet.cpp` | **Yes** | ggml; `PARAKEET_DEVICE=Vulkan1`; README "CUDA and Vulkan are also supported" |
| `CrispASR` | **Yes** | prebuilt `crispasr-linux-x86_64-vulkan.tar.gz`; ggml Vulkan |
| NVIDIA NeMo (torch) | **No** | torch has no Vulkan backend (CUDA/ROCm only) |
| HF Transformers (torch) | **No** | same |
| ONNX Runtime | **No** | **no Vulkan EP**; AMD paths are ROCm / MIGraphX (need ROCm install) |
| sherpa-onnx | **No** | built on onnxruntime; 0 `vulkan` paths in its repo tree |

**The reason it is `yes` for some and `no` for others is architectural:** Vulkan is a **ggml backend**.
Only engines that run the model on **ggml** (NeMo-Speech.cpp, parakeet.cpp, CrispASR) can use Vulkan;
the tensor-runner engines (torch, onnxruntime) never can. **ONNX Runtime's documented execution-
provider set contains no Vulkan EP** (its AMD providers are ROCm/MIGraphX), so sherpa-onnx and any
ORT path are CPU here.

**Fallback cost if Vulkan were unavailable.** CPU is the fallback everywhere. For the ggml engines,
CPU is a supported, correct path (NeMo-Speech.cpp's own README shows a CPU streaming result of **6×
real time** on the 0.6 B streaming model; parakeet.cpp/ CrispASR both default to CPU when no GPU is
found). For ONNX Runtime the nominal AMD path is **ROCm / MIGraphX**, but that needs a ROCm install
(no `sudo` scope here) and the 780M is an **iGPU (gfx1103)** whose ROCm support is limited; this is a
research question, not a promised win — and it is moot because ggml+Vulkan is available.

---

## 5. Incremental / streaming capability (the user's real question)

**Can Parakeet v3 emit stable committed text before the utterance ends? No — in every engine.** It is
an **offline transducer**: its FastConformer encoder is *not* cache-aware, so there is no incremental
encoder state to reuse, and every runtime treats it as one-shot.

Direct evidence in the recommended engine's source (`NVIDIA/NeMo-Speech.cpp`,
`src/asr/recognizer.cpp`):

```cpp
    if (model_->head_kind() != HeadKind::Ctc) {
        auto* transducer = static_cast<RnntModel*>(model_.get());
        if (!transducer->supports_cache_streaming())
            throw std::runtime_error(
                "this transducer encoder is offline-only and cannot serve StreamingRecognize");
```

and the CLI guide states it plainly:

> File transcription submits the complete recording as one offline request by default. Add `--stream`
> to feed a recorded WAV through the streaming recognizer in 160 ms input chunks. `--live` also uses
> the streaming recognizer. **Offline-only models such as Parakeet TDT reject `--stream` and `--live`.**

parakeet.cpp agrees by construction: its `--stream` flag is documented for the *EOU and Nemotron*
models only. sherpa-onnx's v3 path is `OfflineRecognizer` plus the VAD-segmented re-decode example
(§3.5) — a growing-window re-decode, the regime ges2t's Whistle path already uses and the
whisper-streaming report found unstable.

**The streaming sibling that closes the gap.** `nvidia/nemotron-3.5-asr-streaming-0.6b` is a
**cache-aware streaming** FastConformer-RNNT — genuinely different architecture, not a v3 runtime
trick:

- `--stream`, `--live`, and a realtime WebSocket server are all supported by NeMo-Speech.cpp, and it
  is NeMo-Speech.cpp's **default ASR model** (`models/index.json` `defaults.asr`).
- Chunk sizes **80 / 160 / 320 / 560 / 1120 ms** (card); NeMo-Speech.cpp's README quotes **160 ms
  chunks, 27 ms/chunk on CPU = 6× real time**, and **2.3 ms/chunk on an RTX 4090 = 67× real time**.
- The design "maintains caches for all encoder self-attention and convolution layers … there are no
  overlapping computations; each processed frame is strictly non-overlapping", i.e. **append-only
  output**, which is exactly what ges2t's commit cadence needs.
- **Hungarian is supported** (`hu-HU`, "broad-coverage" tier), but at **FLEURS WER ≈ 28.7–33.4**
  vs Parakeet v3 batch **15.72** — incremental Hungarian roughly doubles the error rate.
- Licence is **OpenMDW-1.1** (NVIDIA Open Model License, `license: other`, `license_name:
  openmdw-1.1`), *different from* v3's CC-BY-4.0; q8_0 GGUF **742,090,464 B** at the current HF
  revision (`ea30d66d…`). (NeMo-Speech.cpp's index pins an earlier revision `1c8deae…`, size
  741,548,352, sha256 `a5c435f2…` — a revision drift to note and re-pin in ticket 54.)
- English-only alternative: `nvidia/nemotron-speech-streaming-en-0.6b` (q8_0 699,872,960 B pinned in
  NeMo-Speech.cpp's index, sha256 `d9a01898…`).

**Implications for ADR 0011 and tickets 32–34.** ADR 0011's sliding-window commit cadence exists
*because Whistle re-transcribes a growing buffer*. A cache-aware streaming model removes that
reason: it would commit **append-only tokens per chunk** with no window cap and no seam
de-duplication, exactly the model ADR 0011's amendment assumes ("committed is append-only and never
revised"). If the user picks the streaming sibling, the integration must still respect ADR 0011's
contract (committed-only injection, a typed watermark, trailing space once), but it **retires the
5 s window** for that engine. That is a design input for tickets 32–34, not a code change now.

---

## 6. Integration cost estimate (top engine: NeMo-Speech.cpp)

Honest size: **M — comparable to the whisper.cpp integration (ADR 0021), no easier.** The template is
Epic 3; the shapes line up almost one-for-one.

| ges2t touchpoint | what it needs |
|---|---|
| `src/ges2t/transcribers/nemo_speech.py` (new) | a `Transcriber` adapter. Cleanest is **`ctypes` over `libnemo_speech_asr_c`** (the C ABI), model resident from service start — the exact shape of `whisper_cpp.py`. Fallback shape: subprocess over `nemo-speech transcribe`. |
| `src/ges2t/identifiers.py` | one new engine constant (`ENGINE_NEEMO_SPEECH = "nemo_speech"`), per Epic 4's rule. |
| `src/ges2t/model_store.py` | a pinned entry: `parakeet-tdt-0.6b-v3.q8_0.gguf`, size `713,975,456`, sha256 `e3880d0a…`, plus the **engine archive** pin (Vulkan tarball, sha256 `c8d0ed5b…`). |
| `ges2t setup` | download the **prebuilt Vulkan tarball** and the GGUF, verify, extract; **requires and never installs** prerequisites (curl, a Vulkan driver). If the user opts to build from source, `setup` checks CMake ≥ 3.26, Ninja, a C++17 compiler, SentencePiece dev files, and the Vulkan dev packages — and fails with the exact `pacman` lines (see §7, escalation). |
| `doctor` | engine binary/library present; `nemo-speech doctor`-equivalent backend list; Vulkan device detected; model present + size. |
| resident memory | q8_0 weights ≈ 681 MiB, **plus** ggml compute buffers and the FastConformer activations; expect roughly **0.8–1.2 GB RSS** (ticket 54 measures peak RSS). This is the same order as whisper `medium-q5_0` (0.8 GB). |
| model-load at service start | one `load()` call; GGUF load + graph build. Ticket 54 measures cold/warm. |

**Why it is no easier than whisper.cpp:** it is a new adapter, a new engine pin, a new model pin, a
new `setup` path, and new `doctor` checks — the same surface Epic 3 delivered across tickets 41–51.

**Why `mudler/parakeet.cpp` is a credible alternate:** same C-API + ggml + no-Python shape, **MIT**,
and a ges2t-friendly flat C ABI — but it has **no NVIDIA-published prebuilt Vulkan binary**, so `setup`
would have to **build it from source** (CMake + ggml-Vulkan + SentencePiece), i.e. a strictly larger
`setup` burden than downloading NeMo-Speech.cpp's tarball. Keep it as the fallback if
NeMo-Speech.cpp's offline-Vulkan coverage turns out poor in ticket 54.

---

## 7. Stage-2 measurement plan (what ticket 54 should do)

**Build/download (scratch, outside the repo — `~/.cache/ges2t-discovery/parakeet-v3-engine-landscape/`):**

```bash
# 1. engine: official prebuilt Vulkan binary (no build, no sudo)
curl -fL -o nemo-speech-vulkan.tar.gz \
  https://github.com/NVIDIA/NeMo-Speech.cpp/releases/download/v0.2.0/nemo-speech-0.2.0-linux-x86_64-vulkan.tar.gz
echo "c8d0ed5b9ac5fb6d5c460015ba38ea217415c44edc1e6d49120646781009aee6  nemo-speech-vulkan.tar.gz" | sha256sum -c -
tar xzf nemo-speech-vulkan.tar.gz -C ./engine
./engine/**/nemo-speech doctor            # compiled backends + detected devices (adjust to the archive layout)
# 2. model: official q8_0 GGUF (681 MiB), sha256 verified
curl -fL -o parakeet-tdt-0.6b-v3.q8_0.gguf \
  https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3/resolve/541d1f99c6b0c3cd0b11a95167540bb8edefd82b/parakeet-tdt-0.6b-v3.q8_0.gguf
echo "e3880d0aaaaf2c308ea2c35016b2b895c423eb3fda924c1b463d1c19b7f4d32e  parakeet-tdt-0.6b-v3.q8_0.gguf" | sha256sum -c -
# 3. transcribe, CPU vs Vulkan, on the project's existing reference clips
timeout 300 ./engine/**/nemo-speech transcribe clip.wav --model ./parakeet-tdt-0.6b-v3.q8_0.gguf --device cpu
timeout 300 ./engine/**/nemo-speech transcribe clip.wav --model ./parakeet-tdt-0.6b-v3.q8_0.gguf --device vulkan:0
```

**Metrics ticket 54 collects (reusing the whisper research's `word_diff`/`tokenise` and reference
clips):** cold/warm per-clip **latency**, **RTF**, **peak RSS** (and VRAM if reported), **accuracy**
(std + `word_diff` WER) against the project's reference truth, and — because v3 is batch-only — the
transcript must be **byte-compared CPU vs Vulkan** exactly as the whisper report did. If the user
elects the streaming sibling instead, add a **committed-delta stability** run modelled on the
whisper-streaming report: feed the clip in chunks, record every partial, and check append-only
commit, seam duplication, cut-off and silence hallucination.

**On-machine prerequisite check (read-only, already run for this report):**

```
$ nproc → 16
$ free -g → Mem: 27 total, 17 available
$ /usr/bin/python3 --version → Python 3.14.7
$ vulkaninfo --summary → Vulkan Instance 1.4.357; device AMD Radeon 780M (RADV PHOENIX), radv, Mesa 26.2.3-arch1.1
```

The environment table (`docs/project/orchestrator-state.md` §3) already records `vulkan-headers`,
`spirv-headers`, `shaderc`, `cmake` as installed, so a **source** Vulkan build would not need a new
package **except** `sentencepiece` (dev) if the prebuilt archive is not used. **The prebuilt archive
avoids that entirely and needs no `sudo`** — so ticket 54 needs **no** new package as long as it uses
the release binary. *(If a from-source build is chosen, the exact line to escalate is
`sudo pacman -S sentencepiece cmake ninja` — cmake/ninja are already present, so in practice
`sentencepiece` alone.)*

---

## 8. Recommendation and draft ADR

**Recommendation.** Integrate **`NVIDIA/NeMo-Speech.cpp`** as an opt-in `Transcriber`, running
**`nvidia/parakeet-tdt-0.6b-v3` `q8_0` (CC-BY-4.0, 681 MiB)** on **Vulkan** where available, with the
**default engine unchanged**. It is NVIDIA-published, Apache-2.0, uses the **official in-repo GGUF**,
adds **no Python/`torch`/`numpy` dependency**, ships a **prebuilt Vulkan binary** (no build, no
`sudo`), and exposes a C ABI that maps onto ADR 0010's `Transcriber` exactly as whisper.cpp did. Its
one limitation is decisive for the user's motivation: **Parakeet v3 is batch-only.** If the user's
priority is incremental text, the engine stays NeMo-Speech.cpp but the model should be
**`nvidia/nemotron-3.5-asr-streaming-0.6b`** (OpenMDW-1.1, 708 MiB, true cache-aware streaming,
Hungarian included at lower accuracy). That model choice is an escalation, not a §53 decision.

### Draft ADR 0022 — Parakeet v3 / NeMo-Speech.cpp as a third transcriber backend *(proposal; not written to `docs/adr/`)*

> **Status:** proposed (awaiting user sign-off) · **Date:** 2026-10-14 · **Related:** 0005, 0006, 0010, 0011, 0017, 0018, 0020, 0021
>
> **Context.** The user asked to add NVIDIA **Parakeet TDT 0.6B v3** (`nvidia/parakeet-tdt-0.6b-v3`,
> CC-BY-4.0, 25 European languages incl. Hungarian) as a selectable, offline backend, motivated by
> whisper.cpp's batch-only behaviour. Ticket 53 surveyed every engine (report:
> `docs/project/research/parakeet-v3-engine-landscape/REPORT.md`). Findings: (a) the model is an
> **offline transducer** and is batch-only in every engine; (b) the best engine is NVIDIA's own
> **NeMo-Speech.cpp** (Apache-2.0, C++/ggml), which uses the **official `q8_0` GGUF NVIDIA ships** and
> publishes a **prebuilt Linux x86_64 Vulkan binary** (no Python/`torch`/`numpy`); (c) Vulkan is a
> **ggml** backend, so only NeMo-Speech.cpp / parakeet.cpp / CrispASR can use it on the Radeon 780M —
> ONNX Runtime has no Vulkan EP; (d) incremental text requires the **cache-aware streaming sibling**
> `nvidia/nemotron-3.5-asr-streaming-0.6b` (OpenMDW-1.1), at lower Hungarian accuracy.
>
> **Decision (proposed).**
> 1. Add **`nemo_speech`** as an opt-in `transcriber.engine`; the **default stays `whistle`**, and
>    `whisper_cpp` is unchanged.
> 2. Integrate **in-process via `ctypes` over the C ABI** (`libnemo_speech_asr_c`), model resident
>    from service start — the whisper.cpp shape (ADR 0021 §2).
> 3. Pin the engine archive by **sha256 `c8d0ed5b…`** (v0.2.0 `linux-x86_64-vulkan`) and the model by
>    **sha256 `e3880d0a…` + size `713,975,456`** (ADR 0017).
> 4. **Device:** Vulkan where the binary and driver provide it; CPU otherwise. `setup` **requires and
>    never installs** prerequisites and never runs `sudo`; the prebuilt archive needs no build tooling.
> 5. **Streaming:** the model is **batch-only** — `supports_streaming = False`, exactly like
>    whisper.cpp (ADR 0021 §3). *If* the user additionally adopts the streaming sibling, that is a
>    separate ADR (it would supersede ADR 0011's 5 s-window design for that engine).
> 6. **No runtime network** (ADR 0018): `ensure_model()` is the only networked call; the GGUF and the
>    tarball are acquired by `ges2t setup` and verified.
> 7. **Licences:** engine Apache-2.0 (ggml MIT); model CC-BY-4.0. Record attribution for the
>    CC-BY-4.0 weights in the README, mirroring ADR 0006 §1's discipline.
>
> **Consequences.** A third backend behind the ADR 0010 seam with no shipped default change; the
> integration is **M**, comparable to Epic 3; the repo gains no dependency, binary, or weights; the
> resident cost is ~0.8–1.2 GB. The user gains Hungarian and a stronger multilingual model, but
> **not** incremental text unless the streaming sibling is also adopted.

---

## 9. Confounds and limits

- **No engine was built or run.** Every Vulkan/streaming/latency statement is documentary (upstream
  docs, source, releases) or device-inventory-local (`vulkaninfo`). The NeMo-Speech.cpp Vulkan result
  for **offline Parakeet v3** specifically is *expected*, not measured; `src/asr/recognizer.cpp`
  carries a comment ("… until the offline graph has Vulkan parity coverage; CTC and offline-only TDT
  remain offline") that could mean offline-Vulkan coverage is still being hardened. **Ticket 54 must
  run `check_backend_coverage` (or just measure) to confirm no CPU fallback.**
- **One machine, one driver.** All Vulkan statements are for RADV/Mesa on this 780M; the vendor driver
  (`amdvlk`) is not considered.
- **Revision drift.** NeMo-Speech.cpp's index and the current HF `main` disagree on the
  `nemotron-3.5-asr-streaming-0.6b` artifact size (741,548,352 vs 742,090,464) — different commits.
  The v3 pins agree exactly. Re-pin at ticket-54 time.
- **Accuracy numbers are published FLEURS WER**, not measured here; they are comparable across the two
  models (both report FLEURS) but are not ges2t's reference truth.
- **Community ports were read, not run.** parakeet.cpp/CrispASR claims (Vulkan, C ABI, streaming
  scope) are from their READMEs; their parity claims are unverified here.

## 10. What would falsify the recommendation

1. **Ticket 54 finds the prebuilt Vulkan binary does not run on RADV** (crash, or
   `check_backend_coverage` shows CPU-fallback ops making it slower than CPU). → the Vulkan headline
   is false; fall back to parakeet.cpp/CrispASR or CPU.
2. **The C ABI (`libnemo_speech_asr_c`) cannot keep the model resident / cannot feed 16 kHz mono
   float32 in an in-process loop**, forcing a per-utterance subprocess with a reload cost. → the
   integration shape changes and the cost rises.
3. **`numpy` turns out to be required anywhere in the chosen path.** → a project rule is violated and
   the engine is disqualified (it is the reason NeMo/Transformers/ORT-Python were rejected).
4. **The streaming sibling's Hungarian accuracy is unusable in practice** (~30% FLEURS WER), so
   "incremental + Hungarian" is not actually achievable and the epic's value reduces to multilingual
   batch. → the recommendation narrows to "a better batch model", not the UX fix.
5. **CC-BY-4.0 attribution is unacceptable to the user**, or a redistribution issue surfaces. →
   licence escalation.

## 11. Verification and cleanup

- Read-only local commands run for this report: `vulkaninfo` (device/driver), `nproc`, `free -g`,
  `/usr/bin/python3 --version`. No microphone, clipboard, keystroke, window, notification, or `sudo`.
- Networked calls were **documentation and metadata only**: HF API/site, `raw.githubusercontent.com`,
  `api.github.com`, `pypi.org`, `onnxruntime.ai`. **No model, engine, or audio bytes were downloaded.**
- Nothing was written under `src/ges2t/` or `tests/`; the scratch dir
  (`~/.cache/ges2t-discovery/parakeet-v3-engine-landscape/`) holds only notes/URLs, no binaries.
- Sources: see [`data/sources.md`](data/sources.md). Gate decision for ticket 54: `001-comment.md`.
