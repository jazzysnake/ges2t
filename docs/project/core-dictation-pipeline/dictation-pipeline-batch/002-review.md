# 002 — Review: composition root and batch end-to-end wiring

- **Ticket:** `ticket.md` (`dictation-pipeline-batch`) — P0, composition root
- **Branch:** `feat/dictation-pipeline-batch` @ `76008fa` (reviewer re-ran at this commit)
- **Baseline:** `main` (the branch is older; `git diff main...feat/dictation-pipeline-batch`, three dots)
- **Reviewer scope:** falsification only — no application code, test, or config was changed
- **Manual E2E:** intentionally **unticked**, user-owned; not attempted by this agent

## Verdict

**APPROVE — no blocking findings.** Every automated DoD item is independently reproduced:
the network is unreachable from `run_service` (including the real `_startup` path with the
real engine), composition-root discipline holds, the absent-engine-block case is defaults,
the four `BatchDictationSession` semantics hold at the call-count level, the framework
latency is in the reported range, and the mutation harness is honest. The only DoD item I
could not verify is the **manual, human-voice E2E**, which the ticket itself reassigns to the
user; it must be recorded before the epic is closed, but it does not block merge of this code
(tickets 16/17 depend on it and do not change batch behaviour).

## Hard gates

### 1. No network reachable from this path (ADR 0018) — PASS

Grep of `src/ges2t/` for network-capable imports:

```
src/ges2t/model_store.py:37:import urllib.request
src/ges2t/socket_proto.py:42:import socket
```

`socket` in `socket_proto.py` is used only as `socket.AF_UNIX` (`bind`, `connect`, `accept`);
no `AF_INET`/`AF_INET6` anywhere. `urllib.request` is used only by `model_store._download`,
which is reached only from `model_store._ensure`, which is reached only from
`ensure_model`/`ensure_engine`. `run_service`'s call set (`model_path`, `default_data_dir`,
`MODEL_PIN`, `engine_pin`, `engine_path`, `env_overrides`, `_present`) contains none of them.
`pw_record.py` and `ydotool.py` import `subprocess`, not a network client.

Independent probe (`/tmp/review_probe_network.py`), with `model_store.ensure_model`,
`model_store.ensure_engine`, `urllib.request.urlopen`, and every `AF_INET`/`AF_INET6` socket
patched to raise:

```
rc = 0
events = ['load', 'serve']
fake.calls = []
REACHED = []
PASS: run_service never touched ensure_*, urlopen, or an IP socket
```

This is the ticket's required test reproduced; note `fake.calls == []` — not even the
transcriber's own `ensure_model` was reachable.

Strongest form (`/tmp/review_probe_real_startup.py`): the **real** `_startup` — real
`WhistleTranscriber`, real `PwRecordAudioSource`, real `YdotoolInjector`, real artifact
presence check, real `env_overrides()`, and the real `needle` engine load — with the same
guards:

```
REAL _startup succeeded offline
  _startup wall time: 79.7 ms (includes model load)
  service type: Service
  socket path: /run/user/1000/ges2t.sock
  REACHED: []
PASS: the real composition+load path touched no download, no urlopen, no IP socket
```

The only network-capable third-party import is `needle`, pulled lazily inside
`WhistleTranscriber.load()`; it is never asked to acquire (no `ensure_*`), and
`HF_HUB_OFFLINE=1` is applied first (ADR 0017/0018 mitigation), which the real load above
exercised.

### 2. Composition-root discipline (ADR 0010) — PASS

Only `pipeline.py` imports both a concrete backend adapter and the `Service` class.
Occurrence counts of each concrete symbol per module:

```
module                           | PwRecordAudioSource | YdotoolInjector | WhistleTranscriber | Service          | ServiceServer
---------------------------------+---------------------+-----------------+--------------------+------------------+--------------
src/ges2t/audio/pw_record.py     | 4 (defines)         | 0               | 0                  | 0                | 0
src/ges2t/injectors/ydotool.py   | 0                   | 3 (defines)     | 0                  | 0                | 0
src/ges2t/transcribers/whistle.py| 0                   | 0               | 14 (defines)       | 0                | 0
src/ges2t/service.py             | 0                   | 0               | 0                  | 4 (defines)      | 2 (lazy import in serve())
src/ges2t/socket_proto.py        | 0                   | 0               | 0                  | 1 (vocab: CommandResponse/ServiceState, not Service) | 5 (defines ServiceServer)
src/ges2t/cli.py                 | 0                   | 0               | 0                  | 0 (vocab only)   | 0
src/ges2t/pipeline.py            | 4                   | 3               | 3                  | 5 (constructs)   | 0
```

`pipeline.py` is the **only** module that imports an adapter **and** the `Service` class.
Two adjacent facts, reported rather than hidden:

- `service.py` imports `ServiceServer` lazily inside `serve()` and defines `Service`. The
  socket server is the service's own transport, not one of the swappable backend adapters,
  and the lazy import is the documented local-import that breaks the `socket_proto` ↔
  `service` cycle. It is not an ADR 0010 violation.
- `socket_proto.py` and `cli.py` import `CommandResponse`/`ServiceState` (vocabulary), not
  the `Service` class and not any adapter.

`_build_service` is the single `Service(...)` construction site.

### 3. Absent engine block means defaults, not `KeyError` — PASS

Real config on disk with no `[engine.whistle]` (`/tmp/review_no_engine.toml`), loaded through
`load_config`, then `build_transcriber`:

```
engine_blocks = {}
warnings = []
type = WhistleTranscriber
config = WhistleConfig(language='', window_seconds=5.0, chunk_seconds=1.0)
PASS: a real config with no [engine.whistle] builds with all defaults
```

Mutation of the mandated line:

```
M-A  .get(...,{}) -> [engine]   -> rc=1 :: 4 failed, 18 passed in 0.22s
```

`test_build_transcriber_accepts_a_missing_engine_block_as_all_defaults` and its siblings fail
(the `KeyError` the ticket warns about). Reverted; `git status` clean.

### 4. `BatchDictationSession` semantics — PASS (call-count level)

`/tmp/review_probe_session.py` asserts the transcriber call list, not just the return value:

```
[zero]    out=''                                     audio.calls=['stop']  transcriber.calls=[]
[text]    out='  Verbatim Text!  '                   transcriber.calls=['transcribe']  transcribed=1
[partials] []
[cancel]  audio.calls=['stop']  transcriber.calls=[]
PASS: all four BatchDictationSession semantics hold
```

- zero samples → `""` and `transcriber.calls == []` (no `transcribe`, no `ensure_model`,
  nothing) — ADR 0012;
- otherwise the text is returned byte-for-byte (`'  Verbatim Text!  '`), no strip, no
  trailing space added here — ADR 0012;
- `partials()` yields nothing — ADR 0011;
- `cancel()` stops capture and never transcribes.

Confirmed by mutations M-B (zero-sample check made unconditional) and M-C (constructor
assignment swapped), below.

### 5. The latency claim — PASS (independently reproduced)

My own probe (`/tmp/review_latency.py`): a real `Service` + `ServiceServer` on a `/tmp` Unix
socket (real JSON protocol), a real `PwRecordAudioSource` capturing ~0.3 s of discarded local
audio, an instant fake `Transcriber`, a fake `Injector` that timestamps its call. No model
time, no injection. Two independent runs, medians:

```
run 1: buffer handoff 0.0004 ms (n=50) | capture stop/drain 3.350 ms (n=5, 4069 samples)
       socket status RTT 0.165 ms (n=20) | stop-toggle -> inject-called 3.707 ms (n=5)
run 2: buffer handoff 0.0003 ms (n=50) | capture stop/drain 2.828 ms (n=5, 4410 samples)
       socket status RTT 0.119 ms (n=20) | stop-toggle -> inject-called 3.635 ms (n=5)
```

**My framework number: ~3.6–3.7 ms (stop-toggle → inject-called, model excluded)** — inside
and bracketing the implementer's reported ~3.6–3.8 ms. The method is stated in `001-comment.md`
(real socket + real capture + fake model/injector), and it explicitly excludes model time.

**Injection is correctly not measured and the ADR 0020 baseline is recorded, not claimed as a
pass.** `001-comment.md` states ~40 ms/char at the `ydotool` default and **≈4.8 s for a
≤20-word (~120-char) phrase**, and frames it as the **before** number ticket 23 is measured
against. The same ~40 ms/char / 4.99 s figure is in ticket 23's own table
(`batched-key-emission/ticket.md`) and ADR 0003's amendment. It is nowhere presented as
satisfying ADR 0001. (Minor wording nit: `001-comment.md` attributes the figure to "ADR 0020's
research"; the number actually lives in ADR 0003's amendment and `orchestrator-state.md`.
Immaterial.)

### 6. Mutation harness — PASS with one honest gap

I applied **six** of my own one-at-a-time textual mutations, each run with `timeout 90` via
`subprocess.run(..., timeout=120)`, restoring from a pristine copy after every run (final
restore asserted byte-identical, `git status` clean):

```
CAUGHT (rc=1): M-A  .get(...,{}) -> [engine]                        4 failed, 18 passed
CAUGHT (rc=1): M-B  zero-sample check made unconditional            1 failed, 21 passed
CAUGHT (rc=1): M-C  constructor assignment swapped (transcriber/audio) 1 failed, 21 passed
CAUGHT (rc=1): M-D  inverted size comparison in _present            2 failed, 20 passed
CAUGHT (rc=1): M-E  _startup skips load()                           1 failed, 21 passed
NOT CAUGHT (rc=0): M-F  _session_factory(audio_source, transcriber) 22 passed
```

