# 002 — review: whisper engine rebuild hardening (ticket 51)

- **Branch:** `feat/whisper-engine-rebuild-hardening`, tip `825b40d9b2e0245f2020a008083060faa306e5d2`
- **Base:** `main` @ `af7316c` (the merge of ticket 50)
- **Reviewed diff:** `git diff main...feat/whisper-engine-rebuild-hardening` (three-dot)
- **Reviewer:** independent (did not write the change)

## Verdict: **pass**

All six follow-ups from ticket 49's `002-review.md` (R1–R6) are closed, and **each one was
falsified by me** — the new/changed test was broken and observed to fail, then the source/test was
reverted with `git checkout --` and the tree confirmed clean. The two allowed `src/` behaviour
changes (the `--rebuild` notice and the notice wording) are the **only** changes to `whisper_engine.py`
outside the dead branch removal, and the policy matrix and staged-install code are textually
untouched. The one extra change (making `test_load_uses_the_model_override` hermetic) is test-only,
is the difference between a green and a red suite **on this machine**, and I reproduced its
non-hermeticity against a controlled fake engine **without touching the user's engine**.

The two DoD counts reproduce exactly:

```
$ timeout 600 uv run pytest -q
1230 passed in 9.65s

$ rm -rf .mypy_cache __pycache__ && timeout 600 uv run mypy src/ges2t tests
Success: no issues found in 64 source files
```

Baseline is real, not arithmetic: `git diff main...HEAD -- tests/ | grep -c '^+def test_'` = **2**,
removed = **0**; branch `pytest --collect-only -q` = `1230 tests collected in 0.55s`; clean `main`
(`af7316c`) today has 1228 tests (`1 failed, 1227 passed`) — the one failure is exactly the
non-hermetic `test_load_uses_the_model_override` under the user's real Vulkan engine (see DoD-extra).

---

## Per-criterion findings

### R1 — pin the ordering — **met**

`test_ensure_engine_vulkan_preference_without_vulkan_notices_nothing_first` is committed and
non-vacuous. Breaking the guard it names (deleting

```python
    if preference is GpuPreference.VULKAN and not vulkan.available:
        raise PrerequisiteError(check_prerequisites(BuildVariant.VULKAN, probe))
```

from `ensure_engine`) made the test fail with the false notice before the error, while the two
pre-existing error tests still passed — i.e. the gate is indeed redundant for the *error* and
load-bearing for the *ordering*:

```
$ timeout 300 uv run pytest -q \
    ...::test_ensure_engine_vulkan_preference_without_vulkan_notices_nothing_first \
    ...::test_ensure_engine_forced_vulkan_preference_without_vulkan_still_errors \
    ...::test_ensure_engine_vulkan_preference_without_vulkan_is_a_reported_error \
    ...::test_ensure_engine_vulkan_preference_over_an_existing_cpu_build_errors
E         Left contains one more item: 'rebuilding: the Vulkan dev packages are now available (was CPU-only)'
tests/test_whisper_engine.py:858: AssertionError
...
2 failed, 2 passed in 0.43s
```

(R6 also failed in that run — see R6.) Reverted; `git status --porcelain` empty.

### R2 — strengthen the weak test — **met**

`test_a_failed_fresh_build_leaves_no_engine_verify_build_accepts` now builds a **Vulkan** tree with
no `libggml-vulkan` and asserts `list(destination.iterdir()) == []` (plus no surviving
`.engine.staging-*`). I reproduced the exact staging break the ticket names — installing straight
into the destination (`_install_libraries(build_tree / "bin", destination, …)`) and moving the Vulkan
check after the copy loop:

```
$ timeout 300 uv run pytest -q ...::test_a_failed_fresh_build_leaves_no_engine_verify_build_accepts
        with pytest.raises(BuildFailedError):
            failing.build(_request(BuildVariant.VULKAN, destination))
        assert verify_build(destination) is None
>       assert list(destination.iterdir()) == []
E       AssertionError: assert [PosixPath('/...gml.so'), ...] == []
E         Left contains 12 more items, first extra item: PosixPath('.../engine/libwhisper.so.1.9.5')
tests/test_whisper_engine.py:1138: AssertionError
1 failed in 0.44s
```

