# 002 — Review: layout-aware text→keycode mapping (ticket 22)

- **Ticket:** [ticket.md](ticket.md)
- **Branch:** `feat/xkb-keycode-mapping`, HEAD `d58a50d` (base `36aad65`; reviewed with
  `git diff main...feat/xkb-keycode-mapping`, three dots)
- **Reviewer:** independent review pass
- **Status:** **request changes** — one blocking finding (keypad/`XF86Numeric`
  function-key selection). The keycode unit, genericity mechanism, memory
  safety, lifecycle and typing are otherwise sound.

## Verdict

The module is well built. The ctypes binding is careful, every `malloc`'d
string and compiled keymap is released, `close()` is idempotent, the reverse map
is per-instance, imports are exactly the allowed four, and the layer-3/`Mod5`
and `Lock` handling is correct. The **keycode unit is right**: `KeyEvent.keycode`
is a Linux/evdev `KEY_*` code and ticket 23 must pass it straight to
`ydotool key`. The ticket's `ő/ű = 34/51` are XKB keycodes and must be corrected.

**One blocking defect:** the reverse lookup accepts *any* keysym whose
`xkb_keysym_to_utf32` equals the target codepoint. That admits keypad
(`KP_*`) and `XF86Numeric*` **function keysyms**, which need no modifier, so they
outrank the real character key. The result disagrees with
`xkbcli how-to-type` on ordinary characters across `hu`, `us`, `de`, `fr` and the
live keymap — including digits and `= + * / #`. That is the same silent-wrongness
class this ticket exists to eliminate. Details and the minimal fix are in §2.

## 1. Binding ruling: the keycode unit is evdev, not XKB

**Ruling.** `KeyEvent.keycode` **is** a Linux input-event code (`KEY_*` from
`/usr/include/linux/input-event-codes.h`, evdev), i.e. `xkb_keycode − 8`. Ticket
23 must pass `event.keycode` **directly** into `ydotool key <code>:1 <code>:0`
argv with **no offset**. The ticket's literal `AD11=34`/`BKSL=51` are **XKB**
keycodes and should be corrected to the Linux codes **26** (`ő`) and **43**
(`ű`), with the XKB numbers kept only as a research annotation. The ticket's
`KEY_RIGHTALT = 100` is already evdev and stays.

**Evidence (documented contract — conclusive).**

- `man ydotool`, `key` section: `key [-d,--key-delay <ms>] [<KEYCODE:PRESSED> ...]`.
  It gives the example `28:1 28:0 means pressing on the Enter button` and
  `42:1 38:1 38:0 24:1 24:0 38:1 38:0 42:0 - "LOL"`, then says verbatim:
  `See '/usr/include/linux/input-event-codes.h' for available key codes (KEY_*)`.
- `ydotool key --help` / the installed binary repeat the same
  `input-event-codes.h` reference; the package ships no keymap table.
- Header constants on this machine (`/usr/include/linux/input-event-codes.h`):
  `KEY_O 24`, `KEY_ENTER 28`, `KEY_LEFTCTRL 29`, `KEY_A 30`, `KEY_L 38`,
  `KEY_LEFTSHIFT 42`, `KEY_V 47`, `KEY_KPASTERISK 55`, `KEY_KPPLUS 78`,
  `KEY_KP0 82`, `KEY_KPSLASH 98`, `KEY_RIGHTALT 100`, `KEY_KPEQUAL 117`,
  `KEY_LEFTMETA 125`. The man-page examples resolve exactly (`Enter`=28,
  `LOL`=42/38/24), which only holds in evdev.
- `xkbcli how-to-type --layout hu -- ő` prints `KEYCODE 34`, and `ű` prints
  `51`. Those are XKB keycodes; the module emits `xkb_keycode − 8` = 26/43.
  `KEY_RIGHTALT = 100` is evdev (`XKB 108`), so the ticket's own numbers cannot
  all be XKB. The ticket is internally inconsistent; evdev is the only reading
  that satisfies `KEY_RIGHTALT = 100` *and* what `ydotool key` consumes.
