# 002 — review: `paste_keys` default `ctrl+v` → `ctrl+shift+v`

- **Ticket:** [ticket.md](ticket.md)
- **Reviewed commit:** `1d6351e` (branch `feat/clipboard-paste-keys-default`), diff `main...HEAD`
- **Boundary under review:** a default value plus its prose plus its tests

## Verdict

**PASS on the branch's own Definition of Done.** The default is `ctrl+shift+v`
at every declaration *and* every statement of it in `src/`; the pinning test is
real (it asserts a literal) and is falsified by an actual revert; `pytest` and
`mypy` are green; no test was deleted or weakened; the "both chords" trap is
absent; and nothing but the default value, prose and tests changed.

**One merge-planning finding (not a code defect):** the merge into `main` is
**not** a single-line conflict. The dry-run 3-way merge reports **two**
conflicted files — `docs/project/README.md` (content) and this ticket's
`ticket.md` (`add/add`) — while `tests/test_clipboard_injector.py` **auto-merges
cleanly** (no conflict). Details in "Merge readiness" below. A merge of this
branch must be planned, not discovered.

## 1. Full `ctrl+v` accounting

`grep -rn 'ctrl+v' src/ --include='*.py'` — **12 hits, all classified, none a
statement that `ctrl+v` is the default.** Classification letters: **(a)** the new
default, **(b)** the deliberate caveat telling users to set `ctrl+v` when their
app leaves `ctrl+shift+v` unbound, **(c)** genuinely unrelated.

| # | Hit (branch line) | Text (abridged) | Class |
|---|---|---|---|
| 1 | `layout.py:499` | "a terminal, where ``ctrl+v`` does not" | b — caveat |
| 2 | `layout.py:501` | "set ``paste_keys = ["ctrl+v"]`` there instead" | b — caveat |
| 3 | `layout.py:572` | "pastes in terminals, where ``ctrl+v`` does not" | b — caveat |
| 4 | `layout.py:573` | "set ``paste_keys = ["ctrl+v"]`` there" | b — caveat |
| 5 | `clipboard.py:43` | "``KEY_V`` is 47, so ``ctrl+v`` is emitted exactly as" | c — keycode example (ticket flags it must-not-change) |
| 6 | `clipboard.py:55` | "``ctrl+v`` does not paste in most terminals" | b — caveat |
| 7 | `clipboard.py:58` | "``paste_keys = ["ctrl+v"]`` instead" | b — caveat |
| 8 | `clipboard.py:207` | `_KNOWN_NAMES_HINT`: "most terminals need 'ctrl+shift+v' rather than 'ctrl+v'" | c — chord-parser error hint; still true |
| 9 | `clipboard.py:545` | "a chord must end with … for example 'ctrl+v' or 'ctrl+shift+v'" | c — chord-must-end-with-a-key error example |
| 10 | `clipboard.py:563` | "… ``ctrl+r`` to ``ctrl+v`` (D2)" | c — historical arithmetic-mapping prose |
| 11 | `clipboard.py:588` | "set ``paste_keys = ["ctrl+v"]`` there" | b — caveat |
| 12 | `pipeline.py:240` | "``paste_keys = ["ctrl+v"]``" | b — caveat |

No hit is unclassifiable. Every hit is either the new caveat, an unrelated
parser/keycode example, or an error hint. **Nothing still describes `ctrl+v` as
the default** — including docstrings and error messages:

- `layout.py:502` `DEFAULT_PASTE_KEYS = ("ctrl+shift+v",)` — the declaration;
- `layout.py:569` `ClipboardConfig` docstring — "defaulting to ``("ctrl+shift+v",)``";
- `layout.py:670` empty-`paste_keys` error message — "the default is ``["ctrl+shift+v"]``";
- `clipboard.py:40` module docstring — "defaults to ``["ctrl+shift+v"]``";
- `clipboard.py:584` `ClipboardPasteInjector` docstring — "defaults to ``["ctrl+shift+v"]``";
- `clipboard.py:609` constructor default — `("ctrl+shift+v",)`;
- `pipeline.py:239` `_build_clipboard_injector` docstring — "default ``["ctrl+shift+v"]``".

The only `ctrl+v`-as-default statements that survive are in **historical
records** outside `src/` (`clipboard-paste-injector/008-comment.md`,
`…/003-comment.md`, `…/002-review.md`, `layout-detection-and-config/002-review.md`,
`orchestrator-state.md`). Those are point-in-time records, correctly left alone;
ticket 19 owns the user-facing docs, and no user-facing config reference exists
in the tree yet to go stale.

## 2. Falsification — per declaration

