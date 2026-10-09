# Orchestrator state — resumption point

> **What this is.** Working state for the orchestrating agent, not a project deliverable. It exists
> so a compacted or fresh orchestrator can resume without re-deriving anything. Delete it at
> close-out. The authoritative sources are always: `docs/project/README.md` (the board),
> `docs/adr/` (decisions), and each ticket's `ticket.md` + `NNN-comment.md` + `NNN-review.md`.
> **If this file disagrees with the board, the board wins.**

## 1. Project in one paragraph

**ges2t** (*good enough speech 2 text*) — a minimal, fully offline Linux speech-to-text tool.
Hotkey → speak → the transcription is typed into whatever window has focus. Local model, no cloud.
Python + `uv`, `src/` layout, Wayland-only, **KDE Plasma 6 on Arch is the reference target**.
Backend: Cactus Compute's 16 MB **Whistle** model on the **Needle 3** engine. v1 bar = the user
*actually uses it daily*; packaging/distribution is explicitly out of scope (ADR 0000).

Original orchestrator brief: `.pi/prompts/orchestrate-project.md`.

## 2. Orchestrator role and hard rules

- **Never implement.** Planning artifacts under `docs/` and git merges only. All implementation and
  all review is done by subagents. The single exception is an explicit user request to implement.
- **Every `Agent` call: `model: "deepseek/deepseek-flash"`, `subagent_type: "general-purpose"`.**
- **You are the merge authority.** Only you merge into `main`, only after a review passes.
- **Escalate to the user** on blocked tickets, scope changes, destructive/consequential actions, and
  any decision that would invalidate an existing ADR. Do not silently work around a blocker.
- **Ask before anything externally visible**: injecting keystrokes, writing to the user's clipboard,
  **sending a desktop notification**, running `sudo`, stopping/starting a service, installing
  packages, **recording from the microphone**, and **opening any window on the user's screen**.
- **Trust but verify**: read the actual diff/worktree before merging; a subagent's report describes
  intent, not outcome.

## 3. Environment (verified on this machine)

| Fact | Value |
|---|---|
| OS / session | Arch Linux, KDE **Plasma 6.7.5**, Wayland (`kwin_wayland`) |
| Keyboard layout | **`hu` (Hungarian)** — `X11 Layout: hu`, `VC Keymap: hu`. `~/.config/kxkbrc` has **no `LayoutList`** |
| Python | **3.14.7** at `/usr/bin/python3`; `uv` 0.12.23 |
| Venv requirement | **`uv venv --system-site-packages --python /usr/bin/python3` THEN `uv sync`.** A bare `uv sync` on a fresh checkout creates a venv with `include-system-site-packages = false` **and** picks uv's managed 3.13, silently losing `gi`. There is **no** `[tool.uv]` setting for this (upstream astral-sh/uv#5737 is open) |
| `ydotoold` | **active AND enabled**. Socket `/run/user/1000/.ydotool_socket`. The systemd unit is **`ydotool.service`** — `ydotoold` is only the daemon binary (a ticket once said otherwise; corrected) |
| `/dev/uinput` | `crw-rw----+ root:input` — user `matee` is in `input` and can write it |
| Models | In `~/.local/share/ges2t/`: `models/whistle/2.0.0/whistle.cact` (16,919,407 B), `engine/3.2.0/libneedle.so` (1,581,448 B) |
| Speech test clips | `/tmp/whinspect/` — `clip5.wav`, `clip30.wav`, `2/3/4.wav`, `cny16`, `jfk`, … **Never commit audio.** May be reaped; regenerate with `pw-record` |
| Tray | `python-gobject` 3.56.3, `gtk3` 3.24.52, `libappindicator` 12.10.1 all installed; `assert_appindicator_backend()` **passes** here |
| Clipboard | `wl-copy` / `wl-paste` present (wl-clipboard 2.3.0) |
| Not installed | `wtype`, `dotool`, `numpy` (numpy is deliberately absent — never add it) |
| **User's usual environment** | They use it in a **car with the window open** as well as indoors — i.e. **a noisy, low-frequency-rich environment is a normal use case**. This mattered enormously for ticket 29/31 (see §10) |