- **Observed round trip:** one bounded live probe (see the probe section below)
  passed the module's evdev codes straight to `ydotool key` and produced the
  test string byte-for-byte. This corroborates the documented contract; the
  ruling does **not** rest on documentation alone, but the observed part is a
  single bounded probe, so the documentation/header evidence is the load-bearing
  part.

**Strength.** Conclusive from the man page + headers (`ydotool key`'s unit is
evdev); independently corroborated by one observed round trip. I corrected the
`34/51` reading: those are XKB and belong only in research notes.

## 2. Genericity cross-check against `xkbcli how-to-type` — BLOCKING finding

### Method

`xkbcli` (1.13.2) was run per character, independently of the module:

```
xkbcli how-to-type --layout <hu|us|de|fr> -- <char>
xkbcli how-to-type --keymap=<live dump>   -- <char>   # live keymap
```

`how-to-type` reports **XKB** keycodes, so the comparison converts with
`Linux = XKB − 8`. The live keymap came from `xkbcli dump-keymap-wayland`
(1560 lines, `pc_hu_inet(evdev)_capslock(ctrl_modifier)`; D-Bus
`org.kde.KeyboardLayouts.getLayout()` = `hu`, index 0). Test bank: digits
`0–9`; `abcxyz`; `öüóőúűéáí`; their uppercase forms; the ASCII punctuation set
`-=[]\;',./`"|@#&*~^!$%()_+{}:<>?`; space; and `„”€°`.

All other tested characters **agreed** with `how-to-type` (including the `hu`
repositions `0`→TLDE, `-`→AE11, `y`→`Z` position, `z`→`Y` position, the
Hungarian `ö/ő/ű`, level-3 `„`, and Shift+level-3 `°`). The disagreements are
enumerated below.

### Disagreement table (complete)

| Layout | char | module (Linux/XKB) | module keysym (alias) | `how-to-type` (XKB, key name) | h2t mods |
|---|---|---|---|---|---|
| hu | `=` U+003D | 117 / 125 | `KP_Equal` | 16 `AE07` | Shift |
| hu | `/` U+002F | 98 / 106 | `KP_Divide` | 15 `AE06` | Shift |
| hu | `#` U+0023 | 523 / 531 | `XF86NumericPound` | 53 `AB02` | Mod5 |
| hu | `*` U+002A | 55 / 63 | `KP_Multiply` | 61 `AB10` | Mod5 |
| hu | `+` U+002B | 78 / 86 | `KP_Add` | 12 `AE03` | Shift |
| us | `#` U+0023 | 523 / 531 | `XF86NumericPound` | 12 `AE03` | Shift |
| us | `*` U+002A | 55 / 63 | `KP_Multiply` | 17 `AE08` | Shift |
| us | `+` U+002B | 78 / 86 | `KP_Add` | 21 `AE12` | Shift |
| de | `=` U+003D | 117 / 125 | `KP_Equal` | 19 `AE10` | Shift |
| de | `/` U+002F | 98 / 106 | `KP_Divide` | 16 `AE07` | Shift |
| de | `*` U+002A | 55 / 63 | `KP_Multiply` | 35 `AD12` | Shift |
| fr | `0`…`9` | 512–521 / 520–529 | `XF86Numeric0`…`9` | 19/10/11/12/13/14/15/16/17/18 | Shift |
| fr | `.` U+002E | 121 / 129 | `KP_Decimal` | 59 `AB08` | Shift |
| fr | `/` U+002F | 98 / 106 | `KP_Divide` | 60 `AB09` | Shift |
| fr | `#` U+0023 | 523 / 531 | `XF86NumericPound` | 12 `AE03` | Mod5 |
| fr | `+` U+002B | 78 / 86 | `KP_Add` | 21 `AE12` | Shift |
| live (hu+caps:ctrl_modifier) | `=`,`/`,`#`,`*`,`+` | same 5 as `hu` | same | same | — |

Agreement totals over the bank: `hu` 72/77, `us` 74/77, `de` 74/77, `fr` 63/77.
The live keymap is `hu` plus the Caps remap, so its five disagreements are
exactly the `hu` ones; its `0`, `-`, `y`, `z`, `ö`, `ő`, `ű`, `„`, `°` all
agreed.

