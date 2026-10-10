# 003-review — re-review of the F1/F2 fixes

- **Verdict:** **pass — safe to merge.**
- **Commit under review:** `f1076b6` (`feat/whisper-model-store`), reviewed against `5571bbc`
  (`002-review.md`) and `e6bbc0e` (the implementation tip).
- **Reviewer:** ges2t reviewer (independent). I wrote no application code, test or config. The only
  write is this file. No microphone, clipboard, keystrokes, windows, notifications, `sudo`, installs
  or service changes. **No network was used** (none is needed for this re-review). Every command was
  bounded by `timeout`; every probe file was deleted and its byte-copy restored, and `src/` is
  byte-identical to `e6bbc0e`.

## Result in one line

Both findings are fixed **in substance, not relabelled**: F1's overclaiming name is gone and replaced
by an honestly-worded offline comparison with a docstring saying so; F2's weaker regex/socket-hole
scan is replaced by an AST scan that I made fail on **all four** named gaps. The suite and mypy are
green at the unchanged counts, and the worktree is clean.

## Task 1 — scope: only tests + the new comment changed

```
$ git diff 5571bbc..HEAD --name-only
docs/project/whisper-cpp-backend/whisper-model-store/003-comment.md
tests/test_model_store.py

$ git diff 5571bbc..HEAD --stat -- src tests
 tests/test_model_store.py | 132 ++++++++++++++++++++++++++++++++++++++--------
 1 file changed, 111 insertions(+), 21 deletions(-)

$ git diff e6bbc0e..HEAD -- src/ges2t/model_store.py
(no output)                       # exit 0 — model_store.py unchanged since the implementation tip
```

Note on the task's phrasing: `git diff main...HEAD -- src tests` legitimately shows **both**
`src/ges2t/model_store.py` and `tests/test_model_store.py`, because it is the whole ticket against
`main` (the catalogue/parser/acquisition change). The precise "only tests changed since my review" is
`5571bbc..HEAD`, which shows exactly one file under `src`/`tests` — `tests/test_model_store.py` — and
`model_store.py` is byte-identical to `e6bbc0e`. So the measured pins, catalogue and acquisition code
are untouched, as `003-comment.md` claims.

## Task 2 — F1 fixed honestly

The old names are gone; the new ones are present:

```
$ grep -rn "test_whisper_catalogue_pins_match_the_live_host\|test_http_clients_are_imported_only" tests/
OLD NAMES ABSENT (good)

$ grep -rn "def test_whisper_catalogue_matches_the_recorded_pins\|def test_network_clients_are_confined" tests/
tests/test_model_store.py:555:def test_whisper_catalogue_matches_the_recorded_pins() -> None:
tests/test_model_store.py:869:def test_network_clients_are_confined_to_the_model_store() -> None:
```

`test_whisper_catalogue_matches_the_recorded_pins` now carries a docstring that says plainly it is an
**offline change-detector, not a live-host check**, and never contacts Hugging Face; the
`EXPECTED_WHISPER_PINS` comment says the same and points at the manual live check in `001-comment.md`.
No network was added to the suite: the follow-up test diff introduces only `import ast` /
`dataclasses.dataclass` (it removes `re`), and a grep of the diff for `urllib|requests|httpx|aiohttp|
socket(|urlopen|create_connection|__import__|import_module` matches **only** docstring/constant text,
no import or call. The name no longer promises a live check.

## Task 3 — F2 fixed, not relabelled: I reproduced every named gap and watched it fail

The new scan parses each `src/ges2t/*.py` with `ast` (`ast.Import`/`ast.ImportFrom` + literal
`importlib.import_module`/`__import__` + all referenced `Name`/`Attribute` names) and asserts three
things: HTTP clients only in `model_store.py`, `socket` imported only by the two audited AF_UNIX
modules, and no `AF_INET`/`AF_INET6`/`create_connection` anywhere. The four probes (each a scratch
file, or an append to `socket_proto.py` reverted by byte copy, then `rm -rf .mypy_cache __pycache__`):

