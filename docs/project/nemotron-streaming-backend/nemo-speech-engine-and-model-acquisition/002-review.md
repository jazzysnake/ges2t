# 57 — review

- **Ticket:** `docs/project/nemotron-streaming-backend/nemo-speech-engine-and-model-acquisition/ticket.md`
- **Branch:** `feat/nemo-speech-engine-and-model-acquisition`
- **Worktree:** `.worktrees/nemo-speech-engine-and-model-acquisition`
- **Commit reviewed:** `e1304d214d5a36897738cac1537ac645e0d050ea` (merge-base with `main`: `01c624e`)
- **Reviewer:** ges2t reviewer (independent; no implementation edited)
- **Date:** 2026-10-15

## Verdict: **pass**

The two things this ticket exists to get right — the **pins** and the **verification/acquisition
logic** — both hold up under independent, mechanical checking. All four pinned values match upstream
**metadata** (engine via the GitHub release API, the `.sha256` asset and a `HEAD`; model via the HF
`?blobs=true` API and the revision URL's `x-linked-size`/`x-linked-etag`). I re-ran **17 mutations**
(the comment recorded 12; I picked a superset) and every one made its targeted test **fail**; `src/`
was byte-identical after each revert. The full suite (1288), `mypy src/ges2t tests` (0), the docs link
check (0 broken) and `git status` (clean) all reproduce.

The verdict is **pass**, not unconditioned: three **non-blocking** findings are recorded under
"Findings" (an inaccurate clause in one implementation docstring, a slightly overstated cache
wording, and a residual hole in one static guard that its docstring does not admit). None is a
functional or security defect. The one gate that was **not** run (the ~758 MB real download) is named
honestly in the ticket notes and `001-comment.md`, not ticked as if run.

## What I verified

### 1. The pins against upstream metadata only (no 758 MB download)

Engine (GitHub release API `repos/NVIDIA/NeMo-Speech.cpp/releases/tags/v0.2.0`, plus the `.sha256`
asset, plus `HEAD` on the pinned URL):

```
'nemo-speech-0.2.0-linux-x86_64-vulkan.tar.gz' size=16618142
    digest=sha256:c8d0ed5b9ac5fb6d5c460015ba38ea217415c44edc1e6d49120646781009aee6

$ curl … /nemo-speech-0.2.0-linux-x86_64-vulkan.tar.gz.sha256
c8d0ed5b9ac5fb6d5c460015ba38ea217415c44edc1e6d49120646781009aee6  nemo-speech-0.2.0-linux-x86_64-vulkan.tar.gz

$ curl -sSLI …/…/nemo-speech-0.2.0-linux-x86_64-vulkan.tar.gz
HTTP/2 302
HTTP/2 200
content-length: 16618142
```

Model (HF API `models/nvidia/nemotron-3.5-asr-streaming-0.6b/revision/1c8deae…?blobs=true`):

```
modelId: nvidia/nemotron-3.5-asr-streaming-0.6b
sha: 1c8deaecc64b91f034d73e08dd8b64625eb3395d
FILE: nemotron-3.5-asr-streaming-0.6b.q8_0.gguf
  size: 741548352
  lfs: {'sha256': 'a5c435f294eea8f88ce68dd27b8c3bfea7f777cb2fbba04fcd30eaa555f429ae',
        'size': 741548352, …}

$ curl -sSLI …/resolve/1c8deae…/nemotron-3.5-asr-streaming-0.6b.q8_0.gguf
HTTP/2 302   x-linked-size: 741548352
             x-linked-etag: "a5c435f294eea8f88ce68dd27b8c3bfea7f777cb2fbba04fcd30eaa555f429ae"
HTTP/2 200   content-length: 741548352
```

| pin | code constant | upstream (this run) | match |
|---|---|---|---|
| engine size | `NEMO_SPEECH_SIZE = 16_618_142` | 16618142 | ✔ |
| engine sha256 | `NEMO_SPEECH_SHA256 = c8d0ed5b…aee6` | c8d0ed5b…aee6 (API digest, `.sha256` file, HEAD) | ✔ |
| engine URL | tag `v0.2.0` + `nemo-speech-0.2.0-linux-x86_64-vulkan.tar.gz` | `…/releases/download/v0.2.0/…tar.gz` | ✔ |
| model revision | `NEMO_MODEL_REVISION = 1c8deae…395d` | `1c8deae…395d` | ✔ |
| model size | `NEMO_MODEL_SIZE = 741_548_352` | 741548352 | ✔ |
| model sha256 | `NEMO_MODEL_SHA256 = a5c435f2…29ae` | a5c435f2…29ae (LFS + `x-linked-etag`) | ✔ |

**Revision-qualified, not `main`:** `NEMO_MODEL_URL` is assembled from `NEMO_MODEL_REVISION`
(`model_store.py:436-439`), and the pinned URL serves exactly the pinned bytes (`x-linked-etag`
matches). The ticket's supporting claim that `main` is a **different** artifact is also true:

```
main sha: ea30d66debe3740a08b573244286791d423d6b3e
FILE: …q8_0.gguf size: 742090464 lfs sha256: 3fc991d3badad7277c11030a7519832cddaf2057aafed6d4b25147e953a070b1
```

(742,090,464 B / `ea30d66d…` / `3fc991d3…` — exactly as `model_store.py:407-410` claims.) The pins also
trace verbatim to ticket 55's report (`REPORT.md:100,105,193`) and the design report §7.1.

### 2. Re-falsification of the guards (17 mutations; nothing trusted from the comment)

Each mutation was applied to `src/` with an exact-string replace, the targeted test run
(`uv run pytest -q -p no:cacheprovider <node>`), the file reverted, `.mypy_cache`/`__pycache__`
removed. Harness: `/tmp/probe.py`. All exit codes below are the **mutated** run.

| # | mutation | targeted test | result |
|---|---|---|---|
| P1 | drop the sha256 comparison in `verify_engine` (keep size) | `test_verify_engine_returns_none_for_a_different_pin` | **1 failed** ✔ |
| P2 | `extractall(staging, filter="data")` → `extractall(staging)` | `test_a_path_traversal_member_is_refused` | **1 failed** ✔ (DeprecationWarning: 3.13 default is `fully_trusted`) |
| P3 | drop the entry-library completeness check | `test_an_archive_without_the_entry_library_is_a_typed_error` | **1 failed** ✔ |
| P4 | drop the size check in `ensure_artifact` | `test_ensure_engine_checks_the_archive_size_alone` + `test_ensure_nemo_model_checks_the_size_alone` | **2 failed** ✔ |
| P5 | add `import subprocess` to `nemo_engine.py` | `test_the_engine_module_runs_no_external_command_and_installs_nothing` | **1 failed** ✔ |
| P6 | `_remove_tree(destination)` before the fetch (M8) | `test_a_failed_reinstall_keeps_the_previous_install` | **1 failed** ✔ |
| P7 | `NEMO_SPEECH_ENGINE = "nemo-speech"` (M17) | `test_nemo_backend_token_is_the_engine_value` | **1 failed** ✔ |
| P8 | drop the sha256 check in `ensure_artifact` | `test_ensure_nemo_model_wrong_hash_is_a_typed_error` | **1 failed** ✔ |
| P9 | silently accept the nemo model argument (M13) | `test_nemo_speech_refuses_a_model_argument_it_cannot_honour` | **1 failed** ✔ |
| P10 | drop the idempotent size gate (M12) | `test_ensure_nemo_model_is_idempotent` | **1 failed** ✔ |
| P11 | add `import urllib.request` to `nemo_engine.py` | `test_network_clients_are_confined_to_the_model_store` | **1 failed** ✔ |
| P12 | swap model-before-engine order (M14) | `test_nemo_run_extracts_the_engine_then_acquires_the_model` | **1 failed** ✔ |
| P13 | remove the `PartialSetupError` wrap in `_run_nemo` | `test_nemo_model_failure_after_the_extraction_is_typed_and_names_state` | **1 failed** ✔ |
| P14 | drop `NemoEngineError` from the CLI catch tuple (M18) | `test_setup_nemo_speech_reports_an_engine_failure_cleanly` | **1 failed** ✔ |
| P15 | swallow a nemo engine failure and continue (M16) | `test_nemo_engine_failure_propagates_and_never_acquires_a_model` | **1 failed** ✔ |

Two observations:
- P3's failure is on the **message** (`'lib/libnemo_speech_asr_c.so' in str(exc)`); the staged
  `verify_engine` second line of defence still raises, but without the specific name. Both layers are
  real, and the test does pin the naming.
- P7 leaves `test_nemo_speech_refuses_a_model_argument_it_cannot_honour` **passing** (it asserts the
  message, not the token) — matching the comment's honest note; the identifier guard is the
  `_BACKEND_ENGINES`/token test, which fails. This is a real guard, not vacuous.

After all probes:

```
$ md5sum -c /tmp/baseline_md5.txt
src/ges2t/nemo_engine.py: OK
src/ges2t/model_store.py: OK
src/ges2t/identifiers.py: OK
src/ges2t/setup_command.py: OK
src/ges2t/cli.py: OK
$ git diff --stat            # empty
$ git status --porcelain     # empty
```

### 3. Atomicity and correctness of `ensure_engine` (independent probes, not the suite)

- **Pre-fetch wipe must not destroy a good install.** P6 proves the guard: inserting
  `_remove_tree(destination)` before the fetch makes
  `test_a_failed_reinstall_keeps_the_previous_install` fail with `FileNotFoundError` on the previous
  executable. With the code as shipped, a different-pin re-install whose fetch fails the hash check
  leaves the old tree byte-identical and still verifies. ✔
- **Traversal filter genuinely rejects.** I built real gzip tars and drove the public `ensure_engine`:

  ```
  A_relative_dotdot : EngineArchiveError: '../escaped.txt' would be extracted to '…/escaped.txt',
                      which is outside the destination      escape check: NONE
  C_absolute_symlink: EngineArchiveError: '…/link_abs' is a link to an absolute path   escape: NONE
  D_escaping_symlink: EngineArchiveError: '…/link_up' would link to '…/outside.txt',
                      which is outside the destination      escape: NONE
  ```
  Special files are also refused (`SpecialFileError: 'special' is a special file`), confirmed against
  `tarfile.data_filter`. See Finding 1 for the one wording inaccuracy (absolute **member names** are
  stripped-and-contained, not refused — nothing escapes either way).
- **`verify_engine` catches a tampered manifest, not just a missing file.**

  ```
  tampered-manifest sha256 -> verify: None
  tampered-manifest size   -> verify: None
  missing entry library    -> verify: None
  damaged-contents (same size) -> verify returns engine? True   # documented limitation
  after forced repair: fetcher.calls = 0 | bytes restored: True # repair is fully offline
  ```
- **A failed model step leaves a coherent engine and raises `PartialSetupError`.** P13 confirms the
  wrap is load-bearing; the shipped code wraps `(ModelStoreError, OSError)` — and
  `ArtifactVerificationError` is a `ModelStoreError` subclass (checked), so a model hash/size mismatch
  becomes a `PartialSetupError` naming the installed engine, not an uncaught error. ✔
- **A failed engine step never reaches the model.** P15 confirms; the CLI reports it cleanly (P14).

### 4. The one unverified assumption is stated honestly

The archive's **internal layout** (`ARCHIVE_ROOT_NAME`, `bin/nemo-speech`,
`lib/libnemo_speech_asr_c.so`) is taken from ticket 55's own extraction
(`docs/project/research/nemotron-3-5-asr-streaming-measurement/REPORT.md:92,101,157`), not from a
download here. Both the ticket note and `001-comment.md` say so explicitly. And the code fails
**loudly** if the layout differs:

