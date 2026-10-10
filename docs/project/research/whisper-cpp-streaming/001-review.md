# 001-review — whisper.cpp streaming (measurement/discovery artifact)

- **Verdict:** **changes requested** (central claim sound; two documentation defects must be fixed
  before the report is relayed verbatim)
- **Branch:** `research/whisper-cpp-streaming` (worktree `.worktrees/whisper-cpp-streaming`)
- **Commit reviewed:** `a4c9c7d` (`git rev-parse HEAD`); `git merge-base main HEAD` = `6472a20`
  (main), so `main...HEAD` is the single streaming commit: `93 files changed, 3337 insertions(+)`.
- **Reviewer:** ges2t reviewer (independent). No report/harness/data file was modified; the only
  write is this file. No microphone, clipboard, keystrokes, windows, sudo, package installs, or
  network were used. All whisper.cpp models/audio were read from the existing
  `~/.cache/ges2t-discovery/whisper-cpp-provider/` cache; every command was bounded by `timeout`.

## Summary

The **central claim** — whisper.cpp partial-window output is not stable enough for ges2t's
commit-cadence streaming on this machine, at any measured W/H/model — **reproduces**. I recomputed
the medium-Vulkan `-nt` table from the raw passes (matches cell-for-cell), reproduced two configs
end-to-end with the committed harness (byte-identical per-pass transcripts and WER), reproduced the
`-nt` decode effect (car clip 0.0625 vs 0.1562), reproduced the silence control, the boundary
duplication, and the flush-dominance split, and verified `recompute_summary.py` rebuilds
`summary.jsonl` byte-identically.

The verdict is **changes requested**, not pass, because the report contains two user-facing claims
that its own data does not support as stated:

1. **§3.1 primary table, `latency` column.** For the `clip30` and `jfk` rows (6 of 12 cells) the
   printed values are the **`-nt` synthetic** latencies, not the "timestamped twin's real median
   audio lag" the caption claims, and they contradict the report's own `data/timings.md`.
2. **§3.3(d) silence control.** The "medium and small … commit nothing" contrast is true only on the
   `-ojf` decode the control was actually run with; on the primary `-nt` decode medium **and** small
   emit `you` and the rule commits `you you`. The report itself warns that `-nt` changes the decode
   (§2.3) but does not apply that warning to its own silence control.

Neither defect overturns the conclusion; both are "a claim that reads as measured" that is not, as
stated, on the decode/source the report names.

---

## Criterion 1 — does the harness mirror the shipped commit rule, and what is N?

**Shipped code read:**

`src/ges2t/streaming.py::_drive` (lines ~175-215): `transcript += pass_.committed` (append-only),
and `if fed_samples >= window_seconds * group.sample_rate: transcript += decoder.flush();
fed_samples = 0`, plus a final `decoder.flush()` at end of input. `window_seconds`/`chunk_seconds`
come from the adapter; the loop feeds `_group_chunks(chunks, chunk_seconds=chunk_seconds)`.

`src/ges2t/transcribers/whistle.py::_WhistleStreamingDecoder`: `process()` returns the engine's
`text` as `committed` (no threshold, vote, or confidence); `flush()` ends the feeder so the closed
engine calls `needle_stream_transcribe_stop()`, "which commits the unconfirmed tail and resets the
stream", then opens a fresh generator.

**N.** `docs/adr/0011-streaming-sliding-window.md` quotes `needle.h` verbatim: *"re-transcribes the
buffered audio and commits **the words two consecutive passes agree on**."* The rule's **N is 2**;
there is no vote count. The report states this (§2.1, harness docstring) and it is correct.

**Harness** (`measure_streaming.py::simulate`, `scheme=="cap"`): a buffer starts at a reset point and
grows by `hop_s` per pass; the pass ends when `end = reset+fed` reaches the clip or `full_buffer
>= window_s`; then it commits `cur_seq[window_committed_len : agreed]` where `agreed =
longest_common_prefix(prev_words, cur_words)`, and at a flush commits `cur_seq[window_committed_len:]`
and resets. This is behaviourally the same window mechanism as `drive()` with **`chunk_seconds = H`**
and **`window_seconds = W`** — same growing buffer, same "flush when fed reaches W" boundary, same
final flush, same append-only concatenation, same N=2 longest-common-prefix semantics.

