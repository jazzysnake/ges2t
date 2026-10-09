# 002 — review: CLI surface and commands

**Ticket:** `cli-surface-and-commands` · **Branch:** `feat/cli-surface-and-commands` · **HEAD:** `21e62ce`
**Reviewer:** adversarial review against ADR 0016's command table and exit codes, the ticket's
Definition of Done, and the implementer's `001-comment.md`. Every claim below was reproduced
by hand; probes lived in `/tmp` outside the repo and have been removed. `git status` was clean
before and after.

## Verdict: **pass** — no blocking findings.

All nine subcommands exist with the documented purposes; the documented exit codes are correct
at runtime (`0`, `2`, `3`, plus the documented generic `1` for refusals, and `doctor`'s
unowned non-zero); laziness is real (independently verified in a fresh subprocess); every
failure prints a sentence and no traceback reaches the user without `--debug`/`GES2T_DEBUG`;
the KDE binding instructions are present in the four key-bound commands' help; no subcommand
writes config or compositor state; `--config` resolves through `config.resolve_config_path`;
mypy passes; scope is exactly `src/ges2t/cli.py` + `tests/test_cli.py`. Two **non-blocking
test-honesty gaps** were found and are reported as F1/F2; neither is a functional defect, and
the underlying behaviour is independently verified correct.

---

## Per-command findings table

| Command | Exists | Matches ADR 0016 purpose | Observed exit codes | Service-free/lazy | Findings |
|---|---|---|---|---|---|
| `run` | yes | lazy `ges2t.pipeline.run_service(config)`, returns its code | `1` (module missing, clean) | lazy import; no service needed | contract correct; body is ticket 11 |
| `toggle` | yes | thin client sends `toggle` | `0` ok; `2` no service; `3` state=ERROR; `1` other refusal | service required; does not import engine/GUI | — |
| `ptt-start` | yes | thin client sends `ptt-start` | `0` ok; `2` no service | service required | — |
| `ptt-stop` | yes | thin client sends `ptt-stop` | `0` ok; `2` no service; `3` when state=ERROR | service required | — |
| `cancel` | yes | thin client sends `cancel` | `0` ok; `2` no service; `1` "not recording" | service required | — |
| `status` | yes | prints state, exits `0` | `0` always with a live service (prints `error` too); `2` no service | service required | special-case is correct |
| `setup` | yes | `ensure_model` + `ensure_engine`, progress, exit `0` | `0` ok; `1` store error | no service; no network in tests | — |
| `doctor` | yes | lazy `ges2t.doctor.run_doctor(config)` | `1` (module missing, clean); runtime code is ticket 12's | lazy import; no service needed | runtime KDE hint deferred (F3) |
| `models` | yes | pins listed with present/verified status; `--verify` hashes | `0` listing; `1` `--verify` mismatch | no service; no network | — |

## 1. All nine subcommands

`uv run ges2t --help` lists exactly nine; each `--help` exits 0. Reproduced `run`, `toggle`,
`ptt-start`, `ptt-stop`, `cancel`, `status`, `setup` (injected model store), `doctor`,
`models`. No tenth `help` subcommand, as documented.

## 2. Exit codes — verified with real invocations

