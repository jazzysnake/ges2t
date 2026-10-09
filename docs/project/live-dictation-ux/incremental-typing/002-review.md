# 002 — review: incremental-typing (ticket 34)

- **Ticket:** [incremental-typing](ticket.md) (ticket 34, the payload of the incremental-typing chain)
- **Branch:** `feat/incremental-typing` (HEAD `ffa8a31`, merge-base with `main` `f1c072f`, `main` `27c09f0`)
- **Reviewer:** independent review pass, 2026-10-09
- **Verdict:** **PASS — safe to merge.** The implementation and its tests are correct on every
  load-bearing property. The ADR 0011 amendment is **substantively honest and safe to put in front
  of the user for sign-off**, with a short list of **non-blocking** wording/doc-comment corrections
  in §11 (the amendment's own new section has no overstatement; the stale statements are in the
  ADR's unchanged decision bullet 4 and in three code comments/log lines the amendment did not
  touch).
- **Locking answer (item 1):** the lock held across the typing call is
  `CommittedTyper._lock`, **not** the service's state lock. A wedged typer does **not** stall
  `status` / `toggle` / `cancel`; I measured under 1 ms for all of them while the typer was blocked
  (§1). Shutdown stays bounded (worker-join then partials-join, 5 s each; up to ~10 s worst case).

No source or test change is requested. The findings in §11 are documentation only.

---

## 1. ⚠️ Locking — measured, not argued

The implementer's claim ("one lock is held across the typing call") is true, but the lock is
`CommittedTyper._lock` (`src/ges2t/incremental_typing.py`), which protects only the watermark
(`_typed`, `_finished`, `_error`). The service's `Service._lock` still guards **state transitions
only**, and no command handler acquires the typer lock. So this is not ticket 21 repeated: ticket 21
was a collaborator call (`status_ui`) made *under the service lock*; here the call-out happens under
a private typer lock that the command loop never takes.

**Probe** (`/tmp/t34probe/probe_lock.py`): a `TextTyper` whose `type_text` blocks on an event, wired
through the real `Service` with a session whose `partials()` yields one committed value and then
never ends, so the partials thread sits inside `type_committed` holding `CommittedTyper._lock`.
`service.handle(...)` is then called from the main thread and timed.

```
$ PYTHONPATH=. uv run python /tmp/t34probe/probe_lock.py
toggle -> recording
state while typer wedged: recording
  status     served in    0.067 ms -> ok=True state=recording err=None
  ptt-start  served in    0.028 ms -> ok=False state=recording err=already-recording
  cancel     served in    0.047 ms -> ok=True state=idle err=None

toggle (RECORDING->TRANSCRIBING) served in 0.762 ms -> transcribing
  status     served in    0.110 ms -> ok=True state=transcribing err=None
  toggle     served in    0.029 ms -> ok=True state=transcribing err=None
  cancel     served in    0.018 ms -> ok=False state=transcribing err=busy
  ptt-start  served in    0.012 ms -> ok=False state=transcribing err=busy
```

`status`, `toggle`, `ptt-start` and `cancel` are all served in **< 1 ms** while the typer is wedged.
The `toggle` in `RECORDING` returns in 0.76 ms because its `after` hook only *starts* the finish
worker.

**Which thread types** (`/tmp/t34probe/probe_threads.py`), so "capture never touches the lock" is
mechanical, not asserted:

```
$ PYTHONPATH=. uv run python /tmp/t34probe/probe_threads.py
command thread: MainThread
type_text calls (payload, thread): [('abc', 'ges2t-partials'), (' ', 'ges2t-finish')]
threads alive at the end: []
```

`type_committed` runs on `ges2t-partials`, `finish` on `ges2t-finish`; capture runs on the session's
own `ges2t-streaming-worker` and never takes `CommittedTyper._lock`. So the recording loop is not
blocked by a slow injector.

**The honest caveats, both measured/read:**

