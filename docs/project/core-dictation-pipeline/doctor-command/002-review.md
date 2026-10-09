# 002 — Review: doctor command (ticket 12)

Ticket: `docs/project/core-dictation-pipeline/doctor-command/ticket.md` ·
Epic: `docs/project/core-dictation-pipeline/epic.md` ·
Branch: `feat/doctor-command` · Worktree: `.worktrees/doctor-command` ·
HEAD reviewed: `46a9d0d` · Diff: `git diff main...feat/doctor-command` (three dots)

## Verdict

**PASS — mergeable.** Both hard gates hold; every check's branches are real and
falsifiable; the `ydotoold` socket bug and the `systemctl` typo are real and
fixed; 709 tests pass and mypy is clean.

**One caveat, non-blocking:** the requirement I added — that `doctor` surface a
silently-failing tray — is met as a *capability* (an injectable, tested WARN
branch) but **not** as a live signal. No real run of `doctor` can observe a tray
failure, because the record lives in the service process and there is no
cross-process channel. I rule this an **accepted limitation with a required,
precisely-scoped follow-up** (§4), not a defect in this ticket, because the
live channel needs a socket-protocol addition that this ticket is explicitly not
allowed to make.

Two ticket-document defects are also recorded (§10, §11): the DoD's `systemctl`
command is wrong and the audio table row contradicts the prose. The
implementation is right in both cases.

Everything below was reproduced on this machine; no keystrokes, no `ydotool`, no
clipboard, no `sudo`, no service change, no network. `ydotoold` was never
stopped — failures were forced with a read-only `YDOTOOL_SOCKET` env override.
All probes live in `/tmp/doctor_probe/` and are not committed. The working tree is
clean apart from this file.

## 1. Hard gate: no network I/O (ADR 0018)

**Method.** I did not trust the module's own test (`test_run_doctor_performs_no_network_io`,
which patches only `urllib.request.urlopen`). I ran the real `doctor` end to end
(`verify=False` and `verify=True`) under a `sys.addaudithook` that:

- raises on `socket.__new__` for `AF_INET`/`AF_INET6`,
- raises on `socket.connect` to any inet address, `socket.getaddrinfo`,
  `socket.gethostbyname`, `socket.gethostbyaddr`, `socket.sendto`, `urllib.Request`,
- replaces `urllib.request.urlopen` with a raiser *before* the package is imported,
- records every `subprocess.Popen`.

I first proved the guard is not vacuous (`/tmp/doctor_probe/validate_guard.py`):
`socket(AF_INET)` → blocked, `create_connection` → blocked, `getaddrinfo` → blocked,
`gethostbyname` → blocked, `urlopen` → blocked, `AF_UNIX` → allowed.

**Result.** Both the default and the `--verify` run (which hashes the 16.9 MB
model) completed with exit `0` and:

```
NET_EVENTS=[]
SUB_EVENTS=[('pw-cli', ('pw-cli', 'info', '0')),
            ('xkbcli', ('xkbcli', 'dump-keymap-wayland'))]
```

No network event of any kind. The only subprocesses are the read-only
`pw-cli info 0` and `xkbcli dump-keymap-wayland`; no `systemctl`, no downloader.

**Is the defusing genuinely effective?** `doctor` imports `needle` to check it is
importable, and `needle/__init__.py` does `from ._telemetry import track` at
import time, while `needle/_telemetry.py` sends to a Supabase endpoint via
`urllib.request.urlopen` on a daemon thread *and* writes `~/.cactus_needle/telemetry_id`.
`track()` is only called from `complete`/`run`/`extract`, so a bare import never
reaches it — but the defusing is what makes that safe even if a future version
called it at import. I verified directly:

```
naive _enabled(): True
after _defuse_backend_network(): _enabled(): False
env: {'NEEDLE_TELEMETRY': '0', 'DO_NOT_TRACK': '1', 'HF_HUB_OFFLINE': '1'}
```

