# 001-comment — ticket 55 (Nemotron 3.5 ASR streaming): measurement result and gate for the integration tickets

**Verdict: PROCEED.** The prebuilt NeMo-Speech.cpp Vulkan binary runs on this PC and **uses the RADV
iGPU**; the cache-aware streaming model produces **strictly append-only incremental text** with no
seam duplication, no cut-off hallucination, and no silence hallucination, at every measured chunk
size. The integration tickets may proceed and **ADR 0022 + the ADR 0011 amendment** may be presented.
Two things must NOT be relayed as wins: **Hungarian accuracy** (unmeasured here) and **CPU/Vulkan
transcript parity** (false — they differ deterministically).

Report: [`../../research/nemotron-3-5-asr-streaming-measurement/REPORT.md`](../../research/nemotron-3-5-asr-streaming-measurement/REPORT.md)

## Headline numbers (all real runs, this machine)

- **Engine runs, no build, no `sudo`.** Prebuilt `nemo-speech-0.2.0-linux-x86_64-vulkan.tar.gz`,
  16,618,142 B, sha256 `c8d0ed5b…` verified; `nemo-speech doctor` → `backend_vulkan`, device
  `[0] integrated-gpu AMD Radeon 780M Graphics (RADV PHOENIX)`. Every measured run prints
  `[asr] … backend=Vulkan0` on stderr with `--device vulkan:0`.
- **Vulkan is really used (proven, not assumed).** VRAM Δ across model load **~952 MB on Vulkan** vs
  **exactly 0 on CPU**; peak RSS **~197 MB (Vulkan)** vs **~1,064 MB (CPU)** for the identical C-ABI
  streaming run; CLI RSS **~181 MB (Vulkan)** vs **~1,760–2,100 MB (CPU)** on all 10 clips. No silent
  CPU fallback.
- **Latency / RTF.** Real-time-paced (160 ms chunks): Vulkan **~41–45 ms of work per 160 ms chunk**
  (p90) with **first text ~0.85–1.16 s of speech**; CPU **~72–88 ms** per chunk. Both faster than real
  time (paced RTF 0.98–0.99). Algorithmic lag (encoder watermark − committed text) **160–240 ms**.
- **Committed-delta stability — PASSES.** 10 clips × {Vulkan, CPU} = **20 runs, all
  `append_only_char_frac = 1.0`, 0 rewritten pairs, monotone commits, 0 adjacent duplicate words**;
  the **final extends the last interim** in all 20 (equals it in 18; the other 2 complete a trailing
  partial word). Plus 5 sweep runs (jfk at 80/160/320/560/1120 ms),
  all append-only. The interim is a strict string-prefix extension of the previous one every time.
- **Silence control.** 10 s of digital silence → **0 interims, empty final**, both devices (whisper.cpp
  committed `you you` / `thank you thank you`).
- **Accuracy (word_diff, ticket 32 normalisation, same clips/references as the whisper reports):**
  **jfk 0.000**, **quiet-short 0.259**, **quiet-long 0.167**, **car (user's capture) 0.312**. Whisper
  `medium q5_0` scored **0.062** on the same car clip **with its `-nt` decode (0.156 without it)** —
  so the fair gap on whisper's CLI default is ~2×. Streaming WER == batch WER on Vulkan.
- **Chunk sizes.** Since `--asr.streaming.chunk_size` is CTC-only, the RNNT step is set by
  `rnnt_right_context = 0/1/3/6/13` → **80/160/320/560/1120 ms**. Vulkan RTF rises 3.0→10.5 (jfk) /
  3.5→15.6 (clip30) with chunk; steady p50 21→42 ms; first text 0.88→2.24 s. All five append-only.

## Resolved model revision + sha256 (must be pinned)

| source | revision | size (B) | sha256 |
|---|---|---:|---|
| HF `main` | `ea30d66debe3740a08b573244286791d423d6b3e` | 742,090,464 | `3fc991d3badad7277c11030a7519832cddaf2057aafed6d4b25147e953a070b1` |
| **engine `model-index.json` — CHOSEN** | `1c8deaecc64b91f034d73e08dd8b64625eb3395d` | 741,548,352 | `a5c435f294eea8f88ce68dd27b8c3bfea7f777cb2fbba04fcd30eaa555f429ae` |

Downloaded the **engine-index revision** (ticket guidance: prefer the engine's own index) and verified
sha256 with `sha256sum -c` (**OK**). The two are different commits (542,112 B apart). `ges2t setup`
must pin `1c8deae…` + `a5c435f2…` + size 741,548,352. **Note:** `huggingface_hub`'s
`/api/models?revision=<sha>` silently fell back to `main`; confirm pins via the `resolve/<sha>` LFS
headers. Licence: **OpenMDW-1.1**.

## Safe to relay?

- **Yes** — the engine runs on Vulkan here; the backend/VRAM/RSS evidence; the append-only stability
  result; the jfk/quiet/car English WER; the chunk-size table; the silence control.
- **Yes, with the caveat attached** — "Vulkan and CPU transcript output is **not** byte-identical"
  (deterministic per device; word-diff divergence 0.000–0.185 across the 10 clips). Do not claim parity.
- **No** — any Hungarian WER measured on this machine (there is none), and any "the model is as
  accurate as whisper" claim (false on the noisy car clip).

## Escalate to the user

1. **Hungarian has no ground-truth clip here.** Only the published FLEURS `hu_hu` **≈ 28.7–33.4**
   (broad-coverage; Parakeet v3's is 15.72) can be stated; it is **not locally measured**. A
   **user-supplied Hungarian clip + transcript** is needed to settle it — I did **not** record one
   (no microphone). Please decide whether to provide one or to ship Hungarian with the published
   number only.
2. **Vulkan/CPU divergence is a real integration constraint.** Switching device changes the text. If
   the user cares, the docs should say so, and any "golden transcript" test must be per-backend.
3. **Vulkan costs ~950 MB of VRAM to save modest CPU time** — CPU already runs ~1.8× real time and
   keeps up. The GPU win here is host RAM (~197 MB vs ~1 GB), not speed. Worth stating so the choice
   of default device is informed.
4. **No `sudo` needed and none used.** The prebuilt archive required no build tooling (`ninja` /
   `sentencepiece` are absent — not needed for this path). `numpy` appears nowhere in the measured
   path; the adapter can be pure `ctypes`.
5. **ADR 0022 + ADR 0011 amendment** are drafted verbatim in report §11 / "Draft ADR 0022 / ADR 0011
   amendment" for sign-off; nothing was written to `docs/adr/`.
