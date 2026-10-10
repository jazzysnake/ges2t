# 001 — comment: whisper backend doctor checks

- **Ticket:** `docs/project/whisper-cpp-backend/whisper-doctor/ticket.md`
- **Branch:** `feat/whisper-doctor`
- **Worktree:** `.worktrees/whisper-doctor`
- **Status:** in progress

## Scope read first

Read before touching code: the ticket, `../epic.md`, ADR 0021, ADR 0016, ADR 0013, ADR 0019,
and the merged dependencies (ticket 41 `whisper-model-store`, 42 `whisper-engine-build`,
43 `whisper-transcriber-adapter`, 45 `whisper-backend-config-and-validation`), plus the current
`doctor.py`, `whisper_engine.py`, `model_store.py`, `config.py`, `pipeline.py`,
`transcribers/whisper_cpp*.py`, and `tests/test_doctor.py`.

## Plan (recorded before coding)

Three new `doctor` checks, each gated on `transcriber.engine == "whisper_cpp"`, so a `whistle`
selection reports `OK`/skipped rather than a false failure:

1. **Whisper engine built** — `libwhisper.so` + every `libggml*.so*` the install manifest names,
   present at the resolved data-dir path, and the build's variant (CPU-only / Vulkan) reported.
2. **Whisper model** — the configured (or device-defaulted) model present at its catalogue path,
   named exactly (`ggml-<name>.bin`), with its expected size; with `--verify`, hash-verified
   against its pinned sha256.
3. **Vulkan device** — whether a Vulkan runtime is present; a `WARN` when absent naming the CPU
   cost and the `small-q8_0` default; and, when a GPU runtime *is* present but the built engine
   is CPU-only, the WHY (the missing dev package(s), from the same probe `setup` will use).

The existing 22 checks stay untouched and the two by-design `WARN`s (Hotkey bindings, Tray
failures) still hold, because with the default `whistle` engine all three new checks are skipped.

_(evidence appended below as work lands)_

## What changed

- `src/ges2t/whisper_engine.py` — one shared helper, `vulkan_dev_package_hint(status)`, plus
  `VulkanStatus.missing_packages` (the Arch package names of the missing **dev** pieces; the
  runtime is deliberately excluded). This is the single wording for "a GPU runtime is present but
  a dev package is missing, so the build is CPU-only" — ticket 47's `setup` hint and this ticket's
  doctor check both read it, so they cannot drift. Returns `None` when there is no runtime at all
  (a GPU is not one package away) or when every piece is present.
- `src/ges2t/doctor.py` — three new checks (`check_whisper_engine`, `check_whisper_model`,
  `check_vulkan`), the `WHISPER_CPP_ENGINE` constant, a `_whisper_gate` shared by all three, two new
  `DoctorProbes` fields (`whisper_engine_dir`, `system_probe`), and the wiring in `build_report`
  (inserted after the existing "Engine present", so `names[:8]` is the pinned report order).
  **No existing check function was modified.**
- `tests/test_doctor.py`, `tests/test_whisper_engine.py` — 29 new tests (see below).

## The three new checks and their exact messages

Captured from a real run (`uv run python` against the check functions, no monkeypatching of the
message text — see `/tmp/capture_msgs.py` method in the run log).

### `Whisper engine built`

- whistle selected → `[OK] skipped: transcriber.engine is 'whistle' (whisper.cpp not selected)`
- CPU install → `[OK] libwhisper.so and 1 libggml*.so* present at <dir>; build variant: CPU-only`
- Vulkan install → same, `build variant: Vulkan (GPU)`
- not built → `[FAIL] the whisper.cpp engine is not built at <dir> (no engine.json manifest)`;
  fix `run 'ges2t setup whisper.cpp <model>' to build it (it needs a C++ toolchain and cmake, which ges2t never installs)`
- library renamed by hand → `[FAIL] the installed whisper.cpp engine at <dir> is incomplete: missing libwhisper.so`;
  fix `rebuild the engine with 'ges2t setup whisper.cpp <model>'`
