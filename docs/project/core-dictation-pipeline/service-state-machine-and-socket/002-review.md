# 002 — review: service state machine, single instance, socket protocol

**Ticket:** `service-state-machine-and-socket` · **Branch:** `feat/service-state-machine-and-socket` · **HEAD:** `40934cb`
**Reviewer:** adversarial review against the ticket's transition table, its four ADRs, and the
implementer's `001-comment.md` claims. All probes were run from throwaway copies/scripts
outside the repo and have been removed.

## Verdict: **pass** — no blocking findings.

The transition table is implemented literally; the one judgement call (`TRANSCRIBING + toggle`)
satisfies the table's intent; the loop does not block on `finish()`; failures and empty text
behave as specified; the socket contract holds against real processes; scope, tests, board row,
and typing are as claimed. One real latent risk was found and is reported as a follow-up, not a
blocker.

---

## 1. Transition table — full and adversarial

I re-implemented the harness independently (own session double, own scenario builder) and walked
**every cell in every state** (20 cells), then the ADR 0004 cases. Output:

```
=== full transition table (independent) ===
[PASS] idle + toggle :: got ok=True state=recording err=None
[PASS] idle + ptt-start :: got ok=True state=recording err=None
[PASS] idle + ptt-stop :: got ok=False state=idle err='not-recording'
[PASS] idle + cancel :: got ok=False state=idle err='not-recording'
[PASS] idle + status :: got ok=True state=idle err=None
[PASS] recording + toggle :: got ok=True state=transcribing err=None
[PASS] recording + ptt-start :: got ok=False state=recording err='already-recording'
[PASS] recording + ptt-stop :: got ok=True state=transcribing err=None
[PASS] recording + cancel :: got ok=True state=idle err=None
[PASS] recording + status :: got ok=True state=recording err=None
[PASS] transcribing + toggle :: got ok=True state=transcribing err=None
[PASS] transcribing + ptt-start :: got ok=False state=transcribing err='busy'
[PASS] transcribing + ptt-stop :: got ok=False state=transcribing err='busy'
[PASS] transcribing + cancel :: got ok=False state=transcribing err='busy'
[PASS] transcribing + status :: got ok=True state=transcribing err=None
[PASS] error + toggle :: got ok=True state=idle err=None
[PASS] error + ptt-start :: got ok=True state=idle err=None
[PASS] error + ptt-stop :: got ok=False state=error err='not-recording'
[PASS] error + cancel :: got ok=True state=idle err=None
[PASS] error + status :: got ok=True state=error err=None

=== ADR 0004 rapid / out-of-order ===
[PASS] three toggles: states :: recording,transcribing,transcribing  (starts == 2)
[PASS] ptt-stop idle rejected
[PASS] ptt-start while recording rejected (one start)
[PASS] ptt-start while transcribing busy
[PASS] cancel during recording -> IDLE, no inject
[PASS] cancel while idle rejected
[PASS] error then recovery then record :: error,idle,recording
[PASS] empty finish: no inject, idle, no error
FAILURES: none
```

The table is the specification and it is matched exactly, including `status` never changing
state and `ERROR + ptt-stop` staying in `ERROR` with `not-recording`. I could not construct a
sequence that gets stuck, double-starts a recording, loses a command, or leaves `state`
inconsistent with the observable session lifecycle.

## 2. Judgement call: `TRANSCRIBING + toggle` → queued

**Verdict: the implementation satisfies the table's intent. Not a finding.**

The ticket cell is `→ RECORDING (queued)`. The implementer replies `ok=true`,
`state="transcribing"`, `message="recording queued for after transcription"`, sets a single
`_pending_start` flag, and starts exactly one new recording when `finish()` returns. The two
things the cell requires — *the command is not lost* and *exactly one recording follows* — hold:

