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
window.tg-window.transparent { background-color: transparent; }
.shelf {
    background-color: @theme_bg_color;
    border: 2px solid alpha(@theme_fg_color, 0.18);
    border-radius: 10px;
}
.shelf-header { padding: 4px 4px 2px 10px; }
.shelf-title { font-weight: bold; }
.shelf-footer { padding: 2px 6px 4px 6px; }
.shelf-status { opacity: 0.7; font-size: small; }
.shelf-status.tg-ok { opacity: 1; color: #2e9d57; }
.tg-body { padding: 2px 10px 4px 10px; }
.tg-qr {
    background-color: alpha(@theme_selected_bg_color, 0.15);
    border-radius: 6px;
    padding: 0 0 0 8px;
}
.tg-text { border-radius: 6px; background-color: alpha(@theme_fg_color, 0.05); }
.tg-text textview, .tg-text textview text { background-color: transparent; }
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
# shared look (the same as Drop Shelf)
# --------------------------------------------------------------------------

def icon_button(icon_names, fallback_label, tooltip):
    button = Gtk.Button()
    theme = Gtk.IconTheme.get_default()
    for name in icon_names:
        if theme.has_icon(name):
            button.set_image(Gtk.Image.new_from_icon_name(name, Gtk.IconSize.MENU))
            break
    else:
        button.set_label(fallback_label)
    button.set_relief(Gtk.ReliefStyle.NONE)
    button.set_tooltip_text(tooltip)
    button.set_can_focus(False)
    return button


ICON_NOTE = ["accessories-text-editor-symbolic", "text-editor-symbolic", "document-new-symbolic"]
ICON_GRAB = ["zoom-select-symbolic", "edit-select-all-symbolic", "view-refresh-symbolic"]
ICON_COPY = ["edit-copy-symbolic", "edit-copy"]
ICON_CLOSE = ["window-close-symbolic", "window-close"]
ICON_OPEN = ["web-browser-symbolic", "external-link-symbolic", "document-open-symbolic"]


class ShelfWindow(Gtk.Window):
    """An undecorated window with the Drop Shelf frame: bold title and flat icons on
    top (drag the top to move it), content, and a small footer."""

    def __init__(self, title):
        super().__init__(title=title)
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_skip_pager_hint(True)
        self.set_icon_name("edit-select-all")
        self.get_style_context().add_class("tg-window")
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None and screen.is_composited():
            self.set_visual(visual)
            self.get_style_context().add_class("transparent")

        self.frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.frame.get_style_context().add_class("shelf")
        self.add(self.frame)

        header_events = Gtk.EventBox()
        header_events.connect("button-press-event", self._on_header_press)
        self.header = Gtk.Box(spacing=2)
        self.header.get_style_context().add_class("shelf-header")
        header_events.add(self.header)
        self.frame.pack_start(header_events, False, False, 0)
        title_label = Gtk.Label(label=title, xalign=0)
        title_label.get_style_context().add_class("shelf-title")
        self.header.pack_start(title_label, True, True, 0)

        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.body.get_style_context().add_class("tg-body")
        self.frame.pack_start(self.body, True, True, 0)

        self.footer = Gtk.Box(spacing=4)
        self.footer.get_style_context().add_class("shelf-footer")
        self.frame.pack_start(self.footer, False, False, 0)
        self.status = Gtk.Label(xalign=0)
        self.status.get_style_context().add_class("shelf-status")
        self.status.set_ellipsize(Pango.EllipsizeMode.END)

    def add_header_button(self, button):
        self.header.pack_start(button, False, False, 0)
        return button

    def _on_header_press(self, _widget, event):
        if event.button == 1:
            self.begin_move_drag(event.button, int(event.x_root), int(event.y_root), event.time)
        return False


def qr_row(app, code, with_copy=False):
    row = Gtk.Box(spacing=2)
    row.get_style_context().add_class("tg-qr")
    label = Gtk.Label(xalign=0)
    label.set_markup(f"<small><b>QR</b></small>  {GLib.markup_escape_text(code)}")
    label.set_ellipsize(Pango.EllipsizeMode.END)
    label.set_tooltip_text(code)
    row.pack_start(label, True, True, 0)
    if with_copy:
        copy = icon_button(ICON_COPY, "Copy", "Copy")
        copy.connect("clicked", lambda *_: app.copy(code))
        row.pack_start(copy, False, False, 0)
    if re.match(r"^(https?://|www\.)", code, re.I):
        open_button = icon_button(ICON_OPEN, "Open", "Open in the browser")
        open_button.connect("clicked", lambda *_: app.open_link(code))
        row.pack_start(open_button, False, False, 0)
    return row


# --------------------------------------------------------------------------
# the small popup: first lines, fading out; click for everything
# --------------------------------------------------------------------------

class FadingText(Gtk.DrawingArea):
    """The start of the text. The lower lines dissolve into the background, the
    same way the queue does in Now Playing."""

    MAX_HEIGHT = 124
    FADE = 70

    def __init__(self, text, width, monospace):
        super().__init__()
        lines = re.sub(r"\n\s*\n+", "\n\n", text.strip()).splitlines()[:14]
        self.layout = self.create_pango_layout("\n".join(lines))
        if monospace:
            self.layout.set_font_description(Pango.FontDescription("Monospace 9"))
            self.layout.set_ellipsize(Pango.EllipsizeMode.END)   # keep table rows on one line
        else:
            self.layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        self.layout.set_width(width * Pango.SCALE)
        text_height = self.layout.get_pixel_size()[1]
        self.fades = text_height > self.MAX_HEIGHT
        self.set_size_request(width, min(text_height, self.MAX_HEIGHT))
        self.connect("draw", self._draw)

    def _draw(self, widget, cr):
        ctx = widget.get_style_context()
        fg = ctx.get_color(ctx.get_state())
        cr.set_source_rgba(fg.red, fg.green, fg.blue, fg.alpha)
        PangoCairo.show_layout(cr, self.layout)
        if self.fades:
            found, bg = ctx.lookup_color("theme_bg_color")
            rgb = (bg.red, bg.green, bg.blue) if found else (1, 1, 1)
            h = widget.get_allocated_height()
            top = h - self.FADE
            gradient = cairo.LinearGradient(0, top, 0, h)
            gradient.add_color_stop_rgba(0, *rgb, 0)
            gradient.add_color_stop_rgba(0.75, *rgb, 0.92)
            gradient.add_color_stop_rgba(1, *rgb, 1)
            cr.rectangle(0, top, widget.get_allocated_width(), self.FADE)
            cr.set_source(gradient)
            cr.fill()
        return False


class Popup(ShelfWindow):
    WIDTH = 360

    def __init__(self, app, grab):
        super().__init__("Text Grab")
        self.app = app
        self.grab = grab
        self.hovered = False
        self.closing = False
        self.set_skip_taskbar_hint(True)
        self.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)
        self.set_accept_focus(False)
        self.set_default_size(self.WIDTH, -1)
        self.connect("enter-notify-event", lambda *_: self._hover(True))
        self.connect("leave-notify-event", self._on_leave)

        if grab.text and os.path.exists(QUICK_NOTES):
            note = self.add_header_button(icon_button(ICON_NOTE, "Note", "Put the text on a Quick Notes post-it"))
            note.connect("clicked", lambda *_: (self.app.to_quick_notes(grab.text), self.destroy()))
        again = self.add_header_button(icon_button(ICON_GRAB, "Again", "Grab again"))
        again.connect("clicked", lambda *_: (self.destroy(), self.app.start_grab()))
        close = self.add_header_button(icon_button(ICON_CLOSE, "✕", "Close"))
        close.connect("clicked", lambda *_: self.destroy())

        for code in grab.qr[:2]:
            self.body.pack_start(qr_row(app, code), False, False, 0)

        if grab.text:
            text = FadingText(grab.text, self.WIDTH - 24, grab.layout == "table")
            click = Gtk.EventBox()
            click.add(text)
            click.set_tooltip_text("Click to see all of it")
            click.connect("button-press-event", lambda *_: self._show_all())
            click.connect("realize", lambda w: w.get_window().set_cursor(
                Gdk.Cursor.new_from_name(w.get_display(), "pointer")))
            self.body.pack_start(click, False, False, 0)
        elif not grab.qr:
            empty = Gtk.Label(xalign=0)
            empty.set_line_wrap(True)
            empty.set_max_width_chars(40)
            empty.set_markup("<b>No text found</b>\n<small>Try a bigger box, or zoom in first.</small>"
                             if not grab.error else
                             f"<b>Could not read the text</b>\n<small>{GLib.markup_escape_text(grab.error)}</small>")
            self.body.pack_start(empty, False, False, 0)

        if grab.text:
            show_all = Gtk.Button(label="Show all")
            show_all.set_can_focus(False)
            show_all.connect("clicked", lambda *_: self._show_all())
            self.footer.pack_start(show_all, False, False, 0)
            lines = len(grab.text.splitlines())
            self.status.set_text(f"✓ Copied · {lines} line{'s' if lines != 1 else ''} · "
                                 f"{len(grab.text)} characters")
        elif grab.qr:
            self.status.set_text("✓ QR code copied")
        if self.status.get_text():
            self.status.get_style_context().add_class("tg-ok")
        self.footer.pack_start(self.status, True, True, 0)

        self._place()
        Gtk.Widget.set_opacity(self, 0)
        self._fade_to(1)
        GLib.timeout_add_seconds(POPUP_SECONDS, self._auto_close)

    def _place(self):
        display = Gdk.Display.get_default()
        _screen, px, py = display.get_default_seat().get_pointer().get_position()
        area = display.get_monitor_at_point(px, py).get_workarea()
        self.frame.show_all()
        _minimum, natural = self.frame.get_preferred_size()
        self.move(area.x + area.width - self.WIDTH - 16, area.y + area.height - natural.height - 16)

    def _hover(self, on):
        self.hovered = on
        if on and self.closing:            # mouse came back while fading out: stay
            self.closing = False
            self._fade_to(1)

    def _on_leave(self, _w, event):
        if event.detail != Gdk.NotifyType.INFERIOR:    # not just moving onto a button
            self._hover(False)

    def _fade_to(self, target, then=None):
        step = 0.08 if target > Gtk.Widget.get_opacity(self) else -0.05

        def tick():
            if then is not None and not self.closing:
                return False                       # fade out was cancelled
            value = min(1, max(0, Gtk.Widget.get_opacity(self) + step))
            Gtk.Widget.set_opacity(self, value)
            if (step > 0 and value >= target) or (step < 0 and value <= target):
                if then:
                    then()
                return False
            return True
        GLib.timeout_add(16, tick)

    def _auto_close(self):
        if self.hovered:
            return True           # wait while the mouse is on it
        self.closing = True
        self._fade_to(0, self.destroy)
        return False

    def _show_all(self):
        self.destroy()
        self.app.show_result(self.grab)


