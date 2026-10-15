# 002-review — ticket 56 (nemo-pure-streaming-integration): the pure-streaming integration mechanism

- **Reviewer:** independent verifier (ges2t reviewer)
- **Branch / worktree:** `feat/nemo-pure-streaming-integration` @ `12a4e78` in `.worktrees/nemo-pure-streaming-integration`
- **Reviewed diff:** `git diff main...HEAD` (three-dot; `main` @ `10f28ee`)
- **Deliverables reviewed:** `docs/project/research/nemo-pure-streaming-integration/REPORT.md` (710 lines),
  `docs/project/nemotron-streaming-backend/nemo-pure-streaming-integration/001-comment.md`,
  `ticket.md` status, `docs/project/README.md` row.
- **Verdict:** **changes requested** — one blocking finding (B1: the ticket breakdown drops a file
  §7.1 itself requires). The **design conclusion is correct and safe to relay**; the report artifact
  is not yet final.

I did not write this work and changed nothing in it. My only write is this artifact. I did not edit
the report, the comment, the ticket, `src/` or `tests/`; I ran no `sudo`, no network, no microphone.

---

## 1. Summary

The report answers the user's question — *how to integrate the streaming model without two passes of a
sliding window* — and the structural answer is **correct and I reproduced every claim behind it**:

1. The 5 s window and flush cadence live **only** in `ges2t.streaming.drive` (`src/ges2t/streaming.py:95`),
   whose **only production caller** is `WhistleTranscriber.stream` (`src/ges2t/transcribers/whistle.py:665`).
   I confirmed this with `grep` over `src/` (no other caller), and confirmed `pipeline.py`,
   `streaming_session.py`, `service.py` and `socket_proto.py` never reach the window: the session
   consumes `transcriber.stream(...)` generically (`streaming_session.py:268`) and has **no**
   `window_seconds` parameter by design (`streaming_session.py:97`).
2. The recommended seam (`streaming.drive_append_only` + an `AppendOnlyDecoder` Protocol) is genuinely
   **additive**: `drive`/`StreamingDecoder` keep their signatures, and `streaming_session.py`,
   `service.py`, `socket_proto.py`, `incremental_typing.py` are untouched. The shared-code touches it
   does require (`pipeline.py`, `doctor.py`, `config.py`) are stated, not silent.
3. **ADR 0022's falsifier did not fire.** The proposed adapter consumes each interim and keeps only the
   cumulative committed *string* (the same O(utterance) text the windowed `_drive` already keeps at
   `streaming.py:160`), pushing each chunk straight to the engine — no re-decode, no second pass, no
   growing *audio* buffer. The judgment is sound.
4. The C-ABI function names exist in the installed header with the quoted contracts; the ABI is
   polled/non-blocking with **no callback**; ticket 55's harness drives exactly that loop on the calling
   thread.
5. The two string-boundary numbers reproduce **exactly** from the committed traces: `interims=1841
   end_with_space=0 start_with_space=0`, and non-empty `word_times` only on finals.

The one blocking defect is in the **ticket breakdown** (DoD 6), not the design.

---

## 2. Definition of done — item by item

