# 002 — review: commit-cadence diagnostic (ticket 32)

- **Ticket:** [commit-cadence-diagnostic](ticket.md) (ticket 32, the gate for tickets 33 and 34)
- **Branch:** `feat/commit-cadence-diagnostic`
- **Worktree:** `.worktrees/commit-cadence-diagnostic`
- **Reviewed:** `git diff main...HEAD` — 4 files: `docs/project/README.md`, `ticket.md`, the harness
  `measure_commit_cadence.py`, and the implementer's `001-comment.md`.
- **Date:** 2026-10-09
- **Reviewer:** agent (read-only on the implementation; the only file written is this one)

## Verdict

**REQUEST CHANGES — but the capture step is safe and trustworthy to run now.**

The measurement machinery is correct, the two replacement detectors both demonstrably fire, the
shipped path is really what is measured, the numbers reproduce, and the safety refusal works. What
is missing is not measurement correctness: it is that **one of the two load-bearing detectors — the
one-shot batch-reference diff — is never consulted by the recommendation.** The summary's proceed/stop
decision rests only on the prefix check (a tautology) and the committed-word overlap scan. The batch
diff is printed per input and then ignored. For a ticket whose whole purpose is to gate a feature,
that is a defect in the verdict logic, not in the sample-collection logic.

None of the required changes touch the capture path, so the user can run
`--capture --seconds 25` while they are made. The gate must not be *closed* until they are.

## What I verified, and how

### 1. The `001-comment.md` §2.1 admission is correct: the prefix check cannot fail

The claim "`transcript` is built only by `+=`, and every prefix of a concatenation is a prefix of
that concatenation" is true, and the harness's prefix check is a tautology over the fold.
`check_prefix_property` compares the prefix sums of the recorded committed deltas against
`final_text`, which is itself the sum of exactly those deltas. There is no independent information in
the comparison.

```
random delta sequences: 0 prefix failures out of 20000
tampering final_text to 'Hello worl': failures=1
```

(A 20 000-sequence random property test produced zero failures; the only way to make the check fail
is to corrupt `final_text` away from the sum of the deltas, i.e. to break the invariant it is
nominally testing.)

Consequence: **DoD item 1 is satisfied in letter only.** It does not test "the engine's committed
deltas are themselves append-only", which is the risk the ticket names. The implementer says so in
§2.1, and that admission is honest and correct. The real test is the overlap scan, which I falsified
below.

### 2. Detector 1 — committed-word overlap scan — fires when it should

Mutating the *real* `clip30` measurement (not a synthetic toy) and re-running the shipped detector:

```
REAL clip30: commits=19 overlaps=0 prefix_failures=0
MUTATION 1 (duplicate a real commit): overlaps=1 strongest=1
    example_before='and' delta=' and'
MUTATION 2 (full-text-not-a-delta): overlaps=4 strongest=12
    first hit: already committed 'he' / new delta 'He hoped there would be stew'
```

Scripted cases through the same function:

```
FIRE  baseline append-only: strongest=0
FIRE  2-word restatement (later pass): strongest=2
FIRE  duplicated flush tail: strongest=2
FIRE  full-text-not-a-delta engine: strongest=2
MISS  non-adjacent phrase repeat: strongest=0   (known limitation, see below)
```

So `[4] restatements: 0` on the clips is a **real zero from a detector that fires**, not a vacuous
zero. The `--self-test` is not taken on faith: I read it, confirmed both restating cases exercise
`find_committed_overlaps` (the same function the real measurement uses), and independently
reproduced the firing with my own mutations.

**Known limitation (documented, and covered by detector 2):** the scan matches the *leading* words of
each new delta against the *tail* of the committed history. A restatement of a phrase that is not
adjacent to the end of the transcript is missed:

```
MUTATION 3 (non-adjacent phrase repeat): overlap_scan_hits=0 (MISS by design);
    batch_diff insertions=2 deletions=0 WER=0.500
```

That is a defensible narrowness for this seam — a windowed re-commit surfaces at the start of a
delta — and the batch diff catches the miss. But it is exactly why detector 2 must be wired in.

### 3. Detector 2 — batch-reference diff — works, but does not affect the verdict

`word_diff` is correct and reuses the same normalisation on both sides:

```
OK   identical: (0,0,0)
OK   full-text double (duplicated transcript): insertions=3  wer=1.000
OK   substitution: (1,0,0)
OK   deletion: (0,0,1)
OK   insertion: (0,1,0)
OK   decimal intact "3.2": (0,0,0)
OK   hyphen splits "well-known" == "well known": (0,0,0)
OK   apostrophe dropped "don't" == "dont": (0,0,0)
BAD  empty reference edge: insertions=1 but wer=0.000
```

