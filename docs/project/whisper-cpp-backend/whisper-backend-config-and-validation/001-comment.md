# 001 — comment: whisper backend config and startup validation

- **Ticket:** `docs/project/whisper-cpp-backend/whisper-backend-config-and-validation/ticket.md`
- **Branch:** `feat/whisper-backend-config-and-validation`
- **Worktree:** `.worktrees/whisper-backend-config-and-validation`
- **Status:** in review

## Final verification (re-run on the final commit)

```
$ uv run pytest -q
1128 passed in 7.96s

$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 64 source files
```

## Scope read first

Read before touching code: the ticket, `../epic.md`, ADR 0021, ADR 0009, ADR 0011, ADR 0020,
the merged dependencies (`whisper-transcriber-adapter` ticket 43, `setup-backend-and-model-arguments`
ticket 44), and the current code (`config.py`, `transcribers/whisper_cpp*.py`, `pipeline.py`,
`whisper_engine.py`, `setup_command.py`, `model_store.py`).

## Decisions

_(recorded incrementally; evidence appended below)_

1. **Engine vocabulary.** The engine selector is `transcriber.engine`; the default engine is
   `whistle` (the human "needle" name is the loose ticket/epic spelling). This ticket adds
   `"whisper_cpp"` to `KNOWN_ENGINES`; the default stays `whistle` and Whistle's behaviour is
   untouched.
2. **Where the omitted `[engine.whisper_cpp] model` is resolved: at *transcriber build*, not in
   the generic config parse.** `WhisperCppConfig.from_mapping` now represents an omitted model as
   `None` (unresolved) and does **not** probe the machine — a pure parse stays side-effect-free
   (ADR 0009). The pipeline's composition root resolves `None` with the single shared
   `whisper_engine.default_whisper_model()`, which is also what `ges2t setup` uses, so the
   resident model and the model `setup` would fetch cannot disagree (single source of truth).
3. **Device-aware rule lives with the probe.** `DEFAULT_WHISPER_MODEL_GPU` / `_CPU` and
   `default_whisper_model()` move to `whisper_engine.py` (which owns `inspect_vulkan`); `setup_command`
   re-exports them so ticket 44's public API is unchanged. No probe is duplicated.

_(more below as work lands)_

## What changed

- `src/ges2t/config.py` — `KNOWN_ENGINES = ("whistle", "whisper_cpp")`; `DEFAULT_ENGINE` is
  still `KNOWN_ENGINES[0] == "whistle"`. This is the only generic-layer change; no `[transcriber.*]`
  keys were invented.
- `src/ges2t/whisper_engine.py` — the ADR 0021 §4 rule moved here (next to `inspect_vulkan`):
  `DEFAULT_WHISPER_MODEL_GPU = "medium-q5_0"`, `DEFAULT_WHISPER_MODEL_CPU = "small-q8_0"`,
  `default_whisper_model(system=None)`. Single probe, single source of truth.