| DoD | Item | Verdict | Evidence |
|---|---|---|---|
| 1 | Precise map of the current streaming path with `file:line` | **pass** | Every key symbol resolves: `StreamingPass` `streaming.py:44`, `StreamingDecoder` `:67`, `drive` `:95`, `_drive` `:151`, window `:179`, final flush `:202/206`, `_group_chunks` `:211`, `_target_samples` `:248`; `WhistleTranscriber.stream` `whistle.py:630/665`; `_WhistleStreamingDecoder` `whistle.py:387/435/456`; `CommittedTyper` `incremental_typing.py:69/97/109`; service `handle:252`, `_transition_scope:475`, `_start_partials_locked:525/541`, `_run_finish:427/447`. Line numbers verified by `grep -n`. |
| 2 | The exact seam + how the window is bypassed + concrete shape | **pass** | §3: adapter returns its own generator from `Transcriber.stream` (`interfaces.py:176`), never calling `drive`; §3.3 gives the `AppendOnlyDecoder` Protocol + `drive_append_only`; §3.4 names the new test files. No `interfaces.py` change needed (verified `Transcriber.stream` already exists). |
| 3 | C-ABI interim delivery, thread, service lock | **pass** | Header (§ below) + harness. See finding N2 for one wording overclaim. |
| 4 | Commit policy (word-boundary + pending + watermark + one space) | **pass** | §5.1's `_committed_prefix` matches the worked example on real interims; §5.2 reuses `CommittedTyper.type_committed`/`finish` unchanged. |
| 5 | ≥2 alternatives + recommendation | **pass** | §6 gives A/B/C with blast radius, test churn, Whistle risk; recommends A. |
| 6 | Proposed implementation ticket breakdown | **fail** | §8 gives 57–61 with slug/scope/files/deps/size, correctly ordered (57→58→59; 60 deps 57,59; 61 deps 60). **But `config.py` — a file §7.1 line 478 says must change — is assigned to no ticket.** See **B1**. |
| 7 | Risks, confounds, explicit what-would-falsify | **pass** | §9 lists five falsifiers and finds none fired; §9.2 states the machine/load confounds and the accuracy limits; §9.3 disclaims what it does not establish. |
| 8 | `001-comment.md` (design paragraph + breakdown + escalation) | **pass** (with B1 propagated) | The paragraph matches the report; escalation items 1–4 present; but its breakdown (comment lines 48–52) also omits `config.py`, so B1 applies to both artifacts. |
| 9 | No `src/`/`tests/` change; no engine/model/audio committed; link check 0; clean; committed; status/row `in review` | **pass** | See §8 evidence: `git diff --name-only main...HEAD -- src/ tests/` empty; no binary/model/audio in `git ls-files`; link check `0 broken references`; `git status --porcelain` empty; `ticket.md` → `in review`; README row → `in review` + branch. |

---

## 3. The seven structural probes (falsification attempts)

### Probe 1 — is `drive` reachable from anywhere but `whistle.py:665`? **Yes, only there.**

```
$ grep -rn "streaming\.drive" src/ tests/
src/ges2t/streaming_session.py:21:``stream()`` applies it by driving :func:`ges2t.streaming.drive`. ...
src/ges2t/transcribers/whistle.py:33:  ... :func:`ges2t.streaming.drive` ...
src/ges2t/transcribers/whistle.py:398:  The engine returns bare words, while :func:`ges2t.streaming.drive`
src/ges2t/transcribers/whistle.py:635:  The window/commit algorithm is :func:`ges2t.streaming.drive`: the
src/ges2t/transcribers/whistle.py:665:        return streaming.drive(
tests/test_streaming.py:45:  ... :func:`ges2t.streaming.drive` concatenates them
tests/test_pipeline_streaming.py:220: # ``streaming.drive``); ...
```

The only **call** is `whistle.py:665`; every other hit is a docstring/test comment. `import streaming`
appears only in `whistle.py:64` (`StreamingPass`); no other module imports `streaming`. The session
calls `transcriber.stream` (`streaming_session.py:268`), never `drive`.

```
$ grep -rn "window_seconds" src/ --include=*.py
streaming.py:99,113,130,137,179,145,155   # the driver
streaming_session.py:97                    # "deliberately no window_seconds parameter"
transcribers/whistle_config.py:*           # the Whistle block
transcribers/whistle.py:668                # passed into drive()
```

`pipeline.py` reads only `chunk_seconds` (the *feed* granularity) via
`_streaming_chunk_seconds` (`pipeline.py:430/440`) — not the window. **The additive-bypass premise
holds: the window is not reachable another way.**

### Probe 2 — is the recommended design genuinely additive? **Yes.**

`grep -n "def drive\b\|class StreamingDecoder" src/ges2t/streaming.py` → `:67`, `:95`, unchanged by
the design (§3.3 says "the windowed `drive()` is untouched"). §7.1's changed-file table lists no
`streaming_session.py`, `service.py`, `socket_proto.py` or `incremental_typing.py`, and §7.2 says so
explicitly. The design *does* touch `pipeline.py`, `doctor.py` and `config.py` — but it states that
openly (§7.1, §7.3.3). **No silent shared-code coupling found.** The one coupling,
`_streaming_chunk_seconds` (`pipeline.py:440`, hardcodes `WhistleConfig.from_mapping(...).config.chunk_seconds`),
is real and correctly named (probe 6).

