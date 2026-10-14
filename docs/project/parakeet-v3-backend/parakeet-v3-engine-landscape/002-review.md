# 002-review — ticket 53 (Parakeet v3 engine landscape)

- **Reviewer:** independent (did not author the report; no implementation or report edits made)
- **Date:** 2026-10-14
- **Branch reviewed:** `feat/parakeet-v3-engine-landscape` @ `d60e057`
- **Diff range:** `git diff main...feat/parakeet-v3-engine-landscape` (three-dot; `main` @ `2838335`)
- **Verdict:** **changes requested** — one verifiably false machine-state claim, confined to the
  from-source fallback parenthetical. Every central claim (engine, Vulkan tarball, official GGUF,
  batch-only verdict, streaming sibling, model-choice escalation) **held up under independent
  re-fetch**. The gate decision ("PROCEED") and the integration recommendation are unaffected.

---

## Headline: what I re-fetched and what held

I re-fetched every primary source myself with `curl` (documentation/metadata only; no engine, model,
or audio bytes downloaded). The five consequential claims the task names:

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| a | `NVIDIA/NeMo-Speech.cpp` exists, Apache-2.0, publishes `nemo-speech-0.2.0-linux-x86_64-vulkan.tar.gz` = **16,618,142 B**, sha256 **c8d0ed5b…** | **CONFIRMED exactly** | GitHub API + release asset list + `SHA256SUMS` (below) |
| b | `nvidia/parakeet-tdt-0.6b-v3` publishes the **official** `parakeet-tdt-0.6b-v3.q8_0.gguf` = **713,975,456 B** at rev `541d1f99…`, licence **CC-BY-4.0**, lists **Hungarian** | **CONFIRMED exactly** | HF API + HF tree + `x-linked-etag` headers (below) |
| c | `nvidia/nemotron-3.5-asr-streaming-0.6b` exists, cache-aware streaming, covers `hu-HU`, licence **OpenMDW-1.1** | **CONFIRMED** | HF API + model card (below) |
| d | **No engine** can stream Parakeet v3 | **CONFIRMED** | real `recognizer.cpp` throw + `docs/cli.md` + `parakeet.cpp` README + sherpa-onnx example (below) |

The only claim that **did not hold up** is a secondary machine-state assertion (ninja), detailed under
§"Blocking finding".

---

## Evidence — exact commands and real output

### (a) NeMo-Speech.cpp repo, licence, release asset, sha256

```
$ curl -sS "https://api.github.com/repos/NVIDIA/NeMo-Speech.cpp" | python3 -c '...'
full_name: NVIDIA/NeMo-Speech.cpp
license: Apache-2.0   language: C++   created_at: 2026-07-15T10:46:56Z
pushed_at: 2026-10-13T04:19:58Z   stars: 178

$ curl -sS "https://api.github.com/repos/NVIDIA/NeMo-Speech.cpp/releases"   # v0.2.0 published 2026-10-02
  nemo-speech-0.2.0-linux-x86_64-vulkan.tar.gz 16618142  .../releases/download/v0.2.0/nemo-speech-0.2.0-linux-x86_64-vulkan.tar.gz
  nemo-speech-0.2.0-windows-x86_64-vulkan.zip  19302464
  nemo-speech-0.2.0-linux-x86_64-cpu.tar.gz     6683477
  nightly (published 2026-10-12) also present

$ curl -fL ".../releases/download/v0.2.0/SHA256SUMS"
c8d0ed5b9ac5fb6d5c460015ba38ea217415c44edc1e6d49120646781009aee6  nemo-speech-0.2.0-linux-x86_64-vulkan.tar.gz
```
→ **Size 16,618,142 B and sha256 `c8d0ed5b…aee6` match the report byte-for-byte.** The Windows
`…-vulkan.zip` sibling also exists, as the report says.

### (b) Model identity — HF API, tree, and LFS etags at the pinned revision

