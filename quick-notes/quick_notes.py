#!/usr/bin/env python3
"""Quick Notes - post-it notes that stay on top of your screen.

Usage:
    quick-notes             open a new note (restores saved notes on first start)
    quick-notes --new       open a new note at the mouse pointer
    quick-notes --restore   only bring back saved notes (used for autostart)
    quick-notes --show      bring all notes to the front
    quick-notes --quit      close the app (notes are kept)
    quick-notes --shortcut  print the keyboard shortcut in use
    quick-notes --search    open the note search (Ctrl+Alt+F)

Inside a note:
    Enter finishes the note (double-click to edit again), Shift+Enter new line
    Ctrl+B bold, Ctrl+I italic, Ctrl+T checkbox, Ctrl+N new note

The shortcut can be changed in ~/.config/quick-notes/settings.ini:
    [quick-notes]
    shortcut=<Super>n
Use shortcut=none to turn the built-in shortcut off (e.g. when you set one up in
the Keyboard settings with the command: quick-notes --new).
"""

import base64
import ctypes
import ctypes.util
import datetime
import json
import os
import sys
import time
import unicodedata
import uuid

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

try:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
except ImportError:          # locked notes need python3-cryptography
    AESGCM = Scrypt = InvalidTag = None

try:
    gi.require_version("GdkX11", "3.0")
    from gi.repository import GdkX11  # noqa: E402
except (ValueError, ImportError):
    GdkX11 = None

APP_ID = "io.github.quicknotes.QuickNotes"
DATA_FILE = os.path.join(GLib.get_user_data_dir(), "quick-notes", "notes.json")
SETTINGS_FILE = os.path.join(GLib.get_user_config_dir(), "quick-notes", "settings.ini")
ACTIVE_SHORTCUT_FILE = os.path.join(GLib.get_user_config_dir(), "quick-notes", "active-shortcut")
# Tried in order; the first one no other program is using wins.
DEFAULT_SHORTCUTS = ["<Primary><Alt>n", "<Super>n", "<Primary><Alt>j", "<Primary><Super>n"]
DEFAULT_SEARCH_SHORTCUTS = ["<Primary><Alt>f", "<Super>f", "<Primary><Alt>k"]
ACTIVE_SEARCH_FILE = os.path.join(GLib.get_user_config_dir(), "quick-notes", "active-search-shortcut")
LOCK_MINUTES = 5          # locked notes lock themselves again after this long without use
DOUBLE_CLICK = getattr(Gdk.EventType, "DOUBLE_BUTTON_PRESS", None) or getattr(Gdk.EventType, "_2BUTTON_PRESS")
LOCK_SCHEMA = "org.cinnamon.desktop.screensaver"
LOCK_KEY = "default-message"

BOX_OPEN, BOX_DONE = "☐", "☑"
BOXES = (BOX_OPEN, BOX_DONE)
PREFIXES = (BOX_OPEN + " ", BOX_DONE + " ")

# name: (body, header)
COLORS = {
    "yellow": ("#fff8a6", "#fcec74"),
    "pink":   ("#ffd6e0", "#ffb8ca"),
    "green":  ("#d8f5c8", "#bdeba5"),
    "blue":   ("#d3eaff", "#b3d9ff"),
    "orange": ("#ffdfb8", "#ffc98c"),
    "purple": ("#e8dcff", "#d4c2ff"),
    "white":  ("#fafafa", "#e6e6e6"),
}
DEFAULT_COLOR = "yellow"
DEFAULT_SIZE = (250, 220)


def build_css():
    css = """
window.note { background-color: transparent; }
.note-frame { border-radius: 6px; border: 1px solid rgba(0,0,0,0.18); }
.note-header { border-radius: 6px 6px 0 0; padding: 1px 2px 1px 4px; }
.note-footer { padding: 0 2px 2px 4px; }
.note textview, .note textview text {
    background-color: transparent; color: #262626; caret-color: #262626; font-size: 11pt;
}
.note textview text selection { background-color: rgba(0,0,0,0.18); color: #000000; }
.note scrolledwindow, .note scrolledwindow viewport { background-color: transparent; }
.note button {
    background: transparent; background-image: none; border: none; box-shadow: none;
    text-shadow: none; -gtk-icon-shadow: none; color: #3a3a3a;
    min-height: 18px; min-width: 18px; padding: 1px 5px; border-radius: 4px;
}
.note button:hover { background-color: rgba(0,0,0,0.09); }
.note button:checked { background-color: rgba(0,0,0,0.17); }
.note .grip { color: rgba(0,0,0,0.35); padding: 0 3px; }
.swatch { min-width: 16px; min-height: 16px; border-radius: 8px; border: 1px solid rgba(0,0,0,0.3); }
.note .locked-title { font-weight: bold; color: #3a3a3a; }
.note .locked-dots { color: rgba(0,0,0,0.35); font-size: 16pt; }
.search-meta { font-size: small; opacity: 0.7; }
.search-chip { background-color: #6b4fbb; color: #ffffff; border-radius: 4px; padding: 0 5px;
               font-size: x-small; font-weight: bold; }
.search-danger { color: #d83b3b; font-weight: bold; }
"""
    for name, (body, header) in COLORS.items():
        css += f"""
.note-{name} .note-frame {{ background-color: {body}; }}
.note-{name} .note-header {{ background-color: {header}; }}
.swatch-{name} {{ background-color: {body}; }}
.stripe-{name} {{ background-color: {header}; min-width: 6px; border-radius: 3px; }}
"""
    return css.encode()


def server_time(gdk_window):
    if GdkX11 is not None and isinstance(gdk_window, GdkX11.X11Window):
        try:
            return GdkX11.x11_get_server_time(gdk_window)
        except Exception:
            pass
    return Gtk.get_current_event_time()


def flat_button(label=None, icon_names=(), tooltip="", toggle=False, markup=False):
    button = Gtk.ToggleButton() if toggle else Gtk.Button()
    theme = Gtk.IconTheme.get_default()
    icon = next((n for n in icon_names if theme.has_icon(n)), None)
    if icon:
        button.set_image(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.MENU))
    else:
        text = Gtk.Label()
        if markup:
            text.set_markup(label)
        else:
            text.set_text(label)
        button.add(text)
    button.set_relief(Gtk.ReliefStyle.NONE)
    button.set_can_focus(False)
    button.set_tooltip_text(tooltip)
    return button


