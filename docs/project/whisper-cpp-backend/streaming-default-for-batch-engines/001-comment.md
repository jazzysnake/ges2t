# 001 — comment: streaming default for batch-only engines

- **Ticket:** `docs/project/whisper-cpp-backend/streaming-default-for-batch-engines/ticket.md`
- **Branch:** `feat/streaming-default-for-batch-engines`
- **Worktree:** `.worktrees/streaming-default-for-batch-engines`
- **Status:** in review

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

## What changed

- **`src/ges2t/config.py`** — new `BATCH_ONLY_ENGINES = frozenset({"whisper_cpp"})` next to
  `KNOWN_ENGINES`; `load_config` now reads `streaming.enabled` with
  `default=engine_value not in BATCH_ONLY_ENGINES` (explicit value still wins);
  `StreamingConfig`'s docstring says `enabled` is the *effective* value.
- **`src/ges2t/pipeline.py`** — `_select_session_kind` checks the capability **first** so the
  logged reason names the real cause (a batch-only engine with streaming omitted says
  "does not support streaming", not "streaming.enabled = false"); new
  `_announce_batch_only_engine(config, transcriber)` prints the one-line notice and is called in
  `_startup` right after `build_transcriber`; the two whisper docstrings now say "explicitly".
  The refusal message itself is **unchanged** (the good one, naming both settings).
- **`src/ges2t/setup_command.py`** — `config_guidance` no longer prints the now-redundant
  `[streaming] enabled = false` block; it states the consequence and that the line is not needed.
- **`README.md`** — the whisper section (no `[streaming]` line needed; explicit `true` refused)
  and the setup section updated to match.
- **`docs/adr/0021-whisper-cpp-second-backend.md`** — a dated **amendment** appended (not a
  rewrite of §8), *flagged for sign-off below*.
- Tests: `tests/test_config.py` (engine-aware default, empty table, explicit wins),
  `tests/test_whisper_backend_config.py` (the matrix, the drift guard, the notice, the wiring),
  `tests/test_setup_command.py` (guidance now says the line is not needed).

**No `whisper_engine.py` change** (ticket 49 is editing it in parallel). `doctor.py` needed **no**
change: `_whisper_gate` reads `config.streaming.enabled`, which is now the effective value, so it
reports the rejection only for an explicit `true` and proceeds for the minimal config.

## The startup matrix — real output of a probe (no files committed)

```
BATCH_ONLY_ENGINES = ['whisper_cpp']

unset          + whisper_cpp: enabled=False validate=STARTS session=batch
explicit true  + whisper_cpp: enabled=True  validate=ERROR  session=batch
explicit false + whisper_cpp: enabled=False validate=STARTS session=batch
unset          + whistle:     enabled=True  validate=STARTS session=streaming
```

The explicit-`true` error is verbatim:

```
transcriber.engine = "whisper_cpp" cannot be combined with streaming.enabled = true:
whisper.cpp is batch-only, so there would be no text until the utterance ends.
  hint: set streaming.enabled = false to run whisper.cpp batch-only, or set
        transcriber.engine = "whistle" (the default) to keep live and incremental typing
```

The one-line notice, printed at startup for a batch-only engine:

```
ges2t: transcriber 'whisper_cpp' is batch-only, so streaming is off and there is no text until the utterance ends
```

Reading: `enabled=False` on the unset row is the fix — the default follows the engine — and the
row still **starts** rather than being refused for a value the user never wrote. The notice is a
single line on stderr, once per startup, and never fires for `whistle` (`session=streaming`).

### Confound / scope note (pre-empting the obvious question)

I did **not** run the real service (it would take over the user's machine: socket, tray, model
load). `validate` is the real `pipeline._validate_whisper_config` with the
`_whisper_missing_artifacts` artifact probe stubbed to `[]` so the matrix tests the streaming rule,
not the (separately-tested) artifact rule; `session` is the real `pipeline._select_session_kind`
with the same fakes the suite uses. The notice is the real `_announce_batch_only_engine`.

