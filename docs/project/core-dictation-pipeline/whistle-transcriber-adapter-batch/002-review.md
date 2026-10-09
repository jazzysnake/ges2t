# 002 — Review: Whistle transcriber adapter (batch)

- **Ticket:** `ticket.md` (`whistle-transcriber-adapter-batch`)
- **Branch:** `feat/whistle-transcriber-adapter-batch` (worktree `.worktrees/whistle-transcriber-adapter-batch`)
- **Reviewed HEAD:** `185c602` (two commits on top of `main` `ed7fb44`: `6bea99b` code+tests, `185c602` docs/board)
- **Reviewer:** independent review pass, 2026-10-09, reference machine (16 cores, `x86_64`)
- **Artifacts:** none committed. Probes live in `/tmp/review_whistle/`; `git status` on the branch is clean after every probe.

## Verdict

**PASS — no blocking findings. Safe to merge.**

Every DoD item was reproduced, not trusted: the real engine ran through the adapter with
my own transcript and timings, the ADR 0007 load-time assertion refused two real
divergences, `load()`/`transcribe()` ran with the network and the acquisition functions
armed to raise, the suite passed with `needle` genuinely *unimportable*, and both
falsification probes (top-level `import needle`, unannotated function) made their checks
fail before being reverted. The implementation is a faithful, well-annotated realisation
of the ticket.

Non-blocking observations are in **Risks / follow-ups**; none change the verdict.

## Per-criterion findings

### 1. The real engine works through the adapter — reproduced independently — PASS

Ran the committed adapter end to end against the real, already-installed managed artifacts
(`~/.local/share/ges2t/models/whistle/2.0.0/whistle.cact`,
`~/.local/share/ges2t/engine/3.2.0/libneedle.so`), reading `/tmp/whinspect/clip5.wav` and
`clip30.wav` (int16 16 kHz mono) and converting in memory. No audio committed.

My numbers match the implementer's within run-to-run noise:

| Measurement | Implementer (`001-comment.md`) | Reviewer (cold process) |
|---|---|---|
| cold process load wall | 0.0558 s | **0.0575 s** |
| `load_seconds` (`Whistle(...)`) | 0.03198 s | **0.03163 s** |
| clip5 wall / ttft / decode_tps | 0.2317 s / 113.8 / 213.6 | **0.2304 s / 112.5 / 220.1** |
| clip30 wall / ttft / decode_tps | 5.1399 s / 4297.4 / 164.6 | **5.2813 s / 4421.4 / 164.2** |
| 2.0 s silence | `''` / `''` / 0.0 / 0.0 | **`''` / `''` / 0.0 / 0.0** |

Transcripts are **character-for-character identical** to the implementer's, and my clip30
output equals the pre-existing `/tmp/whinspect/oneshot30.txt` reference exactly. clip5
(`He hoped there would be stew for dinner, turnips and carrots and bruised potatoes.`) is
coherent, correctly punctuated English for a 5 s speech clip; I could not listen to the
audio, but the text is the expected sentence and is stable across runs.

Repeat runs show real CPU variance (clip5 wall 0.196–0.309 s, clip30 wall 3.43–5.28 s;
`decode_tps` 164–449). All observations sit inside the ADR 0005 ranges (5 s: 0.20–0.37 s;
30 s: 2.04–5.45 s). The engine is genuinely running in-process; there is no subprocess hop.

### 2. Load-time pinning is real, not theatre — PASS (with a scoping note)

Two fresh-process divergences with the **real** package:

- **Engine half (real CDLL):** patched `model_store.env_overrides()` so `NEEDLE3_LIB_PATH`
  pointed at a byte-identical copy of the `.so` in `/tmp`, then called `load()`. The real
  `ctypes.CDLL` opened the `/tmp` copy and the adapter refused:

  ```
  A engine divergence: RAISED WhistleLoadError -> the Whistle engine loaded
  /tmp/review_whistle/divergent/libneedle.so, but ges2t pinned
  /home/matee/.local/share/ges2t/engine/3.2.0/libneedle.so: refusing to run against an
  unmanaged engine (ADR 0007)...
  A actually-opened library: /tmp/review_whistle/divergent/libneedle.so
  A engine still unloaded? True
  ```

