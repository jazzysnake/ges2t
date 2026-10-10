# 001 — comment: whisper backend doctor checks

- **Ticket:** `docs/project/whisper-cpp-backend/whisper-doctor/ticket.md`
- **Branch:** `feat/whisper-doctor`
- **Worktree:** `.worktrees/whisper-doctor`
- **Status:** in progress

## Scope read first

Read before touching code: the ticket, `../epic.md`, ADR 0021, ADR 0016, ADR 0013, ADR 0019,
and the merged dependencies (ticket 41 `whisper-model-store`, 42 `whisper-engine-build`,
43 `whisper-transcriber-adapter`, 45 `whisper-backend-config-and-validation`), plus the current
`doctor.py`, `whisper_engine.py`, `model_store.py`, `config.py`, `pipeline.py`,
`transcribers/whisper_cpp*.py`, and `tests/test_doctor.py`.

## Plan (recorded before coding)

Three new `doctor` checks, each gated on `transcriber.engine == "whisper_cpp"`, so a `whistle`
selection reports `OK`/skipped rather than a false failure:

1. **Whisper engine built** — `libwhisper.so` + every `libggml*.so*` the install manifest names,
   present at the resolved data-dir path, and the build's variant (CPU-only / Vulkan) reported.
2. **Whisper model** — the configured (or device-defaulted) model present at its catalogue path,
   named exactly (`ggml-<name>.bin`), with its expected size; with `--verify`, hash-verified
   against its pinned sha256.
3. **Vulkan device** — whether a Vulkan runtime is present; a `WARN` when absent naming the CPU
   cost and the `small-q8_0` default; and, when a GPU runtime *is* present but the built engine
   is CPU-only, the WHY (the missing dev package(s), from the same probe `setup` will use).

The existing 22 checks stay untouched and the two by-design `WARN`s (Hotkey bindings, Tray
failures) still hold, because with the default `whistle` engine all three new checks are skipped.

_(evidence appended below as work lands)_