**Finding (minor, non-blocking):** the report labels H "hop", but in the shipped code the feed unit
is `chunk_seconds` (default 1). The mapping is honest, but calling it a "hop" obscures that H is
also the process() chunk size. `cap` is the faithful mirror; `slide` is extra and is labelled as
such.

The report's §5 "Harness rule fidelity" caveat (the engine may implement "agree" with word-boundary
or probability subtleties) is the one thing I **could not** falsify — the engine is closed — but the
harness implements the *stated* rule (N=2, LCP) and the *shipped* code path correctly.

## Criterion 2 — recompute duty and WER from the raw data

**Independent recomputation** (my script, ticket-32 `word_diff`, count-based commit rule from
`overlap_cur_all`/`agreed`/`flushed`):

```
label                              pass    wall   audio   duty   cw flush  rule    wer 2passWER
ntcap-medium-vk-w10h2-commit         13   16.82  25.044  0.672   64    39    25 0.1406   0.9688
ntcap-medium-vk-w5h1-commit          26   31.10  25.044  1.242   68    14    54 0.2812   0.3750
ntcap-medium-vk-w5h2-commit          13   16.18  25.044  0.646   69    56    13 0.2969   1.3281
ntcap-medium-vk-w10h2-jfk             6    7.42  11.000  0.675   23     9    14 0.0455   0.3636
ntcap-medium-vk-w5h1-jfk             11   13.11  11.000  1.191   23     7    16 0.0455   0.2727
ntcap-medium-vk-w5h2-jfk              6    7.65  11.000  0.696   25    15    10 0.1364   0.5000
ntcap-medium-vk-w10h2-quiet-long     12   16.23  23.806  0.682   69    34    35 0.1528   0.6944
ntcap-medium-vk-w5h1-quiet-long      24   29.09  23.806  1.222   70    13    57 0.2083   0.3611
ntcap-medium-vk-w5h2-quiet-long      12   14.72  23.806  0.618   68    23    45 0.1667   0.5556
```

