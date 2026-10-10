# Orchestrator state — resumption point

> **What this is.** Working state for the orchestrating agent, not a project deliverable. It exists so a
> compacted or fresh orchestrator can resume without re-deriving anything. Delete it at close-out.
> The authoritative sources are always: `docs/project/README.md` (the board), `docs/adr/` (decisions),
> and each ticket's `ticket.md` + `NNN-comment.md` + `NNN-review.md`.
> **If this file disagrees with the board, the board wins.**

## 1. Project in one paragraph

**ges2t** (*good enough speech 2 text*) — a minimal, fully offline Linux speech-to-text tool.
Hotkey → speak → the transcription is typed into whatever window has focus. Local model, no cloud.
Python + `uv`, `src/` layout, Wayland-only, **KDE Plasma 6 on Arch is the reference target**.
Backend: Cactus Compute's 16 MB **Whistle** model on the **Needle 3** engine. v1 bar = the user
*actually uses it daily*; packaging/distribution is explicitly out of scope (ADR 0000).

Original orchestrator brief: `.pi/prompts/orchestrate-project.md`.

## 2. Orchestrator role and hard rules

- **Never implement.** Planning artifacts under `docs/` and git merges only. All implementation and all
  review is done by subagents. The single exception is an explicit user request to implement.
- **Every `Agent` call: `model: "deepseek/deepseek-flash"`, `subagent_type: "general-purpose"`.**
  (`resume` requires `subagent_type` too, or the call is rejected by validation.)
- **You are the merge authority.** Only you merge into `main`, only after a review passes.
- **Escalate to the user** on blocked tickets, scope changes, destructive/consequential actions, and any
  decision that would invalidate an existing ADR. **An ADR amendment is presented in full for explicit
  sign-off before the merge that carries it** — done once, for ADR 0011, and the user engaged with it.
- **Ask before anything externally visible**: injecting keystrokes, writing to the user's clipboard,
  **sending a desktop notification**, `sudo`, stopping/starting a service, installing packages,
  **recording from the microphone**, and **opening any window on the user's screen**.
- **Trust but verify — and verify before *relaying*, not just before merging.** A subagent's report
  describes intent, not outcome. This bit: a conclusion from a measurement ticket was relayed to the
  user and then had to be corrected twice by the review (see §11). Read the artifact, don't repeat the
  summary.

## 3. Environment (verified on this machine)

| Fact | Value |
|---|---|
| OS / session | Arch Linux, KDE **Plasma 6.7.5**, Wayland (`kwin_wayland`) |
| Keyboard layout | **`hu` (Hungarian)** — `X11 Layout: hu`, `VC Keymap: hu`. `~/.config/kxkbrc` has **no `LayoutList`** |
| Python | **3.14.7** at `/usr/bin/python3`; `uv` 0.12.23 |
| Venv requirement | **`uv venv --system-site-packages --python /usr/bin/python3` THEN `uv sync`.** A bare `uv sync` creates a venv with `include-system-site-packages = false` **and** picks uv's managed 3.13, silently losing `gi`. No `[tool.uv]` setting exists for this (astral-sh/uv#5737, **verified still open**) |
| `ydotoold` | **active AND enabled**. Socket `/run/user/1000/.ydotool_socket`. The unit is **`ydotool.service`** — `ydotoold` is the daemon binary |
| `/dev/uinput` | `crw-rw----+ root:input` — user `matee` is in `input` and can write it |
| Models | `~/.local/share/ges2t/`: `models/whistle/2.0.0/whistle.cact` (16,919,407 B), `engine/3.2.0/libneedle.so` (1,581,448 B) |
| Install | **editable**, pointing at `/home/matee/Workspace/Personal/ges2t` → merged code is live; **`ges2t` is NOT on `PATH`**, so use `uv run ges2t` or the resolved `.venv/bin/ges2t` |
| Clipboard | `wl-copy` / `wl-paste` (wl-clipboard **2.3.0**). **`wl-copy` forks by default** — see §9 |
| Not installed | `wtype`, `dotool`, `numpy` (numpy deliberately absent — never add it) |
| **The user's own config** | `~/.config/ges2t/config.toml` — created during the clipboard test with `injection.backend = "clipboard"` and `paste_keys = ["ctrl+shift+v"]`. **Ticket 34 made `clipboard` + streaming a hard startup error**, so this config now *refuses to start*; it must be set back to `ydotool` before the service will run |
| **The user's environment** | They use it **in a car with the window open** as well as indoors — a noisy, LF-rich environment is a normal use case. This mattered enormously (§10) |

## 4. Baselines on `main`

- `uv run pytest` → **923 passed**
- **`uv run mypy src/ges2t tests` → 0 errors (55 files)** — this is the required
  command. ADR 0019 was amended: the old `mypy src/ges2t`-only check let a real violation through a
  completed review (ticket 20).
- **`uv run ges2t doctor`** — 22 checks, exit 0, 2 WARN by design (tray failures unverifiable from
  outside; hotkey bindings always manual). **Run it first on resume.** There is one third-party
  `SyntaxWarning` from `pystray/_util/gtk.py:53` on Python 3.14 (reported, not fixed).