```
$ (archive whose root is 'some-other-directory')
EngineArchiveError: …: the archive has no 'nemo-speech-0.2.0-linux-x86_64-vulkan' directory
  (found: some-other-directory)
```

`_extract` names the directories it found (`nemo_engine.py:536-546`), and missing entry library /
executable each raise `EngineArchiveError` naming the path. `ARCHIVE_ROOT_NAME` is the single
constant to change if the real archive differs.

### 5. Scope / regression

- `_ensure` → `ensure_artifact`: no `_ensure(` call remains in `src/`; all four callers
  (`ensure_model`, `ensure_engine`, `ensure_whisper_model`, `ensure_nemo_model`) use the public name,
  and the signature only adds defaults for the two keyword-only collaborators — behaviour-preserving.
- The `model_store.py` "network only here" guard still holds: P11 shows a `urllib` import in
  `nemo_engine.py` fails it; the shipped scan reports only `model_store.py: {urllib}` (suite green).
- **No new runtime dependency:** `git diff main...HEAD -- pyproject.toml uv.lock` is empty.
  `nemo_engine.py` imports only stdlib + `ges2t.model_store` + `ges2t.whisper_engine`; no
  `numpy`/`torch`/`onnxruntime` in `src/` (only prose mentions in comments of unrelated modules).
