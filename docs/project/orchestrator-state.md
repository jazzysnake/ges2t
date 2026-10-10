# Orchestrator state — resumption point

> **What this is.** Working state for the orchestrating agent, not a project deliverable. It exists so a
> compacted or fresh orchestrator can resume without re-deriving anything. Delete it at close-out.
> The authoritative sources are always: `docs/project/README.md` (the board), `docs/adr/` (decisions),
> `docs/code-quality.md` (the rules), and each ticket's `ticket.md` + `NNN-comment.md` + `NNN-review.md`.
> **If this file disagrees with the board, the board wins.**

## 1. Project in one paragraph

**ges2t** (*good enough speech 2 text*) — a minimal, fully offline Linux speech-to-text tool.
Hotkey → speak → the transcription is typed into whatever window has focus. Local model, no cloud.
Python + `uv`, `src/` layout, Wayland-only, **KDE Plasma 6 on Arch is the reference target**. It now has
**two backends**: the original Cactus **Whistle** model on **Needle 3** (default; streams, types
incrementally) and **whisper.cpp** (opt-in, batch-only, notably more accurate). Packaging is out of scope
(ADR 0000). Original brief: `.pi/prompts/orchestrate-project.md`.

## 2. Orchestrator role and hard rules

- **Never implement.** Planning artifacts under `docs/` and git merges only. All implementation and all
  review is done by subagents. Single exception: an explicit user request.
- **Dispatch the project's role agents** (`.pi/agents/`, created 2026-10-10):
  - **`ges2t-implementer`** — one ticket, own worktree/branch, ADR 0019 style, real verify commands,
    machine-safety prohibitions in the body.
  - **`ges2t-reviewer`** — independent; falsifies claims and tests; `NNN-review.md` with evidence, risks,
    could-not-verify, and \*what would falsify\*.
  - **`ges2t-merger`** — the only agent that writes to `main`; merge, row-wise README resolution,
    status flip, cleanup, re-verify. **You still own the merge decision.**
  - **`ges2t-discovery`** — research/measurement with a durable report, no `src/` change, no `sudo`, no mic.
  All four pin `model: deepseek/deepseek-flash` and `isolation: off`. `.pi/subagents.json` sets
  `fallbackSubagent: false`, so a **misspelled agent type fails closed** rather than silently running
  `general-purpose`.
- **The code-quality rules are a skill** (`.pi/skills/code-quality/SKILL.md`), **preloaded into the
  implementer and reviewer system prompts** via `skills: code-quality`. Do not restate them in briefs.
  Human-readable pointer: `docs/code-quality.md`.
- **You are the merge authority.** Only you merge into `main`, only after a review passes.
- **Escalate to the user** on blocked tickets, scope changes, destructive/consequential actions, and
  anything that invalidates an accepted ADR. **An ADR amendment is presented in full for explicit
  sign-off before the merge that carries it.**
- **Ask before anything externally visible**: keystrokes, the clipboard, a desktop notification,
  `sudo`, installing packages, stopping/starting a service, **recording from the microphone**, **opening
  any window on the user's screen**.
- **Verify before relaying, not just before merging.** A subagent's summary describes intent, not
  outcome. Read the artifact.

## 3. Environment (verified on this machine)

| Fact | Value |
|---|---|
| OS / session | Arch Linux, KDE **Plasma 6.7.5**, Wayland (`kwin_wayland`) |
| Keyboard layout | **`hu` (Hungarian)** — resolved from the live compositor keymap |
| Python / uv | **3.14.7** at `/usr/bin/python3`; `uv` 0.12.23 |
| Venv requirement | **`uv venv --system-site-packages --python /usr/bin/python3` THEN `uv sync`.** A bare `uv sync` creates a venv with `include-system-site-packages = false` and picks uv's managed 3.13, silently losing `gi` (astral-sh/uv#5737) |
| `ydotoold` | **active AND enabled**; socket `/run/user/1000/.ydotool_socket`; unit `ydotool.service` |
| `/dev/uinput` | writable by the user |
| Install | **editable** → merged code is live; **`ges2t` is NOT on `PATH`** (use `uv run ges2t`) |
| Clipboard | `wl-copy`/`wl-paste` (wl-clipboard 2.3.0); **`wl-copy` forks by default** |
| Not installed | `wtype`, `dotool`, `numpy` (numpy deliberately absent — never add it) |
| GPU | **AMD Radeon 780M (RDNA3)**, Vulkan working (`RADV`); dev packages `vulkan-headers`, `spirv-headers`, `shaderc`, `cmake` **all installed** |
| **The user's config** | `~/.config/ges2t/config.toml`: `injection.backend = "ydotool"`, `[streaming] enabled = false`, **`transcriber.engine = "whisper_cpp"`** |
| Whisper data | engine `~/.local/share/ges2t/engine/whisper-cpp/v1.9.5/` (**`variant: vulkan`**); models `~/.local/share/ges2t/models/whisper-cpp/<name>/` (`medium-q5_0` acquired, 539 MB) |
| Git remote | **`origin` = `git@github.com:jazzysnake/ges2t.git`** (ssh). The repo is **public** (set 2026-10-10); `main` tracks `origin/main`. **The merger agent never pushes** (no network, by design) — the orchestrator pushes after a verified merge (§10). No `LICENSE` yet. |
| Baselines on `main` | **`uv run pytest` → 1230 passed**; **`uv run mypy src/ges2t tests` → 0 errors (65 files)**; `doctor` exit 0 |