The failure is at line **1138**, the new assertion; line **1137** (`assert verify_build(destination)
is None`) executed and passed. That is the old test's only assertion, so under the break the old
test would still have passed — exactly the R2 complaint. The first run of the mutation left **12**
orphan library files at the destination (the suffix names: 4 families × 3 symlink/file names; the
`libggml-vulkan` family is absent because `vulkan_library=False`). Reverted; tree clean.

### R3 — dead code — **met**

The old `f"rebuilding: {installed.variant.label} -> {desired.label}"` fallback is gone.
`_rebuild_reason` is now argument-free and returns the one sentence. The call site is guarded by
`installed is not None and _satisfies(installed.variant, desired)` (line 1442 returns early on
satisfaction), and `_rebuild_reason()` is called from exactly one place:

```
$ grep -rn "_rebuild_reason\|_forced_rebuild_reason" src/ges2t --include='*.py'
src/ges2t/whisper_engine.py:1363:def _rebuild_reason() -> str:
src/ges2t/whisper_engine.py:1378:def _forced_rebuild_reason() -> str:
src/ges2t/whisper_engine.py:1453:        _notify(notice, _forced_rebuild_reason())
src/ges2t/whisper_engine.py:1455:        _notify(notice, _rebuild_reason())
```

Exhausting the pairs (`installed.variant`, `desired`), both always `BuildVariant` members — a
manifest with an unknown variant is rejected by `_build_from_mapping`
(`BuildVariant(variant_text)` → `EngineManifestError` → `verify_build` returns `None`), and
`_desired_variant` returns only members:

```
$ timeout 120 uv run python - <<'PY'
from ges2t.whisper_engine import BuildVariant, _satisfies, _rebuild_reason
members = list(BuildVariant)
for installed in members:
    for desired in members:
        if not _satisfies(installed, desired):
            print(f"  {installed.value} -> {desired.value}")
PY
BuildVariant members: ['cpu', 'vulkan']
non-satisfied (installed, desired) pairs -- the only ones that reach _notify:
  cpu -> vulkan
count: 1
_rebuild_reason() -> 'rebuilding: the Vulkan dev packages are now available (was CPU-only)'
```

The single reachable pair is the one the surviving sentence describes, so removing the fallback is
behaviour-preserving. **No test covers the removed branch, because no input can reach it** — correct.

### R4 — `--rebuild` says why — **met**

`ensure_engine` now emits `_forced_rebuild_reason()` when `force=True`. Dropping that branch and
restoring the old `if installed is not None:` shape made the extended test fail:

```
$ timeout 300 uv run pytest -q ...::test_ensure_engine_force_rebuilds_an_already_best_build
>       assert messages == ["rebuilding: forced by --rebuild"]
E       AssertionError: assert [] == ['rebuilding:...by --rebuild']
1 failed in 0.45s
```

The CLI wiring is real: `cli._handle_setup` passes `notice=lambda message: print(message)` →
`run_setup(rebuild=…)` → `_run_whisper(force=rebuild, notice=notice)` →
`ensure_engine(force=…, notice=…)`. Reverted; tree clean. (There is no single end-to-end test that
`--rebuild` prints the *forced* line through the real `ensure_engine` — see Risks, minor.)

### R5 — notice wording — **met**

The notice is now `"rebuilding: the Vulkan dev packages are now available (was CPU-only)"`, and the
old "Vulkan development packages" is gone from `src/`:

```
$ grep -rn "Vulkan dev packages" src/ges2t --include='*.py'
whisper_engine.py:10   ... (docstring)
whisper_engine.py:1146 ... (docstring)
whisper_engine.py:1375 return "rebuilding: the Vulkan dev packages are now available (was CPU-only)"
transcribers/whisper_cpp.py:701  "the Vulkan dev packages present (run 'ges2t setup whisper.cpp') "
cli.py:321            "possible (install the Vulkan dev packages and re-run) and\n"
$ grep -rn "Vulkan development packages" src/ges2t --include='*.py'
(none)
```

Reverting the string to "Vulkan development packages" made the engine test fail on the exact
message:

```
$ timeout 300 uv run pytest -q ...::test_ensure_engine_upgrades_a_cpu_build_when_vulkan_becomes_available \
    ...::test_setup_prints_the_rebuild_reason_notice
E  At index 0 diff: 'rebuilding: the Vulkan development packages are now available (was CPU-only)' != 'rebuilding: the Vulkan dev packages are now available (was CPU-only)'
1 failed, 1 passed in 0.69s
```