- **Weights half:** wrapped `needle.Whistle` to ignore the managed path the adapter passed
  and open the `/tmp` copy; `load()` refused:

  ```
  B weights divergence: RAISED WhistleLoadError -> the Whistle engine loaded weights from
  /tmp/review_whistle/divergent/whistle.cact, but ges2t pinned
  /home/matee/.local/share/ges2t/models/whistle/2.0.0/whistle.cact: refusing to run against
  unmanaged weights (ADR 0007)...
  B engine still unloaded? True
  ```

In both cases `self._engine` stayed `None` after the refusal (fail-closed), so a subsequent
`transcribe()` raises `WhistleNotLoadedError`, not an `AttributeError`.

**Scoping note (not a defect):** with `needle` 3.1.3, `Whistle.weights` is literally
`os.fspath(weights)` of the argument the adapter passed, so the weights comparison is
tautological *for this package version*; it only bites if the package changes to source
weights elsewhere. It cannot, today, catch a stale `NEEDLE_WHISTLE_WEIGHTS`, because
`_apply_env_overrides()` and the explicit `weights=` argument both force the managed path —
i.e. the wrong-artifact path is unreachable by construction, and the assertion is
defence-in-depth against a package change. The **engine half is substantive** and I proved
it bites. This is worth recording for ticket 14 / a future needle upgrade, not fixing here.

### 3. Env overrides in exactly one place; no acquisition or network in load/transcribe — PASS

`grep` shows `model_store.env_overrides()` is called only from `_apply_env_overrides()`, and
`_apply_env_overrides()` is called only at the top of `load()`, **before** `_import_needle()`
and before `Whistle(...)`. `whistle.py` imports no network module (`urllib`, `http`,
`socket`, `requests`) at all.

Runtime proof: I armed `model_store.ensure_model`, `model_store.ensure_engine`,
`socket.socket.connect`, `socket.create_connection`, and `urllib.request.urlopen` to raise,
then loaded and transcribed a real clip:

```
load + transcribe succeeded with network + acquisition armed to raise
text: 'He hoped there would be stew for dinner, turnips and carrots and bruised potatoes.'
ttft_ms: 112.6 decode_tps: 228.3
```

`ensure_model()` delegates on the normal path to `model_store.ensure_model(progress=...)`
then `model_store.ensure_engine(progress=...)`, both keyword-only, `progress` forwarded as
the interface's `Callable[[float], None]`. See risk 3 for the override branch.

### 4. No module-import side effects — PASS (falsified)

`uv run pytest tests/test_whistle_batch.py::test_importing_the_adapter_does_not_pull_in_the_engine`
passes. I falsified it by inserting `import needle` at module top level; the test failed
with `AssertionError: needle was imported by the adapter`, then I reverted
(`git diff --quiet` clean). The AST of the module lists only
`os, time, array, collections.abc, pathlib, typing, ges2t*,` plus the lazy
`needle` inside `_import_needle`.

The `sys.modules["needle"] = None` mechanism is genuine:
`sys.modules[needle]=None -> ModuleNotFoundError: import of needle halted; None in sys.modules`.

### 5. Input validation; no truncation; stdlib conversion — PASS

- `sample_rate != 16000` → `WhistleInputError` naming `16000`; never resampled.
- `> 30 s` → `WhistleInputError` naming the 30 s ceiling; the code never slices samples.
- Exactly 30.000 s (480000 samples) is accepted **both** by `_validate_buffer` and through
  the real engine (`real 30.000s silence -> text=''`).
- The conversion is `array("f", samples).tobytes()`; the AST contains no `numpy`, `wave`, or
  `soxr` import.

### 6. Verbatim pass-through (ADR 0012) — PASS