`check_cactus_needle_importable` calls `_defuse_backend_network()` *before*
`import needle`, and `needle` is imported nowhere else on the doctor path
(`src/ges2t/transcribers/whistle.py:113` is the only `import needle`, inside
`_import_needle`, and no ges2t module imports it at module load). So the
defusing is ordered correctly and effective. **Gate holds.**

## 2. Hard gate: no writes, no system mutation (ADR 0016)

**Method.** Same audit-hook run, extended to record any `open` whose mode/flags
are write (`w/a/x/+`, `O_WRONLY|O_RDWR|O_CREAT|O_TRUNC|O_APPEND`) and any of
`os.mkdir/rename/remove/rmdir/truncate/chmod/chown/utime/link/symlink`,
`shutil.copyfile/move/rmtree`. A filesystem snapshot (`path size mtime`) of
`~/.config`, `$XDG_RUNTIME_DIR` and `~/.local/share/ges2t` was taken before and
after the `--verify` run.

**Result.** `WRITE_EVENT_COUNT=0` — zero write-flagged `open` calls and zero
mutating syscalls in the entire process, across the default and `--verify` runs.
No config file was created: `~/.config/ges2t/` still does not exist, and
`ges2t doctor` never creates it (ADR 0009's "the file is created only by an
explicit user action"). The compositor config is never touched (the only
keymap interaction is `xkbcli dump-keymap-wayland`, a read). The snapshot diff
showed exactly two unrelated mtime changes, both owned by other running
processes — Brave's extension log and `/run/user/1000/dconf/user` — not by the
doctor process; the in-process audit hook is the authoritative evidence and it
recorded nothing. **Gate holds.** (Importing a module can in principle write a
`.pyc`; here all bytecode was already cached, and the hook still recorded zero
writes, so no `__pycache__` was created either.)

## 3. Hard gate: no real tray icon, no display

- `check_tray_backend` calls `ges2t.ui.tray.assert_appindicator_backend()`, whose
  body does `gi.require_version(...)`, `import pystray`, and
  `getattr(pystray.Icon, "__module__", None)`. It constructs **no** icon and opens
  no window (read `src/ges2t/ui/tray.py:assert_appindicator_backend`).
- Importing `ges2t.doctor` loads none of `gi`, `pystray`, `needle`, `PIL`
  (verified independently: `banned loaded: []`).
- `env -u DISPLAY -u WAYLAND_DISPLAY -u XDG_SESSION_TYPE -u QT_QPA_PLATFORM
  uv run pytest tests/test_doctor.py -q` → **85 passed**, exit `0`. No test needs
  a display.

## 4. Ruling on the added requirement: surfacing a silently-failing tray

**What was required.** "`doctor` (and/or `status`) must be able to report them, so
a broken tray is visible rather than merely absent."

**What exists.** `check_tray_failures(*, failure_report: TrayFailureProbe | None)`
is wired into `build_report` as
`check_tray_failures(failure_report=probes.tray_failure_probe)`; `DoctorProbes.tray_failure_probe`
defaults to `None`. A non-`None` provider returning a `TrayFailure` yields a WARN
carrying `TrayFailure.summary()`. The WARN branch is tested
(`test_tray_failures_warn_with_the_recorded_summary`) and I re-caught it with my
own mutation (§12, REV-M3). The real default output is truthful:

> `Tray failures  OK  no in-process tray failure record is available; the
> running service prints tray failures to stderr and again at shutdown`

**Is the requirement met?** **Not in practice — only apparently.** The injectable
probe is scaffolding: no real run of `doctor` ever supplies a provider, so a user
whose tray raises on every call sees a green `OK` line and gets no diagnosis.
The residual real signal is the service's own stderr (`TrayStatusUI._guard`
prints the first distinct message per action) plus `_shutdown_status_ui`, which
prints `ges2t: <summary>` at shutdown (`src/ges2t/pipeline.py:260`). Under the
epic's documented workflow — the user runs `ges2t run` in a terminal — that is a
genuine, immediate signal. But for an autostarted or backgrounded service the
stderr is lost, and the whole point of the added requirement was that `doctor`
is the on-demand place a user looks. So the reviewer's concern (the service
stderr is "likely never read") is *not* answered by this implementation.