Note: the **cli** test passed under the mutation because it feeds its own `notice_message` string
(see Risks). The decision to be generic (not name `spirv-headers`) is defensible and recorded: the
missing subset is unknown at rebuild time. "Consistent with the shared `vulkan_dev_package_hint`" is
satisfied in the project's established sense — `vulkan_dev_package_hint` enumerates the concrete
missing packages, and the notice uses the umbrella term the same feature already uses in
`cli.py`/`whisper_cpp.py`. Reverted; tree clean.

### R6 — the missing case — **met**

`test_ensure_engine_forced_vulkan_preference_without_vulkan_still_errors` is committed: existing
CPU build + `preference=VULKAN` + `force=True` + no Vulkan ⇒ typed `PrerequisiteError` naming the
`pacman` line, `builder.builds == 1`, `messages == []`. Under Probe 1 (gate removed) it failed on
`messages == []` with the forced line; under the stronger break (gate removed **and**
`_desired_variant` ignoring the `vulkan` pin) it failed as "silent CPU build":

```
$ timeout 300 uv run pytest -q ...::test_ensure_engine_forced_vulkan_preference_without_vulkan_still_errors
>       with pytest.raises(PrerequisiteError) as caught:
E       Failed: DID NOT RAISE PrerequisiteError
1 failed in 0.41s
```

So the test genuinely covers `force + gpu = "vulkan"` + no Vulkan, and it fails when that guarantee
is broken. Reverted; tree clean.

### R1/R4 messages vs the typed error — **met**

The ordering is load-bearing and tested: with `gpu="vulkan"` and no Vulkan, neither the automatic
"dev packages are now available" line (R1) nor the forced line (R6) precedes the typed error,
because the explicit gate sits before both `_notify` calls. I broke each and watched the message
appear before the error (Probe 1).

### DoD — pytest + mypy green, counts, no behaviour change — **met**

1230 passed / mypy 0 issues in 64 files (above). The **only** `src/` change is in three regions of
`whisper_engine.py` — proof the matrix and staging code are untouched:

```
$ git diff main...HEAD -- src/ | grep '^[+-]' | grep -v '^[+-][+-]'
-  _rebuild_reason(installed, desired)  (old signature + dead fallback)
+  _rebuild_reason() -> str             (one sentence)
+  _forced_rebuild_reason() -> str      (new)
   (ensure_engine docstring: one sentence about the forced notice)
-  if installed is not None: _notify(notice, _rebuild_reason(installed, desired))
+  if force: _notify(notice, _forced_rebuild_reason())
+  elif installed is not None: _notify(notice, _rebuild_reason())
```

`_satisfies`, `_desired_variant`, `WhisperCppBuilder.build`, `_install_libraries`,
`_staging_directory`, `_replace_directory` and `verify_build` are byte-identical to `main` (they do
not appear in the diff). The old `_rebuild_reason` returned the *same* sentence for the only
reachable input (CPU→VULKAN, R3), so the notice-wording change and the new forced notice are the
only observable differences. `files touched` = `whisper_engine.py`, three test files, README + docs:
no binary, no config, no unrelated module.

### DoD — `001-comment.md` records fixed vs declined — **met**

All six are listed as **Fixed**, each with the reason and its falsification probe. I reproduced
Probes 1–6 independently. One prose slip: the R2 paragraph says the break "leaves 13 orphan library
files", while the verbatim output in the same comment says `Left contains 12 more items`; the true
count is **12** (my Probe 2 produced 12). Documentation-only; non-blocking.

### Extra (outside R1–R6) — `test_load_uses_the_model_override` made hermetic — **met, and justified**

The test now pins `engine_directory=tmp_path`, so `gpu = auto` reads an empty directory (CPU)
instead of the machine store. I proved the mechanism with a **controlled fake Vulkan engine under a
temp `XDG_DATA_HOME`** (constructing an `engine.json` + `libwhisper.so` + `libggml.so`; verified
`verify_build` reads it as VULKAN), by temporarily removing the pin:

| Probe | Condition | Result |
|---|---|---|
| 7a | unpinned, XDG → fake Vulkan engine | **FAILED** `assert [(…, True)] == [(…, False)]` |
| 7b | unpinned, XDG → empty dir | passed (`use_gpu=False`) |
| 7c | pinned, XDG → fake Vulkan engine | passed |
| 7d | whole `tests/test_whisper_cpp.py`, XDG → fake Vulkan engine | `37 passed` |
| —  | whole suite, XDG → fake Vulkan engine | `1230 passed in 10.93s` |

