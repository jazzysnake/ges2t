# 001 — Implementation comment: batched key emission + the injection-latency measurement

- **Ticket:** [ticket.md](ticket.md)
- **Branch:** `feat/batched-key-emission`
- **Worktree:** `.worktrees/batched-key-emission`
- **Status:** in review (code complete and committed; the authorised measurement has now run)
- **Code commit:** `12003db` — "ticket 23: replace ydotool type with batched ydotool key emission"

## What was built

`YdotoolInjector` no longer passes text to `ydotool`. It takes a
`KeySequenceSource` (a Protocol; production passes `XkbKeySequenceSource`, tests
pass a fake), maps the assembled text to Linux `KEY_*` events, and emits the
whole sequence in **one** `ydotool key` invocation. The `ydotool type` path is
**deleted**, not bypassed: `TYPE_ARGUMENTS`, the stdin payload, and the
`--file -` invocation are gone. A test (`test_ydotool_type_is_never_invoked`)
asserts `"type"` never appears in argv.

Files changed:

- `src/ges2t/injectors/ydotool.py` — the emitter (see shapes below).
- `src/ges2t/pipeline.py` — `build_injector` only, kept deliberately minimal for
  ticket 25 (clipboard backend) to rework the same function.
- `tests/test_ydotool_emitter.py` — new, 41 tests.
- `tests/test_ydotool_injector.py` — reduced to the ADR 0012 `decide_injection`
  table (the old file's `ydotool type` delivery tests are superseded; the
  behaviours they guarded are re-tested in the emitter file and the text table).
- `tests/test_pipeline.py` — three `build_injector` tests (trailing space, and
  the new `key_delay_ms`/`layout` consumption + absent-block default).

### Public shapes added to `ydotool.py`

```python
class YdotoolKeymapError(YdotoolError): ...            # layout resolve/compile failure
class YdotoolTextUnreachableError(YdotoolError): ...   # every character unreachable
class YdotoolUnreachableWarning(UserWarning): ...      # some characters skipped

@runtime_checkable
class KeySequenceSource(Protocol):
    def sequence_for(self, text: str) -> KeySequence: ...

class XkbKeySequenceSource:                            # production source
    def __init__(self, layout: str = AUTO_LAYOUT, variant: str = "",
                 *, runner: LayoutCommandRunner | None = None) -> None: ...
    def sequence_for(self, text: str) -> KeySequence: ...

class YdotoolInjector:
    def __init__(self, key_sequence_source: KeySequenceSource, *,
                 trailing_space: bool = True,
                 key_delay_ms: int = DEFAULT_KEY_DELAY_MS,
                 runner: CommandRunner | None = None) -> None: ...
    def inject(self, text: str) -> None: ...
```

`XkbKeySequenceSource` resolves the layout and compiles a **fresh** `XkbKeymap`
on every `sequence_for` call and closes it immediately afterwards, so a layout
switch between utterances cannot pin a stale reverse map (ADR 0020).
`build_injector` wires it from `YdotoolConfig.from_mapping(config.injection_blocks.get("ydotool", {}))`
with `trailing_space=config.ui.trailing_space` and
`key_delay_ms=settings.config.key_delay_ms`.

### The argv shape (binding facts honoured)

`KeyEvent.keycode` is a Linux/evdev `KEY_*` code, rendered **straight** with no
offset and no `-8`/`+8`:

```python
f"{event.keycode}:{1 if event.pressed else 0}"
```

Known short input — `XkbKeySequenceSource("us").sequence_for("a ")`, delay 0:

```
('ydotool', 'key', '-d', '0', '30:1', '30:0', '57:1', '57:0')
```

(`KEY_A`=30, `KEY_SPACE`=57; the trailing space is the ADR 0012 one.)

The measurement phrase (`125` chars / 23 words, `126` bytes + trailing space,
`252` events) produces an argv of `256` tokens beginning
`('ydotool', 'key', '-d', '0', '20:1', '20:0', '35:1', '35:0', ...)` and ending
`(..., '23:1', '23:0', '49:1', '49:0', '57:1', '57:0')`, with `'type'` absent.

## Behaviours that must not regress

