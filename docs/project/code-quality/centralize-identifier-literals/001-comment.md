# 001 — implementer's comment: centralize identifier literals (ticket 48)

Status: **in progress**. Branch `feat/centralize-identifier-literals`, worktree
`.worktrees/centralize-identifier-literals`, based on main `f7e1c90`.

## Baseline measured before any change

```
$ uv run pytest -q
1230 passed in 9.09s
```

(The orchestrator's brief quoted a stale `923 passed`; the real main baseline today is
**1230 passed**. Reported as measured, per rule 9.)

## Shape decision (DoD item 1)

**One leaf module, `src/ges2t/identifiers.py`.**

Justification:

- The defect this ticket exists for is *cross-module drift*: `doctor.py` redefined
  `WHISPER_CPP_ENGINE` and compared bare `"clipboard"`/`"whistle"`, while `pipeline.py`
  compared bare `"whisper_cpp"`/`"whistle"`. A single file makes "where is this identifier
  defined?" have exactly one answer, which is what rule 1 asks for. A per-domain split would
  re-create the question ("is the engine name owned by `config` or by the transcriber?") that
  caused the two spellings in the first place.
- It is a **leaf**: `identifiers.py` imports nothing from `ges2t`, so every domain module can
  depend on it with no import cycle. Collections derived from the values (`KNOWN_ENGINES`,
  `ENGINE_ALIASES`, `BATCH_ONLY_ENGINES`, the model catalogues) stay in the module that owns
  the collection; only the *values* live here.
- `config.py` re-exports the names that were already its public API
  (`WHISTLE_ENGINE`, `WHISPER_CPP_ENGINE`, `INJECTION_DEFAULT_BACKEND`, `KNOWN_HOTKEY_MODES`),
  so existing callers and tests keep importing from the same place. A re-export is an alias,
  not a second definition.

## Identifier inventory (DoD item 4)

Enumerated from the tree with `grep`, not from the ticket's example list. "Definition site"
is the single source of truth after this change; every site listed was a bare literal before.

| Identifier | Value | Definition site (after) | Sites changed |
|---|---|---|---|
| engine `whistle` | `"whistle"` | `identifiers.WHISTLE_ENGINE` | `config.WHISTLE_ENGINE` (alias), `doctor.diagnose_engine_config`, `pipeline._TRANSCRIBER_BUILDERS`, `pipeline._build_whistle` check, `transcribers/whistle.py:name`, `whistle_config.BLOCK_NAME` |
| engine `whisper_cpp` | `"whisper_cpp"` | `identifiers.WHISPER_CPP_ENGINE` | `config.WHISPER_CPP_ENGINE` (alias), `doctor.WHISPER_CPP_ENGINE` (**deleted duplicate**), `pipeline._TRANSCRIBER_BUILDERS`, `pipeline` engine-block reads, `pipeline._validate_whisper_config`, `transcribers/whisper_cpp.py:name`, `whisper_cpp_config.BLOCK_NAME` |
| whisper.cpp CLI token | `"whisper.cpp"` | `identifiers.WHISPER_CPP_CLI_TOKEN` | `config.ENGINE_ALIASES` key, `setup_command.SetupBackend.WHISPER_CPP` |
| injection backend `ydotool` | `"ydotool"` | `identifiers.YDOTOOL_BACKEND` | `config.INJECTION_DEFAULT_BACKEND` (alias), `doctor` compare + `[injection.ydotool]` read + keyboard-layout `required`, `pipeline._INJECTOR_BUILDERS`, `pipeline._build_ydotool_injector` block read |
| injection backend `clipboard` | `"clipboard"` | `identifiers.CLIPBOARD_BACKEND` | `doctor.check_clipboard_backend`, `doctor.diagnose_injection_config`, `pipeline._INJECTOR_BUILDERS`, `pipeline._build_clipboard_injector` block read, `pipeline` streaming-vs-clipboard rejection |
| setup backend `needle` | `"needle"` | `identifiers.NEEDLE_SETUP_BACKEND` | `setup_command.SetupBackend.NEEDLE` |
| needle model `whistle` | `"whistle"` | `identifiers.WHISTLE_MODEL` | `model_store.NEEDLE_MODELS` |
| whisper model `medium-q5_0` | `"medium-q5_0"` | `identifiers.WHISPER_MODEL_MEDIUM_Q5_0` | `whisper_engine.DEFAULT_WHISPER_MODEL_GPU` |
| whisper model `small-q8_0` | `"small-q8_0"` | `identifiers.WHISPER_MODEL_SMALL_Q8_0` | `whisper_engine.DEFAULT_WHISPER_MODEL_CPU` |
| hotkey mode `ptt` | `"ptt"` | `identifiers.HOTKEY_MODE_PTT` | `config.KNOWN_HOTKEY_MODES` |
| hotkey mode `toggle` | `"toggle"` | `identifiers.HOTKEY_MODE_TOGGLE` | `config.KNOWN_HOTKEY_MODES` |
| config key `backend` | `"backend"` | `identifiers.CONFIG_KEY_BACKEND` | `config._INJECTION_KEYS`, `config._read_injection_blocks` (`if name == "backend"`) |
| socket selector `wake` | `"wake"` | `socket_proto._WAKE_SELECTOR_DATA` | `socket_proto.serve_forever` (register + compare) |

