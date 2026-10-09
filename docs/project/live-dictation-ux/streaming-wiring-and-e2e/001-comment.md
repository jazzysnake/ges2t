# 001 — Streaming wiring and end-to-end coverage

- Ticket: `ticket.md` (`streaming-wiring-and-e2e`)
- Branch: `feat/streaming-wiring-and-e2e`
- Worktree: `.worktrees/streaming-wiring-and-e2e`
- Status: **in review**
- Commits: `73f612e` (wiring + shutdown visibility), `8f8b79d` (run_service selection test + stronger log assertions)

## What was built

The batch-vs-streaming selection rule in the composition root, plus the tests
that pin it and the wired path, plus the one service-layer visibility change the
ticket 15 follow-up asked for.

Files changed:

- `src/ges2t/pipeline.py` — the selection rule, threaded through the **private**
  `_session_factory` and `_build_service`; `run_service`'s public signature is
  unchanged.
- `src/ges2t/service.py` — one `logging` import, one module logger, and a
  `WARNING` when `Service.shutdown` gives up on an in-flight transcription (the
  shutdown-window decision below). No session-selection logic; the service stays
  session-agnostic (ADR 0015).
- `tests/test_pipeline_streaming.py` — **new**, 14 tests.
- `tests/test_state_machine.py` — +1 test for the shutdown give-up warning.

No config key, no ADR, no `streaming_session.py` change, no `streaming.py`
change, and no `ges2t run`, keystroke, `ydotool`, clipboard, `sudo`, service, or
network use.

## The selection rule (surgical diff)

`_session_factory(config, transcriber, audio_source)` is now the one place the
choice happens. It is reached from `_build_service(config, …)` from
`_startup(config, …)` from `run_service(config, status_ui=None)`; only private
signatures changed.

```python
def _select_session_kind(config: Config, transcriber: Transcriber) -> str:
    if not config.streaming.enabled:
        reason = "streaming.enabled = false"
        kind = _SESSION_BATCH
    elif not transcriber.supports_streaming:
        reason = f"transcriber {transcriber.name!r} does not support streaming"
        kind = _SESSION_BATCH
    else:
        reason = (
            f"streaming.enabled = true and transcriber {transcriber.name!r} "
            "supports streaming"
        )
        kind = _SESSION_STREAMING
    _logger.debug("using the %s dictation session (%s)", kind, reason)
    return kind


def _session_factory(config, transcriber, audio_source):
    kind = _select_session_kind(config, transcriber)
    chunk_seconds = _streaming_chunk_seconds(config)

    def create() -> DictationSession:
        if kind == _SESSION_STREAMING:
            return StreamingDictationSession(
                audio_source, transcriber, chunk_seconds=chunk_seconds
            )
        return BatchDictationSession(transcriber, audio_source)

    return create
```

Two outcomes, one branch, capability flag only (ADR 0010) — no exception, no
probe, no third branch. The reasoning is logged at DEBUG with the selected kind
and why, so `run_service` records it; a user can reach it without a debugger by
configuring logging, and the test asserts the exact sentence.

### Window and chunk go to their separate owners

- **window** → the adapter: `build_transcriber` already passes
  `WhistleConfig` to `WhistleTranscriber`, and `WhistleTranscriber.stream` applies
  `window_seconds` to `streaming.drive`. Asserted on
  `adapter.config.window_seconds`.