M-F is a byte-for-byte-same-length argument swap in `_build_service`. The **test suite does
not catch it** because no test drives a session through the real `_build_service`/
`run_service` wiring (the `run_service` tests patch `Service.serve`, so no session is ever
created). **mypy does catch it** on a fresh cache:

```
src/ges2t/pipeline.py:237: error: Argument 1 to "_session_factory" has incompatible type "AudioSource"; expected "Transcriber"  [arg-type]
src/ges2t/pipeline.py:237: error: Argument 2 to "_session_factory" has incompatible type "Transcriber"; expected "AudioSource"  [arg-type]
Found 2 errors in 1 file (checked 34 source files)
```

This is a coverage note, not a correctness defect: the type system is the guard the
interface-typed signatures exist to provide (ADR 0019), and the implementer's own claim
("22/22 tests fail under at least one mutation") is about test sensitivity, not about every
possible mutation. I did not re-run the implementer's 23 mutations (their harness was
throwaway and uncommitted); the six above, including a subtle same-length swap, are mine.

**mypy-cache gotcha (for future reviewers):** M-F has identical byte length to the original,
and mypy's incremental cache (keyed on mtime + size) kept reporting the stale error **after**
`git checkout` restored the file. `rm -rf .mypy_cache` gives the true result (clean tree:
`Success: no issues found in 34 source files`). Always clear the cache after same-size probes.

## Judge these

### The documented deviation: `NullStatusUI.error()` prints to stderr — ACCEPTABLE

The ticket note says the adapter's >30 s error "should be surfaced ... as a notification in
epic 2, but for now a clear stderr message is enough". `status_ui.error()` is the only error
channel the service has; a pure no-op would make a failed transcription invisible until
`ges2t status`. Printing `ges2t: <message>` to stderr is exactly the ticket's interim
requirement, it is pinned by `test_null_status_ui_error_writes_to_stderr`, and the
service's `_transition_scope` containment means it cannot wedge the state machine. Making it
uniform would *lose* observability for no benefit. When ticket 17 moves `NullStatusUI` to
`src/ges2t/ui/status.py` it must preserve this behaviour.

### `status_ui=None` (ticket 17) and `_session_factory` (ticket 16) — both real and usable

- **Ticket 17:** `run_service(config, status_ui: StatusUI | None = None)` constructs
  `NullStatusUI()` when `None` and threads the object into `_build_service` → `Service`.
  The parameter is a genuine override point and `_build_service` is the single construction
  site. Ticket 17's DoD additionally wants `run_service` to *choose* `TrayStatusUI` from
  `config.ui.tray` and fall back with a printed warning, and to move `NullStatusUI` out of
  `pipeline.py`. Both are local additions to this shape; the public signature is unchanged.
  **No rework needed.**
- **Ticket 16:** `_session_factory(transcriber, audio_source) -> Callable[[], DictationSession]`
  is the single place a session implementation is chosen, and `Service` never learns which one
  it gets. One caveat to flag for 16: the current factory does **not** receive `config` (or
  `streaming.enabled`/`chunk_seconds`), and `Transcriber` (Protocol) has no `.config`. Ticket 16
  will therefore have to thread `config` into `_session_factory`/`_build_service` (or do the
  selection in a new helper called from `run_service`). That is a private-helper signature
  change, not a public-API change; `run_service(config, status_ui=None)` stays as-is. **Shape
  accommodates 16 with the expected local extension.**

### `build_injector` does not half-implement backend selection — CONFIRMED

`build_injector(config)` is `return YdotoolInjector(config.ui.trailing_space)`. It never reads
`config.injection.backend` or `config.injection_blocks`, so there is no v1/v2 branch to get
half-right; `injection.backend` is preserved untouched in the generic config for ticket 25.

### `model_store.env_overrides()` double-apply — CONFIRMED idempotent

Applied in `_startup` and again in `WhistleTranscriber.load()`:

```
first == second: True
snapshot after 1st == after 2nd: True
  DO_NOT_TRACK=1
  HF_HUB_OFFLINE=1
  NEEDLE3_LIB_PATH=/home/matee/.local/share/ges2t/engine/3.2.0/libneedle.so
  NEEDLE_TELEMETRY=0
  NEEDLE_WHISTLE_WEIGHTS=/home/matee/.local/share/ges2t/models/whistle/2.0.0/whistle.cact
PASS: env_overrides() is idempotent and double-apply is harmless
```

`env_overrides()` is a pure function of the environment, and `os.environ.update` with
identical keys/values is a no-op. Harmless.