These match the report's §3.1 medium table on `passes`, `windows`, `duty`, `first commit`, `prefix
agree` ( = summary `prefix_agreement`), `regressed`, `rewritten`, `dup words`, and `WER (stream)`.

**`recompute_summary.py` reproduces `summary.jsonl` exactly** (run in a `/tmp` copy with
`PYTHONPATH=…/src`):

```
wrote …/data/summary.jsonl (85 rows from 85 pass files)
rows 85 85
ndiff 0
```

So the summary is internally consistent with the committed raw passes under the count-based rule.

### Defect 1 — §3.1 `latency` column (6 of 12 cells)

Report §3.1 caption: *"`latency` is the timestamped twin's real median audio lag (§3.2); all other
columns are `-nt`."* Re-rendering the committed `data/summary.jsonl` with the committed
`render_tables.py` gives, for the medium `-nt` primary table:

```
| medium/vulkan | 5/1 | clip30 | … | 1.315 | … |
| medium/vulkan | 5/2 | clip30 | … | 2.23  | … |
| medium/vulkan | 10/2| clip30 | … | 2.23  | … |
| medium/vulkan | 5/1 | jfk    | … | 0.995 | … |
| medium/vulkan | 5/2 | jfk    | … | 1.67  | … |
| medium/vulkan | 10/2| jfk    | … | 2.03  | … |
```

REPORT.md §3.1 prints, for those same cells, `0.833 / 0.625 / 1.721` (clip30) and `0.357 / 0.65 / 1.0`
(jfk) — which are exactly the **`-nt` synthetic** `commit_latency_median_s` values of the
`ntcap-medium` rows, not the timestamped-twin values. My probe:

```
W/H   clip       REPORT lat timings.md(ojf twin)  ntcap syn match?
5/1   quiet-long      0.860                0.860      0.750 OK
5/2   quiet-long      0.270                0.270      0.214 OK
10/2  quiet-long      2.060                2.060      2.364 OK
5/1   car             1.130                1.130      0.659 OK
5/2   car             1.660                1.660      0.361 OK
10/2  car             2.920                2.920      3.542 OK
5/1   jfk             0.357                0.995      0.357 MISMATCH(nt)
5/2   jfk             0.650                1.670      0.650 MISMATCH(nt)
10/2  jfk             1.000                2.030      1.000 MISMATCH(nt)
5/1   clip30          0.833                1.315      0.833 MISMATCH(nt)
5/2   clip30          0.625                2.230      0.625 MISMATCH(nt)
10/2  clip30          1.721                2.230      1.721 MISMATCH(nt)
```

So §3.1 contradicts the report's own full table (`data/timings.md`, regenerated identically from the
committed data) for the `jfk` and `clip30` rows. §3.2's latency **ranges** are built from the
timestamped twins (e.g. medium 10/2 "2.03–2.92"), so §3.1 and §3.2 use different sources for the
same column. This is a user-facing "measured" number that is not the measurement it names.

### Defect 3 — §1 collapsed duty ranges (3 of 6)

Actual `-nt` Vulkan duty ranges (from `summary.jsonl`):

```
('small', 5.0, 2.0) duty 0.2396 - 0.2533
('medium',5.0, 2.0) duty 0.6056 - 0.6956
('turbo', 10.0,2.0) duty 0.7557 - 0.8409
```

Report §1 prints `small 0.24–0.26`, `medium 0.62–0.70`, `turbo 10/2 0.76–0.81` for those. The §3.2
per-clip values are correct (`small 5/2 0.25/0.25/0.24/0.25`, `medium 5/2 0.62/0.65/0.61/0.70`,
`turbo 10/2 0.76/0.81/0.79/0.84`), so only the §1 collapsed ranges are wrong. Direction is benign
(both extremes still < 1), but the numbers don't match the data.

## Criterion 3 — the four load-bearing claims

### (a) Silence shock — **verified for the decode the control used; mislabeled**

Generated 10 s of digital silence with stdlib `wave` (no microphone). Direct runs:

```
=== medium DEFAULT ===        [00:00:00.000 --> 00:00:02.060]   you
=== medium -nt -np ===         you
=== small  DEFAULT ===        [00:00:00.000 --> 00:00:10.000]   [BLANK_AUDIO]
=== small  -nt -np ===         you
=== turbo  DEFAULT ===        [00:00:00.000 --> 00:00:29.980]   Thank you.
=== turbo  -nt -np ===         you
```

Running the committed harness on the 10 s silence (W5/H1), outputs to `/tmp` only:

```
summary_nt.jsonl : turbo 'you you' ; medium 'you you' ; small 'you you'     (committed_words 2 each)
summary_ts.jsonl : turbo 'thank you thank you' (4 words) ; medium '' ; small ''
```

So:
- **`-ojf` (default text + timestamps), which is what the silence control actually ran:** turbo emits
  `Thank you.` and commits `thank you thank you` (4 words); medium/small emit `[BLANK_AUDIO]` and
  commit nothing. This is the report's claim, and it holds **on that decode**.
- **`-nt` (the report's primary decode):** all three models — **including medium and small** — emit
  `you` and the two-pass rule commits **`you you`**. The report's §3.3(d) contrast ("medium and small
  … commit nothing") is therefore decode-specific and unlabelled, and the report's §2.3 warning that
  `-nt` changes the decode is not applied here.

The conclusion (turbo hallucinates and commits on silence) stands; the report *understates* the
medium/small behaviour under its own primary decode. §3.3(d)'s "(8 commits)" is also the passes'
legacy commit count (see the provenance note in Criterion 5); the count-based rule yields 2.

### (b) Boundary duplication — **verified from `data/passes/`**

Count-based committed text per `ntcap` config (dups = adjacent repeated words):

```
ntcap-medium-vk-w5h2-jfk   dups=[(9,'country')]   text: … what your country country can do for you for you ask …
ntcap-medium-vk-w10h2-jfk  dups=[(21,'country')]  text: … do for your country country
ntcap-medium-vk-w5h1-jfk   dups=[(21,'country')]
ntcap-small-vk-w10h2-jfk   dups=[(21,'country')]
ntcap-small-vk-w5h1-jfk    dups=[(21,'country')]
ntcap-turbo-vk-w10h2-jfk   dups=[(21,'country')]
ntcap-turbo-vk-w5h1-jfk    dups=[(21,'country')]
ntcap-medium-vk-w5h2-commit dups=[(23,'restore')]
ntcap-small-vk-w5h2-commit  dups=[(30,'the')]
ntcap-turbo-vk-w5h2-commit  dups=[(30,'the')]
```

`country country` appears in **7 of 9** jfk configs (medium ×3, small ×2, turbo ×2), matching §3.3(a).
The 6 s-cut trace in §1 also checks out — `ntcap-medium-vk-w5h2-commit` pass 2 `[0,6]` is
`"…across the north of the country."`, flushed, then pass 3 `[6,8]` `"Valley and crews expect to
restore"`, giving the committed `…north of the country valley and crews…`.

