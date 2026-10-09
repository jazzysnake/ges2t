# Clipboard-paste injector (configured alternative)

- **Priority:** P1
- **Status:** in progress
- **Complexity:** S
- **Epic:** ../epic.md

## Current state / context

ADR 0003 always reserved clipboard-paste as a **configured alternative** — "one or the other
configured, not a fallback" — and the amendment of 2026-10-09 promotes it from hypothetical to a
real second `Injector`. Two concrete motivations:

1. It is **layout-independent** and **O(1)**: one `wl-copy` plus one paste keychord, tens of
   milliseconds regardless of transcript length, versus `ydotool`'s per-character emission.
2. It is the documented **escape hatch for text the layout cannot type** (ADR 0020's unreachable
   codepoints — an emoji, or a character absent from the layout and its compose tables).

`wl-copy` and `wl-paste` are already installed. This does **not** become an automatic fallback:
silently switching mechanism changes behaviour the user did not ask for (ADR 0003).

## Definition of done

Create `src/ges2t/injectors/clipboard.py` and `tests/test_clipboard_injector.py`.

- [ ] `ClipboardPasteInjector` satisfies the `Injector` protocol (`inject(text: str) -> None`) and
      is selected by `injection.backend = "clipboard"`.
- [ ] **Text rules are identical to `ydotool`'s** (ADR 0012): empty/whitespace-only input performs
      **no** clipboard write and **no** paste; exactly one trailing space when enabled; the text is
      otherwise unmodified. Reuse the shared `decide_injection` logic from
      `src/ges2t/injectors/ydotool.py` rather than reimplementing the table — one implementation,
      two emitters. If that means moving the helper, do it in a way that keeps ticket 7's tests
      passing unchanged.
- [ ] Sequence: read the current clipboard (`wl-paste`, tolerating an empty/absent selection so
      text-type clipboards that cannot be read do not break injection) → `wl-copy` the payload →
      send the paste keychord → **restore the previous clipboard** after the paste has had time to
      land. The restore must be **best-effort and non-fatal**: failing to restore must never fail
      the injection, and must never overwrite the payload before the application has read it.
- [ ] **The paste keychord is sent as raw keycodes, not text**, so it is layout-safe — use the
      event emitter from ticket 23 (`ctrl+v` = `KEY_LEFTCTRL` 29 + `KEY_V` 47, e.g.
      `29:1 47:1 47:0 29:0`). Do not route the chord through `ydotool type`.
- [ ] `paste_keys` comes from `[injection.clipboard]` (ticket 24 owns the config plumbing),
      defaulting to `["ctrl+v"]`. **Document in the comment and in the config reference that most
      terminals need `ctrl+shift+v`** — this is the known cost of the mechanism and the user
      explicitly wants terminal support, so it must be discoverable rather than a mystery.
- [ ] Failures raise clear, actionable `Injector` errors: `wl-copy`/`wl-paste` missing; no
      Wayland session / no clipboard; daemon unreachable for the keychord; non-zero exits including
      the children's stderr.
- [ ] An injectable runner seam so tests never touch the real clipboard.
- [ ] `tests/test_clipboard_injector.py` covers: the empty/whitespace/trailing-space table (assert
      **no** process is spawned for empty input); the call order (read → copy → paste → restore);
      the keychord is raw keycodes and **not** in argv as text; a failing restore does not fail
      injection; and every error branch, including that stderr reaches the message.
- [ ] `uv run pytest`, `uv run mypy src/ges2t tests` (ADR 0019 covers tests) both green.
- [ ] **Manual, once, reported in `001-comment.md`:** with the daemon running, paste a short phrase
      into a focused **text editor** and into a focused **terminal** (using `ctrl+shift+v` there),
      and report the wall time for each. State explicitly whether the pre-existing clipboard was
      restored. **Ask the user before pasting** — this writes to their clipboard and types into the
      focused window, and unlike the pure typing path it also clobbers their clipboard content.

## Notes

- Do **not** make this the default backend in this ticket. Backend selection stays an explicit
  configuration choice; the default is decided by evidence after ticket 23's measurement.
- Do not implement an automatic fallback from `ydotool` to `clipboard` on failure — that is
  explicitly rejected (ADR 0003). If typing cannot represent some characters, the *warning* is the
  signal, and the user switches backends deliberately.
- Restoring the clipboard is a courtesy, not a guarantee: a long transcript must not be silently
  replaced by the previous clipboard before the target app has pasted it. Prefer a bounded delay
  over a fixed long sleep, and never block the service's command loop.

## Related tickets

- depends on: ../layout-detection-and-config/ticket.md, ../batched-key-emission/ticket.md
- blocks: ../../live-dictation-ux/user-documentation-and-setup-guide/ticket.md
- related ADRs: ../../../adr/0003-text-injection-via-ydotool.md,
  ../../../adr/0020-layout-aware-keycode-mapping.md,
  ../../../adr/0012-verbatim-output-and-trailing-space.md
