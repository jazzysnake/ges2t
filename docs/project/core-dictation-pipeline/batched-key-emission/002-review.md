# 002 — Review: batched key emission + injection-latency measurement

- **Ticket:** [ticket.md](ticket.md)
- **Implementation:** [001-comment.md](001-comment.md)
- **Branch:** `feat/batched-key-emission`
- **Worktree:** `.worktrees/batched-key-emission`
- **Reviewer HEAD:** `6ead966` (measurement artifact + comment); code `12003db`
- **Diff reviewed:** `git diff main...feat/batched-key-emission` (three dots), base `46c7122`
- **Status:** complete

## Verdict

**APPROVE — merge-ready.** The emitter is correct and the three binding interface facts are
honoured. `ydotool type` is genuinely deleted. The measurement evidence is internally
consistent and its harness genuinely enforces focus verification, a bounded attempt,
pre-flight refusal of the dangerous keycodes, and byte-exact comparison. The measurement is
**not** independently reproducible by me (the task forbade re-running it), and I list exactly
what that leaves unverified below.

**One required out-of-scope follow-up:** the shipped default `key_delay_ms = 12` is **over**
ADR 0001's budget (~3.1 s). The ticket reports this honestly and the constant lives in ticket
24's file, so it is not a defect of this diff — but the product is over budget until the
default is changed to a clean low delay (0 measured clean). See "Decision gate" below.

Changed files are exactly the in-scope set; no ADR was edited.

```
docs/project/README.md                                              (board row)
docs/project/core-dictation-pipeline/batched-key-emission/001-comment.md
docs/project/core-dictation-pipeline/batched-key-emission/measure_batched_emission.py
docs/project/core-dictation-pipeline/batched-key-emission/ticket.md (status only)
src/ges2t/injectors/ydotool.py
src/ges2t/pipeline.py                                               (build_injector only)
tests/test_pipeline.py
tests/test_ydotool_emitter.py
tests/test_ydotool_injector.py
```

---

## 1. The emission mechanism — falsified

### `ydotool type` is gone, and text never reaches argv

`TYPE_ARGUMENTS = ("type", "--file", "-")` was **deleted** (not bypassed); `KEY_ARGUMENTS = ("key",)`
replaced it. Grep of `src/` and `tests/` for a live `ydotool type` invocation finds only prose:
module docstrings that say it is never called, plus test prose. No code path passes text or a
stdin payload:

- `_deliver` calls the runner with `argv` built purely from `KeyEvent`s and a hardcoded `b""` stdin.
- `_run_command` still has the generic `input=stdin_bytes` seam parameter, but the only production
  call site passes the empty bytes. No text is ever encoded to stdin or placed in argv.

### The argv shape (my own probe, fake runner, no injection)

I constructed the argv independently through the real `XkbKeySequenceSource` and a recorded
fake runner:

```
a<space> delay0: ('ydotool', 'key', '-d', '0', '30:1', '30:0', '57:1', '57:0')
a trailing_space: ('ydotool', 'key', '-d', '0', '30:1', '30:0', '57:1', '57:0')
A (shift):       ('ydotool', 'key', '-d', '0', '42:1', '30:1', '30:0', '42:0')
phrase chars: 125  argv len: 256
phrase head: ('ydotool', 'key', '-d', '0', '20:1', '20:0', '35:1', '35:0')
phrase tail: ('23:1', '23:0', '49:1', '49:0', '57:1', '57:0')
text leak? False   stdin empty? True   'type' present? False
```

This matches `001-comment.md` exactly. `KEY_A=30`, `KEY_SPACE=57`, `LEFTSHIFT=42` — Linux/evdev
codes with **no offset** (`KeyEvent.keycode` passes straight through). `A` shows correct modifier
wrapping: Shift pressed before the key and released after (reverse order). `argv len = 4 + 252`
for 126 mapped characters = 2 events each.

### One spawn, delay threaded, no spurious Return/Esc/Ctrl

- One injection is one `_runner` call; tested with a 200-event batch and confirmed by the probe.
- `key_delay_ms` is threaded into `-d`; `test_default_key_delay_is_twelve_ms` and
  `test_build_injector_carries_the_configured_key_delay_and_layout` pin it.
