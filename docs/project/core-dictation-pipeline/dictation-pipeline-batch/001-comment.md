# 001 — Composition root and batch end-to-end wiring

Ticket: `ticket.md` (`dictation-pipeline-batch`) · Branch: `feat/dictation-pipeline-batch` ·
Worktree: `.worktrees/dictation-pipeline-batch` · Status: **in review**

## What was built

Exactly the two files the ticket allows:

- `src/ges2t/pipeline.py` — the composition root (ADR 0010).
- `tests/test_pipeline.py` — 22 tests, all with fakes; no mic, no engine, no subprocess, no
  socket, no network.

No other module was created or modified. `pipeline.py` imports only the stdlib plus
`model_store`, `config`, `interfaces`, `service`, `socket_proto`, and the three concrete
adapters (`audio.pw_record`, `injectors.ydotool`, `transcribers.whistle{,_config}`) — this is
the one module allowed to import concrete implementations (ADR 0010, ADR 0019).

## Final public shapes

```python
class PipelineError(Exception):
    def __init__(self, message: str, *, hint: str | None = None) -> None: ...

class NullStatusUI:                       # documented no-op StatusUI
    def idle(self) -> None: ...
    def recording(self) -> None: ...
    def transcribing(self) -> None: ...
    def error(self, message: str) -> None: ...   # writes to stderr (see below)
    def partial(self, text: str) -> None: ...

class BatchDictationSession:              # implements service.DictationSession
    def __init__(self, transcriber: Transcriber, audio_source: AudioSource) -> None: ...
    def start(self) -> None: ...
    def partials(self) -> Iterator[str]: ...
    def finish(self) -> str: ...
    def cancel(self) -> None: ...

def build_transcriber(config: Config) -> Transcriber: ...
def build_audio_source(config: Config) -> AudioSource: ...
def build_injector(config: Config) -> Injector: ...
def run_service(config: Config, status_ui: StatusUI | None = None) -> int: ...
```

Private helpers, all fully annotated and interface-typed where a choice exists:
`_build_whistle`, `_session_factory`, `_build_service`, `_model_path_override`,
`_weights_path`, `_present`, `_missing_artifacts`, `_require_artifacts`, `_startup`,
`_print_failure`. `_TRANSCRIBER_BUILDERS: Mapping[str, Callable[[Config], Transcriber]]`
is the factory's single source of truth.

## The decisions a reviewer should check

