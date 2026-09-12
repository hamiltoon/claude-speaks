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
  requests in ~0.2 s. A `SessionStart` hook pre-warms it, so even the first
  answer of a session is instant. It exits by itself after 10 minutes without work.
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
| `speak-warm.sh` | Claude Code `SessionStart` hook (pre-warm the server) |
| `airpods-ptt.py` | AirPods stem double-press → hold Space (push-to-talk) in the frontmost app |
| `se.hamiltoon.airpods-ptt.plist` | launch agent that keeps `airpods-ptt.py` running |
| `speak-menu.swift` | menu bar switch: speech on/off and stop now (compiled to `build/speak-menu`) |
| `se.hamiltoon.speak-menu.plist` | launch agent that starts the menu bar switch at login |
| `install.sh` | venv, deps, symlinks into `~/.claude/hooks/`, builds the menu switch, launch agents |
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
  "UserPromptSubmit": [{ "hooks": [{ "type": "command", "command": "bash ~/.claude/hooks/speak-stop.sh", "timeout": 5 }] }],
  "SessionStart": [{ "hooks": [{ "type": "command", "command": "bash ~/.claude/hooks/speak-warm.sh", "timeout": 5 }] }]
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

Click the cloud to dismiss it early; that also stops the voice.

## Menu bar switch

A waveform icon in the menu bar shows whether speech is on; it is crossed out
when off. Its menu has **Speak answers** (on/off) and **Stop speaking now**.
It toggles the same `~/.claude/speak.on` file as the terminal commands above,
and picks up terminal changes within two seconds. It is a small native Swift
binary (about 14 MB footprint) started at login by a launch agent. If you
choose Quit, bring it back with:

```bash
launchctl kickstart gui/$(id -u)/se.hamiltoon.speak-menu
```

## AirPods push-to-talk

Claude Code's voice input is hold-Space. `airpods-ptt.py` turns a double-press on
the AirPods stem into holding Space in the frontmost app; double-press again to
release. It catches media keys with a system event tap and swallows them, so
Spotify does not react while it runs. First run: allow the venv's Python under
System Settings > Privacy & Security > Accessibility. The launch agent starts it
at login and restarts it if it dies; log in `~/.claude/airpods-ptt.log`.

```bash
launchctl bootout gui/$(id -u)/se.hamiltoon.airpods-ptt      # stop (gives the stem back to Spotify)
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/se.hamiltoon.airpods-ptt.plist   # start
```

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
