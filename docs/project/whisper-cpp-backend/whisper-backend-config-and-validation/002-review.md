# 002 — review: whisper backend config and startup validation

- **Ticket:** `docs/project/whisper-cpp-backend/whisper-backend-config-and-validation/ticket.md`
- **Branch:** `feat/whisper-backend-config-and-validation` (tip `47a3377`, worktree
  `.worktrees/whisper-backend-config-and-validation`)
- **Diff range read:** `git diff main...feat/whisper-backend-config-and-validation`
- **Verdict: `changes requested`** — the code is complete and every guard is falsifiable, but the
  new user-facing **CPU latency claim is not supported by the research** (`~6× real time`, see
  Finding D1) and must be corrected in `README.md` (and in ADR 0021 before sign-off). One
  non-blocking test-quality note (Finding T1).

Everything below was run by the reviewer in the branch worktree. I did not touch the
implementation; every mutation was reverted with `git checkout -- <file>` + `rm -rf .mypy_cache
__pycache__`, and the tree was `git status --porcelain`-clean at the end.

---

## Baseline (re-run, not trusted)

```
$ cd .worktrees/whisper-backend-config-and-validation
$ timeout 600 uv run pytest -q
1128 passed in 8.30s

$ rm -rf .mypy_cache __pycache__ && timeout 600 uv run mypy src/ges2t tests
Success: no issues found in 64 source files
```

The ticket's stated branch-point baseline is independently confirmed, not taken on trust. I
checked out the branch point `a3dcbf5` into a throwaway worktree and ran the suite against its
source with the existing venv:

```
$ git worktree add --detach /tmp/ges2t-a3dcbf5 a3dcbf5
$ cd /tmp/ges2t-a3dcbf5
$ PYTHONPATH=/tmp/ges2t-a3dcbf5/src .../.venv/bin/python -c "import ges2t; print(ges2t.__file__)"
ges2t from: /tmp/ges2t-a3dcbf5/src/ges2t/__init__.py
$ PYTHONPATH=/tmp/ges2t-a3dcbf5/src timeout 300 .../.venv/bin/python -m pytest -q -p no:cacheprovider
1108 passed in 9.97s
```

So `1108 → 1128` is real, and the `+20` is exactly the one new test file
(`tests/test_whisper_backend_config.py`, 20 tests); the three modified test files changed bodies,
not test counts (`32/37/20` before and after). The temp worktree was removed.

---

## Per-criterion findings against the Definition of Done

| DoD item | Verdict | Evidence |
|---|---|---|
| `transcriber.engine = "whisper_cpp"` accepted, selects the adapter; `whistle` default unchanged | **pass** | selector probe (below); `KNOWN_ENGINES == ("whistle", "whisper_cpp")`, `DEFAULT_ENGINE == "whistle"`; `test_whistle_is_still_the_default_and_still_streams` |
| needle model selectable too (default `whistle`) | **pass** | pre-existing ticket 44; `NEEDLE_MODELS == (whistle,)`, `DEFAULT_NEEDLE_MODEL == "whistle"`; `plan_setup(None, None)` → `SetupPlan(backend=NEEDLE, model='whistle')` |
| `[engine.whisper_cpp]` model/gpu/threads validated with typed errors | **pass** | `WhisperCppConfig.from_mapping` raises `WhisperCppConfigError` naming `engine.whisper_cpp.<key>`; `test_build_transcriber_still_validates_every_whisper_value` |
| Default model rule `medium-q5_0`/`small-q8_0`, overridable, falsifiable | **pass** | rule in `whisper_engine.default_whisper_model`; probes below (Vulkan branch off → `small-q8_0`); `test_an_explicit_model_wins_over_the_device_rule` |
| Startup rejects streaming / model-not-acquired / library-not-built with a clear message | **pass** | three rejection probes below; all typed `PipelineError`, fail closed |
| Model-vs-GPU sizing deliberately **not** checked | **pass** | no `gpu`/`GpuPreference`/sizing read in the validation path; probe (e) below |
| Service constructs and loads whisper at startup; load failure fatal | **pass** | `tests/test_whisper_cpp.py::test_startup_builds_and_loads_the_whisper_transcriber_once` and `..._is_fatal_with_a_clear_message` pass; `_startup` calls `transcriber.load()` once |
| ADR 0021 reviewed and presented for sign-off | **changes requested** | correction of `needle`→`whistle` is right (below); **one residual factual mismatch remains** (Finding D1) |
| User docs: engine selection, accuracy/latency trade, batch-only consequence | **changes requested** | present and mostly accurate, but the CPU latency claim is unsupported (Finding D1) |
| Tests: validation matrix + default rule + needle still streams, each falsified | **pass** | all falsifications reproduced below |
| `uv run pytest` and `uv run mypy src/ges2t tests` green | **pass** | 1128 passed; 0 / 64 |