`_transcription_from_engine` copies `raw["text"]` unchanged (no `.strip()`, no case change,
no trailing space) and passes `language`/`ttft_ms`/`decode_tps` through, `None` when absent.
The unit test feeds `"  Hallo WELT "` and asserts byte-for-byte equality. The result is a
frozen `TranscriptionResult` dataclass (fields `text, language, ttft_ms, decode_tps`), not a
tuple or dict. Real 2 s silence returned `text=""` and raised nothing.

### 7. `stream()` stub; `supports_streaming` — PASS

`inspect.isgeneratorfunction(WhistleTranscriber.stream)` is `False`, so `stream([])` raises
`NotImplementedError` **eagerly**, before any iteration. `supports_streaming` is `True`.
`isinstance(adapter, Transcriber)` is `True` and asserted in a test.

### 8. `WhistleConfig` sourced with an empty-mapping default — PASS / N/A to this diff

The two in-scope files take a `WhistleConfig` value object; the composition that reads
`config.engine_blocks.get(config.transcriber.engine, {})` belongs to ticket 11 and is not
present here. The pattern is documented in `whistle_config.py` and
`from_mapping` accepts an empty mapping. There is **no** `KeyError`-prone index anywhere in
the diff. No violation.

### 9. ADR 0019 and the widened mypy check — PASS (falsified)

`uv run mypy src/ges2t tests` → `Success: no issues found in 24 source files`
(mypy 2.4.0). I falsified the config by appending
`def _probe_unannotated(x): return x` to the module; mypy failed with
`error: Function is missing a type annotation  [no-untyped-def]`, then reverted clean
(`git diff --quiet` clean).

No positional-tuple returns; `_transcription_from_engine` immediately converts the raw
engine `Mapping[str, Any]` into a frozen dataclass; no implicit `Optional`; every definition
has `-> None`. `Any` appears only at the genuine untyped `needle`/engine boundary
(`_needle`, `_import_needle()`, the engine handle, the raw engine JSON) and is documented as
deliberate. No `# type: ignore` is needed anywhere in either file.

### 10. Scope and no-engine testability — PASS

`git diff --name-only main...HEAD -- src tests` is exactly:
`src/ges2t/transcribers/whistle.py`, `tests/test_whistle_batch.py`. Nothing else under `src/`.

I blocked the real `needle` package at interpreter level (a `sitecustomize` `meta_path`
finder that raises `ImportError` for `needle`/`needle.*`) and ran the **entire** suite with
`.venv/bin/python -m pytest`: `253 passed`. So the suite genuinely needs no engine installed,
not merely "does not import it at module scope". The fake fixture's
`sys.modules["needle"] = None` guard is real (proof in criterion 4).

### 11. Board row, ticket status, links, history — PASS

- `docs/project/README.md` row 8: status `in review`, branch
  `feat/whistle-transcriber-adapter-batch`, link `core-dictation-pipeline/whistle-transcriber-adapter-batch/ticket.md`.
- `ticket.md` `**Status:** in review`; all DoD boxes ticked.
- Every relative ticket/ADR/epic link in `ticket.md` resolves (`OK` for all ten targets).
- No git conflict markers (`<<<<<<<`, `>>>>>>>`, `|||||||`) in `src`, `tests`, or `docs`.
- Exactly two commits on top of `main`; `HEAD~2 == main == ed7fb44`; `merge-base(main, HEAD) == ed7fb44`; `main` untouched; branch worktree clean.

## Ticket 14 (streaming adapter) contract

The batch module is additive, and `stream()` is a genuine seam, not a rewrite trap.

- **Extension points to reuse, unchanged:**
  - `_import_needle() -> Any` — lazy, module-global cache in `whistle._needle`, returns a
    test-injected fake untouched. Ticket 14 must keep importing through this, not
    `import needle` directly.
  - `self._engine` — the constructed `needle.Whistle`, assigned only after
    `_assert_managed_artifacts()` passes. `_require_loaded()` is the accessor.
  - `_loaded_engine_path()` — the CDLL-path probe used by the load-time assertion. Ticket 14
    does not need it for streaming, but must not break it (the fake in
    `tests/test_whistle_batch.py` builds `agent.whistle._lib()`).
