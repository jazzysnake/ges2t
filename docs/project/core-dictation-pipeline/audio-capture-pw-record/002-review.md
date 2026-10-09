# 002 — Review: `audio-capture-pw-record`

- **Branch:** `feat/audio-capture-pw-record`
- **Worktree:** `.worktrees/audio-capture-pw-record`
- **HEAD reviewed:** `61973ff` (worktree clean)
- **Reviewer:** independent verification, 2026-10-09
- **Verdict:** **pass** (two orchestrator action items before merging; see "Before merging")

The implementation does what the ticket asks. Every load-bearing claim in `001-comment.md`
was re-measured against the real `pw-record` on this machine and held up. The removed
unknown-target requirement is correctly removed: the target-fallback claim is **true**.
Two things are outside the code and need the orchestrator's attention before merge (a board
README conflict and a pre-existing `mypy tests` failure); neither is a defect in this branch.

## Per-criterion findings

| # | Criterion | Result |
|---|---|---|
| 1 | Unknown `--target` falls back silently | **pass** — exit 0, full samples, zero stderr |
| 2 | `--raw` required; argv contains it | **pass** — 24-byte AU header without it |
| 3 | Exit status unsound; heuristic instead | **pass, with one residual race** |
| 4 | Drain-on-stop does not truncate; thread joined | **pass** |
| 5 | `chunks()` is lazy | **pass** |
| 6 | Error taxonomy and message quality | **pass** |
| 7 | Exactly satisfies `AudioSource` | **pass** |
| 8 | Real-mic measurement | **pass** — 48442/48000 = 0.92 %, 102.8 ms |
| 9 | ADR 0019 / mypy | **pass for `src`; `mypy tests` has a pre-existing failure** |
| 10 | Scope, no forbidden imports, no audio committed | **pass** |
| 11 | Tests honest, headless, fast, real seam | **pass** |
| 12 | Board row, links, conflict markers | **pass in-branch; merge conflict against `main`** |

## Evidence

### 1. Target fallback — the claim the resolution rests on

Real recorder, deliberately bogus name, bounded with `--sample-count`:

```
$ pw-record --rate 16000 --channels 1 --format f32 --raw \
      --target definitely-not-a-real-device-xyz --sample-count 16000 - 2>err | wc -c
exit=0 bytes=64000 samples=16000 stderr bytes: 0

$ pw-record ... --target 999999 --sample-count 16000 -   # nonexistent numeric serial
exit=0 bytes=64000 samples=16000 stderrbytes=0
```

Same result with a real `SIGTERM` instead of `--sample-count` (`--target
definitely-not-a-real-device-xyz`, 1.2 s, then `kill -TERM`): `exit=1 bytes=74984
samples=18746 stderrbytes=0`. It produced samples, so it linked to a node (a non-link such as
`--target 0` produces zero bytes — see below); per PipeWire's policy that is the default node.
**The removed requirement should stay removed.** If it had errored, this would be a blocking
finding.

Supporting: `--target 0` (documented "don't link") produced `bytes=0`, an empty stderr and did
not exit on its own (`timeout` killed it, status 124) — i.e. silence, never an error, exactly
as the amendment states.

### 2. `--raw` is load-bearing

```
# WITHOUT --raw, --sample-count 48000, first 64 bytes:
00000000: 646e 732e 1800 0000 ffff ffff 0600 0000  dns.............
# total bytes: 192024  (48000*4 + 24-byte AU header, magic ".snd" little-endian)

# WITH --raw, --sample-count 48000, first 64 bytes:
00000000: 58e2 0b36 15ab 98b8 4941 efb7 e2ac 3838  X..6....IA......
# total bytes: 192000  (= exactly 48000 float32 samples)
```

The built argv contains `--raw` in both cases:

```
default : ['pw-record', '--rate', '16000', '--channels', '1', '--format', 'f32', '--raw', '-']
device  : [... '--raw', '--target', 'alsa_input.usb-mic', '-']
```

Without `--raw` every sample is shifted by 24 bytes — the ticket's three flags alone are not a
usable invocation, so the addition is correct and must stay.

### 3. Exit status and the failure heuristic

Exit status is genuinely useless as a success signal, exactly as claimed:

| Case | Exit |
|---|---|
| clean `SIGTERM` stop | **1** |
| `--sample-count 48000` normal end | **1** |
| no PipeWire session (`XDG_RUNTIME_DIR=/tmp/nonexistent-pw`) | **1** |

`_decide_failure` keys off "exited on its own before any sample, with stderr", and that is the
right correction. The heuristic is conservative in the right direction (it raises rather than
returning empty when a self-exit is unexplained). Two edge behaviours I constructed:

- **Residual race (non-blocking).** If `stop()` is called within roughly the first 50 ms of
  `start()`, the `SIGTERM` can kill `pw-record` before it writes its connection diagnostic, so
  a real "no session" failure is returned as a normal empty buffer:

  ```
  delay= 0.0s  stop() -> NO RAISE, samples=0
  delay= 0.0s  stop() -> NO RAISE, samples=0
  delay=0.05s  stop() -> RAISED: pw-record exited before capturing any audio ...
  delay=0.2s   stop() -> RAISED: ...
  ```

  This is inherent: the module cannot distinguish "caller stopped instantly" from "session
  never came up" without waiting, and the ticket explicitly makes an immediate stop a normal
  zero-sample stop. In the real batch/streaming path the user holds the hotkey for far longer
  than 50 ms, so `chunks()`/`stop()` see the stderr. Worth a note for ticket 12 `doctor`,
  which owns the pre-flight check.