# --------------------------------------------------------------------------
# global keyboard shortcut
# --------------------------------------------------------------------------

class GlobalHotkey:
    """Grabs one key combination directly on the X server.

    This works no matter what the desktop's shortcut settings say. If another
    program already owns a combination, the grab fails and the next one is tried.
    """

    KEY_PRESS = 2
    GRAB_MODE_ASYNC = 1
    LOCK_MASK = 1 << 1       # Caps Lock
    MOD2_MASK = 1 << 4       # Num Lock
    ERROR_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)

    def __init__(self, callback):
        self.callback = callback
        self.x = None
        self.display = None
        self.active = None
        self._grabbed = None
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
        x.XFlush.argtypes = [vp]
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

    def _x_modifiers(self, gdk_mods):
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
        return [xmods | extra for extra in (0, self.LOCK_MASK, self.MOD2_MASK,
                                            self.LOCK_MASK | self.MOD2_MASK)]

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
        if self._failed:
            return False
        self._grabbed = (keycode, xmods)
        return True

    def start(self, candidates):
        """Grab the first free combination; returns it, or None."""
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
                if now - self._last_press > 0.3:     # ignore key auto-repeat
                    self._last_press = now
                    GLib.idle_add(lambda: self.callback() and False)
                else:
                    self._last_press = now
        return True


def pretty_accel(accel):
    keyval, mods = Gtk.accelerator_parse(accel or "")
    return Gtk.accelerator_get_label(keyval, mods) if keyval else (accel or "")


# --------------------------------------------------------------------------
# locked notes: AES-256-GCM, key from the master password with scrypt
# --------------------------------------------------------------------------

def _b64(raw):
    return base64.b64encode(raw).decode()


def _unb64(text):
    return base64.b64decode(text.encode())