---

## Falsification evidence (break it, watch it fail, revert)

Every probe below was run on the committed tree; the post-revert run is green again.

### 1. Selector — `KNOWN_ENGINES` is what admits `whisper_cpp`

```
MUTATION: KNOWN_ENGINES = ("whistle",)   (src/ges2t/config.py)
$ pytest -q tests/test_whisper_backend_config.py tests/test_config.py
E  ConfigError: transcriber.engine = 'whisper_cpp' is not a known engine; known engines: 'whistle'
FAILED ...::test_known_engines_adds_whisper_cpp_and_keeps_whistle_default
FAILED ...::test_config_parses_the_whisper_cpp_engine
FAILED ...::test_config_rejects_an_unknown_engine_naming_whisper_cpp
FAILED ...::test_the_generic_parse_never_probes_the_machine_for_a_model
4 failed, 49 passed
REVERT → 20 passed
```

(The ticket predicted 3 failures; the generic-parse test also writes a `whisper_cpp` config, so it
fails too — more coverage, not fewer.)

Default engine stayed `whistle` and Whistle still streams:

```
MUTATION: WhistleTranscriber.supports_streaming = False
$ pytest -q ...::test_whistle_is_still_the_default_and_still_streams
>   assert transcriber.supports_streaming is True
E   assert False is True
1 failed
REVERT → pass
```

### 2. The three startup rejections

Each check was disabled in turn; each test failed **seeing the wrong branch** (i.e. the test is
load-bearing, not decoration).

```
MUTATION 2a: `if False and config.streaming.enabled:` in _validate_whisper_config
FAILED ...::test_streaming_with_whisper_is_refused_naming_both_settings
  E  assert 'transcriber.engine = "whisper_cpp"' in "the whisper.cpp model 'tiny-q8_0' ... is not
     acquired or is incomplete ..."   # it fell through to the artifact error

MUTATION 2b: drop the `_present(model_path, ...)` branch
FAILED ...::test_a_model_that_is_not_acquired_is_refused
  E  assert 0 == 1        # _whisper_missing_artifacts returned []

MUTATION 2c: drop the `verify_build` branch
FAILED ...::test_a_library_that_is_not_built_is_refused
  E  assert 0 == 1        # _whisper_missing_artifacts returned []
REVERT each → pass
```

**Typed, fails closed, names the setting(s), with a `ges2t setup …` hint.** End-to-end probe with a
redirected `XDG_DATA_HOME` (real `model_store` / `verify_build`):

```
[a) streaming] refused:
  message: transcriber.engine = "whisper_cpp" cannot be combined with streaming.enabled = true:
           whisper.cpp is batch-only, so there would be no text until the utterance ends.
  hint   : set streaming.enabled = false to run whisper.cpp batch-only, or set
           transcriber.engine = "whistle" (the default) to keep live and incremental typing
[b) model missing] refused:
  message: the whisper.cpp model 'tiny-q8_0' (engine.whisper_cpp.model) is not acquired or is
           incomplete at /tmp/…/models/whisper-cpp/tiny-q8_0/ggml-tiny-q8_0.bin
  hint   : run 'ges2t setup whisper.cpp <model>' to build the engine and download the model
           (the only ges2t command that uses the network)
[c) library missing] refused:
  message: … (libwhisper.so is missing or incomplete)
  hint   : run 'ges2t setup whisper.cpp <model>' to build the engine and download the model …
[d) complete install] NO ERROR (validation passed)
```

Both `2b` and `2c` carry the `ges2t setup whisper.cpp <model>` hint (`_validate_whisper_config`
raises one error joining all problems with a single shared hint). `PipelineError` is the typed
exception and `run_service` returns non-zero; the test
`test_run_service_refuses_a_whisper_misconfig_before_any_load` asserts `built == []` (the
transcriber is never constructed after a refusal) — I ran it green.

### 3. No sizing check

