# 002 — review: layout detection and injection configuration

**Ticket:** `layout-detection-and-config` · **Branch:** `feat/layout-detection-and-config` · **HEAD:** `5332885`
**Reviewer:** adversarial review against ADR 0020's resolution order, ADR 0009's generic/backend-block
split, ADR 0019, and the ticket's Definition of Done. The implementer's `001-comment.md` was treated
as claims to check, not facts. Every claim below was reproduced by hand; probe scripts lived in
`/tmp` outside the repo and have been removed; `git status` was clean before and after.

## Verdict: **pass** — no blocking findings.

The resolution order is exactly ADR 0020's and never guesses `us` (falsification in §1). Both detectors
do bound their subprocesses and return `None` on a hung or absent binary (§2, measured). The real live
session was parsed through the public API (§3). The config split is correct for all three required
behaviours, and the generic layer does not interpret block contents (§4). The ticket-23/25 contract works
as documented and `from_mapping` returns typed dataclasses, not tuples (§5). `DEFAULT_KEY_DELAY_MS` is a
single constant (§6). `mypy` is green and its config bites (§7). Scope is exactly the three named files
plus docs (§8). The suite is fast and touches no real session (§9).

Three **non-blocking test-honesty gaps** were found (F1–F3): behaviours whose removal does *not* fail the
suite. The underlying code is independently verified correct, so none is a functional defect. Two minor
observations (O1–O2) and merge/latency notes follow.

---

## 1. Resolution order — falsified, no silent `us`

ADR 0020 requires explicit **configured** → **live keymap** → detected **layout/variant** → clear error.
No `"us"` literal exists anywhere in `src/` (`grep -rn '"us"' src/` → no matches; the only hits for the
word are docstrings and the unrelated clipboard comment). I built a recording runner and removed each
source in turn:

```
--- Case A: explicit configured, everything else broken ---
  source: configured layout/variant: de nodeadkeys calls: []
--- Case B: auto, live keymap present -> LIVE_KEYMAP, detection NOT probed ---
  source: live keymap calls: ['dump-keymap-wayland'] keymap set: True
--- Case C: auto, live keymap broken, detection present -> DETECTED ---
  source: detected layout: hu calls: ['dump-keymap-wayland', 'getLayoutsList', 'getLayout']
--- Case D: auto, live keymap broken, getLayoutsList broken -> ERROR ---
  raised LayoutResolutionError (no 'us'): True
--- Case E: auto, live keymap broken, list ok but getLayout broken -> ERROR ---
  raised LayoutResolutionError
--- Case F: auto, all tools missing -> ERROR, not us ---
  raised; message mentions xkbcli: True mentions org.kde.keyboard: True contains 'us': False
--- Case G: auto, live keymap empty output -> falls through, not guessed ---
  source: detected
--- Case H: configured whitespace/'auto'/'AUTO'? ---
  configured='' -> live keymap
  configured='  ' -> live keymap
  configured='auto' -> live keymap
ALL ORDER ASSERTIONS PASSED
```

Key facts this establishes:

- **Configured wins and probes nothing**: `runner.calls == []` for an explicit layout, even with a
  working session — an explicit `us` overrides a detected `hu`, as the ADR demands.
- **The live keymap beats detection**: when both are available, only `dump-keymap-wayland` is called.
- **Empty/blank/`auto` mean "detect", not "guess"**: they fall to the live keymap.
- **Exhaustion raises**, and the message names `xkbcli dump-keymap-wayland`, `org.kde.keyboard`, and the
  fix. No branch returns a fabricated `us`.

Mutation check: replacing the `raise LayoutResolutionError(...)` with a silent `ResolvedLayout(...,
layout="us", ...)` fails `test_raises_when_no_source_can_determine_the_layout` (1 failed, 422 passed).
The whole point of the workstream is enforced.

## 2. Nothing can hang — real, bounded, measured

