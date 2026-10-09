# 002 — Review: clipboard-paste injector (#25)

- **Ticket:** [ticket.md](ticket.md)
- **Branch:** `feat/clipboard-paste-injector`
- **Worktree:** `.worktrees/clipboard-paste-injector`
- **Reviewer HEAD:** `fcdba62`
- **Diff basis:** `git diff main...feat/clipboard-paste-injector`

## Verdict: **REQUEST CHANGES** — do not merge yet

Three defects, one of them production-fatal and invisible to the suite as written:

1. **D1 (critical): the "restore the previous clipboard" step never receives the
   previous selection in the real runner.** The restore is effectively a no-op
   that *overwrites the transcript-era clipboard with empty content*. The test
   double records `stdin=previous`, so every restore test passes while the real
   seam does the opposite.
2. **D2 (high): the letter-keycode fallback in `KeyChord.parse` is wrong for 21
   of the 26 letters** (only the five hardcoded ones are right). A mistyped
   `paste_keys` chord can silently emit a *different, potentially destructive*
   chord instead of failing loudly.
3. **D3 (medium): a `wl-paste` timeout escapes as a raw
   `subprocess.TimeoutExpired`, not a typed `ClipboardError`**, contradicting the
   claimed error contract for the read path.

Everything else I checked is solid: the chord, no-text-in-argv, no silent
backend fallback, unknown-backend raise, ADR 0012 empty/trailing-space, config
defaults/warnings, scoping, and the untouched `ydotool` path are all as claimed,
and nothing in the suite could have touched the user's real clipboard.

---

## 0. Real-clipboard safety — the highest-stakes check

**Nothing in the suite could have written, or did write, the user's real
clipboard.** Every clipboard invocation goes through the injected runner.

Evidence from source and tests:

- `ClipboardPasteInjector.inject` only ever calls `self._runner`, which defaults
  to the real `_run_command` **only when `runner=None`**. Production
  `build_injector` is the sole place that omits it.
- The test file constructs the injector exactly twice
  (`tests/test_clipboard_injector.py:174` in `_injector()` and
  `tests/test_clipboard_injector.py:625`), and **both pass
  `runner=FakeClipboardRunner()`**. There is no bare `ClipboardPasteInjector()`
  anywhere in `tests/`.
- `tests/test_clipboard_injector.py` contains **no process-spawning call at
  all**: grepping every `subprocess.*` occurrence yields only
  `subprocess.CompletedProcess` (×3) and `subprocess.TimeoutExpired` (×1).
  `grep -rn Popen tests/*.py` is empty.
- `test_pipeline.py`'s clipboard tests assert on `isinstance` / attributes; they
  never call `inject()`.

Display-independence (a test that needed a display would be a red flag):

```
$ env -u WAYLAND_DISPLAY -u DISPLAY uv run pytest -q
656 passed in 2.73s
```

The implementer's falsification harness is in `/tmp` and uncommitted:

```
/tmp/ticket25_mutations.py   (5323 bytes, untracked)
/tmp/ticket25_evidence.py    (1494 bytes, untracked)
git ls-files | grep ticket25  -> not tracked
```

It only shells out to `uv run pytest`; it never runs `wl-copy`/`ydotool`.

**My own probes touched no clipboard and emitted no keystroke.** For the D1
proof I substituted a harmless `tee` for `wl-copy` in the *exact* deferred
runner shape, writing only to `/tmp`; the other probes used a synthetic runner
that raises immediately. No `wl-copy`, `ydotool`, or `ges2t run` was executed.

**Conclusion:** the only path that can write the user's clipboard is
`build_injector` → `inject()` with the default runner, i.e. a real run. No test
reaches it, so nothing in the suite wrote or could have written the real
clipboard.

---

## 1. Mechanism falsifications (with real output)

### 1.1 The chord is literal evdev keycodes, no offset

`test_ctrl_v_argv_is_the_literal_evdev_chord` asserts the literal tokens, not a
constant comparison:

```python
assert runner.ydotool_calls()[0].argv == (
    "ydotool", "key", "-d", "12", "29:1", "47:1", "47:0", "29:0",
)
```

`29` = `KEY_LEFTCTRL`, `47` = `KEY_V`. This is a genuine literal assertion.

### 1.2 Reordering the releases fails a test (my mutation R1)

Mutation: `for code in reversed(codes)` → `for code in codes`.

