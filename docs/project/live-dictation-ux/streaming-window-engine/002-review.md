# 002 — Review: streaming window engine (generic windowed-commit driver)

- **Ticket:** [ticket.md](ticket.md) (streaming-window-engine)
- **Branch:** `feat/streaming-window-engine` @ `b25ca55`
- **Commits reviewed:** `17755f1` (implementation), `b25ca55` (pre-review language fix)
- **Implementer notes reviewed:** [001-comment.md](001-comment.md) — claims checked, not trusted
- **Spec:** [ADR 0011](../../../adr/0011-streaming-sliding-window.md),
  [ADR 0010](../../../adr/0010-transcriber-interface-design.md),
  [ADR 0019](../../../adr/0019-code-style-typing-and-interfaces.md) (as amended 2026-10-09)
- **Reviewer:** independent review run; `git status` clean at start and end; every probe reverted

## Verdict

**pass** — no blocking findings.

The seam property holds under my own independent construction (30 s / 5 s window / 68 words →
exactly 68 tokens, no duplicate, no loss, and the final string is the verbatim
event-ordered concatenation of every `committed` delta plus every `flush()` return). Boundary
accounting is sample-based and the off-by-one is correct (`>=`): 30 s of 1 s chunks at a 5 s
window produces exactly 7 flushes (6 boundary + 1 final). The language fix is correct and
additive. The ADR 0019 bar holds for the two changed files. The mutation probes confirm the
tests are honest. Four non-blocking risks/follow-ups are recorded below (R1–R4).

One thing the orchestrator must read in full: **the wider ADR 0019 command fails on a file
outside this ticket's scope** (`tests/test_whistle_config.py`), which is the exact pre-existing
gap the 2026-10-09 amendment and ticket 20 exist to close (R1). It is not a defect introduced
by this branch.

## Per-criterion findings

### 1. The seam guarantee — pass

I did not trust the implementer's test. I wrote an **independent oracle decoder**
(`/tmp/probe_streaming.py`, since removed) that reveals words at a fixed rate, keeps the newest
revealed word `pending`, commits everything before it, and commits the tail on `flush()`. It
logs every event (deltas and flushes in call order).

Evidence (30 s of 1 s chunks, 5 s window, 68 distinct `w0..w67`):

```
final.text == expected (exact string): True
verbatim "".join(event-ordered deltas+flushes) == final.text: True
token count: 68 unique count: 68
expected token count: 68
dups: []
missing: []
is_final: True n partials: 30
flush count: 7
```

So the final text is byte-for-byte the concatenation the driver promises, and it equals the
decoder's known-perfect output. The ticket's "no word duplicated or lost at a seam" property is
confirmed by construction, not by re-running the implementer's assertion.

**Adversarial decoders — what `drive()` actually does.** Each case below was run against a
conforming driver; the descriptions are what the *driver* then does.

| Adversarial decoder | `drive()` behaviour | Reading |
|---|---|---|
| A. `committed` always `""`, `pending` carries real text, `flush()` commits the tail | Final text complete (`word1 word2 word3`); partials show the growing pending | Correct. Nothing lost; the decoder chose to commit only at flush |
| B. `committed` always `""` **and** `flush()` returns `""` while `pending` is non-empty | **Text silently dropped**: partials show `word1 word2 word3`, final is `''` | Decoder contract violation; see R2 |
| C. `committed` is cumulative (repeats all text so far each pass) | **Text silently duplicated** (`word1 word1 word2 …`) | Decoder contract violation; see R2 |
| D. `pending` shrinks / grows erratically (committed is a clean delta) | Final text unaffected (`c1 c2 c3 tail`); partials non-monotone (`c1 PPPPPPPPP`, `c1 c2 `, …) | Correct for the final transcript. Only display text is affected |
| E. `flush()` returns `""` but `committed` deltas are complete | Final text complete (`w1 w2 w3 `) | Correct. An empty flush is harmless when nothing is pending |
| F. final group shorter than `chunk_seconds` | Emitted, never dropped (see criterion 2) | Correct |
| G. `flush()` that returns `""` at a boundary where the pass had pending and no committed delta | Same as B at a boundary | Decoder contract violation; see R2 |

