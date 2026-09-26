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
import json
import math
import os
import random
import shutil
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
        self.load()

    def load(self):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        for key in ("name", "coat", "fullness", "fun", "energy", "affection", "muted", "x"):
            if key in data:
                setattr(self, key, data[key])
        if self.coat not in sprites.COATS:
            self.coat = "tabby"

    def save(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        data = {k: getattr(self, k) for k in ("name", "coat", "fullness", "fun", "energy", "affection",
                                              "muted", "x")}
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
    __slots__ = ("key", "y", "x1", "x2", "win_x", "kind", "note")

    def __init__(self, key, y, x1, x2, win_x=0, kind="floor", note=False):
        self.key, self.y, self.x1, self.x2 = key, y, x1, x2
        self.win_x, self.kind, self.note = win_x, kind, note


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

    def refresh(self):
        display = Gdk.Display.get_default()
        self.monitors = []
        segs = []
        x0 = y0 = 10 ** 9
        x1 = y1 = -10 ** 9
        for i in range(display.get_n_monitors()):
            m = display.get_monitor(i)
            geo, area = m.get_geometry(), m.get_workarea()
            self.monitors.append((geo, area))
            segs.append(Seg(("floor", i), area.y + area.height, geo.x, geo.x + geo.width))
            x0, y0 = min(x0, geo.x), min(y0, geo.y)
            x1, y1 = max(x1, geo.x + geo.width), max(y1, geo.y + geo.height)
        self.bounds = (x0, y0, x1, y1)
        self.fullscreen = False
        if self.screen is not None:
            self.screen.force_update()
            workspace = self.screen.get_active_workspace()
            usable = []
            for w in self.screen.get_windows_stacked():
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
            self.fullscreen = bool(active is not None and active.is_fullscreen() and not active.is_minimized())
            self.stacking = tuple(u[0].get_xid() for u in usable)
            for index, (w, x, y, width, height) in enumerate(usable):
                if width < self.MIN_WIDTH or y < y0 + 40:
                    continue
                pieces = [(x + 3, x + width - 3)]
                for _w2, ox, oy, ow, oh in usable[index + 1:]:
                    if oy <= y + 1 and oy + oh > y:
                        pieces = self._cut(pieces, ox, ox + ow)
                title = (w.get_name() or "").lower()
                klass = ((w.get_class_group_name() or "") + " " + (w.get_class_instance_name() or "")).lower()
                note = "quick note" in title or "quick_notes" in klass or "quick-notes" in klass
                for a, b in pieces:
                    if b - a >= 40:
                        segs.append(Seg(("win", w.get_xid()), y, a, b, x, "win", note))
        self.segments = segs

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

    def monitor_at(self, x, y):
        for geo, area in self.monitors:
            if geo.x <= x < geo.x + geo.width and geo.y <= y < geo.y + geo.height:
                return geo, area
        return self.monitors[0] if self.monitors else (None, None)

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

def text_surface(text, scale):
    size = 7 * scale
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, int(size * 1.2), int(size * 1.4))
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
        self.images = [sprites.render(sprites.PARTS["FISH" if kind == "treat" else "YARN"], "tabby", scale)]
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
            if self.x < seg.x1 + 6 or self.x > seg.x2 - 6:
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


# --------------------------------------------------------------------------
# the cat
# --------------------------------------------------------------------------

CALM = {"stand", "sit", "walk", "groom", "sad", "vibe", "look"}
GROUND, RIGHT_WALL, LEFT_WALL, CEILING = 0, 1, 2, 3
ANGLE = {GROUND: 0, RIGHT_WALL: -math.pi / 2, LEFT_WALL: math.pi / 2, CEILING: math.pi}


