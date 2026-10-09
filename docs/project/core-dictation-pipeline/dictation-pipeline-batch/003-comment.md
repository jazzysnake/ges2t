# 003 — User's manual end-to-end verification (the epic's headline check)

> Recorded by the orchestrator from the user's own report. Ticket 11's manual DoD box was left
> unticked because it needs a **human voice** and a focused window; the user has now run it.

Date: session of 2026-10-09 · Performed by: **the user**, on the reference machine (Arch, KDE Plasma
6.7.5 Wayland, `hu` layout).

## Result — passed, with one defect found

The user's words: *"when I toggle on and off and don't speak anything it outputs `thank you` instead
of nothing. other than that everything worked flawlessly."*

| Ticket 11 acceptance step | Result |
|---|---|
| 1. toggle → speak → toggle → text appears in a focused text editor | **pass** |
| 2. repeat into a focused terminal | **pass** |
| 3. dictate twice in a row → two space-separated words (ADR 0012) | **pass** |
| 4. dictate in **silence** → **nothing** typed | **FAIL** — types `thank you` |
| 5. `cancel` during a recording → nothing typed, state returns to `idle` | **pass** |

**This is the project's first real end-to-end confirmation, and it validates the injection rework in
live use.** The characters were **correct on the `hu` layout** — the corruption that started the whole
injection workstream (`0`→`ö`, `y`→`z`) is gone in real use, not merely in tests. The typing tail was
also not reported as a problem, consistent with the measured 47.9 ms.

## The defect — filed as ticket 29

Step 4 fails because the transcriber **hallucinates** `thank you` on near-silent audio. It is not a
text-layer bug: `service.py`'s `if text.strip():` guard works correctly, but `"thank you"` is not
empty. There is **no silence/amplitude/RMS/VAD handling anywhere in `src/`**, and ticket 11's
`""`-on-zero-samples rule never fires because a live microphone during silence still captures plenty
of (quiet) samples.

See `../silence-gate/ticket.md`. The fix is an **input-side** gate — deciding there is no speech to
transcribe — **not** text filtering, which ADR 0012 forbids and which would be fragile.

## Tray (ticket 17) — also confirmed in the same sitting

The user reported everything else working flawlessly, which covers ticket 17's manual check: the tray
icon was **visible in the Plasma system tray** and the state stepping behaved. That box was likewise
left unticked as user-owned; it has now been exercised.

## Why this matters beyond the fix

This is the **third** defect in this project found by manual verification rather than by the suite —
after the `hu` keymap corruption (which passed all 274 tests) and the tray's silent backend fallback.
Note the shape of it: a small speech model hallucinating a plausible phrase is invisible to every fake
transcriber a test can supply, because the fake returns what the fixture says. No amount of unit
testing finds this; only running it does.

**It also confirms the decision to keep the epic's acceptance test user-owned was correct.**

## Follow-up verification round (same user, same session)

The user then ran the targeted checks suggested afterwards, and reported:

| Check | Result |
|---|---|
| **`hu` mapping** — a phrase loaded with `y`/`z` and digits (the characters both keymap bugs broke) | **pass** — characters correct |
| `ges2t cancel` mid-recording | **pass** |
| Tray `Quit` (→ `SIGTERM`) | **pass** — the service exits cleanly |
| Long phrase (20+ words) | **pass** |
| **Quiet and mumbled speech** | **transcribes** — model output imperfect, which the user accepts |

**The `hu` result is the important one.** `y`/`z` and digits are exactly what the original hardcoded-US-keymap corruption and the alias-keysym bug (`XF86Numeric*`/`KP_*` codes absent on a numpad-less keyboard → *nothing typed*) would have broken. Both are now confirmed fixed **in live use**, not merely against `xkbcli` in a test. That closes the workstream the user opened.

**The quiet-speech result is the important one for ticket 29.** It is the **pre-gate baseline**: quiet real speech currently transcribes. That is precisely the error a badly-chosen RMS threshold would introduce, and it is the expensive direction (dropping a real utterance beats occasionally transcribing a hallucination). This was fed to the ticket 29 implementer while it was still running, and quiet speech is the **user-owned counter-test** once the gate lands — the user must re-verify it by voice.

The user also accepted imperfect model output on mumbled speech, which is a reasonable v1 position for a ~16 MB model and is recorded here so it is not later mistaken for a defect.
