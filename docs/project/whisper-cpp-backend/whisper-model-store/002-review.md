# 002-review — whisper.cpp model catalogue and pinned acquisition (ticket 41)

- **Verdict:** **pass**
- **Branch / tip:** `feat/whisper-model-store` @ `e6bbc0e3d9c2d58632ef4e140f4e69f09355f9c6`
  (`git diff main...feat/whisper-model-store`).
- **Reviewer:** ges2t reviewer (independent). I wrote no application code, tests or config. The only
  write is this file. No microphone, clipboard, keystrokes, windows, notifications, `sudo`, installs,
  or service changes. Network was used **only** for the read-only Hugging Face pin check (one tree-API
  `curl` and one bounded 43.5 MB model download into a throwaway `XDG_DATA_HOME`). Every command was
  bounded by `timeout`; every probe left the worktree clean.

## Result in one line

Every definition-of-done item is met; the central claim — that every catalogue pin matches the live
Hugging Face host and that HF's tree-API `lfs.oid` is the file's sha256 — is **independently
verified**, not read. Three falsifications reproduce against the shipped tests. Two non-blocking
findings and one cross-ticket risk are recorded below; none affects the correctness of the deliverable.

## Evidence — the named checks (run by me, on this branch)

```
$ cd .worktrees/whisper-model-store
$ timeout 600 uv run pytest -q
951 passed in 5.19s
EXIT=0

$ rm -rf .mypy_cache __pycache__ && timeout 600 uv run mypy src/ges2t tests
Success: no issues found in 55 source files
EXIT=0
```

Baseline / delta (this is the sort of claim I trace, not trust):

```
$ (main worktree, branch 'main') timeout 300 uv run pytest -q --collect-only | tail -1
923 tests collected

$ (branch) timeout 300 uv run pytest tests/test_model_store.py -q --collect-only | tail -1
57 tests collected          # was 29 on main -> +28, matching 001-comment
```

No skips/xfails appear in the run summary (`951 passed`, no `skipped`/`xfailed`), and
`git diff main...HEAD --name-status` changes only `tests/test_model_store.py` under `tests/`, so no
existing test was deleted, skipped or weakened.

## Definition of done — per-criterion findings

### DoD 1 — catalogue (name, file name, byte size, sha256, URL), only upstream-published files
**Met.** `WHISPER_CPP_MODELS` / `WHISPER_CPP_CATALOGUE` are frozen dataclasses + a
`MappingProxyType`, seven entries. The live HF tree API was re-queried and every entry compared:

```
$ timeout 90 curl -sS "https://huggingface.co/api/models/ggerganov/whisper.cpp/tree/main?recursive=false&expand=true" -o /tmp/hf_tree_review.json   # exit 0
$ uv run python - <<'EOF'  # compares catalogue to the live listing
catalogue name         file                             size match sha256 match live-present
tiny-q8_0              ggml-tiny-q8_0.bin               True       True         True
base-q8_0              ggml-base-q8_0.bin               True       True         True
small-q8_0             ggml-small-q8_0.bin              True       True         True
medium-q5_0            ggml-medium-q5_0.bin             True       True         True
medium-q8_0            ggml-medium-q8_0.bin             True       True         True
large-v3-turbo-q5_0    ggml-large-v3-turbo-q5_0.bin     True       True         True
large-v3-turbo-q8_0    ggml-large-v3-turbo-q8_0.bin     True       True         True
ALL 7 MATCH LIVE: True
ggml-tiny-q5_0.bin present upstream: False
ggml-base-q5_0.bin present upstream: False
ggml-small-q5_0.bin present upstream: False
```

The three pins the research published (`small-q8_0`, `medium-q5_0`, `large-v3-turbo-q5_0`) all match
the live host byte-for-byte; `tiny`/`base`/`small` `q5_0` are **absent upstream on purpose** and are
absent from the catalogue. The live listing also confirms the ticket's publishing facts: `q8_0` for
every size; `q5_0` only for `medium`/`large-v3-turbo` within the shipped range (upstream additionally
has `large-v2-q5_0/q8_0`, `large-v3-q5_0`, and `ggml-large-v1.bin` with no quantised variant — all
deliberately outside scope).

