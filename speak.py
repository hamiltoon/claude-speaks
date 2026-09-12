#!/usr/bin/env python3
"""Speak text with Kokoro (local, via mlx-audio) and show a floating,
audio-reactive cloud overlay while the audio plays.

A resident server keeps the model warm and owns the overlay window; the
CLI is a thin client that starts the server on demand. The server quits
by itself after IDLE_EXIT seconds without work.

Usage:
    speak.py "some text"
    echo "some text" | speak.py
    speak.py --transcript /path/to/session.jsonl   # last assistant text
    speak.py --voice am_fenrir "some text"         # pick a voice
    speak.py --list-voices                         # what the model ships with
    speak.py --demo af_heart,am_fenrir,bf_emma     # hear each voice introduce itself
    speak.py --stop                                # fade out whatever is speaking now
    speak.py --serve                               # run the server (normally automatic)

Voice resolution: --voice > $SPEAK_VOICE > ~/.claude/speak.voice > af_heart.
If the chosen voice is "auto", a stable voice is derived from --session so
different sessions/agents get different voices. Every voice has its own
cloud colour (VOICE_HUES); the sound only tints it slightly.

Run with ~/.venvs/kokoro/bin/python (needs mlx-audio, misaki[en],
en_core_web_sm, sounddevice, pyobjc-framework-Cocoa, pyobjc-framework-Quartz).
"""
from __future__ import annotations

import colorsys
import json
import math
import os
import queue
import re
import signal
import socket
import subprocess
import sys
import threading
import time

import numpy as np

MODEL = "prince-canuma/Kokoro-82M"
VOICE = "af_heart"
SPEED = 1.05
MAX_CHARS = 6000  # cap so a huge answer doesn't turn into a ten-minute monologue
PID_FILE = os.path.expanduser("~/.claude/speak.pid")
SOCK_FILE = os.path.expanduser("~/.claude/speak.sock")
VOICE_FILE = os.path.expanduser("~/.claude/speak.voice")
LOG_FILE = os.path.expanduser("~/.claude/speak.log")
IDLE_EXIT = 600            # server quits after this many idle seconds
SERVER_START_TIMEOUT = 40  # client waits this long for a cold server (model load)
# Pool used by "auto": distinct, clear English voices (a = American, b = British).
AUTO_POOL = ["af_heart", "am_fenrir", "bf_emma", "am_michael", "af_bella", "bm_george",
             "af_nicole", "am_puck", "bf_isabella", "am_adam", "af_sky", "bm_lewis"]

# ---------------------------------------------------------------- look ---
W, H = 380, 380            # window size in points
MARGIN = 8
FPS = 30
FADE_IN = 1.0              # seconds to fade the window in
FADE_OUT = 2.2             # seconds to fade it out once speech ends
DIM_SMOOTH = 0.06          # how gently brightness moves between idle and speaking
TRAIL = 0.84               # how much of the previous frame survives (smoke persistence)
DRIFT = 1.006              # per-frame expansion of the trail (smoke drifts outward)
BAND_PUSH = 0.45           # how far a lit band pushes its puffs outward
PUFFS = 22                 # soft puffs that make up the cloud body
PUFF_ALPHA = 0.10          # per-frame body alpha (accumulates via TRAIL)
GLOW_ALPHA = 0.05          # per-frame additive highlight alpha
# Each voice owns a hue (0..1 around the colour wheel). Unlisted voices get a
# stable hue hashed from their name. The sound shifts the hue by at most
# ±HUE_SPREAD/2 (sharper sounds toward the cooler side).
VOICE_HUES = {
    "af_heart": 0.62,      # blue-violet
    "am_fenrir": 0.02,     # red-orange
    "bf_emma": 0.85,       # pink
    "am_michael": 0.52,    # cyan
    "af_bella": 0.95,      # rose
    "bm_george": 0.12,     # amber
    "af_nicole": 0.75,     # violet
    "am_puck": 0.33,       # green
    "bf_isabella": 0.90,   # magenta
    "am_adam": 0.58,       # blue
    "af_sky": 0.48,        # aqua
    "bm_lewis": 0.08,      # orange
}
HUE_SPREAD = 0.10
SAT_MIN = 0.45
SAT_MAX = 1.0
BANDS = [(80, 300), (300, 700), (700, 1400), (1400, 2500), (2500, 4500), (4500, 9000)]  # Hz
BAND_ATTACK = 0.55
BAND_RELEASE = 0.07
BUD_MAX = 10
SPARKS = False             # set True to re-enable onset sparks
SPARK_MAX = 20             # keep sparks a garnish, not a firework
SPARK_BURST = (1, 5)       # sparks per onset at weak..strong
ONSET_THRESH = 0.06


