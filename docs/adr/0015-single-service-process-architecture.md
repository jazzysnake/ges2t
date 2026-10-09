# One long-lived service process owns the engine, capture, state, and socket

- **Status:** accepted
- **Date:** 2026-10-09

## Context

Several decisions push in the same direction and only make sense together:

- The hotkey design (ADR 0004) makes the CLI a **thin client** that signals a running
  process over a Unix socket.
- The engine is **process-global and not thread-safe** (ADR 0005), so exactly one component
  in exactly one process may hold it.
- Model load is cheap but not free (26 ms measured) and the model occupies ~69 MB, so
  re-loading per keypress is wasteful even where it is possible.
- The tray (ADR 0013) must reflect live state, which exists in the same process that owns
  recording and transcription.
- Model acquisition and loading are separate calls (ADR 0010), and the network is touched
  only by acquisition (ADR 0018).

Without a stated owner, these turn into duplicated state and races.

## Decision

**`ges2t run` is a single long-lived service process, and it owns everything stateful.**

Responsibilities of the service:

- Owns the **engine** (exactly one loaded model; loaded at startup if the model is present).
- Owns the **audio source** (ADR 0008) for the duration of a recording.
- Owns the **state machine** and is the only thing that mutates it.
- Owns the **tray/status UI** on its own thread (ADR 0013).
- Hosts the **Unix domain socket** at `$XDG_RUNTIME_DIR/ges2t.sock`, mode `0600`, and is the
  single instance for the session. A second `ges2t run` must detect the existing socket and
  exit with a clear message rather than starting a competing engine.

Responsibilities of CLI subcommands (ADR 0016):

- `toggle`, `ptt-start`, `ptt-stop`, `cancel`, `status` are **thin clients**: connect to the
  socket, send a short command, exit with a meaningful status code. They do **not** import
  the engine, do not capture audio, and must complete in milliseconds so they are safe to
  bind to a key.
- `setup`, `doctor`, and `models` are **standalone** and do not require the service to be
  running.

State machine (explicit, closed set): **`IDLE → RECORDING → TRANSCRIBING → IDLE`**, plus
`RECORDING → IDLE` via cancel and `* → ERROR → IDLE` on failure.

Hard requirements on the service:

- **Never block the socket loop on transcription.** Transcription happens off the command-
  handling path; commands arriving during `TRANSCRIBING` get a defined answer, not a hang.
- **The service outlives failures.** A dead engine worker, a failed injection, or an
  unavailable audio device must produce an `ERROR` state plus a notification, and must leave
  the service able to accept the next command.
- **Recording is never silently truncated.** The stop path drains captured audio before
  transcription (ADR 0008).
- **No work at idle.** Idle means idle: no polling loops, no network, no busy-wait.

## Consequences

- The hotkey path costs a socket round-trip plus a shell-out (~milliseconds) and the model is
  always warm, which is what makes the sub-1-second framework budget (ADR 0001) plausible.
- Because the CLI never loads the engine, `ges2t toggle` stays fast and a broken engine
  cannot break the key binding — it produces an error notification instead.
- Single-instance enforcement is required, and its failure mode (two engines, two trays, two
  sockets) is a real bug class; it is an explicit item in `doctor` and in the state-machine
  tests (ADR 0014).
- Autostart is a documented **systemd user unit** that runs `ges2t run`; v1 does not install
  it automatically (packaging is out of scope, ADR 0000).
- The boundary is also the abstraction seam: the client protocol is intentionally small
  (a handful of verbs) so a future non-KDE or non-socket transport is a contained change.
