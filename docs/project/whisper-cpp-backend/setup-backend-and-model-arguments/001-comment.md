# setup-backend-and-model-arguments — implementation notes

- **Branch:** `feat/setup-backend-and-model-arguments`
- **Worktree:** `.worktrees/setup-backend-and-model-arguments`
- **Status of this write-up:** in progress (written incrementally; see the commits)

## Baseline (before any change)

```
$ uv run pytest -q
1018 passed in 8.84s

$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 57 source files
```

The orchestrator's "known-good baseline" of `923 passed` is **stale**: `main` at
`27370b6` is `1018 passed`. The real baseline is recorded above and must not
regress.

## Scope

This ticket is the **`ges2t setup` command surface** only. The config-level backend
selection (`transcriber.engine`, `KNOWN_ENGINES`, the default rule *in config*, the strict
startup rejections) belongs to ticket 45 and is **not** implemented here.

Files this ticket touches — flagged for the ticket-45 serialization note:

- `src/ges2t/cli.py` — the `setup` parser and `_handle_setup` handler (ticket 45 does not
  name `cli.py` in its own definition of done, but flags it as a serialization risk).
- `src/ges2t/setup_command.py` — **new** module holding the backend/model parsing, the
  default-model rule and the orchestration. This is deliberately separate from
  `config.py`, so ticket 45's config work does not collide.
- `src/ges2t/model_store.py` — the needle/Whistle model *name* catalogue plus
  `ensure_model(name=...)`; ticket 45 does not touch `model_store.py`.

(Incremental notes continue below.)
