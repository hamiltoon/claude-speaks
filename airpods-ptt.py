#!/usr/bin/env python3
"""AirPods stem → push-to-talk for Claude Code.

Registers as the system's Now Playing app so the AirPods stem commands
reach us, then turns a double-press (next track) into holding the Space
key in the frontmost app. Double-press again to release.

Run with ~/.venvs/kokoro/bin/python. Needs Accessibility permission for
that Python (macOS prompts on first run). While this runs it owns
play/pause, so pause Spotify first.

    airpods-ptt.py            # run in a terminal, Ctrl-C to quit
"""
from __future__ import annotations

import sys
import threading
import time

import numpy as np

SPACE = 49            # macOS virtual keycode
REPEAT_INTERVAL = 0.05  # seconds between synthetic key-repeat events while held
GESTURE = "double"    # "double" = next-track command, "single" = play/pause


def log(msg: str) -> None:
    print(f"[ptt {time.strftime('%H:%M:%S')}] {msg}", flush=True)


class Holder:
    """Holds Space down in the frontmost app: key-down, repeats, key-up."""

    def __init__(self):
        import Quartz as Q

        self.Q = Q
        self.held = False
        self._thread: threading.Thread | None = None

    def _post(self, down: bool, repeat: bool = False):
        Q = self.Q
        ev = Q.CGEventCreateKeyboardEvent(None, SPACE, down)
        if repeat:
            Q.CGEventSetIntegerValueField(ev, Q.kCGKeyboardEventAutorepeat, 1)
        Q.CGEventPost(Q.kCGHIDEventTap, ev)

    def _repeat_loop(self):
        while self.held:
            self._post(True, repeat=True)
            time.sleep(REPEAT_INTERVAL)

    def toggle(self):
        if self.held:
            self.held = False
            if self._thread:
                self._thread.join(timeout=1)
            self._post(False)
            log("released Space")
        else:
            self.held = True
            self._post(True)
            self._thread = threading.Thread(target=self._repeat_loop, daemon=True)
            self._thread.start()
            log("holding Space")


def ensure_accessibility() -> bool:
    from ApplicationServices import AXIsProcessTrusted, AXIsProcessTrustedWithOptions

    if AXIsProcessTrusted():
        return True
    AXIsProcessTrustedWithOptions({"AXTrustedCheckOptionPrompt": True})
    log("not trusted: allow this Python under System Settings > Privacy & Security > Accessibility, then rerun")
    return False


def silent_audio():
    """Play silence so macOS treats us as an active audio app."""
    import sounddevice as sd

    def cb(outdata, frames, _t, _s):
        outdata[:] = 0

    stream = sd.OutputStream(samplerate=24000, channels=1, dtype="float32", callback=cb)
    stream.start()
    return stream


def main() -> int:
    from Cocoa import NSApplication, NSRunLoop, NSDate
    import MediaPlayer as MP

    if not ensure_accessibility():
        return 1
    holder = Holder()
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(1)  # accessory
    stream = silent_audio()

    center = MP.MPRemoteCommandCenter.sharedCommandCenter()

    def handler_factory(name, act):
        def handler(_event):
            log(f"stem: {name}")
            if act:
                holder.toggle()
            return MP.MPRemoteCommandHandlerStatusSuccess
        return handler

    center.nextTrackCommand().addTargetWithHandler_(handler_factory("double-press", GESTURE == "double"))
    center.togglePlayPauseCommand().addTargetWithHandler_(handler_factory("single-press", GESTURE == "single"))
    center.playCommand().addTargetWithHandler_(handler_factory("play", GESTURE == "single"))
    center.pauseCommand().addTargetWithHandler_(handler_factory("pause", GESTURE == "single"))
    center.previousTrackCommand().addTargetWithHandler_(handler_factory("triple-press", False))

    info = MP.MPNowPlayingInfoCenter.defaultCenter()
    info.setNowPlayingInfo_({
        MP.MPMediaItemPropertyTitle: "Claude push-to-talk",
        MP.MPMediaItemPropertyArtist: "claude-speaks",
        MP.MPNowPlayingInfoPropertyPlaybackRate: 1.0,
    })
    info.setPlaybackState_(MP.MPNowPlayingPlaybackStatePlaying)
    log(f"ready: {GESTURE}-press the AirPods stem to hold/release Space. Ctrl-C to quit.")

    try:
        while True:
            NSRunLoop.currentRunLoop().runMode_beforeDate_("kCFRunLoopDefaultMode", NSDate.dateWithTimeIntervalSinceNow_(0.2))
    except KeyboardInterrupt:
        pass
    finally:
        if holder.held:
            holder.toggle()
        stream.stop()
        info.setPlaybackState_(MP.MPNowPlayingPlaybackStateStopped)
    return 0


if __name__ == "__main__":
    sys.exit(main())
