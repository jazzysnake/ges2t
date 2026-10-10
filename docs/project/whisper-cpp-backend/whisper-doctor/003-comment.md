# 003 — comment: 001-comment correction (false 46/47 dedupe claim)

- **Ticket:** `docs/project/whisper-cpp-backend/whisper-doctor/ticket.md`
- **Branch:** `feat/whisper-doctor`
- **Worktree:** `.worktrees/whisper-doctor`
- **Status:** in review

## Why (the review finding)

`002-review.md` (commit `ad01727`) requested one **documentation-only** change, and verified the code,
tests and mypy as mergeable:

> `001-comment.md` states, as present fact: *"ticket 47's `setup` hint and this ticket's doctor check
> both read it, so they cannot drift."* Ticket 47's branch contains **no reference** to
> `vulkan_dev_package_hint` or `missing_packages`; it builds its own `cpu_only_build_advice` /
> `cpu_only_build_hint` in `setup_command.py`. The claim is false today.

The underlying problem the sentence caused: a doc that asserts a dedupe that has not happened is
exactly what lets a duplicate ship — and merging 46/47 as-is leaves **two independent implementations
of the same GPU-path hint**. The wording was also self-contradictory, since the same file's "Notes
for the reviewer" correctly said 47 *should* call the helper.

## The corrections (001-comment.md only)

No `src/` and no `tests/` change; the correction is prose.

1. **The blocking sentence (restated as intent/must-do).**

   Before:

   > This is the single wording for "a GPU runtime is present but a dev package is missing, so the
   > build is CPU-only" — ticket 47's `setup` hint and this ticket's doctor check both read it, so
   > they cannot drift.

   After:

   > This is the **intended** single source for "a GPU runtime is present but a dev package is
   > missing, so the build is CPU-only": ticket 47's `setup` hint must be moved onto it. It is
   > **not shared yet** — 47's branch builds its own `cpu_only_build_advice` / `cpu_only_build_hint`
   > in `setup_command.py`, so until that dedupe lands the wording and the fix line can still drift.

2. **Sibling overclaim of the same kind (the Plan section).** The plan bullet read
   "the WHY (the missing dev package(s), from the same probe `setup` will use)". Ticket 47 does not
   use this probe for its advice (`cpu_only_build_advice` re-probes via
   `check_prerequisites(BuildVariant.VULKAN, probe)`), so the present-tense "setup will use" was the
   same overclaim. Corrected to "the shared `inspect_vulkan` probe the engine build uses", which is
   true (the engine build picks CPU-vs-Vulkan with `inspect_vulkan`, and `check_vulkan` uses it too).

3. **The "Notes for the reviewer" bullet** already said 47 *should* call the helper; it now says 47
   **must be moved onto it** and names 47's current `cpu_only_build_advice`, so the two bullets no
   longer contradict each other.

No other claim in `001-comment.md` was found to be false. Specifically checked and kept:

- "The model is resolved through `pipeline.resolve_whisper_cpp_config` ... the **same** rule the
  service and `setup` use, so doctor never disagrees about which model is in play." — **verified
  true** by `002-review.md`'s instrumentation (doctor/startup/setup resolve the same name and path).
- The captured check messages, counts, falsifications and the end-to-end CLI run — reproduced by the
  reviewer.

## One remaining sibling overclaim, in code (deliberately not changed)

`src/ges2t/whisper_engine.py`'s `vulkan_dev_package_hint` docstring carries the same present-tense
claim:

> This is the one wording for the fact both ``ges2t setup`` (ticket 47) and ``ges2t doctor`` (ticket
> 46) surface ... Keeping it here ... means the two commands cannot drift.

The review instruction freezes `src/`, so this is reported rather than edited. It is the same false
"already shared" statement and must be corrected as part of the **ticket 46/47 dedupe** (move 47's
hint onto this helper, then re-word this docstring to intent). Recording it here so it is not lost.

## Verification

- `git diff --stat` for this correction touches only `001-comment.md` (and this new `003-comment.md`);
  `src/` and `tests/` are byte-identical to the reviewed `ad01727` tree.
- Ticket status is `in review`; `docs/project/README.md` row 46 is `in review`
  (`feat/whisper-doctor`).