**Judgement.** Cases B/C are *not* driver bugs. The driver's published contract is "final text =
concatenation of `committed` deltas + `flush()` returns", and ADR 0011 forbids reconstructing
from `pending` (which is explicitly allowed to be garbage — the implementer's own
`PendingPoisonDecoder` test relies on that). A decoder that returns `""` from `flush()` while
holding an uncommitted `pending` tail, or that returns cumulative instead of delta `committed`,
is violating the delta/flush contract; the driver cannot detect it without trusting `pending`,
which it must not. I found **no way to lose or duplicate text with a decoder that satisfies the
documented contract**, so this is not blocking. It is, however, a sharp and undocumented edge:
the module docstrings describe the delta semantics but do not state in one place that the
decoder owns reconciling `pending` at `flush()`. R2 recommends a one-line docstring addition
and a ticket-14 test, not a code change.

### 2. Boundary timing — pass

Sample-based accounting, not chunk counting: the boundary test is
`fed_samples >= window_seconds * group.sample_rate` with `fed_samples += len(group.samples)`
and `group.sample_rate` read from the real buffer. `_group_chunks` accumulates by sample count
and splits/merges buffers accordingly.

Independent flush-count probe (1 s chunks unless noted):

```
total=30.0s window=5.0s chunk=1.0s -> process=30 flush=7
total= 3.5s window=5.0s chunk=1.0s -> process=4  flush=1  sizes=[16000,16000,16000,8000]
total= 5.5s window=5.0s chunk=1.0s -> process=6  flush=2  sizes=[...16000,8000]
total= 3.0s window=1.0s chunk=1.0s -> process=3  flush=4
```

- 30 s / 5 s: `flush_after_process_calls == [5, 10, 15, 20, 25, 30, 30]` → **6 boundary + 1
  final = 7**. Matches the ticket exactly.
- Uneven 3.5 s of 1 s chunks at a 5 s window: the 0.5 s final group is emitted (`8000` samples),
  no boundary is crossed, and the remainder is flushed **exactly once** (the final flush;
  `flush_calls == 1`). Confirmed with exact byte counts, not a rounded duration.
- Off-by-one: 5 s total (`>=` at exactly 5×16000=80000) flushes at process 5, so the boundary
  fires on the exact boundary, then the final flush fires again → `[5, 5]`, 2 flushes.
  4.999 s (79984 samples) crosses no boundary → `[5]`, 1 flush. The comparison is `>=`, correct.
- `window_seconds == chunk_seconds` flushes after every group (test present).

The only rounding is `_target_samples = max(1, round(chunk_seconds * sample_rate))`, which is
the right place for it; the boundary comparison itself stays integer-vs-float with no per-group
drift.

### 3. The language fix — pass

