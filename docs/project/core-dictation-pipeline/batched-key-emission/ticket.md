# Batched key emission and the injection-latency measurement

- **Priority:** P1
- **Status:** in review
- **Complexity:** M
- **Epic:** ../epic.md

## Current state / context

Measured on this machine (`01`-level facts, see ADR 0003's amendment):

| Config | 124-char phrase | per char |
|---|---|---|
| `ydotool type` default | **4.99 s** | ~40 ms |
| `ydotool type --key-delay 2` | **2.76 s** | ~22 ms |
| extrapolated `--key-delay 0` | ~2.0 s | ~16–18 ms |

The cause is that **`type` sleeps twice per character** (20 ms + 20 ms defaults — the man page's
"12 ms" is stale), while **`ydotoold` itself never sleeps**. `ydotool key` accepts an **explicit
event list** (`ydotool key [-d delay] KEYCODE:PRESSED ...`), so the whole phrase can be sent in
**one** invocation. The prediction is a large speedup, but it is a **paper hypothesis that has
never been measured** — measuring it is this ticket's job.

Ticket 22 (`../xkb-keycode-mapping/ticket.md`) produces the `KeyEvent` sequence; this ticket sends it.

## Definition of done

Modify `src/ges2t/injectors/ydotool.py`; add `tests/test_ydotool_emitter.py`.

- [ ] **Scope note:** the injection contract `YdotoolInjector` exposes is changing (text → key
      sequence), so `build_injector` in `src/ges2t/pipeline.py` — created by ticket 11 — must be
      updated to construct it with a key-sequence source. That file is **in scope**, but keep the
      change minimal and read ticket 11's `001-comment.md` first; ticket 25 will rework the same
      function again for the clipboard backend, so do not restructure it beyond what this needs.
- [ ] `YdotoolInjector` stops passing text to `ydotool`. It takes a key-sequence source (ticket
      22's `XkbKeymap` or an equivalent injected callable) and emits the precomputed events.
      **`ydotool type` is never called** — that code path is deleted, not merely unused.
- [ ] Emission is **one `ydotool key -d <delay> <KEYCODE:PRESSED> ...` invocation per injection**
      where practical. Events are rendered in ticket 22's order; modifiers keep their
      press/release wrapping.
- [ ] The ADR 0012 text rules are unchanged and still tested: empty/whitespace-only input spawns
      **no** process; exactly one trailing space when enabled; the text is otherwise unmodified.
      (Note: the trailing space is now part of the text that gets *mapped*, not appended to a
      `ydotool type` payload.)
- [ ] Unreachable characters (from `KeySequence.unreachable`) are **skipped with a warning**; they
      never abort the whole injection and never emit garbage. If *every* character is unreachable,
      raise a clear error instead of silently typing nothing (ADR 0020).
- [ ] Daemon-unreachable / `ydotool`-missing / non-zero-exit errors keep their existing typed
      behaviour and actionable messages (ticket 7's contract — do not weaken it).
- [ ] A **configurable key delay** replaces the hardcoded default, exposed as
      `[injection.ydotool] key_delay_ms` (ticket 24 owns the config plumbing; consume it here).
- [ ] `tests/test_ydotool_emitter.py` uses a fake runner and asserts: the constructed argv shape
      (`('ydotool','key','-d',...)` then `KEYCODE:1`/`KEYCODE:0` tokens in the right order for a
      known input); that **text is never in argv**; the trailing-space and empty-input rules; the
      unreachable-skip and all-unreachable-error behaviour; and that one injection is one spawn.
- [ ] `uv run pytest`, `uv run mypy src/ges2t tests` (ADR 0019 covers tests) both green.
- [ ] **Build batching and its unit tests first, and do not inject anything until the measurement
      step below is separately authorised.** The code work is independent of the measurement and
      must not be blocked on it.
- [ ] **THE MEASUREMENT (the headline deliverable) — needs explicit authorisation and a bounded,
      controlled probe.** Emitting keys types into whatever window holds focus, and an earlier probe
      in this project opened terminal windows on the user's desktop unannounced, which hid their
      work. Therefore:
      - **Ask the orchestrator for authorisation before injecting.** Do not open a window and do not
        emit anything on your own initiative.
      - When authorised: use **exactly one scratch target** (e.g. a terminal capturing to a file with
        `stty -icanon min 1 time 0`, so no newline is needed to flush), **verify it holds focus
        before emitting**, keep the entire attempt inside a **hard 30-second budget**, and close the
        window afterwards (kill it if it lingers).
      - The payload must contain **no Return, no Esc, no Ctrl/Meta chords** beyond the modifiers the
        mapping itself requires. Do **not** emit a trailing Return to flush.
      - Time a **~20-word (124–127 char)** injection end to end through `YdotoolInjector`, at two or
        three delay values including the minimum that loses no characters, and report:
        - wall time and **per-character** cost at each delay;
        - whether the characters arrived **byte-perfect** (capture and compare them, with a hexdump
          — the user will also confirm what appeared);
        - **whether it fits ADR 0001's sub-1-second framework budget**, explicitly yes or no.
      - Record the raw numbers in `001-comment.md`. **Do not tune the delay to the point of dropping
        characters to make the number look good** — a dropped character is far worse than a slow
        injection; report the lowest delay that was clean.
      - **If authorisation is not given, do not inject.** Record the measurement as **pending**,
        supply a copy-pasteable script the user can run, and say plainly that the budget question is
        unanswered — **never** report it as met.
- [ ] **No spurious modifier or control key.** Unit-test that a batch built from text containing no
      newline includes **no Return/Esc**, that no modifier is pressed without a matching release,
      and that every event list is press/release balanced. (A stray leading newline `0x0a` appeared
      once during ticket 22's probe and did not reproduce. Treat an unexpected Return as a defect,
      not a curiosity: it is the one output that could *execute* something rather than type it.)

**Decision gate — read carefully.** If batching **meets** the budget, we are done with latency and
nothing else is needed. If it **does not**, do **not** silently accept it and do not invent a
workaround: report the number, and state that the remaining option is to type committed text
*during* dictation so the cost overlaps with speaking time (which would require revisiting
ADR 0011's "nothing is injected until release" rule). The orchestrator decides that, not you.

## Notes

- The daemon socket is `SOCK_DGRAM` and is flow-controlled rather than lossy, so a fast batch
  should not drop events — but **verify** rather than assume, since dropped events on a virtual
  keyboard is precisely the failure that looks like "the model misheard".
- Argv length: ~127 chars × 2–4 events × ~8 bytes ≈ a few KB, well within `ARG_MAX`; do not
  escalate to a temp file unless measurement shows a problem.
- Keep the `runner` injection seam so the 36 existing injector tests' style carries over.
- The real daemon is running on this machine, and the user has agreed to hold a focused window for
  the measurement. **Ask for nothing, inject only the measurement payloads, and never include a
  newline** (a newline into a terminal could execute a command).

## Related tickets

- depends on: ../xkb-keycode-mapping/ticket.md, ../layout-detection-and-config/ticket.md,
  ../ydotool-injector/ticket.md
- blocks: none (but see the decision gate above)
- related ADRs: ../../../adr/0003-text-injection-via-ydotool.md,
  ../../../adr/0001-python-implementation-language.md
