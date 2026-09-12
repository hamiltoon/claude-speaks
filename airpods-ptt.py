#!/usr/bin/env python3
"""AirPods stem → push-to-talk for Claude Code.

Two ways to catch the stem, both active:
  1. A system-wide event tap on media-key events (play/pause, next, prev).
     It runs before any app sees the key and swallows it, so Spotify does
     not react. Logs every media key it sees.
  2. Registering as the Now Playing app (MediaPlayer remote commands),
     re-asserted every second, as a fallback.

A double-press (next track) holds the Space key in the frontmost app;
double-press again to release. Change GESTURE to "single" for play/pause.

Run with ~/.venvs/kokoro/bin/python. Needs Accessibility permission for
that Python (macOS prompts on first run).

    airpods-ptt.py            # run in a terminal, Ctrl-C to quit
"""
from __future__ import annotations

import sys
import threading
import time

SPACE = 49              # macOS virtual keycode
REPEAT_INTERVAL = 0.05  # seconds between synthetic key-repeat events while held
GESTURE = "double"      # "double" = next-track, "single" = play/pause

# NX_KEYTYPE_* media key codes carried in NSSystemDefined events (subtype 8).
NX_PLAY, NX_NEXT, NX_PREV, NX_FAST, NX_REWIND = 16, 17, 18, 19, 20
NX_NAMES = {NX_PLAY: "play/pause", NX_NEXT: "next", NX_PREV: "previous", NX_FAST: "fast", NX_REWIND: "rewind"}
TOGGLE_KEYS = {"double": {NX_NEXT, NX_FAST}, "single": {NX_PLAY}}[GESTURE]


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


def install_media_key_tap(holder: Holder):
    """Path 1: swallow media keys system-wide."""
    import Quartz as Q
    from Cocoa import NSEvent

    NX_SYSDEFINED = 14

    def callback(_proxy, etype, event, _refcon):
        if etype == NX_SYSDEFINED:
            ns = NSEvent.eventWithCGEvent_(event)
            if ns is not None and ns.subtype() == 8:
                data1 = ns.data1()
                key = (data1 & 0xFFFF0000) >> 16
                state = (data1 & 0xFF00) >> 8  # 0x0A down, 0x0B up
                if state == 0x0A:
                    log(f"media key: {NX_NAMES.get(key, key)}")
                    if key in TOGGLE_KEYS:
                        holder.toggle()
                if key in NX_NAMES:
                    return None  # swallow so Spotify & co never see it
        return event

    tap = Q.CGEventTapCreate(
        Q.kCGSessionEventTap, Q.kCGHeadInsertEventTap, Q.kCGEventTapOptionDefault,
        Q.CGEventMaskBit(NX_SYSDEFINED), callback, None,
    )
    if tap is None:
        log("event tap could not be created (Accessibility permission?)")
        return None
    src = Q.CFMachPortCreateRunLoopSource(None, tap, 0)
    Q.CFRunLoopAddSource(Q.CFRunLoopGetCurrent(), src, Q.kCFRunLoopCommonModes)
    Q.CGEventTapEnable(tap, True)
    log("media-key tap installed")
    return tap


def silent_audio():
    """Play silence so macOS treats us as an active audio app."""
    import sounddevice as sd

    def cb(outdata, frames, _t, _s):
        outdata[:] = 0

    stream = sd.OutputStream(samplerate=24000, channels=1, dtype="float32", callback=cb)
    stream.start()
    return stream


def install_now_playing(holder: Holder):
    """Path 2: be the Now Playing app so remote commands are routed to us."""
    import MediaPlayer as MP

    center = MP.MPRemoteCommandCenter.sharedCommandCenter()

    def handler_factory(name, act):
        def handler(_event):
            log(f"remote command: {name}")
            if act:
                holder.toggle()
            return MP.MPRemoteCommandHandlerStatusSuccess
        return handler

    center.nextTrackCommand().addTargetWithHandler_(handler_factory("next", GESTURE == "double"))
    center.togglePlayPauseCommand().addTargetWithHandler_(handler_factory("toggle", GESTURE == "single"))
    center.playCommand().addTargetWithHandler_(handler_factory("play", GESTURE == "single"))
    center.pauseCommand().addTargetWithHandler_(handler_factory("pause", GESTURE == "single"))
    center.previousTrackCommand().addTargetWithHandler_(handler_factory("previous", False))

    info = MP.MPNowPlayingInfoCenter.defaultCenter()

    def assert_playing():
        info.setNowPlayingInfo_({
            MP.MPMediaItemPropertyTitle: "Claude push-to-talk",
            MP.MPMediaItemPropertyArtist: "claude-speaks",
            MP.MPNowPlayingInfoPropertyPlaybackRate: 1.0,
        })
        info.setPlaybackState_(MP.MPNowPlayingPlaybackStatePlaying)

    return info, assert_playing


def main() -> int:
    from Cocoa import NSApplication, NSRunLoop, NSDate
    import MediaPlayer as MP

    if not ensure_accessibility():
        return 1
    holder = Holder()
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(1)  # accessory
    stream = silent_audio()
    tap = install_media_key_tap(holder)
    info, assert_playing = install_now_playing(holder)
    assert_playing()
    log(f"ready: {GESTURE}-press the AirPods stem to hold/release Space. Ctrl-C to quit.")

    last = 0.0
    try:
        while True:
            NSRunLoop.currentRunLoop().runMode_beforeDate_("kCFRunLoopDefaultMode", NSDate.dateWithTimeIntervalSinceNow_(0.2))
            if time.time() - last > 1.0:
                assert_playing()
                last = time.time()
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