1. **`.get(..., {})`, never `[...]`.** `_build_whistle` does
   `WhistleConfig.from_mapping(config.engine_blocks.get(config.transcriber.engine, {}))`.
   The generic layer never synthesises an `[engine.whistle]` block, so an absent block is the
   normal case and must mean "all defaults", not a `KeyError` (ADR 0009; ticket 4's hand-off).
   A test (`test_build_transcriber_accepts_a_missing_engine_block_as_all_defaults`) and a
   mutation (M3) pin this.
2. **No network on the startup path.** `run_service` → `_startup` calls `_require_artifacts`
   (existence + pinned byte size, no hash, no download), then `build_*`, then
   `model_store.env_overrides()` and `transcriber.load()`. It never calls
   `transcriber.ensure_model()`, `model_store.ensure_model()`, or `model_store.ensure_engine()`.
   The test monkeypatches `model_store.ensure_model`/`ensure_engine` to raise and asserts
   `run_service` still returns 0 with the call order `["load", "serve"]`. `env_overrides()`
   sets `HF_HUB_OFFLINE=1` (ADR 0017, ADR 0018).
3. **Presence = existence + pinned size, no hash.** `_present` mirrors
   `model_store._present_with_pinned_size` semantics without importing the private helper. A
   full sha256 must not sit on the startup path (ADR 0017). For a caller-supplied
   `transcriber.model_path` override there is no pin of ours, so only existence is checked;
   the engine path always comes from `model_store.engine_path()` (no engine override).
4. **Model location.** `_weights_path` returns the non-empty `config.transcriber.model_path`
   (honoured by `WhistleTranscriber(model_path_override=...)`) or else
   `model_store.model_path()`, which is under `model_store.default_data_dir()`.
5. **`load()` happens once, at startup.** The model stays warm for the process lifetime
   (ADR 0005, ADR 0015). The test asserts the exact order `["load", "serve"]`.
6. **`status_ui=None` default.** `run_service` accepts an optional `StatusUI` so epic 2 injects
   the real tray in the same place without a signature change. A test asserts the injected
   object reaches the constructed `Service`.
7. **Composition stays in small named functions.** `_session_factory` is the seam ticket 16
   extends with the streaming/batch selection rule; `_build_service` is the only place the
   `Service` is constructed. `run_service` is orchestration only.

### One deviation, stated loudly

`NullStatusUI` is **not a pure no-op for `error()`**: it prints `ges2t: <message>` to stderr.
The ticket's note says the adapter's >30 s error "should be surfaced ... as a notification in
epic 2, but for now a clear stderr message is enough". `status_ui.error()` is the only error
channel the service has, so with a pure no-op a failed transcription would be invisible until
`ges2t status`. When the tray lands and is injected here, `NullStatusUI` is simply not
constructed. A test pins the stderr behaviour.

### One redundancy, noted

`WhistleTranscriber.load()` already applies `model_store.env_overrides()`; `_startup` applies
them again explicitly per the ticket's DoD wording ("apply `model_store.env_overrides()`").
It is idempotent (`os.environ.update`) and keeps the contract visible at the composition root.

## Verification (real output)

Baseline before this branch: `uv run pytest -q` → **491 passed**; `uv run mypy src/ges2t tests`
→ **Success: no issues found in 32 source files**.

```
$ uv run pytest tests/test_pipeline.py -q
......................                                                   [100%]
22 passed in 0.10s

$ uv run pytest -q
........................................................................ [ 98%]
.........                                                                [100%]
513 passed in 1.84s

$ uv run mypy src/ges2t tests
Success: no issues found in 34 source files
```

`git status --porcelain` after all work contains only the two new source files (the docs
changes are committed separately): no probe or scratch file is left in the repo.

## Falsification evidence (every new test was made to fail)

A throwaway mutation harness (in `/tmp`, not committed) applied one textual mutation to
`pipeline.py` at a time, ran `tests/test_pipeline.py`, recorded the failures, and restored the
file. Result: **22/22 new tests failed under at least one mutation**, and every mutation that
changed a guarded behaviour produced exactly the expected failures. Representative rows:

```
M2  unknown engine does not raise PipelineError   -> test_build_transcriber_raises_for_an_unknown_engine
M3  engine block indexed, not .get(..., {})       -> 4 build tests (KeyError on the absent block)
M4  engine block ignored                          -> test_build_transcriber_passes_the_engine_block_through
M5  managed weights path wrong                    -> test_missing_artifacts_detects_a_wrong_sized_managed_model
M6  model_path override ignored                   -> test_build_transcriber_honours_a_non_empty_model_path_override
M7  audio device ignored                          -> test_build_audio_source_passes_the_configured_device
M8  audio source always targets a device          -> test_build_audio_source_omits_the_target_by_default
M9  injector ignores trailing_space               -> test_build_injector_carries_the_trailing_space_setting
M10 partials method removed                       -> test_batch_session_is_a_dictation_session + partials test
M11 start is a no-op                              -> test_batch_session_start_starts_capture
M12 finish transcribes on zero samples            -> test_finish_with_zero_samples_returns_empty_and_does_not_transcribe
M13 finish transforms the text                    -> test_finish_returns_the_transcriber_text_unchanged
M14 cancel transcribes                            -> test_cancel_stops_capture_and_does_not_transcribe
M15 _startup calls ensure_model                   -> test_run_service_never_downloads_and_loads_once_before_serving
M16 _startup skips load                           -> test_run_service_never_downloads_and_loads_once_before_serving
M17 run_service ignores the injected status_ui    -> test_run_service_injects_the_status_ui_into_the_service
M18 _require_artifacts never raises               -> test_run_service_reports_missing_artifacts_and_returns_nonzero
M19 _present ignores size                         -> test_missing_artifacts_detects_a_wrong_sized_managed_model
M20 override checked against managed pin size     -> test_missing_artifacts_accepts_a_present_override_and_engine
M21 UnsupportedPlatformError not caught           -> test_missing_artifacts_reports_an_unsupported_platform
M22 NullStatusUI.error is a no-op                 -> test_null_status_ui_error_writes_to_stderr
M23 managed weights forced to a bogus path        -> test_build_transcriber_uses_the_managed_weights_without_an_override
```

`uv run pytest tests/test_pipeline.py -q` after the final revert: `22 passed in 0.10s`.

## Framework latency — number and method

**Method.** A throwaway probe (`/tmp/latency_probe.py`, not committed) on the reference
machine, with no model time and no injection in the loop:

- a real `Service` + `ServiceServer` on a temp Unix socket (real JSON protocol);
- a real `PwRecordAudioSource` capturing ~0.30 s of local audio (PipeWire 1.6.x, default
  device; the audio is discarded, nothing is written and no voice is needed);
- a fake `Transcriber` that returns a canned ≤20-word phrase immediately (model time ≈ 0);
- a fake `Injector` that records the instant it is called (injection time ≈ 0).

It reports the median of: a `status` socket round trip, `PwRecordAudioSource.stop()`
(capture stop + drain), the zero-cost `finish()` handoff between `stop()` and `transcribe()`,
and the full `stop-toggle → inject-called` interval.

**Two runs (medians):**

```
captured samples: 4410 at 16000 Hz
socket status RTT: median 0.33 ms (n=20)
capture stop/drain: median 2.97 ms (n=5)
buffer handoff: median 0.000 ms (n=50)
stop-toggle -> inject-called (fake model, fake injector): median 3.83 ms (n=5)
```

```
captured samples: 4410 at 16000 Hz
socket status RTT: median 0.19 ms (n=20)
capture stop/drain: median 3.07 ms (n=5)
buffer handoff: median 0.000 ms (n=50)
stop-toggle -> inject-called (fake model, fake injector): median 3.56 ms (n=5)
```

**Framework portion this ticket owns: ~3.6–3.8 ms** (socket call + capture stop/drain +
buffer handoff), well under ADR 0001's 1 s budget.

**Injection is deliberately not measured here.** Measuring it means running `ydotool type`
into whatever window has focus, and this ticket was explicitly forbidden from injecting
keystrokes. The pre-rewrite baseline from ADR 0020's research on this machine is **~40 ms per
character at `ydotool`'s default delay** (4.99 s for a 124-character phrase; a ~16–18 ms/char
floor even at `--key-delay 0`, because `type` sleeps twice per character client-side). For a
≤20-word phrase (~120 characters) that is **≈4.8 s**, so the full round trip is far above 1 s.
**That is ticket 23's (`batched-key-emission`) defect to fix and remeasure; this ~40 ms/char
figure is the "before" baseline ticket 23 is compared against.** The `--key-delay` tuning
question is superseded by ADR 0020 and was not touched.