(The only quirk is the empty-reference guard in `WordDiff.wer`, which returns `0.0` when
`reference_words == 0`; it cannot arise for real audio, since the batch reference is non-empty.)

The problem is not the function — it is that it is called in exactly one place, the per-input
report, and never in `report_summary`:

```
=== word_diff call sites ===
563:def word_diff(...)
779:    diff = word_diff(measurement.batch_reference, measurement.final_text)   # per-input only

=== summary STOP condition ===
905:    if prefix_failures or strong_overlaps:
```

So a divergence that only detector 2 sees (my mutation 3) would leave the summary saying "no
committed delta restated already-committed text … PROCEED". On the clips this is masked because the
batch diff happens to be insertion-free, but the logic does not enforce that. **This is the main
requested change (R1).**

### 4. The harness measures the real shipped path, unmodified

- `WhistleTranscriber.stream()` really calls `streaming.drive`:
  `streaming.drive called 1 time(s); returns iterator: True` (spy wrapper intercepted the shipped call).
- The recording subclass only calls `super()` and records; it does not alter return values. The
  harness's own transcript equals a recorder-free `transcriber.stream()` run:

```
direct final : 'He hoped there would be stew for dinner, turnips and carrots and bruised potatoes.'
harness final: 'He hoped there would be stew for dinner, turnips and carrots and bruised potatoes.'
equal: True
direct non-final partials == harness partial_texts: True
```

- The snapshot sequence is the prefix sum of the recorded deltas, and the prefix check passing on
  the clips is itself the proof that `sum(recorded committed) == final_text`, i.e. the recording did
  not diverge from the fold. `drive()` is not re-implemented anywhere in the harness.

### 5. Source-timeline reconstruction is sound

- `gated_timeline` calls the shipped `whistle._has_speech` on the same 1 s buffers `stream()` sees;
  `stream()` filters with the same predicate via `_speech_only`, so the verdicts cannot disagree.
- Process calls map one-to-one onto fed chunks (the harness feeds exactly `chunk_seconds` buffers):

```
fed chunks: 5, gate-starts: 5, process events: 5
source_seconds mismatches vs gate starts: 0
timeline_from_gate: True
```

- Silence-dropped chunks still advance the clock. `clip30` shows it directly in the pass table:
  `t_source` jumps `23.00s → 25.00s` (the 24 s chunk was dropped) with no process event, and the
  final flush sits at `t_source=28.00s` although 30 s was recorded — the trailing 2 s was dropped.
  Total fed 27 s of 30 s, matching the report.

### 6. Reported statistics reproduce

Re-run: `uv run python .../measure_commit_cadence.py --clips /tmp/whinspect --out /tmp/review-clips-report.txt`
(exit 0). Every figure in `001-comment.md` §1–§3 reproduces:

| figure | 001-comment | my run |
|---|---|---|
| clip5 snapshots / commits | 5 / 5 | 5 / 5 |
| clip5 chars/commit | 18.0 / 25.2 / 26.0 | 18.0 / 25.2 / 26.0 |
| clip5 gap source | 1.00 / 1.00 / 1.00 | 1.00 / 1.00 / 1.00 |
| clip5 pending | 18.0 / 27.6 / 33.0 | 18.0 / 27.6 / 33.0 |
| clip30 snapshots / commits | 19 / 19 | 19 / 19 |
| clip30 chars/commit | 22.0 / 35.0 / 50.0 | 22.0 / 35.0 / 50.0 |
| clip30 gap source | 1.00 / 3.00 / 4.00 | 1.00 / 3.00 / 4.00 |
| clip30 pending | 22.0 / 38.6 / 51.0 | 22.0 / 38.6 / 51.0 |
| clip30 batch diff | sub=15 ins=0 del=3 WER=0.261 | sub=15 ins=0 del=3 WER=0.261 |
| total commits | 24 | 24 |
| total chars/commit | 20.0 / 34.0 / 50.0 | 20.0 / 34.0 / 50.0 |
| total gap | 1.00 / 2.90 / 4.00 | 1.00 / 2.90 / 4.00 |

Only the wall-clock gap figures differ in the second decimal (e.g. clip5 median wall gap 0.14 s vs
0.15 s), which is expected run-to-run variation. **No reported figure failed to reproduce.**

### 7. Safety: the harness cannot record unattended

```
$ uv run python .../measure_commit_cadence.py --capture --seconds 5 < /dev/null
EXIT=1
stderr: refusing to record: stdin is not a terminal. The capture step is user-owned -- run
this script yourself, in your own terminal, and press Enter at the prompt so you choose the
moment recording starts.
```

