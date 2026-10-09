# 001 — service state machine, single instance, socket protocol

**Ticket:** `service-state-machine-and-socket` · **Branch:** `feat/service-state-machine-and-socket` · **Status:** in review

## Files

- `src/ges2t/service.py` (new)
- `src/ges2t/socket_proto.py` (new)
- `tests/test_state_machine.py` (new, 44 tests)
- `tests/test_socket_proto.py` (new, 29 tests)

No other module was touched.

## Final signatures

### `ges2t.service`

```python
class ServiceState(StrEnum):        # IDLE, RECORDING, TRANSCRIBING, ERROR
class Command(StrEnum):             # toggle, ptt-start, ptt-stop, cancel, status

@dataclass(frozen=True)
class CommandResponse:
    ok: bool
    state: ServiceState
    error: str | None = None
    message: str | None = None

@runtime_checkable
class DictationSession(Protocol):
    def start(self) -> None: ...
    def partials(self) -> Iterator[str]: ...
    def finish(self) -> str: ...
    def cancel(self) -> None: ...

class Service:
    def __init__(
        self,
        session_factory: Callable[[], DictationSession],
        injector: Injector,
        status_ui: StatusUI,
        socket_path: Path,
    ) -> None: ...
    @property
    def state(self) -> ServiceState: ...
    @property
    def socket_path(self) -> Path: ...
    def handle(self, command: str) -> CommandResponse: ...
    def shutdown(self) -> None: ...
    def serve(self) -> None: ...
```

### `ges2t.socket_proto`

```python
@dataclass(frozen=True)
class CommandRequest:
    cmd: str

@runtime_checkable
class CommandHandler(Protocol):
    @property
    def state(self) -> ServiceState: ...
    def handle(self, command: str) -> CommandResponse: ...

def default_socket_path() -> Path: ...
def encode_request(request: CommandRequest) -> bytes: ...
def decode_request(line: str) -> CommandRequest: ...
def encode_response(response: CommandResponse) -> bytes: ...
def decode_response(line: str) -> CommandResponse: ...
def is_service_listening(path: Path, *, timeout: float = 0.2) -> bool: ...
def send_command(path: Path, command: str, *, timeout: float = 2.0) -> CommandResponse: ...
@contextlib.contextmanager
def signal_shutdown(server: ServiceServer, *, signals: Sequence[int] = (SIGINT, SIGTERM)) -> Iterator[None]: ...

class ServiceServer:
    def __init__(self, path: Path, handler: CommandHandler) -> None: ...
    @property
    def path(self) -> Path: ...
    def bind(self) -> None: ...
    def serve_forever(self) -> None: ...
    def stop(self) -> None: ...
    def close(self) -> None: ...

class ProtocolError(ValueError): ...
class ServiceAlreadyRunningError(RuntimeError): ...
class ServiceNotRunningError(RuntimeError): ...
```

`SOCKET_MODE = 0o600`, `DEFAULT_SOCKET_NAME = "ges2t.sock"`, `DEFAULT_CLIENT_TIMEOUT_SECONDS = 2.0`.

## Wire protocol, exactly as implemented

Newline-delimited JSON, UTF-8, one message per connection (a client sends one line and reads one line).

```text
request : {"cmd": "toggle"}
response: {"ok": true, "state": "recording", "error": null, "message": null}
```

- `state` is one of `idle`, `recording`, `transcribing`, `error` (the `ServiceState` values).
- `error` is a stable machine code or `null`; `message` is a human sentence or `null`.
- Codes emitted today: `not-recording`, `already-recording`, `busy`, `unknown-command`,
  `bad-request` (malformed JSON / no `cmd`), `start-failed`, `transcription-failed`,
  `injection-failed`, `internal-error`.
- Socket leaf: `$XDG_RUNTIME_DIR/ges2t.sock`, mode `0600` (`chmod` after `bind`, before
  `listen`). If `XDG_RUNTIME_DIR` is unset, a per-uid fallback under `tempfile.gettempdir()`
  is used instead of failing.

## How the loop stays non-blocking

- `toggle`/`ptt-stop` from `RECORDING` capture the session under `threading.Lock`, flip to
  `TRANSCRIBING`, install a daemon worker `ges2t-finish`, and return. `session.finish()`
  runs **only** on that worker.
