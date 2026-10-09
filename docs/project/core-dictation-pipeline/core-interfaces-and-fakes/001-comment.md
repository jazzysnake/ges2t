# 001 — Core interfaces and fakes: implementation notes

Ticket: `ticket.md` (core-interfaces-and-fakes) · Branch: `feat/core-interfaces-and-fakes`

## What was created

- `src/ges2t/interfaces.py` — the shared vocabulary (Protocols + frozen data classes).
- `tests/fakes.py` — `FakeAudioSource`, `FakeTranscriber`, `FakeInjector`, `FakeStatusUI`.
- `tests/test_interfaces.py` — the tests the ticket names, plus a few fake-behaviour tests.
- `tests/__init__.py` — package marker so later tickets can `from tests.fakes import ...`.

No `config.py`, `model_store.py`, `service.py`, `doctor.py`, `pipeline.py`, `audio/`,
`injectors/`, `transcribers/`, or `ui/` were created (other tickets own those). The parallel
`config-loading` worktree was not touched.

## Exact final signatures

```python
ProgressCallback = Callable[[float], None]

@dataclass(frozen=True)
class AudioBuffer:
    samples: Sequence[float]
    sample_rate: int

@dataclass(frozen=True)
class TranscriptionSegment:
    text: str
    language: str | None
    is_final: bool
    pass_ms: float | None

@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: str | None
    ttft_ms: float | None
    decode_tps: float | None

@runtime_checkable
class Transcriber(Protocol):
    name: str
    supports_streaming: bool
    def ensure_model(self, progress: ProgressCallback | None = None) -> Path: ...
    def load(self) -> None: ...
    def transcribe(self, buffer: AudioBuffer) -> TranscriptionResult: ...
    def stream(self, chunks: Iterable[AudioBuffer]) -> Iterator[TranscriptionSegment]: ...
    def close(self) -> None: ...

@runtime_checkable
class AudioSource(Protocol):
    def start(self) -> None: ...
    def chunks(self, chunk_seconds: float) -> Iterator[AudioBuffer]: ...
    def stop(self) -> AudioBuffer: ...

@runtime_checkable
class Injector(Protocol):
    def inject(self, text: str) -> None: ...

@runtime_checkable
class StatusUI(Protocol):
    def idle(self) -> None: ...
    def recording(self) -> None: ...
    def transcribing(self) -> None: ...
    def error(self, message: str) -> None: ...
    def partial(self, text: str) -> None: ...
```

`Iterable`, `Iterator`, `Sequence`, and `Callable` are imported from `collections.abc`.
`__all__` exports the eight public names (`ProgressCallback` included).

## Judgement calls

1. **`AudioBuffer.samples` is typed `Sequence[float]`** — the ticket's literal wording. This
   keeps `interfaces.py` free of `numpy`, and it accepts both `list[float]` and
   `array.array("f")`; typeshed models `array` as `MutableSequence[_T]`, so ticket 6's
   `array("f", ...)` satisfies it (verified with a mypy probe). `str`/`bytes` are also
   technically `Sequence`, but the value contract (`float32 mono in [-1, 1]`) is documented
   in the docstring, and enforcing it is the transcriber boundary's job per ADR 0005.
2. **`progress` is typed `ProgressCallback = Callable[[float], None]`** with the fraction in
   `[0.0, 1.0]`. The ticket left it as bare `progress=None`. This alias is the signature
   ticket 5 (`model_store`) and ticket 8 (the Whistle adapter) should use for
   `ensure_model(progress=...)`, so mypy's structural check passes without negotiation.
3. **All three data classes are `frozen=True`.** The ticket explicitly requires frozen only
   for `AudioBuffer`, but ADR 0019 calls return classes "frozen dataclasses", and immutability
   is the right default for data-in/data-out values. This is a strengthening, not a
   deviation; nothing depends on mutating them.
4. **Optional fields are required, not defaulted.** `language`, `pass_ms`, `ttft_ms`, and
   `decode_tps` are `| None` but have no default value, so a caller states them explicitly.
   This matches "write `str | None`, never an implicit Optional".
5. **Fakes record via a `calls: list[str]`** (method names in order) plus a typed per-method
   list where a test needs the argument (`transcribed`, `streamed`, `injected`, `errors`,
   `partials`, `chunk_seconds_seen`, `ensure_model_progress`). Every fake also accepts
   `raise_on_<method>=<Exception>` and exposes it as a settable attribute, so a test can make
   any operation raise on demand. `FakeTranscriber` has four: `raise_on_ensure_model`,
   `raise_on_load`, `raise_on_transcribe`, `raise_on_stream`. `FakeAudioSource` has
   `raise_on_start`/`raise_on_chunks`/`raise_on_stop`, and `FakeInjector` has `error`.