```
R1 release order reversed -> press order: caught (exit 1) 4 failed, 53 passed
FAILED ...::test_ctrl_v_argv_is_the_literal_evdev_chord
FAILED ...::test_ctrl_shift_v_releases_modifiers_in_reverse_order
FAILED ...::test_chord_is_emitted_once_per_configured_chord
FAILED ...::test_missing_wl_paste_is_tolerated
```

### 1.3 Text never in argv or chord stdin (my mutation R2)

Mutation: `argv = (WL_COPY_BINARY, payload)` with `stdin=b""` instead of
`argv=(WL_COPY_BINARY,)` with the payload on stdin.

```
R2 text becomes an argv element: caught (exit 1) 6 failed, 51 passed
FAILED ...::test_text_is_never_an_argv_element
FAILED ...::test_payload_is_delivered_to_wl_copy_over_stdin
FAILED ...::test_trailing_space_appends_exactly_one_space
FAILED ...::test_no_trailing_space_leaves_text_unchanged
FAILED ...::test_text_already_ending_in_space_gets_a_second_one
FAILED ...::test_non_ascii_text_is_delivered_verbatim
```

`test_text_never_reaches_the_chord_stdin` additionally asserts every
`ydotool` call has `stdin == b""`.

### 1.4 No silent fallback when `wl-copy` fails (my mutation R4)

Reading the code: `_write_payload` calls `_run(WL_COPY_BINARY, …)`; `_run`
translates `FileNotFoundError` → `ClipboardNotFoundError`, `TimeoutExpired` →
`ClipboardTimeoutError`, other `OSError` → `ClipboardSpawnError`, and a non-zero
status → `ClipboardInjectionError`. There is no `ydotool`-type fallback anywhere
in the clipboard module.

Mutation: `if completed.returncode != 0:` → `if False:` (swallow a `wl-copy`
failure):

```
R4 wl-copy non-zero exit swallowed (silent no-op): caught (exit 1) 2 failed, 55 passed
FAILED ...::test_wl_copy_nonzero_exit_reports_stderr_and_does_not_swallow
FAILED ...::test_wl_copy_nonzero_with_empty_stderr_still_raises
```

### 1.5 Unknown backend raises instead of defaulting (my mutation R3)

Mutation: `_INJECTOR_BUILDERS.get(backend)` →
`_INJECTOR_BUILDERS.get(backend, _INJECTOR_BUILDERS["ydotool"])`.

```
R3 unknown backend silently defaults to ydotool: caught (exit 1) 2 failed, 32 passed
FAILED ...::test_build_injector_rejects_an_unknown_backend_instead_of_defaulting
FAILED ...::test_build_injector_unknown_backend_from_real_toml_raises
```

The real message names the value and both handled backends:
`injection backend 'clipbord' is not handled by this build; handled backends:
'clipboard', 'ydotool'`.

### 1.6 ADR 0012 — empty/whitespace injects *and copies* nothing

`test_empty_or_whitespace_injects_nothing_and_copies_nothing` asserts
`runner.calls == []` (a **call count**, not just output) over `["", " ", "   ",
"\t", "\n", " \t\n ", "\u00a0"]`, and `last_injection_ms is None`. The two
trailing-space cases assert the payload bytes exactly (`b"hello world "`,
`b"done  "`, non-ASCII verbatim).

### 1.7 Config

- Absent block → defaults via `.get("clipboard", {})`; verified through a **real
  TOML file** on disk in `test_build_injector_from_real_toml_selects_the_clipboard_backend`
  and `..._defaults_to_ydotool` (and the unknown-backend TOML test).
- Explicit `paste_keys = ["ctrl+shift+v"]` honoured (unit and real-TOML tests).
- An invalid chord spelling raises `ClipboardKeyChordError` at *construction*
  (`test_injector_rejects_a_malformed_paste_key_at_construction`) — but see D2:
  a *well-formed but wrong* letter does not raise.
- `ClipboardConfig.from_mapping` warnings are surfaced on `injector.last_warnings`
  (`test_build_injector_clipboard_surfaces_the_block_warnings`,
  `test_injector_surfaces_config_warnings`).

### 1.8 `build_injector` is the only composition point; `ydotool` untouched

- `git diff main...feat/clipboard-paste-injector -- src/ges2t/injectors/ydotool.py`
  is empty. `tests/test_ydotool_emitter.py`, `test_injector.py`, `test_layout.py`
  are unchanged. The old `build_injector` body moved verbatim into
  `_build_ydotool_injector` (same positional `YdotoolInjector(...)` call).
- `tests/test_pipeline.py` deletes exactly **one line** (an import, widened) and
  adds 139; no test was deleted or weakened.