**Ruling: accepted limitation, non-blocking, with a required follow-up ticket.**
It is non-blocking because the DoD's contract for this ticket is satisfiable by a
tested, injectable capability ("covers each check's OK/WARN/FAIL branch by
injecting fake probes"), the record genuinely lives in another process, and
`doctor`'s no-write/no-network constraints make a live wire impossible without a
protocol change. It is a *limitation* because the user-facing outcome the
requirement named is not delivered.

**Precise, actionable follow-up.** File a ticket that adds a **read-only
diagnostics verb to the service socket** and feeds it into `check_tray_failures`:

1. Extend `src/ges2t/socket_proto.py`'s closed verb set (ADR 0016) with a
   read-only command, e.g. `diagnostics`, whose response carries the service's
   `DiagnosticStatusUI.failure_report()` as a small structured record
   (`count: int`, `last_message: str | None`). This requires an ADR 0016
   amendment or a superseding ADR, because that ADR freezes the verb list.
2. Have the service handler render that record from the live `StatusUI`
   (`TrayStatusUI.failure_report()` already exists, `src/ges2t/ui/status.py:117`).
3. In `doctor`, when `is_service_listening` is true, connect and query that verb,
   then pass the result into `DoctorProbes.tray_failure_probe`; keep the current
   `None` summary for the service-not-running case.
4. Mirror it in `ges2t status`, per the DoD's "and/or".

Until that lands, the honest position is: **the tray-failure requirement is
partially met — the branch exists and is tested, but it is inert.** The ticket
must not be recorded as fully satisfying it. The implementation comment's
judgement call #3 is accurate and does not overclaim.

## 5. Falsifying the 22 checks (66 branches)

`/tmp/doctor_probe/checks_probe.py` — built independently of `tests/test_doctor.py`
(its own fakes; fake `PATH`, fake daemon probes, fake `pystray`/`gi` modules, fake
data dir) — exercised **66 OK/WARN/FAIL branch assertions across all 22 checks**
and printed `ALL BRANCHES OK`. Covered, per check: package imports (OK/FAIL);
architecture (OK/FAIL); model present (OK/missing/size); model verified (off /
match / mismatch / missing / custom-path-no-pin); engine present (OK/missing/
size/no-pin); `cactus-needle` importable (OK/FAIL); versions (match / skew /
absent); config valid (OK / `ConfigError` / crash); engine config valid (OK /
`WhistleConfigError` / other-engine / warnings); injection config valid (OK /
`InjectionConfigError` / unknown-backend); config warnings (OK / merged
generic+engine+injection); audio device (empty / present / absent→WARN /
unreadable→WARN / invalid→FAIL); `pw-record` (OK/FAIL); PipeWire session (OK/FAIL);
`ydotoold` (OK/FAIL); `/dev/uinput` (OK/FAIL); tray backend (OK / `_xorg`→FAIL /
missing-`gi`→FAIL); tray failures (no provider / no record / record→WARN); service
socket (absent / listening / stale→FAIL); keyboard layout (live / configured /
detected / unresolvable→FAIL / unresolvable-not-required→WARN / compile→FAIL /
compile-not-required→WARN); clipboard (not selected / present / missing→FAIL);
hotkey (always WARN).

**Fix-line invariant.** Mechanically: for both non-`OK` statuses I attempted
`fix=None`, `""`, `"   "`, `"\t"`, `"\n"` — **all 10 constructions raised
`ValueError`, zero constructed**. `OK` without a fix and non-`OK` with a fix are
accepted. In real runs (`ok.out`, `failrun.out`, `multi.out`) every `FAIL` line is
followed by a non-empty `fix:` line: `FAIL lines=3 without-following-fix=0`
(3-failure run), `1/0` (forced-failure run), `0/0` (healthy run).

## 6. Exit code

- Healthy run (`uv run ges2t doctor`): 22 checks, 21 ok, 1 warning, 0 failures →
  **exit 0**.
- Forced failure via the **read-only** `YDOTOOL_SOCKET=/tmp/definitely-no-ydotool.sock`
  override (the user's daemon was *not* stopped):
  `ydotoold FAIL ... fix: enable and start ... 'systemctl --user enable --now ydotool' ...`
  → **exit 1**, and the failure is re-listed at the end with its fix.
- Three failures forced (`XDG_DATA_HOME=<empty temp>` + bad socket): 18 ok, 1
  warning, 3 failures → **exit 1**.
- The implementer demonstrated `FAIL`-via-override, not by stopping the daemon,
  and reported exit 1. Reproduced exactly.

## 7. Config-error unification and the broad `except`

- Both error types are handled as **separate checks** and never share a base
  class: `diagnose_config` catches `ges2t.config.ConfigError` (names the file and
  key), `diagnose_engine_config` catches
  `transcribers.whistle_config.WhistleConfigError` (names `engine.whistle.<key>`),
  `diagnose_injection_config` catches `InjectionConfigError`
  (`injection.ydotool.<key>`). All three verified to produce the specific wording
  with `"crashed" not in summary`.
- **The "broad `except Exception` was removed" claim is inaccurate as stated.**
  `git show 272c1cc:src/ges2t/doctor.py` shows the broad fallback was present in
  the very first commit of the branch — it was never removed, and it is
  *correct* that it stays as a crash guard. What the implementer actually did
  (commit `9d11c92`, tests only) was **strengthen the tests** so a crash cannot
  pass as a clean config error: a crash now produces `"the check crashed: ..."
  ... report it as a bug`, and the tests assert `"crashed" not in summary` for
  each real error type. I confirmed the property directly: a loader raising
  `RuntimeError` yields a `FAIL` whose summary contains `"crashed"` and does
  **not** contain `"invalid configuration"`. The guarantee is met; the summary
  sentence in `001-comment.md` should say "distinguished by an explicit crash
  marker", not "removed".

## 8. The `ydotoold` `SOCK_DGRAM` bug

The bug is real and the fix is genuinely exercised, not tautological. I bound my
own real listeners (`/tmp/doctor_probe`, own paths) and called the real probe:

```
datagram listener accepted by _ydotoold_reachable: True
stream   listener accepted by _ydotoold_reachable: False
old SOCK_STREAM probe on datagram daemon (the bug): False   <- a running daemon reported unreachable
stream probe on stream listener (what the old probe accepted): True
```

`ydotoold` binds an `AF_UNIX` `SOCK_DGRAM` socket (confirmed on this machine:
the daemon socket is a Unix socket and the real run reports it reachable), so a
`SOCK_STREAM` probe both misses the real daemon and accepts a stream listener.
`_ydotoold_reachable` now passes `socket_type=SOCK_DGRAM`; the two new tests bind
a real datagram listener (must accept) and a real stream listener (must reject),
so a regression to `SOCK_STREAM` fails a test — my mutation REV harness left it
alone; the implementer's M17 covers it and the probe shape is correct.

## 9. Version skew

`check_versions` reads the installed `cactus-needle` distribution via
`importlib.metadata` and always reports it **and** the engine version
(`engine_version = model_store.ENGINE_VERSION = "3.2.0"`). It WARNs on drift and
on absence, naming both pinned and installed versions in the fix line. The pin
lives in one place (`model_store.PINNED_CACTUS_NEEDLE_VERSION = "3.1.3"`) and
matches `pyproject.toml`'s `cactus-needle==3.1.3`. My branch probe:
`version_reader -> "3.1.3"` ⇒ OK; `-> "9.9.9"` ⇒ WARN with both `9.9.9` and
`3.1.3` in summary and fix; `PackageNotFoundError` ⇒ WARN. The real run prints
`cactus-needle 3.1.3 matches the pin 3.1.3; engine 3.2.0`. This is the R1
mitigation: a silent package upgrade now shows a visible doctor line. Satisfied.

## 10. The `systemctl` typo

The ticket's DoD says the fix must say `systemctl --user enable --now ydotoold`.
That command does not work on this machine: `pacman -Ql ydotool` ships
`/usr/lib/systemd/user/ydotool.service`; there is **no** `ydotoold.service`
anywhere under `/usr/lib/systemd` or `/etc/systemd`, and the unit declares no
alias. `doctor` prints the correct
`systemctl --user enable --now ydotool` and appends
`(the systemd unit is ydotool.service)`, while still naming the daemon
`ydotoold`. **The implementation is correct; the ticket's literal string would
have shipped a broken command.** The correction is recorded in `001-comment.md`
judgement call #1. The ticket's own DoD table was **not** amended (§11-style
staleness); the review records it here so the ticket can be corrected before
merge.

## 11. Audio device: `WARN` not `FAIL`

`WARN` is the right call. The DoD's *table* row ("FAIL when `audio.device` ...
does not appear") directly contradicts the DoD's *prose* added later ("this check
is a **WARN** when the configured device is absent (the user may still be
dictating fine, from the wrong mic) and a FAIL only if the device name is
syntactically invalid"). ADR 0008's amendment (2026-10-09) settles it:
`pw-record --target` is only a hint, an unresolved target **silently falls back
to the default microphone** and exits normally with empty stderr, so an absent
device does not mean dictation is broken — it may just be the wrong mic, and
marking it `FAIL` would make a working setup fail `doctor` and mislead the user
about which thing to fix. Only a syntactically impossible name (empty-after-strip,
or containing NUL/CR/LF) is a `FAIL`. My probe confirms: empty → OK, present →
OK, absent → WARN, nodes unreadable → WARN, `"a\nb"` → FAIL. The ticket's table
row is stale and should be corrected to match the prose.

## 12. Independent mutations

Three of my own mutations, applied by `/tmp/doctor_probe/mutations.py`, each with
a per-run `subprocess.run(timeout=180)`, `__pycache__` and `.mypy_cache` cleared
before and after every run, file restored with a sha256 check:

| Mutation | Guarded test(s) | Result |
|---|---|---|
| REV-M1 `check_versions` skew returns `_ok` instead of `_warn` | `test_versions_warns_on_installed_skew_and_names_both` | **CAUGHT** (1 failed) |
| REV-M2 `render_report` drops the "Failures (listed again…)" block | `test_render_report_relists_failures_and_prints_the_bindings`, `test_run_doctor_returns_nonzero_and_lists_every_failure_with_a_fix` | **CAUGHT** (2 failed) |
| REV-M3 `check_tray_failures` turns a recorded failure into `_ok` | `test_tray_failures_warn_with_the_recorded_summary` | **CAUGHT** (1 failed) |

`restored: True`; `git status` after the harness showed only the untracked
`002-review.md`. The implementer's claimed 34/34 are plausible and consistent
with the test set; I did not re-run all 34, but the branch coverage in §5 and
these three independent kills show the tests are not tautological.

## 13. Mechanicals, scope, hygiene

- `timeout 300 uv run pytest -q` → **709 passed**, exit `0`, no skips. The only
  change under `tests/` in the whole diff is the new `tests/test_doctor.py`
  (+1094, −0): no existing test was deleted, skipped or edited. `tests/test_doctor.py`
  collects **85** (84 `def test_` + one `parametrize` doubling), and 709 − 85 =
  **624**, the branch's own before-count. This is the "branch's own before/after":
  `main` has advanced since the cut, so the 624 is measured against the base, not
  current `main`.
- `rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests` →
  `Success: no issues found in 46 source files`, exit `0`.
- **Scope is exactly as declared:** `src/ges2t/doctor.py` (new, 1327 lines),
  `src/ges2t/cli.py` (only `doctor --verify` and the config-error-delegation
  handler), `src/ges2t/model_store.py` (only the `PINNED_CACTUS_NEEDLE_VERSION`
  constant plus its `__all__` entry), `tests/test_doctor.py`, and the ticket's
  `001-comment.md`, DoD `Status: in review`, and the board row
  (`docs/project/README.md`: ticket 12 `to do` → `in review`). No other file.
- **No new dependency:** `pyproject.toml`/`uv.lock` diffs are empty.
- **No ADR edits:** `git diff --stat main...feat -- docs/adr/` is empty.
- **ADR 0019:** `CheckResult`, `DoctorReport`, `ArtifactTarget`, `DoctorProbes`,
  `DoctorOptions`, `ConfigDiagnosis`, `BlockDiagnosis` are all
  `@dataclass(frozen=True)`; every check returns a `CheckResult`; probe
  parameters are fully-spelled `Callable[...]` aliases (no bare `Callable`), and
  every definition is annotated including `-> None` (mypy's
  `disallow_untyped_defs` enforces this over `tests/` too). No positional tuples
  cross a boundary.

## 14. Findings

**Blocking:** none.

**Non-blocking, should be recorded before merge:**

1. **Tray-failure live channel (§4).** Required follow-up ticket: add a read-only
   `diagnostics` socket verb + ADR 0016 amendment, feed it into
   `DoctorProbes.tray_failure_probe`, mirror in `ges2t status`. The DoD bullet
   "surface a silently-failing tray" is *not* fully delivered in a real run.
2. **Ticket DoD staleness (§10, §11).** Correct the DoD table's `systemctl --user
   enable --now ydotoold` to `ydotool`, and the audio row from `FAIL` to `WARN`
   (matching the table's own later prose). The code is right; the ticket is wrong.
3. **`001-comment.md` wording (§7).** "A broad `except Exception` was removed" is
   false — it is still present as the correct crash guard; the tests were
   strengthened to distinguish a crash from a config error. Reword.
4. **`ges2t doctor` with a genuinely missing `ges2t.doctor` (§15).** `from
   ges2t.doctor import DoctorOptions` executes *before* `_load_doctor_entry_point()`,
   so the real missing-module case hits the CLI's generic `except Exception`
   ("unexpected failure: No module named 'ges2t.doctor'") rather than the
   curated `MissingImplementationError` message; the repo's own test passes only
   because the module is cached in the test process. Still exits `1` and names the
   module, so minor. Fix: make the `DoctorOptions` import lazy/inside
   `_load_doctor_entry_point` so the intended message is the one printed.

## 15. What I could not verify

- **The 34 claimed mutations individually.** I independently killed 3 of my own
  (§12); I did not re-run the implementer's full list.
- **Whether the implementer's pre-commit working state really contained a
  `SOCK_STREAM` `ydotoold` probe.** History is squashed: the branch's first
  commit (`272c1cc`) already ships `SOCK_DGRAM`. I verified the *bug semantics*
  are real and the current probe rejects a stream listener and accepts a datagram
  listener, which is what matters.
- **A live silently-failing tray.** By construction there is no live channel to
  exercise; I verified only the injectable WARN branch and read the service-side
  stderr/shutdown printing.
- **`--verify` against a corrupt model on the real pinned file.** I exercised the
  sha256-mismatch branch with an injected `ArtifactPin`; the real model hashes
  correctly (`Model verified OK`, exit 0), which is a positive result, not a
  falsification of the mismatch path (that path is unit-tested and I reproduced
  it in §5).
- **Other platforms/architectures.** The architecture FAIL branch was exercised
  only with injected `platform_key`/`supported_platforms`.

## 16. Evidence index (all under `/tmp/doctor_probe/`, not committed)

| File | Proves |
|---|---|
| `net_probe.py` + `net_probe.err`/`verify.err` | no network (`NET_EVENTS=[]`), no writes (`WRITE_EVENT_COUNT=0`) for default and `--verify` |
| `validate_guard.py` | the audit-hook denial is not vacuous |
| `checks_probe.py` | 66 OK/WARN/FAIL branch assertions across 22 checks |
| `invariant_probe.py` | 10/10 non-OK-without-fix constructions refused |
| `ok.out` / `failrun.out` / `multi.out` | exit 0 (warning-only) / exit 1 (forced FAIL) / exit 1 (3 FAILs), every FAIL carries a fix |
| `mutations.py` | 3/3 independent mutations caught, file restored |
| `missing_module_probe.py` | real missing-`doctor` path (finding 4) |
| `onearg.out` | `run_doctor(config)` one-argument contract still works |
