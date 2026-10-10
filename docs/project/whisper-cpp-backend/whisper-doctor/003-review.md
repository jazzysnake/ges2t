# 003 — review: 001-comment correction (scoped re-review of the `002-review.md` blocker)

- **Ticket:** `docs/project/whisper-cpp-backend/whisper-doctor/ticket.md`
- **Branch:** `feat/whisper-doctor`, worktree `.worktrees/whisper-doctor`
- **Reviewed commit:** `0c42b39` (previous review commit `ad01727`)
- **Scope:** the single documentation fix requested in `002-review.md`. No code re-review; the code
  was frozen and is byte-identical (verified below).

## Verdict

**pass** — the documentation blocker is corrected as required, the sweep found no remaining sibling
overclaim in the artifacts, and only the two intended files changed.

**Safe to merge? Yes.** This ticket's code, tests and mypy were already verified and mergeable in
`002-review.md`; `src/` and `tests/` are byte-identical to that reviewed tree, so those
verifications still hold. The one overclaim that remains is inside a frozen `src/` docstring (below)
and is now *recorded* in `003-comment.md` as part of the ticket 46/47 dedupe, so it cannot be lost.
Merge condition unchanged and already stated: **dedupe ticket 47's hint onto
`whisper_engine.vulkan_dev_package_hint` before merging 47.**

## What I verified (real output)

### 1. Only docs changed; `src/`/`tests/` byte-identical to the reviewed tree

```
$ git log --oneline -1
0c42b39 ticket 46: correct the false 46/47 dedupe claim in 001-comment (intent/must-do) + 003-comment

$ git diff --stat ad01727..HEAD -- src tests
(empty)

$ git diff --quiet ad01727..HEAD -- src tests ; echo $?
0                                    # exit 0 = identical, no byte-level change

$ git diff --stat ad01727..HEAD
 .../whisper-doctor/001-comment.md                  | 18 +++--
 .../whisper-doctor/003-comment.md                  | 78 ++++++++++++++++++++++
 2 files changed, 89 insertions(+), 7 deletions(-)

$ git diff --name-status ad01727..HEAD
M       docs/project/whisper-cpp-backend/whisper-doctor/001-comment.md
A       docs/project/whisper-cpp-backend/whisper-doctor/003-comment.md
```

Only `001-comment.md` (modified) and `003-comment.md` (new) changed. `git status --short` on the
worktree is clean.

### 2. The false claim is restated as intent/must-do, not as a held property

`001-comment.md` no longer asserts that ticket 47 reads the helper. The corrected text reads:

> ... This is the **intended** single source for "a GPU runtime is present but a dev package is
> missing, so the build is CPU-only": ticket 47's `setup` hint **must be moved onto it**. It is
> **not shared yet** — 47's branch builds its own `cpu_only_build_advice` / `cpu_only_build_hint`
> in `setup_command.py`, so until that dedupe lands the wording and the fix line can still drift.

and the "Notes for the reviewer" bullet now says:

> **Shared, not duplicated (intended, not yet true).** ... ticket 47's `setup` hint **must be moved
> onto it** rather than re-worded — 47's branch currently has its own `cpu_only_build_advice`, so
> the two can drift until that dedupe lands.

So it (a) no longer claims 47 reads it, (b) labels the helper the **intended** single source, and
(c) says 47 **must be moved onto it**. That is exactly what the `002-review.md` blocker required.

### 3. Sibling sweep — no overclaim of the same kind remains in the artifacts

Searched both `001-comment.md` and `003-comment.md` for siblings (`cannot drift`, `both read`,
`both use`, `single source`, `one wording`, `the same probe`, `the same rule`, `never disagree`,
`is shared`):

```
001-comment.md:40   This is the **intended** single source ... must be moved onto it   # correctly qualified
001-comment.md:81   "... the same rule the service and `setup` use, so doctor never disagrees ..."
001-comment.md:168  **Shared, not duplicated (intended, not yet true).**                # correctly qualified
003-comment.md:14,32,33,43,56   quotes of the old/other claims, in a "Before" / audit framing
003-comment.md:66,67,70         quote of the remaining code docstring, flagged as false
```

- The `001-comment.md:81` model claim ("the same rule the service and `setup` use, so doctor never
  disagrees about which model is in play") is **true** — `002-review.md` verified by instrumentation
  that doctor, startup and `setup` resolve the same model name and file path (`small-q8_0` /
  `medium-q5_0`); kept, correctly.
- Every `003-comment.md` occurrence is a quotation documenting the correction or the flagged code
  issue, not a fresh assertion.
- The plan-section sibling ("the same probe `setup` will use") was also corrected to "the shared
  `inspect_vulkan` probe the engine build uses", which is **true**: the engine build selects
  CPU-vs-Vulkan via `inspect_vulkan` (`whisper_engine.py:1260`,
  `BuildVariant.VULKAN if inspect_vulkan(probe).available else BuildVariant.CPU`), and `check_vulkan`
  uses the same function (`doctor.py:939`).

### 4. The one remaining sibling is in frozen `src/`, and is accurately recorded

`src/ges2t/whisper_engine.py`, `vulkan_dev_package_hint` docstring (lines 816/820), still carries the
same present-tense "already shared" overclaim:

> This is the one wording for the fact both ``ges2t setup`` (ticket 47) and ``ges2t doctor`` (ticket
> 46) surface ... Keeping it here, next to :func:`inspect_vulkan`, means the two commands cannot drift.

`003-comment.md` reports exactly this ("One remaining sibling overclaim, in code (deliberately not
changed)") and says the review instruction freezes `src/`, so it is recorded for the dedupe rather
than edited. I confirmed the docstring is unchanged and the claim is still false on 47's branch:

```
$ grep -rn "vulkan_dev_package_hint\|missing_packages"   (in .worktrees/engine-naming-and-setup-guidance)
(no matches)
$ grep -n "def cpu_only_build_advice\|def cpu_only_build_hint" src/ges2t/setup_command.py
411:def cpu_only_build_advice(
439:def cpu_only_build_hint(
```

Honest and correct: the artifact neither hides nor edits the frozen code sibling. Because the
correction task forbade `src/` edits, leaving it recorded is the right outcome.

### 5. Nothing else changed

`git diff --name-status ad01727..HEAD` lists only the two docs files; the ticket-status/README rows
were already `in review` before this commit and are unchanged in it. No `src/`, test, config or
`docs/project/README.md` change.

## Could-not-verify

- Nothing material. The `src/` code sibling (the `vulkan_dev_package_hint` docstring) cannot be fixed
  within this correction's frozen-`src/` scope; it is verified as still present and correctly
  recorded, which is all this scoped review can require.

## Safe to merge?

**Yes.** The blocker from `002-review.md` is resolved: the artifact now states the 46/47 relationship
as intent/must-do, with no remaining sibling overclaim in the documents. `src/` and `tests/` are
byte-identical to the independently verified tree, so the earlier code verification (`1158 passed`,
`mypy 0/64`, all 29 new test cases falsified, 25-check doctor surface) stands unchanged. Merge 46;
**dedupe ticket 47's `cpu_only_build_advice`/`cpu_only_build_hint` onto
`whisper_engine.vulkan_dev_package_hint` before merging 47**, which will also let the flagged
`whisper_engine.py` docstring be re-worded from past-tense fact to intent.

## Hygiene

No `src/`, test, config or config-file edit; no microphone, clipboard, keystroke, window,
notification, network, `sudo`, package install or service action. The only write is this review
artifact; `git status --short` is clean.
