# 002 — Review: model store (pinned download, verification, atomic install)

- **Ticket:** `ticket.md` (model-store-and-verification)
- **Branch:** `feat/model-store-and-verification`
- **Worktree:** `.worktrees/model-store-and-verification`
- **Reviewed commits:** `3e56c7d` (ticket) and `1049c7b` (pre-review progress fix) — HEAD `1049c7b`
- **Base:** `main` at `82c49ac` (`board: ticket 3 (config-loading) is done`); `main` has since advanced to include ticket 2 (`interfaces.py`) and ticket 4
- **Reviewer:** independent review run from the worktree above. Every command below was re-run
  by me; no checkbox in `001-comment.md` was trusted. The worktree was clean before and after,
  and the temporary `interfaces.py`/probe I added for the integration check were fully removed
  (`git status --porcelain --untracked-files=all` empty).

## Verdict

**pass**

No blocking findings. Every Definition-of-done item is genuinely satisfied when re-run: the
pins match ADR 0017 character-for-character, atomicity holds by experiment (an interrupted or
mismatched download leaves neither a destination nor a `.part` file), the size-only fast path
does no fetch *and no hash*, the engine is extracted from the wheel with `zipfile`, and
`env_overrides()` returns exactly the five variables. The pre-review progress fix is real: with
`main`'s `src/ges2t/interfaces.py` temporarily restored, an annotated probe passing an
`interfaces.ProgressCallback` into `model_store.ensure_model`/`ensure_engine` is **accepted** by
`uv run mypy src/ges2t`, and the deliberately wrong `Callable[[str], None]` shape is
**rejected** — so the structural match holds against the actual interface, not against the
comment file.

Minor, non-blocking observations are listed at the end (`Any` in one helper, a test
`__exit__` annotation, a malformed-`Content-Length` test gap, a stale snippet in the comment
file, and the missing public "present with pinned size" helper). The orchestrator must resolve
a `docs/project/README.md` merge conflict when merging (`main` changed rows 2 and 4; this
branch changed row 5).

## What was reviewed

`git diff --name-only main...HEAD` is exactly:

```
docs/project/README.md
docs/project/core-dictation-pipeline/model-store-and-verification/001-comment.md
docs/project/core-dictation-pipeline/model-store-and-verification/ticket.md
src/ges2t/model_store.py
tests/test_model_store.py
```

As **source**, exactly `src/ges2t/model_store.py` and `tests/test_model_store.py` were added.
`git diff main...HEAD -- src/ges2t/config.py` is empty — `config.py` is untouched.
`docs/project/README.md` changed only row 5 (status `in review`, branch
`feat/model-store-and-verification`); `ticket.md` changed only its `Status:` line to
`in review`. `pyproject.toml`/`uv.lock` are not in the diff: no dependency was added.

## Pins vs ADR 0017 — character-by-character PASS

I imported the module and compared the emitted values against ADR 0017's table, not against
`001-comment.md`:

```
MODEL_URL   = 'https://huggingface.co/Cactus-Compute/whistle/resolve/main/whistle.cact'
ENGINE_URL  = 'https://huggingface.co/Cactus-Compute/needle3/resolve/main/python/cactus_needle-3.2.0-py3-none-manylinux2014_x86_64.whl'
MODEL_SHA256= 'b6e02f048568ac5d01a2042556c658061e699acbc0aa2a1439f52f3d461dffeb'
ENGINE_SHA256= '0772130a416cc7a07fe444bfe400239a595195324f595b2139391839874fd030'
MODEL_SIZE  = 16919407
ENGINE_SIZE = 1581448
ENGINE_MEMBER= 'needle/libneedle3.so'
PLATFORM_KEY= linux-x86_64

MODEL_URL            MATCH
MODEL_SHA256         MATCH
MODEL_SIZE           MATCH
ENGINE_WHEEL_URL     MATCH
ENGINE_SHA256        MATCH
ENGINE_SIZE          MATCH
ENGINE_MEMBER       MATCH
ALL PINS MATCH ADR 0017: True
```

`MODEL_PIN`/`ENGINE_PIN` are built from those same constants, so `verify()` and the acquisition
path cannot drift.

**Independent on-disk cross-check.** The manual step is not otherwise re-verifiable offline, so
I hashed the artifacts it claims to have produced, in the real data dir (read-only):

