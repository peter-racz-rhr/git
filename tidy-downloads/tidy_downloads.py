#!/usr/bin/env python3
"""Tidy Downloads - keeps your Downloads folder in order by itself.

Fifteen minutes after a download has finished it goes into a folder by type
(PDFs, Images, Documents, Installers, Archives, Videos, Music, Other) inside
Downloads. Installers you haven't touched for 30 days go to the Trash (never
deleted for real). Every tidy can be undone.

Usage:
    tidy-downloads            start in the background (the menu entry opens the window)
    tidy-downloads --window   show the window
    tidy-downloads --now      tidy right away (ignores the 15 minutes)
    tidy-downloads --undo     undo the last tidy
    tidy-downloads --quit     stop

Settings in ~/.config/tidy-downloads/settings.ini:
    [tidy]
    wait_minutes=15
    clean_installers=true
    installer_days=30
    cat_sweeps=true
"""

import json
import os
import shutil
import subprocess
import sys
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

APP_ID = "io.github.tidydownloads.TidyDownloads"
CONFIG_DIR = os.path.join(GLib.get_user_config_dir(), "tidy-downloads")
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.ini")
DATA_DIR = os.path.join(GLib.get_user_data_dir(), "tidy-downloads")
LOG_FILE = os.path.join(DATA_DIR, "history.json")
PIXEL_CAT = os.path.expanduser("~/.local/bin/pixel-cat")
SCAN_SECONDS = int(os.environ.get("TIDY_SCAN_SECONDS", "60"))
KEEP_BATCHES = 30

# (folder, extensions) in the order they are checked
CATEGORIES = [
    ("PDFs", {".pdf"}),
    ("Images", {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".tif", ".tiff", ".heic",
                ".heif", ".avif", ".ico", ".raw", ".cr2", ".nef"}),
    ("Documents", {".doc", ".docx", ".odt", ".rtf", ".txt", ".md", ".ppt", ".pptx", ".odp", ".key",
                   ".xls", ".xlsx", ".ods", ".csv", ".epub", ".pages", ".numbers", ".tex", ".json"}),
    ("Installers", {".deb", ".appimage", ".run", ".flatpakref", ".flatpak", ".rpm", ".snap", ".exe",
                    ".msi", ".dmg", ".iso", ".pkg", ".apk"}),
    ("Archives", {".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".zst", ".tar.gz",
                  ".tar.xz", ".tar.bz2", ".tar.zst"}),
    ("Videos", {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".wmv", ".flv"}),
    ("Music", {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".opus", ".wma"}),
]
OTHER = "Other"
FOLDERS = [c[0] for c in CATEGORIES] + [OTHER]
# still downloading / temporary files: never touch these
PARTIAL = (".crdownload", ".part", ".partial", ".download", ".tmp", ".temp", ".opdownload", "~")
INSTALLER_DIR = "Installers"


def now():
    """The time (TIDY_DAYS_LATER=n pretends it's n days later, for testing)."""
    return time.time() + float(os.environ.get("TIDY_DAYS_LATER", "0")) * 86400


def downloads_dir():
    path = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
    if not path or os.path.realpath(path) == os.path.realpath(os.path.expanduser("~")):
        path = os.path.expanduser("~/Downloads")
    return path


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------

class Settings:
    DEFAULTS = {"wait_minutes": 15, "clean_installers": True, "installer_days": 30, "cat_sweeps": True,
                "since": 0}

    def __init__(self):
        self.kf = GLib.KeyFile()
        try:
            self.kf.load_from_file(SETTINGS_FILE, GLib.KeyFileFlags.KEEP_COMMENTS)
        except GLib.Error:
            pass

    def __getitem__(self, key):
        default = self.DEFAULTS[key]
        try:
            if isinstance(default, bool):
                return self.kf.get_boolean("tidy", key)
            return self.kf.get_integer("tidy", key)
        except GLib.Error:
            return default

    def __setitem__(self, key, value):
        if isinstance(self.DEFAULTS[key], bool):
            self.kf.set_boolean("tidy", key, bool(value))
        else:
            self.kf.set_integer("tidy", key, int(value))
        os.makedirs(CONFIG_DIR, exist_ok=True)
        self.kf.save_to_file(SETTINGS_FILE)


# --------------------------------------------------------------------------
# the tidying itself (no GUI in here)
# --------------------------------------------------------------------------

