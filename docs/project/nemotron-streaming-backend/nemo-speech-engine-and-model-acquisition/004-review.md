# 57 — re-review of the F1–F3 fix pass

- **Ticket:** [ticket.md](ticket.md)
- **Branch:** `feat/nemo-speech-engine-and-model-acquisition`
- **Worktree:** `.worktrees/nemo-speech-engine-and-model-acquisition`
- **Commit reviewed:** `8d6a5af21784bfcf1610e9c1701948bed91e89b5` (parent `12e0bc4`, the reviewed tip)
- **Prior review:** [002-review.md](002-review.md) — verdict **pass**, three non-blocking findings (F1–F3)
- **Fix notes:** [003-comment.md](003-comment.md)
- **Reviewer:** ges2t reviewer (independent; no implementation edited)
- **Date:** 2026-10-15

## Verdict: **pass** — safe to merge

The fix commit `8d6a5af` closes F1 and F2 so the prose now matches the code, and materially
strengthens the F3 static guard. I re-ran the gates (`pytest` 1294 passed, `mypy src/ges2t tests`
0, docs link check 0, `git status` clean), and — the load-bearing check for "no behaviour change" — I
**proved the `src/` diff is docstrings-only by AST equality with docstrings stripped**, not by
reading: the two AST dumps are byte-identical (`4d484b35…` both sides). Every F3 guard I
re-falsified (4 mutations) failed its test as intended and reverted to a byte-identical file.

One **new non-blocking finding** is recorded (Finding 1): the strengthened F3 guard's
`_FORBIDDEN_PROCESS_CALLS` set claims to cover the `os.exec*` and `os.spawn*` families but omits two
real members, `os.execlpe` and `os.spawnlpe`. It is a defense-in-depth *test*, not a runtime defect;
the acquisition code is unchanged and safe. I recommend a two-word follow-up; it does not block merge.

## What I verified, and how

### A. No behaviour change — mechanically, not by reading

`git diff 12e0bc4 8d6a5af -- src/` shows one file, `src/ges2t/nemo_engine.py`, and every hunk is a
docstring. I did not rely on that reading. I parsed both revisions, **removed module/class/function
docstrings from the AST, and compared the dump**:

```
$ git show 12e0bc4:src/ges2t/nemo_engine.py > /tmp/nemo_before.py
$ cp src/ges2t/nemo_engine.py /tmp/nemo_after.py
$ python3 <<'PY'   # strips docstrings, hashes ast.dump()
/tmp/nemo_before.py 4d484b353cecfd4e35bcf30c95267a1ee6a255e39f58a50a4d36eab51340f5b8
/tmp/nemo_after.py  4d484b353cecfd4e35bcf30c95267a1ee6a255e39f58a50a4d36eab51340f5b8
AST (docstrings stripped) identical: True
PY
```

An AST dump cannot carry comments or the prose inside docstrings, so identical dumps prove **no
statement, branch, constant, default, annotation or identifier changed** — the only edits are
docstring/comment text. Corroborating:

- `git diff 12e0bc4 8d6a5af -- src/ges2t/model_store.py src/ges2t/identifiers.py src/ges2t/cli.py src/ges2t/setup_command.py pyproject.toml uv.lock` → **empty**. Only `nemo_engine.py` was touched under `src/`.
- The pin constants are therefore provably unchanged (`NEMO_SPEECH_SIZE/SHA256`, `NEMO_MODEL_*`, `ARCHIVE_ROOT_NAME`); the only diff line matching `ARCHIVE_ROOT_NAME` is the new docstring prose that mentions it.
- No source test reads a docstring: `grep -rn "__doc__" --include="*.py" tests/ src/` → **no matches** (only stale `.pyc` binaries matched, then removed). So the docstring edits cannot flip any test.
- `git diff main...HEAD -- pyproject.toml uv.lock` → **empty** (no new dependency); the branch diff is docs + `src/*.py` + `tests/*.py`, no binary/model/audio (numstat has no `-` entries).

### B. F1 — `_extract` docstring now matches `tarfile`'s `data` filter

The new wording (`nemo_engine.py:520-534`) claims four refusals and one sanitation:

