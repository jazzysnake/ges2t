# 002 — review: centralize identifier literals (ticket 48)

- **Verdict:** `pass`
- **Branch:** `feat/centralize-identifier-literals`, worktree `.worktrees/centralize-identifier-literals`
- **Reviewed tip:** `f8a1cad` (`f8a1cadf17192d3a9ab6c0a38f09627529a0f680`)
- **Base:** `main` = `1b4d4941e4c0032524ebaea14ce5a1d9a5544c58`
- **Diff range read:** `git diff main...feat/centralize-identifier-literals` (three-dot)
- **Context read:** `ticket.md`, `epic.md`, `docs/code-quality.md` (pointer to the `code-quality` skill),
  `001-comment.md`. No ADRs were changed by this ticket.

This is not a measurement artifact; the central claim ("de-duplication only, values byte-identical")
is a factual claim about code and was falsified mechanically (below). **Safe to relay to the user:** yes.

---

## Verdict summary

The refactor is a genuine de-duplication: a new leaf module `src/ges2t/identifiers.py` is the single
definition site, every replaced literal is **byte-identical**, `doctor.py`'s duplicate
`WHISPER_CPP_ENGINE` definition is gone, the file imports nothing from `ges2t`, `tests/` is untouched,
and the scan (reproduced independently, **56 → 41**) and its leftovers are honest. All six DoD items
are met. No blocking findings. Four minor, non-blocking documentation/residual notes are recorded under
**Risks**.

---

## Per-criterion findings (definition of done)

### DoD 1 — single source of truth, shape justified — ✅ met

`src/ges2t/identifiers.py` (107 lines, new) defines each identifier once and carries a docstring
justifying the single-leaf shape (drift is cross-module; a per-domain split re-opens the ownership
question that caused ticket 47). `config.py` re-exports the previously-public names by **import**, not
by a second assignment.

Evidence — the only assignments of identifier names in `src/` are in `identifiers.py`:

```
$ grep -rn -E '^(WHISTLE_ENGINE|WHISPER_CPP_ENGINE|WHISPER_CPP_CLI_TOKEN|YDOTOOL_BACKEND|CLIPBOARD_BACKEND|INJECTION_DEFAULT_BACKEND|NEEDLE_SETUP_BACKEND|WHISTLE_MODEL|WHISPER_MODEL_MEDIUM_Q5_0|WHISPER_MODEL_SMALL_Q8_0|HOTKEY_MODE_PTT|HOTKEY_MODE_TOGGLE|CONFIG_KEY_BACKEND)\b' src/ges2t | sort
src/ges2t/identifiers.py:101:HOTKEY_MODE_TOGGLE: str = "toggle"
src/ges2t/identifiers.py:107:CONFIG_KEY_BACKEND: str = "backend"
src/ges2t/identifiers.py:50:WHISTLE_ENGINE: str = "whistle"
src/ges2t/identifiers.py:55:WHISPER_CPP_ENGINE: str = "whisper_cpp"
src/ges2t/identifiers.py:61:WHISPER_CPP_CLI_TOKEN: str = "whisper.cpp"
src/ges2t/identifiers.py:66:YDOTOOL_BACKEND: str = "ydotool"
src/ges2t/identifiers.py:69:CLIPBOARD_BACKEND: str = "clipboard"
src/ges2t/identifiers.py:72:INJECTION_DEFAULT_BACKEND: str = YDOTOOL_BACKEND
src/ges2t/identifiers.py:78:NEEDLE_SETUP_BACKEND: str = "needle"
src/ges2t/identifiers.py:85:WHISTLE_MODEL: str = "whistle"
src/ges2t/identifiers.py:89:WHISPER_MODEL_MEDIUM_Q5_0: str = "medium-q5_0"
src/ges2t/identifiers.py:93:WHISPER_MODEL_SMALL_Q8_0: str = "small-q8_0"
src/ges2t/identifiers.py:98:HOTKEY_MODE_PTT: str = "ptt"
```

### DoD 2 — literals replaced across `src/` only; tests keep literals; **no behaviour change** — ✅ met

**Central claim falsified, not read.** I imported both `main` and the branch in their own worktrees and
diffed live values for every touched identifier. No mismatch:

```
$ uv run python /tmp/capture_values.py   # (main)   > /tmp/values_main.json
$ uv run python /tmp/capture_values.py   # (branch) > /tmp/values_branch.json
common keys: 24
MISMATCHES: NONE
branch-only keys: identifiers.* (13 names)   # expected, the new module
```