class Vault:
    """Encrypts locked notes. Only the scrambled text is ever written to disk."""

    CHECK = b"quick-notes vault v1"

    def __init__(self, data):
        self.data = dict(data) if data else None
        self.key = None
        self.until = 0.0

    @property
    def available(self):
        return AESGCM is not None

    @property
    def configured(self):
        return bool(self.data)

    @property
    def unlocked(self):
        return self.key is not None

    @staticmethod
    def _derive(password, salt):
        return Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1).derive(password.encode("utf-8"))

    def _seal(self, raw):
        nonce = os.urandom(12)
        return _b64(nonce + AESGCM(self.key).encrypt(nonce, raw, None))

    def _open(self, blob, key=None):
        raw = _unb64(blob)
        return AESGCM(key or self.key).decrypt(raw[:12], raw[12:], None)

    def create(self, password):
        salt = os.urandom(16)
        self.key = self._derive(password, salt)
        self.data = {"version": 1, "kdf": "scrypt", "salt": _b64(salt), "check": self._seal(self.CHECK)}
        self.touch()

    def unlock(self, password):
        key = self._derive(password, _unb64(self.data["salt"]))
        try:
            ok = self._open(self.data["check"], key) == self.CHECK
        except InvalidTag:
            ok = False
        if ok:
            self.key = key
            self.touch()
        return ok

    def lock(self):
        self.key = None

    def touch(self):
        self.until = time.monotonic() + LOCK_MINUTES * 60

    def encrypt(self, obj):
        return self._seal(json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def decrypt(self, blob):
        return json.loads(self._open(blob).decode("utf-8"))


def fold(text):
    """Lowercase without accents ("Cím" -> "cim"), plus a map back to the original positions."""
    out, positions = [], []
    for i, char in enumerate(text):
        plain = "".join(c for c in unicodedata.normalize("NFD", char.lower())
                        if not unicodedata.combining(c))
        for c in plain:
            out.append(c)
            positions.append(i)
    return "".join(out), positions


def runs_text(runs):
    return "".join(str(r[0]) for r in runs or [] if r)


def highlight(text, terms, limit=600):
    """Markup for a search result: matches highlighted, long notes cut around the first match."""
    folded, positions = fold(text)
    marks = [False] * len(text)
    first = None
    for term in terms:
        start = 0
        while term:
            found = folded.find(term, start)
            if found < 0:
                break
            begin, end = positions[found], positions[found + len(term) - 1]
            first = begin if first is None else min(first, begin)
            for k in range(begin, end + 1):
                marks[k] = True
            start = found + 1
    lo = 0
    if first is not None and first > limit // 2:
        lo = max(0, first - 120)
    hi = min(len(text), lo + limit)
    out = ["\u2026"] if lo else []
    esc = GLib.markup_escape_text
    k = lo
    while k < hi:
        j = k
        while j < hi and marks[j] == marks[k]:
            j += 1
        chunk = esc(text[k:j])
        out.append(f"<span background='#ffe36e' foreground='#000000'><b>{chunk}</b></span>" if marks[k] else chunk)
        k = j
    if hi < len(text):
        out.append("\u2026")
    return "".join(out)


def ask_password(parent, create=False, check=None):
    """Master password dialog. Returns the password or None."""
    dialog = Gtk.Dialog(title="Create a master password" if create else "Unlock locked notes",
                        transient_for=parent, modal=True)
    dialog.set_keep_above(True)
    dialog.set_default_size(380, -1)
    area = dialog.get_content_area()
    area.set_border_width(14)
    area.set_spacing(8)
    text = Gtk.Label(xalign=0)
    text.set_line_wrap(True)
    text.set_max_width_chars(46)
    if create:
        text.set_markup("Locked notes are encrypted with this password.\n"
                        "<b>If you forget it, locked notes cannot be recovered.</b>")
    else:
        text.set_text("Type your master password:")
    area.pack_start(text, False, False, 0)
    first = Gtk.Entry(visibility=False, activates_default=True)
    first.set_placeholder_text("Master password")
    area.pack_start(first, False, False, 0)
    second = None
    if create:
        second = Gtk.Entry(visibility=False, activates_default=True)
        second.set_placeholder_text("Type it again")
        area.pack_start(second, False, False, 0)
    error = Gtk.Label(xalign=0)
    error.get_style_context().add_class("search-danger")
    area.pack_start(error, False, False, 0)
    dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
    ok = dialog.add_button("Create" if create else "Unlock", Gtk.ResponseType.OK)
    ok.set_can_default(True)
    ok.grab_default()
    dialog.show_all()
    result = None
    while dialog.run() == Gtk.ResponseType.OK:
        password = first.get_text()
        if create and len(password) < 4:
            error.set_text("Use at least 4 characters.")
            continue
        if create and password != second.get_text():
            error.set_text("The two passwords are different.")
            continue
        if not create and check is not None and not check(password):
            error.set_text("Wrong password.")
            first.set_text("")
            continue
        result = password
        break
    dialog.destroy()
    return result


# --------------------------------------------------------------------------
# one note
# --------------------------------------------------------------------------

class Note(Gtk.Window):
    def __init__(self, app, data):
        super().__init__(application=app, title="Quick Note")
        self.app = app
        self.id = data.get("id") or uuid.uuid4().hex
        self.color = data.get("color") if data.get("color") in COLORS else DEFAULT_COLOR
        self.lock = bool(data.get("lock"))
        self.finished = bool(data.get("finished"))
        self.created = data.get("created") or time.time()
        self.secret = bool(data.get("secret"))       # locked with the master password
        self.enc = data.get("enc")                    # encrypted content of a locked note
        self.revealed = not self.secret
        self.typing_bold = False
        self.typing_italic = False
        self.internal = False     # True while we change the text ourselves

        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_icon_name("accessories-text-editor")
        self.stick()
        w, h = data.get("w") or DEFAULT_SIZE[0], data.get("h") or DEFAULT_SIZE[1]
        self.set_default_size(max(140, w), max(100, h))
        if "x" in data and "y" in data:
            self.move(data["x"], data["y"])

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None and screen.is_composited():
            self.set_visual(visual)
        self.get_style_context().add_class("note")
        self.get_style_context().add_class(f"note-{self.color}")

        self._build_ui()
        if self.secret:
            if self.app.vault.unlocked:
                self.reveal()
            else:
                self._apply_finished()
        else:
            self._load_runs(data.get("runs") or [])

        self.connect("configure-event", lambda *_: self.app.save_soon())
        self.connect("delete-event", self._on_delete_event)
        self.connect("key-press-event", self._on_key)

    # ---------------------------------------------------------------- UI
    def _build_ui(self):
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        frame.get_style_context().add_class("note-frame")
        self.add(frame)

        header_events = Gtk.EventBox()
        header_events.connect("button-press-event", self._on_header_press)
        header = Gtk.Box(spacing=0)
        header.get_style_context().add_class("note-header")
        header_events.add(header)
        frame.pack_start(header_events, False, False, 0)

        new_button = flat_button("+", ("list-add-symbolic",), "New note (Ctrl+N)")
        new_button.connect("clicked", lambda *_: self.app.new_note(near=self))
        header.pack_start(new_button, False, False, 0)

        self.color_button = flat_button("<span size='large'>●</span>", (), "Change color", markup=True)
        self.color_button.connect("clicked", lambda b: self._color_menu().popup_at_widget(
            b, Gdk.Gravity.SOUTH_WEST, Gdk.Gravity.NORTH_WEST, None))
        header.pack_start(self.color_button, False, False, 0)

        search_button = flat_button("S", ("system-search-symbolic", "edit-find-symbolic"),
                                    "Search all notes (Ctrl+Alt+F)")
        search_button.connect("clicked", lambda *_: self.app.show_search())
        header.pack_start(search_button, False, False, 0)

        header.pack_start(Gtk.Box(), True, True, 0)   # spacer - drag here to move

        self.lock_button = flat_button("LS", ("video-display-symbolic", "preferences-desktop-screensaver-symbolic"),
                                       "Show this note on the lock screen", toggle=True)
        self.lock_button.set_active(self.lock and not self.secret)
        self.lock_button.set_sensitive(not self.secret)
        self.lock_button.connect("toggled", self._on_lock_toggled)
        header.pack_start(self.lock_button, False, False, 0)

        self.secret_button = flat_button("P", ("changes-prevent-symbolic", "system-lock-screen-symbolic"),
                                         "Lock this note with your master password (encrypted)", toggle=True)
        self.secret_button.set_active(self.secret)
        self.secret_id = self.secret_button.connect("toggled", self._on_secret_toggled)
        header.pack_start(self.secret_button, False, False, 0)

        delete_button = flat_button("✕", ("window-close-symbolic",),
                                    "Archive note (find it again with Ctrl+Alt+F)")
        delete_button.connect("clicked", lambda *_: self.app.delete_note(self))
        header.pack_start(delete_button, False, False, 0)

        # Text
        self.buffer = Gtk.TextBuffer()
        self.tag_bold = self.buffer.create_tag("bold", weight=Pango.Weight.BOLD)
        self.tag_italic = self.buffer.create_tag("italic", style=Pango.Style.ITALIC)
        self.tag_done = self.buffer.create_tag("done", strikethrough=True, foreground="#777777")

        self.view = Gtk.TextView(buffer=self.buffer)
        self.view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.view.set_left_margin(10)
        self.view.set_right_margin(10)
        self.view.set_top_margin(6)
        self.view.set_bottom_margin(6)
        self.view.set_pixels_below_lines(2)
        self.view.connect("button-press-event", self._on_text_click)
        self.view.connect("key-press-event", self._on_text_key)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.add(self.view)
        self.body = Gtk.Stack()
        scroller.show_all()      # a Stack only switches to pages that are already visible
        self.body.add_named(scroller, "text")
        locked = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER)
        title = Gtk.Label(label="Locked note")
        title.get_style_context().add_class("locked-title")
        dots = Gtk.Label(label="\u2022 \u2022 \u2022 \u2022 \u2022 \u2022")
        dots.get_style_context().add_class("locked-dots")
        unlock = Gtk.Button(label="Unlock")
        unlock.set_halign(Gtk.Align.CENTER)
        unlock.set_can_focus(False)
        unlock.connect("clicked", lambda *_: self.app.unlock_vault(parent=self))
        for widget in (title, dots, unlock):
            locked.pack_start(widget, False, False, 0)
        locked.show_all()
        self.body.add_named(locked, "locked")
        frame.pack_start(self.body, True, True, 0)

        # Footer: formatting + resize grip
        footer = Gtk.Box(spacing=0)
        footer.get_style_context().add_class("note-footer")
        frame.pack_start(footer, False, False, 0)

        # Formatting buttons - hidden while the note is finished.
        self.tools = Gtk.Box(spacing=0)
        footer.pack_start(self.tools, False, False, 0)

        self.bold_button = flat_button("<b>B</b>", (), "Bold (Ctrl+B)", toggle=True, markup=True)
        self.bold_id = self.bold_button.connect("toggled", lambda *_: self.toggle_style("bold"))
        self.tools.pack_start(self.bold_button, False, False, 0)

        self.italic_button = flat_button("<i>I</i>", (), "Italic (Ctrl+I)", toggle=True, markup=True)
        self.italic_id = self.italic_button.connect("toggled", lambda *_: self.toggle_style("italic"))
        self.tools.pack_start(self.italic_button, False, False, 0)

        box_button = flat_button(BOX_DONE, (), "Checkbox (Ctrl+T)")
        box_button.connect("clicked", lambda *_: self.toggle_checkbox_lines())
        self.tools.pack_start(box_button, False, False, 0)


        grip = Gtk.EventBox()
        grip_label = Gtk.Label(label="◢")
        grip_label.get_style_context().add_class("grip")
        grip.add(grip_label)
        grip.set_tooltip_text("Resize")
        grip.connect("button-press-event", self._on_grip_press)
        grip.connect("realize", lambda w: w.get_window().set_cursor(
            Gdk.Cursor.new_from_name(w.get_display(), "se-resize")))
        footer.pack_end(grip, False, False, 0)

        # Everything except the x disappears while the note is finished.
        self.edit_widgets = [new_button, self.color_button, search_button, self.lock_button,
                             self.secret_button, self.tools, grip]
        for widget in self.edit_widgets:
            widget.show_all()
            widget.set_no_show_all(True)

        self.buffer.connect_after("insert-text", self._on_insert_text)
        self.buffer.connect("changed", self._on_changed)
        self.buffer.connect("mark-set", self._on_mark_set)
        self._apply_finished()

    def _color_menu(self):
        menu = Gtk.Menu()
        for name in COLORS:
            item = Gtk.MenuItem()
            row = Gtk.Box(spacing=8)
            swatch = Gtk.Box()
            swatch.set_size_request(16, 16)
            swatch.get_style_context().add_class("swatch")
            swatch.get_style_context().add_class(f"swatch-{name}")
            row.pack_start(swatch, False, False, 0)
            label = name.capitalize() + ("  \u2713" if name == self.color else "")
            row.pack_start(Gtk.Label(label=label, xalign=0), True, True, 0)
            item.add(row)
            item.connect("activate", lambda _i, n=name: self.set_color(n))
            menu.append(item)
        menu.show_all()
        menu.attach_to_widget(self.color_button, None)
        return menu

    # ---------------------------------------------------------------- data
    def set_color(self, name):
        ctx = self.get_style_context()
        ctx.remove_class(f"note-{self.color}")
        self.color = name
        ctx.add_class(f"note-{name}")
        self.app.last_color = name
        self.app.save_soon()

    def to_dict(self):
        x, y = self.get_position()
        w, h = self.get_size()
        data = {"id": self.id, "x": x, "y": y, "w": w, "h": h, "color": self.color,
                "lock": self.lock and not self.secret, "finished": self.finished,
                "created": self.created, "secret": self.secret}
        if self.secret:
            # Only the encrypted text is ever saved for a locked note.
            if self.revealed and self.app.vault.unlocked:
                self.enc = self.app.vault.encrypt(self._runs())
            data["enc"] = self.enc
        else:
            data["runs"] = self._runs()
        return data

    def _runs(self):
        runs = []
        it = self.buffer.get_start_iter()
        end = self.buffer.get_end_iter()
        while it.compare(end) < 0:
            nxt = it.copy()
            nxt.forward_to_tag_toggle(None)
            if nxt.compare(end) > 0 or nxt.equal(it):
                nxt = end.copy()
            text = self.buffer.get_text(it, nxt, True)
            style = (it.has_tag(self.tag_bold), it.has_tag(self.tag_italic))
            if runs and (runs[-1][1], runs[-1][2]) == style:
                runs[-1][0] += text
            else:
                runs.append([text, style[0], style[1]])
            it = nxt
        return runs

    def _load_runs(self, runs):
        self.internal = True
        for run in runs:
            try:
                text, bold, italic = run[0], bool(run[1]), bool(run[2])
            except (IndexError, TypeError):
                continue
            tags = [t for t, on in ((self.tag_bold, bold), (self.tag_italic, italic)) if on]
            self.buffer.insert_with_tags(self.buffer.get_end_iter(), text, *tags)
        self.internal = False
        self._refresh_done_tags()
        self.buffer.place_cursor(self.buffer.get_end_iter())

    def plain_text(self):
        if not self.revealed:
            return ""
        return self.buffer.get_text(self.buffer.get_start_iter(), self.buffer.get_end_iter(), False)

    # ---------------------------------------------------------------- locked notes
    def reveal(self):
        """Show a locked note's text (the vault must be unlocked)."""
        runs = self.app.vault.decrypt(self.enc) if self.enc else []
        self.internal = True
        self.buffer.set_text("")
        self.internal = False
        self._load_runs(runs)
        self.revealed = True
        self._apply_finished()

    def conceal(self):
        """Encrypt the text, wipe it from the window and show the locked page."""
        if self.revealed and self.app.vault.unlocked:
            self.enc = self.app.vault.encrypt(self._runs())
        self.internal = True
        self.buffer.set_text("")
        self.internal = False
        self.revealed = False
        self._apply_finished()

    def _on_secret_toggled(self, button):
        want = button.get_active()
        if want == self.secret:
            return
        if want and not self.app.ensure_vault(parent=self):
            self.secret_button.handler_block(self.secret_id)
            self.secret_button.set_active(False)
            self.secret_button.handler_unblock(self.secret_id)
            return
        self.secret = want
        if want:
            self.enc = self.app.vault.encrypt(self._runs())
            if self.lock:
                self.lock_button.set_active(False)     # a locked note never goes on the lock screen
        else:
            self.enc = None
        self.lock_button.set_sensitive(not want)
        self.app.save_soon()
        self.app.refresh_search()

    # ---------------------------------------------------------------- text behaviour
    def _on_insert_text(self, buffer, location, text, _length):
        if self.internal or not (self.typing_bold or self.typing_italic):
            return
        start = location.copy()
        start.backward_chars(len(text))
        if self.typing_bold:
            buffer.apply_tag(self.tag_bold, start, location)
        if self.typing_italic:
            buffer.apply_tag(self.tag_italic, start, location)

    def _on_changed(self, _buffer):
        if self.secret and self.revealed and not self.internal:
            self.app.vault.touch()      # typing keeps it unlocked
        self._refresh_done_tags()
        self.app.save_soon()
        if self.lock:
            self.app.update_lock_screen_soon()

    def _on_mark_set(self, buffer, location, mark):
        if mark != buffer.get_insert() or buffer.get_has_selection():
            return
        before = location.copy()
        if before.backward_char() and before.get_char() != "\n":
            self.typing_bold = before.has_tag(self.tag_bold)
            self.typing_italic = before.has_tag(self.tag_italic)
        self._sync_style_buttons()

    def _sync_style_buttons(self):
        self.bold_button.handler_block(self.bold_id)
        self.italic_button.handler_block(self.italic_id)
        self.bold_button.set_active(self.typing_bold)
        self.italic_button.set_active(self.typing_italic)
        self.bold_button.handler_unblock(self.bold_id)
        self.italic_button.handler_unblock(self.italic_id)

    def toggle_style(self, which):
        tag = self.tag_bold if which == "bold" else self.tag_italic
        bounds = self.buffer.get_selection_bounds()
        if bounds:
            start, end = bounds
            it = start.copy()
            all_tagged = True
            while it.compare(end) < 0:
                if not it.has_tag(tag) and it.get_char() != "\n":
                    all_tagged = False
                    break
                it.forward_char()
            if all_tagged:
                self.buffer.remove_tag(tag, start, end)
            else:
                self.buffer.apply_tag(tag, start, end)
            setattr(self, f"typing_{which}", not all_tagged)
            self.app.save_soon()
        else:
            setattr(self, f"typing_{which}", not getattr(self, f"typing_{which}"))
        self._sync_style_buttons()
        self.view.grab_focus()

    def _line_start(self, line):
        it = self.buffer.get_iter_at_line(line)
        return it[1] if isinstance(it, tuple) else it

    def _line_prefix(self, line_start):
        end = line_start.copy()
        end.forward_chars(2)
        return self.buffer.get_text(line_start, end, False)

    def toggle_checkbox_lines(self):
        bounds = self.buffer.get_selection_bounds()
        if bounds:
            first, last = bounds[0].get_line(), bounds[1].get_line()
        else:
            first = last = self.buffer.get_iter_at_mark(self.buffer.get_insert()).get_line()
        lines = range(first, last + 1)
        has_all = all(self._line_prefix(self._line_start(l)) in PREFIXES for l in lines)
        self.internal = True
        self.buffer.begin_user_action()
        for line in reversed(lines):
            start = self._line_start(line)
            prefix = self._line_prefix(start)
            if has_all:
                end = start.copy()
                end.forward_chars(2)
                self.buffer.delete(start, end)
            elif prefix not in PREFIXES:
                self.buffer.insert(start, BOX_OPEN + " ")
        self.buffer.end_user_action()
        self.internal = False
        self.view.grab_focus()

    def _refresh_done_tags(self):
        start, end = self.buffer.get_bounds()
        self.buffer.remove_tag(self.tag_done, start, end)
        for line in range(self.buffer.get_line_count()):
            ls = self._line_start(line)
            if ls.get_char() == BOX_DONE:
                s = ls.copy()
                s.forward_chars(2)
                le = ls.copy()
                if not le.ends_line():
                    le.forward_to_line_end()
                if s.compare(le) < 0:
                    self.buffer.apply_tag(self.tag_done, s, le)

    # ---------------------------------------------------------------- finished (read-only) notes
    def _apply_finished(self):
        hidden = self.finished or not self.revealed
        self.view.set_editable(not self.finished)
        self.view.set_cursor_visible(not self.finished)
        for widget in self.edit_widgets:
            widget.set_visible(not hidden)
        self.view.set_tooltip_text("Double-click to edit" if self.finished else None)
        self.body.set_visible_child_name("text" if self.revealed else "locked")

    def set_finished(self, finished):
        self.finished = finished
        if finished:
            cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert())
            self.buffer.place_cursor(cursor)       # drop any selection
        self._apply_finished()
        self.app.save_soon()

    def _iter_at_event(self, view, event):
        bx, by = view.window_to_buffer_coords(Gtk.TextWindowType.TEXT, int(event.x), int(event.y))
        result = view.get_iter_at_location(bx, by)
        if isinstance(result, tuple):
            return (result[1] if result[0] else None), bx
        return result, bx

    def _on_text_click(self, view, event):
        if event.button != 1:
            return False
        if event.type == DOUBLE_CLICK and self.finished:
            self.set_finished(False)
            it, _bx = self._iter_at_event(view, event)
            offset = it.get_offset() if it is not None else self.buffer.get_char_count()
            # After GTK's own double-click handling (which selects a word), put the cursor there.
            GLib.idle_add(lambda: self.buffer.place_cursor(self.buffer.get_iter_at_offset(offset)) or False)
            view.grab_focus()
            return True
        if event.type != Gdk.EventType.BUTTON_PRESS:
            return False
        it, bx = self._iter_at_event(view, event)
        if it is None:
            return False
        if it.get_line_offset() != 0 or it.get_char() not in BOXES:
            return False
        rect = view.get_iter_location(it)
        if not (rect.x <= bx <= rect.x + rect.width + 4):
            return False
        line = it.get_line()
        new = BOX_DONE if it.get_char() == BOX_OPEN else BOX_OPEN
        self.internal = True
        self.buffer.begin_user_action()
        end = it.copy()
        end.forward_char()
        self.buffer.delete(it, end)
        self.buffer.insert(self._line_start(line), new)
        self.buffer.end_user_action()
        self.internal = False
        return True

    def _on_text_key(self, _view, event):
        if event.keyval not in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) or self.finished:
            return False
        if event.state & Gdk.ModifierType.CONTROL_MASK:
            return False
        if not event.state & Gdk.ModifierType.SHIFT_MASK:
            # Plain Enter: the note is done.
            if self.plain_text().strip():
                self.set_finished(True)
            return True
        # Shift+Enter: new line (and the next checkbox inside a checkbox list).
        cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        start = self._line_start(cursor.get_line())
        if self._line_prefix(start) not in PREFIXES:
            return False
        after_prefix = start.copy()
        after_prefix.forward_chars(2)
        line_end = cursor.copy()
        if not line_end.ends_line():
            line_end.forward_to_line_end()
        if not self.buffer.get_text(after_prefix, line_end, False).strip():
            # Enter on an empty checkbox line ends the list.
            self.internal = True
            self.buffer.delete(start, after_prefix)
            self.internal = False
            return True
        self.buffer.insert_at_cursor("\n" + BOX_OPEN + " ")
        self.view.scroll_mark_onscreen(self.buffer.get_insert())
        return True

    # ---------------------------------------------------------------- window behaviour
    def _on_key(self, _widget, event):
        if not event.state & Gdk.ModifierType.CONTROL_MASK:
            return False
        key = Gdk.keyval_to_lower(event.keyval)
        if self.finished and key in (Gdk.KEY_b, Gdk.KEY_i, Gdk.KEY_t):
            return True
        if key == Gdk.KEY_b:
            self.toggle_style("bold")
        elif key == Gdk.KEY_i:
            self.toggle_style("italic")
        elif key == Gdk.KEY_t:
            self.toggle_checkbox_lines()
        elif key == Gdk.KEY_n:
            self.app.new_note(near=self)
        else:
            return False
        return True

    def _on_header_press(self, _widget, event):
        if event.button == 1 and event.type == Gdk.EventType.BUTTON_PRESS:
            self.begin_move_drag(event.button, int(event.x_root), int(event.y_root), event.time)
        return False

    def _on_grip_press(self, _widget, event):
        if event.button == 1:
            self.begin_resize_drag(Gdk.WindowEdge.SOUTH_EAST, event.button,
                                   int(event.x_root), int(event.y_root), event.time)
        return True

    def _on_delete_event(self, *_):
        # Alt+F4 and friends: keep the note, just leave it where it is.
        return True

    def _on_lock_toggled(self, button):
        self.lock = button.get_active()
        self.app.save_soon()
        self.app.update_lock_screen_soon()

    def focus_text(self):
        self.show_all()
        gdk_window = self.get_window()
        self.present_with_time(server_time(gdk_window) if gdk_window else Gtk.get_current_event_time())
        self.view.grab_focus()


