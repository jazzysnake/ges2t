# 002 — Review: core interfaces and test fakes

- **Ticket:** [ticket.md](ticket.md) (core-interfaces-and-fakes)
- **Branch:** `feat/core-interfaces-and-fakes` @ `a297f14`
- **Implementer notes reviewed:** [001-comment.md](001-comment.md) (claims checked, not trusted)
- **Spec:** [ADR 0010](../../../adr/0010-transcriber-interface-design.md),
  [ADR 0019](../../../adr/0019-code-style-typing-and-interfaces.md),
  [ADR 0014](../../../adr/0014-testing-and-quality-bar.md)
- **Reviewer:** independent review run (fresh checkout state, `git status` clean at start and end)

## Verdict

**pass**

No blocking findings. Signature fidelity is exact against the ticket and ADR 0010, the
import-isolation test is a real assertion (falsified and re-verified), scope is exactly the
four permitted paths plus the board/ticket status rows, and the ADR 0019 bar holds. A handful
of non-blocking accuracy/robustness notes and downstream follow-ups are recorded below.

## Signature-fidelity table

Checked against the DoD in [ticket.md](ticket.md) and ADR 0010. "Actual" values were read
from `src/ges2t/interfaces.py` and confirmed by runtime introspection
(see [Evidence 4](#4-signature-introspection)).

| Required name | Ticket spec | Actual code | Match |
|---|---|---|---|
| `AudioBuffer` | frozen dataclass; `samples` float32 mono sequence; `sample_rate: int`; carries, does not validate | `@dataclass(frozen=True)`, `samples: Sequence[float]`, `sample_rate: int`; no validation in `__post_init__` | ✅ match |
| `TranscriptionSegment` | dataclass; `text: str`, `language: str \| None`, `is_final: bool`, `pass_ms: float \| None` | all four exact; `frozen=True` (strengthening, see note N2) | ✅ match |
| `TranscriptionResult` | dataclass; `text: str`, `language: str \| None`, `ttft_ms: float \| None`, `decode_tps: float \| None` | all four exact; `frozen=True` (N2) | ✅ match |
| `Transcriber` | `runtime_checkable` Protocol; `name: str`; `supports_streaming: bool`; `ensure_model(progress=None) -> Path`; `load() -> None`; `transcribe(buffer: AudioBuffer) -> TranscriptionResult`; `stream(chunks: Iterable[AudioBuffer]) -> Iterator[TranscriptionSegment]`; `close() -> None` | `runtime_checkable=True`; member set is exactly `{name, supports_streaming, ensure_model, load, transcribe, stream, close}`; `progress: ProgressCallback \| None = None` with `ProgressCallback = Callable[[float], None]` (typing of the ticket's bare `progress=None`, not a deviation); no `warmup`, no `language` property, no async | ✅ match |
| `AudioSource` | `runtime_checkable`; **exactly** `start() -> None`, `chunks(chunk_seconds: float) -> Iterator[AudioBuffer]`, `stop() -> AudioBuffer`; no callback variant | `runtime_checkable=True`; member set is exactly `{start, chunks, stop}` — no extra method, no callback variant | ✅ match |
| `Injector` | `runtime_checkable`; `inject(text: str) -> None` | `runtime_checkable=True`; member set is exactly `{inject}` | ✅ match |
| `StatusUI` | `runtime_checkable`; `idle()`, `recording()`, `transcribing()`, `error(message: str)`, `partial(text: str)` | `runtime_checkable=True`; member set is exactly `{idle, recording, transcribing, error, partial}` | ✅ match |

Names in the ticket's DoD for later tickets (`config`, `model_store`, `pipeline`, `service`,
`doctor`, streaming) are all satisfied: the eleven downstream tickets can be written against
these exact identifiers.

## Per-criterion findings

### DoD 1 — `src/ges2t/interfaces.py`, no backend/GUI/audio import

Pass. Imports are `__future__`, `collections.abc` (`Callable, Iterable, Iterator, Sequence`),
`dataclasses`, `pathlib`, `typing` — standard library only. No `cactus_needle`, `needle`,
`gi`, `numpy`, `sounddevice`, `pystray`, `pydotool`, `subprocess`. The "adapters are the only
modules allowed to import a backend library" rule (ADR 0010) is respected.

### DoD 2 — fakes

Pass. `tests/fakes.py` defines `FakeAudioSource`, `FakeTranscriber`, `FakeInjector`,
`FakeStatusUI`; each records calls in `calls: list[str]` in order, plus per-method argument
lists (`chunk_seconds_seen`, `started`/`stopped`, `transcribed`, `streamed`,
`ensure_model_progress`, `loaded`/`closed`, `injected`, `errors`, `partials`,
`idle_calls`/`recording_calls`/`transcribing_calls`). `FakeTranscriber` is constructible
streaming-capable or not via `supports_streaming`, and can be told to raise through
`raise_on_ensure_model`/`raise_on_load`/`raise_on_transcribe`/`raise_on_stream` (settable
after construction). `tests/__init__.py` makes `from tests.fakes import ...` importable from
every future test module, as ADR 0014 requires.

I executed the fakes exactly as tickets 6, 7, 13 and 15 describe needing them — all four
usages succeed ([Evidence 3](#3-downstream-usability-probe)):

- **Ticket 6** (`PwRecordAudioSource`): can `isinstance(x, AudioSource)` at runtime;
  `FakeAudioSource` yields its configured buffers through `chunks()` (a lazy generator) and
  `stop()` returns the concatenated "everything captured" buffer at the configured rate.
- **Ticket 7** (`YdotoolInjector`): `FakeInjector.injected` records payloads assertably and
  `error` makes `inject` raise; `isinstance(x, Injector)` works.
- **Ticket 13** (`streaming.drive`): both `Iterable[AudioBuffer]` and
  `Iterator[TranscriptionSegment]` element types are satisfiable from the fakes; the
  deterministic fake *decoder* is ticket 13's own artifact (see follow-up F3).
- **Ticket 15** (`StreamingDictationSession`): `FakeAudioSource` + `FakeTranscriber` are
  sufficient for the plain capture/stream plumbing and for `finish()` returning the fake's
  text; see F3 for the one wording mismatch.

**All four tickets can import these fakes without redefining anything** — the classes are
exhaustive for the flows the tickets describe. The only caveat is F3 (a `StreamingDecoder`
double is not and should not be one of these four, because ticket 13 owns it).

### DoD 3 — `tests/test_interfaces.py` asserts the three things the ticket names

Pass, all three present and non-vacuous:

1. `isinstance(fake, <Protocol>)` for all four protocols (four separate tests). Because
   `Transcriber` has non-method members, `isinstance` genuinely checks `name` and
   `supports_streaming` presence, so this test is not purely structural for the transcriber.
2. `FakeTranscriber(supports_streaming=False).supports_streaming is False`, a
   streaming-capable one reports `True`, and the default is `True`.
3. Import isolation: a **fresh isolated interpreter** (`sys.executable -I -c`, no
   `PYTHONPATH`/user site-packages) imports `ges2t.interfaces` and inspects `sys.modules`;
   `dataclasses` is asserted **present** as a control so the snapshot cannot be vacuously
   empty. `cactus_needle`, `needle`, `gi` and `numpy` are asserted absent.

`gi` and `needle` are both importable in this environment (verified), so two of the four
negative assertions carry real weight. The `cactus_needle` and `numpy` assertions are
currently vacuous because neither distribution is installed — noted as N1, disclosed honestly
in [001-comment.md](001-comment.md).

### DoD 4 — test commands

Pass:

- `uv run pytest` → **20 passed** (17 in `tests/test_interfaces.py`, 3 in `tests/test_smoke.py`).
- `uv run pytest tests/test_interfaces.py` → **17 passed**.
- `uv run mypy src/ges2t` → `Success: no issues found in 3 source files`.
- Bonus, matching the ADR 0019 "tests too" reading: `uv run mypy tests` →
  `Success: no issues found in 4 source files`.

### DoD 5 — ADR 0019 (typing bar)

Pass.

- No positional-tuple return values anywhere in `src/ges2t/interfaces.py` or `tests/fakes.py`.
  `_concatenate` returns `AudioBuffer`; `FakeTranscriber.stream` returns
  `Iterator[TranscriptionSegment]`.
- No bare `Any` in any signature. The only textual `Any` occurrence is inside a docstring
  ("Any of `raise_on_...`"). No `Optional[...]` (PEP 604 `X | None` used throughout).
- Every definition is annotated, including `-> None` on all fakes' `__init__` and every
  `StatusUI`/`AudioSource` method.
- Interface-typed signatures: fakes import from `ges2t.interfaces` and use `AudioBuffer`,
  `TranscriptionSegment`, `TranscriptionResult`, `ProgressCallback`; no concrete adapter class
  appears in any signature.
- `Callable` parameters state their full signature (`ProgressCallback = Callable[[float], None]`).
- The config bites: adding an untyped `def untyped_probe(x)` to `interfaces.py` produces
  `error: Function is missing a type annotation [no-untyped-def]` ([Evidence 2](#2-mypy-effectiveness-probe)),
  so `disallow_untyped_defs`/`no_implicit_optional` in `pyproject.toml` are genuinely active.

### Scope

Pass. `git diff --name-status 5edf284..a297f14` lists exactly:

```
M  docs/project/README.md
A  docs/project/core-dictation-pipeline/core-interfaces-and-fakes/001-comment.md
M  docs/project/core-dictation-pipeline/core-interfaces-and-fakes/ticket.md
A  src/ges2t/interfaces.py
A  tests/__init__.py
A  tests/fakes.py
A  tests/test_interfaces.py
```

No `model_store.py`, `service.py`, `doctor.py`, `pipeline.py`, `audio/`, `injectors/`,
`transcribers/`, or `ui/` exists. The stray `src/ges2t/_probe.py` from the interrupted prior
run is gone; `git status --porcelain` is empty at the start and the end of this review.

### Board row + ticket status

Pass. `docs/project/README.md` row 2 reads `in review` with branch
`feat/core-interfaces-and-fakes`; `ticket.md` header reads `- **Status:** in review`. The two
agree. All relative links touched by the change resolve:
`../project-scaffold/ticket.md`, `../config-loading/ticket.md`,
`../model-store-and-verification/ticket.md`, `../audio-capture-pw-record/ticket.md`,
`../ydotool-injector/ticket.md`, `../../live-dictation-ux/streaming-window-engine/ticket.md`,
`../../../adr/0010-...`, `../../../adr/0014-...`, `../epic.md` (epic files exist in both epic
folders).

### `001-comment.md` accuracy

Subtracted and re-checked claim by claim:

| Claim | Verdict |
|---|---|
| `__all__` exports eight public names, `ProgressCallback` included | ✅ **true** — `len(__all__) == 8`; the eight are exactly `AudioBuffer, AudioSource, Injector, ProgressCallback, StatusUI, Transcriber, TranscriptionResult, TranscriptionSegment` |
| All three data classes are `frozen=True` (strengthening beyond the ticket) | ✅ true and correctly labelled a judgement call |
| Optional fields are required, not defaulted | ✅ true — no dataclass field has a default |
| `ensure_model` typed via a new `ProgressCallback` alias | ✅ true |
| `FakeAudioSource.stop()` concatenates configured buffers / honours `stop_buffer` | ✅ true |
| `FakeTranscriber.stream()` is not a generator, records and raises eagerly | ✅ true |
| Every fake accepts `raise_on_<method>=<Exception>` | ⚠️ **overstated** (N3) — `FakeStatusUI` has no raise hook and `FakeInjector` uses `error`, not `raise_on_inject`. The same bullet self-corrects by listing what each fake actually has, so the effective content is right |
| `tests/__init__.py` added so later tickets import `from tests.fakes import ...` | ✅ true |
| `cactus-needle` installs top-level `needle`, not `cactus_needle` | ✅ **true, independently verified**: `cactus_needle-3.1.3.dist-info/top_level.txt` contains `needle`; `uv run python -I -c "import needle"` succeeds and `import cactus_needle` raises `ModuleNotFoundError`. The note is a genuine and useful finding for tickets 8/14 |
| `numpy` is not installed, so its absence is trivially true | ✅ true, and honestly disclosed |
| `gi` and `needle` are importable, so those negatives are meaningful | ✅ true |
| "No deviation from the ticket's required names" | ✅ true |

No false claim that would change the verdict; N3 is a wording nit in an internal note.

### Tests exercise behaviour, not implementation details

Mostly pass, one structural caveat (N4). Behavioural tests do real work: frozen-ness raises
`FrozenInstanceError`; `AudioBuffer` accepts a non-16 kHz rate; `FakeAudioSource.stop()` is
asserted to return *everything captured including already-yielded audio*; the transcriber's
raise hook is asserted to fire and to leave prior state intact; `ensure_model` is asserted to
call the progress sink with `1.0`; `stream()` is asserted to preserve the exact configured
segments. The protocol-conformance tests are necessarily structural (`isinstance`), but each
fake's behaviour is covered by a separate test, so removing a method body in a way that keeps
the attribute present would be caught elsewhere for `FakeAudioSource`, `FakeInjector`,
`FakeStatusUI` and `FakeTranscriber` alike.

## Evidence

All commands were run from
`/home/matee/Workspace/Personal/ges2t/.worktrees/core-interfaces-and-fakes` at `a297f14`.

### 1. Baseline commands

```
$ uv run pytest
....................                                                     [100%]
20 passed in 0.17s

$ uv run pytest tests/test_interfaces.py -q
.................                                                        [100%]
17 passed in 0.13s

$ uv run mypy src/ges2t
Success: no issues found in 3 source files

$ uv run mypy tests
Success: no issues found in 4 source files
```

### 2. mypy-effectiveness probe

Inserted a deliberately untyped function at the end of `src/ges2t/interfaces.py`:

```
$ uv run mypy src/ges2t
src/ges2t/interfaces.py:203: error: Function is missing a type annotation  [no-untyped-def]
Found 1 error in 1 file (checked 3 source files)
```

Reverted by restoring the file from a saved copy; `git status --porcelain` reported nothing
afterwards.

### 3. Downstream-usability probe

`/tmp/probe_fakes.py` (outside the repository) exercised the four fakes the way tickets 6, 7,
13 and 15 describe, including streaming-capable and non-capable `FakeTranscriber`,
`raise_on_stream`, and call recording:

```
$ PYTHONPATH=. uv run python /tmp/probe_fakes.py
PROBE OK: all ticket 6/7/13/15 fake usages succeed
```

### 4. Signature introspection

`/tmp/probe_introspect.py` (outside the repository) enumerated the published surface:

```
__all__ = ['AudioBuffer', 'AudioSource', 'Injector', 'ProgressCallback', 'StatusUI',
           'Transcriber', 'TranscriptionResult', 'TranscriptionSegment'] count = 8
AudioBuffer: frozen=True fields=[('samples', collections.abc.Sequence[float]), ('sample_rate', <class 'int'>)]
TranscriptionSegment: frozen=True fields=[('text', <class 'str'>), ('language', str | None), ('is_final', <class 'bool'>), ('pass_ms', float | None)]
TranscriptionResult: frozen=True fields=[('text', <class 'str'>), ('language', str | None), ('ttft_ms', float | None), ('decode_tps', float | None)]
Transcriber: runtime_checkable=True
    close, ensure_model, load, name: str, stream, supports_streaming: bool, transcribe
AudioSource: runtime_checkable=True
    chunks, start, stop
Injector: runtime_checkable=True
    inject
StatusUI: runtime_checkable=True
    error, idle, partial, recording, transcribing
ensure_model sig: (self, progress: 'ProgressCallback | None' = None) -> 'Path'
stream sig: (self, chunks: 'Iterable[AudioBuffer]') -> 'Iterator[TranscriptionSegment]'
chunks sig: (self, chunk_seconds: 'float') -> 'Iterator[AudioBuffer]'
```

### 5. Import-isolation falsification attempt (the required probe)

Environment first:

```
$ for m in numpy gi needle cactus_needle ges2t dataclasses; do
    uv run python -I -c "import $m" 2>/dev/null && echo "$m: IMPORTABLE" || echo "$m: NOT importable"
  done
numpy: NOT importable
gi: IMPORTABLE
needle: IMPORTABLE
cactus_needle: NOT importable
ges2t: IMPORTABLE
dataclasses: IMPORTABLE
```

**Probe A (the exact probe requested):** added `import numpy` to
`src/ges2t/interfaces.py`, ran the isolation test.

```
$ uv run pytest tests/test_interfaces.py::test_importing_interfaces_does_not_pull_in_banned_modules -q
ERROR: found no collectors for ...::test_importing_interfaces_does_not_pull_in_banned_modules
...
tests/test_interfaces.py:17: in <module>
    from ges2t.interfaces import (
src/ges2t/interfaces.py:28: in <module>
    import numpy  # PROBE
E   ModuleNotFoundError: No module named 'numpy'
1 error in 0.05s
```

The test **did fail**, but for the wrong reason: `numpy` is not installed, so the module
could not be imported at all and the test never reached the assertion. Probe A therefore does
**not** by itself prove the assertion path bites. It is not a blocking finding — the ticket's
numpy leg is inherently vacuous in an environment without numpy, as
[001-comment.md](001-comment.md) discloses — but it did require a second probe.

**Probe B (the assertion path itself):** added `import gi` (installed) to
`src/ges2t/interfaces.py`, ran the same test.

```
$ uv run pytest tests/test_interfaces.py::test_importing_interfaces_does_not_pull_in_banned_modules -q
    def test_importing_interfaces_does_not_pull_in_banned_modules() -> None:
        present = _modules_present_after_importing_interfaces(
            (*BANNED_MODULES, CONTROL_MODULE)
        )
        assert present[CONTROL_MODULE] is True, "the sys.modules snapshot was empty"
        for module in BANNED_MODULES:
>           assert present[module] is False, f"{module} was imported by ges2t.interfaces"
E           AssertionError: gi was imported by ges2t.interfaces
E           assert True is False
tests/test_interfaces.py:75: AssertionError
1 failed in 0.25s
```

That is the exact assertion firing, with the control assertion passing first. So the
isolation test is **real, not incidental**.

Both probes were fully reverted:

```
$ git status --porcelain
(no output)

$ uv run pytest tests/test_interfaces.py::test_importing_interfaces_does_not_pull_in_banned_modules -q
1 passed in 0.11s
```

## Non-blocking observations

- **N1 — the `numpy`/`cactus_needle` legs are vacuous here.** They assert absence of
  distributions that are not installed, so they would pass even if `interfaces.py` tried to
  import them (in `cactus_needle`'s case) or fail for an unrelated reason (in `numpy`'s case,
  per Probe A). `gi` and `needle` are the load-bearing legs, and they are real. No action
  required for this ticket; worth remembering when the environment gains numpy.
- **N2 — `TranscriptionSegment`/`TranscriptionResult` are frozen** although the ticket only
  demands that for `AudioBuffer`. This is a strengthening consistent with ADR 0019's
  "frozen dataclasses" rule for return classes. Correct, but downstream tickets must not try
  to mutate a segment or result in place.
- **N3 — "Every fake also accepts `raise_on_<method>`" is overstated** in
  [001-comment.md](001-comment.md) §5: `FakeStatusUI` has no raise hook and `FakeInjector`
  uses `error`. The same bullet self-corrects, so this is a note-quality nit only.
- **N4 — protocol-conformance tests are structural.** `isinstance` against the method-only
  protocols (`AudioSource`, `Injector`, `StatusUI`) would still pass if a fake's method body
  were a stub, because `runtime_checkable` only checks member presence. The per-fake behaviour
  tests cover this, so the suite as a whole is behaviour-tested.
- **N5 — `issubclass` cannot be used against `Transcriber`.** Because `Transcriber` is
  `runtime_checkable` *and* has non-method members, `issubclass(X, Transcriber)` raises
  `TypeError: Protocols with non-method members don't support issubclass()`. This is inherent
  to the ticket's required shape (`name`/`supports_streaming` as members), and every caller
  should use `isinstance`. Noted so a later ticket does not reach for `issubclass`.

## Risks / follow-ups for tickets 6, 7, 13, 15

- **Ticket 6 — [audio-capture-pw-record](../audio-capture-pw-record/ticket.md).** The fake
  supports the required flow. Two real-source details are *not* the fake's job and must be
  the ticket's own tests: `chunks()` must stay lazy, and `stop()` must return
  `stop_buffer`-equivalent "everything captured" even when zero samples arrived. The fake's
  `stop()` returns an empty `AudioBuffer` for zero captured buffers, which matches the ticket's
  zero-sample DoD — so a test built on the fake is a fair model of it.
- **Ticket 7 — [ydotool-injector](../ydotool-injector/ticket.md).** The fake records
  `injected` and raises via `error`; that covers the trailing-space table's observation points.
  The injector's `runner` seam is ticket 7's own and should not be added to `FakeInjector`.
- **Ticket 13 — [streaming-window-engine](../../live-dictation-ux/streaming-window-engine/ticket.md).**
  `drive()` needs a `StreamingDecoder` (`process`/`flush`), which is **not** one of the four
  fakes and deliberately so; ticket 13 already owns "a deterministic fake decoder". Do not
  stretch `FakeTranscriber` into that role: `Transcriber.stream()` returns
  `TranscriptionSegment`s while `drive()` consumes a `StreamingDecoder`, and conflating them
  would put windowing logic in the wrong module.
- **Ticket 15 — [streaming-session](../../live-dictation-ux/streaming-session/ticket.md).**
  Its DoD says `tests/test_streaming_session.py` "uses `FakeAudioSource`/`FakeTranscriber`
  only", but its session must drive `streaming.drive(...)` with a `StreamingDecoder` (F3).
  Ticket 15 will either need to own a tiny decoder double or reuse ticket 13's; that wording
  mismatch is a ticket-level item, not a defect here.
  Also note `FakeTranscriber.stream()` materialises its input (`list(chunks)`), so pairing it
  with a generator that never terminates would hang — ticket 15's worker drives a finite
  `audio.chunks()`, so it terminates, but the eventual streaming wiring in ticket 16 should
  feed real `stream()` implementations, not this fake, when testing liveness.
- **Capability branching (ADR 0014 item 4).** `FakeTranscriber(supports_streaming=False)` is a
  first-class non-streaming fake, so ticket 16's selection rule can test "batch when False,
  streaming when True" with no exception-driven control flow.

## Not verified

- **Ticket 6 and 7 manual checks** (a real 3-second `pw-record` capture; real `ydotool`
  injection into a text editor and terminal) are out of scope for this ticket and were not
  attempted here — the DoD makes them manual and they belong to those tickets.
- **Behaviour under a real `numpy` install.** Probe A could not exercise the assertion path,
  so the numpy leg was verified only indirectly, through the `gi` leg. I could not install
  numpy in this environment without changing it, so I did not.
- **Consistent `pytest` import mode on other machines.** `from tests.fakes import ...` relies
  on `tests/__init__.py` plus the pytest rootdir insertion; it is verified here via
  `uv run pytest`, but I did not test alternative `importmode` settings.

## Commands re-run by this review (summary)

| Command | Result |
|---|---|
| `git status --porcelain` (before) | clean |
| `uv run pytest` | 20 passed |
| `uv run pytest tests/test_interfaces.py -q` | 17 passed |
| `uv run mypy src/ges2t` | Success, 3 files |
| `uv run mypy tests` | Success, 4 files |
| `uv run mypy src/ges2t` (untyped-def probe) | 1 error, then reverted |
| `PYTHONPATH=. uv run python /tmp/probe_fakes.py` | PROBE OK |
| `PYTHONPATH=. uv run python /tmp/probe_introspect.py` | surface matches the table above |
| `uv run pytest ...::test_importing_interfaces...` (probe A: `import numpy`) | errored, then reverted |
| `uv run pytest ...::test_importing_interfaces...` (probe B: `import gi`) | assertion fired, then reverted |
| `git status --porcelain` (after) | clean |
