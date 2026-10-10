# 002 — review: the user documentation and setup guide (ticket 19)

- **Ticket:** [ticket.md](ticket.md)
- **Reviewed commit:** `7017233` (`feat/user-documentation-and-setup-guide`, diff `main...HEAD`)
- **Reviewer:** independent review agent; read-only on `README.md`, `docs/setup/` and `src/`
- **Worktree:** `.worktrees/user-documentation-and-setup-guide`

## Verdict

**PASS — safe to merge.** Every documented default, path, command, error message and
behaviour warning was checked against the code **in this worktree**, and every one matches.
All four mechanical gates are green. No source file changed (`git diff --name-only
main...HEAD -- src/` is empty).

Two **minor** documentation-accuracy issues were found (neither is a false statement about
how the software behaves, both are in the "presented as verbatim but isn't" / "not
genuinely unverifiable" class and are worth a small follow-up commit, not a block):

- **F-A** the "real report from the reference machine" in the README is **truncated**: it
  omits the KDE-bindings block that `ges2t doctor` always prints after the summary.
- **F-B** the ticket's own verification record marks a fresh `ges2t setup` download
  "(unverified)" with a reason that is false; the download **is** verifiable here and was
  verified during this review.

No false statement was found about a default, a command, a prerequisite, an error string,
or a user-visible behaviour.

## What was run (and the real result)

| Command | Result |
|---|---|
| `uv run ges2t --version` | `0.1.0` — matches README §4 |
| `uv run ges2t --help` | 9 subcommands + the KDE epilog — matches README §7/§11 |
| `uv run ges2t toggle --help` | the `KDE_SHORTCUT_HINT` epilog, verbatim as README §7 quotes |
| `uv run ges2t doctor` | 22 checks, 20 ok, 2 warn, 0 fail, exit 0 — the README's block is this output **minus** the trailing bindings section (F-A) |
| `uv run ges2t doctor --verify` | Model verified OK, 0 failures, exit 0 |
| `XDG_RUNTIME_DIR=<tmp> uv run ges2t status` | exit **2** + the exact "service is not running" sentence — matches README §11 |
| `uv run ges2t doctor --config <unknown-key.toml>` | `Config warnings WARN … unknown key 'typo'; engine.whistle.window_seconds = 9.0 is too large for the 1 s latency budget …` |
| `uv run ges2t doctor --config <bad-type.toml>` | `Config valid FAIL … expected str, got int (5)` + fix line |
| `XDG_DATA_HOME=<tmp> uv run ges2t setup` | **exit 0**, installed `…/models/whistle/2.0.0/whistle.cact` (16919407 B) and `…/engine/3.2.0/libneedle.so` (1581448 B); both sha256 match the pins exactly. A second run rewrote nothing (mtimes unchanged) — the README's idempotency claim holds |
| `XDG_DATA_HOME=<tmp> uv run ges2t models --verify` | both `verified`, exit 0 |
| `systemd-analyze --user verify docs/setup/ges2t.service` | **exit 0, no output** |
| `uv run python -c "import gi; … Gtk; print('ok')"` | `ok` |
| clipboard/streaming refusal, via `pipeline._validate_injection_backend` | raises the exact `PipelineError` + hint the README quotes |
| `timeout 300 uv run pytest -q` | **904 passed**, process exited |
| `rm -rf .mypy_cache __pycache__ && uv run mypy src/ges2t tests` | **Success: no issues found in 53 source files**, exit 0 |
| project link check over `docs/**/*.md` | **0 broken references** |
| equivalent link check over `README.md` + `docs/setup/autostart.md` | 89 relative links, **0 broken** |

No microphone was opened, no keystroke was emitted, the real clipboard was never touched, and
`ges2t run` was never executed.

## 1. Per-default accuracy

All from the code in this worktree, not from an ADR or the comment's memory.

| Documented default | README | Code that confirms it | Match |
|---|---|---|---|
| config path | `$XDG_CONFIG_HOME/ges2t/config.toml` → `~/.config/ges2t/config.toml` | `config.py:186` `default_config_path()` | ✓ |
| `transcriber.engine` | `"whistle"` | `config.py:60` `DEFAULT_ENGINE = KNOWN_ENGINES[0]`, `:57` | ✓ |
| `transcriber.model_path` | `""` | `config.py:107` | ✓ |
| `hotkey.mode` | `"ptt"` | `config.py:63` `("ptt", "toggle")`, `:114` | ✓ |
| `hotkey.key` | `""` | `config.py:115` | ✓ |
| `audio.device` | `""` | `config.py:69`,`:124` | ✓ |
| `injection.backend` | `"ydotool"` | `config.py:66`,`:129` | ✓ |
| `ui.tray` | `true` | `config.py:136` | ✓ |
| `ui.notifications` | `true` | `config.py:137` | ✓ |
| `ui.trailing_space` | `true` | `config.py:138` | ✓ |
| `streaming.enabled` | `true` | `config.py:145` | ✓ |
| `engine.whistle.language` | `""` | `whistle_config.py:52` | ✓ |
| `engine.whistle.window_seconds` | `5` (≤ 30) | `whistle_config.py:55`,`:65` | ✓ |
| `engine.whistle.chunk_seconds` | `1` (≥ 0.25, < window) | `whistle_config.py:58`,`:62` | ✓ |
| `injection.ydotool.layout` | `"auto"` | `layout.py` `AUTO_LAYOUT` / `YdotoolConfig.layout` | ✓ |
| `injection.ydotool.variant` | `""` | `layout.py` `YdotoolConfig.variant` | ✓ |
| `injection.ydotool.key_delay_ms` | `0` | `layout.py:491` `DEFAULT_KEY_DELAY_MS = 0` | ✓ |
| `injection.clipboard.paste_keys` | `["ctrl+shift+v"]` | `layout.py:502` `DEFAULT_PASTE_KEYS` | ✓ |
| minimum recording duration | 100 ms (plus a sustained-energy gate) | `whistle.py:97` `MIN_UTTERANCE_SECONDS = 0.1`, `:120` `MIN_SPEECH_FRAMES = 3` | ✓ |
| `window_seconds` warning knee | ~6 s | `whistle_config.py:70` `LATENCY_KNEE_SECONDS = 6.0` | ✓ |
| model pin | 16,919,407 B, sha256 `b6e02f04…dffeb` | `model_store.py:101`,`:102` | ✓ |
| engine pin | 1,581,448 B, sha256 `0772130a…4fd030`, v3.2.0 | `model_store.py:82`,`:112`,`:113` | ✓ |
| cactus-needle pin | 3.1.3 | `model_store.py:94`; `pyproject.toml:8` | ✓ |
| engine data root | `$XDG_DATA_HOME/ges2t` | `model_store.py` `default_data_dir()` | ✓ |
| artifact install paths | `models/whistle/2.0.0/whistle.cact`, `engine/3.2.0/libneedle.so` | `model_store.py` `model_path()`/`engine_path()`; **reproduced live** with an isolated `XDG_DATA_HOME` | ✓ |
| exit codes | 0/1/2/3/130 | `cli.py` `EXIT_*` constants | ✓ |
| socket path & mode | `$XDG_RUNTIME_DIR/ges2t.sock`, `0600`, newline-delimited JSON | `socket_proto.py:78` `SOCKET_MODE = 0o600`; module docstring | ✓ |
| notify suppression | 5 s | `notify.py:64` `SUPPRESSION_WINDOW_SECONDS = 5.0` | ✓ |
| clipboard command timeout | 5 s | `clipboard.py:132` `CLIPBOARD_COMMAND_TIMEOUT_SECONDS = 5.0` | ✓ |

The `[section] key` names and shapes in the README's config example are **exactly** the sets
the code accepts: generic `transcriber{engine,model_path}`, `hotkey{mode,key}`,
`audio{device}`, `injection{backend}`, `ui{tray,notifications,trailing_space}`,
`streaming{enabled}` (`config.py:85-90`); `[engine.whistle]{language,window_seconds,chunk_seconds}`
(`whistle_config.py:73`); `[injection.ydotool]{layout,variant,key_delay_ms}` and
`[injection.clipboard]{paste_keys}` (`layout.py:505`,`:508`). No extra or missing key.

## 2. Per-command accuracy

| Documented command | Status |
|---|---|
| `systemctl --user enable --now ydotool.service` | **spelled correctly** — the unit is `ydotool.service` (`pacman -Ql ydotool` → `/usr/lib/systemd/user/ydotool.service`), not `ydotoold`; README §5 says so explicitly. Not run (already active). |
| `systemctl --user status/is-active ydotool.service` | run; `active` |
| `sudo usermod -aG input "$USER"` + re-login | valid; not run (would change groups) |
| `ls -l /dev/uinput` / `test -w /dev/uinput` | run; `crw-rw----+ root input`, writable |
| `uv venv --system-site-packages --python /usr/bin/python3` | flag exists and has **no** `UV_*`/config surface (`uv venv --help`), so the README's "not optional, this flag is the only surface" is correct |
| `uv run python -c "import gi; …"` | run; prints `ok` |
| `uv run ges2t --version` | run; `0.1.0` |
| `uv run ges2t setup` | run in isolation; exit 0, correct paths/sizes/hashes |
| `uv run ges2t models` / `--verify` | run; `present` / `verified` |
| `uv run ges2t doctor` / `--verify` | run; 22 checks, 0 failures |
| `uv run ges2t status` | run; `idle`; with a fake runtime dir, exit 2 + the exact sentence |
| `uv run ges2t run` | correct; **not run** (forbidden, and the user's service owns the socket) |
| `.venv/bin/ges2t toggle` (KDE binding) | correct form; `.venv/bin/ges2t` exists and is executable; bare `ges2t` is **not** on `PATH` (see finding 1) |
| `uv run mypy src/ges2t tests` | run; exit 0 (the amended scope, ADR 0019 — see finding 2) |
| all `systemctl --user … ges2t.service`, `journalctl --user -u ges2t.service …`, `cp`/`$EDITOR`/`daemon-reload` in `autostart.md` | valid systemd/user-journal syntax |
| `docs/setup/ges2t.service` | `systemd-analyze --user verify` exits 0; `After`/`PartOf`/`WantedBy=graphical-session.target` all resolve; `graphical-session.target` is real and active, and is pulled in by `plasma-workspace-wayland.target`, so the autostart claim holds on this machine (confirmed `systemctl --user list-dependencies --reverse graphical-session.target`) |

## 3. Behaviour-warning audit (the load-bearing part)

| Warning the user must see | README | Code: doc says it **and** code does it |
|---|---|---|
| incremental typing is the default with streaming on; **no opt-out keeps streaming** | §9, §10, §13 | `pipeline._streaming_is_selected` = `streaming.enabled and transcriber.supports_streaming`; the only streaming key anywhere is `[streaming].enabled` (`config.py:90`); no incremental-typing key exists in any adapter (`whistle_config.py:73`, `layout.py:505/508`) — so turning it off turns streaming off. ✓ |
| `injection.backend = "clipboard"` + streaming is **refused at startup** | §10 (with the quoted error + hint) | `pipeline._validate_injection_backend` is the **first** call in `_startup`; raises `PipelineError` with exactly the quoted message/hint. Reproduced via the function directly. ✓ |
| default paste chord is **`ctrl+shift+v`**; an app that leaves it unbound pastes nothing | §10, §11 | `layout.py:502`; `ClipboardPasteInjector` emits every configured chord. ✓ |
| a confidently-wrong word can land **mid-dictation**; the tool never auto-corrects | §9, §10, §11, §13 | ADR 0011 amendment + `CommittedTyper` types the committed prefix; recovery = stop → delete → continue. ✓ |
| `key_delay_ms` defaults to **0** (a measurement, not an oversight) | §10 | `layout.py:491`; the 125-char / 47.9 ms vs 1.6 s vs 3.1 s comment is verbatim in the code. ✓ |
| silence/short recording types nothing | §9, §11 | `MIN_UTTERANCE_SECONDS`, `_has_speech`, `WhistleTranscriber.transcribe` returns `text=""` without calling the engine. ✓ |
| text the layout cannot type is skipped with a warning on stderr | §10, §11 | `ydotool.py:436` `warnings.warn(..., YdotoolUnreachableWarning)`; the class subclasses `UserWarning` (`ydotool.py:166`), so it prints to stderr by default. ✓ |
| no clipboard **fallback** (configured alternative only) | §5, §10 | `build_injector` picks by `injection.backend`; no fallback path. ✓ |

## 4. Honesty of the `(unverified)` markings

| Marking | Genuinely unverifiable here? |
|---|---|
| Plasma 6.7.5 *Shortcuts → Add → Command or URL* dialog wording | **yes** — GUI, user-owned, consequential to drive |
| whether Plasma can fire separate key-press/key-release commands | **yes** — compositor property, no GUI automation in scope, no prior verification in-repo |
| the systemd unit actually running `ges2t` (+ its tray) | **yes** — enabling it would fight the user's running service (forbidden) |
| tray overflow-area behaviour | **yes (user-facing Plasma guidance)** — honestly labelled as guidance, not a measurement; the actual invisible-tray cause this project hit (missing `--system-site-packages`) is documented correctly |
| notification popups | **yes** — needs a failing dictation; only the argv/suppression contract was readable |
| non-KDE compositors / non-Arch | **yes** — per ADR 0002 |
| **a fresh `ges2t setup` download** | **NO — this marking is not honest (F-B).** `XDG_DATA_HOME` isolation lets `ges2t setup` run without touching the user's verified artifacts. This review ran exactly that: exit 0, correct paths, correct sizes, both sha256 matching the pins. The `001-comment.md` justification ("forcing a re-download would have required deleting the user's verified artifacts", `001-comment.md:367-369`) is therefore false, and the README's parenthetical "a fresh download" (`README.md:31`) is over-conservative. This is a verification-record inaccuracy, not a false doc claim about the software — the README asserts nothing unverified about `setup`. |

Nothing asserted as verified turned out to be unverified: every executed claim in the comment
(venv, `gi`, `doctor`, `doctor --verify`, `models`, the failure paths, `systemd-analyze`,
`show-environment`, the start-rate limit, pytest = 904, mypy = 0, 0 broken links) was
reproduced and matched.

## 5. The systemd unit

`docs/setup/ges2t.service` is a valid user unit (`systemd-analyze --user verify` → exit 0, no
diagnostics). Its dependency claims are honest and accurate: `After=`/`PartOf=`/`WantedBy=
graphical-session.target` all resolve, and that target exists and is reached from
`plasma-workspace-wayland.target` on this machine. "Documentation only / adjust the two paths"
is stated in the unit header and in `autostart.md`. The honesty statement ("did not enable or
run the unit itself, so the unit running under systemd is unverified") is present in
`autostart.md:82-83` and mirrored in README §13. **Not run**, correctly.

## 6. Independent check of the implementer's findings

1. **`ges2t toggle` as printed by `doctor`/`cli` is not literally bindable — CORRECT.**
   `which ges2t` finds nothing; `.venv/bin` is not on the session `PATH`; `uv sync` installs
   the console script to `.venv/bin/ges2t` (`ls -l` confirms). The bare `ges2t toggle` strings
   in `doctor.py` (`HOTKEY_FIX`, `KDE_BINDING_SECTION`) and `cli.py` (`KDE_SHORTCUT_HINT`) are
   therefore misleading in form, exactly as reported. The README's **absolute-path** advice
   works: `/home/matee/Workspace/Personal/ges2t/.venv/bin/ges2t` exists and is executable. The
   finding is right and worth a follow-up (print the absolute script path). One small
   consequence the finding did not mention: README §7 tells the reader they "can copy them
   from there", but `doctor` prints the bare, non-working form — see F-A-adjacent note below.
2. **ticket text uses `mypy src/ges2t`, the project rule is `src/ges2t tests` — CORRECT.**
   `ticket.md` last checkbox vs `docs/project/README.md` and ADR 0019. Both pass today; the
   ticket text is stale, the README uses the amended command.
3. **`pystray` 0.19.5 `SyntaxWarning` on Python 3.14 — CORRECT and reproduced.** Forcing a
   fresh compile (`PYTHONPYCACHEPREFIX=<tmp>`) prints
   `pystray/_util/gtk.py:53: SyntaxWarning: 'return' in a 'finally' block`. It does not appear
   on the second import (bytecode cached), which is why it is not in the README's report. This
   is a comment-level observation only; the README does not claim it.
4. **ticket 31 board/code mismatch — not re-litigated** (per instructions). No *other*
   board/state inconsistency of that kind was found: ticket 19 is `in review` in both
   `ticket.md` and the board, the branch name matches, and the docs-only `docs/project/README.md`
   diff is consistent. One pre-existing cosmetic nit only: the board says "All 20 decision
   records" (`docs/project/README.md:117`) while `docs/adr/` contains **21** files (0000–0020).
   Unrelated to this branch.
5. **clipboard no-fallback + streaming refusal are load-bearing — CORRECT.** Both are stated
   loudly in the README and both are enforced in `pipeline.py` (`build_injector` has no
   fallback; `_validate_injection_backend` runs before artifacts are touched).

## 7. Findings

**F-A (minor, documentation accuracy).** `README.md:526-557` presents a code block as "A real
report from the reference machine" but omits the KDE-bindings block that `render_report`
**always** appends after the summary (`doctor.py` `KDE_BINDING_SECTION`, printed on every run —
verified line-for-line: the block is byte-identical to the real output apart from the six
missing trailing lines). Adjacent to this, `README.md:252-253` says
"`ges2t doctor` prints the same recommended commands at the end of every report, so you can
copy them from there", while finding 1 correctly establishes that the bindable form is the
**absolute** `.venv/bin/ges2t` path — so "copy them from there" gives the non-working bare
form. Suggested fix: either label the sample as abbreviated, or append the six lines; and say
"the report names the same subcommands; substitute your absolute path".

**F-B (minor, verification record).** `001-comment.md:367-370` and `README.md:31` treat a
fresh `ges2t setup` download as unverifiable, citing the need to delete the user's artifacts.
That is false: an isolated `XDG_DATA_HOME` verifies it with no side effects, and this review
did so (exit 0; both artifacts installed at the documented paths with pinned sizes and
matching sha256; idempotent re-run rewrote nothing). The README text should move "a fresh
download" out of the unverifiable list, and the comment's item 4 should be corrected.

**F-C (cosmetic, pre-existing, not this branch).** Board says 20 decision records; there are 21.

No finding is a false statement about how ges2t behaves, its defaults, its commands, its error
strings, or its prerequisites.

## 8. Could not verify (from this review, explicitly)

- **The Plasma GUI steps** (dialog wording, press/hold support) — the README marks these
  `(unverified)`; agreed.
- **The systemd unit actually launching `ges2t` and showing a tray** — enabling it would fight
  the user's running service; only `systemd-analyze verify` and the environment were checked.
- **A real dictation** (microphone, keystrokes, clipboard) — forbidden to this review by the
  task's hard rules. The incremental-typing behaviour is evidenced by the user's live run in
  `incremental-typing/003-comment.md`, which the README cites.
- **Non-KDE compositors, non-Arch distributions** — untested, as the README states.
- **Tray overflow-area behaviour and notification popups** — not measured (user-facing
  guidance / needs a failure), as the comment states.

## 9. Safe to merge?

**Yes.** The default/config/command/behaviour surface is accurate and reproducible against the
code in this worktree; every mechanical gate passes (904 pytest passing and exiting, mypy 0 on
`src/ges2t tests`, 0 broken links in `docs/` and in the README's own links, valid systemd
unit); no source changed; `git status` is clean. F-A and F-B are small, non-blocking honesty
improvements that a follow-up commit (or the merge commit's fixup) can make. Nothing in the
documentation would send a fresh user down a wrong path toward a broken install.