There are **two independent literal declaration sites**, plus a third field that
*derives* from the first:

1. `layout.py:502` `DEFAULT_PASTE_KEYS = ("ctrl+shift+v",)`
2. `layout.py:576` `ClipboardConfig.paste_keys = DEFAULT_PASTE_KEYS` (alias, not a literal)
3. `clipboard.py:609` `ClipboardPasteInjector.__init__ paste_keys = ("ctrl+shift+v",)`

Each was reverted **alone** to `ctrl+v`; the full suite was run each time.

| Mutation | Tests that fail |
|---|---|
| `DEFAULT_PASTE_KEYS` → `("ctrl+v",)` only | `test_clipboard_injector.py::test_default_chord_is_ctrl_shift_v`, `test_clipboard_injector.py::test_clipboard_config_defaults_when_the_block_is_absent`, `test_layout.py::test_clipboard_config_defaults_to_ctrl_shift_v`, `test_pipeline.py::test_build_injector_clipboard_defaults_when_the_block_is_absent` — **4 failed, 874 passed** |
| Injector constructor default → `("ctrl+v",)` only | `test_clipboard_injector.py::test_injector_defaults_to_ctrl_shift_v_without_paste_keys` — **1 failed, 877 passed** |
| `ClipboardConfig.paste_keys` set to a stale independent literal `("ctrl+v",)` (instead of the alias) | **0 failed — 878 passed** |

Conclusions:

- **Declaration 1 is caught** (4 tests). **Declaration 3 is caught** (exactly the
  new pinning test — that test is the sole guard on the injector's separate
  default, and it does its job).
- **Declaration 2 is *not* independently pinned.** A stale literal there changes
  no test outcome. This is **not a live divergence on this branch**: the field is
  `= DEFAULT_PASTE_KEYS`, `_read_paste_keys` also defaults from
  `DEFAULT_PASTE_KEYS`, and `ClipboardConfig.from_mapping` always constructs
  `cls(paste_keys=paste_keys)` explicitly. The field default is therefore
  unreachable on every tested path (no code calls a bare `ClipboardConfig()`), so
  as written it cannot ship a second default. It is a **latent gap**, not a
  present defect: if a future change turned that field into an independent
  literal, no test would notice. Worth a comment or a `ClipboardConfig()`
  default assertion someday, but it does not block this ticket.
- **The literal is asserted, not a tautology.** The new test is
  `assert injector.paste_keys == ["ctrl+shift+v"]` — a literal.
  `test_layout.py:618` / `test_clipboard_injector.py:914` assert
  `x == DEFAULT_PASTE_KEYS == ("ctrl+shift+v",)`, which chains a literal
  comparison (so a wrong `DEFAULT_PASTE_KEYS` fails the `== ("ctrl+shift+v",)`
  half). `test_default_chord_is_ctrl_shift_v` asserts parsed keycodes against the
  literal `(KEY_LEFTCTRL, KEY_LEFTSHIFT, KEY_V)` (i.e. `(29, 42, 47)`). None is
  an `X == X` check.

## 3. No behaviour change beyond the default

`git diff main...HEAD -- src/` changes only: comments/docstrings, the two default
literals, and one error-message **string**. Specifically untouched:

- **Chord parser** — `KeyChord.parse`, `_resolve_code`, `_LETTER_KEY_CODES`,
  `_MODIFIER_KEY_CODES` unchanged (only a docstring example at line 524 mentions
  `ctrl+shift+v`, which was already there).
- **Chord emission** — the press/release ordering and `ydotool key -d` argv are
  unchanged; the default's emitted keycodes are `29:1 42:1 47:1 47:0 42:0 29:0`,
  pinned by the updated `test_default_chord_is_ctrl_shift_v` and by
  `test_chord_is_emitted_once_per_configured_chord`'s second entry.
- **Error contract** — `ClipboardKeyChordError`/`InjectionConfigError` types and
  raise conditions are unchanged; only the *default value embedded in one message*
  (`layout.py:670`) changed, which the ticket explicitly lists as a statement of
  the default.
- **Restore behaviour** and the `ClipboardRunner` seam — untouched.

The diff is default value + prose + tests.

## 4. The "both chords" trap is absent

The shipped default is a **single** chord in both literal sites
(`("ctrl+shift+v",)`, one element). No `["ctrl+v", "ctrl+shift+v"]` default ships
anywhere in `src/`. The every-chord behaviour is real and still pinned:
`tests/test_clipboard_injector.py::test_chord_is_emitted_once_per_configured_chord`
passes `paste_keys=("ctrl+v", "ctrl+shift+v")` and asserts **two** `ydotool key`
calls — so a two-entry default really would paste twice. The caveat prose
explicitly warns against listing both.

