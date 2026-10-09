# 004 — re-review: R1–R4 fixes on the commit-cadence diagnostic (ticket 32)

- **Ticket:** [commit-cadence-diagnostic](ticket.md) (ticket 32, the gate for tickets 33 and 34)
- **Branch:** `feat/commit-cadence-diagnostic`
- **Worktree:** `.worktrees/commit-cadence-diagnostic`
- **Reviewed delta:** `git diff c05065f..HEAD` (`0dcb9fe` harness fixes, `f86d862` docs +
  folded-in recording). Scope is the delta and its blast radius; the shipped path, the timeline
  reconstruction, the statistics and the safety refusal were already verified in
  [002-review.md](002-review.md) and were not re-reviewed.
- **Date:** 2026-10-09
- **Reviewer:** agent (read-only on the implementation; the only file written is this one)

## Verdict

**PASS — all four fixes are real, and the gate can close.** Tickets 33 and 34 are unblocked.

The main requested change (R1) is genuinely wired: the verdict now consults the batch-reference
detector, and I reproduced the reviewer's original non-adjacent-repeat mutation on the real
`clip30` data myself and saw detector 1 miss it while detector 2 caught it and the summary said
STOP. The user's fresh recording stays quiet under the same detector (`duplicated = []`,
PROCEED with a divergence caveat, not STOP). R2, R3 and R4 are also fixed as claimed. The only
findings left are non-blocking: two stale/ambiguous numbers in `001-comment.md` and a residual
false-positive/false-negative surface in the narrowed signal that the implementer has already
disclosed.

Nothing shipped changed (767 passed, mypy 0 errors, no audio tracked, capture still refuses a
non-tty), so there is no blast radius into the package.

## How I checked (commands are real; output is real)

All probes were run from the worktree with `uv run python`, never the microphone, never the
clipboard, never a keystroke. Probe scripts lived in `/tmp/rev-probe/` and were deleted after the
review; the harness file is byte-identical to `HEAD`.

### R1 — the new phrase signal fires on the non-adjacent repeat, and stays quiet on the real recording

