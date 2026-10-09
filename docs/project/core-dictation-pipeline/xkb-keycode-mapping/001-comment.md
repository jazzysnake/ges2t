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

---

## Addendum 2026-10-09 — blocking finding fixed (canonical keysyms rank first)

**Resolves `002-review.md` §2.** The review passed the ctypes binding, lifecycle,
caching, error types, scope and tests; only the reverse-lookup ranking changed.
No test was deleted, skipped or weakened.

### What changed in the ranking

The reverse lookup compared `xkb_keysym_to_utf32(sym) == codepoint` and ranked
matches by `(mask.bit_count(), mask, level, layout, keycode)`. A keypad or
`XF86Numeric*` keysym that merely *aliases* a character
(`xkb_keysym_to_utf32(KP_Equal) == '='`, `xkb_keysym_to_utf32(XF86Numeric0) ==
'0'`) sits on a level with mask `0x0`, so it won the modifier-count tie-break
over the real key.

The fix binds `xkb_utf32_to_keysym` and prepends a **canonical flag** to the
rank:

```python
def _is_canonical_keysym(self, sym: int, codepoint: int) -> bool:
    canonical = int(self._lib.xkb_utf32_to_keysym(codepoint))
    return sym == canonical or sym == (0x01000000 | codepoint)

# in _build_resolutions, per sym:
alias = 0 if self._is_canonical_keysym(sym, codepoint) else 1
rank = (alias, mask.bit_count(), mask, level, layout, keycode)
```

- `alias` is `0` for a canonical keysym and `1` otherwise, so a canonical match
always wins, whatever its modifier cost; the existing
`(modifier count, mask, level, layout, keycode)` tie-break is unchanged and
still breaks ties among equally canonical matches.
- Aliases are **not rejected**. The `0x01000000 | codepoint` clause is exactly
what keeps the `de` `…`/`′`/`″` alias-only characters resolving (see below).
- `_Resolution.rank` widened to a 6-tuple; the module docstring's resolution
rule and unit statement were updated (`KeyEvent.keycode` is Linux/evdev and is
passed straight to `ydotool key` with **no** offset).

The ticket text was corrected too: the `ő`/`ű` requirement now states the
emitted Linux `KEY_*` codes `26`/`43` (`AD11`=34/`BKSL`=51 are XKB, research
only).

### Cross-check vs `xkbcli how-to-type` — 0 disagreements

Independent re-run. `how-to-type` lists **every** direct access (keycode +
modifiers); the module must emit a keycode from that set. A disagreement is
"module unreachable while `how-to-type` has a direct access", or "module's
Linux keycode (`XKB − 8`) is not in `how-to-type`'s direct-access set".

```
xkbcli how-to-type --layout <hu|us|de|fr> -- <char>
xkbcli how-to-type --keymap=/tmp/live_keymap.xkb -- <char>
```

`/tmp/live_keymap.xkb` is `xkbcli dump-keymap-wayland` (1560 lines,
`pc_hu_inet(evdev)_capslock(ctrl_modifier)`), dumped fresh.

Bank (the literal §2 set, 71 characters):