# --------------------------------------------------------------------------
# the full result window
# --------------------------------------------------------------------------

class ResultWindow(ShelfWindow):
    LAYOUTS = [("reading", "Reading order (text, columns)"), ("table", "Keep rows (tables, lists)")]

    def __init__(self, app):
        super().__init__("Text Grab")
        self.app = app
        self.grab = None
        self.set_default_size(540, 480)
        self.connect("delete-event", lambda *_: self.hide() or True)
        self.connect("key-press-event", lambda _w, e: (self.hide() or True) if e.keyval == Gdk.KEY_Escape else False)

        copy = self.add_header_button(icon_button(ICON_COPY, "Copy", "Copy the text (with your edits)"))
        copy.connect("clicked", lambda *_: self._copy())
        self.note_button = self.add_header_button(
            icon_button(ICON_NOTE, "Note", "Put the text on a Quick Notes post-it"))
        self.note_button.connect("clicked", lambda *_: (self.app.to_quick_notes(self._text()),
                                                        self._say("✓ Sent to Quick Notes")))
        again = self.add_header_button(icon_button(ICON_GRAB, "Again", "Grab again"))
        again.connect("clicked", lambda *_: (self.hide(), self.app.start_grab()))
        menu_button = Gtk.MenuButton()
        menu_button.set_relief(Gtk.ReliefStyle.NONE)
        menu_button.set_can_focus(False)
        menu_button.set_tooltip_text("How to read it")
        menu_button.set_image(Gtk.Image.new_from_icon_name("open-menu-symbolic", Gtk.IconSize.MENU))
        menu_button.set_popup(self._build_menu())
        self.add_header_button(menu_button)
        close = self.add_header_button(icon_button(ICON_CLOSE, "✕", "Close (Esc)"))
        close.connect("clicked", lambda *_: self.hide())

        self.image = Gtk.Image()
        self.image.set_halign(Gtk.Align.START)
        self.body.pack_start(self.image, False, False, 0)
        self.qr_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.body.pack_start(self.qr_box, False, False, 0)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.get_style_context().add_class("tg-text")
        self.view = Gtk.TextView()
        self.view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.view.set_left_margin(8)
        self.view.set_right_margin(8)
        self.view.set_top_margin(6)
        self.view.set_bottom_margin(6)
        scroller.add(self.view)
        self.body.pack_start(scroller, True, True, 0)

        self.footer.pack_start(self.status, True, True, 0)
        grip = Gtk.EventBox()
        grip.add(Gtk.Label(label="◢"))
        grip.set_tooltip_text("Resize")
        grip.connect("button-press-event", self._on_grip_press)
        grip.connect("realize", lambda w: w.get_window().set_cursor(
            Gdk.Cursor.new_from_name(w.get_display(), "se-resize")))
        self.footer.pack_end(grip, False, False, 0)

    def _build_menu(self):
        menu = Gtk.Menu()
        self.layout_items = {}
        group = None
        for key, label in self.LAYOUTS:
            item = Gtk.RadioMenuItem.new_with_label_from_widget(group, label)
            group = item
            item.connect("toggled", self._relayout, key)
            menu.append(item)
            self.layout_items[key] = item
        menu.append(Gtk.SeparatorMenuItem())
        picture = Gtk.MenuItem(label="Open the picture")
        picture.connect("activate", lambda *_: self.grab and Gio.AppInfo.launch_default_for_uri(
            GLib.filename_to_uri(self.grab.original), None))
        menu.append(picture)
        menu.show_all()
        return menu

    def _on_grip_press(self, _widget, event):
        if event.button == 1:
            self.begin_resize_drag(Gdk.WindowEdge.SOUTH_EAST, event.button,
                                   int(event.x_root), int(event.y_root), event.time)
        return True

    def _text(self):
        buf = self.view.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)

    def _say(self, text):
        self.status.set_text(text)

    def _copy(self):
        self.app.copy(self._text())
        self._say("✓ Copied")

    def _describe(self):
        grab = self.grab
        if grab.error:
            return grab.error
        if not grab.text:
            return "No text found" if not grab.qr else "✓ QR code copied"
        lines = len(grab.text.splitlines())
        return f"{lines} line{'s' if lines != 1 else ''} · {len(grab.text)} characters · you can edit it"

    def show_grab(self, grab):
        self.grab = grab
        pb = grab.pixbuf
        if pb.get_height() > 120 or pb.get_width() > 500:
            factor = min(120 / pb.get_height(), 500 / pb.get_width())
            pb = pb.scale_simple(max(1, int(pb.get_width() * factor)), max(1, int(pb.get_height() * factor)),
                                 GdkPixbuf.InterpType.BILINEAR)
        self.image.set_from_pixbuf(pb)
        for child in self.qr_box.get_children():
            self.qr_box.remove(child)
        for code in grab.qr:
            self.qr_box.pack_start(qr_row(self.app, code, with_copy=True), False, False, 0)
        self.view.set_monospace(grab.layout == "table")
        self.view.get_buffer().set_text(grab.text or "")
        item = self.layout_items[grab.layout]
        item.handler_block_by_func(self._relayout)
        item.set_active(True)
        item.handler_unblock_by_func(self._relayout)
        self._say(self._describe())
        self.show_all()
        self.qr_box.set_visible(bool(grab.qr))
        self.note_button.set_visible(os.path.exists(QUICK_NOTES))
        self.present()

    def _relayout(self, item, layout):
        if not item.get_active() or not self.grab:
            return
        self._say("reading again…")
        grab = self.grab

        def done():
            self.view.set_monospace(layout == "table")
            self.view.get_buffer().set_text(grab.text or "")
            if grab.text:
                self.app.copy(grab.text)
            self._say(("✓ Copied · " if grab.text else "") + self._describe())
            return False

        threading.Thread(target=lambda: (grab.read(layout), GLib.idle_add(done)), daemon=True).start()


# --------------------------------------------------------------------------
# application
# --------------------------------------------------------------------------

class TextGrabApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.last = None
        self.busy = False
        self.result_window = None
        self.popup = None
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
            self.close_popup()
            self.popup = Popup(self, grab)
            self.popup.connect("destroy", lambda w: setattr(self, "popup", None) if self.popup is w else None)
            self.popup.show_all()
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

    def close_popup(self):
        if self.popup is not None:
            self.popup.destroy()

    def show_result(self, grab):
        self.close_popup()
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
