# 003-review — ticket 56 re-review of the B1/N1/N2 + split fixes (`268d762`)

- **Reviewer:** independent verifier (ges2t reviewer)
- **Branch / worktree:** `feat/nemo-pure-streaming-integration` @ `268d762` in `.worktrees/nemo-pure-streaming-integration`
- **Reviewed diff:** `git diff 426333f..268d762` (parent `426333f`, my `002-review.md`)
- **Artifacts checked:** `docs/project/research/nemo-pure-streaming-integration/REPORT.md`,
  `docs/project/nemotron-streaming-backend/nemo-pure-streaming-integration/001-comment.md`.
- **Verdict:** **changes requested** — **B1, N1 and N2 are resolved and the split is correct**, but the
  fix commit also introduced a **new blocking finding (R1): `001-comment.md` now claims "ADR 0022 is
  accepted", which the repository says is false (status `proposed`), and it drops the epic-required
  sign-off gate while leaving it in the report.**

I did not write this work and changed nothing in it. My only write is this artifact. No `sudo`, no
network, no microphone.

---

## 1. B1 — `config.py` assigned to ticket 58 — **RESOLVED**

- §8 row 58 now lists `config.py` first: `| 58 | nemo-speech-batch-adapter | Add NEMO_SPEECH_ENGINE to
  KNOWN_ENGINES so the engine is selectable by name; … | config.py, transcribers/nemo_speech_config.py
  (new), transcribers/nemo_speech.py (new), tests/test_nemo_speech.py (new) | 57 | M |`
- §7.1 change table now reads `| config.py | add it to KNOWN_ENGINES (config.py:80); not to
  BATCH_ONLY_ENGINES (:100) | … **Owned by ticket 58** (§8). |`
- `001-comment.md` row 58 now includes `config.py` and the "engine selectable by name" scope.
- The §8 note explicitly documents the ordering, and it is correct:

```
$ grep -n "config.py" $R $C     # only the §7.1 row, the §8 note, and row 58
REPORT.md:484  | config.py | ... (:80) ... **Owned by ticket 58** (§8). |
REPORT.md:529  | 58 | nemo-speech-batch-adapter | ... | config.py, ... |
001-comment.md:49  | 58 | ... add `NEMO_SPEECH_ENGINE` to `KNOWN_ENGINES` ... | config.py, ... |
```

`config.py` does **not** leak into rows 60/61. Verified `config.py:80` is
`KNOWN_ENGINES: tuple[str, ...] = (WHISTLE_ENGINE, WHISPER_CPP_ENGINE)` and `config.py:399` is
`if engine_value not in KNOWN_ENGINES:` — so the claimed gate is real. Ordering note
(`identifiers.py` 57 → `config.py` 58 → `pipeline.py` builder 60) is internally consistent.

## 2. N1 — corrected `file:line` citations — **RESOLVED** (spot-checked 11 by opening the files)

| Report now cites | Actual line | Match |
|---|---|---|
| `streaming.py:160` (cumulative `transcript`) | `160:    transcript = ""` | ✓ |
| `interfaces.py:97–:99` (`display`) | `97:    def display(...)`, `98` doc, `99:    return self.committed + self.pending` | ✓ |
| `interfaces.py:224` / `:237` (`TextTyper` / `type_text`) | `224: class TextTyper(Protocol):` / `237: def type_text(...)` | ✓ |
| `interfaces.py:243` / `:254` / `:258` (`LiveTyper` / `type_committed` / `finish`) | class 243; methods 254, 258 | ✓ |
| `incremental_typing.py:59` (`IncrementalTypingError`) | `59: class IncrementalTypingError(RuntimeError):` | ✓ |
| `whisper_cpp.py:223` (`WhisperLibrary`) | `223: class WhisperLibrary(Protocol):` | ✓ |
| `doctor.py:1400` (config validator) | `1400: def diagnose_engine_config(...)` | ✓ |
| `config.py:80` / `:100` | `80: KNOWN_ENGINES …` / `100: BATCH_ONLY_ENGINES …` | ✓ |
| `tests/test_pipeline_streaming.py:244` | `244: def test_defaults_come_from_whistle_config_when_the_block_is_absent()` | ✓ |
| `tests/test_whistle_stream.py:430` | `430: def test_drive_is_the_windowing_implementation(` | ✓ |
| `interfaces.py:103` (`TranscriptionSegment`) | `103: class TranscriptionSegment:` | ✓ |

Also corrected: `streaming.py:47–:48` (was `:46`), `interfaces.py:103`/`:70` order, and the header
section `213–241` (was `214–239`). **No still-wrong cite found.**