```
$ curl -sS "https://huggingface.co/api/models/nvidia/parakeet-tdt-0.6b-v3"
sha: 541d1f99c6b0c3cd0b11a95167540bb8edefd82b   lastModified: 2026-08-05T21:46:17Z
cardData.license: cc-by-4.0
language (25): en es fr de bg hr cs da nl et fi el hu it lv lt mt pl pt ro sk sl sv ru uk      # "hu" present
12 files; no .onnx among them

$ curl -sS ".../tree/main?recursive=true&blobs=true"
    2508311120  model.safetensors
    2509332480  parakeet-tdt-0.6b-v3.nemo
     713975456  parakeet-tdt-0.6b-v3.q8_0.gguf          # matches report
       1159960  tokenizer.json
          1153  config.json   392 processor_config.json   289 generation_config.json

$ curl -sS -I -L ".../resolve/541d1f99.../parakeet-tdt-0.6b-v3.q8_0.gguf"   # LFS etag = sha256
x-linked-size: 713975456
x-linked-etag: "e3880d0aaaaf2c308ea2c35016b2b895c423eb3fda924c1b463d1c19b7f4d32e"
# model.safetensors -> "3a2026366188c8c68598edbbff92f8d11590a08e0ae2e6775544e7b07d6a5e11"
# .nemo             -> "3cbdc85877e668ca7b82d0d56770eb1fac76691f55d6b97545e8d61ca588d10d"
```
→ Revision, licence, 25 languages (incl. Hungarian), ALL artifact sizes, and ALL three LFS sha256s
match the report **exactly**. Repo total 5,732,780,850 B ≈ 5.73 GB, as stated.
Model card `README.md`: `value: 15.72` for `hu_hu` Test WER, `GOVERNING TERMS: … CC-BY-4.0`,
"automatically detects the language", "600-million-parameter", 25-language list verbatim.

### (c) Streaming sibling

```
$ curl -sS "https://huggingface.co/api/models/nvidia/nemotron-3.5-asr-streaming-0.6b"
sha: ea30d66debe3740a08b573244286791d423d6b3e
cardData.license: other    license_name: openmdw-1.1    language includes: hu

$ curl -sS -I -L ".../resolve/ea30d66d.../nemotron-3.5-asr-streaming-0.6b.q8_0.gguf"
x-linked-size: 742090464
```
→ Card says "**40 language-locales**", "configurable chunk sizes, including 80ms, 160ms, 320ms,
560ms, and 1120ms", "**Cache-Aware FastConformer-RNNT** … there are no overlapping computations; each
processed frame is strictly non-overlapping". Hungarian is in the **broad-coverage** tier; its FLEURS
row reads `28.68 … 33.40` → report's "≈ 28.7–33.4" is exact.
→ `models/index.json` (fetched raw) pins `nemotron-3.5-asr-streaming-0.6b` at `1c8deae…`, licence
"OpenMDW 1.1", q8_0 `741548352`, sha256 `a5c435f2…`, exactly as the report's drift note says; and
`defaults.asr` **is** that model. The English sibling is `699872960` / `d9a01898…`, also exact.

### (d) The batch-only verdict is real — read the source, not the quote

```
$ curl -fL ".../NVIDIA/NeMo-Speech.cpp/main/src/asr/recognizer.cpp" | sed -n '220,225p'
    if (model_->head_kind() != HeadKind::Ctc) {
        auto* transducer = static_cast<RnntModel*>(model_.get());
        if (!transducer->supports_cache_streaming())
            throw std::runtime_error(
                "this transducer encoder is offline-only and cannot serve StreamingRecognize");
```
The throw is at `recognizer.cpp:224`, exactly as cited. `docs/cli.md:65`:
> Offline-only models such as Parakeet TDT reject `--stream` and `--live`.

