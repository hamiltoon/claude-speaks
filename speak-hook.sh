#!/bin/bash
# Claude Code Stop hook: read the last assistant message aloud with Kokoro.
# Enabled only while ~/.claude/speak.on exists (touch / rm to toggle).
# Returns immediately; the speaker runs detached so the hook never blocks.

[ -f "$HOME/.claude/speak.on" ] || exit 0

INPUT=$(cat)
TRANSCRIPT=$(echo "$INPUT" | jq -r '.transcript_path // empty')
SESSION=$(echo "$INPUT" | jq -r '.session_id // empty')
[ -n "$TRANSCRIPT" ] && [ -f "$TRANSCRIPT" ] || exit 0

# Voice: ~/.claude/speak.voice (a voice name, or "auto" for one voice per
# session), else af_heart. See speak.py --list-voices / --demo.
nohup "$HOME/.venvs/kokoro/bin/python" "$HOME/.claude/hooks/speak.py" \
    --transcript "$TRANSCRIPT" --session "$SESSION" >> "$HOME/.claude/speak.log" 2>&1 &
disown
exit 0