## 4. Current baselines on `main`

- `uv run pytest` → **767 passed**
- **`uv run mypy src/ges2t tests` → 0 errors (49 files)** — this is the required command. ADR 0019
  was amended: the old `mypy src/ges2t`-only check let a real violation through a completed review
  (ticket 20).
- Quick health check available to anyone: **`uv run ges2t doctor`** (ticket 12) — 22 checks,
  exit `0` with no `FAIL`s. It reports the resolved keymap and its source, the tray backend, the
  engine/package versions, `ydotoold`, `/dev/uinput`, and the recommended KDE bindings. **Run it
  first on resume** — it is faster than reading the environment table above.
- 21 ADRs (0000–0020), **31 tickets**, 2 epics.

## 5. Board state (27 of 31 done)

**Done (27):** 1–18, 20, 21, 22, 23, 24, 26, 27, 28, 29. Highlights and merge commits:
- 11 `338cdc2` pipeline/composition root · 12 `036d1e2` doctor · 15 `2ba0a3a` streaming session ·
  16 streaming wiring · 17 tray (four review rounds) · 18 `829937a` notifications ·
  20 type-check · 21 `11b1bf3` status-ui deadlock · 22 `0a2074a` keymap ·
  23 `b4aa911` batched emission · 24 `a6ba892` layout/config · 26 `ceee7a2` cli test guarantees ·
  27 layout test guarantees · 28 `37e8937` delay default · 29 `f9df8a0` silence gate

**Remaining (4):**
| # | Ticket | State |
|---|---|---|
| **25** | clipboard-paste-injector | **implemented, in review** — `clipboard.py` + `build_injector` backend selection; the reviewer's first job is confirming nothing ever wrote the user's real clipboard |
| **19** | user-documentation-and-setup-guide | **not started** — the last core ticket; deliberately last because it documents final behaviour |
| **30** | tray-failure-diagnostics | filed follow-up; needs a read-only socket verb (**requires an ADR 0016 amendment**) |
| **31** | speech-gate-recalibration | **PARKED by the user** — branch exists, **review passed on its own evidence** (`dc75977`), **not merged**; the cost is noise-relative and may gate quiet speech in a noisy place. See §10 |

## 6. In-flight agents (collect these first on resume)

Check each worktree's `git log`, then read the ticket + its `NNN-comment.md` / `NNN-review.md`.
**Verify the work yourself before merging.** If an agent died, the work is usually committed or the
worktree is dirty-but-complete — recover from artifacts, do not re-run blindly.

| Ticket | Worktree | Branch | Task |
|---|---|---|---|
| 25 | `.worktrees/clipboard-paste-injector` | `feat/clipboard-paste-injector` | **Review** in progress (real-clipboard safety first) |
| 31 | `.worktrees/speech-gate-recalibration` | `feat/speech-gate-recalibration` | **Review DONE** (`dc75977`): mergeable on its own evidence, but **do not merge** — the cost is noise-relative and the user parked it. Branch stays **unmerged**. |

Everything else is merged and its worktree removed. Use three-dot diffs
(`git diff main...<branch>`): many branches were cut before later merges.

## 7. Workflow discipline (the protocol that has been working)

**Implementer brief:** the ticket path; "read the ticket and every artifact it links (epic, ADRs,
related tickets) first"; worktree + branch (`git worktree add .worktrees/<slug> -b feat/<slug>`;
**never commit to `main`**; **do not remove the worktree**); the ADR 0019 style rules verbatim; the
exact verify commands; a `NNN-comment.md`; status → `in review` (or `blocked`); **only its own board
row**; **commit after each working step**; report real output, not summaries.

**Reviewer brief:** read ticket + epic + ADRs + **every** comment; run the checks (**do not trust
ticked boxes**); **do not touch the implementation or the tests**; `NNN-review.md` with verdict /
per-criterion findings / **evidence (commands + real output)** / risks / an explicit
**could-not-verify** list; commit; report.

