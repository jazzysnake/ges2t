# 001 — ydotool injector implementation

**Ticket:** `ydotool-injector` · **Branch:** `feat/ydotool-injector` · **Status:** in review

## Files

- `src/ges2t/injectors/__init__.py` (new, docstring only)
- `src/ges2t/injectors/ydotool.py` (new)
- `tests/test_ydotool_injector.py` (new, 36 tests)

No other module was touched.

## Verified `ydotool` invocation

`ydotool --help` on this machine lists `click`, `key`, `type`, `mousemove`,
`debug`, `bakers`, and `man ydotool` (dated 2024-07-14) documents the subcommand as:

```
type [-D,--next-delay <ms>] [-d,--key-delay <ms>] [-f,--file <filepath>] "text"
```

with "The filepath may also be '-' to read from stdin." The invocation the code uses is
therefore exactly:

```
ydotool type --file -
```

with the UTF-8 payload on the child's **stdin** (never argv). One process spawn per
`inject()` call; nothing is typed character-by-character from Python. The docstring in
`ydotool.py` records this same verification next to the code.

Note: `ydotool type --help` cannot be used to check the flag, because `ydotool` connects to
the daemon *before* parsing the subcommand's `--help` — on this machine it prints the
socket error and exits 2. The man page is the authoritative source used here.

## Text-assembly semantics (ADR 0012)

Implemented once in `decide_injection(text, *, trailing_space)`, returning a typed union of
frozen dataclasses (`TypeText(payload)` | `SkipInjection(reason)`) rather than a
`str | None` sentinel (ADR 0019). Both `inject()` and the tests call that single function.

| input | `trailing_space` | outcome |
|---|---|---|
| `""` | either | `SkipInjection` — **runner is not called at all** |
| `"   "`, `"\t"`, `"\n"`, `"\u00a0"` | either | `SkipInjection` |
| non-empty | `True` | payload `text + " "`, exactly one appended ASCII space |
| non-empty | `False` | payload `text` unchanged |

The text is otherwise never modified: no `strip()`, no case change, no internal whitespace
collapsing, non-ASCII preserved. A text that already ends in a space (`"done "`) becomes
`"done  "` with `trailing_space=True`, because the rule is "append one space", not
"normalise to one space" — that is asserted directly.

## Delivery, environment, errors

- `YDOTOOL_SOCKET` is honoured by inheriting `os.environ`; when unset the daemon default
  (`$XDG_RUNTIME_DIR/.ydotool_socket`, i.e. `/run/user/1000/.ydotool_socket` here) applies.
  Tests assert the variable reaches the runner when set and is absent when unset.
- Error taxonomy, all subclasses of `YdotoolError`:
  - `YdotoolNotFoundError` — `FileNotFoundError` from the runner; message names `PATH` and
    the install command.
  - `YdotoolDaemonUnreachableError` — non-zero exit whose stderr matches the socket-failure
    markers (`failed to connect socket`, `connection refused`, `ydotoold is running`,
    `no such file or directory`). The message says to run
    `systemctl --user enable --now ydotool` and explains the `/dev/uinput` permission
    requirement (the `input` group / the ydotool udev rule, `crw-rw---- root:input`).
  - `YdotoolInjectionError` — any other non-zero exit; carries `returncode` and `stderr`
    and says delivery may be incomplete. Raised unconditionally, so a non-zero exit after
    partial typing is never swallowed.
- No clipboard or `wtype` fallback and no retry with another mechanism (ADR 0003);
  a whitespace-only input returns without spawning anything.

## Judgement calls

1. **`last_injection_ms` attribute.** ADR 0003 requires a measurable per-call cost, so the
   injector records the wall time of the most recent delivery (`None` when skipped). It is
   plain state on a single-threaded service object and does not change the `Injector`
   interface. Easy to drop if the reviewer prefers strict DoD minimalism.
2. **`--key-delay` left at the ydotool default (12 ms).** I did not pass `--key-delay 0`:
   the ticket explicitly warns about dropped characters, and the default pacing is what
   `ydotool` ships to avoid them. With that default a ~110-character phrase is expected to
   take a few seconds of wall time — which is exactly why the ticket asks for it to be
   *measured*. If the measured number blows the ADR 0001 budget, the right fix is a
   deliberate, measured `--key-delay` decision in a follow-up, not a silent default change
   here. This is flagged for the reviewer.
3. **Daemon-unreachable detection is stderr-text based.** `ydotool` exits 2 for the
   unreachable case (confirmed live below) but the exit-code space is not documented as
   stable, so classification keys off the message and falls back to the generic
   `YdotoolInjectionError`. Markers are matched case-insensitively.