`grep -n "size_bytes\|sizing\|gpu\|GpuPreference" src/ges2t/pipeline.py` in the validation path
returns only the docstrings and the model **file-size** pin (`pin.size_bytes`), never a
model-vs-device comparison. Probe (e): with a complete install, `{model: tiny-q8_0, gpu: vulkan}`
and `{model: tiny-q8_0, gpu: auto}` **pass** validation even though only a CPU engine is installed.
The `large-v3-turbo-q5_0` case refused only because that model was not acquired — the
model-not-acquired check, not a sizing check.

### 4. Device-aware default is one source of truth

```
MUTATION 4: `if False and inspect_vulkan(probe).available:` in default_whisper_model
FAILED ...::test_omitted_model_resolves_to_the_gpu_default_with_vulkan
  E  assert 'small-q8_0' == 'medium-q5_0'
(2 other tests still passed — see note below)

MUTATION 4b: make plan_setup ignore the shared rule (`resolved = DEFAULT_WHISPER_MODEL_CPU`)
FAILED ...::test_the_rule_is_one_source_of_truth_for_setup_and_startup
  E  assert 'small-q8_0' == 'medium-q5_0'
```

Mutations 4 and 4b are complementary: the GPU-default test pins the **value**, and the
one-source-of-truth test pins that **setup and startup use the same function** (it fails when
`plan_setup` diverges). Forcing the Vulkan branch off does **not** fail the one-source-of-truth
test by itself, because both sides then return `small-q8_0` — the test compares setup to startup,
not to a literal. That is the correct division of labour, and both halves were seen to fail.

`load_config` never probes hardware. The ticket's own test
(`test_the_generic_parse_never_probes_the_machine_for_a_model`) monkeypatches
`whisper_engine.default_whisper_model`; I ran it green, and then independently proved the stronger
property with an instrumented probe that records `HostSystemProbe`/`inspect_vulkan`/`shutil.which`
/`Path.is_file`:

```
engine: whisper_cpp
block preserved verbatim: {'language': 'en'}
probe/inspect calls during load_config: []
total 'is_file' calls: 0
```

**Finding T1 (non-blocking).** The shipped test is *blind to a direct probe*: I mutated
`load_config` to call `whisper_engine.inspect_vulkan(whisper_engine.HostSystemProbe())` directly and
the test still passed (`1 passed`). It only guards a call routed through
`whisper_engine.default_whisper_model`. The property holds (proved independently above), but the
test is weaker than its name. Recommend either asserting on the instrumented probe, or renaming the
test to say it guards the *rule* call, not any hardware probe.

### 5. Where "omitted" is resolved — build time, not parse

```
MUTATION 5: `if False and whisper_config.model is None:` in resolve_whisper_cpp_config
FAILED ...::test_omitted_model_resolves_to_the_cpu_default_without_vulkan
FAILED ...::test_omitted_model_resolves_to_the_gpu_default_with_vulkan
FAILED ...::test_partial_vulkan_resolves_to_the_cpu_default
FAILED ...::test_the_rule_is_one_source_of_truth_for_setup_and_startup
FAILED ...::test_build_transcriber_resolves_an_omitted_whisper_model
5 failed, 15 passed
```

(The ticket said 4; there are 5 because `test_build_transcriber_resolves_an_omitted_whisper_model`
also fails.)

`WhisperCppConfig.from_mapping` leaves the model `None` (`test_an_omitted_model_is_left_unresolved…`
passes), and a **directly-constructed adapter still resolves a safety-net default**:

```
$ uv run python -c "…"
parsed model: None
adapter._model_name(): small-q8_0
default_whisper_model(): small-q8_0
model path: /home/matee/.local/share/ges2t/models/whisper-cpp/small-q8_0/ggml-small-q8_0.bin
```

Falsified: removing the safety net makes a directly-constructed adapter return `None`
(`adapter._model_name() with no safety net: None`), which would break `whisper_model_path`.
No test currently pins this adapter-level fallback; the wiring is real, but only the composition
root's resolution is tested. (Small coverage gap; not a blocker.)

---

## ADR 0021 review

### The correction is right

`transcriber.engine` is a **config value** and its default is `whistle`; `needle` is the
`ges2t setup` CLI token. Confirmed in code:

- `src/ges2t/config.py`: `KNOWN_ENGINES = ("whistle", "whisper_cpp")`, `DEFAULT_ENGINE =
  KNOWN_ENGINES[0] == "whistle"`.
- `src/ges2t/setup_command.py`: `SetupBackend.NEEDLE = "needle"` (unchanged; correct as the CLI
  token).

