# 003 — comment: the user's live acceptance test (ticket 34's DoD gate)

- **Ticket:** [ticket.md](ticket.md)
- **Who ran it:** the **user** — this is the ticket's user-owned step, not an agent run
- **Script:** [user-live-test.sh](user-live-test.sh) (prerequisite checks + procedure; it never opens the
  microphone, touches the clipboard, or types)
- **State tested:** `main` after the merge (`5eeca4d`), i.e. the merged code, not a branch

## Result: **all checkboxes passed**

From the script's observation form:

- [x] **text appeared WHILE speaking** (not only on release) — the whole point of the ticket
- [x] **observed lag** — the user reports **"less than a second, likely"**, and explicitly states it was
      **not measured**. Recorded as a qualitative pass against the design's central bet (commits arrive
      every ~1.00 s of source audio), **not** as a figure — do not quote it as one.
- [x] **exactly one trailing space** at the end — not one per commit
- [x] **stop → delete → continue** recovered a word the model got wrong
- [x] **nothing unexpected** (no space per commit, no duplicated word)

## Why this matters more than a green suite

This is the **only** evidence for several things no test in this project can reach:

- **Real incremental typing through `ydotoold`** into a real focused window. Every test uses the injected
  seam; no test emits a keystroke. The whole mechanism — the watermark, the reconciliation against
  `finish()`, the per-utterance trailing space — was verified offline, but "does text actually appear in
  an application while a person speaks" cannot be.
- **The lag is tolerable in practice.** The design's central bet was that commits arriving every ~1.00 s of
  source audio (ticket 32) would feel like dictation rather than lag. That bet is now confirmed by the
  person it was for, qualitatively.
- **The recovery path works under real timing** — the user decision that carries the whole risk
  (stop → delete → continue) depends on the stop being fast enough to catch a bad word as it appears, and
  it was.

## Note for whoever finishes v1

- **Incremental typing is now default behaviour** whenever streaming is on (which is the default), with
  **no opt-out that keeps streaming** — the only switch today is `streaming.enabled = false`, which also
  drops streaming. That limitation was flagged to the user at hand-over; if it becomes a problem, an
  opt-out is its own ticket.
- The **stable-hallucination** caveat stands as recorded in the ADR 0011 amendment: a confidently-wrong
  word can be committed and therefore typed mid-dictation. The user was told to expect it; none was
  reported on this run.
- **Ticket 19 (docs)** must describe this behaviour, including the clipboard-backend incompatibility and
  the `ctrl+shift+v` default.