`LAYOUT_COMMAND_TIMEOUT_SECONDS = 2.0` is passed to `subprocess.run(timeout=...)` in `_run_command`. I
shadowed both real binaries with `sleep 30` scripts and called the public API with the **default** runner:

```
LAYOUT_COMMAND_TIMEOUT_SECONDS = 2.0
1. _run_command(['sleep','30'], 0.3) raised TimeoutExpired in 0.30s
2. live_keymap_text() with hanging xkbcli -> None in 2.00s
3. detect_layout() with hanging busctl -> None in 2.00s
```

Absent binaries (`PATH=/nonexistent`) return `None` in ~1 ms via the `OSError` catch:

```
live_keymap_text absent -> None 0.001s
detect_layout absent -> None 0.001s
```

Both functions catch `OSError`/`subprocess.SubprocessError`; the detectors also catch `LayoutParseError`.
The caller cannot block on a wedged compositor.

**Observation O1 (non-blocking):** `detect_layout` makes *two* sequential `busctl` calls, each with the
2 s bound, and `resolve_layout` calls `live_keymap_text` before it, so the worst case is ~6 s of
resolution latency (2 s + 2 s + 2 s) when everything hangs. It is bounded, not a hang, and the normal
path is milliseconds — but if ticket 23's latency budget is tight, a cumulative budget may be worth
considering. **Observation O2 (non-blocking):** `subprocess.run(timeout=...)` kills only the direct
child; my `#!/bin/sh` fake wrappers orphaned their `sleep` grandchildren (I killed them and confirmed
none remain). The real `xkbcli`/`busctl` are native executables with no children, so there is no
production leak today; a process group would be belt-and-braces if a helper ever forks.

## 3. The live-session parse is real

Ran the exact commands by hand (read-only), then through the public API with the default runner:

```
$ busctl --user call org.kde.keyboard /Layouts org.kde.KeyboardLayouts getLayoutsList
a(sss) 1 "hu" "" "Hungarian"
$ busctl --user call org.kde.keyboard /Layouts org.kde.KeyboardLayouts getLayout
u 0
$ busctl --user introspect org.kde.keyboard /Layouts
.getLayout      method  -  u      -
.getLayoutsList method  -  a(sss) -
```

```
detect_layout (real default runner): LayoutInfo(layout='hu', variant='', display_name='Hungarian', index=0)
live_keymap_text is None: False
live_keymap first line: xkb_keymap {
resolve_layout default: live keymap None None None
keymap non-None: True
```

The parser is exercised against the **real** format, not only the fixture; the fixture matches byte for
byte. **Multi-layout + non-zero index are genuinely tested**, not decorative:
`MULTI_LAYOUTS_LIST = 'a(sss) 2 "us" "" "English (US)" "hu" "" "Hungarian"'` with `u 1` asserts
`index=1` and `layout="hu"` in `test_detect_layout_selects_the_active_entry_in_a_multi_layout_session`,
and `test_parse_multi_layout_output_with_a_non_zero_active_index_keeps_every_entry` proves quoted spaces
survive the tokenizer. The multi-layout string is constructed (this machine has one layout), which is
fair — the shape is confirmed against the real single-layout output and the introspected `a(sss)`
signature. Out-of-range and empty-list cases return `None` rather than clamping (§ honoured).

## 4. Config-block behaviour — the ADR 0009 split applied to injectors

Reproduced each required behaviour by parsing a real TOML file:

```
=== 1. generic layer passes blocks through uninterpreted ===
backend: ydotool
injection_blocks: {'ydotool': {'layout': 'hu', 'variant': '', 'key_delay_ms': 8, 'unknown_thing': 'kept'},
                   'clipboard': {'paste_keys': ['ctrl+shift+v'], 'also_unknown': 99}}
generic warnings: []
=== 2. unknown key inside a *selected* block warns (not raises) ===
config: YdotoolConfig(layout='hu', variant='', key_delay_ms=8)
warnings: ["injection.ydotool: unknown key 'unknown_thing'"]
=== 3. block for an *unselected* backend is preserved, not an error ===
backend: clipboard
blocks preserved: ['clipboard', 'ydotool']
warnings: []
=== 4. wrong-typed value raises ===
raised: injection.ydotool.layout: expected a string, got int (5)
=== 5. scalar key under [injection] warns ===
warnings: ["/tmp/.../config.toml: unknown key 'injection.bakend'"]
```

