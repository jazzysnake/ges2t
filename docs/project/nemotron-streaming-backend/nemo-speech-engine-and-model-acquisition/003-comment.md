# 57 — extension pass: the three non-blocking findings (F1–F3)

- **Ticket:** `docs/project/nemotron-streaming-backend/nemo-speech-engine-and-model-acquisition/ticket.md`
- **Branch:** `feat/nemo-speech-engine-and-model-acquisition` (worktree `.worktrees/nemo-speech-engine-and-model-acquisition`)
- **Reviewed tip this builds on:** `12e0bc4` (reviewer verdict **pass**, `002-review.md`)
- **Author:** ges2t implementer. This is a **verification-and-fix** pass over the reviewed work, not a
  rewrite: no acquisition behaviour changes, no new dependency, no `sudo`, no download.

## Scope

`002-review.md` recorded three **non-blocking** findings (its "Findings" 1–3). This pass closes all
three, with the code and its prose now agreeing, and adds falsification evidence for the one guard
that was strengthened. `002-review.md` Finding 4 (the two same-spelled directory constants) was
flagged "for the record" only and needs no change.

## F1 — the `_extract` docstring now matches `filter="data"`

`src/ges2t/nemo_engine.py` said the filter "refuses absolute member names". It does not: CPython's
`data` filter **strips** the leading `/` and contains the member, so it lands at the staging top
level — outside `ARCHIVE_ROOT_NAME` — and is discarded with the staging tree. I reworded the
docstring to say exactly that, and to name what *is* refused. **No code changed** (the docstring
option, not the filter-tightening option): the security outcome was already correct, so tightening
the filter would have been a behaviour change for no gain.

Probe against the real `tarfile` `data` filter (no `src/` change; `/tmp` scratch, removed after):

```
A_absolute_member_name  : extracted -> ['etc', 'etc/evil.txt']      # sanitised, inside staging
B_dotdot_component      : refused -> OutsideDestinationError: '../escaped.txt' would be extracted to
                          '/tmp/escaped.txt', which is outside the destination
D_absolute_link_target  : refused -> AbsoluteLinkError: 'link_abs' is a link to an absolute path
E_escaping_link_target  : refused -> LinkOutsideDestinationError: 'link_up' would link to
                          '/tmp/outside.txt', which is outside the destination
F_special_fifo          : refused -> SpecialFileError: 'fifo' is a special file
```

The docstring's four claims ("refuses `..`, absolute/escaping link targets, special files";
"sanitises an absolute member *name* into the staging top level") each match a line above. The
traversal test still passes (`test_a_path_traversal_member_is_refused`, and the whole `-k
"traversal or archive"` selection: **10 passed**).

## F2 — the cache wording is honest

