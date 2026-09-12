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
    pyobjc-framework-MediaPlayer pyobjc-framework-ApplicationServices \
    https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl

mkdir -p "$HOME/.claude/hooks"
for f in speak.py speak-hook.sh speak-stop.sh speak-warm.sh; do
    ln -sfn "$HERE/$f" "$HOME/.claude/hooks/$f"
done
chmod +x "$HERE"/speak.py "$HERE"/speak-hook.sh "$HERE"/speak-stop.sh "$HERE"/speak-warm.sh

# Menu bar switch: compile the Swift binary and start it at login.
mkdir -p "$HERE/build"
swiftc -O -swift-version 5 "$HERE/speak-menu.swift" -o "$HERE/build/speak-menu"
cp "$HERE/se.hamiltoon.speak-menu.plist" "$HOME/Library/LaunchAgents/"
launchctl bootout "gui/$(id -u)/se.hamiltoon.speak-menu" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/se.hamiltoon.speak-menu.plist"

# AirPods push-to-talk launch agent (paths in the plist assume this checkout location).
cp "$HERE/se.hamiltoon.airpods-ptt.plist" "$HOME/Library/LaunchAgents/"
launchctl bootout "gui/$(id -u)/se.hamiltoon.airpods-ptt" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/se.hamiltoon.airpods-ptt.plist"

# Download the model once so the first utterance is not a surprise wait.
"$PY" "$HERE/speak.py" --list-voices >/dev/null

echo "Installed. Enable with: touch ~/.claude/speak.on"
echo "Then add the Stop, UserPromptSubmit and SessionStart hooks from README.md to ~/.claude/settings.json"
