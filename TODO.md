# TODO / ideas

Roughly in the order they seem worth doing. Ticked items are done.

## Latency and feel

- [x] Warm server: one resident Kokoro process, clients talk over a Unix socket. First audio ~0.2 s warm vs ~3.4 s cold. Quits after 10 min idle.
- [x] Stream while generating: play the first sentence while the rest renders.
- [x] Hush on new prompt: `UserPromptSubmit` hook sends `--stop`, cloud fades.
- [ ] Pre-warm the server when a Claude Code session starts (`SessionStart` hook) so even the first answer is instant.

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

## Dropped

- Esc to stop: needs Accessibility trust for the venv Python, and Esc also interrupts a running Claude Code turn. `--stop` over the socket does the job without side effects.
