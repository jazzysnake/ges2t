# 001 — CLI surface and commands

**Ticket:** `cli-surface-and-commands` · **Branch:** `feat/cli-surface-and-commands` · **Status:** in review

## Files

- `src/ges2t/cli.py` (rewritten; was the `--version`/`--help` stub)
- `tests/test_cli.py` (new, 60 tests)

No other module was touched. `service.py`, `socket_proto.py`, `config.py` and
`model_store.py` are unchanged.

## Final surface

`main(argv: Sequence[str] | None = None) -> int`, built with `argparse` subparsers.
One console script, `ges2t`, and exactly nine subcommands (ADR 0016):

| Command | Wiring | Exit codes |
|---|---|---|
| `run` | lazy `ges2t.pipeline.run_service(config)` | whatever `run_service` returns |
| `toggle` | `socket_proto.send_command(path, Command.TOGGLE.value)` | 0 / 1 / 2 / 3 |
| `ptt-start` | `send_command(..., Command.PTT_START.value)` | 0 / 1 / 2 / 3 |
| `ptt-stop` | `send_command(..., Command.PTT_STOP.value)` | 0 / 1 / 2 / 3 |
| `cancel` | `send_command(..., Command.CANCEL.value)` | 0 / 1 / 2 / 3 |
| `status` | `send_command(..., Command.STATUS.value)` | 0 / 2 (prints `error` but still 0) |
| `setup` | `model_store.ensure_model` + `ensure_engine` | 0 / 1 |
| `doctor` | lazy `ges2t.doctor.run_doctor(config)` | whatever `run_doctor` returns |
| `models` | `model_store` pins + `verify()` | 0; `--verify` returns 1 on any mismatch |

Cross-cutting options, accepted by **every** subcommand (after the subcommand name):
`--config PATH` and `--debug`. `--version` and `--debug` are also accepted before the
subcommand. `--config` is resolved through `config.resolve_config_path` and passed to
`config.load_config`; only `run` and `doctor` consume it today.

Help text: `toggle`, `ptt-start`, `ptt-stop`, `cancel` and `doctor` carry
`KDE_SHORTCUT_HINT`, which names **System Settings → Shortcuts → Add → Command or URL**
and states that true press-and-hold needs a compositor that fires separate key-press and
key-release commands, otherwise `toggle` is the binding to use. The top-level `--help`
also carries a short version of the same line.

## Exit-code mapping

Named constants, not literals:

```python
EXIT_SUCCESS = 0                 # worked
EXIT_FAILURE = 1                 # documented generic failure (config, setup, refusal)
EXIT_SERVICE_NOT_RUNNING = 2     # ServiceNotRunningError, or absent socket
EXIT_ENGINE_FAILURE = 3          # response.state is ServiceState.ERROR
EXIT_INTERRUPTED = 130           # KeyboardInterrupt
```

- `ServiceNotRunningError` from `socket_proto.send_command` → `CliError(..., exit_code=2)`.
  Its own message already says `start it with 'ges2t run'`, so that sentence is printed
  verbatim.
- A response with `state is ServiceState.ERROR` → exit `3`. This covers both engine and
  injection failures, because the service collapses them into `ERROR` before replying.
- A non-`ok` response in any other state (`not-recording`, `already-recording`, `busy`)
  → exit `1`, with the service's `message`.
- `status` is special: it ignores `ok` and prints `response.state.value`, so `error`
  prints `error` and exits `0` (it answered; the answer is what the user asked for).
- `doctor` returns `run_doctor(config)` unchanged, so ticket 12 owns its non-zero codes.
- `models` returns `0` for a plain listing; with `--verify` it returns `1` if any artifact
  is missing, size-wrong, corrupt or platform-unsupported (see judgement calls).

## How laziness is achieved

- `cli.py` imports only `argparse`, `importlib`, `os`, `sys`, `traceback`, stdlib types,
  `ges2t.__version__`, and the vocabulary `ges2t.service` / `ges2t.socket_proto` (needed
  for `Command`, `CommandResponse`, `ServiceState`, `ServiceNotRunningError`). None of
  those pull in `needle`, `gi`, `pystray`, `ges2t.pipeline` or `ges2t.doctor`.
- `ges2t.config` is imported inside `_load_config`; `ges2t.model_store` inside
  `_handle_setup`/`_handle_models`; the real socket client inside `_send_command`;
  `ges2t.pipeline`/`ges2t.doctor` inside `_load_entry_point`, only when `run`/`doctor`
  actually execute.
- `tests/test_cli.py::test_toggle_does_not_import_the_engine_gui_or_later_tickets` deletes
  the five forbidden names from `sys.modules`, runs `main(["toggle"])` with a stubbed
  client, and asserts none reappeared. It also asserts the verb sent was `"toggle"`.

## `run`/`doctor` lazy-import contract

`_load_entry_point(module_name, attribute)` uses `importlib.import_module` on a string,
then `getattr`, and raises `MissingImplementationError` (a `CliError`) on either
`ImportError` or `AttributeError`. The message names `ges2t.pipeline.run_service` /
`ges2t.doctor.run_doctor` plus the underlying cause, and the hint says the build is
incomplete. `main` prints it as a sentence and returns `1`; a traceback appears only with
`--debug`/`GES2T_DEBUG`. Ticket 11 implements `ges2t.pipeline.run_service(config) -> int`
and ticket 12 `ges2t.doctor.run_doctor(config) -> int`; neither body is stubbed here.