- The lock is held for state transitions only. Every blocking side effect
  (`worker.start()`, `session.cancel()`) is deferred past the `with self._lock` block.
- The response's `state` is captured **while the lock is held**, so an instant `finish()`
  cannot race the reply (a `toggle`→`transcribing` answer never reads back as `idle`).
- `test_commands_are_answered_while_transcription_is_in_flight` holds a `finish()` blocked
  on an event and issues `status` on a watchdog thread; a lock held across transcription
  would make that test time out instead of pass.
- `session.partials()` is forwarded to `status_ui.partial()` on a separate daemon worker
  `ges2t-partials`; a stop event ends it and a raising iterator is swallowed, because
  partials are display-only (ADR 0011) and are never injected.

## Single-instance behaviour (verified by hand)

`ServiceServer.bind()`:

1. If the path exists and `is_service_listening(path)` (a 0.2 s connect succeeds), raise
   `ServiceAlreadyRunningError` — the file is left alone.
2. If the path exists but nothing accepts connections, `unlink()` it and bind a fresh socket
   (stale socket from a crash).
3. `chmod 0600`, `listen(8)`, non-blocking.

Run with two real processes (`/tmp/ges2t_manual_run.sh`, a throwaway script outside the repo):

```
$ stat -c '%a %U:%G %n' /tmp/ges2t-manual-check/ges2t.sock
600 matee:matee /tmp/ges2t-manual-check/ges2t.sock

$ .venv/bin/python /tmp/ges2t_manual_service_check.py second /tmp/ges2t-manual-check/ges2t.sock
REFUSED: a ges2t service is already listening on /tmp/ges2t-manual-check/ges2t.sock; only one may run per session
second service exit code: 2

$ kill -TERM <server pid>; wait
server exit code: 0
socket file after clean shutdown: socket removed
leftover server process: not running
```

So: mode `0600`, the second process refuses and exits with code `2` after printing the
message, and `SIGTERM` produces a clean `0` exit with the socket unlinked. No stray socket
was left behind.

Idle shutdown uses a `selectors` loop plus a self-pipe: a blocked `select()` with zero CPU
and no polling (ADR 0015). `SIGINT`/`SIGTERM` call `server.stop()` (sets an event and writes
the pipe); `service.shutdown()` then cancels any in-progress session and joins the workers
with a 5 s bound.

## Judgement calls (read these before consuming the seam)

1. **`TRANSCRIBING` + `toggle` reports `transcribing`, not `recording`.** The table cell is
   `→ RECORDING (queued)`. The queue flag is set and the reply is `ok=true`,
   `state="transcribing"`, `message="recording queued for after transcription"`. Reporting
   `RECORDING` would lie about the current state (no audio is being captured yet) and would
   make `status` immediately after disagree. The restart happens exactly once, when
   `finish()` returns, and `test_duplicate_toggle_never_stacks_recordings` /
   `test_toggle_during_transcribing_queues_exactly_one_restart` pin it.
2. **`ERROR` recovery is two-step, implemented literally.** `ERROR + toggle`,
   `ERROR + ptt-start`, `ERROR + cancel` all go to `IDLE`; only a following command starts
   recording. This is the table, not a slip, but it means a hotkey press after an error
   clears the error rather than immediately recording.
