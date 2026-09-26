#!/usr/bin/env python3
"""Text Grab - draw a box around anything on screen and get its text (and QR codes).

Press Ctrl+Alt+G, drag a box around text - a PDF, a video, a website that blocks
copying, a photo - and the text is in your clipboard. Works offline (Tesseract),
reads Hungarian, English and German, and QR codes.

Usage:
    text-grab             start in the background (the shortcut then works)
    text-grab --grab      grab now (same as the shortcut)
    text-grab --last      show the last result again
    text-grab --file IMG  read the text of an image file
    text-grab --quit      stop it
    text-grab --shortcut  print the shortcut in use

Settings in ~/.config/text-grab/settings.ini:
    [text-grab]
    shortcut=<Primary><Alt>g     (none = off)
    languages=hun+eng+deu
"""

import ctypes
import ctypes.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango, PangoCairo  # noqa: E402

APP_ID = "io.github.textgrab.TextGrab"
CONFIG_DIR = os.path.join(GLib.get_user_config_dir(), "text-grab")
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.ini")
ACTIVE_SHORTCUT_FILE = os.path.join(CONFIG_DIR, "active-shortcut")
CACHE_DIR = os.path.join(GLib.get_user_cache_dir(), "text-grab")
DEFAULT_SHORTCUTS = ["<Primary><Alt>g", "<Super><Shift>t", "<Primary><Alt>y"]
DEFAULT_LANGUAGES = "hun+eng+deu"
QUICK_NOTES = os.path.expanduser("~/.local/bin/quick-notes")
POPUP_SECONDS = 10

CSS = b"""
window.tg-popup { background-color: transparent; }
.tg-frame {
    background-color: @theme_bg_color;
    border: 1px solid alpha(@theme_fg_color, 0.2);
    border-radius: 10px;
}
.tg-title { font-weight: bold; }
.tg-ok { color: #2e9d57; font-weight: bold; }
.tg-dim { opacity: 0.65; font-size: small; }
.tg-qr { background-color: alpha(@theme_selected_bg_color, 0.15); border-radius: 6px; padding: 6px; }
.tg-preview { font-family: monospace; }
.tg-frame button { padding: 2px 8px; min-height: 0; }
"""


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------

def setting(key, default=None):
    kf = GLib.KeyFile()
    try:
        kf.load_from_file(SETTINGS_FILE, GLib.KeyFileFlags.NONE)
        value = kf.get_string("text-grab", key).strip()
        return value or default
    except GLib.Error:
        return default


def installed_languages():
    try:
        out = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in out.splitlines()[1:] if line.strip() and line.strip() != "osd"]


def languages():
    wanted = setting("languages", DEFAULT_LANGUAGES).split("+")
    available = installed_languages()
    use = [lang for lang in wanted if lang in available] or (["eng"] if "eng" in available else available[:1])
    return "+".join(use)


# --------------------------------------------------------------------------
# reading the text (Tesseract) and QR codes (zbar)
# --------------------------------------------------------------------------

def prepare_image(pixbuf, path):
    """Write a version of the picture that OCR reads best: enlarged, dark text on light."""
    w, h = pixbuf.get_width(), pixbuf.get_height()
    # Screens are low resolution for OCR; enlarging small text helps a lot.
    scale = 3 if h < 80 or w < 200 else (2 if max(w, h) < 2000 else 1)
    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, w * scale, h * scale)
    cr = cairo.Context(surface)
    cr.set_source_rgb(1, 1, 1)
    cr.paint()
    cr.scale(scale, scale)
    Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
    cr.get_source().set_filter(cairo.FILTER_BEST)
    cr.paint()
    # Light text on a dark background (dark mode, video subtitles): flip it.
    tiny = pixbuf.scale_simple(1, 1, GdkPixbuf.InterpType.TILES)
    px = tiny.get_pixels()
    if (0.2126 * px[0] + 0.7152 * px[1] + 0.0722 * px[2]) / 255 < 0.45:
        cr.identity_matrix()
        cr.set_operator(cairo.OPERATOR_DIFFERENCE)
        cr.set_source_rgb(1, 1, 1)
        cr.paint()
    surface.write_to_png(path)