I loaded the harness as a module and measured the two real inputs, then mutated the real `clip30`
measurement by appending a later committed delta that repeats an early phrase (non-adjacent, so
detector 1's leading-words-versus-tail scan cannot see it):

```
=== BASELINE: real clip30 ===
  diff=WordDiff(substitutions=13, insertions=0, deletions=2, reference_words=68)
  duplicated=[]
  verdict='PROCEED WITH THE NAMED CAVEAT. ...'

=== BASELINE: real user recording ===
  diff=WordDiff(substitutions=17, insertions=3, deletions=0, reference_words=62)
  duplicated=[]
  verdict='PROCEED WITH THE NAMED CAVEAT. ...'   # + CAVEAT 0b, not STOP

=== R1 REPRODUCTION: non-adjacent repeat mutations on real clip30 ===
  A: append ' He hoped there would be stew'
      detector1 overlapscan hits=0 strongest=0
      detector2 duplicated=['he hoped there would be stew']
      verdict='STOP. The engine restated committed text. ...'
  B: append ' the squalid quarter of the brothels'
      detector1 overlapscan hits=0 strongest=0
      detector2 duplicated=['the squalid quarter of the brothels']
      verdict='STOP. ...'
  C: append ' fat mutton pieces to be'
      detector1 overlapscan hits=0 strongest=0
      detector2 duplicated=['fat mutton pieces to be']
      verdict='STOP. ...'
```

The baseline user-recording run is the R1 requirement exactly: `insertions=3` (the WAV re-read;
the live run has 4) yet `duplicated=[]` and the verdict is PROCEED with CAVEAT 0b, **not** STOP.
So the narrowed signal is not a disguised `insertions > 0` rule.

I did not take the implementer's Control A on faith. I parsed the exact live strings out of
`/tmp/ges2t-commit-cadence-20261009-195815-report.txt` and ran the shipped functions on them:

```
LIVE transcript words: 66
LIVE batch words     : 62
word_diff(batch, streaming): WordDiff(substitutions=17, insertions=4, deletions=0, reference_words=62) WER=0.339
find_duplicated_phrases   : []
```

That reproduces the live report and `001-comment.md` §3.1 exactly, and shows detector 2 is quiet on
the authoritative live transcript.

### R1 — falsifying the signal in both directions

**Direction 1 — can it fail to fire?** (Is it a floor that never triggers?) Yes, it can fire; only
sub-threshold single-word repeats are missed:

```
1b synthetic hyp repeats 3x, ref repeats 2x: duplicated=['the cat sat the cat sat']
1c synthetic hyp repeats 2x, ref repeats 2x (batch also repeats): duplicated=[]
1a single-word non-adjacent repeat ' potatoes': duplicated=[] ... verdict=PROCEED
```

1b shows the signal genuinely fires on a true positive. 1c is the intended design (if the batch
path repeated it too, it is speech, not a seam artefact). 1a is the honest residual: a single-word
non-adjacent repeat is below both detectors' 2-word floor. The implementer records this limitation
in 003-comment.

**Direction 2 — can it fire on everything?** (Is it trivially satisfied by short/common phrases?)
It is not a "fires on everything" signal — on the two real inputs it is 0, and an ordinary
insertion-only divergence is quiet:

```
2c ordinary insertion only: duplicated=[]
```

But it is satisfiable by ordinary natural repetition when the batch reference happens to render
one instance differently, because the rule is only `${phrase} appears more often in hypothesis
than in reference` with a 2-word floor and no length/rarity weighting:

```
2a natural 'I think ... I think', batch renders one as 'I believe': duplicated=['i think']
2b common bigram 'it was' x2 vs batch x1: duplicated=['it was']
2d hyp='of the people of the town' ref='of the people in the town': duplicated=['of the']
2d hyp='we need to go to the shop to the bank' ref='we need to go to the shop and the bank': duplicated=['to the']
```

So a legitimate "…of the… …of the…" where the batch path substitutes one "of the" would trip a
false STOP. This is a real false-positive surface, but: (a) it did not occur on any measured input
(0 duplicated on both clips and the fresh recording, live and re-read); (b) a false STOP is a
human-review trip, not an unsafe action; and (c) the implementer discloses it in 003-comment
("A natural repetition of a 2+ word phrase that the batch reference omits entirely would be
flagged (a conservative STOP/review, not a false negative)"). I do not consider it gate-blocking,
but the gate's STOP must keep meaning "stop and report", not "cancel automatically", because on a
noisier recording this rule can over-trip.

### R1 — the self-test controls share the real code path

`--self-test` output (exit 0):

```
detector 2: batch-reference diff (the R1 validation controls)
    PASS  batch reference identical: no repeated phrase: detected 0; ins=0 sub=0 del=0 WER=0.000
    PASS  non-adjacent phrase repeat: overlap scan MISSES, batch diff fires: detected 1; ...
          overlap scan strongest=0 (miss required)
    PASS  ordinary insertions, no repeat (the fresh-recording shape): detected 0; ins=2 sub=0 del=0 WER=0.500
  6/6 self-test cases behaved as expected
```

I inspected the source: both `run_self_test` and `report_summary` call
`review_batch_reference(...)`, which is the single function that calls
`find_duplicated_phrases` and `word_diff`. The controls are not a parallel re-implementation, and
their expected values are asserted, not merely printed (`failures += 0 if ok else 1`,
`return 0 if failures == 0 else 1`). Caveat: the self-test exercises the *detector*
(`review_batch_reference`), not the `report_summary` aggregation/STOP branch. I exercised that
branch myself with mutations A–C above, so the verdict path is verified end-to-end on real data.

### R2 — the reference really is the gate-passed audio

Code: `gated_timeline` now returns the gate-passed `AudioBuffer`s, and `run_utterance` builds the
one-shot reference from `speech_buffers`, not `buffers`. Report evidence from the R1 re-run:

```
clip5  : batch reference  : computed on the 5.00 s the gate forwarded (silence removed), ...
clip30 : batch reference  : computed on the 27.00 s the gate forwarded (silence removed), ...
         vs one-shot batch reference: substitutions=13 insertions=0 deletions=2 WER=0.221 (n=68)
```

**Corrected `clip30` number: sub=13 ins=0 del=2 WER=0.221, n=68 (was sub=15 ins=0 del=3
WER=0.261, n=69).** That is exactly the reviewer's `batch(speech-only)` decomposition. The
"same audio" wording is gone from the module docstring, the `run_utterance` docstring and the
report line.

**Residual (non-blocking): `001-comment.md` §3 still carries the stale pre-R2 clip30 number**
(`substitutions=15 insertions=0 deletions=3 WER=0.261 (n=69)`, line 154). It should read
`substitutions=13 insertions=0 deletions=2 WER=0.221 (n=68)`. 003-comment has the corrected value
and even says "the only changed figure is `clip30`'s batch diff", so the intent is clear; only the
`001-comment` block was not updated. This does not affect the gate (both values produce the same
caveat), but it is a factual inconsistency in the headline artifact and should be corrected.

### R3 — the skipped detector is loud, and I exercised the path for real

The implementer stated this path was code-inspected only. It is not: I built a >30 s gate-passed
input by concatenating real `clip30` and `clip5` audio (32.00 s gate-passed) and drove
`run_utterance`:

```
gate-passed seconds = 32.00
batch_reference is None: True
batch_reference_note   : NOT COMPUTED: the gate forwarded 32.00 s, above the engine's 30 s
                         per-call limit; detector 2 did not run for this input
PER-INPUT: batch reference  : NOT COMPUTED: ...
SUMMARY: batch diff (detector 2): 0/1 input(s) ran; ...
SUMMARY: batch diff SKIPPED     : detector 2 did NOT run for 1 input(s): combined >30s (...)
SUMMARY:   CAVEAT 0: detector 2 (the batch-reference diff) did NOT run for 1 input(s) above, so
           the verdict does not rest on a batch comparison there.
```

So R3 is satisfied and the skip branch is exercised, not merely inspected. **Nit (non-blocking):**
when an input's batch diff is skipped, the PROCEED headline still says "no phrase was repeated by
the streaming path that the batch reference does not repeat", which is unknown for that input; the
adjacent `CAVEAT 0` contradicts the overclaim correctly, but the headline sentence could be
scoped to inputs where detector 2 ran.

### R4 — unused import removed

`PwRecordError` is gone from the `ges2t.audio.pw_record` import list in the harness. `grep`
against the harness folder finds it only in the 002/003 prose; the remaining `PwRecordError`
references are in `src/`, `tests/` and unrelated project docs, not the harness.

## Also checked

### The folded-in fresh recording matches the live report

I diffed `001-comment.md` §3.1 against
`/tmp/ges2t-commit-cadence-20261009-195815-report.txt`. Every recorded figure matches exactly:
25.04 s recorded / 25.00 s fed; 6 `flush()` calls (5 window crossings); 17 snapshots PASS;
17 commits, chars median 17.0 / p90 38.4 / max 63.0 (n=17); gap median 1.00 / p90 2.50 / max 4.00 s
(n=16); `pending` median 18.0 / p90 49.0 / max 72.0 (n=31); persistence median 1.00 / p90 1.00 /
max 1.00 s (n=29); `restatements: 0`; `sub=17 ins=4 del=0 WER=0.339 (n=62)`; the verbatim transcript;
the `Thank you.` pending at t=+0 and t=+10 never committed; the committed `' Great night.'` at
t=+16. The numbers are faithful.

**Residual (non-blocking):** §6 says "the measured totals are 3 inputs, 57.0 s of audio fed,
40 commits" — those totals come from the post-R1 re-run that re-read the persisted WAV (16 user
commits), while §3.1 reports the live run (17 commits). 5 + 19 + 16 = 40, not 5 + 19 + 17. The
artifact links to 003-comment for the full wording, so the source is traceable, but §6 should say
the totals use the WAV re-read or should use the live figure. It does not change the verdict.

Also: the live report's own verdict line is the pre-R1 wording (the live capture predates `0dcb9fe`),
so the live run's summary did **not** consult detector 2. The artifact does not misrepresent this
(it labels the quoted post-R1 summary as such), and detector 2 was independently applied to the
live strings in Control A, which I reproduced above.

### The WAV reproducibility caveat is stated, and the harness does not present the replay as equivalent

`001-comment.md` §3.1 and `003-comment.md` both state that the persisted WAV is int16, that
re-reading it shifts the transcript, and that the live float32 numbers are authoritative. The
harness labels the re-read input by filename and prints its own numbers; nothing presents the WAV
replay as equal to the live run. I reproduced the "1262 samples at the clip point" figure exactly:

```
frames 400698
abs(sample) >= 32767 : 1262
```

### The corrected DoD names falsifiable checks

`ticket.md` DoD item 1 now reads "The committed words are new text (the real test). Measure a
restatement two ways that can actually fail" — the committed-word overlap scan and the
batch-reference diff — and states "A hit from either detector is a STOP". DoD item 2 is relabelled
"The append-only prefix invariant (structural check, not a test)… a tautology over the `+=` fold".
Only the ticket-32 row of `docs/project/README.md` changed.

### Nothing shipped changed

```
$ timeout 300 uv run pytest -q
767 passed in 4.72s                       # exit 0, process exited
$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 49 source files   # exit 0
$ uv run mypy docs/.../measure_commit_cadence.py
Success: no issues found in 1 source file
$ uv run python .../measure_commit_cadence.py --capture --seconds 5 < /dev/null
refusing to record: stdin is not a terminal. ...
CAPTURE_EXIT=1
$ git ls-files | grep measure_commit
docs/project/live-dictation-ux/commit-cadence-diagnostic/measure_commit_cadence.py
$ git ls-files | grep -iE '\.wav$|\.flac$'   # NONE
$ grep -rn "measure_commit" src tests        # NONE
$ git status --porcelain                      # (empty after commit of this file)
```

`src/` and `tests/` are byte-identical to `HEAD` (`git diff HEAD -- src tests` empty). The
harness stays outside the package, so the suite is headless and engine-free.

## Could not verify

- **The live capture end-to-end.** I did not record the microphone; the countdown, the
  `● RECORDING` cue, the `pw-record` interaction and the device opening are code-reviewed only.
- **Why the int16 round-trip shifts the transcript.** I confirmed the WAV is int16 and that the
  shift is deterministic, but I cannot separate int16 quantisation from any other read-path
  difference. This is why the live numbers are treated as authoritative, which the artifact states.
- **Generality.** Two clips and one 25 s recording is not a sample. The residual
  `batch(speech-only) vs streaming` divergence (WER 0.221 on `clip30`, 0.339 live on the user
  recording) is not decomposed further; that needs a ground-truth reference over several clips.
- **The false-positive rate of the 2-word phrase rule** on a noisier recording. I demonstrated it
  can fire on common phrases (2a/2b/2d), but I have no measured false-positive rate; on the
  measured inputs it is 0.
- **The `empty reference` WER quirk** remains unreachable on real audio and untested in practice.

## Can the gate close?

**Yes.** The ticket's risk was "the engine's committed deltas are not append-only on real speech".
On the shipped clips (24 commits) and the user's fresh real-device recording (17 commits live, 16
re-read), both detectors report zero hits: the overlap scan is 0 and the batch-reference phrase
detector is 0, with the phrase detector shown non-vacuous in both directions and the divergence
handled as a caveat. The fresh recording is real speech through the real `pw-record` device and
crosses a flush (six `flush()` calls), which is the input the ticket required. Tickets 33
(`committed-delta-interface`) and 34 (`incremental-typing`) are therefore unblocked, with the
standing caveats the harness prints.

The two documentation inconsistencies (`001-comment.md` §3's stale `WER=0.261` and §6's unlabelled
re-read totals) should be corrected in the artifact, and the PROCEED headline should be scoped to
inputs where detector 2 ran, but none of these change the gate's outcome.
