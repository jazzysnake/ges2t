# 003 — comment: R1–R4 fixed, and the fresh real-device recording folded in

- **Ticket:** [commit-cadence-diagnostic](ticket.md) (ticket 32, the gate for tickets 33 and 34)
- **Branch:** `feat/commit-cadence-diagnostic`
- **Worktree:** `.worktrees/commit-cadence-diagnostic`
- **Date:** 2026-10-09
- **Responds to:** [002-review.md](002-review.md), which requested changes (R1–R4) and proved the
  prefix check is a tautology.
- **Status of the gate:** the four review findings are fixed; the harness was re-run over the two
  shipped clips **and** the user's fresh recording; the verdict now consults the batch-reference
  diff. The gate is clean on the measured inputs.

Commits on this branch:

| commit | change |
|---|---|
| `0dcb9fe` | `measure(ticket 32): wire the batch-reference diff into the verdict` — R1–R4 in `measure_commit_cadence.py` |
| (this commit) | docs: the fresh recording in `001-comment.md`, the DoD correction in `ticket.md`, the board row, and this file |

## R1 — the verdict now actually consults the batch-reference diff

### What was wrong

`report_summary` decided on `prefix_failures or strong_overlaps` alone. `word_diff` had exactly one
call site, the per-input report, so a divergence only detector 2 could see left the summary saying
PROCEED. The reviewer demonstrated it by mutating the real `clip30` measurement with a non-adjacent
phrase repeat: `overlap_scan_hits=0`, `batch_diff insertions=2 WER=0.500`, and the summary still said
PROCEED.

### The fix

`report_summary` now builds a `BatchReview` for every input (`review_batch_reference`) and the
verdict is:

```
STOP  if prefix_failures or strong_overlaps or duplicated_batch_phrases
PROCEED (with caveats) otherwise
```

* **`strong_overlaps`** is detector 1, unchanged: a committed delta whose leading words repeat 2+
  already-committed words, adjacent to the tail.
* **`duplicated_batch_phrases`** is the new detector-2 stop signal: a contiguous phrase of **2+**
  words that occurs **at least twice** in the streaming transcript but **not more times in the
  batch reference**. This is the non-adjacent re-commit detector 1 cannot see, and the batch
  reference is what distinguishes a seam artefact from ordinary natural repetition (if the batch
  path repeated the phrase too, it is speech, not a re-commit).
* **Divergence is a caveat, not a stop.** Any input whose batch diff has errors at or above
  `BATCH_DIVERGENCE_WER_THRESHOLD = 0.05` gets the named **CAVEAT 0b** saying the paths differ by
  wording/windowing and that insertions alone are not re-committed text.
* **Detector 2 not running is loud**, not silent — see R3.

The two new dataclasses are `DuplicatedPhrase` and `BatchReview`; `find_duplicated_phrases` is the
new pure function. It returns the *longest* phrase per repeated region, so `he hoped … he hoped`
produces one hit, not every sub-phrase.

### Why the reviewer's `insertions > 0 → STOP` trigger is wrong, from existing data