- `test_newline_free_text_emits_no_return_or_escape`, `test_plain_text_uses_no_control_or_altgr_modifier`,
  and `test_every_emitted_batch_is_press_release_balanced` (us + hu) cover the "one output that could
  execute" concern. The real `us` keymap emits no Return/Esc for the tested phrases.

### ADR 0012 unchanged and still tested

`decide_injection` is byte-for-byte the previous implementation (only docstrings changed). The
trailing space is now part of the **mapped** text: `inject("a")` with `trailing_space=True` maps
`"a "` (probe above) and emits `57:1 57:0`. Empty/whitespace-only input spawns no process *and maps
nothing* (`source.texts == []`), which is a strict improvement over the old path. Text is otherwise
unmodified — no strip, no collapse.

### Unreachable characters

Partial miss: `YdotoolUnreachableWarning` is raised and the reachable characters are emitted in the
same batch. Every-characters-unreachable: `not sequence.events` raises `YdotoolTextUnreachableError`
**before** spawning, with a message naming the characters and no process spawned. Both are tested.

### Ticket 7's error contract preserved

`YdotoolNotFoundError`, `YdotoolDaemonUnreachableError`, `YdotoolInjectionError` and their exact
messages/classification were **not modified** by the diff (only the surrounding `_deliver` body).
The daemon-message-wins-over-generic priority is re-tested. The new `YdotoolKeymapError` subclasses
`YdotoolError`, so callers catching `YdotoolError` still catch every failure.

### `build_injector`

Minimal, as required:

```python
settings = YdotoolConfig.from_mapping(config.injection_blocks.get("ydotool", {}))
return YdotoolInjector(
    XkbKeySequenceSource(settings.config.layout, settings.config.variant),
    trailing_space=config.ui.trailing_space,
    key_delay_ms=settings.config.key_delay_ms,
)
```

An absent block uses `.get("ydotool", {})` and yields all defaults — no `KeyError` (mutation-tested
below). No restructuring beyond this.

---

## 2. The measurement — evidence audited, not independently confirmed

I did **not** re-run the measurement (forbidden: it injects keystrokes and would take focus). I
audited the recorded evidence and the harness code against its claims.

### Evidence arithmetic — checks out

The recorded phrase is `"the quick brown fox jumps over the lazy dog then quietly disappears into
the very deep dark forest and never comes back again"`:

```
phrase chars: 125   words: 23
payload bytes: 126  final byte: 0x20
md5: 6c95fa9168340d89c90433604bbd7c76   (claim: 6c95fa9168340d89c90433604bbd7c76)  MATCH
hexdump decode: 126 bytes, == payload  True
```

The comment's hexdump decodes byte-for-byte to the exact phrase plus one trailing space, so the
recorded md5/hexdump/byte-count are mutually consistent with the stated phrase.

Delay cost is consistent with the claimed mechanism — `ydotool key` sleeping once per event, not
per character:

| delay | measured wall | `delay × 2 × 126` | overhead |
|---:|---:|---:|---:|
| 12 | 3100.7 ms | 3024 ms | +76.7 ms |
| 6 | 1595.4 ms | 1512 ms | +83.4 ms |
| 0 | 47.9 ms | 0 ms | +47.9 ms |

Slope between 12 ms and 6 ms: `(3100.7 − 1595.4) / 6 = 250.9 ms` per ms of delay, over 252 events
= **0.996** events-per-ms — i.e. exactly one sleep per event, plus a fixed ~50–80 ms
spawn/daemon overhead. `ms/char` recomputed: 24.81, 12.76, 0.38 — all match the table.

### Harness audit — the guards are real, with one unverified extra and one best-effort bound

Reading `measure_batched_emission.py`:

- **Positive focus verification before emitting, abort if unconfirmed** — `_observe_focus` loads a
  temporary KWin script over `org.kde.KWin /Scripting` that reports `workspace.activeWindow`, then
  `_focus_confirmed` requires `match` or an active pid equal to the scratch konsole pid;
  `_measure` `break`s without injecting otherwise. The recorded observation
  `org.kde.konsole|3500401|ges2t-measurement-789121a9` carries the **nonce of the scratch window**,
  which is genuine evidence the scratch window was active. **Real guard.**
