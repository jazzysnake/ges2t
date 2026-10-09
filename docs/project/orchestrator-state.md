# Orchestrator state — resumption point

> **What this is.** Working state for the orchestrating agent, not a project deliverable. It exists
> so a compacted or fresh orchestrator can resume without re-deriving anything. Delete it at
> close-out. The authoritative sources are always: `docs/project/README.md` (the board),
> `docs/adr/` (decisions), and each ticket's `ticket.md` + `NNN-comment.md` + `NNN-review.md`.

## 1. Project in one paragraph

**ges2t** (*good enough speech 2 text*) — a minimal, fully offline Linux speech-to-text tool.
Hotkey → speak → the transcription is typed into whatever window has focus. Local model, no cloud.
Python + `uv`, `src/` layout, Wayland-only, **KDE Plasma 6 on Arch is the reference target**.
Backend: Cactus Compute's 16 MB **Whistle** model on the **Needle 3** engine. v1 bar = the user
*actually uses it daily*; packaging/distribution is explicitly out of scope (ADR 0000).

Original orchestrator brief: `.pi/prompts/orchestrate-project.md`.

## 2. Orchestrator role and hard rules

- **Never implement.** Planning artifacts under `docs/` and git merges only. All implementation and
  all review is done by subagents.
- **Every `Agent` call: `model: "deepseek/deepseek-flash"`, `subagent_type: "general-purpose"`.**
- **You are the merge authority.** Only you merge into `main`, only after a review passes.
- **Escalate to the user** on blocked tickets, scope changes, destructive/consequential actions, and
  any decision that would invalidate an existing ADR. Do not silently work around a blocker.
- Ask before anything externally visible: injecting keystrokes, writing to the user's clipboard,
  running `sudo`, enabling services, installing packages.
- **Trust but verify:** read the actual diff/worktree before merging; a subagent's report describes
  intent, not outcome.

## 3. Environment (verified on this machine)

| Fact | Value |
|---|---|
| OS / session | Arch Linux, KDE **Plasma 6.7.5**, Wayland (`kwin_wayland`) |
| Keyboard layout | **`hu` (Hungarian)** — `X11 Layout: hu`, `VC Keymap: hu`. `~/.config/kxkbrc` has **no `LayoutList`** |
| Python | **3.14.7** at `/usr/bin/python3`; `uv` 0.12.23 |
| Venv requirement | **`uv venv --system-site-packages --python /usr/bin/python3` THEN `uv sync`.** A bare `uv sync` on a fresh checkout creates a venv with `include-system-site-packages = false` **and** picks uv's managed 3.13, silently losing `gi`. There is **no** `[tool.uv]` setting for this (upstream astral-sh/uv#5737 is open) |
| `ydotoold` | **active AND enabled** (user enabled it 2026-10-09). Socket `/run/user/1000/.ydotool_socket`, mode 0600 |
| `/dev/uinput` | `crw-rw----+ root:input` — user `matee` is in `input` and can write it |
| Models | Already downloaded to `~/.local/share/ges2t/`: `models/whistle/2.0.0/whistle.cact` (16,919,407 B), `engine/3.2.0/libneedle.so` (1,581,448 B) |
| Speech test clips | `/tmp/whinspect/` — `clip5.wav` (5 s), `clip30.wav` (30 s), etc. **Never commit audio.** May be cleaned by /tmp reaping; if gone, use `pw-record` |
| Not installed | `wtype`, `dotool`, `numpy` (numpy is deliberately absent) |

## 4. Current baselines on `main`

- `uv run pytest` → **567 passed** (~2.7 s)
- **`uv run mypy src/ges2t tests` → 0 errors (37 files)** — this is the required command. ADR 0019
  was amended 2026-10-09: the old `mypy src/ges2t`-only check let a real violation through a
  completed review (see ticket 20).
- `main` HEAD when last checked: `b4aa911` (ticket 23 closed). Merged this session: 24 `a6ba892`,
  21 `11b1bf3`, 26 `ceee7a2`, 11 `338cdc2`, 22 `0a2074a`, **23 `0a2074a`→ its merge commit**;
  all six worktrees removed.
- 21 ADRs (0000–0020), **28 tickets**, 2 epics.

## 5. Board state (20 of 28 done)