---

## 2. Defects (blocking)

### D1 — CRITICAL: the deferred restore never delivers the previous selection

`_restore_clipboard` correctly passes the previous bytes to the runner as
`stdin_bytes`:

```python
completed = self._runner(argv, previous, environment, delay_s=self.restore_delay_s)
```

but the real runner's deferred branch **ignores `stdin_bytes`**:

```python
script = "sleep " + format(delay_s, ".3f") + '; exec "$@"'
subprocess.Popen(["sh", "-c", script, "sh", *argv],
                 stdin=subprocess.PIPE, ..., start_new_session=True)
stdin_bytes = b""          # <- dead assignment; never written to the pipe
```

Nothing is ever written to `proc.stdin`; the `Popen` object is not retained, so
its write end is closed when it is collected. The restore child (after its
sleep) execs `wl-copy`, reads **EOF**, and copies an **empty** selection.

Proof, using `tee` in the exact deferred shape (no clipboard involved):

```
$ uv run python /tmp/restore_probe.py
synthetic returncode: 0
file exists: True
file bytes: b''
```

So the previous selection bytes (`b"PREVIOUS-SELECTION"`) never reach the child.
The fake runner records them (`ClipboardRunnerCall.stdin=previous`), which is why
`test_delivery_order_is_read_copy_paste_restore`,
`test_restore_uses_the_configured_bounded_delay` and the best-effort tests all
pass: **the double and the real seam disagree about the contract.**

Consequences:

- The ticket's "restore the previous clipboard after the paste has had time to
  land" is **not implemented in production**.
- A real run ends with the clipboard set to empty (or otherwise not restored),
  not the user's previous selection — the manual step's `restored: True` would
  come back `False`, and item 6 of the manual script ("`wl-paste` still returns
  your original selection") would fail.
- `001-comment.md`'s diagram line `4. wl-copy < previous on stdin (delay 0.4s)`
  is not true of the real path.

Suggested fix: write `stdin_bytes` to the child's pipe and close it (e.g. keep
the `Popen` handle, `proc.stdin.write(...)`/`close()`), or pass the payload by a
channel the deferred branch actually consumes. Crucially, add a test that
exercises the **real** `_run_command` deferred path with a harmless command (as
this review did with `tee`) so the seam contract cannot drift again.

### D2 — HIGH: the letter-keycode fallback is wrong for 21 of 26 letters

`_resolve_code` resolves a single letter with

```python
return 30 + (ord(upper) - ord("A"))
```

on the false premise (stated in the docstring) that the evdev letter block is
contiguous alphabetically. The real block is `QWERTYUIOP` / `ASDFGHJKL` /
`ZXCVBNM`. Only `a, z, x, c, v` are correct — and only because all five are
hardcoded in `_NAMED_KEY_CODES`; the fallback itself is never exercised by a
correct case. Probe against the real evdev table:

```
letters checked: 26  wrong-or-error: 21
  ctrl+q: evdev 16 -> 46   (KEY_C!)
  ctrl+r: evdev 19 -> 47   (KEY_V!)
  ctrl+b: evdev 48 -> 31   (KEY_S)
  ctrl+e: evdev 18 -> 34   (KEY_G)
  ctrl+j: evdev 36 -> 39   (KEY_SEMICOLON)
  ctrl+m: evdev 50 -> ERROR: ... ends with the modifier 'm' ...
  ... (21 total)
```

This is not merely inert: `paste_keys = ["ctrl+q"]` (or any of the 21) emits a
**different** chord — `ctrl+q` silently becomes `ctrl+c`, `ctrl+r` becomes
`ctrl+v`, i.e. a config typo can produce a destructive keystroke. That directly
violates the module's promise that "a typo in the config is a loud, actionable
failure rather than a chord that silently does nothing" and the review checklist
item "an invalid spelling produces a clear error rather than a silently wrong
chord".

The test `test_letter_and_digit_keys_resolve_without_offset` only asserts
`ctrl+a` and `ctrl+z` — both named — plus the (correct) digit cases, so the
broken fallback is invisible.

Suggested fix: replace the arithmetic fallback with the real evdev letter table,
or reject any letter not in a complete table (loud error). Add a test over the
whole letter block, not just `a` and `z`.

### D3 — MEDIUM: `wl-paste` timeout escapes untyped

`_read_clipboard` catches only `FileNotFoundError`:

```python
try:
    completed = self._runner(argv, b"", environment)
except FileNotFoundError:
    return None
```

