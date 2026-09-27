#!/usr/bin/env python3
"""Pixel Cat - a little pixel-art cat that lives on your desktop.

She walks, sits and naps on top of your windows, jumps between them, climbs the
screen edges, chases your mouse, purrs when you pet her (rub her with the mouse),
wears a headset when music is playing, and gets hungry or bored if you forget her.

Usage:
    pixel-cat             start (the first time: pick a coat and a name)
    pixel-cat --call      call her to the mouse pointer (Ctrl+Alt+C)
    pixel-cat --setup     change her coat or name
    pixel-cat --quit      stop
    pixel-cat --shortcut  print the shortcut in use

Settings in ~/.config/pixel-cat/settings.ini:
    [pixel-cat]
    shortcut=<Primary><Alt>c     (none = off)
    size=2                       (1 = small, 2 = normal ~48 px, 3 = big)
"""

import ctypes
import ctypes.util
import heapq
import itertools
import json
import math
import os
import random
import re
import shutil
import signal
import struct
import subprocess
import sys
import threading
import time
import wave

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
from gi.repository import Gdk, GdkX11, Gio, GLib, Gtk  # noqa: E402

try:
    gi.require_version("Wnck", "3.0")
    from gi.repository import Wnck  # noqa: E402
except (ValueError, ImportError):
    Wnck = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sprites  # noqa: E402

APP_ID = "io.github.pixelcat.PixelCat"
CONFIG_DIR = os.path.join(GLib.get_user_config_dir(), "pixel-cat")
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.ini")
ACTIVE_SHORTCUT_FILE = os.path.join(CONFIG_DIR, "active-shortcut")
DATA_DIR = os.path.join(GLib.get_user_data_dir(), "pixel-cat")
STATE_FILE = os.path.join(DATA_DIR, "cat.json")
SOUND_DIR = os.path.join(GLib.get_user_cache_dir(), "pixel-cat", "sounds")
DEFAULT_SHORTCUTS = ["<Primary><Alt>c", "<Super><Shift>c", "<Primary><Alt>k"]
NAME_IDEAS = ["Cirmi", "Mazsola", "Luna", "Mimi", "Bogyó", "Pötyi", "Nala", "Szotyi", "Morzsa", "Milo",
              "Pamacs", "Csillag", "Bella", "Frida", "Maci"]
TICK_MS = 40
DEBUG_FILE = os.environ.get("PIXEL_CAT_DEBUG")
GRAVITY = 1700.0


def setting(key, default=None):
    kf = GLib.KeyFile()
    try:
        kf.load_from_file(SETTINGS_FILE, GLib.KeyFileFlags.NONE)
        value = kf.get_string("pixel-cat", key).strip()
        return value or default
    except GLib.Error:
        return default


def clamp(value, low, high):
    return max(low, min(high, value))


def rgba_visual(window):
    screen = window.get_screen()
    visual = screen.get_rgba_visual()
    if visual is not None and screen.is_composited():
        window.set_visual(visual)
        return True
    return False


def pointer():
    _screen, x, y = Gdk.Display.get_default().get_default_seat().get_pointer().get_position()
    return x, y


# --------------------------------------------------------------------------
# the cat's needs (saved between runs; time with the app closed doesn't count)
# --------------------------------------------------------------------------

class Pet:
    def __init__(self):
        self.name = None
        self.coat = "tabby"
        self.fullness = 80.0
        self.fun = 70.0
        self.energy = 80.0
        self.affection = 70.0
        self.muted = False
        self.x = None
        self.in_bed = False
        self.wake_at = 0.0
        self.explore = True
        self.hats = True
        self.adopted = None
        self.weather = True
        self.study = True
        self.stats = {}
        self.style = "classic"
        self.load()
        if self.style not in sprites.STYLES:
            self.style = "classic"
        if self.name and not self.adopted:
            self.adopted = time.strftime("%Y-%m-%d")

    def load(self):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        for key in ("name", "coat", "fullness", "fun", "energy", "affection", "muted", "x", "in_bed", "wake_at", "explore", "hats", "adopted", "weather", "study", "stats", "style"):
            if key in data:
                setattr(self, key, data[key])
        if self.coat not in sprites.COATS:
            self.coat = "tabby"

    def save(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        data = {k: getattr(self, k) for k in ("name", "coat", "fullness", "fun", "energy", "affection",
                                              "muted", "x", "in_bed", "wake_at", "explore", "hats",
                                              "adopted", "weather", "study", "stats", "style")}
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, STATE_FILE)

    def tick(self, dt, sleeping):
        self.fullness -= dt * 100 / (5 * 3600)
        self.fun -= dt * 100 / (2.5 * 3600)
        self.affection -= dt * 100 / (6 * 3600)
        self.energy += dt * (100 / (20 * 60) if sleeping else -100 / (4 * 3600))
        for key in ("fullness", "fun", "affection", "energy"):
            setattr(self, key, clamp(getattr(self, key), 0.0, 100.0))

    def add(self, **amounts):
        for key, value in amounts.items():
            setattr(self, key, clamp(getattr(self, key) + value, 0.0, 100.0))

    def count(self, what, n=1):
        self.stats[what] = self.stats.get(what, 0) + n

    def days_together(self):
        if not self.adopted:
            return 1
        try:
            start = time.mktime(time.strptime(self.adopted, "%Y-%m-%d"))
        except ValueError:
            return 1
        return max(1, int((time.time() - start) // 86400) + 1)

    def birthday(self):
        if not self.adopted:
            return False
        t = today()
        return self.adopted[5:] == time.strftime("%m-%d", t) and self.adopted[:4] != str(t.tm_year)

    def hat(self):
        if not self.hats:
            return None
        if self.birthday():
            return "party"
        month = today().tm_mon
        return {10: "pumpkin", 12: "santa"}.get(month)

    def mood(self):
        return (self.fullness * 1.2 + self.fun + self.energy * 0.6 + self.affection) / 3.8

    def mood_word(self):
        m = self.mood()
        return ("very happy" if m > 80 else "happy" if m > 60 else "okay" if m > 42
                else "a bit sad" if m > 28 else "sad")

    def needs_line(self):
        tummy = ("full" if self.fullness > 70 else "fine" if self.fullness > 40
                 else "peckish" if self.fullness > 20 else "hungry")
        play = ("entertained" if self.fun > 70 else "fine" if self.fun > 40
                else "a bit bored" if self.fun > 20 else "bored")
        rest = ("rested" if self.energy > 70 else "fine" if self.energy > 40
                else "sleepy" if self.energy > 20 else "very sleepy")
        return f"Tummy: {tummy} · Play: {play} · Energy: {rest}"


# --------------------------------------------------------------------------
# sounds: a soft purr and meows, made once with a bit of maths
# --------------------------------------------------------------------------

def _write_wav(path, samples, rate, peak):
    top = max(1e-6, max(abs(s) for s in samples))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(s / top * peak * 32767)) for s in samples))


def _purr(rate=22050, seconds=2.2):
    rnd = random.Random(7)
    low = 0.0
    out = []
    for i in range(int(rate * seconds)):
        t = i / rate
        low += (rnd.uniform(-1, 1) - low) * 0.06
        pulse = (0.5 + 0.5 * math.sin(2 * math.pi * 25 * t)) ** 3
        breath = 0.65 + 0.35 * math.sin(2 * math.pi * t / seconds * 2 - math.pi / 2)
        env = min(1.0, t / 0.2, (seconds - t) / 0.35)
        out.append(low * pulse * breath * env)
    return out


def _meow(rate=22050, seconds=0.7, pitch=1.0, trill=False):
    out = []
    phase = 0.0
    for i in range(int(rate * seconds)):
        t = i / rate
        u = t / seconds
        f0 = (430 + 330 * math.sin(math.pi * u ** 0.8) - 90 * u) * pitch
        f0 *= 1 + 0.018 * math.sin(2 * math.pi * 6 * t)
        phase += 2 * math.pi * f0 / rate
        formant = 1900 - 1150 * u            # "mi..." to "...ow"
        s = 0.0
        for k in range(1, 11):
            a = 0.35 / k + 1.4 / k * math.exp(-((k * f0 - formant) / 650) ** 2)
            s += a * math.sin(k * phase)
        env = min(1.0, t / 0.05) * (1.0 if u < 0.55 else max(0.0, (1 - u) / 0.45)) ** 1.5
        if trill:
            env *= 0.55 + 0.45 * math.sin(2 * math.pi * 26 * t)
        out.append(s * env)
    return out


class Sounds:
    VERSION = "1"

    def __init__(self, pet):
        self.pet = pet
        self.player = next((p for p in ("paplay", "pw-play", "aplay") if shutil.which(p)), None)
        self.ready = False
        self.last_meow = 0.0

    def prepare(self):
        stamp = os.path.join(SOUND_DIR, "version")
        try:
            with open(stamp) as f:
                if f.read().strip() == self.VERSION:
                    self.ready = True
                    return
        except OSError:
            pass
        try:
            os.makedirs(SOUND_DIR, exist_ok=True)
            _write_wav(os.path.join(SOUND_DIR, "purr.wav"), _purr(), 22050, 0.30)
            _write_wav(os.path.join(SOUND_DIR, "meow.wav"), _meow(), 22050, 0.22)
            _write_wav(os.path.join(SOUND_DIR, "meow2.wav"), _meow(seconds=0.5, pitch=1.2), 22050, 0.2)
            _write_wav(os.path.join(SOUND_DIR, "mrrp.wav"), _meow(seconds=0.35, pitch=0.9, trill=True),
                       22050, 0.2)
            with open(stamp, "w") as f:
                f.write(self.VERSION)
            self.ready = True
        except OSError:
            self.ready = False

    def play(self, name):
        if self.pet.muted or not self.player:
            return
        if not self.ready:
            self.prepare()
            if not self.ready:
                return
        if name.startswith("meow") or name == "mrrp":
            now = time.monotonic()
            if now - self.last_meow < 4:
                return
            self.last_meow = now
        path = os.path.join(SOUND_DIR, name + ".wav")
        try:
            subprocess.Popen([self.player, path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass


# --------------------------------------------------------------------------
# global keyboard shortcut (X11)
# --------------------------------------------------------------------------

class GlobalHotkey:
    """Grabs one key combination directly on the X server; tries the next one if taken."""

    KEY_PRESS = 2
    GRAB_MODE_ASYNC = 1
    LOCK_MASK = 1 << 1
    MOD2_MASK = 1 << 4
    ERROR_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)

    def __init__(self, callback):
        self.callback = callback
        self.x = None
        self.display = None
        self.active = None
        self._failed = False
        self._last_press = 0.0
        if not os.environ.get("DISPLAY"):
            return
        name = ctypes.util.find_library("X11")
        if not name:
            return
        try:
            x = ctypes.cdll.LoadLibrary(name)
        except OSError:
            return
        vp, ul, ui, i = ctypes.c_void_p, ctypes.c_ulong, ctypes.c_uint, ctypes.c_int
        x.XOpenDisplay.restype, x.XOpenDisplay.argtypes = vp, [ctypes.c_char_p]
        x.XDefaultRootWindow.restype, x.XDefaultRootWindow.argtypes = ul, [vp]
        x.XKeysymToKeycode.restype, x.XKeysymToKeycode.argtypes = ctypes.c_ubyte, [vp, ul]
        x.XGrabKey.argtypes = [vp, i, ui, ul, i, i, i]
        x.XUngrabKey.argtypes = [vp, i, ui, ul]
        x.XSync.argtypes = [vp, i]
        x.XPending.restype, x.XPending.argtypes = i, [vp]
        x.XNextEvent.argtypes = [vp, vp]
        x.XConnectionNumber.restype, x.XConnectionNumber.argtypes = i, [vp]
        x.XSetErrorHandler.restype, x.XSetErrorHandler.argtypes = vp, [vp]
        display = x.XOpenDisplay(None)
        if not display:
            return
        self.x, self.display = x, display
        self.root = x.XDefaultRootWindow(display)
        self._on_error_cb = self.ERROR_HANDLER(self._on_error)

    def _on_error(self, _display, _event):
        self._failed = True
        return 0

    @staticmethod
    def _x_modifiers(gdk_mods):
        m = Gdk.ModifierType
        xmods = 0
        if gdk_mods & m.SHIFT_MASK:
            xmods |= 1 << 0
        if gdk_mods & m.CONTROL_MASK:
            xmods |= 1 << 2
        if gdk_mods & m.MOD1_MASK:
            xmods |= 1 << 3
        if gdk_mods & (m.SUPER_MASK | m.MOD4_MASK):
            xmods |= 1 << 6
        return xmods

    def _variants(self, xmods):
        return [xmods | extra for extra in (0, self.LOCK_MASK, self.MOD2_MASK, self.LOCK_MASK | self.MOD2_MASK)]

    def _grab(self, accel):
        keyval, mods = Gtk.accelerator_parse(accel)
        if not keyval:
            return False
        keycode = self.x.XKeysymToKeycode(self.display, keyval)
        if not keycode:
            return False
        xmods = self._x_modifiers(mods)
        self._failed = False
        previous = self.x.XSetErrorHandler(ctypes.cast(self._on_error_cb, ctypes.c_void_p))
        for variant in self._variants(xmods):
            self.x.XGrabKey(self.display, keycode, variant, self.root, 0,
                            self.GRAB_MODE_ASYNC, self.GRAB_MODE_ASYNC)
        self.x.XSync(self.display, 0)
        if self._failed:
            for variant in self._variants(xmods):
                self.x.XUngrabKey(self.display, keycode, variant, self.root)
            self.x.XSync(self.display, 0)
        self.x.XSetErrorHandler(previous)
        return not self._failed

    def start(self, candidates):
        if self.display is None:
            return None
        for accel in candidates:
            if self._grab(accel):
                self.active = accel
                GLib.io_add_watch(self.x.XConnectionNumber(self.display), GLib.PRIORITY_DEFAULT,
                                  GLib.IOCondition.IN, self._on_x_event)
                self._on_x_event()
                return accel
        return None

    def _on_x_event(self, *_):
        event = ctypes.create_string_buffer(256)
        while self.x.XPending(self.display):
            self.x.XNextEvent(self.display, event)
            if ctypes.c_int.from_buffer(event).value == self.KEY_PRESS:
                now = time.monotonic()
                if now - self._last_press > 0.4:
                    GLib.idle_add(lambda: self.callback() and False)
                self._last_press = now
        return True


def pretty_accel(accel):
    keyval, mods = Gtk.accelerator_parse(accel or "")
    return Gtk.accelerator_get_label(keyval, mods) if keyval else (accel or "")


# --------------------------------------------------------------------------
# is music playing? (any MPRIS player: Spotify, browsers, ...)
# --------------------------------------------------------------------------

class Music:
    def __init__(self):
        self.playing = False
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            self.bus = None
            return
        GLib.timeout_add_seconds(2, self._poll)

    def _poll(self):
        self.bus.call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "ListNames",
                      None, GLib.VariantType("(as)"), Gio.DBusCallFlags.NONE, 800, None, self._got_names)
        return True

    def _got_names(self, bus, result):
        try:
            names = bus.call_finish(result).unpack()[0]
        except GLib.Error:
            return
        players = [n for n in names if n.startswith("org.mpris.MediaPlayer2.")]
        if not players:
            self.playing = False
            return
        answers = []

        def got(bus_, res):
            try:
                answers.append(bus_.call_finish(res).unpack()[0] == "Playing")
            except GLib.Error:
                answers.append(False)
            if len(answers) == len(players):
                self.playing = any(answers)

        for name in players:
            bus.call(name, "/org/mpris/MediaPlayer2", "org.freedesktop.DBus.Properties", "Get",
                     GLib.Variant("(ss)", ("org.mpris.MediaPlayer2.Player", "PlaybackStatus")),
                     GLib.VariantType("(v)"), Gio.DBusCallFlags.NONE, 800, None, got)


# --------------------------------------------------------------------------
# the world: where the cat can stand (window tops, the panel, screen edges)
# --------------------------------------------------------------------------

class Seg:
    """A horizontal line the cat can stand on."""
    __slots__ = ("key", "y", "x1", "x2", "win_x", "kind", "note", "win_w", "win_h", "win_y")

    def __init__(self, key, y, x1, x2, win_x=0, kind="floor", note=False, win_w=0, win_h=0, win_y=None):
        self.key, self.y, self.x1, self.x2 = key, y, x1, x2
        self.win_x, self.kind, self.note = win_x, kind, note
        self.win_w, self.win_h = win_w, win_h
        self.win_y = y if win_y is None else win_y

    def shift(self, dx, dy):
        self.x1 += dx
        self.x2 += dx
        self.y += dy
        self.win_x += dx
        self.win_y += dy


