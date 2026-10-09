# 002 — Reviewer: streaming wiring and end-to-end (ticket 16)

Reviewer for ticket 16 on branch `feat/streaming-wiring-and-e2e`, worktree
`.worktrees/streaming-wiring-and-e2e`, HEAD `450eb73` (three-dot diff against
`main`). Written incrementally; sections are appended as probes complete.

## Verdict (to be finalised)

**PASS — ready to merge** (non-blocking doc/wording notes in §3). See §5 for the
explicit verdict and the what-could-not-verify list.

## 0. Method

- Diff basis: `git diff main...feat/streaming-wiring-and-e2e`.
- All probes use fakes and the real `Service` in-process. No microphone, no
  engine, no `ydotool`, no keystrokes, no clipboard, no `sudo`, no service
  change, no network.
- Every mutation probe: apply, run under an external `timeout`, then
  `git checkout --` and delete `__pycache__` + `.mypy_cache` + `.pytest_cache`;
  `git status --porcelain` asserted clean after every restore.
- The only intended write is this file.

## 1. The selection rule

**Default path is streaming — confirmed end to end.** `StreamingConfig.enabled`
defaults to `True` (`src/ges2t/config.py:145`), `WhistleTranscriber.__init__` sets
`self.supports_streaming = True` (`src/ges2t/transcribers/whistle.py:376`), so a
plain `Config()` selects `_SESSION_STREAMING`.

I did not take the rule's word for it. `/tmp/t16probe/probe_default.py` patched
only the four concrete builders (`_missing_artifacts`, `build_transcriber`,
`build_audio_source`, `build_injector`, `build_status_ui`) and `Service.serve`,
so the **real** `run_service` → `_startup` → `_build_service` → `_session_factory`
path ran, wrapped `pipeline.StreamingDictationSession`/`BatchDictationSession` so
the constructed instance was captured, then drove a real recording on the real
`Service` with a fake streaming transcriber (`segments=[partial, final]`):

```
PROBE_DEFAULT_END_TO_END=PASS streaming
EXIT=0
```

The captured session was a `StreamingDictationSession`; `transcriber.calls`
contained `"stream"` and not `"transcribe"`; and the injector received only the
final text. No session is built at startup — selection happens per recording via
the factory closure. **Streaming is genuinely the default; the rule does not
silently pick batch under normal conditions.**

The rule (`_select_session_kind`) reads only `config.streaming.enabled` and
`transcriber.supports_streaming`: config and the capability flag, never a probe
and never an exception (ADR 0010/0011). Two outcomes, one branch. `_streaming_chunk_seconds`
reads the **selected engine's** block (`config.engine_blocks.get(config.transcriber.engine, {})`)
through `WhistleConfig.from_mapping`, exactly as `_build_whistle` does, so it is
config-derived, not guessed. An invalid block raises `WhistleConfigError` naming
the key (via `_validate_ranges`); it never falls through to the other path. On
the startup path `build_transcriber` has already parsed the same block, so the
second parse cannot add a new failure mode.

**Public signature unchanged.** `git diff` shows no added/removed line matching
`run_service`; `def run_service(config: Config, status_ui: StatusUI | None = None) -> int:`
is a context line. Only the private `_session_factory` and `_build_service`
signatures grew a `config` parameter — precisely ticket 11's reviewer
instruction. `test_the_public_run_service_signature_is_unchanged` pins it.

**No selection logic in `service.py`.** See §3: the only `service.py` change is a
WARNING. The service never learns which session it got.

## 2. Nothing may be injected until release — falsification attempt on the wired path

**Holds.** `/tmp/t16probe/probe_boundary.py` drives the composition root
(`pipeline._build_service`) with the **real** `Service` and the **real**
`StreamingDictationSession`, a fake streaming transcriber, a fake injector and a
fake status UI. Distinct sentinels: four partials
(`ZZPARTIAL-one/two/two/three-q7`, the third a consecutive duplicate) followed by
`ZZFINAL-published-transcript-q7`. Unmutated, run 5/5:

```
PROBE_INJECTION_BOUNDARY=PASS only-final-injected   (5/5, EXIT=0)
```

`injector.injected == ["ZZFINAL-published-transcript-q7"]`; no `ZZPARTIAL-*`
string appears among the injected payloads; `status_ui.partials` advanced
(display-only, deduplicated); `transcriber.calls` had `"stream"`, not
`"transcribe"`. `probe_default.py` (the `run_service` path) asserts the same
boundary independently.

**My own falsifying mutation.** In `streaming_session.py::_record` I replaced the
final branch's `self._final_text = segment.text` with
`self._final_text = self._partials[-1] if self._partials else segment.text`, so a
partial becomes the transcript `finish()` returns. Caches cleared, then:

```
probe_boundary.py  -> AssertionError: ['ZZPARTIAL-three-q7']  (EXIT=1)
pytest tests/test_pipeline_streaming.py -> 2 failed, 12 passed  (EXIT=1)
  FAILED test_wired_streaming_path_displays_partials_but_injects_only_the_final_text
  FAILED test_wired_streaming_path_injects_nothing_for_a_whitespace_final_text
```

After `git checkout -- src/ges2t/streaming_session.py` and clearing
`__pycache__`/`.mypy_cache`, `git status --porcelain` was clean again and the
probe passed. The implementer's `M_partial_becomes_final` claim is independently
reproduced: a partial reaching the injector is caught, and the implementer's own
message (`['POISON display only'] != ['published transcript']`) is of the same
shape. Combined with the structural separation (`_final_text` vs `_partials`;
`finish` reads only the former, `partials` only the latter), **ADR 0011's
nothing-until-release rule is guarded on the wired path.**

## 3. The shutdown-window decision and the `service.py` edit

**Ruling: the decision is right; the edit is justified and minimal.**

- **Not a rationalised drop.** The distinction is exactly the one that matters:
  under ADR 0011 nothing is injected until release, and `finish()` returns only
after joining the session worker. The give-up therefore loses a *whole
utterance*, never partial or corrupt *transcript* — qualitatively different from
the alternative (a bounded session join) which would truncate a *valid*
transcript on the normal path while the user waits. Dropping an utterance on a
wedged-engine teardown is the smaller, ADR-conformant harm.
- **The 5 s bound is ADR 0015's, not a new invention.** Raising it or making it
  configurable would trade a bounded, visible loss for an unbounded, invisible
hang. No principled larger number exists. Deliberately not configurable is the
right call.
- **The WARNING genuinely fires; the test is not vacuous.**
  `test_shutdown_surfaces_a_transcription_it_gave_up_on` monkeypatches
`service_module._SHUTDOWN_JOIN_TIMEOUT_SECONDS` to 0.05, holds `finish()` open on
`FakeDictationSession.block_finish`, waits for `finish_started`, and calls
`shutdown_with_timeout(service, timeout=1.0)` — a real daemon-thread bound that
raises if `shutdown()` wedges. It passed 5/5. My mutation removing the
`_logger.warning(...)` call (replacing the inner `if worker.is_alive():` body
with `pass`) made exactly that test fail (`1 failed, 52 passed`, EXIT=1), so the
assertion `any("gave up" in record.getMessage() ...)` is load-bearing, not
vacuous. The test also asserts `injector.injected == []` at give-up, pinning
"lost utterance, not partial text".
- **The edit is minimal and session-agnostic.** Diff is one `import logging`, one
  module `_logger = logging.getLogger(__name__)`, three doc paragraphs, and the
WARNING inside `shutdown()`. It imports no session/streaming/selection concept,
adds no branch on session kind, and does not touch the factory. ADR
0010/0015's "the service never learns which session it got" still holds.
- **No new config key.** `src/ges2t/config.py` is unchanged; `git diff` shows no
  `shutdown_timeout` or similar.
- **Why touching `service.py` is nevertheless defensible.** The ticket's Notes
  say "Do not touch `service.py`", but that note guards against
*session-selection* logic in the service, and ticket 15's `003-review.md`
explicitly recorded this exact shutdown window as a follow-up "for ticket 16 ...
so the loss is a known, deliberate behaviour". The task brief put the change in
scope. The edit is the smallest possible way to satisfy that follow-up.

**Non-blocking observations (do not block merge):**

1. `ticket.md`'s Notes still say "Do not touch `service.py`", which now
   contradicts the shipped change. The Notes should be updated to record the
   in-scope shutdown-visibility exception (documentation hygiene only).
2. The WARNING text says "the current utterance was not typed", but the joined
   worker spans `session.finish()` **and** `injector.inject(text)`. If the worker
   is alive because a slow `inject()` is still draining (rather than a wedged
   engine), the utterance may in fact have been (partly) typed. This is a
   pre-existing property of the 5 s bound over the whole worker and does not
   inject partial *transcript*, but the message overstates certainty; "the
   transcript may not have been typed" would be more accurate.
3. All DoD checkboxes in `ticket.md`, including the automated ones, are unticked
   even though the automated work is done. Cosmetic.

## 4. Tests, falsification, scope, mechanicals

- **Counts.** Branch `uv run pytest -q` → **639 passed**, exit 0. Current `main`
  collects **624** (verified in the parent worktree, read-only), so **+15**, matching
  the claim. A sorted diff of collected node IDs (`main` vs branch) shows **zero**
  tests in `main` that are missing from the branch and exactly **15** new node IDs —
  nothing deleted, renamed, skipped or weakened. The 15 = 13 `def test_` in
  `test_pipeline_streaming.py` (one parametrized ×2 = 14 collected) + 1 in
  `test_state_machine.py`. `grep skip\|xfail` in the new file: none.