- **Hard 30 s bound** — `signal.alarm(ATTEMPT_BUDGET_S)` around the attempt. This is a *best-effort*
  bound (Python runs signal handlers between bytecodes / when a C call returns), not a hard kill. The
  comment also claims "plus a `timeout 45` controller wrapper"; **that wrapper is not in the
  committed script** — it may have been applied on the command line, but it is not verifiable from
  the artifact.
- **One scratch target** — one `konsole --nofork`, three sequential foreground `head -c 126`
  readers in that one window, no retries. **Holds.**
- **Pre-flight refusal of Return/Esc/Ctrl/Meta** — `FORBIDDEN_KEYCODES = {1,15,28,29,97,125,126}` =
  Esc, Tab, Enter, L/R Ctrl, L/R Meta, checked before anything is emitted; `SystemExit` on a hit.
  **Holds.** (Shift and RAlt, needed by `hu`, are deliberately allowed.)
- **Byte-exact comparison** — `Attempt.byte_perfect` is `captured == expected`, and `_report` refuses
  to call a delay "clean" otherwise. **Holds.**
- The `head -c` vs `timeout cat` SIGTTIN explanation in the self-test is a correct Unix-behaviour
  claim (a `timeout`-wrapped reader lands in its own process group and gets `SIGTTIN` on a read of
  the controlling tty).

**Residual harness risk (not a defect):** the pre-flight maps `PHRASE + " "` on the live keymap,
but `inject` re-resolves the keymap at emission time. A layout switch between the two could in
principle change the emitted sequence. Nothing suggests this happened (all three focus reports and
all three captures were identical), but the pre-flight's guarantee is technically on a separate
mapping call.

### What I could not verify (explicit)

- **Independent reproducibility.** The captures and md5 are recorded in `001-comment.md`; the
  files themselves were deleted. I confirmed the md5 of the reconstructed payload matches the
  recorded string, but I cannot confirm that the run's actual capture files had that md5.
- **That the phrase arrived in a real editor or terminal as the user experiences it.** The target
  was a raw pty running `head -c 126` in non-canonical mode, not an editor/terminal. This proves
  pty-level **byte delivery with nothing dropped**, which is the right proxy for "the daemon's
  event stream is complete", but it does not exercise an editor's event handling, IME, or the
  visual rendering path.
- **The focus report and the `timeout 45` wrapper** are quoted in the comment; I could not observe
  the live run.
- **The `--self-test` reader/focus outputs.** I did not execute them, to respect the "do not open a
  terminal" constraint; I audited their code instead.
- **Machine state at run time** (active layout, daemon build) is not recorded beyond the focus
  windows.

### Decision gate — batching meets the budget, and the ADR 0011 option is unbuilt

- At the lowest clean delay (0 ms) the recorded 47.9 ms is far below ADR 0001's sub-1-second
  framework budget. The decision gate says latency work is then **done**.
- I searched the diff for any speculative construction of the "type during dictation / revisit
  ADR 0011" option: **none**. No streaming/session changes, no partial-emission machinery. The
  branch touches only the emitter, `build_injector`, the emitter/tests, and docs.
- **The shipped default is over budget and is reported as such.** `DEFAULT_KEY_DELAY_MS = 12` in
  `src/ges2t/injectors/layout.py` gives ~3.1 s at the measured 24.8 ms/char. `001-comment.md` says
  so plainly ("above budget") and distinguishes the measured clean delay (0) from the shipped
  default — it does **not** imply the default is fine. The constant is ticket 24's file; changing it
  is the orchestrator's follow-up, correctly left out of this diff.

### Placement of `measure_batched_emission.py`

**Acceptable and should stay.** It holds up the stated condition:

- It sits under `docs/project/...`, **not** under `src/ges2t/`, and so is never importable as part
  of the `ges2t` package (hatchling packages the `src` layout); nothing in `ges2t` can reach it.
- `uv run pytest` does **not** collect it (verified: `--collect-only` shows 563 tests, the script
  is absent), and `mypy src/ges2t tests` does not check it.