class World:
    MIN_WIDTH = 70

    def __init__(self):
        self.screen = Wnck.Screen.get_default() if Wnck else None
        self.segments = []
        self.monitors = []
        self.bounds = (0, 0, 1, 1)
        self.fullscreen = False
        self.stacking = ()
        self._extents = {}
        self.windows = []
        self.ledges = {}             # xid -> [(dx, dy, length)] relative to the window
        self.on_stacking_changed = None
        if self.screen is not None:
            self.screen.connect("window-stacking-changed", lambda *_: self.on_stacking_changed
                                and self.on_stacking_changed())
            self.screen.connect("window-closed", lambda _s, w: self._extents.pop(w.get_xid(), None))

    def _frame_extents(self, xid):
        """Invisible shadow borders around client-side decorated windows."""
        if xid in self._extents:
            return self._extents[xid]
        extents = (0, 0, 0, 0)
        try:
            display = Gdk.Display.get_default()
            win = GdkX11.X11Window.foreign_new_for_display(display, xid)
            if win is not None:
                ok, _type, _fmt, data = Gdk.property_get(
                    win, Gdk.Atom.intern("_GTK_FRAME_EXTENTS", False), Gdk.Atom.intern("CARDINAL", False),
                    0, 16, False)
                if ok and data:
                    extents = tuple(int(v) for v in self._unpack_longs(bytes(data)))
        except Exception:
            extents = (0, 0, 0, 0)
        self._extents[xid] = extents
        return extents

    @staticmethod
    def _unpack_longs(raw):
        size = ctypes.sizeof(ctypes.c_long)
        if len(raw) >= 4 * size:
            return struct.unpack(f"=4{'l' if size == 4 else 'q'}", raw[:4 * size])
        if len(raw) >= 16:
            return struct.unpack("=4l", raw[:16])
        return (0, 0, 0, 0)

    @staticmethod
    def _read_monitors():
        """[(geometry, workarea)] for every screen. PIXEL_CAT_MONITORS="x,y,w,h,wx,wy,ww,wh;..."
        pretends there are other screens (for testing on one screen)."""
        fake = os.environ.get("PIXEL_CAT_MONITORS")
        if fake:
            def rect(x, y, w, h):
                r = Gdk.Rectangle()
                r.x, r.y, r.width, r.height = x, y, w, h
                return r
            out = []
            for part in fake.split(";"):
                v = [int(n) for n in part.split(",")]
                out.append((rect(*v[:4]), rect(*v[4:8])))
            return out
        display = Gdk.Display.get_default()
        return [(display.get_monitor(i).get_geometry(), display.get_monitor(i).get_workarea())
                for i in range(display.get_n_monitors())]

    def refresh(self):
        self.monitors = self._read_monitors()
        segs = []
        x0 = y0 = 10 ** 9
        x1 = y1 = -10 ** 9
        floors = []
        for i, (geo, area) in enumerate(self.monitors):
            floors.append([area.y + area.height, geo.x, geo.x + geo.width, i])
            x0, y0 = min(x0, geo.x), min(y0, geo.y)
            x1, y1 = max(x1, geo.x + geo.width), max(y1, geo.y + geo.height)
        # screens side by side with the bottom at the same height: one floor she can walk across
        floors.sort(key=lambda f: f[1])
        merged = []
        for f in floors:
            if merged and merged[-1][0] == f[0] and abs(merged[-1][2] - f[1]) <= 1:
                merged[-1][2] = f[2]
            else:
                merged.append(f)
        for y, a, b, i in merged:
            segs.append(Seg(("floor", i), y, a, b))
        self.bounds = (x0, y0, x1, y1)
        self.fullscreen = False
        self.fullscreen_rects = []
        if self.screen is not None:
            self.screen.force_update()
            workspace = self.screen.get_active_workspace()
            usable = []
            own = os.getpid()
            for w in self.screen.get_windows_stacked():
                if w.get_pid() == own:
                    continue                          # her own setup/diary windows
                if w.is_minimized() or w.is_shaded():
                    continue
                if w.get_window_type() not in (Wnck.WindowType.NORMAL, Wnck.WindowType.DIALOG,
                                               Wnck.WindowType.UTILITY):
                    continue
                if workspace is not None and not w.is_visible_on_workspace(workspace):
                    continue
                x, y, width, height = w.get_geometry()
                left, right, top, bottom = self._frame_extents(w.get_xid())
                usable.append((w, x + left, y + top, width - left - right, height - top - bottom))
            active = self.screen.get_active_window()
            self.fullscreen = bool(active is not None and active.get_pid() != own and active.is_fullscreen()
                                   and not active.is_minimized())
            self.fullscreen_rects = [(x, y, width, height) for w, x, y, width, height in usable
                                     if w.is_fullscreen()]
            self.stacking = tuple(u[0].get_xid() for u in usable)
            self.windows = [(u[0].get_xid(), u[1], u[2], u[3], u[4]) for u in usable]
            for index, (w, x, y, width, height) in enumerate(usable):
                xid = w.get_xid()
                above = usable[index + 1:]
                if width >= self.MIN_WIDTH and y >= y0 + 40:
                    pieces = [(x + 3, x + width - 3)]
                    for _w2, ox, oy, ow, oh in above:
                        if oy <= y + 1 and oy + oh > y:
                            pieces = self._cut(pieces, ox, ox + ow)
                    title = (w.get_name() or "").lower()
                    klass = ((w.get_class_group_name() or "") + " " + (w.get_class_instance_name() or "")).lower()
                    note = "quick note" in title or "quick_notes" in klass or "quick-notes" in klass
                    for a, b in pieces:
                        if b - a >= 40:
                            segs.append(Seg(("win", xid), y, a, b, x, "win", note, width, height))
                # lines inside the window (text boxes, chat bubbles, progress bars ...)
                for dx, dy, length in self.ledges.get(xid, ()):
                    ly = y + dy
                    if ly > y + height - 8:
                        continue
                    pieces = [(x + dx, x + dx + length)]
                    for _w2, ox, oy, ow, oh in above:
                        if oy <= ly + 1 and oy + oh > ly - 30:
                            pieces = self._cut(pieces, ox, ox + ow)
                    for a, b in pieces:
                        if b - a >= 60:
                            segs.append(Seg(("ledge", xid, dy), ly, a, b, x, "ledge", False, width, height, y))
        self.segments = segs

    def scan_ledges(self, exclude):
        """Look at the screen for long horizontal lines inside windows (the top of a text box,
        a chat bubble, a video's progress bar ...) that she could sit on. It only sees lines,
        not what they are. Everything heavy happens in cairo; Python only scans bit rows."""
        if not self.windows:
            self.ledges = {}
            return
        X0, Y0, X1, Y1 = self.bounds
        W, H = X1 - X0, Y1 - Y0
        pb = Gdk.pixbuf_get_from_window(Gdk.get_default_root_window(), X0, Y0, W, H)
        if pb is None:
            return
        img = cairo.ImageSurface(cairo.FORMAT_RGB24, W, H)
        cr = cairo.Context(img)
        Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0)
        cr.paint()
        # paint over the cat (and her things) by stretching the pixel column next to her,
        # so a line she's standing on still looks like one line
        for ex_x, ex_y, ex_w, ex_h in exclude:
            rx, ry = int(ex_x - X0), int(ex_y - Y0)
            source_x = rx - 1 if rx >= 1 else min(W - 1, rx + int(ex_w))
            column = img.create_for_rectangle(source_x, 0, 1, H)
            cr.save()
            cr.rectangle(rx, ry, ex_w, ex_h)
            cr.clip()
            cr.set_source_surface(column, rx if source_x < rx else rx + ex_w - 1, 0)
            cr.get_source().set_extend(cairo.EXTEND_PAD)
            cr.paint()
            cr.restore()
        diff = cairo.ImageSurface(cairo.FORMAT_RGB24, W, H)
        cr = cairo.Context(diff)
        cr.set_source_surface(img, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_DIFFERENCE)
        cr.set_source_surface(img, 0, -1)          # |row y - row y+1|
        cr.paint()
        BW = W // 4
        small = cairo.ImageSurface(cairo.FORMAT_RGB24, BW, H)
        cr = cairo.Context(small)
        cr.scale(BW / W, 1)
        cr.set_source_surface(diff, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_GOOD)
        cr.paint()
        small.flush()
        data = bytes(small.get_data())
        stride = small.get_stride()
        table = bytes(1 if v > 16 else 0 for v in range(256))
        rows = []
        for y in range(H):
            row = data[y * stride: y * stride + BW * 4]
            bits = (int.from_bytes(row[0::4].translate(table), "big")
                    | int.from_bytes(row[1::4].translate(table), "big")
                    | int.from_bytes(row[2::4].translate(table), "big"))
            rows.append(bits.to_bytes(BW, "big"))
        min_cells = 36                              # 144 px
        run = re.compile(rb"\x01{%d,}" % min_cells)
        needle = b"\x01" * min_cells
        found = {}
        for y in range(6, H - 2):
            bits = rows[y]
            if needle not in bits:
                continue
            for m in run.finditer(bits):
                c0, c1 = m.span()
                busy = sum(rows[yy][c0:c1].count(1) for yy in range(y - 5, y))
                if busy > (c1 - c0) * 5 * 0.2:
                    continue                         # text, pictures: not a clean shelf
                ly, lx1, lx2 = Y0 + y + 1, X0 + c0 * 4, X0 + c1 * 4
                owner = None
                for xid, wx, wy, ww, wh in reversed(self.windows):
                    if wx <= (lx1 + lx2) / 2 <= wx + ww and wy <= ly <= wy + wh:
                        owner = (xid, wx, wy, ww, wh)
                        break
                if owner is None:
                    continue
                xid, wx, wy, ww, wh = owner
                if ly < wy + 45 or ly > wy + wh - 12:
                    continue
                lx1, lx2 = max(lx1, wx + 4), min(lx2, wx + ww - 4)
                if lx2 - lx1 < 120:
                    continue
                found.setdefault(xid, []).append((lx1 - wx, ly - wy, lx2 - lx1))
        ledges = {}
        for xid, items in found.items():
            kept = []
            for dx, dy, length in sorted(items, key=lambda t: t[1]):
                # a thick line gives two edges a few pixels apart: keep the upper one
                if any(abs(dy - ky) < 12 and dx < kx + kl and kx < dx + length for kx, ky, kl in kept):
                    continue
                kept.append((dx, dy, length))
            ledges[xid] = sorted(kept, key=lambda t: -t[2])[:14]
        self.ledges = ledges

    @staticmethod
    def _cut(pieces, a, b):
        out = []
        for p1, p2 in pieces:
            if b <= p1 or a >= p2:
                out.append((p1, p2))
                continue
            if a > p1:
                out.append((p1, a))
            if b < p2:
                out.append((b, p2))
        return out

    def window_pos(self, xid):
        """Where a window is right now (Wnck keeps this up to date as it moves).
        Looked up fresh every time: holding on to a window that has closed crashes Wnck."""
        if xid not in self.stacking:
            return None
        w = Wnck.Window.get(xid)
        if w is None:
            return None
        try:
            x, y, _width, _height = w.get_geometry()
        except Exception:
            return None
        left, _right, top, _bottom = self._frame_extents(xid)
        return x + left, y + top

    def monitor_at(self, x, y):
        for geo, area in self.monitors:
            if geo.x <= x < geo.x + geo.width and geo.y <= y < geo.y + geo.height:
                return geo, area
        # between screens: the nearest one
        best = None
        for geo, area in self.monitors:
            dx = max(geo.x - x, 0, x - (geo.x + geo.width))
            dy = max(geo.y - y, 0, y - (geo.y + geo.height))
            if best is None or dx + dy < best[0]:
                best = (dx + dy, geo, area)
        return (best[1], best[2]) if best else (None, None)

    def covered(self, x, y):
        """Is this spot under a fullscreen window?"""
        return any(fx <= x < fx + fw and fy <= y < fy + fh for fx, fy, fw, fh in self.fullscreen_rects)

    def free_monitor(self):
        """A screen with no fullscreen window on it, or None."""
        for geo, area in self.monitors:
            cx, cy = geo.x + geo.width / 2, geo.y + geo.height / 2
            if not self.covered(cx, cy):
                return geo, area
        return None

    def floor_for(self, x):
        best = None
        for seg in self.segments:
            if seg.kind == "floor":
                if seg.x1 <= x <= seg.x2:
                    return seg
                if best is None or abs((seg.x1 + seg.x2) / 2 - x) < abs((best.x1 + best.x2) / 2 - x):
                    best = seg
        return best

    def find(self, key, x):
        if key[0] == "ledge":
            # lines are found again every few seconds; allow a pixel or two of difference
            same = [s for s in self.segments if s.key[0] == "ledge" and s.key[1] == key[1]
                    and abs(s.key[2] - key[2]) <= 3]
        else:
            same = [s for s in self.segments if s.key == key]
        for s in same:
            if s.x1 - 2 <= x <= s.x2 + 2:
                return s, same
        return None, same

    def landing(self, x, y_from, y_to):
        """The first surface crossed when falling from y_from to y_to at x."""
        best = None
        for s in self.segments:
            if s.x1 <= x <= s.x2 and y_from - 1 <= s.y <= y_to:
                if best is None or s.y < best.y:
                    best = s
        return best


# --------------------------------------------------------------------------
# little floating things: hearts, notes, zzz, thought bubbles, poofs
# --------------------------------------------------------------------------

def text_surface(text, scale, size=None):
    size = size or 7 * scale
    probe = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1))
    probe.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    probe.set_font_size(size)
    width = probe.text_extents(text).x_advance
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, int(width + size * 0.6), int(size * 1.4))
    cr = cairo.Context(surface)
    cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cr.set_font_size(size)
    cr.move_to(scale, size)
    cr.text_path(text)
    cr.set_source_rgb(0.23, 0.2, 0.28)
    cr.set_line_width(scale * 1.2)
    cr.stroke_preserve()
    cr.set_source_rgb(0.96, 0.96, 0.98)
    cr.fill()
    surface.flush()
    return surface


class Floater(Gtk.Window):
    def __init__(self, surface, x, y, rise=36, life=1.4, drift=0.0, hold=0.0):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.surface = surface
        self.w, self.h = surface.get_width(), surface.get_height()
        self.x0, self.y0 = x - self.w / 2, y - self.h
        self.rise, self.life, self.drift, self.hold = rise, life, drift, hold
        self.t = 0.0
        self.alpha = 1.0
        self.set_app_paintable(True)
        composited = rgba_visual(self)
        self.set_default_size(self.w, self.h)
        self.resize(self.w, self.h)
        self.input_shape_combine_region(cairo.Region())          # clicks go through
        if not composited:
            self.shape_combine_region(Gdk.cairo_region_create_from_surface(surface))
        self.connect("draw", self._draw)
        self.move(int(self.x0), int(self.y0))
        self.show()
        GLib.timeout_add(TICK_MS, self._tick)

    def _tick(self):
        self.t += TICK_MS / 1000
        u = clamp((self.t - self.hold) / max(0.01, self.life - self.hold), 0, 1)
        ease = 1 - (1 - u) ** 2
        self.move(int(self.x0 + self.drift * math.sin(u * math.pi * 2)), int(self.y0 - self.rise * ease))
        self.alpha = 1.0 if u < 0.6 else max(0.0, (1 - u) / 0.4)
        self.queue_draw()
        if self.t >= self.life:
            self.destroy()
            return False
        return True

    def _draw(self, _w, cr):
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        cr.set_source_surface(self.surface, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_NEAREST)
        cr.paint_with_alpha(self.alpha)
        return True


class Prop(Gtk.Window):
    """A treat or a ball of yarn lying on the same surface as the cat."""

    def __init__(self, kind, scale, x, y, seg_key):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.kind = kind
        self.scale = scale
        part = {"treat": "FISH", "yarn": "YARN", "mug": "MUG"}[kind]
        self.images = [sprites.render(sprites.PARTS[part], "tabby", scale)]
        if kind == "yarn":
            import copy
            g = copy.deepcopy(sprites.PARTS["YARN"])
            rotated = [list(row) for row in zip(*g[::-1])]
            self.images.append(sprites.render(rotated, "tabby", scale))
        self.w = max(i.get_width() for i in self.images)
        self.h = max(i.get_height() for i in self.images)
        self.x, self.y = x, y
        self.vx = self.vy = 0.0
        self.seg_key = seg_key
        self.landed = False
        self.fell = False
        self.rolled = 0.0
        self.born = time.monotonic()
        self.set_app_paintable(True)
        self.composited = rgba_visual(self)
        self.set_default_size(self.w, self.h)
        self.resize(self.w, self.h)
        self.input_shape_combine_region(cairo.Region())
        self.connect("draw", self._draw)
        self._shape()
        self.place()
        self.show()

    def image(self):
        return self.images[int(self.rolled / (4 * self.scale)) % len(self.images)]

    def _shape(self):
        if not self.composited:
            self.shape_combine_region(Gdk.cairo_region_create_from_surface(self.image()))

    def place(self):
        self.move(int(self.x - self.w / 2), int(self.y - self.h))

    def update(self, dt, world):
        seg, _same = world.find(self.seg_key, self.x)
        if seg is None:
            seg = world.landing(self.x, self.y, 10 ** 6) or world.floor_for(self.x)
            if seg is None:
                return
            self.seg_key = seg.key
            if self.landed:                       # pushed over an edge: now it falls
                self.landed = False
                self.fell = True
        if not self.landed:
            self.vy += GRAVITY * dt
            self.y += self.vy * dt
            if self.y >= seg.y:
                self.y, self.vy, self.landed = seg.y, 0.0, True
        else:
            self.y = seg.y
        if self.vx:
            before = self.image()
            self.x += self.vx * dt
            self.rolled += abs(self.vx * dt)
            self.vx *= 0.975
            if abs(self.vx) < 12:
                self.vx = 0.0
            if self.kind != "mug" and (self.x < seg.x1 + 6 or self.x > seg.x2 - 6):
                self.x = clamp(self.x, seg.x1 + 6, seg.x2 - 6)
                self.vx = -self.vx * 0.6
            if self.image() is not before:
                self._shape()
                self.queue_draw()
        self.place()

    def _draw(self, _w, cr):
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        img = self.image()
        cr.set_source_surface(img, (self.w - img.get_width()) / 2, self.h - img.get_height())
        cr.get_source().set_filter(cairo.FILTER_NEAREST)
        cr.paint()
        return True


class Overlay(Gtk.Window):
    """A click-through picture that the cat moves around (her parachute, her bed)."""

    def __init__(self, surface):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.surface = surface
        self.w, self.h = surface.get_width(), surface.get_height()
        self.set_app_paintable(True)
        composited = rgba_visual(self)
        self.set_default_size(self.w, self.h)
        self.resize(self.w, self.h)
        self.input_shape_combine_region(cairo.Region())
        if not composited:
            self.shape_combine_region(Gdk.cairo_region_create_from_surface(surface))
        self.connect("draw", self._draw)
        self.pos = None

    def place(self, x, y):
        pos = (int(round(x)), int(round(y)))
        if pos != self.pos:
            self.pos = pos
            self.move(*pos)

    def _draw(self, _w, cr):
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        cr.set_source_surface(self.surface, 0, 0)
        cr.paint()
        return True