Spot-check of the `hu`/live mappings that matter (all agreed):

| char | module events `(Linux, pressed)` | `how-to-type` |
|---|---|---|
| `0` | `(41,T)(41,F)` | XKB 49 `TLDE` `[]` |
| `-` | `(53,T)(53,F)` | XKB 61 `AE11` `[]` |
| `ö` | `(11,T)(11,F)` | XKB 19 `AE10` `[]` |
| `ő` | `(26,T)(26,F)` | XKB 34 `AD11` `[]` |
| `ű` | `(43,T)(43,F)` | XKB 51 `BKSL` `[]` |
| `„` | `(100,T)(24,T)(24,F)(100,F)` | XKB 32 `AD09` `[Mod5]` |
| `°` | `(42,T)(100,T)(6,T)(6,F)(100,F)(42,F)` | XKB 14 `AE05` `[Shift Mod5]` |

### Root cause

The module compares `xkb_keysym_to_utf32(sym) == codepoint`. `how-to-type`
compares the level's keysym to the **exact** `xkb_utf32_to_keysym(codepoint)`.
The two differ for function keysyms that alias a character:
`xkb_keysym_to_utf32(XF86Numeric0)` = `'0'`, `xkb_keysym_to_utf32(KP_Multiply)`
= `'*'`, and so on. Because those aliases are on levels whose only modifier mask
is `0x0`, the rank `(mask.bit_count, mask, level, layout, keycode)` puts them
ahead of the real key, which needs Shift/Mod5. The module therefore emits
`KEY_KPEQUAL`/`KEY_KPASTERISK`/`KEY_NUMERIC_POUND`/`KEY_NUMERIC_0`… instead of
the physical key.

This also falsifies the module docstring's claim that the ranking "keeps `0` on
the main row rather than the keypad": on `fr`, `0` resolves to `XF86Numeric0`
(Linux 512), not the main row.

### Why this is blocking

- It is a disagreement with the reference tool the ticket names, on ordinary
  characters, in every layout tested — the review standard given for this work
  treats that as blocking.
- `KEY_NUMERIC_*` (`0x200`–`0x20b`) are documented in the kernel header as
  "used by phones, remote controls"; the corresponding keysyms are
  `XF86Numeric*`. Applications that dispatch on keysym, or terminals in
  application-keypad mode, are not obliged to turn these into text. The whole
  premise of the ticket is that a mapping that is *sometimes* wrong is the bug.
- `xkb_state_key_get_utf32` *does* map these keysyms to the character, so an
  app that goes through that API (Qt/KWin Wayland apps, likely terminals) may
  appear to work. That is why this is a risk rather than a certain visible
  corruption — but it is exactly the kind of narrow, app-dependent behaviour
  that made `ydotool type`'s failure hard to see, and it should not ship.

### Minimal fix (recommended)

Do not reject aliases outright — exact matching is not a strict superset. On
`de`, `…`/`′`/`″` are mapped to the keysyms `U2026`/`U2032`/`U2033`, for which
`xkb_utf32_to_keysym` returns `ellipsis`/`minutes`; `how-to-type` finds **no**
direct access for them while the module resolves them correctly. So:

- Rank a **canonical** match (`sym == xkb_utf32_to_keysym(cp)` or
  `sym == (0x01000000 | cp)`) ahead of any other match, then apply the existing
  `(bit_count, mask, level, layout, keycode)` tie-break. That removes all 25
  disagreements while keeping the `de` Unicode-keysym cases.
- Add a regression test that `hu '='` resolves to `AE07`+Shift (Linux 8 with
  Shift 42) and `fr '0'` to `AE10`+Shift (Linux 11 with Shift 42), asserting the
  module does **not** emit `KEY_KPEQUAL`/`KEY_NUMERIC_0`.

Because the injection probe has been stopped, the orchestrator may want its own
quick check of `=`/`*`/`+`/`/`/`#` on `hu` in a terminal before deciding; the
`xkbcli` evidence alone is already sufficient to require the fix.