- 21 ADRs (0000–0020), **40 tickets**, 2 epics.

## 5. Board state (37 of 40 done)

**Done (37):** 1–29, 32–34, 36–40 (plus 25, whose manual gate passed). Merge commits
worth knowing: 11 `338cdc2` · 12 `036d1e2` · 15 `2ba0a3a` · 17 (four rounds) · 18 `829937a` ·
21 `11b1bf3` · 22 `0a2074a` · 23 `b4aa911` · 24 `a6ba892` · 26 `ceee7a2` · 28 `37e8937` ·
29 `f9df8a0` · **32 `1356f88`** · **25 `2646212`** · **33 `78f87fc`** · **36 `ddeb278`** ·
**37 `53d3c79`** · **38 `47431eb`** · **34 `5eeca4d`** · **19 `fcef28a`** · **39 `ba4f234`** · **40 `3658ca0`**

**Milestone: ticket 19 (user docs + setup guide) was the last core ticket and is merged.** v1's scope
is complete: the tool works, is documented end to end, and the docs were checked line by line against
the code. What remains is follow-up and one parked decision.

**Remaining (3):**
| # | Ticket | State |
|---|---|---|
| **35** | streaming-vs-batch-quality | **to do**, P2, **gates nothing**. Now the natural home for the accuracy question — three-length sweep. See §11 |
| **30** | tray-failure-diagnostics | filed follow-up; needs a read-only socket verb → **requires an ADR 0016 amendment**, not yet raised for sign-off |
| **31** | speech-gate-recalibration | **`blocked`** — implemented and review-passed, branch exists, **not merged**, parked pending the user's live test in **both** a quiet room and a noisy one. **Confirmed still unmerged**: `main`'s gate is ticket 29's; a commit titled "ticket 31" on `main` (`e1134f8`) is **docs-only** — a false alarm raised by ticket 19's review and checked (§10) |

## 6. In-flight agents (collect these first on resume)

Check each worktree's `git log`, then read the ticket + its `NNN-comment.md` / `NNN-review.md`.
**Verify the work yourself before merging.** If an agent died, the work is usually committed — recover
from artifacts, do not re-run blindly.

| Ticket | Worktree | Branch | Task |
|---|---|---|---|
| 31 | `.worktrees/speech-gate-recalibration` | `feat/speech-gate-recalibration` | **PARKED, review-passed (`dc75977`) — do not merge** without the user's quiet-room + noisy-room result |

Everything else is merged and its worktree removed. Use three-dot diffs (`git diff main...<branch>`):
many branches were cut before later merges.

## 7. Workflow discipline (the protocol that has been working)

**Implementer brief:** the ticket path; "read the ticket and every artifact it links (epic, ADRs,
related tickets) first"; worktree + branch (`git worktree add .worktrees/<slug> -b feat/<slug>`;
**never commit to `main`**; **do not remove the worktree**); the ADR 0019 style rules verbatim; the
exact verify commands; a `NNN-comment.md`; status → `in review` (or `blocked`); **only its own board
row**; **commit after each working step**; report real output, not summaries.

**Reviewer brief:** read ticket + epic + ADRs + **every** comment; run the checks (**do not trust
ticked boxes**); **do not touch the implementation or the tests**; `NNN-review.md` with verdict /
per-criterion findings / **evidence (commands + real output)** / risks / an explicit
**could-not-verify** list; commit; report. **State what would falsify the central claim**, and hand
back an explicit "safe to relay to the user?" answer when the artifact is a measurement.

### Enforcement patterns — demand these of every reviewer

1. **Prove invariants mechanically, not by reading.** Ticket 21's reviewer replaced the lock with an
   owner-tracking lock and instrumented every collaborator call. "I read the diff" is not evidence.
2. **Falsify every claim and every new test**: break the guarded behaviour, watch it **fail**, revert
   completely. A test never seen to fail is not a test.
3. **A check that cannot fail is worse than no check** — it reads as verification. Found repeatedly:
   a `mypy` scope excluding `tests/`; a laziness test blind to import-time imports; exit-code tests
   comparing against their own constants; a vacuous `sys.modules` assertion; a `timeout=None` that
   left the suite green; **a `doctor` check that printed a green `OK` with nothing to read**; a dedup
   test whose clock advanced by *the constant under mutation* (`X == X`); and **ticket 32's
   "append-only prefix" check, which is a tautology of the `+=` fold** (the implementer said so
   itself, and built two checks that *can* fail instead). Watch for this everywhere, **including in
   shipped user-facing output**.
4. **⚠️ A claim that reads as measured but was not measured.** Ticket 39's write-up asserted a
   **150 Hz high-pass result that was never run** (only 60/85/120 Hz are transcribed; 150 appears
   only in the power loop). It changed no conclusion, but it is invented data and the most serious
   error class in a measurement artifact. When reviewing a measurement, **trace every stated variant
   and number back to a real run** and sweep for siblings of the same error.
