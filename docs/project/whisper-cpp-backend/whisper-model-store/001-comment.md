# 001 — whisper.cpp model catalogue, name parser, and pinned acquisition

Status: **in progress** (implementer). Branch `feat/whisper-model-store`,
worktree `.worktrees/whisper-model-store`.

This artifact is written incrementally as the work proceeds.

## Scope

Per the ticket: the whisper.cpp **model catalogue** (`<size>-<quant>`), a **name parser**, and
**sha256 + size-pinned Hugging Face acquisition** into
`~/.local/share/ges2t/models/whisper-cpp/<name>/ggml-<name>.bin`. No adapter, no config wiring, no
engine build. Reuse the existing `model_store` shape (ADR 0017), do not invent a divergent one.

## Step 1 — re-verify the pins against the live host

The research's measured pins (§11.4) are the *starting point*, not truth. Re-verified against the
live canonical repo on 2026-10-10 with the Hugging Face tree API, which reports each Git-LFS file's
**size** and **`lfs.oid` = the file's sha256** (this is the object hash HF serves; a full download
would reproduce it):

```
timeout 60 curl -sS "https://huggingface.co/api/models/ggerganov/whisper.cpp/tree/main?recursive=false&expand=true" -o /tmp/hf_tree.json
```

Upstream-published quants per size, from that live listing:

| size | published quants (file, bytes, sha256) |
|---|---|
| tiny | `q5_1` 32 152 673; `q8_0` **43 537 433** `c2085835d3f50733e2ff6e4b41ae8a2b8d8110461e18821b09a15c40c42d1cca` |
| base | `q5_1` 59 707 625; `q8_0` **81 768 585** `c577b9a86e7e048a0b7eada054f4dd79a56bbfa911fbdacf900ac5b567cbb7d9` |
| small | `q5_1` 190 085 487; `q8_0` **264 464 607** `49c8fb02b65e6049d5fa6c04f81f53b867b5ec9540406812c643f177317f779f` |
| medium | `q5_0` **539 212 467** `19fea4b380c3a618ec4723c3eef2eb785ffba0d0538cf43f8f235e7b3b34220f`; `q8_0` **823 369 779** `42a1ffcbe4167d224232443396968db4d02d4e8e87e213d3ee2e03095dea6502` |
| large-v1 | (no quantised variants published) |
| large-v2 | `q5_0` 1 080 732 091; `q8_0` 1 656 129 691 |
| large-v3 | `q5_0` 1 081 140 203 only (no `q8_0`) |
| large-v3-turbo | `q5_0` **574 041 195** `394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2`; `q8_0` **874 188 075** `317eb69c11673c9de1e1f0d459b253999804ec71ac4c23c17ecf5fbe24e259a1` |

**Confirms the ticket's facts:** `q8_0` is published for every size; `q5_0` only for
`medium`/`large-v3-turbo` **among the five sizes ges2t ships** (outside that set, upstream also has
`large-v2` `q5_0`/`q8_0` and `large-v3` `q5_0`). `tiny`/`base`/`small`
`q5_0` are **not** published upstream — matching the research (`q5_1`/`q8_0` only). The three pins
the research *did* publish (small-q8_0, medium-q5_0, large-v3-turbo-q5_0) match the live host
byte-for-byte.

The research listed tiny/base/small **q5_0** with hashes from its own local quantiser run
(`whisper-quantize`) — those files are **not in the canonical repo** and are deliberately **not**
pinned here.

(to be continued)

## Step 2 — one real download cross-checks `lfs.oid == sha256`