- Same refusal for the bare invocation (capture is the default): exit 1.
- The refusal is checked in `main()` *before* `load_engine`, and again inside
  `capture_from_microphone`, so no engine, no `pw-record`, and no report is produced.
- No clipboard, keystroke or injection surface: `grep -niE "clipboard|ydotool|uinput|pyperclip|wl-copy|
  xclip|pbcopy|type_text|inject|keystroke"` matches only prose in comments/docstrings. Imports are
  stdlib plus `ges2t` read/config/audio/transcriber modules.
- Writes: the capture WAV is always under `TMP_DIR = /tmp`; the report defaults to `/tmp` (a user
  may point `--out` elsewhere, which is an explicit choice). No audio is tracked in git
  (`git ls-files | grep -iE '\.wav$|\.flac$'` → empty).

### 8. Nothing shipped changed

```
$ timeout 300 uv run pytest -q
767 passed in 4.65s          # exit 0, process exited
$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 49 source files   # exit 0
$ uv run mypy docs/.../measure_commit_cadence.py
Success: no issues found in 1 source file
```

- No test added: the diff touches no file under `tests/` or `src/`.
- The harness is outside the package (`git ls-files | grep measure_commit` → under `docs/...`), is
  not referenced by `tests/` or `src/`, so `uv run pytest` stays headless and engine-free.
- `git status` clean; `src/` and `tests/` byte-identical to `HEAD`.

### 9. Removed `--reference`/accuracy mode

The removed mode is fully gone: no `--reference` option, no `resolve_reference`, `report_accuracy`,
`Alignment`, `render_aligned`, `strip_edges`, `print_reference_text`, or `PUNCTUATION_EDGES`
(grep → NONE). **Nuance:** `difflib` and the `WordDiff.wer` computation are still present, because
they are the *kept* batch-reference detector (detector 2), not leftovers from the accuracy mode. If
the orchestrator literally intended no `difflib`/WER in the file, that intent conflicts with the
implementer's own load-bearing detector 2; I do not recommend removing them. Nit: `PwRecordError` is
imported but unused.

## What the `clip30` `WER=0.261` does and does not mean

**Does mean:** the streaming path's word sequence differs from a one-shot batch transcription of the
same recording by 15 substitutions + 3 deletions over 69 reference words. The difference is real and
visible (e.g. `latled` vs `laid`, and the Spanish segment rendered differently).

**Does not mean:**

- It is **not** an accuracy score. There is no ground-truth reference text; the "reference" is the
  batch path's own output. It does not contradict ADR 0011's WER 0.176 (windowed) / 0.191 (one-shot),
  which were measured against a ground-truth passage.
- It is **not** a clean streaming-vs-batch comparison, because the two paths do not see the same
  audio. The streaming decoder receives only the 27 s of gate-passed chunks; the batch reference is
  the full 30 s. The docstring's "same audio" is inaccurate. I decomposed it:

```
whole audio 30.00s, speech-only 27.00s
[reported] batch(whole) vs streaming : sub=15 ins=0 del=3 WER=0.261 n=69
batch(speech-only) vs streaming      : sub=13 ins=0 del=2 WER=0.221 n=68
batch(whole) vs batch(speech-only)   : sub=4  ins=0 del=1 WER=0.072 n=69
```

  So about 0.040 of the 0.261 (~28 % of the error, and a 0.072 WER shift all by itself) is purely the
  silence-gate mismatch; the remaining 0.221 is the windowed-reset/full-context difference.
- It is **not** a restatement/duplication signal: insertions are 0, consistent with the overlap
  scan's 0.

**Can the harness support a claim that "the shipped streaming default transcribes materially
differently from batch on longer audio"?** Not by itself. It supports "the outputs differ on this
one clip", and with the speech-only control, "they still differ after removing the silence-gate
confound". It is a sound *detector* (it fires on divergence) but not a sound *quality measurement*:
n=1 long clip, a confounded reference, and no ground truth. A claim about material quality
difference needs a separate measurement against a reference transcript over several clips. The
harness should say this rather than leave the number to be read as a quality verdict.

## Definition-of-done findings

