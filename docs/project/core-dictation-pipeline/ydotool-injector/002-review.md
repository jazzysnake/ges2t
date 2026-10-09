# 002 — Review: Ydotool injector

Ticket: `ticket.md` (`ydotool-injector`) · Branch: `feat/ydotool-injector` ·
Commit reviewed: `9e7225a` · Reviewer artefact, not application code.

## Verdict

**pass**

Every testable Definition-of-Done bullet was reproduced from scratch against the code
(not read off the test file): the full ADR 0012 text-assembly table, stdin-not-argv
delivery, single-spawn batching, `YDOTOOL_SOCKET` passthrough, the four failure modes with
their user-facing messages, and the "no fallback / no retry" property. `uv run mypy
src/ges2t` exits 0 and is demonstrably enforcing; the scope is exactly the permitted files;
36 tests are honest and headless.

The one Definition-of-Done bullet that is **not** met is the live end-to-end injection test.
It is pending **by design** (the orchestrator forbade enabling the daemon), the pending claim
is **honest** — I independently confirmed every environment fact it rests on — and the
recorded enable command is correct. See "Honesty of the pending manual step".

One judgement call needs an explicit decision before or shortly after merge: `--key-delay`
was left at ydotool's default, which will very likely exceed the ADR 0001 sub-1-second
framework budget for a 20-word phrase. It is honestly flagged in `001-comment.md` and can
only be settled by the pending measurement. See Risks.

## Method

All commands were run in the worktree at commit `9e7225a` with the real `uv` environment.
The text-assembly table and the error taxonomy were re-derived independently with throwaway
`uv run python` heredoc probes (never written into the repo) that call `decide_injection`
and `YdotoolInjector` directly with a spy runner. The mypy-effectiveness probe was created,
run, and deleted; `git status --porcelain --untracked-files=all` is empty afterwards.

## Per-criterion findings

### 1. ADR 0012 text-assembly table — PASS (reproduced directly, not via the tests)

I called `decide_injection` directly for every row. Results:

```
text=''           trailing=True  -> SkipInjection
text=''           trailing=False -> SkipInjection
text=' '          ... -> SkipInjection
text='\t'         ... -> SkipInjection
text='\n'         ... -> SkipInjection
text=' \t\n '     ... -> SkipInjection
text='\xa0'       (NBSP)  -> SkipInjection
text='\u2003'     (em space) -> SkipInjection
text='\u3000'     (ideographic space) -> SkipInjection
```

Non-empty cases, all matching the expected payload exactly:

```
OK  'hello world'                     ts=True  -> 'hello world '   (exactly one ASCII space)
OK  'hello world'                     ts=False -> 'hello world'
OK  'done '                           ts=True  -> 'done  '         (append, not normalise)
OK  '  padded  '                      ts=False -> '  padded  '     (no strip)
OK  'two   spaces\tand\ttabs'         ts=False -> 'two   spaces\tand\ttabs'  (no collapsing)
OK  'MiXeD CaSe?!'                    ts=False -> 'MiXeD CaSe?!'   (no case/punct change)
OK  'Grüße – café 😀'                 ts=False -> 'Grüße – café 😀'  (non-ASCII preserved)
```

Whitespace-only through `inject()` with a spy runner: **0 calls** for `""`, `" "`, `"\t"`,
`"\n"`, `" \t\n "`, `"\u00a0"`. The runner is genuinely never invoked (not invoked and
discarded). `str.strip()` covers Unicode whitespace because the check is
`text.strip() == ""`, and Python's own `str.isspace()` set includes NBSP; the text itself is
never stripped.