4. **Whitespace test uses `str.strip()`.** Python's own whitespace set (`str.isspace()`),
   which includes NBSP (`\u00a0`) and is parametrised in the tests. The text itself is never
   stripped.

## Commands run (real output)

```
$ uv run pytest
============================= test session starts ==============================
platform linux -- Python 3.14.7, pytest-9.1.1, pluggy-1.6.0
configfile: pyproject.toml
collected 152 items
tests/test_config.py .................................             [ 21%]
tests/test_interfaces.py .................                         [ 32%]
tests/test_smoke.py ...                                            [ 34%]
tests/test_whistle_config.py ...................................   [ 63%]
....................                                               [ 76%]
tests/test_ydotool_injector.py ....................................[100%]
============================= 152 passed in 0.44s ==============================

$ uv run pytest tests/test_ydotool_injector.py -q
....................................                                 [100%]
36 passed in 0.06s

$ uv run mypy src/ges2t
Success: no issues found in 8 source files
```

`uv run mypy tests/test_ydotool_injector.py` also reports `Success: no issues found in 1
source file`.

## Manual end-to-end verification — **PENDING**

The ticket's manual step (type a 20-word phrase into a focused text editor and a focused
terminal, report wall time and dropped characters) **was not performed**, because it
requires `ydotoold` to be running and the daemon is not, and starting/enabling it is a
system change I must not make. Read-only probes on this machine:

```
$ systemctl --user is-active ydotool
inactive
$ systemctl --user is-enabled ydotool
disabled
$ ls -la /run/user/1000/.ydotool_socket
ls: cannot access '/run/user/1000/.ydotool_socket': No such file or directory
$ ls -la /dev/uinput
crw-rw----+ 1 root input 10, 223 Sep 28 20:18 /dev/uinput
$ test -w /dev/uinput && echo writable
writable
$ ydotool type --file - <<< 'probe'
failed to connect socket `/run/user/1000/.ydotool_socket': No such file or directory
Please check if ydotoold is running.
$ echo $?
2
```

So the **only** missing prerequisite on this machine is the daemon. `/dev/uinput` is already
writable by `matee` (the user is in the `input` group, gid 994), so no group change is
needed here — contrast the ticket's "would require sudo" assumption, which does not hold for
this user.

### Commands the user must run to enable manual verification

```bash
# 1. Start and enable the per-user daemon (Arch ydotool package ships this user unit).
#    No sudo: it is a --user service.
systemctl --user enable --now ydotool
systemctl --user is-active ydotool      # expect: active

# 2. Only if /dev/uinput is NOT writable by your user:
#    either be in the input group (already true here: id -> groups=...,994(input)),
#    or install ydotool's udev rule granting /dev/uinput; a re-login is needed after
#    a group change:
#      sudo usermod -aG input "$USER"
#    Verify with: test -w /dev/uinput && echo writable
```

After step 1, the manual test can be run without root:

```bash
# Focus a text editor, then measure a 20-word injection (~110 chars):
time ydotool type --file - <<< "$(printf 'one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty')"
# or drive the real code path:
uv run python -c "from ges2t.injectors.ydotool import YdotoolInjector; i=YdotoolInjector(); i.inject('the quick brown fox jumps over the lazy dog again and again and again today'); print(i.last_injection_ms)"
```

Repeat with a focused terminal to confirm both targets, then record the wall time and whether
any characters were dropped. **This must be done after the daemon is enabled; it has not been
done yet.**

### What was and was not verified

- Verified: unit behaviour of all four table branches, stdin-not-argv delivery, a single
  spawn per long payload, UTF-8 encoding, `YDOTOOL_SOCKET` passthrough, all three error
  branches and their message content, no-spawn on whitespace-only, `Injector` conformance,
  full test suite green, `mypy src/ges2t` clean.
- Not verified: any live keystroke reaching a real focused application, real wall time, and
  real dropped-character behaviour — blocked solely on the inactive daemon.

## For the orchestrator

- Manual verification is pending on the user enabling `ydotoold`; the code path itself is
  complete and tested. Re-run the ticket's manual step once the daemon is up.
- The `--key-delay` question (judgement call 2) is the one decision worth a reviewer's
  attention: measured live timing may exceed the 1 s budget and need an explicit decision.
- `doctor` (ticket 12) should map `YdotoolNotFoundError` / `YdotoolDaemonUnreachableError`
  / `YdotoolInjectionError` to its check lines; the messages are written to be printed
  verbatim there.