def prepare_qr_image(pixbuf, path):
    """QR readers need a white margin around the code; add one and enlarge a little."""
    w, h = pixbuf.get_width(), pixbuf.get_height()
    scale = 2 if max(w, h) < 1200 else 1
    border = 40
    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, w * scale + 2 * border, h * scale + 2 * border)
    cr = cairo.Context(surface)
    cr.set_source_rgb(1, 1, 1)
    cr.paint()
    cr.translate(border, border)
    cr.scale(scale, scale)
    Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
    cr.get_source().set_filter(cairo.FILTER_NEAREST)     # keep the squares sharp
    cr.paint()
    surface.write_to_png(path)


def clean_text(text):
    lines = [line.rstrip() for line in text.replace("\f", "").splitlines()]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip("\n")


def run_ocr(path, layout):
    """layout: "reading" (text in columns, read column by column) or "table" (keep rows)."""
    psm = "6" if layout == "table" else "3"
    cmd = ["tesseract", path, "stdout", "-l", languages(), "--psm", psm,
           "-c", "preserve_interword_spaces=1"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "OCR failed")
    return clean_text(result.stdout)


def read_qr(path):
    if not shutil.which("zbarimg"):
        return []
    try:
        result = subprocess.run(["zbarimg", "--quiet", "--raw", path], capture_output=True,
                                text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


class Grab:
    """One grabbed picture and what was read from it."""

    def __init__(self, pixbuf):
        self.pixbuf = pixbuf
        os.makedirs(CACHE_DIR, exist_ok=True)
        self.original = os.path.join(CACHE_DIR, "last-original.png")
        self.prepared = os.path.join(CACHE_DIR, "last-prepared.png")
        self.qr_image = os.path.join(CACHE_DIR, "last-qr.png")
        pixbuf.savev(self.original, "png", [], [])
        prepare_image(pixbuf, self.prepared)
        prepare_qr_image(pixbuf, self.qr_image)
        self.text = ""
        self.qr = []
        self.layout = "reading"
        self.error = None

    def read(self, layout=None):
        """Runs in a worker thread."""
        if layout:
            self.layout = layout
        self.qr = read_qr(self.qr_image) or read_qr(self.original)
        try:
            self.text = run_ocr(self.prepared, self.layout)
            self.error = None
        except Exception as e:
            self.text = ""
            self.error = str(e)
        if self.qr and len(self.text.strip()) < 40:
            self.text = ""          # just the QR pattern read as letters - drop it
        return self


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
# the selection overlay: a frozen, dimmed screenshot you drag a box on
# --------------------------------------------------------------------------

class Selector(Gtk.Window):
    def __init__(self, shot, on_done):
        super().__init__()
        self.shot = shot
        self.on_done = on_done
        self.start = None
        self.current = None
        self.finished = False
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.move(0, 0)
        self.set_default_size(shot.get_width(), shot.get_height())
        area = Gtk.DrawingArea()
        area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK |
                        Gdk.EventMask.POINTER_MOTION_MASK)
        area.connect("draw", self._draw)
        area.connect("button-press-event", self._press)
        area.connect("motion-notify-event", self._motion)
        area.connect("button-release-event", self._release)
        self.add(area)
        self.area = area
        self.connect("key-press-event", self._key)
        self.connect("map-event", self._mapped)

    def _mapped(self, *_):
        window = self.get_window()
        window.set_cursor(Gdk.Cursor.new_from_name(self.get_display(), "crosshair"))
        self.move(0, 0)
        self.resize(self.shot.get_width(), self.shot.get_height())
        # take the mouse and keyboard so Esc and the drag always arrive here
        seat = self.get_display().get_default_seat()
        seat.grab(window, Gdk.SeatCapabilities.ALL, True, None, None, None)   # True: events still reach the drawing area
        self.present()
        return False

    def _rect(self):
        if not self.start or not self.current:
            return None
        (x1, y1), (x2, y2) = self.start, self.current
        return int(min(x1, x2)), int(min(y1, y2)), int(abs(x2 - x1)), int(abs(y2 - y1))

    def _draw(self, _widget, cr):
        Gdk.cairo_set_source_pixbuf(cr, self.shot, 0, 0)
        cr.paint()
        w, h = self.shot.get_width(), self.shot.get_height()
        rect = self._rect()
        cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        cr.rectangle(0, 0, w, h)
        if rect and rect[2] > 1 and rect[3] > 1:
            cr.rectangle(*rect)
        cr.set_source_rgba(0, 0, 0, 0.45)
        cr.fill()
        if rect and rect[2] > 1 and rect[3] > 1:
            cr.set_source_rgb(0.26, 0.56, 1.0)
            cr.set_line_width(2)
            cr.rectangle(rect[0] + 0.5, rect[1] + 0.5, rect[2], rect[3])
            cr.stroke()
        # hint at the top
        hint = "Drag a box around the text or QR code  ·  Esc to cancel"
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(Pango.FontDescription.from_string("Sans Bold 11"))
        layout.set_text(hint, -1)
        tw, th = layout.get_pixel_size()
        mx, my = (self.get_display().get_monitor_at_point(0, 0).get_geometry().width - tw) / 2, 18
        cr.set_source_rgba(0, 0, 0, 0.75)
        cr.rectangle(mx - 12, my - 6, tw + 24, th + 12)
        cr.fill()
        cr.set_source_rgb(1, 1, 1)
        cr.move_to(mx, my)
        PangoCairo.show_layout(cr, layout)
        return False

    def _press(self, _w, event):
        if event.button == 1:
            self.start = (event.x, event.y)
            self.current = (event.x, event.y)
        elif event.button == 3:
            self._finish(None)
        return True

    def _motion(self, _w, event):
        if self.start:
            self.current = (event.x, event.y)
            self.area.queue_draw()
        return True

    def _release(self, _w, event):
        if event.button != 1 or not self.start:
            return True
        self.current = (event.x, event.y)
        rect = self._rect()
        self._finish(rect if rect and rect[2] >= 6 and rect[3] >= 6 else None)
        return True

    def _key(self, _w, event):
        if event.keyval == Gdk.KEY_Escape:
            self._finish(None)
            return True
        return False

    def _finish(self, rect):
        if self.finished:
            return
        self.finished = True
        self.get_display().get_default_seat().ungrab()
        self.hide()
        crop = self.shot.new_subpixbuf(*rect).copy() if rect else None
        self.destroy()
        # give the compositor a moment so the overlay is really gone
        GLib.timeout_add(60, lambda: self.on_done(crop, rect) or False)