- `config.py` is untouched (expected; ticket 58 adds `KNOWN_ENGINES`), stated as a boundary.

### 6. Gates reproduced

```
$ uv run pytest -q
1288 passed in 7.34s                     (exit 0)
$ uv run mypy src/ges2t tests            (after rm -rf .mypy_cache __pycache__)
Success: no issues found in 67 source files   (exit 0)
$ python3 <§10 link check>
0 broken references
$ git status --porcelain                 # empty
$ git rev-parse --short main   -> 01c624e ;  uv run pytest --collect-only -q (main) -> 1230 tests collected
```

The comment's baseline (+58 tests over `main`'s 1230) is **accurate**; `main` at `01c624e` collects
1230, this branch 1288.

Real CLI (offline), reproducing `001-comment.md`:

```
$ uv run ges2t setup bogus
ges2t: setup: unknown backend 'bogus'; valid backends: needle, whisper.cpp, nemo_speech, whisper_cpp
  hint: run 'ges2t setup --help' to list the accepted backends and models
exit=1
$ uv run ges2t setup nemo_speech some-other-model
ges2t: setup: the nemo_speech backend has exactly one pinned model (nemotron-3.5-asr-streaming-0.6b)
and acquires it automatically, so it takes no model argument (got 'some-other-model');
run 'ges2t setup nemo_speech'
exit=1
$ uv run ges2t setup --help        # names nemo_speech, "no model argument", "no build"  exit=0
```