- unreadable manifest → `[FAIL] the whisper.cpp engine manifest at <dir>/engine.json is unreadable: <exc>`

### `Whisper model`

- whistle selected → `[OK] skipped: transcriber.engine is 'whistle' (whisper.cpp not selected)`
- missing → `[FAIL] ggml-tiny-q8_0.bin is missing at <path> (expected 43537433 bytes)`;
  fix `run 'ges2t setup whisper.cpp tiny-q8_0' to download and verify the model`
- wrong size → `[FAIL] ggml-tiny-q8_0.bin at <path> is <n> bytes, expected 43537433 bytes`
- present, no `--verify` → `[OK] ggml-tiny-q8_0.bin is present at <path> (43537433 bytes); pass --verify to hash it against its pinned sha256`
- corrupt bytes, `--verify` → `[FAIL] ggml-tiny-q8_0.bin at <path> does not match the pinned sha256 <sha>`
- matching bytes, `--verify` → `[OK] ggml-tiny-q8_0.bin at <path> (<n> bytes) matches the pinned sha256`
- invalid block → `[FAIL] the [engine.whisper_cpp] block is invalid: <WhisperCppConfigError>`

The model is resolved through `pipeline.resolve_whisper_cpp_config` (imported lazily), the **same**
rule the service and `setup` use, so doctor never disagrees about which model is in play. The exact
file name and pinned size are always named. **No model-vs-GPU sizing check** is present.

### `Vulkan device`

- whistle selected → `[OK] skipped: transcriber.engine is 'whistle' (whisper.cpp not selected)`
- no runtime → `[WARN] no Vulkan runtime is available: whisper.cpp runs on the CPU (medium-q5_0 ~19 s for a 25 s clip, RTF 1.30 - still faster than realtime), and the CPU-only default model is small-q8_0`;
  fix `to enable the GPU path install the Vulkan runtime and dev packages ('sudo pacman -S vulkan-icd-loader vulkan-headers spirv-headers shaderc') and re-run 'ges2t setup whisper.cpp <model>'; the CPU-only install works as-is`
- GPU runtime present, dev piece missing (reference machine, `spirv-headers` only) →
  `[WARN] a Vulkan runtime is present at <lib>, but the GPU build is missing spirv-headers, so the engine builds CPU-only`;
  fix (the shared helper) `a Vulkan runtime is present, but the GPU build is missing spirv-headers, so the engine builds CPU-only; install them with 'sudo pacman -S spirv-headers' and rebuild with 'ges2t setup whisper.cpp <model>' to enable the GPU path`
- Vulkan available, engine built with Vulkan → `[OK] Vulkan runtime, headers, SPIR-V headers and glslc present (runtime at <lib>); the installed engine was built with Vulkan`
- Vulkan available, engine CPU-only → `[WARN] Vulkan is available (runtime at <lib>) but the installed engine was built CPU-only`
- Vulkan available, no engine yet → `[OK] ... no engine is built yet, so 'ges2t setup whisper.cpp <model>' will build the Vulkan variant`
- engine Vulkan but no runtime now → `[WARN] the installed engine was built with Vulkan, but no Vulkan runtime is available now, so it may fail to load`

### Startup rejection (whisper_cpp + `streaming.enabled = true`)

All three checks report the refusal instead of a healthy-but-refused state:

- `[FAIL] this config will not start: transcriber.engine = "whisper_cpp" cannot be combined with streaming.enabled = true (whisper.cpp is batch-only, so there would be no text until the utterance ends)`
- fix `set streaming.enabled = false to run whisper.cpp batch-only, or set transcriber.engine = "whistle" (the default) to keep live and incremental typing`

(Header lines are `Whisper engine built` / `Whisper model` / `Vulkan device`.)

## Falsification (each check broken, watched fail, reverted)

Every mutation below was applied to the committed tree, the named test run, the failure observed,
and `src/` restored (`git status` clean after each).