Cross-check of each `identifiers.*` value against its `main` counterpart:

```
identifiers.WHISTLE_ENGINE            'whistle'      vs main config.WHISTLE_ENGINE='whistle'        OK
identifiers.WHISPER_CPP_ENGINE        'whisper_cpp'  vs main config.WHISPER_CPP_ENGINE='whisper_cpp' OK
identifiers.INJECTION_DEFAULT_BACKEND 'ydotool'      vs main config.INJECTION_DEFAULT_BACKEND        OK
identifiers.NEEDLE_SETUP_BACKEND      'needle'       vs main setup_command.NEEDLE='needle'           OK
identifiers.WHISPER_CPP_CLI_TOKEN     'whisper.cpp'  vs main setup_command.WHISPER_CPP='whisper.cpp' OK
identifiers.WHISTLE_MODEL             'whistle'      vs main model_store.NEEDLE_MODELS[0].name       OK
identifiers.WHISPER_MODEL_MEDIUM_Q5_0 'medium-q5_0'  vs main whisper_engine.DEFAULT_WHISPER_MODEL_GPU OK
identifiers.WHISPER_MODEL_SMALL_Q8_0  'small-q8_0'   vs main whisper_engine.DEFAULT_WHISPER_MODEL_CPU OK
identifiers.CONFIG_KEY_BACKEND        'backend'      vs main config._INJECTION_KEYS='backend'        OK
HOTKEY pair == main KNOWN_HOTKEY_MODES: True
socket_proto._WAKE_SELECTOR_DATA = 'wake' ; _LISTEN_SELECTOR_DATA = 'listen'   # main literals were "wake"/"listen"
```

Also unchanged (24/24 common keys, e.g. `config.KNOWN_ENGINES`, `ENGINE_ALIASES`, `DEFAULT_ENGINE`,
`BATCH_ONLY_ENGINES`, `pipeline._TRANSCRIBER_BUILDERS`/`_INJECTOR_BUILDERS` keys,
`whisper_cpp_config.BLOCK_NAME`, `whistle_config.BLOCK_NAME`, `doctor.WHISPER_CPP_ENGINE`).

The scanner (DoD 3) shows **no new literal site** was introduced (branch-minus-main is empty), so the
only source changes are the constant *sources*.

`tests/` untouched:

```
$ git diff main...HEAD -- tests/          # empty
$ git diff main...HEAD --numstat -- tests/  # empty
```

Tests keep their literal assertions, e.g. `tests/test_config.py:125 assert INJECTION_DEFAULT_BACKEND == "ydotool"`,
`tests/test_layout.py:525 ... == "ydotool"`; **no test imports `ges2t.identifiers`** (`grep -rn identifiers tests/` → empty).

### DoD 3 — AST scan over `src/ges2t/` only, reported with leftovers + honest limitation — ✅ met (see R3)

The implementer's scan script was not committed (correct — the ticket calls it a verification step).
I **re-implemented it independently** (comparison positions: operands of `==`/`!=`/`in`/`not in`, match
values, `.get(<literal>, …)` keys; deduped by file+line+literal) and reproduced the exact counts:

```
=== BRANCH (feat) ===
# distinct literal sites in comparison positions: 41
=== MAIN ===
# distinct literal sites in comparison positions: 56
```

Line-number-normalised set difference (`file kind literal`) — the **removed** sites are exactly the 15
identifier comparisons the diff claims (multiset, so duplicates retained):

```
config.py       [compare] 'backend'
doctor.py       [compare] 'clipboard'   (x2)
doctor.py       [compare] 'whistle'
doctor.py       [compare] 'ydotool'     (x2)
doctor.py       [get-key] 'ydotool'
pipeline.py     [compare] 'clipboard'
pipeline.py     [compare] 'whisper_cpp'
pipeline.py     [compare] 'whistle'
pipeline.py     [get-key] 'clipboard'
pipeline.py     [get-key] 'whisper_cpp' (x2)
pipeline.py     [get-key] 'ydotool'
socket_proto.py [compare] 'wake'
```

