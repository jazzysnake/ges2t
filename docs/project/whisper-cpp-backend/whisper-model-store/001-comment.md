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
