# 001 — Implementation comment: layout-aware text→keycode mapping

- **Ticket:** [ticket.md](ticket.md)
- **Branch:** `feat/xkb-keycode-mapping`
- **Status:** in review
- **Files:** `src/ges2t/injectors/keymap.py`, `tests/test_keymap.py` (nothing else was touched)

## What was built

A pure, daemon-free reverse lookup from Unicode text to Linux key events, derived
entirely from an XKB keymap compiled by `libxkbcommon` 1.13.2. No layout table is
hardcoded anywhere; the mapping comes from `xkb_keymap_key_get_syms_by_level` +
`xkb_keysym_to_utf32` for the keycode, and
`xkb_keymap_key_get_mods_for_level` for the minimal modifier mask.

## Final public signatures

```python
LINUX_KEYCODE_OFFSET: Final[int] = 8

@dataclass(frozen=True)
class KeyEvent:
    keycode: int          # Linux input-event code (KEY_*), NOT an XKB keycode
    pressed: bool

@dataclass(frozen=True)
class KeySequence:
    events: tuple[KeyEvent, ...]
    unreachable: tuple[str, ...]

class XkbError(Exception): ...
class XkbLibraryNotFoundError(XkbError): ...   # libxkbcommon.so.0 missing
class XkbKeymapCompileError(XkbError): ...      # bad keymap string / RMLVO names
class XkbKeymapClosedError(XkbError): ...       # use after close()

class XkbKeymap:
    @classmethod
    def from_string(cls, keymap: str) -> XkbKeymap: ...
    @classmethod
    def from_names(
        cls,
        layout: str,
        variant: str = "",
        *,
        rules: str | None = None,
        model: str | None = None,
        options: str | None = None,
    ) -> XkbKeymap: ...
    def sequence_for(self, text: str) -> KeySequence: ...
    def to_string(self) -> str: ...      # round-trip helper (see below)
    def close(self) -> None: ...         # idempotent
    def __enter__(self) -> XkbKeymap: ...
    def __exit__(self, *exc_info: object) -> None: ...
```

`from_names` is the test entry point; `from_string` is production's (ticket 24
passes the live `xkbcli dump-keymap-wayland` output, so `caps:ctrl_modifier` is
honoured). `sequence_for` emits, per character, modifier-downs (ascending
modifier index), key-down, key-up, modifier-ups (reverse order).

## Binding: ctypes, not python-xkbcommon

Used **ctypes** to `libxkbcommon.so.0` (loaded lazily; `XkbLibraryNotFoundError`
with an `sudo pacman -S libxkbcommon` message if absent). No new dependency, per
ADR 0020. The API surface is the ~20 stable, `@since 1.0.0` functions above and
was validated empirically before implementation. All opaque pointers are
`c_void_p` with explicit `argtypes` (omitting them truncates a 64-bit pointer to
`int` and segfaults — I hit this while prototyping). `python-xkbcommon` was not
needed.

## Units — READ THIS, ticket 23 (loud flag)

**`KeyEvent.keycode` is a Linux input-event code (`/usr/include/linux/input-event-codes.h`,
evdev), i.e. `xkb_keycode − 8`.** This is a deliberate resolution of an
inconsistency in the ticket:

- `ydotool key` consumes Linux/`KEY_*` codes: the man page says so, and the
  sibling `clipboard-paste-injector` ticket spells the paste chord as
  `KEY_LEFTCTRL` **29** + `KEY_V` **47** and says "raw keycodes, not text".
- The ticket's level-3 requirement, `KEY_RIGHTALT = 100`, is the Linux code.
- But the `ő`/`ű` research values `AD11=34`, `BKSL=51` are **XKB** keycodes.

No single unit satisfies both numbers. I chose Linux/evdev because that is what
ticket 23 emits and what avoids an off-by-8 at the wrong layer. Therefore
`ő` is emitted as keycode **26** (XKB 34) and `ű` as **43** (XKB 51); `0` on
`hu` is **41** (XKB 49, `TLDE`); `KEY_RIGHTALT` is **100** (XKB 108). The XKB
codes are retained in `tests/test_keymap.py` as named constants so the
relationship is explicit. If the orchestrator intended `KeyEvent` to carry XKB
keycodes instead, ticket 23 must subtract 8 before building argv — say so and I
will flip the boundary and the tests.

## `hu` vs `us` evidence (the genericity claim)

Real command (worktree, `uv run python`):

```
layout=us  '0' -> Linux keycode 11 (XKB 19), unreachable=()
layout=hu  '0' -> Linux keycode 41 (XKB 49), unreachable=()
hu 'ö': events=[(11, True), (11, False)] unreachable=()
hu 'ő': events=[(26, True), (26, False)] unreachable=()
hu 'ű': events=[(43, True), (43, False)] unreachable=()
hu '„': events=[(100, True), (24, True), (24, False), (100, False)] unreachable=()
hu '°': events=[(42, True), (100, True), (6, True), (6, False), (100, False), (42, False)] unreachable=()
```