def log(msg: str) -> None:
    print(f"[speak {time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------- text ---

def last_assistant_text(transcript_path: str) -> str:
    """Return the text blocks of the last assistant entry that has any."""
    found = ""
    with open(transcript_path, encoding="utf-8") as fh:
        for line in fh:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") != "assistant":
                continue
            content = entry.get("message", {}).get("content", [])
            if isinstance(content, str):
                texts = [content]
            else:
                texts = [c.get("text", "") for c in content if c.get("type") == "text"]
            joined = "\n".join(t for t in texts if t.strip())
            if joined:
                found = joined
    return found


def clean_for_speech(text: str) -> str:
    """Strip markdown so the voice doesn't read asterisks and pipes."""
    text = re.sub(r"```.*?```", " code block. ", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"^\s*result:.*$", "", text, flags=re.M | re.I)
    text = re.sub(r"^\s*\|.*\|\s*$", "", text, flags=re.M)      # table rows
    text = re.sub(r"^\s*#+\s*", "", text, flags=re.M)             # headers
    text = re.sub(r"^\s*[-*•]\s+", "", text, flags=re.M)          # bullets
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.M)          # numbered
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"\*(.*?)\*", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)          # links
    text = re.sub(r"https?://\S+", "link", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text).strip()
    if len(text) > MAX_CHARS:
        cut = text[:MAX_CHARS]
        cut = cut[: cut.rfind(".") + 1] or cut
        text = cut + " The rest is on screen."
    return text


def split_sentences(text: str) -> list[str]:
    """Chunks small enough to start playing quickly, big enough to sound natural."""
    parts = re.split(r"(?<=[.!?:])\s+|\n+", text)
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if out and len(out[-1]) < 40:  # glue very short fragments to the previous one
            out[-1] += " " + p
        else:
            out.append(p)
    return out


# -------------------------------------------------------------- voices ---

def list_voices() -> list[str]:
    from huggingface_hub import snapshot_download

    path = snapshot_download(MODEL, allow_patterns=["voices/*"])
    names = [os.path.splitext(f)[0] for f in os.listdir(os.path.join(path, "voices"))]
    return sorted(set(names))


def resolve_voice(cli: str | None, session: str | None) -> str:
    voice = cli or os.environ.get("SPEAK_VOICE")
    if not voice and os.path.exists(VOICE_FILE):
        with open(VOICE_FILE) as fh:
            voice = fh.read().strip()
    voice = voice or VOICE
    if voice == "auto":
        import hashlib

        key = session or str(os.getppid())
        idx = int(hashlib.sha1(key.encode()).hexdigest(), 16) % len(AUTO_POOL)
        voice = AUTO_POOL[idx]
    return voice


def voice_hue(voice: str) -> float:
    if voice in VOICE_HUES:
        return VOICE_HUES[voice]
    import hashlib

    return int(hashlib.sha1(voice.encode()).hexdigest(), 16) % 1000 / 1000.0


# --------------------------------------------------------------- audio ---

def trim_silence(audio: np.ndarray, sr: int, thresh: float = 0.012, keep: float = 0.12) -> np.ndarray:
    """Cut the padding Kokoro leaves at both ends, keeping `keep` seconds."""
    if len(audio) == 0:
        return audio
    win = max(1, int(sr * 0.02))
    n = len(audio) // win
    if n == 0:
        return audio
    rms = np.sqrt(np.mean(audio[: n * win].reshape(n, win) ** 2, axis=1))
    loud = np.nonzero(rms > thresh)[0]
    if len(loud) == 0:
        return audio
    start = max(0, loud[0] * win - int(sr * keep))
    end = min(len(audio), (loud[-1] + 1) * win + int(sr * keep))
    return audio[start:end]


