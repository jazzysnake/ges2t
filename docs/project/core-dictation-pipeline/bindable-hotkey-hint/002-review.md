# 002 — bindable-hotkey-hint (ticket 40): independent review

- **Ticket:** [ticket.md](ticket.md) (ticket 40, P1)
- **Branch:** `feat/bindable-hotkey-hint`
- **Worktree:** `.worktrees/bindable-hotkey-hint`
- **Reviewer:** independent review agent (read-only on `src/` and `tests/`; this file is the only write)
- **Date:** 2026-10-10
- **Diff reviewed:** `git diff main...HEAD` — 10 files, 664 insertions, 46 deletions
- **Implementation artifact reviewed as an assertion set:** [001-comment.md](001-comment.md)

## Verdict: **request changes**

The shipped implementation is **functionally correct** and I reproduced it end to end: the path is
resolved correctly in all three install shapes, the existence gate holds, the thin-client budget is
not broken, every fallback degrades to the bare command, the rendered advice names an existing
executable that runs with no `PATH` and no cwd, and all mechanical gates pass.

There is **one material defect**: the ticket's central subtlety — *not following the virtualenv
`python` symlink* — is **not pinned by any test**. A one-word refactor that adds `.resolve()`
reintroduces exactly the defect this ticket fixes (`doctor` prints the bare `ges2t`) and the entire
**922-test suite still passes**. The test named `test_does_not_follow_the_virtualenv_python_symlink`
claims that property and does not test it. Because ticket 40's definition of done explicitly
requires that "break the resolution" makes the relevant test fail, this must be fixed before merge.
The code is safe to merge as-is; the *test* is what needs strengthening.

---

## 1. Resolution correctness in all three cases — **PASS (real filesystem, not injected)**

`.venv/bin/python` is a symlink in this worktree, exactly as the ticket claims:

```console
$ cd .worktrees/bindable-hotkey-hint
$ ls -la .venv/bin/python*
lrwxrwxrwx 1 matee matee 16 Oct 10 10:51 .venv/bin/python -> /usr/bin/python3
lrwxrwxrwx 1 matee matee  6 Oct 10 10:51 .venv/bin/python3 -> python
lrwxrwxrwx 1 matee matee  6 Oct 10 10:51 .venv/bin/python3.14 -> python
$ readlink -f .venv/bin/python
/usr/bin/python3.14
$ ls -la /usr/bin/ges2t
ls: cannot access '/usr/bin/ges2t': No such file or directory
$ head -1 .venv/bin/ges2t
#!/home/matee/Workspace/Personal/ges2t/.worktrees/bindable-hotkey-hint/.venv/bin/python
```

I exercised the three cases against the **real filesystem** with the default `_is_file` probe (the
unit tests inject `exists`, so this is the missing independent half). Probe tree: a real `ges2t` in
`venv/bin` beside a `python` symlink pointing at `usr/bin/python3` where a **different** `ges2t`
also exists — the trap made concrete:

```console
$ python - <<'PY'   # real files, real probe, no injected exists
from ges2t.console_script import resolve_console_script
venv_py = "/tmp/ges2t_probe/venv/bin/python"
print("venv/bin/python is symlink:", Path(venv_py).is_symlink(), "->", Path(venv_py).resolve())
print("venv resolve  :", resolve_console_script(executable=venv_py))
print("system resolve:", resolve_console_script(executable="/tmp/ges2t_probe/usr/bin/python3"))
Path("/tmp/ges2t_probe/venv/bin/ges2t").unlink()
print("missing resolve:", resolve_console_script(executable=venv_py))
PY
venv/bin/python is symlink: True -> /tmp/ges2t_probe/usr/bin/python3
venv resolve  : /tmp/ges2t_probe/venv/bin/ges2t
system resolve: /tmp/ges2t_probe/usr/bin/ges2t
missing resolve: None
```