### Probe 3 — did ADR 0022's falsifier fire? **No. But one wording needs care.**

The falsifier: *"cannot be carried into the committed-delta path without a growing buffer or a second
pass."* The design pushes each chunk straight to the engine and retains only the cumulative committed
string; `_committed_prefix` is an O(len(interim)) `rfind`, not a second decode. **No growing audio
buffer, no second pass** — the falsifier does not fire. This is consistent with the code the report
cites: `_drive` keeps `transcript` (a cumulative string, `streaming.py:160`), and
`_group_chunks` (`:211`) drains its accumulator per group, so nothing buffers all audio.

Two honest caveats the report itself supplies, which I confirm are *not* falsifier triggers:
- The retained `committed` string grows with the utterance (O(utterance) chars) — exactly as the
  windowed path's `transcript` does. The report says this plainly ("only the cumulative committed
  **string**").
- The engine keeps its own cache-aware state; §9.1(3) says "the caches that grow live inside the
  engine, not ges2t". That is a statement about the *engine*, not the ges2t carrying mechanism.

**However** the citation for the "same string the windowed path keeps" is **wrong**: the report (and
comment) write `streaming.py:167`; `transcript = ""` is at **`streaming.py:160`** (`:167` is
`language: str | None = None`). See **N1**.

### Probe 4 — C-ABI claims. **Confirmed, with one wording overclaim (N2).**

Header `~/.cache/ges2t-discovery/nemotron-3-5-asr-streaming-measurement/engine/nemo-speech-0.2.0-linux-x86_64-vulkan/include/nemo_speech/asr.h` (299 lines):

```
$ grep -in "callback\|blocking\|poll" .../asr.h
(no output; exit 1)                     # -> no callback in the ASR ABI
$ grep -n "stream_push_f32\|stream_next\|stream_finish\|streaming_recognize\|is_final\|audio_processed\|last_error" .../asr.h
217: nemo_speech_asr_streaming_recognize
224: nemo_speech_asr_stream_push_f32      # "Buffer mono float32 audio (no decode) ... next() drives decoding."
234: nemo_speech_asr_stream_finish        # "No more audio: flush the tail. The end-of-stream final is then returned by ... next."
239: nemo_speech_asr_stream_next          # "... or NULL when nothing is currently available (need more audio)."
252: nemo_speech_asr_result_is_final
253: nemo_speech_asr_result_audio_processed
290: // Thread-local last error message ... Valid until the next nemo_speech_asr_* call on the same thread.
```

All four function names exist with the quoted contracts; thread-local `last_error` is as stated. The
harness drives exactly the report's loop — `push_f32` then `_collect` polling `stream_next` until it
returns NULL (`measure_nemo_streaming.py:427/436`, `_collect` `:472-479`), then `stream_finish` + drain
(`:444/448`), all on the calling thread, no callback.

The **threading claim is supported**: the future adapter runs on `ges2t-streaming-worker`
(`streaming_session.py:148`, `_run` `:265`), which is off the service state lock
(`service.py:252`, lock held only in `_transition_scope` `:475`) and off the `ges2t-partials` thread
(`service.py:541/549`). 

**Overclaim (N2):** §4.3.3 says *"The recognizer is process-global and not re-entrant."* The header
actually documents the opposite capability at lines 131–134: *"Transparent dynamic batching across
concurrent recognition calls. A single recognizer owns one model; callers submit independent
utterances from multiple threads and compatible neural work is combined into GPU batches."* The
recognizer is *ges2t-owned, resident for the process* (a design choice, ADR 0021 shape), not an
engine-level singleton, and it is explicitly designed for concurrent calls. The report's *operational*
rule (one recording at a time, a fresh `stream` handle per recording, closed in a `finally`) is still
correct for ges2t's service, but the justification as written is inaccurate.

### Probe 5 — string-boundary evidence re-derived. **Exact match.**