- **Mid-recording process failure after samples** is intentionally swallowed
  (`if capture.captured: return None`): a fatal stderr plus a partial buffer returns the
  partial audio without raising (judgement call 5 in `001-comment.md`). Accepted risk, not a
  defect against the ticket; a stdout *read* error mid-recording still raises
  (`reader_error` is checked first, and `test_stdout_read_error_is_raised_not_swallowed`
  covers it).

### 4. Drain-on-stop, zero-sample, and join

Real recorder, `capture.thread` before and after `stop()`:

```
before stop: thread alive = True
after stop : thread alive = False | returncode = 1 | samples = 4751
reader_error = None | failure = None
```

`stop()` joins the reader (dead on return) and reaps the process. My 3 s measurement returned
`captured=48442` against `yielded=48000` — never truncated. Fake-process tests additionally
assert `sum(chunks) == len(stop())` and that abandoning `chunks()` early still returns the
whole recording.

Zero-sample `stop()` immediately after `start()`, real recorder: `samples = 0 rate = 16000`,
no raise. Matches ADR 0005/0012 ("do not call the engine").

### 5. `chunks()` is lazy

Real measurement: the first 0.1 s chunk arrived at **167.8 ms** while `stop()` happened at
**3050 ms**, so the iterator yields long before the recording ends. The unit test
`test_chunks_is_lazy_and_yields_before_the_recording_ends` asserts `terminate_calls == 0`
after the first `next()`.

### 6. Error taxonomy — two messages verbatim

Missing binary:

```
'pw-record' was not found on PATH: ges2t captures audio exclusively through pw-record, so
install the PipeWire tools (Arch package 'pipewire') and make sure it is on PATH
```

No PipeWire session, surfaced through `chunks()`:

```
pw-record exited before capturing any audio (exit status 1); stderr: error:
pw_context_connect() failed: Host is down. Check that a PipeWire session is running
(`pw-cli info 0`) and that the system default input device exists and is not in use
(`wpctl status`).
```

Both name the program, say what to do, and include the recorder's own stderr where there is
one — a user can act on either. `stop()` before `start()` ("stop() was called before start(),
or after this recording was already stopped: there is no running pw-record capture to stop")
and `start()` twice ("a pw-record capture is already running: start() was called twice. Call
stop() to finish the current recording before starting the next.") are equally explicit.
Branch coverage confirmed by running each branch against the real recorder.

### 7. Protocol conformance

```
isinstance(source, AudioSource) = True
public methods: ['chunks', 'start', 'stop']
init signature: (self, device: 'str' = '', *, spawn: 'SpawnPwRecord' = <...>) -> 'None'
start sig: (self) -> 'None'
chunks sig: (self, chunk_seconds: 'float') -> 'Iterator[AudioBuffer]'
stop sig: (self) -> 'AudioBuffer'
```

Exact match against the `main` `AudioSource` protocol — no callback variant, no extra public
methods. The one addition is the keyword-only `spawn` seam, which is a constructor parameter,
not a public method, and leaves the required signature unchanged.

### 8. My own real-microphone measurement

3-second recording through `PwRecordAudioSource`, all in memory, nothing written to disk
(the measurement script lives in `/tmp`, outside the repo):

```
        target: 48000
      captured: 48442
 deviation_pct: 0.92
first_sample_ms: 102.80
first_chunk_ms: 167.82
       yielded: 48000
        chunks: 30
     truncated: False
 stop_after_ms: 3050.36
deviation within 5%: True
drain not truncated: True
```

Latency across three ~1.2 s trials: first sample 191.4 / 102.3 / 103.6 ms, first 0.1 s chunk
256.3 / 166.9 / 167.8 ms. My numbers match the implementer's report (47418 samples, 1.21 %,
100–120 ms) within normal PipeWire jitter — the budget claim holds.

### 9. ADR 0019 and mypy

```
$ uv run mypy src/ges2t
Success: no issues found in 8 source files

$ uv run mypy tests
tests/test_whistle_config.py:247: error: Argument 1 to "_error_message" has incompatible
type "object"; expected "Mapping[str, Any]"  [arg-type]
Found 1 error in 1 file (checked 7 source files)
```

`tests/test_whistle_config.py` is **identical to `main`** and untouched by this branch, so
that failure is pre-existing (main already tracks it as ticket 20 "type-check-whole-tree").
`uv run mypy tests/test_pw_record.py` is clean.

Config effectiveness probe: I added `src/ges2t/_probe_review_tmp.py` with an unannotated
function, ran `uv run mypy src/ges2t` → `error: Function is missing a type annotation
[no-untyped-def]`, exit 1; I then deleted it and `git status --porcelain --untracked-files=all`
is empty, and `mypy src/ges2t` is clean again. **No probe left behind.**

No positional-tuple public returns, no bare `Any`, no implicit `Optional`, every definition
annotated (including `-> None`). The only tuple is the private test helper `_source_with`,
which ADR 0019 explicitly permits for private helpers.

### 10. Scope

Changed vs `main`: `docs/project/README.md`, the ticket folder's `ticket.md` and
`001-comment.md`, `src/ges2t/audio/__init__.py`, `src/ges2t/audio/pw_record.py`,
`tests/test_pw_record.py`. No other module. No `numpy`, no `gi`, no `shell=True`/`os.system`;
`subprocess.Popen(argv, ...)` with an explicit list. `git ls-files` shows no `.wav/.flac/.au/`
`.ogg/.mp3/.raw/.pcm` anywhere in the tree.

### 11. Tests

`26 passed in 0.07s` (file) and `142 passed in 0.40s` (whole suite). No `monkeypatch` and no
patch of `subprocess.Popen` — the seam is the injected `spawn`. Headless proof: running the
test file with `pw-record` unreachable still passes:

```
$ PATH=/nonexistent .venv/bin/python -m pytest tests/test_pw_record.py -q
26 passed in 0.07s
```

The assertions are concrete (exact argv list, sample counts, `terminate_calls == 0` for
laziness, stderr text in the raised error), so they would fail if the behaviour were removed.
Best-effort real-recorder duplicates of the failure branches (above) agree with the fakes.

### 12. Board, status, links, conflict markers

In the branch: board row 6 is `in review` / `feat/audio-capture-pw-record`; ticket status is
`in review`. Every relative link in `ticket.md` resolves. No conflict markers anywhere in the
tree (the only `grep` hit was a `.venv` site-packages string, not a repo file).

**However**, the branch does **not** merge cleanly into `main`:

```
$ git merge-tree --write-tree main HEAD
CONFLICT (content): Merge conflict in docs/project/README.md
```

Cause: `main` has advanced past the branch point (`584372a`) — tickets 5 and 7 are marked
`done` there and ticket 20 was added — so the branch's one-line board edit collides with the
surrounding changed rows. The board content itself is right; it just needs a manual
reconciliation at merge time.

## Before merging (orchestrator action items)

1. **Resolve the `docs/project/README.md` board conflict.** Mechanical: keep `main`'s rows 5/7
   (`done`) and ticket 20, and apply this branch's ticket 6 row (`in review`,
   `feat/audio-capture-pw-record`).
