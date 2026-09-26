#!/usr/bin/env python3
"""Drop Shelf - a small floating shelf for drag & drop on Linux.

Drop files (or text, links, images) onto the shelf, then drag them out
again into any other window - e.g. Google Drive in the browser.

Usage:
    drop-shelf              start / show the shelf
    drop-shelf --toggle     show the shelf at the mouse, or hide it
    drop-shelf --hidden     start in the background (used for autostart)
    drop-shelf FILE...      put files on the shelf and show it
    drop-shelf --quit       quit the running shelf
    drop-shelf --shortcut   print the keyboard shortcut in use

The shortcut can be changed in ~/.config/drop-shelf/settings.ini:
    [shelf]
    shortcut=<Super>x
"""

import base64
import ctypes
import ctypes.util
import mimetypes
import os
import re
import shutil
import sys
import threading
import time
import urllib.parse
import urllib.request

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango  # noqa: E402

APP_ID = "io.github.dropshelf.DropShelf"
TEMP_DIR = os.path.join(GLib.get_user_cache_dir(), "drop-shelf")
CONFIG_FILE = os.path.join(GLib.get_user_config_dir(), "drop-shelf", "settings.ini")
ACTIVE_SHORTCUT_FILE = os.path.join(GLib.get_user_config_dir(), "drop-shelf", "active-shortcut")
# Tried in order; the first one no other program is using wins.
DEFAULT_SHORTCUTS = ["<Super>z", "<Primary><Alt>z", "<Primary><Super>s", "<Primary><Alt>space"]
ICON_SIZE = 64
CHECK_INTERVAL_MS = 1500

COL_PATH, COL_PIXBUF, COL_NAME, COL_TOOLTIP = range(4)

# What we accept when something is dropped on the shelf, best first.
DROP_PRIORITY = [
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp",
    "text/uri-list",
    "text/x-moz-url",
    "UTF8_STRING", "text/plain;charset=utf-8", "text/plain", "STRING",
]
# What we offer when something is dragged off the shelf.
DRAG_OUT_TARGETS = ["text/uri-list", "UTF8_STRING", "text/plain"]

# Web links ending in one of these are downloaded instead of saved as a link.
DOWNLOAD_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".tif", ".tiff",
    ".ico", ".avif", ".heic", ".pdf", ".zip", ".rar", ".7z", ".tar", ".gz",
    ".xz", ".mp3", ".wav", ".ogg", ".flac", ".m4a", ".mp4", ".webm", ".mkv",
    ".mov", ".avi", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt",
    ".ods", ".odp", ".txt", ".csv", ".json", ".epub", ".deb", ".iso",
}