```
0123456789abcxyzöüóőúűéáíÖÜÓŐÚŰÉÁÍ-=[]\;',./`"|@#&*~^!$%()_+{}:<>? „”€°
```

Result, per layout:

```
hu:                          71 chars, 0 disagreements
us:                          71 chars, 0 disagreements
de:                          71 chars, 0 disagreements
fr:                          71 chars, 0 disagreements
live (hu+caps:ctrl_modifier): 71 chars, 0 disagreements
TOTAL:                      355 chars, 0 disagreements
```

Every one of the review's 25 four-layout disagreements (plus the live keymap's
5, 30 in total) now agrees. Formerly-disagreeing characters, old module keycode
→ new module Linux keycode (XKB in parentheses, with the modifier from
`how-to-type`):

| Layout | char | old module (alias) | new module (Linux) | `how-to-type` |
|---|---|---|---|---|
| hu | `=` | 117 `KP_Equal` | 8 | XKB 16 `AE07` Shift |
| hu | `/` | 98 `KP_Divide` | 7 | XKB 15 `AE06` Shift |
| hu | `#` | 523 `XF86NumericPound` | 45 | XKB 53 `AB02` Mod5 |
| hu | `*` | 55 `KP_Multiply` | 53 | XKB 61 `AB10` Mod5 |
| hu | `+` | 78 `KP_Add` | 4 | XKB 12 `AE03` Shift |
| us | `#` | 523 `XF86NumericPound` | 4 | XKB 12 `AE03` Shift |
| us | `*` | 55 `KP_Multiply` | 9 | XKB 17 `AE08` Shift |
| us | `+` | 78 `KP_Add` | 13 | XKB 21 `AE12` Shift |
| de | `=` | 117 `KP_Equal` | 11 | XKB 19 `AE10` Shift |
| de | `/` | 98 `KP_Divide` | 8 | XKB 16 `AE07` Shift |
| de | `*` | 55 `KP_Multiply` | 27 | XKB 35 `AD12` Shift |
| fr | `0`…`9` | 512…521 `XF86Numeric0`… | 11,2,3,4,5,6,7,8,9,10 | XKB 19,10,…,18 Shift |
| fr | `.` | 121 `KP_Decimal` | 51 | XKB 59 `AB08` Shift |
| fr | `/` | 98 `KP_Divide` | 52 | XKB 60 `AB09` Shift |
| fr | `#` | 523 `XF86NumericPound` | 4 | XKB 12 `AE03` Mod5 |
| fr | `+` | 78 `KP_Add` | 13 | XKB 21 `AE12` Shift |
| live | `= / # * +` | same 5 as `hu` | 8, 7, 45, 53, 4 | same as `hu` |

For the pre-fix ranking the same script reports 30 disagreements (hu 5, us 3,
de 3, fr 14, live 5); after the fix it is 0. Note the §2 prose says 77 chars per
layout while the literal bank it lists is 71; I used the literal bank.

### Regression tests added

- `test_hu_equal_uses_the_real_key_not_keypad_equal`: `hu` `=` resolves to
  Shift + Linux 8 (XKB `AE07`) and never emits `KEY_KPEQUAL` (117).
- `test_fr_zero_uses_the_main_row_not_the_numeric_keypad`: `fr` `0` resolves
  to Shift + Linux 11 (XKB `AE10`) and never emits `KEY_NUMERIC_0` (512).
- `test_de_unicode_keysym_alias_still_resolves` (`…`/`′`/`″`): the alias-only
  Unicode-keysym case still resolves and is not turned into `unreachable`.

### Falsification (ranking reverted, then restored)

Neutralizing only the canonical flag (`alias = 0`) reproduces the bug:

```
$ uv run pytest tests/test_keymap.py -q
...
E       assert [(117, True), (117, False)] == [(42, True), ..., (42, False)]
E       assert [(512, True), (512, False)] == [(42, True), ..., (42, False)]
FAILED tests/test_keymap.py::test_hu_equal_uses_the_real_key_not_keypad_equal
FAILED tests/test_keymap.py::test_fr_zero_uses_the_main_row_not_the_numeric_keypad
2 failed, 24 passed in 0.22s
```

`hu` `=` came back as `KEY_KPEQUAL` (117) and `fr` `0` as `KEY_NUMERIC_0`
(512), i.e. exactly the bug. The change was restored afterwards.

### Verification (real output, after the fix)

```
$ uv run pytest tests/test_keymap.py -q
26 passed in 0.17s          # was 21; +5 regression cases

$ uv run pytest -q
373 passed in 1.51s         # was 368

$ uv run mypy src/ges2t tests
Success: no issues found in 31 source files

$ uv run ruff check src/ges2t/injectors/keymap.py tests/test_keymap.py
All checks passed!
```

(One `ruff format` line-wrap in the new test was fixed; `keymap.py` was already
non-`ruff format` clean before this change, so it was left alone rather than
reformatted wholesale.)

### For the re-reviewer

- Confirm the canonical predicate is `sym == xkb_utf32_to_keysym(cp) or sym ==
  (0x01000000 | cp)`, and that the `0x01000000|cp` clause is what keeps `de`
  `…`/`′`/`″` (which `how-to-type` itself cannot resolve) working.
- Confirm no `KP_*`/`KEY_NUMERIC_*` code appears for `hu`/`us`/`de`/`fr` in the
  cross-check above, and that the module's chosen key is in `how-to-type`'s
  full direct-access set (the set, not just its first row).
- The keycode unit is unchanged and remains Linux/evdev; ticket 23 still adds
  no offset.