The `config.py` diff (72 lines) is plumbing only: it adds `Config.injection_blocks` and two helpers.
`_read_injection_blocks` copies any nested `[injection.<name>]` table verbatim and skips only `backend`;
`_warn_unknown_injection_keys` inspects only *top-level* keys and treats any `Mapping` as a block. It
never reads a block's contents. The hand-written warning helper is justified: the generic
`_warn_unknown_keys` flags every key not in `known` regardless of type, so it would flag each block name
(verified by reading its loop) — the comment in the diff is accurate. The behavioural asymmetry with
`[engine]` (a non-table under `[engine]` is a hard error; a scalar under `[injection]` warns) is the
implementer's judgement call #4 and is defensible, since `[injection]` is the one section that mixes a
selector with nested blocks. `_INJECTION_KEYS` was already `{"backend"}` and is unchanged, so no
pre-existing key changed classification.

## 5. Consumption contract for tickets 23/25 — verified by calling it

Called exactly the documented shape against a `Config()` with no file:

```
ydotool block absent -> default config: YdotoolConfig(layout='auto', variant='', key_delay_ms=12)
  .layout/.variant/.key_delay_ms: auto '' 12
  .warnings: []
  result type: YdotoolConfigResult | is tuple? False
clipboard block absent -> default config: ClipboardConfig(paste_keys=('ctrl+v',))
  .paste_keys: ('ctrl+v',)
  .warnings: []
  result type: ClipboardConfigResult | is tuple? False
raw ['ydotool'] on absent block -> KeyError (so .get(...,{}) is required)
CONTRACT OK
```

- `config.injection_blocks.get("ydotool", {})` is exactly right: the raw mapping raises `KeyError` when
  the block is absent, and `from_mapping({})` yields all defaults — absent really means "all defaults".
- `from_mapping` returns frozen `YdotoolConfigResult`/`ClipboardConfigResult` (`not isinstance(x, tuple)`),
  matching `WhistleConfigResult`; `.config` and `.warnings` are the documented attributes.
- The ticket-23/25 tickets expect `[injection.ydotool] key_delay_ms` and `[injection.clipboard] paste_keys`
  respectively — both are present and typed (`int`, `tuple[str, ...]`).

**Deviation judgement:** block adapters living in `layout.py` is **acceptable**. The DoD names only
`src/ges2t/injectors/layout.py` and `tests/test_layout.py` and requires the block behaviour to be tested
there; adding an unrequested module would have been scope creep, and keeping per-backend interpretation
out of `config.py` is the ADR 0009 rule. The pattern mirrors `transcribers/whistle_config.py`. The file is
now 664 lines mixing session detection and block parsing; if ticket 23/25 grow the surface, a mechanical
split into `injection_config.py` remains easy (nothing else imports these names yet). Not a defect.

## 6. `DEFAULT_KEY_DELAY_MS = 12` is provisional and single-sourced

`grep -n "12" src/ges2t/injectors/layout.py src/ges2t/config.py` returns only the defining line
(`DEFAULT_KEY_DELAY_MS: int = 12`) and its docstring; no other file references the value. `YdotoolConfig()`
and `from_mapping({})` both read the constant, so ticket 23 changes one line. Its docstring records that
`man ydotool` reports 12 ms and that ticket 23 owns the measured value — consistent with ticket 23's own
note that the man page's 12 ms is stale. Confirmed.

## 7. ADR 0019

```
$ uv run mypy src/ges2t tests
Success: no issues found in 31 source files
```