### DoD 2 — name parser; `medium-q5_0` → (medium, q5_0); unknown → typed error naming the valid set
**Met.** Probed directly (real output):

```
$ uv run python -c "..."
large-v3-turbo-q5_0 -> WhisperModelName(size='large-v3-turbo', quant='q5_0')   # LAST hyphen
medium-q5_0 -> WhisperModelName(size='medium', quant='q5_0')
'large-v1-q8_0'  -> WhisperModelNameError | valid in msg: True | valid tuple len 7
'tiny-q5_0'      -> WhisperModelNameError | valid in msg: True | valid tuple len 7
'medium-f16'     -> WhisperModelNameError | valid in msg: True | valid tuple len 7
'medium-q9_9'    -> WhisperModelNameError | valid in msg: True | valid tuple len 7
'' / 'medium' / '-q5_0' / 'medium-' -> WhisperModelNameError | valid tuple len 7
is WhisperModelNameError subclass of ModelStoreError: True
```

The error text names the offending reason and the valid set, e.g.:
`unknown whisper.cpp model 'tiny-q5_0': 'q5_0' is not published upstream for size 'tiny'; valid models: tiny-q8_0, base-q8_0, small-q8_0, medium-q5_0, medium-q8_0, large-v3-turbo-q5_0, large-v3-turbo-q8_0`

### DoD 3 — download + sha256 **and** size verify into `…/whisper-cpp/<name>/ggml-<name>.bin`; idempotent; typed errors
**Met.** Driven through the **real** acquisition path (download → sha256+size → atomic install) into a
throwaway `XDG_DATA_HOME` — no model committed, temp dir removed:

```
$ time XDG_DATA_HOME=/tmp/ges2t-review-pincheck uv run python -c "from ges2t import model_store as m; p=m.ensure_whisper_model('tiny-q8_0'); print('size',p.stat().st_size,'verify',m.verify(p, m.whisper_model('tiny-q8_0').pin()))"
path: /tmp/ges2t-review-pincheck/ges2t/models/whisper-cpp/tiny-q8_0/ggml-tiny-q8_0.bin
size: 43537433
verify: True
real  0m1.657s

$ sha256sum /tmp/ges2t-review-pincheck/ges2t/models/whisper-cpp/tiny-q8_0/ggml-tiny-q8_0.bin
c2085835d3f50733e2ff6e4b41ae8a2b8d8110461e18821b09a15c40c42d1cca  …/ggml-tiny-q8_0.bin
```

The sha256 above equals both the catalogue pin **and** the tree API's `lfs.oid` for that path —
independently confirming `lfs.oid == sha256` (so the other six, checked only against `lfs.oid`, are
credible).

Idempotence and the failure paths, again against the **real** catalogue pin (fetchers injected, so no
network):

```
idempotent: returned …/ggml-tiny-q8_0.bin exists True        # second call did not invoke the fetcher
truncated -> ArtifactVerificationError
  msg: ggml-tiny-q8_0.bin: pinned sha256 c2085835… (43537433 bytes), but downloaded sha256 627b829e…
  dest exists: False     .part files: []
network failure -> ConnectionError simulated drop
  dest exists: False     .part files: []
```

`whisper_model_path()` is validated first, so a bad name raises `WhisperModelNameError` rather than
yielding a plausible path; `ensure_whisper_model` validates the name before any fetch.

### DoD 4 — no runtime network outside the acquisition path (ADR 0018), checked as the existing store is
**Met**, with a documented heuristic limitation (finding F2). `model_store` remains the only `src/`
module importing an HTTP client. The new source-scan test is a **real** check — I made it fail:

```
# scratch file src/ges2t/_review_probe.py containing a nested `import requests`, then:
$ timeout 300 uv run pytest tests/test_model_store.py -q -k imported_only_by_the_model_store
E  AssertionError: {'_review_probe.py': {'requests'}, 'model_store.py': {'urllib'}}
1 failed
# scratch file deleted; git status --porcelain empty
```