- **ADR 0012** — `decide_injection` is unchanged and is still the only
  implementation: empty/whitespace-only spawns **no process and maps nothing**;
  non-empty text gets exactly one appended ASCII space when enabled; the text is
  otherwise unmodified. The trailing space is now part of the text that is
  *mapped*, not appended to a `ydotool type` payload (tested).
- **Unreachable characters** — skipped with `YdotoolUnreachableWarning`; the
  reachable ones are still emitted in the same batch. If **every** character is
  unreachable, `YdotoolTextUnreachableError` is raised and **no process is
  spawned** (tested).
- **ticket 7's error contract** — `YdotoolNotFoundError`,
  `YdotoolDaemonUnreachableError`, `YdotoolInjectionError` keep their exact
  messages and classification; a new `YdotoolKeymapError(YdotoolError)` covers
  layout resolve/compile failures so callers still catch `YdotoolError`.
- **No spurious Return/Esc/Ctrl** — tested for newline-free text, and every
  emitted batch is press/release balanced (per-key stack discipline).

## Real verify output

```
$ uv run pytest -q
563 passed in 2.42s

$ rm -rf .mypy_cache && uv run mypy src/ges2t tests
Success: no issues found in 37 source files
```

(Baseline on `main`: 541 passed, 36 files. The +1 file is
`tests/test_ydotool_emitter.py`; the +22 tests are the new emitter/wiring tests
less the removed `ydotool type` delivery tests.)

## Falsification evidence

Every new test was made to fail by mutating the guarded behaviour, then the file
was restored (`git status` showed only the intended files; the harness lived in
`/tmp` and is not committed). Ten mutations of `ydotool.py` and four of
`pipeline.py`, each run against the relevant test file:

```
M1  keycode offset +8                     failed as expected   6 failed, 35 passed
M2  append Return to argv                 failed as expected   8 failed, 33 passed
M3  always pressed :1                     failed as expected   6 failed, 35 passed
M4  text leaks into argv                  failed as expected   7 failed, 34 passed
M5  no unreachable warning                failed as expected   2 failed, 39 passed
M6  all-unreachable is silent             failed as expected   1 failed, 40 passed
M7  emit only the first event             failed as expected   7 failed, 34 passed
M8  ignore trailing_space                 failed as expected   2 failed, 39 passed
M9  map/spawn even on empty               failed as expected   8 failed, 33 passed
M10 cache the keymap across utterances    failed as expected   1 failed, 40 passed

P1  ignore key_delay_ms                   failed as expected   1 failed, 23 passed
P2  require the block (index, not get)    failed as expected   4 failed, 20 passed
P3  ignore layout                         failed as expected   1 failed, 23 passed
P4  ignore trailing_space                 failed as expected   1 failed, 23 passed
```

The measurement harness itself was falsified too, for the SIGTTIN defect: in a
plain Python pty, the fixed foreground reader captured the payload byte-perfect
while the previous `timeout N cat` form captured **0 bytes**:

```
reader self-test: head -c captured b'the quick brown fox!' (MATCH)
reader self-test: timeout cat captured b'' (0 bytes; expected the SIGTTIN bug)
```

## THE MEASUREMENT

Authorised by the user; **one scratch target, one attempt** (three delay values
inside the single window, no retries). Method:

- One scratch `konsole` opened with `--nofork`, running
  `stty -icanon min 1 time 0` and then a foreground `head -c 126` per delay.
  The reader is a plain foreground child, **not** `timeout cat`: `timeout` puts
  the reader in its own process group, where reading the controlling tty is
  stopped by `SIGTTIN` and captures nothing (reproduced in the self-test above).
  Each `head` exits after exactly the payload length, so the window closes
  itself.
- **Focus was positively confirmed before every injection** by loading a
  temporary KWin declarative script through
  `org.kde.KWin /Scripting org.kde.kwin.Scripting.loadScript` + `start`, which
  reports `workspace.activeWindow`; the script was unloaded after each check. It
  found the scratch window by the nonce in its caption and compared it to the
  active window. Observed active window at **all three** injections:

  ```
  org.kde.konsole|3500401|ges2t-measurement-789121a9 — Konsole
  ```

  (No injection was emitted until this matched.)
- The payload was pre-flighted through the real mapping: `252` events,
  `0` unreachable, no Return/Tab/Esc/Control/Meta, press/release balanced.
