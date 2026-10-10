# 001 — variant-aware build policy: the plan

Ticket 49. Re-running `ges2t setup` currently does nothing when a *valid* engine is already
installed (`ensure_engine()` short-circuits on `verify_build()`), so a CPU-only build made before
`spirv-headers` was installed can never become Vulkan without hand-deleting
`~/.local/share/ges2t/engine/whisper-cpp/<version>/`. This ticket makes the idempotency
**variant-aware** and honours the configured `gpu` preference.

## What I read first

- ticket `49` + `epic.md` + ADR `0021` (`§4` model rule, `§7` CPU/Vulkan detection, `§8` strict validation).
- merged ticket `42` (`whisper-engine-build`) and its `003-comment.md` (the build/detection rule),
  ticket `44` (`setup_command`), `45` (`whisper_cpp_config`), `46`/`47` (the shared GPU hint).
- `src/ges2t/whisper_engine.py`, `src/ges2t/setup_command.py`,
  `src/ges2t/transcribers/whisper_cpp_config.py`, `src/ges2t/pipeline.py`, `src/ges2t/cli.py`,
  `src/ges2t/doctor.py`, and their tests.

## The policy (decided)

Let `available` = the best variant this machine can build **now**:
`VULKAN` when `inspect_vulkan(probe).available`, else `CPU`. Let `installed` = `verify_build(target)`.

`gpu` preference (read from `[engine.whisper_cpp] gpu`, values `auto` / `cpu` / `vulkan` — the
existing `GpuPreference`):

| preference | desired variant | installed CPU + Vulkan now available | installed Vulkan | notes |
|---|---|---|---|---|
| `auto` (default) | `available` | **rebuild as Vulkan** | no-op | the user's bug |
| `cpu` | `CPU` | **no upgrade** (stays CPU) | no-op (never downgrade) | respects the preference |
| `vulkan` | `VULKAN` | rebuild as Vulkan | no-op | if Vulkan cannot be built → **reported error** |

The "no-op only when the installed build is already as good as the machine can now build" rule is:

```
no-op   when installed == VULKAN            (never downgrade a better build)
no-op   when installed == desired           (already what we would build)
rebuild otherwise                           (only CPU → VULKAN)
```

A `gpu = "vulkan"` preference that cannot be satisfied (Vulkan dev pieces missing) raises the
existing typed `PrerequisiteError` — with the exact `pacman` line — even when a CPU build exists;
it is a reported error, never a silent CPU run (matches the runtime rule in
`whisper_cpp.resolve_device`, ADR 0020's no-silent-substitution).

## Where the preference crosses into `ensure_engine`

`ensure_engine()` gains a keyword parameter `preference: GpuPreference` (default `AUTO`). The enum
is the **single** `gpu` vocabulary (`ges2t.transcribers.whisper_cpp_config.GpuPreference`), so the
build policy and the runtime device choice cannot drift. The composition is:

```
config.toml [engine.whisper_cpp] gpu
  -> Ges2t setup loads it (new: setup now reads config)
  -> WhisperCppConfig.from_mapping -> GpuPreference   (the existing validator)
  -> SetupPlan.preference
  -> run_setup -> ensure_engine(preference=...)
```

`ges2t setup` is the only place the config value is read; `ensure_engine` never opens a file.

## Safety and the escape hatch

- **Safe replacement.** `WhisperCppBuilder` stages the library set + manifest in a sibling temp
  directory (same filesystem), verifies it, then swaps the directory into place. A failed rebuild
  leaves the previous engine untouched, and a half-written tree is never at `engine.json`'s path,
  so `verify_build` cannot accept it.
- **`--rebuild`.** Added, because the ticket's complaint is precisely "the only path is to delete
  the directory by hand": `ensure_engine(force=True)` / `ges2t setup --rebuild` bypasses the
  no-op for an already-best build while still honouring the preference and the prerequisite gate.

## `engine.json` fields read by the policy

Only `variant` (the stored `BuildVariant`) decides the rebuild; `verify_build` already reads the
manifest and checks the `entry_library` + every `libraries` entry still exist. The live machine is
probed fresh (`inspect_vulkan`) — never trusted from the stored `vulkan` map — so a dev package
installed since the build is seen.