class Cat(Gtk.Window):
    def __init__(self, app):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.app = app
        self.pet = app.pet
        self.world = app.world
        self.scale = app.scale
        self.S = max(sprites.W, sprites.H) * self.scale
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
        else:
            px, _py = pointer()
            self.x, self.y = float(clamp(px, x0 + 60, x1 - 60)), float(y0 + 10)
            self.set_state("fall")
        self._render()

    def recolor(self):
        self.cache.clear()
        self.current_key = None
        self._render()

    # ---------------------------------------------------------------- drawing
    def _image(self, key):
        if key in self.cache:
            return self.cache[key]
        name, flip, orient, headset = key
        frame = sprites.FRAMES[name]
        if headset and name != "held":
            frame = sprites.with_headset(frame)
        img = sprites.render(frame[0], self.pet.coat, self.scale, flip)
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
        key = (name, flip, self.orient, self.headset)
        if key == self.current_key:
            return
        surface, region = self._image(key)
        self.current, self.current_key = surface, key
        self.input_shape_combine_region(region)
        if not self.composited:
            self.shape_combine_region(region)
        self.queue_draw()

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
        self.state, self.t, self.length, self.data = name, 0.0, length, data
        self.anim = 0.0

    def _tick(self):
        now = time.monotonic()
        dt = min(0.1, now - self.last)
        self.last = now
        if now - self.last_save > 60:
            self.last_save = now
            self.pet.x = int(self.x)
            try:
                self.pet.save()
            except OSError:
                pass
        if self.hidden:
            self._debug(now)
            return True
        self.pet.tick(dt, self.state == "sleep")
        self.t += dt
        self.anim += dt
        self._music(dt)
        getattr(self, "_st_" + self.state)(dt)
        for prop in list(self.props):
            prop.update(dt, self.world)
            if now - prop.born > 90 and self.data.get("prop") is not prop:
                self.props.remove(prop)          # forgotten somewhere: tidy it away
                self.poof(prop.x, prop.y - 4 * self.scale)
                prop.destroy()
        self._ambient(dt)
        self._check_rub(now)
        self._place()
        self._debug(now)
        return True

    def _debug(self, now):
        """Writes what she's doing to $PIXEL_CAT_DEBUG (for testing)."""
        if DEBUG_FILE and now - getattr(self, "_debug_at", 0) > 0.3:
            self._debug_at = now
            with open(DEBUG_FILE, "w") as f:
                json.dump({"state": self.state, "hidden": self.hidden, "x": int(self.x), "y": int(self.y), "orient": self.orient,
                           "seg": str(self.seg.key) if self.seg else None, "headset": self.headset,
                           "win": self.pos, "props": [(p.kind, int(p.x), int(p.y), p.landed, int(p.vx), str(p.seg_key)) for p in self.props], "data": {k: v for k, v in self.data.items() if isinstance(v, (int, float, str))},
                           "pet": {k: round(getattr(self.pet, k), 1)
                                   for k in ("fullness", "fun", "energy", "affection")}}, f)

    def _refresh(self):
        self.world.refresh()
        if self.world.fullscreen and not self.hidden:
            self.hidden = True
            self.hide()
            for prop in self.props:
                prop.hide()
        elif not self.world.fullscreen and self.hidden:
            self.hidden = False
            self.show()
            for prop in self.props:
                prop.show()
            self.raise_all()
        self._reattach()
        return True

    def raise_all(self):
        for w in [self] + self.props:
            gdk_window = w.get_window()
            if gdk_window is not None and w.get_visible():
                gdk_window.raise_()

    def _reattach(self):
        """Ride along when her window moves; fall when it closes or gets covered."""
        if self.seg is None or self.orient != GROUND or self.state in ("jump", "fall", "held", "crouch_jump"):
            return
        seg, same = self.world.find(self.seg.key, self.x)
        if same and self.seg.kind == "win" and same[0].win_x != self.seg.win_x:
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
        else:
            self.set_state("stand", 2)

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
        if u >= 1:
            key = d.get("key")
            seg = None
            if key is not None:
                seg, _same = self.world.find(key, self.x)
            if seg is not None:
                self.seg = seg
                self.y = float(seg.y)
                self.set_state("land", 0.15)
            else:
                self.fall(vx=(d["x1"] - d["x0"]) / d["T"] * 0.3, vy=0)
            if d.get("pounce"):
                self.pet.add(fun=6, energy=-0.5)

    def _st_fall(self, dt):
        self.orient = GROUND
        before = self.y
        self.vy = min(self.vy + GRAVITY * dt, 1600)
        self.y += self.vy * dt
        self.x += self.vx * dt
        self.vx *= 0.99
        x0, _y0, x1, _y1 = self.world.bounds
        if self.x < x0 + 10 or self.x > x1 - 10:
            self.x = clamp(self.x, x0 + 10, x1 - 10)
            self.vx = -self.vx * 0.4
        self._show("jump")
        seg = self.world.landing(self.x, before, self.y) if self.vy >= 0 else None
        if seg is None:
            floor = self.world.floor_for(self.x)
            if floor is not None and self.y >= floor.y:
                seg = floor
        if seg is not None:
            self.seg = seg
            self.y = float(seg.y)
            self.x = clamp(self.x, seg.x1 + 8, seg.x2 - 8)
            self.vx = self.vy = 0.0
            self.set_state("land", 0.2)

    def _st_land(self, dt):
        self._show("crouch0")
        if self.t > self.length:
            self.set_state("stand", random.uniform(0.5, 1.5))

    def _st_climb(self, dt):
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
        x0, _y0, x1, _y1 = self.world.bounds
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
        if self.state not in CALM or not self.on_ground() or self.props:
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
        near = abs(px - self.x) < 280 * self.scale / 2 and self.y - 190 < py < self.y + 8
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
        if self.state == "sleep":
            self.heart()
            return
        self.heart()
        self.pet.add(affection=1.5)
        if self.state in CALM and self.on_ground():
            self.set_state("sit", random.uniform(2, 4), happy=True)

    def call(self):
        if self.state == "held":
            return
        px, py = pointer()
        if self.on_ground() and abs(py - self.y) < 90 and self.seg.x1 <= px <= self.seg.x2 \
                and abs(px - self.x) < 900:
            self.set_state("come", 0, dist=0.0)
            return
        self.poof()
        self.orient = GROUND
        self.x, self.y = float(px), float(py - 4)
        self.poof()
        self.fall()
        GLib.timeout_add(900, lambda: (self.arrived() if self.on_ground() else None) and False)

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

        self.preview = Gtk.DrawingArea()
        self.preview.set_size_request(sprites.W * 6 + 40, sprites.H * 6 + 20)
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
        img = sprites.render(sprites.FRAMES[name][0], self.coat, 6, flip=False)
        cr.set_source_surface(img, (width - img.get_width()) / 2, height - img.get_height() - 6)
        cr.get_source().set_filter(cairo.FILTER_NEAREST)
        cr.paint()
        return False

    def _done(self):
        name = self.entry.get_text().strip() or self.entry.get_placeholder_text()
        self.app.pet.name = name[:24]
        self.app.pet.coat = self.coat
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
        pet = self.pet
        menu = Gtk.Menu()

        def item(label, callback=None, sensitive=True):
            mi = Gtk.MenuItem(label=label)
            mi.set_sensitive(sensitive and callback is not None)
            if callback:
                mi.connect("activate", lambda *_: callback())
            menu.append(mi)
            return mi

        header = item(f"{pet.name} is {pet.mood_word()}")
        header.get_child().set_markup(f"<b>{GLib.markup_escape_text(pet.name)}</b> is {pet.mood_word()}")
        item(pet.needs_line())
        menu.append(Gtk.SeparatorMenuItem())
        item("Give a treat" + ("  (she's hungry!)" if pet.fullness < 30 else ""), lambda: self.cat.give_treat())
        item("Play with yarn" + ("  (she's bored!)" if pet.fun < 25 else ""), lambda: self.cat.play_yarn())
        item(f"Call her here" + (f"  ({self.shortcut})" if self.shortcut else ""), lambda: self.cat.call())
        menu.append(Gtk.SeparatorMenuItem())
        sounds = Gtk.CheckMenuItem(label="Sounds")
        sounds.set_active(not pet.muted)
        sounds.connect("toggled", lambda w: (setattr(pet, "muted", not w.get_active()), pet.save()))
        menu.append(sounds)
        item("Change coat or name…", self.show_setup)
        menu.append(Gtk.SeparatorMenuItem())
        item("Quit", self.quit)
        menu.show_all()
        self.menu = menu
        menu.popup_at_pointer(event)


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
