# 001 — incremental-typing: the payload, the watermark, and the ADR 0011 amendment

- **Ticket:** [incremental-typing](ticket.md) (ticket 34, the payload of the incremental-typing chain)
- **Branch:** `feat/incremental-typing`
- **Worktree:** `.worktrees/incremental-typing`
- **Date:** 2026-10-09
- **Status:** in review. `uv run pytest` → **901 passed**, exit 0; `uv run mypy src/ges2t tests`
  → **0 errors** (53 files).
- **⚠️ This ticket amends [ADR 0011](../../../adr/0011-streaming-sliding-window.md).** The full
  amendment is in §7; the orchestrator must surface it to the user (the user escalates on ADR
  changes and expects the measured evidence attached).

## 1. Headline

During a streaming dictation the **committed** prefix — the words the engine agreed on twice — is
now typed **as it is committed**, instead of nothing until release. Only `committed` is typed,
never `pending` and never `committed + pending`. A **typed watermark** over the cumulative
committed string types each character exactly once, and `finish()` **reconciles** against the final
transcript, which recovers a collapsed `partials()` advance. ADR 0012's trailing space is a
whole-utterance rule: committed deltas are typed raw, and exactly one space is applied once at the
end. The batch path is untouched; `streaming.enabled = false` behaves exactly as before.

Measured facts this builds on (ticket 32, not re-derived here): median **17 characters per commit**,
one commit per **1.00 s** of source audio (p90 2.50 s, max 4.00 s), `pending` median 18 characters
persisting ~1.00 s, and ticket 23's **0.38 ms/char**, so a median commit is ~6 ms of typing. Speed
is not the risk; locking and correctness are.

## 2. What was built, and where

New module `src/ges2t/incremental_typing.py`:

- `CommittedTyper` — the typed watermark and the reconciliation. Its state is
  `_typed: str`, the exact document typed so far (a prefix of the eventual
  `final_text + trailing space`). It is a **position in the cumulative string**, never a sum of
  deltas: `type_committed(c)` types `c[len(_typed):]` and advances `_typed = c`;
  `finish(final_text)` assembles the final document with `decide_injection` (ADR 0012's one
  implementation) and types only the suffix beyond the watermark.
- `IncrementalTypingError` — the invariant violation (a non-append-only committed value), raised at
  `finish`.
- One `threading.Lock` serialises `type_committed` and `finish` and is held **across the typing
  call**, so two callers cannot both claim the same suffix and the order is deterministic. A
  delivery failure is **latched** and re-raised by `finish`, so it reaches the service error path
  instead of being swallowed by the display thread.

New seams in `src/ges2t/interfaces.py`:

- `TextTyper.type_text(text)` — types an exact payload verbatim (no trailing-space rule). Implemented
  by `YdotoolInjector.type_text`; deliberately **not** implemented by the clipboard backend.
- `LiveTyper.type_committed(committed)` / `finish(final_text)` — the service-facing seam that
  `CommittedTyper` implements.

Wiring:

- `src/ges2t/pipeline.py` — `_build_live_typer_factory` builds a **fresh** `CommittedTyper` per
  recording (a single instance would exhaust its watermark after the first utterance, see M10), and
  `_streaming_is_selected` keeps the session choice and the typer choice on the same capability
  rule. `_build_service` passes the factory to `Service`.
- `src/ges2t/service.py` — `Service` takes an optional `live_typer_factory`. `_commit_start` builds
  the per-recording typer; the partials-forwarding thread calls `live_typer.type_committed(live.committed)`;
  the finish worker calls `live_typer.finish(text)`. `live_typer is None` leaves the batch path
  byte-for-byte as it was.

## 3. The trailing-space split (whole-utterance rule)

Real committed deltas arrive as `'Good morning.'`, `' Last'`, `' night storm …'` — the later ones
already carry their own leading separator. Typing each delta through the ordinary trailing-space
path would insert one space per commit. The split is therefore:

- **per commit:** type the committed delta **raw** (`YdotoolInjector.type_text`, no `decide_injection`);
- **once, at `finish()`:** assemble `final_text + " "` (when `ui.trailing_space`) with
  `decide_injection` and type only the suffix beyond the watermark. A final remainder of only the
  trailing space is still typed; a whitespace-only utterance assembles to `""` and types nothing.

Byte-for-byte evidence is §5 and the falsification row for `test_realistic_deltas_do_not_get_a_space_per_commit`.

## 4. Reconciliation against `finish()`, not just `partials()`

`StreamingDictationSession.partials()` dedups on the **display** string (ticket 33's review, §3), so
a pass where `committed` grows by exactly what `pending` shrinks is collapsed and never yielded. A
consumer that only iterates `partials()` loses the tail of every utterance that ends on a collapsed
advance (`partials()` alone types `abc`, `partials()+finish()` types `abcdef`). `CommittedTyper`
consumes `partials()`'s `committed` values and then **reconciles against `finish()`**, so the tail
is recovered. The windowing/threading guarantee is stated in the module docstring: the lock is held
across the typing call, capture never touches it, and shutdown stays bounded because the service
joins its workers with a timeout.

The service hands the finish worker the typer bound to that recording (not the mutable field), so a
queued start cannot swap it out mid-finish.

## 5. The byte-identical-document evidence

This is the load-bearing acceptance criterion. Three independent tests pin it:

| test | what it compares |
|---|---|
| `tests/test_incremental_typing.py::test_typed_document_is_byte_identical_to_the_batch_injection[True]` / `[False]` | `CommittedTyper.typed == decide_injection(final_text, trailing_space=...).payload`, parametrised over `ui.trailing_space` |
| `tests/test_incremental_typing.py::test_drive_output_reconciles_to_the_batch_document` | the real `drive()` fold's committed values plus the final segment produce `" ".join(WORDS_68) + " "` |
| `tests/test_pipeline_streaming.py::test_wired_streaming_path_types_the_byte_identical_document` | the wired service types `"Good morning. Last night storm "` from the real partial/final sequence |

The trailing-space rule is additionally pinned by
`test_realistic_deltas_do_not_get_a_space_per_commit` (single interior separators, exactly one
trailing space, and the exact raw payload list `['Good morning.', ' Last', ' night storm', ' ']`),
and ADR 0012's whitespace rule by
`test_whitespace_only_final_utterance_types_nothing` plus the wired whitespace test.

## 6. Per-test falsification evidence

Harness: `/tmp/mutate_ticket34.py` — apply one exact string replacement to a tracked file, run the
target tests with a 300 s timeout, restore with the original bytes, and confirm
`git diff --quiet` on the mutated file. **11/11 mutations were caught, and every mutated file
restored clean.** Full output: `/tmp/ticket34-mutations.txt`.

| # | mutation (what was broken) | representative target caught |
|---|---|---|
| M1 | `_extend` types the whole `target` instead of the `delta` (duplicates) | `test_wired_streaming_path_recovers_a_collapsed_advance_from_finish` (11 failed) |
| M2 | `finish` does not reconcile the tail (`self._extend(target)` removed) | `test_a_collapsed_advance_is_recovered_from_finish` (14 failed) |
| M3 | trailing space forced on, ignoring `ui.trailing_space` | `test_typed_document_is_byte_identical_to_the_batch_injection[False]` (7 failed) |
| M4 | `type_committed`'s `_finished` short-circuit removed | `test_a_late_partial_after_finish_is_a_noop` |
| M5 | the append-only check in `_extend` neutered | `test_a_non_append_only_value_stops_typing_and_finish_raises` |
| M6 | a delivery failure is swallowed instead of latched | `test_a_delivery_failure_is_latched_and_reraised_by_finish` |
| M7 | `YdotoolInjector.type_text` applies `decide_injection` (a space per raw payload) | `test_wired_streaming_path_applies_the_trailing_space_rule[True]` |
| M8 | config validation becomes a no-op | `test_clipboard_with_streaming_is_a_loud_actionable_error` |
| M9 | the streaming path takes the batch `injector.inject` branch | `test_wired_streaming_path_recovers_a_collapsed_advance_from_finish` (4 failed) |
| M10 | one `CommittedTyper` reused across recordings | `test_each_streaming_recording_gets_a_fresh_watermark` |
| M11 | the streaming live typer is disabled (silent downgrade to batch injection) | `test_wired_streaming_path_types_the_byte_identical_document` (5 failed) |

**No new test passes before and after a mutation in the table.** All 15 `test_incremental_typing.py`
tests and all 11 new `test_pipeline_streaming.py` tests are covered (plus the pre-existing
parametrised trailing-space test, which now exercises the live `type_text` path — M7). Two tests are
deliberately *not* individually discriminating and are declared so:

- `test_finish_is_idempotent` — a second `finish()` is a no-op because the watermark already equals
  the target, so removing the `_finished` guard alone does not retype. It is kept because it pins the
  documented no-op outcome; the discriminating idempotence guard is
  `test_a_late_partial_after_finish_is_a_noop` (M4).
- `test_a_change_confined_to_pending_types_nothing` — the caller never passes `pending` (only
  `live.committed` is wired), so this pins the *unit's* contract rather than a path a mutation in the
  service can reach. The service-level "never pending" pin is
  `test_wired_streaming_path_types_committed_text_and_never_the_pending_tail` (M9).

## 7. ADR 0011 amendment (full text of what changed)

The ADR was edited, not quietly superseded. Three edits:

1. **Status line** now reads: `accepted — amended 2026-10-09 by ticket incremental-typing (see the
   Amendment at the end: the committed prefix is now typed as it is committed; the original "nothing
   is injected until release" rule was broader than its own reason)`.
2. **The decision bullet is corrected in place.** It now reads: *"Partial text is display-only; the
   committed prefix is typed as it is committed. `committed + pending` may be shown as live status
   (tray/notification) and is never injected. … The original rule ('nothing is injected until
   release') was broader than its own stated reason: typing `committed + pending` would indeed
   duplicate and race with the committed-text revisions, but that objection does **not** apply to
   `committed` alone, which is exactly the part that has stopped changing."*
3. **A new section is appended**, reproduced verbatim below.

> ## Amendment 2026-10-09 — inject the committed prefix as it is committed
>
> **Superseding decision (ticket `incremental-typing`, the payload of the incremental-typing
> chain):** during a streaming dictation, ges2t injects the words the engine has **committed**
> **as they are committed**, instead of nothing until release. The injected text is only the
> `committed` prefix — never `pending`, never `committed + pending`. A typed watermark over the
> cumulative committed string guarantees each committed character is typed exactly once, and the
> final reconciliation against `finish()` recovers any advance the deduplicated partials skipped.
> ADR 0012's trailing-space rule is applied **once at the end of the utterance**, not per commit.
>
> ### Why the original reasoning did not require the original rule
>
> The original decision rejected typing during dictation because *"typing partials into the
> focused window would duplicate and race with the committed-text revisions"*. That reason is
> sound for the value the pipeline surfaced at the time — `committed + pending` — because
> `pending` is replaced on every pass and is where the engine's tentative words live. It does
> **not** apply to `committed` alone: `committed` is the part the engine has already agreed on
> twice and is append-only (ticket `committed-delta-interface` made that boundary explicit on
> `LiveText`). Typing only `committed` therefore cannot require a backspace. **The rule was
> broader than its own reason required, and this amendment narrows it to the reason.**
>
> ### The measured evidence
>
> Commit cadence, from ticket 32's real-device run (`commit-cadence-diagnostic`, 25.04 s recorded
> / 25.00 s fed, 17 commits through the real `pw-record` path, with a deliberate mid-utterance
> pause), folded in with the shipped clips (3 inputs, 57.0 s of audio, 40 commits total):
>
> | measurement | result |
> |---|---|
> | median characters per commit | **17** (clips 20; p90 34–38, max 63) |
> | median gap between commits, source audio | **1.00 s** (p90 2.50 s, max 4.00 s) |
> | median `pending` size | **18 characters**, persisting **1.00 s** |
> | committed deltas that repeat already-committed words | **0** (0 with 2+ words, both detectors) |
> | injection cost | ticket 23 measured **0.38 ms/char**, so a median commit is **~6 ms** of typing |
>
> The measured cost is cheap enough that the real risk is **locking**, not speed, and the median
> commit is small enough that typing it as it arrives keeps up with speech. Two observations from
> the real recording shape the caveat below:
>
> - the `Thank you.` hallucination appeared in `pending` **twice** and was **never committed**, so
>   committed-only typing types none of it — this is the design's best evidence; and
> - the deliberate mid-utterance pause produced a **committed** `' Great night.'` that nothing
>   later contradicted. The two-pass commit rule protects against *revision*, not against a
>   *stable hallucination*.
>
> ### Consequences accepted with the change (user decisions, not open questions)
>
> - **A stable hallucination can now be delivered mid-dictation.** The `' Great night.'` case
>   above is the concrete instance. The user accepted this and its recovery: **stop the recording,
>   delete the wrong text, and continue** — which is why the stop path stays instant and the tool
>   deliberately attempts **no** auto-correction.
> - **Focus must hold for the whole utterance.** Committed text lands wherever focus is at each
>   commit; the user does not consider mid-utterance window switching a real usage pattern.
> - **Interleaving with the user's own typing is out of scope** and is a documented limitation.
> - **The clipboard backend is incompatible with streaming**: incremental paste would rewrite the
>   user's clipboard on every commit. The composition root refuses `injection.backend =
>   "clipboard"` together with `streaming.enabled = true` with a loud, actionable error naming both
>   settings and the fix (set `ydotool`, or disable streaming).
> - **The streaming change is cancel-safe but not cancel-undoable:** once a commit has been typed it
>   is in the focused window; `cancel` abandons the rest of the utterance and types nothing further,
>   it cannot remove what was already typed. This is the same accepted risk as the hallucination.
>
> ### What does *not* change
>
> - ADR 0011's decision to **stream** (5 s sliding window, commit on `stop()`) is unchanged.
> - ADR 0003's **no automatic fallback** rule still holds; the clipboard/streaming refusal is
>   exactly that rule applied to a new configuration contradiction.
> - ADR 0012's text rules are unchanged: otherwise verbatim, exactly one trailing space when
>   `ui.trailing_space` is enabled, and whitespace-only input injects **nothing**.
> - The **batch** path and `streaming.enabled = false` behave exactly as before.
> - Language, timestamps, and the window/chunk configuration are untouched.

## 8. Config validation decision

**`injection.backend = "clipboard"` + `streaming.enabled = true` is a hard startup error, not a
warning.** `_validate_injection_backend` runs first in `_startup` (before artifacts) and raises
`PipelineError`; `_build_live_typer_factory` raises the same error structurally when a streaming
session is paired with an injector that is not a `TextTyper`. The message reaches the user through
`run_service`, which prints both setting names and the fix and returns exit code 1
(`test_run_service_reports_the_clipboard_streaming_conflict`).

Justification:

- The clipboard backend cannot type incrementally at all: its smallest operation is "paste the whole
  selection", so incremental paste would rewrite the user's clipboard on every commit — the
  mechanism the user explicitly rejected (ticket decision #4). A warning would let the service run
  and either silently drop incremental typing or silently corrupt the clipboard.
- ADR 0003 forbids a silent mechanism switch. "Degrade either setting quietly" is exactly what this
  rule is meant to prevent.
- The fix is cheap and the message names both settings and the fix:
  `injection.backend = "clipboard" cannot be combined with streaming.enabled = true: incremental
  typing would rewrite the clipboard on every commit.` with the hint
  `set injection.backend = "ydotool" to type as you speak, or set streaming.enabled = false to keep
  the clipboard backend`.
- With `streaming.enabled = false` (or an engine that cannot stream) the combination is allowed and
  the batch path runs unchanged; the structural check only fires for an effective streaming session.

## 9. Batch path and `streaming.enabled = false` are unchanged

`Service`'s batch branch is literally the old code: `if live_typer is not None: ... elif
text.strip(): self._injector.inject(text)`. When no factory is configured (every existing
`Service(...)` call site, and every batch service), `live_typer is None` and the path is identical.
`test_clipboard_with_streaming_disabled_keeps_the_batch_injection` and the existing
`test_wired_batch_path_produces_and_injects_the_core_epic_text` pin it; `_validate_injection_backend`
does not refuse the combination when streaming is off.

## 10. The stable-hallucination caveat, stated (not a surprise)

Ticket 32's real recording committed `' Great night.'` after a deliberate mid-utterance pause,
because nothing later contradicted it. Incremental typing **will deliver that class of
mid-dictation**: the commit rule filters *revision*, not *stable error*. The user's accepted
recovery is **stop → delete → continue** (their decision #2), which is why the stop path stays
instant and why this tool intentionally performs **no** auto-correction. This is stated in the ADR
amendment and in the user live-test script.

**Not built: a focus-moved guard.** The ticket asked to consider one and record why if omitted. We
omit it: there is no reliable stdlib-only, dependency-free way to observe the focused window on
Wayland/KDE, and a guard that guessed wrong would silently *drop* committed text — a worse failure
than the focus-hold behaviour the user explicitly accepted (decision #1). The behaviour is instead
documented.

## 11. Mechanicals (real output)

```
$ timeout 300 uv run pytest -q
901 passed in 5.64s
PYTEST_EXIT=0                       # the process exited; no exit-124 leaked-thread hang

$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 53 source files
MYPY_EXIT=0
```

The caches were cleared before mypy so a reverted mutation cannot look fixed.

## 12. Definition-of-done status

| ticket DoD item | status |
|---|---|
| committed text injected as committed; only `committed`, never `pending`/`committed+pending` | done (§2, §4; M9, and the wired pending-poison test) |
| typed watermark: each committed character exactly once | done (§2; `test_types_each_committed_character_exactly_once`, `test_repeated_cumulative_values_are_not_retried`) |
| reconcile against `finish()`, recover the collapsed advance | done (§4; `test_a_collapsed_advance_is_recovered_from_finish`, the wired collapsed test; M2) |
| typed document byte-identical to end-injection (one trailing space) | done (§5; three tests, parametrised over `ui.trailing_space`) |
| trailing space is a whole-utterance rule | done (§3; `test_realistic_deltas_do_not_get_a_space_per_commit`, M7) |
| injection never blocks the recording loop | done (§2, §4): typing runs on the partials thread, capture never takes the typer lock, shutdown joins with a timeout |
| batch path unchanged; `streaming.enabled = false` unchanged | done (§9) |
| config validation: clipboard + streaming is loud and actionable | done (§8; M8, and the structural test) |
| ADR 0012 rules unchanged (verbatim, one space, whitespace-only → nothing) | done (§5; whitespace tests) |
| user's live test prepared (user-owned, not run) | done (§13; `user-live-test.sh`) |
| `uv run pytest` + `uv run mypy src/ges2t tests` green | done (§11) |
| ADR 0011 amended and surfaced explicitly | done (§7, and this artifact's header) |

## 13. Could not verify — and the user's live test

- **The user's live test was not run.** It records the microphone and types into the focused window;
  the implementer must not do either. The script is prepared at
  `docs/project/live-dictation-ux/incremental-typing/user-live-test.sh`; the observed **lag between
  speaking and text appearing** is the user's number to report.
- **Real `ydotoold` emission.** Every test goes through the injected seam (a recording `TextTyper`
  or a `KeySequenceSource` double); no test emits a keystroke.
- **Per-commit layout resolution cost.** The raw typing path calls
  `XkbKeySequenceSource.sequence_for` per commit, which resolves the live layout and compiles the
  keymap each time (correct per ADR 0020, but more work than one call per utterance). Ticket 32's
  0.38 ms/char is the emission cost, not the keymap rebuild; the total lag is unmeasured until the
  user's live test. It runs off the capture loop either way.
- **Real focus behaviour / mid-utterance window switching.** Accepted as a documented limitation
  (decision #1); no automated test can exercise it headlessly.
- **Clipboard + streaming over a real `wl-copy`.** The refusal is tested at the config and
  composition levels; the real paste path is irrelevant because the combination never starts.
- **The engine's real committed cadence again.** Ticket 32 measured it; this ticket consumes it and
  does not re-measure. The `drive()`-level test uses the deterministic `FakeStreamingDecoder`.

## 14. Files changed

```
src/ges2t/incremental_typing.py     (new)  the watermark and reconciliation
src/ges2t/interfaces.py                    TextTyper + LiveTyper protocols
src/ges2t/injectors/ydotool.py             YdotoolInjector.type_text (raw primitive)
src/ges2t/service.py                       optional per-recording live typer; finish reconciliation
src/ges2t/pipeline.py                      live-typer factory + clipboard/streaming validation
tests/test_incremental_typing.py    (new)  the watermark unit tests
tests/test_pipeline_streaming.py           wired, byte-identical, config and lifecycle tests
tests/fakes.py                             FakeInjector.type_text/typed
docs/adr/0011-streaming-sliding-window.md  the amendment (status, in-place correction, new section)
docs/project/live-dictation-ux/incremental-typing/ticket.md      status -> in review
docs/project/live-dictation-ux/incremental-typing/001-comment.md this artifact
docs/project/live-dictation-ux/incremental-typing/user-live-test.sh (new) the user's script
docs/project/README.md                     only ticket 34's row
```

## 15. §11 wording cleanup (hand-back from `002-review.md` §11)

Applied after the review's PASS, as the review's non-blocking findings (§11) requested. All five
changes are documentation/comments only: no logic, no test, and no assertion was touched. Each
addresses a statement the ADR 0011 amendment left stale (the pre-amendment "nothing is injected
until release" rule).

| # | §11 finding | correction made |
|---|---|---|
| 1 | ADR decision bullet 4 still said the tail is injected **once** on release | `docs/adr/0011-streaming-sliding-window.md:64` now says to type the remaining suffix once, because the committed prefix was already typed as it arrived (points at the amendment). |
| 2 | `Service` shutdown docstring and warning asserted "never partial text" / "nothing was injected" | `src/ges2t/service.py` docstring and the shutdown WARNING now say the batch path injects nothing, while the streaming path has already typed the committed prefix, so only the uncommitted remainder is lost. |
| 3 | Tray/status `partial` docstrings said the text is "never typed" | `src/ges2t/ui/tray.py` and `src/ges2t/ui/status.py` now say the ``display`` (committed + pending) is never typed, while the committed prefix is typed as committed (ADR 0011 as amended). |
| 4 | The `~6 ms` figure is emission-only and excludes the keymap rebuild | `docs/adr/0011-streaming-sliding-window.md` amendment table now labels 0.38 ms/char the `ydotool key` emission cost, names the excluded `XkbKeySequenceSource.sequence_for` rebuild, and calls ~6 ms a lower bound. `src/ges2t/incremental_typing.py`'s docstring carries the same qualification. |
| 5 | "40 commits total" is the WAV re-read total, not the live run | `docs/adr/0011-streaming-sliding-window.md` now labels 40 as the persisted-WAV re-read total (5 + 19 + 16) and notes the authoritative live run had 17 (clips + live = 41). |

The two stale statements that remain are in **test** comments
(`tests/test_state_machine.py:637-638`, `:657-658`); correcting them would mean editing a test
file, so they were left untouched. The review cited only `src/` and the ADR, and the test comment
claims do not affect behaviour.