```
16919407  /home/matee/.local/share/ges2t/models/whistle/2.0.0/whistle.cact
 1581448  /home/matee/.local/share/ges2t/engine/3.2.0/libneedle.so
b6e02f048568ac5d01a2042556c658061e699acbc0aa2a1439f52f3d461dffeb  .../whistle.cact
0772130a416cc7a07fe444bfe400239a595195324f595b2139391839874fd030  .../libneedle.so
(no *.part files under ~/.local/share/ges2t)
```

Both sizes and hashes equal the ADR pins exactly.

## Atomicity — by experiment, not by reading PASS

The ticket requires that an interrupted run cannot leave a file that later passes an existence
check. I drove the real `_ensure` with injected fetchers (no network):

```
== Experiment 1: interrupted fetch mid-write ==
  raised: ConnectionError drop mid-download
  destination exists: False | .part files: []

== Experiment 2: hash mismatch leaves nothing ==
  raised ArtifactVerificationError
  msg: x.cact: pinned sha256 8e9f... (69 bytes), but downloaded sha256 ...   # names artifact + expected + actual
  destination exists: False | .part files: []

== Experiment 3: os.replace strictly after successful hash ==
  installed: True content ok: True
  event order: [('hash', '.whistle.cact.lb8w65fb.part'), ('replace', 'whistle.cact')]
  hash before replace: True
```

- Interrupted fetcher → only the caller's own exception propagates; the `finally` removes the
  partial temp and the destination never appears.
- Wrong hash/size → `ArtifactVerificationError` naming the artifact, expected hash+size and the
  actual hash+size; destination absent and no `.part` left.
- Instrumenting `_sha256_file` and `os.replace` confirms the hash is computed on the temp file
  **before** the rename, in exactly that order, and the rename is within the destination
  directory.

## Size-only fast path, and the no-hash policy PASS

With a right-size file present I watched both a fetcher and `_hash`:

```
== Experiment 4: size-only fast path, no fetch, no hash ==
  returned destination: True
  fetch calls: [] | hash calls: []
```

