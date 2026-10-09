# 001 — Layout detection and injector configuration notes

Ticket: `ticket.md` (layout-detection-and-config, #24) · Branch: `feat/layout-detection-and-config`

## What was built

- `src/ges2t/injectors/layout.py` — live keymap retrieval, KWin layout detection,
  the resolution order, and the injector block adapters.
- `tests/test_layout.py` — 76 tests, all with an injected runner (no real D-Bus,
  no Wayland, no subprocess) except the two pure-parser fixtures, which are the
  real captured `busctl` strings.
- `src/ges2t/config.py` — generic plumbing only: `Config.injection_blocks`, raw
  and uninterpreted, plus warn-don't-raise handling of scalar keys directly under
  `[injection]`.

`keymap.py` and `ydotool.py` were not created or modified, per the ticket.

## Real `busctl` output (run by hand, read-only)

```
$ busctl --user call org.kde.keyboard /Layouts org.kde.KeyboardLayouts getLayoutsList
a(sss) 1 "hu" "" "Hungarian"
$ busctl --user call org.kde.keyboard /Layouts org.kde.KeyboardLayouts getLayout
u 0
$ busctl --user introspect org.kde.keyboard /Layouts
...
org.kde.KeyboardLayouts       interface -         -            -
.getLayout                    method    -         u            -
.getLayoutsList               method    -         a(sss)       -
.setLayout                    method    u         b            -
.switchToNextLayout           method    -         -            -
.switchToPreviousLayout       method    -         -            -
.layoutChanged                signal    u         -            -
.layoutListChanged            signal    -         -            -
```

The parser was then run against the live session through the public API, not just
the fixture:

```
parsed list: (LayoutEntry(layout='hu', variant='', display_name='Hungarian'),)
detect_layout: LayoutInfo(layout='hu', variant='', display_name='Hungarian', index=0)
live_keymap_text first line: xkb_keymap {
resolve_layout (auto): ResolvedLayout(source=<LayoutSource.LIVE_KEYMAP: 'live keymap'>, layout=None, variant=None, display_name=None)
```

`busctl` renders `a(sss)` as `a(sss) <count>` followed by each struct flattened
into three quoted, C-escaped strings; the `as` array of `ListNames` confirmed the
same `signature count "value"...` shape. The multi-layout fixture in the tests is
`a(sss) 2 "us" "" "English (US)" "hu" "" "Hungarian"` with `u 1`, i.e. a non-zero
active index, and `"English (US)"` proves quoted spaces survive the tokenizer.

## Final public shapes (in `src/ges2t/injectors/layout.py`)

```python
LayoutCommandRunner = Callable[[Sequence[str], float], subprocess.CompletedProcess[bytes]]

XKBCLI_BINARY = "xkbcli"; DUMP_KEYMAP_ARGUMENTS = ("dump-keymap-wayland",)
BUSCTL_BINARY = "busctl"
KEYBOARD_SERVICE = "org.kde.keyboard"
KEYBOARD_OBJECT_PATH = "/Layouts"
KEYBOARD_INTERFACE = "org.kde.KeyboardLayouts"
LAYOUT_COMMAND_TIMEOUT_SECONDS = 2.0
AUTO_LAYOUT = "auto"

@dataclass(frozen=True)
class LayoutEntry:      layout: str; variant: str; display_name: str
@dataclass(frozen=True)
class LayoutInfo:       layout: str; variant: str; display_name: str; index: int
class LayoutSource(StrEnum): CONFIGURED = "configured"; LIVE_KEYMAP = "live keymap"; DETECTED = "detected"
@dataclass(frozen=True)
class ResolvedLayout:   source: LayoutSource; layout: str | None; variant: str | None;
                        display_name: str | None; keymap_text: str | None  # repr=False

class LayoutParseError(ValueError): ...
class LayoutResolutionError(Exception): ...

def live_keymap_text(*, runner: LayoutCommandRunner | None = None) -> str | None: ...
def detect_layout(*, runner: LayoutCommandRunner | None = None) -> LayoutInfo | None: ...
def parse_layouts_list(output: str) -> tuple[LayoutEntry, ...]: ...
def parse_active_layout_index(output: str) -> int: ...
def resolve_layout(*, configured_layout: str = AUTO_LAYOUT, configured_variant: str = "",
                   runner: LayoutCommandRunner | None = None) -> ResolvedLayout: ...

# injector block adapters (follow whistle_config.py)
YDOTOOL_BLOCK_NAME = "injection.ydotool"; CLIPBOARD_BLOCK_NAME = "injection.clipboard"
DEFAULT_KEY_DELAY_MS = 12; DEFAULT_PASTE_KEYS = ("ctrl+v",)
class InjectionConfigError(Exception): ...

@dataclass(frozen=True)
class YdotoolConfig:   layout: str = "auto"; variant: str = ""; key_delay_ms: int = 12
    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> YdotoolConfigResult: ...
@dataclass(frozen=True)
class YdotoolConfigResult:   config: YdotoolConfig; warnings: list[str]

@dataclass(frozen=True)
class ClipboardConfig: paste_keys: tuple[str, ...] = ("ctrl+v",)
    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> ClipboardConfigResult: ...
@dataclass(frozen=True)
class ClipboardConfigResult: config: ClipboardConfig; warnings: list[str]
```

`live_keymap_text` and `detect_layout` keep the exact signatures the ticket names.
`ResolvedLayout` is a frozen dataclass, not a tuple; `LayoutSource` is an enum, not
a magic string (ADR 0019).

## Resolution order, as implemented

`resolve_layout` branches in this exact order and returns a `ResolvedLayout` whose
`source` says which branch fired:

1. `configured_layout.strip() not in ("", "auto")` → `LayoutSource.CONFIGURED`,
   carrying `layout`/`variant` verbatim, **no subprocess at all**. An explicit
   value wins even when detection would report something else.
2. `live_keymap_text()` non-`None` → `LayoutSource.LIVE_KEYMAP`, `keymap_text` set
   (this includes options, e.g. `caps:ctrl_modifier`).
3. `detect_layout()` non-`None` → `LayoutSource.DETECTED`, `layout`/`variant`/
   `display_name` set.
4. Otherwise `LayoutResolutionError`, whose message names all three failures and
   tells the user to set `[injection.ydotool] layout = "hu"` or run under KDE.

`layout = "auto"` (and a blank value) means "use the live keymap"; **`us` is never
guessed** — the only way to get `us` is to configure it.

## Config block shape (ADR 0009)

```toml
[injection]
backend = "ydotool"

[injection.ydotool]
layout       = "auto"   # "auto" = live keymap; or an explicit layout such as "hu"
variant      = ""
key_delay_ms = 12

[injection.clipboard]
paste_keys = ["ctrl+v"]  # most terminals need ["ctrl+shift+v"]
```

Generic layer (`config.py`): `InjectionConfig.backend` is the selector;
`Config.injection_blocks: Mapping[str, Mapping[str, Any]]` holds every
`[injection.<name>]` table raw and uninterpreted, including unselected backends.
A scalar key under `[injection]` that is neither `backend` nor a table warns
(`unknown key 'injection.<key>'`); block contents are never inspected here.

Block adapters (`layout.py`, mirroring `whistle_config.py`): the selected
backend's `from_mapping` applies defaults, returns unknown keys as `warnings`, and
raises `InjectionConfigError` only for a wrong-typed value. This is the
"unknown key inside a selected block warns rather than raises" rule.

## Consumption contract for tickets 23 and 25 (READ THIS)

Ticket 23 (batched emission) and ticket 25 (clipboard) should consume the typed
configs, not the raw TOML:

```python
settings = YdotoolConfig.from_mapping(config.injection_blocks.get("ydotool", {}))
# settings.config.layout / .variant / .key_delay_ms, settings.warnings -> doctor

clip = ClipboardConfig.from_mapping(config.injection_blocks.get("clipboard", {}))
# clip.config.paste_keys == ("ctrl+v",), clip.warnings -> doctor
```

and to pick a keymap:

```python
resolved = resolve_layout(
    configured_layout=settings.config.layout,
    configured_variant=settings.config.variant,
)
if resolved.source is LayoutSource.LIVE_KEYMAP:
    compile(resolved.keymap_text)          # authoritative, includes options
else:                                       # CONFIGURED or DETECTED
    build_from_names(resolved.layout, resolved.variant)
```

Deviations the orchestrator must know:

1. **The block adapters live in `layout.py`, not a new `injection_config.py`.**
   The DoD names only `src/ges2t/injectors/layout.py` and `tests/test_layout.py`,
   and requires the config-block behaviour to be tested in `test_layout.py`, so
   the adapters were kept there rather than adding an unrequested module or
   putting backend interpretation into the generic layer. If a separate module is
   preferred, moving `YdotoolConfig`/`ClipboardConfig`/`InjectionConfigError` and
   their helpers/decorators is mechanical; nothing else references them yet.
2. **`DEFAULT_KEY_DELAY_MS = 12`** is provisional: it is `ydotool key`'s own
   documented default (`man ydotool`, line 49). Ticket 23's measurement owns the
   final value; it is one constant at the top of the block section.
3. **`paste_keys` is exposed as a tuple**, not a list, so the frozen config has no
   mutable field. Ticket 25 iterates it unchanged.

## Judgement calls

1. **A non-zero exit is a failure for `live_keymap_text`** just as a missing binary
   is; both return `None`. The ticket says "returns the keymap string; on any
   failure … returns `None`", so a non-zero `xkbcli` exit is not silently treated
   as an empty-but-valid keymap.
2. **`detect_layout` requires both D-Bus calls.** If `getLayoutsList` succeeds but
   `getLayout` fails, there is no honest active index, so it returns `None` rather
   than defaulting to index 0. `getLayoutShortNames` exists on the interface but
   was not used — it is undocumented in the ticket and unnecessary.
3. **Out-of-range active index returns `None`** rather than clamping; a compositor
   that reports an impossible index is a detection failure, not an index-0 result.
4. **Unknown scalar keys under `[injection]` warn, not raise**, matching ADR
   0009's "unknown keys are tolerated but surfaced". For engines a non-table under
   `[engine]` is a hard error, but `[injection]` is a mixed table (selector plus
   blocks), so a scalar is the only structural clue that a key is a typo. A nested
   table is always treated as a backend block and preserved.
5. **`key_delay_ms` accepts an integral float** (`12.0` from TOML) and normalises
   it to `int`, but rejects `bool` (an `int` subclass), non-integral floats, and
   negatives — the same int-vs-bool trap `whistle_config.py` documents.
6. **An empty `paste_keys` array is rejected.** A paste chord with no keys is a
   config error, not a silent no-op.
7. **`ResolvedLayout.keymap_text` is `repr=False`.** The keymap is ~100 KB and
   would swamp any log line that prints a `ResolvedLayout`.
8. **The timeout is a single constant, `LAYOUT_COMMAND_TIMEOUT_SECONDS = 2.0`,**
   passed to `subprocess.run(timeout=...)`. A wedged compositor or D-Bus daemon
   cannot hang dictation; on expiry both functions return `None`.

## Surprises / notes for the orchestrator

- No new dependency: `busctl` (systemd) and `xkbcli` (libxkbcommon) are plain
  subprocesses, consistent with ADR 0001. `busctl` is called as
  `busctl --user call …`, read-only methods only.
- `getLayoutsList` is the ground truth for the *names* because `~/.config/kxkbrc`
  is not authoritative on this machine (no `LayoutList`); the code never reads
  `kxkbrc`, `$LANG`, or `localectl`. This is deliberate.
- The D-Bus interface also exposes `setLayout`/`switchToNextLayout` — **not
  called**; detection is read-only.
- Layout changes are not watched (out of scope): the mapping is recomputed per
  utterance by construction.
- The full suite is now 423 passed (346 pre-existing + 76 new + one incidental),
  and `mypy` is clean.

## Verification (real output)

```
$ uv run pytest
tests/test_whistle_config.py ........................................... [ 81%]
....................                                                     [ 86%]
tests/test_whistle_stream.py .....................                       [ 91%]
tests/test_ydotool_injector.py ....................................      [100%]

============================= 423 passed in 0.92s ==============================

$ uv run pytest tests/test_layout.py -q
........................................................................ [ 94%]
....                                                                     [100%]
76 passed in 0.05s

$ uv run mypy src/ges2t tests
Success: no issues found in 31 source files
```
