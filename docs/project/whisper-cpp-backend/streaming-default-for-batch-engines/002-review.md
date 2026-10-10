# 002 — review: streaming default follows the engine (ticket 50)

- **Ticket:** `docs/project/whisper-cpp-backend/streaming-default-for-batch-engines/ticket.md`
- **Branch:** `feat/streaming-default-for-batch-engines` (tip `6833989`)
- **Base:** `main` = `ce8b4d7` (merge of ticket 47, `05603a2`)
- **Reviewer:** independent verifier (did not write this code; no implementation edits)
- **Verdict:** **pass** — one process gate remains: the ADR 0021 amendment needs the user's
  sign-off before merge (ticket DoD; the orchestrator presents it).

## Summary

The mechanism is the engine-aware default the ticket asked for: `load_config` resolves an
**omitted** `streaming.enabled` to `engine_value not in BATCH_ONLY_ENGINES`, and an explicit value
always wins. Every consumer reads the one effective `config.streaming.enabled`, so no
`bool | None` / explicitness state was introduced. The minimal `[transcriber] engine = "whisper_cpp"`
config now starts as a **batch** session and prints one informational line; only an **explicit**
`true` is refused. `whistle` is unchanged. I reproduced the four-row matrix through the real
`load_config`/validation, broke the mechanism six ways and watched the guarded tests fail, and
confirmed the ADR change is a genuine append whose original §8 text is byte-identical to `main`.

## Commands, with real output

### Suite and types (clean tree, `6833989`)

```
$ uv run pytest -q
........................................................................ [100%]
1206 passed in 10.43s          # final re-run on the cleaned tree: 1206 passed in 7.97s

$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 64 source files
```

Baseline confirmed on `main` (`ce8b4d7`):

```
$ uv run pytest -q
1194 passed in 7.79s
```

1206 − 1194 = 12 new tests (4 in `test_config.py` including a 2-case parametrise, 8 in
`test_whisper_backend_config.py`), matching the diff.

### The startup matrix — real `load_config` + real `_validate_whisper_config` + real `_select_session_kind`

(`_whisper_missing_artifacts` stubbed to `[]` so the streaming rule is under test, not the
separately-tested artifact rule; the engine name is normalised first, so the alias row is real.)

```
BATCH_ONLY_ENGINES = ['whisper_cpp']
whisper_cpp streaming=unset            enabled=False validate=STARTS session=batch
whisper_cpp streaming=enabled = true   enabled=True  validate=ERROR  session=batch
whisper_cpp streaming=enabled = false  enabled=False validate=STARTS session=batch
whistle     streaming=unset            enabled=True  validate=STARTS session=streaming
whistle     streaming=enabled = true   enabled=True  validate=STARTS session=streaming
whistle     streaming=enabled = false  enabled=False validate=STARTS session=batch
whisper.cpp streaming=unset            enabled=False validate=STARTS session=batch   # alias
```

Explicit-`true` error, verbatim (names **both** settings; the good message is unchanged):

```
transcriber.engine = "whisper_cpp" cannot be combined with streaming.enabled = true: whisper.cpp is batch-only, so there would be no text until the utterance ends.
  hint: set streaming.enabled = false to run whisper.cpp batch-only, or set transcriber.engine = "whistle" (the default) to keep live and incremental typing
```

The one-line notice — exactly one `\n`, stderr:

```
REPR: "ges2t: transcriber 'whisper_cpp' is batch-only, so streaming is off and there is no text until the utterance ends\n"
line count (\n): 1
whistle repr: ''
```

### Falsification — each guard broken, watched fail, then reverted (`git status` clean after each)

