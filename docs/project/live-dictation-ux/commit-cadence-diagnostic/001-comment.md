# 001 — commit-cadence diagnostic: what was built, what was measured, what is still missing

- **Ticket:** [commit-cadence-diagnostic](ticket.md) (ticket 32, the gate for tickets 33 and 34)
- **Branch:** `feat/commit-cadence-diagnostic`
- **Worktree:** `.worktrees/commit-cadence-diagnostic`
- **Date:** 2026-10-09
- **Status of the gate:** **open** — the clip half is measured and clean; the fresh real-device
  recording is the user-owned step and is still missing.

## 1. Headline

On the two shipped clips, **32 s of audio fed to the real engine, 24 commits**:

| measurement | result |
|---|---|
| committed deltas that repeat already-committed words | **0** (0 with 2+ words) |
| intermediate transcript snapshots that are not a prefix of the final transcript | **0 of 24** |
| median characters per commit | **20** (p90 34, max 50) |
| median gap between commits, in source-audio seconds | **1.00 s** (p90 2.90 s, max 4.00 s, shortest 1.00 s) |
| median `pending` size | **22 characters** (p90 38.6, max 51) |
| median time a `pending` tail stayed unchanged | **1.00 s** of source audio |

**Preliminary recommendation: proceed with a named caveat.** Type each non-empty commit as it
arrives, never `pending`. This is preliminary because the clips are *read* speech: they carry
neither this machine's microphone noise floor nor the user's voice, which is the whole lesson of
the silence saga (§7).

## 2. What the harness is

`measure_commit_cadence.py` in this folder — a **diagnostic script, not part of the shipped
package** (`uv run pytest` does not see it, does not import it, and stays headless and
engine-free, per ADR 0014).

It drives the **real** engine through the **shipped** path, unmodified:

```
WhistleTranscriber.stream()          (shipped: applies the per-chunk silence gate)
  -> streaming.drive()               (shipped: the `transcript += pass_.committed` fold)
     -> _WhistleStreamingDecoder     (shipped: the engine seam)
```

`drive()` is a pure fold over the decoder's return values and that fold is shipped, so the
script does not re-implement it. To observe `transcript` it temporarily swaps the adapter's
decoder *class* for a recording subclass for the duration of one run
(`DecoderRecorder.install` / `uninstall`): the subclass calls `super()` and records the
`StreamingPass` / `flush()` strings that cross the seam, and nothing else changes. The snapshot
sequence it reports is the prefix sum of exactly those strings — the values the shipped fold
passes through.

The source timeline (`t_source`) is reconstructed by calling the shipped gate predicate
(`whistle._has_speech`) on the same buffers `stream()` will see, so a silence-dropped chunk still
advances the clock; the report prints a `timeline:` line stating whether the `process()`-call
count and the fed-chunk count agree. All inputs are fed as fixed 1.0 s buffers, which is the
`chunk_seconds = 1.0` default from `[engine.whistle]`.

### 2.1 Honesty note on the "prefix" property

`transcript` is built only by `+=`, and **every prefix of a concatenation is a prefix of that
concatenation**. The prefix check is therefore structurally guaranteed and can essentially never
fail — it is reported because the ticket asks for it, but it is not the measurement that decides
the feature. The check that *can* fail, and that the recommendation stands on, is whether the
engine's committed deltas are **new** text:

1. **committed-word overlap scan** — does any later commit begin by repeating words already
   committed? (That is what a restatement looks like through this seam.)
2. **one-shot batch-reference diff** — the same audio transcribed by the same adapter on the
   batch path, compared word-for-word with the streaming transcript (normalised: case-folded,
   punctuation dropped, `3.2` kept intact). A full-text-not-a-delta engine, a duplicated window
   tail, or a wholesale re-commit would all show up here.

Both are reported per input.

### 2.2 The detector is validated, not assumed: `--self-test`

A restatement cannot be produced on demand by the real engine, so `--self-test` runs the
detector against a **scripted** restating decoder — no engine, no device, no audio:

```
--- self-test: does the restatement detector fire? ----------------------
  PASS  append-only engine (the shipped behaviour): expected overlap>=(0) word(s), detected 0; prefix check PASS
  PASS  engine restates a committed phrase in a later pass: expected overlap>=(2) word(s), detected 2; prefix check PASS
        2 word(s) repeated; already committed 'i scream'; new delta ' I scream'
  PASS  flush() re-commits a tail the window already committed: expected overlap>=(2) word(s), detected 2; prefix check PASS
        2 word(s) repeated; already committed 'hello world'; new delta ' hello world'

  3/3 self-test cases behaved as expected
  note: the prefix check PASSES even on the restating cases, which is exactly why the
  committed-word overlap scan and the batch-reference diff exist.
```

So `[4] restatements: 0` on the clips is a real measurement of a detector that demonstrably
fires when it should, not a vacuous zero.

## 3. The numbers actually measured (shipped clips, `/tmp/whinspect`)

Command run by the agent (no microphone, `/tmp/whinspect` still present):

```
uv run python docs/project/live-dictation-ux/commit-cadence-diagnostic/measure_commit_cadence.py \
    --clips /tmp/whinspect --out /tmp/ges2t-commit-cadence-clips-report.txt
```

Engine and configuration: `whistle`, `window_seconds = 5.0`, `chunk_seconds = 1.0`,
`language = ''` (auto-detect), 16000 Hz mono float32. Full report kept at
`/tmp/ges2t-commit-cadence-clips-report.txt`; the transcript of the run is the source of every
number below.

### `clip5.wav` — 5.00 s, all 5.00 s fed (nothing gated out), 2 `flush()` calls (1 window crossing + the final)

```
[1] append-only : 5 intermediate transcript snapshots checked; PASS
[2] commit cadence: 5 commits
    chars per commit : median 18.0 | p90 25.2 | max 26.0 | n=5
    gap, source secs : median 1.00s | p90 1.00s | max 1.00s | n=4
    gap, wall secs   : median 0.15s | p90 0.20s | max 0.22s | n=4
[3] pending size : median 18.0 | p90 27.6 | max 33.0 | n=7
    pending persistence (source secs): median 1.00s | p90 1.00s | max 1.00s | n=5
[4] restatements: 0 committed deltas repeat already-committed words at their start (0 with 2+ words)
    vs one-shot batch reference: substitutions=0 insertions=0 deletions=0 WER=0.000 (n=14 reference words)
```

Final transcript (verbatim):

```
He hoped there would be stew for dinner, turnips and carrots and bruised potatoes.
```

The one-shot batch path produces the identical string.

### `clip30.wav` — 30.00 s recorded, 27.00 s fed (the gate dropped 3 s of silence), 6 `flush()` calls

```
[1] append-only : 19 intermediate transcript snapshots checked; PASS
[2] commit cadence: 19 commits
    chars per commit : median 22.0 | p90 35.0 | max 50.0 | n=19
    gap, source secs : median 1.00s | p90 3.00s | max 4.00s | n=18
    gap, wall secs   : median 0.15s | p90 0.37s | max 0.84s | n=18
[3] pending size : median 22.0 | p90 38.6 | max 51.0 | n=33
    pending persistence (source secs): median 1.00s | p90 1.00s | max 2.00s | n=32
[4] restatements: 0 committed deltas repeat already-committed words at their start (0 with 2+ words)
    vs one-shot batch reference: substitutions=15 insertions=0 deletions=3 WER=0.261 (n=69 reference words)
```

**On the flush path, as the ticket predicted.** `clip30.wav` drives `flush()` **six** times: five
mid-utterance window boundaries plus the final flush. (Five, not six, *mid-utterance* crossings
because the shipped gate drops 3 s of silence per chunk before `drive()` counts samples; the
window fills on audio the engine actually saw.) No restatement appears at any of them — the
`flush()` returns are all new text, e.g. at `t=+10.00s` the tail
`' latled out in thick peppered flour fat and sauce.'` is committed by `flush()` and never
reappears.

Wall-clock cost of the run: `clip5` 0.94 s, `clip30` 8.25 s. The 30 s figure includes a one-shot
batch transcription of the whole clip for the reference diff; it is **not** comparable to ADR
0011's 5.62 s streaming-only measurement.

