# Change the `paste_keys` default to `ctrl+shift+v`

- **Priority:** P1 (v1's fresh-machine first experience)
- **Status:** done
- **Complexity:** XS
- **Epic:** ../../core-dictation-pipeline/epic.md

## Current state / context

The shipped default is `paste_keys = ["ctrl+v"]`. **That value has been exercised by nobody.** The only
chord with real evidence behind it is `ctrl+shift+v`, from the user's manual verification of ticket 25
(`../clipboard-paste-injector/008-comment.md`):

- it worked **in every target the user tried** — nvim, and in their experience browsers and regular
  editors — so **one configuration covered both DoD targets** (an editor *and* a terminal), and the
  planned second pass with a different chord was unnecessary;
- `ctrl+v`, by contrast, **fails in terminals**, which are a first-class target: the epic's exit test
  explicitly names typing into "a text editor **and a terminal**";
- in most GUI toolkits `ctrl+shift+v` means **paste as plain text**, which for dictation is arguably
  *better* — no rich formatting is carried in.

**Decision taken by the user: change the default to `["ctrl+shift+v"]`.**

The honest counterweight, which the docs must carry rather than hide: `ctrl+shift+v` is a **different
binding, not a superset**. An application that leaves it unbound pastes **nothing**, whereas `ctrl+v`
fails *predictably* in terminals — a limitation that is already documented. So the change trades one
known failure mode for a rarer, less predictable one, in exchange for covering both targets by default.

## Definition of done

- [ ] The default is `["ctrl+shift+v"]` **everywhere it is declared** — the injector's own default, the
      `ClipboardConfig` default, and every docstring/module document that states it. Search for
      `ctrl+v` rather than fixing only the one you remember; a stale mention is how a default becomes
      two different defaults.
- [ ] A test **pins the new default** (constructing the injector without `paste_keys` must yield
      `["ctrl+shift+v"]`). Falsify it: change the default back and show the test fails.
- [ ] Tests that assert the *old* default are updated to the new one, not deleted.
- [ ] Documentation states the caveat plainly: **if your application does not bind `ctrl+shift+v`,
      paste nothing happens — set `paste_keys = ["ctrl+v"]` instead.** Also note what
      `ctrl+shift+v` usually means in a GUI (paste as plain text), so the behaviour is not a surprise.
- [ ] `uv run pytest`, `uv run mypy src/ges2t tests` green.

## Notes

- **This is a default change only.** Do not change the mechanism, the chord parser, the error contract,
  or the restore behaviour. The user's own config sets `paste_keys` explicitly, so their setup is
  unaffected either way.
- Ticket 19 (docs) will document this; keep the wording consistent with
  `../clipboard-paste-injector/008-comment.md`.
- Do **not** add a fallback chord list (`["ctrl+v", "ctrl+shift+v"]`): the injector sends **every**
  configured chord, so listing both would paste **twice**.

## Related tickets

- arises from: ../../core-dictation-pipeline/clipboard-paste-injector/ticket.md (ticket 25) and its
  `008-comment.md`; ../../core-dictation-pipeline/clipboard-injection-timeout/ticket.md (ticket 36)
- feeds: ../../live-dictation-ux/user-documentation-and-setup-guide/ticket.md (ticket 19)
- related ADRs: ../../../adr/0003-text-injection-via-ydotool.md (amended)