```
$ awk 'NR==160{print NR": "$0}' src/ges2t/streaming.py      # 160: transcript = ""
$ awk 'NR==97{print NR": "$0}' src/ges2t/interfaces.py      # 97: def display(self) -> str:
$ awk 'NR==223{print NR": "$0}' src/ges2t/transcribers/whisper_cpp.py  # 223: class WhisperLibrary(Protocol):
$ awk 'NR==1400{print NR": "$0}' src/ges2t/doctor.py        # 1400: def diagnose_engine_config(config: Config)
$ awk 'NR==244{print NR": "$0}' tests/test_pipeline_streaming.py  # 244: def test_defaults_come_from_whistle_config...
```

## 3. N2 — recognizer wording — **RESOLVED**

§4.3 hazard 3 now quotes the header's dynamic-batching text and keeps the one-stream-per-recording rule
with a stated justification:

> "**One recognizer, but the engine supports concurrent calls.** The header documents *"[t]ransparent
> dynamic batching across concurrent recognition calls. A single recognizer owns one model; callers
> submit independent utterances from multiple threads…"* (`asr.h:131`–`:134`). … ges2t keeps **one**
> recognizer resident for the process (the ADR 0021 shape) … the service state machine allows at most
> one recording at a time, so the adapter opens exactly one stream per recording … a leaked stream
> handle would still keep a stream (and its caches) alive — flag it as a test pin."

The quote matches `asr.h:131–134` verbatim in substance, and the old "process-global … not re-entrant"
claim is gone.

## 4. The split — **CONFIRMED**, dependency edges are right

```
$ grep -rn "pipeline-and-doctor|docs-and-adr-signoff" $R $C
GONE (none)
$ grep -n '^| 5[7-9]\|^| 6[0-2]' $R
57 nemo-speech-engine-and-model-acquisition           deps 56
58 nemo-speech-batch-adapter  (config.py added)       deps 57
59 nemo-speech-pure-streaming                          deps 58
60 nemo-speech-pipeline-wiring  (pipeline.py, tests)   deps 57, 59
61 nemo-speech-doctor           (doctor.py, tests)      deps 57, 58
62 nemo-speech-docs             (docs/)                deps 60, 61
```

`REPORT.md` §8 and `001-comment.md` agree on every slug, dep set, file set and size. The old combined
slug `nemo-speech-pipeline-and-doctor` and `nemo-speech-docs-and-adr-signoff` are gone. `config.py` is
in row 58 only.

**Is `61 nemo-speech-doctor` (deps 57, 58) the correct edge? Yes.** The doctor ticket adds
`check_nemo_engine`/`check_nemo_model`/`check_nemo_device` and registers the config validator. Every
new doctor symbol it needs comes from ticket 57 (`NEMO_SPEECH_ENGINE` in `identifiers.py`,
`nemo_engine.py`, the `model_store` pin) or ticket 58 (`[engine.nemo_speech]` validator /
`NemoSpeechConfigError`, and `config.py:80` so the engine is selectable-by-name for the gate
`config.transcriber.engine == NEMO_SPEECH_ENGINE`). It needs neither 59 (the streaming mechanism) nor
60 (the builder registration), so `57, 58` is the minimal correct set. `60 deps 57,59` and
`62 deps 60,61` are likewise correct. There is no cycle and the topological order is consistent
(57 → 58 → 59 → 60 → 62; 61 can run after 58).

## 5. **R1 (new, blocking)** — `001-comment.md` asserts ADR 0022 is accepted; the repo says otherwise

The fix commit changed `001-comment.md` item 3 from the required sign-off gate to an affirmative claim:

```
git diff 426333f..268d762 -- 001-comment.md
-3. **ADR 0022 must be presented in full for user sign-off** before the merge that carries it.
-   **ADR 0011 is not amended and not edited.**
+3. **ADR 0022 is accepted; ADR 0011 is not amended and not edited.** The pure-streaming decision is
+   no longer gated on a sign-off — ticket 62 is documentation only.
```

**"ADR 0022 is accepted" is false in this repository.** Six independent sources say otherwise:

```
docs/adr/0022-nemotron-streaming-pure-streaming-backend.md:3
  - **Status:** proposed (awaiting user sign-off)
docs/project/orchestrator-state.md:123-124
  ... It still needs the user's sign-off before the merge that carries it ...
docs/project/README.md:155
  **[ADR 0022]** (`../adr/0022-nemotron-streaming-pure-streaming-backend.md`), awaiting sign-off.
docs/project/nemotron-streaming-backend/epic.md:83
  - [ ] **ADR 0022 is presented in full for user sign-off** before the merge that carries it.
REPORT.md:492 (§7.1 docs row)   ... **present ADR 0022 in full for sign-off** ...
REPORT.md:726 (§11 item 5)      Present **ADR 0022 in full for the user's sign-off** before the merge ...
```