## What I verified without a microphone or injection

1. The 22 unit tests above (all falsified).
2. **No-download**: `run_service` with monkeypatched `model_store.ensure_model`/`ensure_engine`
   that raise still returns 0, with call order `["load", "serve"]`.
3. **Integration smoke** (`/tmp/run_service_{child,integration}.py`, not committed): the real
   `run_service` with only the four builders faked (fake audio source, fake transcriber, fake
   injector), real `Service` + `ServiceServer` + socket + JSON protocol. Observed:

```
status -> ok=True state=idle
CHILD:LOAD
CHILD:AUDIO_START
CHILD:AUDIO_STOP
CHILD:TRANSCRIBE
CHILD:INJECT:smoke test
CHILD:EXIT=0
run_service process exit code: 0
integration smoke: all markers present, clean SIGTERM exit 0
```

   This exercises `run_service`'s full wiring: startup check → `load()` once → serve →
   `toggle` (start) → `toggle` (finish → transcribe → inject) → clean `SIGTERM` returning 0.
4. The framework latency numbers above (real socket + real capture).
5. `uv run mypy src/ges2t tests` over the whole tree including `tests/` (34 files).

## Manual end-to-end — owned by the user, not run by this agent

This step needs a human voice and a focused window, and this agent may not inject keystrokes.
Prerequisites: `uv run ges2t setup` has completed; `ydotoold` is active/enabled and
`/dev/uinput` is accessible (both already true on the reference machine — no `sudo`, no
service change needed).