## 3. Falsification of the hard cases

- **Unreachable codepoints.** `us.sequence_for("😀")` → `unreachable=("😀",)`,
  no events. Also `ā` (U+0101), `ǿ` (U+01FF) → `unreachable`, no nearest-key
  guess. No garbage keycode is ever substituted. ✔
- **`Lock` filtering.** A hand-written keymap (`AD01` type `LOCKONLY`:
  level 1 `[a]` mask `0x0`, level 2 `[section]` mask `0x2`) compiled to a
  keymap where `§` is reachable **only** with `Lock` (`xkbcli how-to-type
  --keymap` confirms `[Lock]`). The module reports `§` in `unreachable` and
  emits no events, and in particular never emits the Caps key (Linux 58); its
  control `a` still resolves. On the real `hu` keymap, `y`/`ő` pick the
  `0x0` level and never Caps, and the `caps:ctrl_modifier` test passes. ✔
- **`Mod5`→`RALT` table.** `_PREFERRED_MODIFIER_KEY_NAMES` is a plain XKB
  **key-name** table (`Shift→LFSH`, `Control→LCTL`, `Mod1→LALT`, `Mod4→LWIN`,
  `Mod5→RALT`, …): physical positions present in every `pc`-derived keycodes
  section, with no per-layout knowledge. I cleared the table at runtime to force
  the scan fallback; `hu '„'` produced **identical** events
  `[(100,T),(24,T),(24,F),(100,F)]`, i.e. the full-scan path finds `RALT` (100)
  for `Mod5` and `LFSH` (42) for `Shift`. The preferred name is verified
  against a real `xkb_state` before use, so a name that does not depress the
  modifier (e.g. `us`, where `RALT` is `Mod1`) falls through to the scan. ✔
- **Dead keys / compose.** Dead-key keysyms have `xkb_keysym_to_utf32 == 0`
  and are ignored. Compose-only characters are reported, not guessed:
  `us` `ā` → `unreachable`; `de` spacing acute U+00B4 → `unreachable`. The
  module neither composes nor emits a wrong key for them, matching the declared
  out-of-scope. ✔

## 4. Memory and lifecycle safety

- **No leak.** 10 000 `sequence_for` calls of a 30-char mixed string on one
  keymap: RSS flat (`20420 kB → 20420 kB`, delta **0 kB**). 500 ×
  (`from_names` + `sequence_for` + `close`): RSS flat (delta **0 kB**). Every
  temporary `xkb_state` is unref'd in a `finally`; `to_string`'s `malloc`'d
  string is freed through libc `free`, which is libxkbcommon's documented
  contract. ✔
- **Lifecycle.** `close()` is idempotent; `sequence_for` and `to_string` after
  `close()` raise `XkbKeymapClosedError` (not a segfault); `__del__` wraps
  `close()` and does not raise on an already-closed or never-closed object. ✔
- **Error paths by injection.** A non-keymap string →
  `XkbKeymapCompileError` (the `xkb_context` is unref'd before raising, so no
  leak on the failure path). Forcing the loader to fail →
  `XkbLibraryNotFoundError` with the actionable `sudo pacman -S libxkbcommon`
  message. Both observed directly, and both are covered by the suite. ✔

## 5. Hermeticity, caching and hygiene

- **No global reverse-map cache.** `_resolutions` and `_mask_keycodes` are
  instance attributes; two live instances (`us`, `hu`) return independent
  answers (`0`→11 vs `0`→41). The only module-level mutable is `_library`, the
  cached `CDLL` handle, which is layout-independent. Ticket 24 can build a fresh
  `XkbKeymap` per utterance. ✔
- **`to_string()`** (added beyond the DoD) is acceptable: additive, documented,
  tested, and useful to ticket 24 for logging the live keymap. The round-trip
  test really compiles a serialized keymap (`xkb_keymap {` … 35 723 bytes) and
  re-maps `"Hello, world! 0"` identically. ✔