`mudler/parakeet.cpp` README: `--stream` line 181 is "cache-aware streaming (**EOU and Nemotron
models**)". `sherpa-onnx` `nemo-parakeet-c-api.c` uses `SherpaOnnxCreateOfflineRecognizer` +
encoder/decoder/joiner int8 ONNX; `parakeet-tdt-simulate-streaming-microphone-cxx-api.cc` creates a
**new** `OfflineStream` per VAD segment and calls `recognizer.Decode` on the whole segment.
→ **No engine streams Parakeet v3.** Confirmed independently.

### Engine / dependency / Vulkan inventory spot-checks (all matched)

- ONNX Runtime EP docs: `vulkan` occurrences = **0**; ROCm 6, MIGraphX 6, CUDA 11 → "no Vulkan EP" ✓.
- `pypi/onnxruntime`: **1.31.0**, MIT, `requires_dist` includes `numpy>=1.21.6` ✓.
- `pypi/sherpa-onnx`: **1.13.8**, `requires_dist = ['sherpa-onnx-core==1.13.8']`;
  `sherpa-onnx-core` **1.13.8**, Apache-2.0, `requires_dist = None`, manylinux2014_x86_64 wheel
  **10,642,497 B** ✓.
- sherpa-onnx repo tree (`git/trees/master?recursive=1`, 8,675 paths): **0** paths contain `vulkan`,
  **9** contain `cuda` ✓.
- `mudler/parakeet.cpp`: MIT, C++, **841** stars, pushed 2026-10-14; README "C++17/ggml port … needs
  no Python at inference time"; `include/parakeet_capi.h` ABI **10**; `PARAKEET_DEVICE=cpu` /
  `Vulkan1`; "Ops that a backend lacks run on the CPU" ✓.
- `CrispStrobe/CrispASR`: MIT, **750** stars, pushed 2026-10-14; "**119 backends**"; prebuilt
  `crispasr-linux-x86_64-vulkan.tar.gz`; `-vulkan` builds "**do not fall back to CPU**"; parakeet v3
  "FastConformer + TDT | 25 EU (auto-detect) | CC-BY-4.0"; "started as a fork of whisper.cpp" ✓.
- NeMo-Speech.cpp `CMakePresets.json`: `vulkan-asr`, `vulkan-diar`, `vulkan-tts`, `vulkan-nmt`,
  `vulkan-speech`, `vulkan-server` ✓. `docs/build.md` "Build outputs" lists `libnemo_speech_asr_c` ✓.
  README has `find_package(NeMoSpeech REQUIRED COMPONENTS ASR)` ✓. README RT table: **27 ms / 6× CPU**,
  **2.3 ms / 67× RTX 4090** ✓.
- `tools/check_backend_coverage.cpp` exists; its header comment is a CPU-fallback diagnostic ✓.
- `NVIDIA-NeMo/NeMo` chunked streaming script path resolves (HTTP 200) ✓.

### Citations — every URL in `data/sources.md` resolves