| # | probe (mutation, then `rm -rf .mypy_cache __pycache__`) | result |
|---|---|---|
| 1 | `default=engine_value not in BATCH_ONLY_ENGINES` → `default=True` | `2 failed, 1 passed`: `test_omitted_streaming_enabled_follows_the_engine` (`assert True is False`), `test_omitted_streaming_with_a_batch_only_engine_starts` |
| 2 | `_validate_whisper_config`: `if config.streaming.enabled:` → `if False and …` | `2 failed`: `test_explicit_true_streaming_with_a_batch_only_engine_is_still_refused` and `test_streaming_with_whisper_is_refused_naming_both_settings` (fall through to the artifact error) |
| 3 | default → `default=False` for **all** engines | `2 failed`: `test_omitted_streaming_enabled_follows_the_engine`, `test_whistle_with_omitted_streaming_still_streams_from_a_file` (`assert False is True`) |
| 4 | `BATCH_ONLY_ENGINES = {WHISTLE_ENGINE}` (drift) | `1 failed`: `test_batch_only_engines_agree_with_the_transcriber_capability` (`frozenset({'whistle'}) == {'whisper_cpp'}`) |
| 5 | add `"fake_engine"` to `KNOWN_ENGINES` (drift on a new engine) | `1 failed`: same guard (`Extra items in the right set: 'fake_engine'`) |
| 6 | delete the `_announce_batch_only_engine(config, transcriber)` call from `_startup` | `1 failed`: `test_startup_announces_a_batch_only_engine` (`assert 'batch-only' in ''`) |

Every guarded test was observed to fail; none is a no-op. All probes reverted; `git status
--porcelain` empty after each.

### Consumers read the effective value (no stale state)

```
doctor._whisper_gate whisper_cpp/unset          -> PROCEEDS (None)
doctor._whisper_gate whisper_cpp/explicit true  -> FAIL: this config will not start: transcriber.engine = "whisper_cpp" cannot ...
doctor._whisper_gate whisper_cpp/explicit false -> PROCEEDS (None)
doctor._whisper_gate whistle                    -> OK      # skipped

minimal whisper + clipboard streaming.enabled = False
_validate_injection_backend minimal whisper+clipboard       -> OK (no raise)          # streaming is off, no conflict
_validate_injection_backend explicit-true+clipboard         -> RAISED (correct)

_streaming_is_selected batch-only, enabled=False -> False
_streaming_is_selected batch-only, enabled=True  -> False   # capability gate still holds
_streaming_is_selected streaming engine, enabled=True -> True
```

`doctor` **proceeds** for `engine="whisper_cpp"` with streaming unset (it then does its own
engine/model checks, which is its job), and rejects only an **explicit** `true` — exactly the
narrowing the ADR records. No `bool | None`, `explicitly_present`, `was_explicit` or
`Optional[bool]` exists anywhere in `src/ges2t/`.

### Ticket 47 guidance (real `config_guidance`)

```
Add this line to /home/u/.config/ges2t/config.toml to select the engine (ges2t never edits it for you):

    transcriber.engine = "whisper_cpp"

whisper.cpp is batch-only, so streaming is off by default for it and there is no text until the utterance ends. No [streaming] line is needed; setting streaming.enabled = true would be refused at startup.
```

The `[streaming] enabled = false` block is gone (`assert "enabled = false" not in guidance`).
`README.md`'s whisper section (only the engine line needed; explicit `true` refused) and the
setup section were updated to match. Ticket 47's own folder
`docs/project/whisper-cpp-backend/engine-naming-and-setup-guidance/` is **untouched**
(`git diff … --name-only` empty).

### ADR 0021 amendment — genuine append, accurate

```
$ git diff main...HEAD -- docs/adr/0021-whisper-cpp-second-backend.md | grep -E '^-[^-]'
(no '-' lines — pure additions)

$ diff <(git show main:docs/adr/0021-whisper-cpp-second-backend.md) <(head -n 121 docs/adr/...md)
PREFIX IDENTICAL — genuine append, original untouched
main ADR lines: 121; amendment starts at line 122
```

§8's original sentence (`a config is rejected at startup when streaming.enabled = true`) is
byte-identical; the new **Amendment 2026-10-10** narrows it to "**explicitly** `true`" and records
that model/library rejection and the no-sizing-check rule are unchanged — all of which I confirmed
against the shipped code above. Nothing in §8 was silently redefined.

### Hygiene / scope

```
git diff --name-only main...HEAD:
  README.md, docs/adr/0021-…, docs/project/README.md,
  docs/project/.../001-comment.md, docs/project/.../ticket.md,
  src/ges2t/config.py, src/ges2t/pipeline.py, src/ges2t/setup_command.py,
  tests/{test_config,test_setup_command,test_whisper_backend_config}.py

git diff … -- src/ges2t/whisper_engine.py | wc -l   -> 0     # ticket 49's file untouched
binary/model committed                              -> none  # no .bin/.so/.gguf in the diff
```