3. **Where the vocabulary lives.** `ServiceState` and `CommandResponse` are defined in
   `service.py` (the ticket says the state enum belongs there, and the response is the
   service's answer type). `socket_proto.py` imports them for the codec — so the transport
   depends on the domain vocabulary, not the reverse. To avoid a module-level import cycle,
   `Service.serve()` imports `ServiceServer`/`signal_shutdown` **locally**; there is no
   top-level `service → socket_proto` import. `socket_proto` still owns the JSON encoding,
   the socket path/mode, both endpoints, and the client helper.
4. **`cancel` runs on the command path but outside the lock.** It does not transcribe, so it
   does not violate "never block on transcription", and keeping it synchronous means a
   `toggle` arriving right after cannot start a second recording while the first is still
   stopping capture. If `session.cancel()` raises, the state becomes `ERROR` and
   `status_ui.error()` fires, but the reply is `ok=true` (the cancel was accepted); per
   ADR 0016 `cancel` has no exit code 3.
5. **The server answers one client at a time** with a 2 s per-connection read timeout. This
   keeps the state machine single-threaded; a hung client cannot stall the service beyond
   that timeout.
6. **Empty-text check uses `.strip()`; the injected text does not.** Whitespace-only output
   skips injection entirely (ADR 0012); non-empty text is passed to the injector byte for
   byte, e.g. `"  hello world  "` is injected unchanged. The trailing space remains the
   injector's job.
7. **A failed transcription drops a queued toggle.** `ERROR` wins; the user's next toggle
   clears the error. Not filling a queued recording after a failure is intentional.

## What tickets 10 / 11 / 12 / 15 must know

- **Ticket 10 (`cli`)** — call `socket_proto.send_command(path, verb, timeout=...)`; it raises
  `ServiceNotRunningError` with an actionable sentence (map to exit `2`) and never hangs.
  Map `response.state is ServiceState.ERROR` to exit `3`; benign rejections
  (`not-recording`, `already-recording`, `busy`) are `ok=false` but not engine/injection
  failures. `default_socket_path()` gives the path.
- **Ticket 11 (`pipeline`)** — `Service(session_factory, injector, status_ui, socket_path)`
  then `service.serve()`. Catch `socket_proto.ServiceAlreadyRunningError` and print its
  message / return non-zero; that is the single-instance path. A no-op `StatusUI` is fine.
- **Ticket 12 (`doctor`)** — `socket_proto.is_service_listening(default_socket_path())` is the
  stale-socket probe; `False` with the file present means "stale, restart `ges2t run`".
- **Ticket 15 (`streaming-session`)** — implement `DictationSession`. The service consumes
  `partials()` on its own forwarder thread, so `partials()` must not block `finish()` and
  should end after `finish()`/`cancel()`; `finish()` is called from the service's worker and
  must return the authoritative transcript.

## Verification (real output)

```
$ uv run pytest
tests/test_config.py .................................                   [ 11%]
tests/test_interfaces.py .................                               [ 17%]
tests/test_model_store.py .............................                  [ 28%]
tests/test_pw_record.py ..........................                       [ 37%]
tests/test_smoke.py ...                                                  [ 38%]
tests/test_socket_proto.py .............................                 [ 48%]
tests/test_state_machine.py ............................................ [ 64%]
tests/test_whistle_config.py ........................................... [ 80%]
tests/test_ydotool_injector.py ....................................      [100%]
============================= 280 passed in 0.95s ==============================

$ uv run pytest tests/test_state_machine.py tests/test_socket_proto.py -q
73 passed in 0.43s

$ uv run mypy src/ges2t
Success: no issues found in 13 source files

$ uv run mypy src/ges2t tests
tests/test_whistle_config.py:247: error: Argument 1 to "_error_message" has incompatible type "object"; expected "Mapping[str, Any]"  [arg-type]
tests/test_model_store.py:349: error: "bool" is invalid as return type for "__exit__" that always returns False  [exit-return]
tests/test_model_store.py:349: note: Use "typing.Literal[False]" as the return type or change it to "None"
tests/test_model_store.py:349: note: If return type of "__exit__" implies that it may return True, the context manager may swallow exceptions
Found 2 errors in 2 files (checked 24 source files)
```

`uv run mypy src/ges2t/service.py src/ges2t/socket_proto.py tests/test_state_machine.py tests/test_socket_proto.py`
→ `Success: no issues found in 4 source files`.

### The whole-tree mypy result is a pre-existing failure, not this ticket's

`mypy src/ges2t tests` fails on two lines in `tests/test_whistle_config.py` and
`tests/test_model_store.py` that are untouched by this branch and already fail on `main`
before this work. They are exactly the gap that ticket 20 (`type-check-whole-tree`, branch
`feat/type-check-whole-tree`) exists to close; that branch is not merged. The ticket's own
DoD line (`mypy src/ges2t`) passes and all four files added here are clean. I did not touch
the two failing test files because ticket 20 owns them and the ticket said to create only
these four files. Flagging it so the board can sequence ticket 20 rather than treating it as
a regression here.

Import isolation was checked: a fresh interpreter importing both modules leaves `gi`,
`pystray`, `needle`, `cactus_needle`, and `subprocess` absent from `sys.modules`.