- **Imports.** Exactly `ctypes`, `ctypes.util`, `dataclasses`, `typing`
  (`from __future__ import annotations` aside). No `subprocess`, no `socket`,
  no `ydotool`, no `sudo`, no daemon. The strings that matched a grep for
  "ydotool"/"sudo" are docstring/error text only. ✔
- **Scope.** `git diff main...HEAD --name-only`: `src/ges2t/injectors/keymap.py`,
  `tests/test_keymap.py`, the ticket's `ticket.md`/`001-comment.md`, and the
  board row in `docs/project/README.md`. Nothing else. `git status` clean. ✔

## 6. Tests and style

- **Counts.** `uv run pytest tests/test_keymap.py -q` → **21 passed**;
  `uv run pytest -q` → **368 passed**. ✔
- **Honesty falsified.** (a) Removing modifier emission at runtime makes
  `us.sequence_for("A")` produce `[(30,T),(30,F)]`, which fails
  `test_shifted_character_presses_shift_before_and_releases_after`. (b) Forcing
  the `us` instance to reuse the `hu` resolution map makes both `0` values `41`,
  which fails `test_same_character_resolves_differently_on_hu_and_us`. The tests
  bite. (All falsification was in-memory; nothing in `src/` or `tests/` was
  edited — `git status` is clean.)
- **mypy.** `uv run mypy src/ges2t tests` → `Success: no issues found in 31
  source files`. The config bites: an untracked `tests/test_zzz_mypy_probe.py`
  with `def untyped_function(x):` produced
  `error: Function is missing a type annotation [no-untyped-def]`; the probe
  file was deleted and mypy is clean again. `pyproject.toml` has
  `disallow_untyped_defs = true`, `no_implicit_optional = true`. ✔
- **Style.** `KeyEvent`/`KeySequence`/`_Resolution` are frozen dataclasses with
  named fields (no positional tuples); `-> None` is present on every `None`
  method; no bare `Any`; `str | None` throughout. The only non-icy detail:
  `_scan_key_for_mod` keeps an `exact` variable that is never assigned non-None
  (dead `exact if exact is not None else partial`); harmless but worth tidying.

## Live injection probe — one observed round trip (byte-exact); re-run inconclusive

I must record this accurately, because the instruction history is complex. The
injection probe ran **twice**: once under the first authorisation, producing a
readable byte-exact result; then the method was stopped, then explicitly
re-authorised as **exactly one** 30-second, one-attempt re-run. That re-run
executed but captured **nothing**, for a harness reason given below. I therefore
treat the *first* round trip as the only observed injection evidence, and it is
corroborating only — the unit ruling does not rest on it.

- **Method.** A dedicated scratch `konsole` window capturing its pty raw
  (`stty -icanon -echo min 1 time 0; cat > file`). Before emitting anything I
  verified focus positively: a temporary KWin declarative script loaded through
  `org.kde.KWin /Scripting` set `workspace.activeWindow` to the target's pid
  and then re-reported `workspace.activeWindow` (`pid=… class=org.kde.konsole`),
  confirming the scratch window held focus. The script was unloaded afterwards.
- **Exact argv** (the unit boundary, documented):

  ```
  ydotool key 41:1 41:0 53:1 53:0 44:1 44:0 21:1 21:0 57:1 57:0 11:1 11:0 \
              26:1 26:0 43:1 43:0 57:1 57:0 \
              100:1 24:1 24:0 100:0 42:1 100:1 6:1 6:0 100:0 42:0
  ```

  (`ydotool`, not `ydotool type`; `ydotool` exit code 0.)
- **Test string** `0-yz öőű „°` (17 UTF-8 bytes). Captured bytes:

  ```
  30 2d 79 7a 20 c3 b6 c5 91 c5 b1 20 e2 80 9e c2 b0
  ```

  Byte-for-byte equal to the expected UTF-8. One first run had a stray leading
  `0x0a`; a clean repeat with a fresh target (file confirmed 0 bytes before
  emission) reproduced exactly 17/17 bytes, and an isolated `41:1 41:0`
  produced exactly `30` (`'0'`), so the argv itself is clean. The anomaly did
  not reproduce and is not attributable to the mapping.