## 5. No test weakened

`git diff main...HEAD -- tests/` contains only:

- two **renames** that assert the default (updated, not removed):
  `test_default_chord_is_ctrl_v` → `…_ctrl_shift_v`,
  `test_clipboard_config_defaults_to_ctrl_v` → `…_ctrl_shift_v`;
- three assertion **updates** (new literal / new keycodes);
- one **new** test (`test_injector_defaults_to_ctrl_shift_v_without_paste_keys`).

No test function was deleted, skipped, or renamed away, and no `skip`/`xfail`
was added. Collection counts confirm this: branch point `f1c072f` = **877**,
branch = **878** (exactly the one added test).

## 6. Mechanicals

```
$ timeout 300 uv run pytest -q
878 passed in 5.26s              # PYTEST_EXIT=0 (process exited; no 124)

$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 51 source files   # MYPY_EXIT=0
```

Caches were cleared before `mypy` so a reverted mutation could not look fixed.

## 7. Merge readiness (the finding that matters for the orchestrator)

`main` is at `e9a6840`; the merge-base is `f1c072f`. A dry run
(`git merge-tree --write-tree --name-only main HEAD`) reports:

| File | Result |
|---|---|
| `docs/project/README.md` | **CONFLICT (content)** |
| `docs/project/core-dictation-pipeline/clipboard-paste-keys-default/ticket.md` | **CONFLICT (add/add)** |
| `tests/test_clipboard_injector.py` | auto-merges cleanly (no conflict) |

So **README row 38 is *not* the only conflict.** Both README *and* `ticket.md`
conflict.

- **`README.md`** — the conflict is confined to the ticket-37/38 block (merged
  tree lines 58–64). `main` has row 37 = `done … merged 53d3c79` and its own row
  38 = `in progress`; the branch has row 37 = `in progress` and row 38 =
  `in review` with the extra caveat wording. Resolution: take **main's row 37**
  and the **branch's row 38** text (then flip 38 to `done` on merge per project
  convention). `main`'s other README edits since the merge-base (row 25's manual
  gate, row 34 = `in progress`, the row-21 serialization line) are in separate
  hunks and auto-merge; only the row-37/38 block conflicts.
- **`ticket.md`** — `add/add` because commit `361db64` on `main` already added
  the same ticket file; the two differ **only** in the Status line (`main`: "to
  do"; branch: "in review"). Trivial resolution.
- **`tests/test_clipboard_injector.py`** — does **not** conflict. Ticket 37's two
  new tests (added near line 657 on `main`) and ticket 38's changes (near lines
  854/980) are in disjoint regions; I confirmed the auto-merged blob contains
  **both** ticket 37's `test_real_immediate_runner_delivers_a_non_empty_payload_to_stdin`
  and ticket 38's `test_default_chord_is_ctrl_shift_v` /
  `test_injector_defaults_to_ctrl_shift_v_without_paste_keys`, with **0** conflict
  markers.

Expected collection after merge: `main` 879 + branch's 1 new test = **880** (the
merged README and ticket files are docs and add no tests).

## Could-not-verify

- **No real application was exercised.** This is a default change pinned by tests
  plus the user's earlier ticket-25 run; the branch pastes into nothing real. That
  is by design and consistent with the ticket.
- **How often `ctrl+shift+v` is unbound** is unknown ("one user, several
  applications" is not a survey). The caveat is documented; the residual risk is
  not measured.
- **`docs/project/orchestrator-state.md` is stale** about the shipped default (it
  still describes `ctrl+v` and the two-pass plan). Correctly left as a
  point-in-time record; ticket 19 owns user-facing docs.
- **The `ClipboardConfig` field default is unpinned** (mutation C above). Not a
  live divergence as written, but not independently guarded either.
- I did **not** exercise the actual merge; the conflict list is from
  `git merge-tree`, which is authoritative for textual conflicts.

## Safety / hygiene

- No clipboard write/read, no keystrokes, no microphone. All test runs used the
  fakes and `cat`/`sh`; the `wl-copy` and `ydotoold` processes in the process
  table predate this review and were not touched.
- Falsification mutations were applied to tracked files, reverted with
  `git checkout --`, and verified: `git status --porcelain` is empty and
  `src/` + `tests/` are byte-identical to `HEAD`.
- No probe files or temp worktrees left behind; no stray processes.

## Safe to merge?

**Yes, after a planned conflict resolution.** The code and tests are correct and
green; the only work is resolving two documentation conflicts (`README.md`
row-37/38 block and the `ticket.md` Status line) and confirming the resulting
suite is 880. There are no code blockers.