The three ADR edits (`§1 "needle"→"whistle"`, `§10 "needle backend"→"whistle engine"`, escape
hatch `needle`→`whistle`) are accurate. Every remaining `needle` in the ADR is legitimate:
the `libneedle3.so` library name (§"in-process"), the `ges2t setup needle` token (§6), the
"needle model" prose (§6/§10), and "the exposure ADR 0006 accepts for Needle" (Consequences).

All other config-value claims check out against the code: `medium-q5_0`/`small-q8_0` (the model
catalogue's `q5_0` entries are exactly `medium`/`large-v3-turbo`; `q8_0` for every size; never
`f16`); `-nt`/greedy pinned (`NO_TIMESTAMPS = True`, `SAMPLING_GREEDY = 0`, `BEST_OF = 1`,
`BEAM_SIZE = 1`); `ges2t setup [backend] [model]` behaviour (`plan_setup(None, None)` → needle +
whistle; `whisper.cpp` + no model → `small-q8_0` CPU / `medium-q5_0` Vulkan); strict startup
validation with no model-vs-GPU sizing. The ADR is still `proposed`, i.e. correctly awaiting the
orchestrator's sign-off step.

### Finding D1 (blocking) — a measured-looking latency claim that the research does not support

ADR 0021 §"Why `medium q5_0` on GPU, `small q8_0` on CPU-only":

> **CPU, however, is ~6× real time** (medium ~24 s for a 30 s clip), so on a CPU-only machine the
> sensible default is the cheaper `small q8_0` …

and the **new** README §"Engines" (added by this ticket):

> at the cost of latency: **~1.7–3 s per 25 s utterance** on the reference Radeon 780M via Vulkan,
> and about **6× real time** on CPU-only machines.

The "~6× real time" is not the batch CPU cost. From
`research/whisper-cpp-provider/REPORT.md` §8 (`RTF = audio_seconds / wall_seconds`; higher is
faster):

| model | CPU wall s | CPU RTF | Vulkan wall s | Vulkan RTF |
|---|---|---|---|---|
| small | 6.45 | 3.9 | 0.87 | 28.9 |
| medium | 19.29 | 1.30 | 1.72 | 14.6 |
| large-v3-turbo | 14.95 | 1.68 | 1.47 | 17.1 |

Batch CPU is **faster** than realtime (medium RTF 1.30 = 0.77× realtime; the CPU-only **default**
`small-q8_0` RTF 4.3 = 0.23× realtime). The ADR's own parenthetical agrees ("medium ~24 s for a
30 s clip" ≈ 0.8× realtime) and contradicts the "~6×" in the same sentence. The `~6×` figure
actually matches the **streaming** CPU duty from `research/whisper-cpp-streaming/REPORT.md`
("`medium q5_0` **CPU** reference at W=5/H=2 is duty **5.87** / **6.11**") — a regime that does
not apply, because whisper.cpp is batch-only by this very ADR. This is a measured-looking number
used outside the measurement it came from; it inflates the CPU cost by roughly 5×.

- **Blocking scope:** correct `README.md` line 436 (e.g. the CPU-only default `small-q8_0` is
  ~6 s for a 25 s utterance, `medium` ~19 s — i.e. a few seconds to ~20 s, not "6× real time"),
  and correct the same sentence in ADR 0021 before the orchestrator presents it for sign-off.
- The `~1.7–3 s` Vulkan figure is defensible as a conservative range around the measured 1.72 s
  (25 s clip) / 2.07 s (30 s clip); I would not block on it, though "~2 s" is the measured number.
