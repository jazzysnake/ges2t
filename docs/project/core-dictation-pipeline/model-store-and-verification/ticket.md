# Model store: pinned download, verification, and atomic install

- **Priority:** P0
- **Status:** to do
- **Complexity:** S
- **Epic:** ../epic.md

## Current state / context

Nothing downloads or stores models yet. ADR 0017 requires that **`ges2t setup` is the only
code path that performs network I/O** (ADR 0018), that the artifacts are pinned by checksum
rather than trusted by URL, and that we own the artifacts instead of relying on the
`cactus-needle` package's hard-coded `~/.cache/cactus-needle` cache.

The verified pins are in ADR 0017 — read it in full. Summary:

| Artifact | Pin |
|---|---|
| `whistle.cact` | `https://huggingface.co/Cactus-Compute/whistle/resolve/main/whistle.cact`, sha256 `b6e02f048568ac5d01a2042556c658061e699acbc0aa2a1439f52f3d461dffeb`, `16,919,407` B |
| `libneedle.so` (engine 3.2.0, x86-64) | from `Cactus-Compute/needle3`, file `python/cactus_needle-3.2.0-py3-none-manylinux2014_x86_64.whl`, member `needle/libneedle3.so`, sha256 `0772130a416cc7a07fe444bfe400239a595195324f595b2139391839874fd030`, `1,581,448` B |

This ticket is the **store**, not the CLI and not the adapter: it exposes functions, and
`../cli-surface-and-commands/ticket.md` calls them. It must be usable with no network by
injecting a fake fetcher.

Relevant decisions: ADR 0017, ADR 0018 (no network outside acquisition), ADR 0006 (pinning
is a mitigation for the closed engine).

## Definition of done

- [ ] `src/ges2t/model_store.py` defines the pins as module-level constants (URL, sha256,
      byte size, version) for exactly the two artifacts above, plus the platform key
      (`sys.platform`/`platform.machine()` → the x86-64 pin).
- [ ] `default_data_dir() -> Path` returns `$XDG_DATA_HOME/ges2t`, default
      `~/.local/share/ges2t`.
- [ ] `model_path(version) -> Path` → `<data_dir>/models/whistle/2.0.0/whistle.cact` and
      `engine_path(version) -> Path` → `<data_dir>/engine/3.2.0/libneedle.so`.
- [ ] `ensure_model(*, fetcher=None, progress=None) -> Path` and
      `ensure_engine(*, fetcher=None, progress=None) -> Path`:
      - return the existing path immediately when a file with the **pinned byte size** is
        already present (no download);
      - otherwise download **atomically** — fetch to a temp file in the destination
        directory, verify sha256 and size, then `os.replace` into place — so an interrupted
        run cannot leave a file that later passes an existence check;
      - on a checksum or size mismatch, delete the temp file and raise a clear error naming
        the artifact, the expected hash, and the actual hash;
      - never write outside `default_data_dir()`.
- [ ] `verify(path, expected) -> bool` does a full sha256 check, used by `ges2t models
      --verify`.
- [ ] The engine step **extracts** `needle/libneedle3.so` from the wheel using `zipfile`
      (the wheel is a zip) and verifies the extracted `.so` hash. No `pip install` of the
      engine wheel, and no write to `~/.cache/cactus-needle`.
- [ ] `env_overrides() -> dict[str, str]` returns the exports ADR 0017 requires, pointing at
      the managed paths: `NEEDLE_WHISTLE_WEIGHTS`, `NEEDLE3_LIB_PATH`, `NEEDLE_TELEMETRY=0`,
      `DO_NOT_TRACK=1`, `HF_HUB_OFFLINE=1`.
- [ ] On an unsupported architecture, the ensure-functions raise a clear
      "no engine artifact pinned for this platform" error instead of downloading anything.
- [ ] Downloads use **`urllib.request` from the stdlib** and follow redirects. No HTTP
      dependency is added.
- [ ] `tests/test_model_store.py` uses a **fake fetcher** so the suite performs no network
      I/O, and covers: already-present + right size → no fetch call; mismatch → error and no
      file left behind; interrupted fetch (fetcher raises mid-write) → no partial file;
      atomic replace happens only after a successful hash; `env_overrides()` contains all
      five variables; unsupported-arch branch raises.
- [ ] `uv run pytest tests/test_model_store.py` passes and `uv run pytest` stays green.
- [ ] **Manual, once:** `uv run python -c "from ges2t.model_store import ensure_model,
      ensure_engine; print(ensure_model(), ensure_engine())"` downloads both artifacts and a
      second invocation prints the same paths without network access. Report the on-disk
      sizes and the wall time of the first download in `001-comment.md`.

- [ ] `uv run mypy src/ges2t` exits 0: every definition fully annotated and every
      signature interface-typed — ADR 0019.

## Notes

- Hashing 17 MB is fast but must not sit on the service's startup path; that is why normal
  load only checks existence and size (ADR 0017). Do not add a hash-on-every-start.
- Keep the `fetcher` parameter a small callable (`url, dest_path -> None`) so the fake is
  trivial and no HTTP mocking library is needed.
- The wheel member path is `needle/libneedle3.so` but the SONAME is `libneedle.so`; name the
  installed file `libneedle.so` to match `NEEDLE3_LIB_PATH` expectations, and say so in a
  comment.
- Do **not** import `needle` here. This module must stay importable without the
  backend installed, because `ges2t models` and `doctor` use it.

## Related tickets

- depends on: ../project-scaffold/ticket.md, ../core-interfaces-and-fakes/ticket.md
- blocks: ../whistle-transcriber-adapter-batch/ticket.md,
  ../cli-surface-and-commands/ticket.md, ../doctor-command/ticket.md
- related ADRs: ../../../adr/0017-model-acquisition-via-setup.md,
  ../../../adr/0018-no-runtime-network.md, ../../../adr/0006-needle3-closed-engine-accepted-risk.md
