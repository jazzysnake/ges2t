# 003-review — re-review of the `002` corrections

- **Verdict:** **pass**
- **Correction commit under review:** `30cd124` (`research/whisper-cpp-streaming`);
  reviewed against `1210637` (`001-review.md`) and `a4c9c7d` (original artifact).
- **Reviewer:** ges2t reviewer (independent). No `REPORT.md`/`002-comment.md`/harness/data file was
  modified; the only write is this file. No microphone (silence generated with stdlib `wave`), no
  clipboard, keystrokes, windows, `sudo`, installs, or network. Every command bounded by `timeout`.
- **Result:** all four `001` findings are fixed; a data-and-harness regression check passes; the
  central claim and every number I verified in `001` are unchanged. One cosmetic provenance nit
  remains (below) — it does not affect any number's value and does not block relaying.

## Task 1 — scope: data/harnesses untouched, corrections are docs-only

```
$ git diff main...HEAD -- src tests
(no output)                       # exit 0

$ git diff main...HEAD --name-only | grep -E '^(src|tests)/'
NONE (good)

$ git diff 1210637..HEAD --stat
 .../research/whisper-cpp-streaming/002-comment.md  | 111 +++++++++++++++++++++
 .../research/whisper-cpp-streaming/REPORT.md       |  80 ++++++++++-----
 2 files changed, 164 insertions(+), 27 deletions(-)

$ git diff 1210637..HEAD --name-only
docs/project/research/whisper-cpp-streaming/002-comment.md
docs/project/research/whisper-cpp-streaming/REPORT.md

$ git diff a4c9c7d..HEAD --stat -- …/data …/measure_streaming.py …/recompute_summary.py …/render_tables.py …/run_matrix.sh
(no output)                       # data + all four harnesses unchanged since the artifact commit
```

Confirmed: no `src/`/`tests/` change, and since `1210637` only `REPORT.md` and the new
`002-comment.md` changed. `git status --porcelain` is empty.

## Task 2 — each 001 finding is actually fixed in the new REPORT.md

### 2.1 §3.1 latency column — **fixed**

Re-rendered the committed `data/summary.jsonl` with the committed `render_tables.py` in a `/tmp`
copy and compared the report's §3.1 table cell-for-cell against `data/timings.md`:

```
§3.1 cells: 12 rows x 13 cols compared; rows with a real mismatch = 0
latency cells (report):
   ('5/1', 'jfk') report 0.995 | data 0.995
   ('5/2', 'jfk') report 1.67  | data 1.67
   ('10/2','jfk') report 2.03  | data 2.03
   ('5/1', 'clip30') report 1.315 | data 1.315
   ('5/2', 'clip30') report 2.23  | data 2.23
   ('10/2','clip30') report 2.23  | data 2.23
```

The only residual textual differences are `fmt()` trailing-zero renderings (`1.0` vs `1`, `0.0` vs
`0`, `1.240` vs `1.24`), not data differences. The caption now reads: *"`latency` is the timestamped
(`-ojf`) twin's real median audio lag for the same config (§3.2); all other columns are `-nt`."* —
it names the source. Fixed.

### 2.2 §3.3(d)/§1 silence — **fixed**

The report now states the **primary `-nt`** behaviour (all three models emit `you` → commit
`you you`) and the `-ojf` behaviour separately (turbo `thank you thank you`, medium/small
`[BLANK_AUDIO]` → nothing), and says it strengthens the conclusion. I re-ran it myself (fresh, `/tmp`
only; stdlib `wave` silence, `--scheme cap --no-timestamps`, W5/H1):

```
rev2-nt-medium-q5_0          committed='you you' commits=2 words=2
rev2-nt-small-q5_0           committed='you you' commits=2 words=2
rev2-nt-large-v3-turbo-q5_0  committed='you you' commits=2 words=2
```

Committed `-ojf` silence rows (unchanged `data/` > still): `cap-medium…silence ''`,
`cap-small…silence ''`, `cap-turbo…silence 'thank you thank you'`. The new §3.3(d) also correctly
notes that turbo's `-ojf` result is four words / **two** count-based commits and that the raw
`commits` field reading 8 is a legacy per-pass counter. Fixed.

### 2.3 §1 duty ranges — **fixed**

Actual `-nt` Vulkan duty min–max per model/config from `summary.jsonl`:

```
medium 5/2  0.6056 - 0.6956      small 5/2  0.2396 - 0.2533      turbo 5/2  0.7521 - 0.8368
medium 10/2 0.6491 - 0.6818      small 10/2 0.2598 - 0.2687      turbo 10/2 0.7557 - 0.8409
medium 5/1  1.1914 - 1.2420      small 5/1  0.4396 - 0.4750      turbo 5/1  1.4897 - 1.5277
```

§1 now prints `small 0.24–0.25, medium 0.61–0.70, turbo 0.75–0.84` (W5/H2) and `small 0.26–0.27,
medium 0.65–0.68, turbo 0.76–0.84` (W10/H2), and `0.44–0.48 / 1.19–1.24 / 1.49–1.53` (H=1). All
match. Fixed.

