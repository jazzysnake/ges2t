# 003 — comment: review 002 corrections

- **Ticket:** `docs/project/whisper-cpp-backend/whisper-backend-config-and-validation/ticket.md`
- **Branch:** `feat/whisper-backend-config-and-validation`
- **Review:** `002-review.md` (commit `915fac6`) — verdict `changes requested`
- **Status:** in progress

## Fix plan

- **D1 (blocking):** remove the unsupported "CPU is ~6× real time" claim from every live doc and
  replace it with the provider report's sourced batch RTFs:
  `small-q8_0` CPU RTF **4.3** (~5.9 s for the 25 s clip, ≈0.23× realtime) and `medium-q5_0` CPU
  RTF **1.30** (~19.3 s, ≈0.77× realtime) — CPU is **faster** than realtime, not 6× slower. The
  `~6×` figure is the *streaming* CPU duty (`whisper-cpp-streaming` report), a regime this ADR's
  batch-only decision excludes. Sources:
  `docs/project/research/whisper-cpp-provider/REPORT.md` §8 tables (lines 253–280, 351–354).
- **T1 (non-blocking):** strengthen the pure-parse test so it fails on a *direct*
  `HostSystemProbe`/`inspect_vulkan`/`shutil.which`/`is_file` probe, and prove the guard is live.
- **Small:** pin the adapter's safety-net default; fix `needle`-as-default wording to `whistle`.

_(evidence appended per fix)_

## D1 — the false CPU latency claim (blocking)

**Corrected statement, used consistently (source: `research/whisper-cpp-provider/REPORT.md` §8):**

> CPU is slower than Vulkan but **still faster than realtime in batch**: for the 25 s clip
> `medium-q5_0` costs **RTF 1.30** (~19.3 s, ≈0.77× realtime) and the CPU-only default `small-q8_0`
> **RTF 4.3** (~5.9 s, ≈0.23× realtime) (`RTF = audio_seconds / wall_seconds`). `small-q8_0` is the
> CPU default for its **latency headroom**, not because `medium` is unusable.
>
> The old "~6× real time" is whisper's **streaming** CPU duty (`whisper-cpp-streaming` report:
> duty 5.87/6.11), a regime this ADR's batch-only decision excludes.

Edited:

- `docs/adr/0021-whisper-cpp-second-backend.md` — rewrote the "~6× real time (medium ~24 s for a
  30 s clip)" sentence (which contradicted itself); the origin of the wrong figure is now named in
  a parenthetical so it is not re-introduced.
- `README.md` — the Engines section: `~1.7 s` on Vulkan, `~19 s` / `~6 s` on CPU, **faster than
  realtime**.
- `docs/project/whisper-cpp-backend/whisper-transcriber-adapter/ticket.md` — also fixed the
  `small-q5_0` → `small-q8_0` model-name error in the same sentence (ticket 44's correction).
- `docs/project/whisper-cpp-backend/whisper-engine-build/ticket.md`
- `docs/project/whisper-cpp-backend/whisper-doctor/ticket.md` (both occurrences)

The epic and `docs/project/README.md` did not carry the CPU claim (checked by grep).
