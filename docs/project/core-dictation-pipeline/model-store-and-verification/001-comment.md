# 001 — Model store implementation notes

Ticket: `ticket.md` (model-store-and-verification) · Branch: `feat/model-store-and-verification`

## What was built

Exactly two files, as the ticket requires:

- `src/ges2t/model_store.py` — pins, platform key, locations, atomic download/verify,
  engine extraction, `env_overrides()` (ADR 0017, ADR 0018).
- `tests/test_model_store.py` — 24 tests, all network-free via an injected fake `fetcher`.

Nothing else was created or modified: no `interfaces.py`, `whistle.py`, `service.py`,
`doctor.py`, or `pipeline.py`; `config.py` is untouched. `model_store.py` imports only
`hashlib`, `os`, `platform`, `shutil`, `sys`, `tempfile`, `urllib.request`, `zipfile`,
`collections.abc`, `dataclasses`, and `pathlib`. It does **not** import `ges2t.interfaces`
and does **not** import `cactus_needle`, so `ges2t models` and `doctor` can import it with
no backend installed.

## Final public shapes

```python
MODEL_VERSION = "2.0.0";  ENGINE_VERSION = "3.2.0"
MODEL_URL = "https://huggingface.co/Cactus-Compute/whistle/resolve/main/whistle.cact"
MODEL_SHA256 = "b6e02f048568ac5d01a2042556c658061e699acbc0aa2a1439f52f3d461dffeb"
MODEL_SIZE = 16_919_407
ENGINE_WHEEL_URL = "https://huggingface.co/Cactus-Compute/needle3/resolve/main/python/cactus_needle-3.2.0-py3-none-manylinux2014_x86_64.whl"
ENGINE_MEMBER = "needle/libneedle3.so"
ENGINE_SHA256 = "0772130a416cc7a07fe444bfe400239a595195324f595b2139391839874fd030"
ENGINE_SIZE = 1_581_448

PLATFORM_KEY: str                              # f"{sys.platform}-{normalised machine}", e.g. "linux-x86_64"
SUPPORTED_ENGINE_PLATFORMS: frozenset[str]     # {"linux-x86_64"}

@dataclass(frozen=True)
class ArtifactPin:  name: str; url: str; sha256: str; size: int; member: str | None = None
MODEL_PIN: ArtifactPin    # member=None
ENGINE_PIN: ArtifactPin   # member="needle/libneedle3.so"

Fetcher = Callable[[str, Path], None]          # url, dest_path -> None (the test seam)
ProgressCallback = Callable[[str], None]       # one human-readable line per milestone

class ModelStoreError(Exception): ...
class UnsupportedPlatformError(ModelStoreError): ...
class ArtifactVerificationError(ModelStoreError): ...  # names artifact, expected+actual sha256/size

def default_data_dir() -> Path
def model_path(version: str = MODEL_VERSION) -> Path      # <data>/models/whistle/<v>/whistle.cact
def engine_path(version: str = ENGINE_VERSION) -> Path    # <data>/engine/<v>/libneedle.so
def engine_pin() -> ArtifactPin                           # raises UnsupportedPlatformError
def verify(path: Path, expected: ArtifactPin) -> bool     # full sha256 + size
def env_overrides() -> dict[str, str]
def ensure_model(*, fetcher: Fetcher | None = None, progress: ProgressCallback | None = None) -> Path
def ensure_engine(*, fetcher: Fetcher | None = None, progress: ProgressCallback | None = None) -> Path
```

The pins are recorded once as plain constants and wrapped in a frozen `ArtifactPin`; both
`verify()` and the acquisition path read the same object, so `ges2t models --verify` and the
installer can never drift. `MODEL_PIN`/`ENGINE_PIN` are the values the CLI should print.

## Atomic install and verification policy

`_ensure()` is the one code path:

1. if the destination is a file of exactly the pinned byte size, return it — **no download,
   no hash**;
2. otherwise `mkdir -p` the destination dir, `tempfile.mkstemp` a `.part` sibling, and call
   the fetcher into that temp file;
3. for the engine, extract `needle/libneedle3.so` from the temp wheel into a second temp file
   with `zipfile` + `shutil.copyfileobj` (no `pip`, no `~/.cache/cactus-needle`);
4. sha256 + size the *installed* artifact (the extracted `.so` for the engine);
5. on mismatch raise `ArtifactVerificationError` (naming artifact, expected hash, actual
   hash) and delete both temp files in a `finally`;
6. on match `os.replace()` the temp file onto the destination, which is atomic because temp
   and destination are in the same directory.