So the test was **non-hermetic** and the pin is **load-bearing**; and no other test flips when a
Vulkan engine is present at the default path. I also reproduced the failure on clean `main`
(read-only against the user's real engine — the same default location, no modification):

```
$ (main worktree, af7316c) XDG_DATA_HOME= timeout 300 uv run pytest -q ...::test_load_uses_the_model_override
>       assert fake.loaded == [(model, False)]
E       AssertionError: assert [(…, True)] == [(…, False)]
1 failed in 0.19s
$ (main worktree) timeout 600 uv run pytest -q
1 failed, 1227 passed in 8.90s
```

Clean `main` is therefore currently red **only** because the user's real engine exists; this branch
makes it green. The fix is test-only, matches the sibling load tests, and does not weaken the
model-override assertion (which is what the test is about). Confirmed the user's engine was not
touched by the runs: `engine.json` sha256 and the directory mtime are byte-for-byte identical
before and after a full suite run (`ea756d1b…`, `2026-10-10 18:38:48`). Reverted; tree clean.

---

## Falsification campaign (real output)

Every probe: mutate → `rm -rf .mypy_cache __pycache__ …` → `timeout … pytest -q …` →
`git checkout -- <file>` → `rm -rf .mypy_cache __pycache__ …` → confirm clean.

| # | Guard broken | Test(s) run | Result | Reverted clean |
|---|---|---|---|---|
| 1 | delete the explicit `vulkan` gate | R1, R6, 2 pre-existing error tests | **R1 + R6 fail**; pre-existing 2 pass | ✔ `diff -q` identical |
| 2 | install into destination + Vulkan check after copy | R2 test | **fails** at the new assert (12 orphans); old assert passes | ✔ |
| 3 | (read-only) exhaust `(installed, desired)` | script | only `cpu -> vulkan` non-satisfied | n/a |
| 4 | drop the forced-notice branch | R4 test | **fails** `[] != ['rebuilding: forced by --rebuild']` | ✔ |
| 5 | revert notice wording | R5 engine test + cli test | engine **fails**; cli passes (fake message) | ✔ |
| 6 | remove gate **and** ignore the `vulkan` pin | R6 test | **fails** `DID NOT RAISE` (silent CPU) | ✔ |
| 7a–d | remove/add the `engine_directory` pin | model-override test (+ suite) | non-hermetic without the pin; hermetic with it | ✔ |

After the whole campaign: `git status --porcelain` empty on the branch, `git diff --stat` empty, no
mutated `src/` or `tests/`, no stray `.engine.staging-*`/`.engine.old`.

## Risks / recommended follow-ups (non-blocking)

- **The cli notice test is a forwarding test, not a wording guard.**
  `test_setup_prints_the_rebuild_reason_notice` sets `fakes.whisper_engine.notice_message` to the
  literal and then asserts that literal appears in stdout; it passed unchanged under Probe 5 when
  the real wording was mutated. The real wording is pinned only by the `test_whisper_engine` test.
  Fine as-is, but if the wording is ever judged important at the CLI boundary, assert on the real
  `_rebuild_reason()` value rather than a copied string.
- **No end-to-end "forced" CLI test.** `--rebuild` is wired and unit-tested at `ensure_engine`, and
  the CLI notice sink is tested separately, but no single test drives `--rebuild` through to the
  printed `rebuilding: forced by --rebuild`. Cheap to add with the existing fakes.
- **A forced rebuild on a machine missing a *general* prerequisite still prints the forced line
  before the error** (e.g. no `cmake`). The line is true (the user did pass `--rebuild`), so it is
  not a false claim, and the ticket's R1/R4 ordering guarantee is specifically about the
  `gpu="vulkan"`-with-no-Vulkan case, which is covered. Left as-is deliberately.
- **`001-comment.md` says "13 orphan library files" where its own output says 12.** Correct the
  prose if the comment is ever reused as evidence.
- **R5 "consistent with `vulkan_dev_package_hint`" is a judgment call.** The shared hint enumerates
  the concrete missing packages; the notice uses the project's umbrella term ("the Vulkan dev
  packages") that `cli.py` and `whisper_cpp.py` already use. Acceptable and documented.

## Could-not-verify

- **Provenance of the user's real engine build.** I confirmed it exists (`vulkan`, 46 MB
  `libggml-vulkan`, built 18:36–18:38) and that the suite does not modify it (sha256 + mtime
  unchanged across two full runs), but I cannot independently attribute *who* built it. The claim
  that it was "outside any command in this ticket" is consistent with the mtimes and with the fact
  that every test injects `FakeCommandRunner`/`FakeWhisperLibrary`, and no test writes under the
  real data dir.