class Synth:
    """Kokoro kept warm; yields (audio, sr) per sentence."""

    def __init__(self):
        from mlx_audio.tts.utils import load_model

        self.model = load_model(MODEL)

    def sentences(self, text: str, voice: str, cancel: threading.Event):
        lang = voice[0] if voice[:1] in "abefhijpz" else "a"
        for sent in split_sentences(text):
            if cancel.is_set():
                return
            chunks: list[np.ndarray] = []
            sr = 24000
            for r in self.model.generate(text=sent, voice=voice, speed=SPEED, lang_code=lang, verbose=False):
                chunks.append(np.asarray(r.audio, dtype=np.float32))
                sr = int(r.sample_rate)
            if chunks:
                yield trim_silence(np.concatenate(chunks), sr, keep=0.08), sr


class StreamPlayer:
    """Plays chunks as they arrive and exposes live audio features."""

    def __init__(self, sr: int):
        self.sr = sr
        self.q: queue.Queue[np.ndarray] = queue.Queue()
        self.buf = np.zeros(0, dtype=np.float32)
        self.pos = 0
        self.queued = 0          # samples waiting in q + buf
        self.closed = False      # producer finished
        self.level = 0.0
        self.centroid = 0.3
        self.bands = np.zeros(len(BANDS))
        self.band_peak = np.full(len(BANDS), 1e-6)
        self.done = threading.Event()
        self._lock = threading.Lock()

    def push(self, audio: np.ndarray):
        with self._lock:
            self.queued += len(audio)
        self.q.put(audio)

    def close(self):
        self.closed = True

    def remaining(self) -> float:
        return self.queued / self.sr

    def near_end(self, seconds: float = 0.25) -> bool:
        return self.closed and self.remaining() < seconds

    def _callback(self, outdata, frames, _time, _status):
        out = np.zeros(frames, dtype=np.float32)
        filled = 0
        while filled < frames:
            if self.pos >= len(self.buf):
                try:
                    self.buf = self.q.get_nowait()
                    self.pos = 0
                except queue.Empty:
                    break
            take = min(frames - filled, len(self.buf) - self.pos)
            out[filled:filled + take] = self.buf[self.pos:self.pos + take]
            self.pos += take
            filled += take
        with self._lock:
            self.queued = max(0, self.queued - filled)
        outdata[:, 0] = out
        chunk = out[:filled]
        n = len(chunk)
        if n:
            self.level = float(np.sqrt(np.mean(chunk**2)))
            spec = np.abs(np.fft.rfft(chunk * np.hanning(n)))
            total = float(np.sum(spec)) + 1e-9
            freqs = np.fft.rfftfreq(n, 1.0 / self.sr)
            cen = float(np.sum(freqs * spec)) / total
            self.centroid = min(1.0, max(0.0, (cen - 500.0) / 2000.0))
            for b, (lo, hi) in enumerate(BANDS):
                sel = (freqs >= lo) & (freqs < hi)
                e = float(np.sqrt(np.mean(spec[sel] ** 2))) if np.any(sel) else 0.0
                self.band_peak[b] = max(e, self.band_peak[b] * 0.995, 1e-6)
                self.bands[b] = min(1.0, e / self.band_peak[b])
        else:
            self.level = 0.0
        if self.closed and self.queued == 0 and self.q.empty():
            import sounddevice as sd

            raise sd.CallbackStop

    def run(self):
        import sounddevice as sd

        try:
            with sd.OutputStream(
                samplerate=self.sr, channels=1, dtype="float32",
                blocksize=1024, callback=self._callback,
            ):
                while not self.done.is_set() and not (self.closed and self.queued == 0):
                    time.sleep(0.02)
                time.sleep(0.05)
        finally:
            self.level = 0.0
            self.done.set()


# ------------------------------------------------------------- overlay ---

