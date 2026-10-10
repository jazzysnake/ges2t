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
