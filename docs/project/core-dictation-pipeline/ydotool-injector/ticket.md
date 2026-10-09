# Ydotool injector

- **Priority:** P0
- **Status:** in review
- **Complexity:** S
- **Epic:** ../epic.md

## Current state / context

Nothing types text yet. ADR 0003 chose `ydotool` (a virtual keyboard via `/dev/uinput`) as the
only v1 injection mechanism, with clipboard-paste explicitly **not** built and **not** an
automatic fallback.

Environment facts on the reference machine: `ydotool` and `ydotoold` are installed at
`/usr/bin/`. The `ydotoold` daemon is currently **inactive and disabled**, and `/dev/uinput`
is `crw-rw----+ root:input`. So in practice the first run of this code will fail with
"daemon not running" until the user enables it — which is exactly the error this ticket must
make actionable.

This ticket also owns the **trailing-space rule**, which is the single exception to verbatim
output (ADR 0012).

Relevant decisions: ADR 0003 (mechanism, no fallback, latency matters), ADR 0012 (verbatim
plus one trailing space; empty text is never injected), ADR 0014 (this is a primary unit-test
target).

## Definition of done

- [ ] `src/ges2t/injectors/__init__.py` exists (may be empty).
- [ ] `src/ges2t/injectors/ydotool.py` defines `class YdotoolInjector` satisfying
      `Injector`:
      - `__init__(self, trailing_space: bool = True, runner=None)` — `runner` is an
        injectable callable so tests never execute `ydotool`;
      - `inject(text: str) -> None` writes the text through the daemon and returns when it
        has been delivered.
- [ ] **Text assembly (ADR 0012), implemented in exactly one place and unit-tested
      directly:**
      - `text` empty or whitespace-only → **no subprocess is spawned at all** (assert the
        runner was not called);
      - non-empty and `trailing_space=True` → the injected payload is
        `text + " "`, exactly one space;
      - non-empty and `trailing_space=False` → the payload is `text` unchanged;
      - the text itself is otherwise **never modified** — no strip, no case change, no
        whitespace collapsing inside the string.
- [ ] Typing is done in a way that does not blow the framework budget or drop characters: use
      `ydotool type --file -` (or the verified equivalent) and pass the payload on **stdin**,
      not as a huge argv. Batch as few daemon round-trips as practical; do not type
      character-by-character from Python. Verify the exact flag against `ydotool --help` on
      this machine and record the verified invocation in `001-comment.md`.
- [ ] Failures raise clear, actionable errors rather than silently doing nothing:
      - `ydotool` missing from `PATH`;
      - daemon unreachable/refused (the current state of this machine!) — the message must
        say to start and enable `ydotoold`, and must mention the `/dev/uinput` permission
        requirement;
      - non-zero exit — include stderr;
      - a non-zero exit **after** partial typing must be reported as a failure, not swallowed.
- [ ] `YDOTOOL_SOCKET` is honoured if set (pass it through to the subprocess environment);
      the default daemon socket is used otherwise.
- [ ] `tests/test_ydotool_injector.py` covers: every branch of the trailing-space table
      above; that the payload reaches the runner via stdin and not argv; each error branch
      with its message content; and that a whitespace-only string performs no call.
- [ ] `uv run pytest tests/test_ydotool_injector.py` passes and `uv run pytest` stays green.
- [ ] **Manual, once** (this is the risky one), reported in `001-comment.md`: after enabling
      `ydotoold` and granting `/dev/uinput` access, inject a 20-word phrase into a focused
      text editor and into a focused terminal; report the measured wall time for the
      injection itself and whether any characters were dropped. If it fails, write a
      `002-comment.md` describing exactly what the permission/daemon problem was rather than
      working around it — this is expected to be the first thing that breaks.

- [ ] `uv run mypy src/ges2t` exits 0: every definition fully annotated and every
      signature interface-typed — ADR 0019.

## Notes

- The measured injection cost counts toward the sub-1-second framework budget (ADR 0001), so
  report it as a number.
- Do **not** add a clipboard or `wtype` fallback, and do not catch a failure and retry with a
  different mechanism (ADR 0003). A failure must be visible.
- Keep `runner` a tiny callable (`argv, stdin_bytes, env -> CompletedProcess`-ish) so the fake
  is trivial.
- `ydotool` emits keycodes, so the target application's own key handling applies; do not
  attempt to compensate for that here.

## Related tickets

- depends on: ../core-interfaces-and-fakes/ticket.md, ../config-loading/ticket.md
- blocks: ../dictation-pipeline-batch/ticket.md, ../doctor-command/ticket.md
- related ADRs: ../../../adr/0003-text-injection-via-ydotool.md,
  ../../../adr/0012-verbatim-output-and-trailing-space.md