- `src/ges2t/setup_command.py` — its private copies of the constants/function are deleted; it now
  **re-exports** `whisper_engine`'s names, so `ges2t setup`'s public API and behaviour are unchanged
  (ticket 44's tests still pass).
- `src/ges2t/transcribers/whisper_cpp_config.py` — an omitted `model` is now `None` (unresolved),
  documented as "resolved at build time"; the module still does not probe the machine. `DEFAULT_MODEL`
  is `None` deliberately, so there is no contradicting static default. `model`/`language`/`threads`/
  `gpu` keep their ticket-43 typed validation.
- `src/ges2t/transcribers/whisper_cpp.py` — the adapter resolves an omitted model through
  `whisper_engine.default_whisper_model()` (cached) as a safety net for a directly-constructed
  adapter; the normal path receives an already-resolved config.
- `src/ges2t/pipeline.py` — new `resolve_whisper_cpp_config(block, *, system=None)` (the build-time
  resolver, test seam included), `_build_whisper_cpp` uses it, and `_validate_whisper_config` +
  `_whisper_streaming_error` + `_whisper_missing_artifacts` are wired into `_startup` after
  `_validate_injection_backend`. `_missing_artifacts` stays Whistle-specific (no behaviour change).

## Exact startup rejections (engine = "whisper_cpp")

1. **streaming enabled** (checked first, before artifacts) —
   `transcriber.engine = "whisper_cpp" cannot be combined with streaming.enabled = true: whisper.cpp is batch-only, so there would be no text until the utterance ends.`
   hint: `set streaming.enabled = false to run whisper.cpp batch-only, or set transcriber.engine = "whistle" (the default) to keep live and incremental typing`.
2. **model not acquired** — `the whisper.cpp model 'tiny-q8_0' is not acquired or is incomplete at <path>`.
3. **library not built** — `the whisper.cpp engine library is not built at <dir> (libwhisper.so is missing or incomplete)`.
   2 and 3 share the hint `run 'ges2t setup whisper.cpp <model>' to build the engine and download the model (the only ges2t command that uses the network)`.
4. **`streaming.enabled = false` and a complete install** → validation is silent.
5. **Model-vs-GPU sizing is NOT checked** — a complete install with any model/`gpu` combination
   passes validation (pinned by `test_model_vs_gpu_sizing_is_not_checked`).

## Falsification (each check removed, watched fail, reverted)

All from the committed tree; `git status` clean after each revert.

- **engine selector**: reverted `KNOWN_ENGINES` to `("whistle",)` → 3 tests fail
  (`test_known_engines_...`, `test_config_parses_the_whisper_cpp_engine`,
  `test_config_rejects_an_unknown_engine_naming_whisper_cpp`).
- **streaming rejection**: `if False and config.streaming.enabled` →
  `test_streaming_with_whisper_is_refused_naming_both_settings` fails (it sees the artifact error).
- **model-not-acquired**: dropped the model branch → `test_a_model_that_is_not_acquired_is_refused`
  fails (`assert 0 == 1`).
- **library-not-built**: dropped the `verify_build` branch →
  `test_a_library_that_is_not_built_is_refused` fails (`assert 0 == 1`).
- **device-aware rule (Vulkan branch)**: forced CPU branch in `default_whisper_model` →
  `test_omitted_model_resolves_to_the_gpu_default_with_vulkan` fails (`medium-q5_0` vs `small-q8_0`).
- **build-time resolution**: removed the `None` resolution in `resolve_whisper_cpp_config` → 4 tests
  fail (`..._cpu_default`, `..._gpu_default`, `..._partial_vulkan`, `..._resolves_an_omitted_model`).
- **whistle still streams**: set `WhistleTranscriber.supports_streaming = False` →
  `test_whistle_is_still_the_default_and_still_streams` fails.

## Test/type counts

- Before this ticket's new tests: `1108 passed` (main at `a3dcbf5`), `0 mypy errors`. The
  orchestrator's stated `923` baseline predates tickets 43/44, which added ~185 tests; the tree at
  the branch point is `1108`.
- After: `1128 passed`, `0 mypy errors` (64 source files checked).

## DoD notes

- **"The needle model is selectable too"** is already satisfied by the merged ticket 44
  (`ges2t setup needle <model>`, `model_store.NEEDLE_MODELS`), and at runtime by
  `transcriber.model_path`; this ticket only pins that the *selector* vocabulary stays `whistle`.
- **Resident load at startup** was wired by ticket 43 and is re-verified here:
  `tests/test_whisper_cpp.py::test_startup_builds_and_loads_the_whisper_transcriber_once`
  (load exactly once) and `..._is_fatal_with_a_clear_message` (fatal load), both updated to set
  `streaming.enabled = false` and to stub the artifact check (the real model is ~500 MB and
  user-owned; the artifact matrix is tested with a sparse file in the new file).
- **No automatic fallback** between engines: `_TRANSCRIBER_BUILDERS` still raises on an unknown
  engine and `_select_session_kind` never switches engine (ADR 0020).

## ADR 0021 review

Reviewed against the implementation. The decisions match (in-process resident model, batch-only,
GPU/CPU default rule, pinned `-nt`/greedy, setup parameterisation, strict startup validation,
no model-vs-GPU sizing). **One correction was needed**: the ADR used `needle` where it meant the
`transcriber.engine` *value*; the project's real selector value is `whistle` (ticket 43/44, and
`KNOWN_ENGINES`). Fixed three places in `docs/adr/0021-whisper-cpp-second-backend.md`:

- Decision §1: "default backend remains `needle`" → "remains `whistle` (the Whistle model on the
  Needle 3 engine)".
- Decision §10: "the **needle backend** remains the shipped default" → "the **`whistle` engine**".
- Escape hatch: "one config value away from `needle`" → "`whistle`".

The `ges2t setup needle <model>` vocabulary (SetupBackend.NEEDLE == "needle") is correct and was
left as-is. The ADR remains `proposed`; the orchestrator presents it for sign-off before merge.

