# Pin the CLI test guarantees (two falsified gaps)

- **Priority:** P2
- **Status:** done
- **Complexity:** XS
- **Epic:** ../epic.md

## Current state / context

Ticket 10 (`cli-surface-and-commands`) passed review and is merged. Its reviewer did something more
useful than checking the boxes: it **tried to make the tests lie**, and two of them did.

**F1 — the laziness test does not test what it claims.** ADR 0016 requires that running
`ges2t toggle` imports neither the engine nor a GUI toolkit, because the CLI is bound to a hotkey
and must start in milliseconds. The test passes **even with a top-level `import pystray` added to
`cli.py`** — it deletes the `sys.modules` entries *after* `cli` has already been imported, so it can
only catch a **runtime** import. The shipped code is genuinely lazy (the reviewer confirmed that in
a fresh subprocess), so this is a **test gap, not a product bug** — but the test's guarantee is
weaker than the ticket, the board, and the review all say it is.

**F2 — the exit-code tests cannot fail.** They compare against the imported constants, so mutating
`EXIT_SERVICE_NOT_RUNNING` to `7` and `EXIT_ENGINE_FAILURE` to `42` leaves **all 60 tests green**.
The runtime values happen to be correct, but ADR 0016 documents `2` and `3` as user-facing API —
compositor bindings and scripts depend on them — and nothing in the suite pins the literals.

Both are the same failure class as the `mypy src/ges2t` gap closed by ticket 20: **a check that
does not cover the thing it claims to cover**, which is worse than no check because it reads as
verification. That is why this is a ticket rather than a note.

Relevant decisions: ADR 0016 (the exit codes and laziness are contract), ADR 0014 (quality bar),
ADR 0019 (typing).

## Definition of done

Modify **only** `tests/test_cli.py`. No production code may change — if a fix appears to require
changing `src/ges2t/cli.py`, **stop**: that would mean F1 or F2 is a real defect, which is a far
more interesting finding. Comment, set the ticket `blocked`, and report.

- [x] **F1:** the laziness test genuinely detects an **import-time** dependency. Use a real
      subprocess with a clean interpreter (e.g. `python -c "import ges2t.cli"` then inspect
      `sys.modules`), so that a top-level `import pystray`/`import needle` in `cli.py` makes the
      test **fail**.
- [x] **Prove it:** temporarily add a top-level `import pystray` to `src/ges2t/cli.py`, confirm the
      test now **fails**, then revert `cli.py` completely (`git status` must be clean of it). Paste
      that output. A laziness test that has never been seen to fail is not a test.
- [x] Keep the test fast — a subprocess import costs tens of milliseconds, which is fine; do not
      turn it into something that starts a service or downloads anything.
- [x] **F2:** the documented exit-code **literals** are pinned independently of the constants, so
      that changing a constant breaks the suite. Assert `2` for the service-absent path and `3` for
      the engine/injection-failure path by literal value, with a comment saying that ADR 0016
      documents them as user-facing API.
- [x] **Prove it:** temporarily mutate `EXIT_SERVICE_NOT_RUNNING` in `cli.py` to `7`, confirm the
      test now **fails**, then revert completely. Paste that output.
- [x] `uv run pytest` and **`uv run mypy src/ges2t tests`** (ADR 0019 covers tests) both pass.
      Report before/after counts; no test may be deleted, skipped, or weakened — only strengthened.
- [x] `001-comment.md` records: what each test claimed vs what it actually checked, the falsification
      output for both, and any **other** test in the suite you noticed that could not fail.

## Notes

- While you are in there, note (do not necessarily fix) any other assertion whose truth depends on
  the constant it asserts against — that pattern is the general defect F2 is an instance of.
- Do not widen this into a test-suite overhaul. Two gaps, two falsifications, done.
- The reviewer also raised three non-blocking items that are **not** this ticket: F3 (`doctor`'s
  runtime key-binding output) belongs to ticket 12, F4 resolves when tickets 11/12 land, and F5 is
  the malformed-protocol-response wording. Leave them.

## Related tickets

- depends on: ../cli-surface-and-commands/ticket.md (the tests being fixed)
- blocks: none
- related ADRs: ../../../adr/0016-cli-surface.md,
  ../../../adr/0014-testing-and-quality-bar.md,
  ../../../adr/0019-code-style-typing-and-interfaces.md
