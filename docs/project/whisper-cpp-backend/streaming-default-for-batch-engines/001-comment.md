# 001 — comment: streaming default for batch-only engines

- **Ticket:** `docs/project/whisper-cpp-backend/streaming-default-for-batch-engines/ticket.md`
- **Branch:** `feat/streaming-default-for-batch-engines`
- **Worktree:** `.worktrees/streaming-default-for-batch-engines`
- **Status:** in progress

## Scope read first (before touching code)

The ticket, `../epic.md`, ADR 0021 (§8 in particular), ADR 0011 (and its 2026-10-09 amendment),
ADR 0019, ADR 0009, the merged ticket 45 (`whisper-backend-config-and-validation`) and ticket 47
(`engine-naming-and-setup-guidance`), their comment/review artifacts, and the current code:
`config.py`, `pipeline.py`, `setup_command.py`, `cli.py`, `doctor.py`.

Baseline on the branch point (`ce8b4d7`, main after ticket 47 merged):

```
$ uv run pytest -q
1194 passed in 11.11s
```

(The orchestrator's stated `923` baseline predates tickets 43–47, which added the whisper backend,
validation, doctor and setup-guidance tests; `1108` at ticket 45's branch point, `1194` here.)

## The bug, restated precisely

`StreamingConfig.enabled` defaults to `True`. Ticket 45's `_validate_whisper_config` refuses
startup whenever `config.streaming.enabled` is true **and** `transcriber.engine == "whisper_cpp"`.
So the minimal config

```toml
[transcriber]
engine = "whisper_cpp"
```

is refused for a value the user never wrote. Ticket 45 was right to refuse an *explicit* conflict
(`supports_streaming = False` would otherwise silently drop the user's request for incremental
typing, ADR 0003/0020); it wrongly treated "the default" as "the request".

## Mechanism decision (ticket requires a justified choice)

**Chosen: an engine-aware default applied in the generic config parse.** `StreamingConfig.enabled`
is resolved at `load_config` time from the selected engine: it defaults to `True` for engines that
stream and `False` for engines the build declares batch-only. An *explicit* value always wins.

The two candidates the ticket named, and why this one:

1. **Engine-aware default (chosen).** After `load_config`, `config.streaming.enabled` is already
   the *effective* value, so every existing reader — `_streaming_is_selected`, `_select_session_kind`,
   `_validate_injection_backend`, `_validate_whisper_config`, and `doctor._whisper_gate` — keeps
   reading one truthful boolean and needs no change. That matters: ADR 0011's rule is "streaming is
   the default when the backend declares `supports_streaming = True`", and this makes the shipped
   default actually agree with that declaration. There is no new type (`bool | None`), no second
   field, and no resolution step that each consumer could forget — the class of drift the project
   keeps getting bitten by.
2. **Tracking "was the key explicitly present" (rejected).** This would keep
   `StreamingConfig.enabled = True` and add explicitness state, then resolve lazily at each call
   site. The problem is exactly the call sites: `_validate_injection_backend` runs *before* the
   transcriber exists (it must stay a config-only check, ticket 34/45 ordering), so it could not
   consult `Transcriber.supports_streaming` without being moved after the artifact/build steps —
   weakening the "config error the user can fix without artifacts" guarantee. Every other reader
   (`doctor`, the session selection) would have to be converted too, and any missed conversion is a
   silent wrong path. The ticket explicitly warns against hidden state; a lazily-resolved flag
   threaded through six call sites is more state, not less.

**No hardware probe, no hidden state.** The default is a pure, deterministic function of the
engine *selector string* the file already contains — the same kind of parse ADR 0009 already owns
(engine names). It never inspects Vulkan, a device, or a model: the device-aware model default
stays where ticket 45 put it (the composition root's `resolve_whisper_cpp_config`). The batch-only
declaration lives in `config.py` next to `KNOWN_ENGINES` as a single constant, and a guard test
asserts it agrees with `Transcriber.supports_streaming` for every known engine so the two cannot
drift.

_(evidence, exact matrix output, falsification, ADR amendment draft, and counts appended below as
the work lands.)_