An interrupted fetcher (raises mid-write) propagates its own exception; the `finally` deletes
the partial temp file and the destination never appears. A wrong-size file already present is
re-downloaded because step 1 does not match. The size-only fast path carries an explicit
comment warning not to "optimise" it into a hash (ADR 0017: hashing 17 MB must not sit on the
service's startup path).

## Manual network step (required by the DoD)

Command, run against the real endpoints (HF `/resolve/` redirects to a CDN; the stdlib opener
follows it — no HTTP dependency):

```
uv run python -c "from ges2t.model_store import ensure_model, ensure_engine; print(ensure_model()); print(ensure_engine())"
```

Real output / observations:

- **Cold first download** (data dir removed first, timed): `3.534 s` wall.
- **Second invocation**: same two paths, `0.099 s` wall — no download.
- Resolved on-disk paths:

  ```
  /home/matee/.local/share/ges2t/models/whistle/2.0.0/whistle.cact
  /home/matee/.local/share/ges2t/engine/3.2.0/libneedle.so
  ```

- On-disk sizes and hashes (both match ADR 0017 exactly):

  ```
  16919407 bytes  .../models/whistle/2.0.0/whistle.cact
    b6e02f048568ac5d01a2042556c658061e699acbc0aa2a1439f52f3d461dffeb
   1581448 bytes  .../engine/3.2.0/libneedle.so
    0772130a416cc7a07fe444bfe400239a595195324f595b2139391839874fd030
  ```

- No `.part` files remain under `~/.local/share/ges2t`.
- `~/.cache/cactus-needle` **already existed before this ticket** (created 2026-10-09 10:46
  by the earlier ADR probe). Our runs left it byte-for-byte unchanged: the mtimes of
  `cactus-needle/…` before and after the download are identical (`diff` empty). It was not
  created or written to by `model_store`.
- Installed files are mode `0600` because `mkstemp` creates them that way; `os.replace`
  preserves the mode. Acceptable for a user's XDG data dir; note it if anything later needs
  them group/world readable.

## Exact `env_overrides()` output

```json
{
  "NEEDLE_WHISTLE_WEIGHTS": "/home/matee/.local/share/ges2t/models/whistle/2.0.0/whistle.cact",
  "NEEDLE3_LIB_PATH": "/home/matee/.local/share/ges2t/engine/3.2.0/libneedle.so",
  "NEEDLE_TELEMETRY": "0",
  "DO_NOT_TRACK": "1",
  "HF_HUB_OFFLINE": "1"
}
```

Paths are computed from `default_data_dir()` at call time, so `XDG_DATA_HOME` is honoured and
a test can assert the five keys without touching the network.

## Verification commands (real output)

```
$ uv run pytest
60 passed in 0.22s            # 36 pre-existing + 24 new

$ uv run pytest tests/test_model_store.py -q
24 passed in 0.18s

$ uv run mypy src/ges2t
Success: no issues found in 4 source files
```

The feature tests cover: present + right size → no fetch; successful download installs the
verified payload; destination absent during fetch (replace is the last step); checksum
mismatch and size mismatch → error + no file + no `.part` leftovers; interrupted fetch → no
partial file; progress lines; engine extraction, wrong member content, missing member,
present-engine fast path; unsupported platform raises for both ensure functions before any
fetch; all five `env_overrides()` keys; `verify()` on valid/tampered/missing files; the ADR
pin values as data; and the XDG default/fallback plus the exact storage layout.

## Judgement calls (the orchestrator should know these)

1. **`verify(path, expected)` takes an `ArtifactPin`, not a bare hash string.** The ticket
   left the type open; the ADR 0019 "classes over primitives" rule and the fact that the pins
   are already `ArtifactPin`s make `model_store.verify(path, model_store.MODEL_PIN)` the
   natural `ges2t models --verify` call. It checks full sha256 **and** byte size.
2. **`ensure_model` also refuses an unsupported platform, before downloading anything.** The
   weights are architecture-independent, so this is stricter than strictly necessary, but the
   DoD says "the ensure-functions raise … instead of downloading anything" and `ges2t setup`
   calls both functions in sequence; failing fast avoids a pointless 17 MB fetch on a platform
   that can never run. Trivially relaxed later if a non-x86-64 engine pin is added.
3. **`progress` is `Callable[[str], None]`** receiving short human-readable lines. No
   signature was specified; a string line keeps the fake trivial and lets `ges2t setup` pass
   `print` directly. Change to a `(done_bytes, total_bytes)` shape only with the CLI ticket.
4. **The engine wheel itself is not hashed** — ADR 0017 pins only the extracted
   `libneedle.so`, and the ticket says to verify the extracted member. The wheel is written to
   a temp file, the member is extracted and verified, and the temp wheel is deleted. A future
   pin could add a container hash, but that would need an ADR update with the wheel's sha256.
5. **`PLATFORM_KEY` is computed at import** from `sys.platform`/`platform.machine()` with
   small aliases (`amd64`/`x64` → `x86_64`, `arm64` → `aarch64`). Tests monkeypatch it to
   simulate an unsupported architecture.
6. **`ensure_model`/`ensure_engine` always use `default_data_dir()`.** The
   `config.transcriber.model_path` override is the pipeline ticket's concern (its DoD says so);
   supporting it here would have widened the signature beyond the ticket.
7. **A right-size-but-wrong-content file is trusted by the fast path** — that is the ADR 0017
   size-only policy, and a test locks it in (`assert fetcher.calls == []`). `verify()` is the
   explicit escape hatch.

## Anything else the orchestrator must know

- `docs/project/README.md` row 5 (model-store-and-verification) was set to `in review` with
  branch `feat/model-store-and-verification`; other rows untouched.
- The worktree `.worktrees/model-store-and-verification` is left in place for the reviewer,
  per instructions.
- The downloaded artifacts live only under `~/.local/share/ges2t` (gitignored by location,
  outside the repo); `git status` is clean of them.
- No HTTP dependency was added to `pyproject.toml`. The ADR 0018 audit is clean: the only
  `urllib` hits in `src/` are inside `model_store.py`.
