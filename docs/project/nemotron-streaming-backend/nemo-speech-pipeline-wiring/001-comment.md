# 001-comment — ticket 60 (NeMo-Speech pipeline wiring): composition root + R1

Branch `feat/nemo-speech-pipeline-wiring` · worktree `.worktrees/nemo-speech-pipeline-wiring` ·
base `66f5235`.

## What this ticket is

Tickets 55–59 are merged. `nemo_speech` is selectable (58) and streams (59) but is **not in
`_TRANSCRIBER_BUILDERS`** and `_streaming_chunk_seconds` is still **Whistle-hardcoded**, so the engine
cannot run end to end yet. This ticket closes that gap and owns **R1** (the batch/stream text
divergence inherited from ticket 59's review).

Three jobs, per the ticket:

- **(A) composition root** — register `_build_nemo_speech`, generalise `_streaming_chunk_seconds`,
  dispatch artifact presence, end-to-end wiring test.
- **(B) R1** — measure offline `recognize_f32` vs streaming-drained-to-final on the reference clips
  (ticket 55's harness; no download, no sudo, no network, no mic), then decide from the numbers:
  unify `transcribe()` as a drain of `stream()`, or keep both and align their configuration.
- **(C) boundaries** — no change to `streaming_session.py`, `service.py`, `socket_proto.py`,
  `incremental_typing.py`; Whistle and whisper.cpp behaviour unchanged.

## Baseline (this worktree, before any change)

```
$ uv run pytest -q
1374 passed, 3 skipped in 12.64s
```

(A bare `uv sync --offline` after `uv venv --system-site-packages --python /usr/bin/python3` — no
network.)

<!-- appended incrementally as work proceeds -->