> "It **refuses** … ``..`` components, link targets that are absolute or escape the destination, and
> special files. An absolute member *name* is not refused but **sanitised** — its leading ``/`` is
> stripped … at the staging top level, hence outside `ARCHIVE_ROOT_NAME` …"

I drove the **real** `tarfile.data_filter` (this machine: CPython **3.14.7**) with hand-built gzip
tars — no `src/` change:

```
A_absolute_member_name  : extracted -> ['etc/evil.txt']      # sanitised, inside staging top level
B_dotdot_component      : refused -> OutsideDestinationError '../escaped.txt' would be extracted to …, which is outside the destination
D_absolute_link_target  : refused -> AbsoluteLinkError 'link_abs' is a link to an absolute path
E_escaping_link_target  : refused -> LinkOutsideDestinationError 'link_up' would link to …, which is outside the destination
F_special_fifo          : refused -> SpecialFileError 'fifo' is a special file
```

Each of the docstring's five claims maps to exactly one line above. **Wording matches behaviour.**
The traversal tests still pass:

```
$ uv run pytest -q -p no:cacheprovider tests/test_nemo_engine.py -k "traversal or archive"
10 passed, 32 deselected in 0.21s
```

### C. F2 — the cache wording is true of the code

`ensure_engine`'s docstring (`nemo_engine.py:645-650`) now says the cache is trusted by **byte size
only**, so a `force` run re-hashes nothing; the sha256 is re-verified **on download**. Traced to the
mechanism it names, `ges2t.model_store.ensure_artifact`:

- Cache short-circuit (`model_store.py:853`): `if _present_with_pinned_size(destination, pin.size): return destination` — **size-only**.
- `_present_with_pinned_size` (`model_store.py:889-897`): `return path.is_file() and path.stat().st_size == expected_size` — and its own docstring says "Deliberately **not** a hash check … the full sha256 is deferred to `verify` and to the acquisition path (ADR 0017)."
- The `sha256`/size mismatch check (`model_store.py:872-875`) runs **only after `fetch(...)`**, i.e. on the download path.
- `ensure_engine` passes `archive_cache_path(version, pin)` as that `destination` (`nemo_engine.py`), so on a `force` the archive is reused from the cache at size cost only — **exactly** as the docstring states, and the same trade the model store makes.

The inline comment in `test_force_re_extracts_even_when_complete` was corrected to the same effect
(test body unchanged). F2 is honestly closed.

### D. F3 — the strengthened guard, re-falsified by me (not trusted from `003-comment.md`)

The guard is refactored into a pure `_scan_for_commands(source) -> _CommandScan`
(`tests/test_nemo_engine.py:706-750`) that reports forbidden module imports, exact forbidden-program
string constants, and a new `_FORBIDDEN_PROCESS_CALLS` name set. Its docstring now states the
**residual** ("``getattr(os, "system")``, a process reached through a helper or an alias, or a dynamic
import whose module name is not a literal"), matching the `model_store` network guard's style.

I applied four mutations, ran the targeted test, and reverted to a **byte-identical** file each time
(`md5sum -c` OK; `.mypy_cache`/`__pycache__` removed after every probe):

| # | mutation (reverted after) | targeted test | result |
|---|---|---|---|
| M1 | append `os.system("ninja -C build")` to `src/ges2t/nemo_engine.py` | `test_the_engine_module_runs_no_external_command_and_installs_nothing` | **1 failed** — `AssertionError: frozenset({'system'})` ✔ |
| M2 | `_scan_for_commands` forced to `process_calls=frozenset()` | `test_the_static_guard_fails_on_a_process_spawning_call` | **4 failed** (os.system/execv/popen/spawnvp) ✔ |
| M3 | `ast.Import` branch no longer records imports | `test_the_static_guard_fails_on_a_subprocess_import` | **1 failed** — `frozenset() == {'subprocess'}` ✔ |
| M4 | guard mutates to flag every bare `ast.Name` | `test_the_static_guard_does_not_flag_a_parameter_named_system` + main guard | **2 failed** ✔ |

So: the guard is **not** vacuous — it fails when its behaviour is broken in either direction
(under-catching and over-catching), and the `os.system`-mutation test the finding asked for exists
and fires. Note M1's code executes at module import (the test reads `_engine_source()` but pytest
also imports `nemo_engine`); it was inert here because `ninja` is absent, and the file was restored
byte-identically — recorded for transparency (see Hygiene).