```
$ python3 <extract urls from data/sources.md> ; curl -sS -o /dev/null -w '%{http_code}' -L <url>
200 huggingface.co/nvidia/parakeet-tdt-0.6b-v3
200 huggingface.co/api/models/nvidia/parakeet-tdt-0.6b-v3
200 huggingface.co/api/models/nvidia/parakeet-tdt-0.6b-v3/tree/main?recursive=true&blobs=true
200 huggingface.co/nvidia/parakeet-tdt-0.6b-v3/resolve/main/config.json
200 huggingface.co/nvidia/parakeet-tdt-0.6b-v3/resolve/main/processor_config.json
200 huggingface.co/nvidia/parakeet-tdt-0.6b-v3/resolve/main/README.md
200 huggingface.co/api/models?search=parakeet-tdt-0.6b-v3&limit=100
200 github.com/NVIDIA/NeMo-Speech.cpp
200 raw.githubusercontent.com/NVIDIA/NeMo-Speech.cpp/main/README.md
200 .../main/docs/cli.md
200 .../main/docs/build.md
200 .../main/models/index.json
200 .../main/src/asr/recognizer.cpp
200 .../main/app/transcribe.cpp
200 .../main/src/asr/runner.cpp
200 .../main/tools/check_backend_coverage.cpp
200 .../main/CMakePresets.json
200 github.com/NVIDIA/NeMo-Speech.cpp/releases/download/v0.2.0/SHA256SUMS
200 api.github.com/repos/NVIDIA/NeMo-Speech.cpp/releases
200 huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b
200 huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b/raw/main/README.md
200 huggingface.co/api/models/nvidia/nemotron-3.5-asr-streaming-0.6b
200 github.com/mudler/parakeet.cpp
200 github.com/CrispStrobe/CrispASR
200 onnxruntime.ai/docs/execution-providers/
200 pypi.org/pypi/onnxruntime/json
200 pypi.org/pypi/sherpa-onnx/json
200 pypi.org/pypi/sherpa-onnx-core/json
200 github.com/k2-fsa/sherpa-onnx
200 raw.githubusercontent.com/k2-fsa/sherpa-onnx/master/c-api-examples/nemo-parakeet-c-api.c
200 raw.githubusercontent.com/k2-fsa/sherpa-onnx/master/cxx-api-examples/parakeet-tdt-simulate-streaming-microphone-cxx-api.cc
```
**32/32 resolve; 0 dead links.** Each cited page (spot-checked line-by-line above) says what the
report attributes to it. `runner.cpp` contains `OfflineRunner` and `CacheStreamRunner`;
`transcribe.cpp` wires `--stream` and `--live`; `config.json` is `ParakeetForTDT`, 24 layers, hidden
1024, 8 heads, 128 mel, durations `[0,1,2,3,4]`, vocab implied 8193.

### Local device inventory (read-only)

```
$ date; nproc; free -g; /usr/bin/python3 --version
Wed Oct 14 11:07:56 AM CEST 2026
16
               total        used        free      shared  buff/cache   available
Mem:              27          10           5           1          12          16
Python 3.14.7

$ vulkaninfo --summary
Vulkan Instance Version: 1.4.357
	deviceName  = AMD Radeon 780M Graphics (RADV PHOENIX)
	driverName  = radv
	driverInfo  = Mesa 26.2.3-arch1.1

$ pacman -Q cmake shaderc vulkan-headers spirv-headers
cmake 4.4.4-1   shaderc 2026.3-1   vulkan-headers 1:1.4.363.0-1   spirv-headers 1:1.4.363.0-1
```
nproc 16, Python 3.14.7, Vulkan instance 1.4.357 / apiVersion 1.4.354 / RADV PHOENIX / Mesa
26.2.3-arch1.1 — all match the report. `free -g` "17 available" is not exactly reproducible (live
memory pressure; I read **16**); total 27 matches. Ephemeral, non-material.

### Boundaries and hygiene

```
$ git diff main...HEAD --name-status
M docs/project/README.md
A docs/project/parakeet-v3-backend/parakeet-v3-engine-landscape/001-comment.md
M docs/project/parakeet-v3-backend/parakeet-v3-engine-landscape/ticket.md
A docs/project/research/parakeet-v3-engine-landscape/REPORT.md
A docs/project/research/parakeet-v3-engine-landscape/data/sources.md

$ git diff main...HEAD --name-only -- src/ tests/     # (empty)
$ git diff main...HEAD --name-only -- docs/adr/       # (empty)
$ git diff main...HEAD --name-only | grep -iE '\.(gguf|onnx|bin|so|wav|mp3|flac|nemo|safetensors|pt|ckpt|tar|gz|zip)$'   # (none)
$ git status --porcelain                              # (empty)

$ python3 <orchestrator-state §10 link check>
0 broken references

$ timeout 300 uv run pytest -q
1230 passed in 10.42s
$ timeout 300 uv run mypy src/ges2t tests
Success: no issues found in 65 source files
$ git status --porcelain                              # (still empty)
```
The ticket-53 scratch dir `~/.cache/ges2t-discovery/parakeet-v3-engine-landscape/raw/` contains only
`.md` / `.json` / `.cc` / `.cpp` notes — no binaries, no weights, no audio (`find … -type f` listed
only text). The report's claim that nothing was built or run is consistent with that (and with §9/§11).

