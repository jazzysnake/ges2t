# Centralize identifier literals into constants

- **Priority:** P2
- **Status:** done
- **Complexity:** M
- **Epic:** ../epic.md

## Current state / context

Identifiers are compared as **bare string literals** across `src/ges2t/` — engine names (`"whistle"`,
`"whisper_cpp"`), injection backends (`"ydotool"`, `"clipboard"`), the setup backend token
(`"whisper.cpp"`), model names (`"whistle"`, `"medium-q5_0"`, …), and config keys/defaults. Ticket 47's
`whisper.cpp` vs `whisper_cpp` trap is the symptom: two modules, two spellings, nothing to catch it.

**Enumerate the real set from the code** (grep for string literals used in comparisons across
`src/ges2t/`), not from this list — this is the inventory the ticket must produce.

## Definition of done

- [x] A **single source of truth** for the identifier strings used in comparisons. Decide the shape —
      one `constants.py`, or a small set of per-domain modules — and justify it in `001-comment.md`
      (ADR 0019's flavour applies: typed, named, no bare literals in logic).
- [x] The scattered literals are replaced by those constants across **`src/ges2t/` only** — **tests
      keep literal strings on purpose** (a test must assert the real value, so a wrong constant fails
      it, not its own constant). No behaviour change.
- [x] **Run an AST-based scan** (like the ADR 0018 network scan) over **`src/ges2t/` only** that
      enumerates the identifier-string literals used in comparisons, and **report it in
      `001-comment.md`**: the inventory plus anything intentionally left. This is a **verification step
      in this definition of done — not a committed test, and not a hard gate**: a remaining literal (a
      legitimate exemption, or one in `tests/`, which keep literals on purpose) does **not** fail the
      ticket. The scan exists to show the refactor's coverage and make the leftovers explicit; state
      its static-scan limitation honestly (a purely dynamic lookup cannot be caught).
- [x] The identifier **inventory** is recorded in `001-comment.md`: each identifier, where it is
      defined, and the sites changed.
- [x] `uv run pytest` green and `uv run mypy src/ges2t tests` 0 errors; report before/after counts; no
      test deleted, skipped or weakened.
- [x] No new dependency, no linter, no behaviour change.

## Notes

- **Scope to identifiers, not prose.** Error/help message text is out of scope (it is not compared).
- **Tests keep literal strings** — do not replace them with constants. A test that compares against the
  constant it is meant to verify proves nothing. The AST scan covers `src/ges2t/` only and is a
  reporting step, so test literals are never flagged and never fail the ticket.
- Do not rename anything a user sees — the **values** stay identical (this is a de-duplication, not a
  rename; ticket 47 owns the one naming change).
- If the sweep turns out to be larger than one session, **stop and split it** — a bounded first pass
  (the identifiers actually compared in `if`/`match`) is better than an unbounded refactor.
- **No keystrokes, no clipboard, no windows, no microphone, no `sudo`, no service changes, no
  network.**

## Related tickets

- depends on: —
- related: ../../whisper-cpp-backend/engine-naming-and-setup-guidance/ticket.md
- related ADRs: ../../../adr/0019-code-style-typing-and-interfaces.md, ../../../adr/0009-configuration-toml.md