## Tests keep literal strings (DoD item 2) — deliberate

`tests/` is **not touched by this sweep**. A test that asserts `INJECTION_DEFAULT_BACKEND ==
"ydotool"` proves the constant is right; a test rewritten to `INJECTION_DEFAULT_BACKEND ==
INJECTION_DEFAULT_BACKEND` proves nothing (rule 8: "a test that asserts its own constant
proves nothing"). The AST scan therefore covers `src/ges2t/` only.

## Scope boundary recorded

- Not in the ticket's identifier sets, so deliberately left (see the scan section for the
  full list): argparse subcommand names in `cli.py` (user-facing CLI surface, each backed by
  `service.Command`), third-party protocol tokens (D-Bus signatures `"a(sss)"`/`"u"`,
  `"libggml-vulkan"` prefix, `"Content-Length"`), stdlib values (`sys.byteorder == "big"`),
  and character-level parsing literals (`""`, `"0"`/`"1"`/`"9"`, quotes/backslashes).

(Scan results, the commands run, and the leftovers are appended below as the work proceeds.)

## AST scan (DoD item 3)

Script: `/tmp/scan_identifiers.py` (not committed — the ticket calls this a verification step, not a
committed gate). It walks the AST of `src/ges2t/**/*.py` and reports every string literal in a
**comparison position**: either side of `==`/`!=`/`in`/`not in`, a `match` pattern, the key argument
of `.get(<literal>, ...)`, or an `if`/`while` test that is not (only) a `Compare`. Duplicate
line+literal hits are deduped.

**Static limitation (stated honestly).** The scan sees literals written in those syntactic positions.
It **cannot** see: a literal used as a dict-literal key (e.g. `{"whistle": builder}`), a key chosen
dynamically, a value assembled from parts, or a literal inside a helper/container built elsewhere. It
is a coverage report, not a proof. Two real cases it therefore misses and this ticket nonetheless
fixed by hand are `pipeline._TRANSCRIBER_BUILDERS`/`_INJECTOR_BUILDERS` (dict-literal keys) and
`setup_command.SetupBackend` (enum member values) — both are verified by the grep in the inventory
section, not by the scan.

### Before (main `f7e1c90`)

```
$ uv run python /tmp/scan_identifiers.py src/ges2t | tail -1
# distinct literal sites in comparison positions: 56
```

### After (this branch)

```
$ uv run python /tmp/scan_identifiers.py src/ges2t | tail -1
# distinct literal sites in comparison positions: 41
```

### Removed — every identifier literal the ticket names, gone from comparisons

```
config.py      [compare] 'backend'
doctor.py      [compare] 'clipboard'   (x2), 'whistle', 'ydotool' (x2)
doctor.py      [get-key] 'ydotool'
pipeline.py    [compare] 'clipboard', 'whisper_cpp', 'whistle'
pipeline.py    [get-key] 'clipboard', 'whisper_cpp' (x2), 'ydotool'
socket_proto.py [compare] 'wake'
```

(`whisper.cpp`, `needle`, `medium-q5_0`, `small-q8_0`, `ptt`, `toggle` were already single definitions
at enum/constant sites and so never appeared as bare comparison literals — they are now *sourced* from
`identifiers.py` too, verified by the inventory grep.)

