# Text injection via ydotool/uinput

- **Status:** accepted
- **Date:** 2026-10-09

## Context

The whole point of ges2t is that transcribed text lands in whatever text field or terminal
currently has focus. On Wayland the only way to synthesise keystrokes into *another*
application is a virtual input device via `/dev/uinput`. The user was offered three options:
**(a)** `ydotool`-style virtual keyboard; **(b)** clipboard plus simulated paste
(`wl-copy` + Ctrl+V); **(c)** both with a fallback. The user chose **(a)**, explicitly stated
that `ydotool` may be a dependency, and stated that clipboard-paste is a possible
*configurable alternative later* — **not** a fallback.

Facts about this machine:

- `ydotool` and `ydotoold` are installed (`/usr/bin/ydotool`, `/usr/bin/ydotoold`).
- `ydotoold` is currently **inactive/dead**; the Arch `ydotool.service` user unit is
  `disabled`. The user must start and enable it.
- `/dev/uinput` exists as `crw-rw----+ root:input`. The `+` means an ACL is present; access
  still depends on group membership or ydotool's udev rule.
- `wtype` is **not** installed; `wl-copy`/`wl-paste` are.
- `ydotool` types by synthesising key events, so it is layout-aware only insofar as the
  tool is told which keymap to use; it emits characters via keycodes.

## Decision

Use **`ydotool` as the text-injection mechanism**, invoked against a running `ydotoold`
daemon. Injection is behind an `Injector` interface with a single v1 implementation,
`YdotoolInjector`.

**Amendment 2026-10-09 — `ydotool type` must not be used, and the mechanism is layout-aware.**
Manual verification on the user's machine found that `ydotool` 1.0.4 resolves text to keycodes
using a **hardcoded US-position table** and **silently drops non-ASCII**, corrupting every
character whose position differs on the user's `hu` layout (`0`→`ö`, `-`→`ü`, `y`→`z`). That is
not configurable — no `--keymap`, no keymap files, no env var, and no upstream fix (see
[ADR 0020](0020-layout-aware-keycode-mapping.md)). Two further measurements reshape this decision:

- **The 5 s typing cost was `ydotool type`'s own client-side sleep**, measured at ~40 ms/char
  (≈5.1 s for a 127-character phrase, ~2.8 s at `--key-delay 2`, with a ~16–18 ms/char floor).
  `type` sleeps **twice per character**; the daemon itself never sleeps. So the cost is
  avoidable, not intrinsic.
- **`ydotool key` accepts an explicit event list**, which lets us precompute the entire sequence
  for a phrase and deliver it in **one** invocation instead of per-character.

So we keep `ydotool` as the **transport** but bypass its text handling entirely:

1. **`ges2t` computes the keycode+modifier sequence itself** using `libxkbcommon` against the
   compositor's live keymap ([ADR 0020](0020-layout-aware-keycode-mapping.md)). We do **not** call
   `ydotool type`, and we do not pass text to `ydotool` at all.
2. **Emission is batched**: the whole sequence goes out in as few `ydotool key` invocations as
   practical, with the delay tuned from a real measurement — the target being that a normal
   utterance fits the ADR 0001 budget.
3. **Characters unreachable on the active layout are skipped and warned about**, never emitted as
   garbage (which is `ydotool`'s current behaviour and why this was hard to diagnose).
4. **Clipboard-paste becomes a real, configured alternative rather than a hypothetical one** —
   `injection.backend = "ydotool" | "clipboard"`. This is the same slot ADR 0003 originally
   reserved ("one or the other configured, not a fallback"), now with a concrete motivation:
   it is layout-independent and O(1), and it is the escape hatch for text the layout cannot
   type. It is still **not** an automatic fallback — silent mechanism switching remains rejected.

- The app **docs** the prerequisites (enable `ydotoold`, grant `/dev/uinput` access) and
  **detects** a broken setup at runtime, producing an actionable error via `ges2t doctor`
  (ADR 0016) rather than silently typing nothing.
- Clipboard-paste is **no longer merely hypothetical**: it is a configured alternative
  (`injection.backend`), evaluated in the same ticket series as the layout work. Automatic
  fallback remains explicitly rejected: silently changing mechanism changes behaviour the user
  did not ask for.
- Because we no longer use `ydotool type`, the tool becomes a dumb transport for precomputed
  event lists. That is a **smaller** surface to depend on than its text handling, and it means a
  future `dotool`/`wtype` swap is a change of emitter, not of mapping.
- Typing latency matters: a long transcript typed key-by-key must not blow the framework's
  sub-1-second budget (ADR 0001) or drop characters. **Measured 2026-10-09: `ydotool type` fails
  this badly** (~40 ms/char, ≈5.1 s for 20 words, with a ~16–18 ms/char floor even at zero delay,
  because it sleeps per character). Hence the batched `ydotool key` emission above; the actual
  figure must be **measured, not assumed**, when the emitter is built — if batching still misses
  the budget, the remaining option is to type committed text during dictation so the cost
  overlaps with speaking.

## Consequences

- ges2t cannot work without `ydotoold` running. This is an accepted, documented setup cost,
  not a bug.
- Because injection is keyboard-event based, transcripts are subject to the target
  application's own key handling. This is a category limitation of the approach, not
  something ges2t can fix.
- Because there is no silent fallback, a broken injector produces a visible error and a
  notification — never a quiet no-op.
- The `Injector` interface plus a fake implementation keeps the pipeline unit-testable
  without `/dev/uinput` (ADR 0014).