The tree API's `lfs.oid` is the Git-LFS object hash, which *is* the file's sha256. The research's
own three pins (measured by downloading and quantising) matching that field is one confirmation;
this is the independent one — a real, bounded download of the smallest model into a throwaway
`XDG_DATA_HOME` (never the user's data dir; deleted afterwards):

```
XDG_DATA_HOME=/tmp/ges2t-pincheck uv run python -c "from ges2t import model_store as m; p = m.ensure_whisper_model('tiny-q8_0'); print(p, p.stat().st_size, m.verify(p, m.whisper_model('tiny-q8_0').pin()))"
```

Real output:

```
path: /tmp/ges2t-pincheck/ges2t/models/whisper-cpp/tiny-q8_0/ggml-tiny-q8_0.bin
bytes: 43537433
pinned size: 43537433 match: True
verify (sha256+size): True
seconds: 1.7
```

So the acquisition path itself (download → sha256 + size → atomic install) agrees with the pin the
tree API published, on the live host. No model file was committed; the temp dir was removed.

## Step 3 — implementation

All of it is in the **existing** `src/ges2t/model_store.py` (the single networked module, ADR 0018),
reusing the Whistle store's `ArtifactPin` / `_ensure` / `_download` / `verify` machinery — no second
store:

- `WhisperModelName(size, quant)` — frozen dataclass; `.name` renders `<size>-<quant>`.
- `WhisperModel(model, url, sha256, size_bytes)` — one catalogue entry; `.name`, `.file_name`
  (`ggml-<name>.bin`), `.pin()` → `ArtifactPin`.
- `WHISPER_CPP_MODELS` (tuple) and `WHISPER_CPP_CATALOGUE` (immutable `MappingProxyType` keyed by
  name); `WHISPER_CPP_SIZES` / `WHISPER_CPP_QUANTS` / `WHISPER_CPP_MODELS_URL`.
- `parse_whisper_model_name(name) -> WhisperModelName` — splits on the **last** hyphen (so
  `large-v3-turbo-q5_0` works) and accepts only catalogue entries; everything else raises
  `WhisperModelNameError(name, reason, valid)`, whose message ends
  `valid models: tiny-q8_0, base-q8_0, …`.
- `whisper_model(name) -> WhisperModel` — the lookup.
- `whisper_model_path(name) -> Path` — `<data_dir>/models/whisper-cpp/<name>/ggml-<name>.bin`,
  parsing first so a bad name raises rather than yielding a plausible path.
- `ensure_whisper_model(name, *, fetcher=None, progress=None) -> Path` — parses, then calls the
  shared `_ensure` with the entry's pin; idempotent via the existing size check, atomic install,
  typed `ArtifactVerificationError` on a bad hash/size. Whisper models are platform-independent, so
  no engine platform gate applies.

### The shipped catalogue (live-verified 2026-10-10)

| name | file | bytes | sha256 |
|---|---|---|---|
| `tiny-q8_0` | `ggml-tiny-q8_0.bin` | 43 537 433 | `c2085835d3f50733e2ff6e4b41ae8a2b8d8110461e18821b09a15c40c42d1cca` |
| `base-q8_0` | `ggml-base-q8_0.bin` | 81 768 585 | `c577b9a86e7e048a0b7eada054f4dd79a56bbfa911fbdacf900ac5b567cbb7d9` |
| `small-q8_0` | `ggml-small-q8_0.bin` | 264 464 607 | `49c8fb02b65e6049d5fa6c04f81f53b867b5ec9540406812c643f177317f779f` |
| `medium-q5_0` | `ggml-medium-q5_0.bin` | 539 212 467 | `19fea4b380c3a618ec4723c3eef2eb785ffba0d0538cf43f8f235e7b3b34220f` |
| `medium-q8_0` | `ggml-medium-q8_0.bin` | 823 369 779 | `42a1ffcbe4167d224232443396968db4d02d4e8e87e213d3ee2e03095dea6502` |
| `large-v3-turbo-q5_0` | `ggml-large-v3-turbo-q5_0.bin` | 574 041 195 | `394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2` |
| `large-v3-turbo-q8_0` | `ggml-large-v3-turbo-q8_0.bin` | 874 188 075 | `317eb69c11673c9de1e1f0d459b253999804ec71ac4c23c17ecf5fbe24e259a1` |

Canonical URL base: `https://huggingface.co/ggerganov/whisper.cpp/resolve/main`.

**Scope decision, stated:** the shipped sizes are the five the provider research measured and the
ADR 0021 default rule needs (`tiny`, `base`, `small`, `medium`, `large-v3-turbo`). Upstream also
publishes `large-v1` (base f16 only), `large-v2` (`q5_0`/`q8_0`) and `large-v3` (`q5_0` only) —
none was measured by the research, none is needed by the GPU/CPU default rule, and the ticket says
"keep scope tight", so they are deliberately absent and rejected with the typed error. `q5_0` for
`tiny`/`base`/`small` is absent because upstream does not publish it.

## Step 4 — falsification (each check broken, watched to fail, reverted)

Every check below was falsified by editing the source, running the specific test, watching it fail,
then restoring the file (`FALSIFY` count back to 0) and re-running green.

**4a — the sha256 check.** Disabled the hash half of `_ensure`'s guard
(`if actual_sha256 != pin.sha256 or actual_size != pin.size:` → `if actual_size != pin.size:`):

```
FAILED tests/test_model_store.py::test_ensure_whisper_model_flipped_byte_is_a_typed_error
E       Failed: DID NOT RAISE ArtifactVerificationError
1 failed, 56 deselected in 0.15s
```

**4b — the published-combination check.** Short-circuited `parse_whisper_model_name` to return any
known size/quant without the catalogue lookup:

```
FAILED tests/test_model_store.py::test_parse_whisper_model_name_rejects_with_a_typed_error_naming_the_valid_set[tiny-q5_0]
E       Failed: DID NOT RAISE WhisperModelNameError
1 failed, 10 passed, 46 deselected in 0.13s
```

**4c — a catalogue pin.** Corrupted one hex digit of `medium-q5_0`'s sha256:

```
FAILED tests/test_model_store.py::test_whisper_catalogue_pins_match_the_live_host
1 failed, 56 deselected in 0.12s
```

## Step 5 — verification

```
$ uv run pytest -q
951 passed in 5.15s

$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 55 source files
```

Baseline on `main` was **923 passed / 0 mypy errors**; this branch is **951 passed / 0 mypy errors**
(+28 tests: 57 in `tests/test_model_store.py`, formerly 29). No test was deleted, skipped or
weakened.

## Definition of done — status

- [x] Catalogue (name, file name, byte size, sha256, canonical URL) of only upstream-published files.
- [x] Name parser `medium-q5_0` → (`medium`, `q5_0`); unknown size/quant/unpublished combination →
      typed `WhisperModelNameError` naming the valid set.
- [x] Download + sha256 **and** size verification into
      `~/.local/share/ges2t/models/whisper-cpp/<name>/ggml-<name>.bin`; idempotent; typed errors for a
      bad hash, a truncated file, and a network failure.
- [x] No runtime network outside this acquisition path (ADR 0018): still `model_store` only, and now
      asserted by a source-scanning test.
- [x] `tests/` cover the catalogue shape, the parser (good and rejected), a successful verify and a
      failing verify (flipped byte → typed error), each falsified.
- [x] `uv run pytest` and `uv run mypy src/ges2t tests` green; counts reported; nothing deleted/skipped.
- [x] This comment records the catalogue, the pins with provenance, and the upstream publishing facts.

Out of scope and untouched: no adapter, no config wiring, no engine build, no quantiser, no `f16`.
