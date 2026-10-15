# 57 — implementation comment

- **Branch:** `feat/nemo-speech-engine-and-model-acquisition`
- **Worktree:** `.worktrees/nemo-speech-engine-and-model-acquisition`
- **Status:** in progress (started 2026-10-14)

## Baseline (measured, before any change)

`uv run pytest -q` in the fresh worktree at `01c624e`:

```
1230 passed in 6.72s
```

**The orchestrator's stated baseline of `923 passed` is stale** — it does not match `main` at the
point this branch was cut. Recorded here rather than silently accepted; the number that must not
regress is **1230 passed**, and `uv run mypy src/ges2t tests` must stay at **0 errors**.

## Pins used (given by the ticket / ticket 55 — not re-derived)

| thing | value |
|---|---|
| engine archive URL | `https://github.com/NVIDIA/NeMo-Speech.cpp/releases/download/v0.2.0/nemo-speech-0.2.0-linux-x86_64-vulkan.tar.gz` |
| engine archive sha256 | `c8d0ed5b9ac5fb6d5c460015ba38ea217415c44edc1e6d49120646781009aee6` |
| engine archive size | `16,618,142` B |
| model URL | `https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b/resolve/1c8deaecc64b91f034d73e08dd8b64625eb3395d/nemotron-3.5-asr-streaming-0.6b.q8_0.gguf` |
| model sha256 | `a5c435f294eea8f88ce68dd27b8c3bfea7f777cb2fbba04fcd30eaa555f429ae` |
| model size | `741,548,352` B |
| model revision | `1c8deaecc64b91f034d73e08dd8b64625eb3395d` |

The archive URL and the revision-qualified model URL are the exact strings ticket 55's report records
(`docs/project/research/nemotron-3-5-asr-streaming-measurement/REPORT.md` §2), so the pins here are the
same artifacts that were measured, byte for byte.

## Extracted layout (read from ticket 55's scratch tree, read-only)

The extracted archive root is `nemo-speech-0.2.0-linux-x86_64-vulkan/` containing
`bin/nemo-speech`, `lib/libnemo_speech_asr_c.so` → `.so.1`, the `libggml*`/`libllama` family,
`include/nemo_speech/*.h`, and `share/{doc,licenses}`. The entry library the C-ABI adapter (ticket 58)
loads is `lib/libnemo_speech_asr_c.so`; the binary `bin/nemo-speech` is what `doctor` runs. This is the
"device detection" surface too: the archive is the **Vulkan** build with a CPU fallback, so acquisition
detects the Vulkan **loader** and reports Vulkan-vs-CPU; the engine enumerates GPUs itself at runtime.

## Plan

1. `identifiers.py` — add `NEMO_SPEECH_ENGINE = "nemo_speech"` (one spelling; the setup token and the
   engine value are the same string, so no second constant and no alias).
2. `model_store.py` — the GGUF pin (`ArtifactPin` + revision) and
   `nemo_model_path()`/`ensure_nemo_model()`, reusing `_ensure`/`verify`; expose the existing download
   implementation as a public seam so `nemo_engine.py` reuses it rather than adding a second urllib loop
   (the "no second networked path" guard test scans for exactly this).
3. `nemo_engine.py` (new) — archive pin, extraction into the data dir, `engine_dir()`,
   `verify_engine()`, device detection. **No build**, no `sudo`, no install.
4. `setup_command.py` + `cli.py` — the `nemo_speech` setup backend: acquire the archive, then the GGUF,
   verify both, print the config line. Hop-by-hop hash/size failure leaves nothing half-written.
5. Tests — hermetic (temp files + fake fetchers, never network): wrong-hash archive and wrong-size
   model both fail; the setup path with fakes; the real seam (a real `.tar.gz` round-trip through
   `tarfile`, no network).

Working log follows, appended as each step lands.

## Step 2 — `nemo_engine.py` (new), and its falsification

`uv run pytest -q tests/test_nemo_engine.py tests/test_model_store.py` → **108 passed**;
`uv run mypy tests/test_nemo_engine.py tests/test_model_store.py` → clean.

Every new guard was falsified (rule 6): the mutation was applied to `src/`, the guarded test was run,
it **failed**, and the file was restored (`git diff` for `src/` empty afterwards; `md5sum` recorded).