```
$ python3 - <traces>            # interims only
interims=1841 end_with_space=0 start_with_space=0        # report §4.4: "0/1841" ✓
$ python3 - stream-jfk-vk.jsonl
total 63 finals 1
rows with non-empty word_times: 1 kinds: ['final']       # report §4.4: "1 of 63 ... the final" ✓
# global: {('interim', False, False): 1841, ('final', True): 23, ('final', False): 2}
```

I also confirmed interims carry no word *texts* either (the committed traces store no `words` field;
`word_times` is empty on all 1841 interims), so the string is genuinely the only boundary source
during streaming. §10.2's `analyze_streaming.py` summary reproduces **exactly** (exit 0, 25 rows =
23 speech + 2 silence; speech `append_only_char_frac = [1.0]`, `rewritten_char_pairs = [0]`,
monotone `True`, `adjacent_duplicate_words = [0]`).

### Probe 6 — ticket breakdown + the `_streaming_chunk_seconds` refactor. **Refactor real; breakdown gaps (B1).**

`pipeline.py:430-440`:
```python
def _streaming_chunk_seconds(config: Config) -> float:
    block = config.engine_blocks.get(config.transcriber.engine, {})
    return WhistleConfig.from_mapping(block).config.chunk_seconds
```
It ignores the selected engine and reads `WhistleConfig` unconditionally — **real**, exactly as
claimed. (Note for the implementer: for a `[engine.nemo_speech]` block with `device = ...`,
`WhistleConfig.from_mapping` does **not** raise — it emits an unknown-key warning and defaults
`chunk_seconds` to `1.0`, so an un-generalised path would silently run the nemo stream at the wrong
feed granularity plus a spurious warning. Since the builder registration is also in ticket 60, the
engine is not selectable until 60 lands, so 57→59→60 is safe.)

Ordering and deps are correct; every ticket has slug/scope/files/deps/size. **Gap:** `config.py`
(add `NEMO_SPEECH_ENGINE` to `KNOWN_ENGINES`) appears only in §7.1 line 478 and in **no** §8 ticket
(verified: `grep -n "config.py" REPORT.md` → only the §7.1 row). This is **B1**.

### Probe 7 — boundaries + DoD 9. **All pass.**

```
$ git diff --name-only main...HEAD -- src/ tests/
(empty)
$ git ls-files | grep -Ei '\.(gguf|wav|mp3|flac|so|dll|dylib|tar\.gz|zip|bin)$|\.onnx$|\.pt$'
(none)
$ git status --porcelain
(empty)
$ python3 <orchestrator-state §10 link check>
0 broken references
$ git diff --stat main...HEAD
 docs/project/README.md                             |   2 +-
 .../nemo-pure-streaming-integration/001-comment.md |  69 ++
 .../nemo-pure-streaming-integration/ticket.md      |   2 +-
 .../REPORT.md                                      | 710 +++++++++++++++++++++
```

`ticket.md` status `to do` → `in review`; README row → `in review … feat/nemo-pure-streaming-integration`.
Code checks (docs-only change, run anyway, bounded): `uv run mypy src/ges2t tests` → **Success: no
issues found in 65 source files**; `timeout 900 uv run pytest -q` → **1230 passed in 8.79s** (exit 0,
process exited — no `124`). Model/engine pins are real: the on-disk GGUF sha256 is
`a5c435f2…429ae` (741,548,352 B) and the archive is `c8d0ed5b…09aee6`, matching ADR 0022 and ticket 55.

---

## 4. Findings

### B1 — **blocking**: the ticket breakdown drops `config.py`

`REPORT.md` §7.1 line 478 states the engine must be added to `KNOWN_ENGINES` (`config.py`), and it is
required: `config.py:80` is `KNOWN_ENGINES = (WHISTLE_ENGINE, WHISPER_CPP_ENGINE)`, and
`transcriber.engine = "nemo_speech"` cannot be selected until the name is in that tuple
(`config.py:399`). Yet **no ticket 57–61 lists `config.py`**:

```
$ grep -n "config.py" docs/project/research/nemo-pure-streaming-integration/REPORT.md
478:| `config.py` | add it to `KNOWN_ENGINES` (`config.py:88`); **not** to `BATCH_ONLY_ENGINES` (`:96`) | ... |
```