**Done (20 of 28):** 1 project-scaffold · 2 core-interfaces-and-fakes · 3 config-loading ·
4 whistle-engine-config · 5 model-store-and-verification · 6 audio-capture-pw-record ·
7 ydotool-injector · 8 whistle-transcriber-adapter-batch · 9 service-state-machine-and-socket ·
10 cli-surface-and-commands · 13 streaming-window-engine · 14 whistle-streaming-adapter ·
20 type-check-whole-tree · 24 layout-detection-and-config (`a6ba892`) · 21 status-ui-outside-lock
(`11b1bf3`) · 26 pin-cli-test-guarantees (`ceee7a2`) · 11 dictation-pipeline-batch (`338cdc2`) ·
22 xkb-keycode-mapping (`0a2074a`) · **23 batched-key-emission** (`b4aa911`)

**In review (see §6):** 17 tray-status-ui and 15 streaming-session — both had **changes requested**
and are in a fix/re-review cycle (17 is on its fifth agent; see §7 on connection deaths) ·
**25 clipboard-paste-injector** and **28 injection-delay-default** — dispatch just made

**To do:** 12 doctor-command (needs 17) · 16 streaming-wiring-and-e2e (needs 15) ·
18 notifications-and-cancel (needs 17) · 19 user-documentation-and-setup-guide (needs everything)

**28 injection-delay-default** is new and was filed from ticket 23's measurement: `0`/`6`/`12` ms were
measured and only `0` (47.9 ms, byte-perfect) fits ADR 0001's budget, so the shipped default of `12`
(3.1 s) would have shipped the product **over its own budget**. Fixing it is a one-value change to
ticket 24's `DEFAULT_KEY_DELAY_MS`.

Modules already on `main`: `interfaces.py`, `config.py`, `model_store.py`, `service.py`,
`socket_proto.py`, `streaming.py`, `cli.py`, `audio/pw_record.py`, `injectors/ydotool.py`,
**`injectors/layout.py`** (ticket 24), **`pipeline.py`** (ticket 11),
`transcribers/{whistle_config,whistle}.py`, `tests/fakes.py`, **`injectors/keymap.py`** (ticket 22).
In flight, not yet merged: `pipeline.py` (ticket 17 modifies it), `streaming_session.py` (ticket 15),
`ui/{status,tray}.py` (ticket 17).

## 6. In-flight agents (collect these first on resume)

Check each worktree's `git log`, then read the ticket + its `001-comment.md` / `002-review.md`.
**Verify the work yourself before merging.** If an agent died, the work is usually committed or the
worktree is dirty-but-complete — recover from artifacts, do not re-run blindly.

| Ticket | Worktree (`/home/matee/Workspace/Personal/ges2t/.worktrees/…`) | Branch | Task |
|---|---|---|---|
| 23 | `batched-key-emission` | `feat/batched-key-emission` | **Implement** — batched `ydotool key`; measurement needs authorisation |
| 17 | `tray-status-ui` | `feat/tray-status-ui` | **Review** of `fea1ecb` |
| 15 | `streaming-session` | `feat/streaming-session` | **Review** of `2d64f83` |
| 27 | `pin-layout-test-guarantees` | `feat/pin-layout-test-guarantees` | **Review** of `911db7b` |

Tickets 21, 24, 26, 11 and 22 are merged and their worktrees removed. Note ticket 26's branch will hit an **add/add conflict on
ticket.md** plus a row-26 conflict at merge (it was cut before the ticket folder and board row were
committed to `main`); resolve to the branch's version — `ticket.md` `in review`, row 26 `in review`.
Use three-dot diffs (`git diff main...<branch>`): several branches were cut before ticket 10 merged.

## 7. Workflow discipline (the protocol that has been working)

**Implementer brief must contain:** the ticket path; "read the ticket and every artifact it links
(epic, ADRs, related tickets) first"; worktree + branch instructions
(`git worktree add .worktrees/<slug> -b feat/<slug>`; **never commit to `main`**; **do not remove the
worktree** — the reviewer needs it); the ADR 0019 style rules verbatim; the exact verify commands;
a `NNN-comment.md` requirement; status → `in review` (or `blocked`); the board row update; commit;
report back (branch, worktree, files, real command output, anything the orchestrator must know).

**Reviewer brief must contain:** read ticket + epic + ADRs + **every** comment; run the checks
(**do not trust ticked boxes**); **do not touch the implementation** (only write the review
artifact); `NNN-review.md` with verdict / per-criterion findings / **evidence (commands + real
output)** / risks / explicitly-what-could-not-be-verified; commit; report verdict + blocking
findings.

**Enforcement patterns to always demand of reviewers (learned the hard way):**