## 4. Board state — 4 epics, 22 ADRs (0000–0021)

**`main` is the only branch and the only worktree** (check `git log -1` for the current tip — do not trust a
SHA written here). All of Epics 1–3 and Epic 4's ticket 48 are **done**.

**Open tickets (the whole remaining board):**

| # | Ticket | State |
|---|---|---|
| **52** | `code-quality/command-verb-identifiers` | **to do, XS** — the socket command-verb family as constants (ticket 48's honestly-scoped-out residual; `"toggle"` is a second spelling of `HOTKEY_MODE_TOGGLE`). Rule 1 again. |
| **35** | `live-dictation-ux/streaming-vs-batch-quality` | **to do, P2, gates nothing** — the three-length streaming-vs-batch sweep (§8) |
| **30** | `core-dictation-pipeline/tray-failure-diagnostics` | **to do** — needs a **read-only socket verb → an ADR 0016 amendment**, not yet raised for sign-off |

**Closed as obsolete:** **31** `speech-gate-recalibration` — measured and rejected in the user's
environment; the parked branch was deleted and its `001-comment.md`/`002-review.md` are kept in the ticket
folder. See §7.

## 5. The whisper.cpp backend (Epic 3, merged 41–47, 49–51; ADR 0021 **accepted**)

- **Opt-in second backend**: `transcriber.engine = "whisper_cpp"` (alias `"whisper.cpp"` accepted); the
  **default stays `whistle`**, so live/incremental typing is unchanged. Choosing whisper **trades away
  incremental typing** — it is **batch-only** (text only when the utterance ends), because its
  partial-window output is not stable enough for the commit-cadence loop (seam duplication, cut-off and
  silence hallucination, 2.3–4.8× worse streaming WER — the `whisper-cpp-streaming` research).
- **In-process `ctypes`** over `libwhisper.so`; the model is **loaded once at service start** and kept
  resident (~0.8–1 GB). No per-utterance subprocess or reload.
- **`-nt` + greedy pinned in code** — `-nt` changes the *decode* (0.062 vs 0.156 WER on the reference
  clip); never inherit the CLI default.
- **Default model**: `medium-q5_0` where Vulkan is available, `small-q8_0` on CPU-only. (`small-q5_0` is
  **not published upstream** — only `q8_0` for all sizes, `q5_0` for `medium`/`large-v3-turbo`; never
  `f16`.)
- **`ges2t setup [backend] [model]`** — no args = `needle` + `whistle`; `setup whisper.cpp <model>`
  **builds** whisper.cpp (pinned `v1.9.5`, commit `d1be6fd…`) and fetches the model from Hugging Face,
  sha256 + size pinned. Setup **requires, never installs** its prerequisites and fails with the exact
  `pacman` line. The needle model is selectable too.
- **Variant-aware rebuild** (ticket 49): re-running `setup` **upgrades a CPU-only engine to Vulkan** once
  the dev package is present, respecting `[engine.whisper_cpp] gpu`; `--rebuild` forces it.
- **Strict startup validation**: with `whisper_cpp`, a config is refused when `streaming.enabled` is
  **explicitly** `true`, when the model is not acquired, or when the library is not built. An **omitted**
  `streaming.enabled` resolves to `false` for a batch-only engine (ADR 0021 §8, amended with sign-off).
  **Model-vs-GPU sizing is deliberately not checked.**
- **Measured** (reference 25 s car clip, `medium-q5_0` Vulkan): **WER 0.062 vs Whistle 0.281/0.391**,
  ~1.7 s/25 s. Evidence: `docs/project/research/whisper-cpp-provider/` and `.../whisper-cpp-streaming/`.
- **Status: the user is using it daily and reports it fast and accurate.**

## 6. Code quality (Epic 4)

- **`48` centralized the identifier strings** compared in `src/` into the leaf module
  `src/ges2t/identifiers.py` (values byte-identical; `doctor.py`'s duplicate constant deleted). **`52`**
  finishes the job for the command-verb family.
- The rules live in **`.pi/skills/code-quality/SKILL.md`**, preloaded into the implementer and reviewer.
  They are the bar for every change; where they and an ADR disagree, **the ADR wins**.

## 7. Ticket 31 — why obsolete (do not silently reopen)

The recalibration is **measured and rejected in the user's environment**: its keep-boundary is
**noise-relative**, so in the car/noisy setting the user dictates in it could **drop real dictation**
(~8× more conservative than the engine), and **true silence is not an input they have**. The shipped
ticket-29 gate is retained. If silence ever becomes a real failure again, that is a **new** ticket with
the environment stated — not a reopening.

## 8. The accuracy question (ticket 35, still open)

The user's live concern is accuracy. Ticket 39 measured four levers on their 25 s recording: a noise
filter is **not** justified; clipping is not the cause; the residual errors are the **16 MB model's
acoustic confusions** (ADR 0005's accepted risk). **Streaming vs batch was the largest lever on that one
recording (0.391 vs 0.281 by `word_diff`), but ADR 0011's clean-clip measurement found the windowed path
*ahead* (0.176 vs 0.191)** — so the direction is **not settled** and must not be relayed as "switch to
batch". **Ticket 35's multi-length sweep decides it.** The whisper backend (§5) is now the concrete
answer to the same question — and the user has adopted it.

## 9. Workflow discipline (the protocol that works)

**Implementer brief:** the ticket path; "read the ticket and every artifact it links first"; worktree +
branch (`git worktree add .worktrees/<slug> -b feat/<slug>`; **never commit to `main`**; **do not remove
the worktree**); the ticket-specific rules; the exact verify commands; a `NNN-comment.md` (next free
number — **never renumber or rewrite**); status → `in review` (or `blocked`); **only its own board row**;
**commit after each working step**; report real output. Long runs write the artifact **incrementally**.

**Reviewer brief:** read the ticket, epic, ADRs, and **every** comment; run the checks (**do not trust
ticked boxes**); **do not touch the implementation or the tests**; `NNN-review.md` with verdict /
per-criterion findings / **evidence** / risks / an explicit **could-not-verify** list; **state what would
falsify the central claim**; hand back an explicit **"safe to relay?"** for a measurement. The skill's
rules (falsify every test, the two caching traps `rm -rf .mypy_cache __pycache__`, real seams, hermetic
tests, no unbounded waits) are the checklist.

**Enforcement patterns:** prove invariants mechanically, not by reading; a check that cannot fail is
worse than no check; a fake modelling a simpler contract than the real binary is not a test of the seam;
a fix can introduce its own regression — **re-review the new paths, not just the finding**.

**On changes requested:** resume the *same* implementer, then re-review. If it is gone ("agent not
found"), dispatch a **fresh** agent scoped to *"verify and extend the existing work, never rewrite it"*.

**Long agents die on connection errors and their context dies with them** — that is why artifacts are
written incrementally and implementers commit per step.

## 10. Merge procedure

1. Confirm the review says **pass** and the ticket is `in review`.
2. `git merge --no-ff feat/<slug> -m "Merge ticket <n>: <slug> — verdict pass (NNNN-review.md). …"` **as
   its own command**, then check `git diff --name-only --diff-filter=U`.
3. **`README.md` conflicts row-wise** (branches are cut before later merges, so their other rows are
   stale — **never** take a branch's row for someone else's ticket).
4. Flip `ticket.md` → `done`, update the row with the merge SHA, commit on `main`.
5. `git worktree remove --force .worktrees/<slug> && git branch -d feat/<slug>`.
6. **Verify the committed object, not the working tree:** `git show HEAD:docs/project/README.md |
   grep -c '^<<<<<<<'` = 0 and `git rev-list --parents -n1 <merge>` shows **2 parents**. Then re-verify
   `main`: `uv run pytest -q`, `mypy src/ges2t tests`, the docs link check, `git status`.
   **Never put `git add`/`git commit` in the same tool block as an `edit`.**
7. **Push**: `git push origin main` (and push the bookkeeping commit). The **merger agent is forbidden to
   push** — the orchestrator pushes **after** the merge is verified, so `origin/main` never leads `main`.

**Link check** (after any docs change; must report 0 — the negative lookbehind also excludes `.` so an
ellipsis in prose is not flagged):

```bash
python3 - <<'PY'
import re, pathlib
tok = re.compile(r'(?<![\w/`.])((?:\.\./)+[\w./-]+?\.md)')
bad=[(str(f), m.group(1)) for f in sorted(pathlib.Path("docs").rglob("*.md"))
     for m in tok.finditer(f.read_text()) if not (f.parent / m.group(1)).resolve().exists()]
print(f"{len(bad)} broken references"); [print(" ", f, t) for f, t in bad]
PY
```

## 11. User preferences and escalation rules

- **Tickets must be small enough for a capable junior developer in one session.** Split anything that is
  really two tickets.
- **Type hints everywhere**, **interface-typed signatures** (Protocols in parameters), **classes instead
  of tuples** — ADR 0019, enforced with `mypy src/ges2t tests`.
- **The user wants explanations, not just status** — *why*, and what it costs. They push back well and
  make good design calls (the `ctrl+shift+v` default, the `doctor` resolved-path fix, the AMD/`spirv`
  hint, the "tests keep literals" refinement). Welcome it.
- **They escalate on ADRs**: amendments are surfaced in full with evidence, never edited quietly.
- **They are not precious about scope**: ship what is good enough; when they say a thing is not a use
  case, believe them and park it with the reasoning written down so it is not silently reopened
  (ticket 31, §7).
- **They dislike duplication and magic values**: one definition per identifier, one spelling per thing,
  one source of truth per message — see the skill.
- **Nothing is dispatched without the user's go** when it is new work; the role agents above do the work.