- `ensure_engine`'s docstring no longer says `force` repairs "from the archive already verified in
  the cache". It now says a cache hit is trusted by **byte size only**
  (`ges2t.model_store.ensure_artifact`, the same trade ADR 0017's model store makes), so a `force`
  run re-hashes nothing — the sha256 is re-verified **on download**.
- The same overclaim in the inline comment of `test_force_re_extracts_even_when_complete` was
  corrected to "trusts a cache hit by pinned byte size alone, so the forced re-extract reuses the
  archive downloaded above, fully offline". The test body is unchanged; only the comment.

Mechanism confirmed in `model_store.ensure_artifact`: the cache short-circuit is
`if _present_with_pinned_size(destination, pin.size): return destination` — size, not hash. The hash
+ size check runs only on the download path.

## F3 — the no-build AST guard now catches `os.system`/`os.exec*`/…

The old guard in `tests/test_nemo_engine.py` caught `import subprocess` and *exact* forbidden-program
string constants, but would miss `os.system("ninja -C build")` (the build tool hidden inside one
string) and `os.exec*` (`os` is imported for `os.rename`/`os.replace`, so the import cannot be
forbidden). It is now refactored into a pure `_scan_for_commands(source) -> _CommandScan` and:

- still flags forbidden module **imports** and exact forbidden-program **constants**; and
- additionally flags a **process-spawning call/attribute** by name — `system`, `popen`, the
  `os.exec*` family and the `os.spawn*` family (`_FORBIDDEN_PROCESS_CALLS`).

Bare names that are parameters (`inspect_device(system: SystemProbe | None = None)`) are deliberately
**not** flagged; only attributed access and a bare *callee* are. Residual, stated in the test
docstring the way the `model_store` network guard states its own: an **indirect** invocation a name
scan cannot follow — `getattr(os, "system")`, a process reached through a helper/alias, or a dynamic
import whose module name is not a literal.

## Falsification (break → watch the guard fail → revert)

Every probe below mutated a file, ran the test, restored the file byte-for-byte (md5 checked), and
left `git status` at exactly the two intended modified files.

```
1) appended `os.system("ninja -C build")` to src/ges2t/nemo_engine.py
   test_the_engine_module_runs_no_external_command_and_installs_nothing
     -> AssertionError: frozenset({'system'})  == frozenset()          [1 failed]  ✔
   restored: md5 src/ges2t/nemo_engine.py OK

2) `_scan_for_commands` forced to `process_calls=frozenset()`
   test_the_static_guard_fails_on_a_process_spawning_call[os.system('ninja -C build')]
   test_the_static_guard_fails_on_a_process_spawning_call[os.execv('/bin/sh', ['sh'])]
   test_the_static_guard_fails_on_a_process_spawning_call[os.popen('cmake .')]
   test_the_static_guard_fails_on_a_process_spawning_call[os.spawnvp(os.P_WAIT, 'make', ['make'])]
     -> AssertionError: frozenset() == frozenset()                     [4 failed]  ✔

3) guard mutated to flag every bare `ast.Name`
   test_the_static_guard_does_not_flag_a_parameter_named_system  and the main guard
     -> AssertionError: frozenset({'system'})                          [2 failed]  ✔
   restored: md5 tests/test_nemo_engine.py OK
```

The new `import subprocess` falsifier (`test_the_static_guard_fails_on_a_subprocess_import`) is the
same guard the reviewer's P5 already exercised; it is now pinned by its own test.

## Gates (this tree)

```
$ uv run pytest -q
1294 passed in 8.57s
$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 67 source files
$ python3 <orchestrator-state §10 link check>
0 broken references
$ git status --porcelain
 M src/ges2t/nemo_engine.py
 M tests/test_nemo_engine.py
```

Test count moved 1288 → **1294** (+6: four parametrised `os.system`/`os.exec*`/`os.popen`/`os.spawnvp`
falsifiers, the `import subprocess` falsifier, and the parameter-not-flagged guard). The orchestrator's
stated baseline "923 passed" is **not** this branch's baseline — `main` at `01c624e` collects 1230 and
the reviewed branch 1288 (the reviewer's numbers); this pass adds 6, none removed, none weakened.

## Superseded wording in `001-comment.md` (not rewritten)

`001-comment.md` line ~231 still reads "`force` repairs that fully offline from the cached,
**hash-verified** archive". That is the exact overclaim F2 corrects in the source; the numbered
artifact is **not** rewritten (project rule), so this comment supersedes it. The corrected statement
is: the cached archive is trusted by byte size for a `force` reuse; the hash was verified when it was
downloaded.

## Machine-safety / hygiene

- No microphone, clipboard, keystroke, window, notification, `sudo`, package install or service
  change. No network use at all in this pass — the ~758 MB download stays un-fetched (unchanged from
  the review's could-not-verify #1).
- Scratch probes lived in `/tmp` and were removed; every mutated file was restored and md5-verified;
  `git status` shows only the two intended source/test modifications. `.mypy_cache`/`__pycache__`
  removed before the final mypy run.
