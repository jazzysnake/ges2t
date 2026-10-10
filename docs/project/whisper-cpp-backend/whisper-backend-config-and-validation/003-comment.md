# 003 — comment: review 002 corrections

- **Ticket:** `docs/project/whisper-cpp-backend/whisper-backend-config-and-validation/ticket.md`
- **Branch:** `feat/whisper-backend-config-and-validation`
- **Review:** `002-review.md` (commit `915fac6`) — verdict `changes requested`
- **Status:** in review

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

## T1 — the pure-parse test now fails on a real probe

`tests/test_whisper_backend_config.py::test_the_generic_parse_never_probes_the_machine` (renamed
from `..._for_a_model`) no longer only monkeypatches `default_whisper_model`. It instruments every
probe entry point reachable from `whisper_engine`:

- `whisper_engine.HostSystemProbe` (construction and all three methods),
- `whisper_engine.inspect_vulkan`,
- `whisper_engine.default_whisper_model`,
- `whisper_engine.shutil.which`,
- `Path.is_file` (records, returns the real value).

It first proves the guard is live -- a **direct** `inspect_vulkan(HostSystemProbe())` call is
recorded -- then clears the record and asserts a `load_config` of a `whisper_cpp` config adds
nothing to it. So it can only pass because `load_config` probed nothing.

Falsified exactly as the reviewer did -- injected a direct probe into `load_config`:

```python
# src/ges2t/config.py, inside load_config, after the toml read:
from ges2t import whisper_engine as _we
_we.inspect_vulkan(_we.HostSystemProbe())
```

```
$ uv run pytest -q tests/test_whisper_backend_config.py -k never_probes
E  AssertionError: assert ['HostSystemProbe', 'inspect_vulkan'] == []
   Left contains 2 more items, first extra item: 'HostSystemProbe'
FAILED ...::test_the_generic_parse_never_probes_the_machine
1 failed, 20 deselected
```

Reverted with `git checkout -- src/ges2t/config.py` → green.

## Small — the adapter's safety-net default is now pinned

`test_a_directly_constructed_adapter_resolves_the_default_model`: a bare
`WhisperCppTranscriber(WhisperCppConfig())` keeps `config.model is None` and resolves
`_model_name()` to the (monkeypatched) rule **once** (cached), and `_model_path()` is the catalogue
path for that resolved name. Removing the safety net fails it:

```
$ uv run pytest -q tests/test_whisper_backend_config.py -k directly_constructed_adapter
E  AssertionError: assert None == 'tiny-q8_0'
FAILED ...::test_a_directly_constructed_adapter_resolves_the_default_model
1 failed, 20 deselected
```

Reverted with `git checkout -- src/ges2t/transcribers/whisper_cpp.py` → green.

## Small — `needle` vs `whistle` vocabulary

The config engine value is `whistle`; `needle` is only the `ges2t setup` CLI token (`SetupBackend`)
and the engine *family* (Needle 3, `libneedle3.so`). Fixed the remaining "default backend stays
`needle`" prose to "the default engine stays `whistle` (the Whistle model on the Needle 3 engine)":

- `docs/project/whisper-cpp-backend/epic.md` (2 places)
- `docs/project/README.md` (epic summary)
- `docs/project/whisper-cpp-backend/whisper-backend-config-and-validation/ticket.md` (prose + DoD)

`ges2t setup needle …` and the Needle-family mentions were left as-is (correct). A post-fix grep
found no remaining `needle`-as-default prose in the live docs.

## Final verification (re-run on the final commit)

```
$ uv run pytest -q
1129 passed in 8.21s

$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 64 source files
```

No `src/ges2t/` behaviour changed: the selector, the three startup rejections, the single-source
device rule, the build-time resolution, and the `whistle` default are exactly as review 002 verified.