- **venv install:** `.venv/bin/python` → `.venv/bin/ges2t` (the script beside the interpreter). ✅
- **system install:** `/usr/bin/python3` → `/usr/bin/ges2t` (the script beside the interpreter). ✅
  (No system install exists on this machine; the real-fs probe above substitutes for one, and the
  injected unit test `test_resolves_a_system_install_script_beside_usr_bin_python` pins the mapping.)
- **symlink trap: the reasoning is correct.** `Path(interpreter).with_name("ges2t")` keeps the
  interpreter's directory and never calls `.resolve()`, so the venv symlink is **not** followed and
  the wrong `/usr/bin/ges2t` is never named. The real venv probe confirms `.venv/bin/python ->
  /usr/bin/python3`, `/usr/bin/ges2t` does not exist, and the resolver returns the venv script. ✅

Real venv probe via the shipped code path:

```console
$ uv run python -c "from ges2t.console_script import resolve_console_script; print(resolve_console_script())"
/home/matee/Workspace/Personal/ges2t/.worktrees/bindable-hotkey-hint/.venv/bin/ges2t
```

## 2. The existence gate — **PASS**

`resolve_console_script` returns a path only after `check(candidate)`; otherwise `None`:

```python
check = exists if exists is not None else _is_file
if not check(candidate):
    return None
return str(candidate)
```

Verified on the real filesystem above (`missing resolve: None`), and the rendered output test in §5
uses the live resolver. A non-existent candidate cannot produce a printed path: `None` flows into
the fallbacks (`console_script if console_script else BARE_COMMAND`) in all three consumers, so
`None` becomes `"ges2t"`, never a printed path.

## 3. Thin-client requirement (ADR 0016) — **PASS, measured**

`cli.py` imports `ges2t.console_script` and calls `resolve_console_script()` in `build_parser()`.
The new module is **stdlib-only** (`sys`, `collections.abc`, `pathlib`); all three are already
imported by `cli.py`. Measured module sets and timing:

`build_parser()` imported `ges2t.*` modules — **HEAD vs `main`** (main extracted read-only to
`/tmp` with `git archive`, run with `PYTHONPATH`):

```console
HEAD (6): ges2t, ges2t.cli, ges2t.console_script, ges2t.interfaces, ges2t.service, ges2t.socket_proto
main (5): ges2t, ges2t.cli,                          ges2t.interfaces, ges2t.service, ges2t.socket_proto
FORBIDDEN pystray / ges2t.pipeline / ges2t.doctor / cactus_needle / needle / gi / ges2t.transcribers / ges2t.injectors: none (both)
```

The delta is exactly one stdlib-only module. `python -X importtime` attributes **131 µs** cumulative
to `ges2t.console_script`, and no new stdlib module (pathlib and `collections` are already in the
`cli` import chain):

```console
$ .venv/bin/python -X importtime -c "import ges2t.cli; ges2t.cli.build_parser()" 2>&1 | grep ges2t.console_script
  import time:       131 |        131 |   ges2t.console_script
```

Interleaved wall-clock (`build_parser` only, 30 runs each, alternating HEAD/main to remove drift):

```console
H n=30 min=38.633 median=42.662 max=50.836
M n=30 min=38.831 median=43.257 max=46.803
```

HEAD is **not** slower (median 42.66 ms vs 43.26 ms; the difference is noise, with HEAD slightly
faster). `ges2t toggle` did not become heavier: no engine, GTK, `pystray`, `pipeline`, or `doctor`
import. ✅

## 4. Fallback in every location that prints the advice — **PASS**

All three sites use the identical `executable = console_script if console_script else BARE_COMMAND`,
which handles both `None` and `""`:

| Site | Old name | New form | Fallback |
|---|---|---|---|
| `doctor.hotkey_fix()` (the WARN `fix:`) | `HOTKEY_FIX` | `hotkey_fix(console_script=...)` | `BARE_COMMAND` |
| `doctor.kde_binding_section()` (report footer) | `KDE_BINDING_SECTION` | `kde_binding_section(console_script=...)` | `BARE_COMMAND` |
| `cli.kde_shortcut_hint()` (epilog of `toggle`, `ptt-start`, `ptt-stop`, `cancel`, `doctor`) | `KDE_SHORTCUT_HINT` | `kde_shortcut_hint(console_script=...)` | `BARE_COMMAND` |

`build_report` resolves once (`console_script = probes.console_script()`) and passes the same value
to both `check_hotkey_bindings` and `DoctorReport`, so the fix line and the footer cannot drift.
`render_report` uses `report.console_script`. No `None`/empty string can reach the f-strings.

Falsification C (§7) shows that the *only* way to produce `'None toggle'` is to delete the
fallback; with the shipped code it is unreachable. `grep -rn "None toggle" src/` is false, and
`uv run ges2t doctor` prints no `None` (see §5).

## 5. Rendered output is the evidence — **PASS**

```console
$ uv run ges2t doctor
...
Hotkey bindings           WARN  ges2t does not grab the hotkey itself; bind the command in KDE
                                fix: System Settings -> Shortcuts -> Add -> Command or URL: bind '/home/matee/Workspace/Personal/ges2t/.worktrees/bindable-hotkey-hint/.venv/bin/ges2t toggle' (recommended), or '/home/matee/Workspace/Personal/ges2t/.worktrees/bindable-hotkey-hint/.venv/bin/ges2t ptt-start' and '/home/matee/Workspace/Personal/ges2t/.worktrees/bindable-hotkey-hint/.venv/bin/ges2t ptt-stop', plus '/home/matee/Workspace/Personal/ges2t/.worktrees/bindable-hotkey-hint/.venv/bin/ges2t cancel'
Summary: 22 checks, 20 ok, 2 warning(s), 0 failure(s).
Recommended KDE key bindings (System Settings -> Shortcuts -> Add -> Command or URL):
  /home/matee/Workspace/Personal/ges2t/.worktrees/bindable-hotkey-hint/.venv/bin/ges2t toggle     start or stop with one key (recommended default)
  ...
```

The printed path **exists and is executable** and is **the script the process runs as**:

```console
$ uv run python -c "import sys,os; from ges2t.console_script import resolve_console_script as r; p=r(); print(sys.executable); print(p, os.path.isfile(p), os.access(p, os.X_OK))"
/home/matee/Workspace/Personal/ges2t/.worktrees/bindable-hotkey-hint/.venv/bin/python
/home/matee/Workspace/Personal/ges2t/.worktrees/bindable-hotkey-hint/.venv/bin/ges2t True True
```

**Bindability, not plausibility** — the exact printed path runs from a launcher-like environment
(`env -i`, no `PATH`, cwd `/`), exit 0:

```console
$ cd / && env -i HOME=/home/matee /home/matee/Workspace/Personal/ges2t/.worktrees/bindable-hotkey-hint/.venv/bin/ges2t toggle --help
usage: ges2t toggle [-h] [--config PATH] [--debug]
...
enter '/home/matee/Workspace/Personal/ges2t/.worktrees/bindable-hotkey-hint/.venv/bin/ges2t toggle' (recommended), ...
# EXIT=0
```

The absolute shebang is why this works with no `PATH`: the kernel resolves the interpreter directly.
`uv run ges2t toggle --help` shows the same resolved hint in the epilog.

## 6. Grep accounting is complete — **PASS**

My own `grep` over `src/` for bindable-command strings:

```console
$ grep -rn "ges2t toggle\|ges2t ptt\|ges2t cancel\|bind '\|Command or URL\|Shortcuts" src/ --include=*.py
src/ges2t/doctor.py:176  "System Settings -> Shortcuts -> Add -> Command or URL: bind "   # fixed
src/ges2t/doctor.py:194  "(System Settings -> Shortcuts -> Add -> Command or URL):"      # fixed
src/ges2t/socket_proto.py:32  docstring "a hotkey bound to ``ges2t toggle``"              # internal docstring
src/ges2t/console_script.py:3,19  module docstring                                       # internal docstring
src/ges2t/cli.py:18  module docstring "because ``ges2t toggle``"                          # internal docstring
src/ges2t/cli.py:95,99  kde_shortcut_hint f-string                                       # fixed
src/ges2t/cli.py:107  _TOP_LEVEL_EPILOG: names subcommands, says "Run 'ges2t <command> --help'"  # names no bindable command
```

String-literal scan for `'ges2t …'` found only: `ges2t <command> --help`, `ges2t setup`,
`ges2t run`, `ges2t doctor`, `ges2t error` (notification title), `HOTKEY_SUMMARY` ("bind the
command in KDE" — names no command). All are shell commands / non-bindings or internal docstrings;
**nothing unaccounted for**. `ptt-start`/`ptt-stop`/`cancel` in `service.py` are enum members and a
docstring table, not output. No `yolobind`-style string exists.

## 7. Independent falsification — **one gap found**

Mutated, tested, restored, sha256-verified (`git status` clean and `git diff HEAD -- src/ tests/`
empty after every experiment).

**A. Break the resolution — symlink following (THE TICKET'S CENTRAL SUBTLETY).**
Changed `Path(interpreter).with_name(...)` → `Path(interpreter).resolve().with_name(...)`. This is
a genuine regression on this machine — the live resolver then returns `None` because the symlink
points at `/usr/bin/ges2t`, which does not exist:

```console
$ .venv/bin/python -c "from ges2t.console_script import resolve_console_script; print('resolved =', resolve_console_script())"
resolved = None                       # doctor would now print the bare 'ges2t' again
$ uv run pytest -q
922 passed in 3.10s                   # <-- NOTHING FAILS. The regression is unpinned.
```

The test `test_does_not_follow_the_virtualenv_python_symlink` injects the fake interpreter
`/home/u/proj/.venv/bin/python`, which **does not exist**, so `Path(...).resolve()` is a no-op on
it and the test cannot tell a resolving implementation from a non-resolving one. The test asserts
`seen == [Path("/home/u/proj/.venv/bin/ges2t")]`, which only proves the candidate's *directory* —
not that it was not symlink-resolved. **This is the defect.** The ticket's DoD ("break the
resolution and show the relevant test fails") is therefore not met for the behavior that matters
most. The fix is to test against a **real** symlink in `tmp_path` (system `python3` and a *different*
system `ges2t`; venv `python` symlinking to it; venv `ges2t` present), so a `.resolve()` mutation
names the wrong side and returns `None`.

**B. Remove the existence gate** → `return str(candidate)` unconditionally. Reproduced the
implementer's claim exactly:

```console
$ uv run pytest -q tests/test_console_script.py
FAILED test_returns_none_when_the_candidate_does_not_exist
FAILED test_does_not_follow_the_virtualenv_python_symlink
2 failed, 5 passed
$ uv run pytest -q
2 failed, 920 passed
```

**C. Remove the fallback** (all three sites) → `executable = console_script`. Fallback tests fail,
including the `'None toggle'` string:

```console
$ uv run pytest -q tests/test_doctor.py tests/test_cli.py -k "fall"
FAILED test_hotkey_bindings_falls_back_to_the_bare_command
FAILED test_hotkey_fix_falls_back_to_the_bare_command
FAILED test_kde_binding_section_falls_back_to_the_bare_command
FAILED test_kde_shortcut_hint_falls_back_to_the_bare_command
4 failed, 155 deselected
E   assert "bind 'ges2t toggle'" in "...bind 'None toggle' (recommended), or 'None ptt-start'..."
$ uv run pytest -q
5 failed, 917 passed   # also test_render_report_relists_failures_and_prints_the_bindings
```

So the existence gate and the fallback **are** pinned; the symlink non-following is **not**.
`grep -rn FALSIFICATION src/` is empty; `git status` clean; sha256 of all three files identical to
pre-mutation after restore.

## 8. README / ticket 19 agreement — **PASS**

README §7 says doctor "resolves the console script **it is itself running as** and prints every
command by that **absolute path** (`…/.venv/bin/ges2t toggle`) … falls back to the bare `ges2t …`
form … on a system install". That is exactly the code. §11's sample report and the §7 example use
the reference-checkout path `/home/matee/Workspace/Personal/ges2t/.venv/bin/ges2t`, which I confirmed
exists and runs (`env -i` probe above); the worktree prints its own `.worktrees/.../.venv/bin/ges2t`,
which is correct because a worktree has its own `.venv` (README already says so). No third variant,
no contradiction. `all 17 → all 18 package modules` matches `len(_OWN_MODULES)` after adding
`ges2t.console_script`.

## 9. Mechanicals — **PASS**

```console
$ timeout 300 uv run pytest -q
922 passed in 5.84s          # PIPESTATUS=0; process exited (no 124 hang)

$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 55 source files

$ python3 <link check from orchestrator-state.md §8>
0 broken references

$ git diff --numstat main...HEAD -- tests/
31  0  tests/test_cli.py
92  0  tests/test_console_script.py
79  0  tests/test_doctor.py
```

No test deleted, skipped, weakened or `xfail`ed (additions only). `git status` clean;
`src/` and `tests/` byte-identical to `HEAD`.

---

## Blockers (request changes)

1. **The symlink non-following behavior is unpinned.** Add a test that uses a **real symlink** (in
   `tmp_path`) so that a `.resolve()` regression fails, or assert via a probe that the candidate
   handed to `exists` is the venv path while the interpreter is really a symlink to a directory that
   also contains `ges2t`. Without it, ticket 40's DoD item "break the resolution and show the
   relevant test fails" is unfulfilled, and the exact defect can silently return.

## Non-blocking observations

- `kde_binding_section`'s fallback is tested only for `None`, not `""` (the same ternary is covered
  for `""` in `hotkey_fix`/`kde_shortcut_hint`; low risk).
- `test_key_bound_help_prints_a_bindable_command` derives its expectation from
  `resolve_console_script()` itself, so it proves consistency, not correctness; the mapping is
  pinned by the injected unit tests, so this is acceptable.
- ADR 0019 bans sentinels as return values; `resolve_console_script -> str | None` uses `None` for
  "not found". The ticket explicitly designs for `None` and it is idiomatic optionality, so I do not
  treat it as a defect — noting it for the record.
- Cosmetic: the absolute path makes the CLI epilog lines very long (already disclosed in
  [001-comment.md](001-comment.md) §8).

## Could not verify

- **A real KDE shortcut.** Binding needs the Plasma GUI and is consequential; not performed. The
  positive evidence is the rendered absolute path, its absolute shebang, and my `env -i` execution
  of that exact path.
- **A real system install at `/usr/bin`.** None exists on this machine; the system case is covered
  by a real-filesystem `/tmp` tree with the default probe and by the injected unit test.
- **Whether any user actually had `ges2t` shadowed on `PATH`.** Not reproduced; the defect is
  verified from the install layout and ADR 0004.
- **Long help-line wrapping** (cosmetic; no terminal width available to the parser).

## Merge safety

The **implementation is safe to merge as-is** — all shipped behavior is correct and all gates pass.
The branch should **not** be merged until the symlink test is strengthened, because the DoD's
falsification requirement for the ticket's core behavior is not met and the test's name currently
overstates what it covers.

_Note: a pre-existing user service (`uv run ges2t run`, pid 4113931, started 10:13) was already
running; it was not touched. I never ran `ges2t run`, recorded audio, touched the clipboard, or
emitted a keystroke. All probe files were created under `/tmp` and removed._
