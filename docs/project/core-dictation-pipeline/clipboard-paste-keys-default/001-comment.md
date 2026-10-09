# 001 — comment: the `paste_keys` default becomes `ctrl+shift+v`

- **Ticket:** [ticket.md](ticket.md)
- **Boundary:** default value only — no change to the chord parser, the delivery
  mechanism, the error contract, the restore behaviour, or how a chord is
  emitted.

## The change

`paste_keys` shipped as `["ctrl+v"]`. Ticket 25's manual verification
(`../clipboard-paste-injector/008-comment.md`) used `["ctrl+shift+v"]` and it
pasted in every target the user tried — nvim, and in their experience browsers
and regular editors — so one configuration covered both DoD targets, an editor
*and* a terminal. `ctrl+v`, by contrast, does not paste in most terminals, which
the epic's exit test names explicitly. The default becomes `["ctrl+shift+v"]`.

Every **declaration** of the default and every **statement** of it moved
together:

| Where | Before | After |
|---|---|---|
| `src/ges2t/injectors/layout.py` — `DEFAULT_PASTE_KEYS` (the declaration) | `("ctrl+v",)` | `("ctrl+shift+v",)` |
| `src/ges2t/injectors/layout.py` — `ClipboardConfig` docstring | `("ctrl+v",)` | `("ctrl+shift+v",)` |
| `src/ges2t/injectors/layout.py` — empty-`paste_keys` error message | `(the default is ["ctrl+v"])` | `(the default is ["ctrl+shift+v"])` |
| `src/ges2t/injectors/clipboard.py` — module docstring | `defaults to ["ctrl+v"]` | `defaults to ["ctrl+shift+v"]` |
| `src/ges2t/injectors/clipboard.py` — `ClipboardPasteInjector` docstring | `defaults to ["ctrl+v"]` | `defaults to ["ctrl+shift+v"]` |
| `src/ges2t/injectors/clipboard.py` — constructor default | `paste_keys=("ctrl+v",)` | `paste_keys=("ctrl+shift+v",)` |
| `src/ges2t/pipeline.py` — `_build_clipboard_injector` docstring | `default ["ctrl+v"]` | `default ["ctrl+shift+v"]` |

The injector still declares its own default **separately** from
`DEFAULT_PASTE_KEYS` (that is pre-existing structure). The new pinning test
asserts the injector's literal so the two declarations cannot diverge silently.

`["ctrl+v", "ctrl+shift+v"]` was deliberately **not** used as a fallback: the
injector emits **every** configured chord, so a two-entry list would paste twice.
`tests/test_clipboard_injector.py::test_chord_is_emitted_once_per_configured_chord`
already pins that every-chord behaviour.

### The caveat, carried with the default

The docstrings state it in the words `008-comment.md` uses: `ctrl+shift+v` is a
**different binding, not a superset**. An application that does not bind it
pastes **nothing**, so set `paste_keys = ["ctrl+v"]` instead. In most GUI
toolkits `ctrl+shift+v` means *paste as plain text*, which for dictation is
desirable rather than a surprise — no rich formatting is carried in.

## `grep` accounting for every `ctrl+v` hit

Command run before and after (source tree):

```
grep -rn 'ctrl+v' src/ges2t/
```

### Changed — these stated the default

| Original hit | Disposition |
|---|---|
| `layout.py:495` `DEFAULT_PASTE_KEYS = ("ctrl+v",)` | the declaration → `("ctrl+shift+v",)` |
| `layout.py:562` docstring `` ``("ctrl+v",)`` `` | statement of the default → `ctrl+shift+v` |
| `layout.py:660` `(the default is ["ctrl+v"])` | statement of the default → `["ctrl+shift+v"]` |
| `clipboard.py:40` `defaults to ``["ctrl+v"]``` | statement of the default → `["ctrl+shift+v"]` |
| `clipboard.py:571` `defaults to ``["ctrl+v"]``` | statement of the default → `["ctrl+shift+v"]` |
| `clipboard.py:590` `paste_keys=("ctrl+v",)` | the constructor default → `("ctrl+shift+v",)` |
| `pipeline.py:238` `(default ``["ctrl+v"]``; …)` | statement of the default → `["ctrl+shift+v"]` |

### Deliberately left — unrelated to the default

| Hit | Why it must not change |
|---|---|
| `clipboard.py:43` `` ``KEY_V`` is 47, so ``ctrl+v`` is emitted exactly as `` | the **letter-keycode example** the ticket flags as must-not-change. The surrounding paragraph now also shows the default's emission (`29:1 42:1 47:1 47:0 42:0 29:0`), but the `ctrl+v` example itself is untouched. |
| `clipboard.py:194` `'ctrl+shift+v' rather than 'ctrl+v'` | `_KNOWN_NAMES_HINT`, **error-message prose about the chord parser**. Still true. |
| `clipboard.py:532` `'ctrl+v' or 'ctrl+shift+v'` | chord-must-end-with-a-key error example. |
| `clipboard.py:550` `` ``ctrl+c`` and ``ctrl+r`` to ``ctrl+v`` (D2)`` | D2 prose about the old arithmetic letter mapping. A historical fact, not a default. |

