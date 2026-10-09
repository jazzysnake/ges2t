# Model acquisition: explicit `ges2t setup`, pinned by checksum

- **Status:** accepted
- **Date:** 2026-10-09

## Context

The transcriber interface owns acquisition (ADR 0010), and the user was asked whether
acquisition should happen automatically on first run, explicitly via a `ges2t setup`
command, or both. **The user chose explicit `ges2t setup` only** — there is no automatic
first-run download.

The backend package `cactus-needle` fetches both the engine wheel and the weights on first
use, into a cache it controls (`~/.cache/cactus-needle/…`). Left alone that is a network
call triggered from inside transcription, which violates ADR 0018. The probe verified the two
escape hatches that let us take control, and verified them offline:

- `NEEDLE_WHISTLE_WEIGHTS` — path to a pre-downloaded `.cact` file.
- `NEEDLE3_LIB_PATH` — path to the gen-3 engine `.so`.
- Tested: cold cache + `HF_HUB_OFFLINE=1` → `LocalEntryNotFoundError`; warm cache + offline →
  works; **empty `HOME` + offline with both overrides set → works**. So the overrides are
  sufficient to run against artifacts we own, with no reliance on upstream's cache layout.

Verified pins (2026-10-09):

| Artifact | Source | Pin |
|---|---|---|
| `whistle.cact` | `https://huggingface.co/Cactus-Compute/whistle/resolve/main/whistle.cact` | sha256 `b6e02f048568ac5d01a2042556c658061e699acbc0aa2a1439f52f3d461dffeb`, size `16,919,407` B |
| `libneedle.so` (engine 3.2.0, x86-64) | `Cactus-Compute/needle3`, file `python/cactus_needle-3.2.0-py3-none-manylinux2014_x86_64.whl`, member `needle/libneedle3.so` | sha256 `0772130a416cc7a07fe444bfe400239a595195324f595b2139391839874fd030`, `1,581,448` B |

Other facts that matter: the cache root `~/.cache/cactus-needle` is **hard-coded** in the
package (only the whisper-compare cache honours `XDG_CACHE_HOME`), so we cannot relocate it
by config — another reason to own the artifacts rather than the cache. The package also
writes `~/.cactus_needle/telemetry_id`; we suppress telemetry outright (ADR 0007).

## Decision

**`ges2t setup` is the only code path that performs network I/O** (ADR 0018). It downloads,
verifies, and installs both artifacts into ges2t-owned locations, and every later run is
fully offline.

- **We own the artifacts, not the package's cache.** `ges2t setup` fetches `whistle.cact`
  directly from the pinned URL and extracts `libneedle.so` from the pinned engine wheel,
  verifying each against the sha256 and byte size in the table above. Nothing is read from
  or written to `~/.cache/cactus-needle`.
- **Store under `$XDG_DATA_HOME/ges2t/`** (default `~/.local/share/ges2t/`):
  `models/whistle/2.0.0/whistle.cact` and `engine/3.2.0/libneedle.so`.
- **Export the overrides.** The service and the adapter set `NEEDLE_WHISTLE_WEIGHTS` and
  `NEEDLE3_LIB_PATH` to those absolute paths before the backend package is imported, in one
  place, alongside `NEEDLE_TELEMETRY=0`, `DO_NOT_TRACK=1` and `HF_HUB_OFFLINE=1`.
  `HF_HUB_OFFLINE=1` is the belt to the overrides' braces: if a path is ever wrong, the
  failure is a loud local error rather than a silent download.
- **The pins live in code**, as constants in `model_store.py` (URL, sha256, size, version),
  so `ges2t models` can print exactly what is installed and what is expected, and a
  reviewer can diff pins against this ADR.
- **Verification policy:** full sha256 verification at `ges2t setup` time and on explicit
  `ges2t models --verify`; at normal load time, verify **existence and byte size** (cheap)
  and defer the hash. Hashing 17 MB is fast, but it must not sit on the startup path
  unnecessarily.
- **Idempotence:** re-running `ges2t setup` with valid artifacts already present performs no
  download and prints what is already installed. A corrupt or size-mismatched artifact is
  re-downloaded.
- **Failure is actionable.** If the model or engine is missing when `ges2t run` starts, the
  error names `ges2t setup` explicitly. It never attempts a download (ADR 0018).
- **Platform pinning is explicit.** The engine pin is x86-64 manylinux. On an unsupported
  architecture, `ges2t setup` fails with a clear "no engine artifact pinned for this
  platform" message rather than downloading a wrong-architecture wheel.
- **Downloads are atomic**: fetched to a temporary file in the destination directory, hashed,
  then renamed into place, so an interrupted setup cannot leave a half-written model that
  later passes an existence check.

## Consequences

- The user's chosen "explicit setup only" flow is what the code does, and the model pin is
  enforced rather than decorative. `HF_HUB_OFFLINE=1` makes the offline property a hard
  boundary, not a hope.
- We take on the job of tracking two upstream artifacts (model URL, engine wheel URL and
  hashes). The cost is a constants block and a test; the benefit is that an upstream change
  cannot silently alter what we run (ADR 0006).
- Because we bypass the package's own fetch, a future `cactus-needle` release that changes
  its cache or loading internals is less likely to break us — but a change to the
  `NEEDLE_WHISTLE_WEIGHTS`/`NEEDLE3_LIB_PATH` contract would, so those env var names are
  treated as a pinned interface and asserted at load time (ADR 0007).
- Deleting `~/.local/share/ges2t/` is a complete, honest reset; `~/.cache/cactus-needle`
  should not exist at all after a normal use, and that is itself a useful audit signal.