- **Shutdown is bounded but can be 2× the documented constant.** `Service.shutdown` joins the finish
  worker with `_SHUTDOWN_JOIN_TIMEOUT_SECONDS` (5 s) and *then* joins the partials thread with the
  same 5 s, so a wedged typer that keeps both alive makes shutdown take ~10 s (measured in the
  probe). It returns; the daemon threads are abandoned. This is not a hang, but the docstring says
  "waits up to `_SHUTDOWN_JOIN_TIMEOUT_SECONDS` for an in-flight transcription", which a reader
  takes as the total bound.
- **A permanently wedged typer leaves the service stuck in `TRANSCRIBING`.** The finish worker
  blocks on the typer lock, so `_complete_transcription` never runs; the command loop still answers,
  `toggle` is queued, and `cancel` returns `busy` (per the state table). This is the expected
  consequence of "the utterance cannot be finalised", not a command-loop stall.

---

## 2. The reconciliation, on the **wired** path (item 2)

I reproduced ticket 33's exact trap through the real `_build_service` /
`StreamingDictationSession` path, and independently showed what a `partials()`-only consumer
loses. The trap is pass 1 `committed="abc", pending="def"`; pass 2 `committed="abcdef", pending=""`
(display `"abcdef"` on both, so the session drops pass 2); final `"abcdef"`.

```
$ PYTHONPATH=. uv run python /tmp/t34probe/probe_reconcile.py
WIRED trap:  injector.typed = ['abc', 'def ']
WIRED trap:  document       = 'abcdef '
WIRED trap:  batch document = 'abcdef '

partials() committed values seen by the forwarder: ['abc']
partials()-ONLY typed document: 'abc'
partials()+finish typed document: 'abcdef '
CONFIRMED: partials() alone loses 'def'; finish() recovers it.
```

No text is lost or duplicated: the service typed exactly `abc` + `def` + `" "` = `abcdef `. A
partials-only consumer loses `def`, exactly as `committed-delta-interface/002-review.md` §3 warns.

**Falsification of the guard:** mutation M2 removes `self._extend(target)` from `finish` (the
reconciliation). It is caught by **14 failing tests** across
`tests/test_incremental_typing.py` and `tests/test_pipeline_streaming.py` (§10), including
`test_a_collapsed_advance_is_recovered_from_finish` and the wired collapsed-advance test. Removing
the guard loses the tail, as predicted.

---

## 3. Exactly-once: the watermark is a position, not a delta sum (item 3)

`_typed` holds the exact document typed so far; `type_committed(c)` types `c[len(self._typed):]`
and sets `_typed = c`; `finish` does the same against the assembled document. Independent probe
with the hostile sequence `["abc", "abc", "abcd", "abcd"]` (a repeated cumulative value, which a
delta-sum would type four times):

```
watermark: raw.typed = ['abc', 'd']  typed = 'abcd'
CONFIRMED: position watermark types each character exactly once.
```

The delta-sum that would double-count (`"abc"+"abc"+"abcd"+"abcd"`) is exactly what mutation **M1**
introduces (`type_text(target)` instead of `type_text(delta)`), and it is caught by **11 failing
tests**. Mutation **M10** (one `CommittedTyper` reused across recordings) is caught by
`test_each_streaming_recording_gets_a_fresh_watermark`.

---

## 4. The byte-identical document (item 4)

I did not trust the test's constant expressions. I compared the **two real paths** through the real
`YdotoolInjector` (a recording `KeySequenceSource`, a no-op runner) — the batch
`inject(final_text)` document vs. the incremental `type_committed(...)` + `finish(final_text)`
document:

```
$ PYTHONPATH=. uv run python /tmp/t34probe/probe_bytes.py
trailing_space=True
  batch source payloads : ['Good morning. Last night storm ']
  stream source payloads: ['Good morning.', ' Last', ' night storm', ' ']
  batch doc    = 'Good morning. Last night storm '
  stream doc   = 'Good morning. Last night storm '
  byte-identical: True
trailing_space=False
  batch source payloads : ['Good morning. Last night storm']
  stream source payloads: ['Good morning.', ' Last', ' night storm']
  batch doc    = 'Good morning. Last night storm'
  stream doc   = 'Good morning. Last night storm'
  byte-identical: True
```