1. **Prove invariants mechanically, not by reading.** E.g. ticket 21's reviewer replaced the lock
   with an owner-tracking lock and instrumented every collaborator call — 0 violations. "I read the
   diff" is not evidence.
2. **Falsify every claim and every new test**: break the thing the test guards, watch it **fail**,
   then revert completely. A test never seen to fail is not a test. (This caught: a vacuous
   `sys.modules` assertion, a laziness test that passes with a top-level import, exit-code tests
   that compare against their own constants, and a `mypy` check that didn't cover tests.)
3. **No unbounded waits in tests.** Use `join(timeout)` so a regression **fails loudly instead of
   hanging**. Mutation-testing runs need a **per-run timeout** — an unbounded mutation harness
   wedged for 87 minutes once and had to be killed.
4. **Clean up**: kill spawned processes, remove stray sockets and probe files, confirm
   `git status` clean. A dead reviewer left `src/ges2t/_probe.py` behind once.
5. Run **`uv run mypy src/ges2t tests`** and confirm the config bites via an unannotated-function
   probe.
6. For injection tickets: **no keystrokes without asking the user**; no newlines in payloads; report
   the lowest delay that loses no characters rather than a flattering number.

**Numbers/artifact convention:** artifacts inside a ticket folder share one numbering sequence
(`001-comment.md`, `002-review.md`, …). List the folder and take the next free number; never reuse
or renumber.

**On changes requested:** resume the *same* implementer with the findings, then re-review. (If
`resume` fails with "agent not found", spawn a fresh implementer scoped to *verify and extend the
existing work* — never to rewrite it.)

**A fix can introduce its own regression — re-review the new paths, not just the finding.** Ticket
21 fixed a reproduced deadlock and thereby *created* a worse bug: the display drain moved into a
`finally` outside the defensive `try/except`, so a raising `status_ui` callback skipped a deferred
side effect and left the service **permanently wedged in `TRANSCRIBING`** (every later command
returned `busy`) — the same "hotkey silently stops responding" class the ticket existed to fix.
So when a change-requested fix comes back, the re-review must inspect the **new code paths it
introduced** and their exception behaviour, not merely confirm that the original finding is gone.
Corollary for briefs: when a fix makes something best-effort, **say explicitly what must stay
fatal** — otherwise one silent failure is traded for another.

**Do not build a ticket's default on a ticket that depends on it.** Ticket 24's `key_delay_ms`
default was specified as "chosen from ticket 23's measurement", but ticket 23 *depends on* ticket 24,
so the default could not exist yet. The implementer made it a single provisional constant and
flagged the contradiction — the right call. Watch for this when writing DoD lines that cite a
downstream ticket's output.

**Enforcement is where the real bugs were found — keep raising the bar.** The defects that
mattered were almost never found by a test: a US-only keymap that corrupted every non-US layout
(all 274 tests passed), a `mypy` check that did not cover tests, a laziness test that passed with a
top-level forbidden import, exit-code tests that compared against their own constants, and a
`sys.modules` assertion that was vacuous because the module was never installed. Each was caught by
someone **trying to falsify a claim**. Ask reviewers for that explicitly every time.

**Two caching traps that make a probe lie — clear both after every mutation.**

1. **`mypy` caches stale errors.** A mutation that leaves a file the **same byte length** can be
   restored by `git checkout` without invalidating `.mypy_cache`, so you read **stale** errors and
   can reach the wrong conclusion. Run `rm -rf .mypy_cache` before trusting any mypy result after a
   mutation or revert. (Found by ticket 11's reviewer.)
2. **Stale bytecode.** Restoring a mutated `src` file with `cp` **within the same second** leaves a
   `.pyc` that Python accepts, so the "restored" run silently executes the **mutation**. Delete
   `__pycache__` too. (Found by ticket 15's reviewer, who was fooled by it once.)

**A hanging suite is worse than a failing one — check that the run *exits*.** Ticket 15's reviewer
found the sharpest version yet: under a lock mutation the test phase printed
`1 failed, 20 passed in 2.17s` and then **pytest never exited** (`exit 124` under a 20 s cap), because
a leaked **non-daemon** worker blocked `threading._shutdown`. So the run reported a failure and then
wedged the terminal, and a call-level `join(timeout)` does **not** help — a bound on the *call*
cannot kill a leaked thread. When requiring "no unbounded waits", verify the **process exits**
(use `timeout <n> uv run pytest` and check the status), not merely that the tests report a result.
- **Never take over the user's screen without asking.** A live-injection probe needed a focused
  scratch terminal, and the `konsole` windows it opened **hid the user's chat** and startled them.
  The probe did produce the project's strongest evidence on the `hu` bug — a **byte-exact 17/17**
  round trip — so the lesson is not "don't probe" but **"ask for that specific side effect up
  front"**: permission to inject keystrokes is *not* permission to take over the display, and an
  agent should also state how long a window will be up. Current rule: any ticket whose verification
  needs a **human voice** or a **visible GUI surface** has that step marked **user-owned**
  (tickets 11 and 17) — the agent prepares a script and hands it over.
- **Long agent runs die on connection errors, and context dies with them.** Three runs were lost in
  one session (ticket 15's reviewer twice, ticket 23's implementer once), all at 600–1160 s. The
  work is not lost if it was *committed*; it **is** lost if a reviewer was still holding findings in
  context. Mitigations: (a) for any long task, tell the agent to **write its artifact to disk
  incrementally** and append after each check, so a death costs at most one check; (b) `resume`
  preserves context and is much cheaper than restarting, but **an agent can be cleaned up and be
  unresumable** — then dispatch a **fresh** agent scoped to *"verify and extend the existing work,
  never rewrite it"*, and say explicitly that the work already exists and where; (c) instruct
  implementers to **commit the code before attempting anything risky or slow** (e.g. an authorised
  measurement), so an infrastructure death cannot leave valuable work uncommitted.

## 8. Merge procedure (and the mistake to avoid)

1. Confirm the review file says **pass** and the ticket is `in review`.
2. `git merge --no-ff feat/<slug> -m "Merge ticket <n>: <slug> … verdict pass (NNN-review.md)."`
3. **If `README.md` conflicts — it usually does — resolve mechanically:** keep the correct status for
   every row on both sides (usually `done` wins for already-merged tickets, and the branch's own row
   becomes `in review`).
4. Flip `ticket.md` status to `done`, update the board row, commit on `main`.
5. `git worktree remove --force .worktrees/<slug>` and `git branch -d feat/<slug>`.
6. Re-run `uv run pytest` and `uv run mypy src/ges2t tests` on `main`, plus
   `uv sync` and the link check (below).

**⚠️ Two ways this has been got wrong — both shipped a committed `README.md` containing conflict
markers.**

1. *Chaining.* Running the merge and the follow-up steps on separate shell lines meant the
   follow-ups ran even though the merge had conflicted. Run the merge as **its own command**, check
   `git diff --name-only --diff-filter=U`, and only then continue.
2. *Interleaving tool calls — hit again on ticket 26.* Issuing the `edit` that resolves the markers
   and the `bash` that runs `git add` **in the same tool block** let them interleave: `git add`
   staged the file *before* the edit landed, so the commit captured the **unresolved** version while
   the working tree looked correct. The check then passed, because it grepped the working tree, not
   the index. **Never put `git add`/`git commit` in the same block as an `edit`.** Do the edits,
   then run the git commands in a **separate** step.

**Verify the committed object, not the working tree.** After any merge or conflict resolution,
assert against `HEAD`, not the filesystem: `git show HEAD:docs/project/README.md | grep -n '^<<<<<<<'`
and `git grep -n '^<<<<<<<' HEAD -- docs` must both be empty. A clean `git status` proves nothing if
the index was staged from the wrong moment. If it slips through, `git commit --amend --no-edit`
repairs a merge at the tip while preserving both parents.

**Link check** (run after any docs change; must report 0). Note the negative lookbehind also
excludes `.`, because an **ellipsis** in prose (``.../001-comment.md``) contains `../` and a
naive pattern flags it as a broken parent link — that happened once and cost a false alarm:

```bash
python3 - <<'PY'
import re, pathlib
tok = re.compile(r'(?<![\w/`.])((?:\.\./)+[\w./-]+?\.md)')
bad=[(str(f), m.group(1)) for f in sorted(pathlib.Path("docs").rglob("*.md"))
     for m in tok.finditer(f.read_text()) if not (f.parent / m.group(1)).resolve().exists()]
print(f"{len(bad)} broken references"); [print(" ", f, t) for f, t in bad]
PY
```

## 9. The injection saga (most important open thread)

**Discovery:** the user's manual verification — not any test — found that `ydotool type` **corrupts
text on a non-US layout**. With layout `hu`: `0`→`ö`, `-`→`ü`, `y`→`z`, matching
`xkbcli how-to-type --layout hu` exactly. Root cause: `ydotool` 1.0.4 resolves text via a
**hardcoded US-position table and silently drops non-ASCII**. **Unfixable by config** (no
`--keymap`, no keymap files, no env var; last upstream release 2023; layout-aware typing is an open
PR). All 274 tests passed while this was broken, because the fake runner asserts the payload reaches
`ydotool`'s stdin — which it does. **This is exactly what ADR 0014's manual-verification carve-out
exists for.**

**Second discovery (measured):** `ydotool type` costs **~40 ms/char** (4.99 s for 124 chars;
2.76 s at `--key-delay 2`; ~16–18 ms/char floor even at delay 0). Cause: `type` **sleeps twice per
character** (20+20 ms defaults — the man page's "12 ms" is stale). **`ydotoold` itself never
sleeps.** So the floor is client-side and may be avoidable by batching.

**Decisions taken (ADR 0020 + ADR 0003 amended):**
- Keep **`ydotool` as transport** but **never call `ydotool type`**; compute the key events
  ourselves.
- Mapping engine = **`libxkbcommon`** (already installed; what KWin uses) via **ctypes** (no new
  dependency), against the **live compositor keymap** from `xkbcli dump-keymap-wayland` (here
  `pc_hu_inet(evdev)_capslock(ctrl_modifier)`). Generic over any XKB layout — **no hardcoded `hu`**.
- Emission = **batched `ydotool key`** with a precomputed event list.
- **Unreachable codepoints** (e.g. emoji) → **skip + warn**, never garbage.
- **Clipboard-paste becomes a real configured alternative** (`injection.backend = "ydotool" |
  "clipboard"`), still **not** an automatic fallback.
- **`wtype` is a dead end** (KWin lacks `zwp_virtual_keyboard_v1`); **`dotool`** is AUR-only →
  fallback backend only.

**✅ TICKET 23 MEASURED IT — THE GATE RESOLVED IN OUR FAVOUR.** Batched `ydotool key`, 125-char
phrase, byte-compared against the capture:

| delay ms | wall ms | ms/char | byte-perfect | within budget |
|---:|---:|---:|:--:|:--|
| 12 | 3100.7 | 24.81 | yes | **no** |
| 6 | 1595.4 | 12.76 | yes | **no** |
| **0** | **47.9** | **0.38** | **yes** | **yes** |

Against a ~4.99 s baseline that is a **~104× speedup**, and it meets ADR 0001's budget by 20×. So
**latency work is finished** and the option to type committed text *during* dictation was **NOT
built** — ADR 0011's "nothing is injected until release" rule stands unchanged.

The cost is linear (`delay × 2 × 126` events — `ydotool key` sleeps once per event, down *and* up,
not per character), so **any non-zero delay reintroduces 252 sleeps**; that is why ticket 28 sets the
default to `0`. Caveat to keep honest: the capture target was a **pty**, not a real editor, and the
numbers are **not independently reproducible** (the captures were deleted) — the reviewer audited
the evidence and recomputed the md5 and the linear model rather than re-running it.

## 10. Open items not yet ticketed / to watch

- **`cactus-needle==3.1.3` vs engine pin `3.2.0`** — pre-existing skew, documented in ticket 8's
  review. Ticket 12 (`doctor`) is already required to report both versions and warn on drift,
  because the streaming decoder is coupled to 3.1.3's exact generator shape (one dict per `next()`,
  terminal tail dict) and the test fake encodes the same shape — so an upgrade could break the real
  seam **while the suite stays green**.
- **Ticket 21 B1 — RESOLVED**, merged at `11b1bf3` (review `003` pass). The display drain is now
  best-effort **per callback**; a raising `session.start()`/`finish()`/`cancel()` or
  `injector.inject()` still moves the service to `ERROR` (the re-review proved it, and showed the
  fix *reduced* masking rather than adding it). **Open consequence, do not lose it:** the
  containment is silent, so a `StatusUI` that always raises would stop updating with no signal at
  all — the ADR 0013 silent-degradation class. The re-review ruled it **non-blocking but a required
  follow-up**, now written into ticket **17** (`TrayStatusUI` must record its own failures) and
  ticket **12** (`doctor`/`status` must surface them).
- **Ticket 12 is the dumping ground for environment checks** — it already must check: config
  validity + warnings, model/engine presence, PipeWire session (the mitigation for ticket 6's
  ~50 ms zero-sample race), `ydotoold`, `/dev/uinput`, tray backend (`appindicator`, not `_xorg`),
  audio device present in the node list, engine/package versions, and print the recommended KDE
  key bindings.
- **Ticket 6's unknown `--target` requirement was removed** (ADR 0008 amended): `--target` is only a
  PipeWire hint and falls back silently, so a bad device cannot be detected in the audio source.
- Minor accepted nits: test 3 in `test_whistle_config.py` covers non-finite only on
  `window_seconds`; `001-comment.md` files contain a few harmless wording nits.

## 11. User preferences and escalation rules

- **Tickets must be small enough for a capable junior developer in one session.** Split anything
  that is really two tickets.
- **Type hints everywhere** (including `-> None`), **interface-typed signatures** (protocols in
  parameters, not just inheritance), and **classes instead of positional tuples** — ADR 0019, and
  it *must* be enforced with `mypy src/ges2t tests`.
- The user wants **explanations, not just status**: when something changes the plan, explain *why*
  and what it costs. They push back well and have corrected the orchestrator's framing more than
  once; welcome that.
- The user **escalates on ADRs**: amendments to accepted ADRs should be surfaced explicitly, with the
  measured evidence, rather than quietly edited.
- Verified user decisions so far: Python MVP; Wayland-only; hotkey via compositor-bound CLI command;
  `ydotool` injection; config in TOML at `~/.config/ges2t/config.toml` with a generic layer + engine
  selector + engine-specific blocks; streaming **is** v1; no post-processing except one trailing
  space; tray via `pystray`'s **AppIndicator** backend with system-site-packages; `ges2t setup` is
  the **only** networked path; accepted the closed-engine risk; **agreed to all three injection
  workstreams and to a generic, configurable keymap** (not hardcoded `hu`).

## 12. Remaining work order (dependency-respecting)

```
22 (fixing) ─┐
24 (merged) ─┼─→ 23 (batch + MEASURE — the decision gate) ─→ 25 (clipboard)
             │
11 (merged) ─┴─→ 17 (tray, dispatched) ─→ 18 (notify) ─→ 19 (docs)
                15 (session, dispatched) ─→ 16 (wiring)
                12 (doctor — hold until 22 merges)
26 (merged), 27 (dispatched) — test-guarantee pinning
```

**Ticket 11's latency result sharpens ticket 23's gate:** the framework portion (socket RTT +
capture stop/drain + handoff) is **~3.6–3.8 ms**, independently re-measured by the reviewer. The
entire latency problem is therefore **injection** (~40 ms/char ≈ 4.8 s for ~120 chars today). So
23's gate is one clean question — does batched `ydotool key` get a 20-word phrase under 1 s? If yes,
latency work ends; if no, bring the user the incremental-injection choice rather than deciding it.

**Accepted non-blocking gap (ticket 11, no ticket filed):** swapping the session factory's arguments
is caught by **mypy** but not at runtime. Deliberately not ticketed — unlike the earlier gaps
(`mypy` not covering tests, the vacuous laziness test, exit codes compared to themselves), a real
check *does* cover it and any real run would fail loudly.

**Ticket 11 owns `src/ges2t/pipeline.py` (the composition root), and several later tickets edit that
same file.** Its DoD was amended before dispatch, because three of its criteria were invalidated by
ADR 0020 or cannot be done by an agent at all: (i) its `--key-delay` item is **superseded** — ticket
23 owns injection latency now, so 11 records the *before* number instead of tuning a delay that is
no longer used; (ii) its manual end-to-end test needs a **human voice** and is therefore the
**user's** step, with the implementer preparing the script; (iii) its character-correctness and
<1 s criteria **cannot pass until ticket 23 lands** on a `hu` layout, so they are recorded as known
in-flight defects rather than blockers. Ticket 11 must not inject keystrokes.

**Sequencing traps — check these before dispatching anything:**
- ~~**17 must follow 11**~~ — **satisfied**: 11 is merged, and 17 was dispatched after it.
- **12 should follow 22**, since it now checks `libxkbcommon` loadability and the resolved keymap.
- **23 and 25 both rework `build_injector`**, which 11 created — read ticket 11's `001-comment.md`
  before dispatching 23 so the backend-selection contract is consistent. 11's reviewer noted 16 must
  thread `config`/streaming settings into the **private** `_session_factory`, leaving the public
  `run_service` signature unchanged.
- 15/16 depend on 9 + 14 (done) **and 11** (done). 19 is the final ticket: README + setup guide +
  systemd.

**Epic 1's exit test** (not yet satisfied): `ges2t toggle` → speak → `ges2t toggle` types correct
text into a **text editor** and a **terminal**; silence types nothing; framework overhead < 1 s
excluding model time — and now also **with correct characters on a non-US layout**.