```
[PASS] TRANSCRIBING+toggle reply :: ok=True state=transcribing message='recording queued for after transcription'
[PASS] one queued toggle -> exactly one new start :: starts=1 created=2
[PASS] two queued toggles -> exactly one new start :: starts=1 created=2
[PASS] ten queued toggles -> exactly one new start :: starts=1 created=2
[PASS] queued toggle survives empty finish :: starts=1
```

Two toggles during one `TRANSCRIBING` window yield **one** queued recording, not two and not
zero; ten yield one. Reporting `transcribing` rather than `RECORDING` is the honest current
state (no capture has begun) and avoids a `status` immediately after contradicting the toggle
reply. I judge this a faithful reading of "(queued)", not a contradiction of the table.

## 3. Never block the socket loop on `finish()`

Code path (read, not just tested): `toggle`/`ptt-stop` from `RECORDING` capture the session and
flip to `TRANSCRIBING` under `threading.Lock`, build a daemon worker named `ges2t-finish`, and
return; `_run_finish` calls `session.finish()` with **no lock held**. The only lock acquisitions
after that are short state transitions in `_complete_transcription`/`_fail`. `session.cancel()`
is deferred past the `with self._lock` block in `_cancel_locked`. The lock is not held across
transcription or across the session's stop path.

Experiment with a `finish()` blocked on an event the test controls:

```
=== never block the loop: blocked finish ===
[PASS] status answers while finish blocked :: ok=True state=transcribing
[PASS] ptt-start returns busy while finish blocked :: ok=False error='busy'
[PASS] toggle answers while finish blocked :: ok=True message='recording queued for after transcription'
[PASS] finish still in flight
```

All three returned within the 1 s watchdog while `finish()` remained blocked (a lock held across
`finish()` would have made `status` hang). No hang in the shipped implementation.

## 4. Failures do not kill the service

```
[PASS] start raises: -> ERROR + status_ui.error :: err=['could not start recording: no device']
[PASS] start raises: next command accepted :: -> IDLE
[PASS] finish raises: -> ERROR + status_ui.error :: err=['transcription failed: engine died']
[PASS] finish raises: next command accepted :: -> IDLE
[PASS] inject raises: -> ERROR + status_ui.error :: err=['injection failed: ydotoold down']
[PASS] inject raises: next command accepted :: -> IDLE
```

All three self-report failures move to `ERROR`, call `status_ui.error(...)` with an actionable
sentence, and accept the next command (per the table, first to `IDLE`, then a following command
acts).

## 5. Empty text is never injected (ADR 0012)

```
[PASS] empty finish: no inject, idle, no error :: inj=[] err=[]
```
Independently: `""`, `"   "`, `"\t\n"`, `"\u00a0"` all skip the injector, end in `IDLE`, and emit
no error. Non-empty text is passed verbatim (`text.strip()` only gates the call; the argument is
unchanged), leaving the trailing space to the injector — consistent with ADR 0012.

## 6. Socket and protocol, real processes

Reproduced the implementer's manual check and extended it. Real `Service.serve()` process, real
second process, real `kill`:

```
### 1. server with a real Service (RECORDING at shutdown)
server pid=3244177 socket exists=yes
stat: 600 matee:matee /tmp/ges2t-review/live/ges2t.sock
### 2. second server must refuse with non-zero exit
REFUSED: a ges2t service is already listening on .../ges2t.sock; only one may run per session
second exit=2
### 3. client round trip
RESPONSE: CommandResponse(ok=True, state=<ServiceState.RECORDING: 'recording'>, ...)
client exit=0
### 4. SIGTERM: clean shutdown, cancel session, remove socket
server exit=0
socket after SIGTERM: removed
process alive: no
session log: start cancel
### 5. stale socket is rebound
stale exists=yes
RESPONSE: ... message='handled toggle'
server exit=0
stale socket after shutdown: removed
### 7. SIGINT shutdown
server exit=0
socket after SIGINT: removed
### 8. leftovers
no socket_proc processes
```