- It refuses to run without `--authorised` (exit 2), and `--self-test` runs the reader/focus checks
  with no window and no injection.
- `001-comment.md` explicitly frames it as a "reproducible measurement artifact, not shipped code".

This is the documented exception, so it is fine. It is not part of the shippable surface.

---

## 3. Mechanicals and falsification

### Gates (cache cleared before each)

```
$ uv run pytest -q
563 passed in 3.42s

$ rm -rf .mypy_cache && uv run mypy src/ges2t tests
Success: no issues found in 37 source files
```

Both match the claim (563 tests; 37 files). Pytest collects no test from the docs script.

### I broke the guarded behaviours myself and watched the tests fail

Each mutation was applied to the working tree, the emitter/pipeline tests run, then the file was
restored bit-identically (`diff -q` against the backup) and caches cleared.

| mutation | result |
|---|---|
| keycode offset `+8` in `_build_argv` | **6 failed**, incl. `test_real_us_keymap_passes_linux_keycode_through_without_offset` |
| **text appended to argv** (`+ (text,)`) | **6 failed**, incl. `test_text_is_never_an_argv_element`, `test_argv_is_key_with_delay_then_ordered_tokens` |
| **unexpected `28:1`/`28:0` (Return) appended** | **8 failed**, incl. `test_newline_free_text_emits_no_return_or_escape` |
| removed all-unreachable raise (silent) | **1 failed**, `test_all_unreachable_raises_and_spawns_nothing` |
| `pipeline` requires the block (`["ydotool"]`) | **4 failed**, incl. `test_build_injector_defaults_when_the_ydotool_block_is_absent` |

The two falsifications the task specifically demands — **text in argv** and **an unexpected
Return** — both fail loudly. The absent-block `KeyError` is caught. I did not independently
reproduce all 14 of the comment's mutations; the required and highest-risk ones are above, and
they agree with the comment's matrix.

### ADR 0019 and scope

- `-> None` on every new method; `KeySequenceSource` is a Protocol and is the parameter type of
  `YdotoolInjector.__init__`; `TypeText`/`SkipInjection` are frozen dataclasses; the emitter takes
  `Sequence[KeyEvent]`, not positional tuples. Conforms.
- No ADR edits, no ADR 0011 work, no new dependency, no config change. Scope is exactly the
  allowed set.

---

## Minor notes (non-blocking, for the orchestrator)

1. **`build_injector` discards `settings.warnings`.** Its docstring says unknown-key warnings are
   "left for `ges2t doctor` to surface", but `ges2t.doctor` does not yet exist on this branch and
   no other caller parses `[injection.ydotool]`. So an unknown key there is currently silent.
   Ticket 24 owns config plumbing; fine to leave, but the docstring overstates today's behaviour.
2. **Empty-events/empty-unreachable edge case:** a malformed source returning no events and no
   unreachable characters yields `YdotoolTextUnreachableError` with `(<none>)`. The real
   `XkbKeymap` cannot produce this (every char is resolved or listed unreachable), so it is
   cosmetic.
3. **Tests reach private attributes** (`injector._source`, `source._layout`) in
   `tests/test_pipeline.py`. Acceptable for wire-up tests; not a correctness issue.
4. **The measurement's `timeout 45` wrapper and the `--self-test` outputs are asserted in prose
   only**; only `signal.alarm` is in the script. Worth recording in the comment as "run under
   `timeout 45`" if that is how it was invoked.

---

## Bottom line

The rewrite does what the ticket says: text never enters argv, one injection is one batched
`ydotool key` spawn, modifiers wrap correctly, the ADR 0012 text table and ticket 7's error
contract are preserved, unreachable characters are skipped or made a hard error, and the keymap
is rebuilt per utterance. The measurement is plausibly byte-perfect at delay 0 and is far inside
the budget, with a harness whose focus/abort/forbidden-keycode/byte-exact guards I could read and
believe — while being explicit that I could not reproduce it or observe it in a real editor.

**Merge, and immediately schedule the `key_delay_ms` default change (12 → 0) so the shipped
product is not over budget.**
