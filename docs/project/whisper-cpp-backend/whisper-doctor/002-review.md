# 002 — review: whisper backend doctor checks

- **Ticket:** `docs/project/whisper-cpp-backend/whisper-doctor/ticket.md`
- **Branch:** `feat/whisper-doctor` (tip `64bfa23`), worktree `.worktrees/whisper-doctor`
- **Reviewer:** independent verifier (did not write this code)
- **Diff read:** `git diff main...feat/whisper-doctor` (three-dot; merge-base `1edf2ca`)

## Verdict

**changes requested** — one required, documentation-only correction in `001-comment.md` (a false
coordination claim, and the direct cause of the dedupe question the orchestrator must settle). The
**code, tests and mypy are verified and mergeable**; every DoD item passes under falsification, and
no `src/` behaviour is in doubt. The requested change is a single sentence, not a behaviour change.

The blocking finding is:

- `001-comment.md` states, as present fact: *"ticket 47's `setup` hint and this ticket's doctor check
  both read it, so they cannot drift."* Ticket 47's branch
  (`feat/engine-naming-and-setup-guidance`, tip `583d79e`) contains **no reference** to
  `vulkan_dev_package_hint` or `missing_packages`; it builds its own `cpu_only_build_advice` /
  `cpu_only_build_hint` in `setup_command.py`. The claim is false today (and the same file's later
  "Notes for the reviewer" says the opposite — 47 *should* call it). A doc that asserts a dedupe that
  has not happened is exactly the kind of statement that lets a duplicate ship. It must be corrected
  to intent/must-do before merge.

## Per-criterion findings (Definition of Done)

| DoD item | Verdict | Evidence |
|---|---|---|
| Built-engine check: `libwhisper.so` + `libggml*.so` present at resolved data-dir path; reports CPU-only vs Vulkan | **pass** | Falsified by mutation (P1, P2, P3, F1, F2); real run below shows `libwhisper.so and 9 libggml*.so* present … build variant: CPU-only`. |
| Model check: present, `--verify` hash-verified against pinned sha256; names exact file + expected size | **pass** | Falsified (P4, P5, P6, F3, F4, F5, F6); real messages captured below; no model-vs-GPU sizing check. |
| GPU/Vulkan check: device available + which; no runtime → WARN naming CPU cost (`medium-q5_0` ~19 s, RTF 1.30) and `small-q8_0` default | **pass** | Falsified (P7, P8, P8b, P11, F7–F10); real outputs below; real end-to-end run shows the available-but-CPU-only WARN. |
| `whisper_cpp` + `streaming.enabled = true` → checks explain the startup rejection | **pass** | Falsified (P12; all 3 params failed); gate returns `_whisper_streaming_rejection` for all three. |
| With another engine selected, whisper checks are OK/skipped, not false failures | **pass** | Falsified (P13, F11); end-to-end run shows 3× `OK skipped: transcriber.engine is 'whistle'`. |
| **No model-vs-GPU sizing check** | **pass** | `check_whisper_model` only checks presence + pinned size/sha; `grep default_whisper_model doctor.py` → no sizing comparison; any catalogue model accepted. |
| Existing 22 checks untouched; total count and two by-design WARNs hold | **pass** | Only deletion in `doctor.py` diff is `-from ges2t import model_store`; end-to-end run = 25 checks, 2 WARNs (Hotkey bindings, Tray failures). |
| Tests falsify each new check; watched fail, reverted | **pass** | 25 new test functions all falsified individually (see "Falsification" section); `git status` clean, `src/`/`tests/` unmodified after every probe. |
| `uv run pytest` and `uv run mypy src/ges2t tests` green; report counts | **pass** | `1158 passed`; `Success: no issues found in 64 source files`. Baseline on `main` (`02171eb`) verified at `1129 passed`. |
| `001-comment.md` records each new check and its exact message | **pass, with the blocking inaccuracy above** | Every quoted message was reproduced against a real run; the "both read it / cannot drift" sentence is false. |

**Not a defect, recorded:** the ticket prose says *"With `needle` selected, the whisper checks are
`OK`/skipped"*. The shipped default engine value is `whistle` (ADR 0021 §10), and the code/tests gate
on `whistle` — the ticket's "needle" is the neede/Whistle backend in prose. It matches the
orchestrator's instruction ("skipped for `whistle`").

## Evidence — real output

### Full suite and mypy (post-merge, on the branch tip)

```
$ uv run pytest -q
1158 passed in 8.32s

$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 64 source files
```

```
$ (in main worktree) uv run pytest -q
1129 passed in 8.69s          # baseline confirmed; 1158 − 1129 = 29 new tests
```