### Enforcement patterns — demand these of every reviewer

1. **Prove invariants mechanically, not by reading.** Ticket 21's reviewer replaced the lock with an
   owner-tracking lock and instrumented every collaborator call. "I read the diff" is not evidence.
2. **Falsify every claim and every new test**: break the guarded behaviour, watch it **fail**, revert
   completely. A test never seen to fail is not a test.
3. **A check that cannot fail is worse than no check** — it reads as verification. Found repeatedly:
   a `mypy` scope that excluded `tests/`; a laziness test blind to import-time imports; exit-code
   tests comparing against their own constants; a vacuous `sys.modules` assertion; a
   `timeout=None` that left the suite green; **a `doctor` check that printed a green `OK` with
   nothing to read**; and a dedup test whose clock advanced by *the constant under mutation*
   (`X == X`). Watch for this pattern everywhere, including in **shipped user-facing output**.
4. **No unbounded waits.** Use `join(timeout)`. Mutation runs need a **per-run timeout** — an
   unbounded harness wedged for 87 minutes once.
5. **A hanging suite is worse than a failing one: check that the process *exits*.** Ticket 15's
   reviewer found the sharpest case: a mutation printed `1 failed, 20 passed in 2.17s` and then
   **pytest never exited** (`exit 124` under a cap) because a leaked **non-daemon** worker blocked
   `threading._shutdown`. A bound on the *call* cannot kill a leaked thread. Verify with
   `timeout <n> uv run pytest` and **check the status**.
6. **Clean up**: kill spawned processes, remove stray sockets and probe files, confirm `git status`
   clean. A dead reviewer once left `src/ges2t/_probe.py` behind.
7. **Never commit a modified `src/` after probing.** Check `git diff --stat` before committing; a
   reviewer once left `pipeline.py` in its deliberately-mutated state, which would have shipped a
   **deliberately broken** file that the tests were *designed* to pass against.

### Two caching traps that make a probe lie — clear both after every mutation

1. **`mypy` caches stale errors.** A mutation that leaves a file the **same byte length** can be
   restored by `git checkout` without invalidating `.mypy_cache`, so you read stale errors.
2. **Stale bytecode.** Restoring a mutated file with `cp` **within the same second** leaves a `.pyc`
   Python accepts, so the "restored" run silently executes the **mutation**.

→ After every probe: `rm -rf .mypy_cache __pycache__`.

### Other hard-won rules

- **Artifact numbering:** files inside a ticket folder share one sequence (`001-comment.md`,
  `002-review.md`, `003-comment.md`, …). List the folder, take the next free number, **never
  renumber or rewrite** — append addenda instead.
- **On changes requested:** resume the *same* implementer; then re-review. If `resume` says "agent
  not found", dispatch a **fresh** agent scoped to *"verify and extend the existing work, never
  rewrite it"*, and say explicitly that the work exists and where.
- **A fix can introduce its own regression — re-review the new paths, not just the finding.** Three
  times this session: ticket 21's deadlock fix wedged the service in `TRANSCRIBING`; ticket 15's
  daemon fix opened a shutdown data-loss window (examined and accepted); ticket 17's fallback
  narrowing stopped catching **thread-start** failures, so a broken tray could stop ges2t starting —
  the very thing ADR 0013 forbids. Corollary: when a fix makes something best-effort, **state
  explicitly what must stay fatal**.