def category_of(name):
    lower = name.lower()
    for folder, exts in CATEGORIES:
        for ext in exts:
            if lower.endswith(ext):
                return folder
    # no known extension: ask the system what kind of file it is
    ctype, _uncertain = Gio.content_type_guess(name, None)
    mime = Gio.content_type_get_mime_type(ctype) or ""
    for prefix, folder in (("image/", "Images"), ("video/", "Videos"), ("audio/", "Music"),
                           ("text/", "Documents")):
        if mime.startswith(prefix):
            return folder
    return OTHER


def free_name(folder, name):
    """name, or "name (2).ext" if that's taken: nothing is ever overwritten."""
    target = os.path.join(folder, name)
    if not os.path.lexists(target):
        return target
    base, ext = name, ""
    for double in (".tar.gz", ".tar.xz", ".tar.bz2", ".tar.zst"):
        if name.lower().endswith(double):
            base, ext = name[: -len(double)], name[-len(double):]
            break
    else:
        base, ext = os.path.splitext(name)
    n = 2
    while os.path.lexists(os.path.join(folder, f"{base} ({n}){ext}")):
        n += 1
    return os.path.join(folder, f"{base} ({n}){ext}")


def arrived(stat):
    """When the file showed up in Downloads (a move or a finished download updates ctime)."""
    return max(stat.st_mtime, stat.st_ctime)


def last_used(stat):
    return max(stat.st_atime, stat.st_mtime, stat.st_ctime)