`load_config` gained no hardware probe: the only `device` reference in `config.py` is the
pre-existing `audio.device` key; there is no Vulkan / library / subprocess inspection. The new
`BATCH_ONLY_ENGINES` is a constant engine-name declaration; the device-aware model default stays at
the composition root. New code is fully annotated (`frozenset[str]`, typed signature), mypy green.

## DoD, item by item

| DoD item | Verdict | Evidence |
|---|---|---|
| unset + batch-only **starts**, streaming off, one-line notice | **pass** | matrix row 1; notice one line |
| explicit `true` + batch-only **still a startup error** naming both settings | **pass** | matrix row 2 + verbatim error; F2 |
| explicit `false` behaves as now | **pass** | matrix row 3 |
| `whistle` unchanged; unset still streams | **pass** | matrix row 4; F3 |
| mechanism decided & justified; no hardware probe; no hidden state | **pass** | `001-comment.md`; `config.py`; grep for explicitness state empty |
| ADR 0021 §8 updated (presented for sign-off if meaning changes) | **pass (gate: user sign-off)** | pure append, prefix byte-identical, accurate |
| tests **falsify** the matrix (break each, watch fail, revert) | **pass** | F1–F6 table |
| ticket 47 `setup` guidance + README updated | **pass** | real `config_guidance`; README diff; ticket-47 folder untouched |
| `pytest` + `mypy` green; counts reported | **pass** | 1206 passed; 0 / 64 |

## Risks / follow-ups (all non-blocking)

1. **ADR merge gate.** The ADR's *meaning* changes, so per the ticket DoD the amendment must be
   presented to the user for sign-off before merge. The orchestrator owns that; the artifact is
   correct and safe to present.
2. **The notice also prints for an explicit `enabled = false`** on a batch-only engine (probed:
   both unset and explicit-false print). It is truthful and harmless — the user did choose a
   batch-only engine — but it is redundant when the user already wrote `false`. Optional follow-up
   if the redundant line ever becomes noise.
3. **The drift guard hardcodes the two engine names** in its `declared` dict. That is why the
   "new engine" probe fails loudly rather than silently passing — a good property — but adding an
   engine means editing this test as well as `KNOWN_ENGINES`. Acceptable; could be derived from a
   registry in a future ticket.

## Could-not-verify (each reason itself true)

- **The real service / real dictation.** I did not start `ges2t` (it would take over the machine:
  socket, tray, model load — outside the machine-safety rules). The matrix is exercised through the
  real `load_config`, `_validate_whisper_config`, `_select_session_kind`, `_validate_injection_backend`
  and `_announce_batch_only_engine`, with only the separately-tested `_whisper_missing_artifacts`
  artifact probe stubbed. The end-to-end "speak → text" path is out of scope for this ticket and was
  not run.
- **The full `setup` orchestration** (build + download) — network and toolchain work, not run; only
  the shipped `config_guidance` string was inspected.
- **`doctor`'s post-gate engine/model checks** for whisper — I checked the *gate* (streaming)
  behaviour only; the artifact checks are ticket 46's and unchanged here.

## What would falsify the central claim

The central claim is: *an omitted `streaming.enabled` resolves to `false` for a batch-only engine, so
the minimal whisper config starts as a batch session, while an explicit `true` is still refused; and
every consumer reads that one effective value.* It would be false if — for unset + `whisper_cpp` —
`config.streaming.enabled` were `True`, or `_validate_whisper_config` raised, or the session were
`streaming`, or the notice failed to print (or printed more than one line); or if an explicit `true`
did **not** raise; or if `doctor` proceeded on an explicit `true`; or if some consumer still read a
raw/unresolved flag. I probed each of these and the claim held.

## Safe to relay to the user?

**Yes**, with the one caveat the ticket itself imposes: the ADR 0021 amendment changes §8's meaning
and needs the user's sign-off before merge. No unverified measured number is quoted here (the only
numbers are suite counts, reproduced above).

## ADR-amendment verdict (one line for the orchestrator)

**Genuine**: §8's original text is byte-identical to `main` and the dated **Amendment 2026-10-10**
is appended, accurately describing the shipped behaviour (reject only an **explicit**
`streaming.enabled = true`; an omitted value resolves to `false` for a batch-only engine) as a
refinement — not a reversal — of §8; present it to the user for sign-off before merge.
