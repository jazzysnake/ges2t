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

- `uv run pytest` → **483 passed** (~1.5 s)
- **`uv run mypy src/ges2t tests` → 0 errors (32 files)** — this is the required command. ADR 0019
  was amended 2026-10-09: the old `mypy src/ges2t`-only check let a real violation through a
  completed review (see ticket 20).
- `main` HEAD at time of writing: `f135868` (ticket 27 filed). Ticket 24 merged at `a6ba892`.
- 21 ADRs (0000–0020), **27 tickets**, 2 epics.

## 5. Board state (13 of 26 done)

**Done (14 of 27):** 1 project-scaffold · 2 core-interfaces-and-fakes · 3 config-loading ·
4 whistle-engine-config · 5 model-store-and-verification · 6 audio-capture-pw-record ·
7 ydotool-injector · 8 whistle-transcriber-adapter-batch · 9 service-state-machine-and-socket ·
10 cli-surface-and-commands · 13 streaming-window-engine · 14 whistle-streaming-adapter ·
20 type-check-whole-tree · **24 layout-detection-and-config** (merged `a6ba892`, review `002` pass)

**In review / in flight (see §6):** 21 status-ui-outside-lock (changes requested, B1 fix running) ·
22 xkb-keycode-mapping (implementing, nothing committed yet) · 24 layout-detection-and-config
(implementation committed `5332885`, reviewer running) · 26 pin-cli-test-guarantees (implementing,
nothing committed yet)

**To do:** 11 dictation-pipeline-batch · 12 doctor-command · 15 streaming-session ·
16 streaming-wiring-and-e2e · 17 tray-status-ui · 18 notifications-and-cancel ·
19 user-documentation-and-setup-guide · 23 batched-key-emission · 25 clipboard-paste-injector ·
**27 pin-layout-test-guarantees** (filed from ticket 24's review: both detectors' non-zero-exit
guards are unexercised — empty stdout makes `rc=1` indistinguishable from success — and the 2 s
timeout is never enforced, since `timeout=None` still passes the suite. Same failure class as
tickets 20 and 26. The guards themselves were hand-verified, so it is a **test** gap, not a bug.)

Modules already on `main`: `interfaces.py`, `config.py`, `model_store.py`, `service.py`,
`socket_proto.py`, `streaming.py`, `cli.py`, `audio/pw_record.py`, `injectors/ydotool.py`,
`transcribers/{whistle_config,whistle}.py`, `tests/fakes.py`.

## 6. In-flight agents (collect these first on resume)

Check each worktree's `git log`, then read the ticket + its `001-comment.md` / `002-review.md`.
**Verify the work yourself before merging.** If an agent died, the work is usually committed or the
worktree is dirty-but-complete — recover from artifacts, do not re-run blindly.

| Ticket | Worktree (`/home/matee/Workspace/Personal/ges2t/.worktrees/…`) | Branch | Task |
|---|---|---|---|
| 21 | `status-ui-outside-lock` | `feat/status-ui-outside-lock` | **Re-review** of the B1 fix (`080a2a4`) |
| 22 | `xkb-keycode-mapping` | `feat/xkb-keycode-mapping` | **Review** of `d58a50d`, incl. an authorized `ydotool key` round-trip probe |
| 26 | `pin-cli-test-guarantees` | `feat/pin-cli-test-guarantees` | **Review** of `99ff8fb` |

Ticket 24 merged and its worktree removed. Note ticket 26's branch will hit an **add/add conflict on
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

**⚠️ The mistake made once:** chaining the merge and the follow-up steps on separate shell lines
meant that when the merge conflicted, the follow-ups still ran and **committed `README.md` with
conflict markers in it**. Run the merge as its own command, check `git diff --name-only
--diff-filter=U`, and only then continue.

**Link check** (run after any docs change; must report 0):

```bash
python3 - <<'PY'
import re, pathlib
tok = re.compile(r'(?<![\w/`(])((?:\.\./)+[\w./-]+?\.md)')
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

**⛔ TICKET 23 DECISION GATE — do not lose this.** Ticket 23 measures the batched emission against
ADR 0001's **sub-1-second** framework budget. If it **meets** it, latency work is done. If it does
**not**, report the number and *then* raise with the user whether to type committed text **during**
dictation (overlapping typing with speaking), which would require revisiting **ADR 0011**'s "nothing
is injected until release" rule. The user approved that option in principle, but it is deliberately
held in reserve pending the measurement. **Do not build it speculatively.**

## 10. Open items not yet ticketed / to watch

- **`cactus-needle==3.1.3` vs engine pin `3.2.0`** — pre-existing skew, documented in ticket 8's
  review. Ticket 12 (`doctor`) is already required to report both versions and warn on drift,
  because the streaming decoder is coupled to 3.1.3's exact generator shape (one dict per `next()`,
  terminal tail dict) and the test fake encodes the same shape — so an upgrade could break the real
  seam **while the suite stays green**.
- **Ticket 21 B1** — a raising `status_ui` callback aborts the deferred side effect, permanently
  wedging the service in `TRANSCRIBING`. Fix = make the **display** drain best-effort only; a
  raising `session.start()`/`finish()`/`injector.inject()` must keep failing loudly. Ticket 17's
  tray is what will trip this.
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
21 (fix B1) ─┐
22 ──────────┼─→ 23 (batch + MEASURE) ─→ 25 (clipboard)
24 ──────────┘
26 (independent)
11 (pipeline/E2E) ─→ 12 (doctor) · 15 (session) · 16 (wiring) · 17 (tray) · 18 (notify) ─→ 19 (docs)
```

Tickets 15/16 depend on 9 + 14 (done) and 11. Ticket 17 depends on **21** (the deadlock fix) as
well as 10/11. Ticket 19 is the final ticket and owns the README + setup guide + systemd unit.

**Epic 1's exit test** (not yet satisfied): `ges2t toggle` → speak → `ges2t toggle` types correct
text into a **text editor** and a **terminal**; silence types nothing; framework overhead < 1 s
excluding model time — and now also **with correct characters on a non-US layout**.
