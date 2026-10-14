# 003-review — ticket 53 (focused re-review of F1 fix)

- **Reviewer:** independent (same reviewer as `002-review.md`; no implementation, report, comment or
  ticket edits made by me)
- **Date:** 2026-10-14
- **Branch reviewed:** `feat/parakeet-v3-engine-landscape`
- **Commit under review:** `1724d9a` (parent `f761206` — my `002-review.md`)
- **Verdict:** **pass** — F1 is fully resolved. The single blocking finding from `002-review.md` is
  closed, the correction is applied in both places it appeared, no central claim was altered, and the
  PROCEED gate is intact.

---

## What the fix claims, and what I verified

The commit message says: "REPORT.md §7 and 001-comment.md wrongly said ninja was already present …
From-source escalation line is now `sudo pacman -S ninja sentencepiece`. The recommended
prebuilt-archive path still needs no build tooling and no sudo. Also fixed the `ENGINE_NEMO_SPEECH`
constant spelling and ticked the ticket DoD checkboxes." I verified each independently.

### 1. The new on-machine block matches reality

```
$ command -v ninja ; echo "exit=$?"
(absent)              exit=1
$ pacman -Q ninja sentencepiece
error: package 'ninja' was not found
error: package 'sentencepiece' was not found
$ command -v cmake glslc
/usr/bin/cmake
/usr/bin/glslc
$ pacman -Q cmake shaderc vulkan-headers spirv-headers
cmake 4.4.4-1
shaderc 2026.3-1
vulkan-headers 1:1.4.363.0-1
spirv-headers 1:1.4.363.0-1
```
REPORT.md §7 (lines 414–419) now reproduces exactly this:

```
$ pacman -Q cmake shaderc vulkan-headers spirv-headers
$   cmake 4.4.4-1   shaderc 2026.3-1   vulkan-headers 1:1.4.363.0-1   spirv-headers 1:1.4.363.0-1
$ command -v glslc → /usr/bin/glslc
$ command -v ninja → (not found)
$ pacman -Q ninja sentencepiece
$   error: package 'ninja' was not found
$   error: package 'sentencepiece' was not found
```
**Match, byte-for-byte on the meaningful tokens.** `ninja` and `sentencepiece` are absent;
`cmake`, `glslc`, `shaderc`, `vulkan-headers`, `spirv-headers` are present. (I re-derived all of this
myself; I did not take the block on trust.)

### 2. Escalation line correct, and the prebuilt/no-sudo distinction preserved

REPORT.md §7 (lines 422–430):
> A **from-source** Vulkan build additionally needs **`ninja`** and **`sentencepiece` (dev)** — *both
> verified absent on this machine* … So the exact from-source escalation line is
> **`sudo pacman -S ninja sentencepiece`** (`cmake`, `glslc`/`shaderc`, and the Vulkan/SPIR-V headers
> are already present and are *not* in that line). **The recommended prebuilt-archive path needs none
> of this — no build tooling and no `sudo`** — so ticket 54 needs **no** new package as long as it uses
> the release binary.

`001-comment.md` (lines 41–45):
> **No `sudo`/package is required** for the recommended path (prebuilt Vulkan binary). Only a
> *from-source* fallback needs **`sudo pacman -S ninja sentencepiece`** — both **`ninja` and
> `sentencepiece` are absent** here (verified …); `cmake` (4.4.4), `glslc` (shaderc 2026.3), and the
> Vulkan/SPIR-V headers **are** present and are not in that line.

→ The escalation string is **exactly `sudo pacman -S ninja sentencepiece`**, and the
prebuilt-path-needs-no-build-tooling-and-no-`sudo` distinction is **explicitly retained in both
files**. The old self-contradiction ("names ninja in the command, then says ninja is present") is gone.

### 3. Correction applied everywhere; no leftover contradiction

```
$ grep -rn -i "ninja\|sentencepiece\|already present" \
    docs/project/research/parakeet-v3-engine-landscape/ \
    docs/project/parakeet-v3-backend/parakeet-v3-engine-landscape/
REPORT.md:416  $ command -v ninja → (not found)                    # new block
REPORT.md:417  $ pacman -Q ninja sentencepiece                     # new block
REPORT.md:418  $   error: package 'ninja' was not found            # new block
REPORT.md:419  $   error: package 'sentencepiece' was not found    # new block
REPORT.md:424–428  "both verified absent … sudo pacman -S ninja sentencepiece … already present
                   and are NOT in that line"                       # corrected prose
001-comment.md:42–44  "sudo pacman -S ninja sentencepiece … both absent … are present"  # corrected
002-review.md:*  (my prior review quoting the OLD, now-superseded text — historical record only)
```
The only surviving instances of the old "already present" wording are **inside `002-review.md`**, which
correctly quotes the pre-fix text to document the finding. `REPORT.md:142` and `REPORT.md:362` still
list **Ninja** as a from-source prerequisite for `setup` to *check* — that is consistent with the fix,
not a contradiction. No stale claim remains in the shipped report or the gate comment.

### 4. Boundaries — only the intended files changed

