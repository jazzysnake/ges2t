# Sources — ticket 53 (Parakeet v3 engine landscape)

All fetched 2026-10-14. Curated list; each row is a URL that backs a claim in `../REPORT.md`.

## Model

| source | backs |
|---|---|
| https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3 | model repo, card, licence, languages, architecture |
| https://huggingface.co/api/models/nvidia/parakeet-tdt-0.6b-v3 | `sha` `541d1f99…`, `cardData.license=cc-by-4.0`, `language` incl. `hu` |
| https://huggingface.co/api/models/nvidia/parakeet-tdt-0.6b-v3/tree/main?recursive=true&blobs=true | file sizes + LFS sha256 (`model.safetensors`, `.nemo`, `q8_0.gguf`) |
| https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3/resolve/main/config.json | FastConformer/TDT dims, `ParakeetForTDT` |
| https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3/resolve/main/processor_config.json | 16 kHz, 128 mel, n_fft 512, hop 160 |
| https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3/resolve/main/README.md | languages list, Hungarian FLEURS 15.72, NeMo/NeMo-Speech.cpp/Transformers usage, streaming script |
| https://huggingface.co/api/models?search=parakeet-tdt-0.6b-v3&limit=100 | the export/port landscape (ONNX, GGUF, MLX, CoreML, sherpa-onnx, …) |

## Recommended engine — NVIDIA/NeMo-Speech.cpp

| source | backs |
|---|---|
| https://github.com/NVIDIA/NeMo-Speech.cpp | repo, Apache-2.0, C++, ggml/llama.cpp |
| https://raw.githubusercontent.com/NVIDIA/NeMo-Speech.cpp/main/README.md | supported models, 160 ms chunks, 67×/6× RT, install, licence |
| https://raw.githubusercontent.com/NVIDIA/NeMo-Speech.cpp/main/docs/cli.md | `--stream`/`--live`; "Offline-only models such as Parakeet TDT reject"; backends; `--device vulkan:0`; `doctor`; cache |
| https://raw.githubusercontent.com/NVIDIA/NeMo-Speech.cpp/main/docs/build.md | Vulkan build prereqs; C ABI `libnemo_speech_asr_c`; CMake presets; outputs |
| https://raw.githubusercontent.com/NVIDIA/NeMo-Speech.cpp/main/models/index.json | pinned revision `541d1f99…`, GGUF size `713975456`, sha256 `e3880d0a…`, licence; defaults; nemotron streaming pins |
| https://raw.githubusercontent.com/NVIDIA/NeMo-Speech.cpp/main/src/asr/recognizer.cpp | "offline-only and cannot serve StreamingRecognize"; offline+TDT remain offline |
| https://raw.githubusercontent.com/NVIDIA/NeMo-Speech.cpp/main/app/transcribe.cpp | `--stream` wiring |
| https://raw.githubusercontent.com/NVIDIA/NeMo-Speech.cpp/main/src/asr/runner.cpp | `OfflineRunner` / `CacheStreamRunner` |
| https://raw.githubusercontent.com/NVIDIA/NeMo-Speech.cpp/main/tools/check_backend_coverage.cpp | CPU-fallback diagnostic (offline Vulkan parity) |
| https://raw.githubusercontent.com/NVIDIA/NeMo-Speech.cpp/main/CMakePresets.json | `vulkan-asr`, `vulkan-server`, … |
| https://github.com/NVIDIA/NeMo-Speech.cpp/releases/download/v0.2.0/SHA256SUMS | Vulkan tarball sha256 `c8d0ed5b…`, size 16,618,142 |
| https://api.github.com/repos/NVIDIA/NeMo-Speech.cpp/releases | release assets and sizes |

## Streaming sibling

| source | backs |
|---|---|
| https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b | cache-aware streaming RNNT, 80–1120 ms chunks, 40 locales incl. hu-HU, licences, GGUF size |
| https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b/raw/main/README.md | language tiers, per-language FLEURS WER table, architecture, HF (06/04/2026) |
| https://huggingface.co/api/models/nvidia/nemotron-3.5-asr-streaming-0.6b | `sha ea30d66d…`, `license: other` / `openmdw-1.1`, `hu`, q8_0 size 742,090,464 |

## Other engines

| source | backs |
|---|---|
| https://github.com/mudler/parakeet.cpp (+ `/master/README.md`) | MIT ggml port, CPU/CUDA/Vulkan, C ABI, `--stream` scope, `parakeet-tdt-0.6b-v3` |
| https://github.com/CrispStrobe/CrispASR (+ `/main/README.md`) | MIT whisper.cpp fork, 119 backends, Vulkan tarball, parakeet v3 |
| https://onnxruntime.ai/docs/execution-providers/ | EP list — no Vulkan; ROCm/MIGraphX are AMD paths |
| https://pypi.org/pypi/onnxruntime/json | 1.31.0, MIT, `numpy>=1.21.6` |
| https://pypi.org/pypi/sherpa-onnx/json + https://pypi.org/pypi/sherpa-onnx-core/json | 1.13.8, Apache-2.0, no `numpy` in the meta/core wheel |
| https://github.com/k2-fsa/sherpa-onnx | repo; 0 `vulkan` paths |
| https://raw.githubusercontent.com/k2-fsa/sherpa-onnx/master/c-api-examples/nemo-parakeet-c-api.c | v3 loaded as `OfflineRecognizer` (encoder/decoder/joiner int8 ONNX) |
| https://raw.githubusercontent.com/k2-fsa/sherpa-onnx/master/cxx-api-examples/parakeet-tdt-simulate-streaming-microphone-cxx-api.cc | "simulate streaming" = VAD-segmented re-decode |

## Local (read-only)

```
vulkaninfo 2>&1 | grep -E 'deviceName|driverName|apiVersion'
#   apiVersion = 1.4.354 ; deviceName = AMD Radeon 780M Graphics (RADV PHOENIX) ;
#   driverName = radv ; driverInfo = Mesa 26.2.3-arch1.1
nproc            # 16
free -g          # Mem: 27 total, 17 available
python3 --version # Python 3.14.7
```
