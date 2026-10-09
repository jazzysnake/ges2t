# Set the injection key-delay default from the measurement

- **Priority:** P1
- **Status:** in review
- **Complexity:** XS
- **Epic:** ../epic.md

## Current state / context

Ticket 23 (`../batched-key-emission/ticket.md`) replaced `ydotool type` with batched `ydotool key` and
measured it. On the reference machine, for a 125-character phrase, byte-compared against the capture:

| delay ms | wall ms | ms/char | byte-perfect | ADR 0001 budget (< 1 s) |
|---:|---:|---:|:--:|:--|
| 12 | 3100.7 | 24.81 | yes | **no** |
| 6 | 1595.4 | 12.76 | yes | **no** |
| **0** | **47.9** | **0.38** | **yes** | **yes** |

The cost is linear (`delay × 2 × 126` events — `ydotool key` sleeps once per event, down *and* up, not
per character), so **every non-zero delay reintroduces 252 sleeps**. The shipped default is
`DEFAULT_KEY_DELAY_MS = 12` in `src/ges2t/injectors/layout.py`, left **provisional** by ticket 24
specifically so this measurement could set it ("ticket 23's measurement owns the final value").

**With the default at 12 the product ships over its own budget** — 3.1 s per phrase — even though the
implementation can do 47.9 ms. That is the whole point of this ticket.

**Why `0` and not a small margin.** `0`, `6` and `12` were **measured**; `6` and `12` are both over
budget. `0` is therefore the only **measured** in-budget value. Choosing, say, `2 ms` would mean
shipping an extrapolated guess from the linear model — fine arithmetic, but not evidence. The
measurement's own caveat is that the capture target was a **pty** (`head -c`), not a real editor, so
we cannot claim `0` is universally drop-proof; that is exactly why the knob stays documented rather
than why we should guess a margin.

Relevant decisions: ADR 0001 (the budget), ADR 0020 (batched emission), ADR 0009 (config).

## Definition of done

- [ ] `DEFAULT_KEY_DELAY_MS` in `src/ges2t/injectors/layout.py` becomes **`0`**, with a comment
      stating the measured basis (47.9 ms / 125 chars, byte-perfect, versus 3.1 s at 12) and citing
      ticket 23's `001-comment.md`. **This is a value change only** — do not restructure the constant,
      rename it, or change how it is consumed.
- [ ] A test **pins** the default to the measured value, so a future change to it is a deliberate act
      rather than a silent drift. The test's comment must say the value comes from measurement, so
      nobody "tidies" it.
- [ ] `[injection.ydotool] key_delay_ms` stays a **documented user override**; nothing about the
      override behaviour changes. Confirm the absent-block path still means "all defaults"
      (`.get(..., {})`, not `KeyError`).
- [ ] Anything that states the default in prose is updated if it exists (a config example, a docstring
      or a comment). **Do not** write new user documentation — ticket
      `../../live-dictation-ux/user-documentation-and-setup-guide/ticket.md` owns that.
- [ ] `uv run pytest` and **`uv run mypy src/ges2t tests`** both green (ADR 0019 covers `tests/`).
      Report before/after counts; no test may be deleted, skipped or weakened.
- [ ] `001-comment.md` records the measurement basis, the value chosen, and the caveat that the
      capture target was a pty rather than a real application.

## Notes

- **Do NOT re-measure and do NOT inject keystrokes.** The measurement required a focused scratch
  window and explicit user authorisation; re-running it would take over the user's screen. Work from
  the recorded numbers in ticket 23's `001-comment.md`.
- **Do NOT change the emission mechanism** in `src/ges2t/injectors/ydotool.py` — that ticket is
  reviewed, merged and done. This ticket touches the default value and its test, nothing else.
- If a user later reports a dropped character in a specific application, raising `key_delay_ms` is the
  intended fix and the knob is already there. Note that in the comment so the reasoning is not lost.
- `--self-test` / measurement scripts live under `docs/` and are not packaged; do not move them.