**Exactly one implementation.** Grepping `src/` for the logic finds it only in
`decide_injection` (`src/ges2t/injectors/ydotool.py:155-159`): one `text.strip() == ""`
guard, one `text + " "`, one passthrough. The only other `trailing_space` hits are in
`config.py` (the flag's definition), and the only other `.strip()` in the module is on
`stderr`, not the payload. `inject()` has no second copy of the table — it delegates and
branches on the typed result.

**Typed result object, not a sentinel/tuple.** `InjectionDecision = TypeText | SkipInjection`
is a union of two frozen (`@dataclass(frozen=True)`) value objects with named fields
(`payload`, `reason`). No `str | None`, no positional tuple. A test also asserts the
frozen-ness (`FrozenInstanceError`). PASS.

### 2. Payload on stdin, not argv — PASS

Direct assertion against the constructed argv:

```
argv  = ('ydotool', 'type', '--file', '-')
stdin = b'SECRETPAYLOAD '
'SECRETPAYLOAD' in any argv element: False
argv == (YDOTOOL_BINARY, *TYPE_ARGUMENTS): True
exactly one runner call for "word " * 5000: True
```

The runner is called once per `inject()` with `input=<payload bytes>`; there is no
character-by-character loop and no `shell=True`. The argv is the constant
`("ydotool", "type", "--file", "-")`, and the payload is UTF-8 encoded into the child's
stdin. PASS.

A pleasant, unremarked bonus: per `ydotool` 1.0.4's own `type` help (extracted from the
binary, `/usr/bin/ydotool`), *"Escape is enabled by default when typing command line
arguments, and disabled by default when typing from file and stdin."* Using `--file -`
therefore also avoids ydotool's backslash-escape interpretation, which is exactly what
"verbatim, no rewriting" (ADR 0012) demands. The implementer justified the flag on
`ARG_MAX`/process-table grounds; the verbatim argument is a second, stronger reason the
choice is right.

### 3. `--key-delay` left at the default — DEFERRED, honestly flagged (see Risks)

`ydotool` is invoked as `type --file -` with no `--key-delay`, so the daemon's compiled
default applies. The implementer documented this as judgement call 2 and explicitly asks
for a measured follow-up decision rather than silently changing the default. The ticket
warns about dropped characters and asks for the wall time to be *measured*, so leaving the
default and deferring the decision to the (pending) live test is defensible.

