# Epic: Code quality

- **Priority:** P2
- **Status:** to do
- **Epic owner:** orchestrator

## Goal

Remove the **drift class** that produced the `whisper.cpp` / `whisper_cpp` mismatch: the project's
**identifier strings** (engine names, injection backends, setup backend tokens, model names, config keys
and defaults) must have **one definition** and be compared through named constants — never as bare
literals scattered through conditionals.

## Why it matters

Two modules using two spellings of the same identifier is invisible until a user hits it — which is
exactly what happened on the first real whisper run. A bare `"whisper.cpp"` in one `if` and
`"whisper_cpp"` in another has no compiler or test to catch it. Centralising the identifiers, and
guarding the rule with a test, turns a silent trap into a failing test.

## Deliverable

A single source of truth for the identifier strings, the comparisons using it, and a guard test that
fails when a new bare identifier literal appears in a comparison.

## Acceptance criteria

- [ ] `uv run pytest` and `uv run mypy src/ges2t tests` green; **no behaviour change**.
- [ ] Each identifier set (engines, injection backends, setup backend tokens, model names, the config
      keys/defaults used in comparisons) has exactly **one** definition.
- [ ] An AST scan enumerates the identifier literals compared in `src/ges2t/` and the leftovers are
      reported — a **verification step of the ticket, not a committed gate**; string literals in
      `tests/` are fine by design.
- [ ] Every ticket's `docs/project/README.md` row is `done`.

## Out of scope for this epic

- Human-readable message text (only identifiers).
- Third-party identifiers (whisper.cpp's own C symbols, TOML/JSON keys owned by other projects).
- Adding a linter or any new dependency; reorganising modules beyond what the constants need.
- Behavioural changes of any kind.
