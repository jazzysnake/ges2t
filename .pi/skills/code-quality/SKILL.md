---
name: code-quality
description: The ges2t code-quality rules every implementation and review is held to. Preloaded automatically into the ges2t implementer and reviewer agents; read it before writing or reviewing code in this project.
---

# ges2t code-quality rules

These are the rules every implementation and every review in this project is held to. Each one exists
because it was learned from a **real defect here**, not from style preference. They refine
[ADR 0019](../../../docs/adr/0019-code-style-typing-and-interfaces.md) (the typing and interface bar) and
[ADR 0014](../../../docs/adr/0014-testing-and-quality-bar.md) (the testing bar); where this file and an
ADR disagree, **the ADR wins**.

## The rules

### 1. One definition per identifier
Identifier strings — engine names, injection backends, setup tokens, model names, config keys, defaults —
are **defined once** and compared through named constants. **No bare string literal in an `if`, `match`,
or comparison.** Two modules spelling the same identifier twice is how `whisper.cpp` and `whisper_cpp`
shipped as one thing with two names.
*Check:* the same literal appears in a comparison in two files.

### 2. One spelling per thing
If two surfaces must accept the same token, either **share one spelling**, or make the alias **explicit,
normalised in one place, and documented**. An alias is a decided, tested behaviour — not an accident.
*Check:* a user-facing token and the value it maps to are spelled differently, with no alias.

### 3. One source of truth per user-facing message
When two commands render the same sentence (a hint, a fix, a warning), they call **one** function. Two
wordings of one message will drift.
*Check:* grep for the sentence; it is formatted in more than one place.

### 4. Type everything — ADR 0019
Full annotations including `-> None`; **interface-typed signatures** (Protocols in parameters, not just
inheritance); **classes instead of positional tuples**. Enforced by `uv run mypy src/ges2t tests`.
*Check:* the mypy command above, which covers `tests/` too.

### 5. No silent degradation; fail closed
A configuration that cannot work **fails loudly** with a typed, actionable error naming the offending
setting(s). A configured alternative is **never** silently substituted (ADR 0020). A **default** must not
masquerade as a user choice — error when the user asked for a contradiction, not when a default did.
*Check:* every failure path raises a typed error; no `except: pass`; no fallback that changes behaviour.

### 6. A test must be able to fail
Never delete, weaken, skip or `xfail` a test to go green. **Falsify every new guard**: break the guarded
behaviour, watch the test **fail**, revert completely. A check that cannot fail is **worse than no
check** — it reads as verification.
*Check:* for each new test, name the mutation that makes it fail.

### 7. Test the real seam
When a seam wraps a subprocess or a native library, **at least one test drives the real thing** (a child
process, the real `.so`). A fake that models a **simpler contract than reality** is not a test of the
seam — twice this suite was fully green while the real path was broken.
*Check:* is there a test naming the real binary/library, that skips (loudly) rather than silently passing?

### 8. Tests are hermetic
No test may depend on the machine's real data dir, hardware, network or clock. Use `tmp_path` and fakes.
No network in the suite. **Tests keep literal strings** — a test that asserts its own constant proves
nothing.
*Check:* run the suite with the user's real state present; nothing flips.

### 9. Docs must not overclaim — a doc that lies is worse than no doc
Every stated number, variant, or threshold **traces to a real run**. Never write a result that was not
measured. State the confounds and **what would falsify** the claim. Qualify a number with the conditions
it holds under (`-nt`, the machine, the clip).
*Check:* for each number in a doc, name the command that produced it.

### 10. No dead code
Remove unreachable fallbacks, commented-out blocks, and speculative branches. If a branch is reachable,
say so and cover it with a test.
*Check:* a fallback that no input can reach.

### 11. No magic numbers
Name thresholds, limits and delays as **typed constants** with a comment on where the number came from.
*Check:* a numeric literal in a condition.

### 12. Bounded, clean, honest work
Bound every external call with a `timeout`; check the process **exits**. Kill spawned processes, remove
probe files, leave `git status` clean. **Never commit a `src/` changed only for probing** — check
`git diff --stat` first. Commit after each working step. Report **real output**, not summaries.

## How this is enforced

- **Implementers** run the ticket's verify commands and paste real output; the artifact goes on disk,
  incrementally.
- **Reviewers** do not trust ticked boxes: they re-run the checks, **falsify** every claim and every new
  test, and record evidence, risks, and a could-not-verify list.
- **The orchestrator** merges only after a review passes, and verifies the committed object.

A change that cannot meet these rules is a change whose **ticket is wrong** — say so and split it, rather
than ship the shortcut.