However, the man page (`man ydotool`, 2024-07-14) documents `type -d,--key-delay` as
"Delay time between key events (up/down each). **Default 12ms**". At two events per
character, a 20-word phrase (~110–120 chars) is ≈ `2 × 115 × 12ms` ≈ **2.8 s**, i.e. it
would blow the ADR 0001 sub-1-second framework budget that ADR 0003 explicitly cites. This
is an estimate from documentation, not a measurement — the binary only exposes the default
as a runtime `%d` placeholder, so I could not read the true value without a live daemon. It
must be resolved by the pending measurement, not assumed either way. The latency-budget
concern is **flagged honestly** in `001-comment.md` ("may exceed the 1 s budget and need an
explicit decision").

### 4. No fallback, no retry, no swallowing — PASS

- The module contains exactly one `try` / `except FileNotFoundError`, which **re-raises** a
  typed error. No bare `except`, no `except Exception`, no passthrough of a failure.
- Grepping `src/` for `clipboard|wl-copy|wl-paste|wtype|xdotool` finds only docstrings
  *stating the absence*. No alternative mechanism exists anywhere in the tree.
- No retry loop and no second mechanism: on a simulated daemon failure the runner is called
  **exactly once** with `('ydotool', 'type', '--file', '-')`, then a typed error is raised.
- A non-zero exit is raised unconditionally, so a non-zero exit *after* partial typing
  cannot be swallowed (section 5).

ADR 0003 explicitly rejects a silent fallback; the code cannot fall back. PASS.

### 5. Error taxonomy — PASS (quoted verbatim below)

Four real branches reproduced end to end through `inject()`:

**Missing `ydotool` (runner raises `FileNotFoundError`)** →

> ydotool was not found on PATH, so ges2t cannot type. Install the ydotool package (Arch:
> 'sudo pacman -S ydotool') and confirm 'ydotool' runs in the same environment as ges2t.

**Daemon unreachable (real machine stderr, exit 2)** →

> ydotoold is not reachable, so ges2t cannot type into the focused window (ADR 0003 has no
> clipboard fallback). Start and enable the daemon with 'systemctl --user enable --now
> ydotool', and make sure it may write /dev/uinput: your user needs the 'input' group or
> ydotool's udev rule, which is why /dev/uinput is mode crw-rw---- root:input (a re-login
> picks up a group change). ydotoold said: failed to connect socket
> `/run/user/1000/.ydotool_socket': No such file or directory
> Please check if ydotoold is running.

Both are actionable, name the exact fix command, and the daemon message mentions both the
daemon and the `/dev/uinput` permission requirement the ticket demands. They are
user-facing and fit ADR 0016's "actionable sentence … the exact command that fixes it", and
ticket 12 `doctor` can print them verbatim.

**Generic non-zero exit** → `YdotoolInjectionError`, carrying `returncode` and `stderr`:

> ydotool exited with status 1 after being asked to type; any text already delivered to the
> focused window may be incomplete. stderr: uinput write failed

**Non-zero exit after partial typing** takes the same path and raises rather than returning
(status-137 simulation: "… after being asked to type; any text already delivered to the
focused window may be incomplete. stderr: Killed"). Empty stderr still raises and renders
`<empty>`. Classification keys off stderr markers case-insensitively and falls back to the
generic error. PASS.

Minor, non-blocking: the daemon detector includes the broad marker
`"no such file or directory"`, so a *generic* failure whose stderr happens to contain that
phrase (e.g. an unrelated missing path) would be reported as "daemon not reachable". The
message it prints still mentions `/dev/uinput`, so the user is not badly misled, and
`Permission denied` on the socket is *not* a marker and degrades to the generic error
(which lacks the `/dev/uinput` fix). Acceptable for v1; worth tightening if `doctor`
proves it confusing.

### 6. `YDOTOOL_SOCKET` — PASS

```
set   -> child env['YDOTOOL_SOCKET'] = /tmp/custom.sock
unset -> present in child env: False
```

The child environment is a copy of `os.environ`, so when the variable is set it reaches
`ydotool` unchanged and when unset the daemon default (`$XDG_RUNTIME_DIR/.ydotool_socket`)
applies. Tests cover both directions. PASS.

### 7. ADR 0019 — PASS

- No positional-tuple returns. `decide_injection -> InjectionDecision` (a union of frozen
  dataclasses); `_subprocess_environment -> dict[str, str]`; `_looks_like_daemon_failure ->
  bool`; every `__init__`, `inject`, `_deliver` has `-> None`.
- No bare `Any` anywhere in the package (grep: none). The runner seam is fully typed as
  `CommandRunner = Callable[[Sequence[str], bytes, Mapping[str, str]],
  subprocess.CompletedProcess[bytes]]` — a full `Callable` signature, not `Callable[...]`.
- No implicit `Optional`; `CommandRunner | None` and `float | None` are explicit.
- Signature style: the only collaborator (`runner`) is a typed callable seam passed in, and
  `YdotoolInjector` satisfies the `Injector` Protocol structurally — no concrete class
  crosses a module boundary.

`uv run mypy src/ges2t` →

```
Success: no issues found in 8 source files        # exit 0
```

`uv run mypy tests/test_ydotool_injector.py` → `Success: no issues found`. Note: plain
`uv run mypy tests` exits 1 on a **pre-existing, out-of-scope** error in
`tests/test_whistle_config.py:247` (`[arg-type]`), which I reproduced on `main` and which
this commit did not touch. The ticket's stated command is `uv run mypy src/ges2t`, which is
clean, so this is not a finding against this ticket — but it means "mypy is green for the
whole repo" is not currently true.

Effectiveness demonstrated (config really enforces `disallow_untyped_defs`):

```
$ cat > src/ges2t/_review_probe.py <<'EOF'
def untyped_probe(x):
    return x
