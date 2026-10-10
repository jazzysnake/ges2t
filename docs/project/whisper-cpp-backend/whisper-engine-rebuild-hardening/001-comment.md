# 001 — rebuild hardening: plan and result

Ticket 51. Ticket 49 passed review and recorded six **non-blocking** follow-ups (R1–R6) in its
`002-review.md`. This ticket closes them: the rebuild code is correct today, so this is test
coverage plus a little tidiness and two wording changes (`--rebuild` message, notice wording).

**No behaviour change** beyond the `--rebuild` message and the Vulkan notice wording: the rebuild
policy matrix and the staged-install safety are untouched.

## What I read first

- ticket `51` + `epic.md`
- ticket `49`'s `002-review.md` (the R1–R6 findings, verbatim) and `001-comment.md`
  (the policy matrix and the staging design)
- `src/ges2t/whisper_engine.py` (`ensure_engine`, `_desired_variant`, `_satisfies`,
  `_rebuild_reason`, `WhisperCppBuilder.build`, `_install_libraries`, `verify_build`),
  `src/ges2t/setup_command.py` (`run_setup`/`_run_whisper`), `src/ges2t/cli.py`
  (`_handle_setup`, the `--rebuild` flag) and their tests.

## Baseline (before any change)

```
$ timeout 600 uv run pytest -q
1228 passed in 10.77s

$ rm -rf .mypy_cache __pycache__ && timeout 600 uv run mypy src/ges2t tests
Success: no issues found in 64 source files
```

## R1–R6: fixed or declined

_(filled in as each is implemented; see below for the falsification evidence.)_

## Falsification

_(each new/strengthened test broken, watched fail, reverted.)_