- mode is exactly `0600` (`stat -c '%a'` → `600`);
- second server refuses with a clear message and **exit 2**, leaving the live socket alone;
- a stale socket (file exists, nothing listening) is unlinked and rebound;
- `SIGTERM` and `SIGINT` both exit `0`, **cancel the in-progress session** (`session.log` =
  `start cancel`, no `finish`), remove the socket file, and leave no stray process;
- client fail-fast, measured:
  ```
  CLIENT_ERROR: ... did not answer within 0.5s; it may be stuck      (silent peer)
  silent-peer client exit: 5
  absent-socket client exit: 5 elapsed=0.119s
  ```
  A silent peer is bounded by the configured timeout; a missing socket fails in ~0.1 s. It never
  hangs.

## 7. Forbidden imports and the session protocol

```
fresh-interpreter sys.modules check: FORBIDDEN_PRESENT:[]
service: suspicious import lines -> []
socket_proto: suspicious import lines -> []
DictationSession methods: ['cancel', 'finish', 'partials', 'start']
protocol check OK
```

A fresh interpreter importing both modules leaves `gi`, `pystray`, `needle`, `cactus_needle`,
and `subprocess` absent from `sys.modules`. The only textual match is the word `subprocess` in a
docstring. The `DictationSession` protocol is exactly `start`/`partials`/`finish`/`cancel`.

## 8. ADR 0019 / mypy

```
$ uv run mypy src/ges2t/service.py src/ges2t/socket_proto.py tests/test_state_machine.py tests/test_socket_proto.py
Success: no issues found in 4 source files

$ uv run mypy src/ges2t tests
tests/test_whistle_config.py:247: error: Argument 1 to "_error_message" has incompatible type "object"; expected "Mapping[str, Any]"  [arg-type]
tests/test_model_store.py:349: error: "bool" is invalid as return type for "__exit__" that always returns False  [exit-return]
Found 2 errors in 2 files (checked 24 source files)
```

The wide check fails on **only** those two pre-existing errors, both in files this ticket did not
touch. The branch is 11 commits behind `main` and predates `ed7fb44` (ticket 20 merged on
`main`), which fixes exactly these two files; `git diff HEAD main` confirms
`tests/test_model_store.py` and `tests/test_whistle_config.py` are the only test files where
`main` has fixes. **Nothing in this ticket's four files fails the wide check.** Merging `main`
(or rebasing) makes the ADR 0019 whole-tree command green; no change is needed in this ticket.

The implementer's `001-comment.md` claim ("pre-existing, ticket-20 scope") is accurate for its
base.

## 9. Scope

`git diff --name-only <merge-base> HEAD` is exactly:

```
docs/project/README.md
docs/project/core-dictation-pipeline/service-state-machine-and-socket/001-comment.md
docs/project/core-dictation-pipeline/service-state-machine-and-socket/ticket.md
src/ges2t/service.py
src/ges2t/socket_proto.py
tests/test_socket_proto.py
tests/test_state_machine.py
```

No other `src/` file changed; no other ticket's README row was touched (only row 9, `to do` →
`in review`). No conflict markers in any reviewed file.

## 10. Tests: honest, headless, fast

`uv run pytest` on the branch: `280 passed in 0.94s`; the two ticket files alone: `73 passed`.
Socket tests use `tmp_path` and timeouts ≤ 2 s (mostly 0.2–1 s); the server runs on a daemon
thread joined with a 2 s bound. My real-process runs left `no socket_proc processes` behind.

Mutation testing on throwaway copies (branch untouched) confirmed the tests would fail if the
behaviour were removed:

- dropping the queued restart → `test_duplicate_toggle_never_stacks_recordings` and
  `test_toggle_during_transcribing_queues_exactly_one_restart` **fail** (2 failed, 71 passed);
- removing the `text.strip()` guard → `test_empty_finish_text_is_never_injected` **fails** for
  all four empty variants (4 failed, 69 passed);
- making `finish()` run synchronously on the command path→ the suite **hung** (the watchdog
  tests never returned); I killed it rather than let it block, which is itself evidence the
  "never block" tests bite, but I did **not** capture a clean failure line for it.

