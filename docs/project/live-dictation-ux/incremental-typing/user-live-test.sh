#!/usr/bin/env bash
# Ticket 34's user-owned live acceptance test (incremental typing).
#
# The user runs this. No agent may record the microphone or emit a keystroke,
# so this script only *checks prerequisites and prints the procedure*: it never
# opens the microphone, never touches the clipboard, and never types.
#
# Run it from the worktree:
#   bash docs/project/live-dictation-ux/incremental-typing/user-live-test.sh
set -u

echo "=== ticket 34 live test: prerequisites (read-only) ==="

if pgrep -x ydotoold >/dev/null 2>&1; then
    echo "  OK   ydotoold is running"
else
    echo "  FAIL ydotoold is NOT running -> the ydotool backend cannot type."
    echo "       fix: systemctl --user enable --now ydotool"
fi

if command -v ydotool >/dev/null 2>&1; then
    echo "  OK   ydotool is on PATH"
else
    echo "  FAIL ydotool is NOT on PATH"
fi

if [ -f "$HOME/.config/ges2t/config.toml" ]; then
    echo "  ---- ~/.config/ges2t/config.toml ----"
    sed -n '1,40p' "$HOME/.config/ges2t/config.toml"
    if grep -Eq '^\s*backend\s*=\s*"clipboard"' "$HOME/.config/ges2t/config.toml"; then
        echo "  FAIL injection.backend = \"clipboard\" with the default"
        echo "       streaming.enabled = true is refused at startup by design (ticket 34)."
        echo "       Set backend = \"ydotool\", or streaming.enabled = false."
    fi
else
    echo "  note no config file: the defaults are backend = \"ydotool\","
    echo "       streaming.enabled = true, ui.trailing_space = true"
fi

cat <<'STEPS'

=== the procedure ===

Terminal A (the service):
    cd /home/matee/Workspace/Personal/ges2t/.worktrees/incremental-typing
    uv run ges2t run

Terminal B, or a key you have bound to `ges2t toggle`:
    1. Focus a plain text editor (any app with a visible caret).
    2. Toggle dictation ON and start speaking. Start with a full sentence.
    3. WATCH TYPING WHILE YOU SPEAK. Text is expected to appear as the engine
       commits words, i.e. a little behind your voice, before you stop.
       Optional: have a stopwatch or phone clock ready and write down the lag
       between finishing a word and seeing it in the editor. Report the number;
       a qualitative "instant / about a second / several seconds" is fine.
    4. Toggle dictation OFF and confirm the final words and exactly one trailing
       space appear.

=== the recovery test (user decision #2) ===

    5. Toggle ON again. Deliberately dictate a phrase you expect the model to
       get wrong.
    6. The moment you see the wrong word appear, toggle OFF (this is the "stop"
       the decision depends on staying instant).
    7. Delete the wrong text yourself and toggle ON to continue dictating. The
       tool must not attempt any auto-correction.

=== what to report back ===

  [ ] text appeared WHILE speaking (not only on release)
  [ ] observed lag .......................................
  [ ] exactly one trailing space at the end ............
  [ ] stop -> delete -> continue recovered the wrong word
  [ ] anything unexpected (e.g. a space per commit, a duplicated word)

=== the accepted caveat you may see ===

A stable hallucination can be committed and therefore typed mid-dictation: on
the ticket-32 recording the deliberate pause produced a committed
' Great night.' that nothing later contradicted. The commit rule protects
against revision, not against a stable hallucination. Stop -> delete ->
continue is the accepted recovery; the tool does not auto-correct.

Interleaving with your own typing is out of scope and is a documented
limitation.
STEPS
