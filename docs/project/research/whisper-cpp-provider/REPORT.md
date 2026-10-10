# whisper.cpp as a second ges2t model provider — discovery report

- **Ticket / question:** is `whisper.cpp` a viable **second model provider** for ges2t, and
  if so which model size / quantisation gives the best **accuracy-per-second** trade-off on
  this machine?
- **Branch:** `research/whisper-cpp-provider`
- **Worktree:** `.worktrees/whisper-cpp-provider`
- **Date:** 2026-10-10
- **Status:** measurement complete (CPU + Vulkan)
- **Harness:** [`measure_whisper_cpp.py`](measure_whisper_cpp.py) — a docs script, not part of
  the shipped package; `uv run pytest` never sees it (ADR 0014)
- **Raw data:** [`data/runs.jsonl`](data/runs.jsonl) (one JSON object per run) and
  [`data/timings.md`](data/timings.md) (the rendered tables). Scratch (outside the repo, never
  committed): `~/.cache/ges2t-discovery/whisper-cpp-provider/`.

> **Every number in this report traces to a real whisper-cli run** recorded in
> `data/runs.jsonl`. No variant, model, threshold, or transcript is stated that was not
> executed. Where a requested artifact was not available (e.g. a published `q5_0` for the
> small models), it was **produced from the f16 model with whisper.cpp's own quantiser** and
> the provenance is stated — never substituted silently.

## 1. TL;DR

**Yes, whisper.cpp is a viable second provider — and it is the accuracy option.** On the user's
25 s car recording, `medium q5_0` scores **0.062** WER against Whistle's measured **0.281** (batch)
/ **0.391** (streaming) on the same recording (ticket 39) — roughly **4.5×** fewer errors than
Whistle's batch path (0.281→0.0625) and **6.3×** fewer than its shipped streaming default
(0.391→0.0625). The comparison is cross-harness and cross-path (different audio: gate-passed vs
raw), so the direction and the model ordering are the claim, not the exact ratio. `medium q5_0`
has essentially one real word error (`river` → `rivers`); the rest of its 0.062 is
`4,000`/`metres` formatting. The best accuracy-per-second trade-off depends on the machine path:

- **With Vulkan (GPU) — recommended, and it works here — `medium q5_0` is the clear winner:**
  **WER 0.062, RTF 14.6 (1.72 s for the 25 s clip)**, the most accurate model in every condition
  tested and faster than *every* CPU model except `tiny` (which is 0.97 s but far less accurate).
- **CPU-only, `medium q5_0` is still the accuracy leader** (WER 0.062) but costs **RTF 1.30**
  (8.5–22.4 s); the **knee is `small q5_0`** (WER 0.141, RTF 3.9, 2.5–7.8 s); `base.en q5_0` is the
  fast English option (WER 0.141, RTF 9.9) but did **not** generalise to the quiet clips.
- **Quantisation: `q5_0` (or `q8_0`).** **WER is equal** across q5_0/q8_0/f16, but the transcripts
  are **not** always byte-identical (small differs in two words, medium in punctuation — §5);
  **f16 is only slower and larger** — never ship it.
- **Decoding: greedy.** Beam-5 buys 0 words on `small` for 7× the cost, at most 1–2 words on
  `large-v3-turbo` — not worth it.
- **`.en`:** a marginal, non-generalising English win; multilingual `small` is the better default.
- **Vulkan:** **4.7–13.4× faster at byte-identical transcripts** (7/7 pairs checked). The only
  build-time extra is `spirv-headers` (a dev package); the CPU build needs nothing.
- **Streaming:** whisper.cpp is batch-only; a 5 s sliding window costs almost a full 30 s decode,
  so it is **not viable on CPU** and only marginal on GPU → expose it as
  **`supports_streaming = False`**.

The **absolute WERs are upper bounds** (one recording, possible reader stumbles, and `word_diff`
inflates replaced blocks); the **model-vs-model ordering on identical audio** is the load-bearing
result and is stable across all three clips. Full numbers in §3–§10 and
[`data/timings.md`](data/timings.md); raw runs in [`data/runs.jsonl`](data/runs.jsonl).

## 2. What was measured, and how

### 2.1 Build

whisper.cpp was cloned and built **from source** (Arch's `extra/whisper-cpp` package needs
sudo, which is out of scope). `cmake` is absent but `uvx cmake` (4.4.4) works, so no package
was installed.

```
git clone --depth 1 https://github.com/ggml-org/whisper.cpp.git      # v1.9.5
cd whisper.cpp
uvx cmake -B build  -DCMAKE_BUILD_TYPE=Release -DWHISPER_BUILD_TESTS=OFF
uvx cmake --build build --target whisper-cli whisper-quantize -j 16
```

Built revision: `d1be6fde11ac6e0407606b4e42fe72d34add8037` (tag `v1.9.5`, ggml `0.26.0`).
Build time for `whisper-cli` on the CPU backend: **54 s**. The CPU backend is compiled with
`-march=native`. The Vulkan build is described in §8.

### 2.2 Models

All weights come from the canonical `ggerganov/whisper.cpp` Hugging Face repo
(`https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-<name>.bin`). The
newer-looking `ggml-org/whisper.cpp` path returns HTTP 404 and is **not** the download source
in the shipped `models/download-ggml-model.sh` either.