### 2.4 Load-sensitivity caveat — **present**

§1's duty paragraph now ends: *"The `< 1` margin is thin and load-sensitive: an independent
reproduction on this same worktree (load average ~2.9) measured `medium` car W=5/H=2 duty **0.918**,
not 0.646 (see §5)."* §5's load bullet was expanded with the same 0.918/0.933 vs 0.646/0.696 and the
statement that the H=2 margin "must not be read as a guarantee". Present and correct (those are the
values I reported in `001-review.md`). Fixed.

## Task 3 — sibling sweep: no new contradiction

- **§1 quiet-long streaming WER `0.15–0.21` → `0.15–0.26`.** All-model `-nt` quiet-long WER range is
  `0.1528–0.2639` → `0.15–0.26`. Correct (the old value was the medium-only range). The adjacent
  `jfk 0.045–0.136` is the all-model range `0.0455–0.1364`. Consistent.
- **§4 latency range `0.2–3.5 s` → `0.27–3.0 s median`.** All `-ts` cap (non-silence) median
  latencies run `0.27` (medium 5/2 quiet-long) to `3.0` (small 10/2) → `0.27–3.0`. Correct.
- **§7 `medium` W10/2 latency `≈ 2–3.5 s` → `≈ 2–3 s`.** Medium W10/2 timestamped-twin medians are
  `2.03, 2.06, 2.23, 2.92` → `≈ 2–3 s`. Correct (2.92 still rounds into "3").
- **§4 hallucination row** now reads *"all three models commit `you you` on the primary `-nt` decode
  (turbo `thank you thank you` on `-ojf`)"* — matches the corrected §3.3(d) and the data.
- **§2.2 note** added: in the `cap` scheme `H` plays the shipped adapter's `chunk_seconds` and `W`
  the `window_seconds`. This addresses the `001` naming point and introduces no contradiction.
- No other changed numbers: the §3.2 table, §3.3(a)–(c), §3.4 flush table, §3.5 slide paragraph, and
  the §6 falsification thresholds are byte-unchanged from the version I verified, and every value in
  them still matches `summary.jsonl`/`timings.md`.

## Task 4 — regression: nothing the review verified has changed

```
$ PYTHONPATH=<worktree>/src python3 recompute_summary.py     # in /tmp copy
wrote …/data/summary.jsonl (85 rows from 85 pass files)
rows committed/recomputed: 85 85
byte-identity ndiff: 0
```

`recompute_summary.py` still reproduces `summary.jsonl` byte-identically, and `data/` + the four
harnesses are unchanged since `a4c9c7d`/`1210637` (Task 1). Headline numbers intact: car W5/2
committed 69 words of which 56 (81 %) from a flush tail; car WER 0.297 (W5/2) / 0.141 (W10/2) vs
batch 0.062; the flush-split table (§3.4) and the boundary-duplication set are unmodified. Nothing I
verified in `001` regressed.

## Residual risks / minor note (non-blocking)

- **§5 provenance nit.** The expanded load bullet ends *"only `small` (duty ~0.24–0.25 here,
  ~0.4–0.5 in the review) has robust headroom."* The **value** is plausible — I measured `small`
  q5_0 Vulkan car W5/H2 `-nt` duty **0.4605** on this session (load ~1.5) — but **neither
  `001-review.md` nor `002-comment.md` measured `small` W5/H2 duty**: `001` measured only `medium`
  car/jfk (0.918/0.933) and `small` silence duty (0.648/0.789). So "in the review" is an incorrect
  source attribution for a number that is, in fact, reproducible here. Recommend rewording to
  "~0.4–0.5 under this session's load" (or dropping the parenthetical). Cosmetic: both 0.25 and 0.46
  are well under 1, so the "small has robust headroom" conclusion is unaffected.
- **Everything else is as bounded as before.** Duty remains load-sensitive (the report now says so);
  the legacy per-pass `committed_new`/`committed_total_words` provenance is now documented in §5.

## Could-not-verify

- The closed `needle` engine's exact agree-rule (unchanged from `001`; the harness implements the
  *stated* N=2 LCP rule and the shipped `drive()` path).
- The whole 36-config matrix as fresh runs (unchanged); I re-verified the §3.1 medium table against
  `summary.jsonl`/`timings.md`, and re-ran the silence control in both decodes.
- I did not measure `small` W5/H2 duty at load average ~2.9; my 0.4605 is at load ~1.5, which is why
  I call the §5 `small` figure's *value* plausible rather than exact.

## Safe to relay to the user verbatim?

**Yes.** The central conclusion (do not ship a whisper.cpp streaming path; keep
`supports_streaming = False`) is unchanged and re-verified, all four `001` findings are fixed, the
data/harness regression check passes, and the sibling sweep introduced no new contradiction. The
single residual is the cosmetic §5 source attribution ("~0.4–0.5 in the review"), whose number is
reproducible but whose attribution is wrong; fixing the wording is optional and does not affect any
stated value or the recommendation.