| # | mutation applied to `src/` | targeted test | result |
|---|---|---|---|
| M1 | `extractall(..., filter="data")` → `extractall(...)` | `test_a_path_traversal_member_is_refused` | **failed** ✔ |
| M2 | drop the sha256 comparison in `verify_engine` | `test_verify_engine_returns_none_for_a_different_pin` | **failed** ✔ |
| M3 | drop the entry-library completeness check | `test_an_archive_without_the_entry_library_is_a_typed_error` | **failed** ✔ |
| M4 | drop the size check in the shared verifier | `test_ensure_{nemo_model_wrong_size…,engine_checks_the_archive_size_alone}` | **2 failed** ✔ |
| M5 | `import subprocess` added to `nemo_engine.py` | `test_the_engine_module_runs_no_external_command…` | **failed** ✔ |
| M6 | `NemoEngine.complete()` → `True` | `test_verify_engine_returns_none_when_a_file_was_deleted` | **failed** ✔ |
| M8 | `_remove_tree(destination)` before the fetch | `test_a_failed_reinstall_keeps_the_previous_install` | **failed** ✔ |
| M10 | `verify_engine` → always `None` | `test_a_complete_install_is_not_refetched` | **failed** ✔ |
| M11 | install the GGUF at a wrong destination | `test_ensure_nemo_model_downloads_verifies_and_installs` | **failed** ✔ |
| M12 | drop the idempotent size gate in `ensure_artifact` | `test_ensure_nemo_model_is_idempotent` | **failed** ✔ |

A first attempt at M11 did not apply (the `str.replace` target did not match) and the test passed —
recorded here rather than counted as a pass; M11b with a matching target failed the test as intended.
M2's first run also passed because the "different pin" differed in **size** as well, so the size check
rejected it before the hash check was reached; the test now uses a same-size, different-hash pin so
the sha check is isolated.

Two properties worth naming honestly:

- **The wrong-size tests assert the pair, not the size in isolation** — a truncated payload differs in
  hash too. `test_…_checks_the_size_alone` (added to both suites) closes that hole with a pin whose
  sha256 is the payload's but whose size is wrong, so only the size check can reject it (M4).
- **The extraction seam is real.** The tests build a real gzip tar and let `tarfile` open it; no fake
  models the archive format. The `data` filter's traversal refusal (M1) is therefore a test of the
  real behaviour, not of a stub.

## Step 3 — `ges2t setup nemo_speech` (`setup_command.py` + `cli.py`)

`SetupBackend.NEMO_SPEECH` takes its value from `NEMO_SPEECH_ENGINE` — setup token, engine value and
(ticket 58's) TOML block name are one string, so there is no second constant and no alias
(`test_nemo_backend_token_is_the_engine_value`). `SetupDependencies` gained `nemo_engine` and
`nemo_model` Protocols; the needle and whisper.cpp paths are untouched.

Acquisition order is **engine archive first, then the 741 MB GGUF** — the small, prerequisite-free
extraction is proven before the big download, and a model failure raises `PartialSetupError` naming the
installed engine, exactly as the whisper path does.

**`ges2t setup nemo_speech <model>` is refused loudly.** The backend has exactly one pinned GGUF, so a
model argument selects nothing; accepting and ignoring it is how a user comes to believe they switched
models (rule 5). The error names the pinned model and the correct command, and it is raised in the
**planning** phase, so no byte is downloaded on a rejected invocation.

The CLI help keeps the literal `'nemo_speech'` in its prose, as the whisper.cpp precedent already does
with `'whisper.cpp'`; the identifier-drift guard is therefore the `setup_command` test above, not the
help-text test. The CLI help text change is a documentation guard only.

### Falsification of the new setup/CLI guards

| # | mutation applied to `src/` | targeted test | result |
|---|---|---|---|
| M13 | silently accept and ignore a nemo model argument | `test_nemo_speech_refuses_a_model_argument…` + the CLI twin | **2 failed** ✔ |
| M14 | swap the order (model before engine) | `test_nemo_run_extracts_the_engine_then_acquires_the_model` | **2 failed** ✔ |
| M15 | drop the nemo entry from `_BACKEND_ENGINES` | `test_nemo_backend_token_is_the_engine_value` | **failed** ✔ |
| M16 | swallow a nemo engine failure and continue to the model | `test_nemo_engine_failure_propagates_and_never_acquires_a_model` | **failed** ✔ |
| M17 | change `NEMO_SPEECH_ENGINE` to `"nemo-speech"` | `test_nemo_backend_token_is_the_engine_value` | **failed** ✔ |
| M18 | drop `NemoEngineError` from the CLI's catch tuple | `test_setup_nemo_speech_reports_an_engine_failure_cleanly` | **failed** ✔ |

M14's first attempt produced a *syntax error* (the mutation left a dangling `except`), which is
detected but is not evidence that the order assertion works; M14b applied a valid swap and the two
order/partial-failure tests failed as intended. M17 also left `test_setup_help_names_the_nemo_backend`
**passing**, because that help text is a literal — the honest reading, and the reason the identifier
guard is the setup_command test rather than the help test.

## Verification (run from the worktree)

```
$ uv run mypy src/ges2t tests
Success: no issues found in 67 source files

$ uv run pytest -q
1288 passed in 7.34s
```

Baseline before this branch: **1230 passed**, 0 mypy errors. So: **+58 tests, no regressions, 0 mypy
errors** (the orchestrator's stated `923 passed` baseline does not match `main` at `01c624e`).