# --------------------------------------------------------------------------
# the small popup: first lines, fading out; click for everything
# --------------------------------------------------------------------------

class Popup(Gtk.Window):
    WIDTH = 380

    def __init__(self, app, grab):
        super().__init__()
        self.app = app
        self.grab = grab
        self.hovered = False
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)
        self.set_accept_focus(False)
        self.get_style_context().add_class("tg-popup")
        screen = self.get_screen()
        if screen.get_rgba_visual() is not None and screen.is_composited():
            self.set_visual(screen.get_rgba_visual())

        events = Gtk.EventBox()
        events.connect("enter-notify-event", lambda *_: setattr(self, "hovered", True))
        events.connect("leave-notify-event", lambda *_: setattr(self, "hovered", False))
        self.add(events)
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        frame.set_border_width(10)
        frame.get_style_context().add_class("tg-frame")
        events.add(frame)

        top = Gtk.Box(spacing=6)
        if grab.text:
            status = Gtk.Label(label="✓ Text copied", xalign=0)
            status.get_style_context().add_class("tg-ok")
        elif grab.qr:
            status = Gtk.Label(label="✓ QR code copied", xalign=0)
            status.get_style_context().add_class("tg-ok")
        else:
            status = Gtk.Label(label="No text found" if not grab.error else "Could not read the text", xalign=0)
            status.get_style_context().add_class("tg-title")
        top.pack_start(status, True, True, 0)
        close = Gtk.Button(label="✕")
        close.set_relief(Gtk.ReliefStyle.NONE)
        close.connect("clicked", lambda *_: self.destroy())
        top.pack_start(close, False, False, 0)
        frame.pack_start(top, False, False, 0)

        for code in grab.qr[:2]:
            qr = Gtk.Box(spacing=6)
            qr.get_style_context().add_class("tg-qr")
            label = Gtk.Label(label=f"QR: {code}", xalign=0)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_max_width_chars(34)
            qr.pack_start(label, True, True, 0)
            if re.match(r"^(https?://|www\.)", code, re.I):
                open_button = Gtk.Button(label="Open")
                open_button.connect("clicked", lambda _b, c=code: self.app.open_link(c))
                qr.pack_start(open_button, False, False, 0)
            frame.pack_start(qr, False, False, 0)

        if grab.text:
            # the first lines, fading out at the bottom
            preview_lines = grab.text.splitlines()[:4]
            overlay = Gtk.Overlay()
            label = Gtk.Label(label="\n".join(preview_lines), xalign=0, yalign=0)
            label.get_style_context().add_class("tg-preview")
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_max_width_chars(44)
            label.set_size_request(self.WIDTH - 22, -1)
            overlay.add(label)
            if len(grab.text.splitlines()) > 3:
                fade = Gtk.DrawingArea()
                fade.set_valign(Gtk.Align.END)
                fade.set_size_request(-1, 26)
                fade.connect("draw", self._draw_fade)
                overlay.add_overlay(fade)
                overlay.set_overlay_pass_through(fade, True)
            click = Gtk.EventBox()
            click.add(overlay)
            click.set_tooltip_text("Click to see all of it")
            click.connect("button-press-event", lambda *_: self._show_all())
            frame.pack_start(click, False, False, 0)
            lines = len(grab.text.splitlines())
            info = Gtk.Label(label=f"{lines} line{'s' if lines != 1 else ''} · {len(grab.text)} characters",
                             xalign=0)
            info.get_style_context().add_class("tg-dim")
            frame.pack_start(info, False, False, 0)
        elif grab.error:
            err = Gtk.Label(label=grab.error, xalign=0)
            err.set_line_wrap(True)
            err.get_style_context().add_class("tg-dim")
            frame.pack_start(err, False, False, 0)

        buttons = Gtk.Box(spacing=4)
        if grab.text:
            all_button = Gtk.Button(label="Show all")
            all_button.connect("clicked", lambda *_: self._show_all())
            buttons.pack_start(all_button, False, False, 0)
            if os.path.exists(QUICK_NOTES):
                note_button = Gtk.Button(label="To Quick Notes")
                note_button.connect("clicked", lambda *_: (self.app.to_quick_notes(grab.text), self.destroy()))
                buttons.pack_start(note_button, False, False, 0)
        again = Gtk.Button(label="Grab again")
        again.connect("clicked", lambda *_: (self.destroy(), self.app.start_grab()))
        buttons.pack_start(again, False, False, 0)
        frame.pack_start(buttons, False, False, 0)

        self.set_default_size(self.WIDTH, -1)
        self._place()
        GLib.timeout_add_seconds(POPUP_SECONDS, self._auto_close)

    def _place(self):
        display = Gdk.Display.get_default()
        _screen, px, py = display.get_default_seat().get_pointer().get_position()
        area = display.get_monitor_at_point(px, py).get_workarea()
        self.move(area.x + area.width - self.WIDTH - 16, area.y + area.height - 260)

    def _draw_fade(self, widget, cr):
        h = widget.get_allocated_height()
        bg = self.get_style_context().lookup_color("theme_bg_color")
        color = bg[1] if bg[0] else Gdk.RGBA(1, 1, 1, 1)
        gradient = cairo.LinearGradient(0, 0, 0, h)
        gradient.add_color_stop_rgba(0, color.red, color.green, color.blue, 0)
        gradient.add_color_stop_rgba(1, color.red, color.green, color.blue, 1)
        cr.set_source(gradient)
        cr.paint()
        return False

    def _auto_close(self):
        if self.hovered:
            return True           # wait while the mouse is on it
        self.destroy()
        return False

    def _show_all(self):
        self.destroy()
        self.app.show_result(self.grab)


