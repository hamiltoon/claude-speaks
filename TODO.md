# TODO / ideas

Roughly in the order they seem worth doing. Ticked items are done.

## Latency and feel

- [x] Warm server: one resident Kokoro process, clients talk over a Unix socket. First audio ~0.2 s warm vs ~3.4 s cold. Quits after 10 min idle.
- [x] Stream while generating: play the first sentence while the rest renders.
- [x] Hush on new prompt: `UserPromptSubmit` hook sends `--stop`, cloud fades.
- [x] Pre-warm the server when a Claude Code session starts (`SessionStart` hook → `speak.py --warm`) so even the first answer is instant.

## Multi-agent presence

- [ ] One cloud per agent, side by side. `auto` voices already give each session its own voice and colour; let overlays coexist in slots along the bottom edge instead of replacing each other.
- [ ] Idle presence: a tiny dim cloud per running session, brightening when it speaks or needs input.
- [ ] Spoken agent names: prefix the first utterance with "Issue worker on liero-rs".

## What gets spoken

- [ ] Speak summaries, not answers: run the final message through Haiku ("one spoken sentence, max 20 words") before TTS. Full text stays on screen.
- [ ] `Notification` hook: say "I need your permission" / "waiting on you" when Claude blocks, with a pulsing cloud.
- [ ] Language switch: detect Swedish and route through ElevenLabs or macOS Premium Alva; keep Kokoro for English.

## Visual polish

- [ ] Pitch as colour tint instead of spectral brightness, so questions rise and statements settle visibly.
- [ ] Dark-background mode: sample the screen behind the window and pick lighter/darker rendering.
- [ ] Personality per voice: distinct puff count, drift speed, trail length, not only colour.
- [ ] Sparks are implemented but off (`SPARKS = False`); revisit as a subtle garnish.

## Input

- [ ] Talk back: push-to-talk with local Whisper, drop the transcript into the Claude Code prompt.
- [x] AirPods gesture as push-to-talk: `airpods-ptt.py`, double-press the stem to hold/release Space. Runs as a launch agent
  (`se.hamiltoon.airpods-ptt.plist`). Needs Accessibility permission for the venv Python; swallows media keys while running.
  Original notes:
  Feasible: a small daemon registers as the Now Playing app via `MPRemoteCommandCenter` (MediaPlayer framework, pyobjc),
  receives the stem's play/pause and next-track commands, and toggles a synthetic Space key-down/up into the frontmost app
  with `CGEventPost`. Caveats: needs Accessibility permission for the venv Python (same wall as Esc); the daemon only
  receives stem commands while it is the Now Playing app, so it must play silent audio and it hijacks play/pause from
  Spotify while active; the press-and-hold gesture is reserved for Siri/noise control and cannot be intercepted, so use
  double-press (next track). Estimate: an afternoon. Not started; wait for a quiet usage window.

## Dropped

- Esc to stop: needs Accessibility trust for the venv Python, and Esc also interrupts a running Claude Code turn. `--stop` over the socket does the job without side effects.

## Considered: rewrite in Rust (2026-09-12, decided against)

- Latency is model-bound: warm first audio is ~0.2 s and almost all of it is Kokoro on the GPU. Python overhead is milliseconds; the client's ~0.1 s startup is hidden behind the detached hook.
- Blocker: Kokoro's English phonemizer (misaki) is Python-only. The Rust path is ONNX via `ort` plus espeak-ng phonemes, which audibly lowers voice quality.
- What Rust would buy: one binary, no venv quirks, tens of MB idle instead of hundreds (torch/transformers imports), cold start under 1 s. A `SessionStart` pre-warm hook gets the cold-start win in Python for free.
- If a Rust project is wanted anyway: rewrite only the overlay + client as a small binary speaking the existing socket protocol to the Python synthesis server. Clean cut, exercises `objc2` and real-time Core Graphics, keeps the voice.