---

## Blocking finding

### F1 — false machine-state claim about `ninja` (REPORT.md §7 and 001-comment.md)

Both artifacts assert the from-source fallback needs no new package beyond SentencePiece because
`cmake`/`ninja` are already present. **`ninja` is not installed on this machine.** `cmake` and
`glslc` (via `shaderc`) are.

Report §7:
> The environment table … already records `vulkan-headers`, `spirv-headers`, `shaderc`, `cmake` as
> installed, so a **source** Vulkan build would not need a new package **except** `sentencepiece` …
> … the exact line to escalate is `sudo pacman -S sentencepiece cmake ninja` — **cmake/ninja are
> already present**, so in practice `sentencepiece` alone.

001-comment.md:
> Only a *from-source* build would need `sudo pacman -S sentencepiece` (**cmake/ninja/glslc/spirv-headers
> already present**).

Verification:
```
$ command -v ninja ; pacman -Q ninja ; pacman -Qs ninja
(not found)
error: package 'ninja' was not found
(no output; exit 1)
$ ls .venv/bin/ninja
No such file or directory
$ command -v samu ; find ~/.local /usr/local/bin -maxdepth 3 -name 'ninja*'
(no samu; nothing)
$ command -v cmake glslc
/usr/bin/cmake
/usr/bin/glslc        # owned by shaderc 2026.3-1
```
`docs/build.md` itself lists "CMake 3.26 or newer **and Ninja**" as a build prerequisite. The
sentence is also self-contradictory: it names `ninja` in the `pacman -S` line and then says ninja is
already present.

**Why it matters:** rule 9 ("a doc that lies is worse than no doc") and the DoD's explicit requirement
that the comment "flag … a required `sudo pacman -S …` line". If the user picked the from-source
fallback, the escalation list would be missing `ninja`. The **recommended** path (the prebuilt Vulkan
tarball) needs no build tooling at all, so the gate decision and the ticket-54 plan are **unaffected**
— this is a contained, 2-minute correction, not a re-think.

**Required fix:** drop/correct the "cmake/ninja are already present … sentencepiece alone" claim in
report §7 and the matching parenthetical in 001-comment.md; state that a from-source build additionally
needs `ninja` (and `sentencepiece`) installed. (The `sudo pacman -S sentencepiece cmake ninja` command
line itself is already complete; only the "already present" follow-up is wrong.)

**Minor, non-blocking:** the ticket's own DoD checkboxes are all still `- [ ]` although the status is
`in review`. The underlying items are all evidenced (below); tick them (or let the orchestrator) —
this is bookkeeping, not a defect in the artifacts.

---

## Per-criterion findings against the ticket's Definition of done

