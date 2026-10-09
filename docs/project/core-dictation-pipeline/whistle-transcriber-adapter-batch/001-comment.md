# 001 — Whistle transcriber adapter (batch) implementation notes

Ticket: `ticket.md` (`whistle-transcriber-adapter-batch`) · Branch:
`feat/whistle-transcriber-adapter-batch` · Worktree:
`.worktrees/whistle-transcriber-adapter-batch`

## What was built

Exactly the two files the ticket allows:

- `src/ges2t/transcribers/whistle.py` — `WhistleTranscriber`, the only module in
  ges2t that imports the engine package, and the batch `transcribe()` path.
- `tests/test_whistle_batch.py` — 24 tests, no engine, no model, no network.

No other module was created or modified (`whistle_config.py`, `model_store.py`,
`config.py`, `interfaces.py`, `fakes.py` untouched). The module imports only
`os`, `time`, `array`, `pathlib`, `typing`, `collections.abc`,
`ges2t.model_store`, `ges2t.interfaces`, and `ges2t.transcribers.whistle_config`
— no `numpy`, no module-level `needle` (ADR 0010, ADR 0019).

## Final signatures

```python
SAMPLE_RATE: int = 16000
MAX_SECONDS: float = 30.0

_needle: Any = None  # lazily imported engine package; replaceable by tests

class WhistleTranscriberError(Exception): ...
class WhistleNotLoadedError(WhistleTranscriberError): ...
class WhistleInputError(WhistleTranscriberError): ...
class WhistleLoadError(WhistleTranscriberError): ...

class WhistleTranscriber:
    name: str = "whistle"            # set in __init__
    supports_streaming: bool = True  # set in __init__
    config: WhistleConfig
    load_seconds: float | None       # None until the first successful load()

    def __init__(
        self, config: WhistleConfig, model_path_override: Path | None = None
    ) -> None: ...
    def ensure_model(self, progress: ProgressCallback | None = None) -> Path: ...
    def load(self) -> None: ...
    def transcribe(self, buffer: AudioBuffer) -> TranscriptionResult: ...
    def stream(
        self, chunks: Iterable[AudioBuffer]
    ) -> Iterator[TranscriptionSegment]: ...  # NotImplementedError stub
    def close(self) -> None: ...
```

Private helpers, all fully annotated: `_import_needle() -> Any`,
`_apply_env_overrides() -> None`, `_resolved(path: Path) -> Path`,
`_float32_bytes(samples: Sequence[float]) -> bytes`,
`_validate_buffer(buffer: AudioBuffer) -> None`,
`_transcription_from_engine(raw: Mapping[str, Any]) -> TranscriptionResult`,
`_loaded_engine_path(needle_module: Any) -> Path | None`, plus the private methods
`_weights_path()`, `_assert_managed_artifacts(...)` and `_require_loaded()`.

`isinstance(WhistleTranscriber(WhistleConfig()), Transcriber)` is asserted in a
test and passes (runtime_checkable Protocol).

## Lazy import and the ADR 0007 load-time assertion

`needle` is **not** imported at module import time. `_import_needle()` does
`import needle` on first use and caches it in the module global `_needle`; if
`_needle` was already replaced (by a test), it is returned untouched. A test that
imports `ges2t.transcribers.whistle` in a fresh `python -I` interpreter confirms
`needle`/`cactus_needle`/`gi`/`numpy` are absent from `sys.modules`, and the test
fixture additionally sets `sys.modules["needle"] = None`, so a stray real import
would raise rather than pass on a machine where the package is installed.

`load()` does, in order:

1. `_apply_env_overrides()` — `os.environ.update(model_store.env_overrides())`,
   the one and only place ges2t applies the five overrides (ADR 0007, ADR 0017).
   It runs **before** the lazy import, so the engine sees the managed paths and
   the telemetry opt-outs from the start.
2. `needle.Whistle(weights=str(self._weights_path()))` where `_weights_path()` is
   `model_path_override` if given, else `model_store.model_path()`.
3. `_assert_managed_artifacts(...)`:
   - **weights** — compare `engine.weights` (what `needle.Whistle` actually opened)
     against `_weights_path()`, both `Path.resolve()`d. A mismatch raises
     `WhistleLoadError` naming both paths and telling the user to run `ges2t setup`
     and clear a stale `NEEDLE_WHISTLE_WEIGHTS`.
   - **engine** — the package exposes no public accessor for the shared library it
     opened, so `_loaded_engine_path()` reads the ctypes handle the package caches:
     `needle.agent.whistle._lib()._name`. That is the exact path `ctypes.CDLL` was
     given (the `NEEDLE3_LIB_PATH` override), and it is compared against
     `model_store.engine_path()`. If the private internals are unavailable the
     helper returns `None` and the assertion **fails closed** with a clear message,
     rather than skipping the check.
4. Only then is `self._engine` assigned. `load()` is idempotent (early return when
   already loaded), and `load_seconds` records the wall time of the
   `Whistle(...)` construction.

## Audio conversion and validation

`transcribe()` validates before touching the engine:

- `sample_rate != 16000` → `WhistleInputError` naming 16000; never resampled.
- `len(samples) / 16000 > 30.0` → `WhistleInputError` naming the 30 s ceiling and
  the actual duration/sample count; never truncated. Exactly 30.000 s (480000
  samples) is accepted, matching the engine's "≤30 s" contract.