class NebulaOverlay:
    """Native macOS window (pyobjc + Core Graphics) drawing a glowing,
    trailing cloud that billows per frequency band. Persistent: shown per
    utterance via begin(), hidden again once it has faded out.
    """

    def __init__(self):
        from Cocoa import (
            NSApplication, NSBackingStoreBuffered, NSColor, NSFloatingWindowLevel,
            NSMakeRect, NSScreen, NSView, NSWindow, NSWindowStyleMaskBorderless,
        )
        import Quartz as Q

        self.Q = Q
        self.level_fn = lambda: None
        self.centroid_fn = lambda: 0.3
        self.bands_fn = lambda: np.zeros(len(BANDS))
        self.finished = lambda: False
        self.on_hidden = lambda: None
        owner = self

        class NebulaView(NSView):
            def isOpaque(self):
                return False

            def mouseDown_(self, _event):
                owner.fading_out = True

            def drawRect_(self, _rect):
                try:
                    owner._blit()
                except Exception:
                    import traceback

                    traceback.print_exc()

        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(1)  # accessory: no Dock icon, no focus steal
        vf = NSScreen.mainScreen().visibleFrame()
        x = vf.origin.x + vf.size.width - W - MARGIN
        y = vf.origin.y + MARGIN
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, W, H), NSWindowStyleMaskBorderless, NSBackingStoreBuffered, False
        )
        win.setOpaque_(False)
        win.setBackgroundColor_(NSColor.clearColor())
        win.setHasShadow_(False)
        win.setLevel_(NSFloatingWindowLevel)
        win.setCollectionBehavior_(1 | 16)  # all spaces, stationary
        win.setAlphaValue_(0.0)
        view = NebulaView.alloc().initWithFrame_(NSMakeRect(0, 0, W, H))
        win.setContentView_(view)
        self.app, self.win, self.view = app, win, view
        self._stopping = False
        self.visible = False
        self.alpha = 0.0
        self.fading_out = False
        self.dim = 0.5

        # Offscreen persistence buffers (ping-pong), at backing scale.
        self.scale = float(win.backingScaleFactor() or 2.0)
        self.bw, self.bh = int(W * self.scale), int(H * self.scale)
        self.cs = Q.CGColorSpaceCreateDeviceRGB()
        # 32-bit float components: with 8-bit buffers the faded trail rounds
        # to 1/255 and never reaches zero, leaving a faint permanent ghost.
        self.buf = [
            Q.CGBitmapContextCreate(
                None, self.bw, self.bh, 32, self.bw * 16, self.cs,
                Q.kCGBitmapFloatComponents | Q.kCGImageAlphaPremultipliedLast,
            )
            for _ in range(2)
        ]
        self.cur = 0

        # Animation state.
        rng = np.random.default_rng(7)
        self.rng = rng
        self.t = 0.0
        self.sat = 0.0
        self.base_hue = VOICE_HUES[VOICE]
        self.hue = self.base_hue
        self.idle = True
        self.prev_level = 0.0
        self.band_env = np.zeros(len(BANDS))
        self.puff_angle = rng.uniform(0, 2 * math.pi, PUFFS)
        self.puff_dist = rng.uniform(0.05, 0.55, PUFFS)
        self.puff_scale = rng.uniform(0.45, 0.85, PUFFS)
        self.puff_spin = rng.uniform(-0.45, 0.45, PUFFS)
        self.puff_bob = rng.uniform(0.4, 1.2, PUFFS)
        self.puff_hue = rng.uniform(-0.07, 0.07, PUFFS)
        self.puff_gain = rng.uniform(0.5, 1.3, PUFFS)
        self.puff_band = rng.integers(0, len(BANDS), PUFFS)
        self.buds: list[dict] = []
        self.sparks: list[dict] = []

    # -- lifecycle -------------------------------------------------------

    def begin(self, hue: float):
        """Show the cloud for a new utterance (called from any thread)."""
        self.base_hue = hue
        self.hue = hue
        self.sat = 0.0
        self.dim = 0.5
        self.alpha = 0.0
        self.fading_out = False
        self.band_env[:] = 0
        self.buds.clear()
        self.sparks.clear()
        for b in self.buf:
            self.Q.CGContextClearRect(b, self.Q.CGRectMake(0, 0, self.bw, self.bh))
        self.visible = True

    def stop_app(self):
        if self._stopping:
            return
        self._stopping = True
        from Cocoa import NSEvent, NSMakePoint

        self.app.stop_(None)
        ev = NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
            15, NSMakePoint(0, 0), 0, 0, 0, None, 0, 0, 0
        )
        self.app.postEvent_atStart_(ev, True)

    def run(self):
        from Cocoa import NSTimer

        self.timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(1.0 / FPS, True, self._tick)
        self.app.run()
        self.timer.invalidate()
        self.win.orderOut_(None)

    # -- simulation ------------------------------------------------------

    def _tick(self, _timer):
        if not self.visible:
            return
        if not self.win.isVisible():
            self.win.orderFrontRegardless()
        dt = 1.0 / FPS
        if self.finished():
            self.fading_out = True
        if self.fading_out:
            self.alpha -= dt / FADE_OUT
            if self.alpha <= 0.0:
                self.win.setAlphaValue_(0.0)
                self.win.orderOut_(None)
                self.visible = False
                self.on_hidden()
                return
        else:
            self.alpha = min(1.0, self.alpha + dt / FADE_IN)
        a = max(0.0, min(1.0, self.alpha))
        self.win.setAlphaValue_(a * a * (3 - 2 * a))  # ease in/out
        self.t += dt
        level = self.level_fn()
        if self.fading_out:
            level = 0.0  # keep the speaking look, just let the bands settle
        if level is None:
            target_dim = 0.5
            target_sat = 0.15
            target_hue = self.base_hue
            bands = np.full(len(BANDS), 0.12 + 0.08 * math.sin(self.t * 1.3))
        else:
            target_dim = 1.0
            target_sat = self.sat if self.fading_out else min(SAT_MAX, SAT_MIN + level * 7.0)
            # Sharper sounds nudge the hue toward the cooler side, dull ones warmer.
            target_hue = self.hue if self.fading_out else self.base_hue + (self.centroid_fn() - 0.5) * HUE_SPREAD
            bands = self.bands_fn()
            jump = level - self.prev_level
            if jump > ONSET_THRESH:
                self._sprout(bands, jump)
        self.dim += (target_dim - self.dim) * DIM_SMOOTH
        self.idle = self.dim < 0.75  # only gates the additive core glow now
        self.prev_level = level if level is not None else 0.0
        for b in range(len(BANDS)):
            k = BAND_ATTACK if bands[b] > self.band_env[b] else BAND_RELEASE
            self.band_env[b] += (bands[b] - self.band_env[b]) * k
        self.sat += (target_sat - self.sat) * 0.25
        self.hue += (target_hue - self.hue) * 0.12
        for bud in self.buds:
            bud["age"] += dt
        self.buds = [b for b in self.buds if b["age"] < b["life"]]
        for sp in self.sparks:
            sp["age"] += dt
            sp["x"] += sp["vx"] * dt
            sp["y"] += sp["vy"] * dt
            sp["vx"] *= 0.97
            sp["vy"] *= 0.97
        self.sparks = [s for s in self.sparks if s["age"] < s["life"]]
        self._render()
        self.view.setNeedsDisplay_(True)

    def _sprout(self, bands, jump):
        rng = self.rng
        strength = min(1.0, jump / 0.15)
        if len(self.buds) < BUD_MAX:
            self.buds.append({
                "angle": float(rng.uniform(0, 2 * math.pi)),
                "dist": float(rng.uniform(0.45, 0.7)),
                "size": float(rng.uniform(0.35, 0.75)) * (0.7 + 0.6 * strength),
                "age": 0.0,
                "life": float(rng.uniform(0.8, 1.5)),
                "band": int(np.argmax(bands)),
            })
        if not SPARKS:
            return
        n = int(round(SPARK_BURST[0] + (SPARK_BURST[1] - SPARK_BURST[0]) * strength))
        cx, cy = W / 2, H / 2
        for _ in range(n):
            if len(self.sparks) >= SPARK_MAX:
                break
            a = float(rng.uniform(0, 2 * math.pi))
            spd = float(rng.uniform(20, 70)) * (0.6 + 0.6 * strength)
            r0 = float(rng.uniform(15, 45))
            self.sparks.append({
                "x": cx + r0 * math.cos(a), "y": cy + r0 * math.sin(a),
                "vx": spd * math.cos(a), "vy": spd * math.sin(a),
                "age": 0.0, "life": float(rng.uniform(0.8, 1.6)),
                "size": float(rng.uniform(1.0, 2.0)),
                "hue_off": float(rng.choice([0.0, 0.0, 0.0, 0.5])),  # a rare one in the complementary hue
            })

    # -- rendering -------------------------------------------------------

    def _rgba(self, h, s, v, a):
        r, g, b = colorsys.hsv_to_rgb(h % 1.0, max(0.0, min(1.0, s)), max(0.0, min(1.0, v)))
        return (r, g, b, a)

    def _radial(self, ctx, x, y, r, stops):
        """stops: list of ((r,g,b,a), location) from centre to edge."""
        Q = self.Q
        comps = []
        locs = []
        for (cr, cg, cb, ca), loc in stops:
            comps += [cr, cg, cb, ca]
            locs.append(loc)
        grad = Q.CGGradientCreateWithColorComponents(self.cs, comps, locs, len(locs))
        c = Q.CGPointMake(x * self.scale, y * self.scale)
        Q.CGContextDrawRadialGradient(ctx, grad, c, 0.0, c, r * self.scale, 0)

    def _render(self):
        Q = self.Q
        src = self.buf[self.cur]
        dst = self.buf[1 - self.cur]
        bw, bh = self.bw, self.bh
        full = Q.CGRectMake(0, 0, bw, bh)

        # 1. Fade + expand the previous frame: this is the smoke trail.
        Q.CGContextClearRect(dst, full)
        img = Q.CGBitmapContextCreateImage(src)
        Q.CGContextSaveGState(dst)
        Q.CGContextSetAlpha(dst, TRAIL)
        Q.CGContextTranslateCTM(dst, bw / 2, bh / 2)
        Q.CGContextScaleCTM(dst, DRIFT, DRIFT)
        Q.CGContextRotateCTM(dst, 0.004)
        Q.CGContextTranslateCTM(dst, -bw / 2, -bh / 2)
        Q.CGContextDrawImage(dst, full, img)
        Q.CGContextRestoreGState(dst)

        cx, cy = W / 2, H / 2
        t = self.t
        dim = self.dim
        base = 52.0

        # 2. Cloud body (normal blend): puffs pushed out by their band.
        Q.CGContextSetBlendMode(dst, Q.kCGBlendModeNormal)
        puffs = []
        for i in range(PUFFS):
            env = float(self.band_env[self.puff_band[i]])
            a = self.puff_angle[i] + t * self.puff_spin[i]
            d = base * self.puff_dist[i] * (0.85 + 0.15 * math.sin(t * self.puff_bob[i] + i)) * (1.0 + BAND_PUSH * env)
            r = base * self.puff_scale[i] * (1.0 + self.puff_gain[i] * env)
            puffs.append((cx + d * math.cos(a), cy + d * math.sin(a), r, self.hue + self.puff_hue[i], env))
        for bud in self.buds:
            life = bud["age"] / bud["life"]
            grow = min(1.0, life * 4.0)
            fade = 1.0 - max(0.0, (life - 0.4) / 0.6)
            d = base * (bud["dist"] + 0.35 * life)
            r = base * bud["size"] * grow * (0.6 + 0.4 * fade)
            puffs.append((cx + d * math.cos(bud["angle"]), cy + d * math.sin(bud["angle"]), r, self.hue + 0.08, 1.0))
        for px, py, r, h, env in puffs:
            s = self.sat
            self._radial(dst, px, py, r, [
                (self._rgba(h, s * 0.35, 1.0, PUFF_ALPHA * dim), 0.0),
                (self._rgba(h, s * 0.8, 0.95, PUFF_ALPHA * 0.8 * dim), 0.45),
                (self._rgba(h, s, 0.85, PUFF_ALPHA * 0.35 * dim), 0.8),
                (self._rgba(h, s, 0.8, 0.0), 1.0),
            ])

        # 3. Additive highlights: bright core + band-lit rims + sparks.
        Q.CGContextSetBlendMode(dst, Q.kCGBlendModePlusLighter)
        self._radial(dst, cx, cy, base * 0.9, [
            (self._rgba(self.hue, self.sat * 0.2, 1.0, GLOW_ALPHA * 1.6 * dim), 0.0),
            (self._rgba(self.hue, self.sat * 0.6, 1.0, GLOW_ALPHA * 0.6 * dim), 0.5),
            (self._rgba(self.hue, self.sat, 1.0, 0.0), 1.0),
        ])
        for px, py, r, h, env in puffs:
            if env < 0.25:
                continue
            self._radial(dst, px, py, r * 0.8, [
                (self._rgba(h, self.sat * 0.5, 1.0, GLOW_ALPHA * env * dim), 0.0),
                (self._rgba(h, self.sat, 1.0, 0.0), 1.0),
            ])
        for sp in self.sparks:
            life = sp["age"] / sp["life"]
            a = (1.0 - life) ** 1.5
            twinkle = 0.7 + 0.3 * math.sin(t * 25 + sp["x"])
            self._radial(dst, sp["x"], sp["y"], sp["size"] * (1.0 + 1.5 * life), [
                (self._rgba(self.hue + sp["hue_off"], 0.25, 1.0, 0.55 * a * twinkle), 0.0),
                (self._rgba(self.hue + sp["hue_off"], 0.9, 1.0, 0.2 * a), 0.5),
                (self._rgba(self.hue + sp["hue_off"], 1.0, 1.0, 0.0), 1.0),
            ])
        self.cur = 1 - self.cur

    def _blit(self):
        from Cocoa import NSGraphicsContext

        Q = self.Q
        ctx = NSGraphicsContext.currentContext().CGContext()
        img = Q.CGBitmapContextCreateImage(self.buf[self.cur])
        Q.CGContextDrawImage(ctx, Q.CGRectMake(0, 0, W, H), img)