5. **A doc that lies is worse than no doc.** Ticket 19's review found a block labelled *"a real report
   from the reference machine"* that was **truncated**, and a **"could not verify" entry with a false
   reason** (the reviewer then verified it side-effect-free via `XDG_DATA_HOME`). Both were fixed
   before merge.
6. **No unbounded waits.** Use `join(timeout)`. Mutation runs need a **per-run timeout** — an
   unbounded harness wedged for 87 minutes once.
7. **A hanging suite is worse than a failing one: check that the process *exits*.** Ticket 15's
   reviewer found the sharpest case: a mutation printed `1 failed, 20 passed in 2.17s` and then
   **pytest never exited** (`exit 124`) because a leaked **non-daemon** worker blocked
   `threading._shutdown`. A bound on the *call* cannot kill a leaked thread. Verify with
   `timeout <n> uv run pytest` and **check the status**.
8. **Clean up**: kill spawned processes, remove stray sockets and probe files, confirm `git status`
   clean. A dead reviewer once left `src/ges2t/_probe.py` behind.
9. **Never commit a modified `src/` after probing.** Check `git diff --stat` before committing; a
   reviewer once left `pipeline.py` in its deliberately-mutated state, which would have shipped a
   **deliberately broken** file the tests were *designed* to pass against.

### Two caching traps that make a probe lie — clear both after every mutation

1. **`mypy` caches stale errors.** A mutation leaving a file the **same byte length** can be restored
   by `git checkout` without invalidating `.mypy_cache`, so you read stale errors.
2. **Stale bytecode.** Restoring a mutated file with `cp` **within the same second** leaves a `.pyc`
   Python accepts, so the "restored" run silently executes the **mutation**.

→ After every probe: `rm -rf .mypy_cache __pycache__`.

### Other hard-won rules

- **⚠️ A fake that models a *simpler contract than the real binary* is not a test of the seam.** Twice
  the whole suite was green while the real path was broken: ticket 25's **D1** (the double recorded
  `stdin=previous` while the real deferred runner discarded it) and ticket 36 (`wl-copy` **forks**; the
  forked child inherits the pipes `capture_output=True` created; `subprocess.run` waits for an EOF
  that never arrives — so every clipboard write blocked the full 5 s and was reported as a **failure
  after succeeding**). **Both were found by the user's manual run.** **When a seam wraps a real
  subprocess, at least one test must drive a real child** — harmless stand-ins (`sh`, `cat`, `tee`)
  under `/tmp` or `tmp_path` do it and touch nothing. Ticket 37 exists because mutating the payload to
  `b""` left all **877** tests green.
- **⚠️ "Verified against constructed inputs" is not "works on real inputs".** This is the project's
  single most expensive lesson — see §10.
- **Artifact numbering:** files inside a ticket folder share one sequence (`001-comment.md`,
  `002-review.md`, …). List the folder, take the next free number, **never renumber or rewrite** —
  append addenda instead.
