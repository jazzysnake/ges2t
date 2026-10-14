# Nemotron 3.5 ASR streaming on this PC — does it deliver stable append-only incremental text, and at what cost?

- **Ticket / question:** ticket 55 of Epic 6. Run the prebuilt **NeMo-Speech.cpp** Vulkan engine on
  this machine (AMD Radeon 780M, RADV) against **`nvidia/nemotron-3.5-asr-streaming-0.6b`** and
  measure: does the GPU path actually engage (not a silent CPU fallback), per-chunk latency / RTF /
  peak RSS, **Vulkan vs CPU**, accuracy against the existing English ground truth, and — the core
  claim — whether streaming output is **stable and append-only** (no seam duplication, cut-off
  hallucination, or silence hallucination).
- **Branch:** `feat/nemotron-3-5-asr-streaming-measurement`
- **Worktree:** `.worktrees/nemotron-3-5-asr-streaming-measurement`
- **Date:** 2026-10-14
- **Machine:** reference box (Ryzen 7 7840HS + Radeon 780M iGPU, RDNA3/RADV, Mesa 26.2.3), Arch + KDE
  Plasma 6, Wayland, 16 threads, 27 GiB RAM, load average ~1.4–3.3 during the runs.
- **Scratch (outside the repo, never committed):**
  `~/.cache/ges2t-discovery/nemotron-3-5-asr-streaming-measurement/`
  (16,618,142 B engine tarball, 741,548,352 B GGUF, extracted engine, full per-partial JSON with word
  lists, sliced WAVs).
- **Raw evidence:** committed under [`data/`](data/): `cli_runs.jsonl`, `stream_summary.jsonl`,
  `analysis.jsonl`, `paced_summary.jsonl`, `chunk_sweep.jsonl`, `timings.md`, `engine_doctor.txt`,
  `model_revision.md`, and **the full per-partial traces** — all 25 traces in `data/partials/`
  (streaming, paced, silence) and all 5 sweep traces in `data/sweep/`, each with `word_times` so
  `analyze_streaming.py` reproduces `analysis.jsonl` from the repo alone (the `words` array is
  omitted as redundant with the transcript). No engine binary, model weight, or audio file is
  committed.

> **Method.** Every number below traces to a command actually run for this report on this machine.
> The engine is the official prebuilt archive (no build, no `sudo`); the model is the revision the
> engine's own index pins. The CLI harness records wall/RTF/RSS/accuracy. The `ctypes` C-ABI harness
> drives `libnemo_speech_asr_c` and records **every interim result** the streaming decoder emits,
> because the file CLI (`nemo-speech transcribe --stream`) prints only the final transcript — the
> partial stream is only reachable through the C ABI, which is also the seam the future adapter uses.
> Section 2 gives the exact commands; §9 the confounds.

---

## 1. Answer (headline)

**Yes on Vulkan, and the core claim holds: on this machine the cache-aware streaming model produces
strictly append-only incremental text, with no seam duplication, no cut-off hallucination, and no
silence hallucination. Vulkan is the cheaper path on *both* counts — host RSS ~0.2× CPU's (the q8_0
weights live in VRAM) and roughly **half the per-chunk latency** — at the price of ~950 MB of VRAM;
the real cost of the streaming model is **accuracy**, and the Hungarian accuracy that motivated the
epic is not measurable here.**

1. **The engine runs here, on Vulkan, prebuilt, no build, no `sudo`.** The 16,618,142 B archive's
   sha256 matched; `nemo-speech doctor` reports `backend_vulkan` and device `[0] integrated-gpu AMD
   Radeon 780M Graphics (RADV PHOENIX)`. Every measured run prints `[asr] … backend=Vulkan0` on
   stderr with `--device vulkan:0`, and `--device cpu` prints `backend=CPU`.
2. **Vulkan is actually used, proven three ways, not assumed.** (a) the stderr backend line; (b)
   `--device cpu` streams with **VRAM delta 0 MB** while `--device vulkan` streams with **VRAM delta
   ~952 MB** (sysfs `mem_info_vram_used`, sampled across model load); (c) **peak RSS ~197 MB on
   Vulkan vs ~1,064 MB on CPU** for the identical C-ABI run — the 707 MiB q8_0 weights are resident
   in VRAM, not host RAM. There is **no silent CPU fallback**.
3. **Latency / cost.** Real-time-paced (`--pace`), Vulkan decodes a 160 ms chunk in **~41–45 ms
   (p90)** and emits its first text after **~0.85–1.16 s of wall clock** (the engine's audio
   watermark at that point is 0.96–1.28 s); CPU needs **~72–88 ms** per chunk. Both keep up
   (RTF < 1); Vulkan has ~3.5× headroom per chunk, CPU ~1.8×. The algorithmic
   lag between the encoder watermark and the committed text is **160–240 ms** (one chunk).
4. **Committed-delta stability — the core claim — passes on all 20 runs (10 clips × Vulkan/CPU),
   and on all five chunk sizes.** The interim transcript is a **strict string-prefix extension** of
   the previous one **100 % of the time** (`append_only_char_frac = 1.0`), with **0 rewritten pairs**,
   **monotone commit counts**, and **0 adjacent duplicate words**. On 10 s of digital silence: **0
   interims, an empty final**, both devices — no fantasy words. This is the property whisper.cpp's
   windows could not provide ([`whisper-cpp-streaming`](../whisper-cpp-streaming/REPORT.md): every
   window boundary duplicated a word and all models hallucinated on silence).