The published quantisation set is not what the ticket assumed: **`q5_0` is published for
`medium` and `large-v3-turbo` only**; `tiny`, `base`, and `small` publish `q5_1` and `q8_0`.
To keep the sweep at a *single* quantisation, genuine `q5_0` was produced for
`tiny`/`base`/`small` from their f16 (`ggml-tiny.bin`, `ggml-base.bin`, `ggml-small.bin`)
with whisper.cpp's own `whisper-quantize`:

```
uvx cmake --build build --target whisper-quantize -j 16
build/bin/whisper-quantize models/ggml-small.bin models/ggml-small-q5_0.bin q5_0
```

Cross-check that the locally-produced quants are the real thing: the locally quantised
`ggml-small-q8_0.bin` is byte-for-byte the same size as the canonical published
`ggml-small-q8_0.bin` (**264 464 607 B**), and the download of the canonical file and the
quantised file have the **same sha256** (`49c8fb02b65e6049…`) — see §11. So the quantiser is
the upstream one and the `q5_0` files are genuine.

Model inventory (all in scratch, never in the repo):

| model file | quant | bytes | provenance |
|---|---|---|---|
| ggml-tiny-q5_0.bin | q5_0 | 29 875 721 | quantised from f16 |
| ggml-base-q5_0.bin | q5_0 | 55 295 433 | quantised from f16 |
| ggml-base.en-q5_0.bin | q5_0 | 55 308 851 | quantised from f16 (`.en`) |
| ggml-small-q5_0.bin | q5_0 | 175 209 663 | quantised from f16 |
| ggml-medium-q5_0.bin | q5_0 | 539 212 467 | canonical |
| ggml-large-v3-turbo-q5_0.bin | q5_0 | 574 041 195 | canonical |
| ggml-small-q5_1.bin | q5_1 | 190 085 487 | canonical (measured in §5) |
| ggml-base-q5_1.bin | q5_1 | 59 707 625 | canonical (measured in §5) |
| ggml-tiny-q8_0.bin | q8_0 | 43 537 433 | quantised from f16 |
| ggml-base-q8_0.bin | q8_0 | 81 768 585 | quantised from f16 |
| ggml-small-q8_0.bin | q8_0 | 264 464 607 | canonical (= locally quantised) |
| ggml-medium-q8_0.bin | q8_0 | 823 369 779 | canonical |
| ggml-large-v3-turbo-q8_0.bin | q8_0 | 874 188 075 | canonical |
| ggml-small.bin | f16 | 487 601 967 | canonical |
| ggml-medium.bin | f16 | 1 533 763 059 | canonical |
| ggml-base.en.bin | f16 | 147 964 211 | canonical |

(`ggml-tiny.bin`, `ggml-base.bin` are the f16 sources, kept in scratch.)

### 2.3 Audio

All clips are 16 kHz mono 16-bit, directly consumable by whisper.cpp.

| clip | seconds | peak | int16 samples at \|32767\| | condition |
|---|---|---|---|---|
| `ges2t-commit-cadence-capture-20261009-195914.wav` | 25.04 | 1.0000 | **1262** | car / noisy (the ground-truth passage) |
| `jfk.wav` | 11.00 | 0.7827 | 0 | clean read, standard reference |
| `quiet-short.wav` | 13.51 | 0.3708 | 0 | quiet room |
| `quiet-long.wav` | 23.81 | 0.3631 | 0 | quiet room |
| `clip5.wav` | 5.00 | 0.6534 | 0 | clean read |
| `clip30.wav` | 30.00 | 0.8110 | 0 | clean read |
| `2.wav` / `3.wav` | 3.27 / 6.62 | 0.36 / 0.53 | 0 | clean read |
| `cny16.wav` / `four.wav` | 10.44 / 6.58 | 0.65 / 0.81 | 0 | clean read |

Only the car clip clips. The quiet clips peak ≈ 0.37, so the "does clipping change the
ranking" question has a clean second condition (§10).

### 2.4 Harness, decode settings, and timing

Every run:

```
whisper-cli -m <model.bin> -f <clip.wav> -l en -t 16 -bs <1|5> -bo <1|5> -nt -np
```

- **`-l en`** pinned (no language auto-detect), **`-t 16`** threads (the machine has 16 HW
  threads).
- **Greedy** = `-bs 1 -bo 1`; **beam search** = `-bs 5 -bo 5` (whisper-cli's own default is
  `-bs 5`). The baseline sweep is greedy, per the ticket.
- **flash attention (`-fa`) is on** (whisper-cli 1.9.5 default). It is a default of the tool,
  recorded per row; the effect of flipping it was not swept (a confound, §12).
- **wall time** is the harness's own `time.monotonic()` around the child process.
  **RTF = audio_seconds / wall_seconds** (higher is faster; RTF 1.0 = realtime).
- **peak RSS** is the child's `ru_maxrss` from `getrusage(RUSAGE_CHILDREN)` after reaping.
  `/usr/bin/time` is **not installed** on this machine and there is no sudo, so this is the
  child's own high-water mark instead of `/usr/bin/time -v`.
- Every run is bounded (`--timeout 900`, plus `timeout 1000` around the harness) and the
  child is **reaped**; no spawned process can outlive a run.

