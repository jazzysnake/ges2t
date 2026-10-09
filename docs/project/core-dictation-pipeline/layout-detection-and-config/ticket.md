# Layout detection and injection configuration

- **Priority:** P1
- **Status:** to do
- **Complexity:** S
- **Epic:** ../epic.md

## Current state / context

Ticket 22 maps characters to key events against a keymap; ticket 23 emits them. **Neither knows
which keymap the user is actually on.** This ticket answers that and makes it configurable, and
owns the config changes the other two consume.

Research facts (ADR 0020; all verified on this machine):

- **The ground-truth keymap is available from the compositor:**
  `xkbcli dump-keymap-wayland` returns the **live** keymap *including options* — here
  `pc_hu_inet(evdev)_capslock(ctrl_modifier)`, which includes the user's `caps:ctrl_modifier` KDE
  option. Compiling that string means our mapping cannot drift from what KWin will do.
- **The active layout is on D-Bus:** service `org.kde.keyboard`, object `/Layouts`, interface
  `org.kde.KeyboardLayouts`, owned by `kwin_wayland`:
  `getLayoutsList() → a(sss)` and `getLayout() → u`. Measured: `["hu","","Hungarian"]`, index `0`.
- **`~/.config/kxkbrc` is NOT authoritative here** — it contains only
  `[Layout] Options=caps:ctrl_modifier` and no `LayoutList`, so Plasma falls back to the system
  default (`/etc/vconsole.conf` `XKBLAYOUT=hu`, `localectl`). Never assume kxkbrc.
- Environment also carries `XKB_DEFAULT_LAYOUT=hu`, `XKB_DEFAULT_OPTIONS=terminate:ctrl_alt_bksp`.

There is **no D-Bus client dependency** in the project and none should be added for this: `busctl`
(systemd) and `xkbcli` (libxkbcommon) are both already installed and are plain subprocesses,
consistent with the project's stdlib-first posture (ADR 0001). An injectable runner keeps this
testable without touching a real session.

## Definition of done

Create `src/ges2t/injectors/layout.py` and `tests/test_layout.py`, and make the config changes
below. Do not modify `keymap.py` (ticket 22) or `ydotool.py`'s emission logic (ticket 23).

- [ ] **Live keymap retrieval:** `live_keymap_text(*, runner=None) -> str | None` shells out to
      `xkbcli dump-keymap-wayland` and returns the keymap string; on any failure (missing binary,
      no Wayland session, timeout) it returns `None` so callers can fall back. Never raises for the
      missing-binary case, never hangs (use a bounded timeout).
- [ ] **Layout detection:** `detect_layout(*, runner=None) -> LayoutInfo | None` where
      `LayoutInfo` is a frozen dataclass holding the layout, variant, display name, and the active
      index, parsed from `busctl --user call org.kde.keyboard /Layouts
      org.kde.KeyboardLayouts getLayoutsList` and `getLayout`. Bounded timeout; returns `None`
      rather than raising when KDE's service is absent (non-KDE compositors).
- [ ] **Resolution order, implemented explicitly and documented:** an explicit **configured**
      value wins; otherwise the **live keymap**; otherwise detected **layout/variant names**;
      otherwise a clear error naming what could not be determined. Never silently guess `us`.
- [ ] **Config changes** (this ticket owns them; the generic/engine-specific split of ADR 0009
      applies to injectors exactly as it does to engines):
      - `[injection] backend = "ydotool"` — the backend selector (already present);
      - `[injection.ydotool]` block, passed through the config layer **uninterpreted** like
        `[engine.*]`, holding `layout = "auto"` (or an explicit layout string), `variant = ""`, and
        `key_delay_ms` (default chosen from ticket 23's measurement);
      - `[injection.clipboard]` block holding `paste_keys = ["ctrl+v"]` (ticket 25 consumes it);
      - an **unknown key inside a selected injection block warns** rather than raising, matching
        the engine-block behaviour, and blocks for unselected backends are preserved.
      - Add an ADR 0009 amendment is **not** needed — its generic/backend-block pattern already
        covers this; follow `transcribers/whistle_config.py` as the pattern for the block parser,
        and say so in your comment.
- [ ] `tests/test_layout.py`, with an **injected runner** so no real D-Bus or Wayland session is
      touched: parses a realistic `busctl` `getLayoutsList` output (multi-layout case included,
      with a non-zero active index); returns `None` on missing service / non-zero exit / garbage
      output / timeout; `live_keymap_text` returns `None` when `xkbcli` is absent; the resolution
      order is honoured at each level; an explicit config value overrides detection.
- [ ] `uv run pytest`, `uv run mypy src/ges2t tests` (ADR 0019 covers tests) both green.

## Notes

- The D-Bus object path and method names must be taken from the ADR, not invented; the parsing
  should tolerate the exact `busctl` output format, which wraps values in the `a(sss)` signature
  syntax. Capture a real sample in your comment (running `busctl --user call ... getLayoutsList`
  is read-only and safe — unlike `ydotool`, it injects nothing).
- `layout = "auto"` must mean "use the live keymap", not "guess from `$LANG`".
- Do not watch for layout changes in this ticket. The mapping is recomputed per utterance
  (ADR 0020); reacting to mid-utterance switches is a possible later refinement, not scope here.
- If the compositor is not KDE, detection returns `None` and the configured value or the live
  keymap is used — that keeps us compositor-agnostic without claiming to test other compositors
  (ADR 0002).

## Related tickets

- depends on: ../config-loading/ticket.md (the generic/backend-block pattern it extends)
- blocks: ../batched-key-emission/ticket.md, ../clipboard-paste-injector/ticket.md
- related ADRs: ../../../adr/0020-layout-aware-keycode-mapping.md,
  ../../../adr/0009-configuration-toml.md, ../../../adr/0002-wayland-kde-target.md
