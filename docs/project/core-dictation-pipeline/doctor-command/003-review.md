# 003 — Focused re-review: the two 002 fixes (tray honesty, wording, missing-module path)

Ticket: `docs/project/core-dictation-pipeline/doctor-command/ticket.md`
Branch: `feat/doctor-command` @ `35e0970` (worktree `.worktrees/doctor-command`)
Scope of this pass: the two fix commits `125ae52` (FIX 1 + FIX 3) and `35e0970`
(FIX 2), against `002-review.md`, which **PASSED** with non-blocking findings.

This is a *proportionate* re-review. `002-review.md` already verified the hard
gates, all 22 checks, the `ydotoold` `SOCK_DGRAM` probe, the `systemctl` unit-name
correction, the exit-code semantics and the fix-line invariant. Those were
**confirmed unchanged** here (see §5) rather than re-derived.

**Verdict (provisional): PASS — mergeable.** FIX 1 is the important one and it is
genuinely fixed and falsifiable in both directions; FIX 2's corrected wording now
matches the code; FIX 3's ordering fix and its subprocess test are correct. See
the mutation results in §2.

---

## 1. FIX 1 — `Tray failures`: no provider is a `WARN`, never a false green

`002-review.md` §4/§9 found that `check_tray_failures` returned `OK` when
`DoctorProbes.tray_failure_probe` defaulted to `None`, so **no real run could ever
report a tray failure** — a check that cannot fail, reading as verification. The
fix makes the no-provider branch `_warn(...)` with a fix line and turns a recorded
failure into `_fail(...)`.

Real output (this machine, `uv run python`, no provider):

```text
NO PROVIDER -> WARN | tray failures cannot be determined from here: the record lives in
the running service process, and doctor does not connect to it or write a file
  fix: watch the service's stderr for 'ges2t: the tray has failed ...' lines and its
  shutdown summary, or run 'ges2t run' in a terminal to see them
PROVIDER no record -> OK | the tray has recorded no failures | fix: None
PROVIDER record -> FAIL | the tray has failed 3 times: state update: no display
  fix: restart 'ges2t run' and read the tray messages; a tray that keeps failing will
  not update
  equals TrayFailure.summary(): True
```

- The no-provider result is `WARN` (not `OK`, not `FAIL`): nothing is known to be
  broken, but doctor cannot claim health it cannot observe.
- It carries a non-empty `fix:` line. This is not merely observed — it is
  **structurally enforced**: `CheckResult.__post_init__` raises `ValueError` for a
  non-`OK` result without a fix, so the `WARN` could not exist without one.
- The recorded-failure `FAIL` summary is exactly `TrayFailure.summary()`.
- Exit code stays **`0`**: `DoctorReport.exit_code` is `EXIT_FAILURE iff
  self.failures` (only `FAIL`), and `run_doctor` documents "warnings are allowed".
  A lone `WARN` therefore does not change the status.

---

## 2. Mutation falsification (independently applied)

Each mutation was applied by editing the source, run with a per-run timeout,
then fully reverted (`git checkout -- <file>` plus `rm -rf __pycache__
.mypy_cache` so no stale `.pyc` could satisfy the restored run). File hashes were
compared before/after.

Baseline hashes before any probe:

```text
cfb62f17...  src/ges2t/doctor.py
f58090bf...  src/ges2t/cli.py
c44c2967...  tests/test_doctor.py
```

**M35 — no-provider `WARN` mutated back to `OK`** (restores the pre-fix false
 green). After clearing caches:

```text
$ uv run pytest tests/test_doctor.py::test_tray_failures_warn_without_a_provider_instead_of_claiming_ok -q
E  AssertionError: assert <CheckStatus.OK: 'OK'> is not <CheckStatus.OK: 'OK'>
FAILED ... 1 failed in 0.45s   (exit 1)
```

**CAUGHT.** This is the important direction: the pre-fix false green is now
falsifiable.

