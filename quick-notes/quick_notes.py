#!/usr/bin/env python3
"""Quick Notes - post-it notes that stay on top of your screen.

Usage:
    quick-notes             open a new note (restores saved notes on first start)
    quick-notes --new       open a new note at the mouse pointer
    quick-notes --restore   only bring back saved notes (used for autostart)
    quick-notes --show      bring all notes to the front
    quick-notes --quit      close the app (notes are kept)

Inside a note:
    Ctrl+B bold, Ctrl+I italic, Ctrl+T checkbox, Ctrl+N new note
"""

import json
import os
import sys
import uuid

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

try:
    gi.require_version("GdkX11", "3.0")
    from gi.repository import GdkX11  # noqa: E402
except (ValueError, ImportError):
    GdkX11 = None

APP_ID = "io.github.quicknotes.QuickNotes"
DATA_FILE = os.path.join(GLib.get_user_data_dir(), "quick-notes", "notes.json")
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
"""
    for name, (body, header) in COLORS.items():
        css += f"""
.note-{name} .note-frame {{ background-color: {body}; }}
.note-{name} .note-header {{ background-color: {header}; }}
.swatch-{name} {{ background-color: {body}; }}
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
# one note
# --------------------------------------------------------------------------

class Note(Gtk.Window):
    def __init__(self, app, data):
        super().__init__(application=app, title="Quick Note")
        self.app = app
        self.id = data.get("id") or uuid.uuid4().hex
        self.color = data.get("color") if data.get("color") in COLORS else DEFAULT_COLOR
        self.lock = bool(data.get("lock"))
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

        header.pack_start(Gtk.Box(), True, True, 0)   # spacer - drag here to move

        self.lock_button = flat_button("L", ("changes-prevent-symbolic", "system-lock-screen-symbolic"),
                                       "Show this note on the lock screen", toggle=True)
        self.lock_button.set_active(self.lock)
        self.lock_button.connect("toggled", self._on_lock_toggled)
        header.pack_start(self.lock_button, False, False, 0)

        delete_button = flat_button("✕", ("window-close-symbolic",), "Delete note")
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
        frame.pack_start(scroller, True, True, 0)

        # Footer: formatting + resize grip
        footer = Gtk.Box(spacing=0)
        footer.get_style_context().add_class("note-footer")
        frame.pack_start(footer, False, False, 0)

        self.bold_button = flat_button("<b>B</b>", (), "Bold (Ctrl+B)", toggle=True, markup=True)
        self.bold_id = self.bold_button.connect("toggled", lambda *_: self.toggle_style("bold"))
        footer.pack_start(self.bold_button, False, False, 0)

        self.italic_button = flat_button("<i>I</i>", (), "Italic (Ctrl+I)", toggle=True, markup=True)
        self.italic_id = self.italic_button.connect("toggled", lambda *_: self.toggle_style("italic"))
        footer.pack_start(self.italic_button, False, False, 0)

        box_button = flat_button(BOX_DONE, (), "Checkbox (Ctrl+T)")
        box_button.connect("clicked", lambda *_: self.toggle_checkbox_lines())
        footer.pack_start(box_button, False, False, 0)

        grip = Gtk.EventBox()
        grip_label = Gtk.Label(label="◢")
        grip_label.get_style_context().add_class("grip")
        grip.add(grip_label)
        grip.set_tooltip_text("Resize")
        grip.connect("button-press-event", self._on_grip_press)
        grip.connect("realize", lambda w: w.get_window().set_cursor(
            Gdk.Cursor.new_from_name(w.get_display(), "se-resize")))
        footer.pack_end(grip, False, False, 0)

        self.buffer.connect_after("insert-text", self._on_insert_text)
        self.buffer.connect("changed", self._on_changed)
        self.buffer.connect("mark-set", self._on_mark_set)

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
        return {"id": self.id, "x": x, "y": y, "w": w, "h": h,
                "color": self.color, "lock": self.lock, "runs": self._runs()}

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
        return self.buffer.get_text(self.buffer.get_start_iter(), self.buffer.get_end_iter(), False)

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

    def _on_text_click(self, view, event):
        if event.type != Gdk.EventType.BUTTON_PRESS or event.button != 1:
            return False
        bx, by = view.window_to_buffer_coords(Gtk.TextWindowType.TEXT, int(event.x), int(event.y))
        result = view.get_iter_at_location(bx, by)
        it = result[1] if isinstance(result, tuple) else result
        if isinstance(result, tuple) and not result[0]:
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
        if event.keyval not in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            return False
        if event.state & (Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.CONTROL_MASK):
            return False
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
        for item in data.get("notes", []):
            note = Note(self, item)
            self.notes.append(note)
            note.show_all()

    def do_shutdown(self):
        self.save_now()
        Gtk.Application.do_shutdown(self)

    def do_command_line(self, command_line):
        args = set(command_line.get_arguments()[1:])
        if "--help" in args or "-h" in args:
            command_line.print_(__doc__)
        elif "--quit" in args:
            self.quit()
        elif "--restore" in args:
            pass
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
        if note in self.notes:
            self.notes.remove(note)
        was_locked = note.lock
        note.destroy()
        self.save_soon()
        if was_locked:
            self.update_lock_screen_soon()

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
        data = {"version": 1, "last_color": self.last_color, "lock_original": self.lock_original,
                "notes": [n.to_dict() for n in self.notes]}
        try:
            os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
            tmp = DATA_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
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
            if note.lock:
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
    return QuickNotesApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