Byte-identical on both `ui.trailing_space` values, including exactly one trailing space.

The in-repo test `test_typed_document_is_byte_identical_to_the_batch_injection` is not asserting a
constant: it compares `typer.typed` against `decide_injection(final_text, ...).payload`, and
`decide_injection` is the **one** implementation the batch `inject` uses (`inject` → `decide_injection`
→ `_deliver`), which I confirmed by reading `YdotoolInjector.inject`. Falsification: mutation **M3**
(force `trailing_space=True`) is caught by the `[False]` parametrisation; **M7**
(`type_text` applies `decide_injection`, i.e. a space per raw delta) is caught by the wired
trailing-space test.

---

## 5. The trailing space is per-utterance (item 5)

Confirmed by the same probe: the committed deltas reach `type_text` **raw**
(`['Good morning.', ' Last', ' night storm']`) and exactly one `" "` is appended by `finish`
(`['Good morning.', ' Last', ' night storm', ' ']`). `test_realistic_deltas_do_not_get_a_space_per_commit`
pins the same list, and it is falsified by M7. `type_text` deliberately types a whitespace-only
payload (documented in its docstring) because the trailing space is part of the document; the
whole-utterance whitespace-only rule is applied by `finish`'s `decide_injection`, which the probe
confirmed injects nothing.

---

## 6. No silent downgrade (item 6)

`_validate_injection_backend` is the first statement of `_startup`, so it is reachable through the
real `run_service` path and fires before artifacts are touched. I proved precedence by forcing the
artifact check to a sentinel:

```
$ PYTHONPATH=. uv run python /tmp/t34probe/probe_config_batch.py
run_service exit code: 1
stderr: ges2t: injection.backend = "clipboard" cannot be combined with streaming.enabled = true:
          incremental typing would rewrite the clipboard on every commit.
          hint: set injection.backend = "ydotool" to type as you speak, or set
                streaming.enabled = false to keep the clipboard backend
CONFIRMED: loud startup error names both settings and the fix.

_startup raised: PipelineError - injection.backend = "clipboard" cannot be combined with stream...
CONFIRMED: contradiction is reported before the artifact check.

CONFIRMED: streaming-selected config always gets a live typer factory.
structural backstop raised: injection.backend = "clipboard" cannot be combined with stream...
```

- The message names `injection.backend`, `clipboard`, `streaming.enabled`, and the fix (`ydotool`,
  `streaming.enabled = false`).
- A direct `_build_service` call with a streaming transcriber and a non-`TextTyper` injector raises
  the same `PipelineError` (structural backstop).
- **No silent-fallback path:** `_build_live_typer_factory` returns `None` **only** when
  `_streaming_is_selected` is false, and `_streaming_is_selected` is the same predicate
  `_select_session_kind` uses to choose the streaming session. For an effectively-streaming config
  the factory is never `None`; a `TextTyper` that fails is latched and re-raised by `finish` on the
  service error path (see `test_a_streaming_typing_failure_moves_the_service_to_error`).
- Falsification: **M8** (config check no-op) is caught; **M11** (streaming live typer disabled) is
  caught by 5 tests.

One nuance, not a defect: for `clipboard` + `streaming.enabled = true` with a **non-streaming
engine**, the config-level check still refuses (the user turned the streaming switch on). The ADR's
wording is "clipboard together with `streaming.enabled = true`", so this is consistent.

---

## 7. Regressions — "what did this change stop doing?" (item 7)

- **Batch path byte-identical main vs HEAD.** I ran the same probe against the archived `main` tree
  (`git archive main`, no checkout) and HEAD:

  ```
  $ diff <(grep -v ges2t_file batch_main.json) <(grep -v ges2t_file batch_head.json)
  BATCH PATH IDENTICAL main vs HEAD
  ```

  Both injected `['hello world']` with `calls == ['inject']` for the default config and for
  `streaming.enabled = false`. `_run_finish`'s batch branch is literally the old
  `elif text.strip(): self._injector.inject(text)`.
- **`streaming.enabled = false` and a non-streaming engine** both take the batch path:
  `injected=['hello world'] typed=[]` (probe above).