Observed today (both tickets unwritten):

```text
$ uv run ges2t run
ges2t: ges2t.pipeline.run_service is not available in this build (No module named 'ges2t.pipeline')
  hint: this command's implementation is not part of this build yet; reinstall ges2t from a complete release
$ uv run ges2t doctor
ges2t: ges2t.doctor.run_doctor is not available in this build (No module named 'ges2t.doctor')
  hint: this command's implementation is not part of this build yet; reinstall ges2t from a complete release
```

Both exit `1`, with no traceback.

## Judgement calls

- **`--config` on all nine commands.** ADR 0016 says "every subcommand that reads
  configuration"; only `run` and `doctor` read it today. I put `--config` on every
  subcommand for a uniform surface, so a future command that reads config does not change
  the user's compositor binding, and so `ges2t toggle --config …` never fails on an
  unrecognised argument. Commands that do not read it ignore it silently; none of them
  write it.
- **`--config`/`--debug` default to `argparse.SUPPRESS`.** In Python 3.14 a subparser
  copies its whole namespace over the parent's, so a plain `False`/`None` default on the
  per-subcommand option clobbered a top-level `--debug` passed *before* the subcommand.
  `SUPPRESS` makes the attribute simply absent unless the option is given, and every read
  goes through `getattr(args, …, default)`. Both `ges2t --debug toggle` and
  `ges2t toggle --debug` now work (tested).
- **`models --verify` exits non-zero on mismatch.** ADR 0016's table gives `models` "0 ok",
  but a corrupted artifact is a broken install, so a *verifying* run returns `1` (the
  documented generic failure) when anything is missing/wrong/corrupt/unsupported. A plain
  listing always returns `0`. This is the one place I extended the ADR's exit-code table;
  `doctor` remains the designated non-zero diagnostic.
- **No `help` subcommand.** `argparse` already provides `-h/--help` on each subcommand;
  ADR 0016 fixes the surface at nine commands, so I did not add a tenth.
- **`setup` progress** is printed as whole-percent lines (`  whistle.cact: 42%`) via a
  small `_ProgressPrinter` callable, deduplicated per percent. `models` prints a small
  aligned table; `--verify` hashes each artifact with `model_store.verify` (ADR 0017's
  explicit-check path), while a plain listing only checks existence and byte size, which is
  the cheap check ADR 0017 mandates for the load path.
- **No argument-framework dependency** (ADR 0001 posture): stdlib `argparse` only.
- **`main` never catches `SystemExit`.** `--version`/`--help` and argparse errors keep
  their normal behaviour; the `console_scripts` wrapper exits with `main`'s `int`.

## Orchestrator notes

- **Do not rename a subcommand.** It is the string in the user's KDE shortcut configuration
  (ADR 0004/0016); renaming needs a superseding ADR.
- Ticket 11 must expose `ges2t.pipeline.run_service(config: Config) -> int`; ticket 12 must
  expose `ges2t.doctor.run_doctor(config: Config) -> int`. The CLI passes the **loaded
  `Config` object**, not a path. `doctor` should print the recommended key bindings at
  runtime (ADR 0016); this ticket only put them in `doctor --help`, because `run_doctor`
  does not exist yet.
- `_send_command(command: str) -> CommandResponse` is the test seam for thin clients.
  Changing its shape breaks `tests/test_cli.py`.
- `models --verify` returning non-zero is a deliberate, documented extension of ADR 0016's
  table; if the reviewer wants strict "0 ok", that is a one-line change.

## Verification (real output)

```text
$ uv run pytest
============================= 407 passed in 1.72s ==============================

$ uv run pytest tests/test_cli.py -q
............................................................             [100%]
60 passed in 0.39s

$ uv run mypy src/ges2t tests
Success: no issues found in 30 source files

$ uv run ges2t --help
usage: ges2t [-h] [--version] [--debug] COMMAND ...
...
commands:
  COMMAND
    run        run the service and status UI (starts the long-lived process)
    toggle     start recording if idle, else stop and transcribe (default key
               binding)
    ptt-start  start recording (press-to-talk, key press)
    ptt-stop   stop recording and transcribe (press-to-talk, key release)
    cancel     abort the current recording without transcribing
    status     print the service state (idle, recording, transcribing or
               error)
    setup      download and verify the pinned model and engine (the only
               network use)
    doctor     diagnose the environment and print the recommended key bindings
    models     list the pinned models and whether they are present or verified

$ XDG_RUNTIME_DIR=$(mktemp -d) uv run ges2t toggle
ges2t: the ges2t service is not running (no answer from /tmp/tmp.CEoJFS0cnm/ges2t.sock: [Errno 2] No such file or directory); start it with 'ges2t run'
$ echo $?
2
```

`uv run ges2t run` and `uv run ges2t doctor` fail their lazy imports (tickets 11 and 12
unwritten) with the clean two-line message shown above and exit `1` — no traceback. With
`--debug` the same run appends the full chained traceback.