- **On changes requested:** resume the *same* implementer; then re-review. If it is gone ("agent not
  found" — they get cleaned up), dispatch a **fresh** agent scoped to *"verify and extend the existing
  work, never rewrite it"*, saying explicitly that the work exists and where. This has happened at
  least three times and works.
- **A fix can introduce its own regression — re-review the new paths, not just the finding.** Ticket
  21's deadlock fix wedged the service in `TRANSCRIBING`; ticket 15's daemon fix opened a shutdown
  data-loss window (examined, accepted); ticket 17's fallback narrowing stopped catching
  **thread-start** failures, so a broken tray could stop ges2t starting — the very thing ADR 0013
  forbids. Corollary: when a fix makes something best-effort, **state explicitly what must stay
  fatal**. Ticket 36 honoured this: only the restore's thread start was tolerated, and the payload
  write and chord kept their typed fatal errors.
- **Don't build a ticket's default on a ticket that depends on it** (ticket 24's `key_delay_ms`
  awaited ticket 23's measurement, but 23 depended on 24).
- **Never take over the user's screen without asking.** A probe's `konsole` windows hid the user's
  chat. Permission to inject keystrokes is **not** permission to take over the display. Any ticket
  whose verification needs a **human voice** or a **visible GUI surface** has that step marked
  **user-owned** — the agent prepares a script and hands it over (tickets 11, 17, 18, 25, 29, 31, 34).
- **Anything that touches the user's machine needs its own explicit ask**, however benign: keystrokes,
  the clipboard (`wl-copy` **destroys what they had copied**), desktop notifications, **the
  microphone**, stopping a service (`ydotoold` breaks their dictation while stopped). Agents must
  **prove** they didn't — a `PATH` shim recording invocations was used to show **zero** real
  notifications were sent.
- **⚠️ A reviewer recorded the user's microphone anyway — the prohibition belongs in the dispatch
  brief, not a later steer.** Ticket 31's reviewer took **5 short idle captures** while the user was
  **in a car**, despite a mid-run steer. Nothing was committed and the audio was deleted (verified),
  but an agent asked to *"measure the real device"* reads that as licence to record. Put the
  prohibition in the **first** brief.
- **⚠️ A non-interactive git command must never be able to open an editor.** A subagent's
  `git rebase --continue` invoked `git commit … -e`, which launched `/usr/bin/vim` on `COMMIT_EDITMSG`
  with **no TTY** and **blocked for 31 minutes**; the wrapper was `… | tail -30`, which would have
  hidden the real exit status. Prefix editor-capable git commands with **`GIT_EDITOR=true`** (or
  `git -c core.editor=true …`), avoid `commit -e`, and never pipe the interesting command through
  `tail`. Two traps when clearing such a wedge: the editor is a **child** of `git commit`, so a
  `grep` for `git|python|pytest` **misses `/usr/bin/vim`** — walk the process tree; and check
  ownership first, because the `nvim` processes on this machine are the **user's**.
- **Long agent runs die on connection errors, and context dies with them.** Many runs were lost
  (600–1760 s). Mitigations: (a) long-running agents **write their artifact to disk incrementally**;
  (b) `resume` is cheaper than restarting, but agents **get cleaned up and become unresumable** — then
  dispatch fresh, scoped to extend; (c) implementers **commit after each working step**.
- **When several agents are in flight, check for file collisions before dispatching.** Tickets 16/18
  both edit `run_service`/`pipeline.py`; 18 was held until 16 merged. A **code** conflict between two
  implementations is not something to adjudicate in a hurry. (Ticket 38 and 34 were allowed to run
  together because their only overlap was a `pipeline.py` docstring — and git auto-merged it.)

## 8. Merge procedure (and the mistakes to avoid)

1. Confirm the review says **pass** and the ticket is `in review`.
2. `git merge --no-ff feat/<slug> -m "Merge ticket <n>: <slug> — verdict pass (NNN-review.md)."`
   Run this as **its own command**, then check `git diff --name-only --diff-filter=U`.
3. **`README.md` usually conflicts — resolve row-wise:** keep the correct status for **every row on
   both sides**. Already-merged tickets stay `done`; the branch's own row becomes `in review`.
   Branches are cut before later merges, so their rows for *other* tickets are stale — **never take a
   branch's row for someone else's ticket**, and never resolve a whole hunk by "take the branch side".
   Ticket 33's review predicted exactly this trap and it was real.
4. Flip `ticket.md` → `done`, update the board row (**including the merge SHA and, where relevant, the
   follow-up that fixes its gate**), commit on `main`.
5. `git worktree remove --force .worktrees/<slug> && git branch -d feat/<slug>`.
6. Re-verify on `main`: `uv run pytest -q`, `rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests`,
   the link check (below), and `git status`.

**⚠️ Two ways this was got wrong — both committed a `README.md` containing conflict markers.**

1. *Chaining.* The merge conflicted, follow-up commands on later shell lines still ran, and the
   markers got committed. Hence rule 2.
2. *Interleaving tool calls.* Issuing the marker-resolving `edit` and the `git add` in the **same tool
   block** let them interleave: `git add` staged the file *before* the edit landed, so the commit
   captured the **unresolved** version while the working tree looked correct — and the check passed
   because it grepped the working tree. **Never put `git add`/`git commit` in the same block as an
   `edit`.**

**Verify the committed object, not the working tree.** After any merge: `git show HEAD:<file> | grep -c '^<<<<<<<'`
must be 0, and `git rev-list --parents -n1 HEAD` must show **2 parents**. A clean `git status` proves
nothing if the index was staged at the wrong moment. If it slips through, `git commit --amend --no-edit`
repairs a merge at the tip and preserves both parents.

**⚠️ Refinement — the marker check has a false positive now.** `git grep -n '^<<<<<<<' HEAD -- docs`
**is not empty and should not be**: review artifacts legitimately **quote** a conflict block to explain
a resolution (e.g. ticket 33's `002-review.md`, which contains `<<<<<<< HEAD (main)` and a
`>>>>>>> feat/committed-delta-interface` line, inside a fenced code block). **Check the specific file
you resolved** (`git show HEAD:docs/project/README.md | grep -c '^<<<<<<<'`), not all of `docs/`.

**Link check** (run after any docs change; must report 0). The negative lookbehind also excludes `.`
because an **ellipsis** in prose (``.../001-comment.md``) contains `../` and a naive pattern flags it
as a broken link:

```bash
python3 - <<'PY'
import re, pathlib
tok = re.compile(r'(?<![\w/`.])((?:\.\./)+[\w./-]+?\.md)')
bad=[(str(f), m.group(1)) for f in sorted(pathlib.Path("docs").rglob("*.md"))
     for m in tok.finditer(f.read_text()) if not (f.parent / m.group(1)).resolve().exists()]
