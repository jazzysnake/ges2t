# 001 — Streaming window engine implementation notes

Ticket: `ticket.md` (`streaming-window-engine`) · Branch: `feat/streaming-window-engine`
Worktree: `.worktrees/streaming-window-engine`

## What was built

Exactly the two files the ticket allows:

- `src/ges2t/streaming.py` — the generic windowed-commit driver.
- `tests/test_streaming.py` — 19 tests against a deterministic fake decoder.

No other module was touched. `streaming.py` imports only `collections.abc`,
`dataclasses`, `typing`, and the already-merged `ges2t.interfaces`. It imports no
backend, no audio library, no `subprocess`, no `threading`, and no clock. Ticket 14
consumes `StreamingDecoder`, `StreamingPass`, and `drive` exactly as named here.

## Final public signatures

```python
from collections.abc import Callable, Iterable, Iterator

@dataclass(frozen=True)
class StreamingPass:
    committed: str
    pending: str

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

Everything is fully annotated including the private helpers (`_drive`,
`_group_chunks`, `_target_samples`); there are no tuples in any public return, no bare
`Any`, and no implicit `Optional` (ADR 0019). `StreamingPass` is a frozen dataclass,
not the tuple the pre-correction ticket sketched. `StreamingDecoder` is marked
`@runtime_checkable` to match the other Protocols in `interfaces.py`.

## How duration was accounted

The window boundary is decided by **samples**, never by counting groups:

```python
fed_samples += len(group.samples)
if fed_samples >= window_seconds * group.sample_rate:
    ...flush...; fed_samples = 0
```

- Grouping (`_group_chunks`) accumulates the incoming sample values and emits
  `round(chunk_seconds * sample_rate)`-sample groups, carrying the remainder to the
  next group. Incoming buffers that are shorter than one group are combined; buffers
  longer than one group are split; the final leftover is emitted as a short group by
  the loop's tail when the input ends, so it is **never dropped**.
- The boundary test compares an integer sample count against
  `window_seconds * sample_rate`, avoiding the float-drift an accumulator of
  per-group `len(rate)` divisions would accumulate at fractional chunk sizes.
- A single group can overshoot the window by at most one `chunk_seconds` (the sanity
  guard keeps `window_seconds >= chunk_seconds`), so exactly one flush per group is
  ever needed; no `while` flush loop, and a bad argument cannot spin.

All buffers in one stream must share a sample rate; a mismatch raises `ValueError`
rather than silently concatenating samples at different rates. The 16 kHz contract
(ADR 0005) makes this unreachable in practice.

## Seam-loss reasoning

The transcript is accumulated as literal string concatenation of each pass's
`committed` delta plus each `flush()` return value: `transcript += pass_.committed`,
`transcript += decoder.flush()`. `pending` is used **only** for the display text of a
partial (`transcript + pending`) and is cleared on a boundary flush, because the
flush has just committed exactly that tail. The final segment is built from
`transcript` after the final flush, never from `pending`.

Therefore a word can be lost only if the decoder's own deltas lose it, and duplicated
only if the decoder returns it in two deltas. The driver never re-feeds audio into a
completed stream and never re-reads a pass. The seam test models the engine's real
two-passes-agree rule (the newest revealed word stays `pending`, everything before it
is `committed`, `flush()` commits the tail) and asserts the concatenation over 30 s of
1 s chunks at a 5 s window is exactly the 68-word known-perfect utterance, with no
token duplicated or dropped.

**Contract for ticket 14 (important):** the driver concatenates deltas verbatim, so
the decoder's `committed` / `flush()` strings must carry their own whitespace (a
continuation delta needs its leading space). The fake models the concatenation-safe
convention. If the engine returns bare words without separators, ticket 14 must add
the separator inside `_WhistleStreamingDecoder`, not here.

## Judgement calls (the orchestrator should know these)

1. **`drive` is a thin eager-validating wrapper over a private generator `_drive`.**
   The ticket says "keep it a generator", and it still returns a lazy `Iterator` whose
   memory is bounded by one group, not by the utterance. The reason for the wrapper is
   that a generator function defers its whole body — including the guards — until the
   first `next()`, so `drive(bad...)` would not raise at the call site. The two guards
   (`chunk_seconds <= 0`, `window_seconds < chunk_seconds`) now run eagerly and raise
   `ValueError`; iteration stays lazy. If a reviewer requires `drive` itself to be a
   generator function, moving the guards into the body is a two-line change, but the
   call-time failure would be lost.
2. **Guards raise `ValueError`, not `AssertionError`.** The ticket allows either; a
   `ValueError` survives `python -O`. The messages name both values. There is no
   check for the 0.25 s floor, the 30 s ceiling, or the latency-knee warning — that is
   `whistle_config.py`'s job (ADR 0011), not re-implemented here.
3. **`language` and `pass_ms` are `None` on every yielded segment.** `StreamingPass`
   is fixed by the ticket as `(committed, pending)` only, so the driver has no access
   to the engine's language or timing. `pass_ms` is consistent with ADR 0011 ("timings
   are ours", measured by the session), but **language is a real limitation**: ADR
   0011 says the final language is "whatever the engine says, taken from the final
   committed pass". If ticket 14/15 needs it, `StreamingPass` must gain an optional
   `language` field, which is a deliberate deviation from this ticket's fixed shape
   and should be decided by the orchestrator, not improvised inside the adapter.
4. **The final `flush()` is unconditional**, including after a group that landed
   exactly on a boundary (where the fresh stream returns the empty string) and for
   empty input.
   That is what makes the counts deterministic: 30 s at a 5 s window is 30 `process`
   calls and 7 `flush` calls (boundaries after process 5/10/15/20/25/30 plus the
   final one); empty input is 0 `process` and 1 `flush`, one empty final segment.
5. **A discarded variable-name collision was avoided:** `_group_chunks` uses a local
   `sample_rate`, distinct from the `sample_rate` field on `AudioBuffer`.

## Test surface handed to ticket 15

`tests/test_streaming.py` defines `FakeStreamingDecoder` (deterministic, models the
two-pass commit rule, records `process_calls`, `flush_calls`,
`flush_after_process_calls`, `processed_samples`, `available_history`). The
streaming-session ticket explicitly expects to import this double rather than
redefine it. Tests are otherwise pure-logic: no sleeps, no threads, no engine, runtime
0.14 s.

## Verification (real output)

```
$ uv run pytest tests/test_streaming.py -q
19 passed in 0.14s

