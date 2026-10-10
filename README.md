# ges2t — *good enough speech 2 text*

## 1. What ges2t is

ges2t is a minimal, fully offline dictation tool for Linux. You press a key, speak, and the
text is typed into whatever window currently has focus. Transcription runs entirely on your
machine with Cactus Compute's 16 MB **Whistle** model on the **Needle 3** engine: there is no
cloud, no account, and no text leaves the computer. A long-lived background service
(`ges2t run`) owns the warm model, the microphone, the tray icon and a Unix socket; the key
you bind in KDE sends one short command to that service over the socket.

## 2. Status

**Early personal-MVP. Not packaged, not distributed, no install target.**

Scope and non-goals are fixed by [ADR 0000](docs/adr/0000-project-scope-and-non-goals.md):
there is no AUR package, no Flatpak, no AppImage, no installer, no Windows/macOS support and
no CI. This is deliberate — v1 exists to be a daily driver on the author's own Arch + KDE
Plasma 6 machine. Everything below is a **manual** step, and that is why this README is
load-bearing rather than decorative: it is the installation path.

The only machine this project has been verified on is:

- Arch Linux, `x86_64`, Python 3.14.7 (`/usr/bin/python3`), `uv` 0.12.23;
- KDE Plasma **6.7.5** on Wayland (`XDG_SESSION_TYPE=wayland`, `XDG_CURRENT_DESKTOP=KDE`);
- PipeWire 1.6.8, `ydotoold` running as the user unit `ydotool.service`.

Every command, path and default in this document was read out of the code in this repository
and, where it can be run safely, actually run on that machine. Steps that could **not** be
executed by whoever wrote this document (anything requiring the Plasma settings GUI or a real
microphone) are marked **(unverified)** and the verification gap is recorded in
[`docs/project/live-dictation-ux/user-documentation-and-setup-guide/001-comment.md`](docs/project/live-dictation-ux/user-documentation-and-setup-guide/001-comment.md).

## 3. Requirements

- **Arch Linux on `x86_64`.** Only `linux-x86_64` has a pinned engine artifact
  ([ADR 0017](docs/adr/0017-model-acquisition-via-setup.md)); `ges2t setup` fails on anything
  else instead of downloading a wrong-architecture binary.
- **Wayland.** The reference compositor is **KDE Plasma 6 on Wayland**
  ([ADR 0002](docs/adr/0002-wayland-kde-target.md)). X11 is not supported, and other Wayland
  compositors (sway, Hyprland, GNOME) are **untested and unverified**. Plasma 6.7.5 is the
  version the acceptance tests ran on.
- **PipeWire**, for audio capture through `pw-record`
  ([ADR 0008](docs/adr/0008-audio-capture-via-pw-record.md)).