### Totals across both inputs

```
inputs measured        : 2
audio fed to the engine: 32.00 s
commits observed       : 24
chars per commit       : median 20.0 | p90 34.0 | max 50.0 | n=24
gap between commits    : median 1.00s | p90 2.90s | max 4.00s | n=22
prefix property        : 2/2 inputs PASS
committed-word repeats : 0 hit(s), 0 with 2+ words
```

## 4. Verbatim evidence that `pending` is the part that gets revised

The two-pass commit rule is visible directly in the seam record. For `clip5.wav`, the tail that
was on screen when each commit landed, and what was actually committed:

```
t=+1.00s   pending: 'He hoped.'                       committed: 'He'
t=+2.00s   pending: ' hoped there would be stew for...' committed: ' hoped there would be stew'
t=+3.00s   pending: ' for dinner, turnips.'            committed: ' for dinner,'
t=+4.00s   pending: ' turnips and carrots and'         committed: ' turnips and carrots and'
t=+5.00s   pending: ' bruised potatoes.'               committed: ' bruised potatoes.'
```

And the sharpest case on `clip30.wav`:

```
t=+25.00s  pending: ' quarter of the broughles.'   committed: ' quarter of the'
t=+26.00s  pending: ' brothels stuffed into'       committed: ' brothels stuffed into'
```

At `t=+1s` the tentative `hoped.` was **not** committed; at `t=+25s` the garbled `broughles.` was
**not** committed, and the corrected `brothels` was committed one pass later. In every case the
pending tail is revised *before* it reaches the transcript, which is exactly the property the
incremental-typing design depends on: text only becomes typeable once the engine has agreed on it
twice, and once committed it is never taken back on these inputs.

**Honest limitation of the rule, not of the safety property:** `clip30.wav` commits
`' latled out in thick peppered flour fat and sauce.'` at the `t=+10s` flush. Two consecutive
passes did agree on the mis-hearing `latled`, so it was committed and would have been typed. The
commit rule filters *uncertainty*, not *error*. That is a quality matter (the user will eyeball
the transcript against what they read), not a typing-safety matter: nothing needs a backspace.

## 5. What the numbers mean for the injector

* **Unit of injection:** one non-empty `committed` delta. Never `pending`, never
  `committed + pending`.
* **Rate:** one commit per ~1 s of speech at the median; the shortest observed gap is **1.00 s of
  source audio**, so because audio arrives in real time the injector never has to fire faster
  than about **once per second**, carrying a median of **20 characters**. There is no burst
  smaller than one chunk, so no debouncing is needed for latency's sake; the natural cadence is
  the chunk cadence.
* **Worst observed quiet stretch:** the p90/max gap of 2.9 s / 4.0 s is *fewer* commits, not more
  — a longer pause means the engine has nothing to commit. It does not increase the injection
  rate.
* **Backspaces:** zero required on the measured inputs. The whole feature is safe on the clip
  evidence; ticket 34's design should still be cancel-safe and should never attempt a delete,
  because that is the one behaviour a future restatement would make dangerous.
* **`pending` latency:** the uncommitted tail persists ~1 s (p90 1 s, max 2 s) and is 22
  characters at the median. That is the lag between speaking and text appearing if the user were
  shown only committed text; it does not affect correctness of what is typed, only how soon.

## 6. The recommendation, with the numbers behind it

**Proceed with the named caveat** (as printed by the harness):

> On 2 inputs (32.0 s of audio) no committed delta restated already-committed text, and every
> intermediate transcript snapshot was a prefix of the final one. The measurements support typing
> each non-empty commit as it arrives: a median of 20 characters per commit, one commit every
> 1.00 s of source audio (p90 2.90 s, shortest 1.00 s). Because audio arrives in real time, the
> injector never needs to fire more often than about once every 1.00 s.
>
> CAVEAT 1: the injection unit is the commit delta, never `pending`.
> CAVEAT 2: the shipped clips are read speech; the fresh user recording must be added before the
> gate can be considered closed.
> CAVEAT 3: the engine is closed-source (ADR 0006); a restatement outside this input set cannot
> be ruled out, so the injector should stay cancel-safe.