| DoD item | Verdict | Evidence |
|---|---|---|
| **1. Model identity** — repo, revision, licence, params, per-artifact byte sizes, architecture, languages incl. Hungarian, official ONNX (deny) | **pass** | HF API `sha 541d1f99…`, `cc-by-4.0`, 25 languages incl. `hu`; tree sizes 2,508,311,120 / 2,509,332,480 / 713,975,456 match; `x-linked-etag` sha256s match; `config.json` `ParakeetForTDT` 24L/1024/8/128/durations[0..4]; card FLEURS `hu_hu` 15.72; repo has no `.onnx` |
| **2. Engine inventory** — NeMo, ORT (all Linux EPs), sherpa-onnx, any ggml port; per-engine linkage + transitive deps + numpy question + licence + CPU/GPU | **pass** | §3.1–3.7 verified above; ONNX RT no Vulkan EP + `numpy>=1.21.6`; sherpa no-numpy core wheel 10,642,497 B; `parakeet.cpp` and `CrispASR` both real (MIT, ggml) — "do not assume none exists" answered |
| **3. Vulkan verdict per engine for 780M/RDNA3/RADV, with evidence + fallback cost** | **pass** | Per-engine table with reasons; device inventory re-confirmed (`vulkaninfo --summary`); architectural reason (Vulkan=ggml) correct; fallback (CPU/ROCm-MIGraphX) recorded. NeMo-Speech.cpp is marked "Yes (expected)" and honestly withheld from "measured" (§9) |
| **4. Incremental/streaming capability + explicit v3-vs-sibling answer + ADR 0011 implications** | **pass** | `recognizer.cpp:224` throw + `cli.md:65` restriction re-read; sibling card is cache-aware (80–1120 ms), FLEURS hu ≈ 28.7–33.4 vs 15.72; §5 notes it retires the 5 s window for that engine (design input to 32–34) |
| **5. Integration cost (top 1–2 engines), size XS–XL, vs whisper.cpp** | **pass** | §6 enumerates adapter, `identifiers.py`, model store, `setup`, `doctor`, RSS, load; size **M**; honest "no easier than whisper.cpp" |
| **6. Stage-2 measurement plan** — scratch dir outside repo, artifacts+sizes, exact commands, metrics | **pass** | §7 gives the scratch path, both URLs + sha256 verify lines, `timeout`-bounded CPU/Vulkan transcribes, and the metric list (latency/RTF/RSS/`word_diff` WER/byte-compare; streaming delta-stability) |
| **7. Recommendation + draft ADR verbatim, nothing written to `docs/adr/`** | **pass** | §8 recommends `NVIDIA/NeMo-Speech.cpp` + v3 q8_0, with a full draft ADR 0022 in the report; `docs/adr/` unchanged (empty diff) |
| **`001-comment.md` gate decision (proceed/stop) + 2–3 deciding facts + escalation** | **pass** (with F1 caveat) | Comment decides **PROCEED** with three deciding facts and escalates the model-choice (A/B/C), the CC-BY-4.0 attribution, and the from-source `pacman` line — but that from-source line's "ninja already present" aside is the F1 error |
| **No `src/ges2t/` or `tests/` modified** | **pass** | `git diff main...HEAD -- src/ tests/` empty |
| **No engine/model/weight/audio committed; scratch outside repo and named** | **pass** | name-filtered diff empty; scratch dir holds only text notes; path named in report header/§7/§11 |
| **Link check 0 broken; `git status` clean** | **pass** | reproduction of the §10 snippet → `0 broken references`; `git status --porcelain` empty (before and after running the suite) |
| **Everything committed on the branch; ticket + README row updated** | **pass** | commit `d60e057`; `ticket.md` status `in review`; `docs/project/README.md` row `in review` |

---

## Internal consistency and honesty

- **No fabricated measurement.** The report states in the header, §9 and §11 that **no engine was
  built or run**, and every Vulkan/streaming/latency line is explicitly documentary or device
  inventory. I swept every `measur*` occurrence: all either describe a *published* number, a
  *planned* ticket-54 measurement, or the explicit disclaimer. Nothing reads as a ges2t measurement.
- **Confounds present and true:** no engine run; one machine/driver; nemotron revision drift
  (HF `ea30d66d` vs engine pin `1c8deae`) — both sides re-verified; accuracy is published FLEURS, not
  reference truth; community ports read not run.
