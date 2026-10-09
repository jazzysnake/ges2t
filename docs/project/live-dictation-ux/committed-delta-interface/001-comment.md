# 001 — committed-delta-interface: the representation, the fold, and the falsification

- **Ticket:** [committed-delta-interface](ticket.md) (ticket 33 — the interface change ticket 34 depends on)
- **Branch:** `feat/committed-delta-interface`
- **Worktree:** `.worktrees/committed-delta-interface`
- **Date:** 2026-10-09
- **Status:** in review. `uv run pytest` → **776 passed**; `uv run mypy src/ges2t tests` → **0 errors**.
- **Behaviour:** no observable change. Nothing is typed during dictation; the tray tooltip shows the
  same bytes it showed before.

## 1. Headline

`TranscriptionSegment` no longer carries one merged `str`. It carries a
`LiveText(committed, pending)` value object, and `drive()` populates `committed` with the
**cumulative** committed prefix — the running transcript, not the per-pass delta. The streaming
session, the service and the `StatusUI` protocol all forward the whole `LiveText`, so the committed
value survives to the UI layer intact. The display string is derived
(`LiveText.display == committed + pending`) and is byte-for-byte the old `transcript + pending`.

Because the boundary is now explicit, ticket 34 can type `committed` without inferring it from two
successive partials — which the ticket correctly notes is impossible to do safely.

## 2. The representation, and why

Chosen in `src/ges2t/interfaces.py`:

```python
@dataclass(frozen=True)
class LiveText:
    committed: str   # cumulative, append-only; the final segment's value is the transcript
    pending: str     # replaced every pass; display-only

    @property
    def display(self) -> str:
        return self.committed + self.pending


@dataclass(frozen=True)
class TranscriptionSegment:
    live: LiveText
    language: str | None
    is_final: bool
    pass_ms: float | None

    @property
    def text(self) -> str:      # keeps every existing read site working
        return self.live.display
```

Two decisions worth naming:

- **A dedicated class, not a second `str` field.** A `TranscriptionSegment(text: str, committed:
  str, ...)` has two same-typed fields whose meanings differ by one word, which is exactly the
  confusion ADR 0019 warns about. `LiveText` names the two halves and derives the display string, so
  a caller cannot accidentally pass the display where the committed value is wanted — the type
  differs.
- **`live` is the field and `text` is a property.** This keeps every existing `segment.text` read
  (the session, the tests, the whistle adapter tests) compiling and returning the identical string.
  The only signature change is at construction, where the split is the point.

`LiveText` lives in `interfaces.py` (stdlib-only, ADR 0010); it pulls in no backend or GUI.

## 3. Delta versus cumulative

This was the ticket's stated trap, and it is handled explicitly in `_drive()`:

```python
transcript = ""          # cumulative committed prefix, only ever grows
pending = ""
...
for group in _group_chunks(...):
    pass_ = decoder.process(group)
    transcript += pass_.committed      # pass_.committed is a per-pass DELTA; folded in
    pending = pass_.pending            # replaced, not folded
    ...
    live = LiveText(committed=transcript, pending=pending)   # CUMULATIVE on the segment
    yield TranscriptionSegment(live=live, is_final=False, ...)

transcript += decoder.flush()
yield TranscriptionSegment(live=LiveText(committed=transcript, pending=""), is_final=True, ...)
```

- `StreamingPass.committed` stays a **delta** at the decoder seam (unchanged, ADR 0011).
- The value a consumer sees on a segment is **cumulative** for both partials and the final segment,
  so the sequence is append-only and the final segment needs no special case: its `committed` is the
  same accumulator, and its `pending` is empty.
- The final segment is therefore *not* built from a delta and the partials are *not* display strings.
  There is exactly one accumulator (`transcript`) and one place it reaches the consumer, so a naive
  "type each delta then type the final cumulative value" duplicate is impossible to introduce
  *through this interface* — `committed` is one monotonically growing string, which is precisely
  what ticket 34's typed watermark diffs against.

## 4. Proof the display string is unchanged

The pre-change code emitted:

```python
text = transcript + pending
yield TranscriptionSegment(text=text, ...)
```

The new code emits `LiveText(committed=transcript, pending=pending)` and `segment.text` is
`self.live.committed + self.live.pending`. The two operands are the same two variables with the same
values at the same point in the loop, so the concatenation is identical by construction. The final
segment was `text=transcript` and is now `LiveText(committed=transcript, pending="")`, whose display
is also `transcript`.

Tests that pin this rather than assert it by hope:

- `tests/test_streaming.py::test_display_string_is_byte_for_byte_the_pre_split_output` — the display
  sequence equals the FakeStreamingDecoder's `available_history` (the words-revealed-so-far string
  the old merged code produced), and `text == committed + pending` for every segment.
- `tests/test_status_ui.py::test_tray_partial_renders_committed_plus_pending_byte_for_byte` — feeding
  `LiveText(committed="the quick ", pending="brown fox")` yields the exact tooltip
  `"ges2t: recording\nthe quick brown fox"`.