## 11. Board row, ticket status, links

`docs/project/README.md` row 9 is `in review` with `feat/service-state-machine-and-socket`; the
ticket header is `Status: in review`. All relative links in the ticket resolve (epic,
dependencies, blocks, the three ADRs). No conflict markers.

---

## Downstream contracts (final signatures)

These are the signatures later tickets compile against. `001-comment.md` matches the code; the
points below are the ones a consumer must know.

### `ges2t.service`

```python
class ServiceState(StrEnum):  IDLE="idle"; RECORDING="recording"; TRANSCRIBING="transcribing"; ERROR="error"
class Command(StrEnum):       TOGGLE="toggle"; PTT_START="ptt-start"; PTT_STOP="ptt-stop"; CANCEL="cancel"; STATUS="status"

@dataclass(frozen=True)
class CommandResponse:
    ok: bool; state: ServiceState; error: str | None = None; message: str | None = None

@runtime_checkable
class DictationSession(Protocol):
    def start(self) -> None: ...
    def partials(self) -> Iterator[str]: ...
    def finish(self) -> str: ...
    def cancel(self) -> None: ...

class Service:
    def __init__(self, session_factory: Callable[[], DictationSession],
                 injector: Injector, status_ui: StatusUI, socket_path: Path) -> None: ...
    @property
    def state(self) -> ServiceState: ...
    @property
    def socket_path(self) -> Path: ...
    def handle(self, command: str) -> CommandResponse: ...
    def shutdown(self) -> None: ...
    def serve(self) -> None: ...
```

- **Ticket 10 (`cli`)**: `send_command` raises `ServiceNotRunningError` → exit 2; map
  `CommandResponse.state is ServiceState.ERROR` → exit 3. Benign rejections
  (`not-recording`, `already-recording`, `busy`) are `ok=False` but not exit 3.
- **Ticket 11 (`pipeline`)**: `Service(...).serve()` performs its own `bind()`; catch
  `ServiceAlreadyRunningError` and print its message / exit non-zero.
- **Ticket 12 (`doctor`)**: `is_service_listening(default_socket_path())`; `False` with the file
  present means stale.
- **Ticket 15 (`streaming-session`)**: `partials()` is consumed on a dedicated daemon thread and
  must not block `finish()`; it should end after `finish()`/`cancel()`. `finish()` runs on the
  service's worker thread and is the authoritative transcript. A raising `partials()` iterator is
  swallowed by design.

### `ges2t.socket_proto`

```python
SOCKET_MODE = 0o600
DEFAULT_SOCKET_NAME = "ges2t.sock"
DEFAULT_CLIENT_TIMEOUT_SECONDS = 2.0

@dataclass(frozen=True)
class CommandRequest:  cmd: str

@runtime_checkable
class CommandHandler(Protocol):
    @property
    def state(self) -> ServiceState: ...
    def handle(self, command: str) -> CommandResponse: ...

def default_socket_path() -> Path: ...
def encode_request(request: CommandRequest) -> bytes: ...
def decode_request(line: str) -> CommandRequest: ...      # raises ProtocolError
def encode_response(response: CommandResponse) -> bytes: ...
def decode_response(line: str) -> CommandResponse: ...     # raises ProtocolError
def is_service_listening(path: Path, *, timeout: float = 0.2) -> bool: ...
def send_command(path: Path, command: str, *, timeout: float = 2.0) -> CommandResponse: ...
@contextlib.contextmanager
def signal_shutdown(server: ServiceServer, *,
                    signals: Sequence[int] = (SIGINT, SIGTERM)) -> Iterator[None]: ...

class ServiceServer:
    def __init__(self, path: Path, handler: CommandHandler) -> None: ...
    @property
    def path(self) -> Path: ...
    def bind(self) -> None: ...            # raises ServiceAlreadyRunningError; rebinds stale files
    def serve_forever(self) -> None: ...
    def stop(self) -> None: ...
    def close(self) -> None: ...           # stops, closes, unlinks the socket file

class ProtocolError(ValueError): ...
class ServiceAlreadyRunningError(RuntimeError): ...
class ServiceNotRunningError(RuntimeError): ...
```