EOF
$ uv run mypy src/ges2t
src/ges2t/_review_probe.py:1: error: Function is missing a type annotation  [no-untyped-def]
Found 1 error in 1 file (checked 9 source files)   # exit 1
$ rm src/ges2t/_review_probe.py
$ uv run mypy src/ges2t
Success: no issues found in 8 source files          # exit 0
$ git status --porcelain --untracked-files=all      # empty
```

Probe fully reverted; the worktree is clean. (The brief also warned a previous agent left a
probe behind; there is none in this tree.)

### 8. Scope — PASS

`git diff --name-only main...HEAD` is exactly:

```
docs/project/README.md
docs/project/core-dictation-pipeline/ydotool-injector/001-comment.md
docs/project/core-dictation-pipeline/ydotool-injector/ticket.md
src/ges2t/injectors/__init__.py      (new, docstring only)
src/ges2t/injectors/ydotool.py       (new)
tests/test_ydotool_injector.py       (new)
```

No forbidden module is touched: `interfaces.py`, `config.py`, `cli.py`, `tests/fakes.py`,
and the transcribers package are all unchanged. `injectors/ydotool.py` imports only
`os`, `subprocess`, `time`, `collections.abc`, `dataclasses` — no backend, no GUI, no
network, so it is importable in a bare environment.

### 9. Tests — PASS

```
$ uv run pytest -q
152 passed in 0.38s

$ uv run pytest tests/test_ydotool_injector.py -q
36 passed in 0.06s
```

36 collected, matching `001-comment.md`. Every test fakes the `runner` seam, so nothing
touches a daemon or `/dev/uinput` (headless, per ADR 0014). The tests exercise real
behaviour, not constants. I specifically checked whether any would pass with the behaviour
removed:

- remove the whitespace guard → `test_whitespace_only_inject_performs_no_call` fails (it
  asserts `runner.calls == []`);
- remove the append → `test_trailing_space_appends_exactly_one_space` and
  `test_text_already_ending_in_space_gets_a_second_one` fail;
- move the payload into argv → `test_inject_writes_payload_to_stdin_not_argv` fails (it
  asserts the substring is in no argv element);
- swallow a non-zero exit → the four failure tests fail;
- stop inheriting the environment → `test_ydotool_socket_is_passed_through_when_set` fails;
- type character-by-character → `test_single_runner_call_for_a_long_payload` fails.

The `--key-delay` and live-timing claims are, correctly, **not** asserted by any unit test
(they cannot be), which is why the manual step is the remaining gap.

### 10. Board, ticket status, links — PASS

`docs/project/README.md` row 7 is `in review` with branch `feat/ydotool-injector`; the
ticket header is `Status: in review`. All relative links in the ticket and comment resolve
as files, including `../epic.md`, the four related tickets, and the three ADRs
(`0001`/`0003`/`0012`). The DoD boxes are all left unchecked, including the manual one —
conservative and honest, since the manual step really is not done.

## Honesty of the pending manual step

**The "pending" claim is honest.** I independently reproduced every environment fact it
rests on, and none of it was faked:

```
$ systemctl --user is-active ydotool      # inactive (exit 3)
$ systemctl --user is-enabled ydotool     # disabled (exit 1)
$ ls -la /run/user/1000/.ydotool_socket   # No such file or directory
$ ls -la /dev/uinput                      # crw-rw----+ 1 root input 10, 223
$ id                                       # groups=...,994(input)
$ test -w /dev/uinput && echo writable    # writable
$ ydotool type --file - <<< 'probe'
failed to connect socket `/run/user/1000/.ydotool_socket': No such file or directory
Please check if ydotoold is running.
$ echo $?                                  # 2
```

The recorded enable command is correct: `systemctl --user list-unit-files` shows
`ydotool.service disabled`, and `systemctl --user cat ydotool` resolves to the shipped
**user** unit `/usr/lib/systemd/user/ydotool.service` (`ExecStart=/usr/bin/ydotoold`,
`WantedBy=default.target`) — so `systemctl --user enable --now ydotool` is right and needs
no `sudo`. The comment's "no such thing as `ydotool type --help`" note is also true: it
connects to the daemon before parsing and exits 2 (reproduced above); the man page is the
correct authority.

**What remains unverified:** any real keystroke reaching a focused application, the real
per-call wall time, real dropped-character behaviour — and, as a consequence, the
`--key-delay` decision. Nothing else about the injector is unverified. The comment's "what
was and was not verified" section is accurate.