$ uv run pytest -q
135 passed in 0.62s

$ uv run mypy src/ges2t
Success: no issues found in 7 source files
```

`uv run mypy tests/test_streaming.py` also reports `Success: no issues found in 1
source file` (not required by the DoD, but the test file is fully annotated too).

## Hand-off checklist for the orchestrator

- Ticket 14 consumes `StreamingPass`, `StreamingDecoder`, `drive` under exactly these
  names; `drive`'s keyword arguments are `window_seconds`, `chunk_seconds`,
  `on_partial`.
- Ticket 14 must return concatenation-safe deltas (see the seam contract above).
- The `language`-on-`StreamingPass` question in judgement call 3 is the one open
  design point that could require touching this module after review.
- No `main` commit was made; the branch is `feat/streaming-window-engine` and the
  worktree is left in place for the reviewer.

## Pre-review fix: language propagation (amends judgement call 3)

The `language` gap noted in judgement call 3 above is now closed at the source,
before review, so that ticket 14 does not have to edit this ticket's module.

- `StreamingPass` gained `language: str | None = None`; it stays a frozen
  dataclass and `committed`/`pending` are still required. Construction by keyword
  is unchanged, so the shared `FakeStreamingDecoder` (and ticket 15) keep
  working.
- `drive()` propagates the **most recent non-`None`** language onto every yielded
  `TranscriptionSegment`, partials and the final one alike. The rule: the engine
  detects language per pass and an early pass may not have reported one yet, so a
  later `None` must not erase a language already seen. The tracked value survives
  window flushes, so the final segment carries the last non-`None` value the
  decoder ever reported (ADR 0011: language is "whatever the engine says, taken
  from the final committed pass").
- `ttft_ms`/`decode_tps` are not added (ADR 0011: streaming JSON does not provide
  them); `pass_ms` stays `None`.
- `drive()`'s grouping, boundary-flush timing, transcript construction, and
  single final segment are untouched; the seam/perfection assertions pass
  unchanged.

New tests in `tests/test_streaming.py` (22 total now, up from 19): the fake
decoder accepts an optional `languages` sequence, and two tests cover the
most-recent-non-`None` rule (including "later overrides earlier") and the
never-reported case. The pre-existing `all(partial.language is None ...)`
assertion in the partials test still holds because the fake defaults to no
language.

Real output after the fix:

```
$ uv run pytest tests/test_streaming.py -q
22 passed in 0.16s

$ uv run pytest
138 passed in 0.41s

$ uv run mypy src/ges2t
Success: no issues found in 7 source files
```

