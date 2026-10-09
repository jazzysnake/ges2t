# Make the whole tree type-clean, tests included

- **Priority:** P2
- **Status:** to do
- **Complexity:** XS
- **Epic:** ../epic.md

## Current state / context

ADR 0019 requires full type annotations with `mypy` enforcement, and states explicitly that it
applies to **"tests and helper code, not just to `src/`"**. But every ticket's definition of done
named only `uv run mypy src/ges2t`, so **tests were never type-checked**.

That gap is not theoretical — it let a real violation survive a completed ticket's review.
Running the wider command on `main` at `665f7bb`:

```
$ uv run mypy src/ges2t tests
tests/test_whistle_config.py:247: error: Argument 1 to "_error_message" has incompatible type "object"; expected "Mapping[str, Any]"  [arg-type]
Found 1 error in 1 file (checked 7 source files)
```

The defect came from ticket 4 (`whistle-engine-config`), which passed its review because its
DoD — and therefore the reviewer — checked `src/ges2t` only. Ticket 7's reviewer found it
incidentally while running `mypy tests` on an unrelated branch, and correctly reported it as
**pre-existing**.

ADR 0019 has been amended (2026-10-09) so the enforcement command is now
`uv run mypy src/ges2t tests`. This ticket closes the violation that the old command allowed.

Relevant decisions: ADR 0019 (typing rules and enforcement), ADR 0014 (quality bar).

## Definition of done

- [ ] `uv run mypy src/ges2t tests` exits **0**.
- [ ] The violation at `tests/test_whistle_config.py:247` is fixed by giving the test helper a
      correct type — **not** by `cast(Any, ...)`, not by a blanket `# type: ignore`, and not by
      deleting or weakening the assertion. Work out what the helper actually receives and
      annotate it accordingly. If the helper genuinely accepts a raw TOML mapping, `Mapping[str,
      Any]` is right and the *caller* is what needs fixing; determine which and say so.
- [ ] The test's **intent and coverage are unchanged**: `uv run pytest` passes with the same
      number of tests as before the change, and the specific behaviour that test covers is still
      asserted. Report the before/after test counts.
- [ ] `uv run mypy src/ges2t` still exits 0.
- [ ] `uv run pytest` passes.
- [ ] No behaviour change anywhere: this is a typing fix only. If you find yourself changing
      production code, stop and write a comment instead — that would mean the error was real.
- [ ] `001-comment.md` records: the exact cause of the error, what you changed and why that is
      the correct fix rather than a suppression, the before/after test counts, and whether you
      found any *other* latent type problems while the wider check was running.

## Notes

- This is deliberately one small ticket rather than a hole punched in a later one. It is also a
  demonstration of the amended ADR 0019: the fix is only half the work, and the other half is
  that the check now actually covers what the rule claims.
- Do not "fix" other files opportunistically. If the wider command surfaces additional
  violations beyond line 247, **report them** in the comment; do not silently expand scope
  beyond `tests/test_whistle_config.py`.
- Check whether the helper's parameter is annotated `object` deliberately (e.g. to accept any
  TOML value) — if so, the right fix may be a narrowing `isinstance` check at the call site,
  which is strictly better than a broader parameter type because it also makes the test's
  assumption explicit.

## Related tickets

- depends on: ../whistle-engine-config/ticket.md (the source of the defect)
- blocks: none
- related ADRs: ../../../adr/0019-code-style-typing-and-interfaces.md,
  ../../../adr/0014-testing-and-quality-bar.md