CSS = b"""
window.drop-shelf.transparent { background-color: transparent; }
.shelf {
    background-color: @theme_bg_color;
    border: 2px solid alpha(@theme_fg_color, 0.18);
    border-radius: 10px;
}
.shelf.dropping {
    border-color: @theme_selected_bg_color;
    background-color: mix(@theme_bg_color, @theme_selected_bg_color, 0.18);
}
.shelf-header { padding: 4px 4px 2px 10px; }
.shelf-title { font-weight: bold; }
.shelf-footer { padding: 2px 6px 4px 6px; }
.shelf-empty { opacity: 0.55; }
.shelf iconview, .shelf scrolledwindow { background-color: transparent; }
.shelf iconview:selected { border-radius: 6px; }
.shelf-status { opacity: 0.7; font-size: small; }
"""


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def safe_name(text, fallback="item", limit=60):
    text = re.sub(r"[\x00-\x1f/\\:*?\"<>|]+", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text[:limit].strip() or fallback


def unique_path(directory, name):
    base, ext = os.path.splitext(name)
    candidate = os.path.join(directory, name)
    n = 2
    while os.path.lexists(candidate):
        candidate = os.path.join(directory, f"{base} ({n}){ext}")
        n += 1
    return candidate


def short_label(name, limit=22):
    if len(name) <= limit:
        return name
    keep_end = limit // 2 - 2
    return name[: limit - keep_end - 1] + "…" + name[-keep_end:]


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def load_icon(path):
    """A thumbnail for images, otherwise the theme icon for the file type."""
    theme = Gtk.IconTheme.get_default()
    try:
        info = Gio.File.new_for_path(path).query_info(
            "standard::icon,standard::content-type,standard::size,thumbnail::path",
            Gio.FileQueryInfoFlags.NONE, None)
    except GLib.Error:
        info = None

    if info is not None:
        thumb = info.get_attribute_byte_string("thumbnail::path")
        candidates = []
        if thumb and os.path.exists(thumb):
            candidates.append(thumb)
        ctype = info.get_content_type() or ""
        if ctype.startswith("image/") and info.get_size() < 40 * 1024 * 1024:
            candidates.append(path)
        for c in candidates:
            try:
                return GdkPixbuf.Pixbuf.new_from_file_at_scale(c, ICON_SIZE, ICON_SIZE, True)
            except GLib.Error:
                pass
        gicon = info.get_icon()
        if gicon is not None:
            icon_info = theme.lookup_by_gicon(gicon, ICON_SIZE, Gtk.IconLookupFlags.FORCE_SIZE)
            if icon_info is not None:
                try:
                    return icon_info.load_icon()
                except GLib.Error:
                    pass
    for name in ("text-x-generic", "unknown", "image-missing"):
        try:
            return theme.load_icon(name, ICON_SIZE, Gtk.IconLookupFlags.FORCE_SIZE)
        except GLib.Error:
            continue
    return None


def icon_button(icon_names, fallback_label, tooltip, toggle=False):
    button = Gtk.ToggleButton() if toggle else Gtk.Button()
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


def target_list(names):
    return [Gtk.TargetEntry.new(name, 0, i) for i, name in enumerate(names)]


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
# settings
# --------------------------------------------------------------------------

class Settings:
    DEFAULTS = {"keep_after_drag": False, "hide_when_empty": True}

    def __init__(self):
        self.values = dict(self.DEFAULTS)
        kf = GLib.KeyFile()
        try:
            kf.load_from_file(CONFIG_FILE, GLib.KeyFileFlags.NONE)
            for key in self.values:
                try:
                    self.values[key] = kf.get_boolean("shelf", key)
                except GLib.Error:
                    pass
        except GLib.Error:
            pass

    def __getitem__(self, key):
        return self.values[key]

    def __setitem__(self, key, value):
        self.values[key] = value
        kf = GLib.KeyFile()
        try:   # keep keys we don't manage here, like "shortcut"
            kf.load_from_file(CONFIG_FILE, GLib.KeyFileFlags.KEEP_COMMENTS)
        except GLib.Error:
            pass
        for k, v in self.values.items():
            kf.set_boolean("shelf", k, v)
        try:
            os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
            kf.save_to_file(CONFIG_FILE)
        except (OSError, GLib.Error) as e:
            print(f"drop-shelf: could not save settings: {e}", file=sys.stderr)


# --------------------------------------------------------------------------
# the shelf window
# --------------------------------------------------------------------------

class ShelfWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Drop Shelf")
        self.app = app
        self.settings = app.settings
        self.dragging = []        # paths of the drag currently leaving the shelf
        self.drag_ok = False
        self.status_timeout = 0

        self.set_default_size(330, 250)
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_icon_name("edit-paste")
        self.stick()
        self.get_style_context().add_class("drop-shelf")

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None and screen.is_composited():
            self.set_visual(visual)
            self.get_style_context().add_class("transparent")

        self.store = Gtk.ListStore(str, GdkPixbuf.Pixbuf, str, str)
        self._build_ui()
        self._setup_drop_target()

        self.connect("delete-event", self._on_delete)
        self.connect("key-press-event", self._on_key)
        GLib.timeout_add(CHECK_INTERVAL_MS, self._prune_missing)
        self.update_state()

    # ---------------------------------------------------------------- UI
    def _build_ui(self):
        self.frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.frame.get_style_context().add_class("shelf")
        self.add(self.frame)

        # Header: drag it to move the window.
        header_events = Gtk.EventBox()
        header_events.connect("button-press-event", self._on_header_press)
        header = Gtk.Box(spacing=2)
        header.get_style_context().add_class("shelf-header")
        header_events.add(header)
        self.frame.pack_start(header_events, False, False, 0)

        self.title_label = Gtk.Label(label="Drop Shelf", xalign=0)
        self.title_label.get_style_context().add_class("shelf-title")
        header.pack_start(self.title_label, True, True, 0)

        self.pin_button = icon_button(
            ["view-pin-symbolic", "view-pin", "emblem-important-symbolic"], "Pin",
            "Keep items on the shelf after dragging them out", toggle=True)
        self.pin_button.set_active(self.settings["keep_after_drag"])
        self.pin_button.connect("toggled", self._on_pin_toggled)
        header.pack_start(self.pin_button, False, False, 0)

        clear_button = icon_button(
            ["edit-clear-all-symbolic", "edit-clear-symbolic", "edit-clear"], "Clear",
            "Clear the shelf (files are not deleted)")
        clear_button.connect("clicked", lambda *_: self.clear())
        header.pack_start(clear_button, False, False, 0)

        menu_button = Gtk.MenuButton()
        menu_button.set_relief(Gtk.ReliefStyle.NONE)
        menu_button.set_can_focus(False)
        menu_button.set_tooltip_text("More")
        menu_button.set_image(Gtk.Image.new_from_icon_name("open-menu-symbolic", Gtk.IconSize.MENU))
        menu_button.set_popup(self._build_main_menu())
        header.pack_start(menu_button, False, False, 0)

        hide_button = icon_button(
            ["window-close-symbolic", "window-close"], "✕",
            "Hide the shelf (Esc). Items stay on it.")
        hide_button.connect("clicked", lambda *_: self.hide())
        header.pack_start(hide_button, False, False, 0)

        # Body: an empty hint or the item grid.
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.frame.pack_start(self.stack, True, True, 0)

        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.CENTER)
        empty.get_style_context().add_class("shelf-empty")
        empty_icon = Gtk.Image.new_from_icon_name("document-send-symbolic", Gtk.IconSize.DIALOG)
        empty.pack_start(empty_icon, False, False, 0)
        empty_label = Gtk.Label()
        empty_label.set_markup("<b>Drop anything here</b>\n<small>files, folders, images, links, text\n"
                               "or press Ctrl+V to paste</small>")
        empty_label.set_justify(Gtk.Justification.CENTER)
        empty.pack_start(empty_label, False, False, 0)
        self.stack.add_named(empty, "empty")

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.icon_view = Gtk.IconView(model=self.store)
        self.icon_view.set_pixbuf_column(COL_PIXBUF)
        self.icon_view.set_text_column(COL_NAME)
        self.icon_view.set_tooltip_column(COL_TOOLTIP)
        self.icon_view.set_selection_mode(Gtk.SelectionMode.MULTIPLE)
        self.icon_view.set_item_width(84)
        self.icon_view.set_item_padding(4)
        self.icon_view.set_margin(6)
        self.icon_view.set_spacing(2)
        self.icon_view.enable_model_drag_source(
            Gdk.ModifierType.BUTTON1_MASK, target_list(DRAG_OUT_TARGETS),
            Gdk.DragAction.COPY | Gdk.DragAction.MOVE | Gdk.DragAction.LINK)
        self.icon_view.connect("drag-begin", self._on_items_drag_begin)
        self.icon_view.connect("drag-data-get", self._on_drag_data_get)
        self.icon_view.connect("drag-failed", self._on_drag_failed)
        self.icon_view.connect("drag-end", self._on_drag_end)
        self.icon_view.connect("item-activated", self._on_item_activated)
        self.icon_view.connect("button-press-event", self._on_icon_view_press)
        scroller.add(self.icon_view)
        self.stack.add_named(scroller, "items")

        # Footer: drag-all handle, status text, resize grip.
        footer = Gtk.Box(spacing=4)
        footer.get_style_context().add_class("shelf-footer")
        self.frame.pack_start(footer, False, False, 0)

        self.drag_all_button = Gtk.Button(label="Drag all")
        self.drag_all_button.set_tooltip_text("Drag this button to drag every item at once")
        self.drag_all_button.set_can_focus(False)
        self.drag_all_button.drag_source_set(
            Gdk.ModifierType.BUTTON1_MASK, target_list(DRAG_OUT_TARGETS),
            Gdk.DragAction.COPY | Gdk.DragAction.MOVE | Gdk.DragAction.LINK)
        self.drag_all_button.drag_source_set_icon_name("edit-select-all")
        self.drag_all_button.connect("drag-begin", self._on_all_drag_begin)
        self.drag_all_button.connect("drag-data-get", self._on_drag_data_get)
        self.drag_all_button.connect("drag-failed", self._on_drag_failed)
        self.drag_all_button.connect("drag-end", self._on_drag_end)
        self.drag_all_button.connect("clicked", lambda *_: self.icon_view.select_all())
        footer.pack_start(self.drag_all_button, False, False, 0)

        self.status_label = Gtk.Label(xalign=0)
        self.status_label.get_style_context().add_class("shelf-status")
        self.status_label.set_ellipsize(Pango.EllipsizeMode.END)
        footer.pack_start(self.status_label, True, True, 0)

        grip = Gtk.EventBox()
        grip.add(Gtk.Label(label="◢"))
        grip.set_tooltip_text("Resize")
        grip.connect("button-press-event", self._on_grip_press)
        grip.connect("realize", lambda w: w.get_window().set_cursor(
            Gdk.Cursor.new_from_name(w.get_display(), "se-resize")))
        footer.pack_end(grip, False, False, 0)

    def _build_main_menu(self):
        menu = Gtk.Menu()

        paste = Gtk.MenuItem(label="Paste from clipboard  (Ctrl+V)")
        paste.connect("activate", lambda *_: self.paste_clipboard())
        menu.append(paste)

        select_all = Gtk.MenuItem(label="Select all  (Ctrl+A)")
        select_all.connect("activate", lambda *_: self.icon_view.select_all())
        menu.append(select_all)

        menu.append(Gtk.SeparatorMenuItem())

        hide_empty = Gtk.CheckMenuItem(label="Hide shelf when it becomes empty")
        hide_empty.set_active(self.settings["hide_when_empty"])
        hide_empty.connect("toggled", lambda w: self.settings.__setitem__("hide_when_empty", w.get_active()))
        menu.append(hide_empty)

        menu.append(Gtk.SeparatorMenuItem())

        quit_item = Gtk.MenuItem(label="Quit Drop Shelf")
        quit_item.connect("activate", lambda *_: self.app.quit())
        menu.append(quit_item)

        menu.show_all()
        return menu

    def _build_item_menu(self, paths):
        menu = Gtk.Menu()
        several = len(paths) > 1

        open_item = Gtk.MenuItem(label="Open" if not several else f"Open {len(paths)} items")
        open_item.connect("activate", lambda *_: [self.open_path(p) for p in paths])
        menu.append(open_item)

        folder_item = Gtk.MenuItem(label="Show in folder")
        folder_item.connect("activate", lambda *_: self.show_in_folder(paths[0]))
        menu.append(folder_item)

        copy_item = Gtk.MenuItem(label="Copy path" if not several else "Copy paths")
        copy_item.connect("activate", lambda *_: self.copy_paths(paths))
        menu.append(copy_item)

        menu.append(Gtk.SeparatorMenuItem())

        remove_item = Gtk.MenuItem(label="Remove from shelf  (Del)")
        remove_item.connect("activate", lambda *_: self.remove_paths(set(paths)))
        menu.append(remove_item)

        menu.show_all()
        return menu

    # ---------------------------------------------------------------- state
    def paths(self):
        return [row[COL_PATH] for row in self.store]

    def selected_paths(self):
        return [self.store[p][COL_PATH] for p in self.icon_view.get_selected_items()]

    def update_state(self):
        count = len(self.store)
        page = "items" if count else "empty"
        # a Gtk.Stack ignores switching to a page that isn't shown yet (the shelf
        # starts hidden at login), so make sure it is
        self.stack.get_child_by_name(page).show_all()
        self.stack.set_visible_child_name(page)
        self.drag_all_button.set_sensitive(count > 0)
        self.drag_all_button.set_label(f"Drag all ({count})" if count else "Drag all")
        self.title_label.set_text("Drop Shelf" if not count else
                                  f"Drop Shelf · {count} item{'s' if count != 1 else ''}")
        if not self.status_timeout:
            self.status_label.set_text("" if count else "Esc to hide")

    def flash_status(self, text, seconds=3):
        self.status_label.set_text(text)
        if self.status_timeout:
            GLib.source_remove(self.status_timeout)

        def reset():
            self.status_timeout = 0
            self.update_state()
            return False
        self.status_timeout = GLib.timeout_add_seconds(seconds, reset)

    def add_path(self, path):
        path = os.path.abspath(path)
        if not os.path.lexists(path) or path in self.paths():
            return False
        name = os.path.basename(path.rstrip("/")) or path
        tooltip = GLib.markup_escape_text(path)
        try:
            if os.path.isfile(path):
                tooltip += f"\n{human_size(os.path.getsize(path))}"
        except OSError:
            pass
        self.store.append([path, load_icon(path), short_label(name), tooltip])
        self.update_state()
        return True

    def remove_paths(self, paths):
        it = self.store.get_iter_first()
        while it is not None:
            if self.store[it][COL_PATH] in paths:
                if not self.store.remove(it):
                    it = None
            else:
                it = self.store.iter_next(it)
        self.update_state()

    def clear(self):
        self.store.clear()
        self.update_state()

    def _prune_missing(self):
        gone = {p for p in self.paths() if not os.path.lexists(p)}
        if gone:
            self.remove_paths(gone)
            self._maybe_autohide()
        return True

    def _maybe_autohide(self):
        if len(self.store) == 0 and self.settings["hide_when_empty"] and self.get_visible():
            def hide_if_still_empty():
                if len(self.store) == 0:
                    self.hide()
                return False
            GLib.timeout_add(250, hide_if_still_empty)

    # ---------------------------------------------------------------- show / hide
    def show_at_pointer(self):
        display = self.get_display()
        pointer = display.get_default_seat().get_pointer()
        _screen, px, py = pointer.get_position()
        monitor = display.get_monitor_at_point(px, py)
        area = monitor.get_workarea()
        width, height = self.get_size()
        x = min(max(px - width // 2, area.x), area.x + area.width - width)
        y = min(max(py - 30, area.y), area.y + area.height - height)
        self.move(x, y)
        self.show_all()
        self.present()

    def toggle(self):
        if self.get_visible():
            self.hide()
        else:
            self.show_at_pointer()

    def _on_delete(self, *_):
        self.hide()
        return True

    def _on_header_press(self, _widget, event):
        if event.button == 1:
            self.begin_move_drag(event.button, int(event.x_root), int(event.y_root), event.time)
        return False

    def _on_grip_press(self, _widget, event):
        if event.button == 1:
            self.begin_resize_drag(Gdk.WindowEdge.SOUTH_EAST, event.button,
                                   int(event.x_root), int(event.y_root), event.time)
        return True

    def _on_key(self, _widget, event):
        ctrl = event.state & Gdk.ModifierType.CONTROL_MASK
        key = event.keyval
        if key == Gdk.KEY_Escape:
            self.hide()
        elif key in (Gdk.KEY_Delete, Gdk.KEY_BackSpace, Gdk.KEY_KP_Delete):
            self.remove_paths(set(self.selected_paths()))
        elif ctrl and key in (Gdk.KEY_v, Gdk.KEY_V):
            self.paste_clipboard()
        elif ctrl and key in (Gdk.KEY_a, Gdk.KEY_A):
            self.icon_view.select_all()
        elif ctrl and key in (Gdk.KEY_c, Gdk.KEY_C):
            self.copy_paths(self.selected_paths())
        elif ctrl and key in (Gdk.KEY_q, Gdk.KEY_Q):
            self.app.quit()
        else:
            return False
        return True

    # ---------------------------------------------------------------- item actions
    def _on_icon_view_press(self, view, event):
        if event.type != Gdk.EventType.BUTTON_PRESS or event.button != 3:
            return False
        tree_path = view.get_path_at_pos(int(event.x), int(event.y))
        if tree_path is None:
            view.unselect_all()
            self._build_main_menu().popup_at_pointer(event)
            return True
        if not view.path_is_selected(tree_path):
            view.unselect_all()
            view.select_path(tree_path)
        self._item_menu = self._build_item_menu(self.selected_paths())
        self._item_menu.popup_at_pointer(event)
        return True

    def _on_item_activated(self, _view, tree_path):
        self.open_path(self.store[tree_path][COL_PATH])

    def open_path(self, path):
        try:
            Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(path).get_uri(), None)
        except GLib.Error as e:
            self.flash_status(f"Could not open: {e.message}")

    def show_in_folder(self, path):
        uri = Gio.File.new_for_path(path).get_uri()
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            bus.call_sync("org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                          "org.freedesktop.FileManager1", "ShowItems",
                          GLib.Variant("(ass)", ([uri], "")), None,
                          Gio.DBusCallFlags.NONE, 3000, None)
        except GLib.Error:
            parent = Gio.File.new_for_path(os.path.dirname(path)).get_uri()
            try:
                Gio.AppInfo.launch_default_for_uri(parent, None)
            except GLib.Error as e:
                self.flash_status(f"Could not open folder: {e.message}")

    def copy_paths(self, paths):
        if not paths:
            return
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text("\n".join(paths), -1)
        self.flash_status("Path copied" if len(paths) == 1 else f"{len(paths)} paths copied")

    # ---------------------------------------------------------------- dragging out
    def _on_items_drag_begin(self, _widget, _context):
        self.dragging = self.selected_paths()
        self.drag_ok = True

    def _on_all_drag_begin(self, _widget, _context):
        self.dragging = self.paths()
        self.drag_ok = True

    def _on_drag_data_get(self, _widget, _context, data, _info, _time):
        if not self.dragging:
            return
        if data.get_target().name() == "text/uri-list":
            data.set_uris([Gio.File.new_for_path(p).get_uri() for p in self.dragging])
        else:
            data.set_text("\n".join(self.dragging), -1)

    def _on_drag_failed(self, _widget, _context, _result):
        self.drag_ok = False
        return False

    def _on_drag_end(self, _widget, _context):
        if self.drag_ok and self.dragging and not self.settings["keep_after_drag"]:
            self.remove_paths(set(self.dragging))
            self._maybe_autohide()
        self.dragging = []

    def _on_pin_toggled(self, button):
        self.settings["keep_after_drag"] = button.get_active()
        self.flash_status("Items stay after dragging" if button.get_active()
                          else "Items leave the shelf after dragging")

    # ---------------------------------------------------------------- dropping in
    def _setup_drop_target(self):
        self.drag_dest_set(Gtk.DestDefaults(0), target_list(DROP_PRIORITY),
                           Gdk.DragAction.COPY | Gdk.DragAction.MOVE | Gdk.DragAction.LINK)
        self.connect("drag-motion", self._on_drag_motion)
        self.connect("drag-leave", self._on_drag_leave)
        self.connect("drag-drop", self._on_drag_drop)
        self.connect("drag-data-received", self._on_drag_data_received)

    @staticmethod
    def _is_own_drag(context):
        return Gtk.drag_get_source_widget(context) is not None

    def _choose_target(self, context):
        offered = {t.name() for t in context.list_targets()}
        for name in DROP_PRIORITY:
            if name in offered:
                return name
        return None

    def _on_drag_motion(self, _widget, context, _x, _y, time_):
        if self._is_own_drag(context) or self._choose_target(context) is None:
            Gdk.drag_status(context, 0, time_)
            return False
        actions = context.get_actions()
        action = Gdk.DragAction.COPY if actions & Gdk.DragAction.COPY else context.get_suggested_action()
        Gdk.drag_status(context, action, time_)
        self.frame.get_style_context().add_class("dropping")
        return True

    def _on_drag_leave(self, *_):
        self.frame.get_style_context().remove_class("dropping")

    def _on_drag_drop(self, widget, context, _x, _y, time_):
        self.frame.get_style_context().remove_class("dropping")
        target = None if self._is_own_drag(context) else self._choose_target(context)
        if target is None:
            return False
        widget.drag_get_data(context, Gdk.Atom.intern(target, False), time_)
        return True

    def _on_drag_data_received(self, _widget, context, _x, _y, data, _info, time_):
        try:
            added = self.receive_selection(data)
        except Exception as e:  # never leave the source app hanging
            print(f"drop-shelf: drop failed: {e}", file=sys.stderr)
            added = False
        # Never ask the source to delete anything - we only hold references.
        Gtk.drag_finish(context, added, False, time_)

    def receive_selection(self, data):
        target = data.get_target().name()
        raw = data.get_data() or b""

        if target.startswith("image/") and raw:
            ext = mimetypes.guess_extension(target) or ".png"
            return self.add_bytes(raw, f"Image {time.strftime('%Y-%m-%d %H-%M-%S')}{ext}")

        if target == "text/uri-list":
            uris = data.get_uris() or [u for u in raw.decode("utf-8", "replace").splitlines()
                                       if u and not u.startswith("#")]
            return self.add_uris(uris)

        if target == "text/x-moz-url":
            text = raw.decode("utf-16", "replace") if b"\x00" in raw else raw.decode("utf-8", "replace")
            lines = [l.strip() for l in text.replace("\x00", "").splitlines() if l.strip()]
            if lines:
                return self.add_uri(lines[0], lines[1] if len(lines) > 1 else None)
            return False

        text = data.get_text()
        if text is None:
            text = raw.decode("utf-8", "replace")
        return self.add_text(text)

    # ---------------------------------------------------------------- adding things
    def add_uris(self, uris):
        added = False
        for uri in uris:
            added = self.add_uri(uri.strip()) or added
        return added

    def add_uri(self, uri, title=None):
        if not uri:
            return False
        scheme = GLib.uri_parse_scheme(uri)
        if scheme is None and os.path.isabs(uri):
            return self.add_path(uri)
        if scheme == "file":
            path = Gio.File.new_for_uri(uri).get_path()
            return bool(path) and self.add_path(path)
        if scheme == "data":
            return self.add_data_uri(uri)
        if scheme in ("http", "https"):
            ext = os.path.splitext(urllib.parse.urlparse(uri).path)[1].lower()
            if ext in DOWNLOAD_EXTS:
                self.download(uri, title)
                return True
            return self.add_link(uri, title)
        # Anything else Gio can read (smb://, sftp://, mtp:// ...): use its local path if any.
        path = Gio.File.new_for_uri(uri).get_path()
        if path and os.path.lexists(path):
            return self.add_path(path)
        return self.add_link(uri, title)

    def add_bytes(self, blob, name):
        path = unique_path(TEMP_DIR, safe_name(name))
        with open(path, "wb") as f:
            f.write(blob)
        return self.add_path(path)

    def add_text(self, text):
        stripped = (text or "").strip()
        if not stripped:
            return False
        if "\n" not in stripped and re.match(r"^(https?|ftp)://\S+$", stripped):
            return self.add_uri(stripped)
        if "\n" not in stripped and stripped.startswith("file://"):
            return self.add_uri(stripped)
        if "\n" not in stripped and os.path.isabs(stripped) and os.path.lexists(stripped):
            return self.add_path(stripped)
        first_words = safe_name(stripped.splitlines()[0], "Text", 40)
        return self.add_bytes(text.encode("utf-8"), f"{first_words}.txt")

    def add_link(self, url, title=None):
        if not title:
            parsed = urllib.parse.urlparse(url)
            tail = urllib.parse.unquote(os.path.basename(parsed.path.rstrip("/")))
            title = f"{parsed.netloc} {tail}".strip() or "Link"
        body = f"[InternetShortcut]\nURL={url}\n"
        return self.add_bytes(body.encode("utf-8"), f"{safe_name(title, 'Link')}.url")

    def add_data_uri(self, uri):
        match = re.match(r"^data:([^;,]*)(;base64)?,(.*)$", uri, re.S)
        if not match:
            return False
        mime = match.group(1) or "text/plain"
        payload = match.group(3)
        blob = base64.b64decode(payload) if match.group(2) else urllib.parse.unquote_to_bytes(payload)
        ext = mimetypes.guess_extension(mime) or ".bin"
        return self.add_bytes(blob, f"Dropped {time.strftime('%Y-%m-%d %H-%M-%S')}{ext}")

    def download(self, url, title=None):
        self.flash_status("Downloading…", seconds=60)

        def worker():
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 DropShelf"})
                with urllib.request.urlopen(request, timeout=30) as response:
                    blob = response.read()
                    ctype = (response.headers.get_content_type() or "").lower()
                name = urllib.parse.unquote(os.path.basename(urllib.parse.urlparse(url).path)) or "download"
                if not os.path.splitext(name)[1]:
                    name += mimetypes.guess_extension(ctype) or ""
                GLib.idle_add(self._download_done, blob, name, url, title)
            except Exception as e:
                print(f"drop-shelf: download failed, keeping a link instead: {e}", file=sys.stderr)
                GLib.idle_add(self._download_done, None, None, url, title)

        threading.Thread(target=worker, daemon=True).start()

    def _download_done(self, blob, name, url, title):
        if blob is not None:
            self.add_bytes(blob, name)
            self.flash_status("Downloaded")
        else:
            self.add_link(url, title)
            self.flash_status("Could not download – saved as a link")
        return False

    def paste_clipboard(self):
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        # Files copied in Nemo / Nautilus / Caja.
        sel = clipboard.wait_for_contents(Gdk.Atom.intern("x-special/gnome-copied-files", False))
        if sel is not None and sel.get_data():
            lines = sel.get_data().decode("utf-8", "replace").splitlines()
            uris = [l for l in lines[1:] if l]
            if self.add_uris(uris):
                return
        uris = clipboard.wait_for_uris()
        if uris and self.add_uris(uris):
            return
        image = clipboard.wait_for_image()
        if image is not None:
            path = unique_path(TEMP_DIR, f"Pasted image {time.strftime('%Y-%m-%d %H-%M-%S')}.png")
            image.savev(path, "png", [], [])
            self.add_path(path)
            return
        text = clipboard.wait_for_text()
        if text and self.add_text(text):
            return
        self.flash_status("Nothing to paste")


# --------------------------------------------------------------------------
# application (single instance - a second launch talks to the first one)
# --------------------------------------------------------------------------

class DropShelfApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.window = None
        self.settings = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        # The shelf always starts empty: throw away text/images saved last time.
        shutil.rmtree(TEMP_DIR, ignore_errors=True)
        os.makedirs(TEMP_DIR, exist_ok=True)
        self.settings = Settings()
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.window = ShelfWindow(self)

        kf = GLib.KeyFile()
        try:
            kf.load_from_file(CONFIG_FILE, GLib.KeyFileFlags.NONE)
            wanted = kf.get_string("shelf", "shortcut").strip() or None
        except GLib.Error:
            wanted = None
        self.hotkey = GlobalHotkey(self.window.toggle)
        active = self.hotkey.start([wanted] if wanted else DEFAULT_SHORTCUTS)
        if active is None:
            print(f"drop-shelf: could not use the shortcut {wanted or DEFAULT_SHORTCUTS[0]} "
                  "(taken by another program?)", file=sys.stderr)
        try:
            os.makedirs(os.path.dirname(ACTIVE_SHORTCUT_FILE), exist_ok=True)
            with open(ACTIVE_SHORTCUT_FILE, "w", encoding="utf-8") as f:
                f.write(pretty_accel(active) if active else "none")
        except OSError:
            pass

    def do_command_line(self, command_line):
        args = command_line.get_arguments()[1:]
        cwd = command_line.get_cwd() or os.getcwd()
        flags = {a for a in args if a.startswith("--")}
        files = [a for a in args if not a.startswith("--")]

        if "--quit" in flags:
            self.quit()
            return 0

        added = False
        for f in files:
            if GLib.uri_parse_scheme(f):
                added = self.window.add_uri(f) or added
            else:
                added = self.window.add_path(os.path.join(cwd, f)) or added

        if "--toggle" in flags and not files:
            self.window.toggle()
        elif "--hide" in flags:
            self.window.hide()
        elif "--hidden" in flags and not files:
            pass
        elif added or files or "--show" in flags or not self.window.get_visible():
            self.window.show_at_pointer()
        else:
            self.window.present()
        return 0


def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        return 0
    if "--shortcut" in sys.argv:
        try:
            with open(ACTIVE_SHORTCUT_FILE, encoding="utf-8") as f:
                print(f.read().strip())
        except OSError:
            print("none (is Drop Shelf running?)")
        return 0
    return DropShelfApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