Named constants in `cli.py`: `EXIT_SUCCESS=0`, `EXIT_FAILURE=1`,
`EXIT_SERVICE_NOT_RUNNING=2`, `EXIT_ENGINE_FAILURE=3`, `EXIT_INTERRUPTED=130`. Forced each
path against a **real socket + real `Service`** (my own harness, not the ticket's tests):

```
-- healthy service --
toggle -> 0
recording
status -> 0
-- service that fails to start a session (engine failure) --
toggle -> 3
error
status -> 0
-- injection failure collapses to ERROR; ptt-stop in ERROR -> 3 --
toggle(start)   -> 0
toggle(stop)    -> 0
error
status          -> 0
ptt-stop(err)   -> 3
-- cancel while idle -> refusal exit 1 --
cancel -> 1
```

No-service path, all five thin clients (`echo $?`):

```
$ uv run ges2t toggle
ges2t: the ges2t service is not running (no answer from /tmp/tmp.0oc1D0hpd2/ges2t.sock: [Errno 2] No such file or directory); start it with 'ges2t run'
exit=2
```

`ptt-start`, `ptt-stop`, `cancel`, `status` all returned exactly `2` with the same message.
`status` printed `error` and returned `0` for an ERROR-state service, exactly as specified.
`socket_proto.send_command` raises `ServiceNotRunningError` on connect failure, timeout, EOF,
and OSError; `_send_request` maps that to `CliError(exit_code=2)`. ERROR responses map to `3`
in `_handle_thin_client`. Mapping is correct.

`doctor`'s non-zero code is `run_doctor(config)` returned unchanged, so ticket 12 owns it —
see "Could not verify".

## 3. Laziness — verified, and the test falsified honestly

Fresh subprocess, no test harness:

```
after `import ges2t.cli`:  []
main(['toggle']) exit: 0
after main(['toggle']):      []
import cli: 68.6 ms; main: 6.0 ms
```

None of `needle`, `gi`, `pystray`, `ges2t.pipeline`, `ges2t.doctor` is in `sys.modules` after
importing the module **or** after `main(["toggle"])`. `cli.py` imports only `argparse`,
`importlib`, `os`, `sys`, `traceback`, stdlib types, `ges2t.__version__`, `ges2t.service`,
`ges2t.socket_proto`.

**Falsification result (see F1):** the ticket's test
`test_toggle_does_not_import_the_engine_gui_or_later_tickets` **passes even with a top-level
`import pystray` added to `cli.py`**. It does catch a *runtime* import (adding
`import pystray` inside `_handle_thin_client` fails it). The reason: the test deletes the
forbidden names from `sys.modules` after `cli` has already been imported, and Python does not
re-run module-level import statements, so a top-level import is invisible to the assertion.
Both probes were reverted; `git status` clean.

## 4. Actionable errors, no tracebacks

Forced failures and their sentences (verbatim):

```
$ uv run ges2t run
ges2t: ges2t.pipeline.run_service is not available in this build (No module named 'ges2t.pipeline')
  hint: this command's implementation is not part of this build yet; reinstall ges2t from a complete release
exit=1
```

```
$ uv run ges2t setup        # with ensure_model raising ModelStoreError
Acquiring the pinned Whistle weights and Needle 3 engine.
This is the only ges2t command that uses the network; the rest runs offline.
ges2t: setup failed: download refused: connection reset by peer
  hint: check your network connection and the paths above, then re-run 'ges2t setup'
exit=1
```

Malformed config (through the same `_load_config` that `run`/`doctor` call):

```
ges2t: invalid configuration: /tmp/.../broken.toml: invalid TOML: Expected ']' at the end of a table declaration (at line 1, column 13)
  hint: fix the file named above, or pass a different one with '--config <path>'
exit_code=1
ges2t: invalid configuration: /tmp/.../wrong.toml: key 'audio.device': expected str, got int (42)
exit_code=1
```

**Judgement:** both quoted messages name the object and the fix. The service-absent line names
the socket path and the exact command (`ges2t run`); a user can act on it. The config line
names the file, the parse position or key, and the flag. Good.

`--debug` and `GES2T_DEBUG=1` append the full chained traceback; without them, only the
sentence. Verified for `run`, `doctor`, and an unexpected `RuntimeError` (`ges2t: unexpected
failure: boom` + "re-run with '--debug'"), and for `--debug` in **both** positions
(`--debug toggle` and `toggle --debug`). `main` does not catch `SystemExit`, so
`--version`/`--help`/argparse errors keep their normal behaviour.

## 5. `setup` and `models`

`setup` with an injected model store and `urllib.request.urlopen` rigged to raise: the two
`ensure_*` functions were called once each, progress callbacks fired, output was
`Model:`/`Engine:`/`Setup complete.`, exit `0`, and no network call occurred. Progress printer
dedupes to whole percent (`0%`, `42%`, `100%`) and never prints duplicate percents.

`models` with files of exactly the pinned byte size but wrong content:

```
--- plain `models` (size check only) ---
whistle.cact   2.0.0    present     ...
libneedle.so   3.2.0    present     ...
exit: 0
--- `models --verify` (full sha256) ---
whistle.cact   2.0.0    corrupt     ...
libneedle.so   3.2.0    corrupt     ...
ges2t: 2 artifact(s) did not verify; run 'ges2t setup'
exit: 1
```

This is the decisive proof that `--verify` forces the real sha256 (the plain path passes
size-mismatched content; `--verify` catches it and returns non-zero).

Service requirements: `models` returned `0` with no service running; `setup` is service-free;
`doctor` reaches its lazy import (not a service error) with no service; `toggle`/`ptt-*`/
`cancel`/`status` all returned `2` with no service. Matches ADR 0016.

## 6. KDE binding help text

`toggle`, `ptt-start`, `ptt-stop`, `cancel` and `doctor` carry `KDE_SHORTCUT_HINT`; the top
level carries a short version. Verbatim from `ges2t toggle --help`:

```
Binding (KDE): System Settings -> Shortcuts -> Add -> Command or URL, then
enter the ges2t command shown above. True press-and-hold (ptt-start/ptt-stop)
works only when the compositor can fire separate key-press and key-release
commands; if it cannot, bind 'ges2t toggle' instead. ges2t never edits your
compositor settings or your config file.
```

It names the exact KDE path and states the compositor key-press/key-release requirement
(ADR 0004). Satisfies the item as worded ("help text"). The runtime doctor hint is deferred
(F3).

## 7. No config/compositor writes

By inspection: `cli.py` contains no `open(..., "w")`, `write`, `write_text`, `unlink`,
`mkdir`, `chmod`, `os.remove`, or `shutil` call (the only match for "write" is the module
docstring). Ran all eight commands that parse `--config` against an **existing** temp config:
content and nanosecond mtime were unchanged and the directory still held only `config.toml`.
Ran them against a **nonexistent** temp config: it was not created. `setup` writes only
artifacts under `$XDG_DATA_HOME/ges2t` via `model_store`, never the config.

## 8. `--config`

Accepted after the subcommand name by all nine subcommands
(`test_each_subcommand_parses_and_accepts_config` parametrizes all nine; verified by hand).
Resolution goes through `config.resolve_config_path` inside `_load_config`, then
`load_config`. End-to-end value propagation:

```
run --config exit: 0
config received audio.device: my-special-mic
config received ui.trailing_space: False
doctor --config exit: 0 audio.device: my-special-mic
```

Placement note: `--config` is a per-subcommand option only, so `ges2t --config x toggle`
rejects `/tmp/x.toml` as the COMMAND (exit 2). The `001-comment.md` states this explicitly
("after the subcommand name") and ADR 0016 only requires acceptance by each subcommand, so
this is consistent, not a defect.

## 9. ADR 0019 and mypy

```
$ uv run mypy src/ges2t tests
Success: no issues found in 30 source files
$ uv run mypy src/ges2t
Success: no issues found in 15 source files
```

`[tool.mypy]` sets `disallow_untyped_defs = true` and `no_implicit_optional = true`. Config
bites: appending an unannotated `_mypy_probe_function(x)` produced
`error: Function is missing a type annotation [no-untyped-def]` and exit 1; fully reverted and
mypy green again. No `Any`, `Optional`, or positional `tuple[...]` in `cli.py`; the only
`tuple[str, ...]` uses are homogeneous constants in tests, which ADR 0019 permits. Handlers
use `-> None`/`-> int`; `_load_entry_point` is typed `Callable[[Config], int]`; no
implementation-typed collaborator signatures.

## 10. Scope and hygiene

```
$ git diff --name-only <merge-base>..HEAD
docs/project/README.md
docs/project/core-dictation-pipeline/cli-surface-and-commands/001-comment.md
docs/project/core-dictation-pipeline/cli-surface-and-commands/ticket.md
src/ges2t/cli.py
tests/test_cli.py
```

Only `cli.py` + `test_cli.py` are code. `service.py`, `socket_proto.py`, `config.py`,
`model_store.py` were not touched; `pyproject.toml` and the root `README.md` are unchanged;
the only `docs/project/README.md` change is ticket 10's own row (`to do` → `in review`). No
`<<<<<<<`/`>>>>>>>` markers (the one grep hit was a `=====` pytest separator inside
`001-comment.md`). No stray `ges2t` processes and no `ges2t*.sock` left behind; `git status`
clean after all probes.

## 11. Tests

```
$ uv run pytest
407 passed in 1.15s
$ uv run pytest tests/test_cli.py -q
60 passed in 0.18s
```

Counts match the claim. Grep for `ServiceServer|serve_forever|urlopen|urllib|socket(|bind(|
download|sleep` finds only a docstring and the literal `"download refused"` string: no test
starts a service, downloads, or sleeps. The two service-absent tests use a private
`XDG_RUNTIME_DIR` with no socket, and the model-store tests monkeypatch paths/pins. Tests are
honest about behaviour: mutating a handler's return value or adding a runtime import fails
them. Two gaps in *value*-pinning are reported below.

---

## Findings

### F1 — non-blocking: the laziness test does not catch top-level imports

The test deletes the five forbidden names from `sys.modules` and then calls `main(["toggle"])`.
Because `tests/test_cli.py` imports `cli` at collection time, a module-level forbidden import
has already loaded and been deleted, and Python does not re-execute the import statement, so
the assertion never sees it:

```
$ pytest tests/test_cli.py::test_toggle_does_not_import_the_engine_gui_or_later_tickets -q
1 passed   # with `import pystray` added at the top of cli.py  (probe reverted)
1 failed   # with `import pystray` added inside _handle_thin_client (probe reverted)
```

The shipped code is genuinely lazy (section 3, subprocess check), so this is a test-strength
gap, not a defect. Suggested fix: assert that the forbidden names are absent **immediately
after importing `ges2t.cli`** in a subprocess (or record `set(sys.modules)` at import and
assert none of the forbidden names entered during `main`), in addition to the runtime check.

### F2 — non-blocking: exit-code tests compare against constants, not literals

The tests assert `main(["toggle"]) == EXIT_SERVICE_NOT_RUNNING` and
`== EXIT_ENGINE_FAILURE`, importing those constants from `cli`. Mutating the constants to
`7` and `42` still passes all 60 tests:

```
EXIT_SERVICE_NOT_RUNNING = 7
EXIT_ENGINE_FAILURE = 42
60 passed in 0.40s    # probe reverted
```

The runtime values are correct today (section 2), and the tests would catch a *behaviour*
change that stopped returning the constant. But the ticket's DoD asks the suite to cover
"returning exactly `2`" and "returning `3`", and a one-line swap of the constants would slip
through. Suggested fix: add a single test pinning the constants
(`assert EXIT_SERVICE_NOT_RUNNING == 2`, `assert EXIT_ENGINE_FAILURE == 3`, …) or compare
against literals at the two key call sites.

### F3 — non-blocking: doctor's runtime key bindings are deferred to ticket 12

The ticket's DoD says "`ges2t doctor` also prints the recommended bindings". Today the hint is
only in `doctor --help`; `run_doctor` does not exist, and `001-comment.md` is explicit that
runtime printing belongs to ticket 12. This is honest and expected, but the checkbox is
generous: the runtime requirement is not yet met. Ticket 12 must print the bindings.

### F4 — non-blocking: malformed config is masked by the missing module in the current build

`_handle_run`/`_handle_doctor` call `_load_entry_point` **before** `_load_config`, so in this
build `ges2t run --config broken.toml` reports the missing `ges2t.pipeline` module, not the
config error. Once tickets 11/12 exist this resolves naturally; no change needed here. Noted
so the ordering is a conscious choice, not an accident.

### F5 — minor: a malformed protocol response is a generic "unexpected failure"

A server answering non-JSON produces exit 1 with
`ges2t: unexpected failure: response is not valid JSON: ...` plus the "re-run with --debug"
line — no traceback, but less actionable than the other messages. `_send_request` catches only
`ServiceNotRunningError`; a `ProtocolError` falls through to the top-level guard. Acceptable,
but ticket 11/12 or a follow-up could map it to a version-mismatch sentence.

---

## Risks / follow-ups for the orchestrator

- **Ticket 11** must expose `ges2t.pipeline.run_service(config: Config) -> int`; **ticket 12**
  must expose `ges2t.doctor.run_doctor(config: Config) -> int`. The CLI passes the loaded
  `Config` object, not a path (fixed contract; changing it silently breaks `_handle_run`/
  `_handle_doctor`).
- **Ticket 12** must print the recommended key bindings at runtime (F3) and owns `doctor`'s
  non-zero codes; the CLI returns them unchanged.
- **Tickets 17/18** bind these exact command strings; renaming any subcommand is a breaking
  change to the user's compositor config (ADR 0016). The CLI strings are the fixed surface.
- **`_send_command(command: str) -> CommandResponse`** is the documented test seam; changing
  its shape breaks `tests/test_cli.py`.
- The F1/F2 test gaps are cheap to close and worth doing before the command surface is frozen,
  but they do not block this merge: the behaviours they would guard are correct at runtime and
  independently verified here.

## Could not verify

- `doctor`'s non-zero-when-broken behaviour and runtime binding output: `ges2t.doctor` does
  not exist yet (ticket 12). Structurally the CLI returns `run_doctor(config)` unchanged.
- `run`'s exit codes for "socket already in use" / "model missing": `ges2t.pipeline` does not
  exist yet (ticket 11); the CLI returns `run_service(config)` unchanged.
- Real network acquisition in `setup`: deliberately never exercised (would download ~18 MB).
  Verified instead through the injected model-store seam with `urllib` rigged to raise, plus
  the `model_store` unit tests.