- **chunk** → the session: `_streaming_chunk_seconds` parses the same
  `[engine.whistle]` block with the same `WhistleConfig.from_mapping` and
  `.get(..., {})` defaulting used by `_build_whistle`, and passes only
  `chunk_seconds` to `StreamingDictationSession` (which has no window parameter,
  by ticket 15's design). Asserted on `session._chunk_seconds` and on
  `audio.chunk_seconds_seen`.

Parsing the block unconditionally is safe: `build_transcriber` has already
validated it on the startup path, so the batch branch cannot acquire a new
failure mode; the parse is one-time at service construction, not per recording.

### Partials stay display-only

The wiring does **nothing** with partials: the service already forwards
`session.partials()` to `status_ui.partial()` on its own daemon thread (ticket 9,
ticket 21), and only the result of `finish()` is injected. Ticket 15's structural
separate-field design is untouched. The new wired test drives the **real**
`Service` built by `_build_service` and asserts the injector receives only the
final text and never the `POISON display only` partial.

## The shutdown-window decision (ticket 15 follow-up)

**Decision: keep the 5 s shutdown bound and the unbounded session join, and make
the give-up visible with a `WARNING` — do not wait longer.**

What the window is: `Service.shutdown()` joins the `_run_finish` daemon thread
with `_SHUTDOWN_JOIN_TIMEOUT_SECONDS = 5.0`. If `session.finish()` (whose join is
unbounded on purpose) is still in flight past that bound, `shutdown()` returned
silently and the process exited, killing the daemon finish worker before it could
`inject()`. Ticket 15's review recorded this as its follow-up for this ticket.

Reasoning:

1. **Waiting longer is the wrong lever.** ADR 0015 is explicit that shutdown must
   not hang forever on a wedged engine. The 5 s bound exists for exactly that,
   and removing it (or raising it to a "safe" value) trades a bounded, visible
   loss for an unbounded, invisible hang — which is worse for a systemd unit and
   for Ctrl-C. There is no principled larger number: a legitimately slow flush
   and a wedged engine are indistinguishable from the outside.
2. **The loss is ADR-0011-conformant.** Nothing is injected until release, so the
   give-up is a *lost utterance*, never partial or corrupt text. That is the
   qualitatively smaller harm the ADR protects, and it is unchanged by this
   ticket.
3. **A bounded *session* join would be worse.** Ticket 15 already argued this:
   shortening the session's unbounded join would fire on a normal, slow final
   flush *while the service is still running and the user is waiting*, silently
   truncating a valid transcript. The safe place to be visible is at teardown,
   where the service has already declared it is giving up.
4. **The only real defect was that it was silent.** `shutdown()` did not check
   `worker.is_alive()` after the bounded join, so the drop produced no signal at
   all. One `_logger.warning(...)` fixes that. `logging`'s last-resort handler
   writes WARNING records to stderr with no configuration, so a user running
   `ges2t run` in a terminal (or under systemd, which captures stderr) sees it.
5. **Deliberately not configurable.** A `shutdown_timeout` key would invite users
   to paper over a slow engine with a longer hang; engine/adapter performance is
   the real fix. No new config key was added (ticket's "which session is derived,
   not configured" spirit).

This is the one place I touched `service.py`, against the ticket Notes' blanket
"Do not touch `service.py`". That note guards against session-selection logic in
the service; this change adds no selection logic and leaves the service
session-agnostic. The task brief for this ticket explicitly put a service-layer
behaviour change for the shutdown window in scope. A reviewer should judge the
change on that basis.

## Real verify output

Baseline on `main` (`52d63a0`): `624 passed`, `mypy` 44 files.

```
$ rm -rf .mypy_cache __pycache__ && timeout 300 uv run pytest -q
639 passed in 4.57s
PYTEST_EXIT=0

$ rm -rf .mypy_cache __pycache__ && timeout 300 uv run mypy src/ges2t tests
Success: no issues found in 45 source files
MYPY_EXIT=0
```

624 → 639 tests (+15: 14 in `test_pipeline_streaming.py`, 1 in
`test_state_machine.py`); 44 → 45 files under mypy. The full suite was run under
an external `timeout` and the process **exited 0** — no unbounded wait, no leaked
non-daemon thread.

## Falsification evidence

A throwaway harness (`/tmp/t16_falsify.py`, not committed) applied one textual
mutation at a time, ran the relevant file under `timeout 90`, restored with
`git checkout --`, and cleared `__pycache__`/`.mypy_cache`/`.pytest_cache` before
every run (the stale-bytecode and stale-mypy-cache traps). Every new test failed
under at least one mutation:

```
P_always_batch            (selection ignores capability/switch)     9 failed
P_always_streaming        (switch ignored)                          3 failed
P_ignore_capability       (supports_streaming ignored)              1 failed
P_ignore_chunk_config     (chunk_seconds hard-coded)                1 failed
P_remove_debug_log        (DEBUG line removed)                      3 failed
P_run_service_param_renamed (public signature changed)              1 failed
S_remove_shutdown_warning (give-up warning removed)                 1 failed
M_partial_becomes_final   (partial stored as the transcript)        11 failed
```

Restoration was asserted after each mutation (`git status --porcelain <file>`
empty) and the harness reprints the failing test names. Full matrix detail:

| Mutation | Notable tests that failed |
|---|---|
| `P_always_batch` | streaming/type, window+chunk, defaults, wired partials, whitespace, both trailing-space params, `test_session_selection_is_logged_at_debug_level`, `test_run_service_selects_streaming_at_startup` |
| `P_always_streaming` | capability and switch-off batch tests, batch log reason, wired batch core-epic test |
| `P_ignore_capability` | `test_batch_is_selected_when_streaming_is_enabled_but_the_engine_cannot` |
| `P_ignore_chunk_config` | `test_window_stays_with_the_adapter_and_chunk_reaches_the_session` |
| `P_remove_debug_log` | both log tests + `test_run_service_selects_streaming_at_startup` |
| `P_run_service_param_renamed` | `test_the_public_run_service_signature_is_unchanged` |
| `S_remove_shutdown_warning` | `test_shutdown_surfaces_a_transcription_it_gave_up_on` |
| `M_partial_becomes_final` | wired injection boundary + ticket 15's authoritative-final tests |

**Proof that a partial reaching the injector is caught.** Under
`M_partial_becomes_final`, the wired test fails at the exact injection boundary:

```
>           assert injector.injected == ["published transcript"]
E           AssertionError: assert ['POISON display only'] == ['published transcript']
E             At index 0 diff: 'POISON display only' != 'published transcript'
FAILED tests/test_pipeline_streaming.py::test_wired_streaming_path_displays_partials_but_injects_only_the_final_text
```

Under the same mutation, `test_wired_streaming_path_injects_nothing_for_a_whitespace_final_text`
also fails (the partial is injected), so both the "final is authoritative" and the
"silence injects nothing" rules are guarded on the wired path.

## Manual end-to-end — owned by the user, not run by this agent

The ticket's manual DoD needs a **microphone, a real model, and a focused
window**; this agent may not inject keystrokes, so this box is deliberately left
**unticked**. Prerequisites on the reference machine (already true):
`ges2t setup` completed, `ydotoold` active/enabled, `/dev/uinput` accessible.

Run `uv run ges2t run` in one terminal and use another for the commands.

```bash
# 0. streaming is on by default: [streaming] enabled defaults true, no config edit
#    Needed once, to see the selection line: configure DEBUG logging for ges2t.pipeline.

# 1. long utterance past the engine's 30 s ceiling (the case batch cannot do)
time ( uv run ges2t toggle; sleep 40; uv run ges2t toggle )   # speak for ~40 s
#   EXPECT: the complete text, with no duplicated or dropped words at the seams.

# 2. partials during the utterance
#   EXPECT (tray): partial text advances while recording.
#   EXPECT (headless): if the tray is off, the partials are only display-only and
#   produce no log by default; a debug log of status_ui.partial calls is the
#   fallback confirmation. Nothing is typed before release in either case.

# 3. batch fallback unchanged
#   set [streaming] enabled = false, restart `ges2t run`, then:
time ( uv run ges2t toggle; sleep 10; uv run ges2t toggle )   # ~10 s dictation
#   EXPECT: still works, identical text behaviour (batch path).

# 4. numbers to report: total wall time for the ~40 s utterance (from `time`),
#    and the per-chunk latency median. The shipped service does not log per-chunk
#    timing; ADR 0011's measured median for the capped 5 s window is 148 ms
#    (p90 ~300 ms). A fresh number needs the engine probe used for ADR 0011.
```

Expected observations and known in-flight defects: the `hu`-layout character
correctness and injection latency were fixed by tickets 22/23/28 and are not part
of this ticket; what this ticket proves is that the **streaming** path is selected
and used, that partials are display-only, and that the batch path is unchanged
when the switch is off.

## Anything a reviewer must know

- Only ticket 16's `ticket.md` status and board row were changed (to `in review`
  with the branch). No other ticket's row was touched.
- The `service.py` change is the shutdown visibility only — verify it adds no
  session knowledge and that `test_shutdown_surfaces_a_transcription_it_gave_up_on`
  genuinely falsifies the WARNING by removing it (mutation `S` above).
- `_streaming_chunk_seconds` is parsed even on the batch branch. It is one-time
  at startup and cannot add a startup failure `build_transcriber` would not
  already produce; if a future engine needs a different chunk source, that is the
  point to revisit (ADR 0010/0019).
- The window/chunk split is asserted against the adapter and the session
  separately, exactly as `../streaming-session/ticket.md` requires.
- No network, no mic, no subprocess, no keystrokes were used anywhere in this
  ticket's verification.