### E. Gates re-run (this tree, `8d6a5af`)

```
$ timeout 600 uv run pytest -q
1294 passed in 11.20s                          (exit 0)
$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 67 source files    (exit 0)
$ python3 <orchestrator-state.md §10 link check>
0 broken references
$ git status --porcelain                       (empty)
$ git rev-parse HEAD
8d6a5af21784bfcf1610e9c1701948bed91e89b5
```

1294 = the reviewed 1288 + 6 (003-comment's count), none removed or weakened.

### F. `002-review.md`'s conclusion still holds

Every non-docstring fact in `002-review.md` is about code that is **AST-identical** between `12e0bc4`
and `8d6a5af`, so its pin verification, its 17 mutation results, and its extraction/verify findings
carry over unchanged. I independently re-ran the guards most relevant to acquisition:

```
$ uv run pytest -q -p no:cacheprovider tests/test_nemo_engine.py -k "previous_install or wrong_hash or leaves_nothing or idempotent"
4 passed, 38 deselected in 0.10s
```

and the pins are untouched (see A). Nothing regressed.

## Findings

1. **(new, non-blocking) The F3 guard's `os.exec*`/`os.spawn*` coverage is incomplete: it omits
   `os.execlpe` and `os.spawnlpe`.** The comment/docstring claim to catch "the ``os.exec*`` family"
   and "the ``os.spawn*`` family", but `_FORBIDDEN_PROCESS_CALLS` lists 7 of 8 members of each. I
   enumerated the real API and exercised the real scan function (no code executed — the sample source
   is parsed, not run):

   ```
   os.exec*  : ['execl', 'execle', 'execlp', 'execlpe', 'execv', 'execve', 'execvp', 'execvpe']
   exec* NOT covered : ['execlpe']
   os.spawn* : ['spawnl', 'spawnle', 'spawnlp', 'spawnlpe', 'spawnv', 'spawnve', 'spawnvp', 'spawnvpe']
   spawn* NOT covered: ['spawnlpe']

   $ uv run python -c 'import test_nemo_engine as t; …'
   os.system("x")            -> process_calls=['system']
   os.execv("/bin/sh", ["sh"]) -> process_calls=['execv']
   os.execlpe("/bin/sh", "sh") -> process_calls=[]        <-- missed
   os.spawnvp(0, "x", ["x"])   -> process_calls=['spawnvp']
   os.spawnlpe(0, "/bin/sh", "sh", {}) -> process_calls=[] <-- missed
   ```

   Consequence is bounded: to slip past **both** the name set and the forbidden-program constant set,
   a regression would have to call a missed variant *and* name a program outside
   `{sudo,pacman,cmake,ninja,make,sentencepiece,pip,meson}` (e.g. `os.execlpe("/usr/bin/cc","cc")`).
   The realistic failure (`import subprocess`, `os.system`) is caught (D, M1–M3). So this is a
   two-name gap in a defense-in-depth test, not a defect in the shipped acquisition logic — hence
   **non-blocking**. Recommend adding `"execlpe"` and `"spawnlpe"` to `_FORBIDDEN_PROCESS_CALLS`
   (a one-line follow-up) so the "family" claim is literally true.
2. **(carried from `002-review.md`, still valid, non-blocking) The real ~758 MB download was never
   run**, so the archive's internal layout (`ARCHIVE_ROOT_NAME`, `bin/nemo-speech`,
   `lib/libnemo_speech_asr_c.so`) remains the one unproven assumption. The code fails **loudly**
   (`EngineArchiveError` naming the directories found) if it differs. Unchanged by this pass.
3. **Observation (not a finding, carried):** `ENGINE_DIRECTORY_NAME` and
   `NEMO_MODELS_DIRECTORY_NAME` share the spelling `"nemo-speech"` in two modules for two distinct
   namespaces (`engine/…` vs `models/…`) — no drift risk, same shape as the whisper.cpp precedent.