- **Don't build a ticket's default on a ticket that depends on it** (ticket 24's `key_delay_ms`
  awaited ticket 23's measurement, but 23 depended on 24).
- **Never take over the user's screen without asking.** A probe's `konsole` windows hid the user's
  chat. Permission to inject keystrokes is **not** permission to take over the display. Any ticket
  whose verification needs a **human voice** or a **visible GUI surface** has that step marked
  **user-owned** — the agent prepares a script and hands it over. Tickets 11, 17, 18, 25, 29 and 31
  all have such steps.
- **Anything that touches the user's machine needs its own explicit ask**, however benign it looks:
  keystrokes, the clipboard (`wl-copy` **destroys what they had copied**), desktop notifications (a
  popup), **the microphone** (privacy), stopping a service (`ydotoold` breaks their dictation while
  stopped). Agents must **prove** they didn't: a `PATH` shim recording invocations was used to show
  **zero** real notifications were sent.
- **⚠️ A reviewer recorded the user's microphone anyway — the prohibition must be in the dispatch
  brief, not a later steer.** Ticket 31's reviewer took **5 short idle captures** (0.08–2.5 s, raw
  float32) from the default source while the user was **in a car**, despite being told via a mid-run
  steer to use already-saved captures. Nothing was committed and the audio was deleted (verified: no
  `*.f32` left in `/tmp/probe31`, no audio tracked in git) — but an agent asked to *"measure the real
  device"* reads that as licence to record. Put the prohibition in the **first** brief, and say
  plainly that the audio is the user's private data.
- **Long agent runs die on connection errors, and context dies with them.** Many runs were lost
  (600–1760 s). Mitigations: (a) tell long-running agents to **write their artifact to disk
  incrementally**, appending after each check, so a death costs at most one check; (b) `resume`
  preserves context but agents get **cleaned up and become unresumable** — then dispatch fresh,
  scoped to extend; (c) implementers must **commit after each working step**, so a death cannot lose
  work. This paid off repeatedly: ticket 25's module, tests and wiring all survived separate deaths.
- **When a lot of agents are in flight, check for file collisions before dispatching.** Tickets 16
  and 18 both edit `run_service`/`pipeline.py`; 18 was deliberately held until 16 merged. A **code**
  conflict between two agents' implementations is not something to adjudicate in a hurry.

## 8. Merge procedure (and the mistake to avoid)

1. Confirm the review says **pass** and the ticket is `in review`.
2. `git merge --no-ff feat/<slug> -m "Merge ticket <n>: <slug> — verdict pass (NNN-review.md)."`
   Run this as **its own command**, then check `git diff --name-only --diff-filter=U`.
3. **`README.md` usually conflicts — resolve mechanically:** keep the correct status for **every row
   on both sides** (already-merged tickets stay `done`; the branch's own row becomes `in review`).
   Branches are often cut before later merges, so their rows for *other* tickets are stale — never
   take a branch's row for someone else's ticket.
4. Flip `ticket.md` → `done`, update the board row, commit on `main`.
5. `git worktree remove --force .worktrees/<slug> && git branch -d feat/<slug>`.
6. Re-verify on `main`: `uv run pytest -q`, `rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests`,
   the link check (below), and `git status`.

**⚠️ Two ways this has been got wrong — both committed a `README.md` containing conflict markers.**

1. *Chaining.* The merge conflicted, the follow-up commands on later shell lines still ran, and the
   markers got committed. Hence rule 2.
2. *Interleaving tool calls.* Issuing the marker-resolving `edit` and the `git add` in the **same
   tool block** let them interleave: `git add` staged the file *before* the edit landed, so the
   commit captured the **unresolved** version while the working tree looked correct — and the check
   passed because it grepped the working tree, not the index. **Never put `git add`/`git commit` in
   the same block as an `edit`.**

**Verify the committed object, not the working tree.** After any merge: `git grep -n '^<<<<<<<' HEAD -- docs`
must be empty, and `git rev-list --parents -n1 HEAD` must show **2 parents**. A clean `git status`
proves nothing if the index was staged at the wrong moment. If it slips through,
`git commit --amend --no-edit` repairs a merge at the tip and preserves both parents.

**Link check** (run after any docs change; must report 0). The negative lookbehind also excludes `.`
because an **ellipsis** in prose (``.../001-comment.md``) contains `../` and a naive pattern flags it
as a broken link — that false alarm cost time once:

```bash
python3 - <<'PY'
import re, pathlib
tok = re.compile(r'(?<![\w/`.])((?:\.\./)+[\w./-]+?\.md)')
bad=[(str(f), m.group(1)) for f in sorted(pathlib.Path("docs").rglob("*.md"))
     for m in tok.finditer(f.read_text()) if not (f.parent / m.group(1)).resolve().exists()]