# --------------------------------------------------------------------------
# the full result window
# --------------------------------------------------------------------------

class ResultWindow(Gtk.Window):
    def __init__(self, app):
        super().__init__(title="Text Grab")
        self.app = app
        self.grab = None
        self.set_default_size(560, 520)
        self.set_keep_above(True)
        self.set_icon_name("edit-select-all")
        self.connect("delete-event", lambda *_: self.hide() or True)
        self.connect("key-press-event", lambda _w, e: (self.hide() or True) if e.keyval == Gdk.KEY_Escape else False)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(10)
        self.add(box)
        self.image = Gtk.Image()
        self.image.set_halign(Gtk.Align.START)
        box.pack_start(self.image, False, False, 0)
        self.qr_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.pack_start(self.qr_box, False, False, 0)
        scroller = Gtk.ScrolledWindow()
        scroller.set_shadow_type(Gtk.ShadowType.IN)
        self.view = Gtk.TextView()
        self.view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.view.set_monospace(True)
        self.view.set_left_margin(8)
        self.view.set_right_margin(8)
        self.view.set_top_margin(6)
        scroller.add(self.view)
        box.pack_start(scroller, True, True, 0)

        row = Gtk.Box(spacing=6)
        self.layout_combo = Gtk.ComboBoxText()
        self.layout_combo.append("reading", "Reading order (text, columns)")
        self.layout_combo.append("table", "Keep rows (tables, lists)")
        self.layout_combo.set_tooltip_text("Read the same picture again in another way")
        self.layout_combo.connect("changed", self._relayout)
        row.pack_start(self.layout_combo, False, False, 0)
        self.status = Gtk.Label(xalign=0)
        self.status.get_style_context().add_class("tg-dim")
        row.pack_start(self.status, True, True, 0)
        copy = Gtk.Button(label="Copy")
        copy.set_tooltip_text("Copy the text (including your edits)")
        copy.connect("clicked", lambda *_: self._copy())
        row.pack_start(copy, False, False, 0)
        self.note_button = Gtk.Button(label="To Quick Notes")
        self.note_button.connect("clicked", lambda *_: self.app.to_quick_notes(self._text()))
        row.pack_start(self.note_button, False, False, 0)
        again = Gtk.Button(label="Grab again")
        again.connect("clicked", lambda *_: (self.hide(), self.app.start_grab()))
        row.pack_start(again, False, False, 0)
        box.pack_start(row, False, False, 0)

    def _text(self):
        buf = self.view.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)

    def _copy(self):
        self.app.copy(self._text())
        self.status.set_text("Copied")

    def show_grab(self, grab):
        self.grab = grab
        pb = grab.pixbuf
        if pb.get_height() > 140 or pb.get_width() > 520:
            factor = min(140 / pb.get_height(), 520 / pb.get_width())
            pb = pb.scale_simple(max(1, int(pb.get_width() * factor)), max(1, int(pb.get_height() * factor)),
                                 GdkPixbuf.InterpType.BILINEAR)
        self.image.set_from_pixbuf(pb)
        for child in self.qr_box.get_children():
            self.qr_box.remove(child)
        for code in grab.qr:
            line = Gtk.Box(spacing=6)
            line.get_style_context().add_class("tg-qr")
            label = Gtk.Label(label=f"QR: {code}", xalign=0)
            label.set_selectable(True)
            label.set_line_wrap(True)
            line.pack_start(label, True, True, 0)
            copy = Gtk.Button(label="Copy")
            copy.connect("clicked", lambda _b, c=code: self.app.copy(c))
            line.pack_start(copy, False, False, 0)
            if re.match(r"^(https?://|www\.)", code, re.I):
                open_button = Gtk.Button(label="Open")
                open_button.connect("clicked", lambda _b, c=code: self.app.open_link(c))
                line.pack_start(open_button, False, False, 0)
            self.qr_box.pack_start(line, False, False, 0)
        self.qr_box.show_all()
        self.view.get_buffer().set_text(grab.text or "")
        self.layout_combo.handler_block_by_func(self._relayout)
        self.layout_combo.set_active_id(grab.layout)
        self.layout_combo.handler_unblock_by_func(self._relayout)
        self.note_button.set_visible(os.path.exists(QUICK_NOTES))
        self.status.set_text(grab.error or "")
        self.show_all()
        self.note_button.set_visible(os.path.exists(QUICK_NOTES))
        self.present()

    def _relayout(self, combo):
        if not self.grab:
            return
        layout = combo.get_active_id()
        self.status.set_text("reading again…")
        grab = self.grab

        def work():
            grab.read(layout)
            GLib.idle_add(lambda: (self.view.get_buffer().set_text(grab.text or ""),
                                   self.app.copy(grab.text) if grab.text else None,
                                   self.status.set_text("copied")) and False)
        threading.Thread(target=work, daemon=True).start()