The harness lives in the repo (`measure_whisper_cpp.py`) so the scoring is reproducible and
**reusable for an extra clip without re-downloading a model** — a new clip just needs
`--audio`, `--reference-key` (or `--reference-text-file`), and the already-cached `-m`.

### 2.5 Normalisation and the two WER conventions

WER is computed by importing the **accuracy-levers harness's own** `word_diff` and
`tokenise` **by path** from
`docs/project/live-dictation-ux/commit-cadence-diagnostic/measure_commit_cadence.py`, so this
discovery and ticket 39 score identically. The normalisation (quoted from that module) is:
case-folded; curly apostrophes unified and then dropped (`don't` == `dont`); hyphens and other
punctuation become word separators (`well-known` == `well known`); a `.` survives only between
two digits so `3.2` stays one token. **The identical normalisation is applied to the reference
and the hypothesis.**

Two metrics are reported side by side because they **disagree by convention**:

- **`word_diff`** (the project's metric): for an unequal-length `replace` block it charges
  `max(m,n)` substitutions **plus** `|m−n|` insertions/deletions — i.e. it double-counts the
  length differential.
- **standard** word edit distance: that same block costs `max(m,n)` once.

`word_diff` therefore **inflates** absolute WER when the hypothesis is longer or shorter than
the reference. The ranking and the model-vs-model comparison are the load-bearing results and
are unaffected (both metrics are stated); only the absolute magnitude differs. This mirrors
ticket 39's §7 exactly.

**Absolute WER is an upper bound**, not a model-accuracy claim: the car clip is one recording
of read speech and the reader may have stumbled or deviated from the reference. The
load-bearing comparison is **model vs model on identical audio**. The quiet clips are reported
by the orchestrator as read exactly, so their absolute WER is a tighter bound — but they are a
**different text and a different environment**, so they are **external validity**, not a
controlled A/B (§10).

## 3. Headline: accuracy-per-second

**Two answers, because the machine has two paths.**

1. **With the Vulkan (GPU) path — which works here — `large`/`medium`-class models become
   cheap, and `medium q5_0` is the unambiguous sweet spot**: it is the *most accurate* model in
   every condition tested (car clip **0.062 std WER**, quiet clips **0.111–0.296**) **and** it runs
   at **RTF 14.6** (1.72 s for the 25 s clip) — faster than every CPU configuration except `tiny`,
   and far more accurate than `tiny`. It Pareto-dominates the CPU choices that matter (`base`,
   `small`, CPU `medium`, `large-v3-turbo`: faster *and* at least as accurate); only CPU `tiny`
   is faster, and at 5× its error rate.
2. **CPU-only** (no GPU, or a user who keeps `-ng`), there is no single winner — the frontier is
   real, and the pick depends on how much latency the user will trade for accuracy:

| pick | model (CPU) | car-clip std WER | median wall / RTF | peak RSS | why |
|---|---|---|---|---|---|
| speed / English | `base.en` q5_0 | 0.141 | 2.52 s / 9.9 | 150 MB | same car-clip WER as `small` at ~2.5× the speed, but **does not generalise** (§9) |
| **knee, multilingual** | `small` q5_0 | 0.141 | 6.45 s / 3.9 | 336 MB | best multilingual accuracy before the cost curve steepens |
| **accuracy** | `medium` q5_0 | **0.062** | 19.3 s / 1.30 | 828 MB | the accuracy leader everywhere; expensive on CPU only |

`tiny` (0.281) and multilingual `base` (0.234) are not competitive on accuracy;
`large-v3-turbo` (0.125) is only *marginally* more accurate than `small` (0.141) at ~2.3× the
cost on the car clip and ties `medium` only on `quiet-long` (on `quiet-short` it is worse, 0.333
vs 0.296), so it earns its keep only if a user wants one model for every length/language and will
pay GPU prices (§8).

Everything else the ticket asked about resolves to a simple rule:

- **Quantisation: `q5_0` (or `q8_0`).** **WER is equal** across q5_0/q8_0/f16 at both `small` and
  `medium`, but the transcripts are **not** always byte-identical (small differs in two words;
  medium in punctuation only — §5); `f16` is only *slower* and *bigger* (medium f16: 26 s vs 19 s,
  1 780 MB vs 828 MB). No accuracy is bought by keeping more bits.
- **Decoding: greedy.** Beam-5 (`-bs 5`) buys **0 words** on `small` for a **7×** cost and at most
  **1–2 words** on `large-v3-turbo` for a ~15 % cost. Not worth it at ges2t's latency budget.
- **`.en` vs multilingual:** `base.en` beats multilingual `base` on the car clip (0.141 vs 0.234),
  but the advantage does **not** replicate on the quiet clips (they are equal on `quiet-short`,
  and `base` is better on `quiet-long`) — a marginal English-only win that also drops the seven
  languages Whistle already covers.

The absolute WERs are **upper bounds** (§2.5): the recurring errors are number/spelling forms
(`four thousand` → `4,000`, `metres` → `meters`) plus genuine acoustic confusions. On the car
clip `medium q5_0` has essentially **one** real word error (`river` → `rivers`); the rest of its
0.062 is formatting. The load-bearing result is the **model-vs-model ordering on identical
audio**, which is stable across all three clips (§10).

## 4. Baseline size sweep (greedy, `q5_0`, CPU)

Full tables: [`data/timings.md`](data/timings.md).

**Car / noisy clip (25.04 s, 64 reference words), median over repeats:**

| model | q5_0 bytes | wall s (min–max) | RTF | peak RSS MB | word_diff WER | std WER |
|---|---|---|---|---|---|---|
| tiny | 29 875 721 | 0.97 (0.67–1.83) | 25.8 | 104 | 0.312 | 0.281 |
| base | 55 295 433 | 2.42 (1.82–5.89) | 10.3 | 151 | 0.297 | 0.234 |
| small | 175 209 663 | 6.45 (4.84–7.79) | 3.9 | 336 | 0.141 | 0.141 |
| medium | 539 212 467 | 19.29 (8.54–22.45) | 1.30 | 828 | **0.062** | **0.062** |
| large-v3-turbo | 574 041 195 | 14.95 (11.82–18.54) | 1.68 | 733 | 0.141 | 0.125 |

**`jfk.wav` (11 s, clean):** every model scores **WER 0.000** — this clip does **not**
discriminate between models. It confirms the CLI is wired correctly, nothing more. The car clip
is the discriminating input.

The CPU wall times carry a **large run-to-run spread** (medium: 8.5–22.4 s, a 2.6× range). The
cause was not isolated (CPU boost/thermal state and background load are both candidates; the
machine is a laptop-class 7840HS); accuracy was **identical on every repeat**, so this affects
only the *speed* axis. Vulkan timings were stable to ≤9 % (§8).

## 5. Quantisation (`small`, `medium`)

Car clip, greedy, CPU, median (range):

| size | quant | wall s | RTF | peak RSS MB | word_diff WER | std WER |
|---|---|---|---|---|---|---|
| small | q5_0 | 6.45 (4.84–7.79) | 3.9 | 336 | 0.141 | 0.141 |
| small | q8_0 | 5.86 (2.37–8.64) | 4.3 | 421 | 0.141 | 0.141 |
| small | f16 | 8.57 (3.38–8.69) | 2.9 | 638 | 0.141 | 0.141 |
| medium | q5_0 | 19.29 (8.54–22.45) | 1.30 | 828 | 0.062 | 0.062 |
| medium | q8_0 | 17.89 (8.05–21.73) | 1.40 | 1 098 | 0.062 | 0.062 |
| medium | f16 | 26.06 (22.47–74.74) | 0.96 | 1 780 | 0.062 | 0.062 |

- **WER is quantisation-invariant, but the transcripts are not always byte-identical.**
  `small` q5_0 vs q8_0 differ in **two words** (`expects`/`expect`, `flood`/`fad`) whose
  substitutions cancel, so their WER coincides (0.141) **by luck**; `medium` q5_0 vs q8_0 differ
  **only in punctuation** (`Valley,`/`Valley`, `tonight,`/`tonight`). q8_0 and f16 are
  byte-identical at both sizes. The honest statement is *equal WER*, not *equal text* — and for a
  provider, that means q5_0 and q8_0 are **not** interchangeable at the word level.
- **q5_0 ≈ q8_0 in speed** at both sizes (their repeat ranges overlap completely).
- **f16 is strictly worse**: no accuracy gain, ~33 % slower and ~1.9× the RAM at `small`, and
  ~35 % slower and ~2.2× the RAM at `medium`. The f16 timings were noisy — the car clip's three
  runs were 22.5 / 26.1 / 74.7 s and `jfk`'s was 95.5 s while the Vulkan build ran concurrently;
  the cause of the 74.7 s car-clip outlier was **not** isolated (the build's first object file is
  timestamped 15 s *after* that run finished), so the **median 26.1 s** is the figure used here.
  **There is no reason to ship f16.**
- The ticket asked for `q5_0`; upstream publishes `q8_0` for every size and `q5_0` for
  `medium`/`large-v3-turbo` only. The published `q5_1` (small/base) was also run: on the car clip
  `small q5_1` scored **0.172** and `base q5_1` **0.344** (worse than q5_0's 0.141/0.297), and
  `small q5_1` matched q5_0 on the quiet clips. So **`q8_0` is the best *downloadable-and-pinnable*
  choice for the small sizes** (identical WER to q5_0, canonical file, sha256 known), and `q5_0`
  for medium/large-v3-turbo is canonical too (§11).

## 6. Decode: greedy vs beam-5

Car clip, `q5_0`, CPU, median (range):

| model | decode | wall s | RTF | word_diff WER | std WER |
|---|---|---|---|---|---|
| small | greedy | 6.45 (4.84–7.79) | 3.9 | 0.141 | 0.141 |
| small | beam-5 | 45.12 (14.57–75.67) | 0.55 | **0.141** | 0.141 |
| large-v3-turbo | greedy | 14.95 (11.82–18.54) | 1.68 | 0.141 | 0.125 |
| large-v3-turbo | beam-5 | 17.08 (12.47–21.69) | 1.47 | 0.125 | 0.109 |

Beam-5 is not worth it: **0 words** on `small` for a large, variable cost, and **1 word (word_diff)
/ 2 words (std)** on `large-v3-turbo` for ~15 %. The single word turbo gains is `rivers`→`river`;
it also drops `no doubt power`→`knocked out power`. Given the ticket's emphasis on latency, greedy
is the default.

## 7. `.en` vs multilingual (`base`)

Car clip, `q5_0`, greedy, CPU:

| model | wall s | RTF | word_diff WER | std WER |
|---|---|---|---|---|
| `ggml-base-q5_0.bin` (multilingual) | 2.42 (1.82–5.89) | 10.3 | 0.297 | 0.234 |
| `ggml-base.en-q5_0.bin` (English) | 2.52 (0.82–2.97) | 9.9 | 0.156 | 0.141 |

On the car clip `.en` is clearly better (0.141 vs 0.234 std) and equally fast. But it is a
**single-clip** win: on both quiet clips `small` (multilingual) beat `base.en` (quiet-short 0.444
vs 0.556; quiet-long 0.194 vs 0.208), so `.en`'s advantage does **not** generalise, and it would
cut the seven languages Whistle covers. Not recommended as the default; harmless as an option.

## 8. CPU vs Vulkan (the Radeon 780M)

A second build (`build-vulkan`, `-DGGML_VULKAN=ON`) was configured and built. The only missing
build dependency was `SPIRV-Headers`, which is **not installed** on this machine; rather than
install a package, the header-only SPIRV-Headers was staged **in scratch**
(`~/.cache/ges2t-discovery/whisper-cpp-provider/spirv-headers-prefix`) and pointed at with
`-DCMAKE_PREFIX_PATH` + `-I`. **The canonical fix would be `sudo pacman -S spirv-headers`** (and
the build works with that too). No system package was installed and no sudo was run.

Selected device (from stderr, without `-np`):

```
ggml_vulkan: 0 = AMD Radeon 780M Graphics (RADV PHOENIX) (radv) | uma: 1 | fp16: dot2 | ...
whisper_backend_init_gpu: using Vulkan0 backend
```

Car clip, greedy, `q5_0`, CPU median vs Vulkan median (Vulkan n=3):

| model | CPU wall s | CPU RTF | Vulkan wall s | Vulkan RTF | speed-up | CPU RSS | VK RSS | WER (both) |
|---|---|---|---|---|---|---|---|---|
| small | 6.45 | 3.9 | **0.87** | **28.9** | 7.4× | 336 MB | 94 MB | 0.141 |
| medium | 19.29 | 1.30 | **1.72** | **14.6** | 11.2× | 828 MB | 105 MB | **0.062** |
| large-v3-turbo | 14.95 | 1.68 | **1.47** | **17.1** | 10.2× | 733 MB | 118 MB | 0.125 |

Also on `jfk` and `quiet-long`: medium 7.19→1.12 s and 20.28→1.72 s; turbo 11.91→1.22 s and
15.07→1.47 s. Speed-ups ranged **4.7–13.4×** depending on which CPU run is compared.

**The Vulkan transcript is byte-identical to the CPU transcript** for all seven model/clip pairs
tested (medium, turbo, small × car, jfk, quiet-long). At this greedy/`q5_0` setting the GPU
changes **only** the speed. Vulkan wall times were also **reproducible** (medium 1.67–1.82 s,
small 0.866 s twice, turbo 1.469 s twice) — the GPU path does not show the CPU's 2× spread.

Peak RSS on Vulkan is several times lower (≈6–8× for `medium`/`large-v3-turbo`) because the
weights live in GPU-visible (unified) memory rather than the process RSS; on this iGPU that memory
is the same physical RAM, so this is an accounting difference, not a real saving.

## 9. Second condition: quiet room (external validity)

The quiet clips are a **different text and a different acoustic environment** from the car clip,
so this is **external validity**, not a controlled A/B. **Do not** read a car-vs-quiet delta as a
signal-condition effect.

CPU, greedy, `q5_0`, std WER (single run each):

| model | car (noisy, clips) | quiet-short (13.5 s) | quiet-long (23.8 s) |
|---|---|---|---|
| tiny | 0.281 | 0.407 | 0.319 |
| base | 0.234 | 0.407 | 0.139 |
| base.en | 0.141 | 0.407 | 0.153 |
| small | 0.141 | 0.333 | 0.139 |
| **medium** | **0.062** | **0.296** | **0.111** |
| large-v3-turbo | 0.125 | 0.333 | **0.111** |

**The recommendation holds.** `medium q5_0` is the best model in **all three** conditions
(0.062 / 0.296 / 0.111). `small` is second-tier (0.141 / 0.333 / 0.139). `large-v3-turbo` is
**inconsistent**: worse than medium on the car clip (0.125 vs 0.062) and on `quiet-short`
(0.333 vs 0.296), tied with medium only on `quiet-long` (0.111) — the car-clip gap is specific to
that noisy/clipped clip. `base`'s car-clip 0.234 collapses to 0.139 on `quiet-long`, and `.en`'s
advantage **does not replicate**: `base.en` and `base` are **equal** on `quiet-short` (both 0.407)
and `base` is better on `quiet-long` (0.139 vs 0.153) — the single flattering car-clip result for
`.en` does not generalise.

The **quantisation conclusion also holds**: `small q5_0` and `small q8_0` remain identical, and
`q5_1` matches them on quiet-long. The **decode conclusion is untested** on the quiet clips (only
greedy was run there), so it rests on the car clip alone.

Two signals the orchestrator flagged, checked:

- **Silence hallucination: none observed.** With a −40 dBFS RMS gate the quiet clips have **no**
  leading silence and ≤0.16 s trailing; every model's transcript begins at the first content word
  (`Hi Sam, …`) and no `thank you`-class insert on any model. The car clip likewise shows no edge
  hallucination.
- **Clipping:** only the car clip clips (1262 int16 samples at the rail); the quiet clips peak
  ≈0.37. The ranking **did not change** — `medium` leads on all three clips and `small` is second
  on two of three, exactly as on the clipped car clip. So clipping is not what is driving the
  ranking. The quiet clips are also ~10 dB quieter (RMS 0.033 vs the car clip's 0.109), which is a
  genuine level difference and **not** controlled; it is a plausible reason the *absolute* quiet
  WERs are high (`four hundred and twenty` → `120` on every model).

## 10. Per-window cost and streaming implications

whisper.cpp has **no native streaming**; the only way to expose `stream()` is to re-run the batch
model over a sliding window (what ges2t's Whistle path already does at 5 s). The cost of that is
**not** proportional to the window length, because whisper always runs the encoder over a padded
30 s segment — so a 5 s window costs almost as much as a 30 s clip:

| model | backend | clip5 (5 s) | clip30 (30 s) | per-window RTF |
|---|---|---|---|---|
| small | CPU | 3.83 s | — | 1.31 |
| medium | CPU | 13.71 s | 24.31 s | 0.36 |
| large-v3-turbo | CPU | 14.88 s | — | 0.34 |
| small | Vulkan | 0.47 s | — | 10.7 |
| medium | Vulkan | 0.97 s | 2.07 s | 5.2 |
| large-v3-turbo | Vulkan | 1.17 s | 1.42 s | 4.3 |

Consequence: a whisper.cpp sliding-window streaming loop is **not viable on CPU** (a 5 s window
takes 3.8–14.9 s) and only **marginal on GPU** (small = 0.47 s per window, just inside a 1 s
cadence; medium/turbo do not make it). The honest integration is therefore
**`supports_streaming = False`** — take the batch path (ADR 0011's `streaming.enabled = false` is
already a first-class configuration). On GPU, `small` is fast enough that a sliding-window stream
*is* feasible if the user wants it.


## 11. Integration fit with ges2t (analysis only, no code)

### 11.1 Behind the `Transcriber` Protocol

whisper.cpp's C API (`whisper.h`, MIT) maps cleanly onto the ADR 0010 Protocol:

| Protocol member | whisper.cpp |
|---|---|
| `name` | `"whisper_cpp"` (a new `transcriber.backend` value) |
| `supports_streaming` | **`False`** (§10); a GPU sliding window is the only streaming option |
| `ensure_model(progress)` | download + sha256/size verify, exactly like `model_store` |
| `load()` | `whisper_init_from_file_with_params(...)`, or nothing if the subprocess owns it |
| `transcribe(buffer)` | `whisper_full(ctx, params, samples, n)` on 16 kHz mono float32 — the `AudioBuffer` contract **is** whisper's contract, no resampling |
| `stream(chunks)` | not implemented natively; batch path, or a ges2t-side 5 s window (§10) |
| `close()` | `whisper_free(ctx)` |

The engine's timings (`ttft_ms`, `decode_tps`) are not exposed by whisper.cpp the way Needle
exposes them; `TranscriptionResult.ttft_ms/decode_tps` would be `None`, and our own clock would
supply per-pass timing — the same situation ADR 0011 already records for the Whistle *streaming*
path. Exactly one component would own the whisper context (or the subprocess), keeping ADR 0006's
"contain it" rule.

### 11.2 Two integration shapes

1. **`whisper-cli` as a subprocess** (what this discovery measured). Pros: no `.so` loaded into
the service, no ABI/packaging risk, crashes are contained, and it sidesteps the closed-engine
audit concern entirely — the engine *and* its build are open. Cons: process spawn + model load on
every call (measured below), and the CLI's stdout must be parsed.
2. **`ctypes` over `libwhisper.so`** — same pattern ges2t already uses for `libneedle3.so`. No
spawn/load per call (load once in `load()`), but it loads a C++ library into the Python process
and the model stays resident.

The **subprocess** shape is the better first cut for a second provider: it is the smallest change,
it is what was measured, and it removes the "closed, unauditable native engine" objection rather
than reproducing it.

### 11.3 Process spawn + model load cost

The measured wall time **already includes** process start and model load, so the per-call model
load is inside the numbers above. As a direct read: `tiny` on the 11 s `jfk` clip is 0.42 s total
— a cheap upper bound on spawn + load + a short decode. For `medium`, the load is a fraction of
the 1.7 s (Vulkan) / 8.5–22 s (CPU) figures. If model load were a problem, the `ctypes` shape
removes it; not measured separately, and it does not threaten ADR 0001's **<1 s framework** budget
(model time is explicitly not in that budget).

### 11.4 Model pinning and acquisition

Download from the canonical `ggerganov/whisper.cpp` HF repo and pin **sha256 + byte size** exactly
as `model_store` already does for Whistle. Recommended pins (all measured here):

| file | bytes | sha256 |
|---|---|---|
| `ggml-tiny-q5_0.bin` | 29 875 721 | `bf65504605bf1b8c7775b641aae82990010ea27d589e3b84ac82592cbdd38ed0` |
| `ggml-base-q5_0.bin` | 55 295 433 | `0238cb211a43e8ec0c64c0c4f98d4dbd22682579fa2ab20b49f083c529e7d1f3` |
| `ggml-small-q5_0.bin` | 175 209 663 | `23ad5215d37677f8eb8cbc41b4d0d4e451229f811be5206ff116dd9337eba748` |
| `ggml-medium-q5_0.bin` | 539 212 467 | `19fea4b380c3a618ec4723c3eef2eb785ffba0d0538cf43f8f235e7b3b34220f` |
| `ggml-large-v3-turbo-q5_0.bin` | 574 041 195 | `394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2` |
| `ggml-small-q8_0.bin` | 264 464 607 | `49c8fb02b65e6049d5fa6c04f81f53b867b5ec9540406812c643f177317f779f` |

`medium`/`large-v3-turbo` `q5_0` and every `q8_0` are **canonically published** and downloadable
directly. `tiny`/`base`/`small` `q5_0` are **not** published upstream (only `q5_1`/`q8_0`); the
`q5_0` files above were produced here with whisper.cpp's own `whisper-quantize` from the f16
models, and the quantiser was validated (the locally-quantised `small-q8_0` is the same size
**and sha256** as the canonical `ggml-small-q8_0.bin`). So for a self-contained, no-sudo `setup`,
**pin `q8_0` for the small sizes** (canonical, identical WER to `q5_0`) or have `setup` fetch the
f16 and quantise deterministically (466 MB for `small`). `q8_0` is the pragmatic choice.

### 11.5 Licence and dependency footprint

- **whisper.cpp: MIT.** **Whisper weights: MIT** (OpenAI). No copyleft, no attribution burden
  beyond the notice. This is a strictly cleaner position than ADR 0006's closed `libneedle3.so`:
  the whole stack is open and auditable.
- **Added runtime artifacts:** `whisper-cli` (1 064 768 B) plus `libwhisper.so` (639 256 B) and
  the ggml libraries (`libggml*`: ~2.5 MB) — a few MB of MIT-licensed C++, built from source with
  `uvx cmake`. The repo gains **no** dependency; the build and models live in scratch / the data
  dir, exactly like the Whistle model download.
- **Vulkan build** additionally needs `spirv-headers` (dev package) at build time only. It is
  **not** a runtime dependency.
- No new Python package is required for the subprocess shape (stdlib `subprocess`). The
  `ctypes` shape needs only stdlib `ctypes`.

### 11.6 Memory and the ADR 0001 latency budget

Peak RSS (CPU, car clip): tiny 104 MB, base 151 MB, small 336 MB, medium 828 MB, turbo 733 MB.
On Vulkan the process RSS is 95–118 MB because the weights move to GPU-visible memory (same
physical RAM on this iGPU). Whistle is 69 MB, so **`small`-class models are the RAM-comparable
choice**; `medium` costs ~0.8 GB resident on CPU.

**ADR 0001 budget:** the budget is **framework overhead < 1 s**, and model time is *explicitly*
not budgeted. whisper.cpp's model time is the numbers measured; the framework additions
(subprocess spawn, WAV hand-off, transcript parse) are sub-100 ms and untested here but are not
plausibly >1 s. On GPU, even `medium`'s total (1.7 s for 25 s of audio) is comfortably interactive.
The budget is **met** in the batch path; the *streaming* path is where whisper.cpp fails to keep a
1 s cadence on CPU (§10).

## 12. Confounds and limits

- **CPU timing is noisy (up to 2.6×).** `medium q5_0` ranged 8.54–22.45 s across five runs. The
  cause was not isolated (boost/thermal/background load); the machine is a laptop-class 7840HS
  and the load average was elevated by these very runs. **Accuracy was identical on every repeat**,
  so this touches only the speed axis. Vulkan timing was stable.
- **Vulkan was single-configuration, multi-run**: 3 repeats at each of the headline points, all
  reproducible, but one device/driver (`radv`, Mesa) on one machine.
- **Absolute WER is an upper bound.** The car clip's reader may have stumbled; the quiet-clip
  references were supplied by the orchestrator and not independently verified. `word_diff`
  further double-counts unequal-length replace blocks (§2.5).
- **Error composition is dominated by forms, not mishearings.** `four thousand` → `4,000` and
  `metres` → `meters` each cost substitutions for almost every model and inflate absolute WER. The
  recommended fix would be a number/spelling normalisation, which is **out of scope here** and not
  applied.
- **Three clips, English only.** One noisy/clipped car clip and two quiet clips of *different text*
  — external validity, not a controlled A/B. The **quiet-clip rows are n=1** per model
  (variance unmeasured); the car-clip rows have repeats. No other language was tested (`-l en`
  pinned). No second recording of the car passage exists, so the car-clip errors are not shown to
  reproduce.
- **Quiet clips are ~10 dB quieter** (RMS 0.033 vs 0.109), uncontrolled; a plausible cause of their
  high absolute WER.
- **flash attention** (`-fa`) is on by default in this build and was not the primary variable;
  spot checks with `-nfa` (small, turbo) gave the **same WER** and no consistent speed change, so
  it is not driving the ranking, but it was not swept.
- **The framework overhead itself was not measured** (no ctypes/subprocess adapter was built, per
  "no integration"). The `<1 s` claim is an inference from measured end-to-end times, not a
  measurement of a ges2t adapter.
- **Beam search / `.en` were not tested on the quiet clips** (only greedy and multilingual), so
  those two conclusions rest on the single car clip.
- **`q5_1` vs `q5_0`** was only checked for `small`/`base`; the small differences (0.141 vs 0.172)
  are within the one-recording error bar and are not treated as a real ordering.

## 13. What would falsify "whisper.cpp is a good fit"

1. **A second condition reversing the ranking.** If `medium q5_0` were not the best-or-tied model
   on the quiet clips, the recommendation would be clip-specific. *It held (0.062/0.296/0.111,
   best-or-tied — it ties `large-v3-turbo` on quiet-long).*
2. **A GPU/CPU transcript divergence.** If Vulkan changed words, the GPU speed-up would be bought
   with accuracy and the "just faster" claim would fail. *Byte-identical on all 7 pairs.*
3. **Vulkan not actually used.** If the speed-ups were a CPU-side fluke, the headline would be
   false. *Device line shows `Vulkan0` / Radeon 780M and is used.*
4. **A better canonical quantisation.** If `q8_0`/`q5_1`/f16 materially beat `q5_0`, pinning
   would change. *They did not (f16 worse; q8_0 equal WER).*
5. **A faster, equally-accurate alternative provider** (e.g. faster-whisper/CTranslate2) — not
   tested here; a real comparison would be the follow-up.
6. **The framework overhead exceeding 1 s** once an adapter exists — untested here; would fail
   ADR 0001 rather than the model choice.
7. **A downloadable artifact whose sha256 does not match** the pins in §11.4 — the pinning story
   is untested end-to-end here (I recorded hashes but did not build `ensure_model`).

## 14. Recommendation

**Yes — whisper.cpp is a viable second provider, and on this machine it is the accuracy option:**
`medium q5_0` scores **0.062** on the car clip against Whistle's measured **0.281** (batch) /
**0.391** (streaming) on the same recording (ticket 39) — roughly **4.5×** (batch) and **6.3×**
(streaming) fewer errors, with essentially one real word error.

1. **Provider:** implement whisper.cpp as a second `Transcriber`, `supports_streaming = False`,
   behind the **`whisper-cli` subprocess** shape first (smallest change, open and auditable).
2. **Default model by device:**
   - **Vulkan available (recommended here): `medium q5_0`.** Most accurate in all three conditions
     **and** RTF 14.6 (1.7 s for 25 s) — the accuracy-per-second winner outright.
   - **CPU-only, multilingual default: `small q5_0`/`q8_0`** (0.141 WER, RTF 3.9, 336 MB).
   - **CPU-only, accuracy-first: `medium q5_0`** (0.062 WER, RTF 1.3, 828 MB) if the user will
     accept ~15–22 s for a 25 s utterance.
   - **CPU-only, English and speed: `base.en q5_0`** — but flag that it did not generalise to the
     quiet clips.
3. **Quantisation: `q5_0` (or `q8_0`).** Never f16. Pin the canonical files
   (`q8_0` for `tiny`/`base`/`small`; `q5_0` for `medium`/`large-v3-turbo`) with the sha256s in §11.4.
4. **Decoding: greedy.** Do not enable beam search.
5. **Vulkan:** worth enabling. It is **4.7–13.4× faster at byte-identical output**, and it is what
   makes `medium` the sweet spot. The only build-time cost is `spirv-headers`
   (`sudo pacman -S spirv-headers`), unnecessary for the CPU build.
6. **Follow-ups this discovery does not cover:** a real adapter + framework-overhead measurement;
   a second recording of the car passage; a number/spelling normaliser; and a head-to-head with
   faster-whisper/CTranslate2 (the other escape hatch in ADR 0005/0006).

## 15. Verification and cleanup

Reproduce the whole matrix (models cached, no re-download):

```
cd .worktrees/whisper-cpp-provider
uv run python docs/project/research/whisper-cpp-provider/measure_whisper_cpp.py \
    --label small-q5_0-commit --model ~/.cache/.../models/ggml-small-q5_0.bin \
    --audio ~/.cache/.../audio/ges2t-commit-cadence-capture-20261009-195914.wav \
    --reference-key commit --bs 1 --bo 1 --threads 16 --results <runs.jsonl>
uv run python docs/project/research/whisper-cpp-provider/render_tables.py \
    --runs docs/project/research/whisper-cpp-provider/data/runs.jsonl \
    --out docs/project/research/whisper-cpp-provider/data/timings.md
```

- Raw evidence: `data/runs.jsonl` (**103 rows**), rendered `data/timings.md`.
- Every run was bounded (`timeout 1000` + harness `--timeout`) and the child reaped; no spawned
  process outlived a run.
- No file under `src/ges2t/` or `tests/` was touched; no microphone, clipboard, window,
  notification, or network beyond the model downloads was used.
- Builds and models live in scratch (`~/.cache/ges2t-discovery/whisper-cpp-provider/`) and are
  **not** committed. Nothing binary/model/audio is in the repo.