- **Tray live display content is unchanged.** The service still forwards `live` to
  `status_ui.partial(live)`, and the tray still renders `live.display`
  (`src/ges2t/ui/tray.py:332`). Content, including the pending tail, is unchanged. The only
  behavioural difference is ordering/latency: `type_committed` now runs *before* `status_ui.partial`,
  so a slow typer delays the tray update by the typing cost (~6 ms median; unbounded if wedged).
- **`YdotoolInjector.type_text` did not alter the batched-emit path or its error contract.** It is
  additive and shares `_deliver` verbatim, so `YdotoolNotFoundError`,
  `YdotoolTextUnreachableError`, the unreachable-character warning, and `last_injection_ms` all
  behave the same. It only skips the whitespace-only `SkipInjection` shortcut (by design: the
  caller owns the whole-utterance rule).
- **Test hygiene:** `git diff main...HEAD --stat -- tests/` touches only `tests/fakes.py`,
  `tests/test_incremental_typing.py` (new) and `tests/test_pipeline_streaming.py`; **2** test
  functions were renamed (not deleted) and **25** added; no `skip`/`xfail` was added; and no test
  emits a keystroke, touches the clipboard, or records the microphone (the one real
  `YdotoolInjector` path uses a recording `KeySequenceSource` and a `_NullRunner` that returns a
  `CompletedProcess` without spawning).

---

## 8. ADR 0011 amendment — citation audit (item 8)

Every number is traceable. I checked each against `commit-cadence-diagnostic/001-comment.md` §3/§3.1
and `003-comment.md`, and `batched-key-emission/001-comment.md`.

| ADR figure | traceable source | match |
|---|---|---|
| median 17 chars/commit | ticket 32 real-device §3.1: "median 17.0" | ✅ |
| "(clips 20; p90 34–38, max 63)" | clips median 20.0/p90 34.0; real-device p90 38.4/max 63.0 | ✅ |
| median gap 1.00 s; p90 2.50 s; max 4.00 s | real-device §3.1: 1.00 / 2.50 / 4.00 s | ✅ |
| median `pending` 18 chars, persisting 1.00 s | real-device §3.1: 18 / 1.00 s | ✅ |
| 0 committed-word repeats ("both detectors") | §3.1 "restatements: 0"; 003-comment detector 1 + 2 both 0 | ✅ |
| 0.38 ms/char ⇒ ~6 ms | batched-key-emission/001: 47.9 ms / 126 chars = 0.38; 17 × 0.38 ≈ 6.5 ms | ✅ |
| 25.04 s recorded / 25.00 s fed, 17 commits | ticket 32 §3.1 verbatim | ✅ |
| 3 inputs, 57.0 s of audio, 40 commits total | 003-comment post-R1 totals table (5+19+16 = 40; 5+30+25 = 55–57 s) | ✅ (see note) |
| `Thank you.` in `pending` twice, never committed | §3.1 verbatim | ✅ |
| `Great night.` committed after the pause | §3.1 verbatim | ✅ |

The two caveats the ticket required are both **stated**, not buried:

- **stable hallucination** — "A stable hallucination can now be delivered mid-dictation", with the
  `' Great night.'` instance and the stop → delete → continue recovery; and
- **cancel-safe but not cancel-undoable** — "once a commit has been typed it is in the focused
  window; `cancel` abandons the rest … it cannot remove what was already typed".

The amendment also correctly states what does *not* change (stream decision, ADR 0003 no-fallback,
ADR 0012 rules, batch path). It does **not** overstate the safety of `committed`: it says the
commit rule protects against revision, not against a stable error.

**Honesty findings (non-blocking, §11):** the amendment does not contradict the code, but three
code comments/log lines and one unchanged ADR decision bullet still carry the pre-amendment claim,
and the `~6 ms` figure is emission-only.

---

## 9. Mechanicals (item 9)

```
$ timeout 300 uv run pytest -q
901 passed in 5.68s
PYTEST_EXIT=0                       # the process exited; no exit-124 leaked-thread hang

$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 53 source files
MYPY_EXIT=0
```