It calls `self._runner` directly rather than `self._run`, so a wedged compositor
(watchdog timeout) surfaces as a raw `subprocess.TimeoutExpired` for the
read path. Probe (synthetic runner, no clipboard):

```
UNTYPED: subprocess.TimeoutExpired - Command 'wl-paste' timed out after 5.0 seconds
```

The ticket lists "no Wayland session / no clipboard" under failures that must
raise clear, actionable errors; a raw `TimeoutExpired` is neither typed nor
actionable. Fix: either tolerate the read timeout like a missing `wl-paste`
(`return None`, consistent with best-effort read) or raise
`ClipboardTimeoutError(WL_PASTE_BINARY, 5.0)`; add the corresponding test.

---

## 3. Mechanicals and my own mutations

```
$ uv run pytest -q                          -> 656 passed in 2.73s   (WAYLAND_DISPLAY/DISPLAY unset)
$ uv run pytest tests/test_clipboard_injector.py --collect-only -q
                                            -> 57 tests collected
$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
                                            -> Success: no issues found in 41 source files
```

- New clipboard tests: exactly **57** (collected independently). New
  backend-selection tests in `test_pipeline.py`: **10** (counted). `656 − 57 −
  10 = 589`, consistent with the claimed `main` baseline; I did **not**
  re-check out `main` to re-run it.
- No test deleted/skipped/weakened: `test_pipeline.py` has one deleted line (an
  import), the `ydotool` test files are byte-identical, and `pytest` reports no
  skips.
- My own mutation harness `/tmp/reviewer25_mutations.py` applied **four**
  mutations to the real source, ran the affected test file as a fresh subprocess
  with clear caches and a 180 s per-run timeout, and restored after each:

```
R1 release order reversed -> press order:        caught (exit 1) 4 failed, 53 passed
R2 text becomes an argv element:                 caught (exit 1) 6 failed, 51 passed
R3 unknown backend silently defaults to ydotool: caught (exit 1) 2 failed, 32 passed
R4 wl-copy non-zero exit swallowed:              caught (exit 1) 2 failed, 55 passed
uncaught: 0
```

`__pycache__`/`.mypy_cache` were cleared before every run, and
`git status` is clean with `src`/`tests` byte-identical to `HEAD`. The
implementer's 10-mutation harness was not re-run in full, but I independently
reproduced its two headline cases (text-in-argv, unknown-backend-default) as R2
and R3.

- ADR 0019: `mypy` clean; `KeyChord` and `ClipboardRunnerCall` are frozen
  dataclasses; the seam is a `Protocol`; every function is annotated including
  `-> None`.
- Scope is exactly the six allowed code/test files plus `docs/project/README.md`
  and the ticket's own status/`001-comment.md`.

---

## 4. Honesty of `001-comment.md`

Good:

- The user-owned manual step is prominently flagged: "A real run writes to the
  user's clipboard and emits a keystroke into whatever window has focus. The
  agent did not and must not do this", with the script printing the current
  clipboard first and stating "that value will be temporarily replaced". The
  DoD box for the manual step is left unticked and the reason is given.
- The error-contract, config and `ydotool`-unchanged claims match the code.

Problems:

- **The restore claim is false of the production path.** The sequence diagram
  and the "Restore is best effort" paragraph describe `wl-copy < previous on
  stdin`, but the real deferred runner discards `stdin_bytes` (D1). The restore
  is verified only against the fake, which is exactly the kind of claim that
  needed the (skipped) manual run to be true.
- **The "a single letter or digit" claim is false for 21 letters** (D2).
- Minor: `001-comment.md` says "The DoD box for this step is left unticked",
  implying the others are ticked, but **no** DoD box in `ticket.md` is ticked.
- Minor: "The `ydotool` path is byte-for-byte the previous code" is true of the
  code body; the helper's docstring was edited (harmless).

The suite is honest about what it does not do (fake runner everywhere), but the
comment overstates the production restore.

---

## 5. What I could not verify

- Real `wl-copy`/`wl-paste`/`ydotool`/`ydotoold` behaviour and the manual
  end-to-end run (forbidden here and user-owned). This is precisely the run that
  would have exposed D1.
- The `main` baseline of 589 tests was not re-run; it is arithmetically
  consistent with the independently collected 57 + 10.
- The deferred restore's real-world timing/race (0.4 s vs the application's
  selection round trip) — unobservable without a live session.
- Whether `wl-copy` with empty stdin clears the selection or sets an empty one;
  either way the previous selection is not restored (D1).
