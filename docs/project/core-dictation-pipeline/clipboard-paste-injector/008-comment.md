# 008 — comment: the user's manual verification (ticket 25's DoD gate)

- **Ticket:** [ticket.md](ticket.md)
- **Who ran it:** the **user** — this is the ticket's user-owned DoD step, not an agent run
- **Ticket 36 fix in place:** yes; this is the first real-`wl-copy` evidence for it

## Configuration verified

```toml
[injection]
backend = "clipboard"

[injection.clipboard]
paste_keys = ["ctrl+shift+v"]
```

## Procedure and result

1. Marker placed on the clipboard: `printf 'MY-ORIGINAL-CLIPBOARD' | wl-copy`.
2. Dictated a short phrase into a focused **nvim**, using the hotkey toggle.
3. Checked both outcomes.

**Result: PASS.**

- **The phrase pasted.** This is the half that had never worked: before ticket 36 the write
  succeeded, the paste chord never ran, and the injection was reported as a failure.
- **The pre-existing clipboard was restored.** `wl-paste` returned the original marker, so the
  read → copy → paste → restore sequence completes against the real `wl-copy`.

This single run therefore verifies: the real `wl-copy` write, the real paste chord through
`ydotoold`, and the deferred restore — none of which any test could reach.

## The `paste_keys` observation, and why it matters more than it sounds

The user used **`ctrl+shift+v` from the start**, not the shipped default `["ctrl+v"]`, reasoning
that nvim needs it and that in their experience the same chord also works in browsers and regular
editors. It worked in every target they used, which is why they saw no reason to also test
`ctrl+v`.

Two consequences:

1. **`ctrl+shift+v` covered both DoD targets in one configuration** — the editor *and* the terminal
   (nvim is a terminal application), so the "two passes with different config" plan was
   unnecessary for this user.
2. **The shipped default `["ctrl+v"]` has now been exercised by nobody.** The only chord with real
   evidence behind it is `ctrl+shift+v`. That is an argument for reconsidering the default rather
   than for assuming `ctrl+v` is safe because it is conventional.

Honest limits on that argument:

- **Wall time:** the user reports it **"was fast enough"** — a qualitative pass, not a stopwatch number.
  No timing was recorded, so this is a usability judgement rather than a measurement. Low risk on the
  mechanism (O(1) write plus one chord, with a known ~0.4 s restore delay), but it is not evidence and
  is not written up as such.
- **Not universal:** in most GUI toolkits `ctrl+shift+v` means *paste as plain text*. For dictation
  that is arguably the **better** behaviour (no rich formatting carried in), but it is a different
  binding from `ctrl+v`, not a superset, and some applications may leave it unbound.
- **One user, several applications** is strong practical evidence and not a survey.

## Related

- the fix this verifies: `../clipboard-injection-timeout/ticket.md` (ticket 36)
- the mechanism and its residuals: `007-comment.md`