Test-count arithmetic checked: 25 new `def test_` functions, two of them
`@pytest.mark.parametrize` with 3 cases each → (25 − 2) + 2×3 = **29**. No test deleted
(`git diff main...HEAD -- tests/ | grep '^-'` shows only three import lines).

### End-to-end `doctor` CLI, default engine (read-only, nonexistent `--config`)

```
$ uv run ges2t doctor --config /tmp/ges2t-does-not-exist.toml
...
Whisper engine built      OK    skipped: transcriber.engine is 'whistle' (whisper.cpp not selected)
Whisper model             OK    skipped: transcriber.engine is 'whistle' (whisper.cpp not selected)
Vulkan device             OK    skipped: transcriber.engine is 'whistle' (whisper.cpp not selected)
...
Tray failures             WARN  tray failures cannot be determined from here: ...
Hotkey bindings           WARN  ges2t does not grab the hotkey itself; bind the command in KDE
Summary: 25 checks, 22 ok, 2 warning(s), 1 failure(s).
```

25 checks confirmed; the three whisper checks sit after "Engine present" and are **OK/skipped**; the
only two WARNs are the by-design ones. The single FAIL is `Tray backend`, and the comment's claim
that it is "an artifact of this worktree's fresh `.venv`" is **itself true**: the main worktree venv
has `gi` (`/usr/lib/python3.14/site-packages/gi/__init__.py`) while the worktree venv does not
(`ModuleNotFoundError: No module named 'gi'`).

### End-to-end `doctor` CLI, `whisper_cpp` config (temp config in `/tmp`, user config untouched)

```
$ uv run ges2t doctor --config /tmp/whisper-doctor-probe-config.toml
Whisper engine built      OK    libwhisper.so and 9 libggml*.so* present at
                                /home/matee/.local/share/ges2t/engine/whisper-cpp/v1.9.5; build variant: CPU-only
Whisper model             OK    ggml-medium-q5_0.bin is present at
                                /home/matee/.local/share/ges2t/models/whisper-cpp/medium-q5_0/ggml-medium-q5_0.bin
                                (539212467 bytes); pass --verify to hash it against its pinned sha256
Vulkan device             WARN  Vulkan is available (runtime at /usr/lib/libvulkan.so) but the installed engine was built CPU-only
Summary: 25 checks, 21 ok, 3 warning(s), 1 failure(s).
```

This exercises the new checks against the **real filesystem/manifest** (no monkeypatching).

### Model resolves through the same path as `setup` and startup (instrumented)

```
[CPU-only host]
  doctor  resolved model : small-q8_0
  startup resolved model : small-q8_0
  setup   resolved model : small-q8_0
  same name: True ; same file path: True
[Vulkan host]
  doctor  resolved model : medium-q5_0
  startup resolved model : medium-q5_0
  setup   resolved model : medium-q5_0
  same name: True ; same file path: True
```

`check_whisper_model` → `pipeline.resolve_whisper_cpp_config` → `whisper_engine.default_whisper_model`
and `model_store.whisper_model_path`, exactly the functions `_build_whisper_cpp`/`_whisper_missing_artifacts`
(startup) and `plan_setup` (`setup`) use. No divergent path.

### Real new-check messages (reproduced, matching `001-comment.md`)

```
NOT BUILT:    FAIL | the whisper.cpp engine is not built at <dir> (no engine.json manifest)
              fix: run 'ges2t setup whisper.cpp <model>' to build it (it needs a C++ toolchain and cmake, which ges2t never installs)
UNREADABLE:   FAIL | the whisper.cpp engine manifest at <dir>/engine.json is unreadable: <exc>
INVALID BLOCK:FAIL | the [engine.whisper_cpp] block is invalid: engine.whisper_cpp.model = 'small-q5_0': unknown whisper.cpp model ...
MODEL MISSING:FAIL | ggml-tiny-q8_0.bin is missing at <path> (expected 43537433 bytes)
NO RUNTIME:   WARN | no Vulkan runtime is available: whisper.cpp runs on the CPU (medium-q5_0 ~19 s for a 25 s clip,
              RTF 1.30 - still faster than realtime), and the CPU-only default model is small-q8_0
GPU MISSING:  WARN | a Vulkan runtime is present at <lib>, but the GPU build is missing vulkan-headers, spirv-headers,
              shaderc, so the engine builds CPU-only
              fix: ... install them with 'sudo pacman -S vulkan-headers spirv-headers shaderc' ...
```

The two ADR-0021 numbers quoted in the WARN (`medium-q5_0` ~19 s, RTF 1.30) trace to ADR 0021 §"Why
medium q5_0 on GPU, small q8_0 on CPU-only" (~19.3 s, RTF 1.30) — citations, not new measurements.

### Existing checks untouched