**Could it be done without `sudo` or a persistent change?** Yes — `systemctl --user start
ydotool` (start only, no `enable`) would be transient and root-free, and the user is
already in `input` with `/dev/uinput` writable. But it is still a runtime system change, it
would type real keystrokes into whatever window currently has focus, and the orchestrator
explicitly reserved this decision. **I did not run it** and am flagging it for the
orchestrator to decide. `Linger=no`, so even after a start it would not survive logout.

## Evidence — commands and real output

```
$ git rev-parse HEAD
9e7225a8636f46a2cdc7644d8f983ff270062c39

$ uv run pytest -q
152 passed in 0.38s

$ uv run pytest tests/test_ydotool_injector.py -q
36 passed in 0.06s
$ uv run pytest tests/test_ydotool_injector.py --collect-only -q | tail -1
36 tests collected in 0.03s

$ uv run mypy src/ges2t
Success: no issues found in 8 source files          # exit 0
$ uv run mypy tests/test_ydotool_injector.py
Success: no issues found in 1 source file           # exit 0
$ uv run mypy tests
tests/test_whistle_config.py:247: error: Argument 1 to "_error_message" has incompatible
type "object"; expected "Mapping[str, Any]"  [arg-type]         # pre-existing on main
```

The independent `uv run python` probes' full output is summarised in sections 1–6 above;
the load-bearing lines are the table reproduction (`SkipInjection` for every
empty/whitespace/NBSP input, exact payloads for the four non-empty cases),
`'SECRETPAYLOAD' in any argv element: False`, and the two verbatim error messages.

## Risks / follow-ups

1. **`--key-delay` vs the ADR 0001 budget (must not be forgotten).** At the documented
   12 ms default, a 20-word phrase is expected to take seconds, so the shipped injector
   probably violates the sub-1-second framework budget that ADR 0003 cites. The pending
   manual measurement is the deciding data point. If it exceeds budget, the fix is a
   deliberate, measured `--key-delay` (or `-H/--key-hold`) value in a small follow-up —
   and it should be *measured*, because pushing pacing too low is exactly what drops
   characters. Do not close the epic's latency criterion until this number exists.
2. **Ticket 11 (`dictation-pipeline-batch`) wires this injector.** It must construct
   `YdotoolInjector(trailing_space=<ui.trailing_space from config>)`, keep the trailing
   space at the injection boundary only (the `TranscriptionResult` stays clean, ADR 0012),
   and treat every `YdotoolError` as a visible failure — never catch it and fall back. The
   injector deliberately has no configuration coupling, so the composition root owns the
   flag.
3. **Ticket 12 (`doctor`) reports these errors.** It should map `YdotoolNotFoundError`,
   `YdotoolDaemonUnreachableError`, and `YdotoolInjectionError` to distinct check lines and
   print the messages (they were written to be printed verbatim). The daemon message
   already contains the enable command and the `/dev/uinput` requirement ADR 0016 asks for.
4. **Tickets 15/16 (`streaming-session` / wiring) depend on injection timing.** The
   per-call cost ADR 0003 requires is exposed as `YdotoolInjector.last_injection_ms`
   (`None` when skipped). It is currently set *before* the return-code check, so a failed
   attempt also records a duration despite the docstring saying "the most recent
   non-skipped delivery"; harmless but worth aligning wording or moving the assignment if a
   later ticket reads it.
5. **Non-blocking taxonomy nit:** the `"no such file or directory"` marker is broad, and a
   socket `Permission denied` degrades to the generic error (which lacks the `/dev/uinput`
   guidance). Tighten if `doctor` output proves confusing in practice.

## Not verified / out of scope

- **The live end-to-end injection test is not performed** (pending by design; see above).
  Consequently there is no measured wall time, no dropped-character observation, and no
  confirmation that the default `--key-delay` meets the latency budget.
- I did not start or enable `ydotoold`, run `sudo`, or change any system setting, and I did
  not type keystrokes anywhere.
- I reviewed only the injector; the composition, service loop, and `doctor` do not exist
  yet and are tickets 11/12.
- `mypy tests` has one pre-existing unrelated failure
  (`tests/test_whistle_config.py:247`) that this branch neither introduced nor was asked to
  fix.