It catches nested/indented imports too (`^\s*` + `re.MULTILINE`), and scans the editable-install
source tree (`model_store.__file__` resolves to `.worktrees/whisper-model-store/src/ges2t`).
`test_doctor_socket_probes_are_unix_only` (pre-existing) still guards the doctor's AF_UNIX sockets.

### DoD 5 — tests cover catalogue shape, parser good/rejected, successful verify, failing verify; each falsified
**Met.** 27 whisper-named tests run green. I re-ran the implementer's three falsifications myself
(src mutated, test watched to fail, reverted, green again):

```
# 4a — hash half of the guard removed: `if actual_size != pin.size:`
FAILED tests/test_model_store.py::test_ensure_whisper_model_flipped_byte_is_a_typed_error
E  Failed: DID NOT RAISE ArtifactVerificationError
1 failed, 56 deselected in 0.14s

# 4b — parse short-circuited to accept a valid size/quant without catalogue lookup
FAILED …::test_parse_whisper_model_name_rejects_with_a_typed_error_naming_the_valid_set[tiny-q5_0]
E  Failed: DID NOT RAISE WhisperModelNameError
1 failed, 10 passed, 46 deselected in 0.14s

# 4c — one hex digit of medium-q5_0's sha256 corrupted
FAILED tests/test_model_store.py::test_whisper_catalogue_pins_match_the_live_host
1 failed, 56 deselected in 0.12s
```

After each probe: `git checkout -- src/ges2t/model_store.py`, `rm -rf .mypy_cache __pycache__`, byte
comparison to the pre-probe copy (`IDENTICAL`), and re-run green. `git status` is clean.

### DoD 6 — pytest and mypy green; counts reported; nothing deleted/skipped/weakened
**Met.** See the named-checks block. `+28` tests, `923 → 951`, `0` mypy errors, `55` source files,
no skip/xfail, only `tests/test_model_store.py` touched under `tests/`.

### DoD 7 — `001-comment.md` records the catalogue, pins with provenance, upstream publishing facts
**Met.** It records the live tree-API listing, the byte-for-byte match of the three research pins, the
deliberate absence of `q5_0` for the small sizes (locally quantised by the research, not published),
the scope decision (`large-v1/v2/v3` excluded), the real-download cross-check, and the shipped
catalogue table. The substantive claims in it check out; only a test name overclaims (finding F1).

## Scope / hygiene

```
$ git diff main...HEAD --name-status
M  docs/project/README.md
A  docs/project/whisper-cpp-backend/whisper-model-store/001-comment.md
M  docs/project/whisper-cpp-backend/whisper-model-store/ticket.md
M  src/ges2t/model_store.py
M  tests/test_model_store.py
```

The only `src/` change is `model_store.py` — no adapter, no engine build, no config wiring, no
quantiser. Style follows ADR 0019: `WhisperModelName`/`WhisperModel` are frozen dataclasses (classes
over tuples), `parse_whisper_model_name`/`whisper_model`/`ensure_whisper_model` are fully annotated and
interface-typed (`Fetcher`, `ProgressCallback`), and `WHISPER_CPP_CATALOGUE` is an immutable
`Mapping`. `git status --porcelain` empty; no `*.bin`/`*.cact`/`*.part` in the tree; the downloaded
model lived in `/tmp` and was deleted.

## Findings and risks (all non-blocking)

- **F1 (nit, naming) — the test name overclaims a live check.** `test_whisper_catalogue_pins_match_the_live_host`
  compares the catalogue against `EXPECTED_WHISPER_PINS`, a literal dict in the same test file; it
  never contacts Hugging Face. It *is* a useful cross-file change-detector (it caught the corrupted
  pin in 4c), and the constant's own comment says it is "restated as data", but the function name
  reads as a live-host verification that was not performed. Recommend renaming to something like
  `test_whisper_catalogue_matches_the_recorded_pins`. The live verification is real — it is the
  manual step in `001-comment.md` §1 and my independent re-check above — so no number is wrong.
