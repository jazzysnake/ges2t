# Addendum — the batch headline is `-nt`-specific

- **Author:** orchestrator (not a discovery agent).
- **Date:** 2026-10-10.
- **Basis:** a finding from the whisper.cpp **streaming** discovery
  ([`../whisper-cpp-streaming/REPORT.md`](../whisper-cpp-streaming/REPORT.md)), independently
  reproduced by that artifact's review ([`../whisper-cpp-streaming/001-review.md`](../whisper-cpp-streaming/001-review.md)),
  and re-checked directly by the orchestrator.

This addendum changes **no number** in [`REPORT.md`](REPORT.md). It qualifies **which decode** the
headline number came from, because `whisper-cli -nt` is **not** output-only — it changes the decode.

## What was found

§2.4 records the invocation, which includes **`-nt`** (`--no-timestamps`). On the car clip,
`medium q5_0` (Vulkan, greedy, word_diff via ticket-32's scorer):

| flags | transcript (excerpt) | word_diff WER |
|---|---|---|
| `-nt` (the headline) | "…storm **knocked out power** to roughly 4,000 homes across the **Northern Valley**…" | **0.0625** (4s/0i/0d) |
| default (timestamps on) | "…storm **no doubt powered** roughly 4,000 homes across the **northern valley**…" | **0.1562** (8s/0i/2d) |

The default-flag transcript is materially worse.

## What this means for the headline

§1/§14 state "`medium q5_0` … 0.062 against Whistle's 0.281 (batch) / 0.391 (streaming)" — i.e.
**~4.5×**. That holds **when the provider is run with `-nt`**, which is a reasonable choice for ges2t
(timestamps are irrelevant to typing text). With `whisper-cli`'s **default** flags the comparison is
**0.156 vs 0.281 — ~1.8×**, still a clear win.

So the honest statement is: **whisper.cpp beats Whistle on accuracy either way; the size of the win
(≈4.5× vs ≈1.8×) depends on passing `-nt`.** Any `whisper_cpp` adapter must **pin that flag
deliberately** rather than inherit the CLI default.

## Limits

One recording, one length, read speech; absolute WER is an upper bound. Whether `-nt` helps
generally, or only on this clip, was **not** measured — this is a single-clip observation, exactly
like the batch numbers it qualifies.