**M20 — a recorded failure mutated back to `OK`** (the provider must not be
ignored):

```text
$ uv run pytest tests/test_doctor.py::test_tray_failures_fail_with_the_recorded_summary -q
E  AssertionError: assert <CheckStatus.OK: 'OK'> is <CheckStatus.FAIL: 'FAIL'>
FAILED ... 1 failed in 0.50s   (exit 1)
```

**CAUGHT.**

**M36 — the import ordering reverted** (`DoctorOptions` before the entry point):

```text
$ uv run pytest tests/test_doctor.py::test_doctor_with_a_genuinely_missing_module_prints_the_curated_message -q
E  assert 'ges2t.doctor.run_doctor' in "ges2t: unexpected failure: blocked for the test\n ..."
FAILED ... 1 failed in 0.34s   (exit 1)
```

**CAUGHT.** I then ran the *in-process* missing-module test under the same
reverted ordering: `1 passed in 0.39s` — direct evidence that only the
fresh-interpreter test catches this (see §4).

After each probe: `git checkout -- <file>`, `rm -rf .mypy_cache`, and every
`__pycache__` directory removed, because a same-second `cp`/checkout can leave a
`.pyc` Python still accepts. The three hashes were re-checked after every probe
and match the baseline above exactly; `git status` shows only the new
`003-review.md`.

---

## 3. FIX 2 — the corrected wording matches the code

**What the code does** (`src/ges2t/doctor.py:1036-1055`, `diagnose_config`): a
`ConfigError` from the loader is rendered `invalid configuration: {exc}`; any
*other* exception is a separate `except Exception` arm rendered
`the check crashed: {type(exc).__name__}: {exc}`. Independent probe:

```text
CRASH                FAIL=True INVALID=False :: the check crashed: RuntimeError: probe exploded
CLEAN-ConfigError    FAIL=False INVALID=True :: invalid configuration: /etc/x.toml: key 'a': bad
```