Consumers should know:

- `send_command`'s `timeout` is a **keyword-only** argument; it bounds both connect and read.
- The server answers **one client at a time** with a 2 s per-connection read timeout, so a hung
  client cannot stall the service beyond 2 s.
- `signal_shutdown` installs handlers only on the main thread; from another thread it is a no-op
  (`serve()` is therefore expected to run on the main thread).
- `Service.serve()` imports `socket_proto` **locally** to break the module cycle; a top-level
  `import ges2t.socket_proto` pulls in `ges2t.service`, the reverse of the stated dependency
  direction. Importing `service` alone does not import `socket_proto`.
- Error codes currently emitted: `not-recording`, `already-recording`, `busy`, `unknown-command`,
  `bad-request`, `start-failed`, `transcription-failed`, `injection-failed`, `internal-error`.

---

## Risks / follow-ups (non-blocking)

1. **`status_ui` is called while the state lock is held.** `_set_state` invokes
   `status_ui.idle()/recording()/transcribing()/error()` inside `with self._lock`. A `StatusUI`
   that reads live state back from the service — which is exactly what a tray (ADR 0013) that
   shows the current state might do — deadlocks, because `Service._lock` is a plain
   `threading.Lock`. Reproduced on a throwaway copy:
   ```
   DEADLOCK: handle('toggle') did not return within 1.5s
   ```
   (probe: a `StatusUI.recording()` that calls `service.state`). A slow/blocking `status_ui`
   call would likewise stall the socket loop. Recommend having `_set_state` collect the display
   action and invoking `status_ui` after the lock is released. This is outside the ticket's
   stated lock rule (which is about `finish()`/`audio.stop()`), so it is a follow-up, not a
   blocker — but ticket 11/13 should not assume a `StatusUI` may query the service.
2. **Whole-tree mypy is red on this branch only because the branch predates ticket 20.** Merge
   `main` (`ed7fb44`) before/while merging this branch so `uv run mypy src/ges2t tests` exits 0,
   as ADR 0019 now requires.
3. **Leftover manual-check files in `/tmp`** from the implementer's check are still present
   (`/tmp/ges2t_manual_run.sh`, `/tmp/ges2t_manual_service_check.py`, `/tmp/ges2t_measure*.py`).
   Harmless, but the ticket's own "clean up after yourself" standard would remove them.
4. **`ERROR` recovery is two-step** (`ERROR + toggle` → `IDLE`, a *second* `toggle` records):
   correct per the table, but a hotkey press after an error clears the error rather than
   immediately recording. Worth documenting for the CLI/tray so it is not mistaken for a lost
   keypress.

## Explicitly not verified

- Mutation results for the **socket timeout/chmod/stale-rebind** tests: the mutation harness
  hung on the `finish()`-on-command-path mutation and was killed before those mutations ran, so I
  have **no** mutation evidence for tests 4–6. The behaviours themselves were verified directly
  against real processes (section 6).
- The ADR 0019 "the config bites" probe: I did **not** run an unannotated-function probe to prove
  `disallow_untyped_defs`/`no_implicit_optional` fail the build. `mypy` is clean on the four files
  and the two remaining errors are exactly the pre-existing ones, but the config-bites check was
  not executed.
- Static reading covered `service.py`/`socket_proto.py` in full; I did not exhaustively audit
  every test file for hidden sleeps or non-daemon threads beyond the reported suite timing and
  process/thread cleanup check.
- No stress/fuzz test beyond the enumerated sequences (1/2/10 queued toggles, blocked `finish`);
  I did not run a randomized scheduler fuzz.

All probe scripts, mutant copies, temp sockets, and spawned processes from this review have been
removed; `git status` on the branch is clean.