`ninja`/`sentencepiece` are `ABSENT` (`command -v`); the module never names them (P5). The user's real
state (`~/.local/share/ges2t`, `~/.cache/ges2t`) is untouched by the suite and the probes
(`find … -mmin -30` → empty).

## Per-criterion findings against the definition of done

| DoD item | verdict | evidence |
|---|---|---|
| `identifiers.py` defines `NEMO_SPEECH_ENGINE = "nemo_speech"`, one definition, used by comparison | **pass** | `identifiers.py:69`; `SetupBackend.NEMO_SPEECH` and `_BACKEND_ENGINES` both take the constant; no bare literal in any comparison in `src/` (grep). P7 fails the guard. |
| `nemo_engine.py` mirrors `whisper_engine.py` (pin, extraction, `engine_dir()`, `verify_engine()`, device detection); never builds, never installs | **pass** | Module present; P5/P11 fail the AST guard on `import subprocess`/`urllib`; extraction/verify/device verified by suite + probes §3–4. |
| `model_store.py` gains the GGUF pin (sha256+size+revision) and `nemo_model_path()`/`ensure_nemo_model()`, reusing helpers | **pass** | `model_store.py:404-480, 636-643, 805-824`; uses `ensure_artifact`; pins verified §1; P8 catches a wrong hash. |
| `ges2t setup nemo_speech` acquires engine + GGUF, verifies hashes, prints the config line; mismatch is a hard failure leaving nothing half-written | **pass (code + logic)** | Engine-first order (P12), hash/size hard-fail with no leftovers (`test_wrong_hash_archive_fails_and_leaves_nothing`, `…_leaves_nothing`), config line printed (`test_config_guidance_for_nemo_prints_the_exact_engine_line`). **The real acquisition was not run** — see could-not-verify. |
| `setup` requires and never installs prerequisites; never `sudo`; prebuilt path needs no `ninja`/`sentencepiece` | **pass** | AST guard (P5); `ninja`/`sentencepiece` ABSENT and never named; no `subprocess`/`sudo` in `nemo_engine.py`. |
| No runtime network; acquisition only here; `ensure_*` the only networked call | **pass** | Static guard holds (P11); `nemo_engine` reaches the network only through `model_store.ensure_artifact`. |
| Hermetic tests (wrong-hash archive + wrong-size model fail); `pytest` green; `mypy src/ges2t tests` 0 | **pass** | 1288 passed; 0 mypy errors (67 files, after cache clear); wrong-hash/wrong-size tests exist and P4/P8 show they fail. |
| Docs link check 0 broken; `docs/project/README.md` row updated | **pass** | `0 broken references`; README row set to `in review` (not `done`, correctly). |
| Unrun gate (real download) named, not ticked as if run | **pass** | Named in ticket "Not run here" note and `001-comment.md` §"The one gate that is not run". The DoD boxes are all `[x]`, but on **hermetic** coverage; the gate is a note, not a DoD line. See Risks for the nuance. |