Cross-checked against the installed tool:

```
$ xkbcli how-to-type --layout us 0   ->  KEYCODE 19  KEY NAME AE10  MODIFIERS [ ]
$ xkbcli how-to-type --layout hu 0   ->  KEYCODE 49  KEY NAME TLDE  MODIFIERS [ ]
```

`us` and `hu` give **different** keycodes for `0` (11 vs 41), which is exactly
what a hardcoded table cannot produce — the test that asserts this
(`test_same_character_resolves_differently_on_hu_and_us`) says so in its
docstring. The confirmed corruption is gone: on `hu`, `0` does not resolve to
the `AE10` key that produces `ö` (that key, Linux 11, is what `ö` correctly maps
to). `ő`/`ű` are direct and unmodified; `„` (`AD09`, Linux 24) uses AltGr;
`°` (`AE05`, Linux 6) uses Shift+AltGr and releases them in reverse.

## `Lock` and level-3

- **`Lock` is filtered out of every candidate mask** (`mod_get_index("Lock")`);
  if a level's *only* masks need `Lock` it is treated as inaccessible and skipped
  rather than pressing the Caps key. This is what keeps `caps:ctrl_modifier`
  from leaking: the hu letters are offered as `0x0` or `Shift|Lock`, and `0x0`
  wins. A test loads `hu` with `options="caps:ctrl_modifier"` and asserts the
  Caps keycode (58) is never emitted while Shift still is.
- **Level-3 / AltGr** is the mask bit for `Mod5` (index 7). It is translated to
  the physical key via `mod_get_name` → `RALT` → `xkb_keymap_key_by_name`, then
  converted to Linux **100**. Chosen mask is minimal by `(bit_count, value)`, so
  no gratuitous Ctrl/Alt/modifier is ever added.

## Judgement calls

1. **Modifier→key translation uses a small standard XKB key-name table**
   (`Shift→LFSH`, `Control→LCTL`, `Mod1→LALT`, `Mod4→LWIN`, `Mod5→RALT`, …).
   libxkbcommon exposes no public "which key depresses modifier N" API, so the
   name is the only portable handle; the mapping is a *physical position* map,
   not a layout table (those names exist in every pc keymap). Every choice is
   **verified with a real `xkb_state`** that it depresses exactly that modifier,
   and falls back to scanning the keymap if not — that is why `hu` `Mod5` gets
   `RALT` (108) rather than the `<LVL3>` key (92) that also sets `Mod5`, and why
   `us` (where `RALT` is `Mod1`) still resolves a `Mod5` requirement through the
   scan.
2. **`unreachable` is deduplicated in first-seen order.** A repeated emoji
   should warn once, not once per occurrence. `events` still records every
   reachable character in order.
3. **Zero/multi-codepoint keysyms are unreachable**, and dead keys are not
   composed here (documented in the module docstring). Dead keys still emit as
   ordinary events; compose lookup is intentionally out of scope for this ticket.
4. **`to_string()` was added** (not in the DoD) so `from_string` can be tested
   hermetically — `test_keymap_can_be_reloaded_from_its_serialized_string`
   serializes a compiled `us` keymap and recompiles it, with no `xkbcli`. It
   frees the `malloc`'d string with libc `free`, as libxkbcommon requires. It is
   also useful to ticket 24 for logging the live keymap.
5. **Caching is per instance, never global.** The reverse map is built lazily on
   first `sequence_for` and cached on the object, because the keymap is
   immutable. A stale instance is stale — ticket 24/23 must build a fresh
   `XkbKeymap` per utterance (ADR 0020), which the module docstring says
   explicitly.
6. `_num_mods` comes from `xkb_keymap_num_mods`; the mask-buffer for
   `get_mods_for_level` grows from 16 to 256 if ever saturated.

## Memory management

`xkb_keymap_unref` and `xkb_context_unref` are called in `close()` (idempotent,
also via `__exit__`, and in `__del__` wrapped so a finalizer never raises). Every
temporary `xkb_state` is created and unref'd inside the helper that needs it
(`finally`), so nothing leaks in a long-running service. No global keymap.

## Anything the orchestrator must know

- **Deviated on the keycode unit** (see above): events are Linux/evdev, so ticket
  23 can pass `event.keycode` straight to `ydotool key KEYCODE:PRESSED` with no
  offset. `ő`/`ű` are 26/43 here, not the ticket's 34/51 (those are XKB codes).
- Added `XkbKeymap.to_string()` beyond the DoD. It is additive and tested.
- Ticket 23 can consume `XkbKeymap.sequence_for(text)` directly; it does not need
  anything else from this module.

## Verification (real output)

```
$ uv run pytest tests/test_keymap.py -q
.....................                                                    [100%]
21 passed in 0.08s

$ uv run pytest
368 passed in 2.12s

$ uv run mypy src/ges2t tests
Success: no issues found in 31 source files
```

The suite compiles keymaps from names only; it needs no `xkbcli`, no session, no
daemon, and touches no `/dev/uinput`.