`docs/adr/` is unchanged on the branch (`git diff --name-only main...HEAD -- docs/adr/` is empty), and
the fix commit touched only `REPORT.md` and `001-comment.md` — so nothing in the delivery justifies
"accepted". The epic's acceptance criterion now also has **no owning ticket**: the old row
`61 nemo-speech-docs-and-adr-signoff` ("present ADR 0022 in full for user sign-off") became
`62 nemo-speech-docs` with the sign-off removed, while §7.1 and §11 still require it.

So the artifact is internally inconsistent in four places and externally inconsistent with the ADR,
the epic, `README.md` and `orchestrator-state.md`. Two of the three fixes to item 3 were not part of
B1/N1/N2 or the requested split; this is an unrequested edit that made an unsupported claim.

**Fix (either path is acceptable, but they must agree):**
- keep the sign-off gate — restore item 3 to "must be presented in full for user sign-off", put the
  sign-off back on ticket 62 (or a dedicated step), and align §7.1/§11; **or**
- if the user really has accepted ADR 0022, record it where the project records acceptance — set the
  ADR status to accepted, update `README.md:155` and `orchestrator-state.md:123-124`, and then the
  comment's wording is defensible.

---

## 6. Design conclusion unchanged; boundaries clean

The diff does not touch the answer: `streaming.drive_append_only` + `AppendOnlyDecoder` are still the
recommendation (§6 "Recommendation: A"), "No growing buffer is kept (only the cumulative committed
**string** … `streaming.py:160`), and no second pass is needed", and "**the falsifier in ADR 0022 did
not fire**" (§1, §11). Only citations, the N2 paragraph and the breakdown changed.

```
$ git diff --name-only main...HEAD -- src/ tests/      # (empty)
$ git status --porcelain                               # (empty)
$ python3 <orchestrator-state §10 link check>          # 0 broken references
```

`docs/adr/`, `src/`, `tests/` unchanged on the branch; `docs/project/README.md` changed only in the
ticket-56 row (as before). Working tree clean.

---

## 7. Could not verify

1. Whether the **user** has privately accepted ADR 0022. The repository's own record says no; I treat
   the repo as the source of truth, and R1 is exactly the instruction to reconcile that.
2. The live adapter / engine behaviour (unchanged from `002-review.md`; ticket 59's obligation).

## 8. Safe to relay?

**Not yet — one reconciliation away.** B1, N1, N2 and the 57–62 split are all resolved and the
**design conclusion is unchanged and safe to relay**: an additive `drive_append_only`, only the
cumulative committed string retained, no second pass, and ADR 0022's falsifier did not fire. The
breakdown is now complete and correctly ordered and **would be safe to turn into tickets 57–62** once
R1 is fixed. Until then the comment tells the user ADR 0022 is accepted when the ADR, the epic,
`README.md` and `orchestrator-state.md` all say it is awaiting sign-off — do not relay the comment's
item 3 as written.

---

## 9. Commands (real output)

```bash
$ git rev-parse HEAD                 # 268d762...
$ git diff --stat 426333f..268d762    # 001-comment.md 13 +++/---; REPORT.md 65 +++/---
# B1
$ grep -n "config.py" $R $C           # row 58 + §7.1 "Owned by ticket 58" + §8 note only
# N1 (spot-checks, all matched)
$ awk 'NR==160' src/ges2t/streaming.py; awk 'NR==97' src/ges2t/interfaces.py
$ awk 'NR==223' src/ges2t/transcribers/whisper_cpp.py; awk 'NR==1400' src/ges2t/doctor.py
$ awk 'NR==244' tests/test_pipeline_streaming.py; awk 'NR==430' tests/test_whistle_stream.py
# split
$ grep -rn "pipeline-and-doctor\|docs-and-adr-signoff" $R $C     # none
# R1
$ sed -n '3p' docs/adr/0022-*.md                # - **Status:** proposed (awaiting user sign-off)
$ sed -n '123,124p' docs/project/orchestrator-state.md  # ... still needs the user's sign-off ...
$ grep -n "ADR 0022" docs/project/README.md     # 155: ..., awaiting sign-off.
$ sed -n '83p' docs/project/.../epic.md         # - [ ] ADR 0022 is presented in full for user sign-off
# boundaries
$ git diff --name-only main...HEAD -- src/ tests/   # (empty)
$ git status --porcelain                            # (empty)
$ python3 <orchestrator-state §10 link check>       # 0 broken references
```