## Findings (none blocking)

1. **`nemo_engine.py:526-530` — implementation docstring overclaims the `data` filter.** It says the
   filter "refuses absolute member names". Empirically (CPython 3.13 `tarfile._get_filtered_attrs`)
   an absolute member **name** is *stripped* of its leading `/` and extracted **inside** the
   destination (raised `AbsolutePathError` only when still absolute, e.g. Windows `C:/`); it is
   absolute **link targets**, `..`/outside-destination paths, and special files that are *refused*.
   The security outcome is correct — a stripped absolute member lands at the staging top level,
   **outside** `ARCHIVE_ROOT_NAME`, and is discarded with the staging tree (my probe B: nothing
   escaped), and the archive is hash-pinned first — but the wording is inaccurate. Recommend
   rewording to "contains/refuses" or "sanitises absolute names, refuses `..`, escaping links and
   special files". Non-blocking.
2. **Cache wording is slightly stronger than the mechanism.** `ensure_engine`'s docstring and the
   comment describe `force` as re-extracting "from the archive already **verified in the cache**". A
   cache hit is trusted by **byte size only** (`_present_with_pinned_size`), so a `force` run does not
   re-hash the cached archive. This is ADR 0017's deliberate model-store trade, reused deliberately,
   and the functional behaviour is fine (my probe: forced repair is offline and correct). Recommend
   the word "hash-verified" be qualified as "hash-verified when first downloaded". Non-blocking.
3. **Residual hole in the `nemo_engine` AST guard, not stated in its docstring.** The guard catches
   HTTP/process imports and *exact* forbidden program-name string constants; it would not catch
   `os.system("ninja -C build")` (command line embedded in one string) or `os.exec*` (`os` is not in
   the forbidden-module set). The realistic failure (`import subprocess`) *is* caught (P5), and the
   module is small and reviewed, so this is a residual, not a defect — but unlike the model_store
   network guard, this one does not document its residual. Recommend a one-line scope note.
4. **Observation (not a finding):** `ENGINE_DIRECTORY_NAME = "nemo-speech"` and
   `NEMO_MODELS_DIRECTORY_NAME = "nemo-speech"` are two constants with the same spelling in two
   modules for two different subdirectories (`engine/…` vs `models/…`). They spell the token
   **identically**, so there is no drift risk, and they are conceptually distinct namespaces (the
   whisper.cpp precedent has the same shape). Flagged only for the record.

## Risks / follow-up tickets

- **Follow-up (small, docs-only):** a ticket or a one-line edit to correct Finding 1 and qualify
  Finding 2; add the scope note for Finding 3. No behavioural change.