- **mypy.** `rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests` →
  `Success: no issues found in 45 source files` (exit 0).
- **Boundedness.** `rm -rf .mypy_cache __pycache__ && timeout 300 uv run pytest -q`
  printed `639 passed in 4.62s` and the **process exited 0** (`PYTEST_EXIT=0`) —
  no summary-then-hang. The new file ran 5/5 (`14 passed`, 0.12–0.13 s) and the
  shutdown test 5/5 (`1 passed`), no flakes. The wired tests bound every wait via
  `_wait_for` and the session reads via `_call_with_timeout` (daemon thread +
  timeout), and the session worker is a daemon, so a regression fails loudly
  instead of hanging `threading._shutdown`.
- **My three-plus independent mutations** (each with an external `timeout`, caches
  cleared, restored with `git checkout --`, `git status` clean after every run):

| Mutation | Result |
|---|---|
| `_record`: partial becomes `_final_text` | probe injected `['ZZPARTIAL-three-q7']`; `2 failed, 12 passed` |
| `_select_session_kind`: ignore `streaming.enabled` | `3 failed, 11 passed` (switch-off batch test + log reason + wired batch) |
| `_streaming_chunk_seconds`: hard-code `1.0` | `1 failed, 13 passed` (`window_stays_with_the_adapter_and_chunk_reaches_the_session`) |
| `shutdown`: remove the WARNING | `1 failed, 52 passed` (the give-up test) |

- **Scope.** `git diff main...feat/streaming-wiring-and-e2e --name-status` touches
  only `docs/project/README.md`,
the ticket's own docs (`ticket.md`, `001-comment.md`), `src/ges2t/pipeline.py`,
`src/ges2t/service.py`, `tests/test_pipeline_streaming.py`,
`tests/test_state_machine.py`. `git diff ... -- docs/adr/` → empty (no new ADR);
`streaming_session.py`/`streaming.py` → unchanged; `src/ges2t/config.py` →
unchanged (no config key). Matches the ticket's scope exactly.
- **`run_service` signature.** Only context lines mention it; the changed
  signatures are the private `_session_factory`/`_build_service`
  (`+ config`). `test_the_public_run_service_signature_is_unchanged` pins it.
- **ADR 0019.** New helpers are fully annotated (`-> str`, `-> float`,
  `-> Callable[[], DictationSession]`), interface-typed (`Transcriber`,
  `AudioSource`), no positional-tuple return, no new dataclass needed. mypy 0
  errors over `tests` too.
- **Manual E2E.** `001-comment.md` contains the four-step script (40 s utterance,
  partials, batch fallback, latency numbers) with expected observations, and the
  manual DoD box in `ticket.md` is unticked. I did **not** run it (user-owned,
  requires mic/window/keystrokes).

## 5. Could not verify

- The real `WhistleTranscriber`/engine path: every probe used the shared fakes.
  `WhistleTranscriber.supports_streaming` is `True` by construction (line 376),
  but I did not load the model.
- The manual end-to-end DoD (mic, focused window, real model, latency numbers) —
  user-owned, not attempted, `ges2t run` never executed.
- That the 5 s shutdown window is reachable against a genuinely slow real
  Whisper/Needle final flush; established from code reading plus the fake-driven
  test, not an E2E.
- The implementer's full 8-mutation matrix; I re-ran four of my own (above),
  three of which reproduce the claimed classes (boundary, selection, warning).

## Verdict

**PASS — ready to merge** (two non-blocking documentation/wording notes in §3).

- **Streaming is genuinely the default.** `Config()` → `streaming.enabled=True` +
  `WhistleTranscriber.supports_streaming=True` → `StreamingDictationSession`,
  confirmed end-to-end through the real `run_service`. The rule is config-derived,
  two-outcome, one branch, capability flag only; invalid config raises clearly.
- **The injection boundary holds** under my own mutation: a partial can never reach
  the injector on the wired path, and making one the final text fails two wired
  tests plus my independent probe.
- **The shutdown decision is right and the `service.py` edit is justified, minimal
  and session-agnostic.** The WARNING is real and its test is non-vacuous.
- **Nothing was deleted, skipped or weakened; the suite exits 0; mypy is clean;
  scope is exactly as declared.**
- **Orchestrator:** `main` has advanced (docs-only commits `2783656`, `b872ccc`,
  `47b9502`, `43ea99b` — no code/test-node change) past the branch's merge-base
  `52d63a0`; rebase/merge is trivial but should be done before landing. Consider
  the §3 notes (stale ticket Notes line, WARNING wording) as a follow-up.