Independently verified with my own `LangDecoder` (not the implementer's fake):

```
passes report en,None,es,None,None over a boundary at 5 s -> ['en','en','es','es','es','es'], final 'es'
passes report fr,None                                  -> ['fr','fr','fr']   (later None does not erase)
passes never report                                    -> [None, None, None] (no crash, no fabrication)
```

- Most recent **non-`None`** value is carried onto every segment, including the final one.
- A later `None` does not erase an earlier language.
- A decoder that never reports one yields `None` everywhere, no crash and no fabrication.
- The tracked value lives outside the `for` loop and is **not reset** on a window `flush()`, so
  it survives boundaries; the final segment carries the last non-`None` value for the whole
  utterance (verified over a boundary in the first case above).
- The module docstring was updated and no longer contradicts the behaviour; it now states
  language is "the most recent non-`None` language the decoder reported". The
  `StreamingPass.language` field is `str | None = None`, so `committed`/`pending` stay required
  and keyword construction is unchanged — `FakeStreamingDecoder` and the merged
  `tests/fakes.py` doubles keep working.

### 4. Pure logic, generator, eager guards — pass

- `streaming.py` imports only `__future__`, `collections.abc` (`Callable`, `Iterable`,
  `Iterator`), `dataclasses`, `typing` (`Protocol`, `runtime_checkable`), and
  `ges2t.interfaces` (`AudioBuffer`, `TranscriptionSegment`). No `subprocess`, `threading`,
  `needle`, `sounddevice`, `numpy`, `wave`, `socket`, `asyncio`, or clock. The only textual
  match for a forbidden word is the sentence "no subprocess and no thread" in the docstring.
- `drive()` returns `_drive(...)`, a generator object: `isinstance(it, Iterator) is True`, and
  zero `process()`/`flush()` calls happen at call time; the first `next()` makes exactly one
  `process()` call. Memory is bounded by one group (plus the transcript string), not by
  utterance length.
- Eager justification: the two guards run in `drive()` **before** it hands back `_drive(...)`.
  I confirmed both raise at call time, not on first iteration:
  `chunk_seconds=0 -> ValueError at call time`, `window<chunk -> ValueError at call time`.
  This is the right call: a generator function defers its whole body, so a plain `def drive`
  generator would not raise until first `next()`. Eager validation + a lazy inner generator is
  defensible and is directly tested (`test_non_positive_chunk_seconds_is_rejected`,
  `test_window_smaller_than_chunk_is_rejected`, `test_drive_is_lazy_until_iterated`).
- Empty input: one final segment with empty text, 0 `process` calls, 1 `flush` call.
- Bonus robustness (not required but good): a sample-rate mismatch raises
  `ValueError: all buffers in a stream must share one sample rate, got 16000 and 8000`.

### 5. Nothing reconstructed from `pending` — pass

`transcript` is built only as `transcript += pass_.committed` and `transcript += decoder.flush()`
in the boundary path and once more before the final yield. `pending` is assigned from
`pass_.pending` and used only to compute the display `text = transcript + pending`, then cleared
to `""` after a flush. The final `TranscriptionSegment` is `text=transcript`. My adversarial
case D (garbage `pending` that changes size each pass) leaves the final text untouched, and the
implementer's `test_final_text_is_never_built_from_pending` uses a poison token that would show
up if `pending` reached the final text. A mutation that adds `pending` to the final text fails
two tests (see criterion 8).

### 6. ADR 0019 and the widened check — pass for this ticket, with a pre-existing out-of-scope failure

- **Tuples / `Any` / `Optional` / `-> None`:** no positional-tuple returns; the only `tuple`
  occurrence in `streaming.py` is inside a docstring explaining why `StreamingPass` is *not* a
  tuple. No bare `Any`, no `typing.Optional`, no `# type: ignore`, no un-annotated definition.
  Every `def` carries a return annotation, including `-> None`. `StreamingPass` is a frozen
  dataclass; `StreamingDecoder` is a `runtime_checkable` `Protocol` with exactly
  `{process, flush}`.
- **Narrow check:** `uv run mypy src/ges2t` → `Success: no issues found in 7 source files`.
- **Widened check (ADR 0019 as amended 2026-10-09):**
  `uv run mypy src/ges2t tests` → **1 error, in a file this ticket did not touch:**
  ```
  tests/test_whistle_config.py:247: error: Argument 1 to "_error_message" has incompatible type "object"; expected "Mapping[str, Any]"  [arg-type]
  Found 1 error in 1 file (checked 14 source files)
  ```
  This is exactly the failure mode the ADR amendment records ("a genuine violation survived
  ticket 4's review"), and there is a live `feat/type-check-whole-tree` worktree for ticket 20
  to fix it. Checking **only this ticket's files**:
  `uv run mypy src/ges2t/streaming.py tests/test_streaming.py` →
  `Success: no issues found in 2 source files`. So the branch introduces no typing violation;
  the wide command's single failure is pre-existing and out of scope.
- **Config effectiveness probe (allowed and reverted):** I copied a temporary
  `src/ges2t/_probe_untyped.py` containing `def untyped_probe(x): return x`. The wide command
  then additionally reported `src/ges2t/_probe_untyped.py:1: error: Function is missing a type
  annotation  [no-untyped-def]`, proving `disallow_untyped_defs` is genuinely active. I removed
  the file; `git status --porcelain` is empty afterwards.
- **Branch base note:** this branch was cut from `584372a`, before main's ADR amendment commit
  `5c9a1f8`, so the ADR text *inside this worktree* still shows the old `mypy src/ges2t` command.
  Main already carries the amendment, so the merge is fine; the reviewer ran the wider command as
  instructed.

### 7. Scope — pass

`git diff --name-only main...HEAD` lists exactly:

```
docs/project/README.md
docs/project/live-dictation-ux/streaming-window-engine/001-comment.md
docs/project/live-dictation-ux/streaming-window-engine/ticket.md
src/ges2t/streaming.py
tests/test_streaming.py
```

Only the two permitted code files were added/changed; no other module was touched. No forbidden
module appears. No conflict markers (`git diff --check` reports only a trailing blank line at
EOF in `001-comment.md`, a cosmetic nit).

### 8. Tests are honest, headless, fast — pass

`uv run pytest tests/test_streaming.py -q` → **22 passed in 0.09 s**; `uv run pytest -q` →
**138 passed in 0.43 s**. No sleeps, threads, engine, audio, or network; the fake is
deterministic.

I ran mutation testing in a **throwaway git worktree** at the same commit (never editing the
reviewed worktree), reverting each mutation and confirming the temp worktree ended clean. Every
mutation was caught:

| Mutation to `streaming.py` | Result | Failing tests (examples) |
|---|---|---|
| `>=` → `>` at the boundary | 6 failed | `test_thirty_seconds…` (7-flush count), `test_short_incoming_buffers_are_combined_into_groups`, `test_uneven_tail_that_crosses_the_window…` |
| final text includes `pending` | 2 failed | `test_final_text_is_never_built_from_pending`, `test_uneven_tail_shorter_than_a_window_is_flushed_once` |
| final `flush()` removed | 10 failed | the seam, boundary, empty-input and error-propagation tests |
| language tracking removed | 2 failed | `test_language_is_the_most_recent_non_none…`, `test_later_language_replaces_an_earlier_one` |
| `committed` appended twice | 4 failed | `test_final_text_is_the_known_perfect_utterance_across_windows` (the seam guard), `test_final_text_is_never_built_from_pending`, … |

So the seam/perfect-model assertions bite on a duplicated delta, and the language tests bite on
a removed tracker. The pre-review fix did **not** weaken the existing expectations: the
`b25ca55` test diff is purely additive (new `languages=` keyword on the fake plus three new
language tests); no assertion from `17755f1` was changed or relaxed. The pre-existing
`all(partial.language is None …)` assertion in the partials test still holds because the fake
defaults to no language.

### 9. Board row, ticket status, links — pass

- `docs/project/README.md` row 13: `in review`, branch `feat/streaming-window-engine`.
- `ticket.md` header: `- **Status:** in review`; the row and the header agree.
- Every relative path token in `ticket.md` resolves:
  `../epic.md`, `../streaming-session/ticket.md`, `../whistle-streaming-adapter/ticket.md`,
  `../../core-dictation-pipeline/core-interfaces-and-fakes/ticket.md`,
  `../../core-dictation-pipeline/whistle-engine-config/ticket.md`,
  `../../../adr/0011-…`, `../../../adr/0010-…`, `../../../adr/0019-…` (all OK).
- No conflict markers in any changed file.

## Evidence

All commands run from
`/home/matee/Workspace/Personal/ges2t/.worktrees/streaming-window-engine` at `b25ca55`.

### 1. Baselines

```
$ uv run pytest tests/test_streaming.py -q
22 passed in 0.09s
$ uv run pytest -q
138 passed in 0.43s
$ uv run mypy src/ges2t
Success: no issues found in 7 source files
$ uv run mypy src/ges2t tests            # ADR 0019 as amended
tests/test_whistle_config.py:247: error: Argument 1 to "_error_message" has incompatible type "object"; expected "Mapping[str, Any]"  [arg-type]
Found 1 error in 1 file (checked 14 source files)
$ uv run mypy src/ges2t/streaming.py tests/test_streaming.py
Success: no issues found in 2 source files
```

### 2. mypy effectiveness probe (temporary, reverted)

```
$ cp probe src/ges2t/_probe_untyped.py && uv run mypy src/ges2t tests
tests/test_whistle_config.py:247: error: ... [arg-type]
src/ges2t/_probe_untyped.py:1: error: Function is missing a type annotation  [no-untyped-def]
Found 2 errors in 2 files (checked 15 source files)
$ rm src/ges2t/_probe_untyped.py && git status --porcelain
(empty)
```

### 3. Seam + adversarial probes

Full output transcribed in criteria 1–3 above. The independent oracle reconstructed the final
text exactly; the adversarial decoders A–G produced the behaviours tabulated there.

### 4. Mutation testing (throwaway worktree, reverted)

```
### MUTATION: final text includes pending         2 failed, 20 passed
### MUTATION: language tracking removed           2 failed, 20 passed
### MUTATION: final flush removed                10 failed, 12 passed
### MUTATION: leftover tail group dropped         2 failed, 20 passed
### MUTATION: committed appended twice            4 failed, 18 passed
restored: True
```

## Downstream contracts to report

Exact final signatures (confirmed by `inspect.signature`):

```python
@dataclass(frozen=True)
class StreamingPass:
    committed: str
    pending: str
    language: str | None = None

@runtime_checkable
class StreamingDecoder(Protocol):
    def process(self, buffer: AudioBuffer) -> StreamingPass: ...
    def flush(self) -> str: ...

def drive(
    decoder: StreamingDecoder,
    chunks: Iterable[AudioBuffer],
    *,
    window_seconds: float,
    chunk_seconds: float,
    on_partial: Callable[[str], None] | None = None,
) -> Iterator[TranscriptionSegment]: ...
```

- **`drive`** is consumed by ticket 14 (`WhistleTranscriber.stream`) and ticket 15
  (`StreamingDictationSession`). Keyword arguments are `window_seconds`, `chunk_seconds`,
  `on_partial`. The return is lazy; the guards raise `ValueError` eagerly at call time.
- **`StreamingPass`** gained `language: str | None = None`. `committed`/`pending` remain
  required, and construction by keyword is unchanged, so ticket 14's
  `StreamingPass(committed=..., pending=...)` still compiles and `tests/fakes.py` is
  unaffected.
- **Concatenation-safety requirement on the decoder (must be honoured by ticket 14).** The driver
  joins `committed` and `flush()` strings **verbatim**. The decoder therefore owns all
  inter-delta whitespace: a real engine that returns bare words without a leading space would
  produce run-together text. This is documented in `001-comment.md` ("Contract for ticket 14")
  and in `FakeStreamingDecoder`'s docstring, but it is not stated in `streaming.py`'s
  `StreamingPass`/`StreamingDecoder` docstrings — see R2.
- **`FakeStreamingDecoder` reusability (ticket 15).** The fake is deterministic, pure-logic, and
  exported via `__all__`; `tests/` is a package, and there is precedent
  (`from tests.fakes import ...`). I confirmed `from tests.test_streaming import
  FakeStreamingDecoder` works. So it is importable and reusable. **But** it implements
  `StreamingDecoder` (`process`/`flush`), while ticket 15's `StreamingDictationSession` is typed
  to take a `Transcriber` and call `transcriber.stream(...)`. Ticket 15's own text is ambiguous
  about whether the session drives `drive()` directly or wraps it inside a `Transcriber`; and
  the existing `tests/fakes.py::FakeTranscriber.stream()` returns configured segments rather
  than calling `drive()`. Reuse works only if ticket 15 either (a) takes a `StreamingDecoder` and
  calls `drive()` (contradicting its stated constructor signature), or (b) writes a small
  `Transcriber` adapter whose `stream()` delegates to `drive(FakeStreamingDecoder(...), ...)`.
  This is a ticket-15 integration decision, not a ticket-13 defect (see R3).

## Risks / follow-ups (all non-blocking)

- **R1 — wider mypy command fails on an out-of-scope file.** `uv run mypy src/ges2t tests`
  exits non-zero because of the pre-existing violation in `tests/test_whistle_config.py`. This
  is the gap ADR 0019's 2026-10-09 amendment and ticket 20
  (`feat/type-check-whole-tree`) exist to close. This branch adds no violation; do not attribute
  the failure to this ticket. Merge order note: if the ADR amendment and ticket 20 land first,
  the wide command should then be fully green.
- **R2 — the delta/flush contract is sharp and not stated in `streaming.py`.** A decoder that
  returns `""` from `flush()` while an uncommitted `pending` tail exists silently drops that
  text; a decoder that returns cumulative `committed` silently duplicates. The driver is correct
  given the contract (it must not read `pending`), but the contract is only in
  `001-comment.md`/the fake. Recommend a one-line addition to the `StreamingPass` or
  `StreamingDecoder` docstring ("`committed`/`flush()` must be concatenation-safe, and `flush()`
  must return the entire uncommitted tail; `pending` is never used") and a ticket-14 test that a
  decoder returning empty flushes loses text. This is ticket 14's work, not a change required
  here.
- **R3 — ticket 15's reuse story is internally inconsistent** (see Downstream contracts). The
  orchestrator should pin down before ticket 15: does the session take a `Transcriber` (then
  `FakeStreamingDecoder` lives behind a `Transcriber` adapter) or a `StreamingDecoder` (then the
  session calls `drive()` and `FakeTranscriber` is irrelevant to the streaming path)?
- **R4 — cosmetic.** `001-comment.md` ends with a blank line at EOF; and `FakeStreamingDecoder`
  lives in `tests/test_streaming.py` while `tests/fakes.py` is the documented shared-double
  module. Both work (cross-import is proven), but moving the decoder to `tests/fakes.py` would be
  more consistent with ADR 0014 and this repo's convention. Neither affects correctness.

## Not verified / out of reach

- The language behaviour and seam behaviour were verified only against deterministic fakes; the
  real-engine seam proof (68→68 words, WER, per-chunk latency) belongs to ticket 14's manual
  reference-machine run and is not part of this ticket.
- `pass_ms` is always `None` and timestamps are deliberately absent; ADR 0011 assigns per-chunk
  timing to the session, so this is by design and was not exercised here.
- The driver only sanity-checks `chunk_seconds <= 0` and `window_seconds < chunk_seconds`; the
  0.25 s floor, 30 s ceiling, and latency-knee warning are intentionally left to
  `whistle_config.py` (ADR 0011) and are not verified by this ticket.