# -------------------------------------------------------------- server ---

class Server:
    """Owns the warm model, the overlay window and a Unix socket.

    Requests are JSON lines: {"cmd": "speak", "items": [[voice, text], ...]}
    or {"cmd": "stop"}. A new speak request replaces whatever is playing.
    """

    def __init__(self):
        self.requests: queue.Queue[dict] = queue.Queue()
        self.cancel = threading.Event()
        self.player: StreamPlayer | None = None
        self.gen_done = threading.Event()
        self.last_activity = time.time()
        self.busy = False

    def run(self) -> int:
        # Single instance: if the socket answers, someone else is serving.
        if _send({"cmd": "ping"}) is not None:
            log("server already running")
            return 0
        try:
            os.unlink(SOCK_FILE)
        except FileNotFoundError:
            pass
        with open(PID_FILE, "w") as fh:
            fh.write(str(os.getpid()))
        log("loading model")
        t0 = time.time()
        self.synth = Synth()
        self.overlay = NebulaOverlay()
        self.overlay.level_fn = lambda: (self.player.level if self.player else None)
        self.overlay.centroid_fn = lambda: (self.player.centroid if self.player else 0.3)
        self.overlay.bands_fn = lambda: (self.player.bands.copy() if self.player else np.zeros(len(BANDS)))
        self.overlay.finished = lambda: (
            self.gen_done.is_set() and (self.player is None or self.player.done.is_set() or self.player.near_end())
        )
        signal.signal(signal.SIGUSR1, lambda *_: self.stop_current())
        signal.signal(signal.SIGTERM, lambda *_: self.overlay.stop_app())
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(SOCK_FILE)
        self.sock.listen(8)
        threading.Thread(target=self._accept_loop, daemon=True).start()
        threading.Thread(target=self._work_loop, daemon=True).start()
        threading.Thread(target=self._idle_watch, daemon=True).start()
        log(f"ready in {time.time() - t0:.1f}s")
        self.overlay.run()
        try:
            os.unlink(SOCK_FILE)
        except FileNotFoundError:
            pass
        log("bye")
        return 0

    # -- socket ----------------------------------------------------------

    def _accept_loop(self):
        while True:
            conn, _ = self.sock.accept()
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket):
        with conn:
            data = b""
            while not data.endswith(b"\n"):
                chunk = conn.recv(65536)
                if not chunk:
                    break
                data += chunk
            try:
                req = json.loads(data.decode("utf-8"))
            except json.JSONDecodeError:
                conn.sendall(b'{"ok": false}\n')
                return
            self.last_activity = time.time()
            cmd = req.get("cmd")
            if cmd == "speak":
                self.stop_current()
                self.requests.put(req)
            elif cmd == "stop":
                self.stop_current()
            conn.sendall(b'{"ok": true}\n')

    # -- work ------------------------------------------------------------

    def stop_current(self):
        self.cancel.set()
        if self.player:
            self.player.done.set()
        self.overlay.fading_out = True

    def _work_loop(self):
        while True:
            req = self.requests.get()
            self.busy = True
            try:
                self._speak(req)
            except Exception as exc:
                log(f"speak failed: {exc}")
            finally:
                self.busy = False
                self.last_activity = time.time()

    def _speak(self, req: dict):
        items = req.get("items") or []
        if not items:
            return
        self.cancel = threading.Event()
        cancel = self.cancel
        self.gen_done = threading.Event()
        # Hide any previous cloud immediately; a new one fades in.
        self.overlay.begin(voice_hue(items[0][0]))
        player = StreamPlayer(24000)
        self.player = player
        threading.Thread(target=player.run, daemon=True).start()
        t0 = time.time()
        first = True
        for voice, text in items:
            for audio, sr in self.synth.sentences(text, voice, cancel):
                if cancel.is_set():
                    break
                if first:
                    log(f"first audio after {time.time() - t0:.2f}s")
                    first = False
                player.push(audio)
            if cancel.is_set():
                break
            if len(items) > 1:
                player.push(np.zeros(int(24000 * 0.6), dtype=np.float32))
        player.close()
        self.gen_done.set()
        player.done.wait()
        # Wait for the fade-out to hide the window before taking the next job.
        while self.overlay.visible and not cancel.is_set():
            time.sleep(0.05)
        if self.player is player:
            self.player = None

    def _idle_watch(self):
        while True:
            time.sleep(5)
            if not self.busy and not self.overlay.visible and time.time() - self.last_activity > IDLE_EXIT:
                log("idle, exiting")
                self.overlay.stop_app()
                return