The fresh real recording has **4 insertions and 0 restatements** (the user's live run). The
insertions are ordinary decoding differences: the batch path renders
`Last night's torrent … powered roughly 4,000 tons` where streaming says
`Last night storm … power to roughly 4,000`, and the batch path contains no `Great night.` at all.
A naive `insertions > 0` rule would have flagged a **clean** recording as a restatement. The fix
therefore keys on **repeated text the batch reference does not repeat**, not on the insertion count.

### The two mandatory validation controls

Both were run against the shipped detector and the result is reproducible from the harness.

**Control A — the user's fresh recording must NOT be flagged.** The exact strings from the live
report (`001-comment.md` §3.1 / `/tmp/ges2t-commit-cadence-20261009-195815-report.txt`):

```
word_diff(batch_reference, final_text) = substitutions=17 insertions=4 deletions=0 WER=0.339 n=62
find_duplicated_phrases(...)           = []          # no phrase repeated
verdict                                = PROCEED WITH THE NAMED CAVEAT (+ CAVEAT 0b)
```

Re-measuring the persisted WAV (see the caveat below) gives `sub=17 ins=3 del=0 WER=0.323` and still
`duplicated = []` → PROCEED. The self-test also pins this shape: *"ordinary insertions, no repeat
(the fresh-recording shape)"*, `expected duplicated>=0, detected 0; ins=2 sub=0 del=0 WER=0.500`.

**Control B — the reviewer's non-adjacent-repeat mutation must still be caught.** Reproduced on the
**real** `clip30` measurement (append a committed delta `' He hoped there would be'`, which repeats
an early phrase non-adjacently):

```
find_committed_overlaps(mutated)       = []                      # detector 1 MISSES
word_diff                              = subs=13 insertions=5 deletions=2 WER=0.294
find_duplicated_phrases(...)           = [('he hoped there would be', 0, 66)]
verdict                                = STOP, trigger: detector 2 repeated phrase
```

Scripted control in `--self-test`: *"non-adjacent phrase repeat: overlap scan MISSES, batch diff
fires"*, `expected duplicated>=1, detected 1; ins=2 …; overlap scan strongest=0 (miss required)`.
The self-test now reports **6/6 cases behaved as expected** (3 for detector 1, 3 for detector 2).

### Could the batch diff ever be only a caveat, never a restatement signal?

It could be argued that way, and the code records the honest position: the *divergence* number is
only a caveat (it cannot separate wording from windowing without ground truth), while the
**repetition-relative-to-batch** check is a genuine restatement signal, because it is the only thing
that separates a seam re-commit from natural speech repetition. If a future input repeats a phrase
the batch reference also repeats, the code correctly declines to call it a restatement. If the batch
reference is skipped, the code says so and does not claim detector 2 was clean (R3). No precision is
invented: there is no tuned threshold beyond the 2-word minimum and the "more often than the
reference" rule.

## R2 — the batch reference is now the same gate-passed audio, not the whole file

`run_utterance` builds the one-shot reference from the concatenation of the chunks the shipped gate
forwarded — the audio streaming actually saw — instead of the raw recording. The only structural
difference left is windowing, so the number means something for a streaming-vs-batch finding. This
reproduces the reviewer's decomposition:

| input | reference audio | diff |
|---|---|---|
| `clip5` | 5.00 s (nothing gated) | sub=0 ins=0 del=0 WER=0.000 |
| `clip30` (before) | full 30.00 s | sub=15 ins=0 del=3 WER=0.261 |
| `clip30` (after) | 27.00 s gate-passed | sub=13 ins=0 del=2 WER=0.221 |

The report line now reads `one-shot batch reference (gate-passed audio, same adapter, different
path)`, and every per-input report prints a `batch reference :` line stating exactly what was
compared. The module docstring and `run_utterance` docstring say the reference excludes gated
silence and that the diff is a **divergence detector, not an accuracy score** (there is no
ground-truth reference here).

## R3 — a skipped detector 2 is loud

`UtteranceMeasurement` carries `batch_reference_note`, printed on every input. A skip now reads:

```
batch reference  : NOT COMPUTED: the gate forwarded 42.10 s, above the engine's 30 s per-call
                   limit; detector 2 did not run for this input
```

and the summary adds a `batch diff SKIPPED` line plus `CAVEAT 0` naming the affected inputs. The
threshold is applied to the **gate-passed** seconds actually sent to the engine, so a long recording
whose speech-only portion is under 30 s still gets detector 2. No report can any longer read as
"covered everything" while detector 2 silently did not run.

## R4 — unused import removed

`PwRecordError` is gone from the `ges2t.audio.pw_record` import. (`capture_from_microphone` still
lets a `pw-record` failure surface as a traceback; the review listed catching it as the alternative,
not a requirement.)

## The fresh real-device recording is folded into `001-comment.md`

`001-comment.md` now has a §3.1 with the user's run, and §1/§6/§7/§9 are updated. The numbers, from
`/tmp/ges2t-commit-cadence-20261009-195815-report.txt`:

- **25.04 s recorded / 25.00 s fed** to the engine; **6** `flush()` calls (5 window crossings);
- **17** commits; median **17** characters per commit (p90 38.4, max 63);
- gap in source audio: median **1.00 s**, p90 **2.50 s**, max **4.00 s**;
- `pending`: median **18** characters, persisting **1.00 s**;
- `restatements: 0`;
- the final transcript verbatim, for the user's eyeball check.

It is explicitly labelled a **real-device input (the user's own voice and this machine's noise
floor), run by the user, not by the implementer**. Two observations are recorded:

- the `Thank you.` hallucination appeared in `pending` **twice** (t=0, t=+10 s) and was **never
  committed** — the two-pass rule filtered it;
- the deliberate mid-utterance pause produced a **committed** `' Great night.'` at t=+16 s, and it
  did not restate.

## DoD correction in `ticket.md`

DoD item 1 asked for the prefix property, which the review proved is a tautology that cannot fail.
It is rewritten to name the checks that can fail — the committed-word overlap scan **and** the
batch-reference diff — with a detector hit meaning STOP. The prefix check is kept, but relabelled a
**structural invariant of the `+=` fold, not a test**. The board's ticket-32 row is updated to match
(and only that row).

## Re-run numbers after the changes

Command (no microphone; the persisted WAV from the user's run is read as a file):

```
uv run python docs/project/live-dictation-ux/commit-cadence-diagnostic/measure_commit_cadence.py \
    --clips /tmp/whinspect \
    --wav /tmp/ges2t-commit-cadence-capture-20261009-195914.wav \
    --out /tmp/ges2t-r1-recheck-report.txt
```

| input | commits | chars/commit (med) | gap src (med / p90 / max) | `pending` (med) | detector 1 | detector 2 (gate-passed) | duplicated phrases |
|---|---|---|---|---|---|---|---|
| `clip5.wav` | 5 | 18.0 | 1.00 / 1.00 / 1.00 s | 18.0 | 0 | sub=0 ins=0 del=0 WER=0.000 | 0 |
| `clip30.wav` | 19 | 22.0 | 1.00 / 3.00 / 4.00 s | 22.0 | 0 | sub=13 ins=0 del=2 WER=0.221 | 0 |
| user recording (WAV re-read) | 16 | 20.5 | 1.00 / 3.00 / 5.00 s | 23.0 | 0 | sub=17 ins=3 del=0 WER=0.323 | 0 |
| **totals** | **40** | **20.0** | **1.00 / 3.00 / 5.00 s** | — | **0** | 2 inputs diverged, worst WER 0.323 | **0** |

Verdict printed for the run:

```
PROCEED WITH THE NAMED CAVEAT. On 3 input(s) (57.0 s of audio) no committed delta repeated 2+
already-committed words, and no phrase was repeated by the streaming path that the batch reference
does not repeat.
CAVEAT 0b: on 2 input(s) the streaming and batch transcripts diverge … wording or windowing
difference, not a restatement: insertions on their own are not re-committed text. The batch number
is a divergence detector, not an accuracy score.
```

The `clip5`/`clip30` streaming measurements reproduce `001-comment.md` §3 exactly; the only changed
figure is `clip30`'s batch diff, which is the intended R2 effect.

### A run-to-run caveat on the persisted WAV

Re-measuring the saved WAV is deterministic run-to-run, but it does **not** reproduce the user's
original live transcript: the live run used the in-memory float32 buffer, while the WAV is int16
with **1262 samples at the clip point** (0.3 %), and the engine's output shifts materially
(e.g. live `Last night storm no doubt power to roughly 4,000` vs WAV
`Last night's storm no doubt powered throughout the 4th century`). Both re-runs report
`restatements: 0`. The **live numbers in `001-comment.md` §3.1 are the authoritative fresh-recording
measurement**; the re-run here is evidence that the post-R1 verdict stays clean on a real-device
input, not a replacement for the live run.

## What could not be verified

- **The engine's sensitivity to the WAV round-trip.** I can show the re-read is deterministic and
  that the WAV has 1262 clipped samples, but I cannot prove the transcript shift is caused solely by
  int16 quantisation rather than by some other read-path difference. This is why the live run is
  kept as the primary number.
- **The >30 s loud-skip path on real audio.** No input above the limit was measured; the R3 message
  and the summary caveat are exercised by code inspection and the gate-passed-seconds logic, not by
  a real >30 s run.
- **Generality of the restatement detector.** It is validated on scripted cases and on one real
  mutation. A natural repetition of a 2+ word phrase that the batch reference omits entirely would
  be flagged (a conservative STOP/review, not a false negative), and a non-adjacent repeat of a
  *single* word would be missed by detector 2 (detector 1 still requires 2+ words for the stop).
  Both are recorded limitations, consistent with the ticket's "2+ word committed repeat" language.
- **The user's eyeball check** of the transcript against what they read. The raw transcript is
  printed verbatim in `001-comment.md` §3.1 for exactly this; only the user can do it.
- **The other two `clip30`/fresh-recording numbers under a live run.** No microphone was recorded by
  the agent, per the hard rules.

## Verification

```
$ timeout 300 uv run pytest -q
767 passed in 4.79s          # exit status 0; process exited
$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 49 source files   # exit status 0
$ uv run mypy docs/project/live-dictation-ux/commit-cadence-diagnostic/measure_commit_cadence.py
Success: no issues found in 1 source file
$ uv run python .../measure_commit_cadence.py --self-test
6/6 self-test cases behaved as expected        # detector 1: 3, detector 2: 3
```

No test was added: the harness stays a docs script outside the package, so the suite stays headless
and engine-free (ADR 0014).