print(f"{len(bad)} broken references"); [print(" ", f, t) for f, t in bad]
PY
```

## 9. The injection saga — RESOLVED, and confirmed in live use

**Discovery:** the user's manual verification — not any test — found `ydotool type` **corrupts text on
a non-US layout**: on `hu`, `0`→`ö`, `-`→`ü`, `y`→`z`. Root cause: `ydotool` resolves text via a
**hardcoded US-position table** and silently drops non-ASCII. **Unfixable by config.** All 274 tests
passed while it was broken, because the fake runner asserts the payload reaches `ydotool`'s stdin —
which it does. Exactly what ADR 0014's manual-verification carve-out exists for.

**Decisions (ADR 0020 + amended ADR 0003):** keep `ydotool` as transport but **never call
`ydotool type`**; map characters ourselves via **libxkbcommon** (ctypes, no new dependency) against
the **live compositor keymap** from `xkbcli dump-keymap-wayland`; emit via a **batched
`ydotool key`**; **skip-and-warn** on unreachable codepoints; make **clipboard-paste a configured
alternative**, never an automatic fallback. `wtype` is a dead end (KWin lacks
`zwp_virtual_keyboard_v1`); `dotool` is AUR-only.

**The measurement (ticket 23) — the gate resolved in our favour.** 125-char phrase, byte-compared
against the capture:

| delay ms | wall ms | ms/char | byte-perfect | within ADR 0001 budget |
|---:|---:|---:|:--:|:--|
| 12 | 3100.7 | 24.81 | yes | **no** |
| 6 | 1595.4 | 12.76 | yes | **no** |
| **0** | **47.9** | **0.38** | **yes** | **yes** |

~104× faster than the ~4.99 s baseline, meeting the budget 20×. So **latency work is finished**, and
the option to type committed text *during* dictation was **NOT built** — ADR 0011's "nothing is
injected until release" stands unchanged. The cost is linear (`delay × 2 × 126` events — `ydotool key`
sleeps once per event, *down and up*), so any non-zero delay reintroduces 252 sleeps; that is why
**ticket 28 set the shipped default to `0`** (at `12` the product would have shipped **over its own
budget**). Honest caveats: the capture target was a **pty**, not a real editor, and the numbers are
not independently reproducible (captures deleted) — the reviewer audited the evidence and recomputed
the md5 and the linear model rather than re-running it.

**⚠️ One keycode trap, already fixed:** the reverse lookup originally matched on
`xkb_keysym_to_utf32` equality, which let **keypad/`XF86Numeric` aliases** win — they need no
modifier, so they outranked the real key. On a keyboard without a numpad that means `= / + * # .`
and **every digit on `fr`** typed **nothing**. Fixed by ranking canonical keysyms ahead of aliases
(not rejecting aliases — `de`'s `…′″` need them). Post-fix: **0 disagreements against
`xkbcli how-to-type` across 5 keymaps** (plus a 432-char sweep at 0).

**User-confirmed in live use:** `hu` mapping correct by voice, including `y`/`z` and digits — the
exact characters both keymap bugs broke.

## 10. The silence saga — three attempts, and the biggest lesson in the project

The user toggled recording **on and off without speaking** and got **`thank you`** typed. Three
attempts, each overturning the last explanation:

1. **Ticket 29 attempt 1 — "near-silent audio hallucinates".** Wrong. Digital silence and low-level
   noise return `''` at every level. The real synthetic triggers are **flat DC**, **50 Hz hum**, and
   **short transients** (one 5 ms click → `'Thank you.'`).
2. **Ticket 29 attempt 2 — a whole-buffer RMS floor.** Fixed DC and the accidental double-tap but
   the reviewer **did the arithmetic** and showed a realistic silent toggle (which contains the
   **hotkey's own key clicks**) measures 2.6–6.5× the threshold — it fixed only inputs that never
   failed. Replaced by a **frame-level sustained-energy gate** (a click is 1–2 × 20 ms frames; speech
   is many; bar = 3 consecutive frames). That gate **is merged** and is what ships today. Its review's
   verdict is the honest framing: **"merge the code; do not call it 'silence solved'"**.
3. **Ticket 31 (`feat/speech-gate-recalibration`) — PARKED, unmerged.** The user *still* got
   `Thank you.`, so a dedicated diagnostic ran. **Not a stale service, not mains hum, not command
   output** (that hunch was tested and ruled out: the string is provably the engine's return value,
   traced `Service` → session → engine → injector). The cause: **the user's "silence" is not silent.**
   Twenty-two real captures measure **AC RMS 0.036–0.26 — as loud as the speech clips
   (0.047–0.14)** — because the device emits a **broadband ≈20–25 Hz drift at amplitude ≈0.04**. So
   the amplitude gate correctly classifies it as speech and the model hallucinates on it.
   Ticket 31 replaces amplitude with a **speech-band energy ratio** (fraction of AC energy above
   200 Hz ≥ 0.15): idle measures **0.001–0.065**, speech **0.762–0.980** — an order of magnitude
   apart, and **level-invariant**. It also *measured and rejected* the simpler fix (a steeper
   high-pass still passed idle: 12–122 active frames at 300 Hz).
   **Why it is not merged:** the user asked to pause it, and **their situation explained the whole
   mystery** — they were **in a car with the window open**, i.e. wind and road rumble are almost
   certainly the "20–25 Hz drift". So the failure is **environment-dependent** (in a quiet room the
   original gate probably worked all along), *and* the new gate's measured cost (quiet speech needs
   gain ≥ 0.4 where the engine reads down to 0.1) was measured **with that noise present** — in the
   very environment where they dictate. Merging it could **drop real dictation**, which is worse than
   the bug. **Absence of absolute silence is not a use case they have.**

**The review then sharpened the cost with its own fresh measurement, and the park decision came out
stronger, not weaker.** Independent real captures: idle ratio **0.0031–0.0061** vs speech
**0.762–0.980** — a **24.6× / 5.1×** margin around the 0.15 threshold, so the *rejection* side is
safer than the ticket claimed, and idle is gated with a **counted zero engine calls** (the *old* gate
on the same real idle makes the engine emit `'you'`, `"I'm not going to do anything."`,
`"¿Qué estás buscando?"` — i.e. it types garbage on every silent toggle in this environment). **But**
the reviewer's floor measured **0.28–0.35 RMS — 6–8× the ticket's 0.043-RMS floor** — and the
keep-boundary is **noise-relative**: the gate needs speech RMS ≥ ~0.47 × the noise RMS (≈ −6.5 dB
SNR), so on today's floor it keeps nothing below **gain ≈ 4.0 (speech RMS ≈ 0.21)** while the
**engine still returns near-correct text at gain 0.5 (RMS 0.027)** — roughly **8× more conservative
than the engine**. In the car, quiet speech the engine can read **is** gated. The ticket's cost table
is correct *for the floor it used*; it simply never said the boundary moves with the noise.

**Verdict shape: mergeable, but the noisy-environment trade is not settled by offline data.** It
needs the user's live test in **both** a quiet room and a noisy one. If their real quiet speech fails
it, the options are to lower `SPEECH_BAND_RATIO` (accepting more hallucination on noise) or to fix
the noise **at the input** (PipeWire high-pass, another device, disable AGC/ALC) — **neither to be
built speculatively.**

### ⚠️ THE LESSON — put this first when briefing any future audio/signal work

**"Verified against constructed inputs" is not "works on real inputs".** Every gate threshold above
was derived from **synthetic** signals — digital silence, synthesised clicks, a 50 Hz tone — and
reviewed rigorously **against those synthetic claims**. Nobody fed the **actual device's** audio
through the gate until the user toggled it. Two rounds of well-evidenced, correctly-reviewed work
aimed at the wrong mechanism, and the fix only became real when a diagnostic **measured the real
captures**.

**Corollary, and it applies far beyond audio:** *"this ticket is rigorous" is not the same as "this
ticket fixes the reported symptom."* When a ticket exists to fix a user-visible symptom, the
acceptance test must include **the user's actual case**, measured — not a synthetic stand-in for it.

## 11. What the user has personally verified (do not re-litigate)

Recorded in `docs/project/core-dictation-pipeline/dictation-pipeline-batch/003-comment.md`:

- End-to-end dictation into an editor and a terminal — **pass**
- **`hu` mapping correct** (`y`/`z`, digits) — **pass** (the workstream's whole point)
- `ges2t cancel` mid-recording, tray `Quit` (→ `SIGTERM`) — **pass**
- Long phrases (20+ words) — **pass**
- **Quiet and mumbled speech transcribes**; imperfect model output on mumbled speech is **accepted**
  (a ~16 MB model; "good enough" is the bar)
- **Silence → `thank you`** — the bug in §10, still open as far as the shipped code goes
- `ges2t doctor` runs and works — **pass**
- Tray icon visible in Plasma and state stepping — **pass** (check the **overflow "⌃" area**; Plasma
  collapses new tray icons by default, so a missing icon may just be hidden)
- **Their verification was on the BATCH session** — streaming became the default in ticket 16
  *afterwards*, so the basics were worth a repeat pass on the streaming path (partials now show in
  the tray tooltip, a new visible behaviour).

## 12. User preferences and escalation rules

- **Tickets must be small enough for a capable junior developer in one session.** Split anything
  that is really two tickets.
- **Type hints everywhere** (including `-> None`), **interface-typed signatures** (Protocols in
  parameters, not just inheritance), **classes instead of positional tuples** — ADR 0019, enforced
  with `mypy src/ges2t tests`.
- **The user wants explanations, not just status**: when something changes the plan, explain *why*
  and what it costs. They push back well and have corrected the orchestrator's framing repeatedly —
  welcome it.
- **They escalate on ADRs:** amendments must be surfaced explicitly with measured evidence, never
  edited quietly. Ticket 30 (a new socket verb) **requires an ADR 0016 amendment** — that is flagged
  in the ticket, not slipped in.
- **They are not precious about scope**: they have repeatedly said not to get stuck, and to ship
  what is good enough. When they say a thing is not a use case, believe them and park it — with the
  reasoning written down so it is not silently reopened.
- Verified decisions: Python MVP; Wayland-only; hotkey via compositor-bound CLI command; `ydotool`
  injection; TOML config at `~/.config/ges2t/config.toml` (generic layer + engine selector +
  engine-specific blocks); streaming **is** v1; no post-processing except one trailing space; tray via
  `pystray`'s **AppIndicator** backend with `--system-site-packages`; `ges2t setup` is the **only**
  networked path; closed-engine risk accepted; generic configurable keymap (never hardcoded `hu`);
  injection delay default `0`; **minimum recording duration 100 ms** (their specified value).

## 13. Remaining work and order

**Ticket 19 (docs + setup guide) is the last core ticket and goes last, on purpose** — it documents
final behaviour, and writing it earlier means documenting behaviour that then changes.

```
25 (in review) ─→ merge
31 (reviewed, PARKED — do not merge without the user's quiet-room + noisy-environment result)

19 (docs)      — needs everything else settled first
30 (tray diagnostics) — optional, P2; needs the ADR 0016 amendment
```

**Epic 1's exit test** — `ges2t toggle` → speak → `ges2t toggle` types correct text into a **text
editor** and a **terminal**; silence types nothing; framework overhead < 1 s excluding model time;
correct characters on a non-US layout. **The user has run essentially all of this and it passes
except the silence case** (§10). The remaining honest gap to "I use it daily" is the silence
hallucination on a noisy input, which is parked at the user's direction.