## Checks and hygiene

- `uv run pytest -q` → **513 passed in 1.83s**. `git diff main...feat/... --name-status`
  shows only `tests/test_pipeline.py` added under `tests/`; it contains **22** `def test_`
  functions. 513 − 491 = 22, consistent with the stated `main` baseline (not independently
  re-run on `main`).
- `uv run mypy src/ges2t tests` → **Success: no issues found in 34 source files** (after
  clearing the stale cache described above).
- mypy config **bites**: appending an unannotated `def review_unannotated_probe(x): return x`
  to `tests/test_pipeline.py` produced
  `tests/test_pipeline.py:352: error: Function is missing a type annotation [no-untyped-def]`
  (rc=1); fully reverted, cache cleared, final run clean.
- No test deleted, skipped, or weakened: no `skip`/`xfail`/`@pytest.mark` in the new file, and
  no existing test file was touched.
- **ADR 0019:** no definition missing a return annotation (`_session_factory`/`_build_service`
  are multi-line signatures with `->` on the closing line); no `tuple[...]` positional returns
  in `pipeline.py`; no `# type: ignore` in the new files; every collaborator parameter/return
  that has an interface is Protocol-typed (`Transcriber`, `AudioSource`, `Injector`, `StatusUI`,
  `DictationSession`, `Callable[[], DictationSession]`); no implicit `Optional` (clean mypy
  under `no_implicit_optional = true`).
- **Scope:** only `src/ges2t/pipeline.py`, `tests/test_pipeline.py`,
  `docs/project/README.md` (board row `to do` → `in review`), this ticket's `ticket.md`
  (checkboxes), and `001-comment.md`. **No ADR was edited.**
- **No forbidden operations:** the branch diff introduces no `subprocess`/`ydotool`/clipboard/
  `sudo`/service change in code; the only mentions are documentation and the
  `YdotoolInjector` construction. I used no keystrokes, no `ydotool`, no clipboard, no `sudo`,
  and changed no service.

## Manual E2E preparation — CORRECT (user-owned)

`001-comment.md` contains a copy-pasteable script for all five ticket steps (editor, focused
terminal, twice-in-a-row, silence, cancel) with the expected state transitions, followed by the
known in-flight defects recorded with their ticket numbers: **`hu` layout corruption
(`0`→`ö`, `y`→`z`), ticket 22**, and the **>1 s injection tail, ticket 23** — explicitly marked
"not a new blocker", with the working behaviours (text appears, silence types nothing, cancel
types nothing and returns to `idle`, two phrases give two space-separated words) called out as
what must be recorded. The DoD checkbox is intentionally unticked. I did not attempt it.

## Risks and non-blocking observations

1. **M-F coverage gap** (above): session-factory argument binding is enforced by mypy but not
   by a runtime test. Cheap fix later: drive one `toggle` end-to-end through the real
   `_build_service` with fakes and assert the injected text.
2. **`PipelineError` hint is wrong for an unknown engine.** `run_service` catches *all*
   `PipelineError`s (including `build_transcriber`'s "engine not handled") and appends the
   "run `ges2t setup`" hint, which is misleading for a config error. Practically unreachable
   because `load_config` rejects unknown engines first and `KNOWN_ENGINES` == the builder keys,
   so this is defensive only.
3. **Presence logic duplicated.** `pipeline._present` mirrors
   `model_store._present_with_pinned_size` instead of calling it (deliberately, to avoid
   importing a private name). Two definitions of "present" can drift; the ADR 0017 invariant
   (existence + size, no hash on startup) currently holds.
4. **Broad `except Exception` in `run_service`** turns any startup bug into "could not start
   the service" + the setup hint (exit 1) rather than a traceback. Deliberate ("never traceback
   at the top level") and documented, but it can hide programming errors in logs.
5. **mypy cache staleness** (above) is an environment trap for reviewers; not a code defect.

## What I could NOT verify

- **The manual, human-voice E2E** (editor/terminal/back-to-back/silence/cancel on the real
  desktop). Forbidden to an agent and owned by the user; the only unticked DoD item.
- **Real `ydotool` injection latency and the `hu` corruption** — measuring them requires
  injecting keystrokes into a focused window, which is forbidden. I verified only that the
  ~40 ms/char / ≈4.8 s baseline is *recorded* as ticket 23's before-number.
- **The implementer's `/tmp` probe and integration-smoke scripts** were not committed, so I
  could not inspect them; I substituted my own independent network, session, and latency probes.
- **The implementer's full 23-mutation harness** — I ran six mutations of my own instead.
- **A live `ges2t toggle` round trip through the actual `run_service`/`serve()` with a real
  socket client** — I exercised the real `_startup` and a real `Service`+`ServiceServer` with
  fakes, but never the real CLI against the real engine.