```
$ git show --stat 1724d9a
 .../parakeet-v3-engine-landscape/001-comment.md  |  7 ++++--
 .../parakeet-v3-engine-landscape/ticket.md       | 10 ++++----
 .../REPORT.md                                    | 27 ++++++++++++++--------
 3 files changed, 28 insertions(+), 16 deletions(-)

$ git diff main...HEAD --name-only -- src/ tests/   # (empty)
$ git diff main...HEAD --name-only -- docs/adr/     # (empty)
$ git diff main...HEAD --name-only | grep -iE '\.(gguf|onnx|bin|so|wav|mp3|flac|nemo|safetensors|pt|ckpt|tar|gz|zip)$'   # (none)
$ git status --porcelain                            # (empty)
```
The full `git show 1724d9a` diff touches exactly: REPORT.md §6 (one constant name), REPORT.md §7
(machine block + escalation prose), REPORT.md §7 `free -g` line, `001-comment.md` escalation bullet,
and `ticket.md` DoD checkboxes. **No central claim was altered.** I diffed the numeric/licence lines
between the report at `d60e057` and at HEAD — the only hit was the new escalation sentence, never a
size, sha256, revision, licence or recommendation:

```
$ git diff d60e057..HEAD -- REPORT.md | grep -E '^[+-]' | grep -iE 'sha256|e3880d0a|c8d0ed5b|713|16,618|15.72|742|OpenMDW|CC-BY|541d1f99|recommend'
+already present and are *not* in that line). **The recommended prebuilt-archive path needs none of
```
Re-grepped REPORT.md at HEAD: `16,618,142`, `c8d0ed5b…aee6`, `713,975,456`, `e3880d0aaa…d32e`,
`541d1f99…`, `15.72`, `742,090,464`, `CC-BY-4.0`, `OpenMDW-1.1` all present and unchanged.
`001-comment.md` still reads `**Verdict: PROCEED** …` and `**Safe to relay?** Yes for the
engine/Vulkan/dependency findings`. Ticket status and README row remain `in review` (not `done`).

### 5. DoD checkboxes are genuinely satisfied

The five boxes are now all `- [x]`. I re-ran the evidence under each:

| Ticked box | Re-verified |
|---|---|
| `001-comment.md` states the gate decision + deciding facts + escalation | `PROCEED`, three deciding facts, escalation (licence, A/B/C model choice, from-source `pacman` line) — present and now correct |
| No file under `src/ges2t/` or `tests/` modified | `git diff main...HEAD -- src/ tests/` → empty |
| No engine/model/weight/audio committed; scratch outside repo, path named | binary-name grep → none; scratch `~/.cache/ges2t-discovery/parakeet-v3-engine-landscape/` named in report header/§7/§11 and holds only text notes |
| Link check 0 broken; `git status` clean | reproduction of the orchestrator-state §10 snippet → `0 broken references`; `git status --porcelain` empty |
| Everything committed on the branch; ticket status + README row updated | commit `1724d9a` on `feat/parakeet-v3-engine-landscape`; `ticket.md` `in review`; README row `in review` |

**Every ticked box is genuinely satisfied.**

---

## Residual risk

- **The central open question is unchanged and still ticket 54's job:** whether the prebuilt Vulkan
  binary actually offloads the *offline* Parakeet-v3 TDT graph on RADV / avoids CPU-fallback ops.
  The report marks this "Yes (expected)" and flags `check_backend_coverage` + a CPU-vs-Vulkan timing;
  that is honest and was not weakened by this fix.
- **`free -g`** is live; the report now says "Mem: 27 total, ~16–17 GiB available (live; re-read 17
  here, 16 in review)" — accurate and self-consistent with the ephemeral nature.
- **Minor incompleteness (non-blocking):** `REPORT.md:372` lists parakeet.cpp's from-source burden as
  "CMake + ggml-Vulkan + SentencePiece" without naming Ninja. It is not a false claim (the correct
  prerequisite list is given in §7), so no action required.
- **`ENGINE_NEMO_SPEECH`** spelling fixed from the earlier `ENGINE_NEEMO_SPEECH` — matches the
  `identifiers.py` convention.

## Could-not-verify

- Same residual unknowns as `002-review.md` §"Could-not-verify": on-device Vulkan offload for the
  offline TDT graph, `check_backend_coverage` output, exact NeMo/Transformers dependency trees,
  archive self-containedness, and parakeet.cpp/CrispASR runtime parity claims. All remain ticket-54 /
  out-of-scope items and none is affected by this fix.

## What would falsify this verdict / safe to relay

This "pass" would be falsified if the corrected §7 block did not match the machine (it does), if the
from-source line were wrong (it is exactly `sudo pacman -S ninja sentencepiece`), if any central
size/sha256/licence had moved (none did), or if the prebuilt/no-sudo distinction had been lost (it is
stated in both files). **Safe to relay?** Yes — the engine/Vulkan/dependency/streaming findings remain
documentary + device inventory and are now free of the one false machine-state claim; no *measured*
Vulkan/accuracy number exists (ticket 54 produces those).