- **F2 (low) — the source scan excludes `socket` wholesale.** `_HTTP_CLIENT_MODULES` omits `socket`
  because the doctor and `socket_proto` use AF_UNIX. That rationale is stated in the docstring, but
  the exclusion is by module, not by address family, so a raw-socket network client slips through — I
  demonstrated it:

  ```
  # scratch src/ges2t/_review_probe_socket.py: `import socket` + socket.create_connection(...)
  $ uv run pytest … -k imported_only_by_the_model_store
  1 passed          # NOT caught
  ```

  The regex also misses `import os, httpx` (captures only `os`) and `importlib.import_module("httpx")`.
  These are heuristic limits of a static scan, not false verification (the ADR's own audit list names
  `socket`, so this is slightly narrower than ADR 0018). Suggest a follow-up that flags
  `AF_INET`/`AF_INET6` socket construction, or a runtime `socket` guard in the suite. The realistic
  HTTP clients (`urllib`, `http`, `httpx`, `requests`, `aiohttp`, `ftplib`) are all covered.
- **R1 (cross-ticket, important) — `small-q5_0` is the epic's CPU default but is not acquirable.**
  epic.md, ADR 0021 §4 and this ticket's Notes all name `small-q5_0` as the CPU-only default, but the
  catalogue correctly ships only upstream-published files and therefore has `small-q8_0` only:

  ```
  small-q5_0 -> REJECTED: unknown whisper.cpp model 'small-q5_0': 'q5_0' is not published upstream for size 'small'
  small-q8_0 -> in catalogue
  medium-q5_0 -> in catalogue
  ```

  Ticket 44 / the config default rule must resolve this (`small-q8_0` is the research's own
  "pragmatic choice", §11.4). This ticket is right to defer it, but the conflict should not reach the
  config ticket unflagged.
- **R2 (note) — idempotence is a size re-check, not a hash re-check.** A second run returns an
  existing file of the pinned byte size without hashing (ADR 0017's existing shape). The DoD word
  "re-verifies" is satisfied only at size granularity; a same-size corrupt file is not re-downloaded
  (that is what `verify` / `ges2t models --verify` are for). Consistent with `ensure_model`.
- **R3 (note) — the network-failure test's exception type differs from the real one.** The test
  injects a fetcher raising `ConnectionError`; the real `_download` wraps `urllib.request.urlopen`,
  which raises `urllib.error.URLError` (an `OSError`). The code neither catches nor wraps it, so both
  propagate unchanged and cleanup is identical — but no test asserts the real downloader's failure
  type.

## Could-not-verify (each reason is itself true)

- **The other six models' sha256 via a full download.** I downloaded only the smallest
  (`tiny-q8_0`, 43.5 MB) to honour the one-bounded-download rule; the other six are quoted at
  hundreds of MB. Their pins are checked against the live tree API's `lfs.oid`, and I proved on
  `tiny-q8_0` that `lfs.oid` equals the downloaded file's sha256, so the check is sound but not a
  per-file full download.
- **A real network *failure* on the acquisition path.** The sandbox has working connectivity and I
  did not manufacture a live drop; the failure branch is exercised through the injected `Fetcher`
  seam (the designed contract), not `urllib` itself.
- **Future upstream drift.** The pins were correct against the host on 2026-10-10; nothing here can
  verify they will remain so.

## What would falsify the central claim

- The HF tree API reporting a `size` or `lfs.oid` different from any catalogue pin (it did not).
- A downloaded catalogue file whose `sha256sum` differs from its pin (tiny-q8_0 matched).
- An entry in the catalogue with no corresponding upstream `ggml-*.bin` (none).
- A `q5_0` file upstream for `tiny`/`base`/`small` (none published).
- `parse_whisper_model_name("large-v3-turbo-q5_0")` splitting on the first hyphen (it does not).
- A `src/` module other than `model_store.py` importing an HTTP client (none does).

## Safe to relay to the user?

**Yes.** The catalogue is correct against the live host, the parser and verification behave as
specified, the suite is green and its checks are real (I broke each and watched it fail), and no
shipped output contains a fabricated number. Relay with the R1 caveat: the epic's `small-q5_0` CPU
default is not in the catalogue and must become `small-q8_0` (or the default rule must change) in the
config/setup ticket.