- The accuracy claims are correct: Whistle `word_diff` 0.281 batch / 0.391 streaming (0.391 is
  reproduced in `live-dictation-ux/accuracy-levers/` artifacts), whisper `medium-q5_0` 0.062, and
  `0.281/0.0625 = 4.50 ≈ 4.5× fewer errors`. The batch-only consequence ("no text until the
  utterance ends") matches the streaming research. The README's "no automatic fallback (ADR 0020)"
  is correct in code.

**Adjacent (low severity, pre-existing, not in this diff):** the epic and `docs/project/README.md`
still say the default backend "stays `needle`/Whistle". Since the ADR deliberately corrected the
config *value* vocabulary to `whistle`, those concept-level mentions are now the only remaining
`needle`-as-default prose. Worth a one-line tidy while the ADR is in front of the user.

---

## Scope and hygiene

- **Generic-layer change is exactly one line** — `KNOWN_ENGINES` in `config.py` (plus a comment).
  `git diff main...HEAD -- src/ges2t/config.py` shows nothing else. No new `[transcriber.*]` keys.
- **ADR 0019 style:** `mypy` with `disallow_untyped_defs = true` passes on `src/ges2t` and
  `tests` (0 / 64); new functions are fully annotated.
- **No auto-fallback between engines (ADR 0020):** `_TRANSCRIBER_BUILDERS.get(engine)` raises
  `PipelineError` for an unknown engine; `_select_session_kind` never switches engine. The only
  auto behaviour is device-level (`GpuPreference.AUTO`), and `gpu = "vulkan"` on a CPU engine
  raises `WhisperCppLoadError` rather than silently using the CPU.
- **No binary or model committed:** the branch diff is all `.py`/`.md` (`git diff --numstat` has no
  `-` binary rows); no tracked file > 1 MB.
- **Clean after probing:** `git status --porcelain` empty; `.mypy_cache`/`__pycache__` removed;
  the throwaway `a3dcbf5` worktree removed. No `src/` file was left mutated.

---

## Risks / follow-up tickets

1. **(D1) Correct the CPU latency claim** in the README and ADR 0021 before merge/sign-off (the
   blocking item).
2. **(T1) Strengthen the pure-parse test** so it fails on a direct `HostSystemProbe`/`inspect_vulkan`
   call, or rename it to state its actual scope.
3. **Pin the adapter's safety-net default** with a test (`WhisperCppTranscriber(WhisperCppConfig())`
   resolves a model) — currently only the composition-root path is covered.
4. Nothing in this ticket checks the *device* default against the *installed engine* at startup
   (model-vs-GPU sizing is intentionally out of scope; `gpu = "vulkan"` on a CPU build fails at
   `load()`, which is fatal — that is the designed behaviour, not a gap).

---

## Could not verify

- **The real whisper.cpp artifact matrix on this machine.** The checks use the real
  `model_store.whisper_model_path`/`whisper_model`/`verify_build` with a redirected `XDG_DATA_HOME`
  and a sparse pinned file / empty library. I did not build `libwhisper.so` or download a ~500 MB
  model (no network, no packages), so "the service refuses to start" is proven against the real
  *path and manifest logic*, not against a full user install. This is the same boundary ticket 43
  drew and is honest.
- **The ADR's `~6× real time` and `~1.7–3 s` numbers were not re-measured** — I traced them to the
  research report's tables; the `6×` is a transcription error (see D1), and the batch CPU numbers
  in that report are the authority.
- **The Vulkan-default branch on real hardware.** This machine's `HostSystemProbe` reports no
  Vulkan build (so the default resolves to `small-q8_0`); the `medium-q5_0` branch is exercised only
  through `FakeSystemProbe`. `default_whisper_model` is read-only here — no device was driven.
- **`--config` end-to-end through the CLI** was not re-run; the config work was exercised at the
  `load_config`/`build_transcriber`/`_validate_whisper_config` seams, which is where the change
  lives.

---

## What would falsify the central claim

The central claim is: *"`transcriber.engine = "whisper_cpp"` is selectable, its model is resolved
by one device-aware rule shared with `ges2t setup`, and a whisper config that cannot work is
refused at startup (streaming / model / library) while the default `whistle` engine is unchanged."*

Falsifiers, each reproducible:

- Removing `"whisper_cpp"` from `KNOWN_ENGINES` makes the selector tests fail (shown).
- Forcing the Vulkan branch off in `default_whisper_model` flips `medium-q5_0`→`small-q8_0` and
  fails the GPU-default test (shown).
- Making `plan_setup` diverge from the shared rule fails the one-source-of-truth test (shown).
- Disabling any of the three startup checks makes its test fail, each seeing the *wrong branch*
  (shown).
- Adding a model-vs-GPU sizing check would make `test_model_vs_gpu_sizing_is_not_checked` fail —
  it is absent, so the check passes (shown).
- If `load_config` probed hardware, my instrumented probe would record a call (it records none).
- If whisper + streaming were silently downgraded rather than refused, the streaming test would
  see the artifact error (as it does under mutation 2a, which is why it fails).

## Safe to relay to the user?

- **Code, tests, startup validation, the model rule, and the ADR's `whistle` correction: yes.**
  Every guard was broken and watched to fail; the suite (1128) and `mypy` (0/64) are green; the
  tree is clean and nothing was left mutated.
- **The README/ADR CPU latency figure ("~6× real time"): no** — it is unsupported by the research
  and should be corrected before the docs are relayed (Finding D1). Everything else in the new
  engine-selection/accuracy/batch-only text is accurate.