# --------------------------------------------------------------------------
# application
# --------------------------------------------------------------------------

class TextGrabApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.last = None
        self.busy = False
        self.result_window = None
        self.hotkey = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.hold()
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.hotkey = GlobalHotkey(self.start_grab)
        wanted = setting("shortcut")
        if wanted and wanted.lower() in ("none", "off"):
            active = None
        else:
            active = self.hotkey.start([wanted] if wanted else DEFAULT_SHORTCUTS)
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(ACTIVE_SHORTCUT_FILE, "w", encoding="utf-8") as f:
                f.write(pretty_accel(active) if active else "none")
        except OSError:
            pass

    def do_command_line(self, command_line):
        args = command_line.get_arguments()[1:]
        cwd = command_line.get_cwd() or os.getcwd()
        if "--quit" in args:
            self.quit()
        elif "--grab" in args:
            self.start_grab()
        elif "--last" in args:
            if self.last:
                self.show_result(self.last)
        elif "--file" in args:
            i = args.index("--file")
            if i + 1 < len(args):
                self.grab_file(os.path.join(cwd, args[i + 1]))
        return 0

    # ---------------------------------------------------------------- grabbing
    def start_grab(self):
        if self.busy:
            return
        self.busy = True
        # let popups / menus close before the picture is taken
        GLib.timeout_add(150, self._take_screenshot)

    def _take_screenshot(self):
        root = Gdk.get_default_root_window()
        shot = Gdk.pixbuf_get_from_window(root, 0, 0, root.get_width(), root.get_height())
        if shot is None:
            self.busy = False
            return False
        selector = Selector(shot, self._selected)
        selector.show_all()
        return False

    def _selected(self, crop, _rect):
        if crop is None:
            self.busy = False
            return
        self.process(crop)

    def grab_file(self, path):
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
        except GLib.Error as e:
            self.show_message(f"Could not open the image: {e.message}")
            return
        pixbuf = pixbuf.apply_embedded_orientation() or pixbuf
        self.busy = True
        self.process(pixbuf)

    def process(self, pixbuf):
        grab = Grab(pixbuf)

        def done():
            self.busy = False
            self.last = grab
            if grab.text:
                self.copy(grab.text)
            elif grab.qr:
                self.copy(grab.qr[0])
            popup = Popup(self, grab)
            popup.show_all()
            return False

        threading.Thread(target=lambda: (grab.read(), GLib.idle_add(done)), daemon=True).start()

    # ---------------------------------------------------------------- actions
    def copy(self, text):
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(text, -1)
        clipboard.store()

    def open_link(self, link):
        if not re.match(r"^https?://", link, re.I):
            link = "https://" + link
        try:
            Gio.AppInfo.launch_default_for_uri(link, None)
        except GLib.Error as e:
            self.show_message(f"Could not open the link: {e.message}")

    def to_quick_notes(self, text):
        os.makedirs(CACHE_DIR, exist_ok=True)
        path = os.path.join(CACHE_DIR, f"to-note-{int(time.time() * 1000)}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        try:
            subprocess.Popen([QUICK_NOTES, f"--add-file={path}"])
        except OSError as e:
            self.show_message(f"Could not reach Quick Notes: {e}")

    def show_result(self, grab):
        if self.result_window is None:
            self.result_window = ResultWindow(self)
        self.result_window.show_grab(grab)

    def show_message(self, text):
        dialog = Gtk.MessageDialog(message_type=Gtk.MessageType.INFO, buttons=Gtk.ButtonsType.OK, text=text)
        dialog.set_keep_above(True)
        dialog.run()
        dialog.destroy()


def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        return 0
    if "--shortcut" in sys.argv:
        try:
            with open(ACTIVE_SHORTCUT_FILE, encoding="utf-8") as f:
                print(f.read().strip())
        except OSError:
            print("none (is Text Grab running?)")
        return 0
    if not shutil.which("tesseract"):
        print("text-grab: Tesseract is not installed (sudo apt install tesseract-ocr)", file=sys.stderr)
    return TextGrabApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
