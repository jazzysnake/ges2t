# 002 — review: committed-delta-interface (ticket 33)

- **Ticket:** [committed-delta-interface](ticket.md)
- **Branch:** `feat/committed-delta-interface` (HEAD `34da4f4`, merge-base with `main` `fe4f977`, 26 behind)
- **Reviewer:** independent review pass, 2026-10-09
- **Verdict:** **PASS — safe to merge**, with one board-resolution caveat in §7 and one
  must-know for ticket 34 in §3. No source/test changes requested.

The central claim — "no observable behaviour change" — is **confirmed mechanically**, not
asserted: a scripted decoder run against the pre-change tree and HEAD produces byte-identical
display strings, session partial sequences, languages, `is_final` flags, per-pass/flush counts,
and final text. The two new invariants (cumulative/append-only `committed`; delta-vs-cumulative
separation) hold, and every new test I attacked was falsified by its mutation.

---

## 1. No-behaviour-change claim — pre/post mechanical comparison

Pre-change code was copied out with `git archive main` (no checkout of `main` in the worktree),
and an identical probe script drove both trees with the **same** scripted decoder:

```bash
git archive main | tar -x -C /tmp/ges2t-main
PYTHONPATH=/tmp/ges2t-main/src uv run python /tmp/probe.py > /tmp/probe_pre.json   # PRE_EXIT=0
uv run python /tmp/probe.py                          > /tmp/probe_post.json  # POST_EXIT=0
```

The probe runs a 7-process / 3-flush script (two of whose consecutive passes advance
`committed` while leaving the display unchanged), through both `drive()` (display strings,
`on_partial`) and `StreamingDictationSession` (the `status_ui.partial(...)` sequence). Comparing
only the fields that exist pre-change:

```bash
diff /tmp/obs_pre.json /tmp/obs_post.json
# (no output)
```

**Byte-identical: `drive_displays`, `drive_languages`, `drive_is_final`,
`drive_process_calls`, `drive_flush_calls`, `session_partial_displays`, `session_final_text`,
`session_process_calls`, `session_flush_calls`, trap-script displays/final.**

Emitted display strings (identical pre and post):

```
'the quick brown'
'the quick brown'
'the quick brown fox jumps '
'the quick brown fox jumps over the lazy dog'
'the quick brown fox jumps over the lazy dog'
'the quick brown fox jumps over the lazy dog. '
'the quick brown fox jumps over the lazy dog. done'   <- non-final flush boundary
'the quick brown fox jumps over the lazy dog. done'   <- final
```

`status_ui.partial(...)` display sequence after the session's (pre-existing) dedup:

```
'the quick brown'
'the quick brown fox jumps '
'the quick brown fox jumps over the lazy dog'
'the quick brown fox jumps over the lazy dog. '
'the quick brown fox jumps over the lazy dog. done'
final_text = 'the quick brown fox jumps over the lazy dog. done'
```

`process_calls=7`, `flush_calls=3`, all `language="en"`, final `is_final=True` — identical. The
only JSON fields that differ are the *new* `live.committed` / `live.pending` fields (absent, hence
`null`, on the pre-change tree). This is the strongest available evidence for the claim: the
display path is provably unchanged, not merely "tests pass".

## 2. Delta vs cumulative — verified over a multi-pass, multi-flush run

`StreamingPass.committed` stays a per-pass delta (unchanged; ADR 0011). The value on a segment is
the cumulative accumulator. From the post-change probe (`/tmp/probe_post.json`):

```
drive committed:
  'the '
  'the quick '
  'the quick brown fox jumps '
  'the quick brown fox jumps over '
  'the quick brown fox jumps over the '
  'the quick brown fox jumps over the lazy dog. '
  'the quick brown fox jumps over the lazy dog. done'   <- non-final, flush boundary
  'the quick brown fox jumps over the lazy dog. done'   <- final
```

Mechanical checks (`/tmp/verify_props.py`):

- each value `startswith` the previous (append-only prefix chain) — **True** for both `drive()` and
  the session sequence;
- final `committed == final text == whole transcript` — **True**;
- final `pending == ""` — **True**;
- the concatenation of the per-step committed diffs (`committed[i][len(committed[i-1]):]`) equals
  the final transcript — **True** (no duplication, no loss).

The session sequence is the same property under dedup:
`['the ', 'the quick brown fox jumps ', 'the quick brown fox jumps over ',
'the quick brown fox jumps over the lazy dog. ', 'the quick brown fox jumps over the lazy dog. done']`
— every value a prefix of the next.