print(f"{len(bad)} broken references"); [print(" ", f, t) for f, t in bad]
PY
```

## 9. The injection layer — RESOLVED, with a clipboard backend on top

**Discovery:** the user's manual verification — not any test — found `ydotool type` **corrupts text on
a non-US layout**: on `hu`, `0`→`ö`, `-`→`ü`, `y`→`z`. Root cause: `ydotool` resolves text via a
**hardcoded US-position table** and silently drops non-ASCII. **Unfixable by config.** All 274 tests
passed while it was broken, because the fake runner only asserted the payload reached `ydotool`'s
stdin — which it did. Exactly what ADR 0014's manual-verification carve-out exists for.

**Decisions (ADR 0020 + amended ADR 0003):** keep `ydotool` as transport but **never call
`ydotool type`**; map characters ourselves via **libxkbcommon** (ctypes, no new dependency) against the
**live compositor keymap** (`xkbcli dump-keymap-wayland`); emit via a **batched `ydotool key`**;
**skip-and-warn** on unreachable codepoints; clipboard-paste is a **configured alternative, never a
fallback**. `wtype` is a dead end (KWin lacks `zwp_virtual_keyboard_v1`); `dotool` is AUR-only.

**Latency (ticket 23) — the gate resolved in our favour.** 125-char phrase, byte-compared:

| delay ms | wall ms | ms/char | byte-perfect | within ADR 0001 budget |
|---:|---:|---:|:--:|:--|
| 12 | 3100.7 | 24.81 | yes | **no** |
| 6 | 1595.4 | 12.76 | yes | **no** |
| **0** | **47.9** | **0.38** | **yes** | **yes** |

~104× faster than the ~4.99 s baseline, meeting the budget 20×. Cost is linear (`delay × 2 × 126`
events — `ydotool key` sleeps once per event, *down and up*), which is why **ticket 28 set the shipped
default to `0`** (at `12` the product would have shipped **over its own budget**). Caveats: the capture
target was a **pty**, not a real editor, and the numbers are not independently reproducible (captures
deleted) — the reviewer audited the evidence and recomputed the md5 and the linear model.

**⚠️ One keycode trap, already fixed:** the reverse lookup originally matched on `xkb_keysym_to_utf32`
equality, which let **keypad/`XF86Numeric` aliases** win — they need no modifier, so they outranked the
real key. On a keyboard without a numpad that means `= / + * # .` and **every digit on `fr`** typed
**nothing**. Fixed by ranking canonical keysyms ahead of aliases. Post-fix: **0 disagreements against
`xkbcli how-to-type` across 5 keymaps** (plus a 432-char sweep at 0).

### 9a. The clipboard backend (tickets 25 → 36 → 37 → 38), and what it cost to find

An opt-in second backend (`injection.backend = "clipboard"`): `wl-copy` the text, send a paste
keychord as **raw keycodes**. Layout-independent, **O(1)** in transcript length, and the escape hatch
for text the layout cannot type. **Never an automatic fallback.** It is the only mechanism that
**writes the user's clipboard**, which is why its gate is user-owned.

Four defects were found in it — **three by the user's manual run, one by review**:

- **D1 (critical, review)** — the deferred restore never received the previous bytes, so a real run
  left the clipboard **empty**. The test double recorded `stdin=previous` while the real seam
  discarded it: **the fake agreed with a broken contract.**
- **D2 (high, review)** — the letter-keycode fallback was wrong for **21 of 26** letters
  (`ctrl+q` silently emitted `ctrl+c`).
- **D3 (medium, review)** — a `wl-paste` timeout escaped untyped.
- **R2 (review)** — an uncaught `Thread.start()` `RuntimeError` escaped **after the paste succeeded**,
  the same shape as ticket 17.
- **⚠️ The keep-worthy one — ticket 36, found by the user:** **`wl-copy` forks by default.** The forked
  child inherits the pipes `capture_output=True` created, and `subprocess.run` reads to EOF, which
  never arrives while that daemon lives. **Every clipboard write blocked the full 5 s and was reported
  as a failure — although the write had succeeded.** The user's report is what identified it: *"the
  transcript did make it to my clipboard"*. Upstream `wl-copy.c` confirms it: it **drains stdin before
  forking**, `dup2`s stdout and stdin to `/dev/null`, but **leaves stderr** — the exact pipe the old
  code waited on. Fixed by redirecting both streams to `tempfile.TemporaryFile()` regular files, which
  a forked child may inherit harmlessly; **both streams stay captured** and the timeout still fires for
  a genuinely wedged command.

**Ticket 37** then closed the blind spot that let it ship: mutating the payload to `b""` left the
**entire 877-test suite green**. The new test drives the **real** `_run_command` with a **real forking
child** (`sh -c '( sleep n ) & …'`, no clipboard) and is the **unique** guard — with the mutation the
suite is `1 failed, 878 passed`.

**Ticket 38** changed the default paste chord from `ctrl+v` to **`ctrl+shift+v`** (see §11b).

**Residuals, all non-blocking and recorded:** R1 (a >64 KiB restore truncates to the 64 KiB pipe buffer
if the process exits inside the delay window), R3 (a blocked daemon thread if the restore child never
reads), R4 (a pre-existing read-path `OSError` escapes raw), **R5** (a leaked blocked restore child per
failed `Thread.start` — the fix would be `proc.kill()`, **not** `stdin.close()`, which would restore an
*empty* selection).