The policy is documented both where it is enforced
(`_present_with_pinned_size` docstring: *"Deliberately not a hash check … Do not 'optimise' this
into a hash"*) and in ADR 0017 ("at normal load time, verify **existence and byte size**
(cheap) and defer the hash"). The dedicated test
`test_present_file_of_pinned_size_is_returned_without_fetching` uses a right-size but
wrong-content file and asserts `fetcher.calls == []`, which locks in the size-only decision
(a hash-every-load implementation would fail it). I accept the deliberate trust of
right-size/wrong-content files: `verify()` is the explicit escape hatch, and this is the ADR.

## Engine extraction, no `pip`, no `~/.cache/cactus-needle` PASS

- `_extract_member` opens the fetched wheel with `zipfile.ZipFile`, `getinfo("needle/libneedle3.so")`,
  and `shutil.copyfileobj` to a sibling temp; the **extracted `.so`** is then hashed and sized.
  A wrong member content test raises `ArtifactVerificationError`; a missing member raises a
  clear `ModelStoreError` naming the member.
- `grep -rniE "pip install|subprocess|cactus-needle" src/ges2t/model_store.py` finds only
  docstrings/comments; there is no `pip`, no `subprocess`, no write under `~/.cache`. The only
  filesystem writes are `destination.parent.mkdir`, `mkstemp` siblings, and `os.replace`.
- A fresh-interpreter import probe reports `BAD_IMPORTS: []` for `ges2t.interfaces`,
  `cactus_needle`, and `needle`, and the module's only imports are stdlib
  (`hashlib, os, platform, shutil, sys, tempfile, urllib.request, zipfile, collections.abc,
  dataclasses, pathlib, typing`). It is therefore importable with no backend installed, as
  `ges2t models`/`doctor` require.

## `env_overrides()` and write-scope PASS

```
== Experiment 5: env_overrides exactly five, managed paths ==
  keys: ['DO_NOT_TRACK', 'HF_HUB_OFFLINE', 'NEEDLE3_LIB_PATH', 'NEEDLE_TELEMETRY', 'NEEDLE_WHISTLE_WEIGHTS']
  values via default_data_dir: True True
  constants: {'NEEDLE_TELEMETRY': '0', 'DO_NOT_TRACK': '1', 'HF_HUB_OFFLINE': '1'}
```

Exactly the five ADR variables, with both paths recomputed from `default_data_dir()` (so
`XDG_DATA_HOME` is honoured and the values cannot drift from `model_path()`/`engine_path()`).
Every write path derives its directory from a `model_path()`/`engine_path()` result, i.e. from
`default_data_dir()`; nothing else is opened for writing.

## `urllib.request` only, no new HTTP dependency PASS

`grep -rnE "urllib|httpx|requests|socket" src/` returns hits only in `model_store.py` (the
acquisition path), matching ADR 0018's audit rule. `pyproject.toml` still lists only
`cactus-needle==3.1.3`, `pystray`, `pillow` — no HTTP client added. Redirects are followed by
the default urllib opener; `_download` names that explicitly (HF `/resolve/` → CDN).

## Progress fix — structural match verified against the real interface

The fix's own claim ("structurally identical to `ges2t.interfaces.ProgressCallback`") was
checked the way the orchestrator asked: restore `main`'s `src/ges2t/interfaces.py` into the
worktree, add a throwaway annotated probe, run mypy, then remove both.

**Correct callback — accepted:**

```
$ git show main:src/ges2t/interfaces.py > src/ges2t/interfaces.py
$ cat > src/ges2t/_probe_progress.py   # run_model/run_engine/run_optional, all annotated,
                                       # all passing interfaces.ProgressCallback
$ uv run mypy src/ges2t
Success: no issues found in 6 source files
exit=0
```

**Wrong shape — rejected (proves the probe is meaningful and mypy is really checking the seam):**

```
src/ges2t/_probe_progress.py:27: error: Argument "progress" to "ensure_model" has incompatible type "Callable[[str], None]"; expected "Callable[[float], None] | None"  [arg-type]
Found 1 error in 1 file (checked 6 source files)
exit=1
```

So ticket 8's call `model_store.ensure_model(progress=<interfaces.ProgressCallback>)` type-checks
with no import between the two modules, exactly as the anti-coupling rule requires.

**Cleanup (mandatory):** both files deleted, caches removed, and `git status
--porcelain --untracked-files=all` is empty; `uv run mypy src/ges2t` returns `Success: no
issues found in 4 source files` and `uv run pytest` returns `65 passed`. No probe was left
behind.

**Fraction semantics — by experiment:**

```
present CL=10, chunk 4 : [0.4, 0.8]          # 1.0 deliberately left to _ensure
missing CL            : []
malformed CL='abc'    : []
malformed CL=''       : []
CL='0'                : []
full ensure_model     : [0.0, 0.058, 0.116, ..., 0.986, 1.0]
count of 1.0: 1 | first: 0.0 | last: 1.0
present-file fractions: [1.0]
```

- `0.0` at download start, exactly one `1.0` after verification+install (and `[1.0]` when the
  fast path returns an already-present artifact). Monotonic and clamped to `[0.0, 1.0]`.
- Missing **or malformed** `Content-Length` yields **no** intermediate fractions — the header is
  treated as unknown, never as zero (`_content_length` returns `None` for `None` and for
  non-`int(text)`). The lifecycle endpoints survive because `_ensure`, not `_download`, owns
  them. `001-comment.md`'s claim is accurate; the malformed case is verified here but see the
  test-gap note below.
- Callback exceptions are swallowed by `except Exception` in `_report`, so only `Exception` is
  caught (`KeyboardInterrupt`/`SystemExit` propagate). This is defensible for acquisition
  robustness and is tested (`test_progress_callback_exceptions_do_not_abort_acquisition`). It
  does mean a genuinely buggy progress sink is silent; acceptable, and progress stays optional.

## Unsupported architecture PASS

```
== Experiment 6: unsupported platform raises, downloads nothing ==
  engine raised: no engine artifact pinned for this platform (plan9-vax); supported platforms: linux-x86_64
  model raised:  no engine artifact pinned for this platform (plan9-vax); supported platforms: linux-x86_64
  fetch calls during unsupported: []
```

`ensure_engine` resolves `engine_pin()` first; `ensure_model` calls
`_require_supported_platform()` first. Both fail before touching the fetcher.
`test_unsupported_platform_raises_before_downloading` locks this in. Making `ensure_model`
stricter than the weights strictly need is a documented judgement call and matches the DoD's
"the ensure-functions raise" wording; I accept it.

## ADR 0019 — typing PASS (with one minor `Any`)

- No positional-tuple returns: `env_overrides -> dict[str, str]` (an env mapping the ticket
  specifies), everything else returns a class or scalar; `_ensure -> Path`, `engine_pin ->
  ArtifactPin`, `verify -> bool`, `default_data_dir/model_path/engine_path -> Path`.
- No implicit `Optional`: `fetcher: Fetcher | None = None`, `progress: ProgressCallback | None
  = None`, `member: str | None = None`, `-> int | None` are all explicit.
- Every `def` — including `__init__ -> None`, `_remove -> None`, `_report -> None`, and the
  nested `fetch -> None` closure — is annotated. `uv run mypy src/ges2t` exits 0
  (`Success: no issues found in 4 source files`).
- **Enforcement is effective.** Adding a temporary unannotated function made mypy exit non-zero:

  ```
  src/ges2t/_probe_progress.py:21: error: Function is missing a type annotation  [no-untyped-def]
  Found 1 error in 1 file (checked 6 source files)
  exit=1
  ```

  then reverted.
- **Minor (non-blocking):** the only bare `Any` is `_content_length(response: Any) -> int | None`
  (`model_store.py:438`), introduced by the progress fix. A real type is knowable
  (`http.client.HTTPResponse`, from `urllib.request.urlopen`) and ADR 0019 prefers an
  interface-typed signature; a small `_Response` Protocol (`.headers`, `.read`) would also let
  the test `FakeResponse` stand in without `Any`. It is a single private helper, mypy passes,
  and it is at an HTTP boundary, so I do not block on it.

## Tests — honest, headless, network-free

```
$ uv run pytest tests/test_model_store.py -q
.............................                                            [100%]
29 passed in 0.12s

$ uv run pytest -q
.................................................................        [100%]
65 passed in 0.24s
```

- No network: every acquisition test injects a `fetcher`; the two downloader tests monkeypatch
  `model_store.urllib.request.urlopen` with an in-memory `FakeResponse`. A grep for
  `urlopen|requests|httpx|socket|http.client|pip` in the test file matches only those mocked
  spots. `XDG_DATA_HOME` is monkeypatched to `tmp_path` for every acquisition test, so nothing
  leaves the sandbox.
- Every one of the 29 tests contains an `assert` or a `pytest.raises` (checked with an AST
  walk).
- **Mutation experiment (run in a throwaway `/tmp` copy, worktree untouched).** To confirm the
  tests do not pass with the behaviour removed, I copied `src`+`tests`, verified the mutated
  module was the one imported, and removed one behaviour at a time:

  ```
  disable hash+size verification   -> 3 failed, 26 passed  (mismatch tests fail)
  disable size-only fast path      -> 3 failed, 26 passed  (present/no-fetch tests fail)
  drop the 0.0 start report        -> 2 failed, 27 passed  (progress tests fail)
  drop temp cleanup in finally     -> 3 failed, 26 passed  (leftover/partial tests fail)
  disable unsupported-platform gate-> 1 failed, 28 passed  (unsupported-arch test fails)
  ```

  Each mutation failed exactly the tests that pin it, and the copy was deleted afterwards.

## Scope, board, links

- Source scope is exactly the two required files; `config.py` untouched; no forbidden module
  (`interfaces.py`, `whistle.py`, `service.py`, `doctor.py`, `pipeline.py`) exists in `src/`.
- Board row 5: `in review`, `feat/model-store-and-verification`. Ticket header:
  `Status: in review`. Correct pre-merge state. Every relative link in `ticket.md` resolves
  (`../epic.md`, the sibling tickets, the three ADRs, `../project-scaffold`, etc.).
- `001-comment.md` records the required manual step (`3.534 s` cold, `0.099 s` warm) and the
  sizes/hashes; I confirmed the on-disk result independently.

## Risks / follow-ups

1. **Merge conflict with `main` (must-fix at merge time).** A dry run
   (`git merge-tree --write-tree HEAD main`) reports `CONFLICT (content): Merge conflict in
   docs/project/README.md`. `main` has since set rows 2 and 4 to `done` while this branch set
   row 5; the orchestrator must resolve the README keeping both. `ticket.md` auto-merges.
2. **Ticket 8 hand-off.** `model_store.ensure_model`/`ensure_engine` are keyword-only
   (`*, fetcher, progress`) and accept `ProgressCallback | None`; the adapter's
   `ensure_model(progress=None)` must call them with keywords. `load()` applies
   `env_overrides()` to `os.environ` in one place. The structural type match is proven above.
3. **Ticket 10/12 hand-off.** `verify(path, expected)` takes an **`ArtifactPin`**
   (`MODEL_PIN`/`ENGINE_PIN`), not a hash string — the CLI `models --verify` and `doctor
   --verify` must pass the pin objects. There is **no public "present with pinned size"**
   helper; `doctor`'s "Model present/Engine present" checks and `models`' non-`--verify`
   listing will otherwise have to call the private `_present_with_pinned_size` or reimplement
   the size comparison. Consider a small public `is_present(pin: ArtifactPin) -> bool` on a
   follow-up ticket.
4. **Only x86-64 Linux is pinned** (`SUPPORTED_ENGINE_PLATFORMS = {"linux-x86_64"}`), and
   `ensure_model` fails on anything else. That is the ADR's explicit platform pinning, but
   `doctor` should present it as "architecture not supported" rather than double-reporting a
   missing model.
5. **Installed files are mode `0600`** because `mkstemp` creates them that way and
   `os.replace` preserves it. Fine for a per-user XDG data dir; note it if anything ever needs
   them readable by another user, and `doctor` should not be surprised.
6. **`~/.cache/cactus-needle` exists on this machine** (mtime 10:46, i.e. before the 12:09
   download). `model_store` did not create or touch it — that is the pre-existing ADR probe —
   but ADR 0017 treats its absence as an audit signal, so `doctor` may want to report it.
7. **The engine wheel container is not hashed** (only the extracted `.so` is pinned), which
   matches ADR 0017 and the ticket. A future pin could add the wheel hash, but it would need an
   ADR update.
8. **Pre-existing board staleness, not this ticket's defect:** `main`'s board now marks ticket 2
   `done` and contains `src/ges2t/interfaces.py`, whereas this branch's base predates it. The
   branch depends on ticket 2's `ProgressCallback` shape; if that shape ever changes, this
   module must follow (the probe above is the check).

## Minor observations (none blocking)

- **`001-comment.md` has a stale snippet.** Its "Final public shapes" block still says
  `ProgressCallback = Callable[[str], None]  # one human-readable line per milestone`, and
  judgement call #3 argues for a string line. The appended fix section corrects the behaviour,
  and the code is the fraction version, but the earlier block contradicts it. Documentation-only
  inconsistency in the review artifact; worth a one-line fix before the ticket is closed.
- **Test typing:** `FakeResponse.__exit__(self, *exc: object) -> bool` triggers mypy's
  `exit-return` check if tests are type-checked (`mypy tests/test_model_store.py` reports it).
  ADR 0019 extends annotations to tests; `Literal[False]` (or `None`) is the precise type. The
  DoD's mypy command covers `src/ges2t` only, so this is not a gate.
- **Test gap:** the malformed-`Content-Length` claim ("never zero, no intermediate fractions")
  is only covered for the *absent* header (`test_download_without_content_length_reports_no_fractions`);
  the `"abc"`/`""`/`"0"` cases are not tested. I verified them by experiment; a test would make
  the claim permanent.

## What I could not verify

- **The real network download itself.** I did not re-fetch the 17 MB model / wheel; the pins,
  the manual timings, and the on-disk hashes are consistent and the artifacts are present and
  match, but the cold-download wall time is taken from `001-comment.md`. The atomicity,
  verification, and progress behaviour were all reproduced locally with injected fetchers and a
  mocked `urlopen`.
- **Ticket 8's actual adapter code** does not exist yet, so the integration is verified at the
  type seam (the probe) and by reading its ticket, not end to end.
- **The `~/.cache/cactus-needle` "unchanged" claim** is corroborated by its pre-download mtime
  and the absence of any write path in the code, but I did not diff its contents before/after a
  fresh acquisition.