### (c) Flush dominates — **verified exactly**

```
ntcap-medium-vk-w5h1-commit   total= 68 flush= 14 (21%) rule= 54
ntcap-medium-vk-w5h2-commit   total= 69 flush= 56 (81%) rule= 13
ntcap-medium-vk-w10h2-commit  total= 64 flush= 39 (61%) rule= 25
ntcap-small-vk-w5h2-commit    total= 70 flush= 40 (57%) rule= 30
ntcap-turbo-vk-w5h2-commit    total= 65 flush= 48 (74%) rule= 17
```

Matches the report's §3.4 table row-for-row; the 81 % for medium car W5/H2 is `56/69 = 81.16 %`.

### (d) Accuracy degradation — **recomputed**

Streaming `-nt` WER vs the same human references (count-based committed text): medium car
W5/1 = 0.281, W5/2 = 0.297, W10/2 = 0.141; jfk 0.045/0.136/0.045; quiet-long 0.208/0.167/0.153. These
match §3.1 and §3.2. Batch car 0.062 is reproduced below. `0.141/0.062 = 2.27×`,
`0.297/0.062 = 4.79×` → the §1 "2.3–4.8×" is correct.

## Criterion 4 — the `-nt` finding and the merged report's headline

Reproduced with the cached binary on the car clip:

```
medium q5_0 Vulkan car clip -nt      wer=0.0625 sub=4 ins=0 del=0
medium q5_0 Vulkan car clip default  wer=0.1562 sub=8 ins=0 del=2
```

`-nt` **does** change the greedy decode (`…knocked out power to roughly 4,000…` vs `…no doubt powered
roughly 4000…`; `rivers` vs `river`). This confirms §2.3.

The already-merged provider report's harness **does** pass `-nt -np`
(`docs/project/research/whisper-cpp-provider/measure_whisper_cpp.py:242-243`) and its §2.4 states it.
So the merged report's **headline is affected**: its `medium q5_0` car-clip **0.062** is the `-nt`
decode. On the default decode the same model/clip/reference is **0.1562**, so the headline "roughly
**4.5×** fewer errors than Whistle's batch (0.281)" is `0.281/0.0625 = 4.5` with `-nt` but
`0.281/0.1562 ≈ 1.8×` without it. The merged report should be caveated: *the absolute car-clip WER
and the Whistle ratio are on whisper-cli's `-nt` decode; the default decode is ~2.5× worse on that
clip. The model ordering (medium best) is unchanged.* The merged report is not dishonest (it
documents the flag), but its headline number is decode-specific and this is not stated as such.

## Criterion 5 — falsification honesty, scope, and boundedness

- **§6 falsification is honest.** It names concrete thresholds (prefix agree ≥ ~0.95, `regressed=0`,
  no seam dup, car WER ≈ 0.062, duty < 1 with latency ≤ ~1 s at H ≤ 1, no silence hallucination
  committed), and correctly states that none of (1)(2)(3) was met by any of the `3 models × 3
  configs × 4 clips = 36` Vulkan cap combinations. The "closest" config it names (car W10/H2) is
  supported: medium W10/2 `prefix_agreement` = 0.4495, turbo = 0.92ish, car WER 0.141 vs batch
  0.062, no adjacent dup. The central claim is an inductive "at any measured config" claim, and the
  §6 structure (one counterexample config falsifies) is the right one.