5. **Accuracy is the weak side, and it is the model's, not the engine's.** On the reference clips,
   word-diff WER (ticket 32 normalisation) is **jfk 0.000**, **quiet-short 0.259**, **quiet-long
   0.167**, **car (user's capture) 0.312**. Whisper `medium q5_0` scored **0.062** on the same car
   clip **under its `-nt` decode (0.156 without it)**, so the fair gap on the CLI default is ~2× —
   this model is weaker on that noisy real-device recording, as the survey warned.
6. **Vulkan and CPU are NOT byte-identical — deterministic per device, divergent across devices.**
   On all 10 CLI clips the two backends' transcripts differ (word-diff divergence 0.000–0.185; e.g.
   jfk ends `…country` on Vulkan vs `…country.` on CPU). Both are reproducible (2–3 repeats per
   device, identical hashes). This is a backend numeric difference, and it is *positive* evidence
   the GPU path is real — a silent CPU fallback would have produced byte-identical CPU output.
7. **Hungarian is not locally measured.** The model advertises `hu-HU` (broad-coverage tier, published
   FLEURS WER ≈ 28.7–33.4), but there is **no Hungarian clip with a human reference** in the repo or
   scratch, and recording one needs a microphone (out of scope). A user-supplied Hungarian sample +
   transcript is required to settle it — flagged in `001-comment.md`.

**Recommendation:** proceed with the integration (Epic 6 / ADR 0022) **on the strength of the
streaming contract**, which is real and clean here; but (a) treat **Vulkan as the default device for
this engine** with a documented CPU fallback, and (b) do **not** promise Hungarian accuracy — it is
unmeasured on this machine and published-worse than Parakeet v3. The 5 s sliding window genuinely
does not apply: this engine commits append-only by construction. See §11.

---

## 2. Method and exact commands

Scratch: `~/.cache/ges2t-discovery/nemotron-3-5-asr-streaming-measurement/` (SC below).
Engine: `SC/engine/nemo-speech-0.2.0-linux-x86_64-vulkan/bin/nemo-speech` (RPATH `$ORIGIN/../lib`, so
no `LD_LIBRARY_PATH` needed). Model: `SC/models/nemotron-3.5-asr-streaming-0.6b.q8_0.gguf`.

**Engine + model acquisition (network, once):**

```bash
curl -fL -o nemo-speech-0.2.0-linux-x86_64-vulkan.tar.gz \
  https://github.com/NVIDIA/NeMo-Speech.cpp/releases/download/v0.2.0/nemo-speech-0.2.0-linux-x86_64-vulkan.tar.gz
echo "c8d0ed5b9ac5fb6d5c460015ba38ea217415c44edc1e6d49120646781009aee6  nemo-speech-0.2.0-linux-x86_64-vulkan.tar.gz" | sha256sum -c -
mkdir -p engine && tar xzf nemo-speech-0.2.0-linux-x86_64-vulkan.tar.gz -C engine
./engine/nemo-speech-0.2.0-linux-x86_64-vulkan/bin/nemo-speech doctor

curl -fL -o models/nemotron-3.5-asr-streaming-0.6b.q8_0.gguf \
  "https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b/resolve/1c8deaecc64b91f034d73e08dd8b64625eb3395d/nemotron-3.5-asr-streaming-0.6b.q8_0.gguf"
echo "a5c435f294eea8f88ce68dd27b8c3bfea7f777cb2fbba04fcd30eaa555f429ae  models/nemotron-3.5-asr-streaming-0.6b.q8_0.gguf" | sha256sum -c -
```

**Measurement harnesses** (committed beside this report, run from the worktree):

```bash
# CLI matrix: wall/RTF/RSS/accuracy, batch, every clip × {vulkan:0, cpu}
bash docs/project/research/nemotron-3-5-asr-streaming-measurement/run_matrix.sh cli
# C-ABI streaming matrix: every partial, Vulkan then CPU
bash docs/project/research/nemotron-3-5-asr-streaming-measurement/run_matrix.sh stream-vk
bash docs/project/research/nemotron-3-5-asr-streaming-measurement/run_matrix.sh stream-cpu
# Append-only / stability metrics (needs ticket 32's word_diff)
PYTHONPATH=src python3 docs/project/research/nemotron-3-5-asr-streaming-measurement/analyze_streaming.py \
  --partials "$HOME/.cache/ges2t-discovery/nemotron-3-5-asr-streaming-measurement/partials" \
  --out "$HOME/.cache/ges2t-discovery/nemotron-3-5-asr-streaming-measurement/analysis.jsonl"
# Curate the committed evidence + render tables
PYTHONPATH=src python3 docs/project/research/nemotron-3-5-asr-streaming-measurement/render_report_data.py
```

Every run is wrapped in `timeout` (240–300 s in the matrix; the harness has an internal wall
timeout too); peak RSS is the child's `ru_maxrss` high-water mark; the child is waited on, so no
process outlives a run. The three harnesses are discovery scripts under `docs/` (ADR 0014), not
shipped code.

**Reference audio** — the same clips the whisper reports measured —
`~/.cache/ges2t-discovery/whisper-cpp-provider/audio/`: `jfk.wav`, `quiet-short.wav`,
`quiet-long.wav`, the user's `ges2t-commit-cadence-capture-20261009-195914.wav` (the "car" clip),
`clip5.wav`, `clip30.wav`, `2.wav`, `3.wav`, `four.wav`, `cny16.wav`. The human references are the
verbatim ones in `whisper-cpp-provider/measure_whisper_cpp.py::REFERENCES`; the word_diff and
tokenise normalisation is ticket 32's, imported by path, so the WER columns are apples-to-apples with
the whisper reports.

**Control:** 10 s of digital silence, generated in scratch (`silence-10s.wav`), both devices.