- Hard 30-second budget enforced with `signal.alarm` around the attempt plus a
  `timeout 45` controller wrapper; the window was killed/closed in `finally`.
- Capture compared **byte-for-byte** against the expected 126 bytes, with a
  hexdump.

### Numbers

| delay ms | wall ms | ms/char | bytes | byte-perfect |
|---:|---:|---:|---:|:--:|
| 12 | 3100.7 | 24.81 | 126 | YES |
| 6 | 1595.4 | 12.76 | 126 | YES |
| **0** | **47.9** | **0.38** | **126** | **YES** |

**Lowest clean delay: 0 ms**, byte-perfect. All three capture files were
identical (`md5 6c95fa9168340d89c90433604bbd7c76`, 126 bytes, final byte `0x20`
— the trailing space).

Hexdump of the captured payload (identical at every delay):

```
000000  74 68 65 20 71 75 69 63 6b 20 62 72 6f 77 6e 20  the quick brown
000010  66 6f 78 20 6a 75 6d 70 73 20 6f 76 65 72 20 74  fox jumps over t
000020  68 65 20 6c 61 7a 79 20 64 6f 67 20 74 68 65 6e  he lazy dog then
000030  20 71 75 69 65 74 6c 79 20 64 69 73 61 70 70 65   quietly disappe
000040  61 72 73 20 69 6e 74 6f 20 74 68 65 20 76 65 72  ars into the ver
000050  79 20 64 65 65 70 20 64 61 72 6b 20 66 6f 72 65  y deep dark fore
000060  73 74 20 61 6e 64 20 6e 65 76 65 72 20 63 6f 6d  st and never com
000070  65 73 20 62 61 63 6b 20 61 67 61 69 6e 20        es back again
```

### Budget verdict — explicit

**YES.** Batched `ydotool key` at the lowest clean delay (0 ms) took **47.9 ms**
for the 125-character phrase (`0.38 ms/char`), which is **far below** ADR 0001's
sub-1-second framework budget. For comparison, the pre-rewrite `ydotool type`
baseline was ~40 ms/char (≈4.99 s for 124 chars).

### Decision gate

Batching **meets** the budget, so the latency work is done. The remaining option
(construction during dictation, revisiting ADR 0011) is **not needed** and was
**not built**. The default `[injection.ydotool] key_delay_ms = 12` is
conservative (3.1 s at the measured 24.8 ms/char, above budget); delay `0` was
clean and 65× faster. A follow-up delay-default decision belongs to the
orchestrator/ticket 24's constant; this ticket reports the numbers and does not
change the shipped default.

## Anything observed but not explained

- Nothing anomalous. No stray leading `0x0a`/Return appeared in any of the three
  captures; every batch was exactly the mapped phrase plus the single trailing
  space. The pre-flight refused Return/Tab/Esc/Control/Meta keycodes, so the one
  output that could *execute* rather than type was structurally excluded.
- The delay cost is linear and almost exactly `delay × 2 × 126` events:
  12 ms → 3100 ms, 6 ms → 1595 ms. That is consistent with `ydotool key`
  sleeping once per event (down and up), not per character. At delay 0 the
  daemon writes the whole batch with no client-side sleep.

## The measurement script — framing

`measure_batched_emission.py` is a **reproducible measurement artifact**, not
shipped code. It is deliberately:

- **not** under `src/ges2t/`, so it is never importable as part of the package
  and can never be reached by `ges2t`;
- guarded by `--authorised` (it refuses and exits 2 otherwise) because it emits
  real keystrokes;
- equipped with `--self-test`, which exercises the reader and focus mechanisms
  with **no GUI window and no injection at all**;
- keeping the ticket's hard constraints in code: no newline, one scratch window,
  positive KWin focus verification, hard 30-second budget, byte-for-byte
  comparison with hexdump, and window/process cleanup.

It was committed with this comment rather than with the code commit, so a
measurement failure could not contaminate the code work.

## Clean-up confirmation

- `pgrep konsole` → none; the single scratch window closed itself.
- No KWin script remains loaded; `/tmp/ges2t_measure_kwin_scripts/` is gone.
- Probe captures were verified and then removed; `/tmp/ges2t_measure_batched_emission/`
  is gone.
- `ydotoold` (pid 3339542) is the pre-existing daemon, not started by this run.
