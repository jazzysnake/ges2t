# 003 — review: fixes for `002-review.md` D1 (blocking) and T1

- **Ticket:** `docs/project/whisper-cpp-backend/whisper-backend-config-and-validation/ticket.md`
- **Branch:** `feat/whisper-backend-config-and-validation` — tip `7cd7978`
- **Fix commits:** `b82c2e8` (D1 + vocabulary), `7cd7978` (T1 + adapter safety-net test)
- **Prior review:** `002-review.md` (`915fac6`) — verdict `changes requested`
- **Verdict: `pass` — safe to merge.**

Everything below was run by the reviewer in the branch worktree. Each mutation was reverted with
`git checkout -- <file>` + `rm -rf .mypy_cache __pycache__`; the tree is `git status --porcelain`
clean, and no `src/` file was left mutated.

---

## 1. Scope — docs + tests only, no `src/` behaviour change

```
$ git diff 915fac6..HEAD --name-only
README.md
docs/adr/0021-whisper-cpp-second-backend.md
docs/project/README.md
docs/project/whisper-cpp-backend/epic.md
docs/project/whisper-cpp-backend/whisper-backend-config-and-validation/003-comment.md
docs/project/whisper-cpp-backend/whisper-backend-config-and-validation/ticket.md
docs/project/whisper-cpp-backend/whisper-doctor/ticket.md
docs/project/whisper-cpp-backend/whisper-engine-build/ticket.md
docs/project/whisper-cpp-backend/whisper-transcriber-adapter/ticket.md
tests/test_whisper_backend_config.py

$ git diff 47a3377..HEAD -- src
(empty)
```

No `src/ges2t/` behaviour changed since the reviewed tip `47a3377`; the only code touched is the
new/strengthened tests. `002-review.md` is byte-unchanged (`git diff 915fac6..HEAD -- …/002-review.md`
empty).

## 2. Regression

```
$ timeout 600 uv run pytest -q
1129 passed in 7.91s

$ rm -rf .mypy_cache __pycache__ && timeout 600 uv run mypy src/ges2t tests
Success: no issues found in 64 source files

$ git status --porcelain
(empty)
```

`1128 → 1129` is the one new test (`test_a_directly_constructed_adapter_resolves_the_default_model`);
the pure-parse test was renamed, not counted twice.

---

## 3. D1 (blocking) — the unsupported "~6× real time" claim is gone from every live doc

`git grep -n "6×\|6x" -- . ':!docs/project/research'` now returns, for the claim:

| location | classification |
|---|---|
| `docs/adr/0021-whisper-cpp-second-backend.md:57` | **intentional origin note** — "(The "~6× real time" figure seen elsewhere is whisper's **streaming** CPU duty, a regime this ADR's batch-only decision excludes.)" |
| `…/whisper-backend-config-and-validation/002-review.md` (lines 8, 255, 261, 263, 275, 282, 338, 375) | historical review artifact (mine; unchanged) |
| `…/whisper-backend-config-and-validation/003-comment.md` (lines 10, 13, 14, 32, 37) | the implementer's comment artifact (describes the fix) |

The other `6x` hits are unrelated (`measure_batched_emission.py` hex offsets, `silence-gate`
"2.6×" amplitude, `whistle-streaming-adapter` "0.126× realtime"). **No live doc carries the claim.**

The replacements match `research/whisper-cpp-provider/REPORT.md` §8 exactly:

```
research §8:  small   CPU 6.45 s  RTF 3.9  | Vulkan 0.87 s RTF 28.9
              medium  CPU 19.29 s RTF 1.30 | Vulkan 1.72 s RTF 14.6
              turbo   CPU 14.95 s RTF 1.68 | Vulkan 1.47 s RTF 17.1
```

- **ADR 0021** — "**CPU is slower than Vulkan but still faster than realtime in batch**: on that
  same 25 s clip `medium q5_0` costs **RTF 1.30** (~19.3 s, ≈0.77× realtime) and the cheaper
  `small q8_0` **RTF 4.3** (~5.9 s, ≈0.23× realtime) (`RTF = audio_seconds / wall_seconds`; provider
  REPORT §8)." ✓ (the `small q8_0` figure is §5's 5.86 s / RTF 4.3.)
- **README.md:436** — "on that 25 s clip it runs in **~1.7 s** … on CPU-only machines it is still
  **faster than realtime** (`medium-q5_0` ~19 s, the CPU default `small-q8_0` ~6 s)." ✓
- **`whisper-doctor/ticket.md`, `whisper-engine-build/ticket.md`, `whisper-transcriber-adapter/ticket.md`**
  — all replaced with RTF 1.30 / ~19 s / `small-q8_0` (RTF 4.3, ~6 s); the adapter ticket also
  fixed its `small-q5_0` → `small-q8_0` slip. ✓
- The epic and `docs/project/README.md` never carried the `6×` claim; they keep the older
  "~1.7–3 s" Vulkan approximation (see residual risks).