The samples become raw float32 bytes with the stdlib only:
`array("f", samples).tobytes()`. No WAV path, no WAV round-trip, no numpy. The
engine receives `bytes`, `language=self.config.language` verbatim (empty string =
auto-detect, handled by the engine — no code mapping, per the ticket).

`_transcription_from_engine` copies `text` unchanged (no strip/case/trailing
space, ADR 0012) and passes `language`, `ttft_ms`, `decode_tps` through, mapping
absent keys to `None`. Silence comes back from the engine as `text=""`.

## Manual real-engine run (once)

Run through the real adapter on the reference machine, with the already-installed
managed artifacts (`~/.local/share/ges2t/models/whistle/2.0.0/whistle.cact`,
`~/.local/share/ges2t/engine/3.2.0/libneedle.so`); no download, no audio file
committed. Clips are int16 16 kHz mono from `/tmp/whinspect/`, converted to float
in memory:

```
load: wall=0.0558s load_seconds=0.03198344001430087
/tmp/whinspect/clip5.wav: audio=5.00s wall=0.2317s
  text='He hoped there would be stew for dinner, turnips and carrots and bruised potatoes.'
  language='en' ttft_ms=113.8 decode_tps=213.6
/tmp/whinspect/clip30.wav: audio=30.00s wall=5.1399s
  text='He hoped there would be stew for dinner, turnips and carrots, and bruised potatoes, and fat mutton pieces to be laid out in thick, peppered, flour fat and sauce. "In the Ramas medius meridas revoloteraman and bonus bajados de y medicuille legendario tumacti." After early nightfall the yellow lamps would light up here and there the squalid quarter of the brothels. "Stuff it into you," his belly counselled him.'
  language='en' ttft_ms=4297.4 decode_tps=164.6
silence 2.0s: text='' language='' ttft_ms=0.0 decode_tps=0.0
```

`load_seconds` (the `Whistle(...)` construction) is 0.032 s; the first-call wall
also pays the one-time `import needle`, so process-level "load" was 0.056 s.
The 5 s wall (0.23 s) and 30 s wall (5.14 s) are inside the ranges the ticket
records (0.20–0.37 s and 2.04–5.45 s). **Silence returned an empty string and
raised nothing**, so the adapter's validation, byte conversion, engine call,
managed-path assertion, and pass-through all ran end to end.

## Judgement calls (read these before extending)

1. **`ensure_model()` with a `model_path_override` skips the weights download.**
   The engine is always ensured; the managed weights download is skipped because a
   caller that supplied an explicit weights path owns that artifact, and
   downloading a different file would be wrong. Without an override it calls
   `model_store.ensure_model` then `model_store.ensure_engine` and returns the
   managed weights path. `progress` is forwarded to each acquisition unchanged
   (so a UI sees `0.0 → 1.0` once per artifact; aggregating them is out of scope
   for this ticket). Ticket 11 constructs the adapter without an override, so the
   normal path is unaffected.
2. **`stream()` raises `NotImplementedError` eagerly**, not as a generator, so a
   caller learns immediately rather than on first `next()`. `supports_streaming`
   is `True` because it reports the engine's capability (the ticket says so), even
   though the streaming implementation is ticket 14's.
3. **`close()` cannot unload the engine.** Needle has no unload entry point and
   the model is process-global (ADR 0005), so `close()` drops ges2t's reference
   (`self._engine = None`) and makes the adapter reusable; `transcribe()` then
   raises `WhistleNotLoadedError` again. No lock was added: the engine is not
   thread-safe and concurrent use is not supported (ADR 0015).
4. **No `WhistleConfig.language` translation.** The string (including `""`) is
   handed to the engine as-is; the engine's own `_options()` turns a falsy value
   into `None`. `language=""` returned for silence is the engine's value and is
   passed through.
5. **Rebase onto current `main` (`ed7fb44`).** The branch was originally cut at
   `32408eb`, which failed `uv run mypy src/ges2t tests` on two pre-existing files
   (`tests/test_whistle_config.py`, `tests/test_model_store.py`) under mypy 2.4.0.
   Ticket 20 (`type-check-whole-tree`, merged as `62df286`) fixes exactly those,
   so the branch was rebased onto `ed7fb44` to satisfy the ticket's whole-tree
   mypy requirement. The diff beyond the two deliverable files is only the ticket
   status/checkboxes and the board row.

## For ticket 14 (streaming adapter)

- The extension point is `WhistleTranscriber.stream()` in this same file; it is a
  deliberate stub. Add the streaming loop here, do not create a separate class.
- Reuse `_import_needle()` / `self._engine` from `load()`; the engine instance of
  `needle.Whistle` exposes `.stream(chunks, language=..., keywords=...)` yielding
  per-chunk dicts with `text`, `pending`, `language`, `received`, `pass_ms`.
- The test fake pattern to copy is in `tests/test_whistle_batch.py`
  (`FakeNeedleModule` / `FakeWhistleEngine`), including the
  `sys.modules["needle"] = None` guard. `_loaded_engine_path()` reads
  `needle.agent.whistle._lib()._name`; keep that in mind if the fake grows a
  `.stream()`.
- One deviation to flag loudly: none. The batch module is additive.

## Commands and real output

```
$ uv run pytest tests/test_whistle_batch.py -q
24 passed in 0.32s

$ uv run pytest -q
253 passed in 1.01s

$ uv run mypy src/ges2t tests
Success: no issues found in 24 source files
```
