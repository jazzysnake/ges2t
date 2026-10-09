# CLI surface and commands

- **Priority:** P0
- **Status:** in review
- **Complexity:** M
- **Epic:** ../epic.md

## Current state / context

`project-scaffold` created `src/ges2t/cli.py` as a stub handling only `--version`/`--help`.
This ticket replaces it with the full v1 surface from ADR 0016 — which is user-facing API,
because these commands are what the user binds to keys in KDE and what the README documents.

`../service-state-machine-and-socket/ticket.md` provides the socket protocol and client
helper; `../model-store-and-verification/ticket.md` provides the acquisition functions.

**Fixed contract with two later tickets** (so they do not need to edit this file):

- `ges2t run` lazily imports `ges2t.pipeline.run_service(config) -> int` (ticket
  `../dictation-pipeline-batch/ticket.md`).
- `ges2t doctor` lazily imports `ges2t.doctor.run_doctor(config) -> int` (ticket
  `../doctor-command/ticket.md`).

If either import fails, print a clear internal error naming the missing module — do not
crash with a traceback.

Relevant decisions: ADR 0016 (the surface, exit codes, laziness), ADR 0015 (thin client),
ADR 0017 (setup/models), ADR 0009 (`--config`).

## Definition of done

- [x] `src/ges2t/cli.py` implements `main(argv=None) -> int` with `argparse` and exactly these
      subcommands, each with its ADR 0016 purpose:

      | Command | Behaviour |
      |---|---|
      | `run` | lazy-import and call `pipeline.run_service(config)`, return its exit code |
      | `toggle` | thin client: send `toggle`, map the response to an exit code |
      | `ptt-start` | thin client: send `ptt-start` |
      | `ptt-stop` | thin client: send `ptt-stop` |
      | `cancel` | thin client: send `cancel` |
      | `status` | thin client: send `status`, print the state to stdout, exit 0 |
      | `setup` | call `model_store.ensure_model` + `ensure_engine`, printing progress; exit 0 |
      | `doctor` | lazy-import and call `doctor.run_doctor(config)` |
      | `models` | list the pinned models, present/verified status; `--verify` forces a full sha256 check |

- [x] **Exit codes are implemented as documented**, not as arbitrary 1s: `0` success; `2`
      service not running/absent socket; `3` engine or injection failure. `doctor` returns
      non-zero when something is broken. Each code is defined as a named constant.
- [x] `--config <path>` is accepted by every subcommand that reads configuration, and passed
      through to `load_config`.
- [x] **Lazy imports.** Running `ges2t toggle` must not import `needle`, `gi`,
      `pystray`, `ges2t.pipeline`, or `ges2t.doctor`. Assert this in a test by inspecting
      `sys.modules` after invoking `main(["toggle"])` with a stubbed client.
- [x] **Every failure prints an actionable sentence** and returns its exit code; no raw
      traceback reaches the user. A traceback is shown only when `--debug` (or an env var) is
      set.
- [x] Help text for `toggle`, `ptt-start`, `ptt-stop`, and `cancel` includes the KDE
      **System Settings → Shortcuts → Add → Command or URL** line to bind them, since that is
      the only way the hotkey works (ADR 0004). `ges2t doctor` also prints the recommended
      bindings.
- [x] No subcommand writes to the user's config file or compositor settings.
- [x] `tests/test_cli.py` covers: `--version`; `--help` for the parser and for each
      subcommand; argument parsing for every subcommand including `--config`; the
      service-absent path returning exactly `2` with a clear message; the engine-failure path
      returning `3`; `status` printing the state; `setup` calling the injected
      `model_store` functions (monkeypatched, no network); `models` listing and
      `--verify` behaviour; and the **lazy-import** assertion above.
- [x] `uv run pytest tests/test_cli.py` passes and `uv run pytest` stays green.
- [x] `uv run ges2t --help` lists all nine subcommands, and
      `uv run ges2t toggle` with no service running prints a clear message and exits `2`
      (verified by hand, reported in `001-comment.md`).

- [x] `uv run mypy src/ges2t` exits 0: every definition fully annotated and every
      signature interface-typed — ADR 0019.

## Notes

- One console script, no plugin entry points; ADR 0016 explains why adding a subcommand is
  cheap but renaming one is a breaking change to the user's compositor config.
- Use `argparse` subparsers rather than a CLI framework. An extra dependency is not justified
  for nine commands (ADR 0001's "prefer stdlib on 3.14" posture).
- Keep `main()` returning an `int` and let the `console_scripts` wrapper exit with it, so
  tests can call it directly.
- Do not implement `run_service` or `run_doctor` here, and do not add placeholder bodies that
  duplicate their logic — just the lazy calls.

## Related tickets

- depends on: ../config-loading/ticket.md, ../model-store-and-verification/ticket.md,
  ../service-state-machine-and-socket/ticket.md
- blocks: ../dictation-pipeline-batch/ticket.md, ../doctor-command/ticket.md,
  ../../live-dictation-ux/user-documentation-and-setup-guide/ticket.md
- related ADRs: ../../../adr/0016-cli-surface.md, ../../../adr/0015-single-service-process-architecture.md,
  ../../../adr/0017-model-acquisition-via-setup.md