# -------------------------------------------------------------- client ---

def _send(req: dict, timeout: float = 2.0) -> dict | None:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(SOCK_FILE)
            s.sendall((json.dumps(req) + "\n").encode("utf-8"))
            data = b""
            while not data.endswith(b"\n"):
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
            return json.loads(data.decode("utf-8") or "{}")
    except (OSError, json.JSONDecodeError):
        return None


def ensure_server() -> bool:
    if _send({"cmd": "ping"}) is not None:
        return True
    logf = open(LOG_FILE, "ab")
    subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "--serve"],
        stdin=subprocess.DEVNULL, stdout=logf, stderr=logf, start_new_session=True,
    )
    deadline = time.time() + SERVER_START_TIMEOUT
    while time.time() < deadline:
        time.sleep(0.2)
        if _send({"cmd": "ping"}) is not None:
            return True
    return False


# ---------------------------------------------------------------- main ---

def main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Speak text with Kokoro and a floating overlay.")
    ap.add_argument("text", nargs="*", help="text to speak (stdin if omitted)")
    ap.add_argument("--transcript", help="read the last assistant message from this JSONL")
    ap.add_argument("--voice", help="Kokoro voice name, or 'auto'")
    ap.add_argument("--session", help="session id, used by 'auto' voice selection")
    ap.add_argument("--list-voices", action="store_true")
    ap.add_argument("--demo", help="comma-separated voices; each introduces itself")
    ap.add_argument("--stop", action="store_true", help="fade out the currently running speaker")
    ap.add_argument("--serve", action="store_true", help="run the resident server")
    args = ap.parse_args(argv[1:])

    if args.serve:
        return Server().run()
    if args.list_voices:
        print("\n".join(list_voices()))
        return 0
    if args.stop:
        if _send({"cmd": "stop"}) is None:
            try:
                with open(PID_FILE) as fh:
                    os.kill(int(fh.read().strip()), signal.SIGUSR1)
            except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
                pass
        return 0

    voice = resolve_voice(args.voice, args.session)
    if args.demo:
        items = [
            [v.strip(), f"Hi John, this is {v.strip().split('_', 1)[1]}. Different agents could sound like me."]
            for v in args.demo.split(",")
        ]
    else:
        if args.transcript:
            text = clean_for_speech(last_assistant_text(args.transcript))
        elif args.text:
            text = clean_for_speech(" ".join(args.text))
        else:
            text = clean_for_speech(sys.stdin.read())
        if not text:
            return 0
        items = [[voice, text]]

    if not ensure_server():
        print("speak.py: server did not start; see ~/.claude/speak.log", file=sys.stderr)
        return 1
    resp = _send({"cmd": "speak", "items": items})
    return 0 if resp and resp.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