(and 001-comment.md's breakdown omits it too). Ticket 58 claims its scope makes the engine "usable
batch-only", but as written the engine name never enters `KNOWN_ENGINES`, so even the batch path of 58
cannot be exercised. This fails DoD 6 ("the files it touches") and contradicts §7.1.
**Fix:** assign `config.py` to a ticket (57 or 58 is the natural home) in §8 *and* in
`001-comment.md`'s table; keep §7.1 and §8 consistent.

### N1 — non-blocking: a cluster of wrong `file:line` citations

The report's stated contract is "every structural claim carries a `file:line` reference". Several are
off (the referenced code is correct in every case; the line is not):

| Report says | Actual |
|---|---|
| `streaming.py:167` for the cumulative `transcript` (§1, §9.1.3; also `001-comment.md`) | **`streaming.py:160`** |
| `interfaces.py:93–94` for `display = committed + pending` | **`interfaces.py:96–98`** |
| `interfaces.py:224` for `TextTyper.type_text` | `:224` is the class; method at **`:237`** |
| `interfaces.py:243/254` for `LiveTyper.type_committed`/`finish` | class at `:243`; methods at **`:254`/`:258`** |
| `incremental_typing.py:63` for `IncrementalTypingError` | **`:59`** |
| `whisper_cpp.py:262` for `WhisperLibrary` (ADR 0021 precedent) | **`:223`** |
| `config.py:88`/`:96` for `KNOWN_ENGINES`/`BATCH_ONLY_ENGINES` | **`:80`/`:100`** |
| `doctor.py:127` for the config-validator registration | **`:1400`** (`diagnose_engine_config`) |
| header "streaming section lines 214–239" | section spans **213–241** |
| `tests/test_whistle_stream.py:433` | test def at **`:430`** |
| `tests/test_pipeline_streaming.py:236` | test def at **`:244`** |

The `streaming.py:167` error is the one that matters most, because it anchors the "no growing buffer"
claim. **Fix:** sweep and correct the citations (this also fixes the same wrong line in
`001-comment.md`).

### N2 — non-blocking: "the recognizer is process-global and not re-entrant" is not supported

See Probe 4. The header documents transparent dynamic batching for concurrent calls. **Fix:** reword
to "ges2t owns one recognizer for the process (ADR 0021 shape); the engine supports concurrent calls,
but the service state machine allows only one recording at a time, and each recording opens a fresh
stream handle." The design conclusion is unchanged.

### Non-findings (things I checked that a skeptical reviewer would worry about, and that hold)

- No test is deleted/weakened/skipped (this ticket adds no tests — rule 6 is N/A; the report's proposed
  ticket-59 tests are a future obligation, not a claim of this ticket).
- The `_partials` list in the session grows with each distinct partial (pre-existing, shared by the
  windowed path) — not a ges2t *audio* buffer and not a falsifier trigger.
- The report's appendix commands (`§10.1` header grep, `§10.2` analysis, `§10.3` trace reads) all
  reproduce; exit 0.

---

## 5. Risks and recommended follow-up tickets

- **R1 (B1).** Until `config.py` has an owning ticket, the Epic 6 integration tickets as written
  cannot select `nemo_speech`. Fix B1 before creating tickets 57–61.
- **R2 (ordering).** 59 flips `supports_streaming = True` while 60 generalises `_streaming_chunk_seconds`
  and registers the builder. The engine is inert until 60 lands, so order is safe — but 59 and 60 must
  ship together for correct feed granularity (0.16 s, not Whistle's 1.0 s default).
- **R3.** No end-to-end adapter behaviour is measured by this ticket (correctly); the string-boundary
  rule and exactly-once typing are ticket 59's test obligation. Recommend that ticket 59 pin:
  one final segment, no window flush, append-only `committed`, `pending` never typed, and a real-child
  test for the ctypes seam (rule 7).
- **R4.** Ticket 60's `_streaming_chunk_seconds` generalisation should also cover the silence-gate
  decision (§5.4) and the `window_seconds`-warning behaviour for the nemo block (ADR 0022 §Consequences).

---

## 6. Could not verify

1. **A live adapter / runtime poll loop for this ticket.** No adapter exists; the report does not claim
   one. The ABI contract was verified against the header text and ticket 55's harness source, not by a
   fresh engine run.
2. **Live timing of `stream_next` (blocking vs prompt).** The header says NULL when no result is ready
   and the harness polls; I relied on ticket 55's measured ~24–47 ms/chunk rather than timing a call
   myself.
3. **Hungarian accuracy and CPU/Vulkan parity.** Unmeasured on this machine (report §9.2, ticket 55
   §1/§6). I confirmed the report disclaims them; I did not measure them either.
4. **The engine's streaming-config geometry** (chunk/context sizes the adapter will pass to
   `streaming_recognize`). The report does not specify it and it does not affect the falsifier.
5. **A fresh run of the *engine*.** Out of scope for a reading ticket; the falsifier is settled by the
   header + harness + committed traces.

---

## 7. Safe to relay?

**The design conclusion is safe to relay to the user.** The answer to their question — the window is
adapter-local, an additive `drive_append_only` bypasses it cleanly, only the committed string is kept,
no second pass, and the ADR 0022 falsifier did not fire — is verified against the code, the C header,
and the committed traces, and I found no counterexample.

**The report/ticket-breakdown artifact should not be relayed as final until B1 is fixed** (and N1/N2
corrected): the breakdown becomes the Epic 6 tickets, and as written it cannot select the engine. The
report is honest about the one real refactor and about the accuracy limits, so nothing here needs to be
*retracted* — only completed and corrected.

---

## 8. Evidence appendix (commands + real output)

```bash
# Branch and range
$ git rev-parse HEAD
12a4e7887609669bd098ba128a2b776f13b5eafa        # 12a4e78

# Probe 1
$ grep -rn "streaming\.drive" src/ tests/        # only call: whistle.py:665 (others: docstrings)
$ grep -rn "window_seconds" src/ --include=*.py  # driver + whistle_config + whistle.py only

# Probe 4 (header, read-only)
$ grep -in "callback\|blocking\|poll" .../nemo_speech/asr.h      # (no output; exit 1)
$ wc -l .../nemo_speech/asr.h                   # 299
# header lines 224/234/239/252/253/290 quoted in §3 above

# Probe 5
$ timeout 300 env PYTHONPATH=src python3 .../analyze_streaming.py --partials .../data/partials --out /tmp/nemo56_analysis.jsonl
# exit 0; 25 rows; speech fracs [1.0]; rewritten [0]; mono [True]; dups [0]; final_extends [True]
$ python3 - .../data/partials <<'PY'            # interims=1841 end_with_space=0 start_with_space=0
$ python3 - stream-jfk-vk.jsonl                 # total 63 finals 1; non-empty word_times: 1 (final)

# DoD 9
$ git diff --name-only main...HEAD -- src/ tests/   # (empty)
$ git ls-files | grep -Ei '\.(gguf|wav|so|tar\.gz)$|...'   # (none)
$ git status --porcelain                            # (empty)
$ python3 <orchestrator-state §10 link check>       # 0 broken references
$ timeout 300 uv run mypy src/ges2t tests           # Success: no issues found in 65 source files
$ timeout 900 uv run pytest -q                      # 1230 passed in 8.79s   (exit 0)

# Pins
$ sha256sum .../models/nemotron-3.5-asr-streaming-0.6b.q8_0.gguf
a5c435f294eea8f88ce68dd27b8c3bfea7f777cb2fbba04fcd30eaa555f429ae
$ sha256sum .../nemo-speech-0.2.0-linux-x86_64-vulkan.tar.gz
c8d0ed5b9ac5fb6d5c460015ba38ea217415c44edc1e6d49120646781009aee6
```

Machine hygiene: no `sudo`, no network, no microphone/clipboard/keystroke injection, no window; only
the docs link check, `uv run mypy`, `uv run pytest` (both bounded), read-only `sha256sum`, and repo
greps were run. `git status` is clean; no `src/`/`tests/`/probe file was left behind.