- **Python 3.12+** (`requires-python = ">=3.12"`; developed on 3.14.7) and
  **[uv](https://docs.astral.sh/uv/)** for the environment and the lockfile.
- **`libxkbcommon` and its XKB data**, used to turn text into keycodes for the active
  keyboard layout ([ADR 0020](docs/adr/0020-layout-aware-keycode-mapping.md)). KWin itself
  needs them, so they are already present on any KDE system; `ges2t doctor` checks them.

System packages:

```bash
sudo pacman -S ydotool python-gobject gtk3 libappindicator libxkbcommon xkeyboard-config pipewire
```

| Package | Why | Version verified here |
|---|---|---|
| `ydotool` | types the text through a virtual input device ([ADR 0003](docs/adr/0003-text-injection-via-ydotool.md)) | 1.0.4-2 |
| `python-gobject` | provides `gi`, which the tray's AppIndicator backend needs ([ADR 0013](docs/adr/0013-tray-via-pystray-appindicator.md)) | 3.56.3-1 |
| `gtk3` | the GTK3 typelibs the tray needs | 1:3.24.52-1 |
| `libappindicator` | the `AppIndicator3` typelib; without it `pystray` silently picks an invisible backend on Plasma 6 | 12.10.1-2 |
| `libxkbcommon` | text→keycode mapping ([ADR 0020](docs/adr/0020-layout-aware-keycode-mapping.md)) | 1.13.2-1 |
| `xkeyboard-config` | the XKB layout data `libxkbcommon` compiles | 2.48-1 |
| `pipewire` | provides `pw-record` and the session | 1:1.6.8-1 |


`wl-clipboard` (verified: 1:2.3.0-1) is needed **only** if you choose the clipboard injection
backend (`injection.backend = "clipboard"`); the default `ydotool` backend does not use it.

## 4. Install

```bash
git clone <this repository> ges2t
cd ges2t

# Create the virtualenv ONCE, explicitly, with system site packages.
uv venv --system-site-packages --python /usr/bin/python3

# Then install/update the environment from the lockfile.
uv sync
```

**The `--system-site-packages` flag is not optional.** `uv` has no `[tool.uv]` setting that
persists it (`uv venv`'s flag is the only surface, and
[uv#5737](https://github.com/astral-sh/uv/issues/5737) is still open), so a plain `uv sync`
on a machine with no `.venv` creates a venv **without** the system `gi` module. `pystray` then
silently selects its XEmbed backend, Plasma 6 has no XEmbed systray, and you get a
working-looking service with an **invisible tray icon**
([ADR 0013](docs/adr/0013-tray-via-pystray-appindicator.md)).

Check it, and repair it if needed:

```bash
# Expected output: ok
uv run python -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk; print('ok')"

# If the line above fails with ModuleNotFoundError: No module named 'gi', re-create the venv:
uv venv --clear --system-site-packages --python /usr/bin/python3 && uv sync
```

`uv sync` reuses an existing `.venv` whose interpreter satisfies `requires-python`; it does
not recreate it, so this setting survives `uv sync`, `uv run` and `uv lock`.

The install creates a console script at `.venv/bin/ges2t`. That **absolute path** is what you
bind to a key in section 7 — not `uv run`.

Development commands (the same environment):

```bash
uv run ges2t --version    # prints 0.1.0
uv run pytest             # the test suite (local only; there is no CI)
uv run mypy src/ges2t tests
```

The type check is part of the project's quality bar
([ADR 0019](docs/adr/0019-code-style-typing-and-interfaces.md)); it is not needed to use
ges2t.

## 5. Prepare the input permission

ges2t types by synthesising key events through **`ydotoold`** and `/dev/uinput`. This is not
optional: **without it, ges2t records and transcribes but types nothing**, and on Wayland
there is no other way to put text in another window
([ADR 0003](docs/adr/0003-text-injection-via-ydotool.md)).

```bash
# 1. Enable and start the daemon. The systemd unit is 'ydotool.service',
#    NOT 'ydotoold' — ydotoold is the daemon binary the unit runs.
systemctl --user enable --now ydotool.service

# 2. Confirm it is running.
systemctl --user status ydotool.service

# 3. Grant /dev/uinput access: add your user to the 'input' group,
sudo usermod -aG input "$USER"

# 4. then LOG OUT and BACK IN so the new group membership takes effect.
```

The device looks like this on the reference machine — `crw-rw---- root:input` plus an ACL, so
access depends on group membership (or ydotool's udev rule):

```console
$ ls -l /dev/uinput
crw-rw----+ 1 root input 10, 223 Oct  9 14:08 /dev/uinput
```

After logging back in, both of these must pass before you go on:

```bash
systemctl --user is-active ydotool.service   # expected: active
test -w /dev/uinput && echo "uinput writable" # expected: uinput writable
```

`ges2t doctor` checks exactly these two things (as `ydotoold` and `/dev/uinput access`) and
prints the fix for each (section 11).

The daemon listens on a Unix datagram socket at `$XDG_RUNTIME_DIR/.ydotool_socket`
(`/run/user/1000/.ydotool_socket` on the reference machine). `ydotool` itself honours the
`YDOTOOL_SOCKET` environment variable, so a daemon you started by hand on a different socket
is recognised too.

There is **no clipboard fallback**. The clipboard backend is a *configured alternative*, not
an automatic fallback ([ADR 0003](docs/adr/0003-text-injection-via-ydotool.md)); ges2t never
silently switches mechanism.

## 6. Get the model

```bash
uv run ges2t setup
```

This is the **only** ges2t command that uses the network
([ADR 0017](docs/adr/0017-model-acquisition-via-setup.md),
[ADR 0018](docs/adr/0018-no-runtime-network.md)). Everything afterwards runs offline. It:

1. downloads the pinned Whistle weights `whistle.cact`
   (16,919,407 bytes, sha256 `b6e02f04…dffeb`) from
   `https://huggingface.co/Cactus-Compute/whistle/resolve/main/whistle.cact`;
2. extracts the pinned Needle 3 engine `libneedle.so`
   (1,581,448 bytes, sha256 `0772130a…4fd030`, engine version 3.2.0) from the pinned
   `cactus_needle-3.2.0-…manylinux2014_x86_64.whl`;
3. verifies each file's **sha256 and byte size** against the pins, then installs it under
   `$XDG_DATA_HOME/ges2t/` (default `~/.local/share/ges2t/`):

   ```
   ~/.local/share/ges2t/models/whistle/2.0.0/whistle.cact
   ~/.local/share/ges2t/engine/3.2.0/libneedle.so
   ```

It is **idempotent**: a valid artifact already present is left alone and nothing is
downloaded. Nothing is written to `~/.cache/cactus-needle` — ges2t owns its artifacts, not
the upstream package's cache. Downloads are atomic (temp file, hash, rename), so an
interrupted setup cannot leave a half-written model that later passes a size check.

Check what is installed, and optionally hash it:

```bash
$ uv run ges2t models
artifact       version  status      path
whistle.cact   2.0.0    present     /home/matee/.local/share/ges2t/models/whistle/2.0.0/whistle.cact
libneedle.so   3.2.0    present     /home/matee/.local/share/ges2t/engine/3.2.0/libneedle.so

$ uv run ges2t models --verify
artifact       version  status      path
whistle.cact   2.0.0    verified    /home/matee/.local/share/ges2t/models/whistle/2.0.0/whistle.cact
libneedle.so   3.2.0    verified    /home/matee/.local/share/ges2t/engine/3.2.0/libneedle.so
```

`ges2t run` checks for these two files at startup but **never downloads**; if one is missing
it tells you to run `ges2t setup`
([ADR 0017](docs/adr/0017-model-acquisition-via-setup.md),
[ADR 0018](docs/adr/0018-no-runtime-network.md)).

## 7. Bind a key (KDE)

ges2t never grabs a hotkey itself — it has no input permissions and never observes your
keystrokes ([ADR 0004](docs/adr/0004-global-hotkey-via-cli-and-service.md)). **KDE runs the
command, and the key belongs to KDE.** A bound key with no compositor binding does nothing,
and the `hotkey.mode`/`hotkey.key` config keys are documentation only
([ADR 0009](docs/adr/0009-configuration-toml.md)).

In **System Settings → Shortcuts → Add → Command or URL**, add one or more of:

| Command to bind | What it does |
|---|---|
| **`…/.venv/bin/ges2t toggle`** | start recording if idle, or stop and transcribe if recording — **the recommended binding** |
| `…/.venv/bin/ges2t ptt-start` | press-to-talk: start recording |
| `…/.venv/bin/ges2t ptt-stop` | press-to-talk: stop and transcribe |
| `…/.venv/bin/ges2t cancel` | abort a recording without transcribing |

Use the **absolute path** to the console script created by `uv sync`, for example:

```
/home/matee/Workspace/Personal/ges2t/.venv/bin/ges2t toggle
```

`uv run ges2t toggle` is **not** a reliable shortcut command: `uv run` needs the project's
working directory and the shortcut does not set one. Bind `.venv/bin/ges2t` directly. (If you
clone the repository somewhere else, use that copy's absolute path; a git worktree has its
own `.venv`.)

**True press-and-hold (`ptt-start`/`ptt-stop`) only works if your compositor can fire a
separate command on key press and on key release.** That is a property of the shortcut system,
not of ges2t, and it was **not verified on Plasma 6.7.5 by this documentation**
**(unverified)**. `toggle` needs no such feature and is always safe: it is the guaranteed
mode, so when in doubt bind `toggle`. If you try `ptt-start`/`ptt-stop` and the second
invocation is never sent, the recording never stops — bind `toggle` instead.

`ges2t doctor` names the same subcommands at the end of every report, so you can use that
list directly: it resolves the console script **it is itself running as** and prints every
command by that **absolute path** (`…/.venv/bin/ges2t toggle`), which is exactly what a
shortcut needs. When it cannot resolve the path to an existing file it falls back to the bare
`ges2t …` form, which a shortcut can run only on a system install (where the script is on
`PATH`); substitute your own absolute `.venv/bin/ges2t` path as above in that case.

## 8. Run it

Run the service in a terminal:

```bash
uv run ges2t run
```

This owns the warm model, the microphone, the tray/status UI and the Unix socket at
`$XDG_RUNTIME_DIR/ges2t.sock` ([ADR 0015](docs/adr/0015-single-service-process-architecture.md)).
The model is loaded once at startup and stays warm; startup never touches the network. Exit
with `Ctrl+C` (or the tray's **Quit**).

Only one service may run per session. A second `ges2t run` refuses with
`a ges2t service is already listening on …; only one may run per session`.

**Optional: autostart.** [`docs/setup/ges2t.service`](docs/setup/ges2t.service) is a
ready-to-copy systemd **user** unit and [`docs/setup/autostart.md`](docs/setup/autostart.md)
explains how to enable it, check it and read its logs. Nothing installs it for you — it is
documentation only ([ADR 0015](docs/adr/0015-single-service-process-architecture.md)).
The unit's `ExecStart` and `WorkingDirectory` are paths you must edit for your own checkout.

While the service runs, the CLI commands are thin socket clients: they need no configuration,
load no engine, and complete in milliseconds — which is what makes them safe to bind to a
key ([ADR 0016](docs/adr/0016-cli-surface.md)).

## 9. Use it

| Command | Flow |
|---|---|
| `.venv/bin/ges2t toggle` | press once to start recording, press again to stop and transcribe — the default binding |
| `.venv/bin/ges2t ptt-start` / `ptt-stop` | hold-to-talk, if your compositor supports it (section 7) |
| `.venv/bin/ges2t cancel` | abort the recording; nothing is transcribed and nothing more is typed |
| `.venv/bin/ges2t status` | print the current state: `idle`, `recording`, `transcribing` or `error` |

The tray icon (when `ui.tray = true`, the default) shows the state as a colour and offers
**Toggle dictation** (the default menu action), **Cancel** and **Quit**.

Three behaviours worth knowing before you start:

- **Text is typed as you speak.** Streaming is the default transcription path
  ([ADR 0011](docs/adr/0011-streaming-sliding-window.md) and its amendment), and the words
  the engine has *committed* are typed **while you are still speaking** rather than at the
  end — this is incremental typing, verified live. See section 10 for the one switch, and for
  the important consequence: a word the engine settles on can be wrong, and it is already in
  your document. The recovery is **stop → delete the wrong text → continue**; ges2t never
  auto-corrects.
- **A trailing space is appended per utterance.** With `ui.trailing_space = true` (the
  default) the injected text is the transcript plus exactly one ASCII space, so two
  dictations in a row do not run together
  ([ADR 0012](docs/adr/0012-verbatim-output-and-trailing-space.md)). Otherwise the engine's
  text is injected verbatim: no punctuation insertion, no casing fixes, no filler removal.
- **Silent input types nothing.** A recording shorter than 100 ms or with no sustained speech
  energy produces no text at all — not even a space. The engine hallucinates phrases such as
  "Thank you." on key clicks, DC offset and mains hum, so silence is gated deliberately
  ([ADR 0012](docs/adr/0012-verbatim-output-and-trailing-space.md)).

On failure (dead engine, refused injection) the service moves to `error`, shows it in the
tray, and `notify-send` raises a desktop notification naming the problem and its fix
(`ui.notifications = true`, the default). Identical messages are suppressed for 5 seconds so
a hammered key does not stack popups. Nothing is reachable *only* through the tray: every
control also exists as a CLI command ([ADR 0013](docs/adr/0013-tray-via-pystray-appindicator.md)).

## 10. Configuration

The configuration file is **`$XDG_CONFIG_HOME/ges2t/config.toml`**, default
**`~/.config/ges2t/config.toml`** ([ADR 0009](docs/adr/0009-configuration-toml.md)).
`--config <path>` overrides the location for every subcommand that reads configuration.

- **A missing file is not an error** — all defaults apply and `ges2t run` works out of the
  box. ges2t never creates or edits this file; you do.
- **Invalid TOML, an unknown engine, or a wrong-typed value is a hard error** naming the file
  and the key. ges2t never silently falls back to defaults.
- **Unknown keys are tolerated but surfaced**: `ges2t doctor` reports them as warnings so a
  typo is visible without being fatal.

### The two layers

The schema is split into a **generic layer** that ges2t understands regardless of which
engine is selected, and **engine-specific blocks** that only the selected engine's adapter
interprets. This split is deliberate ([ADR 0009](docs/adr/0009-configuration-toml.md)),
and it matters: `window_seconds` is **not** a generic key — it belongs to `[engine.whistle]`.

```toml
# ---- generic: understood by ges2t regardless of which engine is selected ----

[transcriber]
engine     = "whistle"   # ENGINE SELECTOR: which [engine.<name>] block is active
model_path = ""          # "" = the ges2t-managed artifact from 'ges2t setup'

[hotkey]
mode = "ptt"             # "ptt" | "toggle" — DOCUMENTATION ONLY (the binding is in KDE)
key  = ""                # DOCUMENTATION ONLY (the binding is in KDE)

[audio]
device = ""              # PipeWire target (node name or id); "" = system default

[injection]
backend = "ydotool"      # "ydotool" | "clipboard"

[ui]
tray           = true
notifications  = true
trailing_space = true    # append one ASCII space per utterance (ADR 0012)

[streaming]
enabled = true           # app-level: use the streaming path when the engine supports it

# ---- engine-specific: owned and validated by the selected engine's adapter ----

[engine.whistle]         # active only while transcriber.engine == "whistle"
language       = ""      # "" = engine auto-detect
window_seconds = 5       # streaming commit-window cap, seconds (ADR 0011)
chunk_seconds  = 1       # streaming feed granularity, seconds (ADR 0011)

# ---- injector-specific: owned by the selected injection backend ----

[injection.ydotool]      # active only while injection.backend == "ydotool"
layout       = "auto"    # "auto" = the live compositor keymap (ADR 0020)
variant      = ""        # only meaningful with an explicit layout
key_delay_ms = 0         # delay between emitted key events

[injection.clipboard]    # active only while injection.backend == "clipboard"
paste_keys = ["ctrl+shift+v"]
```

### Defaults, exactly as they are in the code

| Key | Default | Meaning |
|---|---|---|
| `transcriber.engine` | `"whistle"` | engine selector; `whistle` is the only engine in v1 |
| `transcriber.model_path` | `""` | `""` = the pinned artifact under `$XDG_DATA_HOME/ges2t` |
| `hotkey.mode` | `"ptt"` | accepted values `"ptt"` and `"toggle"`; **does not bind anything** |
| `hotkey.key` | `""` | documentation only |
| `audio.device` | `""` | `""` = system default input; a value is a PipeWire target |
| `injection.backend` | `"ydotool"` | `"ydotool"` or `"clipboard"` |
| `ui.tray` | `true` | show the tray icon |
| `ui.notifications` | `true` | `notify-send` on failure |
| `ui.trailing_space` | `true` | one ASCII space appended per utterance |
| `streaming.enabled` | `true` | use streaming when the engine declares support |
| `engine.whistle.language` | `""` | `""` = auto-detect; any string is passed to the engine |
| `engine.whistle.window_seconds` | `5` | streaming commit window, must be ≤ 30 |
| `engine.whistle.chunk_seconds` | `1` | feed granularity, must be ≥ 0.25 and < `window_seconds` |
| `injection.ydotool.layout` | `"auto"` | `"auto"` = live compositor keymap; otherwise e.g. `"hu"` |
| `injection.ydotool.variant` | `""` | used with an explicit `layout` |
| `injection.ydotool.key_delay_ms` | `0` | inter-key delay passed to `ydotool key` |
| `injection.clipboard.paste_keys` | `["ctrl+shift+v"]` | the paste chord(s) to send |

The **window and chunk sizes live in `[engine.whistle]`**, not in `[streaming]`, because they
are properties of *this* engine's re-transcription behaviour and mean nothing to an engine
that streams properly. `[streaming].enabled` is the only streaming key the app itself
understands.

### Behaviours the configuration controls, and their traps

**Incremental typing is the default.** With `streaming.enabled = true` (default), the words
the engine commits are **typed as you speak**; the trailing space is still applied once, at
the end of the utterance. There is currently **no opt-out that keeps streaming**: the only
switch is `streaming.enabled = false`, which also disables streaming and drops you back to
the batch path (record the whole utterance, transcribe once, type once).

That matters because of what the ADR 0011 amendment accepted: **the two-pass commit rule
protects against revision, not against a stable hallucination**, so a confidently-wrong word
can be committed and therefore typed *mid-dictation*. Nothing corrects it. The recovery is
**stop the recording → delete the wrong text → continue**; the stop path is deliberately
instant. On the batch path a bad word is at least visible before it is typed, because nothing
is typed until you stop.

**The clipboard backend cannot be combined with streaming.** `injection.backend =
"clipboard"` with `streaming.enabled = true` (the default) is refused at startup with a loud,
actionable error:

```
ges2t: injection.backend = "clipboard" cannot be combined with streaming.enabled = true:
incremental typing would rewrite the clipboard on every commit.
  hint: set injection.backend = "ydotool" to type as you speak, or set
        streaming.enabled = false to keep the clipboard backend
```

Incremental paste would have to rewrite your clipboard on every commit, so the combination is
an error rather than a silent downgrade. To use the clipboard backend, set
`streaming.enabled = false`:

```toml
[streaming]
enabled = false

[injection]
backend = "clipboard"

[injection.clipboard]
paste_keys = ["ctrl+shift+v"]
```

The clipboard backend is for text the active keyboard layout cannot type: it writes the
transcript with `wl-copy`, sends one raw-keycode paste chord, and restores the previous
clipboard afterwards. Its default chord is **`ctrl+shift+v`**, because that is the only chord
with manual-verification evidence behind it and it pastes in terminals, where `ctrl+v` does
not. **An application that leaves `ctrl+shift+v` unbound pastes nothing** — set
`paste_keys = ["ctrl+v"]` there (in most GUI toolkits `ctrl+shift+v` means *paste as plain
text*, which for dictation is arguably the better variant, but it is a different binding, not
a superset). `paste_keys` is a list because the injector sends **every** chord in it; do not
list two chords to "try both", or you paste twice.

**`key_delay_ms` defaults to `0`, and that is a measurement, not an oversight.** The batched
`ydotool key` path was measured on a 125-character phrase: `0` took 47.9 ms and was
byte-perfect, while `6` took 1.6 s and `12` took 3.1 s — both over the project's
sub-1-second framework budget ([ADR 0001](docs/adr/0001-python-implementation-language.md)).
Only 0, 6 and 12 were measured, so `0` is the only measured in-budget value. Raise it **only**
if a specific application drops characters; it is the intended fix for that.

**Raising `window_seconds` costs latency.** The value is capped at 30 (the engine's own
per-call ceiling) and values above about **6 s** are accepted **with a warning**: measured
per-chunk latency crosses the 1-second budget there
([ADR 0011](docs/adr/0011-streaming-sliding-window.md)). `chunk_seconds` must be at least
0.25 and strictly less than `window_seconds`; anything else is a hard configuration error.
Do not run the window uncapped — that is the failure the cap exists to prevent.

**Layouts.** By default ges2t maps text to keycodes using the **live compositor keymap**
(`xkbcli dump-keymap-wayland`), recomputed per utterance, so a non-US layout types the
correct characters ([ADR 0020](docs/adr/0020-layout-aware-keycode-mapping.md)). Text the
active layout cannot type (for example an emoji) is **skipped with a warning on stderr**,
never emitted as garbage; the clipboard backend is the escape hatch for such text. Resolution
order is: an explicit `[injection.ydotool] layout` → the live keymap → KDE's detected layout.
If all three fail you get an error telling you to set `layout` explicitly, for example
`layout = "hu"`.

## 11. Troubleshooting

**Start here**, because it names the failing check and its fix:

```bash
uv run ges2t doctor          # fast; checks existence and size
uv run ges2t doctor --verify # also hashes the model and engine against their pins
```

Exit code **0 with no `FAIL` lines is the healthy state.** `WARN` lines do not change the
exit code: two warnings are expected on this machine (the tray's runtime failure record cannot
be read from outside the running service, and the hotkey binding always needs manual
confirmation — see the example below).

### What `ges2t doctor` checks

Every environment prerequisite in this README has a check. `FAIL` lines are listed again at
the end of the report with their fix.

| Check | What it means | Covered in |
|---|---|---|
| Package imports | ges2t's own modules import; a failure means a broken install | section 4 |
| Architecture | this platform has a pinned engine artifact | section 3 |
| Model present | `whistle.cact` exists at the pinned size | section 6 |
| Model verified | with `--verify`: its sha256 matches the pin | section 6 |
| Engine present | `libneedle.so` exists at the pinned size | section 6 |
| cactus-needle importable | the pinned `cactus-needle` package imports | section 4 |
| Engine/package versions | installed `cactus-needle` matches the pin (WARN on skew); engine version | section 3 |
| Config valid | the TOML parses and every generic value has the right type | section 10 |
| Engine config valid | the `[engine.whistle]` block validates | section 10 |
| Injection config valid | the selected `[injection.<backend>]` block validates | section 10 |
| Config warnings | unknown keys anywhere, including the engine block (WARN) | section 10 |
| Audio device | `audio.device` is empty or present in PipeWire's node list (WARN if absent) | section 10 |
| pw-record present | `pw-record` is on `PATH` | section 3 |
| PipeWire session | a PipeWire session is reachable without recording anything | section 3 |
| ydotoold | the daemon answers on its socket | section 5 |
| /dev/uinput access | this user may write `/dev/uinput` | section 5 |
| Tray backend | `pystray` selected the visible **AppIndicator** backend, not `_xorg` | sections 4, 11 |
| Tray failures | the running tray's failure record — WARN when it cannot be determined | section 11 |
| Service socket | no socket, a listening socket, or a stale socket that refuses connections | sections 8, 11 |
| Keyboard layout | the layout resolves (configured → live keymap → detected) and compiles | sections 9, 10 |
| Clipboard backend | `wl-copy` exists when `injection.backend = "clipboard"` | section 10 |
| Hotkey bindings | always WARN: the binding lives in KDE, and ges2t never touches it | section 7 |

### A real report from the reference machine

```console
$ uv run ges2t doctor
ges2t doctor

Package imports           OK    all 18 package modules import (Python 3.14.7)
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
Tray failures             WARN  tray failures cannot be determined from here: the record lives in the running service process, and doctor does not connect to it or write a file
                                fix: watch the service's stderr for 'ges2t: the tray has failed ...' lines and its shutdown summary, or run 'ges2t run' in a terminal to see them
Service socket            OK    the service is listening at /run/user/1000/ges2t.sock
Keyboard layout           OK    resolved from the live compositor keymap; libxkbcommon compiled it
Clipboard backend         OK    not selected (injection.backend = 'ydotool'); 'wl-copy' is not required
Hotkey bindings           WARN  ges2t does not grab the hotkey itself; bind the command in KDE
                                fix: System Settings -> Shortcuts -> Add -> Command or URL: bind '/home/matee/Workspace/Personal/ges2t/.venv/bin/ges2t toggle' (recommended), or '/home/matee/Workspace/Personal/ges2t/.venv/bin/ges2t ptt-start' and '/home/matee/Workspace/Personal/ges2t/.venv/bin/ges2t ptt-stop', plus '/home/matee/Workspace/Personal/ges2t/.venv/bin/ges2t cancel'

Summary: 22 checks, 20 ok, 2 warning(s), 0 failure(s).

Recommended KDE key bindings (System Settings -> Shortcuts -> Add -> Command or URL):
  /home/matee/Workspace/Personal/ges2t/.venv/bin/ges2t toggle     start or stop with one key (recommended default)
  /home/matee/Workspace/Personal/ges2t/.venv/bin/ges2t ptt-start  press-to-talk: bind to the key press
  /home/matee/Workspace/Personal/ges2t/.venv/bin/ges2t ptt-stop   press-to-talk: bind to the key release
  /home/matee/Workspace/Personal/ges2t/.venv/bin/ges2t cancel     abort a recording without transcribing
ges2t never edits your compositor settings or your config file (ADR 0004).
```

`doctor` performs no network I/O, writes nothing to disk, and touches no config or system
state, so it is safe to run at any time — including while the service is running
([ADR 0016](docs/adr/0016-cli-surface.md)).

### Symptoms and fixes

| Symptom | Likely cause | Fix |
|---|---|---|
| Nothing is typed and the service shows `error`, with a message or notification naming `ydotoold` | `ydotoold` is not running, or `/dev/uinput` is not writable | `systemctl --user enable --now ydotool.service`; check `systemctl --user is-active ydotool.service` and `test -w /dev/uinput`; add yourself to the `input` group and re-login (section 5) |
| Nothing is typed and the service shows `error`, with a message naming a layout or characters | the layout could not be resolved, or the text is untypeable on it | `ges2t doctor` → **Keyboard layout**; set `[injection.ydotool] layout` explicitly (section 10) |
| Nothing is typed and the run ends normally (`idle`) | the speech gate: a recording under 100 ms, or with no sustained speech energy, types nothing on purpose — silence in, nothing out | speak a normal phrase; check the microphone with `ges2t doctor` (**Audio device**, **PipeWire session**) |
| Nothing is typed although there clearly was audio, and no error appears | the focused application discarded the synthetic keystrokes | try another application; synthetically typed keys are subject to the target's own key handling ([ADR 0003](docs/adr/0003-text-injection-via-ydotool.md)) |
| Text appears in the wrong window or field | focus moved during the utterance — committed words land wherever focus is at each commit | keep the target window focused for the whole utterance; this is a documented limitation of incremental typing ([ADR 0011](docs/adr/0011-streaming-sliding-window.md) amendment) |
| A word is confidently wrong, mid-sentence | a stable hallucination was committed and typed (the two-pass commit rule protects against revision, not against this) | stop, delete the wrong text, continue; ges2t never auto-corrects (section 10) |
| The tray icon is invisible on Plasma 6 | the venv was created **without** `--system-site-packages`, so `gi` is missing and `pystray` fell back to the invisible XEmbed backend | `ges2t doctor` → **Tray backend** FAIL; re-create the venv (section 4) |
| The tray icon is not in the tray but the service works | Plasma hides new AppIndicator icons in the tray's **overflow / expanded** area | open the tray's expanded area (`Show hidden icons` / the chevron on the system tray) and pin the ges2t icon so it survives; this is normal Plasma behaviour, not a ges2t failure |
| The tray stopped updating but the service still works | a tray callback raised; the service deliberately contains display exceptions, so the tray records them instead of dying | `ges2t doctor` → **Tray failures** WARN names where they surface; watch the service's stderr for `ges2t: the tray has failed …` lines and its shutdown summary |
| `ges2t run` exits immediately: *a ges2t service is already listening* | a service is already running for this session | use it; or stop it (`Ctrl+C` in its terminal, or the tray's **Quit**), then restart |
| `ges2t toggle` fails with exit 2 and *the ges2t service is not running* | the service is not up | start it with `ges2t run` (or the systemd unit, section 8) |
| `doctor` says the socket exists but refuses connections (stale) | a previous service died without cleaning up its socket file | restart `ges2t run`; a stale socket is removed on the next start |
| `doctor` says the model or engine is missing / has the wrong size | `ges2t setup` has not run, or a download was interrupted | `uv run ges2t setup`; then `uv run ges2t models --verify` |
| `doctor --verify` says the model does not match the pinned sha256 | the file is corrupt | `uv run ges2t setup` re-downloads it |
| `doctor` warns about unknown config keys | a typo in `config.toml`; unknown keys are ignored but surfaced | fix or remove the keys named in the warning (section 10) |
| `doctor` fails **Config valid** with a message naming a file and key | wrong-typed value or invalid TOML | fix the key named, or pass a different file with `--config <path>` (section 10) |
| Wrong characters on a non-US layout, or some characters missing | the layout could not be resolved, or the text contains characters the layout cannot type | `ges2t doctor` → **Keyboard layout**; set `[injection.ydotool] layout` explicitly (e.g. `"hu"`); skipped characters are warned about on stderr and the clipboard backend is the escape hatch (section 10) |
| A long dictation fails with *the Whistle engine accepts at most 30 s of audio per call* | you are on the batch path | use streaming: leave `streaming.enabled = true` (the default); the batch path's 30 s ceiling is a hard engine limit ([ADR 0005](docs/adr/0005-stt-backend-cactus-whistle.md), [ADR 0011](docs/adr/0011-streaming-sliding-window.md)) |
| The clipboard backend seems to hang or reports a timeout | `wl-copy` needs `WAYLAND_DISPLAY`/`XDG_RUNTIME_DIR` and is bounded by a 5-second timeout per invocation | `ges2t doctor` → **Clipboard backend** (`wl-copy` on `PATH`); run inside the Wayland session, not over SSH; a `wl-copy` that forks and never exits was a real bug that is fixed, but any invocation that exceeds the bound is reported as a timeout, not silently swallowed |
| The clipboard backend pastes nothing | the application does not bind `ctrl+shift+v` | set `paste_keys = ["ctrl+v"]` (section 10) |
| `notify-send` popups never appear | no notification daemon, or `notify-send` is missing | notifications are best-effort; the tray and the CLI still report failures, and the service's stderr always carries them |

For anything else, run with a traceback:

```bash
uv run ges2t --debug doctor
GES2T_DEBUG=1 uv run ges2t run
```

Failure messages are documented exit codes, not arbitrary 1s: **0** ok, **1** generic
failure, **2** service not running, **3** engine or injection failed, **130** interrupted
([ADR 0016](docs/adr/0016-cli-surface.md)).

## 12. Architecture and decisions

The service is one long-lived process that owns everything stateful; the CLI subcommands are
thin clients over a Unix socket ([ADR 0015](docs/adr/0015-single-service-process-architecture.md),
[ADR 0004](docs/adr/0004-global-hotkey-via-cli-and-service.md)):

```
KDE key ──> .venv/bin/ges2t toggle ──unix socket──> ges2t run (service)
                                                       ├── WhistleTranscriber (Needle 3, in-process)
                                                       ├── PwRecordAudioSource (pw-record subprocess)
                                                       ├── injector (ydotool key / wl-copy + paste)
                                                       └── tray + notify-send (status display)
```

Module layout (`src/ges2t/`):

| Module | Responsibility |
|---|---|
| `cli.py` | the `ges2t` command surface, lazy imports, documented exit codes ([ADR 0016](docs/adr/0016-cli-surface.md)) |
| `service.py` | the state machine (`idle → recording → transcribing → idle`, plus `error`), the session seam, the socket loop ([ADR 0015](docs/adr/0015-single-service-process-architecture.md)) |
| `socket_proto.py` | newline-delimited JSON over `$XDG_RUNTIME_DIR/ges2t.sock`, mode 0600 |
| `pipeline.py` | the composition root: the only module that chooses concrete implementations ([ADR 0010](docs/adr/0010-transcriber-interface-design.md)) |
| `interfaces.py` | the `Transcriber`/`AudioSource`/`Injector`/`StatusUI` protocols and their data classes |
| `config.py` | the generic TOML layer and the two selectors ([ADR 0009](docs/adr/0009-configuration-toml.md)) |
| `transcribers/` | the Whistle adapter and its `[engine.whistle]` block |
| `audio/pw_record.py` | PipeWire capture, drained on stop ([ADR 0008](docs/adr/0008-audio-capture-via-pw-record.md)) |
| `injectors/` | layout detection and keymap mapping (`layout.py`, `keymap.py`), the batched `ydotool` emitter, the clipboard injector ([ADR 0003](docs/adr/0003-text-injection-via-ydotool.md), [ADR 0020](docs/adr/0020-layout-aware-keycode-mapping.md)) |
| `streaming.py`, `streaming_session.py`, `incremental_typing.py` | the windowed streaming driver, the session over capture, and committed-prefix typing ([ADR 0011](docs/adr/0011-streaming-sliding-window.md)) |
| `model_store.py` | the artifact pins, acquisition, and verification ([ADR 0017](docs/adr/0017-model-acquisition-via-setup.md)) |
| `doctor.py` | every environment check ([ADR 0016](docs/adr/0016-cli-surface.md)) |
| `notify.py`, `ui/` | `notify-send` and the `pystray` AppIndicator tray ([ADR 0013](docs/adr/0013-tray-via-pystray-appindicator.md)) |

All decisions live in `docs/adr/`:

| ADR | Decision |
|---|---|
| [0000](docs/adr/0000-project-scope-and-non-goals.md) | Project scope and non-goals for v1 |
| [0001](docs/adr/0001-python-implementation-language.md) | Python as the implementation language, and the sub-1-second framework budget |
| [0002](docs/adr/0002-wayland-kde-target.md) | Wayland-only, KDE Plasma 6 as the reference compositor |
| [0003](docs/adr/0003-text-injection-via-ydotool.md) | Text injection via `ydotool`/uinput (amended: batched `ydotool key`, layout-aware) |
| [0004](docs/adr/0004-global-hotkey-via-cli-and-service.md) | Global hotkey as a compositor-bound CLI command over a service socket |
| [0005](docs/adr/0005-stt-backend-cactus-whistle.md) | The STT backend: Cactus Whistle on Needle 3; the 30 s per-call ceiling |
| [0006](docs/adr/0006-needle3-closed-engine-accepted-risk.md) | Accepted risk: the Needle 3 engine is a closed binary |
| [0007](docs/adr/0007-whistle-integration-via-cactus-needle-package.md) | Whistle integration through the official `cactus-needle` package |
| [0008](docs/adr/0008-audio-capture-via-pw-record.md) | Audio capture via `pw-record` behind an `AudioSource` interface |
| [0009](docs/adr/0009-configuration-toml.md) | Configuration is TOML at `$XDG_CONFIG_HOME/ges2t/config.toml`; the generic/engine split |
| [0010](docs/adr/0010-transcriber-interface-design.md) | The `Transcriber` interface owns acquisition; interfaces and data classes everywhere |
| [0011](docs/adr/0011-streaming-sliding-window.md) | Streaming: 5 s sliding window, commit on `stop()`; **amended: committed words are typed as they are committed** |
| [0012](docs/adr/0012-verbatim-output-and-trailing-space.md) | Verbatim output plus one configurable trailing space; silence injects nothing |
| [0013](docs/adr/0013-tray-via-pystray-appindicator.md) | Tray via `pystray`'s AppIndicator backend; the venv needs `--system-site-packages` |
| [0014](docs/adr/0014-testing-and-quality-bar.md) | Testing and quality bar: headless `pytest`, no CI, measurements not adjectives |
| [0015](docs/adr/0015-single-service-process-architecture.md) | One long-lived service process owns the engine, capture, state and socket |
| [0016](docs/adr/0016-cli-surface.md) | The CLI surface and its documented exit codes |
| [0017](docs/adr/0017-model-acquisition-via-setup.md) | Explicit `ges2t setup`, pinned by sha256 and size |
| [0018](docs/adr/0018-no-runtime-network.md) | No runtime network: acquisition is the only online operation |
| [0019](docs/adr/0019-code-style-typing-and-interfaces.md) | Code style: full type annotations, interface-typed signatures, classes over tuples |
| [0020](docs/adr/0020-layout-aware-keycode-mapping.md) | Layout-aware text→keycode mapping via `libxkbcommon` |

The project board, epics and tickets are in `docs/project/`. Related tickets for this
documentation live in `docs/project/live-dictation-ux/user-documentation-and-setup-guide/`.

### Working on ges2t

```bash
uv sync                                   # create/update the environment from uv.lock
uv run pytest                             # the full test suite, headless
uv run mypy src/ges2t tests               # static typing, per ADR 0019
```

Tests never touch a real microphone, a real clipboard, `/dev/uinput`, a real compositor or a
real engine — every backend is behind an interface with a fake
([ADR 0014](docs/adr/0014-testing-and-quality-bar.md)). Developer requirements are the same
as section 3.

## 13. Known limitations

- **The engine is a closed binary.** The Needle 3 engine (`libneedle.so`) ships as a
  stripped x86-64 ELF with no published source: it is legal, unauditable and unpatchable. The
  weights and the Python wrapper are Apache-2.0. This risk was accepted knowingly, and the
  escape hatch is a second `Transcriber` implementation (for example whisper.cpp/ggml)
  ([ADR 0006](docs/adr/0006-needle3-closed-engine-accepted-risk.md)).
- **No post-processing.** The engine's text is injected verbatim: no punctuation restoration,
  no casing fixes, no filler-word removal, no translation. The only exception is the single
  trailing space ([ADR 0012](docs/adr/0012-verbatim-output-and-trailing-space.md),
  [ADR 0000](docs/adr/0000-project-scope-and-non-goals.md)).
- **Incremental typing has no opt-out that keeps streaming.** Turning it off means turning
  streaming off (`streaming.enabled = false`), which drops you to the batch path and its 30 s
  per-call ceiling. A stable hallucination can therefore be typed mid-dictation; recovery is
  stop → delete → continue, and ges2t never auto-corrects
  ([ADR 0011](docs/adr/0011-streaming-sliding-window.md) amendment).
- **Focus must hold for the whole utterance.** Committed text lands wherever focus is at each
  commit. Switching windows mid-dictation is not supported, and interleaving with your own
  typing is out of scope.
- **`cancel` is cancel-safe but not cancel-undoable.** Once a commit has been typed it is in
  the focused window; `cancel` abandons the rest of the utterance and types nothing further,
  but it cannot remove what was already typed.
- **The batch path has a hard 30 s ceiling**, the engine's own per-call limit. The streaming
  path lifts it, which is why streaming is the default
  ([ADR 0005](docs/adr/0005-stt-backend-cactus-whistle.md),
  [ADR 0011](docs/adr/0011-streaming-sliding-window.md)).
- **Mixed-language detection is unstable**, because the engine re-detects the language per
  streaming pass. A mid-utterance language switch may be mis-detected; this is recorded as a
  measurement and not worked around in v1
  ([ADR 0011](docs/adr/0011-streaming-sliding-window.md)).
- **Only KDE/Arch/Wayland is verified.** X11 is unsupported; other Wayland compositors and
  other distributions are untested and unverified
  ([ADR 0002](docs/adr/0002-wayland-kde-target.md)). The venv, the system packages and the
  `input` group step are Arch-flavoured.
- **No packaging and no installer**, by design; the steps in this README are the installation
  path ([ADR 0000](docs/adr/0000-project-scope-and-non-goals.md)).
- **The tray needs a working AppIndicator host.** ges2t refuses to start a tray it cannot
  display rather than silently falling back, and remains fully usable from the CLI when the
  tray is unavailable ([ADR 0013](docs/adr/0013-tray-via-pystray-appindicator.md)).
- **Notifications are best-effort.** No notification daemon or no `notify-send` means no
  popups, not a failure.
- **Unverified in this document:** the exact wording of Plasma 6.7.5's *Shortcuts → Add →
  Command or URL* dialog; whether Plasma 6.7.5 can fire separate key-press and key-release
  commands for a custom shortcut (so true press-and-hold is unproven); the tray's behaviour
  when the service is started by the systemd user unit rather than from a terminal; and a
  fresh `ges2t setup` download on a machine without the artifacts. See
  [`001-comment.md`](docs/project/live-dictation-ux/user-documentation-and-setup-guide/001-comment.md)
  for each gap.