- **The real end-to-end acquisition remains unrun.** Until someone runs
  `ges2t setup nemo_speech` on the network, the *archive layout* assumption is unverified (by design).
  If it is wrong, `ensure_engine` fails loudly with `EngineArchiveError`; the fix is one constant
  (`ARCHIVE_ROOT_NAME`) plus, if the entry paths differ, `ENTRY_LIBRARY_RELATIVE_PATH` /
  `EXECUTABLE_RELATIVE_PATH`. Worth doing before ticket 58 depends on the exact `.so` path.
- **Ticked DoD boxes vs the unrun gate.** Every box is `[x]`; the acquisition behaviour is proven
  hermetically, not against the real bytes. The note makes this explicit, but a reader who stops at
  the checklist could mistake it for a real download. Recommend the ticket keep the "Not run here"
  paragraph prominent (it does).
- **No blocking risks.** No new dependency, no `numpy`/`torch`, no runtime network, no `sudo`.

## Could-not-verify (each reason is itself true)

1. **The real ~758 MB download (`ges2t setup nemo_speech`) was not run.** Reason: this review has no
   authorisation to use the network for a bulk download, and the machine-safety rules forbid it
   without an explicit user-owned confirmation. I verified the **pins** against upstream metadata
   (small reads only) and the **logic** hermetically; I did not fetch the archive or the GGUF.
2. **The archive's real internal layout** (`ARCHIVE_ROOT_NAME`, `bin/nemo-speech`,
   `lib/libnemo_speech_asr_c.so`) is taken from ticket 55's extraction, not re-derived here — same
   network reason. (Confirmed the code fails loudly if it differs.)
3. **What the orchestrator originally stated as the baseline ("923 passed")** cannot be verified from
   the repository; I *did* verify the number that matters: `main` at `01c624e` collects 1230 tests and
   this branch 1288.
4. **The HF/GitHub values are metadata, not the served bytes' own hash.** I confirmed the release's
   `.sha256` asset, the GitHub API `digest`, the `HEAD` `content-length`, the HF LFS `sha256`, and the
   revision URL's `x-linked-etag`/`x-linked-size` all agree — a strong consistency check, but not a
   `sha256sum` of the downloaded file (that is the unrun gate above).

## Is this a measurement artifact, and what would falsify the central claim?

The central claim is: **"the four pinned values match the live upstream artifacts, and the
acquisition/verification code enforces them (hash, size, revision, atomicity, traversal) loudly."**

- **What would falsify it:** any pinned value disagreeing with the live release/HF metadata; a
  revision-qualified URL that secretly resolves `main`; or a guard that does not fail when its
  behaviour is broken. I ran all three: the values agree (engine digest + `.sha256` + `HEAD`; model
  LFS + `x-linked-etag`), the URL is revision-qualified and `main` is a different artifact, and 17
  mutations each made their test fail. The claim survived.
- **Residual falsification risk:** only the real bytes themselves (the unrun download). A
  layout mismatch or a CDN serving different bytes than the metadata describes would falsify the
  *layout* assumption / the metadata-consistency assumption and is not covered here — but both would
  fail loudly in code.

## Safe to relay to the user?

**Yes — with the stated caveat.** The pins and the enforcement logic are independently verified and
the suite/mypy/link-check are green; the verdict is `pass`. Relay it together with the one honest
caveat that the **real download was not run** (network user-owned), so the archive's internal layout
is the only unproven assumption, and the code fails loudly if it is wrong.

## Hygiene

- No application code, test, config, ticket or comment was edited. My only write is this file.
- Every probe mutated `src/`, ran the test, and reverted; final `git diff --stat` empty and `md5sum -c`
  OK for all five touched files (guards against the stale-`.pyc`/same-length-restore traps — I
  removed `.mypy_cache` and `__pycache__` after each probe).
- No microphone, clipboard, keystroke, window, notification, `sudo`, package install or service
  change. Network use was limited to small metadata reads (GitHub release API/`.sha256`/`HEAD`, HF
  `?blobs=true`/`HEAD`) — no artifact was downloaded.
- `git status` clean; no probe files or sockets left in the worktree (probe scripts live in `/tmp`).