## 3. The dedup subtlety — answer for ticket 34

**The claim is true, but only conditionally, and the condition matters.**

`partials()` dedups on `committed + pending` (the pre-existing rule, unchanged). When a pass
advances `committed` by exactly what `pending` shrinks, the display is unchanged and the session
drops the segment. I constructed exactly that (trap script: pass 1 `committed="abc",
pending="def"`; pass 2 `committed="abcdef", pending=""`; final `"abcdef"`):

```
partials() committed observed: ['abc']
finish() final text:            'abcdef'
partials()+final typing:        'abcdef'  -> exactly-once True, watermark == final True
partials()-only   typing:       'abc'     -> lost tail 'def'
```

So the cumulative value **does** catch up: because `committed` is append-only, the dropped `"def"`
reappears inside the next advancing partial's `committed` (demonstrated on the main 7-pass script,
where the two collapsed advances are recovered at the *next* advancing partial), or — if the
advance is the last one before the end — in the final segment's `committed`.

**Caveat / must-know for ticket 34:** `partials()` does **not** yield the final segment. The final
committed value is reachable only through `finish()`, which returns a plain `str`. The
exactly-once guarantee therefore holds for a consumer that diffs each `partials()` `committed`
**and then reconciles against `finish()`'s final text** (as 001-comment §7 says ticket 34 should).
A consumer that iterates `partials()` alone and never reads the final text **loses the last
collapsed advance** (the `'def'` above). This is not a defect in ticket 33 — no injection happens
here — but ticket 34 must consume the final text as the catch-up, and the service's existing
one-shot final injection must become a diff against the typed watermark rather than a re-type of
the whole transcript.

## 4. Falsification of the new tests (independent reproduction)

Harness `/tmp/mutate.py`: apply one exact string replacement, run only the target tests with a
120 s timeout, then `git checkout -- <file>` in a `finally`; restored bytes compared and
`git status`/`git diff` verified empty afterwards. All mutations were **CAUGHT**; the exact failing
nodes match the implementer's matrix.

| # | mutation | targets | result |
|---|---|---|---|
| M1 | `drive` exposes `committed=pass_.committed` (per-pass delta) | (a)(b)(c)(d) | 4 failed — all four |
| M2 | `committed=transcript + pending, pending=pending` | (a)(b)(c)(d) | 2 failed: **(c)(d)**; (a)(b) pass (as 001 says) |
| M2b | `committed=transcript + pending, pending=""` | (a)(b)(c)(d) | 2 failed: **(a)(d)** |
| M3 | final `committed=""` | (b) | 1 failed |
| M4 | `TranscriptionSegment.text` returns `live.committed` | (c), tray | 1 failed: **(c)**; tray passes (it uses `live.display`) |
| M5 | `LiveText.display` returns `committed` | live, tray, (c) | 3 failed — all |
| M9 | tray stores `live.committed` | tray | 1 failed |

Restoration: `git status after: ''`, `git diff --stat after: ''`.

I also confirmed the tests are not vacuous against a real type break: replacing one call site with
`ui.partial("display-only")` makes `mypy` fail
(`tests/test_status_ui.py:41: error: Argument 1 to "partial" of "NullStatusUI" has incompatible
type "str"; expected "LiveText"`), proving mypy genuinely checks the changed surface.

## 5. Regression surface — "what did this change stop doing?"

- **Every call site and implementation updated.** `grep -rn "def partial" src tests` shows only
  `live: LiveText` signatures (interfaces, tray, notify, ui/status, fakes, and the test doubles);
  `partials()` is `Iterator[LiveText]` in service, pipeline, streaming_session, and the fakes.
  `mypy` checks 49 files (`src=24`, `tests=25`) with 0 errors, so a missed site would have failed.
- **Notification text unchanged.** `git diff main...HEAD -- src/ges2t/notify.py` changes only the
  `partial` parameter and its forwarding; the `error` notification body/summary/urgency are
  untouched. `test_notify.py` passes.
- **Tray tooltip unchanged.** `self._partial = live.display`; `_tooltip()` is untouched. The new
  test pins the exact string `"ges2t: recording\nthe quick brown fox\n"`-style output
  (`"ges2t: recording\nthe quick brown fox"`). Tooltip bytes are identical.
- **No injection path added.** `grep -rn committed src/ges2t` finds no injector reference; the
  service forwards `live` only to `status_ui.partial()`, and `inject()` is still called exactly
  once after `session.finish()`. `on_partial` has no production callers (only `streaming.py`
  itself and tests), so ticket 33 adds no typing path.