## 10. The silence saga — three attempts, and the project's most expensive lesson

The user toggled recording **on and off without speaking** and got **`thank you`** typed. Three
attempts, each overturning the last explanation:

1. **Ticket 29 attempt 1 — "near-silent audio hallucinates".** Wrong. Digital silence and low-level
   noise return `''` at every level. The real synthetic triggers are **flat DC**, **50 Hz hum**, and
   **short transients** (one 5 ms click → `'Thank you.'`).
2. **Ticket 29 attempt 2 — a whole-buffer RMS floor.** Fixed DC and the accidental double-tap, but the
   reviewer **did the arithmetic** and showed a realistic silent toggle (which contains the **hotkey's
   own key clicks**) measures 2.6–6.5× the threshold — it fixed only inputs that never failed.
   Replaced by a **frame-level sustained-energy gate** (a click is 1–2 × 20 ms frames; speech is many;
   bar = 3 consecutive frames). That gate **is merged** and is what ships today. Its review's verdict
   is the honest framing: **"merge the code; do not call it 'silence solved'"**.
3. **Ticket 31 (`feat/speech-gate-recalibration`) — PARKED, unmerged.** The user *still* got
   `Thank you.`, so a dedicated diagnostic ran. **Not a stale service, not mains hum, not command
   output** (that hunch was tested and ruled out — the string is provably the engine's return value,
   traced `Service` → session → engine → injector). The cause: **the user's "silence" is not silent.**
   Real captures measure **AC RMS 0.036–0.26 — as loud as the speech clips (0.047–0.14)** — because the
   device emits a **broadband ≈20–25 Hz drift at amplitude ≈0.04**. So the amplitude gate correctly
   classifies it as speech and the model hallucinates on it. Ticket 31 replaces amplitude with a
   **speech-band energy ratio** (AC energy above 200 Hz ≥ 0.15): idle **0.001–0.065**, speech
   **0.762–0.980** — an order of magnitude apart and **level-invariant**. It also *measured and
   rejected* the simpler fix (a steeper high-pass still passed idle).
   **Why it is not merged:** the user asked to pause, and **their situation explained the whole
   mystery** — they were **in a car with the window open**, so wind and road rumble are almost
   certainly the "20–25 Hz drift". The failure is **environment-dependent**, *and* the new gate's
   measured cost (quiet speech needs gain ≥ 0.4 where the engine reads down to 0.1) was measured **with
   that noise present** — the very environment where they dictate. Merging it could **drop real
   dictation**, which is worse than the bug. **Absence of absolute silence is not a use case they have.**

**The review sharpened the cost and the park decision came out stronger.** Independent captures: idle
ratio **0.0031–0.0061** vs speech **0.762–0.980** (24.6× / 5.1× margins), and idle is gated with a
**counted zero engine calls** — the *old* gate on the same real idle makes the engine emit `'you'`,
`"I'm not going to do anything."`, `"¿Qué estás buscando?"`, i.e. it **types garbage on every silent
toggle in that environment**. But the reviewer's floor measured **0.28–0.35 RMS — 6–8× the ticket's
0.043-RMS floor** — and the keep-boundary is **noise-relative** (speech RMS ≥ ~0.47 × noise RMS, ≈
−6.5 dB SNR), so today it keeps nothing below **gain ≈ 4.0** while the **engine still reads near-correct
text at gain 0.5** — ~8× more conservative than the engine.

**Verdict shape: mergeable, but the noisy-environment trade is not settled by offline data.** It needs
the user's live test in **both** a quiet room and a noisy one.
**⚠️ A false alarm to know about:** ticket 19's review reported that ticket 31's code had merged
(`e1134f8` on `main`). **It has not** — that commit is **docs-only** (it creates ticket 31's
`ticket.md`), and `main`'s gate is still ticket 29's. Verified by inspecting the commit and the code.

### ⚠️ THE LESSON — put this first when briefing any future audio/signal work

**"Verified against constructed inputs" is not "works on real inputs".** Every gate threshold above was
derived from **synthetic** signals — digital silence, synthesised clicks, a 50 Hz tone — and reviewed
rigorously **against those synthetic claims**. Nobody fed the **actual device's** audio through the
gate until the user toggled it. Two rounds of well-evidenced, correctly-reviewed work aimed at the wrong
mechanism, and the fix only became real when a diagnostic **measured the real captures**.

**Corollary, and it applies far beyond audio:** *"this ticket is rigorous" is not the same as "this
ticket fixes the reported symptom."* When a ticket exists to fix a user-visible symptom, the acceptance
test must include **the user's actual case**, measured.

## 11. ⚠️ THE ACCURACY QUESTION (tickets 39 and 35) — the user's live concern

