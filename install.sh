#!/bin/bash
# Set up the venv, dependencies and hook symlinks for claude-speaks.
# Idempotent; safe to re-run. Requires uv (brew install uv).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
VENV="$HOME/.venvs/kokoro"
PY="$VENV/bin/python"

if [ ! -x "$PY" ]; then
    uv venv --python 3.12 "$VENV"
fi

uv pip install --python "$PY" \
    mlx-audio "misaki[en]" sounddevice \
    pyobjc-framework-Cocoa pyobjc-framework-Quartz \
    https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl

mkdir -p "$HOME/.claude/hooks"
for f in speak.py speak-hook.sh speak-stop.sh; do
    ln -sfn "$HERE/$f" "$HOME/.claude/hooks/$f"
done
chmod +x "$HERE"/speak.py "$HERE"/speak-hook.sh "$HERE"/speak-stop.sh

# Download the model once so the first utterance is not a surprise wait.
"$PY" "$HERE/speak.py" --list-voices >/dev/null

echo "Installed. Enable with: touch ~/.claude/speak.on"
echo "Then add the Stop and UserPromptSubmit hooks from README.md to ~/.claude/settings.json"