class Trampoline(Overlay):
    def __init__(self, scale, cx, base):
        self.images = [sprites.render(sprites.PARTS[n], "tabby", scale) for n in ("TRAMPOLINE", "TRAMPOLINE_DOWN")]
        super().__init__(self.images[0])
        self.cx, self.base = cx, base
        top = base - self.h + 1 * scale
        self.mat_y = top + 2 * scale            # where her paws touch the mat
        self.place(cx - self.w / 2, top)
        self.show()

    def squash(self):
        self.surface = self.images[1]
        self.queue_draw()
        GLib.timeout_add(160, self._unsquash)

    def _unsquash(self):
        self.surface = self.images[0]
        self.queue_draw()
        return False


class IdleClock:
    """How long since you last touched the mouse or keyboard (X screensaver extension;
    falls back to watching the mouse only)."""

    class _Info(ctypes.Structure):
        _fields_ = [("window", ctypes.c_ulong), ("state", ctypes.c_int), ("kind", ctypes.c_int),
                    ("til_or_since", ctypes.c_ulong), ("idle", ctypes.c_ulong), ("event_mask", ctypes.c_ulong)]

    def __init__(self):
        self.xss = None
        self.last_pointer = None
        self.last_move = time.monotonic()
        try:
            xss = ctypes.cdll.LoadLibrary(ctypes.util.find_library("Xss") or "libXss.so.1")
            x11 = ctypes.cdll.LoadLibrary(ctypes.util.find_library("X11"))
            x11.XOpenDisplay.restype = ctypes.c_void_p
            x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
            x11.XDefaultRootWindow.restype = ctypes.c_ulong
            x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
            xss.XScreenSaverAllocInfo.restype = ctypes.POINTER(self._Info)
            xss.XScreenSaverQueryInfo.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(self._Info)]
            display = x11.XOpenDisplay(None)
            if display:
                self.xss, self.display = xss, display
                self.root = x11.XDefaultRootWindow(display)
                self.info = xss.XScreenSaverAllocInfo()
        except (OSError, TypeError, AttributeError):
            self.xss = None

    def seconds(self):
        if self.xss is not None and self.xss.XScreenSaverQueryInfo(self.display, self.root, self.info):
            return self.info.contents.idle / 1000.0
        now = time.monotonic()
        p = pointer()
        if p != self.last_pointer:
            self.last_pointer, self.last_move = p, now
        return now - self.last_move


class Weather:
    """Budapest's weather every 30 minutes from open-meteo.com (free, no account)."""

    URL = ("https://api.open-meteo.com/v1/forecast?latitude=47.498&longitude=19.040"
           "&current=weather_code,temperature_2m&timezone=Europe%2FBudapest")

    def __init__(self, pet):
        self.pet = pet
        self.kind = None          # "rain", "snow" or None
        self.text = None
        self._check()
        GLib.timeout_add_seconds(30 * 60, self._check)

    def _check(self):
        if self.pet.weather and not os.environ.get("PIXEL_CAT_WEATHER"):
            threading.Thread(target=self._fetch, daemon=True).start()
        elif os.environ.get("PIXEL_CAT_WEATHER"):
            self.kind = os.environ["PIXEL_CAT_WEATHER"]          # for testing: rain / snow
            self.text = f"{self.kind} (test)"
        return True

    def _fetch(self):
        import urllib.request
        try:
            with urllib.request.urlopen(self.URL, timeout=15) as r:
                data = json.load(r)["current"]
        except Exception:
            return
        code = int(data.get("weather_code", 0))
        temp = data.get("temperature_2m")
        if code in (71, 73, 75, 77, 85, 86):
            kind, word = "snow", "snowing"
        elif code in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99):
            kind, word = "rain", "raining"
        else:
            kind, word = None, {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "cloudy",
                                45: "foggy", 48: "foggy"}.get(code, "dry")
        GLib.idle_add(lambda: (setattr(self, "kind", kind),
                               setattr(self, "text", f"Budapest: {word}, {temp:.0f}°C" if temp is not None
                                       else f"Budapest: {word}")) and False)


class Butterfly(Gtk.Window):
    """Flutters in, dances around her for a while (she tries to catch it), then leaves.
    Now and then it lands on her head instead."""

    def __init__(self, scale, x, y):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.scale = scale
        self.frames = [sprites.render(sprites.PARTS[n], "tabby", scale) for n in ("BUTTERFLY0", "BUTTERFLY1")]
        self.w, self.h = self.frames[0].get_width(), self.frames[0].get_height()
        self.x, self.y = float(x), float(y)
        self.mode = "enter"
        self.t = 0.0
        self.mode_t = 0.0
        self.target = (x, y)
        self.speed = 90.0
        self.boost = 0.0
        self.set_app_paintable(True)
        self.composited = rgba_visual(self)
        self.set_default_size(self.w, self.h)
        self.resize(self.w, self.h)
        self.input_shape_combine_region(cairo.Region())
        self.connect("draw", self._draw)
        self.frame = 0
        self._shape()
        self.move(int(self.x), int(self.y))
        self.show()

    def _shape(self):
        if not self.composited:
            self.shape_combine_region(Gdk.cairo_region_create_from_surface(self.frames[self.frame]))

    def chaseable(self):
        return self.mode in ("hover",)

    def dodge(self):
        self.target = (self.x + random.uniform(-90, 90), self.y - random.uniform(60, 110))
        self.boost = 0.6

    def leave(self):
        self.mode, self.mode_t = "leave", 0.0

    def settle_on(self, cat):
        self.mode, self.mode_t = "perch", 0.0
        self.cat = cat

    def update(self, dt, cat):
        """Returns False once it has flown away."""
        self.t += dt
        self.mode_t += dt
        x0, y0, x1, _y1 = cat.world.bounds
        hx, hy = cat.head_point()
        if self.mode == "enter":
            self.target = (cat.x + random.uniform(-60, 60), hy - 90)
            if math.hypot(self.x - cat.x, self.y - (hy - 90)) < 120:
                self.mode, self.mode_t = "hover", 0.0
        elif self.mode == "hover":
            if self.mode_t > 22:
                self.leave()
            elif random.random() < dt * 0.8 and self.boost <= 0:
                self.target = (cat.x + random.uniform(-160, 160), hy - random.uniform(50, 170))
        elif self.mode == "perch":
            self.target = (hx - self.w / 2 + 2 * self.scale, hy - self.h + 5 * self.scale)
            if self.mode_t > 4:
                self.leave()
        if self.mode == "leave":
            self.target = (x1 + 60 if self.x > (x0 + x1) / 2 else x0 - 60, self.y - 80)
            if self.x < x0 - 40 or self.x > x1 + 40 or self.y < y0 - 40:
                self.destroy()
                return False
        speed = self.speed * (2.8 if self.boost > 0 else 1.0) * (1.5 if self.mode == "leave" else 1.0)
        self.boost -= dt
        tx, ty = self.target
        dx, dy = tx - self.x, ty - self.y
        dist = math.hypot(dx, dy)
        if self.mode == "perch" and dist < 3:
            self.x, self.y = tx, ty
        elif dist > 1:
            step = min(dist, speed * dt)
            self.x += dx / dist * step
            self.y += dy / dist * step + math.sin(self.t * 9) * 18 * dt
        flap = int(self.t / (0.35 if self.mode == "perch" else 0.12)) % 2
        if flap != self.frame:
            self.frame = flap
            self._shape()
            self.queue_draw()
        self.move(int(self.x), int(self.y))
        return True

    def _draw(self, _w, cr):
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        cr.set_source_surface(self.frames[self.frame], 0, 0)
        cr.paint()
        return True


class Rope(Gtk.Window):
    """The grappling hook's rope, from her paws up to the hook."""

    def __init__(self, scale):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.scale = scale
        self.hook = sprites.render(sprites.PARTS["HOOK"], "tabby", scale)
        self.surface = None
        self.set_app_paintable(True)
        self.composited = rgba_visual(self)
        self.input_shape_combine_region(cairo.Region())
        self.connect("draw", self._draw)

    def set_ends(self, a, b, hooked=False):
        sc = self.scale
        pad = 6 * sc
        x0, y0 = int(min(a[0], b[0]) - pad), int(min(a[1], b[1]) - pad)
        w, h = int(abs(a[0] - b[0]) + 2 * pad), int(abs(a[1] - b[1]) + 2 * pad)
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, max(1, w), max(1, h))
        cr = cairo.Context(surface)
        steps = max(1, int(math.hypot(b[0] - a[0], b[1] - a[1]) / sc))
        for i in range(steps + 1):
            px = a[0] + (b[0] - a[0]) * i / steps - x0
            py = a[1] + (b[1] - a[1]) * i / steps - y0
            cr.set_source_rgb(0.23, 0.14, 0.09)
            cr.rectangle(int(px / sc) * sc - sc / 2, int(py / sc) * sc, sc, sc)
            cr.fill()
        if hooked:
            cr.set_source_surface(self.hook, b[0] - x0 - self.hook.get_width() / 2, b[1] - y0 - sc)
            cr.paint()
        surface.flush()
        self.surface = surface
        self.resize(max(1, w), max(1, h))
        self.move(x0, y0)
        if not self.composited:
            self.shape_combine_region(Gdk.cairo_region_create_from_surface(surface))
        if not self.get_visible():
            self.show()
        self.queue_draw()

    def _draw(self, _w, cr):
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        if self.surface is not None:
            cr.set_operator(cairo.OPERATOR_OVER)
            cr.set_source_surface(self.surface, 0, 0)
            cr.paint()
        return True


class PortalWindow(Overlay):
    """A swirling portal standing on a surface."""

    def __init__(self, scale, cx, base):
        self.frames = [sprites.render(sprites.portal(i), "tabby", scale) for i in range(3)]
        super().__init__(self.frames[0])
        self.place(cx - self.w / 2, base - self.h)
        self.step = 0
        self.alive = True
        self.show()
        GLib.timeout_add(110, self._spin)

    def _spin(self):
        if not self.alive:
            return False
        self.step += 1
        self.surface = self.frames[self.step % 3]
        self.queue_draw()
        return True

    def close(self):
        if self.alive:
            self.alive = False
            Floater(self.surface, self.pos[0] + self.w / 2, self.pos[1] + self.h, rise=0, life=0.35)
            self.destroy()


def today():
    """Today's date (PIXEL_CAT_DATE=YYYY-MM-DD pretends another day, for testing)."""
    fake = os.environ.get("PIXEL_CAT_DATE")
    if fake:
        try:
            return time.strptime(fake, "%Y-%m-%d")
        except ValueError:
            pass
    return time.localtime()


def now_ok(data, name, seconds):
    """A little per-state cooldown: True at most once every `seconds`."""
    key = "_next_" + name
    now = time.monotonic()
    if now >= data.get(key, 0):
        data[key] = now + seconds
        return True
    return False


def night(hour=None):
    hour = time.localtime().tm_hour if hour is None else hour
    return hour >= 20 or hour < 7


def next_morning():
    """7:00 tomorrow (or today, if it's still before 7)."""
    now = time.localtime()
    target = time.mktime((now.tm_year, now.tm_mon, now.tm_mday, 7, 0, 0, 0, 0, -1))
    if target <= time.time():
        target = time.mktime((now.tm_year, now.tm_mon, now.tm_mday + 1, 7, 0, 0, 0, 0, -1))
    return target


# --------------------------------------------------------------------------
# the cat
# --------------------------------------------------------------------------

CALM = {"stand", "sit", "walk", "groom", "sad", "vibe", "look", "study"}
GROUND, RIGHT_WALL, LEFT_WALL, CEILING = 0, 1, 2, 3
ANGLE = {GROUND: 0, RIGHT_WALL: -math.pi / 2, LEFT_WALL: math.pi / 2, CEILING: math.pi}