**branch − main = ∅** (no literal site added). The 41 leftovers all fall into the table's categories
(empty-string/control char, XDG/env names, third-party protocol keys, ges2t wire/manifest fields, char
parsing, binary/path names, stdlib `byteorder`); I spot-checked each against the code. The stated static
limitation is itself true: the scan sits in syntactic comparison positions and therefore does **not**
see the `pipeline._TRANSCRIBER_BUILDERS`/`_INJECTOR_BUILDERS` **dict-literal keys** (main's 56 has no
such hit) nor the `SetupBackend` **enum values** — both were fixed by hand, as the comment says.

### DoD 4 — inventory recorded in `001-comment.md` — ✅ met (see R4)

The inventory table covers every constant in `identifiers.py` with definition site and changed sites,
and matches the diff (13 constants, 12 source files touched). One newly-introduced local constant is not
tabled (R4).

### DoD 5 — `pytest` green, `mypy` 0, before/after counts, no test weakened — ✅ met (see R1)

```
$ uv run pytest -q                      # branch
1230 passed in 8.41s
$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests   # branch
Success: no issues found in 65 source files

$ uv run pytest -q                      # main (baseline)
1230 passed in 9.42s
$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests   # main (baseline)
Success: no issues found in 64 source files
```

65 = `src` 32 + `tests` 33, and `[tool.mypy]` has **no `exclude`**, so `tests/` really is type-checked
(no scope trick). No test was added/deleted/skipped/weakened (`tests/` diff empty).

### DoD 6 — no new dependency, no linter, no behaviour change — ✅ met

```
$ git diff main...HEAD -- pyproject.toml uv.lock   # empty
```

No new numeric literal in `identifiers.py`, no new branch/dead code (the only `+elif` is the rewritten
comparison), no new dependency or linter.

---

## Enforcement probes (all reverted; caches cleared after each mutation)

### P1 — no behaviour change (central claim)

Live-value diff of `main` vs branch (above): **MISMATCHES: NONE** across 24 shared keys, all 13
`identifiers.*` values equal their main counterparts. Scanner shows no new literal site. **Not
falsified.**

### P2 — single source; one drift reaches many sites

`doctor.py` no longer *defines* `WHISPER_CPP_ENGINE` — it imports it (`doctor.py:72`) and uses it at
782/859. Drift probe:

```
$ sed -i 's/^CLIPBOARD_BACKEND: str = "clipboard"/CLIPBOARD_BACKEND: str = "clipboardX"/' src/ges2t/identifiers.py
$ rm -rf .mypy_cache __pycache__ && uv run pytest -q | grep -E '^FAILED|failed'
11 failed, 1219 passed in 9.67s
FAILED tests/test_doctor.py::test_clipboard_backend_fail_when_wl_copy_is_missing
FAILED tests/test_doctor.py::test_injection_config_warns_on_unknown_clipboard_key
FAILED tests/test_pipeline.py::test_build_injector_clipboard_carries_the_trailing_space_setting
FAILED tests/test_pipeline.py::test_build_injector_clipboard_defaults_when_the_block_is_absent
FAILED tests/test_pipeline.py::test_build_injector_clipboard_surfaces_the_block_warnings
FAILED tests/test_pipeline.py::test_build_injector_from_real_toml_selects_the_clipboard_backend
FAILED tests/test_pipeline.py::test_build_injector_reads_the_clipboard_paste_keys
FAILED tests/test_pipeline.py::test_build_injector_returns_the_clipboard_backend_when_selected
FAILED tests/test_pipeline_streaming.py::test_build_service_structurally_refuses_streaming_with_a_non_typing_injector
FAILED tests/test_pipeline_streaming.py::test_clipboard_with_streaming_is_a_loud_actionable_error
FAILED tests/test_pipeline_streaming.py::test_run_service_reports_the_clipboard_streaming_conflict
```

One edit to `identifiers.py` breaks 11 load-bearing tests across three modules — the constant is
**read**, not cosmetic. Reverted:

```
$ git checkout -- src/ges2t/identifiers.py && rm -rf .mypy_cache __pycache__
$ git diff --stat              # empty
$ git status --porcelain       # empty
$ diff -q /tmp/identifiers.py.bak src/ges2t/identifiers.py
BYTE-IDENTICAL to pre-probe backup
```

### P3 — leaf purity / derived collections / re-exports