- **Run 1 conclusion.** The observed run supports the evdev ruling and shows the
  module types `0`, the `hu` repositions, `ö/ő/ű`, `„` and `°` correctly through
  real `ydotool key` on the live `hu` keymap. It does **not** cover the
  keypad/alias characters in §2 (they were outside the authorised test string).
  It is a single bounded observation, so I treat it as corroborating, not as the
  load-bearing evidence.

### Re-run (exactly one attempt, 30-second budget) — inconclusive, harness error

- The method was re-authorised once. The attempt computed the argv from the live
  keymap (identical to Run 1), launched a **self-closing** scratch `konsole`
  (`sh -c 'stty -icanon -echo min 1 time 0; timeout 7 cat > file'`, no
  `--hold`), and verified focus before emitting: a KWin script reported
  `workspace.activeWindow` as `pid=3418970 class=org.kde.konsole`, which was the
  scratch target's pid. Only then did it emit `ydotool key` (exit code 0).
- **Result: the capture file was 0 bytes (mismatch).** The cause was my harness,
  not the module: the reader was wrapped as `timeout 7 cat`, and GNU `timeout`
  runs the child in its **own process group**, so `cat` reading the controlling
  tty was stopped by `SIGTTIN` and never read the injected bytes. The window
  self-closed; the whole attempt took **5.67 s** (well inside 30 s).
- Per the one-attempt/no-second-window rule I did **not** retry. No konsole is
  left open and no probe file remains. This attempt therefore contributes no
  evidence either way.

### How strongly is the unit ruling evidenced?

- **Load-bearing:** `man ydotool` (the `key` command consumes
  `/usr/include/linux/input-event-codes.h` `KEY_*` codes) and the header
  constants (`KEY_LEFTCTRL=29`, `KEY_RIGHTALT=100`, `KEY_V=47`, …), plus the
  `xkbcli` cross-check showing `ő`/`ű` are XKB 34/51. This is conclusive on its
  own for "evdev is the right unit, no offset".
- **Corroborating:** Run 1's observed round trip (byte-exact 17/17). The re-run
  added nothing.
- So the ruling rests on **documentation + headers + `xkbcli`**, not on the
  probe. The observed round trip makes it stronger, but is not required.

## Risks

- **Alias/function-key selection (§2) is the blocking risk.** Until fixed, `hu`
  text containing `= + * / #` and `fr` digits can be delivered as
  `KP_*`/`KEY_NUMERIC_*` events.
- **`fr` digits** are the worst case: a whole character class (0–9) resolves to
  `XF86Numeric*`, so a French layout user would type every digit through
  phone/remote keysyms.
- **Group/layout switching mid-utterance** is correctly out of scope here
  (per-utterance rebuild is ticket 24), but ticket 23/24 must not reuse a stale
  instance — the module documents this.
- **`xkbcli` availability** is only a test/reference concern; the module itself
  needs only `libxkbcommon.so.0`.

## Could not verify

- The real application-level effect of the alias keysyms (`KP_*`,
  `XF86Numeric*`) in the user's terminal/editor. This would need a working
  injection probe; Run 1 did not cover those characters and the authorised
  re-run failed on a harness fault (`SIGTTIN`) before yielding data. The
  `xkbcli` disagreement and the `xkb_state_key_get_utf32` nuance are documented
  above.
- Behaviour on layouts other than `hu`, `us`, `de`, `fr` and the live keymap;
  the algorithm is generic, and the exhaustive alias scan only covered those
  four.
- Whether `ydotool`'s daemon version matches the man page exactly; only the
  installed 1.0.4 binary/man page were inspected (they agree).

## Appendix — how to reproduce

```
uv run pytest tests/test_keymap.py -q
uv run pytest -q
uv run mypy src/ges2t tests
for L in hu us de fr; do for C in 0 - y z ö ő ű „ ° = / # * +; do
  printf '%s %s: ' "$L" "$C"; xkbcli how-to-type --layout "$L" -- "$C" | sed -n '/Direct access/,/Compose/p'; done; done
```

`xkbcli how-to-type` reports XKB keycodes; compare against `module_keycode + 8`.
