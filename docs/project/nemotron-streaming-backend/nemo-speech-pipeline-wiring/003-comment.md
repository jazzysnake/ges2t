# 003-comment — ticket 60: response to review 002 (F1/F2/F3)

- **Ticket:** [ticket.md](ticket.md) — composition root + R1 resolution, Epic 6, ADR 0022.
- **Branch / worktree:** `feat/nemo-speech-pipeline-wiring` @ `.worktrees/nemo-speech-pipeline-wiring`
  (base `66f5235`, review tip `8ca6641`).
- **Scope:** **docs + the harness only.** No change to `src/ges2t/` or `tests/` — the review confirmed
  the R1 decision and every `src/` change are correct, so this pass fixes only the evidence artifact
  (`001-comment.md`) and its harness (`measure_r1.py`).

The review (`002-review.md`) returned **changes requested** with all findings in the evidence/docs, not
the source. This file records each fix and its evidence.

## `src/` and `tests/` are untouched — hash evidence

The two changed source files still hash to the exact baseline the comment records (and the review's
falsification matrix reverted to):

```
$ sha256sum src/ges2t/pipeline.py src/ges2t/transcribers/nemo_speech.py
055c44c734917e7628db0b38b02789607492abad4738971563d1a8194378825a  src/ges2t/pipeline.py
684ebc7cde99cb3b6eb4cdc976fad33cef3c6f26456ad60b31c7ed10e72b06ee  src/ges2t/transcribers/nemo_speech.py
```

Both match the recorded prefixes `pipeline.py 055c44c7…`, `nemo_speech.py 684ebc7c…`. Only these files
are modified in this pass:

```
$ git status --short
 M docs/project/nemotron-streaming-backend/nemo-speech-pipeline-wiring/001-comment.md
 M docs/project/nemotron-streaming-backend/nemo-speech-pipeline-wiring/measure_r1.py
?? docs/project/nemotron-streaming-backend/nemo-speech-pipeline-wiring/003-comment.md
```

<!-- appended incrementally as work proceeds -->