**The user is hesitant to daily-drive because accuracy is not what they want, and asked for a noise
filter.** Ticket 39 measured four candidate levers on the user's 25 s ground-truth recording. **The
passage is recorded in `docs/project/live-dictation-ux/accuracy-levers/ticket.md`** — it had never been
written into an artifact before, and a WER measurement without a reference is not a measurement.

**What the measurements support (two review rounds; every number reproduced byte-identically):**

| Lever | Result |
|---|---|
| **A noise filter** | **NOT justified.** A 60 Hz high-pass removes **86.0 %** of the recording's AC power (150 Hz, analysed for power *only*, removes 92.9 %) and buys **2 words of 64** (WER 0.391 → 0.359). 85/120 Hz buy **1 word each**. Pulling harder buys **fewer** — a lever whose effect shrinks as you pull it is not the cause |
| **Clipping** | **Not the cause.** 1262 int16 samples (0.315 %), but only **23** float samples ≥ 1.0, and a non-clipping 0.50 gain reproduces the baseline **exactly** |
| **Gain** | `gain_0.85` produces a transcript **byte-identical** to `highpass_60` — two unrelated transforms, same output. That is a **decoding-basin flip**, not a graded signal-quality effect |
| **Model capacity** | **The dominant errors survive the filter**: `valley`→`rally`, `crews`→`cruise`, `flood`→`front`, `river`→`reverse`, `roads`→`roadroom`. These are **acoustic confusions of the 16 MB model** (ADR 0005's accepted risk) |
| **Streaming vs batch** | **The largest lever measured — on this ONE recording.** Streaming **0.391** vs batch **0.281** by the harness's `word_diff` (6 words / −0.094 by standard edit distance — `word_diff` **double-counts unequal-length `replace` blocks** and inflates it) |

**⚠️ THE DIRECTION IS NOT SETTLED, AND THIS WAS ALREADY GOT WRONG ONCE.** **ADR 0011's own clean-clip
measurement found the windowed path *ahead* of one-shot (WER 0.176 vs 0.191)** — the opposite way. So
this is **one 25 s sample against a contrary prior**, not a case for switching the default. The
orchestrator relayed "the streaming path is the real lever, switch to batch" to the user and then had
to **correct it** — that is why §2 says *verify before relaying*. **Ticket 35's multi-length sweep
decides it.** Do not present "switch to batch" as a fix until then.

**The clean cross-check:** on `clip5.wav` (no clipping, ~1.4 % power below 60 Hz) **all eight variants
produce one identical transcript at WER 0.000** — the variant effects only appear on long, noisy input,
which is where decoding luck matters.

**Honest bounds on all of it:** one recording, one length, read speech; the reader may have deviated
from the reference, so absolute WER is an **upper bound** while **variant-vs-variant is fair**; and the
persisted WAV is an **int16 round-trip that does not reproduce the live transcript** (ticket 32's
caveat — `ferry→fairy` from the live run does not reproduce on the WAV).

**The orchestrator's own hypothesis was supported:** effort belongs on the **decoding path and the
model**, not on a filter.

## 11a. What the user has personally verified (do not re-litigate)

- End-to-end dictation into an editor and a terminal — **pass** (`dictation-pipeline-batch/003-comment.md`)
- **`hu` mapping correct** (`y`/`z`, digits) — **pass**; the workstream's whole point
- `ges2t cancel` mid-recording, tray `Quit` (→ `SIGTERM`) — **pass**; long phrases — **pass**
- **Quiet and mumbled speech transcribes**; imperfect output is **accepted** ("good enough" is the bar)
- **Silence → `thank you`** — the bug in §10, still open as the shipped code stands
- `ges2t doctor` — **pass**; tray icon visible in Plasma (check the **overflow "⌃" area**; Plasma
  collapses new tray icons by default) — **pass**
- **The clipboard backend (ticket 25's gate) — PASS.** Run after ticket 36's fix, with
  `injection.backend = "clipboard"` and `paste_keys = ["ctrl+shift+v"]`: **the phrase pasted and the
  pre-existing clipboard was restored.** Recorded in `clipboard-paste-injector/008-comment.md`. This
  is the **only** real-`wl-copy` evidence in the project, and it verifies the real write, the real
  chord through `ydotoold`, and the deferred restore.
- **Incremental typing (ticket 34's gate) — PASS, every checkbox.** Text appears **while speaking**,
  exactly **one trailing space**, and **stop → delete → continue** recovered a word the model got
  wrong. Lag reported as **"less than a second, likely", explicitly not measured** — recorded as a
  qualitative pass, **not as a figure**. Recorded in `incremental-typing/003-comment.md`.

## 11b. Closed decisions worth not re-opening

- **`paste_keys` default is `ctrl+shift+v`** (ticket 38, merged `47431eb`). The shipped `ctrl+v` had
  been **exercised by nobody**; `ctrl+shift+v` was the only chord with manual evidence and it worked in
  **every** target the user tried (nvim, terminal, browser), so one config covered both DoD targets.
  Declared in **three** places (`layout.py`'s `DEFAULT_PASTE_KEYS`, the injector's constructor,
  `pipeline.py`'s docstring) — the review classified all 12 remaining `ctrl+v` hits: 8 are the
  deliberate caveat (*"if your app leaves `ctrl+shift+v` unbound, set `["ctrl+v"]`"*), 4 unrelated
  chord-parser prose. **Honest counterweight, documented with the default:** it is a *different
  binding, not a superset* — an unbound `ctrl+shift+v` pastes **nothing**, whereas `ctrl+v` fails
  *predictably* in terminals. **Do not** configure both: the injector emits **every** chord listed, so
  `["ctrl+v", "ctrl+shift+v"]` pastes **twice**.
- **ADR 0011 is amended (user sign-off given, merged `5eeca4d`).** The committed prefix is now typed
  **as it is committed**; `pending` never is. The original rule (*"nothing is injected until release"*)
  was **broader than its own reason**: its objection — that typing partials "would duplicate and race
  with the committed-text revisions" — is sound for `committed + pending` but **not** for `committed`
  alone, which is append-only and exactly the part that stopped changing. Consequences the user
  accepted: focus must hold for the whole utterance; a **stable hallucination can land mid-dictation**
  (recovered by stop → delete → continue, and the tool **never** auto-corrects); interleaving with the
  user's own typing is **out of scope**; and **cancel is safe but not undo** — it stops typing further
  but cannot remove what is already in the window.
- **The clipboard backend is incompatible with streaming** and is **refused at startup** with a loud
  error naming both settings (ticket 34). It cannot type incrementally, so continuing would silently
  drop the feature or silently rewrite the clipboard.
- **There is no opt-out that keeps streaming.** Incremental typing rides on streaming, so
  `streaming.enabled = false` is the only switch and it also drops streaming. Documented in ticket 19's
  README; if it becomes a problem, an opt-out is its own ticket.

## 12. User preferences and escalation rules

- **Tickets must be small enough for a capable junior developer in one session.** Split anything that
  is really two tickets. (The 32 → 33 → 34 chain is the model: measurement gate, interface, payload.)
- **Type hints everywhere** (including `-> None`), **interface-typed signatures** (Protocols in
  parameters, not just inheritance), **classes instead of positional tuples** — ADR 0019, enforced with
  `mypy src/ges2t tests`.
- **The user wants explanations, not just status** — *why*, and what it costs. They push back well and
  have corrected the orchestrator's framing repeatedly; welcome it. They also **make good design
  calls**: the `ctrl+shift+v` default and the `doctor` resolved-path fix were both their ideas.
- **They escalate on ADRs:** amendments are surfaced in full with measured evidence, never edited
  quietly. Ticket 30 (a new socket verb) **requires an ADR 0016 amendment** — flagged in the ticket.
- **They are not precious about scope:** they say not to get stuck and to ship what is good enough.
  When they say a thing is not a use case, believe them and park it — with the reasoning written down
  so it is not silently reopened.
- Verified decisions: Python MVP; Wayland-only; hotkey via compositor-bound CLI command; `ydotool`
  injection; TOML config at `~/.config/ges2t/config.toml`; streaming **is** v1 and now types
  incrementally; no post-processing except one trailing space; tray via `pystray`'s **AppIndicator**
  backend with `--system-site-packages`; `ges2t setup` is the **only** networked path; closed-engine
  risk accepted; generic configurable keymap (never hardcoded `hu`); `key_delay_ms` default `0`;
  minimum recording duration **100 ms**; `paste_keys` default `ctrl+shift+v`.

## 13. Remaining work

**v1's scope is complete** — the last core ticket (19) is merged, and the tool is in daily-drivable
shape with the user having verified both new mechanisms personally. What is left is follow-up:

```
40  bindable-hotkey-hint      in review      — the resolved-path fix for the hotkey advice (§9a)
35  streaming-vs-batch        to do, P2      — the sweep that settles the accuracy direction (§11)
30  tray-failure-diagnostics  to do, optional — needs an ADR 0016 amendment, not yet raised
31  speech-gate-recalibration blocked/parked  — awaiting the user's quiet + noisy room test (§10)
```

**The three honest open questions, in the user's own terms:**

1. **Accuracy** — is the streaming default costing real words? One recording says yes, ADR 0011 says no.
   **Ticket 35 decides it**, and the user can also try `streaming.enabled = false` as an experiment.
2. **The silence gate** — ticket 31 is parked on their live test in both environments. If it fails
   there, the options are lowering `SPEECH_BAND_RATIO` or fixing the *input* noise — **neither to be
   built speculatively**.
3. **The model** — if the residual errors are the 16 MB model's acoustic confusions, the only real lever
   is a larger model, which is **ADR 0005's accepted risk and a scope decision to bring to the user**,
   not to decide alone.

**Epic 1's exit test:** `ges2t toggle` → speak → `ges2t toggle` types correct text into a **text editor**
and a **terminal**; silence types nothing; framework overhead < 1 s excluding model time; correct
characters on a non-US layout. **The user has run essentially all of this and it passes except the
silence case** (§10).