Run `uv run ges2t run` in a terminal and leave it running. Open a second terminal for the
toggle commands. Then, without touching `--key-delay`:

```bash
# 1. text editor: focus a text editor, then from another terminal:
uv run ges2t toggle        # state -> recording
#   say a short phrase, e.g. "the quick brown fox"
uv run ges2t toggle        # state -> transcribing, then idle; text appears
uv run ges2t status        # expect: idle

# 2. same in a focused terminal:
uv run ges2t toggle
#   say a short phrase
uv run ges2t toggle        # the words appear at the shell prompt

# 3. twice in a row (ADR 0012): dictate two phrases back to back
uv run ges2t toggle        # start phrase one
#   say phrase one, then:
uv run ges2t toggle        # stop phrase one; wait for 'uv run ges2t status' to show idle
uv run ges2t toggle        # start phrase two
#   say phrase two, then:
uv run ges2t toggle        # stop phrase two
#   expect two space-separated words (the trailing space is the injector's)

# 4. silence (ADR 0012): say nothing
uv run ges2t toggle
uv run ges2t toggle        # expect: NOTHING typed; state returns to idle

# 5. cancel:
uv run ges2t toggle        # start recording
uv run ges2t cancel        # expect: NOTHING typed; state -> idle
uv run ges2t status        # expect: idle
```

**Expected observations and known in-flight defects — recorded, not re-litigated:**

- **(`a`) `hu`-layout character corruption (ticket 22 / ADR 0020).** On the reference machine's
  non-US `hu` layout the injected characters will be wrong — `0`→`ö`, `y`→`z` — because the
  current injector still calls `ydotool type`, which maps text through a hardcoded US keymap.
  Tickets `../xkb-keycode-mapping/ticket.md` (22) and `../batched-key-emission/ticket.md` (23)
  exist to fix exactly this. **Not a new blocker.**
- **(`b`) injection tail far above 1 s (ticket 23 / ADR 0020).** Same cause: `ydotool type`
  costs ~40 ms/char. Ticket 23 owns the batched `ydotool key` emission and the injection
  latency. **Not a new blocker.**
- What **does** count as working and must be recorded: text appears at all, silence types
  nothing, `cancel` types nothing and returns to `idle`, and two phrases give two
  space-separated words.
- If something fails **outside** (a) and (b), the ticket is `blocked` with a comment describing
  exactly what failed — not a workaround.

The user should paste the observed results back here so the manual DoD checkbox can be ticked.

## For the orchestrator / reviewer

- The manual E2E DoD checkbox is intentionally left unticked; it is user-owned and cannot be
  completed by an agent. Everything else is done and verified.
- Ticket 16's hand-off: extend `_session_factory` (and, if it needs config, `run_service`) with
  the streaming selection step; do not touch `service.py`. `run_service`'s optional
  `status_ui=None` is the injection point epic 2 will use for the tray.
- Ticket 12's hand-off: `_missing_artifacts`/`_present` are reusable for `doctor`'s model/engine
  presence checks; `doctor` should report both versions and the `cactus-needle` 3.1.3 vs engine
  pin 3.2.0 skew (see ticket 8's `001-comment.md`).
- `build_injector` implements the v1 single backend (`ydotool`) exactly as the DoD states;
  `injection.backend` is preserved in the generic config for ticket 25's clipboard backend.
- No ADR was changed. No streaming was implemented. No keystrokes were injected, no `sudo`, no
  service changes, no network.