**Chunk sizes:** the engine does **not** expose the model's 80/160/320/560/1120 ms chunk configs
through `--asr.streaming.chunk_size` (that flag is the *CTC* buffered window and left the RNNT step
at 160 ms for every value tested). The cache-aware RNNT step is set by `asr.streaming.rnnt_right_context`
with `step = 80 ms × (rc + 1)`; the documented sizes map to **rc = 0 / 1 / 3 / 6 / 13**. Both the
CLI default and the primary streaming matrix use rc = 1 (**step 160 ms**); the sweep covers all five.

---

## 3. Engine runs here — archive, `doctor`, devices

The release archive downloaded and verified, no build tooling and no `sudo`:

```
16,618,142  nemo-speech-0.2.0-linux-x86_64-vulkan.tar.gz
nemo-speech-0.2.0-linux-x86_64-vulkan.tar.gz: OK
```

Extracted (48 MB; `bin/nemo-speech`, `lib/libnemo_speech_asr_c.so.1` + ggml CPU/Vulkan libs,
headers, docs). `nemo-speech doctor` (full output in [`data/engine_doctor.txt`](data/engine_doctor.txt)):

```
$ nemo-speech doctor
NeMo-Speech.cpp 0.2.0
Features: asr backend_vulkan diarization http integrated_vad model_pull punctuation realtime_websocket speech_translation text_normalization translation tts
Devices:
  [0] integrated-gpu AMD Radeon 780M Graphics (RADV PHOENIX) (17.5 GiB)
  [1] cpu            AMD Ryzen 7 7840HS with Radeon 780M Graphics (27.1 GiB)
Model downloads: /usr/bin/curl

$ vulkaninfo --summary
    apiVersion = 1.4.354   deviceName = AMD Radeon 780M Graphics (RADV PHOENIX)   driverName = radv
```

The `backend_vulkan` feature flag and device `[0]` are the compiled-in Vulkan backend and the RADV
iGPU. The file CLI adds, on every run:

```
[asr] model=… head=rnnt backend=Vulkan0
[asr] mode=streaming head=rnnt left=56 center=1 right=1 attention=58 encoder-frames step=160ms
```

with `--device cpu` the second line's first token becomes `backend=CPU`. **These are the backend
proof lines quoted in §6.**

## 4. Model revision resolved

Two different commits exist (fetched 2026-10-14). The HF API's `?revision=<sha>` fell back to `main`,
so each row was confirmed from the `resolve/<sha>` LFS headers (`x-repo-commit`, `x-linked-size`,
`x-linked-etag`) — see [`data/model_revision.md`](data/model_revision.md):

| source | revision | size (B) | sha256 |
|---|---|---:|---|
| HF `main` | `ea30d66debe3740a08b573244286791d423d6b3e` | 742,090,464 | `3fc991d3badad7277c11030a7519832cddaf2057aafed6d4b25147e953a070b1` |
| **engine `model-index.json` (chosen)** | `1c8deaecc64b91f034d73e08dd8b64625eb3395d` | 741,548,352 | `a5c435f294eea8f88ce68dd27b8c3bfea7f777cb2fbba04fcd30eaa555f429ae` |

