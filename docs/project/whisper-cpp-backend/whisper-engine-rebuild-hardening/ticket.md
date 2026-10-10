# whisper engine rebuild: test hardening and follow-ups

- **Priority:** P2
- **Status:** done
- **Complexity:** XS
- **Epic:** ../epic.md

## Current state / context

Ticket 49 (variant-aware engine rebuild) passed review, which recorded six **non-blocking** follow-ups
(R1–R6). This ticket closes them so the rebuild policy's guarantees are actually pinned by tests rather
than by the reviewer's one-off probes. The code is correct today; what is missing is coverage and a
little tidiness.

## Definition of done

- [x] **R1 — pin the ordering.** With `gpu = "vulkan"` and no Vulkan available, **no** "rebuilding: …"
      notice is emitted **before** the typed `PrerequisiteError`. The explicit-`vulkan` gate is redundant
      for the *error* but load-bearing for the *ordering* (removing it prints a false notice first); add
      the test that locks it.
- [x] **R2 — strengthen the weak test.** `test_a_failed_fresh_build_leaves_no_engine_verify_build_accepts`
      currently passes even under the staging break (installing straight into the destination); make it
      **fail** in that case.
- [x] **R3 — dead code.** Remove the unreachable `_rebuild_reason` fallback (or cover it with a test, if
      it is in fact reachable — say which).
- [x] **R4 — `--rebuild` says why.** A forced rebuild states that it is forced (not silently identical to
      an automatic one).
- [x] **R5 — notice wording.** Decide whether the notice names `spirv-headers` or the generic "Vulkan
      development packages", and make it consistent with the shared `vulkan_dev_package_hint` sentence.
- [x] **R6 — the missing case.** Add a committed test for `force + gpu = "vulkan"` + no Vulkan.
- [x] `uv run pytest` and `uv run mypy src/ges2t tests` green; report counts; **no behaviour change**
      beyond the `--rebuild` message and the notice wording.
- [x] `001-comment.md` records which of R1–R6 were fixed vs declined, with the reason.

## Notes

- Test-and-wording only; do **not** change the rebuild **policy** (ticket 49's matrix is reviewed and
  correct) or the staged-install safety.
- **No keystrokes, no clipboard, no windows, no microphone, no `sudo`, no package installs, no service
  changes, no network.**

## Related tickets

- depends on: ../whisper-engine-variant-rebuild/ticket.md
- related ADRs: ../../../adr/0021-whisper-cpp-second-backend.md