Full annotations (including `-> None` on the recording-runner `__init__`/`__call__`), frozen dataclasses
for every multi-value return (`LayoutInfo`, `ResolvedLayout`, `YdotoolConfigResult`,
`ClipboardConfigResult`), `LayoutSource` as a `StrEnum` rather than magic strings, explicit `X | None`
instead of implicit Optional, and `Any` used **only** in `Mapping[str, Any]` at the raw-TOML boundary
(which ADR 0019 explicitly permits) — no bare `Any` where a real type is knowable. `tuple[...]` is used
only for genuinely homogeneous sequences (`parse_layouts_list -> tuple[LayoutEntry, ...]`,
`paste_keys: tuple[str, ...]`), and no positional tuple crosses a module boundary.

The config **bites**. A temporary `tests/zz_mypy_probe.py` with an unannotated function and an implicit
Optional:

```
tests/zz_mypy_probe.py:1: error: Function is missing a type annotation  [no-untyped-def]
tests/zz_mypy_probe.py:5: error: Incompatible default for parameter "x" ... [assignment]
Found 2 errors in 1 file (checked 32 source files)
```

Probe removed; `mypy` returned to `Success` and `git status` is clean.

**Observation O3 (non-blocking, ADR tension):** `live_keymap_text -> str | None` and
`detect_layout -> LayoutInfo | None` use `None` as a failure sentinel, which ADR 0019's "never a sentinel"
rule discourages. The ticket's DoD prescribes these exact signatures ("on any failure … returns `None` so
callers can fall back"), and a clear-error alternative is not requested, so following the ticket is
correct here; the tension is worth recording so a future ticket does not treat the signatures as an ADR
violation. `resolve_layout` (the actual decision point) does raise a typed error.

## 8. Scope and hygiene

Changed files vs `main` (`git diff --name-only main...HEAD`):

```
docs/project/README.md
docs/project/core-dictation-pipeline/layout-detection-and-config/001-comment.md
docs/project/core-dictation-pipeline/layout-detection-and-config/ticket.md
src/ges2t/config.py
src/ges2t/injectors/layout.py
tests/test_layout.py
```

- `src/ges2t/injectors/keymap.py` and `src/ges2t/injectors/ydotool.py` are **untouched** by this branch
  (empty diff vs `main`; the last commit touching either is the pre-existing ticket-7 commit). `keymap.py`
  is ticket 22's, as required.
- The only `src/` modules changed are `config.py` and the new `injectors/layout.py`.
- The board diff is a single ticket-24 row (`to do` → `in review`, branch named); `ticket.md` changes only
  its status line.
- No conflict markers (`git grep -E '^(<<<<<<<|=======|>>>>>>>)' HEAD -- src tests` → none), no stray
  `xkbcli`/`busctl`/`ges2t` processes or sockets (the one `tmux -L default attach-session -t ges2t` is a
  pre-existing user session, unrelated), probes fully reverted, `git status` clean.

**Merge note for the orchestrator:** the branch is based on merge-base `36aad65`; `main` has since moved
to `d832def` (ticket 10 merged, adding `src/ges2t/cli.py`). Since `36aad65..main` changes only `cli.py`
and docs, there is **no file overlap** with this branch and the merge should be conflict-free; rebasing
onto current `main` before merging is still the safe order.

## 9. Tests — honest, fast, session-free

```
$ uv run pytest
423 passed in 1.49s
$ uv run pytest tests/test_layout.py -q
76 passed in 0.13s
```

`tests/test_layout.py` alone collects **76** tests. Baseline at the merge-base is **347**; 347 + 76 = 423,
so the implementer's total is right (the comment's "346 pre-existing + one incidental" breakdown is a
minor arithmetic slip — the pre-existing count is 347; total unchanged).

**No real D-Bus or Wayland session is touched.** Every test passes an injected runner (or calls the pure
parsers). Proof: I ran the layout tests with `subprocess.run`/`Popen`/`call` monkeypatched to raise via a
throwaway `/tmp` pytest plugin — `76 passed in 0.13s`, i.e. no test ever spawned a process. There is no
`monkeypatch`, no `_run_command` call, and no `subprocess.run` in the test file.

