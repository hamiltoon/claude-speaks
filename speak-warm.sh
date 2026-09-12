#!/bin/bash
# Claude Code SessionStart hook: pre-warm the speech server so the first
# answer of a session speaks instantly. Only while ~/.claude/speak.on exists.
cat >/dev/null
[ -f "$HOME/.claude/speak.on" ] || exit 0
"$HOME/.venvs/kokoro/bin/python" "$HOME/.claude/hooks/speak.py" --warm >>"$HOME/.claude/speak.log" 2>&1 &
disown
exit 0