- **The `stream()` stub is a clean seam.** It is a normal (non-generator) function returning
  `Iterator[TranscriptionSegment]` and raising `NotImplementedError` eagerly. Ticket 14 turns
  its body into a generator (adding `yield`); the signature and the `Transcriber` Protocol do
  not change, and `supports_streaming` is already `True`. Callers currently learn
  "not implemented" on the call; after ticket 14 they learn it does work — the intended
  change.
- **Engine contract to know:** the real `Whistle` exposes
  `.stream(chunks, language=None, keywords=None)` yielding one dict per chunk plus a final
  one, with keys `text` (committed), `pending` (unconfirmed tail), `language`, `received`,
  `pass_ms`. Map to `TranscriptionSegment(text, language, is_final, pass_ms)`; the engine does
  **not** report `is_final`, so ticket 14 must derive it (last yield is final). Join committed
  segments with a single space, and never inject an empty/whitespace-only final segment
  (ADR 0012).
- **Loaded-engine handle facts ticket 14 must not trip over:**
  - The engine is **process-global and not thread-safe** (ADR 0005); one owner, one call at a
    time, no locking (ADR 0015).
  - `load()` is idempotent and is the only place `env_overrides()` is applied and the
    artifact assertion runs. Do **not** re-apply env or change paths after `load()`: needle
    caches `_loaded`/`_handle` process-globally, so a changed path would not reload the
    engine and the assertion would then compare the new expected path against the old handle.
  - `close()` only drops ges2t's reference (`self._engine = None`); it cannot unload the
    library, and `load()` afterwards re-binds cheaply because the library is still loaded.
  - `WhistleConfig.window_seconds` / `chunk_seconds` are currently unused by the adapter —
    they are ticket 14's knobs.
- **Test seam to copy:** `FakeWhistleEngine` / `FakeNeedleModule` (including the
  `sys.modules["needle"] = None` guard). If the fake grows a `.stream()`, keep
  `needle.agent.whistle._lib()._name` reachable so the load-time assertion stays exercised.

## Risks / follow-ups (non-blocking)

1. **Weights half of the assertion is tautological on needle 3.1.3** (criterion 2). The
   engine-library half is the substantive check. A future needle upgrade or a move to a
   hand-written `ctypes` wrapper (ADR 0007 path (b)) should re-evaluate whether the weights
   check still adds anything, and ticket 14 should not weaken the engine check.
2. **Package vs engine version skew:** `pyproject.toml` pins `cactus-needle==3.1.3` while the
   managed engine pin is `3.2.0` (ADR 0017). Pre-existing and out of scope, but worth a
   note so nobody assumes the two move together.
3. **`ensure_model()` with `model_path_override` skips `model_store.ensure_model`** (only the
   engine is ensured). This is a documented, defensible judgement call — the caller owning
   the weights should not trigger a different download — but it is a literal deviation from
   the DoD wording "delegates to `model_store.ensure_model` and `model_store.ensure_engine`".
   Ticket 11 constructs the adapter without an override, so the normal path is unaffected.
4. **`load_seconds` is set before `_assert_managed_artifacts`**, so a refused load leaves a
   non-`None` `load_seconds`. Cosmetic; a consumer reading it after a failure would see a
   time for a load that did not take effect.
5. **`_float32_bytes` is native-endian**, documented as little-endian on the pinned
   x86-64 target. Correct for the only supported platform (`linux-x86_64`); a future
   big-endian port would need an explicit byte order.

## Could not verify

- I could not **listen** to the clips, so "the transcript is correct for the audio" is
  judged by coherence, by stability across repeated runs and processes, and by exact
  agreement with the implementer's transcript and the pre-existing `oneshot30.txt`
  reference — not by a human ear.
- The exact rebase history (original base `32408eb`, the pre-rebase mypy failure) is a claim
  in `001-comment.md` I did not reconstruct; I verified the end state instead: two commits,
  `HEAD~2 == main == ed7fb44`, clean tree, no stray commits.
- No offline-network isolation beyond the runtime arming in criterion 3 (the machine has
  connectivity, so I proved no call site was reached rather than that the machine was
  offline).