New `ctrl+v` occurrences introduced by this ticket are the **caveat itself**
(`paste_keys = ["ctrl+v"]` in `layout.py`, `clipboard.py`, `pipeline.py`).

### Out of `src/ges2t/` — left on purpose

- **Tests that do not assert the default** keep `ctrl+v` as an explicit fixture:
  `tests/test_layout.py` (the generic-layer TOML fixture, the unknown-key test,
  the invalid-value parametrize), `tests/test_clipboard_injector.py` (the
  `_injector` helper default, the explicit-chord tests, the D2 typo test, the
  keycode test), `tests/test_doctor.py`, `tests/test_pipeline.py` (the
  `past_keys` typo fixture). The `_injector` helper's `("ctrl+v",)` default is a
  test fixture, not the product default; changing it would only churn the
  explicit-chord tests.
- **Tests that did assert the default were updated, not deleted** (see below).
- **Historical docs** (`docs/project/orchestrator-state.md`,
  `*/001-comment.md`, `*/002-review.md`, the ticket files) are point-in-time
  records. `orchestrator-state.md` still describes the pre-change default and
  the two-pass plan; editing a state snapshot is out of this ticket's scope.
  Ticket 19 owns the user-facing docs.

## Tests

Updated (old default → new default):

- `tests/test_layout.py::test_clipboard_config_defaults_to_ctrl_shift_v`
- `tests/test_clipboard_injector.py::test_clipboard_config_defaults_when_the_block_is_absent`
- `tests/test_clipboard_injector.py::test_default_chord_is_ctrl_shift_v`
  (keycodes are now `(29, 42, 47)`)
- `tests/test_pipeline.py::test_build_injector_clipboard_defaults_when_the_block_is_absent`

Added (pins the injector's own default):

```python
def test_injector_defaults_to_ctrl_shift_v_without_paste_keys() -> None:
    injector = ClipboardPasteInjector(runner=FakeClipboardRunner())
    assert injector.paste_keys == ["ctrl+shift+v"]
```

### Falsification

Reverted **only** the two source declarations to the old value
(`git checkout f84e993~1 -- src/ges2t/injectors/layout.py src/ges2t/injectors/clipboard.py`),
leaving the tests in place:

```
$ uv run pytest -q \
    tests/test_clipboard_injector.py::test_injector_defaults_to_ctrl_shift_v_without_paste_keys \
    tests/test_clipboard_injector.py::test_clipboard_config_defaults_when_the_block_is_absent \
    tests/test_clipboard_injector.py::test_default_chord_is_ctrl_shift_v \
    tests/test_layout.py::test_clipboard_config_defaults_to_ctrl_shift_v \
    tests/test_pipeline.py::test_build_injector_clipboard_defaults_when_the_block_is_absent
```

```
FAILED tests/test_clipboard_injector.py::test_injector_defaults_to_ctrl_shift_v_without_paste_keys
FAILED tests/test_clipboard_injector.py::test_clipboard_config_defaults_when_the_block_is_absent
FAILED tests/test_clipboard_injector.py::test_default_chord_is_ctrl_shift_v
FAILED tests/test_layout.py::test_clipboard_config_defaults_to_ctrl_shift_v
FAILED tests/test_pipeline.py::test_build_injector_clipboard_defaults_when_the_block_is_absent
5 failed in 0.30s
```

The new test's own failure:

```
    injector = ClipboardPasteInjector(runner=FakeClipboardRunner())
>   assert injector.paste_keys == ["ctrl+shift+v"]
E   AssertionError: assert ['ctrl+v'] == ['ctrl+shift+v']
E     At index 0 diff: 'ctrl+v' != 'ctrl+shift+v'
tests/test_clipboard_injector.py:925: AssertionError
```

Restored the source (`git checkout f84e993 -- …`) and the same five tests pass:

```
.....                                                                    [100%]
5 passed in 0.17s
```

## Verification

```
$ uv run pytest -q
878 passed in 4.04s          # exit status 0

$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 51 source files   # exit status 0
```

The ticket's checklist said "879", but this branch's baseline at `f1c072f` is
**877** collected tests (`877 + 1 new = 878`; the collection count was checked by
running `--collect-only` against the pre-change tree). No test was deleted.

## What could not be verified

- **No real application was exercised.** This is a default change pinned by
  tests and by the user's earlier manual run of ticket 25; nothing here pastes
  into a real editor or terminal. That evidence already exists in
  `008-comment.md` and is the *reason* for the change, not a result of it.
- **`ctrl+shift+v` is still not universal** — the caveat is documented, but the
  frequency of applications that leave it unbound is unknown ("one user, several
  applications" is not a survey).
- **`docs/project/orchestrator-state.md` is now stale** about the shipped
  default. Left as a historical record; ticket 19 owns user-facing docs.