2. **Know that the ADR 0008 amendment is not in this branch's tree.** It was committed to
   `main` (`cd49971`), and `b603f5c` deliberately kept it there ("the ticket file itself is
   owned by the feature branch and will arrive with the merge"). So the branch's ticket says
   "Recorded as an amendment in ADR 0008" while the branch's own `docs/adr/0008-...md` still
   lists "unknown device target" as a failure mode. This is intentional and consistent after
   the merge into `main`, but commit `2a36a00`'s subject "ADR 0008 amended" is misleading: that
   commit touches only `README.md` and `ticket.md`. No action needed if the merge target is
   `main`.

## Risks and follow-ups

- **Ticket 11 (batch pipeline) and tickets 15/16 (streaming)** consume `chunks()` and must call
  `stop()` exactly once; the returned `AudioBuffer` already includes everything `chunks()`
  yielded, so summing chunks *and* `stop()` would double-count. Construct with
  `PwRecordAudioSource(device=config.audio.device)`; `""` emits no `--target`. A zero-sample
  `stop()` is normal and means "do not call the engine".
- **Ticket 12 (`doctor`)** now owns the configured-device check. The residual
  immediate-`stop()` race (criterion 3) is exactly why a pre-flight check belongs there: the
  capture path can return an empty buffer when no session exists and `stop()` arrives within
  ~50 ms. `doctor` validating the configured device against the session node list is the right
  safety net.
- **Mid-recording fatal failure after samples returns a partial buffer silently** (by design).
  If the pipeline prefers to surface that, it needs a policy decision, not a change here.
- **Stale `## Blocker` section.** The ticket still carries a "## Blocker" heading describing
  the now-resolved item while its status is `in review`. Cosmetic; worth folding into the
  resolution text so the ticket doesn't contradict itself.
- **`uv run mypy tests` is red** on `tests/test_whistle_config.py:247`, pre-existing and
  unrelated (ticket 20). Flagging so the merge isn't blamed for it.

## Could not verify

- Whether an unresolved `--target` resolves to the *same* node a default (no `--target`) run
  would pick, sample-for-sample. I confirmed the fallback produces audio and no error, which is
  all the amendment claims; distinguishing "default node" from "some other node" needs
  `pw-dump`/`wpctl` node inspection, which is ticket 12's scope.
- The exact 24-byte header offset was confirmed from bytes, but I did not decode the AU payload
  to prove every sample was shifted (the byte-count arithmetic, 192024 = 192000 + 24, is
  conclusive enough).
- Real-mic mid-recording device failure (unplug). The swallow-after-samples behaviour is
  argued from the code path, not reproduced with hardware.