**Falsification by mutation** (a copy of the tree at `/tmp/mut`, restored between mutations; the repo was
never edited for this):

| Mutation | Suite |
|---|---|
| M1 replace the final `raise` with a silent `us` fallback | **1 failed** |
| M5 unknown block key raises instead of warns | **2 failed** |
| M6 drop blocks for the unselected backend | **3 failed** |
| M7 detect before the live keymap (order swap) | **4 failed** |
| M8 clamp an out-of-range index to 0 | **1 failed** |
| M9 accept an empty/whitespace keymap | **1 failed** |
| M11 drop `SubprocessError` from `live_keymap_text`'s catch | **1 failed** |
| M12 drop `LayoutParseError` from `detect_layout`'s catch | **1 failed** |
| M2 remove the `getLayout` non-zero-exit guard | 423 passed (**gap F1**) |
| M10 remove the `getLayoutsList` non-zero-exit guard | 423 passed (**gap F1**) |
| M3 remove the `xkbcli` non-zero-exit guard | 423 passed (**gap F2**) |
| M4 ignore the timeout in `_run_command` (`timeout=None`) | 423 passed (**gap F3**) |

The behaviours that carry the ticket's weight (order, no-`us`, warn-don't-raise, preserve-unselected,
reject-garbage) are all pinned by tests that fail when removed.

### Non-blocking findings

- **F1/F2 — the non-zero-exit guards are not isolated.** Both detectors' `returncode != 0 → None` guards
  exist and are correct: a direct probe feeding `rc=1` **with non-empty/valid stdout** returns `None`
  (`live_keymap_text rc=1 + non-empty stdout -> None`; `detect_layout getLayout rc=1 + valid stdout ->
  None`; `getLayoutsList` likewise). But the tests that claim to cover non-zero exit supply *empty* stdout,
  so they also pass through the parse-failure path and the suite cannot tell the guard was removed. Suggest
  one test per guard with `rc=1` and a valid-looking body.
- **F3 — the timeout is never enforced in a test.** `test_live_keymap_text_uses_a_bounded_timeout` and
  `test_detect_layout_passes_a_bounded_timeout_to_every_call` assert the *value* handed to an injected
  runner, not that `subprocess.run` applies it; no test calls `_run_command`. Removing the `timeout=`
  argument from the real runner leaves all 423 tests green. Enforcing the bound is verified here by hand
  (2.00 s), but a unit test that runs `_run_command(["sleep","30"], 0.3)` and expects `TimeoutExpired`
  would pin it. This is the one behaviour the DoD calls out for the "never hangs" requirement that the
  suite does not actually test.

None of F1–F3 is a functional defect; they are test-strength gaps, recorded so the next ticket does not
assume the suite has pinned them.

## Could not verify

- A **real multi-layout KDE session** and a **non-zero active index** on hardware: this machine exposes a
  single layout (index `0`). The multi-layout path is covered only by a constructed fixture whose format
  was confirmed against the live output and the introspected `a(sss)` signature.
- A **non-KDE compositor**: the `None`/"clear error" paths are unit-tested with injected failures
  (`FileNotFoundError`, non-zero exit, garbage, timeout), but no second compositor was available to
  exercise end to end.
- `all-unreachable` / cross-layout behaviour is ticket 23's and out of scope here.

## Follow-ups (not blocking this merge)

1. Add the F1/F2/F3 tests above (or hand them to ticket 23's test run) so the guards and the real
   timeout are pinned.
2. Consider a cumulative resolution budget if ticket 23's latency measurement finds the ~6 s worst-case
   resolution path matters (O1).
3. Optional: validate `injection.backend` against the known set in the generic layer so a typo cannot
   silently fall back to defaults — pre-existing, out of scope, noted for the config/doctor tickets.