### Remaining 41 sites — all intentionally left, with the exemption for each

The scan reports the *literal text*; a remaining literal is not a failure (the ticket says so
explicitly). Categorised:

| Leftover | Sites | Why it is not this ticket's scope |
|---|---|---|
| Empty-string / control-char validity checks (`''`, `'\x00'`, `'\n'`, `'\r'`) | `doctor.py:275,1079,1081`, `injectors/clipboard.py:521`, `injectors/layout.py:351,431,662`, `injectors/ydotool.py:236,424` | Emptiness/whitespace validation, not an identifier. Naming `""` is noise, not de-duplication. |
| XDG / environment variable names | `config.py:253` `XDG_CONFIG_HOME`, `doctor.py:411` `YDOTOOL_SOCKET`, `doctor.py:414`/`socket_proto.py:147` `XDG_RUNTIME_DIR`, `model_store.py:494` `XDG_DATA_HOME`, `whisper_engine.py:578` `XDG_CACHE_HOME` | Keys owned by the XDG spec / ydotool, not by ges2t. Epic out-of-scope: "identifiers owned by other projects". |
| Third-party protocol / reply keys | `doctor.py:479-490` `id`/`info`/`props`/`object.serial` (KWin D-Bus), `injectors/layout.py:293,319` `a(sss)`/`u` (D-Bus signatures), `model_store.py:812` `Content-Length` (HTTP), `transcribers/whistle.py:317-341` `language`/`ttft_ms`/`decode_tps` (cactus-needle JSON) | Upstream vocabulary ges2t does not own. |
| ges2t's own manifest / wire-protocol field names | `whisper_engine.py:943` `vulkan` (engine.json), `socket_proto.py:164-189` `cmd`/`ok`/`state`/`error`/`message` (one-module NDJSON schema) | Not in the ticket's identifier sets; each schema is built and read in one module and covered by round-trip tests, so there is no cross-module drift to remove here. Listed as a candidate follow-up. |
| Character-level parsing | `injectors/layout.py:254,271,273` `"`/`\\`, `injectors/clipboard.py:573,575` `1`/`9`/`0` (evdev digit row) | Individual characters in a parser, not identifiers. |
| Binary / path / native-artifact names | `whisper_engine.py:1154` `git`, `whisper_engine.py:1239` `libggml-vulkan` (native lib prefix), `injectors/ydotool.py:88` `YDOTOOL_BINARY = "ydotool"` | These are executable / file names, a different identifier family (binary names), and `libggml-vulkan` is a third-party artifact prefix. Not in the ticket's sets. |
| stdlib value | `audio/pw_record.py:194` `sys.byteorder == "big"` | A value of the standard library, not ours. |

Also intentionally left, outside the scan's reach: argparse subcommand names in `cli.py`
(`"toggle"`, `"ptt-start"`, … as `add_parser` names, `_THIN_CLIENT_COMMANDS`/`_HANDLERS` keys,
`"backend"` as an argument name) and the `"toggle"` spelling in `service.Command` / `_BINDABLE_COMMANDS`.
Those are the user-facing CLI surface, each already backed by `service.Command`; they are the
**command-verb** family, which the ticket does not list, and the bounded-first-pass rule says to leave
them rather than widen this sweep.

## No behaviour change (DoD item 6)

Every replacement is the same string value. Confirmed by importing the wired modules and printing the
live values, plus a byte-level grep of all `src/` string literals (no value edited):

```
config.WHISTLE_ENGINE whistle
config.INJECTION_DEFAULT_BACKEND ydotool
ENGINE_ALIASES {'whisper.cpp': 'whisper_cpp'}
KNOWN_HOTKEY_MODES ('ptt', 'toggle')
setup tokens ['needle', 'whisper.cpp']
whistle BLOCK_NAME engine.whistle
whisper BLOCK_NAME engine.whisper_cpp
needle default whistle
whisper defaults medium-q5_0 small-q8_0
```

`git diff` shows only identifier *sources* changed: no message text, no threshold, no control flow.

## Verification commands (real output)

```
$ uv run pytest -q
1230 passed in 9.85s

$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 65 source files
```

Baseline on main was the same `1230 passed` (measured, see top); no test was added, deleted, skipped,
weakened, or rewritten. `tests/` was not touched.