# --------------------------------------------------------------------------
# search window: every note, on screen and archived
# --------------------------------------------------------------------------

class SearchWindow(Gtk.Window):
    def __init__(self, app):
        super().__init__(title="Search notes")
        self.app = app
        self.set_default_size(540, 640)
        self.set_keep_above(True)
        self.set_icon_name("edit-find")
        self.connect("delete-event", lambda *_: self.hide() or True)
        self.connect("key-press-event", self._on_key)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(10)
        self.add(box)
        top = Gtk.Box(spacing=6)
        self.entry = Gtk.SearchEntry()
        self.entry.set_placeholder_text("Search your notes (e.g. wifi, Marci, +36)")
        self.entry.connect("search-changed", lambda *_: self.refresh())
        top.pack_start(self.entry, True, True, 0)
        self.vault_button = Gtk.Button()
        self.vault_button.connect("clicked", self._on_vault_button)
        top.pack_start(self.vault_button, False, False, 0)
        box.pack_start(top, False, False, 0)
        self.status = Gtk.Label(xalign=0)
        self.status.get_style_context().add_class("search-meta")
        box.pack_start(self.status, False, False, 0)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        placeholder = Gtk.Label(label="No notes found")
        placeholder.get_style_context().add_class("search-meta")
        placeholder.set_margin_top(30)
        placeholder.show()
        self.listbox.set_placeholder(placeholder)
        scroller.add(self.listbox)
        box.pack_start(scroller, True, True, 0)

    def open(self):
        self.refresh()
        self.show_all()
        self.present()
        self.entry.grab_focus()

    def _on_key(self, _widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.hide()
            return True
        return False

    def _on_vault_button(self, *_):
        if self.app.vault.unlocked:
            self.app.lock_vault()
        else:
            self.app.unlock_vault(parent=self)

    def flash(self, text):
        self.status.set_text(text)
        GLib.timeout_add_seconds(3, lambda: self.status.set_text("") or False)

    def _items(self):
        vault = self.app.vault
        items = []
        for note in self.app.notes:
            text = note.plain_text() if note.revealed else None
            items.append({"where": "screen", "note": note, "id": note.id, "color": note.color,
                          "time": note.created, "secret": note.secret, "text": text})
        for entry in self.app.archive:
            text = None
            if entry.get("secret"):
                if vault.unlocked and entry.get("enc"):
                    try:
                        text = runs_text(vault.decrypt(entry["enc"]))
                    except Exception:
                        text = None
            else:
                text = runs_text(entry.get("runs"))
            items.append({"where": "archive", "entry": entry, "id": entry.get("id"),
                          "color": entry.get("color", DEFAULT_COLOR),
                          "time": entry.get("archived") or entry.get("created") or 0,
                          "secret": bool(entry.get("secret")), "text": text})
        items.sort(key=lambda i: (i["where"] != "screen", -i["time"]))
        return items

    def refresh(self):
        vault = self.app.vault
        self.vault_button.set_visible(vault.configured)
        self.vault_button.set_label("Lock now" if vault.unlocked else "Unlock locked notes")
        query = self.entry.get_text().strip()
        terms = [t for t in fold(query)[0].split() if t]
        for child in self.listbox.get_children():
            self.listbox.remove(child)
        hidden_locked = 0
        shown = 0
        for item in self._items():
            if item["text"] is None:                 # a locked note while the vault is locked
                if terms:
                    hidden_locked += 1
                    continue
            elif terms:
                folded = fold(item["text"])[0]
                if not all(t in folded for t in terms):
                    continue
            self.listbox.add(self._row(item, terms))
            shown += 1
        if hidden_locked:
            self.status.set_text(f"{hidden_locked} locked note(s) not searched - unlock them to include them")
        elif not terms:
            self.status.set_text(f"{shown} note(s)")
        else:
            self.status.set_text(f"{shown} match(es)")
        self.listbox.show_all()

    def _row(self, item, terms):
        row = Gtk.ListBoxRow()
        outer = Gtk.Box(spacing=8)
        outer.set_border_width(6)
        row.add(outer)
        stripe = Gtk.Box()
        stripe.get_style_context().add_class(f"stripe-{item['color']}")
        outer.pack_start(stripe, False, False, 0)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        outer.pack_start(col, True, True, 0)

        meta = Gtk.Box(spacing=6)
        when = datetime.datetime.fromtimestamp(item["time"]).strftime("%b %d, %Y %H:%M")
        label = Gtk.Label(label=("On screen \u00b7 created " if item["where"] == "screen" else "Archived ") + when,
                          xalign=0)
        label.get_style_context().add_class("search-meta")
        meta.pack_start(label, False, False, 0)
        if item["secret"]:
            chip = Gtk.Label(label="LOCKED")
            chip.get_style_context().add_class("search-chip")
            meta.pack_start(chip, False, False, 0)
        col.pack_start(meta, False, False, 0)

        text = Gtk.Label(xalign=0)
        text.set_line_wrap(True)
        text.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        text.set_max_width_chars(52)
        text.set_selectable(False)
        if item["text"] is None:
            text.set_markup("<i>Locked note - unlock to see and search it</i>")
        else:
            text.set_markup(highlight(item["text"], terms) or "<i>(empty)</i>")
        col.pack_start(text, False, False, 0)

        buttons = Gtk.Box(spacing=4)
        buttons.set_halign(Gtk.Align.END)
        if item["text"] is None:
            unlock = Gtk.Button(label="Unlock")
            unlock.connect("clicked", lambda *_: self.app.unlock_vault(parent=self))
            buttons.pack_start(unlock, False, False, 0)
        else:
            copy = Gtk.Button(label="Copy")
            copy.set_tooltip_text("Copy the whole note")
            copy.connect("clicked", lambda *_: self._copy(item["text"]))
            buttons.pack_start(copy, False, False, 0)
        if item["where"] == "screen":
            show = Gtk.Button(label="Show")
            show.set_tooltip_text("Bring this note to the front")
            show.connect("clicked", lambda *_: item["note"].present())
            buttons.pack_start(show, False, False, 0)
        else:
            back = Gtk.Button(label="Put back on screen")
            back.connect("clicked", lambda *_: self.app.restore_note(item["id"]))
            buttons.pack_start(back, False, False, 0)
            forever = Gtk.Button(label="Delete forever")
            forever.connect("clicked", lambda b: self._confirm_delete(b, item["id"]))
            buttons.pack_start(forever, False, False, 0)
        col.pack_start(buttons, False, False, 0)
        return row

    def _copy(self, text):
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(text, -1)
        self.flash("Copied to the clipboard")

    def _confirm_delete(self, button, note_id):
        # First click asks, second click deletes (this one can't be undone).
        if getattr(button, "armed", False):
            self.app.delete_forever(note_id)
            return
        button.armed = True
        button.set_label("Really delete? Click again")
        button.get_style_context().add_class("search-danger")

        def disarm():
            try:
                button.armed = False
                button.set_label("Delete forever")
                button.get_style_context().remove_class("search-danger")
            except Exception:
                pass
            return False
        GLib.timeout_add_seconds(4, disarm)


# --------------------------------------------------------------------------
# the application: keeps the notes, saves them, talks to the lock screen
# --------------------------------------------------------------------------

class QuickNotesApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.notes = []
        self.last_color = DEFAULT_COLOR
        self.lock_original = None
        self._save_id = 0
        self._lock_id = 0
        self.hotkey = None
        self.search_hotkey = None
        self.vault = Vault(None)
        self.archive = []
        self.search_window = None

    # ---------------------------------------------------------------- lifecycle
    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.hold()   # keep running with zero notes so the shortcut stays instant
        provider = Gtk.CssProvider()
        provider.load_from_data(build_css())
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        data = self._read()
        self.last_color = data.get("last_color") if data.get("last_color") in COLORS else DEFAULT_COLOR
        self.lock_original = data.get("lock_original")
        self.vault = Vault(data.get("vault"))
        self.archive = [a for a in data.get("archive", []) if isinstance(a, dict)]
        for item in data.get("notes", []):
            note = Note(self, item)
            self.notes.append(note)
            note.show_all()

        self.hotkey = GlobalHotkey(self.new_note)
        wanted = self._configured_shortcut()
        if wanted and wanted.lower() in ("none", "off"):
            active = None      # the user set a shortcut in the desktop's keyboard settings instead
        else:
            active = self.hotkey.start([wanted] if wanted else DEFAULT_SHORTCUTS)
        if active is None and not (wanted and wanted.lower() in ("none", "off")):
            print(f"quick-notes: could not use the shortcut {wanted or DEFAULT_SHORTCUTS[0]} "
                  "(taken by another program?)", file=sys.stderr)
        try:
            os.makedirs(os.path.dirname(ACTIVE_SHORTCUT_FILE), exist_ok=True)
            with open(ACTIVE_SHORTCUT_FILE, "w", encoding="utf-8") as f:
                f.write(pretty_accel(active) if active else "none")
        except OSError:
            pass

        # second shortcut: the note search
        self.search_hotkey = GlobalHotkey(self.show_search)
        wanted = self._configured_shortcut("search_shortcut")
        if wanted and wanted.lower() in ("none", "off"):
            found = None
        else:
            found = self.search_hotkey.start([wanted] if wanted else DEFAULT_SEARCH_SHORTCUTS)
        try:
            with open(ACTIVE_SEARCH_FILE, "w", encoding="utf-8") as f:
                f.write(pretty_accel(found) if found else "none")
        except OSError:
            pass
        GLib.timeout_add_seconds(5, self._vault_tick)

    @staticmethod
    def _configured_shortcut(key="shortcut"):
        kf = GLib.KeyFile()
        try:
            kf.load_from_file(SETTINGS_FILE, GLib.KeyFileFlags.NONE)
            return kf.get_string("quick-notes", key).strip() or None
        except GLib.Error:
            return None

    def do_shutdown(self):
        self.lock_vault()
        self.save_now()
        Gtk.Application.do_shutdown(self)

    def do_command_line(self, command_line):
        args = set(command_line.get_arguments()[1:])
        if "--quit" in args:
            self.quit()
        elif "--restore" in args:
            pass

        elif "--search" in args:
            self.show_search()
        elif "--show" in args:
            for note in self.notes:
                note.present()
        else:
            self.new_note()
        return 0

    # ---------------------------------------------------------------- notes
    def new_note(self, near=None):
        w, h = DEFAULT_SIZE
        display = Gdk.Display.get_default()
        if near is not None:
            nx, ny = near.get_position()
            px, py = nx + 30, ny + 30
            x, y = px, py
        else:
            _screen, px, py = display.get_default_seat().get_pointer().get_position()
            x, y = px - w // 2, py - 15
        area = display.get_monitor_at_point(px, py).get_workarea()
        x = min(max(x, area.x), area.x + area.width - w)
        y = min(max(y, area.y), area.y + area.height - h)

        note = Note(self, {"x": x, "y": y, "w": w, "h": h, "color": self.last_color})
        self.notes.append(note)
        note.focus_text()
        self.save_soon()
        return note

    def delete_note(self, note):
        """The x button: the note leaves the screen but stays searchable in the archive."""
        entry = note.to_dict()
        if not entry.get("secret") and not runs_text(entry.get("runs")).strip():
            entry = None                           # empty notes are simply dropped
        if note in self.notes:
            self.notes.remove(note)
        was_locked = note.lock
        note.destroy()
        if entry is not None:
            for key in ("x", "y"):
                entry.pop(key, None)
            entry["lock"] = False
            entry["archived"] = time.time()
            self.archive.append(entry)
        self.save_soon()
        self.refresh_search()
        if was_locked:
            self.update_lock_screen_soon()

    def restore_note(self, note_id):
        entry = next((a for a in self.archive if a.get("id") == note_id), None)
        if entry is None:
            return
        self.archive.remove(entry)
        display = Gdk.Display.get_default()
        _screen, px, py = display.get_default_seat().get_pointer().get_position()
        area = display.get_monitor_at_point(px, py).get_workarea()
        w, h = entry.get("w") or DEFAULT_SIZE[0], entry.get("h") or DEFAULT_SIZE[1]
        data = dict(entry, x=min(max(px - w // 2, area.x), area.x + area.width - w),
                    y=min(max(py - 15, area.y), area.y + area.height - h), finished=True)
        data.pop("archived", None)
        note = Note(self, data)
        self.notes.append(note)
        note.show_all()
        note.present()
        self.save_soon()
        self.refresh_search()

    def delete_forever(self, note_id):
        self.archive = [a for a in self.archive if a.get("id") != note_id]
        self.save_now()
        self.refresh_search()

    # ---------------------------------------------------------------- search + locked notes
    def show_search(self):
        if self.search_window is None:
            self.search_window = SearchWindow(self)
        self.search_window.open()

    def refresh_search(self):
        if self.search_window is not None and self.search_window.get_visible():
            self.search_window.refresh()

    def ensure_vault(self, parent=None):
        """Make sure notes can be locked: create the master password or unlock."""
        if not self.vault.available:
            dialog = Gtk.MessageDialog(transient_for=parent, modal=True, message_type=Gtk.MessageType.ERROR,
                                       buttons=Gtk.ButtonsType.OK,
                                       text="Locked notes need the python3-cryptography package.")
            dialog.format_secondary_text("Install it with:  sudo apt install python3-cryptography")
            dialog.run()
            dialog.destroy()
            return False
        if self.vault.unlocked:
            self.vault.touch()
            return True
        if not self.vault.configured:
            password = ask_password(parent, create=True)
            if password is None:
                return False
            self.vault.create(password)
            self.save_now()
            return True
        return self.unlock_vault(parent)

    def unlock_vault(self, parent=None):
        if not self.vault.configured or not self.vault.available:
            return False
        if self.vault.unlocked:
            return True
        if ask_password(parent, check=self.vault.unlock) is None:
            return False
        for note in self.notes:
            if note.secret and not note.revealed:
                try:
                    note.reveal()
                except Exception as e:
                    print(f"quick-notes: could not open a locked note: {e}", file=sys.stderr)
        self.refresh_search()
        return True

    def lock_vault(self):
        if not self.vault.unlocked:
            return
        for note in self.notes:
            if note.secret and note.revealed:
                note.conceal()
        self.save_now()
        self.vault.lock()
        self.refresh_search()

    def _vault_tick(self):
        if self.vault.unlocked and time.monotonic() > self.vault.until:
            self.lock_vault()
        return True

    # ---------------------------------------------------------------- saving
    def _read(self):
        try:
            with open(DATA_FILE, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:
            backup = DATA_FILE + ".broken"
            print(f"quick-notes: could not read notes ({e}), moved to {backup}", file=sys.stderr)
            try:
                os.replace(DATA_FILE, backup)
            except OSError:
                pass
            return {}

    def save_soon(self):
        if self._save_id:
            GLib.source_remove(self._save_id)
        self._save_id = GLib.timeout_add(400, self._save_timeout)

    def _save_timeout(self):
        self._save_id = 0
        self.save_now()
        return False

    def save_now(self):
        if self._save_id:
            GLib.source_remove(self._save_id)
            self._save_id = 0
        data = {"version": 2, "last_color": self.last_color, "lock_original": self.lock_original,
                "notes": [n.to_dict() for n in self.notes], "archive": self.archive,
                "vault": self.vault.data}
        try:
            os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
            tmp = DATA_FILE + ".tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # only you can read it
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, DATA_FILE)
        except OSError as e:
            print(f"quick-notes: could not save notes: {e}", file=sys.stderr)

    # ---------------------------------------------------------------- lock screen
    def update_lock_screen_soon(self):
        if self._lock_id:
            GLib.source_remove(self._lock_id)
        self._lock_id = GLib.timeout_add(600, self._update_lock_screen)

    @staticmethod
    def _lock_settings():
        source = Gio.SettingsSchemaSource.get_default()
        schema = source.lookup(LOCK_SCHEMA, True) if source else None
        if schema is None or not schema.has_key(LOCK_KEY):
            return None
        return Gio.Settings.new(LOCK_SCHEMA)

    def _update_lock_screen(self):
        self._lock_id = 0
        settings = self._lock_settings()
        if settings is None:
            return False
        texts = []
        for note in self.notes:
            if note.lock and not note.secret:
                lines = [l.strip() for l in note.plain_text().splitlines() if l.strip()]
                if lines:
                    texts.append("  ·  ".join(lines))
        if texts:
            if self.lock_original is None:
                self.lock_original = settings.get_string(LOCK_KEY)
            settings.set_string(LOCK_KEY, "   |   ".join(texts)[:400])
        elif self.lock_original is not None:
            settings.set_string(LOCK_KEY, self.lock_original)
            self.lock_original = None
        self.save_soon()
        return False


def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        return 0
    if "--search-shortcut" in sys.argv:
        try:
            with open(ACTIVE_SEARCH_FILE, encoding="utf-8") as f:
                print(f.read().strip())
        except OSError:
            print("none (is Quick Notes running?)")
        return 0
    if "--shortcut" in sys.argv:
        try:
            with open(ACTIVE_SHORTCUT_FILE, encoding="utf-8") as f:
                print(f.read().strip())
        except OSError:
            print("none (is Quick Notes running?)")
        return 0
    return QuickNotesApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
