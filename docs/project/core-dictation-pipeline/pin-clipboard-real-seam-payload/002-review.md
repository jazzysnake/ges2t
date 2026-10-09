# 002 — Review: pin the real-seam payload delivery (#37)

**Ticket:** `pin-clipboard-real-seam-payload` (#37) · **Branch:** `feat/pin-clipboard-real-seam-payload` ·
**Worktree:** `.worktrees/pin-clipboard-real-seam-payload` · **Reviewed HEAD:** `f29d6e2`
**Merge-base with `main`:** `97604eb` (`git merge-base main HEAD`), the artifact's stated base
**Reviewer:** read-only on implementation and tests; every mutation below was reverted byte-identically
(sha256) before the next step, and the tree was clean at every checkpoint.

## Verdict: **PASS**

Two things had to be true and both are: (1) the new stdin assertion is the **only** thing in the
suite that catches `input=stdin_bytes → input=b""`, and (2) `src/` is byte-identical to `HEAD` and to
`main`'s pre-existing file — this ticket changes nothing executable. The tests are not vacuous, no
existing test was touched, and the doc correction is accurate rather than merely plausible.

One nuance, not a blocker: the new *stdout* test is falsified by its mutation, but it is **not the
only** test that catches it — the pre-existing `test_real_immediate_runner_still_captures_stdout`
does too (see §1.4). The artifact never claims uniqueness for the stdout mutation, so no correction
is needed; it is recorded here for the record.

---

## 1. Falsification reproduced independently

Baseline before any mutation (`rm -rf .mypy_cache __pycache__`, caches cleared):

```
$ timeout 300 uv run pytest -q
879 passed in 7.45s
PYTEST_EXIT=0
```

`src/ges2t/injectors/clipboard.py` fixed-blob hash at rest:

```
9e0f4e9b35191622e09c5b970149924027068ec47e6f885e466b0a345f2bfed5
```

### 1.1 stdin mutation — `input=stdin_bytes` → `input=b""`

Exactly one occurrence in the file. Mutated with the same anchored expression the implementer used:

```
$ grep -c "input=stdin_bytes" src/ges2t/injectors/clipboard.py
1
$ perl -pi -e 's/^            input=stdin_bytes,$/            input=b"",/' src/ges2t/injectors/clipboard.py
$ grep -n "input=" src/ges2t/injectors/clipboard.py
420:            input=b"",
$ sha256sum src/ges2t/injectors/clipboard.py
cb971d8df4e52e0e773b16dc94a05a362721c9fedb431144bb84c5ae48c00e76  src/ges2t/injectors/clipboard.py
```

Mutant hash matches the implementer's recorded `cb971d8d…`. Targeted run:

```
$ timeout 120 uv run pytest -q -p no:cacheprovider tests/test_clipboard_injector.py \
    -k "delivers_a_non_empty_payload_to_stdin"
>       assert target.read_bytes() == payload, "the child did not receive stdin bytes"
E       AssertionError: the child did not receive stdin bytes
E       assert b'' == b'na\xc3\xafv...x9f\x8e\xa4\n'
tests/test_clipboard_injector.py:688: AssertionError
1 failed, 92 deselected in 0.30s
TARGETED_EXIT=1
```

Whole suite on the mutant (the claim is that **only** the new test fails):

```
$ timeout 300 uv run pytest -q -p no:cacheprovider
1 failed, 878 passed in 7.32s
FULL_MUTANT_EXIT=1
FAILED tests/test_clipboard_injector.py::test_real_immediate_runner_delivers_a_non_empty_payload_to_stdin
```

Exactly one failure — the new stdin test — and the other 878 pass. **Confirmed.**

Revert:

```
$ cp /tmp/t37-review/clipboard_fixed.py src/ges2t/injectors/clipboard.py
$ sha256sum -c /tmp/t37-review/fixed.sha
/tmp/t37-review/clipboard_fixed.py: OK
$ sha256sum src/ges2t/injectors/clipboard.py
9e0f4e9b35191622e09c5b970149924027068ec47e6f885e466b0a345f2bfed5  src/ges2t/injectors/clipboard.py
$ git status --porcelain      # empty
```

Reverted byte-for-byte; `git status` empty.

### 1.2 stdin mutation on `main` (why the ticket exists)

Ticket 36's `002-review.md` §11 M4 records that on `main` this same mutation left the whole suite
green (`877 passed`). That is history I did not re-run (it would require overlaying `main`'s tests);
the ticket's premise and the unique-failure result in §1.1 are consistent with it.

### 1.3 stdout mutation — `stdout=stdout_file` → `stdout=subprocess.DEVNULL`

The anchored pattern matches only the `_run_immediate` assignment (the `stdout=stdout_file.read()`
line does not match):

```
$ perl -pi -e 's/^            stdout=stdout_file,$/            stdout=subprocess.DEVNULL,/' src/ges2t/injectors/clipboard.py
$ sha256sum src/ges2t/injectors/clipboard.py
495cdd109b2abc22205e863323eb39b71e0b0b28441fc02cd25c5fcd9e5aec7f  src/ges2t/injectors/clipboard.py
```

Mutant hash matches the implementer's recorded `495cdd10…`. Targeted run:

```
$ timeout 120 uv run pytest -q -p no:cacheprovider tests/test_clipboard_injector.py \
    -k "non_empty_stdout_payload"
>       assert completed.stdout == previous, "stdout bytes were not captured exactly"
E       AssertionError: stdout bytes were not captured exactly
E       assert b'' == b'old selecti...0\x9f\x8e\xa4'
tests/test_clipboard_injector.py:715: AssertionError
1 failed, 92 deselected in 0.29s
TARGETED_STDOUT_EXIT=1
```

Whole suite on this mutant (I ran it even though the artifact only showed the targeted run):

```
$ timeout 300 uv run pytest -q -p no:cacheprovider
2 failed, 877 passed in 7.56s
FULL_STDOUT_MUTANT_EXIT=1
FAILED tests/test_clipboard_injector.py::test_real_immediate_runner_still_captures_stdout
FAILED tests/test_clipboard_injector.py::test_real_immediate_runner_captures_a_non_empty_stdout_payload
```

**Nuance:** the stdout capture path is caught by the pre-existing
`test_real_immediate_runner_still_captures_stdout` (from ticket 36) as well as the new test. The new
test is therefore not the sole guard for that property — but it is not vacuous, it does fail on the
mutation, and it strengthens the coverage with a real regular-file source, exact multi-byte bytes and
an empty-stderr assertion. The artifact's §3.2 is accurate about what it measured.

Revert: `sha256sum -c` OK, hash back to `9e0f4e9b…`, `git status --porcelain` empty.

---

## 2. Guard-assertion audit (are the tests vacuous?)

Both tests carry two guard assertions before the byte-exact comparison:

```python
assert len(payload) > 0, "the payload under test must be non-empty"
assert any(byte > 0x7F for byte in payload), "payload must be multi-byte"
assert target.read_bytes() == payload, "the child did not receive stdin bytes"
```

These assert properties of the **test's own literal**, not of the code under test, so at runtime for
the current literal they are tautologies. That is the correct pattern here — they are regression
guards against a future edit that would silently hollow the test out. I verified their effect by
replicating the exact assertion logic against edge inputs (read-only: I did not edit the tests):

```
empty          -> BLOCKED by guard: the payload under test must be non-empty
ascii-only     -> BLOCKED by guard: payload must be multi-byte
stdin literal  -> guards PASS
stdout literal -> guards PASS
```

Measured literals:

- stdin payload: `len=32`, `nonempty=True`, `has>0x7F=True`
- stdout previous: `len=29`, `nonempty=True`, `has>0x7F=True`

Why this matters: if `payload` were `b""`, the byte-exact comparison would degenerate to
`b"" == b""` and the `input=b""` mutation would pass — i.e. the test would measure nothing. The
non-empty guard prevents exactly that. The multi-byte guard is what makes a text-mode / re-encoding
defect detectable: an ASCII-only expected value would round-trip even if the child's bytes were
decoded and re-encoded. The byte-exact comparison is only meaningful because the expected bytes are
non-empty and could differ from the actual — and under the mutation they do (`b''` vs the 32-byte
payload, real output in §1.1).

Verdict on vacuity: **not vacuous.** The guard is a meta-assertion, but it is a live one, and the
comparison beneath it is falsified by a real, reverted mutation.

---

## 3. No source change

```
$ git diff --numstat main...HEAD -- src/      # empty
$ git diff --name-only main...HEAD
docs/project/README.md
docs/project/core-dictation-pipeline/clipboard-injection-timeout/001-comment.md
docs/project/core-dictation-pipeline/pin-clipboard-real-seam-payload/001-comment.md
docs/project/core-dictation-pipeline/pin-clipboard-real-seam-payload/ticket.md
tests/test_clipboard_injector.py
```

The branch touches only tests and docs. `src/` is byte-identical to `HEAD` (and to `main`'s
pre-existing file) at rest:

```
src/ges2t/injectors/clipboard.py   9e0f4e9b35191622e09c5b970149924027068ec47e6f885e466b0a345f2bfed5
git show HEAD:src/ges2t/injectors/clipboard.py | sha256sum  → same
```

No source file changed at any checkpoint; the only deviations were the two deliberate, reverted
falsification mutations above.

---

## 4. No real clipboard, no leaked children

Every real `_run_command` call site in the whole test file (the new tests plus existing ones):

| line | argv | purpose |
|---|---|---|
| 529 | `destination` (`tee`/`true`) | deferred real-seam test |
| 554 | `("tee", str(target))` | deferred stdin round-trip |
| 619 | `("sh", "-c", <fork script>)` | forking-child stand-in |
| 636 | `("sh", "-c", "printf PREVIOUS")` | existing stdout capture |
| 652 | `("sh", "-c", "sleep 30")` | wedged-child timeout |
| 679 | `("sh", "-c", 'cat > "$1"', "sh", str(target))` | **new stdin test** |
| 710 | `("cat", str(source))` | **new stdout test** |

Only `sh`, `cat`, `tee`, `true`, `sleep` appear. `wl-copy`, `wl-paste` and `ydotool` exist on this
host (`/usr/bin/…`) but are **never** executed by these tests with data: the injector tests all inject
`FakeClipboardRunner`, and the new tests call `_run_command` directly with harmless stand-ins. No
keystroke, no microphone, no `ges2t run`. The user's clipboard was not touched.

Child-leak check: ran the test file five consecutive times and checked for survivors:

```
run 1 exit=0 93 passed in 0.92s
run 2 exit=0 93 passed in 0.88s
run 3 exit=0 93 passed in 0.92s
run 4 exit=0 93 passed in 0.86s
run 5 exit=0 93 passed in 0.88s
pgrep -a -f 'sleep 30|cat > .*immediate-stdin|previous-selection.bin|restore.bin|tee /tmp'
  -> no stray stand-in processes
```

(The only `pgrep` match was the checking shell command itself.) The forking-child test's `sleep 30`
is killed in its `finally` via `_kill_pid_file`; nothing survived.

---

## 5. No existing test weakened, renamed, reordered or deleted

```
$ git diff --numstat main...HEAD -- tests/
62	0	tests/test_clipboard_injector.py
$ git diff main...HEAD -- tests/ | grep -c "^-[^-]"
0
$ git diff main...HEAD -- tests/ | grep "^@@"
@@ -654,6 +654,68 @@ def test_real_immediate_runner_still_bounds_a_wedged_child(
```

One hunk, insertions only, zero deletions. `def test_` count goes 53 → 55. The name-sequence diff
against `main` shows the two additions in place and nothing else moved:

```
26a27,28
> test_real_immediate_runner_delivers_a_non_empty_payload_to_stdin
> test_real_immediate_runner_captures_a_non_empty_stdout_payload
```

No rename, no reorder, no deletion.

---

## 6. The doc correction is accurate

Ticket 36's `001-comment.md` quoted `git show HEAD:src/ges2t/injectors/clipboard.py` as producing the
pre-fix blob and recorded `deef264e…`. I re-ran every command in the correction note:

```
$ git show main:src/ges2t/injectors/clipboard.py | sha256sum
9e0f4e9b35191622e09c5b970149924027068ec47e6f885e466b0a345f2bfed5  -
$ git show HEAD:src/ges2t/injectors/clipboard.py | sha256sum
9e0f4e9b35191622e09c5b970149924027068ec47e6f885e466b0a345f2bfed5  -
$ git show 2e9fb05^:src/ges2t/injectors/clipboard.py | sha256sum
deef264efdf6bd370ddacd4944b4f2e1cc01d56fbebc8f349154b6f7f97f7bfb  -
$ git rev-parse 2e9fb05^
78141d22c63f164011239638d781a313f4e3fe04
$ git merge-base --is-ancestor ddeb278 main && echo ancestor
ancestor
$ git show -s --format='%h %s' ddeb278
ddeb278 Merge ticket 36: clipboard-injection-timeout — verdict pass (002-review.md). …
```

So:

- `HEAD:…` yields the **fixed** blob `9e0f4e9b…` — the original quoted command was indeed wrong.
- `main:…` **also** yields the fixed blob today, because `main` has merged ticket 36 as `ddeb278`
  (an ancestor of `main`; `main`'s tip is now `f1c072f`). The correction's statement that
  `git show main:…` now yields the fixed blob is correct.
- `2e9fb05^` resolves to `78141d2` and yields the recorded pre-fix blob `deef264e…`, so the stable
  reference the correction points at is right, and the recorded hash was right.

The reasoning in the correction note is honest and matches the repository: only the command was
mis-transcribed, the hash was not, and the note is added visibly rather than silently rewriting the
artifact. **Confirmed.**

---

## 7. Mechanicals (post-revert, caches cleared)

```
$ rm -rf .mypy_cache __pycache__ && find . -name __pycache__ -type d -prune -exec rm -rf {} +
$ timeout 300 uv run pytest -q
879 passed in 7.27s
FINAL_PYTEST_EXIT=0          # 877 + 2 new; process exits, no leaked-thread hang at 124

$ rm -rf .mypy_cache __pycache__ && find . -name __pycache__ -type d -prune -exec rm -rf {} +
$ uv run mypy src/ges2t tests
Success: no issues found in 51 source files
FINAL_MYPY_EXIT=0
```

Caches were cleared before every run so a reverted mutation could not look fixed. Expected 879; got
879; exit status 0 both times.

---

## 8. Could not verify

- **A real live Wayland clipboard write/paste.** Forbidden here and user-owned; the new tests
  deliberately use `sh`/`cat` stand-ins. The real `wl-copy`/`wl-paste`/`ydotool` path still needs the
  user's manual run (ticket 25's gate) — this ticket does not replace it.
- **Real `wl-copy` fork/stderr behaviour on a live compositor.** Not executed; relied on ticket 36's
  source audit.
- **The generic daemonising-child stdin-buffer hazard** (ticket 36 `002-review.md` §5). Explicitly
  out of scope; payloads here are tiny by design.
- **The guard firing in situ by editing the test.** I am read-only on tests, so I replicated the
  assertion logic against empty/ASCII/multi-byte inputs rather than temporarily editing the guard.
- **Ticket 36's original `877 passed` mutation-escape on `main`.** Not re-run (would need `main`'s
  test file overlaid); it is the documented premise, and the unique-failure result here is consistent
  with it.

## 9. Safe to merge

**Yes.** No `src/` change, tests are additions only, the stdin mutation is caught uniquely by the new
test and the stdout mutation by the new test plus an existing one, the guards are effective, the doc
correction is accurate, `git status` is clean, `src/`/`tests/` are byte-identical to `HEAD`, and the
suite (879) and mypy (0 errors) both pass with clean exit statuses. The one observation in §1.3 is a
redundancy note, not a defect.