```
# (a) multi-import  `import os, httpx`
$ timeout 300 uv run pytest tests/test_model_store.py -q -k network_clients
E         Left contains 1 more item:
E         {'_review_probe_multi.py': frozenset({'httpx'})}
tests/test_model_store.py:895: AssertionError
FAILED tests/test_model_store.py::test_network_clients_are_confined_to_the_model_store
1 failed, 56 deselected in 0.31s        PYTEST_EXIT=1

# (b) dynamic literal import  `importlib.import_module("requests")`
E         {'_review_probe_dynamic.py': frozenset({'requests'})}
tests/test_model_store.py:895: AssertionError
1 failed, 56 deselected in 0.30s        PYTEST_EXIT=1

# (c) raw-socket client in a new module  `import socket` + `socket.create_connection(...)`
>       assert socket_importers == _SOCKET_MODULES_ALLOWED, socket_importers
E       AssertionError: {'_review_probe_raw.py', 'doctor.py', 'socket_proto.py'}
tests/test_model_store.py:896: AssertionError
1 failed, 56 deselected in 0.30s        PYTEST_EXIT=1

# (d) `socket.AF_INET` inside the allow-listed socket_proto.py (append, then reverted)
>       assert network_socket_users == {}, network_socket_users
E       AssertionError: {'socket_proto.py': frozenset({'AF_INET'})}
tests/test_model_store.py:897: AssertionError
1 failed, 56 deselected in 0.30s        PYTEST_EXIT=1
```

After each: probe deleted (or `socket_proto.py` restored — `cmp` reported **RESTORED IDENTICAL**),
`git diff --stat src/` empty, `git status --porcelain` empty.

The `socket` allow-list is exactly the audited modules:

```
$ grep -rn "^\s*\(import\|from\)\s\+\(socket\|urllib\|http\|httpx\|requests\|aiohttp\|ftplib\)" src/ges2t/*.py
src/ges2t/doctor.py:52:import socket
src/ges2t/model_store.py:39:import urllib.request
src/ges2t/socket_proto.py:42:import socket

_SOCKET_MODULES_ALLOWED == {"doctor.py", "socket_proto.py"}
```

Probe (c) confirms **a new socket importer fails**; probe (d) confirms the construct check is not
defeated by the allow-list. At the helper level, I also verified the scanner's outputs directly:
`import os, httpx` → `{httpx}`, `import os, urllib.request` → `{urllib}`, `importlib.import_module
("requests")` → `{requests}`, `__import__("aiohttp")` → `{aiohttp}`, `from http import client` →
`{http}`, `socket.create_connection(...)` → `{create_connection}`, `socket.socket(socket.AF_INET)` →
`{AF_INET}`. This is a real check, not a relabelled regex.

## Task 4 — the residual limit is stated, not hidden

Both the test docstring and `003-comment.md` state the scan is **static matching, not a runtime
guard**, and both name the residual hole: a bare `socket.socket()` (which defaults to `AF_INET`)
followed by `.connect((host, port))` inside one of the two allow-listed AF_UNIX modules is not caught.
That disclosure is true, and the code's own claims are scoped to it. One further residual is only
partially disclosed and is recorded below as R1 (non-blocking).

## Task 5 — regression (unchanged counts)

```
$ timeout 600 uv run pytest -q
951 passed in 5.47s                    PYTEST_EXIT=0

$ rm -rf .mypy_cache __pycache__ && timeout 600 uv run mypy src/ges2t tests
Success: no issues found in 55 source files        MYPY_EXIT=0

$ git status --porcelain
(empty)                                # clean
$ find . \( -name '_review_probe*' -o -name '*.part' \) | grep -v .git
none
$ uv run pytest tests/test_model_store.py -q --collect-only | tail -1
57 tests collected                     # unchanged — the fix renamed/strengthened, added nothing
```

`951 passed / 0 mypy errors / 55 files` and `57` tests in the file are exactly the pre-fix counts, so
no test was added, removed, skipped or weakened.

## Residual risks (none blocking)

- **R1 (documentation, low).** The docstring says "The residual hole is a bare `socket.socket()` …"
  (singular), and `003-comment.md` similarly names only that one. The word "literal" in "literal
  `importlib.import_module`" hints at it, but neither artifact spells out the *other* evasions a static
  scan cannot see: a non-literal dynamic import (`name = "requests"; importlib.import_module(name)`),
  a non-literal `__import__`, or `exec("import requests")`. I confirmed all three evade the scan:

  ```
  variable dynamic  http=[] socket_import=False net_constructs=[]
  exec string       http=[] socket_import=False net_constructs=[]
  ```

  This is inherent to a static name scan and the realistic HTTP clients (`import urllib/http/httpx/
  requests/aiohttp/ftplib`) are all caught, so it does not change the verdict. A one-line broadening
  of the "residual hole" sentence to "residual holes" would make the disclosure complete; strictly
  optional.
- **R2 (pre-existing, unchanged).** The scan reads name matches, so a genuine tripwire is a
  false positive for a local identifier named `create_connection`/`AF_INET` — acceptable and
  intentional.

## Safe to merge?

**Yes.** F1 is fixed honestly (renamed, docstring states offline comparison, no network added); F2 is
fixed in substance (AST scan; all four named gaps reproduced and failed; new socket importers fail;
the socket allow-list is exactly the two audited modules); the residual limit is stated; and the
suite/mypy/working tree are green at the unchanged counts. The only note is the optional
doc-broadening in R1.