```
$ git diff main...HEAD -- src/ges2t/doctor.py | grep '^-' | grep -v '^---'
-from ges2t import model_store
```

The only removed line is an import (replaced by `from ges2t import model_store, whisper_engine`); no
existing check function was modified. `build_report` gains exactly three `_guard(...)` entries,
inserted after "Engine present"; the ordered-names test pins `names[:8]`.

## Falsification (every new test broken, watched fail, reverted)

Each mutation was applied to the committed tree, the named test run, the failure observed, then
`git checkout -- src/ges2t/doctor.py src/ges2t/whisper_engine.py` + `rm -rf .mypy_cache __pycache__`.
Baseline of the same selection before mutations: `17 passed`.

```
P1  engine missing-lib term dropped          -> 1 failed
P2  engine not-built branch (if False)       -> 1 failed
P3  engine variant label hardcoded           -> 1 failed
P4  model missing branch (if False)          -> 1 failed
P5  model wrong-size branch (if False)       -> 1 failed
P6  model verify branch (if False)           -> 1 failed
P7  no-runtime WARN -> OK                    -> 1 failed
P8  GPU-present missing-dev (helper->None)   -> 1 failed
P8b GPU-present missing-dev (branch->OK)     -> 1 failed
P9  Vulkan-avail + Vulkan-build OK branch    -> 1 failed
P10 Vulkan-avail + CPU-build WARN branch     -> 1 failed
P11 hint names only missing packages         -> 1 failed
P12 startup-rejection gate (if False)        -> 3 failed
P13 skip-for-another-engine gate (if False)  -> 3 failed
F1  engine OK branch always FAIL             -> 1 failed
F2  manifest-unreadable except narrowed      -> 1 failed
F3  model-present OK branch -> FAIL          -> 1 failed
F4  verified-OK summary text mutated         -> 1 failed
F5  model ignores configured block           -> 1 failed
F6  invalid-block except narrowed            -> 1 failed
F7  missing_packages always all three        -> 1 failed
F8  no-runtime hint guard dropped            -> 1 failed
F9  all-present hint guard narrowed          -> 1 failed
F10 Vulkan-available branch disabled         -> 1 failed
F11 build_report skip broken                 -> 1 failed
F12 build_report whisper path broken         -> 1 failed
```

All 25 new test functions (29 cases) were individually demonstrated to fail when their guarded
behaviour was broken; none is vacuous. `git status --short` was empty after the batch, and the final
post-probe `pytest`/`mypy` above were re-run on the clean tree.

## Dedupe assessment (integration point with ticket 47)

**Answer: No — 46's helper does not fully express the hint 47 needs, and yes, merging both as-is
leaves two sources of truth for the same user-facing message.**

- Ticket 46's helper is `vulkan_dev_package_hint(status: VulkanStatus) -> str | None`, keyed on a
  **live** `inspect_vulkan` status (`runtime is not None and not available`). For a machine with a
  GPU runtime but a missing dev package it returns one sentence naming the missing `pacman` package(s)
  plus the exact `sudo pacman -S …` line.
- Ticket 47's helper is `cpu_only_build_advice(build: EngineBuild, *, system)` /
  `cpu_only_build_hint(...)` in `setup_command.py`, keyed on the **installed build's** variant
  (`build.variant is CPU and build.vulkan.runtime is not None`), re-probing the current system via
  `check_prerequisites(BuildVariant.VULKAN, probe)` and filtering host-build packages
  (`base-devel`, `cmake`). Its inputs (the build) are not expressible by 46's signature, and it
  carries extra data 46 does not (`CpuOnlyBuildAdvice.named`, the host-build filter).
- So 46's helper is the **narrower** of the two, not a superset; it cannot simply be called by 47
  without changing 47's gate/inputs.