- **Other consumers of `TranscriptionSegment`.** `WhistleTranscriber.stream()` only
  `return streaming.drive(...)`; no production code constructs, serialises
  (`asdict`/`astuple`/`replace`), assigns `.text`, or compares segments for equality. The only
  segment-equality use is in `tests/test_interfaces.py`, updated. `ui/status.py`'s new
  `interfaces` import is stdlib-only and creates no cycle.

## 6. Mechanicals

```
timeout 300 uv run pytest -q        -> EXIT=0, 776 passed in 6.18s   (no exit-124 hang)
rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
                                    -> EXIT=0, "Success: no issues found in 49 source files"
```

`git diff main...HEAD -- tests/`: **no test removed** (no deleted `def test_`), **no skip/xfail
added**, and every removed assertion reappears in an equivalent `[live.display for live in ...]`
form. The only edits are type/signature plumbing plus the genuinely new tests (a)–(d),
`LiveText` unit tests, the tray byte-for-byte test, and the two session split/dedup tests.
`git diff HEAD -- src tests` is empty and `git status --porcelain` is clean at finish.

## 7. Merge readiness

`git merge-tree --write-tree --name-only main HEAD` reports exactly one conflicted path:
`docs/project/README.md` (it auto-merges `pipeline.py` and `test_pipeline.py`). A scratch clone
merge confirms the same.

**Caveat the orchestrator must know:** the README conflict hunk is **not** just row 33 — it spans
**rows 32 and 33** because main changed the adjacent row 32 and the branch changed row 33:

```
<<<<<<< HEAD (main)
| 32 | commit-cadence-diagnostic ... | S | done | ... | feat/commit-cadence-diagnostic (merged 1356f88) |
| 33 | committed-delta-interface ...  | S | in progress | 32 | feat/committed-delta-interface |
=======
| 32 | commit-cadence-diagnostic ... | S | to do | ... | — |
| 33 | committed-delta-interface ...  | S | in review | 32 | feat/committed-delta-interface |
>>>>>>> feat/committed-delta-interface
```

The branch side carries a **stale row 32** (the pre-ticket-32 row). It is not a change the branch
made — `git diff $(git merge-base main HEAD) HEAD -- docs/project/README.md` shows the branch
touched **only its own row 33** — but a wholesale "take the branch side" resolution would corrupt
the board by reverting row 32 to `to do`/old text. Correct resolution: keep **main's row 32** and
the **branch's row 33**. No other ticket's row is modified by this branch, and main's other board
edits (row 25, row 35, dispatch wave 18) auto-merge. With that row-wise resolution the branch is
safe to merge.

## Per-DoD checklist

- [x] `TranscriptionSegment` carries committed separately via the named `LiveText` value object
      (ADR 0019; stdlib-only in `interfaces.py`, ADR 0010).
- [x] `drive()` populates it and is precise about delta vs cumulative (§2).
- [x] Session and service expose the committed value with no observable display change (§1, §5);
      tray tooltip bytes unchanged.
- [x] No injection change; no typing path added (§5).
- [x] Tests pin (a) append-only committed, (b) final == concatenation of committed parts,
      (c) display byte-for-byte unchanged, (d) pending-only change does not advance committed —
      all four present and each independently falsified (§4).
- [x] `uv run pytest` and `uv run mypy src/ges2t tests` green (§6).

## Could not verify

- **Real microphone / end-to-end dictation.** Hard rule: the microphone was not recorded and no
  keystroke or clipboard action was taken. Evidence is the deterministic probe plus the suite.
- **Real tray rendering.** No display in this environment; the exact tooltip string handed to the
  icon is verified, not OS rendering (`pystray`/AppIndicator not exercised).
- **Real Whistle engine committed/pending cadence.** The adapter seam is unchanged and ticket 32
  already measured it; this review did not re-measure live engine values.
- **Service-level threaded forwarding compared pre/post.** The service forwarder is a one-line
  pass-through (`status_ui.partial(live)`); the deterministic session-level sequence plus code
  inspection and the existing state-machine tests stand in for a threaded byte comparison.

## Bottom line

No blockers. The refactor is genuinely behaviour-preserving on the display path, the
delta-vs-cumulative trap is handled, and the dedup interaction is safe **provided ticket 34
consumes the `finish()` final text as the watermark catch-up** (`partials()` alone never yields
the final segment). Safe to merge once the README conflict is resolved row-wise, keeping main's
row 32 and this branch's row 33.