The distinction is real: a non-config exception cannot be mistaken for a clean
config error, and `test_config_valid_turns_an_unexpected_loader_crash_into_a_fail`
pins it. The same shape holds for `WhistleConfigError` and `InjectionConfigError`
(their own arms return the adapter's message; the fallback says `crashed`).

**What the addendum claims, checked against history:**

- `git show 9d11c92 --stat` → `tests/test_doctor.py | 33 +++-`, **one file**. The
  commit is tests-only, exactly as the addendum says. Its diff strengthens the
  three config-error tests with `"crashed" not in summary` / exact wording.
- `git show 272c1cc:src/ges2t/doctor.py | grep 'the check crashed'` → present in
  the branch's **first** commit. So the broad `except Exception` was present from
  the start and was **never removed**.

**No overcorrection.** The addendum now states the guard "was never removed ...
remains the correct crash guard", and the code does exactly that. It does not
claim a code change happened, and it correctly attributes M26/M28 to the
*test-strengthening*, not a removal. This matches the code and the history.

---

## 4. FIX 3 — the ordering fix, and why the test must be a subprocess

`_handle_doctor` now resolves the entry point first:

```python
run_doctor = _load_doctor_entry_point()      # importlib.import_module("ges2t.doctor")
from ges2t.doctor import DoctorOptions        # only reached if doctor exists
```

With doctor absent, `importlib.import_module` raises `ImportError`, which
`_load_doctor_entry_point` wraps as the curated `MissingImplementationError`
(naming `ges2t.doctor.run_doctor`, "not available in this build"), instead of the
`from ges2t.doctor import DoctorOptions` line raising a bare `ModuleNotFoundError`
that the top-level `except Exception` prints as "unexpected failure".

**The in-process test genuinely cannot catch this — proven, not asserted.** The
existing in-process test monkeypatches `importlib.import_module`, but the test
module imported `ges2t.doctor` at collection time, so the module is already in
`sys.modules`. With the reverted ordering, `from ges2t.doctor import DoctorOptions`
succeeds from the cache and the only failing call is the monkeypatched
`importlib.import_module` inside `_load_doctor_entry_point` — which runs in *both*
orderings. Empirically, under the reverted ordering: in-process test **passed**,
fresh-interpreter test **failed**. A fresh interpreter with a `sys.meta_path`
blocker is therefore *required* to exercise the real path; this is a correct use
of `subprocess`, not a shortcut.

---

## 5. Nothing else moved (verified by diff)

`git diff 1788070..HEAD` (the 002 review commit to HEAD) touches exactly four
files: `001-comment.md` (the addendum), `src/ges2t/cli.py` (the `_handle_doctor`
ordering only), `src/ges2t/doctor.py` (the `check_tray_failures` body and its
docstring only), and `tests/test_doctor.py` (two tray-failure tests changed to
the corrected expectations plus the one new fresh-interpreter test).

- **The 22 checks.** The `build_report` result list is untouched; the diff is
  confined to `check_tray_failures`, so the other **21 checks are unchanged**. I
  counted the list at HEAD: 22 entries, with `Tray failures` in position 18.
- **`ydotoold` `SOCK_DGRAM` probe**: unchanged by the diff (its tests are in the
  untouched region).
- **`systemctl` / `ydotool.service` correction**: unchanged by the diff.
- **Exit-code semantics / fix-line invariant**: unchanged; confirmed live above
  (`WARN` keeps exit 0, and `CheckResult.__post_init__` forces the fix line).
- **No-network / no-writes gates.** I did **not** re-run the full audit-hook
  probe. Reason: the diff changes a pure in-memory returning function and only
  *reorders* an import of a symbol already exported by an already-imported module
  — it can introduce no new module import and no I/O. The harness script is also
  no longer present in `/tmp`. Instead I ran the module's own guards,
  `test_run_doctor_performs_no_network_io` and
  `test_doctor_socket_probes_are_unix_only`: **2 passed**.
- **Scope**: `git diff --name-only main...feat/doctor-command | grep -iE
  'adr|config'` → none. No ADR edit, no config change. (`model_store.py` was part
  of the branch before the 002 review and is untouched by these fixes.)

**Claims re-run at HEAD:** `timeout 300 uv run pytest -q` → **710 passed in
3.87s, exit 0**; `rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests`
→ **Success: no issues found in 46 source files**.

---

## 6. What I could not verify

- **The implementer's full 36/36 mutation harness.** I independently applied the
  three named mutations (both tray directions and the ordering) and caught all
  three; I did not re-run the other 33.
- **The full end-to-end audit-hook probe** for no-network/no-writes (see §5 for
  why it was not affected, and what I ran instead).
- **A real run reaching the tray `FAIL`.** In any real `ges2t doctor` run the
  provider is `None`, so the `FAIL` branch is exercised only by injection/tests;
  the WARN now states that honestly. The residual real signal really does exist:
  `src/ges2t/ui/tray.py:421` prints `ges2t: the tray has failed N times: ...` to
  stderr, and `_shutdown_status_ui` (`src/ges2t/pipeline.py:260-273`) repeats the
  summary at shutdown — so the fix line's `'ges2t: the tray has failed ...'` and
  "shutdown summary" are accurate, not invented. What I could not do is observe
  that live without starting a service/tray, which the constraints forbid.

---

## Verdict

**PASS — mergeable.** The only required 002 fix (FIX 1) is real, correctly
scoped, and falsifiable in both directions; FIX 2's corrected wording matches the
code and the history; FIX 3 fixes a genuine message path and its fresh-interpreter
test is the only test that can catch it. The previously-verified gates are
unchanged. Recommended follow-up remains 002 §9: a read-only diagnostics channel
feeding `DoctorProbes.tray_failure_probe`, tracked separately.