| DoD item | Finding |
|---|---|
| append-only prefix, per utterance and total | Implemented and PASS (24/24 snapshots on clips). **Structurally unfalsifiable**; the implementer's §2.1 admission is correct. Satisfied in letter, not a real test of the named risk. |
| commit cadence, distribution and gap in seconds | Implemented; reproduced exactly. |
| `pending` size and persistence | Implemented; reproduced exactly. |
| any observed restatement, verbatim | Overlap scan implemented, validated by self-test **and** by mutation of the real measurement; none on the clips (real zero). Batch diff also implemented and working, but **not wired into the verdict** (R1). |
| shipped clips **plus ≥1 fresh real-device recording crossing a flush** | Clips done; `flush()` exercised 5× mid-utterance on `clip30`. Fresh recording is user-owned and still missing — the gate stays open. |
| recommendation with the numbers behind it | Present (proceed with 3 caveats). It uses cadence + overlap scan but **not** the batch diff; the "same audio" wording is inaccurate (R1, R2). |
| harness out of the package; pytest headless/engine-free | Confirmed: 767 passed, harness never imported by tests/src. |
| ask before recording; `/tmp` only; never commit audio | Confirmed: non-tty refusal exits 1; no clipboard/injection surface; WAV under `/tmp`; no audio tracked. |

## Required changes

- **R1 — wire the batch-reference diff into the verdict.** `report_summary` currently decides on
  `prefix_failures or strong_overlaps` only. It must also aggregate
  `word_diff(batch_reference, final_text)` over the inputs and, at minimum:
  - treat **insertions > 0** as a possible duplication/restatement and emit a STOP/REVIEW verdict
    (insertions are the signature of duplicated text), and
  - emit a named caveat for any input whose substitution+deletion rate is non-trivial, stating that
    streaming and batch diverge on that input and that the divergence may be wording or windowing,
    not restatement; and
  - never print "no committed delta restated already-committed text … PROCEED" as though detector 2
    were clean when its diff is not.
  This is what makes the implementer's own §2.1 claim ("the check the recommendation stands on is
  new text: overlap scan **and** batch-reference diff") true of the code.
- **R2 — correct the "same audio" claim and disclose the confound.** The module docstring, the
  `with_batch_reference` comment, the report line "one-shot batch reference (same audio, same
  adapter, different path)", and `001-comment.md` §2.1/§3 should say that the streaming path sees
  only gate-passed chunks while the batch reference sees the full recording, so the diff conflates
  silence removal with windowing. Either compute the reference on the gate-passed concatenation (the
  `batch(speech-only)` variant above) or print the confound in the report. It must also state that
  this number is a detector, not an accuracy score.
- **R3 (small) — say when detector 2 did not run.** The batch reference is skipped when
  `duration > MAX_SECONDS` (30 s). The report prints no batch line at all, so a >30 s capture would
  silently lose detector 2. Print "batch reference not computed (>30 s)" or cap the requested capture
  accordingly (the 25 s default is fine).
- **R4 (nit) — remove the unused `PwRecordError` import**, or catch it around `capture_from_microphone`
  so a `pw-record` failure is a clean message rather than a traceback.

## Could not verify

- The fresh real-device capture end-to-end: I deliberately did **not** record the microphone (the
  capture step is user-owned). The countdown, `● RECORDING` cue, counter, `pw-record` interaction and
  resulting WAV are code-reviewed only, not executed. In particular I cannot confirm this machine's
  default input device opens cleanly under `pw-record`.
- Engine behaviour on this device's noise floor and the user's voice; on a real mid-utterance pause;
  and on any input longer than the two shipped clips.
- Statistical generality: two clips, only one of which is long, is not a sample.
- Whether the residual `batch(speech-only) vs streaming` WER 0.221 is windowing alone or something
  else; isolating it would need a ground-truth reference and multiple clips.
- The `empty reference` WER quirk is unreachable on real audio, so it is untested in practice.

## Is the harness trustworthy enough to run the capture step on?

**Yes.** The capture path is safe (refuses a non-tty, spawns `pw-record` only after the user presses
Enter, writes the WAV under `/tmp`, never touches the clipboard or emits a keystroke), and the
numbers it will produce for the two deciding measurements — the committed-word overlap scan and the
cadence/`pending` distributions — are correct and come from the real shipped fold. The one caveat is
that the summary line the user will read does not yet fold in the batch-reference diff (R1), so they
should read the per-input `[4] … vs one-shot batch reference` line themselves, and treat any
insertions there as the restatement signal the gate exists to catch. Fix R1–R2 before closing the
gate.

The ticket's preliminary "proceed with a named caveat" recommendation is **supported by the evidence
I could check on the clips**, and the open risk after the fresh recording is real but narrow: a
2+ word committed repeat, a non-adjacent phrase repeat that only the batch diff would surface, or a
device-specific restatement. None of those can be ruled out by the clip measurement, which is exactly
why the fresh recording remains required and why detector 2 must feed the verdict.
