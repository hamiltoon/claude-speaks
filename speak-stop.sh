#!/bin/bash
# Claude Code UserPromptSubmit hook: hush the speaker when John starts a new
# prompt. Cheap socket message; no-op if nothing is speaking.
cat >/dev/null
"$HOME/.venvs/kokoro/bin/python" "$HOME/.claude/hooks/speak.py" --stop >/dev/null 2>&1 &
disown
exit 0
