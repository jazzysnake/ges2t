# Whistle transcriber adapter (batch)

- **Priority:** P0
- **Status:** done
- **Complexity:** M
- **Epic:** ../epic.md

## Current state / context

We have interfaces (`../core-interfaces-and-fakes/ticket.md`), a pinned model store
(`../model-store-and-verification/ticket.md`), and a validated engine config block
(`../whistle-engine-config/ticket.md`). Nothing actually transcribes yet.

This ticket implements `WhistleTranscriber`, the **only** module in the codebase allowed to
import `needle` (ADR 0010). It implements the **batch** path only; the streaming path
is `../../live-dictation-ux/whistle-streaming-adapter/ticket.md`, which extends this same
file.

Measured facts to design against (ADR 0005, ADR 0007, ADR 0011 — read all three):

- `needle` calls the engine `CDLL` **in-process**; there is no subprocess hop. Its
  `Whistle` class has `.transcribe(audio, language=None, keywords=None, word_timestamps=False)`
  and returns a dict including `text`, `language`, `ttft_ms`, `decode_tps`.
- It accepts WAV paths, `bytes` (raw **little-endian float32**), `array('f')`, objects with
  `.tobytes()` + `.dtype`, or an iterable of floats. Passing int16 bytes is silently
  interpreted as garbage.
- Since we set `NEEDLE_WHISTLE_WEIGHTS` and `NEEDLE3_LIB_PATH` (ADR 0017), it loads our
  artifacts and never needs the network.
- 5 s one-shot: 0.20–0.37 s wall, `ttft` 85–249 ms. 30 s one-shot: 2.04–5.45 s wall.
- Audio contract: 16 kHz mono float32; **30 s maximum per call**.

## Definition of done

- [x] `src/ges2t/transcribers/whistle.py` defines `class WhistleTranscriber` satisfying the
      `Transcriber` Protocol (assert it with `isinstance` in a test):
      - `name = "whistle"`, `supports_streaming = True`;
      - `__init__(self, config: WhistleConfig, model_path_override: Path | None = None)`;
      - `ensure_model(progress=None) -> Path` delegates to `model_store.ensure_model` and
        `model_store.ensure_engine`; it is the only place this class may touch the network,
        and it must not download anything else;
      - `load() -> None` applies `model_store.env_overrides()` to `os.environ`, then
        constructs `cactus_needle.Whistle(weights=<managed .cact path>)`, and **asserts** that
        the weights and engine it is running are the managed, verified paths (ADR 0007) —
        raising a clear error otherwise rather than silently fetching something else;
      - `transcribe(buffer: AudioBuffer) -> TranscriptionResult` converts the samples to raw
        little-endian float32 **without a WAV round-trip** (WAV parsing was measured to cost
        0.036 s per 5 s and 0.218 s per 30 s — pure waste), calls the engine, and returns a
        `TranscriptionResult` carrying `text`, `language`, `ttft_ms`, `decode_tps`;
      - `close() -> None` releases the model;
      - calling `transcribe()` before `load()` raises a clear error, not an `AttributeError`.
- [x] Input validation: a buffer longer than 30 s raises a clear error naming the limit; a
      buffer whose `sample_rate` is not 16000 raises a clear error. Neither silently truncates.
- [x] Silence produces an **empty string**, not an exception and not garbage — this is a
      normal outcome (ADR 0005) and must not be laundered into a fake word.
- [x] `text` is returned **exactly as the engine produced it** — no stripping, no
      capitalisation, no trailing space (ADR 0012; the space is added by the injector).
- [x] `tests/test_whistle_batch.py` runs **without the engine installed**: it monkeypatches
      the module-level `needle` reference with a fake object exposing `Whistle`, and
      asserts the conversion path (a 16 kHz float32 buffer arrives at the fake as raw
      float32 bytes of the expected length), the 30 s and sample-rate validation, the
      before-`load()` error, and pass-through of `text`/`language`/timings unchanged.
- [x] `uv run pytest tests/test_whistle_batch.py` passes and `uv run pytest` stays green.
- [x] **Manual, once, on the reference machine**, reported in `001-comment.md`: after
      `ges2t setup`, transcribe a real 5-second speech clip through this adapter and report
      wall time, the returned text, and `ttft_ms`/`decode_tps`. This is the first proof the
      engine actually runs under our plumbing.

- [x] `uv run mypy src/ges2t` exits 0: every definition fully annotated and every
      signature interface-typed — ADR 0019.

## Notes

- **Progress-callback shape is fixed by the interface, and ticket 5 got it wrong.**
  `interfaces.py` defines `ProgressCallback = Callable[[float], None]` (a fraction), while the
  merged `model_store.py` defines its own `ProgressCallback = Callable[[str], None]` (a
  message). `ensure_model()` is the seam between them, so this is a real `mypy` failure at
  this integration point, not a style choice. They must agree on `Callable[[float], None]` —
  the downloader knows content-length and can report a fraction. Do **not** import
  `interfaces.py` into `model_store.py` to share the alias; matching *structural* types is
  sufficient and preserves the anti-coupling rule.
- Import `needle` lazily **inside** `load()` (and `ensure_model()` if it needs it), not
  at module import time. `tests/test_whistle_batch.py` monkeypatches the module attribute, so
  bind it through a module-level name such as `_needle` that tests can replace.
- Do not add `numpy`. Convert with `array.array('f', samples).tobytes()` (stdlib), which the
  probe verified the package accepts as raw float32 bytes.
- `WhistleConfig.language` is passed through as the `language` argument; empty string means
  auto-detect. Do not translate or map language codes.
- Do **not** implement `stream()` beyond a `NotImplementedError` stub in this ticket — the
  streaming adapter ticket owns it, and implementing it here would make that ticket a
  rewrite rather than an addition.
- The engine is process-global and not thread-safe (ADR 0005): do not add locking that
  implies concurrent use is supported. One owner, one call at a time (ADR 0015).

## Related tickets

- depends on: ../core-interfaces-and-fakes/ticket.md,
  ../model-store-and-verification/ticket.md, ../whistle-engine-config/ticket.md
- blocks: ../dictation-pipeline-batch/ticket.md,
  ../../live-dictation-ux/whistle-streaming-adapter/ticket.md
- related ADRs: ../../../adr/0005-stt-backend-cactus-whistle.md,
  ../../../adr/0007-whistle-integration-via-cactus-needle-package.md,
  ../../../adr/0010-transcriber-interface-design.md,
  ../../../adr/0012-verbatim-output-and-trailing-space.md