```
$ python - <<'PY' ... ast.parse identifiers.py
from __future__ import ['annotations']      # the ONLY import
```
`grep 'ges2t' identifiers.py` hits are docstring prose only. Derived collections stay in their owners:
`KNOWN_ENGINES`/`ENGINE_ALIASES`/`BATCH_ONLY_ENGINES` in `config.py`,
`NEEDLE_MODELS`/`DEFAULT_NEEDLE_MODEL` in `model_store.py`. `config.py` names are imported re-exports
(lines 42–45, listed in `__all__`), never reassigned.

### P4 — tests untouched

`git diff main...HEAD -- tests/` empty (shown above).

### P5 — AST scan

Reproduced 56 → 41 (shown above); removed set = the 15 claimed identifier sites; branch−main = ∅.

### P6 — rules compliance / clean tree

No new dep/linter (empty pyproject/uv.lock diff). No magic number introduced. Final:

```
$ git status --porcelain     # empty
$ git diff --stat            # empty
HEAD=f8a1cadf17192d3a9ab6c0a38f09627529a0f680
```

No `src/` left mutated; no probe file inside the repo (probe helper lived in `/tmp`, removed below).

---

## Risks

- **R1 (doc, minor).** `001-comment.md` reports the *after* mypy count (0 / 65) but not the *before*
  count, though the DoD asks for before/after. Measured: **main 0 / 64 → branch 0 / 65** (the +1 is
  `identifiers.py`). No correctness impact.
- **R2 (residual drift, minor).** `service.Command.TOGGLE = "toggle"` / `cli.py` `"toggle"` are a second
  spelling of `HOTKEY_MODE_TOGGLE`'s value. The comment scopes this out as the *command-verb* family
  (a distinct identifier, backed by `service.Command`) and the ticket's bounded-first-pass rule permits
  leaving it. Worth a follow-up if the command verb and the hotkey mode are ever meant to track each
  other — but as written they are different identifiers that merely share a spelling.
- **R3 (scan-description imprecision, minor).** The leftovers table lists
  `injectors/clipboard.py:573` (`"1"`/`"9"`, a chained relational compare) and
  `whisper_engine.py:1154` (`"git"`, a call argument) as scan leftovers, but the operator set that
  yields the quoted 56 → 41 (Eq/NotEq/In/NotIn) does **not** count those two sites. They are still
  genuine leftovers and are correctly categorised; the count is unaffected (with all comparison
  operators the numbers become 58 → 43, not 56 → 41). Not a false claim about coverage, but the table
  is slightly over-inclusive relative to the stated scan.
- **R4 (inventory gap, minor).** The new local constant `socket_proto._LISTEN_SELECTOR_DATA = "listen"`
  is not in the inventory table (it is only registered, never compared, so it is outside the compared
  set — hence harmless).

None of R1–R4 block: no behaviour change, no rule violation, no dead code, no new dependency.

## Recommended follow-up tickets (optional)

1. If the CLI command verbs are meant to share semantics with the hotkey modes, give them one
   definition; otherwise record explicitly that the duplication is intentional (rule 2).
2. Extend the scan's operator set (or the table) so its "remaining N" and its listed leftovers are
   exactly 1:1; today the table names a couple of sites the scan's operator set excludes.

## Could not verify

- **The implementer's exact scan script** — not committed (correctly, per the ticket). I reproduced
  *an* equivalent scan with the documented positions and **the same 56 → 41 counts**, so the reported
  numbers are corroborated, but I cannot claim byte-for-byte identity with their script. This is a
  limitation of the evidence, not a reason for doubt: my independent numbers match.
- **Dynamic identifier lookups the static scan cannot see** — by construction no static scan can prove
  their absence. I mitigated with a raw-value `grep` over `src/` (every residual occurrence of the
  identifier values is message text, a docstring, a command verb, or a file/binary name), but a value
  assembled from parts at runtime would evade both.
- **The real whisper.cpp / needle binary seams** — not exercised here; this ticket does not touch those
  seams, so it is out of scope (rule 7 applies to seams that wrap subprocesses; none changed).
- Each "could not verify" reason above is itself true: the script is genuinely absent from
  `git ls-files`, the scan is genuinely static, and no seam code appears in the diff.

## What would falsify the central claim

- Any `identifiers.*` value differing from its `main` counterpart (checked: none), or
- any changed site whose raw literal differed from the constant it was replaced by (checked: branch
  introduced no new literal site and values are byte-identical), or
- a test failing on the branch that passes on `main` (none: 1230 passed on both).