- Both, however, render the **same diagnostic class** ("Vulkan runtime present, dev package(s) missing
  → CPU-only build; install <pacman line>") in different wording and with different fix text. Because
  they do not share a function or a message constant, the wording, the condition and the fix line can
  drift even though the underlying `PACKAGE_*` / `PACMAN_PREFIX` constants are shared.
- Confirmed mechanically: `grep -rn 'vulkan_dev_package_hint\|missing_packages'` in the 47 worktree
  → no matches; `grep -rn 'cpu_only_build\|CpuOnlyBuildAdvice'` in the 46 worktree → no matches.
- Therefore, **as things stand, merging 46 and 47 as-is gives two independent implementations of the
  same hint.** Recommended: before merging 47, move the one message/advice into `whisper_engine`
  (extend the helper to accept the build, or fold 47's richer `CpuOnlyBuildAdvice` there) and have
  both `doctor` and `setup` read it.
- Mechanical merge note: both branches edit the same region of `docs/project/README.md` (adjacent
  rows 46/47) → expect a trivial textual conflict there; no `src/`/`tests/` file is touched by both.

## Risks

1. **Duplicated GPU-path hint (above).** Moderate; a source-of-truth split for one message, mitigated
   only by the shared package constants. **Requires an orchestrator dedupe decision before merging 47.**
2. **`check_vulkan` device detection has no test for the *uninstalled-then-rebuilt* sequence.** The
   current real machine's live probe reports all four Vulkan pieces present while the installed
   manifest records `spirv_headers=None`; the "GPU runtime present, dev package missing" branch is
   therefore only exercised with `FakeSystemProbe`, never against the real host. Correct by
   construction, but not end-to-end verifiable on this machine today.
3. **Coverage gap: doctor's omitted-model default is not pinned by a test.** All `test_doctor.py`
   whisper cases pass an explicit model (`_whisper_config` defaults to `tiny-q8_0`). The device-aware
   default is covered for startup/setup in `test_whisper_backend_config.py` and I verified doctor
   agrees by instrumentation, but a regression that made *doctor* ignore the omitted model would not
   fail the doctor suite. Low risk; a one-line test would close it. (Ticket 50 changes the streaming
   default, not the model default.)
4. **`check_vulkan`'s `assert hint is not None`** is a runtime `assert`. It is provably unreachable
   (runtime present + not available ⇒ non-empty `missing_packages`), and the mutation P8 shows the
   branch is exercised — but under `python -O` an `assert` is stripped. No functional impact here.

## Could-not-verify (each reason is itself true)

- **The "GPU runtime present but `spirv-headers` missing" branch against the real host.** The live
  `inspect_vulkan(HostSystemProbe())` on this machine now finds all four pieces
  (`runtime … spirv_headers=…/spirv.h … shader_compiler=…/glslc`, `available: True`), so no real-host
  run can enter that branch. Exercised instead with the test's `FakeSystemProbe` and with my own
  direct `check_vulkan` call using a fake probe (verified reason: the real probe output above).
- **Startup rejection through the running service (`ges2t run`).** I did not start the service (no
  service changes, machine safety). The rejection is verified only at the check-function level (unit
  tests + direct calls), not by observing `ges2t run` refuse a `whisper_cpp` + `streaming.enabled`
  config.
- **`doctor` against the user's own config file with the whisper engine.** I used a temp config in
  `/tmp` so the user's `config.toml` was untouched; the real user-config path (`~/.config/ges2t`) was
  not exercised for a whisper selection.
- **Ticket 47's final merged behaviour.** 47 is in parallel review at a different tip; I read its
  branch, not a merge.

## What would falsify the central claim

The central claim is: *the three new doctor checks correctly diagnose the whisper engine, model and
Vulkan path, are skipped (not failed) for the default `whistle` engine, and explain the startup
rejection.* It is falsified if any of the following is observed on `feat/whisper-doctor`:

- `uv run ges2t doctor --config <nonexistent>` reports **anything other than 3× `OK`/skipped** for the
  three whisper checks when `transcriber.engine` is `whistle` (or reports a count other than 25).
- Any of the 29 new tests passes after the guarded behaviour is broken (I found none).
- A model resolved by `check_whisper_model` differs from the model `resolve_whisper_cpp_config` /
  `plan_setup` resolve for the same block (instrumentation says they agree).
- The `whisper_cpp` + `streaming.enabled = true` config produces `OK` for any of the three checks
  instead of `FAIL` (P12 says it fails).

**Safe to relay to the user?** Mostly **yes** for the code and counts; **no** for the sentence
"ticket 47's setup hint and this ticket's doctor check both read it, so they cannot drift." Do not
relay that as fact — it is false on 47's branch, and the orchestrator must dedupe before merging 47.

## Follow-up tickets I recommend

1. **Correct `001-comment.md`** (required for this review): restate the 46/47 helper sentence as
   intent/must-do, or delete it, so the artifact does not assert a dedupe that does not exist.
2. **Dedupe the GPU-path hint** into `whisper_engine` before merging 47 (or immediately after), so
   `setup` and `doctor` share one message and one condition.
3. **A one-line doctor test for the omitted-model default** (device-aware `medium-q5_0` vs
   `small-q8_0`) to close risk 3.

## Hygiene

All mutation probes were reverted; `git diff --stat -- src/ tests/` is empty on the branch and
`git status --short` is clean. `rm -rf .mypy_cache __pycache__` was run after every probe. Probe
files in `/tmp` were removed. No microphone, clipboard, keystroke, window, notification, network,
`sudo`, package install or service action was performed; the only writes were `/tmp` probe scripts
and this review artifact.
