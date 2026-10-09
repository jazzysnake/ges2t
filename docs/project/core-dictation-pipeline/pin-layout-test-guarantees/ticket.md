# Pin the layout detection test guarantees (three falsified gaps)

- **Priority:** P2
- **Status:** to do
- **Complexity:** XS
- **Epic:** ../epic.md

## Current state / context

Ticket 24 (`layout-detection-and-config`) passed review and is merged
(`002-review.md`, verdict **pass**). The reviewer did more than check the boxes: it **tried to make
the tests lie**, and three of them did. None of these is a product bug — the reviewer confirmed each
guarded behaviour **by hand** — so the shipped code is correct. But the tests are **weaker than
everything downstream believes they are**, and ticket 23 is about to build on this module.

**F1 — the non-zero-exit guards are untested in isolation.** Both `live_keymap_text` and
`detect_layout` treat a non-zero exit as failure. The tests exercise them only with **empty stdout**,
so a non-zero exit and an empty success are indistinguishable: **removing the guards entirely leaves
all 423 tests green.** The reviewer verified the guards are real by driving `rc=1` **with valid
stdout** by hand and observing `None` — which is exactly the case no test covers.

**F2 — the live-keymap non-zero-exit guard has the same weakness**, on the path that matters most:
this is the keymap the service will actually type through.

**F3 — the 2-second timeout is never enforced by a test.** The tests assert only the **value passed
to the injected runner**; setting `timeout=None` inside `_run_command` still leaves 423 green. The
bound was verified by hand. A timeout that no test enforces is a claim, not a guarantee — and this
is the timeout that keeps a wedged `busctl`/`xkbcli` from hanging the hotkey.

All three are the same failure class as the `mypy src/ges2t` gap (ticket 20), the CLI laziness and
exit-code gaps (ticket 26), and ultimately the `ydotool` layout corruption: **a check that does not
cover the thing it claims to cover**, which is worse than no check because it reads as verification.
This is now the fourth instance in this project. That is why it is a ticket rather than a note.

Relevant decisions: ADR 0020 (layout-aware mapping), ADR 0014 (quality bar), ADR 0019 (typing).

## Definition of done

Modify **only** `tests/test_layout.py`. No production code may change — if a fix appears to require
changing `src/ges2t/injectors/layout.py`, **stop**: that would mean one of F1–F3 is a real defect,
which is a far more interesting finding. Comment, set the ticket `blocked`, and report.

- [ ] **F1:** a non-zero exit **with valid stdout** is pinned as failure for **both**
      `live_keymap_text` and `detect_layout` (use the injected runner, no real subprocess).
- [ ] **F2:** the same for the live-keymap path specifically.
- [ ] **F3:** the timeout is genuinely enforced. Assert that the detector **passes a timeout** to the
      runner *and* that a runner which outlives it yields `None`/the documented failure in **bounded**
      time. The test must be **bounded**: use a bounded helper (e.g. `call_with_timeout`) so that a
      regression **fails loudly instead of hanging** the suite. Never `sleep` for the full 2 s in a
      test if a shorter injected timeout proves the same thing.
- [ ] **Prove each one:** for F1–F3, break the behaviour in `src/ges2t/injectors/layout.py` (drop the
      guard, set `timeout=None`), confirm the new test now **fails**, then revert `layout.py`
      completely (`git status` must be clean of it). Paste the output. A guard test that has never
      been seen to fail is not a test.
- [ ] `uv run pytest` and **`uv run mypy src/ges2t tests`** (ADR 0019 covers tests) both pass. Report
      before/after counts; no test may be deleted, skipped, or weakened — only strengthened.
- [ ] `001-comment.md` records: what each test claimed vs what it actually checked, the falsification
      output for all three, and any **other** assertion in `tests/test_layout.py` that could not fail
      (e.g. a list iterated by the same test that asserts on it — the failure mode ticket 26 found in
      `tests/test_cli.py`). Record those even if not fixed.

## Notes

- The 2 s timeout is a constant; if the DoD's bounded test needs a shorter one, inject it rather than
  changing the production constant.
- Do **not** weaken the existing hand-verified behaviour to make a test convenient. The
  implementation is accepted as correct; this ticket only makes the suite able to tell.