class Tidier:
    def __init__(self, settings):
        self.settings = settings
        self.root = downloads_dir()
        self.sizes = {}          # file -> size at the last scan (still growing = not finished)
        self.history = self._load()

    # ---- history, for undo
    def _load(self):
        try:
            with open(LOG_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return []

    def _save(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = LOG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.history[-KEEP_BATCHES:], f, ensure_ascii=False, indent=1)
        os.replace(tmp, LOG_FILE)

    # ---- one pass
    def tidy(self, ignore_wait=False):
        """Sort finished downloads; returns the batch (moves and trashed files) or None."""
        if not os.path.isdir(self.root):
            return None
        wait = 0 if ignore_wait else self.settings["wait_minutes"] * 60
        moves, trashed = [], []
        seen = {}
        try:
            entries = list(os.scandir(self.root))
        except OSError:
            return None
        for entry in entries:
            name = entry.name
            if name.startswith(".") or name.lower().endswith(PARTIAL) or name in FOLDERS:
                continue
            try:
                if not entry.is_file(follow_symlinks=False):
                    continue
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            seen[entry.path] = st.st_size
            if arrived(st) < self.settings["since"]:
                continue                            # was already here before you installed me
            if not ignore_wait:
                if self.sizes.get(entry.path) != st.st_size:
                    continue                        # new or still growing: look again next time
                if now() - arrived(st) < wait:
                    continue
            folder = os.path.join(self.root, category_of(name))
            try:
                os.makedirs(folder, exist_ok=True)
                target = free_name(folder, name)
                os.rename(entry.path, target)
                moves.append([entry.path, target])
            except OSError:
                continue
        self.sizes = seen
        if self.settings["clean_installers"]:
            trashed = self._clean_installers()
        if not moves and not trashed:
            return None
        batch = {"time": time.time(), "moves": moves, "trashed": trashed}
        self.history.append(batch)
        self._save()
        return batch

    def _clean_installers(self):
        folder = os.path.join(self.root, INSTALLER_DIR)
        limit = self.settings["installer_days"] * 86400
        out = []
        try:
            entries = list(os.scandir(folder))
        except OSError:
            return out
        for entry in entries:
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if now() - last_used(st) < limit:
                continue
            try:
                Gio.File.new_for_path(entry.path).trash(None)
                out.append(entry.path)
            except GLib.Error:
                continue
        return out

    # ---- undo
    def undo(self):
        """Put the last batch back. Returns (restored, problems)."""
        if not self.history:
            return 0, 0
        batch = self.history.pop()
        restored = problems = 0
        for original, target in reversed(batch["moves"]):
            try:
                if os.path.lexists(original) or not os.path.lexists(target):
                    problems += 1
                    continue
                os.rename(target, original)
                restored += 1
            except OSError:
                problems += 1
        for path in batch["trashed"]:
            if restore_from_trash(path):
                restored += 1
            else:
                problems += 1
        # empty category folders we made are removed again
        for folder in FOLDERS:
            try:
                os.rmdir(os.path.join(self.root, folder))
            except OSError:
                pass
        self._save()
        return restored, problems

    def counts(self):
        out = {}
        for folder in FOLDERS:
            try:
                out[folder] = sum(1 for e in os.scandir(os.path.join(self.root, folder)) if e.is_file())
            except OSError:
                out[folder] = 0
        return out


def restore_from_trash(original):
    """Bring a trashed file back to where it was (home trash only)."""
    trash = os.path.join(GLib.get_user_data_dir(), "Trash")
    info_dir = os.path.join(trash, "info")
    best = None
    try:
        for info in os.scandir(info_dir):
            if not info.name.endswith(".trashinfo"):
                continue
            kf = GLib.KeyFile()
            try:
                kf.load_from_file(info.path, GLib.KeyFileFlags.NONE)
                path = GLib.Uri.unescape_string(kf.get_string("Trash Info", "Path"), None)
                date = kf.get_string("Trash Info", "DeletionDate")
            except GLib.Error:
                continue
            if path == original and (best is None or date > best[1]):
                best = (info, date)
    except OSError:
        return False
    if best is None or os.path.lexists(original):
        return False
    stored = os.path.join(trash, "files", best[0].name[: -len(".trashinfo")])
    try:
        os.makedirs(os.path.dirname(original), exist_ok=True)
        shutil.move(stored, original)
        os.remove(best[0].path)
        return True
    except OSError:
        return False


def describe(batch):
    counts = {}
    for _src, dst in batch["moves"]:
        folder = os.path.basename(os.path.dirname(dst))
        counts[folder] = counts.get(folder, 0) + 1
    parts = [f"{n} {folder_word(folder, n)}" for folder, n in counts.items()]
    if batch["trashed"]:
        n = len(batch["trashed"])
        parts.append(f"{n} old installer{'s' if n != 1 else ''} to the Trash")
    return ", ".join(parts)


def folder_word(folder, n):
    single = {"PDFs": "PDF", "Images": "image", "Documents": "document", "Installers": "installer",
              "Archives": "archive", "Videos": "video", "Music": "music file", "Other": "other file"}
    word = single.get(folder, folder)
    if n == 1:
        return word
    return {"PDF": "PDFs", "music file": "music files", "other file": "other files"}.get(word, word + "s")


# --------------------------------------------------------------------------
# notifications (with an Undo button)
# --------------------------------------------------------------------------

class Notifier:
    def __init__(self, on_undo):
        self.on_undo = on_undo
        self.last_id = 0
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self.bus.signal_subscribe("org.freedesktop.Notifications", "org.freedesktop.Notifications",
                                      "ActionInvoked", "/org/freedesktop/Notifications", None,
                                      Gio.DBusSignalFlags.NONE, self._action)
        except GLib.Error:
            self.bus = None

    def show(self, title, body, undo=True):
        if self.bus is None:
            return
        actions = ["undo", "Undo"] if undo else []
        hints = {"desktop-entry": GLib.Variant("s", "tidy-downloads")}
        try:
            result = self.bus.call_sync(
                "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications", "Notify",
                GLib.Variant("(susssasa{sv}i)", ("Tidy Downloads", 0, "folder-download", title, body,
                                                 actions, hints, 12000)),
                GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, 3000, None)
            self.last_id = result.unpack()[0]
        except GLib.Error:
            pass

    def _action(self, _bus, _sender, _path, _iface, _signal, params):
        note_id, action = params.unpack()
        if note_id == self.last_id and action == "undo":
            self.on_undo()


# --------------------------------------------------------------------------
# the window (menu entry): status, tidy now, undo, settings
# --------------------------------------------------------------------------

CSS = b"""
window.tidy.transparent { background-color: transparent; }
.shelf {
    background-color: @theme_bg_color;
    border: 2px solid alpha(@theme_fg_color, 0.18);
    border-radius: 10px;
}
.shelf-header { padding: 4px 4px 2px 10px; }
.shelf-title { font-weight: bold; }
.shelf-status { opacity: 0.7; font-size: small; }
.tidy-body { padding: 4px 12px 10px 12px; }
.tidy-count { font-weight: bold; }
.tidy-dim { opacity: 0.65; }
"""


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


class TidyWindow(Gtk.Window):
    ICONS = {"PDFs": "application-pdf", "Images": "image-x-generic", "Documents": "x-office-document",
             "Installers": "system-software-install", "Archives": "package-x-generic",
             "Videos": "video-x-generic", "Music": "audio-x-generic", "Other": "text-x-generic"}

    def __init__(self, app):
        super().__init__(title="Tidy Downloads")
        self.app = app
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_resizable(False)
        self.set_icon_name("folder-download")
        self.set_position(Gtk.WindowPosition.CENTER)
        self.get_style_context().add_class("tidy")
        screen = self.get_screen()
        if screen.get_rgba_visual() is not None and screen.is_composited():
            self.set_visual(screen.get_rgba_visual())
            self.get_style_context().add_class("transparent")
        self.connect("key-press-event", lambda _w, e: e.keyval == Gdk.KEY_Escape and self.hide())
        self.connect("delete-event", lambda *_: self.hide() or True)

        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        frame.get_style_context().add_class("shelf")
        self.add(frame)
        header_events = Gtk.EventBox()
        header_events.connect("button-press-event", lambda _w, e: e.button == 1 and self.begin_move_drag(
            e.button, int(e.x_root), int(e.y_root), e.time))
        header = Gtk.Box(spacing=2)
        header.get_style_context().add_class("shelf-header")
        header_events.add(header)
        frame.pack_start(header_events, False, False, 0)
        title = Gtk.Label(label="Tidy Downloads", xalign=0)
        title.get_style_context().add_class("shelf-title")
        header.pack_start(title, True, True, 0)
        opener = icon_button(["folder-open-symbolic", "folder-open"], "Open", "Open the Downloads folder")
        opener.connect("clicked", lambda *_: Gio.AppInfo.launch_default_for_uri(
            GLib.filename_to_uri(self.app.tidier.root), None))
        header.pack_start(opener, False, False, 0)
        close = icon_button(["window-close-symbolic", "window-close"], "✕", "Close (Esc)")
        close.connect("clicked", lambda *_: self.hide())
        header.pack_start(close, False, False, 0)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body.get_style_context().add_class("tidy-body")
        frame.pack_start(body, True, True, 0)
        self.where = Gtk.Label(xalign=0)
        self.where.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.where.get_style_context().add_class("tidy-dim")
        body.pack_start(self.where, False, False, 0)

        self.grid = Gtk.Grid(column_spacing=10, row_spacing=4)
        body.pack_start(self.grid, False, False, 0)

        self.status = Gtk.Label(xalign=0)
        self.status.set_line_wrap(True)
        self.status.set_max_width_chars(40)
        self.status.get_style_context().add_class("shelf-status")
        body.pack_start(self.status, False, False, 0)

        buttons = Gtk.Box(spacing=6)
        tidy = Gtk.Button(label="Tidy now")
        tidy.get_style_context().add_class("suggested-action")
        tidy.connect("clicked", lambda *_: self.app.tidy(ignore_wait=True, manual=True))
        buttons.pack_start(tidy, True, True, 0)
        self.undo_button = Gtk.Button(label="Undo last tidy")
        self.undo_button.connect("clicked", lambda *_: self.app.undo())
        buttons.pack_start(self.undo_button, True, True, 0)
        body.pack_start(buttons, False, False, 0)

        body.pack_start(Gtk.Separator(), False, False, 2)
        s = app.settings
        row = Gtk.Box(spacing=6)
        row.pack_start(Gtk.Label(label="Sort files", xalign=0), False, False, 0)
        wait = Gtk.SpinButton.new_with_range(0, 240, 5)
        wait.set_value(s["wait_minutes"])
        wait.connect("value-changed", lambda w: s.__setitem__("wait_minutes", w.get_value_as_int()))
        row.pack_start(wait, False, False, 0)
        row.pack_start(Gtk.Label(label="minutes after they arrive"), False, False, 0)
        body.pack_start(row, False, False, 0)
        row = Gtk.Box(spacing=6)
        clean = Gtk.CheckButton(label="Move installers to the Trash after")
        clean.set_active(s["clean_installers"])
        clean.connect("toggled", lambda w: s.__setitem__("clean_installers", w.get_active()))
        row.pack_start(clean, False, False, 0)
        days = Gtk.SpinButton.new_with_range(7, 365, 1)
        days.set_value(s["installer_days"])
        days.connect("value-changed", lambda w: s.__setitem__("installer_days", w.get_value_as_int()))
        row.pack_start(days, False, False, 0)
        row.pack_start(Gtk.Label(label="days unused"), False, False, 0)
        body.pack_start(row, False, False, 0)
        if os.path.exists(PIXEL_CAT):
            cat = Gtk.CheckButton(label="Pixel Cat sweeps when I tidy")
            cat.set_active(s["cat_sweeps"])
            cat.connect("toggled", lambda w: s.__setitem__("cat_sweeps", w.get_active()))
            body.pack_start(cat, False, False, 0)
        self.refresh()

    def refresh(self):
        tidier = self.app.tidier
        self.where.set_text(f"Watching {tidier.root.replace(os.path.expanduser('~'), '~', 1)}")
        for child in self.grid.get_children():
            self.grid.remove(child)
        counts = tidier.counts()
        for i, folder in enumerate(FOLDERS):
            col, row = (i % 2) * 3, i // 2
            self.grid.attach(Gtk.Image.new_from_icon_name(self.ICONS[folder], Gtk.IconSize.MENU), col, row, 1, 1)
            self.grid.attach(Gtk.Label(label=folder, xalign=0), col + 1, row, 1, 1)
            n = Gtk.Label(label=str(counts[folder]), xalign=1)
            n.get_style_context().add_class("tidy-count")
            n.set_margin_end(12)
            self.grid.attach(n, col + 2, row, 1, 1)
        self.grid.show_all()
        last = tidier.history[-1] if tidier.history else None
        if last:
            when = time.strftime("%H:%M", time.localtime(last["time"]))
            day = "" if time.strftime("%Y%m%d") == time.strftime("%Y%m%d", time.localtime(last["time"])) \
                else time.strftime("%b %d ", time.localtime(last["time"]))
            self.status.set_text(f"Last tidy: {day}{when} · {describe(last)}")
        else:
            self.status.set_text("Nothing tidied yet. New downloads are sorted "
                                 f"{self.app.settings['wait_minutes']} minutes after they arrive.")
        self.undo_button.set_sensitive(bool(tidier.history))


# --------------------------------------------------------------------------
# application
# --------------------------------------------------------------------------

class TidyApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.window = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.hold()
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.settings = Settings()
        self.tidier = Tidier(self.settings)
        self.notifier = Notifier(self.undo)
        GLib.timeout_add_seconds(2, lambda: self.tidy() and False)
        GLib.timeout_add_seconds(SCAN_SECONDS, lambda: self.tidy() or True)

    def do_command_line(self, command_line):
        args = command_line.get_arguments()[1:]
        if "--quit" in args:
            self.quit()
        elif "--now" in args:
            self.tidy(ignore_wait=True, manual=True)
        elif "--undo" in args:
            self.undo()
        elif "--window" in args:
            self.show_window()
        return 0

    def show_window(self):
        if self.window is None:
            self.window = TidyWindow(self)
            self.window.show_all()
        self.window.refresh()
        self.window.present()

    def tidy(self, ignore_wait=False, manual=False):
        batch = self.tidier.tidy(ignore_wait=ignore_wait)
        if batch is None:
            if manual:
                self.notifier.show("Downloads are already tidy", "Nothing to sort right now.", undo=False)
        else:
            n = len(batch["moves"])
            title = f"Tidied {n} file{'s' if n != 1 else ''}" if n else "Cleaned up old installers"
            self.notifier.show(title, describe(batch))
            if self.settings["cat_sweeps"] and os.path.exists(PIXEL_CAT):
                try:
                    subprocess.Popen([PIXEL_CAT, "--sweep", title + "!"], stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
                except OSError:
                    pass
        if self.window is not None:
            self.window.refresh()

    def undo(self):
        restored, problems = self.tidier.undo()
        if restored or problems:
            body = f"{restored} file{'s' if restored != 1 else ''} back where they were"
            if problems:
                body += f"; {problems} couldn't be moved back (renamed or deleted since)"
            self.notifier.show("Undone", body, undo=False)
        if self.window is not None:
            self.window.refresh()


def main():
    if "--help" in sys.argv[1:] or "-h" in sys.argv[1:]:
        print(__doc__)
        return 0
    return TidyApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