## 5. Falsification evidence

Each new/changed test was proved able to fail by breaking the behaviour it guards, then restoring
(restoration verified with `git diff` being empty and the suite green). Harness: a throwaway script
that applied one targeted string replacement to a tracked file, ran only the target tests, and
restored with `git checkout -- <file>` in a `finally`.

| # | mutation (what was broken) | target test(s) | observed |
|---|---|---|---|
| M1 | `drive` exposes the per-pass **delta**: `LiveText(committed=pass_.committed, ...)` | (a),(b),(c),(d) | all four FAILED |
| M2 | `drive` merges the tail: `committed=transcript + pending, pending=pending` | (c),(d) | FAILED (the display double-counts the tail) |
| M2b | `drive` merges and empties the tail: `committed=transcript + pending, pending=""` | (a),(d) | FAILED |
| M3 | the final segment's `committed` is `""` (delta only) | (b) | FAILED |
| M4 | `TranscriptionSegment.text` returns `committed` only | (c) | FAILED |
| M5 | `LiveText.display` returns `committed` only | `LiveText` unit test, tray tooltip, (c) | all FAILED |
| M6 | session drops the split: stores `LiveText(committed=segment.text, pending="")` | session-preserved test, service-exposes test | both FAILED |
| M7 | session dedups on `committed` instead of `display` | dedup test | FAILED |
| M8 | service merges before forwarding to the UI | service-exposes test | FAILED |
| M9 | tray renders `committed` only | tray tooltip test | FAILED |

Node ids (all in the worktree):

- (a) `tests/test_streaming.py::test_committed_is_cumulative_and_append_only_across_segments`
- (b) `tests/test_streaming.py::test_final_segment_is_cumulative_and_reaches_the_whole_transcript`
- (c) `tests/test_streaming.py::test_display_string_is_byte_for_byte_the_pre_split_output`
- (d) `tests/test_streaming.py::test_a_change_confined_to_pending_does_not_advance_committed`

**No new test passes before the change.** They all reference `segment.live` / `LiveText`, which did
not exist before this ticket, so they cannot even be collected against the pre-change tree. The two
pre-existing tests that were edited (`test_partials_are_yielded_after_each_group_and_match_committed_plus_pending`,
`test_partials_advance_and_consecutive_duplicates_are_dropped`) were only adapted to the new types;
the *new* invariants are in the tests above, not in those.

Two honest caveats about the matrix:

- M2 (merged, tail kept) does not break (a) or (b): because `FakeStreamingDecoder`'s display is
  itself monotone, a merged `committed` is still "append-only", and the concatenation of its deltas
  still reaches the final text. It *is* caught, by (c) and (d). The realistic merge — emptying the
  tail into `committed` — is M2b, and (a)/(d) catch it.
- (b) catches the delta bug (M1) and the empty-final bug (M3), not the merge; that is its intended
  scope (final value equals the concatenation of the committed parts).

## 6. No injection change

The service still injects exactly once, after `session.finish()`, and only if the final text is not
whitespace (`Service._run_finish`). The partials thread calls only `status_ui.partial(live)`.
`grep -rn "committed" src/ges2t` finds no injector reference: the committed value is *available* at
the UI layer and is used by nothing yet. Ticket 34 is the ticket that acts on it.

## 7. Interface surface changed (for ticket 34)

```python
StatusUI.partial(live: LiveText) -> None            # was partial(text: str)
DictationSession.partials() -> Iterator[LiveText]   # was Iterator[str]
BatchDictationSession.partials() -> Iterator[LiveText]
TranscriptionSegment.live: LiveText                 # new; .text is now a property
drive(..., on_partial: Callable[[LiveText], None] | None = None)
```

Ticket 34 should consume `segment.live.committed` from `partials()` (cumulative, append-only) plus
the final segment's `live.committed`, and diff each against a typed watermark. It must **not** type
`live.display`. It should also be aware that the session dedups consecutive partials on
`.display` (unchanged pre-existing rule), so two segments whose display is identical but whose
committed advanced are collapsed to one — the cumulative value caught up at the next advancing
segment or at the final segment, so a watermark over `committed` stays exactly-once either way.

## 8. What could not be verified

- **The tray tooltip against a real display.** The tooltip test uses the injected `_FakeIcon`; the
  `pystray`/AppIndicator path was not run (no display in this environment). The string handed to the
  icon is verified; the OS rendering is not.
- **A real end-to-end dictation.** This ticket changes no injection, and the microphone was not
  recorded (hard rule), so there is no live observation to report. The evidence is the unit/session/
  service tests above.
- **The engine's own `committed`/`pending` values from `WhistleTranscriber.stream()`.** The
  `drive()`-level tests use the deterministic fake decoder; ticket 32 already measured the real
  engine's commit cadence, and this ticket does not re-measure it. Nothing in this change alters the
  adapter's decoder seam, so that measurement is unaffected.