- **What would falsify** (§10) is present and specific (binary won't run on RADV / CPU-fallback ops;
  C ABI can't stay resident; `numpy` required anywhere; sibling Hungarian unusable; licence refusal).
- **"Safe to relay?"** — present in `001-comment.md`. My independent read: **yes for the
  engine/Vulkan/dependency/streaming findings** (all documentary + device inventory, now
  independently re-verified); **no measured Vulkan/accuracy number exists yet** — correct. One
  correction: the from-source `sudo pacman -S` guidance must add `ninja` (F1) before relay.
- **One spelling nuance (non-blocking):** §6's illustrative constant is written
  `ENGINE_NEEMO_SPEECH` (double-E) for `"nemo_speech"`; when the real constant is added it should be
  `ENGINE_NEMO_SPEECH` per the Epic-4 / `identifiers.py` convention.

---

## What would falsify the central claim (and what would falsify *this review*)

Central claim: *NeMo-Speech.cpp on Vulkan is the right engine for v3, and v3 is batch-only.* Falsified
by (as §10 already says): (1) the prebuilt Vulkan binary crashes on RADV or `check_backend_coverage`
shows CPU-fallback ops making it no faster than CPU; (2) the C ABI cannot keep the model resident /
cannot feed 16 kHz mono float32 in-process; (3) `numpy` turns out required on the chosen path;
(4) the streaming sibling's Hungarian is unusable; (5) the CC-BY-4.0 attribution is unacceptable.
This review's own verdict would be falsified if the asset sizes/sha256s, the GGUF LFS etag, the licence
fields, or the `recognizer.cpp` throw differed on re-fetch — they did not.

**Safe to relay to the user?** For the engine/Vulkan/dependency/streaming findings: **yes**, subject
to fixing F1. For any *measured* performance/accuracy number: **no — none was produced here.**

---

## Could-not-verify (each reason checked true)

1. **Whether the prebuilt Vulkan binary actually offloads the offline v3 TDT graph on this RADV
   device.** Not attempted: running/building an engine is ticket 54's scope and was explicitly
   out of scope. The report flags this as its own residual unknown; `recognizer.cpp`'s comment
   ("… until the offline graph has Vulkan parity coverage; CTC and offline-only TDT remain offline")
   makes it a genuine open question, not a formality.
2. **`check_backend_coverage` output for the offline TDT graph.** Not run (no engine, by scope).
3. **Whether `nemo_toolkit[asr]` / HF Transformers install trees pull `numpy`+`torch` exactly.**
   Inferred from PyPI/`requires_dist` and upstream docs, not by resolving the full trees; the two
   paths are rejected anyway, and the report labels them "not viable" on that basis.
4. **The report's §3.2 characterisation of NeMo's chunked streaming as overlapping re-decode.**
   The script path resolves (HTTP 200), but I did not read/execute it.
5. **Self-containedness of the prebuilt archive and its "no Python at inference" runtime.** From
   `docs/build.md`/README, not by unpacking the binary (that would be an engine download).
6. **`parakeet.cpp` / `CrispASR` Vulkan and parity claims.** From their READMEs, not run — the report
   says the same.
7. **`free -g` "17 available".** Ephemeral; I read 16. Total (27) matches; non-material.
8. **`ninja` "already present".** Checked and **false** — see F1 (this is a refutation, not a
   could-not-verify).

---

## Risks and recommended follow-ups

1. **(blocking, this ticket) Fix F1** — correct the `ninja` claim in REPORT.md §7 and 001-comment.md.
2. **(ticket 54)** Confirm offline-TDT Vulkan coverage with `nemo-speech doctor` +
   `tools/check_backend_coverage` + a CPU-vs-Vulkan timing, as the report's §10/§7 already demand.
3. **(ticket 54)** Re-pin the streaming sibling: HF `main` is `ea30d66d` / `742,090,464`; the engine
   index pins `1c8deae` / `741,548,352`. Pick one and pin sha256+size (ADR 0017).
4. **(orchestrator → user)** Surface the model-choice escalation (A: v3 batch / B: Nemotron-streaming
   incremental / C: both) before ticket 54 commits to a model — v3 alone does **not** fix the
   incremental-text pain that motivated the epic.
5. **(eventual ADR 0022)** Record CC-BY-4.0 attribution for the v3 weights in the README (report §8.7
   already proposes this).