- **engine library renamed → FAIL**: dropped the `build.libraries.missing()` term →
  `test_whisper_engine_fails_when_a_library_is_renamed` failed (`OK` vs `FAIL`); reverted, passed.
- **corrupt model → FAIL**: forced the `--verify` branch false →
  `test_whisper_model_verified_fails_on_a_corrupt_model` failed (`OK` vs `FAIL`); reverted, passed.
- **no Vulkan → WARN**: forced the no-runtime branch `OK` →
  `test_vulkan_warns_without_a_runtime_and_names_the_cpu_cost` failed; reverted, passed.
- **GPU present but CPU-only build → missing-package explanation**: (a) forced the runtime-present
  branch `OK` → `test_vulkan_warns_with_the_missing_dev_package_when_a_gpu_is_present` failed;
  (b) made `vulkan_dev_package_hint` always return `None` → both
  `test_vulkan_dev_package_hint_names_only_the_missing_packages` and
  `test_vulkan_warns_with_the_missing_dev_package_when_a_gpu_is_present` failed
  (`AssertionError`); reverted, passed.
- **skip for another engine**: forced the gate's engine test false → all three
  `test_whisper_checks_are_skipped_for_another_engine` params failed; reverted, passed.
- **startup rejection explained**: forced the gate's streaming test false → all three
  `test_whisper_checks_explain_the_startup_rejection` params failed; reverted, passed.

## Verification

```
$ uv run pytest -q
1158 passed in 8.70s

$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 64 source files
```

The orchestrator's `923 passed` baseline predates tickets 41–45; the branch point (`df4738f`) is
`1129 passed`, and this ticket adds 29 tests (1158 = 1129 + 29). No test was deleted, skipped or
weakened.

## End-to-end CLI run (read-only; a nonexistent `--config`, so the user's file is untouched)

```
$ uv run ges2t doctor --config /tmp/ges2t-does-not-exist.toml
...
Whisper engine built      OK    skipped: transcriber.engine is 'whistle' (whisper.cpp not selected)
Whisper model             OK    skipped: transcriber.engine is 'whistle' (whisper.cpp not selected)
Vulkan device             OK    skipped: transcriber.engine is 'whistle' (whisper.cpp not selected)
...
Summary: 25 checks, 22 ok, 2 warning(s), 1 failure(s).
```

The three new checks sit right after "Engine present"; with the default `whistle` engine they are
`OK`/skipped (never false failures), the 22 pre-existing checks are all present, and the only two
warnings are the by-design ones (Hotkey bindings, Tray failures). The single failure in this run is
`Tray backend` and is an artifact of **this worktree's fresh `.venv`** (uv created it without
`--system-site-packages`, so `gi` is not importable); it is unrelated to this ticket and the main
worktree's venv has `gi`. The check runs no microphone, clipboard, keystroke, window, notification,
`sudo`, service or network action — every probe is an inspection (ADR 0016).

## Notes for the reviewer

- **Existing 22 checks untouched.** No existing check function was edited; the three new checks are
  inserted after "Engine present". With the default `whistle` engine they are `OK`/skipped, so the
  two by-design `WARN`s (Hotkey bindings, Tray failures) remain exactly two and the exit code is
  unchanged. `test_build_report_adds_the_whisper_checks_and_keeps_two_by_design_warnings` pins this.
- **Shared, not duplicated.** The GPU-present-but-CPU-only wording lives in
  `whisper_engine.vulkan_dev_package_hint`; ticket 47's `setup` hint should call it rather than
  re-word it.
- **No model-vs-GPU sizing check** (ADR 0021 §8): any configured model is accepted.
- The whisper block is validated by the new model check (catching `WhisperCppConfigError`), not by
  the existing "Engine config valid" check, which this ticket deliberately left untouched; that
  check still reports the whisper block as having "no engine-block validator in this build", which
  is an accurate statement about doctor's own wiring.
- All probes are read-only (`inspect_vulkan`, `read_build`, `is_file`, `stat`); doctor still does no
  network I/O, writes nothing and touches no device.

