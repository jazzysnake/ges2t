# Config loading: generic layer, engine selector, engine-specific blocks

- **Priority:** P0
- **Status:** done
- **Complexity:** S
- **Epic:** ../epic.md

## Current state / context

No configuration code exists. ADR 0009 fixes both the file location
(`$XDG_CONFIG_HOME/ges2t/config.toml`, default `~/.config/ges2t/config.toml`), the format
(TOML via the stdlib `tomllib`), and — importantly — a **hard split between a generic layer
and engine-specific blocks**. The user explicitly required that split: the generic config
must not contain engine assumptions, and it must contain an **engine selector**.

The schema and all rules are in ADR 0009; read it in full. This ticket implements the
**generic layer only**. It must not know what `window_seconds` means.

Relevant decisions: ADR 0009 (schema and rules), ADR 0010 (the factory that consumes the
selector).

## Definition of done

- [x] `src/ges2t/config.py` defines frozen dataclasses for the generic sections:
      `TranscriberConfig(engine: str, model_path: str)`, `HotkeyConfig(mode: str, key: str)`,
      `AudioConfig(device: str)`, `InjectionConfig(backend: str)`, `UIConfig(tray: bool,
      notifications: bool, trailing_space: bool)`, `StreamingConfig(enabled: bool)`, and a
      top-level `Config` holding them plus `engine_blocks: Mapping[str, Mapping[str, Any]]`.
- [x] `load_config(path: Path | None = None) -> ConfigLoadResult` returns a **frozen
      dataclass** `ConfigLoadResult(config: Config, warnings: list[str])` — warnings are a
      typed field, not a tuple element (ADR 0019). It implements exactly ADR 0009's rules:
      - a **missing file yields all defaults**, and is not an error;
      - invalid TOML raises a clear error naming the file and the parse problem;
      - a wrong-typed value (e.g. `tray = "yes"`) raises a clear error naming the file, the
        key, the expected type and the received type;
      - **unknown keys in the generic sections are collected and returned as warnings**, not
        raised — expose them so `doctor` can print them;
      - `engine_blocks` contains **every raw `[engine.<name>]` table**, uninterpreted;
      - a `transcriber.engine` value that is not a known engine name raises a clear error
        listing the known names;
      - `hotkey.mode` accepts only `"ptt"` and `"toggle"`; anything else is an error.
- [x] The set of known engine names is a single module-level constant (a tuple), so the
      factory and the error message cannot drift apart.
- [x] `--config <path>` resolution is provided as a helper the CLI can call, with the XDG
      default used when the flag is absent.
- [x] `tests/test_config.py` covers every bullet above, including: absent file →
      defaults; unknown generic key → warning not error; unknown engine name → error
      listing known names; `[engine.whistle]` and `[engine.something_else]` both survive
      into `engine_blocks` verbatim; and a bad-type case.
- [x] `uv run pytest tests/test_config.py` passes and `uv run pytest` stays green.

- [x] `uv run mypy src/ges2t` exits 0: every definition fully annotated and every
      signature interface-typed — ADR 0019.

## Notes

- **Do not validate or interpret `[engine.*]` contents.** ADR 0009 makes that the adapter's
  job. In particular, do not add `window_seconds` or `language` to any generic dataclass —
  their home is `[engine.whistle]`, validated by
  `../whistle-engine-config/ticket.md`.
- Blocks for engines that are not selected must be **preserved, not errors**; a user may keep
  a second engine configured while running the first.
- `tomllib` is read-only. If a test needs a config file, write it with `Path.write_text` in
  `tmp_path`; do not add a TOML-writing dependency.
- Keep `tomllib` usage in this module only — no other module should open the config file.
- The default `engine` value is `"whistle"` and the default `model_path` is `""` (meaning
  "use the default cache location", resolved in ADR 0017 / the model store ticket).

## Related tickets

- depends on: ../project-scaffold/ticket.md, ../core-interfaces-and-fakes/ticket.md
- blocks: ../whistle-engine-config/ticket.md, ../service-state-machine-and-socket/ticket.md,
  ../cli-surface-and-commands/ticket.md
- related ADRs: ../../../adr/0009-configuration-toml.md,
  ../../../adr/0010-transcriber-interface-design.md,
  ../../../adr/0019-code-style-typing-and-interfaces.md
