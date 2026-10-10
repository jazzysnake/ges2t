# 003-comment — follow-up fixes for the two review findings (F1, F2)

Status: **in review** again, after implementing the reviewer's two findings from
[`002-review.md`](002-review.md). Branch `feat/whisper-model-store`. No measured pin, the
catalogue, or any acquisition behaviour changed — the edits are **tests only**
(`tests/test_model_store.py`).

## F1 — the test name overclaimed a live-host check

**What was wrong.** `test_whisper_catalogue_pins_match_the_live_host` compares the catalogue against
`EXPECTED_WHISPER_PINS`, a literal dict in the same file. It never contacts Hugging Face, so its name
read as a verification it did not perform (the reviewer's signature failure class).

**Fix — renamed and re-documented, no network added:**

- `test_whisper_catalogue_pins_match_the_live_host` → **`test_whisper_catalogue_matches_the_recorded_pins`**,
  with a docstring stating plainly: *"This is an offline change-detector, **not** a live-host check:
  it only compares two constants and never contacts Hugging Face. The live re-verification is the
  manual step recorded in this ticket's 001-comment.md."*
- The `EXPECTED_WHISPER_PINS` comment now says the pins are *recorded as data* that were verified
  against the live host (provenance: the tree-API listing in `001-comment.md`), and explicitly that
  the constant does not contact the network.

The old name still appears in `001-comment.md` §4c and `002-review.md`; those artifacts are
**append-only** and are deliberately left as the historical record of what was run. The rename is
recorded here instead of rewritten there.

## F2 — the source scan was weaker than its claim

**What was wrong.** The scan excluded `socket` wholesale (so a raw-socket client passed), and the
regex missed `import os, httpx` (only the first name) and `importlib.import_module("httpx")`.

**Fix — the scan is now AST-based and catches each named gap** (still static, still no network):

- `import ast`, not a regex. `_imported_top_level_names(tree)` walks `ast.Import` / `ast.ImportFrom`
  and so sees **every** name in a multi-import.
- `_dynamic_import_target(node)` resolves `importlib.import_module("x")` and `__import__("x")` when
  the argument is a string literal.
- `_scan_module` also collects every referenced `Name` / `Attribute` name, and the test asserts:
  1. HTTP clients (`urllib`, `http`, `httpx`, `requests`, `aiohttp`, `ftplib`) are imported by
     **exactly** `model_store.py` (as `urllib`);
  2. `socket` is imported by **exactly** `{"doctor.py", "socket_proto.py"}` (the two audited AF_UNIX
     IPC modules) — a **new** module that imports `socket` now fails until a human adds it;
  3. the network-socket constructs `AF_INET`, `AF_INET6` and `create_connection` appear in **no**
     source module.
- Renamed `test_http_clients_are_imported_only_by_the_model_store` →
  **`test_network_clients_are_confined_to_the_model_store`**, and its docstring states the residual
  limit honestly: this is a static matching scan, not a runtime guard; a bare `socket.socket()`
  (defaults to `AF_INET`) plus `.connect((host, port))` hidden inside one of the two allow-listed
  AF_UNIX modules is **not** caught. The realistic HTTP clients are.

`ModuleScan` is a frozen dataclass (classes over tuples, ADR 0019); every helper is fully annotated.

### Evidence — each named gap now fails the test (probes created, watched, deleted)

The reviewer's four gaps, reproduced against the new scan (all in one `src/ges2t` scratch file each,
removed afterwards; `socket_proto.py` probe reverted by byte copy):

```
# (a) multi-import: `import os, httpx`
FAILED tests/test_model_store.py::test_network_clients_are_confined_to_the_model_store
tests/test_model_store.py:895: AssertionError

# (b) dynamic import: `import importlib; importlib.import_module("requests")`
E  AssertionError: {'_probe_dynamic.py': frozenset({'requests'}), 'model_store.py': frozenset({'urllib'})}
FAILED …::test_network_clients_are_confined_to_the_model_store
1 failed, 56 deselected in 0.31s

# (c) raw-socket client: `import socket; socket.create_connection(("example.invalid", 80))`
E  AssertionError: {'_probe_raw.py', 'doctor.py', 'socket_proto.py'}
FAILED …::test_network_clients_are_confined_to_the_model_store
1 failed, 56 deselected in 0.30s

# (d) AF_INET inside an allow-listed module (appended to socket_proto.py, then reverted)
E  AssertionError: {'socket_proto.py': frozenset({'AF_INET'})}
tests/test_model_store.py:897: AssertionError
1 failed, 56 deselected in 0.31s
```

After every probe: probe file deleted (or `socket_proto.py` restored by byte copy),
`git diff --stat src/ges2t` empty, `FALSIFY` marker count 0, suite green again.

## Verification (after the fixes, final committed state)

```
$ uv run pytest -q
951 passed in 4.84s

$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 55 source files
```

Counts unchanged from `002-review` (**951 passed / 0 mypy errors / 55 source files**): the two edits
rename and strengthen existing tests, they neither add nor remove a collected test. `git status`
clean; no `src/` change in this follow-up.
