# 001 — Doctor command implementation notes

Ticket: `ticket.md` (`doctor-command`, #12) · Branch: `feat/doctor-command` ·
Worktree: `.worktrees/doctor-command` · Status: **in review**

## What was built

- `src/ges2t/doctor.py` — the whole command: one independent, injectable check
  per prerequisite, a frozen result object per check, report assembly, and
  `run_doctor(config, options=None) -> int`.
- `src/ges2t/cli.py` — the `doctor` subparser gains `--verify`; `_handle_doctor`
  now passes the resolved config path and the flag through a new
  `_load_doctor_entry_point`, so doctor (not the CLI) catches and reports a
  generic `ConfigError`.
- `src/ges2t/model_store.py` — one constant, `PINNED_CACTUS_NEEDLE_VERSION`
  (`"3.1.3"`), the distribution pin doctor compares the installed package
  against (ticket 14's risk R1). The model/engine artifact pins are unchanged.
- `tests/test_doctor.py` — 85 tests, every external probe injected.

No tray icon is created, no terminal or GUI window is opened, no keystroke is
injected, no `ydotool` is run, nothing is written to the clipboard, the disk, or
system state, and there is no `sudo`. `doctor` is safe to run while the service
is running.

## The exact checks implemented (22)

| # | Check (report name) | OK | WARN | FAIL |
|---|---|---|---|---|
| 1 | Package imports | all own modules import | — | a module fails; fix: `uv sync`/reinstall |
| 2 | Architecture | platform is pinned | — | no pin; fix names the pinned platforms |
| 3 | Model present | `whistle.cact` at pinned size | — | missing/wrong size; fix: `ges2t setup` |
| 4 | Model verified | `--verify` sha256 matches, or "not requested" | — | missing/mismatch; fix: `ges2t setup` |
| 5 | Engine present | `libneedle.so` at pinned size | — | missing/wrong size/no pin; fix: `ges2t setup` |
| 6 | cactus-needle importable | `import needle` works | — | fails; fix: `uv sync` |
| 7 | Engine/package versions | installed `cactus-needle` == pin | differs, or not installed | — (always reported) |
| 8 | Config valid | generic TOML + types valid | — | `ConfigError`; fix names the file + key |
| 9 | Engine config valid | `[engine.whistle]` valid | — | `WhistleConfigError`; fix names block+key+range |
| 10 | Injection config valid | selected `[injection.*]` valid | — | `InjectionConfigError`; fix names block+key |
| 11 | Config warnings | no unknown keys | lists generic+engine+injection unknown keys | — |
| 12 | Audio device | empty=default, or present in node list | configured name absent, or nodes unreadable | syntactically invalid name |
| 13 | pw-record present | on `PATH` | — | absent; fix: `pipewire` package |
| 14 | PipeWire session | `pw-cli info 0` succeeds | — | unreachable; fix: how to check the session |
| 15 | ydotoold | socket accepts a connection | — | unreachable; fix: `systemctl --user enable --now ydotool` |
| 16 | /dev/uinput access | writable | — | not writable; fix: `input` group + re-login |
| 17 | Tray backend | `assert_appindicator_backend()` passes | — | fails; fix: three Arch packages + `--system-site-packages` |
| 18 | Tray failures | no record, or no failures | `TrayFailure.summary()` | — |
| 19 | Service socket | absent, or listening | — | exists but stale; fix: restart `ges2t run` |
| 20 | Keyboard layout | resolved (source printed) and compiled | unresolvable/uncompilable when clipboard backend | unresolvable/uncompilable with ydotool |
| 21 | Clipboard backend | ydotool selected, or `wl-copy` present | — | clipboard selected and `wl-copy` missing |
| 22 | Hotkey bindings | — | always; prints the exact KDE binding steps | — |

`run_doctor` returns `0` when there is no `FAIL` (warnings allowed) and `1`
otherwise. `FAIL` entries are re-listed at the end with their fix lines, and the
recommended KDE bindings are printed once for copying. The `CheckResult` data
class refuses to construct a non-`OK` result without a non-empty fix line, so a
failure cannot be printed without a fix.

## Real command output (reference machine)

`uv run ges2t doctor` (exit **0**): the machine is currently healthy — the user
enabled `ydotoold` on 2026-10-09, so the FAIL the ticket anticipated is not
present today and the tool reports truthfully:

```text
ges2t doctor

Package imports           OK    all 17 package modules import (Python 3.14.7)
Architecture              OK    linux-x86_64 has a pinned engine artifact
Model present             OK    whistle.cact is present at /home/matee/.local/share/ges2t/models/whistle/2.0.0/whistle.cact (16919407 bytes)
Model verified            OK    not requested; pass --verify to hash the model against its pinned sha256
Engine present            OK    libneedle.so is present at /home/matee/.local/share/ges2t/engine/3.2.0/libneedle.so (1581448 bytes)
cactus-needle importable  OK    'needle' imports
Engine/package versions   OK    cactus-needle 3.1.3 matches the pin 3.1.3; engine 3.2.0
Config valid              OK    the configuration parsed and every generic value has the right type
Engine config valid       OK    [engine.whistle] is valid
Injection config valid    OK    [injection.ydotool] is valid
Config warnings           OK    no unknown keys were found
Audio device              OK    audio.device is empty: the system default input is used
pw-record present         OK    'pw-record' is at /usr/bin/pw-record
PipeWire session          OK    a PipeWire session is reachable
ydotoold                  OK    the ydotoold daemon is reachable at /run/user/1000/.ydotool_socket
/dev/uinput access        OK    /dev/uinput is writable by this user
Tray backend              OK    pystray selected the visible AppIndicator backend
Tray failures             OK    no in-process tray failure record is available; the running service prints tray failures to stderr and again at shutdown
Service socket            OK    the service is listening at /run/user/1000/ges2t.sock
Keyboard layout           OK    resolved from the live compositor keymap; libxkbcommon compiled it
Clipboard backend         OK    not selected (injection.backend = 'ydotool'); 'wl-copy' is not required
Hotkey bindings           WARN  ges2t does not grab the hotkey itself; bind the command in KDE
                                fix: System Settings -> Shortcuts -> Add -> Command or URL: bind 'ges2t toggle' (recommended), or 'ges2t ptt-start' and 'ges2t ptt-stop', plus 'ges2t cancel'

Summary: 22 checks, 21 ok, 1 warning(s), 0 failure(s).
```

The FAIL branch is real, not only unit-tested: pointing `YDOTOOL_SOCKET` at a
path with no daemon (a read-only environment override; the running daemon was not
touched) makes the check fail with exit **1** and the fix line re-listed:

```text
ydotoold                  FAIL  the ydotoold daemon is not reachable at /tmp/definitely-no-ydotool.sock
                                fix: enable and start the ydotoold daemon with 'systemctl --user enable --now ydotool' (the systemd unit is ydotool.service), or start 'ydotoold' by hand; make sure it can write /dev/uinput
...
Summary: 22 checks, 20 ok, 1 warning(s), 1 failure(s).

Failures (listed again with their fixes):
  ydotoold: the ydotoold daemon is not reachable at /tmp/definitely-no-ydotool.sock
    fix: enable and start the ydotoold daemon with 'systemctl --user enable --now ydotool' (the systemd unit is ydotool.service), or start 'ydotoold' by hand; make sure it can write /dev/uinput
```

`uv run ges2t doctor --verify` hashes the 16.9 MB model and reports
`Model verified OK ... matches the pinned sha256` (exit 0). An invalid generic
config (`device = 123`) and an invalid engine block (`chunk_seconds = 0.01`) each
produce a `FAIL` line carrying the file/key/range and exit 1; a config with an
unknown key, a typo'd `audio.device`, and `injection.backend = "clipboard"`
produces three warnings (generic unknown key, absent device, and a passing
`wl-copy`) and exits 0.

## Verification commands and real output

Baseline on `main` at dispatch: **624 passed**, **44 files** under `mypy`.

```text
$ uv run pytest tests/test_doctor.py -q
85 passed in 0.86s

$ uv run pytest -q
709 passed in 4.81s

$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 46 source files
```

`uv run mypy src/ges2t tests` is the ADR 0019 enforcement command and covers the
tests too. The two extra files over the baseline are `src/ges2t/doctor.py` and
`tests/test_doctor.py`.

## Falsification evidence

A mutation harness (in `/tmp`, not committed) applies one textual mutation at a
time to `doctor.py`/`cli.py`, clears `__pycache__`, runs the single test that
guards the mutated behaviour, restores the file, and re-hashes every touched
file. Result: **34/34 mutations made the guarded test fail; all files restored
byte-for-byte.**

```text
CAUGHT  M1 fix-line guard removed
CAUGHT  M2 exit code always 0
CAUGHT  M3 package-import failure ignored
CAUGHT  M4 architecture failure ignored
CAUGHT  M5 model size ignored
CAUGHT  M6 model missing ignored
CAUGHT  M7 sha256 verify ignored
CAUGHT  M8 engine pin-None ignored
CAUGHT  M9 cactus-needle import failure ignored
CAUGHT  M10 version skew not warned
CAUGHT  M11 missing distribution not warned
CAUGHT  M12 audio device absent treated as OK
CAUGHT  M13 invalid device not failed
CAUGHT  M14 pw-record absence ignored
CAUGHT  M15 session failure ignored
CAUGHT  M16 ydotoold failure ignored
CAUGHT  M17 ydotoold probe uses SOCK_STREAM (the measured bug)
CAUGHT  M18 uinput failure ignored
CAUGHT  M19 tray assertion not called
CAUGHT  M20 tray failure report ignored
CAUGHT  M21 stale socket treated as OK
CAUGHT  M22 layout resolution failure ignored
CAUGHT  M23 keymap compile failure ignored
CAUGHT  M24 clipboard absence ignored
CAUGHT  M25 hotkey not warned
CAUGHT  M26 generic ConfigError not caught
CAUGHT  M27 WhistleConfigError not caught
CAUGHT  M28 InjectionConfigError not caught
CAUGHT  M29 block warnings ignored
CAUGHT  M30 failures not relisted
CAUGHT  M31 gi imported at doctor module import
CAUGHT  M32 doctor --verify flag not wired
CAUGHT  M33 CLI pre-empts the config error
CAUGHT  M34 backend telemetry/network not defused

caught 34/34
files restored: True
```

Two mutations are worth recording because the first version of the tests did
**not** catch them, and the tests had to be strengthened:

- **M26/M28.** Mutating `except ConfigError`/`except InjectionConfigError` still
  left the `except Exception` fallback, which returns a `FAIL` whose summary
  contains the original exception text — so the weak assertion
  `"<key>" in summary` passed. The tests now assert `"crashed" not in summary`
  and the exact wording, and both mutations are caught.
- **M17** is the measured bug itself: `ydotoold` listens on a **`SOCK_DGRAM`**
  Unix socket, and my first probe used `SOCK_STREAM`, so a *running* daemon was
  reported unreachable (`Errno 91: Protocol wrong type for socket`). The real
  run caught it before any test did; the tests now bind a real `SOCK_DGRAM`
  socket and assert the probe accepts it **and** that a `SOCK_STREAM` listener is
  rejected, so the bug cannot return.

The `no network` test patches `urllib.request.urlopen` to raise and runs the
whole report with fake probes; a separate test drives the real socket probes with
`socket.socket` wrapped to assert every family is `AF_UNIX`. A subprocess test
imports `ges2t.doctor` in a fresh interpreter and asserts `gi`, `pystray`,
`needle` and `PIL` are absent from `sys.modules`.

## Judgement calls the orchestrator must know

1. **The ticket's `ydotoold` fix string is wrong; the unit is `ydotool`.** The
   DoD says the fix must say `systemctl --user enable --now ydotoold`, but
   `/usr/lib/systemd/user/ydotool.service` is the installed unit
   (`pacman -Ql ydotool`), and `ydotoold` is only the daemon binary. Doctor
   therefore prints the correct command
   `systemctl --user enable --now ydotool`, names the daemon `ydotoold`, and
   notes `(the systemd unit is ydotool.service)`. Shipping the ticket's literal
   string would have told users a command that does not work.
2. **Audio device: WARN when absent, FAIL only on an invalid name.** The DoD's
   table row reads "FAIL when ... does not appear", but its prose (added later)
   is explicit: absent is a **WARN** because PipeWire silently falls back to the
   default mic (ADR 0008's amendment) and dictation may still work; only a
   syntactically impossible name is a FAIL. I followed the prose.
3. **Tray failures are a capability, not yet a live wire-up.** The default
   provider is `None`, so today's report says no in-process record is available
   and points at the running service's stderr. `TrayStatusUI.failure_report()`
   is injectable through `DoctorProbes.tray_failure_probe`, and the check turns a
   `TrayFailure` into a `WARN` with `summary()`. The record lives in the service
   process and doctor performs no writes, so a live cross-process channel needs
   the socket protocol (ADR 0016's verb set is closed) — ticket 17's own
   hand-off says the `status`/`doctor` wire-up is *future*. This is called out
   here rather than faked.
4. **`run_doctor` keeps the one-argument contract but gains an optional second
   argument.** It is `run_doctor(config, options=None)`; `run_doctor(config)`
   still works. The optional `DoctorOptions` carries the resolved config path
   (so doctor itself reports a generic `ConfigError` instead of the CLI
   pre-empting it) and `--verify`. The CLI still passes the **loaded `Config`**
   as ticket 10's contract requires; `cli.py`/`doctor.py` are the two files that
   ticket's serialization note already assigns to #12.
5. **Backend telemetry/network is defused before `import needle`.** Doctor
   imports the backend to check importability, so it applies
   `model_store.env_overrides()` first (process-local; nothing is written). I
   confirmed on this machine that `import needle` spawns no telemetry thread and
   that `track()` is only called from `transcribe`/`stream`/`extract`, but the
   override is cheap and matches ADR 0007's "before the backend is imported".
6. **`CheckResult` is a frozen dataclass, not a tuple; `CheckStatus` is a
   `StrEnum`.** The report is a frozen `DoctorReport` with `failures`/`warnings`/
   `exit_code` properties (ADR 0019). Each check takes its dependencies as
   parameters with real defaults, and the whole set is bundled in the frozen
   `DoctorProbes` (passed via `DoctorOptions`) so tests inject fakes and never
   touch the system.
7. **The two config error types are kept distinct.** Doctor catches
   `ges2t.config.ConfigError` (file path) and
   `ges2t.transcribers.whistle_config.WhistleConfigError` (block key) — plus
   `InjectionConfigError` for the injector block — as three separate checks and
   never introduces a shared base class. The block-key wording is preserved
   verbatim in the summary.

## Anything else the reviewer should check

- `doctor` imports no `gi`/`pystray`/`needle` at module load and calls
  `assert_appindicator_backend()` (imports only) rather than building an icon.
- Every probe is inspection: `shutil.which`, `os.access`, a Unix-socket
  `connect`, read-only `pw-cli`/`pw-dump`, and the tray assertion. No keystroke,
  no `ydotool`, no clipboard write, no `sudo`, no service change.
- `ges2t models --verify` is unchanged and still reports both artifacts
  `verified`.
- The manual DoD checkbox ("must FAIL on ydotoold") is satisfied by the
  `YDOTOOL_SOCKET` override above, not by stopping the user's daemon. The daemon
  is currently active/enabled, so the healthy run is the truthful current state.

## Test coverage map (highlights)

| Behaviour | Test(s) |
|---|---|
| each check's OK/WARN/FAIL branch | one or more per check, all with fake probes |
| a non-OK result must carry a fix line | `test_non_ok_result_requires_a_non_empty_fix_line` |
| failure exit code is genuinely non-zero | `test_report_exit_code_is_nonzero_when_any_check_fails`, `test_run_doctor_returns_nonzero_and_lists_every_failure_with_a_fix` |
| warning-only run exits 0 | `test_run_doctor_returns_zero_with_only_a_warning` |
| every printed FAIL has a fix | `test_run_doctor_every_failure_result_has_a_fix_line`, `test_render_report_relists_failures_and_prints_the_bindings` |
| generic + engine `ConfigError` unification | `test_config_valid_fail_...`, `test_engine_config_fail_...`, `test_injection_config_fail_...` |
| unknown-key warnings merged | `test_build_report_runs_every_check_and_merges_block_warnings` |
| no network client invoked | `test_run_doctor_performs_no_network_io`, `test_doctor_socket_probes_are_unix_only` |
| no GUI/backend import at module load | `test_importing_doctor_pulls_in_no_gui_or_backend` |
| ydotoold datagram socket type | `test_ydotoold_probe_accepts_a_datagram_socket`, `test_ydotoold_probe_rejects_a_stream_socket` |
| CLI `--verify` and un-pre-empted config error | `test_doctor_parser_accepts_verify`, `test_doctor_cli_passes_the_path_and_verify_flag_to_run_doctor`, `test_doctor_cli_does_not_pre_empt_a_generic_config_error` |

---

## 001a — Addendum: review 002 fixes (tray-failure honesty, wording, missing-module path)

Status: still **in review**. `002-review.md` returned **PASS — mergeable** with no
blocking defects; this addendum records the one required change it asked for and
two corrections. The hard gates (no network, no writes) are untouched, as are the
other 21 checks, the `ydotoold` `SOCK_DGRAM` fix, the `systemctl` correction, the
exit-code semantics, and the fix-line invariant. Ticket stays `in review`; the
board row is unchanged.

### FIX 1 (required) — `Tray failures` no longer reports a false green

`check_tray_failures` used to return `OK` when `DoctorProbes.tray_failure_probe`
was `None`, so a real run always showed a green line even though the record lives
in the service process and doctor never sees it. **A check that cannot fail is
worse than no check, because it reads as verification.** The check now says:

- **no provider → `WARN`** ("tray failures cannot be determined from here ..."),
  with a fix line naming where failures *do* surface (the service's stderr and
  its shutdown summary). Not a `FAIL`, because nothing is known to be broken;
  not `OK`, because doctor cannot claim health it cannot observe.
- **provider present, no record → `OK`** ("the tray has recorded no failures").
- **provider present, a record → `FAIL`** carrying `TrayFailure.summary()`.

Real output on the reference machine (`uv run ges2t doctor`, exit **0**):

```text
Tray backend              OK    pystray selected the visible AppIndicator backend
Tray failures             WARN  tray failures cannot be determined from here: the record lives in the running service process, and doctor does not connect to it or write a file
                                fix: watch the service's stderr for 'ges2t: the tray has failed ...' lines and its shutdown summary, or run 'ges2t run' in a terminal to see them
...
Summary: 22 checks, 20 ok, 2 warning(s), 0 failure(s).
```

This is deliberate: **a check with no data says so rather than claiming health.**
The proper long-term channel — a read-only `diagnostics` socket verb feeding the
provider — is a separate ticket and is deliberately not implemented here.

### FIX 2 — correction: the broad `except Exception` was never removed

The original comment (and my report) could be read as claiming the broad
`except Exception` fallback was removed. It was not, and should not be: it was
present in the branch's first commit (`272c1cc`) and remains the correct crash
guard. Precise statement of what actually happened: commit `9d11c92` was
**tests only**; it strengthened the assertions so a check crash cannot pass as a
clean config error. A crashing loader now yields a `FAIL` whose summary contains
`"crashed"` and **not** `"invalid configuration"`, and the tests assert exactly
that for `ConfigError`, `WhistleConfigError` and `InjectionConfigError`. Nothing
in the implementation changed for this point. (The mutation harness's M26/M28
entries describe this same test-strengthening, not a code removal.)

### FIX 3 — the minor missing-module message path

`_handle_doctor` imported `DoctorOptions` *before* `_load_doctor_entry_point()`,
so a build with a genuinely missing `ges2t.doctor` hit the CLI's generic
`except Exception` ("unexpected failure: No module named 'ges2t.doctor'") instead
of the curated `MissingImplementationError`. The entry point is now resolved
first, so the module is imported (and its absence detected) before
`DoctorOptions` is imported. A fresh-interpreter test blocks `ges2t.doctor` with
a `sys.meta_path` finder and asserts stderr contains
`ges2t.doctor.run_doctor is not available in this build` and **not**
`unexpected failure` — a case the existing in-process test could not catch,
because that process has already imported `ges2t.doctor`.

### Falsification (both directions, plus the ordering)

The mutation harness was extended and re-run: **36/36 mutations caught, all files
restored byte-for-byte.** The three relevant ones:

```text
CAUGHT  M20 tray failure record ignored            (FAIL -> OK; test_tray_failures_fail_with_the_recorded_summary)
CAUGHT  M35 tray failures with no provider claimed OK (WARN -> OK; test_tray_failures_warn_without_a_provider_instead_of_claiming_ok)
CAUGHT  M36 missing-module ordering reverted       (DoctorOptions before the entry point; fresh-interpreter test)
caught 36/36
files restored: True
```

Both tray directions fail the moment they are broken: no-provider claimed `OK`,
and a recorded failure ignored. The full harness output is in
`/tmp/mutation-run.txt`.

### Verification after the fixes

```text
$ uv run pytest tests/test_doctor.py -q
86 passed in 0.96s

$ timeout 300 uv run pytest -q
710 passed in 3.19s (exit 0)

$ rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests
Success: no issues found in 46 source files
```

No test was deleted, skipped or weakened; two tray-failure tests changed to the
new (correct) expectations and two tests were added (no-provider `WARN`, and the
fresh-interpreter missing-module path).