- **901 passed**, matching the implementer's claim, and the process **exited** (status 0).
- **mypy 0 errors, 53 files.**
- **New tests:** `tests/test_incremental_typing.py` → 15 passed; all of
  `tests/test_pipeline_streaming.py` → 23 passed.
- No test deleted (2 renamed), none skipped/xfailed, and none emits a real keystroke, touches the
  real clipboard, or records the microphone (§7).

---

## 10. Independent mutations (item 10)

My own harness (`/tmp/t34probe/mutate.py`): one exact byte replacement, `timeout 300 uv run pytest`
on the target tests, then a byte-exact restore and `git status` check after every case. All
replacements were in `src/` and every one was restored.

```
CAUGHT  M1 type whole target instead of delta :: 11 failed, 27 passed in 0.35s
CAUGHT  M2 finish does not reconcile the tail :: 14 failed, 24 passed in 0.39s
CAUGHT  M3 trailing space forced on :: 6 failed, 9 passed in 0.14s
CAUGHT  M7 type_text applies decide_injection (a space per delta) :: 2 failed, 36 passed in 0.26s
CAUGHT  M8 config validation is a no-op :: 1 failed, 22 passed in 0.24s
CAUGHT  M9 streaming path takes the batch inject branch :: 4 failed, 19 passed in 0.26s
CAUGHT  M10 one CommittedTyper reused across recordings :: 1 failed, 22 passed in 0.21s
CAUGHT  M11 streaming live typer disabled (silent downgrade) :: 5 failed, 18 passed in 0.21s

caught 8/8
```

`git status --porcelain` was empty before and after; every mutated file was restored byte-identically.
The implementer's 11/11 claim is therefore consistent with the 8 load-bearing cases I reproduced
independently.

---

## 11. Findings — documentation only, no change requested

These do not affect behaviour or the user's decisions, but the orchestrator should know them before
surfacing the ADR to the user, because they are statements the amendment leaves stale.

