# Rebuild the engine when a better variant becomes available

- **Priority:** P1
- **Status:** in review
- **Complexity:** S
- **Epic:** ../epic.md

## Current state / context

The builder (`whisper_engine.ensure_engine()`, ticket 42) is **idempotent**: a valid build short-circuits
and nothing else is checked. But the **variant** is chosen from the prerequisites **at build time**, and
`engine.json` records the decision (`variant`, and the `vulkan` piece map).

The user hit the consequence: they built the engine before installing `spirv-headers` (so it recorded
`"variant": "cpu"`, `"spirv_headers": null`, no `libggml-vulkan.so`), then installed `spirv-headers` and
re-ran `setup` — which did **nothing**, because the build is "valid". There is no path from a CPU-only
build to a GPU build except manually deleting `~/.local/share/ges2t/engine/whisper-cpp/<version>/`. The
runs stayed on the CPU.

## Definition of done

- [x] `ensure_engine()` (or the setup path) is **variant-aware**: an existing build is a no-op **only
      when it is already as good as the machine can now build**. If the built variant is `cpu` but the
      prerequisites for `vulkan` are now present (and the configured preference allows it), re-running
      `setup` **rebuilds as Vulkan** and **says so** (a one-line "rebuilding: … → vulkan (spirv-headers
      is now available)").
- [x] **The configured preference is respected**: with `[engine.whisper_cpp] gpu = "cpu"`, setup must
      **not** silently upgrade to Vulkan; with `gpu = "auto"` (the default), a CPU build upgrades to
      Vulkan when it can; with `gpu = "vulkan"`, a CPU build that cannot be rebuilt to Vulkan stays a
      reported error (as it is today), not a silent CPU run.
- [x] A **`--rebuild` (or equivalent) way to force it** may be added if the variant rule needs an
      escape hatch — say whether you added one and why.
- [x] Rebuilding is safe: the old build is replaced atomically enough that a failed rebuild does not
      leave a half-installed engine that `verify_build` accepts.
- [x] Tests **falsify** the rule: built `cpu` + Vulkan now available → rebuilds; built `vulkan` + Vulkan
      available → **no** rebuild; built `cpu` + only CPU available → no rebuild; `gpu = "cpu"` + Vulkan
      available → **no** upgrade. Break each, watch a test fail, revert.
- [x] `uv run pytest` and `uv run mypy src/ges2t tests` green; report counts; no behaviour change beyond
      the rebuild policy.
- [x] `001-comment.md` records the policy and the `engine.json` fields it reads.
- [ ] **Manual, on the reference machine (USER-OWNED).** Install the missing Vulkan dev
      package, re-run `ges2t setup whisper.cpp <model>`, and confirm `"variant": "vulkan"` in
      `engine.json` (and `gpu = "cpu"` prevents the upgrade). The agent may not install packages
      or use `sudo`; the exact steps are in `001-comment.md`.

## Notes

- This is the missing half of ticket 47's GPU hint: 47 tells the user **why** it is CPU-only; this
  ticket makes re-running `setup` actually **fix** it.
- The user-owned end-to-end check (install the piece, re-run `setup`, confirm `variant: vulkan` and a
  faster run) is left unticked and handed over — the agent may not install packages or use `sudo`.
- **No keystrokes, no clipboard, no windows, no microphone, no `sudo`, no service changes, no network.**

## Related tickets

- depends on: ../whisper-engine-build/ticket.md
- related: ../engine-naming-and-setup-guidance/ticket.md (the GPU hint), ../whisper-doctor/ticket.md
- related ADRs: ../../../adr/0021-whisper-cpp-second-backend.md
