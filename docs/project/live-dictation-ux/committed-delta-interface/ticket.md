# Expose the committed delta on the streaming segment

- **Priority:** P1
- **Status:** done
- **Complexity:** S
- **Epic:** ../epic.md

## Current state / context

`drive()` emits each non-final segment as the committed prefix concatenated with the unconfirmed tail:

```python
text = transcript + pending
yield TranscriptionSegment(text=text, language=language, is_final=False, pass_ms=None)
```

That merges two values with **different lifetimes**. `pending` is **replaced** on every pass;
`transcript` only ever **grows**. The committed prefix is therefore the final transcript's prefix by
construction — but **the boundary is destroyed before any consumer sees it**.

It cannot be recovered downstream. The longest common prefix of two successive partials is **not** the
committed prefix, because a `pending` word that happens to agree across two consecutive passes is
indistinguishable from a committed one. Inferring it would type speculative text into the user's
document, which is exactly the failure this feature must not have.

So the two values must be carried **separately**.

## Definition of done

- [ ] `TranscriptionSegment` carries the committed text separately from the pending tail. Prefer an
      explicit, named representation over two bare `str` fields that are trivially confusable — see
      the reasoning in ../../../adr/0019-code-style-typing-and-interfaces.md.
- [ ] `drive()` populates it. **Be precise about delta versus cumulative**: `pass_.committed` is a
      **per-pass delta**, while the final segment's text is the **whole accumulated transcript**.
      Mixing the two naively duplicates the entire utterance — this is the single most likely way to
      get this ticket wrong.
- [ ] The streaming session and the service expose the committed value to the UI layer **without
      changing any currently observable behaviour**: the tray tooltip must still show the same live
      text (`committed + pending`) it shows today.
- [ ] **No injection change in this ticket.** When it is done, nothing is typed during dictation,
      exactly as now.
- [ ] Tests pin all four of: (a) the committed value is **append-only** across segments; (b) the final
      segment's text equals the concatenation of the committed parts; (c) the **display** string is
      byte-for-byte unchanged; (d) a change confined to `pending` does **not** advance the committed
      value.
- [ ] `uv run pytest` and `uv run mypy src/ges2t tests` both green.

## Notes

- Deliberately separable from the injector: this is an interface change with its own invariant, and it
  is the one part of the feature that can be fully tested without touching injection at all.
- Read `_drive()` in `src/ges2t/streaming.py` completely before changing anything — the `+=` fold and
  the flush path are the whole story, and the final `flush()` after the loop is easy to miss.
- This ticket does **not** amend ADR 0011; it changes no behaviour. The amendment belongs to
  ../incremental-typing/ticket.md.

## Related tickets

- depends on: ../commit-cadence-diagnostic/ticket.md (its numbers decide whether we proceed at all)
- blocks: ../incremental-typing/ticket.md
- related ADRs: ../../../adr/0011-streaming-sliding-window.md,
  ../../../adr/0010-transcriber-interface-design.md,
  ../../../adr/0019-code-style-typing-and-interfaces.md
