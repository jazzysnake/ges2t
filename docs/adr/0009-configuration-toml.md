# Configuration is TOML at `~/.config/ges2t/config.toml`

- **Status:** accepted
- **Date:** 2026-10-09

## Context

v1 has no settings GUI (ADR 0000), so every user-tunable knob must live in a file. The user
was asked for format and location and answered: **TOML**, at the path the orchestrator
proposed, `~/.config/ges2t/config.toml`.

Requirements that shaped the schema:

- The **input device** must be configurable (explicit user requirement).
- **Mode** must be configurable between press-to-talk and toggle, with **press-to-talk as
  the default** (explicit user requirement).
- The **hotkey** is configurable, but on Wayland the actual key grab happens in the
  compositor (ADR 0004), so the config value is documentation/default-generation, not a
  binding.
- Backend selectors exist for the pluggable seams (`Transcriber`, `AudioSource`,
  `Injector`), even though each has exactly one implementation in v1.
- Post-processing is out of scope (ADR 0000), but the **trailing space** the user asked for
  (ADR 0012) is a configurable behaviour.

## Decision

Configuration is read from **`$XDG_CONFIG_HOME/ges2t/config.toml`**
(default **`~/.config/ges2t/config.toml`**), parsed with the stdlib `tomllib`. A `--config
<path>` flag overrides the location.

Accepted v1 schema (keys shown with their defaults):

```toml
[hotkey]
mode = "ptt"          # "ptt" | "toggle"
key  = ""             # documentation only; bound in the compositor

[audio]
device = ""           # PipeWire target (node name or id); "" = system default

[transcriber]
backend  = "whistle"
model_path = ""       # "" = default cache location (ADR 0017)
language = ""         # "" = engine auto-detect

[injection]
backend = "ydotool"

[ui]
tray           = true
notifications  = true
trailing_space = true   # ADR 0012

[streaming]
enabled = true          # ADR 0011
```

Rules the implementation must follow:

- **Missing file is not an error.** Absent config means "all defaults"; `ges2t run` works
  out of the box. The file is created only by an explicit user action, never silently.
- **Invalid TOML or a wrong-typed value is a hard, human-readable error** naming the file
  and the offending key. Never fall back to defaults silently, and never start recording
  with half a config.
- **Unknown keys are tolerated but surfaced**: `ges2t doctor` reports them as warnings. This
  keeps forward compatibility cheap without hiding typos entirely.
- **`tomllib` only.** No TOML-writing dependency is added; if a ticket needs to write a
  config, it writes text or does not write at all.
- Config is loaded and validated **once at service start** and re-read only on explicit
  request, never mid-recording.

## Consequences

- Zero third-party dependency for configuration, which fits the "prefer stdlib on Python
  3.14" posture (ADR 0001).
- The schema is the contract between tickets: the config ticket defines the dataclasses, and
  every later ticket consumes them rather than reading TOML itself.
- Because the hotkey is not actually grabbed by us, users can edit `key` and see no
  behaviour change — so `doctor` must explain that the real binding lives in System
  Settings. This is a documentation burden we accept in exchange for not needing input
  permissions (ADR 0004).
- `model_path = ""` deliberately defers the cache layout decision to ADR 0017, so this ADR
  does not hardcode a path that the setup ticket may need to change.