1. **ADR decision bullet 4 still says "inject **once**" on release.** The ADR's third design bullet
   was corrected in place, but bullet 4 ("On key release, call `stop()` a final time, append the
   tail, and inject **once**", `0011-streaming-sliding-window.md:64`) was not. The amendment section
   supersedes it explicitly, and the status line points there, so it is not *quiet*, but a reader of
   the decision body alone sees the old rule. A one-line rewording ("type the remaining suffix
   once") would remove the internal contradiction.
2. **`Service`'s shutdown message/docstring still assert the pre-amendment outcome.**
   `src/ges2t/service.py:36` and the warning at `:578-585` say "the loss is a missing transcript,
   never partial text" / "the current utterance was not typed (ADR 0011: a lost utterance, never
   partial text)". On the streaming path committed text *has* been typed by the time a wedged
   finish worker is abandoned, so those lines are now false for streaming (they remain true for
   batch). Also note the warning fires after the 5 s worker-join even though earlier commits are
   already in the focused window.
3. **Tray/status `partial` docstrings say "never typed (ADR 0011)".**
   `src/ges2t/ui/tray.py:331` and `src/ges2t/ui/status.py:91`. The *display* is not what is typed,
   but the same `LiveText.committed` value now is, so "never typed" is misleading.
4. **The `~6 ms` figure is emission-only.** Ticket 23's 0.38 ms/char is the `ydotool key` emission
   cost; the per-commit path also calls `XkbKeySequenceSource.sequence_for`, which rebuilds the
   keymap per commit (disclosed in `001-comment.md` §13 but not in the ADR). The claim "a median
   commit is ~6 ms of typing" is therefore a lower bound, not the full per-commit cost. It does not
   change the conclusion (still cheap), but the ADR should say "emission cost".
5. **The "40 commits total" is from the WAV re-read, not the live run.** `003-comment.md`'s totals
   table sums 5 + 19 + 16 = 40 because the persisted-WAV re-read splits the user recording into 16
   commits, while the authoritative live run in §3.1 had 17. Adding the live 17 to the clips' 24
   gives 41. The ADR's 40 is traceable to the cited artifact, but the sentence mixes the live 17
   with the re-read total; the orchestrator should not treat 40 as a live-run total.

Suggested disposition: fold 1–5 into the same hand-back as the sign-off (they are wording, not
evidence), or land a follow-up docs commit. None blocks merge.

---

## 12. Could not verify

- **The user's live test** — it records the microphone and types into the focused window. The
  script (`user-live-test.sh`) is read-only: it checks prerequisites and prints the procedure and
  never records/types. The observed **lag** is the user's number to report. The
  `XkbKeySequenceSource` per-commit keymap rebuild is included in that lag, which is unmeasured.
- **Real `ydotoold` emission / real focus behaviour / mid-utterance window switching.** As
  designed, the suite goes through the injected seam. No keystroke was emitted and the real
  clipboard was untouched during this review.
- **Real clipboard + streaming.** The combination is refused before it can start, at both the config
  and composition levels; the refusal is what is tested.
- **The engine's real committed cadence.** Ticket 32 measured it and this ticket consumes it, as the
  ticket directs; the `drive()`-level test uses the deterministic `FakeStreamingDecoder`.
- **A real wedged `ydotoold`.** I instrumented the typing seam (the exact boundary a real subprocess
  sits behind), not a real hung daemon; the deadlock question is answered by the lock graph, which
  the probe confirms.
- **A committed value followed by a whitespace-only final transcript.** `finish("")` with a non-empty
  `_typed` latches `IncrementalTypingError` (a non-prefix target), so the service reports an error
  rather than silently dropping text. This is unreachable in a real run (`committed` is a prefix of
  the final segment) and has no dedicated test; I note it as the one invariant edge not pinned.

---

## 13. Merge readiness

- `git status --porcelain` is empty; the worktree is clean; no probe or mutated file was left behind.
- `git merge-tree`/README: the branch changes only ticket 34's row in `docs/project/README.md`
  (`to do`/`—` → `in review`/`feat/incremental-typing`); no adjacent-row conflict is introduced by
  this branch's own change. **The branch is safe to merge.**
- The ADR amendment's new section is honest (numbers traceable, caveats stated, no code
  contradiction). With the five wording items in §11 noted, **it is honest enough for the
  orchestrator to put in front of the user for sign-off.** The user's four decisions are not
  re-opened by the amendment, and the amendment matches the code on every substantive claim.

## Per-DoD checklist

- [x] Committed text injected as committed; only `committed`, never `pending`/`committed + pending`
      (§2, §6; wired pending-poison test).
- [x] Typed watermark: each committed character exactly once, position not delta sum (§3; M1, M10).
- [x] Reconcile against `finish()`, recover the collapsed advance (§2; wired trap + M2).
- [x] Typed document byte-identical to end-injection, one trailing space (§4; two-path probe).
- [x] Trailing space is a whole-utterance rule (§5; raw deltas + one final space; M7).
- [x] Injection does not block the recording loop / command loop; shutdown bounded (§1, measured).
- [x] Batch path and `streaming.enabled = false` unchanged (§7; main-vs-HEAD diff).
- [x] Config validation is a loud startup error reachable through `run_service` (§6; M8, M11).
- [x] ADR 0012 rules unchanged (verbatim, one space, whitespace-only → nothing) (§4, §5).
- [x] ADR 0011 amended and surfaced; numbers traceable; caveats stated (§8).
- [x] `uv run pytest` (901, exit 0) and `uv run mypy src/ges2t tests` (0 errors, 53 files) green
      (§9).
- [ ] User's live test — user-owned, prepared, not run by the reviewer (correct by design; §12).

## Bottom line

**PASS — safe to merge.** The feature types only the append-only committed prefix, exactly once,
reconciles the dedup trap against `finish()`, and produces a document byte-identical to the batch
path. The "one lock across typing" claim is correct but harmless: it is `CommittedTyper._lock`, not
the service's state lock, and the command loop answered `status`/`toggle`/`cancel` in under a
millisecond while the typer was blocked. The ADR amendment is honest and evidence-backed; the only
request is the non-blocking wording cleanup in §11.