6. **`FakeTranscriber.stream()` is not a generator function.** It records the call and raises
   eagerly, then returns a ready iterator. A generator body would defer both until first
   `next()`, which makes "was `stream()` called?" and "did it raise?" awkward to assert.
   `FakeAudioSource.chunks()` *is* a generator (it must be, to model lazy capture).
7. **`FakeAudioSource.stop()` returns the concatenation of the configured buffers** (or an
   explicit `stop_buffer`) at the configured `sample_rate`, modelling "everything captured,
   including already-yielded audio".
8. **`tests/__init__.py` is added** so future tickets import the fakes as
   `from tests.fakes import ...`. Without it (and with no `conftest.py`), pytest puts
   `tests/` on `sys.path`, which supports only `from fakes import ...`. `from tests.fakes`
   is the convention this ticket documents.

## Surprising finding the orchestrator must know

**The `cactus-needle` distribution installs its top-level module as `needle`, not
`cactus_needle`.** Verified in this worktree:

```
$ ls .venv/lib/python3.14/site-packages/ | grep -i needle
cactus_needle-3.1.3.dist-info
needle
$ cat .venv/lib/python3.14/site-packages/cactus_needle-3.1.3.dist-info/top_level.txt
needle
$ uv run python -I -c "import needle; print(needle.__version__)"
3.1.3
$ uv run python -I -c "import cactus_needle"
ModuleNotFoundError: No module named 'cactus_needle'
```

ADR 0007 already says `needle.Needle` and `needle/_worker.py`, so the ADR is consistent; it
is **ADR 0010, this ticket's text, and the Whistle adapter tickets (8 and 14)** that say
`cactus_needle`. Ticket 8 in particular says "monkeypatches the module-level `cactus_needle`
reference" and ticket 14 says "monkeypatched module-level `_cactus_needle`". Those tickets
must bind `needle` (e.g. `import needle as _cactus_needle` or, better, a module-level
`_needle: types.ModuleType | None`), or they will fail with `ModuleNotFoundError`.

I did not change any ticket or ADR. I handled it in `test_interfaces.py` by asserting **both**
`cactus_needle` and `needle` stay absent after `import ges2t.interfaces`, so the rule keeps
its meaning regardless of which name is imported. **Flag for ticket 8/14: the real module
name is `needle`.**

A second, smaller finding: `numpy` is not installed in this environment at all (consistent
with ADR 0007 and the scaffold notes), so its absence is trivially true — but it is still
asserted as the ticket requires.

## Verification (real output)

```
$ uv run pytest
platform linux -- Python 3.14.7, pytest-9.1.1, pluggy-1.6.0
rootdir: /home/matee/Workspace/Personal/ges2t/.worktrees/core-interfaces-and-fakes
collected 20 items

tests/test_interfaces.py .................                               [ 85%]
tests/test_smoke.py ...                                                  [100%]

============================= 20 passed in 0.17s ==============================   # exit 0

$ uv run mypy src/ges2t
Success: no issues found in 3 source files                                        # exit 0

$ uv run pytest tests/test_interfaces.py -q
.................                                                        [100%]
17 passed in 0.16s                                                                # exit 0
```

Extra, not required by the DoD but part of the ADR 0019 bar ("tests are fully annotated too"):

```
$ uv run mypy tests
Success: no issues found in 4 source files                                        # exit 0
```

The import test runs `sys.executable -I -c "import ges2t.interfaces; ..."` in a fresh,
isolated interpreter and inspects `sys.modules`; `dataclasses` (imported by `interfaces`) is
asserted **present** as a control, so the absence assertions are not vacuous. `gi` (3.14
system site-packages) and `needle` are both importable here, so those two negatives are
meaningful rather than incidental.

## Anything else the orchestrator should know

- Later tickets must import the fakes as `from tests.fakes import ...` and must not redefine
  them (ADR 0014).
- `AudioBuffer` is a pure value object: it carries `sample_rate` and does **not** validate
  16 kHz. The 16 kHz/30 s checks belong in the Whistle adapter (tickets 8, 14).
- `AudioSource` has exactly the three methods the ticket names; no callback variant was
  added. `Transcriber` has no `warmup()`, no `language` property, and no async method.
- `Transcriber.stream()` returns an `Iterator` and deliberately contains no windowing; the
  5 s window/commit logic is ticket 13 (`streaming-window-engine`) against the
  `StreamingDecoder`/`drive()` seam.
- No deviation from the ticket's required names: everything downstream is written against
  exactly the names listed above.