- **No `src/` or `tests/` change.** `git diff main...HEAD --name-only | grep -E '^(src|tests)/'`
  → empty. All 93 changed paths are under `docs/project/research/whisper-cpp-streaming/`.
- **No committed binary/model/audio.** `--numstat` has no `-` rows and no `.wav/.bin/.so/.ggml/...`
  path is in the diff; the folder's `.gitignore` only lists `__pycache__/` and `*.pyc`. Scratch
  (sliced WAVs, per-pass JSON) lives under `~/.cache/…`, as claimed.
- **Boundedness/cleanup.** `run_matrix.sh` bounds every phase invocation with `timeout 900` and each
  `whisper-cli` with `--timeout 300`; `run_cli` uses `proc.communicate(timeout=…)`, and on expiry
  `proc.kill(); proc.communicate()`. `_pass_end_times` always terminates (cap breaks at clip end;
  slide advances by H). After all my runs, `pgrep -a whisper-cli` → none.
- **§8 reproduction.** `measure_streaming.py --verify-tokens` printed
  `verify-tokens: rebuilt=62 expected=62 MATCH=True`, matching §8.
- **Provenance note (minor).** `data/passes*/**`'s per-pass `committed_new`/`committed_total_words`
  were produced by an **older, time-watermark** rule, not the committed count-based
  `measure_streaming.py`. Evidence: committed `passes_ojf/cap-turbo-…-silence.jsonl` commits
  `["you"]` on every pass (total 1→9), while the committed harness on the same audio commits
  `["thank","you"]` at pass 1 and 6 (total 4). Raw `transcript`/`overlap_*`/`agreed`/`flushed` are
  identical and reproduce; `recompute_summary.py` rebuilds the committed text count-based and is
  byte-identical to `summary.jsonl`, so the report's numbers are reproducible from the passes —
  but re-running the committed harness does **not** reproduce the passes' bookkeeping, and §3.3(d)'s
  "(8 commits)" is that stale count.

---

## Reproduction evidence (commands)

```bash
# main is a direct ancestor, so the three-dot and two-dot diffs coincide here
git -C .worktrees/whisper-cpp-streaming merge-base main HEAD   # 6472a20…
git -C .worktrees/whisper-cpp-streaming rev-parse HEAD         # a4c9c7d…
git -C .worktrees/whisper-cpp-streaming diff main...HEAD --shortstat  # 93 files, 3337 insertions

# recompute_summary reproduces summary.jsonl byte-identically (in a /tmp copy)
PYTHONPATH=…/src python3 recompute_summary.py   # wrote … 85 rows from 85 pass files; ndiff 0

# render_tables (committed scripts) vs REPORT.md §3.1 latency cells → 6 mismatches (probe above)

# end-to-end harness reproduction (current code) vs committed passes, -nt
timeout 300 uv run python measure_streaming.py --scheme cap --no-timestamps \
  --model …/ggml-medium-q5_0.bin --model-name medium-q5_0 --backend vulkan \
  --window 5 --hop 2 --audio …/jfk.wav … --passes-out /tmp/rev_probe/repro/jfk.jsonl …
# transcripts equal: True  wordseqs equal: True
timeout 600 uv run python measure_streaming.py … --audio …/ges2t-commit-cadence-…wav …
# transcripts equal: True  wordseqs equal: True   wer 0.2969 (report 0.297)

# silence + -nt decode, and batch -nt vs default (outputs quoted above)
whisper-cli -m …/ggml-large-v3-turbo-q5_0.bin -f /tmp/rev_probe/silence10.wav -l en -t 16   # Thank you.
… -nt -np                                                                                   # you

# verify-tokens
timeout 300 uv run python measure_streaming.py --verify-tokens --model …/ggml-medium-q5_0.bin \
  --audio …/ges2t-commit-cadence-20261009-195914.wav --backend vulkan
# verify-tokens: rebuilt=62 expected=62 MATCH=True
```