If the fresh recording produces a 2+ word committed repeat, the harness says **STOP** in the
summary and this commentary is superseded: stop and report, do not engineer around it.

## 7. What could not be measured without the user, and the exact command to fix that

The agent **must not** record the microphone: it cannot tell the user when to start. The harness
refuses to record when stdin is not a terminal (`--capture` under automation exits 1 with a clear
message), which was verified.

Still missing:

1. this device's **own noise floor** and the **user's own voice** through the real `pw-record`
   path — the only input that carries them;
2. a real **mid-utterance pause**, where a `flush()`-committed tail is most likely;
3. confirmation that the **silence gate** behaves the same way on the live device as on the
   clips (chunk drops change how often the window fills);
4. the user's own **eyeball check of the transcript** against the passage they read (there is no
   scoring in the harness; the raw final transcript is printed verbatim for exactly this).

### The command the user must run

From the worktree, in their own terminal:

```
cd /home/matee/Workspace/Personal/ges2t/.worktrees/commit-cadence-diagnostic
uv run python docs/project/live-dictation-ux/commit-cadence-diagnostic/measure_commit_cadence.py --capture --seconds 25
```

(`--capture` is also the default, so the bare script behaves the same.)

**What they will see, in order:**

1. The banner, then `engine : whistle`, `window_seconds : 5.0  chunk_seconds: 1.0`, and the
   capture instructions:
   `This run records from your microphone; you own the start.` with the five bullet lines.
2. `capture device  : the system default input device` (or the `audio.device` from their config),
   `recorder : pw-record at 16000 Hz mono`, `requested length: 25 s (fixed)`, and
   `tip : leave a deliberate 2-3 s pause in the middle; ...`.
3. `Recording from ... for 25 s.` then
   `Press Enter to arm the recorder (nothing is captured before you do).` — **nothing is captured
   until they press Enter**.
4. A 3-2-1 countdown, printed one line per second.
5. A blank line, then the unmistakable cue:

   ```
   ● RECORDING — speak now
   ```

   with a live counter underneath, updated once per second:
   `  1 s / 25 s recorded` … ` 25 s / 25 s recorded`.
6. `■ stopped`.
7. `captured 25.xx s; kept under /tmp only (never committed): /tmp/ges2t-commit-cadence-capture-<timestamp>.wav`
   (the WAV is in `/tmp` only and is never committed).
8. The full measurement report for the capture, including `[1]`–`[4]`, the pass table, and the
   **final transcript verbatim**.
9. `--- summary and recommendation ---` with the aggregate numbers and the proceed/stop verdict.
10. `full report written to /tmp/ges2t-commit-cadence-<timestamp>-report.txt`.

**What to hand back:** the path printed on the last line (or the whole file). The numbers in
§1–§3 above can then be compared with the fresh input's numbers directly, because the harness
prints them in the same shape.

## 8. Verification run before hand-back

```
$ timeout 300 uv run pytest -q
767 passed in 6.29s          # exit status 0; the process exited (no leaked thread)
$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 49 source files
```

No test was added: the harness is a docs script, and the suite must stay headless and
engine-free (ADR 0014). The harness itself is mypy-clean on its own
(`uv run mypy docs/project/live-dictation-ux/commit-cadence-diagnostic/measure_commit_cadence.py`
→ no issues).

## 9. Definition-of-done status

| ticket DoD item | status |
|---|---|
| append-only property, per utterance and total | done for the clips (24/24 snapshots, 2/2 inputs); detector validated by `--self-test` |
| commit cadence: distribution and gap in seconds | done for the clips (§3) |
| `pending` size and persistence | done for the clips (§3) |
| any observed restatement, verbatim | none observed on the clips; detector proven non-vacuous; full text printed so a restatement would be visible |
| shipped clips **plus at least one fresh real-device recording** | **not done** — the clips are measured; the fresh recording is the user-owned step in §7 |
| capture step user-owned and self-announcing | implemented and reviewed (§7); the run itself is the user's |
| recommendation with the numbers behind it | done, preliminary (§6), to be confirmed by the fresh recording |
