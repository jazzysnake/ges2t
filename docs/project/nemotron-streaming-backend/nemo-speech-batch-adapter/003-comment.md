# 003 — implementer comment (ticket 58, addressing review F1/F2)

Branch `feat/nemo-speech-batch-adapter`, worktree `.worktrees/nemo-speech-batch-adapter`, base
`938dc57`. Fresh implementer resolving the two findings in `002-review.md`; the implementation under
review is verified and **extended**, never rewritten. Only `tests/test_nemo_speech.py` changes.

## What I am doing

* **F1 (blocking).** The forwarded float32 payload was unpinned: `buffer.samples` → `[]` survived the
  whole fake suite *and* the opt-in real-seam decode (the real clip was silence, expected `""`).
  Fix both halves:
  * hermetic — assert the **actual** recorded payload (values, length, non-empty), not just that
    `recognize` was called;
  * real seam — mirror whisper.cpp's test: use a **speech** clip and assert a **word** appears.
* **F2 (non-blocking).** `test_real_library_rejects_a_missing_model`'s comment claimed the library's
  own `last_error` is surfaced while only a static prefix was asserted. Assert the surfaced
  `last_error` text instead.

## Real-engine probe (before editing, read-only)

Drove the already-downloaded scratch engine+model (CPU, no download, no `sudo`, no network) on the
whisper.cpp reference clips to pick a stable word:

```
--- jfk.wav (176000 samples) ---
'and so my fellow americans ask not what your country can do for you ask what you can do for your country'
--- clip5.wav (80000 samples) ---
'he hoped there would be stew for dinner turnips and carrots and bruised potatoes'
--- cny16.wav (166960 samples) ---
'he hoped there would be stew for dinner turnips and carrots and bruised potatoes and fat mutton pieces to be ladled out in thick peppered flour fattened sauce'
--- four.wav (105216 samples) ---
'y en las ramas medios sumergidas revoloteaban algunos pájaros de imérico y legendario plumaje'
```

`clip5.wav` (the clip whisper.cpp's `test_real_libwhisper_transcribes_cached_audio` uses) yields the
same reference word **"dinner"**, so the real-seam test can mirror that pattern.

`last_error()` on a failed create (for F2):

```
MESSAGE: "nemo_speech_asr_create failed with status 3 for the model at /nonexistent/model.gguf: Failed to load GGML file: /nonexistent/model.gguf (run 'ges2t setup nemo_speech' to acquire the pinned model)"
last_error after: 'Failed to load GGML file: /nonexistent/model.gguf'
```

So F2's assertion reads the library's own `last_error()` back and requires it to appear in the raised
message — no hardcoded library wording, and it fails if `{self.last_error()}` is dropped.

<!-- appended incrementally as work proceeds -->

## Changes (only `tests/test_nemo_speech.py`; `git diff --stat` = 52 insertions, 8 deletions)

* **F1 hermetic** — new `test_transcribe_forwards_the_actual_audio_payload`: transcribes a distinctive
  six-sample buffer and asserts `fake.recognized[0][1] == tuple(samples)`, the forwarded length equals
  the buffer length, and the payload is non-empty. This reads the samples the fake already recorded
  (the fake was not changed to record them -- it already did; nothing ever read them).
* **F1 real seam** — `_REAL_AUDIO` now defaults to the **speech** clip
  `~/.cache/ges2t-discovery/whisper-cpp-provider/audio/clip5.wav` (same clip and reference word
  whisper.cpp's test uses); `test_real_library_recognizes_cached_audio` asserts the reference word
  `_REAL_SPEECH_WORD = "dinner"` instead of `result.text == ""`.
* **F2** — `test_real_library_rejects_a_missing_model` now reads the library's own `last_error()` back
  and requires it to appear in the raised message (`assert surfaced in message`), replacing the
  comment that claimed more than the static-prefix assertion proved.

## Falsification — each mutation applied, watched to fail, reverted

### F1 hermetic: `transcribe` forwards `[]` instead of `buffer.samples`

```
$ perl -0pi -e 's/handle, buffer\.samples, sample_rate=buffer\.sample_rate/handle, [], sample_rate=buffer.sample_rate/' src/ges2t/transcribers/nemo_speech.py
$ uv run pytest tests/test_nemo_speech.py::test_transcribe_forwards_the_actual_audio_payload -q
>       assert forwarded == tuple(samples)
E       assert () == (0.0, 0.25, -..., -1.0, 0.125)
E         Right contains 6 more items, first extra item: 0.0
1 failed in 0.24s

$ uv run pytest tests/test_nemo_speech.py -q
1 failed, 46 passed, 2 skipped in 0.23s        # was green before the fix
```

### F1 real seam: same `[]` mutation, real decode

```
$ GES2T_NEMO_REAL_SEAM=1 uv run pytest tests/test_nemo_speech.py::test_real_library_recognizes_cached_audio -q
>       assert text.strip(), "the real library produced no text on real speech"
E       AssertionError: the real library produced no text on real speech
E       assert ''
1 failed in 0.37s                              # previously "1 passed in 0.15s" for silence
```

### F2: drop `{self.last_error()}` from the create failure message

```
$ perl -0pi -e 's/: \{self\.last_error\(\)\} \(run/: (run/' src/ges2t/transcribers/nemo_speech.py
$ GES2T_NEMO_REAL_SEAM=1 uv run pytest tests/test_nemo_speech.py::test_real_library_rejects_a_missing_model -q
E       AssertionError: the library's own last_error 'Failed to load GGML file: /nonexistent/model.gguf' was not surfaced in "nemo_speech_asr_create failed with status 3 for the model at /nonexistent/model.gguf: (run 'ges2t setup nemo_speech' to acquire the pinned model)"
1 failed in 0.18s
```

### Reverted — `src/` byte-identical

After each probe: `git checkout -- src/ges2t/transcribers/nemo_speech.py`.

```
$ cmp /tmp/nemo_speech.py.bak src/ges2t/transcribers/nemo_speech.py  -> identical
$ sha256sum src/ges2t/transcribers/nemo_speech.py /tmp/nemo_speech.py.bak
319582d66a45ffe60a8444f6f8b8d669374ba05da71e48bbd3cfc51362231e83  src/ges2t/transcribers/nemo_speech.py
319582d66a45ffe60a8444f6f8b8d669374ba05da71e48bbd3cfc51362231e83  /tmp/nemo_speech.py.bak
$ git diff --stat
 tests/test_nemo_speech.py | 60 ++++++++++++++++++++++++++++++++++++++++-------
```

## Verify (real output, run in the worktree, after the `src/` reverts)

```
$ uv run pytest -q
1341 passed, 2 skipped in 10.39s

$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 70 source files

$ python3 - <<'PY'   # the §10 docs link check
0 broken references

$ git status --short
 M tests/test_nemo_speech.py
?? docs/project/nemotron-streaming-backend/nemo-speech-batch-adapter/003-comment.md
```

Suite count moved 1340 → 1341 (one new hermetic test). Opt-in real seam: `2 passed in 1.58s` under
`GES2T_NEMO_REAL_SEAM=1`. Plain `pytest` skips both heavy real-seam tests (2 skipped). No `src/`
change, no new dependency, no `numpy`/`torch`, no `sudo`, no download.