## Risks / follow-up tickets

- **Follow-up (trivial):** add `execlpe` and `spawnlpe` to `_FORBIDDEN_PROCESS_CALLS` (Finding 1). A
  one-line test-only change; could ride with the next ticket that touches this file, or be done now.
- **Follow-up (unchanged):** the real end-to-end `ges2t setup nemo_speech` download is still the only
  unrun gate; if the archive layout differs, the fix is `ARCHIVE_ROOT_NAME` plus the two entry paths.
- **No blocking risks.** No runtime change, no new dependency, no network, no `sudo`.

## Could-not-verify (each reason is itself true)

1. **The real archive/GGUF bytes and their internal layout were not fetched here.** Reason: the
   machine-safety rules forbid network use for a bulk download without the user's confirmation, and
   this pass made **no network calls at all**. `002-review.md` verified the pins against upstream
   *metadata*; this pass additionally proves the pin constants are byte-unchanged (AST equality), so
   those metadata checks carry over verbatim.
2. **The end-to-end `ges2t setup nemo_speech` path against the real CDN was not run** — same network
   reason. It is proven hermetically (fake fetcher + real gzip tar).
3. I did **not** re-run all 17 of `002-review.md`'s mutations individually; instead I established the
   stronger invariant that `src/` is AST-identical modulo docstrings, which makes each of those
   results provably unchanged, and I re-ran the traversal, atomicity, hash and idempotency guards.

## Is this a measurement artifact, and what would falsify the central claim?

The central claim of this pass is: **"F1–F3 are closed — the `_extract` and cache docstrings now
describe what the code does, the no-build guard catches `os.system`/`os.exec*`/`os.popen`, and no
behaviour changed."**

- **What would falsify it:** a `src/` change that alters a statement (my AST comparison would differ);
  an `_extract` claim that does not survive the real `data` filter (it does — B); a cache docstring
  that disagrees with `ensure_artifact` (it agrees — C); a guard that does not fail when broken (it
  does — D). All four checks were run and the claim survived.
- **Partial falsification found, non-blocking:** the guard's *complete-family* framing is false for
  `execlpe`/`spawnlpe` (Finding 1) — the "catches the os.exec*/os.spawn* family" sub-claim is
  falsified, the "catches os.system/os.exec*/os.popen" sub-claim holds for the realistic members.

## Safe to relay to the user? **Yes.** · Safe to merge? **Yes.**

- The three recorded findings F1–F3 are genuinely closed (docstrings match code; guard strengthened,
  falsified, and its residual documented).
- No behaviour changed (proved by AST equality, not reading); all gates green; branch is docs + tests
  + a docstring-only `src/` edit.
- Relay with the one carried caveat: the real ~758 MB download is still unrun (network user-owned),
  and the new trivial residual in Finding 1 (two missing `os.exec*`/`os.spawn*` names in a test guard).
- **Do not** set the ticket `done` or merge from this agent — the ticket stays `in review`, the README
  row stays `in review`, and the merger/orchestrator owns the merge and push (orchestrator-state §10).

## Hygiene

- No application code, test, config, ticket or comment was edited. My only write is this file.
- Every probe mutated a file, ran the test, and reverted; final `md5sum -c` OK for both files and
  `git diff --stat` empty. `.mypy_cache` and every `__pycache__` were removed after each probe and at
  the end (guards the stale-`.pyc`/same-length-restore traps).
- **Transparency on M1/M3/M4:** appending executable source to `nemo_engine.py` is executed by pytest
  at import. It was inert (`ninja` absent; the `execlpe` coverage probe was moved to a pure
  `ast.parse` call that never runs the sample). No process was spawned (`pgrep -af ninja|/bin/sh`
  matched only my own shell command); both files were restored byte-identically.
- No microphone, clipboard, keystroke, window, notification, `sudo`, package install or service
  change. **No network use in this pass.**
- `git status --porcelain` clean in both the worktree and `main`; probe files (in `/tmp`) removed; no
  sockets or stray processes left.