**Chosen: the engine-index revision `1c8deae…`** (ticket guidance: "prefer consistency with the
engine's own index"). The downloaded GGUF is 741,548,352 B and its sha256 verified with `sha256sum -c`
(**OK**). The two artifacts differ by 542,112 B and are not interchangeable pins; `ges2t setup` must
pin `1c8deae…` + `a5c435f2…` (or update the engine and re-pin both together). The model's licence is
**OpenMDW-1.1** (`cardData.license = other`, `license_name = openmdw-1.1`).

## 5. Latency, RTF, RSS

Two shapes were measured: the **CLI** (`nemo-speech transcribe`, one process per clip, `[asr]` line on
stderr) and the **C-ABI streaming** harness (model created once per run, audio pushed 160 ms at a
time). Full tables in [`data/timings.md`](data/timings.md).

### 5.1 CLI batch — wall, RTF, peak RSS (all 10 clips, both devices)

| clip | dev | audio s | wall s | RTF | peak RSS MB | WER (word_diff) |
|---|---|---:|---:|---:|---:|---:|
| jfk | vulkan | 11.00 | 5.11 | 2.15 | 180.9 | 0.000 |
| jfk | cpu | 11.00 | 4.38 | 2.51 | 1848.9 | 0.000 |
| quiet-short | vulkan | 13.51 | 4.79 | 2.82 | 180.7 | 0.259 |
| quiet-short | cpu | 13.51 | 5.07 | 2.67 | 1882.8 | 0.296 |
| quiet-long | vulkan | 23.81 | 6.05 | 3.94 | 180.8 | 0.167 |
| quiet-long | cpu | 23.81 | 6.14 | 3.88 | 2018.2 | 0.194 |
| car (capture) | vulkan | 25.04 | 6.22 | 4.03 | 180.8 | 0.312 |
| car (capture) | cpu | 25.04 | 6.70 | 3.74 | 2034.9 | 0.234 |
| clip30 | vulkan | 30.00 | 7.28 | 4.12 | 180.7 | — |
| cny16 | vulkan | 10.44 | 4.34 | 2.40 | 180.6 | — |
| … (clip5, 2, 3, four complete in `data/cli_runs.jsonl`) | | | | | | |

The wall time includes process start, model load, and (for this cache-aware model) the whole-file
decode, so it over-states steady-state cost and is load-sensitive (load average ~1.4–3.3 during the
session; the whisper report saw the same `medium` Vulkan 5 s window vary 0.97→1.9 s with load).
**The RSS column is the headline and it is not noise**: Vulkan holds **180.5–180.9 MB** on every clip
regardless of length; CPU holds **1,758–2,099 MB**. The 707 MiB quantised weights live in VRAM on
Vulkan.

### 5.2 C-ABI streaming — throughput, memory, latency

Model load is lazy: `create` returns in **~0.10–0.12 s**, and the first push does the graph
compile + weight upload (the warm-up). Steady-state per-chunk figures exclude the first three pushes.

| clip | dev | audio s | stream s | RTF | peak RSS MB | VRAM Δ MB | interims | first text (s audio) | steady p50 / p90 ms |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| jfk | vulkan | 11.00 | 3.66 | 3.01 | 196.8 | 952 | 62 | 1.28 | 47.1 / 57.6 |
| car | vulkan | 25.04 | 6.27 | 3.99 | 197.3 | 952 | 146 | 1.92 | 24.3 / 58.2 |
| quiet-long | vulkan | 23.81 | 5.82 | 4.09 | 197.2 | 952 | 130 | 3.20 | 25.7 / 58.1 |
| clip30 | vulkan | 30.00 | 6.55 | 4.58 | 197.8 | 952 | 183 | 0.96 | 24.4 / 57.0 |
| cny16 | vulkan | 10.44 | 2.35 | 4.45 | 197.1 | 953 | 61 | 0.96 | 24.4 / 28.9 |
| jfk | cpu | 11.00 | 4.83 | 2.28 | 1063.7 | **0** | 62 | 1.28 | 67.2 / 73.5 |
| car | cpu | 25.04 | 5.91 | 4.24 | 1064.4 | **0** | 146 | 1.92 | 35.6 / 37.6 |
| clip30 | cpu | 30.00 | 7.11 | 4.22 | 1064.2 | **0** | 183 | 0.96 | 36.1 / 37.7 |
| (all 20 rows in `data/stream_summary.jsonl`) | | | | | | | | | |

- **Peak RSS: ~197 MB (Vulkan) vs ~1,064 MB (CPU)** for the identical harness. The CLI's higher CPU
  figure (~1.8–2.1 GB) reflects its extra subsystems; both shapes show Vulkan sitting ~5–10× lower.
- **VRAM Δ: ~952–962 MB on Vulkan, exactly 0 on CPU** (sysfs `mem_info_vram_used` sampled before
  model create and after streaming). This is direct proof the weights+buffers are on the GPU.
- **RTF 1.8–4.6** (audio seconds per stream second) either way; CPU is occasionally *faster* on very
  short clips because Vulkan pays a fixed device/graph cost.
- **First text** appears after 0.70–1.32 s of wall / 0.96–3.2 s of audio-processed watermark. The
  large values (quiet-short 2.56 s, quiet-long 3.2 s) are where the opening audio carried no
  decodable token for a while, not a stall.

### 5.3 Real-time-paced runs (the human-facing latency)

Feeding 160 ms of audio every 160 ms of wall (`--pace`), Vulkan stays caught up and emits its first
text after ~1 s:

| label | audio s | stream s | first text wall s | first text audio s | steady p50 ms | steady p90 ms |
|---|---:|---:|---:|---:|---:|---:|
| paced-jfk-vulkan | 11.00 | 11.10 | **1.164** | 1.28 | 40.8 | 44.7 |
| paced-clip30-vulkan | 30.00 | 30.09 | **0.854** | 0.96 | 41.2 | 45.0 |
| paced-jfk-cpu | 11.00 | 11.18 | 1.209 | 1.28 | 71.8 | 87.7 |

Vulkan spends ~45 ms of each 160 ms chunk (3.5× headroom); CPU ~88 ms (1.8× headroom). Both are
**faster than real time**, so neither drops audio. The algorithmic lag (encoder watermark minus the
last committed word's end time) is **160–240 ms** across all 20 runs — the text trails the spoken
audio by about one chunk, which is the expected cache-aware right-context cost.

### 5.4 Chunk-size sweep (Vulkan)

`rnnt_right_context` 0/1/3/6/13 → step 80/160/320/560/1120 ms. Bigger chunks trade latency for
throughput:

| step ms | jfk stream s / RTF | clip30 stream s / RTF | steady p50 ms (jfk) | first text (s audio) |
|---:|---:|---:|---:|---:|
| 80 | 3.66 / 3.00 | 8.49 / 3.53 | 21.5 | 1.28 |
| 160 | 2.22 / 4.96 | 4.74 / 6.33 | 21.9 | 1.28 |
| 320 | 1.66 / 6.64 | 2.97 / 10.11 | 24.3 | 1.60 |
| 560 | 1.31 / 8.38 | 2.68 / 11.18 | 32.7 | 1.68 |
| 1120 | 1.05 / 10.49 | 1.93 / 15.57 | 42.3 | 2.24 |

All five are faster than real time; **all five are append-only** (see §7). `jfk` is word-perfect
(WER 0.000) at every chunk size. The integration should pick a step from the *measured* set rather
than a guessed one (see §11).

## 6. Vulkan vs CPU — byte-compare and the actual-device proof

**The GPU is really used.** Three independent signals agree: the engine's own `backend=Vulkan0` /
`backend=CPU` stderr line; VRAM Δ 952 MB vs 0; peak RSS 197 MB vs 1,064 MB. A silent CPU fallback
would show `backend=CPU`, 0 VRAM, and ~1 GB RSS — none of which occurred.

**The transcripts are byte-compared and are NOT identical.** On the identical CLI invocation

```bash
nemo-speech transcribe jfk.wav --model … --device vulkan:0
nemo-speech transcribe jfk.wav --model … --device cpu
```

the outputs differ, deterministically:

```
vulkan:  c6870698b3d8615104954a5e963760199d908d598c83b4eefd09229c2977633b
cpu:     b6696939a50134db4cfc1fb13a2c11cb5d5702af573ad76054b2ca6c01c5ec0c
vulkan tail: "…your country"      cpu tail: "…your country."
```

Both hashes reproduce across 2–3 repeats per device (jfk, quiet-short, car all verified). Across all
**10** CLI clips the transcripts differ; the word-diff divergence (`word_diff(cpu, vulkan)`) is:

| clip | divergence | clip | divergence |
|---|---:|---|---:|
| jfk | 0.000 (trailing `.` only) | clip30 | 0.029 |
| clip5 | 0.000 (trailing `.`) | 3 | 0.056 |
| 2 | 0.000 (trailing `.`) | four | 0.000 (trailing `.`) |
| cny16 | 0.036 | quiet-long | 0.101 |
| quiet-short | 0.185 | car | 0.091 |

So the backends agree word-for-word on some clips (jfk/2/four/clip5 — the only difference is a
trailing full stop) and disagree by up to 18.5 % WER on others (quiet-short: Vulkan "tiles arrived
Tuesday … the invoice came to a hundred and twenty"; CPU "tails arrived Tuesday … one hundred and
twenty"). This is a genuine quantised-ggml backend numeric difference, and it is **deterministic**:
the same device gives the same bytes every time. The integration must not assume CPU/Vulkan
transcript parity; if it pins a model hash it should expect a small device-dependent WER spread, not
byte equality.

Two nuances to carry into the adapter:

- **CLI `--stream` and CLI batch produce the same final transcript** (verified on jfk, quiet-long,
  clip30: identical hashes). Both route through the streaming graph.
- The **C-ABI streaming** final differs from the **CLI batch** final on CPU (jfk trailing `.`; car
  "restore" vs "really store"; quiet-long/quiet-short word swaps) but is **identical to the CLI batch
  on Vulkan** for all four reference clips. Feeding granularity does *not* explain it (a whole-file
  C-ABI push on CPU reproduced the 160 ms-chunk result byte-for-byte), so the CLI's batch path uses a
  slightly different invocation of the graph on CPU. For the adapter, the C-ABI streaming path is the
  one that matters, and it is the one measured in §7.

## 7. Committed-delta stability (the core claim)

For each of the 10 clips × {Vulkan, CPU} the C-ABI harness recorded **every** interim the decoder
emitted (mid-word interims included, e.g. `"And so my fellow Ameri"` → `"And so my fellow
Americans"`). The metric shapes mirror the whisper-streaming report so the two are comparable.
Definitions, all computed from `data/partials/` and `data/analysis.jsonl`:

- **append-only (char) frac** — fraction of consecutive interim pairs where the previous transcript is
  a **string prefix** of the current one. 1.0 = the text never revises; it only grows.
- **rewritten pairs** — pairs where it is *not* a prefix (whisper.cpp's windows scored 0.29–1.0
  prefix agreement with many rewritten pairs).
- **monotone commits** — the number of final words already present, in order, never decreases.
- **adjacent dups** — repeated adjacent words in the committed text (whisper.cpp's seam duplication).
- **algorithmic lag** — `audio_processed_s` minus the last committed word's end time.

| clip | device | interims | append-only (char) | rewritten | monotone | adjacent dups | lag med s |
|---|---|---:|---:|---:|---|---:|---:|
| jfk | vulkan | 62 | 1.0 | 0 | True | 0 | 0.24 |
| jfk | cpu | 62 | 1.0 | 0 | True | 0 | 0.24 |
| quiet-short | vulkan | 70 | 1.0 | 0 | True | 0 | 0.24 |
| quiet-long | vulkan | 130 | 1.0 | 0 | True | 0 | 0.24 |
| car | vulkan | 146 | 1.0 | 0 | True | 0 | 0.24 |
| car | cpu | 146 | 1.0 | 0 | True | 0 | 0.24 |
| clip30 | vulkan | 183 | 1.0 | 0 | True | 0 | 0.24 |
| clip5 / 2 / 3 / four / cny16 | both | 15–61 | 1.0 | 0 | True | 0 | 0.16–0.24 |

**All 20 runs: `append_only_char_frac = 1.0`, `rewritten = 0`, `monotone = True`, `adjacent dups = 0`.**
The sweep adds 5 more runs (jfk at 80/160/320/560/1120 ms), all `1.0` / `0`. A committed text of
`"And so my fellow Americans ask not what your country can do for you. Ask what you can do for your
country"` was built one growing prefix at a time, never retracted.

**Failure modes, looked for and absent:**

- **Seam duplication** — none. There is no seam: the encoder maintains per-layer attention and
  convolution caches, frames are strictly non-overlapping, and the interim is a growing prefix. No
  adjacent duplicate word in any committed text (the failure whisper.cpp produced at every window
  boundary).
- **Cut-off hallucination** — none observed. The interim prefix grows monotonically; the model never
  invented a word at a "cut" and then removed it, because there is no cut — the final is a
  prefix-extension of the last interim in **all 20 runs** (exactly equal in **18**; in the other 2 it
  completes a trailing *partial word*, e.g. `bruised po` → `bruised potatoes`, which is not a
  revision). (Mid-word interims such as `Ameri` are an *incomplete* last token, not a revision; the
  completed word appears in the next interim.)
- **Silence hallucination** — none. On 10 s of digital silence the decoder emitted **0 interims and an
  empty final** on both Vulkan and CPU; the CLI printed an empty line. (Compare whisper.cpp: `medium`,
  `small`, and `large-v3-turbo` all committed `you you`, turbo `thank you thank you`.)

**One integration nuance, not a defect:** the interim's *last* token may be a partial word (`Ameri`).
ges2t's contract types committed words and keeps a pending tail; the adapter must commit only up to
the last **word/frame boundary** and treat the trailing partial as pending. The commitment is still
append-only at the completed-word level, which is exactly ADR 0011's contract.

**Commit policy tested:** the engine's native interim stream — `interim_results = true` on the C ABI
`RecognitionOptions`; every `stream_next` result with `is_final = false` is an interim, the single
`is_final = true` is the final. No ges2t-side two-pass rule was applied; nothing was de-duplicated.
Endpointing was left off (one final per clip).

## 8. Accuracy

Word-diff WER uses ticket 32's `word_diff` (case-folded, curly apostrophes dropped, punctuation a word
separator, a `.` kept only between digits) against the same references the whisper reports used; the
standard edit-distance WER is in `data/cli_runs.jsonl` (`wer_std`). Streaming WER is the WER of the
C-ABI final transcript; batch WER is the CLI's.

| clip | ref words | streaming WER (vk) | batch WER (vk) | batch WER (cpu) | whisper `medium q5_0` batch (vk, `-nt`) |
|---|---:|---:|---:|---:|---:|
| jfk | 22 | **0.000** | 0.000 | 0.000 | 0.000 |
| quiet-short | 27 | **0.259** | 0.259 | 0.296 | — |
| quiet-long | 36 | **0.167** | 0.167 | 0.194 | 0.153 |
| car (capture) | 64 | **0.312** | 0.312 | 0.234 | **0.062** (0.156 without `-nt`) |

- On Vulkan, **streaming and batch WER are identical** (the final equals the batch transcript).
- The model is **word-perfect on the clean jfk clip** and worse than whisper `medium` on the noisy
  real-device car clip (0.312 vs 0.062 with `-nt`, **0.156 with whisper's CLI default**) and on
  quiet-long (0.167 vs 0.153). This is the
  model, not the engine — the survey's published Parakeet-v3 Hungarian FLEURS is much higher (`15.72`
  vs this model's ≈ 29–33), but ticket 54 was obsolete and never measured v3 here, so that number is
  **published FLEURS only**, and Parakeet v3 is batch-only in every engine.
- The car failure is real mishearing: "northern valley and crews" → "Northern Ralley and cruise",
  "river crested at 3.2 meters" → "reverse crested it three point two meters", "ferry service" →
  "ferry service". A user who wants accuracy keeps `whisper_cpp`; this backend's value is the
  incremental text.
- **Hungarian is not locally measured.** There is no Hungarian clip with a human reference in the repo
  or scratch; recording one is out of scope (no microphone). Only the published **FLEURS `hu_hu` WER
  ≈ 28.7–33.4** (broad-coverage tier; Parakeet v3's is 15.72) can be reported. A user-supplied
  Hungarian sample + transcript is required to settle it — flagged in `001-comment.md`.

## 9. Confounds and limits

- **The session was not idle.** Load average ≈ 1.4–3.3 while the matrix ran (other agent processes, a
  browser, a running `ges2t` service). Every *timing* is therefore an upper bound and load-sensitive;
  RTF/wall numbers from different runs are comparable only in broad outline (the whisper report hit
  the same 2× spread). The **accuracy and append-only results are deterministic** (same bytes on
  repeat) and are not load-sensitive.
- **One machine, one driver.** All Vulkan statements are for RADV/Mesa 26.2.3 on the Radeon 780M
  (gfx1103). The vendor `amdvlk` driver and other GPUs are untested.
- **CPU vs Vulkan is not parity.** The transcripts differ deterministically (0.000–0.185 divergence);
  this is inherent to quantised ggml backends and is a fact the integration must carry (a device
  change can change the text). The comparison is like-for-like (same engine, model, clip, decode,
  chunk) but **not** numerically identical.
- **CLI batch vs C-ABI streaming differ on CPU** (identical on Vulkan). Per-clip accuracy for CPU in
  §5.1 is the CLI figure; the streaming final is in `data/analysis.jsonl`. Feeding granularity is not
  the cause (verified).
- **Single feed size in the matrix.** The primary streaming matrix pushes 160 ms per chunk; the
  chunk-size sweep covers all five documented steps but only on two clips (jfk reference-perfect;
  clip30 no reference).
- **No ground truth for 6 of 10 clips** (`clip5`, `clip30`, `2`, `3`, `four`, `cny16`); their WER
  columns are blank, exactly as the whisper reports left them. Their transcripts are still stability
  evidence.
- **`clip30`'s reference is absent**, so the car/jfk/quiet numbers carry the accuracy conclusion.
- **Peak RSS** is `ru_maxrss`; the CLI's figure includes its extra subsystems, so the CLI and C-ABI
  RSS are not the same measurement. The **within-shape Vulkan-vs-CPU ratio** is the meaningful part.
- **VRAM Δ** is a sysfs global delta, so concurrent GPU users (compositor, browser) add noise; the
  ~952 MB figure was stable across all Vulkan runs, and CPU's exact 0 is unambiguous.
- **Hungarian** is published-FLEURS only, not measured here.
- **Reuse fidelity.** The stability metric is computed from the engine's *interim strings* via ticket
  32's tokenise; the adapter's own boundary handling (committing up to a word boundary) is not
  exercised. The direction of the finding (append-only, no revision) does not depend on that.

## 10. What would falsify the central claim

The central claim is: **on this PC, on Vulkan, the streamed output is stable, append-only incremental
text — never revised, never duplicated at a seam, never invented at a cut, and silent on silence — at
every chunk size, while keeping up with real time.** It would be falsified by any of:

1. a run where an interim transcript is **not** a string extension of the previous one (any
   `append_only_char_frac < 1.0` or `rewritten > 0`), on any clip or chunk size;
2. **adjacent duplicate words** in a committed text (a seam re-commit) that the reference does not
   contain;
3. a **final that removes or rewrites already-shown words** (a late correction of committed text);
4. **non-empty text on digital silence** on either device;
5. **RTF ≥ 1** under real-time pacing (the engine falling behind the microphone), or a first-text
   latency so large the user would not see the point.

I found none of these: 20/20 stability runs (plus 5 sweep runs) had `1.0` / `0` / `True`, the final
equalled the last interim in 18 of 20 and extended it in 20 of 20, the silence control was empty on both devices, and paced RTF was
0.98–0.99 with ~0.85–1.16 s to first text. The claim is supported.

**What is *not* supported and must not be relayed as a win:** Hungarian accuracy (unmeasured), and any
claim of CPU/Vulkan transcript parity (false — they differ deterministically).

## 11. Answer and recommendation

**Answer.** The prebuilt NeMo-Speech.cpp Vulkan binary runs on this machine and uses the RADV iGPU,
proven by the engine's `backend=Vulkan0` line, a ~952 MB VRAM delta, and ~197 MB vs ~1,064 MB peak RSS
against CPU. It streams `nemotron-3.5-asr-streaming-0.6b` with **strictly append-only committed text**
at every measured chunk size, no revisions, no seam duplication, no silence hallucination, and ~45 ms
of work per 160 ms chunk with text appearing ~1 s into speech. The trade-offs are real: Vulkan costs
~950 MB of VRAM to save modest CPU time (CPU is already ~1.8× real-time), the CPU/Vulkan transcripts
differ, and the model's English accuracy is well below whisper `medium` on the noisy car clip, with
Hungarian unmeasured here.

**Recommendation for the integration tickets (Epic 6):**

1. **Proceed.** The core blocker (whisper.cpp's unstable windows) does not exist here: the cache-aware
   engine commits append-only by construction, so ADR 0011's 5 s sliding window genuinely does not
   apply (see the ADR 0011 amendment below).
2. **Default device: `vulkan` when `doctor` finds the device, else `cpu`** — never the reverse, since
   Vulkan is 5–10× lighter in host RAM. The engine's own `auto` already selects it; the adapter should
   surface the backend line in `doctor` and never silently fall back (ADR 0020).
3. **Pick the step by measurement, not guess.** 160 ms (rc = 1) gives ~1 s to first text at ~45 ms of
   work per chunk — ample headroom. If the user wants lower latency, 80 ms (rc = 0) costs ~21 ms/chunk
   and first text stays ~1 s; if throughput matters more than latency, 320–560 ms is safer on a loaded
   machine. Commit only at word boundaries and hold the trailing partial token as pending.
4. **Do not scope Hungarian as a win.** Ship it as a selectable model with the published FLEURS number
   documented; ask the user for one Hungarian clip + transcript to measure it before any accuracy
   claim. English users keep `whisper_cpp` for accuracy.
5. **Pin `1c8deae…` / `a5c435f2…` / 741,548,352 B** (engine-index revision) **and** the engine archive
   sha256 `c8d0ed5b…`; if the engine is upgraded, re-pin the model from its new index (the HF `main`
   pointer is a different commit).
6. **No `numpy`, no `torch`.** The C-ABI path is pure `ctypes`; nothing in the measured path needed
   `numpy`, and the engine's Python converter is not required. `pytest`/`mypy` stay clean.

## Draft ADR 0022 / ADR 0011 amendment

*(presented here for sign-off; nothing written to `docs/adr/`)*

### Draft ADR 0022 — Nemotron 3.5 streaming ASR as a third transcriber backend *(proposal)*

> **Status:** proposed (awaiting user sign-off) · **Date:** 2026-10-14 · **Related:** 0005, 0006, 0010,
> 0011, 0015, 0016, 0017, 0018, 0019, 0020, 0021
>
> **Context.** The user wants text committed **while speaking**, which the shipped `whisper_cpp`
> backend (ADR 0021) cannot do — it is batch-only. Ticket 53 found Parakeet v3 batch-only in every
> engine and selected **`NVIDIA/NeMo-Speech.cpp`**; the user chose the **cache-aware streaming
> sibling** `nvidia/nemotron-3.5-asr-streaming-0.6b` over Parakeet v3, accepting broad-coverage
> Hungarian. Ticket 55 measured the prebuilt Vulkan engine on this machine
> (`docs/project/research/nemotron-3-5-asr-streaming-measurement/REPORT.md`): the GPU path engages
> (backend line + ~952 MB VRAM + 197 MB vs 1,064 MB RSS), the streamed text is **append-only with zero
> revisions/duplications/hallucinations** across 10 clips × 2 backends and 5 chunk sizes, and ~45 ms
> of work per 160 ms chunk with ~1 s to first text.
>
> **Decision (proposed).**
> 1. Add **`nemo_speech`** as an opt-in `transcriber.engine`; the **default stays `whistle`**, and
>    `whisper_cpp` is unchanged.
> 2. Integrate **in-process via `ctypes` over `libnemo_speech_asr_c`** (the model resident from service
>    start), the ADR 0021 shape. No Python, no `torch`, no `numpy`.
> 3. The engine reports **`supports_streaming = True`** and its committed output is append-only; the
>    ADR 0011 contract (committed-only injection, typed watermark, trailing space once) is preserved,
>    with commitment at word boundaries and the trailing partial token held as pending.
> 4. **Device:** Vulkan when the binary and driver provide it, CPU otherwise; **never a silent
>    substitution** (ADR 0020). `doctor` reports the engine library, the model, the detected device,
>    and the effective backend.
> 5. **Pins (ADR 0017):** engine archive sha256 `c8d0ed5b…` (v0.2.0 `linux-x86_64-vulkan`); model
>    `nemotron-3.5-asr-streaming-0.6b.q8_0.gguf` sha256 `a5c435f2…`, size 741,548,352, revision
>    `1c8deae…`. `setup` **requires and never installs** prerequisites and never runs `sudo`.
> 6. **No runtime network** (ADR 0018): `setup` acquires and verifies the archive and the GGUF;
>    inference is offline.
> 7. **Licences:** engine Apache-2.0 (ggml MIT); model **OpenMDW-1.1**. Record attribution.
> 8. **Known limits stated in the docs:** Vulkan needs ~950 MB VRAM; CPU and Vulkan transcripts are
>    not byte-identical; English accuracy is weaker than whisper `medium` on noisy speech; Hungarian
>    accuracy is published-FLEURS only and unmeasured on this machine.
>
> **Consequences.** A third backend behind the ADR 0010 seam with no shipped default change; the repo
> gains no dependency, binary, weight, or audio; resident host cost ~200 MB (Vulkan) / ~1 GB (CPU); the
> user gains incremental text at a documented accuracy cost. It requires the ADR 0011 amendment below.

### Draft ADR 0011 amendment — the 5 s sliding window does not apply to a cache-aware streaming engine *(proposal)*

> **Status:** proposed (awaiting user sign-off) · **Date:** 2026-10-14 · **Amends:** ADR 0011
>
> **Context.** ADR 0011's sliding-window commit cadence exists *because* the Needle/Whistle engine
> re-transcribes a growing buffer, so the window must cap the buffer and a two-pass agreement rule must
> decide what is stable. Ticket 55 measured the Nemotron 3.5 cache-aware streaming engine and found the
> engine itself commits **append-only**: the encoder keeps per-layer attention/convolution caches, each
> processed frame is strictly non-overlapping, and every interim transcript is a **string-prefix
> extension** of the previous one — 1.0 across 20 runs and 5 chunk sizes, with no seam duplication and
> no silence hallucination (report §7).
>
> **Decision (proposed).** For a `Transcriber` whose engine reports cache-aware streaming
> (`supports_streaming = True` **and** an append-only interim contract proven by the engine's own
> output):
> 1. **Retire the 5 s `window_seconds` and the two-pass agreement rule** — they are unnecessary: there
>    is no growing buffer to cap and nothing for a second pass to confirm.
> 2. **Preserve the contract exactly:** committed text is append-only and never revised; it is the only
>    text injected; the pending/trailing tail is display-only and never injected; the trailing space is
>    emitted once (tickets 32–34).
> 3. **Commit at word boundaries:** the engine's interim may end in a partial word; the adapter commits
>    only the completed-word prefix and keeps the partial token pending.
> 4. This amendment is **scoped to such engines**; the Whistle path keeps ADR 0011's sliding window
>    unchanged.
>
> **Consequences.** The engine named in ADR 0022 streams with no window cap and no de-duplication step,
> removing the entire class of seam errors the whisper-streaming report documented. The `window_seconds`
> config key becomes optional/ignored for this engine (validation must say so, not silently accept it).

## Reproduction and cleanup

Run from the worktree (all commands bound by `timeout`; no network during measurement, no `sudo`, no
microphone):

```bash
bash docs/project/research/nemotron-3-5-asr-streaming-measurement/run_matrix.sh cli
bash docs/project/research/nemotron-3-5-asr-streaming-measurement/run_matrix.sh stream-vk
bash docs/project/research/nemotron-3-5-asr-streaming-measurement/run_matrix.sh stream-cpu
PYTHONPATH=src python3 docs/project/research/nemotron-3-5-asr-streaming-measurement/analyze_streaming.py \
  --partials "$HOME/.cache/ges2t-discovery/nemotron-3-5-asr-streaming-measurement/partials" \
  --out "$HOME/.cache/ges2t-discovery/nemotron-3-5-asr-streaming-measurement/analysis.jsonl"
PYTHONPATH=src python3 docs/project/research/nemotron-3-5-asr-streaming-measurement/render_report_data.py
```

The committed per-partial traces let a reviewer re-derive the append-only result **from the repo
alone** (no scratch dir):

```bash
R=docs/project/research/nemotron-3-5-asr-streaming-measurement
PYTHONPATH=src python3 $R/analyze_streaming.py --partials $R/data/partials --out /tmp/re_analysis.jsonl
diff <(sort $R/data/analysis.jsonl) <(sort /tmp/re_analysis.jsonl)   # identical (25 rows)
PYTHONPATH=src python3 $R/analyze_streaming.py --partials $R/data/sweep --out /tmp/re_sweep.jsonl
# every sweep trace: append_only_char_frac = 1.0, rewritten_char_pairs = 0
```

The scratch dir (`~/.cache/ges2t-discovery/nemotron-3-5-asr-streaming-measurement/`) holds the engine
tarball + extracted tree, the GGUF, the full per-partial JSON with word lists, and the sliced WAVs. It
is left tidy; nothing under it is committed. No model, binary, or audio file is in the repo. No
`numpy` dependency or import exists anywhere in the project or the measured path (`grep -r numpy`
finds only the project's own "numpy is deliberately absent" prose and its guard test).
