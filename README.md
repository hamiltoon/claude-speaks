# claude-speaks

Claude Code reads its answers aloud with a local TTS voice and shows a
floating, audio-reactive cloud in the corner of the screen while it talks.
Everything runs on the Mac: Kokoro-82M through MLX, a native AppKit overlay,
no cloud services.

Built 2026-09-12 in one sitting; see `TODO.md` for where it is heading.

## How it works

```
Claude Code ──Stop hook──▶ speak-hook.sh ──▶ speak.py (client)
                                                │  Unix socket ~/.claude/speak.sock
                                                ▼
                                        speak.py --serve
                                        ├─ Kokoro kept warm (mlx-audio)
                                        ├─ sentence-by-sentence synthesis → streaming playback
                                        └─ NebulaOverlay: Core Graphics cloud, fades in/out
Claude Code ──UserPromptSubmit hook──▶ speak-stop.sh ──▶ speak.py --stop
```

- **Stop hook** fires when Claude finishes a turn. The hook reads the last
  assistant message from the session transcript and hands it to the client,
  detached, so the hook returns instantly.
- **Server** starts on demand, loads the model once (~3 s), then answers
  requests in ~0.2 s. It exits by itself after 10 minutes without work.
- **Overlay** is a borderless, transparent, always-on-top window drawn with
  Core Graphics into a floating-point persistence buffer, which gives the
  smoke trails. Six frequency bands each push their own puffs outward; hue is
  fixed per voice with a slight tint from the sound; saturation follows
  loudness. It eases in over 1 s and dissolves over 2 s as the last word lands.
- **Voices**: every Kokoro voice has its own cloud colour, so an agent is
  recognisable by ear and eye.

## Files

| File | Purpose |
|---|---|
| `speak.py` | client + server + overlay, single file |
| `speak-hook.sh` | Claude Code `Stop` hook |
| `speak-stop.sh` | Claude Code `UserPromptSubmit` hook (hush) |
| `install.sh` | venv, deps, symlinks into `~/.claude/hooks/` |
| `TODO.md` | ideas and status |

## Install

```bash
./install.sh
```

That creates `~/.venvs/kokoro` (Python 3.12 via uv), installs mlx-audio,
misaki, the spaCy English model, sounddevice and pyobjc, and symlinks the
scripts into `~/.claude/hooks/`. Then add the hooks to `~/.claude/settings.json`:

```json
"hooks": {
  "Stop": [{ "hooks": [{ "type": "command", "command": "bash ~/.claude/hooks/speak-hook.sh", "timeout": 10 }] }],
  "UserPromptSubmit": [{ "hooks": [{ "type": "command", "command": "bash ~/.claude/hooks/speak-stop.sh", "timeout": 5 }] }]
}
```

## Use

```bash
touch ~/.claude/speak.on      # enable (applies to every Claude Code session)
rm ~/.claude/speak.on         # disable

~/.venvs/kokoro/bin/python speak.py "Hello John"
~/.venvs/kokoro/bin/python speak.py --voice am_fenrir "Hello John"
~/.venvs/kokoro/bin/python speak.py --list-voices
~/.venvs/kokoro/bin/python speak.py --demo af_heart,am_fenrir,bf_emma,am_puck
~/.venvs/kokoro/bin/python speak.py --stop
```

Voice selection: `--voice` > `$SPEAK_VOICE` > `~/.claude/speak.voice` > `af_heart`.
Put `auto` in `~/.claude/speak.voice` and each session gets a stable voice
derived from its session id, so parallel agents sound different.

Click the cloud to dismiss it early.

## Tuning

All knobs are constants at the top of `speak.py`: window size, fade times,
trail persistence, puff count, per-voice hues, band ranges, sparks on/off.

## Gotchas met along the way

- uv's Python ships tkinter without a usable Tcl library; irrelevant now
  (AppKit), but `TCL_LIBRARY` fixes it if you ever need Tk.
- spaCy's model auto-download breaks under uv; install the wheel directly.
- `NSGradient` multi-stop needs an explicit colour space or it throws inside
  `drawRect`, which crashes the process silently with exit 133.
- 8-bit persistence buffers never decay to zero; use float components.
- `screencapture -R` takes points, not pixels. Visual checks: full capture +
  `sips -c` crop of the bottom-right corner.
