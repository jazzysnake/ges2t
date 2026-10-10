# Identifier constants: the command-verb family

- **Priority:** P2
- **Status:** to do
- **Complexity:** XS
- **Epic:** ../epic.md

## Current state / context

Ticket 48 centralized the identifier strings compared in `src/` into `ges2t.identifiers`, verified as
de-duplication only (values byte-identical). It **scoped out the command-verb family** — an honest,
recorded boundary, not an oversight — and ticket 48's review recorded it as residual drift risk:

- the socket `Command` verbs (`toggle`, `start`, `stop`, `cancel`, `status`, `ptt-start`, `ptt-stop`,
  `wake`, `listen`, …) are compared as **bare literals** in `cli.py`, `service.py` and `socket_proto.py`;
- `"toggle"` in particular is a **second spelling** of `HOTKEY_MODE_TOGGLE`'s value — the same
  one-thing-two-spellings shape as the `whisper.cpp`/`whisper_cpp` trap this epic exists to remove.

Ticket 48's review also recorded three doc nits in its `001-comment.md` (the mypy *before* count was not
stated; the leftovers table is over-inclusive for the scan's operator set; a new
`_LISTEN_SELECTOR_DATA` was omitted from the inventory).

## Definition of done

- [ ] The **command-verb strings** are defined once (in `ges2t.identifiers`, or wherever the `Command`
      enum/canonical set already lives — decide and justify) and the comparisons in `cli.py`,
      `service.py` and `socket_proto.py` use them. `"toggle"` is no longer a second spelling of the
      hotkey mode's value.
- [ ] **No behaviour change**: every command string on the wire is **byte-identical** — this is
      de-duplication, not a rename, and the socket protocol must not change. Show the values are
      unchanged.
- [ ] Re-run the ticket-48 AST scan and report the new **compared-literal count** (ticket 48 went
      56 → 41) plus what remains, honestly categorised. (The scan is a **verification step, not a
      committed test and not a hard gate** — see ticket 48's definition of done.)
- [ ] The three ticket-48 doc nits are **noted** in a new comment artifact (artifact files are
      append-only — do not rewrite ticket 48's `001-comment.md`), and corrected where the correction
      belongs to *this* ticket's own artifacts.
- [ ] `uv run pytest -q` and `uv run mypy src/ges2t tests` green; report counts; no test deleted,
      skipped or weakened.
- [ ] No new dependency, no linter.

## Notes

- Read [`../../../code-quality.md`](../../../code-quality.md) — this is rule 1 again, one family later.
- If the command-verb set turns out to be larger than a session, take the **bounded first pass** rule:
  the verbs actually **compared** (not the ones merely printed in help text).
- **No keystrokes, no clipboard, no windows, no microphone, no `sudo`, no package installs, no service
  changes, no network.**

## Related tickets

- depends on: ../centralize-identifier-literals/ticket.md
- related ADRs: ../../../adr/0016-cli-surface.md, ../../../adr/0019-code-style-typing-and-interfaces.md
