# Layout-aware text→keycode mapping (libxkbcommon)

- **Priority:** P1
- **Status:** in review
- **Complexity:** M
- **Epic:** ../epic.md

## Current state / context

`ydotool` 1.0.4 resolves **text** to keycodes with a hardcoded US-position table and **silently
drops non-ASCII** (source: `Client/tool_type.c`, `ascii2keycode_map`). The user's layout is `hu`,
so manual verification reproduced: `0`→`ö`, `-`→`ü`, `y`→`z` — matching
`xkbcli how-to-type --layout hu` exactly. There is no `--keymap` flag, no keymap files, no layout
env var, and no upstream fix (last release 2023; layout-aware typing is an open PR). AUR
`ydotool-git` is the same code.

**So `ges2t` must compute the key events itself.** This ticket builds that mapping in isolation as
**pure, testable logic** — it emits nothing and touches no daemon. Ticket 23 consumes it.

Read **`docs/adr/0020-layout-aware-keycode-mapping.md`** in full; it is the specification. Key
points: `libxkbcommon` 1.13.2 is **already installed** (KWin uses it); the reverse lookup is
`xkb_keymap_key_get_syms_by_level()` + `xkb_keymap_key_get_mods_for_level()` over
`min_keycode..max_keycode` × layout group × level; `dotool`'s `xkb/xkb.go` is a reference
implementation of the same algorithm.

## Definition of done

Create **only** `src/ges2t/injectors/keymap.py` and `tests/test_keymap.py`. Do not modify
`ydotool.py`, `config.py`, or anything else — other tickets own those. This module must **not**
import `subprocess`, `socket`, or anything that talks to a daemon.

- [ ] A frozen dataclass for one event: `KeyEvent(keycode: int, pressed: bool)`, plus a frozen
      result class `KeySequence(events: tuple[KeyEvent, ...], unreachable: tuple[str, ...])` —
      **no positional tuples** (ADR 0019).
- [ ] `XkbKeymap` loaded from a **keymap string** (`xkb_keymap_new_from_string`) and/or from
      layout+variant **names** (`xkb_keymap_new_from_names`). Both entry points, so tests need no
      `xkbcli` and production can use the live compositor keymap (ticket 24).
- [ ] `sequence_for(text: str) -> KeySequence`: for each character, find the minimal key
      sequence that produces it, and wrap it with the required modifier key down/up events.
      - **Modifiers must be pressed before** the key and **released after**, and released in
        reverse order. Verify ordering explicitly in a test.
      - **Choose the modifier mask without `Lock`.** The live `hu` keymap remaps Caps here
        (`caps:ctrl_modifier`), and Caps/Num state changes the base level.
      - Level-3 characters (AltGr) must work: `Mod5` = physical **`KEY_RIGHTALT`** (Linux keycode
        100). On `hu`, level-3 characters such as `„` need it.
      - Translate mask bits to physical modifier keycodes via `xkb_keymap_mod_get_name` /
        `mod_get_index` rather than hardcoding a mask→key table.
- [ ] **Characters with no direct access are reported, not dropped and not raised:**
      `unreachable` collects them so the caller can warn (ADR 0020's skip-and-warn rule). Do not
      attempt compose/dead-key lookup in this ticket; note in the comment that direct access is
      what is implemented and that dead-key composition is emitted as ordinary key events.
- [ ] **ctypes binding to `libxkbcommon.so.0` is preferred** — no new dependency (ADR 0020). Load
      lazily and raise a clear error if the library is missing. If the binding proves unreliable,
      `python-xkbcommon` (Arch `extra`, MIT) is an acceptable fallback **but you must justify it in
      your comment and note the added dependency**; do not add a dependency silently.
- [ ] `tests/test_keymap.py` covers, using keymaps compiled **from names** (no `xkbcli`, no
      machine dependency):
      - **genericity**: the same code maps correctly for **two different layouts** — assert the
        `hu` mapping of `0` differs from the `us` mapping of `0` (this is the whole point; a
        single-layout test would pass with a hardcoded table);
      - the confirmed `hu` corruptions are fixed: `0` must **not** resolve to the keycode that
        produces `ö`;
      - Hungarian `ő` / `ű` resolve to a direct keycode with no modifier (research: `AD11`=34,
        `BKSL`=51);
      - a level-3 character on `hu` includes `KEY_RIGHTALT` (100) in its sequence;
      - modifier ordering: for a shifted character, the events are shift-down, key-down,
        key-up, shift-up;
      - a character unreachable on the layout (e.g. `😀`) appears in `unreachable` and contributes
        **no** events;
      - ASCII round-trip sanity: every printable ASCII character resolves on `us`.
- [ ] `uv run pytest tests/test_keymap.py -q` passes, `uv run pytest` stays green, and
      **`uv run mypy src/ges2t tests`** exits 0 (ADR 0019 amended 2026-10-09: it covers tests).

## Notes

- **Do not hardcode any layout table.** The whole requirement is that the mapping is derived from
  the keymap, so any XKB layout works. A test that would still pass with a hardcoded `hu` table is
  a test failure.
- The mapping is valid only for the keymap it was computed from — recomputation per utterance is
  ticket 24/23's concern, but do not cache globally in a way that makes that impossible.
- Opaque `libxkbcommon` pointers mean the ctypes declarations need care with `restype`/`argtypes`
  (return the struct pointers as `c_void_p`). Memory: `xkb_keymap_unref`/`xkb_state_unref` must be
  called; do not leak in a long-running service.
- `xkb_keysym_to_utf32()` bridges keysyms to codepoints. Some keysyms map to multi-codepoint or
  zero-codepoint values — treat those as unreachable rather than guessing.

## Related tickets

- depends on: ../core-interfaces-and-fakes/ticket.md
- blocks: ../batched-key-emission/ticket.md
- related ADRs: ../../../adr/0020-layout-aware-keycode-mapping.md,
  ../../../adr/0003-text-injection-via-ydotool.md