**ADR decision text is now factually accurate.** Spot-checked against the research: CPU RTF
1.30/4.3 (§8/§5); Vulkan 1.72 s (§8); accuracy 0.062 vs 0.281/0.391 ≈ 4.5× with `-nt` (~1.8× on
the CLI default); `-nt` 0.0625 vs 0.1562; batch-only WER 2.3–4.8× worse / 81% flush tail /
boundary/silence hallucination; "5 s chunks 0.266 / 10 s chunks 0.141" matches
`research/whisper-cpp-streaming/data/timings.md` (`wer_worddiff 0.2656`) and the cap W=10/2 car
(0.141); ~0.8 GB resident (CPU RSS 828 MB). No unsupported number found.

**ADR-accuracy line for sign-off:** *ADR 0021's decision text is factually accurate — the CPU
latency claim now matches provider REPORT §8 (medium RTF 1.30 / small-q8_0 RTF 4.3, both faster
than realtime), and the remaining quantitative claims are supported by the provider and streaming
research; the only caveats are the epic's/docs-README's older "~1.7–3 s" approximation (vs the
measured 1.72 s) and the deliberately-retained origin note.*

## 4. T1 — the pure-parse test now fails on a real probe

The test (`test_the_generic_parse_never_probes_the_machine`, renamed) instruments **every**
reachable entry point — `whisper_engine.HostSystemProbe` (constructor + 3 methods),
`whisper_engine.inspect_vulkan`, `whisper_engine.default_whisper_model`,
`whisper_engine.shutil.which`, and `Path.is_file` — proves the guard is live (a direct
`inspect_vulkan(HostSystemProbe())` is recorded), then asserts `load_config` adds nothing. It is
**not** merely monkeypatching the default-model rule.

I reproduced the falsification independently, injecting a **direct** probe into `load_config`:

```
MUTATION: whisper_engine.inspect_vulkan(whisper_engine.HostSystemProbe())  # inside load_config
$ pytest -q tests/test_whisper_backend_config.py -k never_probes
>       assert probes == []
E       AssertionError: assert ['HostSystemP...spect_vulkan'] == []
E         Left contains 2 more items, first extra item: 'HostSystemProbe'
FAILED tests/test_whisper_backend_config.py::test_the_generic_parse_never_probes_the_machine
1 failed, 20 deselected
REVERT → green
```

This is exactly the mutation that left the shipped 002 version green; it now fails. (The check
cannot pass vacuously: the pre-assert forces the recording machinery to have fired.)

## 5. Adapter safety-net test

`test_a_directly_constructed_adapter_resolves_the_default_model` pins that a bare
`WhisperCppTranscriber(WhisperCppConfig())` keeps `config.model is None`, resolves `_model_name()`
to the rule once (cached), and `_model_path()` is the catalogue path for the resolved name. Broken
and reverted:

```
MUTATION: `if False and self._resolved_model is None:` in _model_name
FAILED tests/test_whisper_backend_config.py::test_a_directly_constructed_adapter_resolves_the_default_model
E       AssertionError: assert None == 'tiny-q8_0'
REVERT → green
```

## 6. Vocabulary — `whistle` is the engine value, `needle` is the CLI token

Every default-engine statement now says `whistle`: ADR §1/§10 and the escape hatch, the epic (3
places), `docs/project/README.md`, the config ticket (prose + DoD), and README §"Engines" ("**`whistle` (the default)**"). `ges2t setup needle …` and the Needle 3 / `libneedle.so` /
`cactus-needle` family mentions are left as-is (correct). `git grep 'engine = "needle"'` finds no
config-value claim. **Two loose shorthands remain** (non-blocking, see risks):
`whisper-doctor/ticket.md` "With `needle` selected" and the config ticket's DoD "that `needle`
still streams" — neither asserts a config value, but `whistle` would be tighter.

---

## Residual risks / recommended follow-ups (all non-blocking)

1. **Loose `needle` shorthand** in `whisper-doctor/ticket.md` ("With `needle` selected") and the
   config ticket's DoD line ("`needle` still streams"). No factual `transcriber.engine = "needle"`
   claim exists anywhere; a one-word tidy to `whistle` would make the vocabulary uniform.
2. **`~1.7–3 s`** survives in `epic.md:21` and `docs/project/README.md:91` for the Vulkan decode.
   The measured value is 1.72 s (25 s clip) / 2.07 s (30 s clip); "~1.7–3 s" is a conservative
   range, not the `6×` defect. Optional tidy.
3. The ADR keeps the **origin note** naming the streaming-duty source of the old `~6×` figure —
   intentional, and the right way to prevent re-introduction.

## Could not verify

- The batch CPU/Vulkan numbers were **traced to the research tables, not re-measured** (no build,
  no model download, no network on this machine). The replacement text is a faithful transcription
  of REPORT §8/§5.
- No real whisper.cpp install exists here, so the artifact matrix is still exercised through the
  real `model_store`/`verify_build` logic with sparse files and a redirected `XDG_DATA_HOME`
  (unchanged from reviews 002/001).

## Safe to merge?

**Yes.** Scope is docs + tests only; no `src/` behaviour changed since the already-verified
`47a3377`. D1 is fixed with research-sourced numbers, T1 now genuinely fails on a direct probe, the
adapter safety-net default is pinned, the durable vocabulary is `whistle`, and the suite/type check
are green (1129 passed; 0 / 64) with a clean tree. ADR 0021's decision text is factually accurate
and ready for the orchestrator to present for sign-off.