class Cat(Gtk.Window):
    def __init__(self, app):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.app = app
        self.pet = app.pet
        self.world = app.world
        self.scale = app.scale
        self.art = sprites.STYLES[self.pet.style]
        self.S = max(self.art.W, self.art.H) * self.scale
        self.set_app_paintable(True)
        self.composited = rgba_visual(self)
        self.set_default_size(self.S, self.S)
        self.resize(self.S, self.S)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                        | Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.connect("draw", self._draw)
        self.connect("button-press-event", self._on_press)
        self.connect("button-release-event", self._on_release)
        self.connect("motion-notify-event", self._on_motion)

        self.cache = {}
        self.current = None
        self.current_key = None
        self.pos = None

        self.x, self.y = 0.0, 0.0
        self.vx = self.vy = 0.0
        self.facing = 1
        self.orient = GROUND
        self.seg = None
        self.state = "fall"
        self.t = 0.0
        self.length = 0.0
        self.data = {}
        self.anim = 0.0

        self.headset = False
        self.pajamas = False
        self.chute = None
        self.portals = []
        self.butterfly = None
        self.bf_tries = 0
        self.mug = None
        self.box = None
        self.laser = None
        self.letter = None
        self.idle_mode = False
        self.work_s = 0.0
        self.reminded_at = 0.0
        self.reminders = 0
        self.idle_clock = IdleClock()
        self.plan_cost = 0.0
        self.party_at = 0.0
        self.route = None
        self.bed = None
        self.music_off_since = 0.0
        self.props = []
        self.press = None
        self.rubs = []
        self.petting_since = None
        self.grumpy_until = 0.0
        self.cooldowns = {}
        self.hidden = False
        self.last = time.monotonic()
        self.last_save = self.last

        self.world.refresh()
        self._enter()
        self.show()
        GLib.timeout_add(TICK_MS, self._tick)
        GLib.timeout_add(250, self._refresh)

    # ---------------------------------------------------------------- start
    def _enter(self):
        """Start on the floor where she was last time (or drop in from above)."""
        x0, y0, x1, _y1 = self.world.bounds
        if self.pet.x is not None and x0 + 40 <= self.pet.x <= x1 - 40:
            floor = self.world.floor_for(self.pet.x)
            self.x, self.y = float(self.pet.x), float(floor.y)
            self.seg = floor
            self.set_state("sit", random.uniform(3, 6))
            if self.pet.in_bed and time.time() < self.pet.wake_at:
                self.pajamas = True
                self._make_bed(self.x)
                self._get_in_bed()
            else:
                self.pet.in_bed = False
        else:
            px, _py = pointer()
            self.x, self.y = float(clamp(px, x0 + 60, x1 - 60)), float(y0 + 10)
            self.set_state("fall")
        self._render()

    def recolor(self):
        self.cache.clear()
        self.current_key = None
        if self.art is not sprites.STYLES[self.pet.style]:
            # switched between classic and detailed: a different window size
            self.art = sprites.STYLES[self.pet.style]
            self.S = max(self.art.W, self.art.H) * self.scale
            self.set_default_size(self.S, self.S)
            self.resize(self.S, self.S)
            self.pos = None
        self._render()

    # ---------------------------------------------------------------- drawing
    def _image(self, key):
        if key in self.cache:
            return self.cache[key]
        name, flip, orient, extras, pajamas = key
        frame = self.art.FRAMES[name]
        if pajamas:
            frame = self.art.with_nightcap(frame)
        else:
            for extra in extras:
                frame = self.art.with_extra(frame, extra)
        img = sprites.render(frame[0], self.pet.coat, self.scale, flip, pajamas)
        S = self.S
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, S, S)
        cr = cairo.Context(surface)
        cr.translate(S / 2, S / 2)
        cr.rotate(ANGLE[orient])
        cr.translate(-S / 2, -S / 2)
        cr.set_source_surface(img, (S - img.get_width()) // 2, S - img.get_height())
        cr.get_source().set_filter(cairo.FILTER_NEAREST)
        cr.paint()
        surface.flush()
        region = Gdk.cairo_region_create_from_surface(surface)
        self.cache[key] = (surface, region)
        return self.cache[key]

    def _show(self, name, flip=None):
        if flip is None:
            flip = self.facing < 0
        key = (name, flip, self.orient, self._dress(name), self.pajamas)
        if key == self.current_key:
            return
        surface, region = self._image(key)
        self.current, self.current_key = surface, key
        self.input_shape_combine_region(region)
        if not self.composited:
            self.shape_combine_region(region)
        self.queue_draw()

    def prop_scale(self):
        """Her bed, box, parachute ... grow with her for the detailed style."""
        return self.scale + (1 if self.art.name == "detailed" else 0)

    def _dress(self, name):
        """What she's wearing on top of this frame: scarf, hat, headset, umbrella, a letter."""
        out = []
        upright = name not in ("held", "dangle", "dangle_happy", "jump") and self.orient == GROUND
        weather = self.app.weather.kind if self.pet.weather else None
        if weather == "snow" and name not in ("held",):
            out.append("scarf")
        hat = self.pet.hat()
        if self.headset and name != "held":
            out.append("headset")
        elif hat:
            out.append("hat:" + hat)
        if weather == "rain" and upright and not self.state.startswith("sleep") and self.state != "box":
            out.append("umbrella")
        if self.letter is not None and name.startswith(("walk", "stand")):
            out.append("envelope")
        if self.state == "sweep" and name.startswith("stand"):
            out.append("broom0" if int(self.anim / 0.22) % 2 == 0 else "broom1")
        return tuple(out)

    def _draw(self, _w, cr):
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        if self.current is not None:
            cr.set_source_surface(self.current, 0, 0)
            cr.paint()
        return True

    def _place(self):
        S = self.S
        if self.orient == GROUND:
            wx, wy = self.x - S / 2, self.y - S
        elif self.orient == RIGHT_WALL:
            wx, wy = self.x - S, self.y - S / 2
        elif self.orient == LEFT_WALL:
            wx, wy = self.x, self.y - S / 2
        else:
            wx, wy = self.x - S / 2, self.y
        pos = (int(round(wx)), int(round(wy)))
        if pos != self.pos:
            self.pos = pos
            self.move(*pos)

    def head_point(self):
        """Where floaters (hearts, notes) come out: above her head."""
        if self.orient != GROUND:
            return self.x, self.y - self.S / 2
        return self.x + self.facing * 4 * self.scale, self.y - self.S + 4 * self.scale

    def _render(self):
        self._place()

    # ---------------------------------------------------------------- floaters
    def float_part(self, part, **kw):
        x, y = self.head_point()
        grid = sprites.PARTS[part] if isinstance(part, str) else part
        Floater(sprites.render(grid, "tabby", self.scale), x + random.uniform(-4, 4) * self.scale, y, **kw)

    def heart(self):
        self.float_part("HEART", drift=random.uniform(-6, 6) * self.scale / 2)
        self.pet.count("hearts")

    def note(self):
        self.float_part("NOTE", rise=30 * self.scale / 2, drift=5 * self.scale, life=1.6)

    def zzz(self):
        x, y = self.head_point()
        Floater(text_surface("z", self.scale), x + 6 * self.scale * self.facing, y + 6 * self.scale,
                rise=26 * self.scale / 2, drift=3 * self.scale, life=2.2)

    def bubble(self, part):
        x, y = self.head_point()
        grid = sprites.bubble_with(part, 3, 2) if part == "FISH" else sprites.bubble_with(part, 4, 1)
        Floater(sprites.render(grid, "tabby", self.scale), x + 10 * self.scale, y,
                rise=6, life=3.2, hold=2.0)

    def poof(self, x=None, y=None):
        x = self.x if x is None else x
        y = (self.y - 4 * self.scale) if y is None else y
        Floater(sprites.render(sprites.PARTS["POOF"], "tabby", self.scale * 2), x, y, rise=10, life=0.6)

    # ---------------------------------------------------------------- state machine
    def set_state(self, name, length=0.0, **data):
        if self.chute is not None and name != "fall":
            self._close_chute()
        if self.state == "fall" and name != "fall" and self.data.get("tramp"):
            t = self.data["tramp"][0]
            GLib.timeout_add(700, lambda: (self.poof(t.cx, t.base), t.destroy()) and False)
        self.state, self.t, self.length, self.data = name, 0.0, length, data
        self.anim = 0.0

    def _tick(self):
        now = time.monotonic()
        dt = min(0.1, now - self.last)
        self.last = now
        if now - self.last_save > 15:
            self.last_save = now
            self.pet.x = int(self.x)
            try:
                self.pet.save()
            except OSError:
                pass
        if self.hidden:
            self._debug(now)
            return True
        self.pet.tick(dt, self.state in ("sleep", "bed"))
        self.t += dt
        self.anim += dt
        self._music(dt)
        self._follow_window()
        getattr(self, "_st_" + self.state)(dt)
        for prop in list(self.props):
            prop.update(dt, self.world)
            if prop is self.mug and prop.fell and prop.landed and self.state != "mug_tap":
                self._smash(prop)
                continue
            if now - prop.born > 90 and self.data.get("prop") is not prop:
                self.props.remove(prop)          # forgotten somewhere: tidy it away
                self.poof(prop.x, prop.y - 4 * self.scale)
                prop.destroy()
                if prop is self.mug:
                    self.mug = None
        self._ambient(dt)
        self._surprises(dt, now)
        self._activity(dt, now)
        self._move_laser()
        self._check_rub(now)
        self._place()
        self._debug(now)
        return True

    def _debug(self, now):
        """Writes what she's doing to $PIXEL_CAT_DEBUG (for testing)."""
        if DEBUG_FILE and now - getattr(self, "_debug_at", 0) > 0.3:
            self._debug_at = now
            with open(DEBUG_FILE, "w") as f:
                json.dump({"state": self.state, "hidden": self.hidden, "chute": self.chute is not None,
                           "tramp": bool(self.data.get("tramp")), "x": int(self.x), "y": int(self.y), "orient": self.orient,
                           "seg": str(self.seg.key) if self.seg else None, "headset": self.headset,
                           "win": self.pos, "props": [(p.kind, int(p.x), int(p.y), p.landed, int(p.vx), str(p.seg_key)) for p in self.props], "data": {k: v for k, v in self.data.items() if isinstance(v, (int, float, str))},
                           "pet": {k: round(getattr(self.pet, k), 1)
                                   for k in ("fullness", "fun", "energy", "affection")}}, f)

    def _refresh(self):
        now = time.monotonic()
        if self.pet.explore and not self.hidden and now - getattr(self, "_scan_at", 0) > 5 \
                and self.state not in ("sleep", "bed", "held"):
            self._scan_at = now
            exclude = []
            if self.pos is not None:
                exclude.append((self.pos[0], self.pos[1], self.S, self.S))
            for w in self.props + ([self.chute] if self.chute else []):
                if w.get_window() is not None:
                    x, y = w.get_position()
                    exclude.append((x, y, w.w, w.h))
            try:
                self.world.scan_ledges(exclude)
            except Exception:
                self.world.ledges = {}
        elif not self.pet.explore and self.world.ledges:
            self.world.ledges = {}
        self.world.refresh()
        extras = self._extras()
        covered = self.world.covered(self.x, self.y - self.S / 2)
        if covered and len(self.world.monitors) > 1 and self.bed is None and self.state not in ("held",):
            free = self.world.free_monitor()
            if free is not None:
                # fullscreen on her screen: she slips over to the other one instead of hiding
                geo, area = free
                floor = self.world.floor_for(area.x + area.width / 2)
                self._close_gadgets()
                self.route = None
                self.orient = GROUND
                self.seg = floor
                self.x = float(clamp(area.x + area.width / 2, floor.x1 + 10, floor.x2 - 10))
                self.y = float(floor.y)
                self.set_state("sit", random.uniform(3, 6))
                self._place()
                covered = False
        if covered and not self.hidden:
            self.hidden = True
            self.hide()
            for w in extras:
                w.hide()
        elif not covered and self.hidden:
            self.hidden = False
            self.show()
            for w in extras:
                w.show()
            self.raise_all()
        self._reattach()
        return True

    def _extras(self):
        """Every little window that belongs to her: props, parachute, bed, rope, portals ..."""
        out = list(self.props) + list(self.portals)
        for w in (self.chute, self.butterfly, self.data.get("rope"),
                  (self.data.get("tramp") or (None,))[0]):
            if w is not None:
                out.append(w)
        if self.bed:
            out += list(self.bed[:2])
        if self.box:
            out += list(self.box[:2])
        if self.laser is not None:
            out.append(self.laser[0])
        return out

    def raise_all(self):
        back = ([self.bed[0]] if self.bed else []) + ([self.box[0]] if self.box else [])
        front = ([self.bed[1]] if self.bed else []) + ([self.box[1]] if self.box else [])
        others = [w for w in self._extras() if w not in back and w not in front]
        for w in back + [self] + others + front:
            try:
                gdk_window = w.get_window()
            except Exception:
                continue
            if gdk_window is not None and w.get_visible():
                gdk_window.raise_()

    def _follow_window(self):
        """While you drag the window she's on, move with it every frame (smooth)."""
        s = self.seg
        if s is None or s.kind not in ("win", "ledge") or self.orient != GROUND \
                or self.state in ("jump", "fall", "held", "crouch_jump", "bed"):
            return
        pos = self.world.window_pos(s.key[1])
        if pos is None:
            return
        dx, dy = pos[0] - s.win_x, pos[1] - s.win_y
        if not dx and not dy:
            return
        for seg in self.world.segments:
            if seg.key[:2] == s.key[:2] and seg is not s:
                seg.shift(dx, dy)
        s.shift(dx, dy)
        self.x += dx
        self.y += dy
        for prop in self.props:
            if prop.seg_key[:2] == s.key[:2]:
                prop.x += dx
                prop.y += dy

    def _reattach(self):
        """Ride along when her window moves; fall when it closes or gets covered."""
        if self.seg is None or self.orient != GROUND or self.state in ("jump", "fall", "held", "crouch_jump",
                                                                        "bed", "box"):
            return
        seg, same = self.world.find(self.seg.key, self.x)
        if same and self.seg.kind in ("win", "ledge") and same[0].win_x != self.seg.win_x:
            dx = same[0].win_x - self.seg.win_x
            self.x += dx
            for prop in self.props:
                if prop.seg_key == self.seg.key:
                    prop.x += dx
            seg, same = self.world.find(self.seg.key, self.x)
        if seg is None:
            self.seg = None
            self.fall()
            return
        self.seg = seg
        self.y = float(seg.y)

    # ---------------------------------------------------------------- helpers
    def on_ground(self):
        return self.orient == GROUND and self.seg is not None

    def _sad(self):
        return self.pet.mood() < 35

    def _walk_frame(self, dist_per_frame=None):
        step = dist_per_frame or 3 * self.scale
        return f"walk{int(self.data.get('dist', 0) / step) % 4}"

    def fall(self, vx=0.0, vy=0.0):
        self.orient = GROUND
        self.seg = None
        self.vx, self.vy = vx, vy
        self.set_state("fall")

    def decide(self):
        self.route = None
        if self.portals:
            self._close_gadgets()
        if not self.on_ground():
            self.fall()
            return
        treats = [pr for pr in self.props if pr.kind == "treat" and pr.seg_key == self.seg.key]
        if treats:
            self._go_eat(treats[0])
            return
        p = self.pet
        hour = time.localtime().tm_hour
        night = hour >= 22 or hour < 7
        options = {"walk": 5.0, "stand": 1.2, "sit": 3.0, "groom": 1.2}
        sleep = 0.25 if p.energy > 70 else (1.5 if p.energy > 40 else 6.0)
        if night:
            sleep *= 2.5
        if self.seg.note:
            sleep *= 3
        if self.app.music.playing:
            sleep *= 0.6
            options["vibe"] = 7.0
        options["sleep"] = sleep
        if self._jump_targets():
            options["jump"] = 2.5
        if self._sad():
            options["sad"] = 4.0
        notes = [s for s in self.world.segments if s.note and s.key != self.seg.key]
        if notes and (p.energy < 60 or random.random() < 0.3):
            options["go_note"] = 1.5
        if p.energy > 30 and len(self.world.segments) > 1:
            options["adventure"] = 1.8 if p.fun > 30 else 1.0
        if self.pet.study and self.work_s > 10 * 60:
            options["study"] = 3.0            # you're working, so she studies along
        total = sum(options.values())
        r = random.uniform(0, total)
        for name, weight in options.items():
            r -= weight
            if r <= 0:
                break
        self.start(name)

    def start(self, name):
        s = self.seg
        if name == "walk":
            span = random.uniform(40, 260) * random.choice((-1, 1))
            target = clamp(self.x + span, s.x1 + 12, s.x2 - 12)
            if abs(target - self.x) < 10:
                target = clamp(self.x - span, s.x1 + 12, s.x2 - 12)
            run = random.random() < 0.15
            self.walk_to(target, run=run)
        elif name == "stand":
            self.set_state("stand", random.uniform(1.5, 4))
        elif name == "sit":
            self.set_state("sit", random.uniform(4, 12))
        elif name == "groom":
            self.set_state("groom", random.uniform(2, 4.5))
        elif name == "sleep":
            self.set_state("yawn", 1.3, then="sleep")
        elif name == "vibe":
            self.set_state("vibe", random.uniform(8, 20))
        elif name == "sad":
            self.set_state("sad", random.uniform(5, 10))
        elif name == "jump":
            targets = self._jump_targets()
            if targets:
                seg, x = random.choice(targets)
                self.jump_to(x, seg.y, seg.key)
            else:
                self.set_state("sit", 3)
        elif name == "go_note":
            self._go_toward_note()
        elif name == "adventure":
            self._adventure()
        elif name == "study":
            self.set_state("study", random.uniform(25, 70))
        else:
            self.set_state("stand", 2)

    def _adventure(self):
        """Off somewhere else on the screen, by whatever it takes: jumps, climbing,
        the grappling hook, or a portal."""
        if not self.on_ground():
            return
        options = [t for t in self.world.segments
                   if t.x2 - t.x1 >= 80 and not (t.key == self.seg.key and t.x1 <= self.x <= t.x2)]
        if not options:
            self.set_state("sit", 3)
            return
        # favour places above her: that's where the gadgets come out
        weights = [3.0 if t.y < self.y - 150 else 1.0 for t in options]
        goal = random.choices(options, weights)[0]
        x = random.uniform(goal.x1 + 20, goal.x2 - 20)
        arrive = lambda: self.set_state("sit", random.uniform(3, 8), happy=True)
        if math.hypot(x - self.x, goal.y - self.y) > 350 and random.random() < 0.3:
            self.open_portal(goal, x, arrive)          # why walk
            return
        self.go_to(goal, x, then=arrive, fail=self.decide)

    def exclaim(self):
        x, y = self.head_point()
        Floater(text_surface("!", self.scale), x, y, rise=14 * self.scale / 2, life=0.9)

    def slip(self, direction):
        """Oops: she loses her footing and goes over the edge."""
        self.exclaim()
        self.set_state("slip", 0.35, dir=direction)

    def _st_slip(self, dt):
        self._show("crouch1" if int(self.anim / 0.08) % 2 else "crouch0")
        self.x += self.data["dir"] * 30 * dt
        if self.t > self.length:
            self.x += self.data["dir"] * 12
            self.fall(vx=self.data["dir"] * 40)

    def walk_to(self, target, run=False, then=None):
        self.facing = 1 if target > self.x else -1
        self.set_state("walk", 0, target=target, speed=(150 if run else 42) * self.scale / 2,
                       dist=0.0, then=then)

    def _jump_targets(self, max_up=230, max_side=320):
        if not self.on_ground():
            return []
        out = []
        for s in self.world.segments:
            if s.x2 - s.x1 < 50:
                continue
            if s.key == self.seg.key and s.x1 <= self.x <= s.x2:
                continue
            dy = s.y - self.y
            if dy < -max_up or dy > 650:
                continue
            nearest = clamp(self.x, s.x1 + 16, s.x2 - 16)
            inward = random.uniform(10, 60)
            x = clamp(nearest + (inward if nearest <= s.x1 + 16 else -inward if nearest >= s.x2 - 16 else 0),
                      s.x1 + 16, s.x2 - 16)
            dx = x - self.x
            if abs(dx) > max_side + max(0, dy) * 0.25:
                continue
            if abs(dx) < 25 and abs(dy) < 25:
                continue
            out.append((s, x))
        return out

    def _go_toward_note(self):
        notes = [s for s in self.world.segments if s.note]
        if not notes:
            self.set_state("sit", 3)
            return
        goal = min(notes, key=lambda s: abs((s.x1 + s.x2) / 2 - self.x) + abs(s.y - self.y))
        targets = self._jump_targets()
        direct = [t for t in targets if t[0].key == goal.key]
        if direct:
            seg, x = direct[0]
            self.jump_to(x, seg.y, seg.key)
            return
        if targets:
            gx, gy = (goal.x1 + goal.x2) / 2, goal.y
            seg, x = min(targets, key=lambda t: abs(t[1] - gx) + abs(t[0].y - gy))
            if abs(x - gx) + abs(seg.y - gy) < abs(self.x - gx) + abs(self.y - gy):
                self.jump_to(x, seg.y, seg.key)
                return
        self.walk_to(clamp((goal.x1 + goal.x2) / 2, self.seg.x1 + 12, self.seg.x2 - 12))

    def jump_to(self, x, y, key=None, pounce=False):
        self.facing = 1 if x > self.x else -1
        dist = math.hypot(x - self.x, y - self.y)
        up = max(0.0, self.y - y)
        self.set_state("crouch_jump", 0.1 if pounce else 0.3, x0=self.x, y0=self.y, x1=x, y1=y, key=key,
                       T=clamp(0.35 + dist / 1100, 0.35, 1.0),
                       H=18 + up * 0.5 + abs(x - self.x) * 0.08, pounce=pounce)

    # ---------------------------------------------------------------- states
    def _st_stand(self, dt):
        if self._sad():
            self._show("stand_sad")
        else:
            self._show("stand" if int(self.anim / 0.7) % 2 == 0 else "stand2")
        if self.t > self.length:
            self.decide()

    def _st_sit(self, dt):
        cycle = self.anim % 5.0
        if self.butterfly is not None and self.butterfly.mode == "perch":
            self._show("sit_happy")
            return
        if self._sad():
            self._show("sit_sad" if int(self.anim / 0.9) % 2 == 0 else "sit_sad2")
        elif self.data.get("happy"):
            self._show("sit_happy" if int(self.anim / 0.6) % 2 == 0 else "sit_happy2")
        elif 4.0 < cycle < 4.15:
            self._show("sit_blink")
        elif 1.5 < cycle < 2.2:
            self._show("sit_tail")
        else:
            self._show("sit")
        if self.t > self.length:
            self.decide()

    _st_look = _st_sit

    def _st_sad(self, dt):
        self._show("sit_sad" if int(self.anim / 0.9) % 2 == 0 else "sit_sad2")
        if self.t > self.length:
            self.decide()

    def _st_groom(self, dt):
        self._show("groom0" if int(self.anim / 0.25) % 2 == 0 else "groom1")
        if self.t > self.length:
            self.set_state("sit", random.uniform(2, 6))

    def _st_yawn(self, dt):
        self._show("yawn")
        if self.t > self.length:
            if self.data.get("then") == "sleep":
                length = random.uniform(60, 240) * (2 if self.pet.energy < 30 else 1)
                self.set_state("sleep", length)
            else:
                self.set_state("sit", random.uniform(2, 5))

    def _st_sleep(self, dt):
        self._show("sleep0" if int(self.anim / 1.3) % 2 == 0 else "sleep1")
        if self.anim % 3.2 < dt:
            self.zzz()
        if self.t > self.length or self.pet.energy >= 99.5:
            if random.random() < 0.25:
                self.app.sounds.play("mrrp")
            if self.seg is not None and self.seg.kind != "floor" and random.random() < 0.08:
                self.slip(self.facing)           # rolled over in her sleep
                return
            self.set_state("yawn", 1.3)

    def _st_walk(self, dt):
        target = self.data["target"]
        if self.seg is not None:
            target = clamp(target, self.seg.x1 + 8, self.seg.x2 - 8)
        speed = self.data["speed"]
        step = speed * dt
        remaining = target - self.x
        self.facing = 1 if remaining > 0 else -1 if remaining < 0 else self.facing
        moved = min(step, abs(remaining))
        self.x += moved * self.facing
        self.data["dist"] += moved
        self._show(self._walk_frame(2 * self.scale if speed > 80 else None))
        if abs(target - self.x) < 1:
            then = self.data.get("then")
            if then:
                then()
            else:
                self._at_walk_end()

    def _at_walk_end(self):
        s = self.seg
        x0, _y0, x1, _y1 = self.world.bounds
        at_left, at_right = self.x <= s.x1 + 14, self.x >= s.x2 - 14
        if s.kind == "floor" and (at_left and s.x1 <= x0 + 1 or at_right and s.x2 >= x1 - 1) \
                and random.random() < 0.35:
            self.orient = RIGHT_WALL if at_right else LEFT_WALL
            self.x = float(x1 if at_right else x0)
            self.y = s.y - self.S / 2
            self.seg = None
            self.set_state("climb", 0, dist=0.0)
            return
        if s.kind in ("win", "ledge") and (at_left or at_right) and random.random() < 0.12:
            self.slip(-1 if at_left else 1)
            return
        if s.kind == "win" and (at_left or at_right) and random.random() < 0.3:
            # hop down off the edge
            self.x += (-1 if at_left else 1) * 18
            self.fall(vx=(-1 if at_left else 1) * 60)
            return
        if random.random() < 0.5:
            targets = self._jump_targets()
            if targets and random.random() < 0.6:
                seg, x = random.choice(targets)
                self.jump_to(x, seg.y, seg.key)
                return
        self.decide()

    def _st_crouch_jump(self, dt):
        self._show("crouch0")
        if self.t > self.length:
            d = self.data
            self.seg = None
            self.set_state("jump", d["T"], **d)

    def _st_jump(self, dt):
        d = self.data
        u = clamp(self.t / d["T"], 0, 1)
        self.x = d["x0"] + (d["x1"] - d["x0"]) * u
        self.y = d["y0"] + (d["y1"] - d["y0"]) * u - 4 * d["H"] * u * (1 - u)
        self._show("jump")
        if u >= 1 and d.get("climb"):
            key, side = d["climb"]
            self.seg = None
            self.orient = RIGHT_WALL if side == "left" else LEFT_WALL
            self.x, self.y = d["x1"], d["y1"] - self.S / 2
            self.set_state("climb", 0, dist=0.0, onto=(key, side))
            return
        if u >= 1:
            key = d.get("key")
            seg = None
            if key is not None:
                seg, _same = self.world.find(key, self.x)
            if seg is not None and seg.kind != "floor" and not d.get("pounce") and self.route is None \
                    and random.random() < 0.07:
                # just missed it
                self.exclaim()
                self.fall(vx=(d["x1"] - d["x0"]) / d["T"] * 0.2, vy=60)
            elif seg is not None:
                self.seg = seg
                self.y = float(seg.y)
                self.set_state("land", 0.15)
            elif d.get("into_box") and self.box is not None:
                floor = self.world.floor_for(self.x)
                self.seg = floor
                self.x, self.y = float(self.box[2]), float(floor.y - 1 * self.scale)
                self.pet.count("boxes")
                self.pet.add(fun=10)
                self.set_state("box", random.choice((random.uniform(30, 50), random.uniform(90, 180))))
                self.raise_all()
            else:
                self.fall(vx=(d["x1"] - d["x0"]) / d["T"] * 0.3, vy=0)
            if d.get("pounce"):
                self.pet.add(fun=6, energy=-0.5)

    def _st_fall(self, dt):
        self.orient = GROUND
        before = self.y
        if self.chute is None:
            self.vy = min(self.vy + GRAVITY * dt, 1600)
            if self.vy > 60 and "tramp" not in self.data:
                height = self._drop_height()
                if height > 170 * self.scale / 2:
                    if self.vy > 150:
                        self._open_chute()
                elif height > 60 * self.scale / 2:
                    self._put_trampoline()
                else:
                    self.data["tramp"] = None
        if self.data.get("tramp"):
            cx = self.data["tramp"][0].cx          # aim for the middle of the trampoline
            self.vx = 0.0
            self.x += (cx - self.x) * min(1.0, dt * 8)
        if self.chute is not None:
            # float down gently, swaying a little
            target = 85 * self.scale / 2
            self.vy += (target - self.vy) * min(1.0, dt * 5)
            self.vx *= 0.95
            self.x += math.sin(self.t * 2.3) * 22 * dt * self.scale / 2
        self.y += self.vy * dt
        self.x += self.vx * dt
        self.vx *= 0.99
        x0, _y0, x1, _y1 = self.world.bounds
        if self.x < x0 + 10 or self.x > x1 - 10:
            self.x = clamp(self.x, x0 + 10, x1 - 10)
            self.vx = -self.vx * 0.4
        if self.chute is not None:
            self._show("dangle_happy" if int(self.t / 1.5) % 3 == 2 else "dangle", flip=False)
            self._place_chute()
        else:
            self._show("jump")
        seg = self.world.landing(self.x, before, self.y) if self.vy >= 0 else None
        if seg is None:
            floor = self.world.floor_for(self.x)
            if floor is not None and self.y >= floor.y:
                seg = floor
        tramp = self.data.get("tramp")
        if tramp is not None and self.data.get("bounces", 0) > 0 and self.vy > 0 \
                and self.y >= tramp[0].mat_y and abs(self.x - tramp[0].cx) < tramp[0].w / 2:
            # boing
            self.data["bounces"] -= 1
            self.y = tramp[0].mat_y - 1
            self.vy = -min(abs(self.vy) * 0.75, 650)
            tramp[0].squash()
            return
        if seg is not None:
            self.seg = seg
            self.y = float(seg.y)
            self.x = clamp(self.x, seg.x1 + 8, seg.x2 - 8)
            self.vx = self.vy = 0.0
            self.set_state("land", 0.2)

    def _put_trampoline(self):
        seg = self.world.landing(self.x, self.y, 10 ** 6) or self.world.floor_for(self.x)
        if seg is None:
            self.data["tramp"] = None
            return
        cx = clamp(self.x + self.vx * 0.25, seg.x1 + 14 * self.scale, seg.x2 - 14 * self.scale)
        tramp = Trampoline(self.prop_scale(), cx, seg.y)
        self.data["tramp"] = (tramp, seg.key)
        self.data["bounces"] = random.choice((1, 2))
        self.vx *= 0.3
        self.raise_all()

    def _drop_height(self):
        seg = self.world.landing(self.x, self.y, 10 ** 6) or self.world.floor_for(self.x)
        return (seg.y - self.y) if seg is not None else 0

    def _open_chute(self):
        img = sprites.render(sprites.PARTS["PARACHUTE"], self.pet.coat, self.prop_scale())
        self.chute = Overlay(img)
        self._place_chute()
        self.chute.show()
        self.raise_all()

    def _place_chute(self):
        c = self.chute
        # the harness hangs just above her head (the "dangle" frame's head starts at row 1)
        head_top = self.y - self.S + (self.S - self.art.H * self.scale) + self.art.dangle_top * self.scale
        c.place(self.x - c.w / 2, head_top - c.h + 2 * self.scale)

    def _close_chute(self):
        c, self.chute = self.chute, None
        if c.pos is not None:
            # it folds down and fades away
            Floater(c.surface, c.pos[0] + c.w / 2, c.pos[1] + c.h, rise=-14 * self.scale, life=0.5)
        c.destroy()

    def _st_land(self, dt):
        self._show("crouch0")
        if self.t > self.length:
            if self.route is not None:
                self._next_hop()
            elif self.laser is not None:
                self.set_state("laser", 0)
            elif self.butterfly is not None and self.butterfly.chaseable():
                self.set_state("chase_bf", 0)
            else:
                self.set_state("stand", random.uniform(0.5, 1.5))

    def _st_climb(self, dt):
        if "onto" in self.data:
            self._climb_window(dt)
            return
        speed = 45 * self.scale / 2
        self.y -= speed * dt
        self.data["dist"] += speed * dt
        # climbing up: on the right wall she faces up unflipped, on the left wall flipped
        self._show(self._walk_frame(), flip=(self.orient == LEFT_WALL))
        geo, area = self.world.monitor_at(self.x - 1 if self.orient == RIGHT_WALL else self.x + 1, self.y)
        top = area.y if area is not None else 0
        if self.y <= top + self.S / 2:
            going_right = self.orient == LEFT_WALL
            self.orient = CEILING
            self.facing = 1 if going_right else -1
            self.x += self.S / 2 * (1 if going_right else -1)
            self.y = float(top)
            distance = random.uniform(80, 420)
            self.set_state("ceiling", 0, target=self.x + distance * self.facing, dist=0.0)
        elif random.random() < 0.04 * dt:
            off = self.S / 2 * (-1 if self.orient == RIGHT_WALL else 1)
            self.x += off
            self.y += self.S / 2
            self.fall(vx=off)

    def _st_ceiling(self, dt):
        speed = 40 * self.scale / 2
        geo, _area = self.world.monitor_at(self.x, self.y + 1)
        x0, x1 = (geo.x, geo.x + geo.width) if geo is not None else self.world.bounds[::2]
        target = clamp(self.data["target"], x0 + self.S, x1 - self.S)
        step = min(speed * dt, abs(target - self.x))
        self.x += step * (1 if target > self.x else -1)
        self.data["dist"] += step
        # upside down: moving right means flipped
        self._show(self._walk_frame(), flip=(target > self.x) or self.facing > 0)
        if abs(target - self.x) < 1:
            self.y += self.S
            self.fall()

    def _st_stalk(self, dt):
        px, py = pointer()
        self.facing = 1 if px > self.x else -1
        self._show("crouch0" if int(self.anim / 0.12) % 2 == 0 else "crouch1")
        if self.t > self.length:
            if not self.on_ground():
                self.decide()
                return
            reach = 260 * self.scale / 2
            tx = clamp(px, self.x - reach, self.x + reach)
            ty = clamp(py, self.y - 200, self.y)
            self.jump_to(tx, ty, None, pounce=True)

    def _st_eat(self, dt):
        self._show("eat0" if int(self.anim / 0.3) % 2 == 0 else "eat1")
        if self.t > self.length:
            prop = self.data.get("prop")
            if prop in self.props:
                self.props.remove(prop)
                prop.destroy()
            self.pet.add(fullness=40, affection=5)
            self.pet.count("treats")
            self.heart()
            self.set_state("sit", random.uniform(3, 6), happy=True)

    def _st_petted(self, dt):
        self._show("sit_happy" if int(self.anim / 0.5) % 2 == 0 else "sit_happy2")
        last = self.data.get("last_rub", 0)
        if time.monotonic() - last > 1.8:
            self.petting_since = None
            self.set_state("sit", random.uniform(3, 7), happy=True)

    def _st_held(self, dt):
        self._show("held", flip=False)

    def _st_vibe(self, dt):
        beat = 0.45
        self._show("sit_bob" if int(self.anim / beat) % 2 == 0 else "sit")
        if self.anim % 1.6 < dt:
            self.note()
        if self.t > self.length or not self.app.music.playing:
            self.set_state("sit", random.uniform(2, 5))

    def _st_ask(self, dt):
        self._show("sit_sad" if self._sad() else "sit")
        if self.t > self.length:
            self.decide()

    def _st_play(self, dt):
        yarn = self.data["prop"]
        if yarn not in self.props:
            self.set_state("sit", 3, happy=True)
            return
        if self.data["bats"] <= 0:
            self.props.remove(yarn)
            self.poof(yarn.x, yarn.y - 4 * self.scale)
            yarn.destroy()
            self.pet.add(fun=45, affection=8, energy=-3)
            self.pet.count("yarn")
            self.heart()
            self.set_state("sit", random.uniform(3, 6), happy=True)
            return
        if not yarn.landed:
            self._show("crouch0" if int(self.anim / 0.12) % 2 == 0 else "crouch1")
            return
        gap = yarn.x - self.x
        self.facing = 1 if gap > 0 else -1
        if abs(gap) > 12 * self.scale:
            speed = 150 * self.scale / 2
            self.x += clamp(gap, -speed * dt, speed * dt)
            self.data["dist"] = self.data.get("dist", 0) + speed * dt
            self._show(self._walk_frame(2 * self.scale))
        else:
            self._show("crouch1")
            if abs(yarn.vx) < 30:
                yarn.vx = random.uniform(140, 260) * random.choice((-1, 1)) * self.scale / 2
                self.data["bats"] -= 1

    def _st_come(self, dt):
        px, _py = pointer()
        if self.seg is not None:
            px = clamp(px, self.seg.x1 + 10, self.seg.x2 - 10)
        gap = px - self.x
        self.facing = 1 if gap > 0 else -1
        speed = 170 * self.scale / 2
        if abs(gap) > 6 and self.t < 6:
            self.x += clamp(gap, -speed * dt, speed * dt)
            self.data["dist"] = self.data.get("dist", 0) + speed * dt
            self._show(self._walk_frame(2 * self.scale))
        else:
            self.arrived()

    def arrived(self):
        self.app.sounds.play("meow2")
        self.heart()
        self.pet.add(affection=3)
        self.set_state("sit", random.uniform(4, 8), happy=True)

    # ---------------------------------------------------------------- things that can happen any time
    def _ambient(self, dt):
        now = time.monotonic()
        p = self.pet
        if self.state not in CALM or not self.on_ground() or self.props or self.route is not None:
            return

        def ready(name, seconds):
            if now - self.cooldowns.get(name, -1e9) > seconds:
                self.cooldowns[name] = now
                return True
            return False

        if p.fullness < 30 and ready("hungry", 90 if p.fullness < 10 else 180):
            self.set_state("ask", 4)
            self.bubble("FISH")
            self.app.sounds.play("meow")
            return
        if p.fun < 25 and ready("bored", 200):
            self.set_state("ask", 4)
            self.bubble("YARN")
            self.app.sounds.play("mrrp")
            return
        px, py = pointer()
        near = abs(px - self.x) < 280 * self.scale / 2 and self.y - 190 < py < self.y + 8 and self.butterfly is None
        if near and abs(px - self.x) > 30 and self.state != "vibe":
            chance = 0.03 + (0.07 if p.fun < 50 else 0)
            if random.random() < chance * dt and ready("stalk", 25):
                self.set_state("stalk", random.uniform(0.9, 1.6))

    def _music(self, dt):
        playing = self.app.music.playing
        now = time.monotonic()
        if playing:
            self.music_off_since = 0.0
            if not self.headset:
                self.headset = True
                self.note()
        elif self.headset:
            if not self.music_off_since:
                self.music_off_since = now
            elif now - self.music_off_since > 40:
                self.headset = False

    # ---------------------------------------------------------------- mouse
    def _on_press(self, _w, event):
        if event.button == 3:
            self.app.show_menu(event)
            return True
        if event.button == 1:
            self.press = (event.x_root, event.y_root, time.monotonic())
            self.drag_samples = [(time.monotonic(), event.x_root, event.y_root)]
        return True

    def _on_motion(self, _w, event):
        now = time.monotonic()
        if self.press is not None:
            if self.state != "held":
                if math.hypot(event.x_root - self.press[0], event.y_root - self.press[1]) > 6:
                    if self.bed is not None:
                        self.wake_up()
                    self._close_gadgets()
                    if self.state == "box" and self.box is not None:
                        box, self.box = self.box, None
                        box[0].destroy()
                        box[1].destroy()
                    self.route = None
                    self.orient = GROUND
                    self.seg = None
                    self.petting_since = None
                    self.set_state("held")
            if self.state == "held":
                self.x = event.x_root
                self.y = event.y_root + self.S - 3 * self.scale
                self.drag_samples.append((now, event.x_root, event.y_root))
                self.drag_samples = self.drag_samples[-6:]
                self._place()
            return True
        return True

    def _check_rub(self, now):
        """Petting: the pointer going back and forth over her."""
        if self.pos is None or self.press is not None or self.orient != GROUND:
            self.rubs = []
            return
        px, py = pointer()
        wx, wy = self.pos
        margin = 3 * self.scale
        if not (wx + margin <= px <= wx + self.S - margin and wy + self.S * 0.3 <= py <= wy + self.S + margin):
            self.rubs = []
            return
        self.rubs = [r for r in self.rubs if now - r[0] < 1.2]
        if not self.rubs or self.rubs[-1][1] != px:
            self.rubs.append((now, px))
        turns = 0
        direction = 0
        for (_t1, a), (_t2, b) in zip(self.rubs, self.rubs[1:]):
            d = b - a
            if abs(d) < 2:
                continue
            sign = 1 if d > 0 else -1
            if direction and sign != direction:
                turns += 1
            direction = sign
        if turns >= 2:
            self.rubs = self.rubs[-1:]
            self.petted()

    def debug_do(self, what):
        """For testing: make her do something now."""
        if what == "sleep":
            self.set_state("yawn", 1.3, then="sleep")
        elif what == "climb" and self.on_ground():
            x0, _y0, x1, _y1 = self.world.bounds
            floor = self.world.floor_for(x1 - 5)
            self.orient, self.seg = RIGHT_WALL, None
            self.x, self.y = float(x1), floor.y - self.S / 2
            self.set_state("climb", 0, dist=0.0)
        elif what == "stalk":
            self.set_state("stalk", 1.2)
        elif what == "hungry":
            self.pet.fullness = 15
            self.cooldowns.pop("hungry", None)
        elif what == "bored":
            self.pet.fun = 15
            self.cooldowns.pop("bored", None)
        elif what == "bed":
            self.send_to_bed()
        elif what == "wake":
            self.wake_up()
        elif what == "portal":
            px, py = pointer()
            goal = self.world.landing(px, py - 2, 10 ** 6) or self.world.floor_for(px)
            self.open_portal(goal, px, self.arrived)
        elif what == "diary":
            self.app.show_diary()
        elif what == "other":
            self.other_screen()
        elif what == "laser":
            self.toggle_laser()
        elif what == "box":
            self.give_box()
        elif what == "photo":
            self.take_photo()
        elif what == "study":
            self.set_state("study", 30)
        elif what == "remind":
            self.work_s = self.STUDY_WORK
            self.reminded_at = 0
        elif what == "idle":
            self.idle_clock.seconds = lambda: 999
        elif what == "back":
            self.idle_clock.seconds = lambda: 0
        elif what == "butterfly":
            if self.butterfly is None:
                self.spawn_butterfly()
        elif what == "mug":
            if self.on_ground() and self.seg.kind == "win" and self.mug is None:
                self.mug_time()
        elif what == "adventure":
            self._adventure()
        elif what == "slip":
            self.slip(self.facing)
        elif what == "vibe":
            self.set_state("vibe", 20)
        elif what == "sad":
            self.pet.fullness = self.pet.fun = self.pet.affection = 10
            self.set_state("sad", 6)

    def _on_release(self, _w, event):
        if event.button != 1 or self.press is None:
            return True
        press, self.press = self.press, None
        if self.state == "held":
            samples = self.drag_samples
            vx = vy = 0.0
            if len(samples) >= 2 and samples[-1][0] - samples[0][0] > 0.01:
                span = samples[-1][0] - samples[0][0]
                vx = (samples[-1][1] - samples[0][1]) / span
                vy = (samples[-1][2] - samples[0][2]) / span
            self.fall(vx=clamp(vx, -900, 900), vy=clamp(vy, -600, 400))
        elif time.monotonic() - press[2] < 0.4:
            self.clicked()
        return True

    # ---------------------------------------------------------------- interactions
    def petted(self):
        now = time.monotonic()
        if self.state in ("held", "jump", "fall", "crouch_jump", "climb", "ceiling"):
            return
        if now < self.grumpy_until:
            if self.state != "walk" and self.on_ground():
                away = self.x - 120 * self.facing
                self.walk_to(clamp(away, self.seg.x1 + 12, self.seg.x2 - 12), run=True)
            return
        if self.state == "sleep":
            self.set_state("yawn", 1.0)
            return
        if self.state == "bed":
            if now - self.data.get("last_heart", 0) > 0.8:
                self.data["last_heart"] = now
                self.heart()
                self.pet.add(affection=2)
            if now - self.data.get("last_purr", 0) > 2.5:
                self.data["last_purr"] = now
                self.app.sounds.play("purr")
            return
        if self.petting_since is None:
            self.petting_since = now
        if now - self.petting_since > 12:
            # enough petting for now
            self.petting_since = None
            self.grumpy_until = now + 20
            self.app.sounds.play("mrrp")
            if self.on_ground():
                away = self.x - 150 * self.facing
                self.walk_to(clamp(away, self.seg.x1 + 12, self.seg.x2 - 12), run=True)
            return
        if self.state != "petted":
            self.set_state("petted", 0, last_rub=now, last_heart=0.0, last_purr=0.0)
        self.data["last_rub"] = now
        if now - self.data.get("last_heart", 0) > 0.55:
            self.data["last_heart"] = now
            self.heart()
            self.pet.add(affection=3, fun=1)
        if now - self.data.get("last_purr", 0) > 2.0:
            self.data["last_purr"] = now
            self.app.sounds.play("purr")

    def clicked(self):
        if self.state in ("sleep", "bed"):
            self.heart()
            return
        self.heart()
        self.pet.add(affection=1.5)
        if self.state in CALM and self.on_ground():
            self.set_state("sit", random.uniform(2, 4), happy=True)

    def call(self):
        if self.state == "held":
            return
        if self.bed is not None:
            self.wake_up()
        px, py = pointer()
        goal = self.world.landing(px, py - 2, 10 ** 6) or self.world.floor_for(px)
        if not self.on_ground() or goal is None:
            self._pop_to_pointer()
            return
        self.go_to(goal, px, then=self.arrived, fail=self._pop_to_pointer)

    def _pop_to_pointer(self):
        px, py = pointer()
        self.route = None
        self.poof()
        self.orient = GROUND
        self.x, self.y = float(px), float(py - 4)
        self.poof()
        self.fall()
        GLib.timeout_add(900, lambda: (self.arrived() if self.on_ground() else None) and False)

    # ---------------------------------------------------------------- finding a way over the windows
    def _hop(self, a, b):
        """Where to jump from surface a to land on surface b, or None if it's too far."""
        if b.x2 - b.x1 < 44 or a.x2 - a.x1 < 30:
            return None
        dy = b.y - a.y
        if dy < -230:
            return None
        if b.x2 <= a.x1 + 12:
            tx, lx = a.x1 + 12, b.x2 - 18
        elif b.x1 >= a.x2 - 12:
            tx, lx = a.x2 - 12, b.x1 + 18
        else:
            lo, hi = max(a.x1 + 12, b.x1 + 18), min(a.x2 - 12, b.x2 - 18)
            if lo > hi:
                return None
            tx = lx = (lo + hi) / 2
        if abs(lx - tx) > 320 + max(0, dy) * 0.3:
            return None
        return tx, lx

    def _climb_hop(self, a, b):
        """Climb up the side of window b from surface a (when it's too high to jump)."""
        if b.kind != "win" or a.y <= b.y + 40 or a.y - (b.y + b.win_h) > 200:
            return None
        for side in ("left", "right"):
            wall = b.win_x if side == "left" else b.win_x + b.win_w
            stand = wall - 8 if side == "left" else wall + 8
            land = wall + 16 if side == "left" else wall - 16
            if a.x1 + 6 <= stand <= a.x2 - 6 and b.x1 <= land <= b.x2:
                return stand, land, side
        return None

    def _start_window_climb(self, key, side):
        seg, same = self.world.find(key, 0)
        seg = seg or (same[0] if same else None)
        if seg is None:
            self._next_hop()
            return
        wall = seg.win_x if side == "left" else seg.win_x + seg.win_w
        bottom = seg.y + seg.win_h
        self.facing = 1 if side == "left" else -1
        middle = self.y - self.S / 2
        if middle > bottom - 10:
            # the window ends above her: jump up to its side first
            self.set_state("crouch_jump", 0.25, x0=self.x, y0=self.y, x1=float(wall), y1=bottom - 10 + self.S / 2,
                           key=None, T=0.45, H=20.0, pounce=False, climb=(key, side))
            return
        self.seg = None
        self.orient = RIGHT_WALL if side == "left" else LEFT_WALL
        self.x, self.y = float(wall), middle
        self.set_state("climb", 0, dist=0.0, onto=(key, side))

    def _climb_window(self, dt):
        key, side = self.data["onto"]
        same = [t for t in self.world.segments if t.key == key]
        if not same:
            self.fall()
            return
        top = same[0]
        wall = top.win_x if side == "left" else top.win_x + top.win_w
        self.x = float(wall)
        speed = 70 * self.scale / 2
        self.y -= speed * dt
        self.data["dist"] += speed * dt
        self._show(self._walk_frame(), flip=(self.orient == LEFT_WALL))
        if self.y - self.S / 2 <= top.y:
            land = wall + 16 if side == "left" else wall - 16
            seg, pieces = self.world.find(key, land)
            seg = seg or (min(pieces, key=lambda t: abs((t.x1 + t.x2) / 2 - land)) if pieces else None)
            if seg is None:
                self.fall()
                return
            self.orient = GROUND
            self.seg = seg
            self.x = float(clamp(land, seg.x1 + 8, seg.x2 - 8))
            self.y = float(seg.y)
            self.facing = 1 if side == "left" else -1
            self.set_state("land", 0.2)

    def _grapple_hop(self, a, b, ax):
        """Shoot the grappling hook from surface a up to the edge of b (anything high above)."""
        if b.y > a.y - 60 or b.x2 - b.x1 < 44:
            return None
        x0, y0, _x1, _y1 = self.world.bounds
        if b.y < y0 + 30:
            return None
        anchor = clamp(ax, b.x1 + 16, b.x2 - 16)
        tx = clamp(anchor, a.x1 + 12, a.x2 - 12)
        if abs(anchor - tx) > 120:
            return None
        return tx, anchor

    def _plan(self, goal):
        """The cheapest chain of jumps and climbs from where she is to the goal surface
        (Dijkstra; walking, jumping and climbing all cost something)."""
        segs = [t for t in self.world.segments if t.x2 - t.x1 >= 44]
        start = self.seg
        ident = lambda t: (t.key, t.x1, t.x2)
        if ident(start) == ident(goal):
            return []
        best = {ident(start): 0.0}
        count = itertools.count()
        heap = [(0.0, next(count), start, self.x, [])]
        while heap:
            cost, _n, a, ax, path = heapq.heappop(heap)
            if ident(a) == ident(goal):
                self.plan_cost = cost
                return path
            if len(path) >= 7 or cost > best.get(ident(a), float("inf")):
                continue
            for b in segs:
                if ident(b) == ident(a):
                    continue
                hop = self._hop(a, b)
                if hop is not None:
                    tx, lx = hop
                    step = ("jump", tx, lx, b)
                    c = cost + abs(tx - ax) + 80 + abs(lx - tx) * 0.5
                else:
                    climb = self._climb_hop(a, b)
                    if climb is not None:
                        tx, lx, side = climb
                        step = ("climb", tx, side, b)
                        c = cost + abs(tx - ax) + 60 + (a.y - b.y)
                    else:
                        grapple = self._grapple_hop(a, b, ax)
                        if grapple is None:
                            continue
                        tx, lx = grapple
                        step = ("grapple", tx, lx, b)
                        c = cost + abs(tx - ax) + 250 + (a.y - b.y) * 0.4
                if c >= best.get(ident(b), float("inf")):
                    continue
                best[ident(b)] = c
                heapq.heappush(heap, (c, next(count), b, lx, path + [step]))
        return None

    def go_to(self, seg, x, then, fail):
        self.route = {"key": seg.key, "x": x, "then": then, "fail": fail, "hops": 0,
                      "since": time.monotonic()}
        self._next_hop()

    def _next_hop(self):
        r = self.route
        if r is None:
            return
        if not self.on_ground() or r["hops"] > 10 or time.monotonic() - r["since"] > 25:
            self.route = None
            r["fail"]()
            return
        goal, same = self.world.find(r["key"], r["x"])
        goal = goal or (same[0] if same else None)
        if goal is None:
            self.route = None
            r["fail"]()
            return
        path = self._plan(goal)
        if path is None:
            # no way by paw: out comes the portal gun
            self.route = None
            self.open_portal(goal, r["x"], r["then"])
            return
        if not path:
            self.route = None
            self.walk_to(clamp(r["x"], self.seg.x1 + 10, self.seg.x2 - 10), run=True, then=r["then"])
            return
        if r["hops"] == 0 and self.plan_cost > 900 and random.random() < 0.6:
            # a long way round: sometimes she just takes the portal
            self.route = None
            self.open_portal(goal, r["x"], r["then"])
            return
        kind, tx, lx, target = path[0]
        r["hops"] += 1
        key = target.key
        if kind == "climb":
            side = lx
            self.walk_to(tx, run=True, then=lambda: self._start_window_climb(key, side))
            return
        if kind == "grapple":
            self.walk_to(tx, run=True, then=lambda: self.start_grapple(key, lx))
            return

        def takeoff():
            seg, same_ = self.world.find(key, lx)
            seg = seg or (same_[0] if same_ else None)
            if seg is None:
                self._next_hop()
                return
            self.jump_to(clamp(lx, seg.x1 + 12, seg.x2 - 12), seg.y, key)
        self.walk_to(tx, run=True, then=takeoff)

    # ---------------------------------------------------------------- butterflies, mugs, birthdays
    def _surprises(self, dt, now):
        if self.butterfly is not None:
            if not self.butterfly.update(dt, self):
                self.butterfly = None
                if self.state == "chase_bf":
                    self.set_state("sit", random.uniform(3, 6), happy=True)
        awake = self.state in CALM and self.on_ground() and self.route is None and not self.props \
            and self.laser is None and self.box is None and not self.idle_mode
        if awake and self.butterfly is None and random.random() < dt / (300 if not night() else 1200):
            self.spawn_butterfly()
        elif awake and self.mug is None and self.seg.kind == "win" and self.seg.x2 - self.seg.x1 >= 160 \
                and random.random() < dt / 480:
            self.mug_time()
        if self.butterfly is not None and self.butterfly.chaseable() and self.state in CALM \
                and self.on_ground() and self.route is None and self.laser is None:
            self.bf_tries = 0
            self.set_state("chase_bf", 0)
        if self.box is not None and self.state != "box" and self.route is None \
                and now - self.box[0].born > 75:
            box, self.box = self.box, None          # she wasn't interested after all
            self.poof(box[2], box[1].pos[1] + box[1].h - 4 * self.scale)
            box[0].destroy()
            box[1].destroy()
        if self.pet.birthday() and now - self.party_at > 25 and self.state not in ("bed", "sleep") \
                and not self.hidden:
            self.party_at = now
            if self.pet.x is not None and not getattr(self, "_wished", False):
                self._wished = True
                x, y = self.head_point()
                Floater(text_surface(f"Happy birthday, {self.pet.name}!", self.scale, 6 * self.scale),
                        x, y - 6 * self.scale, rise=30, life=4.0, hold=2.5)
            for _ in range(5):
                x, y = self.head_point()
                Floater(sprites.render(sprites.PARTS["CONFETTI"], "tabby", self.scale),
                        x + random.uniform(-20, 20) * self.scale / 2, y,
                        rise=random.uniform(20, 50), drift=random.uniform(-8, 8) * self.scale, life=1.6)

    def spawn_butterfly(self):
        self.pet.count("butterflies")
        geo, _area = self.world.monitor_at(self.x, self.y - 1)
        side = random.choice((-1, 1))
        start = (geo.x - 20) if side < 0 else (geo.x + geo.width + 20)
        self.butterfly = Butterfly(self.scale, start, self.y - random.uniform(120, 220))
        self.raise_all()

    def _st_chase_bf(self, dt):
        bf = self.butterfly
        if bf is None or not bf.chaseable() or not self.on_ground():
            self.set_state("sit", random.uniform(3, 6), happy=True)
            return
        gap = bf.x - self.x
        self.facing = 1 if gap > 0 else -1
        target = clamp(bf.x, self.seg.x1 + 10, self.seg.x2 - 10)
        if abs(target - self.x) > 26 * self.scale / 2:
            speed = 150 * self.scale / 2
            self.x += clamp(target - self.x, -speed * dt, speed * dt)
            self.data["dist"] = self.data.get("dist", 0) + speed * dt
            self._show(self._walk_frame(2 * self.scale))
            self.data["crouch"] = 0.0
            return
        # under it: wiggle, then leap
        self.data["crouch"] = self.data.get("crouch", 0.0) + dt
        self._show("crouch0" if int(self.anim / 0.1) % 2 == 0 else "crouch1")
        if self.data["crouch"] > 0.7:
            self.bf_tries += 1
            if self.bf_tries > 3:
                self.butterfly.settle_on(self) if random.random() < 0.35 else self.butterfly.leave()
                self.pet.add(fun=15)
                self.set_state("sit", random.uniform(4, 7), happy=True)
                return
            self.butterfly.dodge()
            self.jump_to(bf.x, clamp(bf.y + 10 * self.scale, self.y - 200, self.y), None, pounce=True)

    def mug_time(self):
        """A little mug appears near the edge... and she knows exactly what to do."""
        s = self.seg
        direction = 1 if s.x2 - self.x < self.x - s.x1 else -1
        edge = s.x2 if direction > 0 else s.x1
        mx = edge - direction * 7 * self.scale
        self.mug = Prop("mug", self.scale, mx, s.y, s.key)
        self.mug.landed = True
        self.poof(mx, s.y - 3 * self.scale)
        self.props.append(self.mug)
        mug = self.mug

        def arrived():
            if mug not in self.props:
                self.decide()
                return
            self.facing = direction
            self.set_state("mug_look", 1.4, taps=0, dir=direction)
        stand = clamp(mx - direction * 13 * self.scale, s.x1 + 8, s.x2 - 8)
        self.set_state("look", 2.0)
        GLib.timeout_add(800, lambda: (self.walk_to(stand, then=arrived) if self.state == "look" else None) and False)

    def _st_mug_look(self, dt):
        # the look
        self._show("sit_blink" if 0.6 < self.t < 0.75 else "sit")
        if self.t > self.length:
            self.set_state("mug_tap", 0, taps=0, dir=self.data["dir"])

    def _st_mug_tap(self, dt):
        mug = self.mug
        if mug is None or mug not in self.props:
            self.set_state("sit", 3)
            return
        d = self.data
        phase = int(self.t / 0.45)
        self._show("tap" if self.t % 0.45 < 0.2 else "sit")
        if phase > d["taps"]:
            d["taps"] = phase
            if phase < 3:
                mug.x += d["dir"] * 3 * self.scale
                mug.place()
            elif phase == 3:
                mug.vx = d["dir"] * 70 * self.scale / 2   # and off it goes
                self.app.sounds.play("mrrp")
            elif mug.fell and mug.landed:
                self._smash(mug)
                self.set_state("sit", random.uniform(4, 8), happy=True)
        if self.t > 8:
            self.set_state("sit", 3)

    def _smash(self, mug):
        for _ in range(4):
            Floater(sprites.render(sprites.PARTS["SHARD"], "tabby", self.scale), mug.x, mug.y,
                    rise=random.uniform(6, 22), drift=random.uniform(-14, 14) * self.scale / 2, life=0.7)
        self.poof(mug.x, mug.y - 3 * self.scale)
        if mug in self.props:
            self.props.remove(mug)
        mug.destroy()
        self.mug = None
        self.pet.add(fun=10)
        self.pet.count("mugs")

    # ---------------------------------------------------------------- study buddy and idle mode
    STUDY_WORK, STUDY_BREAK = 45 * 60, 10 * 60

    def _activity(self, dt, now):
        idle = self.idle_clock.seconds()
        if idle < 60:
            self.work_s += dt
        elif idle > 5 * 60:
            if self.work_s >= self.STUDY_WORK and idle > self.STUDY_BREAK:
                self.pet.count("breaks")
            if idle > self.STUDY_BREAK or self.work_s < self.STUDY_WORK:
                self.work_s = 0.0
                self.reminders = 0
        # break reminder
        if self.pet.study and self.work_s >= self.STUDY_WORK and idle < 60 and self.reminders < 3 \
                and now - self.reminded_at > 5 * 60 and self.state in CALM and self.bed is None:
            self.reminded_at = now
            self.reminders += 1
            self.go_to_pointer(then=self._break_time)
        # idle mode: you're away, she naps in the middle of the screen
        if not self.idle_mode and idle > 3 * 60 and self.bed is None and self.state in CALM \
                and self.on_ground() and self.route is None and self.laser is None:
            self.idle_mode = True
            _geo, area = self.world.monitor_at(*pointer())
            middle = area.x + area.width / 2
            floor = self.world.floor_for(middle)
            self.go_to(floor, middle, then=lambda: self.set_state("sleep", 4 * 3600), fail=self.decide)
        elif self.idle_mode and idle < 2:
            self.idle_mode = False
            if self.state in ("sleep", "walk", "yawn") or self.route is not None:
                self.route = None
                self.set_state("stretch", 1.2, then_hi=True)

    def _st_stretch(self, dt):
        self._show("stretch")
        if self.t > self.length:
            self.set_state("yawn", 1.0)
            if self.data.get("then_hi"):
                GLib.timeout_add(1100, lambda: (self.say_hi() if self.state in CALM or self.state == "yawn"
                                                else None) and False)

    def say_hi(self):
        self.app.sounds.play("meow2")
        self.heart()
        self.set_state("sit", random.uniform(4, 8), happy=True)

    def _break_time(self):
        self.app.sounds.play("meow")
        self.pet.add(affection=2)
        x, y = self.head_point()
        mins = self.STUDY_BREAK // 60
        Floater(text_surface(f"Break time! {mins} minutes", self.scale, 6 * self.scale),
                x, y - 4 * self.scale, rise=24, life=6.0, hold=4.0)
        self.set_state("sit", random.uniform(6, 10), happy=True)

    def go_to_pointer(self, then):
        px, py = pointer()
        goal = self.world.landing(px, py - 2, 10 ** 6) or self.world.floor_for(px)
        if not self.on_ground() or goal is None:
            then()
            return
        self.go_to(goal, px, then=then, fail=then)

    def _st_study(self, dt):
        self._show("study0" if int(self.anim / 3.0) % 2 == 0 else "study1")
        if self.t > self.length:
            self.decide()

    # ---------------------------------------------------------------- the laser pointer
    def toggle_laser(self):
        if self.laser is not None:
            self.stop_laser()
            return
        if self.bed is not None:
            self.wake_up()
        dot = Overlay(sprites.render(sprites.PARTS["LASER"], "tabby", self.scale))
        dot.show()
        self.laser = (dot, time.monotonic())
        self.raise_all()
        if self.on_ground():
            self.set_state("laser", 0)

    def stop_laser(self):
        if self.laser is None:
            return
        self.laser[0].destroy()
        self.laser = None
        self.pet.add(fun=30, energy=-6, affection=4)
        self.pet.count("laser")
        if self.state == "laser":
            self.set_state("sit", random.uniform(3, 6), happy=True)

    def _move_laser(self):
        if self.laser is None:
            return
        dot, started = self.laser
        px, py = pointer()
        dot.place(px - dot.w / 2, py - dot.h / 2)
        if time.monotonic() - started > 60:
            self.stop_laser()

    def _st_laser(self, dt):
        if self.laser is None or not self.on_ground():
            self.decide()
            return
        px, py = pointer()
        s = self.seg
        target = self.world.landing(px, py - 2, 10 ** 6)
        # the dot is on another surface: jump over if she can
        if target is not None and target.key != s.key and now_ok(self.data, "hop", 0.8):
            hop = self._hop(s, target)
            if hop is not None and abs(hop[0] - self.x) < 40:
                self.jump_to(clamp(px, target.x1 + 12, target.x2 - 12), target.y, target.key)
                return
        tx = clamp(px, s.x1 + 10, s.x2 - 10)
        gap = tx - self.x
        if abs(gap) > 6:
            self.facing = 1 if gap > 0 else -1
            speed = 190 * self.scale / 2
            self.x += clamp(gap, -speed * dt, speed * dt)
            self.data["dist"] = self.data.get("dist", 0) + speed * dt
            self._show(self._walk_frame(2 * self.scale))
        else:
            self._show("crouch0" if int(self.anim / 0.1) % 2 == 0 else "crouch1")
        # pounce when the dot is just above her
        if abs(px - self.x) < 50 and self.y - 200 < py < self.y - 20 and now_ok(self.data, "pounce", 1.2):
            self.jump_to(px, clamp(py + 10 * self.scale, self.y - 200, self.y), None, pounce=True)

    # ---------------------------------------------------------------- the box
    def give_box(self):
        floor = self.world.floor_for(self.x)
        if floor is None or self.box is not None:
            return
        margin = 20 * self.scale
        bx = clamp(self.x + self.facing * 60 * self.scale / 2, floor.x1 + margin, floor.x2 - margin)
        back = Overlay(sprites.render(sprites.PARTS["BOX"], self.pet.coat, self.prop_scale()))
        back.born = time.monotonic()
        front = Overlay(sprites.render(sprites.PARTS["BOX_FRONT"], self.pet.coat, self.prop_scale()))
        front.place(bx - front.w / 2, floor.y - front.h)
        back.place(bx - back.w / 2, floor.y - front.h - 2 * self.scale)
        back.show()
        front.show()
        self.poof(bx, floor.y - 4 * self.scale)
        self.box = (back, front, bx, floor.key)
        self.raise_all()
        if self.bed is None and self.state not in ("held",):
            self.set_state("look", 2.0)
            GLib.timeout_add(900, self._head_to_box)

    def _head_to_box(self):
        if self.box is None or self.state not in CALM or self.bed is not None:
            return False
        bx, key = self.box[2], self.box[3]
        floor = self.world.floor_for(bx)
        if self.on_ground():
            self.go_to(floor, bx - self.facing * 18 * self.scale, then=self._hop_in_box, fail=self._hop_in_box)
        return False

    def _hop_in_box(self):
        if self.box is None:
            self.decide()
            return
        self.route = None
        bx = self.box[2]
        floor = self.world.floor_for(bx)
        self.facing = 1 if bx >= self.x else -1
        self.jump_to(bx, floor.y - 1 * self.scale, None)
        self.data["into_box"] = True             # carried into the jump by crouch_jump

    def _st_box(self, dt):
        long = self.length > 60
        if long and self.t > 8:
            self._show("sleep0" if int(self.anim / 1.5) % 2 == 0 else "sleep1")
            if self.anim % 3.6 < dt:
                self.zzz()
        else:
            self._show("sit_blink" if self.anim % 4.0 > 3.85 else "sit")
        if self.t > self.length:
            bx = self.box[2] if self.box else self.x
            floor = self.world.floor_for(bx)
            self.seg = floor
            out = clamp(bx + random.choice((-1, 1)) * 30 * self.scale, floor.x1 + 10, floor.x2 - 10)
            self.jump_to(out, floor.y, floor.key)
            box, self.box = self.box, None
            if box:
                GLib.timeout_add(2500, lambda: (self.poof(box[2], box[1].pos[1] + box[1].h - 4 * self.scale),
                                                box[0].destroy(), box[1].destroy()) and False)

    # ---------------------------------------------------------------- two screens
    def other_screen(self):
        here, _area = self.world.monitor_at(self.x, self.y - 1)
        others = [m for m in self.world.monitors if m[0].x != here.x or m[0].y != here.y]
        if not others:
            return
        _geo, area = others[0]
        middle = area.x + area.width / 2
        floor = self.world.floor_for(middle)
        if self.bed is not None:
            self.wake_up()
        if self.on_ground():
            self.open_portal(floor, middle, self.arrived)
        else:
            self._pop_to(middle, floor.y - 4)

    def _pop_to(self, x, y):
        self.route = None
        self.poof()
        self.orient = GROUND
        self.x, self.y = float(x), float(y)
        self.poof()
        self.fall()

    # ---------------------------------------------------------------- sweeping (Tidy Downloads)
    def sweep(self, text):
        if self.bed is not None or self.hidden or not self.on_ground() or self.state in ("held", "box"):
            return
        if self.state not in CALM and self.state not in ("sleep",):
            return
        self.route = None
        self.set_state("sweep", 3.6, text=text)

    def _st_sweep(self, dt):
        # the broom swings between two spots and she shuffles along with it
        step = int(self.anim / 0.22)
        self.x += (1 if step % 2 == 0 else -1) * 6 * dt * self.scale
        self._show("stand" if step % 4 < 2 else "stand2")
        if self.anim % 0.6 < dt:
            dust = sprites.render(sprites.PARTS["DUST"], "tabby", self.scale)
            Floater(dust, self.x + self.facing * 12 * self.scale, self.y - 1 * self.scale,
                    rise=8 * self.scale, drift=self.facing * 6 * self.scale, life=0.7)
        if self.t > self.length:
            x, y = self.head_point()
            Floater(text_surface(self.data.get("text") or "All tidy!", self.scale, 6 * self.scale),
                    x, y - 4 * self.scale, rise=20, life=3.5, hold=2.0)
            self.pet.count("sweeps")
            self.set_state("sit", random.uniform(3, 6), happy=True)

    # ---------------------------------------------------------------- a letter from Mail Brief
    def deliver(self, text):
        if self.bed is not None or self.hidden:
            return
        self.letter = text[:90]
        self.pet.count("letters")
        self.route = None
        if self.state in ("held",):
            return
        self.go_to_pointer(then=self._hand_over)

    def _hand_over(self):
        text, self.letter = self.letter, None
        if not text:
            self.decide()
            return
        x, y = self.head_point()
        Floater(sprites.render(sprites.PARTS["ENVELOPE"], "tabby", self.scale), x + 8 * self.scale, y + 10 * self.scale,
                rise=20, life=2.0, hold=1.0)
        Floater(text_surface(text, self.scale, 5 * self.scale), x, y - 6 * self.scale, rise=18, life=8.0, hold=6.0)
        self.app.sounds.play("mrrp")
        self.set_state("sit", random.uniform(4, 8), happy=True)

    # ---------------------------------------------------------------- photos
    def take_photo(self):
        self.set_state("sit", 2.5, happy=True)
        GLib.timeout_add(700, self._snap)

    def _snap(self):
        x0, y0, x1, y1 = self.world.bounds
        w, h = 4 * self.S, 3 * self.S
        rx = int(clamp(self.x - w / 2, x0, x1 - w))
        ry = int(clamp(self.y - h + self.S / 3, y0, y1 - h))
        pb = Gdk.pixbuf_get_from_window(Gdk.get_default_root_window(), rx, ry, w, h)
        if pb is None:
            return False
        shot = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
        cr = cairo.Context(shot)
        Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0)
        cr.paint()
        zoom, border, bottom = 2, 24, 90
        W, H = w * zoom + 2 * border, h * zoom + border + bottom
        card = cairo.ImageSurface(cairo.FORMAT_RGB24, W, H)
        cr = cairo.Context(card)
        cr.set_source_rgb(0.98, 0.97, 0.94)
        cr.paint()
        cr.save()
        cr.translate(border, border)
        cr.scale(zoom, zoom)
        cr.set_source_surface(shot, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_NEAREST)
        cr.paint()
        cr.restore()
        cr.set_source_rgb(0.2, 0.2, 0.25)
        cr.select_font_face("Sans", cairo.FONT_SLANT_ITALIC, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(26)
        cr.move_to(border, H - bottom + 42)
        cr.show_text(self.pet.name or "My cat")
        cr.select_font_face("Sans", cairo.FONT_SLANT_ITALIC, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(16)
        cr.move_to(border, H - bottom + 70)
        cr.show_text(time.strftime("%Y. %m. %d.  %H:%M"))
        folder = os.path.join(GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES)
                              or os.path.expanduser("~/Pictures"), "Pixel Cat")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{self.pet.name or 'cat'} {time.strftime('%Y-%m-%d %H-%M-%S')}.png")
        card.write_to_png(path)
        self.pet.count("photos")
        flash = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        fc = cairo.Context(flash)
        fc.set_source_rgba(1, 1, 1, 0.85)
        fc.paint()
        Floater(flash, rx + w / 2, ry + h, rise=0, life=0.25)
        hx, hy = self.head_point()
        Floater(text_surface("Saved to Pictures!", self.scale, 5 * self.scale), hx, hy - 4 * self.scale,
                rise=16, life=2.5, hold=1.5)
        self.last_photo = path
        return False

    # ---------------------------------------------------------------- grappling hook
    def _gun_tip(self, frame):
        """Screen position of the tip of the gadget she's holding in an aiming frame."""
        hx, hy = self.art.FRAMES[frame][1]
        dx, dy = self.art.gun_tips[frame]
        cx, cy = hx + dx, hy + dy
        if self.facing < 0:
            cx = self.art.W - 1 - cx
        wx, wy = self.x - self.S / 2, self.y - self.S
        top = self.S - self.art.H * self.scale
        return wx + (cx + 0.5) * self.scale, wy + top + (cy + 0.5) * self.scale

    def start_grapple(self, key, anchor_x):
        seg, same = self.world.find(key, anchor_x)
        seg = seg or (same[0] if same else None)
        if seg is None or not self.on_ground():
            self._next_hop()
            return
        self.facing = 1 if anchor_x >= self.x else -1
        self.pet.count("grapples")
        self.set_state("grapple_aim", 0.5, key=key, ax=float(anchor_x), win_x=seg.win_x)

    def _anchor(self):
        d = self.data
        seg, same = self.world.find(d["key"], d["ax"])
        seg = seg or (same[0] if same else None)
        if seg is None:
            return None
        if seg.win_x != d["win_x"]:           # the window moved: the hook moves with it
            d["ax"] += seg.win_x - d["win_x"]
            d["win_x"] = seg.win_x
        return d["ax"], float(seg.y), seg

    def _drop_rope(self):
        rope = self.data.get("rope")
        if rope is not None:
            rope.destroy()
            self.data["rope"] = None

    def _close_gadgets(self):
        """Put away portals and rope (also when she's interrupted halfway)."""
        for portal in self.portals:
            portal.close()
        self.portals = []
        if self.state.startswith("grapple"):
            self._drop_rope()
        if not self.get_visible() and not self.hidden:
            self.show()

    def _st_grapple_aim(self, dt):
        self._show("aim_hook")
        if self.t > self.length:
            d = self.data
            d["rope"] = Rope(self.scale)
            d["tip"] = self._gun_tip("aim_hook")
            self.state, self.t = "grapple_shoot", 0.0

    def _st_grapple_shoot(self, dt):
        self._show("aim_hook")
        anchor = self._anchor()
        if anchor is None:
            self._drop_rope()
            self.decide()
            return
        tip = self.data["tip"]
        u = clamp(self.t / 0.3, 0, 1)
        end = (tip[0] + (anchor[0] - tip[0]) * u, tip[1] + (anchor[1] - tip[1]) * u)
        self.data["rope"].set_ends(tip, end, hooked=u >= 1)
        if u >= 1:
            self.seg = None
            self.state, self.t = "grapple_pull", 0.0

    def _st_grapple_pull(self, dt):
        anchor = self._anchor()
        if anchor is None:
            self._drop_rope()
            self.fall()
            return
        ax, ay, seg = anchor
        self._show("dangle", flip=False)
        hold = 5 * self.scale                     # her paws, just above her head
        speed = 320 * self.scale / 2
        self.y = max(ay + self.S - hold, self.y - speed * dt)
        self.x += (ax - self.x) * min(1.0, dt * 6) + math.sin(self.t * 5) * 20 * dt
        self.data["rope"].set_ends((self.x, self.y - self.S + hold), (ax, ay), hooked=True)
        if self.y <= ay + self.S - hold + 0.5:
            self._drop_rope()
            self.orient = GROUND
            self.seg = seg
            self.x = float(clamp(ax, seg.x1 + 10, seg.x2 - 10))
            self.y = float(seg.y)
            self.set_state("land", 0.2)

    # ---------------------------------------------------------------- portal gun
    def open_portal(self, goal, gx, then):
        if not self.on_ground():
            self._pop_to_pointer()
            return
        s = self.seg
        step = 34 * self.scale / 2
        ax = clamp(self.x + self.facing * step, s.x1 + 12, s.x2 - 12)
        if abs(ax - self.x) < step / 2:
            self.facing = -self.facing
            ax = clamp(self.x + self.facing * step, s.x1 + 12, s.x2 - 12)
        bx = clamp(gx, goal.x1 + 14, goal.x2 - 14)
        self.facing = 1 if ax >= self.x else -1
        self.pet.count("portals")
        self.set_state("portal_aim", 0.7, ax=ax, akey=s.key, bx=bx, bkey=goal.key, then=then, made=False)

    def _st_portal_aim(self, dt):
        d = self.data
        self._show("aim_portal")
        if self.t > 0.3 and not d["made"]:
            d["made"] = True
            goal, same = self.world.find(d["bkey"], d["bx"])
            goal = goal or (same[0] if same else None)
            if goal is None:
                self.decide()
                return
            d["pa"] = PortalWindow(self.prop_scale(), d["ax"], self.seg.y)
            d["pb"] = PortalWindow(self.prop_scale(), d["bx"], goal.y)
            self.portals = [d["pa"], d["pb"]]
            self.app.sounds.play("mrrp")
            self.raise_all()
        if self.t > self.length:
            pa, pb = d["pa"], d["pb"]

            def enter():
                self.hide()                       # in she goes
                self.set_state("portal_travel", 0.35, pa=pa, pb=pb, bx=d["bx"], bkey=d["bkey"], then=d["then"])
            self.walk_to(d["ax"], run=False, then=enter)

    def _st_portal_travel(self, dt):
        d = self.data
        if self.t < self.length:
            return
        goal, same = self.world.find(d["bkey"], d["bx"])
        goal = goal or (same[0] if same else self.world.floor_for(d["bx"]))
        self.seg = goal
        self.orient = GROUND
        self.x, self.y = float(d["bx"]), float(goal.y)
        self._place()
        self.show()
        self.raise_all()
        pa, pb, then = d["pa"], d["pb"], d["then"]

        def out():
            self._close_gadgets()
            then()
        out_x = clamp(self.x + self.facing * 30 * self.scale / 2, goal.x1 + 10, goal.x2 - 10)
        self.walk_to(out_x, run=False, then=out)

    # ---------------------------------------------------------------- bedtime
    def send_to_bed(self):
        if self.bed is not None or self.state == "held":
            return
        self.pet.in_bed = True
        self.pet.wake_at = next_morning() if night() else time.time() + 45 * 60
        self.poof()
        self.pajamas = True
        # her bed goes in the bottom-left corner of the screen
        _geo, area = self.world.monitor_at(self.x, self.y - 1)
        bx = area.x + 18 * self.scale
        floor = self.world.floor_for(bx)
        self._make_bed(bx)
        self.app.sounds.play("mrrp")
        if self.on_ground():
            self.go_to(floor, bx, then=self._get_in_bed, fail=self._pop_into_bed)
            return
        else:
            self._pop_into_bed()

    def _make_bed(self, bx):
        floor = self.world.floor_for(bx)
        back = Overlay(sprites.render(sprites.PARTS["BED"], self.pet.coat, self.prop_scale()))
        front = Overlay(sprites.render(sprites.PARTS["BED_FRONT"], self.pet.coat, self.prop_scale()))
        back.place(bx - back.w / 2, floor.y - back.h)
        front.place(bx - front.w / 2, floor.y - front.h)
        back.show()
        front.show()
        self.bed = (back, front, bx)
        self.raise_all()

    def _pop_into_bed(self):
        self.route = None
        self.poof()
        self._get_in_bed()
        self.poof()

    def _get_in_bed(self):
        if self.bed is None:
            self.decide()
            return
        bx = self.bed[2]
        floor = self.world.floor_for(bx)
        self.orient = GROUND
        self.seg = floor
        self.x = float(bx)
        self.y = float(floor.y - 2 * self.scale)
        self.set_state("bed")
        self.raise_all()

    def _st_bed(self, dt):
        self._show("sleep0" if int(self.anim / 1.5) % 2 == 0 else "sleep1")
        if self.anim % 3.6 < dt:
            self.zzz()
        if time.time() >= self.pet.wake_at:
            self.wake_up()

    def _remove_bed(self):
        if self.bed is not None:
            back, front, bx = self.bed
            self.bed = None
            self.poof(bx, back.pos[1] + back.h if back.pos else None)
            back.destroy()
            front.destroy()

    def wake_up(self):
        was_in_bed = self.state == "bed"
        self._remove_bed()
        if self.pajamas:
            self.poof()
        self.pajamas = False
        self.pet.in_bed = False
        self.route = None
        if was_in_bed and self.seg is not None:
            self.y = float(self.seg.y)
            self.set_state("yawn", 1.3)

    def give_treat(self):
        if not self.on_ground():
            self.call()
            return
        s = self.seg
        x = clamp(self.x + self.facing * random.uniform(60, 120), s.x1 + 12, s.x2 - 12)
        if abs(x - self.x) < 30:
            x = clamp(self.x - self.facing * 80, s.x1 + 12, s.x2 - 12)
        prop = Prop("treat", self.scale, x, s.y - 160, s.key)
        self.props.append(prop)
        self.set_state("look", 0.7)          # she watches it fall, then decide() sends her to eat it

    def _go_eat(self, prop):
        def reach():
            if prop in self.props:
                self.set_state("eat", 2.6, prop=prop)
            else:
                self.decide()
        self.walk_to(prop.x, run=True, then=reach)

    def play_yarn(self):
        if not self.on_ground():
            return
        s = self.seg
        x = clamp(self.x + self.facing * random.uniform(80, 160), s.x1 + 12, s.x2 - 12)
        yarn = Prop("yarn", self.scale, x, s.y - 140, s.key)
        self.props.append(yarn)
        self.set_state("play", 0, prop=yarn, bats=random.randint(3, 5), dist=0.0)


# --------------------------------------------------------------------------
# pick a coat and a name
# --------------------------------------------------------------------------

CSS = b"""
.pc-preview { background-color: #c9d6e3; border-radius: 10px; }
.pc-coat { padding: 4px 8px; }
.pc-title { font-weight: bold; font-size: large; }
.pc-dim { opacity: 0.7; }
"""


class SetupWindow(Gtk.Window):
    def __init__(self, app, first):
        super().__init__(title="Adopt a cat" if first else "Your cat")
        self.app = app
        self.first = first
        self.coat = app.pet.coat
        self.step = 0
        self.set_resizable(False)
        self.set_icon_name("face-smile")
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_keep_above(True)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_border_width(16)
        self.add(box)
        title = Gtk.Label(label="Choose your cat" if first else "Change her look or name", xalign=0)
        title.get_style_context().add_class("pc-title")
        box.pack_start(title, False, False, 0)

        self.art_style = app.pet.style
        styles = Gtk.Box(spacing=0)
        styles.get_style_context().add_class("linked")
        styles.set_halign(Gtk.Align.CENTER)
        first_button = None
        for key, label in (("classic", "Classic (48 px)"), ("detailed", "Detailed (64 px)")):
            b = Gtk.RadioButton.new_with_label_from_widget(first_button, label)
            first_button = first_button or b
            b.set_mode(False)
            b.set_active(key == self.art_style)
            b.connect("toggled", lambda w, k=key: w.get_active() and self._on_style(k))
            styles.pack_start(b, False, False, 0)
        box.pack_start(styles, False, False, 0)

        self.preview = Gtk.DrawingArea()
        big = sprites.STYLES["detailed"]
        self.preview.set_size_request(big.W * 5 + 40, big.H * 5 + 10)
        self.preview.get_style_context().add_class("pc-preview")
        self.preview.connect("draw", self._draw_preview)
        box.pack_start(self.preview, False, False, 0)

        grid = Gtk.FlowBox()
        grid.set_selection_mode(Gtk.SelectionMode.NONE)
        grid.set_max_children_per_line(3)
        grid.set_min_children_per_line(3)
        group = None
        for key, (label, _colors) in sprites.COATS.items():
            button = Gtk.RadioButton.new_from_widget(group)
            group = group or button
            button.set_mode(False)
            inner = Gtk.Box(spacing=6)
            icon = sprites.render(sprites.PARTS["HEAD"], key, 2)
            image = Gtk.Image.new_from_surface(icon)
            inner.pack_start(image, False, False, 0)
            inner.pack_start(Gtk.Label(label=label), False, False, 0)
            button.add(inner)
            button.get_style_context().add_class("pc-coat")
            button.set_active(key == self.coat)
            button.connect("toggled", self._on_coat, key)
            grid.add(button)
        box.pack_start(grid, False, False, 0)

        row = Gtk.Box(spacing=6)
        row.pack_start(Gtk.Label(label="Name:"), False, False, 0)
        self.entry = Gtk.Entry()
        self.entry.set_text(app.pet.name or "")
        self.entry.set_placeholder_text(random.choice(NAME_IDEAS))
        self.entry.connect("activate", lambda *_: self._done())
        row.pack_start(self.entry, True, True, 0)
        idea = Gtk.Button(label="Idea")
        idea.set_tooltip_text("Suggest a name")
        idea.connect("clicked", lambda *_: self.entry.set_text(random.choice(NAME_IDEAS)))
        row.pack_start(idea, False, False, 0)
        box.pack_start(row, False, False, 0)

        hint = Gtk.Label(xalign=0)
        hint.set_markup("<small>Rub her with the mouse to pet her, drag to pick her up,\n"
                        "right-click for treats, play and settings.</small>")
        hint.get_style_context().add_class("pc-dim")
        box.pack_start(hint, False, False, 0)

        done = Gtk.Button(label="Adopt" if first else "Save")
        done.get_style_context().add_class("suggested-action")
        done.connect("clicked", lambda *_: self._done())
        box.pack_start(done, False, False, 0)
        self.connect("delete-event", self._on_close)
        GLib.timeout_add(140, self._animate)

    def _on_style(self, key):
        self.art_style = key
        self.preview.queue_draw()

    def _on_coat(self, button, key):
        if button.get_active():
            self.coat = key
            self.preview.queue_draw()

    def _animate(self):
        if not self.get_visible():
            return False
        self.step += 1
        self.preview.queue_draw()
        return True

    def _draw_preview(self, widget, cr):
        width, height = widget.get_allocated_width(), widget.get_allocated_height()
        cycle = self.step % 48
        name = f"walk{self.step % 4}" if cycle < 28 else ("sit" if cycle < 40 else "sit_happy")
        art = sprites.STYLES[self.art_style]
        img = sprites.render(art.FRAMES[name][0], self.coat, 6 if art.name == "classic" else 5, flip=False)
        cr.set_source_surface(img, (width - img.get_width()) / 2, height - img.get_height() - 6)
        cr.get_source().set_filter(cairo.FILTER_NEAREST)
        cr.paint()
        return False

    def _done(self):
        name = self.entry.get_text().strip() or self.entry.get_placeholder_text()
        self.app.pet.name = name[:24]
        self.app.pet.coat = self.coat
        self.app.pet.style = self.art_style
        if not self.app.pet.adopted:
            self.app.pet.adopted = time.strftime("%Y-%m-%d")
        self.app.pet.save()
        self.destroy()
        self.app.setup_window = None
        self.app.ensure_cat()

    def _on_close(self, *_):
        self.app.setup_window = None
        if self.first and not self.app.pet.name:
            self.app.quit()
        return False


# --------------------------------------------------------------------------
# application
# --------------------------------------------------------------------------

class PixelCatApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.pet = None
        self.cat = None
        self.setup_window = None
        self.menu = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.hold()
        # logging out or shutting down: save her first
        for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, lambda *_: (self.quit(), False)[1])
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        try:
            self.scale = int(clamp(int(setting("size", "2")), 1, 4))
        except ValueError:
            self.scale = 2
        self.pet = Pet()
        self.sounds = Sounds(self.pet)
        threading.Thread(target=self.sounds.prepare, daemon=True).start()
        self.weather = Weather(self.pet)
        self.world = World()
        self.world.on_stacking_changed = lambda: self.cat and self.cat.raise_all()
        self.music = Music()
        self.hotkey = GlobalHotkey(lambda: self.cat and self.cat.call())
        wanted = setting("shortcut")
        if wanted and wanted.lower() in ("none", "off"):
            active = None
        else:
            active = self.hotkey.start([wanted] if wanted else DEFAULT_SHORTCUTS)
        self.shortcut = pretty_accel(active) if active else None
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(ACTIVE_SHORTCUT_FILE, "w", encoding="utf-8") as f:
                f.write(self.shortcut or "none")
        except OSError:
            pass

    def do_shutdown(self):
        if self.cat is not None and self.pet is not None:
            self.pet.x = int(self.cat.x)
        if self.pet is not None and self.pet.name:
            try:
                self.pet.save()
            except OSError:
                pass
        Gtk.Application.do_shutdown(self)

    def do_command_line(self, command_line):
        args = command_line.get_arguments()[1:]
        if "--quit" in args:
            self.quit()
            return 0
        if "--setup" in args:
            self.show_setup()
            return 0
        if "--call" in args:
            if self.cat is not None:
                self.cat.call()
                return 0
        if "--sweep" in args:
            i = args.index("--sweep")
            if self.cat is not None:
                self.cat.sweep(args[i + 1] if i + 1 < len(args) else "All tidy!")
            elif not self.pet.name and self.setup_window is None:
                self.quit()
            return 0
        if "--deliver" in args:
            i = args.index("--deliver")
            text = args[i + 1] if i + 1 < len(args) else "You've got mail"
            if not self.pet.name:
                if self.cat is None and self.setup_window is None:
                    self.quit()
                return 0
            if self.cat is None:
                self.ensure_cat()
                GLib.timeout_add(3000, lambda: self.cat.deliver(text) and False)
            else:
                self.cat.deliver(text)
            return 0
        if "--treat" in args and self.cat is not None:
            self.cat.give_treat()
            return 0
        if "--do" in args and self.cat is not None:
            i = args.index("--do")
            if i + 1 < len(args):
                self.cat.debug_do(args[i + 1])
            return 0
        if "--yarn" in args and self.cat is not None:
            self.cat.play_yarn()
            return 0
        if not self.pet.name:
            self.show_setup()
        else:
            self.ensure_cat(call=True)     # started again from the menu: she comes to you
        return 0

    def ensure_cat(self, call=False):
        if self.cat is None:
            self.cat = Cat(self)
            self.sounds.play("mrrp")
        elif call:
            self.cat.call()
        else:
            self.cat.recolor()

    def show_setup(self):
        if self.setup_window is None:
            self.setup_window = SetupWindow(self, first=not self.pet.name)
            self.setup_window.show_all()
            self.setup_window.entry.grab_focus()
        self.setup_window.present()

    def show_menu(self, event):
        pet, cat = self.pet, self.cat
        menu = Gtk.Menu()

        def item(label, callback=None, into=menu):
            mi = Gtk.MenuItem(label=label)
            mi.set_sensitive(callback is not None)
            if callback:
                mi.connect("activate", lambda *_: callback())
            into.append(mi)
            return mi

        def check(label, value, setter, into, tooltip=None):
            mi = Gtk.CheckMenuItem(label=label)
            mi.set_active(value)
            if tooltip:
                mi.set_tooltip_text(tooltip)
            mi.connect("toggled", lambda w: (setter(w.get_active()), pet.save()))
            into.append(mi)

        def submenu(label):
            mi = Gtk.MenuItem(label=label)
            sub = Gtk.Menu()
            mi.set_submenu(sub)
            menu.append(mi)
            return sub

        header = item(f"{pet.name} is {pet.mood_word()}")
        header.get_child().set_markup(f"<b>{GLib.markup_escape_text(pet.name)}</b> is {pet.mood_word()}"
                                      + ("  (it's her birthday!)" if pet.birthday() else ""))
        item(pet.needs_line())
        if pet.weather and self.weather.text:
            item(self.weather.text)
        menu.append(Gtk.SeparatorMenuItem())
        item("Give a treat" + ("  (she's hungry!)" if pet.fullness < 30 else ""), cat.give_treat)
        play = submenu("Play" + ("  (she's bored!)" if pet.fun < 25 else ""))
        item("Ball of yarn", cat.play_yarn, play)
        item("Stop the laser pointer" if cat.laser is not None else "Laser pointer", cat.toggle_laser, play)
        item("Cardboard box", cat.give_box if cat.box is None else None, play)
        item("Call her here" + (f"  ({self.shortcut})" if self.shortcut else ""), cat.call)
        if len(self.world.monitors) > 1:
            item("Go to the other screen", cat.other_screen)
        if cat.bed is not None:
            item("Wake her up", cat.wake_up)
        else:
            item("Send her to bed" + ("  (it's late!)" if night() else ""), cat.send_to_bed)
        menu.append(Gtk.SeparatorMenuItem())
        item("Take a photo", cat.take_photo)
        item(f"{pet.name}'s diary", self.show_diary)
        settings = submenu("Settings")
        check("Sounds", not pet.muted, lambda v: setattr(pet, "muted", not v), settings)
        check("Study buddy (break reminders)", pet.study, lambda v: setattr(pet, "study", v), settings,
              "After 45 minutes of work she reminds you to take a 10-minute break")
        check("Weather outfits (Budapest)", pet.weather,
              lambda v: (setattr(pet, "weather", v), v and self.weather._check()), settings,
              "Umbrella when it rains, scarf when it snows")
        check("Seasonal hats", pet.hats, lambda v: (setattr(pet, "hats", v), cat.recolor()), settings,
              "Pumpkin in October, Santa hat in December, party hat on her birthday")
        check("Explore inside windows", pet.explore, lambda v: setattr(pet, "explore", v), settings,
              "Sit on text boxes, chat bubbles, progress bars and other lines inside windows")
        item("Change coat or name…", self.show_setup)
        menu.append(Gtk.SeparatorMenuItem())
        item("Quit", self.quit)
        menu.show_all()
        self.menu = menu
        menu.popup_at_pointer(event)

    def show_diary(self):
        if getattr(self, "diary", None) is not None:
            self.diary.present()
            return
        pet = self.pet
        win = Gtk.Window(title=f"{pet.name}'s diary")
        win.set_resizable(False)
        win.set_keep_above(True)
        win.set_position(Gtk.WindowPosition.CENTER)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_border_width(18)
        win.add(box)
        portrait = sprites.render(sprites.STYLES[pet.style].FRAMES["sit_happy"][0], pet.coat,
                                  5 if pet.style == "classic" else 4)
        box.pack_start(Gtk.Image.new_from_surface(portrait), False, False, 0)
        title = Gtk.Label(xalign=0.5)
        title.set_markup(f"<span size='x-large' weight='bold'>{GLib.markup_escape_text(pet.name)}</span>")
        box.pack_start(title, False, False, 0)
        days = pet.days_together()
        since = Gtk.Label(label=f"Together for {days} day{'s' if days != 1 else ''}"
                          + (f" (since {pet.adopted.replace('-', '. ')}.)" if pet.adopted else ""))
        since.get_style_context().add_class("pc-dim")
        box.pack_start(since, False, False, 4)
        st = pet.stats
        rows = [("Hearts received", "hearts"), ("Treats eaten", "treats"), ("Yarn games", "yarn"),
                ("Laser chases", "laser"), ("Boxes sat in", "boxes"), ("Butterflies chased", "butterflies"),
                ("Mugs knocked off", "mugs"), ("Grappling hooks fired", "grapples"), ("Portals opened", "portals"),
                ("Letters delivered", "letters"), ("Downloads swept", "sweeps"), ("Breaks she made you take", "breaks"), ("Photos taken", "photos")]
        grid = Gtk.Grid(column_spacing=24, row_spacing=4)
        for i, (label, key) in enumerate(rows):
            grid.attach(Gtk.Label(label=label, xalign=0), 0, i, 1, 1)
            value = Gtk.Label(xalign=1)
            value.set_markup(f"<b>{st.get(key, 0)}</b>")
            grid.attach(value, 1, i, 1, 1)
        box.pack_start(grid, False, False, 6)
        win.connect("destroy", lambda *_: setattr(self, "diary", None))
        self.diary = win
        win.show_all()

def main():
    if "--shortcut" in sys.argv[1:]:
        try:
            with open(ACTIVE_SHORTCUT_FILE, encoding="utf-8") as f:
                print(f.read().strip())
        except OSError:
            print("none (Pixel Cat is not running)")
        return 0
    if "--help" in sys.argv[1:] or "-h" in sys.argv[1:]:
        print(__doc__)
        return 0
    return PixelCatApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
