# Layout-aware text→keycode mapping via libxkbcommon

- **Status:** accepted
- **Date:** 2026-10-09

## Context

ADR 0003 chose `ydotool` for text injection. Manual verification on the user's machine then
found that **`ydotool` cannot type correct text on a non-US layout**, and a follow-up research
pass established why and what to do about it.

Measured and source-confirmed facts:

- `ydotool` **1.0.4** (Arch `extra`, the latest release; upstream's last release is 2023-01-30)
  resolves text to keycodes with a **hardcoded 128-entry ASCII→keycode table at US positions**
  plus an uppercase flag (`Client/tool_type.c`, `ascii2keycode_map`). **Non-ASCII characters are
  silently dropped.** There is no `--keymap` flag, no keymap files, and no layout env var; AUR
  `ydotool-git` is the same code, and layout-aware `type` exists only as an **open upstream PR**.
  So this is not a misconfiguration — it cannot be fixed by config, and we should not wait.
- The user's layout is `hu`. Confirmed corruption, matching `xkbcli how-to-type --layout hu`
  exactly: US `AE10` (`0`) → `ö`, US `AE11` (`-`) → `ü`, US `AD06` (`y`) → `z`.
- **`libxkbcommon` 1.13.2 is already installed** and is what KWin itself uses. Its
  `xkb_keymap_key_get_syms_by_level()` + `xkb_keymap_key_get_mods_for_level()` give a complete
  reverse lookup (keysym → keycode + minimal modifier mask) for **any** layout, so the mechanism
  is generic over XKB layouts and needs no curated per-layout tables.
- The **ground-truth keymap is obtainable from the compositor**:
  `xkbcli dump-keymap-wayland` returns the live keymap *including options* — on this machine
  `pc_hu_inet(evdev)_capslock(ctrl_modifier)`, which includes the user's `caps:ctrl_modifier` KDE
  option. Compiling that string means our mapping **cannot drift** from what KWin will do.
- `org.kde.KeyboardLayouts` (D-Bus, owned by `kwin_wayland`) reports the configured layouts and
  the **active** one: measured `["hu","","Hungarian"]`, index `0`. Note `~/.config/kxkbrc` has
  **no** `LayoutList`, so kxkbrc is not authoritative here.
- **`wtype` is not viable on the reference target**: it needs `zwp_virtual_keyboard_v1`, which
  KWin does not implement (not among the 66 globals `wayland-info` advertises).
- **`dotool`** is layout-aware and correct in principle, but is **AUR-only**, GPL-3, needs its own
  daemon, and its own man page admits only heuristic dead-key support. Fallback backend, not a
  dependency.

Alternatives considered and rejected: **vendored per-layout tables** (defeats the user's explicit
"do not hardcode `hu`, support popular layouts" requirement, and rots); **`python-xkbcommon`**
(a fine wrapper, but adds a dependency for ~8 stable `@since 1.0.0` functions); **waiting for
upstream `ydotool`**; **`wtype`**; **`dotool`** as the primary.

## Decision

Build the text→keycode mapping ourselves on **`libxkbcommon`**, and keep `ydotool` purely as the
transport (ADR 0003, amended).

- **Keymap source, in order:** an explicit configured keymap/layout; otherwise the **live
  compositor keymap** from `xkbcli dump-keymap-wayland`; otherwise XKB layout+variant names.
  Compile it with `xkb_keymap_new_from_string()` and derive the mapping from it, so options like
  `caps:ctrl_modifier` are honoured rather than guessed.
- **Reverse lookup:** iterate `min_keycode..max_keycode` × layout group × level, compare keysyms
  (via `xkb_keysym_to_utf32`) against the target codepoint, and take the minimal modifier mask
  from `xkb_keymap_key_get_mods_for_level()`. Translate mask bits to physical modifier keycodes
  via `xkb_keymap_mod_get_name`/`mod_get_index`. `dotool`'s `xkb/xkb.go` is the reference
  implementation for this algorithm.
- **Layout detection:** `org.kde.KeyboardLayouts` D-Bus (`getLayoutsList()`, `getLayout()`) with
  `kxkbrc` / `localectl` / environment as fallbacks. An explicit config value always overrides
  detection.
- **ctypes is preferred over a new dependency.** The required API surface is small and stable, and
  the project's posture is stdlib-first. If the binding proves unreliable, taking
  `python-xkbcommon` (Arch `extra`, MIT) is acceptable **but must be justified in the ticket**.
- **Modifier masks are chosen without `Lock`**, since Caps Lock state changes the base level and
  the live keymap already remaps Caps here.
- **The mapping is computed per utterance**, because the active layout group can change between
  utterances; a sequence computed from a stale keymap is silently wrong.
- **Unreachable codepoints** (absent from both direct and compose lookup — e.g. an emoji) are
  **skipped and warned about** by default, never emitted as garbage. Clipboard paste is the
  documented opt-in escape hatch for such text.
- **Dead keys and compose sequences** are reproduced by emitting the dead key (down/up) followed
  by the base key (down/up); the application composes them. They require no special support from
  us beyond correct keycode emission.

## Consequences

- The corruption class is eliminated **generically**: any XKB layout works, including level-3
  (AltGr/`Mod5`, physically `KEY_RIGHTALT`) and Hungarian `ő`/`ű`, and the user does not have to
  pick from a curated list.
- The mapping is authoritative because it comes from the same keymap the compositor will use.
  The cost is a dependency on `xkbcli`/`libxkbcommon` being present — true on any KDE Wayland
  system, since KWin itself needs it, and it is checked by `doctor`.
- A computed sequence is valid **only for the layout it was computed from**. Switching layouts
  mid-utterance produces wrong output, which is why the mapping is per utterance; watching
  `layoutChanged` for true mid-utterance switching is a possible later refinement.
- Some codepoints are simply not typeable on a given layout. The skip-and-warn default makes that
  visible instead of silently dropping characters — which is exactly what `ydotool` does today
  and what made this bug hard to see.
- If `libxkbcommon` ever disappears from a target system, the fallback is `python-xkbcommon` or
  `dotool`; the mapping is behind the `Injector` seam (ADR 0003), so neither is a rewrite.