- **A real `WhisperCppBuilder` run against real `git`/`cmake`** (the staging swap over a real
  compiler tree). R2 is proven at the *fake-runner* level; the real `SubprocessRunner` seam *is*
  exercised with real children (`sys.executable -c …`, bounded 1–30 s,
  `tests/test_whisper_engine.py:1170+`), but builder composition over that seam is not driven
  against a real build tree. Same limitation the ticket 49 review recorded.
- **The user-owned real E2E** (install `spirv-headers`, real clone + `cmake` build, real
  `engine.json`), which needs `sudo`, package installs and the network — all forbidden here.
- **The historical "1228" baseline as-of-then.** I confirmed clean `main` has **1228** tests and is
  today `1 failed, 1227 passed`; the "+2 new tests" delta is confirmed by the diff count
  (2 added, 0 removed) and `--collect-only` = 1230. I did not run `main` *before* the engine was
  installed, so the pre-install "1228 passed" is inferred, not re-measured.
- I did not exercise `default_config_path()` against the user's real config dir (the CLI tests use
  temp configs); out of scope for this ticket.

## What would falsify the central claim

Central claim: *every R1–R6 guarantee is now pinned by a committed test that fails when the
guarantee is broken, the only production behaviour change is the `--rebuild` notice plus the notice
wording, and the rebuild policy matrix and staged-install safety are unchanged.*

Any one of these would falsify it (the marked ones are the mutations I ran, and each **did** break
its test):

1. deleting the explicit `vulkan` gate leaves the R1/R6 ordering tests **green** (Probe 1 — they
   went red).
2. the staging break (install to destination + late Vulkan check) leaves the R2 test **green**
   (Probe 2 — it went red).
3. `_rebuild_reason`'s removed fallback is reachable from some `(installed, desired)` pair
   (Probe 3 — the only non-satisfied pair is CPU→VULKAN).
4. dropping the forced notice leaves the R4 test **green** (Probe 4 — it went red).
5. a wording change away from "the Vulkan dev packages" leaves the engine test **green** (Probe 5 —
   it went red).
6. `force + gpu="vulkan"` + no Vulkan returns a CPU build or succeeds (Probe 6 — the test failed
   with `DID NOT RAISE`).
7. any matrix/staging function appears in `git diff main...HEAD -- src/` (it does not), or a
   policy-matrix test flips under a `_satisfies`/`_desired_variant` mutation (ticket 49's campaign
   did that; this ticket did not touch those functions).

None hold.

## Safe to relay to the user?

**Yes.** The claims are structural (test strength, reachability, ordering) and each was
falsified-and-recovered; the only measured numbers are test counts (1230 / 1228 / 1227 / 64 / +2),
independently reproduced. One user-facing caveat worth relaying: **clean `main` is currently red on
this machine (1 failed) purely because the user's real Vulkan engine now exists**; this branch turns
it green by pinning the one non-hermetic test, and `main` should be merged with this fix (or the
user's engine will keep `main` red). This is not a timing/measurement artifact.

## Reviewer hygiene / machine safety attestation

- No implementation file was edited by me; the only write is this artifact.
- Every mutation was reverted with `git checkout --`; final `git status --porcelain` on the branch is
  empty and `git diff --stat` is empty; no `src/` left mutated in any worktree.
- The user's engine was **not** built, modified, moved or deleted; `engine.json` sha256 and the
  engine dir mtime are unchanged before/after the suite. All reproduction used a temp
  `XDG_DATA_HOME` fake engine under `/tmp`.
- No microphone, clipboard, keystroke, window, notification, `sudo`, package install, service
  change or network access. Probe scratch (`/tmp/probe-xdg`, `/tmp/probe-xdg-empty`, copied source
  backups) removed; `__pycache__`/`.mypy_cache`/`.pytest_cache` cleared in both worktrees.
