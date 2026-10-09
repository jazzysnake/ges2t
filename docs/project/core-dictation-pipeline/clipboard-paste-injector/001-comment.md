# 001 — Implementation comment: clipboard-paste injector

- **Ticket:** [ticket.md](ticket.md) (clipboard-paste-injector, #25)
- **Branch:** `feat/clipboard-paste-injector`
- **Worktree:** `.worktrees/clipboard-paste-injector`
- **Status:** in review (code complete and committed; the **manual end-to-end run is
  user-owned and NOT performed** — see the script below)
- **Base:** `00adf75` (merge of ticket 15). No ADR was edited.

## What was built

A second `Injector` backend, `ClipboardPasteInjector`, selected by
`injection.backend = "clipboard"`. It is a **configured alternative**, never an
automatic fallback (ADR 0003, ADR 0020): `build_injector` picks exactly one
backend and an unknown value raises.

Files changed:

- `src/ges2t/injectors/clipboard.py` — new, the backend (module docstring covers
  the whole contract).
- `tests/test_clipboard_injector.py` — new, 57 tests, **all with a fake runner**;
  nothing here writes a real clipboard or emits a keystroke.
- `src/ges2t/pipeline.py` — `build_injector` only: backend dispatch via a
  `_INJECTOR_BUILDERS` mapping, mirroring the existing `_TRANSCRIBER_BUILDERS`.
  The `ydotool` path is byte-for-byte the previous code moved into
  `_build_ydotool_injector`.
- `tests/test_pipeline.py` — 10 new backend-selection tests, including two that
  load a **real TOML file** through `load_config`.

### The delivery sequence (read → copy → paste → restore)

```
inject(text)
  └─ decide_injection(text, trailing_space=...)      # ADR 0012, imported from ydotool.py
       SkipInjection → return, spawning NOTHING
       TypeText(payload) →
         1. wl-paste --no-newline                     # read the existing selection (best effort)
         2. wl-copy  < payload on stdin               # overwrite the clipboard
         3. ydotool key -d <ms> 29:1 47:1 47:0 29:0   # raw evdev chord, no text
         4. wl-copy  < previous on stdin  (delay 0.4s) # restore, best effort
```

- **Keycodes are Linux/evdev with no offset.** `ctrl+v` is emitted literally as
  `29:1 47:1 47:0 29:0`; `ctrl+shift+v` as `29:1 42:1 47:1 47:0 42:0 29:0`
  (modifiers released in reverse order). `ydotool type` is never invoked.
- **The text is delivered to `wl-copy` over stdin.** It never appears in any
  argv element (argv is `("wl-copy",)`), so it never reaches the process table,
  and the `ydotool` chord invocation has an **empty stdin**. The only stream
  carrying the transcript is `wl-copy`'s stdin, which is the mechanism.
- **Restore is best effort and non-fatal.** It is spawned as a *detached child*
  (`sh -c 'sleep 0.4; exec wl-copy …'`) so the service's command loop is never
  blocked. A failing restore raises :class:`ClipboardRestoreWarning`, never an
  error, and the transcript is already pasted before the child can overwrite it.
- **`wl-paste` is tolerated when unreadable** (an image, a password-manager
  selection, or `wl-paste` missing): the read returns `None`, no restore is
  attempted, and the injection still succeeds.
- **Timeout:** immediate `wl-copy`/`wl-paste` runs are bounded at 5 s; the
  deferred restore child is deliberately not (it sleeps by design).

### Public shapes (`src/ges2t/injectors/clipboard.py`)

```python
WL_COPY_BINARY = "wl-copy"; WL_PASTE_BINARY = "wl-paste"
CLIPBOARD_COMMAND_TIMEOUT_SECONDS = 5.0
CLIPBOARD_RESTORE_DELAY_SECONDS = 0.4

class ClipboardError(Exception): ...
class ClipboardNotFoundError(ClipboardError):     binary: str
class ClipboardInjectionError(ClipboardError):    binary, returncode, stderr
class ClipboardTimeoutError(ClipboardError):      binary, seconds
class ClipboardSpawnError(ClipboardError):        binary, detail
class ClipboardKeyChordError(ClipboardError, ValueError): ...
class ClipboardRestoreWarning(UserWarning): ...

@dataclass(frozen=True)
class ClipboardRunnerCall:  argv; stdin; env; delay_s

class ClipboardRunner(Protocol):                       # interface-typed seam
    def __call__(self, argv, stdin_bytes, env, *, delay_s=None) -> CompletedProcess: ...

@dataclass(frozen=True)
class KeyChord:
    name: str; events: tuple[KeyEvent, ...]
    @property keycodes -> tuple[int, ...]
    @classmethod parse(cls, spelling: str) -> KeyChord

def is_modifier_keycode(keycode: int) -> bool: ...

class ClipboardPasteInjector:
    def __init__(self, *, paste_keys=("ctrl+v",), trailing_space=True,
                 key_delay_ms=DEFAULT_KEY_DELAY_MS,
                 restore_delay_s=CLIPBOARD_RESTORE_DELAY_SECONDS,
                 runner=None, warnings=()) -> None: ...
    paste_keys: list[str]; trailing_space: bool; restore_delay_s: float
    last_injection_ms: float | None; last_warnings: list[str]
    def inject(self, text: str) -> None: ...
```

`KeyChord`/`ClipboardRunnerCall` are frozen dataclasses, the seam is a Protocol,
and every function is annotated including `-> None` (ADR 0019).

## The config contract consumed (ticket 24)

Exactly the hand-off ticket 24 documented:

```python
settings = ClipboardConfig.from_mapping(config.injection_blocks.get("clipboard", {}))
settings.config.paste_keys   # tuple[str, ...], default ("ctrl+v",)
settings.warnings            # ["injection.clipboard: unknown key '…'", ...]
```

An **absent** `[injection.clipboard]` block is the normal case and yields all
defaults through `.get(..., {})` — never a `KeyError` (proved by the real-TOML
test). The block's warnings are carried on `injector.last_warnings` so `doctor`
can surface them rather than swallowing them.

`build_injector` end to end, all proved by tests (two of them through a **real
`config.toml`** on disk):

| `injection.backend` | block | result |
|---|---|---|
| absent (default `"ydotool"`) | absent | `YdotoolInjector`, `key_delay_ms == 12` |
| `"ydotool"` | — | `YdotoolInjector` |
| `"clipboard"` | absent | `ClipboardPasteInjector`, `paste_keys == ["ctrl+v"]` |
| `"clipboard"` | `paste_keys = ["ctrl+shift+v"]` | `ClipboardPasteInjector`, `["ctrl+shift+v"]` |
| `"clipboard"` + `ui.trailing_space = false` | — | `trailing_space is False` |
| `"clipboard"` | `past_keys = [...]` (typo) | injector built, warning surfaced on `last_warnings` |
| `"magic"` / `"clipbord"` | — | **`PipelineError`** naming the handled backends |

## Real verify output (this worktree)

```
$ uv run pytest -q
656 passed in 4.06s

$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 41 source files
```

Baseline on `main` was **589 passed, 39 files**; this branch is **656 passed,
41 files** (+57 clipboard tests, +10 pipeline tests, +1 module, +1 test file).
`git diff --quiet HEAD -- src tests` reports the tree byte-identical to HEAD
after all probes.

## Falsification evidence

A harness in `/tmp` (not committed) applied one mutation at a time to the real
source, ran the affected test file as a fresh subprocess, and restored the file;
`__pycache__` and `.mypy_cache` were cleared before every run. **All 10
mutations were caught** (non-zero pytest exit):

```
M1  keycode offset +8 in the chord argv          caught   4 failed, 53 passed
M2  text appended to the wl-copy argv            caught   6 failed, 51 passed
M3  read the clipboard after overwriting it      caught   2 failed, 55 passed
M4  unknown backend silently defaults            caught   2 failed, 32 passed
M5  empty/whitespace text still spawns           caught   7 failed, 50 passed
M6  restore failure made fatal                   caught   1 failed, 56 passed
M7  trailing space ignored                       caught   3 failed, 54 passed
M8  wl-copy non-zero exit swallowed              caught   2 failed, 55 passed
M9  clipboard block warnings swallowed           caught   1 failed, 33 passed
M10 modifiers released in press order            caught   4 failed, 53 passed

uncaught mutations: 0
```

The two the ticket names specifically, with the failing test names:

**M2 — text leaking into argv:**

```
FAILED tests/test_clipboard_injector.py::test_text_is_never_an_argv_element
FAILED tests/test_clipboard_injector.py::test_payload_is_delivered_to_wl_copy_over_stdin
FAILED tests/test_clipboard_injector.py::test_trailing_space_appends_exactly_one_space
FAILED tests/test_clipboard_injector.py::test_no_trailing_space_leaves_text_unchanged
FAILED tests/test_clipboard_injector.py::test_text_already_ending_in_space_gets_a_second_one
FAILED tests/test_clipboard_injector.py::test_non_ascii_text_is_delivered_verbatim
6 failed, 51 passed
```

**M4 — an unknown backend silently defaulting:**

```
FAILED tests/test_pipeline.py::test_build_injector_rejects_an_unknown_backend_instead_of_defaulting
FAILED tests/test_pipeline.py::test_build_injector_unknown_backend_from_real_toml_raises
2 failed, 32 passed
```

After all probes, `git status` is clean and `git diff --quiet HEAD -- src tests`
confirms both files are byte-identical to the commit. The harness and evidence
scripts are in `/tmp/ticket25_mutations.py` and `/tmp/ticket25_evidence.py` and
are **not** committed.

## The error contract

**Missing `wl-copy`** (or `ydotool` for the chord) → `ClipboardNotFoundError`
(`FileNotFoundError` is translated inside the runner call). Message:

> `wl-copy was not found on PATH, so ges2t cannot use the clipboard injection
> backend. Install the wl-clipboard package (Arch: 'sudo pacman -S wl-clipboard')
> and confirm 'wl-copy' runs in the same environment as ges2t, or set
> injection.backend = "ydotool" in ~/.config/ges2t/config.toml.`

It is **never** a silent no-op. A **missing `wl-paste`** is the one tolerated case
(it is read-only and only costs the restore), and only for the read.

**Non-zero exit** → `ClipboardInjectionError(binary, returncode, stderr)`; the
children's stderr reaches the message (`stderr: <empty>` when blank). A non-zero
`ydotool` → the same type with `binary == "ydotool"`.

**Timeout** → `ClipboardTimeoutError`; **other spawn failure** → `ClipboardSpawnError`
— both distinct types rather than a `returncode = -1` sentinel (ADR 0019).

**Unknown `injection.backend`** → `PipelineError` at startup, **not** a config
error and not a default:

> `injection backend 'clipbord' is not handled by this build; handled backends:
> 'clipboard', 'ydotool'`

The message names the offending value and both handled backends, so a typo is
fixed by reading the error rather than by guessing.

## Discoverability: terminals need `ctrl+shift+v`

Documented in three places, as the ticket asks: the `clipboard.py` module
docstring, the `ClipboardPasteInjector` docstring and the `build_injector`
docstring, and the chord error hint itself
(`"Note that most terminals need 'ctrl+shift+v' rather than 'ctrl+v'."`).

## ⚠️ User-owned manual verification — NOT performed

**A real run writes to the user's clipboard and emits a keystroke into whatever
window has focus. The agent did not and must not do this** (writing the clipboard
destroys the user's current selection; injecting keystrokes takes over their
screen). The DoD box for this step is **left unticked**.

Prerequisite: `ydotoold` running (`systemctl --user status ydotool`), a Wayland
session, and `wl-clipboard` installed.

**Script (paste into a shell). It prints the current clipboard first — that value
will be temporarily replaced — then waits 5 s so you can focus the target window.**

```bash
cd /home/matee/Workspace/Personal/ges2t/.worktrees/clipboard-paste-injector

run_manual() {  # $1 = comma-separated paste keys, $2 = label
  uv run python - "$1" "$2" <<'PY'
import subprocess, sys, time
from ges2t.injectors.clipboard import ClipboardPasteInjector

def clipboard() -> bytes:
    return subprocess.run(
        ["wl-paste", "--no-newline"], capture_output=True, check=False
    ).stdout

keys = sys.argv[1].split(",")
label = sys.argv[2]
before = clipboard()
print(f"[{label}] clipboard BEFORE: {before!r}")
print(f"[{label}] focus the target window now; injecting in 5 s ...")
time.sleep(5)
inj = ClipboardPasteInjector(paste_keys=keys)
inj.inject("hello from ges2t clipboard")
time.sleep(1.0)          # let the restore land
after = clipboard()
print(f"[{label}] wall ms: {inj.last_injection_ms:.1f}")
print(f"[{label}] clipboard AFTER:  {after!r}")
print(f"[{label}] restored: {after == before}")
print(f"[{label}] warnings: {inj.last_warnings}")
PY
}

run_manual "ctrl+v"        "EDITOR"      # focus a text editor (Kate, Gedit, ...)
run_manual "ctrl+shift+v"  "TERMINAL"    # focus a terminal
```

**Expected observations:**

1. In a **text editor** with `ctrl+v`: the phrase `hello from ges2t clipboard `
   (one trailing space) appears at the cursor.
2. In a **terminal** with `ctrl+shift+v`: the same phrase appears at the prompt.
   (`ctrl+v` alone does **not** paste in a terminal — it quotes the next
   character; that is the documented reason for the `paste_keys` setting.)
3. `wall ms` should be small — **tens of milliseconds**, independent of transcript
   length (the point of the O(1) mechanism). Report the two numbers.
4. `restored: True` means the pre-existing clipboard was put back after the paste;
   `False` means it was not. **State explicitly which you observed.** (The restore
   is a courtesy, not a guarantee — it is best effort by design.)
5. **Any error is printed, never a silent no-op** — a missing `wl-copy`/`ydotool`
   or a stopped `ydotoold` raises with an actionable message.
6. Afterwards, verify `wl-paste` still returns your original selection. If the
   run was interrupted after step 2 but before the restore (unlikely: 0.4 s), the
   clipboard will hold the transcript instead.

## Anything a reviewer must know

- **The `ydotool` backend is unchanged byte-for-byte.** The old `build_injector`
  body moved verbatim into `_build_ydotool_injector`; `tests/test_ydotool_*.py`
  and `test_pipeline.py`'s three original `build_injector` tests are untouched.
- **`last_warnings` is the mechanism by which config warnings are surfaced.**
  Ticket 23's review noted that the ydotool path discarded `settings.warnings`
  (deferred to `doctor`); this backend does not, because the ticket requires it.
  `ges2t doctor` (ticket 12) should read `injector.last_warnings`.
- **The restore is a detached child, and its stderr is discarded.** A failing
  restore is reported to the *user* as a warning but the child's own stderr is not
  captured in production (`stderr=DEVNULL`); the warning names the exit status.
  This is deliberate best-effort behaviour, not an oversight.
- **The manual E2E is the remaining gate.** Everything else is unit-proved with a
  fake runner; the real clipboard and real keystroke path is the user's step.