No probe wrote inside the repo; `git status --porcelain` in the worktree was empty throughout and
after.

## Could-not-verify

- **The closed engine's exact "two consecutive passes agree" implementation.** `needle` is closed;
  I verified the shipped code path (`drive()` appends `committed`, `flush()` at the window boundary)
  and the *stated* rule (N=2, `needle.h`/ADR 0011), and that the harness implements that stated rule
  as an LCP. I cannot mechanically prove the closed engine's `committed` equals an LCP of two passes.
  (Reason is itself true: `needle` ships as a binary via `cactus-needle`; no source available.)
- **The full 36-config Vulkan matrix and the `slide`/CPU rows as fresh runs.** I re-ran 2 configs
  end-to-end, the silence control (both decodes), the batch `-nt`/default car clip, and
  `--verify-tokens`; the remaining rows I recomputed from the committed raw passes (which reproduce
  exactly for the 2 I re-ran) via the committed `recompute_summary.py`. The raw `transcript` fields
  themselves were not re-run for every config.
- **The original session's exact duty numbers.** My reproduction measured medium car W5/H2 duty
  **0.918** and jfk **0.933** vs the report's 0.646/0.696, i.e. the reported `< 1` margin is
  load-sensitive and thinner than stated on this session (load average ~2.9 during my runs). The
  report flags duty as an upper bound; I could not reproduce the original load.
- **Ticket-32 Whistle numbers** are taken from ADR 0011 / the ticket-32 diagnostic as cited; I did
  not re-run the Whistle engine.

## What would falsify the central claim

The claim — *on this machine, whisper.cpp partial windows are not stable enough for ges2t's two-pass
commit-cadence streaming, at any measured W/H/model on Vulkan* — is falsified by finding **one**
measured config on the real-device car clip with **all** of:

1. prefix agreement ≈ 1.0 (`prefix_agreement` ≥ ~0.95 and `regressed_passes = 0`);
2. no committed adjacent duplicate the batch/reference does not also contain;
3. committed WER ≈ the batch WER (≈ 0.062 on car, 0.0 on jfk);
4. duty < 1 and latency ≤ ~1 s at a hop a user would accept (H ≤ 1 s);
5. and no hallucination committed on the silence control.

I searched the 85 committed rows and found none; the closest (car W10/H2) fails (1)/(3). Because the
claim is inductive over configs, it can never be *proven*, only bounded by the search performed —
and the report is honest about that bound.

## Safe to relay to the user?

**The central conclusion is safe to relay: do not ship a whisper.cpp streaming path; keep
`supports_streaming = False` and take the batch path.** That recommendation is supported by my
independent recomputation and reproduction.

**The artifact should not be relayed verbatim until two doc fixes land**, because a reader would
inherit two errors:

- §3.1's `jfk`/`clip30` `latency` cells must be the timestamped-twin values (0.995/1.67/2.03 and
  1.315/2.23/2.23) or the column relabelled; and the §1 duty ranges `small 5/2`, `medium 5/2`,
  `turbo 10/2` corrected to 0.24–0.25, 0.61–0.70, 0.76–0.84;
- §3.3(d) must be labelled `-ojf` and must state that under the primary `-nt` decode medium **and**
  small also emit `you` and commit `you you`.

## Recommended follow-up tickets (not blockers)

1. **Doc fix** in `REPORT.md` §3.1 latency column, §1 duty ranges, §3.3(d) decode labelling, and the
   `-ojf`-only scope of the silence/CPU rows.
2. **Caveat the merged provider report** (`docs/project/research/whisper-cpp-provider/REPORT.md`
   §1/§2.4): note that the 0.062 headline and the 4.5× ratio are on the `-nt` decode, ~1.8× on the
   default decode.
3. **If streaming is revisited:** a resident-model integration (fixes the H=1 budget), a
   seam-dedup step, and a `-nt`-vs-default decode decision — the report already says this; keep it.
