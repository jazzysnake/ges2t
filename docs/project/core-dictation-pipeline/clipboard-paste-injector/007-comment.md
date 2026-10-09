# 007 — Comment: the R5 correction and the rebase onto `main`

- **Ticket:** [ticket.md](ticket.md) (clipboard-paste-injector, #25)
- **Branch:** `feat/clipboard-paste-injector`
- **Worktree:** `.worktrees/clipboard-paste-injector`
- **Status:** in review
- **Reviewed HEAD (pre-rebase lineage):** `ed45570` (`006-review.md`)
- **Rebased HEAD:** `b1a110d`
- **Old merge-base:** `00adf75` (ticket 15 merge)
- **New rebase base:** `main` at `1610537` when the rebase ran; `main` has since
  advanced to `fe4f977` (orchestrator docs only). The branch was **not** rebased
  again on purpose — the orchestrator merges with `--no-ff`.

This comment closes the two integration items `006-review.md` left to the
orchestrator (§4 and §9): correcting the wrong restore-child mechanism in
`005-comment.md` as residual risk **R5**, and rebasing the branch onto the
advanced `main` while reconciling the two conflicts. It changes **no product
code**: the source is the same reconciliation that already passed `001`–`006`,
now replayed on top of `main`.

Nothing here (or in any command this comment records) writes the user's real
clipboard, runs `wl-copy`/`wl-paste`/`ydotool`/`ges2t run`, or emits a
keystroke. The R5 probe uses a harmless `tee` under `/tmp`; the backend proof
only *constructs* injector objects and never starts one.

---

## 1. Task 1 — the `005-comment.md` claim was wrong; R5 recorded

### What was wrong

`005-comment.md` said that on the failed-`Thread.start` path the already-spawned
restore child "is unreferenced and its stdin closes on collection, so a real
`wl-copy` would restore an empty selection" — i.e. a prompt EOF. `006-review.md`
§4 measured that this is false: a still-running `Popen` is appended to
`subprocess._active` by `Popen.__del__`, which keeps the object and its stdin
pipe open, so the child **blocks on stdin for the life of the ges2t process**.
It only sees EOF when the parent closes the fd (normally at process exit), and
only then would a real `wl-copy` write an empty selection.

### What I changed

Two sentences in `005-comment.md` were corrected to the real mechanism, and R5
was added in the same residual-risk style the other residuals use:

- the "What could NOT verify" bullet (lines ~164–171) now says a real child
  **blocks on stdin for the life of the parent** rather than being released on
  collection, and points at R5/`007-comment.md`;
- the "Anything a reviewer must know" bullet (lines ~193–204) now states the
  `subprocess._active` retention, the service-lifetime block, the leaked process,
  and the unchanged user-visible outcome; it also records explicitly that closing
  the child's stdin is **not** the fix (it would restore an empty selection) and
  that killing the child would be a separate change;
- R5 is listed under "Out of scope" next to R1/R3/R4.

No product code was touched for R5. Per the task, the leak was **documented, not
worked around**: closing the child's stdin would make the child read EOF and
restore an **empty** selection (worse for the user than keeping the transcript);
killing the child would avoid both the leak and the empty write but is a separate
change and was not attempted.

### R5, restated in the residual-risk style

- **R5 (measured in `006-review.md` §4, re-measured here): the restore child
  spawned before a failed `Thread.start` is retained via `subprocess._active`
  and blocks on stdin for the life of the service.** It is leaked, one per
  occurrence, until the parent closes the fd. The user-visible clipboard outcome
  is acceptable — the transcript stays on the clipboard, no corruption — and the
  leak is not newly introduced by the R2 fix (the child was already spawned on
  the pre-fix path; `service.py` also drops the escaping exception), but the
  disclosed timing in `005-comment.md` was wrong and is now corrected. A future
  one-line `proc.kill()` on the failure path (not `stdin.close()`) would remove
  both the leak and the long-lived pipe.

### The mechanism, re-measured here (no clipboard, `tee` under `/tmp`)

The child is `tee`, which — unlike the suite's `true` — reads stdin:

```
$ timeout 60 uv run python /tmp/ticket25_r5_probe.py
subprocess._active entries after del+gc: 1
child pid 3889131 alive after gc: True
child pid 3889131 alive after +1.5s: True
ticket25_r5_out size after +1.5s: 0
cleanup (probe only): SIGTERM + reap the leaked child
child pid 3889131 alive after TERM+reap: False
PROBE_EXIT=0
```

`subprocess._active` holds the dropped `Popen`, the child is still alive more
than 1.5 s later, and it has written nothing: it is blocked on `read()`, not
released on collection. `pgrep -af 'tee /tmp/ticket25_r5_out'` afterwards shows
only the `pgrep` shell itself — the probe cleaned up its own child. This matches
`006-review.md`'s longer (>2.5 s) probe; a real `wl-copy` behaves the same way,
since the pipe and `_active` mechanics are generic (the review notes it did not
observe a real `wl-copy` — neither did I).

---

## 2. Task 2 — the rebase and the two conflicts

`git rebase main` replayed all twelve ticket-25 commits. Two stopped on
conflicts, exactly as `006-review.md` §9 predicted:

```
$ git rebase main
Rebasing (1/12)...(3/12)
Auto-merging src/ges2t/pipeline.py
CONFLICT (content): Merge conflict in src/ges2t/pipeline.py
Auto-merging tests/test_pipeline.py
error: could not apply ea91053... ticket 25: build_injector backend selection ...
...
Rebasing (4/12)
Auto-merging docs/project/README.md
CONFLICT (content): Merge conflict in docs/project/README.md
error: could not apply fcdba62... ticket 25: 001-comment ...
...
Successfully rebased and updated refs/heads/feat/clipboard-paste-injector.
```

`tests/test_pipeline.py` auto-merged (see §2.3). No other file conflicted.

### 2.1 `src/ges2t/pipeline.py` — keep **both** sides

This is the real reconciliation. The two sides are:

- **`main`** grew the tray/notifications/streaming composition (`build_status_ui`,
  `_send_command_to_service`, `NotifyingStatusUI`, `StreamingDictationSession`,
  `_select_session_kind`, `_shutdown_status_ui`) and changed `_session_factory`
  to take `config` and choose batch vs streaming.
- **ticket 25** turned the single `build_injector` into a selection: it split the
  old body into `_build_ydotool_injector`, added `_build_clipboard_injector`, the
  `_INJECTOR_BUILDERS` mapping, and a new `build_injector` that dispatches on
  `config.injection.backend` and raises `PipelineError` for an unknown value.

Git aligned the branch's trailing `_session_factory` stub with main's
`_send_command_to_service`, so the conflict hunk mixed all three. I resolved it so
that **both** behaviours survive:

1. kept ticket 25's `_build_clipboard_injector`, `_INJECTOR_BUILDERS`, and the new
   dispatching `build_injector` (with its unknown-backend `PipelineError`);
2. kept main's `_send_command_to_service` unchanged after them;
3. **dropped the branch's stale `_session_factory` stub** — main's
   `config`-aware, streaming-selecting `_session_factory` (below the hunk) is the
   only one in the file.

The module docstring and imports had already auto-merged: the `build_injector`
bullet carries both the ticket-25 clipboard text and main's streaming bullet, and
the import block has both `ClipboardPasteInjector`/`ClipboardConfig` and
`NotifyingStatusUI`/`StreamingDictationSession`/`ges2t.ui.status`. `build_injector`
stays in `__all__`.

Proof of "both sides survive" (no `_session_factory` was reverted):

```
$ grep -n "^def \|^_INJECTOR_BUILDERS" src/ges2t/pipeline.py
...
235:def _build_clipboard_injector(config: Config) -> Injector:
259:_INJECTOR_BUILDERS: Mapping[str, Callable[[Config], Injector]] = {
265:def build_injector(config: Config) -> Injector:
289:def _send_command_to_service(command: str) -> CommandResponse:
298:def build_status_ui(config: Config) -> StatusUI:
...
403:def _session_factory(          # main's config-aware version, the only one
...
$ grep -n "_session_factory" src/ges2t/pipeline.py
32:  streaming session (ADR 0011). :func:`_session_factory` owns the one
403:def _session_factory(
437:        _session_factory(config, transcriber, audio_source),
```

### 2.2 `docs/project/README.md` — mechanical, never another ticket's row

The conflict was the two adjacent board lines 25 and 28: the branch had updated
row 25 to `in review` but still carried the **stale** row 28 (`to do`, `—`), while
`main` had row 28 `done` / `feat/injection-delay-default (merged)` and row 25 still
`to do`. Resolution, mechanically per the task:

- **row 25 (this ticket's own row):** `in review` / `feat/clipboard-paste-injector`
  (the branch's value);
- **row 28 (someone else's ticket):** `main`'s value, `done` /
  `feat/injection-delay-default (merged)`. The branch's copy of row 28 was stale by
  construction; taking it would have reverted a merged ticket.

All of `main`'s other rows (12, 15, 16, 17, 18, 28, 29, 30, 31, 32, 33, 34, and the
serialization table) are kept untouched. The net diff against `main` is exactly
one line:

```
$ git diff main -- docs/project/README.md
-| 25 | ...clipboard-paste-injector... | S | to do | 23, 24 | — |
+| 25 | ...clipboard-paste-injector... | S | in review | 23, 24 | feat/clipboard-paste-injector |
```

No other board row was changed anywhere in this task.

### 2.3 `tests/test_pipeline.py` — auto-merged

Git auto-merged it; the only line it replaced from `main` is the `ges2t.config`
import, which ticket 25 widened to include `InjectionConfig`. All of `main`'s
tests remain (36 `def test_` functions) and ticket 25's 13 `build_injector`
backend-selection tests are present.

### 2.4 No conflict markers survive

```
$ git grep -nE '^(<<<<<<<|=======|>>>>>>>)' HEAD -- src tests docs/project/README.md
NO_MARKERS
```

(The earlier grep hits elsewhere in `docs/` are prose in review artifacts that
quotes the markers, not real conflict markers.)

---

## 3. The old → new SHA mapping (review lineage stays traceable)

`001`–`006` cite pre-rebase SHAs. After the rebase the same commits are:

| Old SHA | New SHA | Commit |
|---|---|---|
| `bfc1a7d` | `5a9db62` | clipboard-paste injector module (wl-copy + raw keycode chord) |
| `6e69ed8` | `38559ec` | clipboard injector tests (chord, order, errors, config) + typed timeout/spawn errors |
| `ea91053` | `2fad575` | build_injector backend selection (ydotool\|clipboard), unknown raises |
| `fcdba62` | `0749059` | 001-comment (verification, falsification, user-owned manual script); status in review |
| `2b5fb26` | `5170cae` | 002-review (request changes: restore seam, letter keymap, paste-timeout typing) |
| `4ca89db` | `707ea27` | fix D1 restore stdin, D2 evdev letter table, D3 read timeout |
| `d2d65f2` | `54f066c` | 003-comment (D1/D2/D3 fixes, falsification, verification) |
| `1c93a35` | `6ed3bd2` | 004-review (pass: D1/D2/D3 fixes verified, no regressions) |
| `726358a` | `af717c4` | fix R2 (restore thread-start failure must not fail injection) |
| `fc556cc` | `bc4d72f` | 005-comment (R2 fix, falsification, unverified list) |
| `ed45570` | `2adb832` | 006-review (focused R2-delta re-review: pass; R5 claim correction) |
| `7ccf38b` | `b1a110d` | correct 005-comment restore-child mechanism (R5) — **new HEAD** |

Merge base `00adf75` → new base `main` `1610537` (later `fe4f977`). The exact
pairing was verified by subject equality (script asserts `old_subject ==
new_subject` for all twelve). Note `ea91053`'s subject contains a `|`, so the
table escapes it above. The three review commits that cite a *fix* commit — `005`
(`726358a`), `006` (`fc556cc`/`726358a`) — keep their meaning: `726358a` is now
`af717c4`, `fc556cc` is now `bc4d72f`.

---

## 4. Proof the reconciliation works

### 4.1 The backend selection still selects correctly (real command, no clipboard)

`build_injector` only constructs objects; it never spawns `wl-copy`/`ydotool`.
A real `uv run` against the rebound tree:

```
$ timeout 120 uv run python /tmp/ticket25_backend_probe.py
backend="clipboard" -> ClipboardPasteInjector paste_keys=['ctrl+shift+v']
backend="clipboard" (absent block) -> paste_keys=['ctrl+shift+v']
backend="ydotool"   -> YdotoolInjector
backend="clipbord"  -> PipelineError: injection backend 'clipbord' is not handled by this build; handled backends: 'clipboard', 'ydotool'
PROBE_OK
PROBE_EXIT=0
```

Ticket 25's behaviour survived the rebase: `clipboard` builds a
`ClipboardPasteInjector` carrying `[injection.clipboard] paste_keys`, `ydotool`
builds a `YdotoolInjector`, and an unknown backend raises the naming
`PipelineError` instead of silently defaulting.

The 13 in-suite selection tests (including the real-TOML loaders) also pass:

```
$ timeout 120 uv run pytest -q -p no:cacheprovider tests/test_pipeline.py -k "build_injector" -v
======================= 13 passed, 23 deselected in 0.32s =======================
EXIT=0
```

`main`'s behaviour survived too — the rebased tree still composes the tray,
streaming, and notification paths (see the `def` listing in §2.1), and the full
suite below exercises them.

### 4.2 Full suite (caches cleared; the process exits)

```
$ rm -rf .mypy_cache __pycache__ && find . -name __pycache__ -type d -prune -exec rm -rf {} +
$ timeout 300 uv run pytest -q
865 passed in 7.12s
PYTEST_EXIT=0
```

The process exits normally (no leaked-thread hang after the pass). The count is
`main`'s suite plus ticket 25's additions; on the pre-rebase merge-base the
branch alone was 687, which is consistent with `main` having added its own tests
in the interval.

### 4.3 Type check (caches cleared before trusting it)

```
$ rm -rf .mypy_cache __pycache__ && find . -name __pycache__ -type d -prune -exec rm -rf {} +
$ uv run mypy src/ges2t tests
Success: no issues found in 51 source files
MYPY_EXIT=0
```

ADR 0019 (type hints everywhere, Protocol-typed parameters, classes over
positional tuples) is unchanged: the reconciliation added no untyped code.

---

## 5. What I could not verify

- **Real `wl-copy`/`wl-paste`/`ydotool`/`ydotoold` behaviour and the manual
  end-to-end run.** Forbidden here (writes the user's clipboard, types into the
  focused window) and user-owned; `001-comment.md`'s checklist remains the gate.
- **R5 with a real `wl-copy`.** I confirmed the `subprocess._active` retention and
  the service-lifetime block with `tee` under `/tmp`; the pipe/`_active` mechanics
  are generic but I did not observe an actual blocked `wl-copy`.
- **Real thread exhaustion** (the `RuntimeError` is still injected by patching
  `Thread.start`) and the **real 0.4 s-vs-application-round-trip race** — both
  unobservable without a live Wayland session, as recorded in `005`/`006`.
- **Whether any Python version other than this CPython 3.14.7 changes the
  `subprocess._active` retention** (long-standing stdlib behaviour).
- **The rebase against the newest `main` tip (`fe4f977`).** The rebase ran against
  `1610537`; the orchestrator's later commits are `docs/` only and I was
  instructed not to rebase again. The orchestrator should confirm they touch
  nothing this branch changed.
- **The merge itself.** Not performed by design (`--no-ff` is the orchestrator's).

---

## 6. Anything the orchestrator must know before merging

- **Merge with `--no-ff`.** The branch is linear on `main@1610537` and clean; no
  product code differs from the reviewed `ed45570` tree apart from the
  reconciliation itself.
- **Ticket 25's board row ends as `in review`**; every other row is `main`'s. The
  only README diff against `main` is row 25.
- **R5 is a documented residual, not a regression:** one blocked, leaked restore
  child per failed-`Thread.start` occurrence, transcript preserved, no corruption.
  The cheap future fix is `proc.kill()` (not `stdin.close()`) on the failure path.
- **`main` moved on by one docs-only commit after the rebase**; merging
  `--no-ff` is expected to be conflict-free because it touches only `docs/`.
- The five hard rules held throughout: no real clipboard, no keystroke, no
  microphone, no merge to `main`, no worktree removal; this task committed after
  each step (`7ccf38b` → now `b1a110d` for the rebased history, plus this
  `007-comment.md` commit).