## Falsification — each row broken, watched fail, reverted (`git status` clean after each)

| # | break | failing test(s) |
|---|---|---|
| F1 (unset + batch → starts) | config default back to `default=True` | `test_omitted_streaming_enabled_follows_the_engine`, `test_omitted_streaming_with_a_batch_only_engine_starts` |
| F2 (explicit true → error) | `if False and config.streaming.enabled` in `_validate_whisper_config` | `test_explicit_true_streaming_with_a_batch_only_engine_is_still_refused`, `test_streaming_with_whisper_is_refused_naming_both_settings` (both fall through to the artifact error) |
| F3 (explicit false → starts) | force the effective value true for a batch-only engine | `test_explicit_false_streaming_with_a_batch_only_engine_starts` |
| F4 (whistle + unset → streams) | streaming default false for **every** engine | `test_omitted_streaming_enabled_follows_the_engine`, `test_whistle_with_omitted_streaming_still_streams_from_a_file` |
| F5 (drift guard) | `BATCH_ONLY_ENGINES` swapped to `{whistle}` | `test_batch_only_engines_agree_with_the_transcriber_capability` |
| F6 (startup wiring) | `_announce_batch_only_engine(...)` call removed from `_startup` | `test_startup_announces_a_batch_only_engine` |

Note on F3: the engine-aware-default mechanism deliberately does **not** keep the "was the key
written?" bit, so an F3 break (force `true`) also breaks the F1 unset case — both are the same
effective-`false` path. That is the trade-off the mechanism decision above argues for: one
truthful boolean everywhere instead of explicitness state threaded through six call sites.

## ADR 0021 §8 amendment — DRAFTED, FLAGGED FOR SIGN-OFF

ADR 0021 §8 says a config is "rejected at startup when `streaming.enabled = true`". That sentence
was written when `streaming.enabled` defaulted to `true` for every engine, so it now **overstates**
the rule: under the engine-aware default a whisper config reaches validation as `true` only when
the user wrote it. I did **not** rewrite §8 in place — I appended a dated **Amendment 2026-10-10**
section (the ADR 0011 precedent) that narrows §8 to the explicit `true` and records that everything
else (model/library rejections, no sizing check) is unchanged. **The orchestrator must present this
amendment for the user's sign-off before merge**; it changes the ADR's meaning from "the setting is
`true`" to "the setting is explicitly `true`".

## Ticket 47 guidance update

`config_guidance` for `whisper.cpp` no longer prints the `[streaming] enabled = false` block. It
now reads:

```
Add this line to /home/u/.config/ges2t/config.toml to select the engine (ges2t never edits it for you):

    transcriber.engine = "whisper_cpp"

whisper.cpp is batch-only, so streaming is off by default for it and there is no text until the utterance ends. No [streaming] line is needed; setting streaming.enabled = true would be refused at startup.
```

The README's whisper section and setup section were updated to match (no `enabled = false` block,
explicit-`true` refusal). `docs/project/whisper-cpp-backend/engine-naming-and-setup-guidance/`'s
existing artifacts were **not** touched (never rewrite an artifact).

## DoD status

- [x] unset + batch-only starts (batch), announced in one line.
- [x] explicit `true` + batch-only still a startup error naming both settings.
- [x] explicit `false` behaves as before (batch, no refusal).
- [x] `whistle` + unset still streams; nothing in the whistle path changed.
- [x] Mechanism decided and justified (engine-aware default), no hardware probe in `load_config`, no hidden state.
- [x] ADR 0021 §8 amendment drafted and flagged for sign-off.
- [x] Matrix tests falsified (F1–F6).
- [x] Ticket 47 guidance + README updated.
- [x] `pytest` + `mypy` green.

## Final counts

```
$ uv run pytest -q
1206 passed in 10.49s

$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 64 source files
```

(1194 at the branch point → 1206: twelve new tests.) Both green; `git status` clean.
