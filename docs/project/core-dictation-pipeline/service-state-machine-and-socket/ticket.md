# Service: state machine, single instance, socket protocol

- **Priority:** P0
- **Status:** to do
- **Complexity:** M
- **Epic:** ../epic.md

## Current state / context

The CLI must be a thin client that signals a running process (ADR 0004), the engine is
process-global and not thread-safe so exactly one process may own it (ADR 0005), and the tray
must reflect live state from that same process (ADR 0013). ADR 0015 is the decision that ties
these together: one long-lived service owns the engine, capture, state, and the socket.

Nothing of this exists yet. This ticket builds the **service and its protocol only** — it
does not build concrete capture/transcription/injection and it does not build the CLI. That
keeps it testable with the fakes from
`../core-interfaces-and-fakes/ticket.md`, with no mic, no engine, no `/dev/uinput`.

Relevant decisions: ADR 0015 (the whole architecture), ADR 0004 (the hotkey path and why the
state machine must tolerate rapid/out-of-order commands), ADR 0014 (the state machine is a
primary test target).

## Definition of done

- [ ] `src/ges2t/service.py` defines:
      - a closed state enum with exactly: `IDLE`, `RECORDING`, `TRANSCRIBING`, `ERROR`;
      - `class DictationSession` (Protocol) — **the seam this ticket publishes for later
        tickets** — with `start() -> None`, `partials() -> Iterator[str]` (display-only live
        text, may yield nothing), `finish() -> str` (blocking; returns the final text), and
        `cancel() -> None`. The service never touches audio, the engine, or the injector
        directly; it only drives a session.
      - `class Service` taking injected dependencies: `session_factory: Callable[[],
        DictationSession]`, `injector: Injector`, `status_ui: StatusUI`, and a socket path.
      - the command handlers for `toggle`, `ptt-start`, `ptt-stop`, `cancel`, `status`,
        implemented as explicit transitions from the table below.
- [ ] The transition table is implemented literally and is the specification:

      | state | `toggle` | `ptt-start` | `ptt-stop` | `cancel` | `status` |
      |---|---|---|---|---|---|
      | IDLE | → RECORDING | → RECORDING | error `not-recording` | error `not-recording` | `idle` |
      | RECORDING | → TRANSCRIBING | error `already-recording` | → TRANSCRIBING | → IDLE | `recording` |
      | TRANSCRIBING | → RECORDING (queued) | error `busy` | error `busy` | error `busy` | `transcribing` |
      | ERROR | → IDLE | → IDLE | error `not-recording` | → IDLE | `error` |

- [ ] **Never block the socket loop on transcription.** `finish()` runs off the command-
      handling path; commands arriving while `TRANSCRIBING` receive their defined answer
      (per the table) rather than hanging. A `toggle` in `TRANSCRIBING` must not lose the
      request or start a recording on top of one.
- [ ] **Failures do not kill the service.** A raising session or a raising `injector` moves the
      service to `ERROR`, calls `status_ui.error(<message>)`, and returns the service to a
      state where the next command is accepted. Specifically test: `finish()` raises;
      `inject()` raises; `start()` raises.
- [ ] Empty transcriptions: when `finish()` returns an empty or whitespace-only string, **no
      injection call happens at all** (ADR 0012) and the state returns to `IDLE` with no
      error.
- [ ] `src/ges2t/socket_proto.py` defines the wire protocol and both ends:
      - **newline-delimited JSON**, one line per message: request `{"cmd": "<verb>"}`, response
        `{"ok": bool, "state": "idle|recording|transcribing|error", "error": "code-or-null",
        "message": "human text or null"}`;
      - socket path `$XDG_RUNTIME_DIR/ges2t.sock`, created with mode `0600`;
      - **single instance**: if the socket exists and a connection succeeds, the new service
        exits with a clear message; if the socket file exists but nothing is listening, it is
        removed and rebound (stale socket);
      - a client helper used by the CLI, which fails fast with a clear error when no service
        is listening and never hangs (bounded connect+socket timeouts).
- [ ] Signal handling: `SIGINT`/`SIGTERM` shut the service down cleanly, cancel any in-progress
      session, and remove the socket file.
- [ ] `tests/test_state_machine.py` drives the **entire transition table**, plus the
      adversarial cases ADR 0004 calls out: duplicate `toggle`, `ptt-stop` without
      `ptt-start`, `ptt-start` while already recording, `ptt-start` while transcribing,
      `cancel` during recording, `cancel` while idle, an error followed by a successful
      command, and a session whose `finish()` returns `""`.
- [ ] `tests/test_socket_proto.py` covers: request/response round-trip against a real socket
      in `tmp_path`; the stale-socket rebind path; mode `0600` on the created socket; and that
      the client raises a clear error rather than hanging when nothing is listening.
- [ ] `uv run pytest tests/test_state_machine.py tests/test_socket_proto.py` passes and
      `uv run pytest` stays green.

- [ ] `uv run mypy src/ges2t` exits 0: every definition fully annotated and every
      signature interface-typed — ADR 0019.

## Notes

- The end-to-end behaviour of a real dictation (concrete session, real audio, real injection)
  is **not** in this ticket — that is `../dictation-pipeline-batch/ticket.md`, which
  implements `DictationSession` and passes a factory to `Service`.
- Do not import `gi`, `pystray`, `cactus_needle`, or `subprocess` here. Everything concrete
  arrives through the injected dependencies; that is what makes this ticket's tests honest.
- Use a `threading.Lock` only around state transitions, and keep `finish()` outside it —
  holding the lock across transcription is the classic way this design deadlocks.
- `status_ui.partial()` is display-only (ADR 0011): the service may forward partials from the
  session while recording, but it must never inject them.
- Document the transition table in a docstring so the code and the table cannot drift.

## Related tickets

- depends on: ../core-interfaces-and-fakes/ticket.md, ../config-loading/ticket.md
- blocks: ../cli-surface-and-commands/ticket.md, ../dictation-pipeline-batch/ticket.md,
  ../../live-dictation-ux/streaming-session/ticket.md
- related ADRs: ../../../adr/0015-single-service-process-architecture.md,
  ../../../adr/0004-global-hotkey-via-cli-and-service.md,
  ../../../adr/0012-verbatim-output-and-trailing-space.md
