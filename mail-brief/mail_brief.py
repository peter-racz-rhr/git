#!/usr/bin/env python3
"""Mail Brief - a small line on your desktop that tells you what's in your Gmail.

It checks your unread Gmail every hour, has an AI (Groq, free tier) write a
very short summary of each new email, marks what is important, needs an
action or contains a deadline, and pops up a notification when something new
arrives. Click the line to see the emails; click an email to open it in Gmail.

Usage:
    mail-brief            open the line
    mail-brief --check    check for new emails now
    mail-brief --toggle   show or hide the line
    mail-brief --quit     close it
    mail-brief --diagnose check the Gmail login, the search and the Groq key step by step
"""

import datetime
import email
import email.policy
import email.utils
import html
import html.parser
import imaplib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

APP_ID = "io.github.mailbrief.MailBrief"
CONFIG_DIR = os.path.join(GLib.get_user_config_dir(), "mail-brief")
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.ini")
SECRETS_FILE = os.path.join(CONFIG_DIR, "secrets.json")
DATA_DIR = os.path.join(GLib.get_user_data_dir(), "mail-brief")
CACHE_FILE = os.path.join(DATA_DIR, "cache.json")

IMAP_HOST = os.environ.get("MAIL_BRIEF_IMAP_HOST", "imap.gmail.com")
IMAP_PORT = int(os.environ.get("MAIL_BRIEF_IMAP_PORT", "993"))
IMAP_SSL = os.environ.get("MAIL_BRIEF_IMAP_SSL", "1") != "0"
GROQ_URL = os.environ.get("MAIL_BRIEF_GROQ_URL", "https://api.groq.com/openai/v1/chat/completions")

# Unread mail from the last week, without Gmail's Promotions / Social tabs (ads, social
# network notifications) - no AI credit is spent on those.
GMAIL_QUERY = "is:unread newer_than:7d -category:promotions -category:social"
MODELS = ["llama-3.3-70b-versatile", "openai/gpt-oss-120b", "llama-3.1-8b-instant"]
DEFAULT_MODEL = MODELS[0]
MAX_NEW_PER_CHECK = 30          # stay well inside Groq's free limits
MAX_BODY_CHARS = 6000           # very long emails are cut here before summarizing
USER_AGENT = "MailBrief/1.0 (personal Linux desktop widget)"

SYSTEM_PROMPT = """You sort and summarize emails for a high school student who also does \
Model United Nations (MUN). Today is {today}.

For the email you get, answer with a JSON object with exactly these keys:
- "important": true if it matters to the student personally: messages from teachers, the \
school, classmates or family, MUN organizers/chairs/delegates, anything that needs a reply \
or action, grades, schedule changes, deadlines. false for newsletters, marketing, \
automatic notifications that need nothing, and general announcements.
- "action": if the student has to do something, a very short imperative phrase in the \
email's language (e.g. "Reply to confirm attendance"); otherwise null.
- "deadline": the date by which something must be done or happens, as YYYY-MM-DD \
(resolve words like "Friday" or "tomorrow" using today's date); otherwise null.
- "deadline_note": a few words saying what the deadline is for, in the email's language; \
otherwise null.
- "summary": the summary, written in the SAME LANGUAGE as the email. Make it as short as \
possible WITHOUT losing information: keep every date, time, place, name, number, link \
purpose and request. One short sentence for simple emails, a few for dense ones. No \
greeting, no "This email says".

Answer with the JSON object only."""


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def run_async(work, done=None):
    def runner():
        try:
            result = work()
        except Exception as e:  # handed to done()
            result = e
        if done is not None:
            GLib.idle_add(lambda: done(result) and False)
    threading.Thread(target=runner, daemon=True).start()


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, data, private=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if private else 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


class _TextFromHtml(html.parser.HTMLParser):
    BLOCK = {"br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4", "table", "section", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head"):
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_to_text(markup):
    parser = _TextFromHtml()
    try:
        parser.feed(markup)
    except Exception:
        return re.sub(r"<[^>]+>", " ", markup)
    return html.unescape("".join(parser.parts))


QUOTE_START = re.compile(r"^(On .+ wrote:|.+ írta:|-----Original Message-----|_{10,}|From: .+)$", re.I)


def clean_body(text):
    lines = []
    for line in text.replace("\r", "").split("\n"):
        stripped = line.strip()
        if stripped.startswith(">"):
            continue
        if QUOTE_START.match(stripped) and lines:
            break                          # everything below is the quoted earlier conversation
        lines.append(re.sub(r"[ \t ]+", " ", stripped))
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def parse_message(raw):
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    subject = str(msg.get("subject") or "(no subject)").strip()
    name, address = email.utils.parseaddr(str(msg.get("from") or ""))
    try:
        when = email.utils.parsedate_to_datetime(str(msg.get("date"))).timestamp()
    except (TypeError, ValueError):
        when = time.time()
    body = ""
    try:
        part = msg.get_body(preferencelist=("plain", "html"))
        if part is not None:
            content = part.get_content()
            body = html_to_text(content) if part.get_content_type() == "text/html" else content
    except Exception:
        body = ""
    return {"subject": subject, "sender": name or address, "address": address,
            "date": when, "body": clean_body(body)}


# --------------------------------------------------------------------------
# Gmail over IMAP (app password)
# --------------------------------------------------------------------------

class Gmail:
    # Gmail may list the fields of a FETCH answer in any order, so look for each one separately.
    UID_RE = re.compile(rb"\bUID (\d+)")
    MSGID_RE = re.compile(rb"X-GM-MSGID (\d+)")
    THRID_RE = re.compile(rb"X-GM-THRID (\d+)")

    def __init__(self, address, app_password):
        self.address = address
        self.app_password = app_password.replace(" ", "")

    def _connect(self):
        if IMAP_SSL:
            conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=40)
        else:
            conn = imaplib.IMAP4(IMAP_HOST, IMAP_PORT, timeout=40)
        try:
            conn.login(self.address, self.app_password)
        except imaplib.IMAP4.error as e:
            raise PermissionError("Gmail refused the login. Check the address and the app password.") from e
        typ, _ = conn.select("INBOX")
        if typ != "OK":
            raise IOError("Could not open the inbox")
        return conn

    @classmethod
    def _ids(cls, header):
        uid, msgid, thread = cls.UID_RE.search(header), cls.MSGID_RE.search(header), cls.THRID_RE.search(header)
        if not (uid and msgid and thread):
            return None
        return uid.group(1).decode(), msgid.group(1).decode(), int(thread.group(1))

    def unread(self, known):
        """Returns (list of all unread message ids, dict of newly downloaded messages)."""
        conn = self._connect()
        try:
            typ, data = conn.uid("SEARCH", "X-GM-RAW", f'"{GMAIL_QUERY}"')
            uids = data[0].split() if typ == "OK" and data and data[0] else []
            ids = {}                                    # msgid -> (uid, thread id)
            for start in range(0, len(uids), 200):
                chunk = b",".join(uids[start:start + 200]).decode()
                typ, data = conn.uid("FETCH", chunk, "(X-GM-MSGID X-GM-THRID)")
                for item in data or []:
                    header = item[0] if isinstance(item, tuple) else item
                    parsed = self._ids(header or b"")
                    if parsed:
                        uid, msgid, thread = parsed
                        ids[msgid] = (uid, thread)
            new = [m for m in ids if m not in known]
            # newest first (higher UID = newer); the rest waits for the next check
            new.sort(key=lambda m: int(ids[m][0]), reverse=True)
            downloaded = {}
            for msgid in new[:MAX_NEW_PER_CHECK]:
                uid, thread = ids[msgid]
                typ, data = conn.uid("FETCH", uid, "(BODY.PEEK[])")   # PEEK: stays unread
                raw = next((item[1] for item in data or [] if isinstance(item, tuple)), None)
                if raw:
                    info = parse_message(raw)
                    info["thread"] = thread
                    downloaded[msgid] = info
            return list(ids), downloaded
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    def mark_read(self, msgid):
        conn = self._connect()
        try:
            typ, data = conn.uid("SEARCH", "X-GM-MSGID", msgid)
            for uid in (data[0].split() if typ == "OK" and data and data[0] else []):
                conn.uid("STORE", uid.decode(), "+FLAGS", "(\\Seen)")
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    def link(self, thread):
        return (f"https://mail.google.com/mail/?authuser={urllib.parse.quote(self.address)}"
                f"#all/{int(thread):x}")


# --------------------------------------------------------------------------
# Groq (free tier) summaries
# --------------------------------------------------------------------------

class Summarizer:
    def __init__(self, api_key, model):
        self.api_key = api_key
        self.model = model or DEFAULT_MODEL

    def summarize(self, info):
        body = info["body"]
        cut = len(body) > MAX_BODY_CHARS
        body = body[:MAX_BODY_CHARS] + ("\n[... the rest of this long email was cut off]" if cut else "")
        text = (f"From: {info['sender']} <{info['address']}>\n"
                f"Date: {datetime.datetime.fromtimestamp(info['date']).strftime('%Y-%m-%d %H:%M')}\n"
                f"Subject: {info['subject']}\n\n{body or '(empty)'}")
        payload = {
            "model": self.model,
            "temperature": 0.2,
            "max_tokens": 500,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT.format(today=datetime.date.today().isoformat())},
                {"role": "user", "content": text},
            ],
        }
        for attempt in range(6):
            request = urllib.request.Request(
                GROQ_URL, data=json.dumps(payload).encode(), method="POST",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
                         "User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    answer = json.loads(response.read())
                break
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    raise PermissionError("Groq refused the API key. Check it in Settings.") from e
                if e.code == 429 and attempt < 5:     # free-tier rate limit: wait and retry
                    wait = float(e.headers.get("retry-after") or 10 * (attempt + 1))
                    time.sleep(min(90.0, max(1.0, wait)))
                    continue
                if e.code >= 500 and attempt < 5:
                    time.sleep(5 * (attempt + 1))
                    continue
                raise IOError(f"Groq error {e.code}") from e
        content = answer["choices"][0]["message"]["content"]
        try:
            result = json.loads(content)
        except ValueError:
            match = re.search(r"\{.*\}", content, re.S)
            result = json.loads(match.group(0)) if match else {}
        deadline = result.get("deadline")
        try:
            deadline = datetime.date.fromisoformat(str(deadline)).isoformat() if deadline else None
        except ValueError:
            deadline = None
        return {"important": bool(result.get("important")),
                "action": (result.get("action") or None),
                "deadline": deadline,
                "deadline_note": result.get("deadline_note") or None,
                "summary": (result.get("summary") or "").strip() or "(no summary)",
                "cut": cut}


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------

class Settings:
    GROUP = "mail-brief"

    def __init__(self):
        self.kf = GLib.KeyFile()
        try:
            self.kf.load_from_file(SETTINGS_FILE, GLib.KeyFileFlags.KEEP_COMMENTS)
        except GLib.Error:
            pass
        self.secrets = load_json(SECRETS_FILE, {})

    def get(self, key, default=None):
        try:
            return self.kf.get_string(self.GROUP, key)
        except GLib.Error:
            return default

    def get_int(self, key, default=0):
        try:
            return self.kf.get_integer(self.GROUP, key)
        except GLib.Error:
            return default

    def get_bool(self, key, default=False):
        try:
            return self.kf.get_boolean(self.GROUP, key)
        except GLib.Error:
            return default

    def set(self, key, value):
        if isinstance(value, bool):
            self.kf.set_boolean(self.GROUP, key, value)
        elif isinstance(value, int):
            self.kf.set_integer(self.GROUP, key, value)
        else:
            self.kf.set_string(self.GROUP, key, str(value))
        os.makedirs(CONFIG_DIR, exist_ok=True)
        self.kf.save_to_file(SETTINGS_FILE)

    def set_secret(self, key, value):
        self.secrets[key] = value
        save_json(SECRETS_FILE, self.secrets, private=True)

    @property
    def ready(self):
        return bool(self.get("address") and self.secrets.get("app_password") and self.secrets.get("groq_key"))


# --------------------------------------------------------------------------
# the window: a small line that opens into a list
# --------------------------------------------------------------------------

CSS = b"""
window.mb { background-color: transparent; }
.mb-frame {
    background-color: @theme_bg_color;
    border: 1px solid alpha(@theme_fg_color, 0.18);
    border-radius: 10px;
}
.mb-frame.mb-new { border-color: @theme_selected_bg_color; box-shadow: inset 0 0 0 1px @theme_selected_bg_color; }
.mb-bar { padding: 3px 4px 3px 10px; }
.mb-count { font-weight: bold; }
.mb-dim { opacity: 0.65; font-size: small; }
.mb-chip {
    background-color: #d83b3b; color: #ffffff; border-radius: 4px;
    padding: 0 5px; font-size: x-small; font-weight: bold;
}
.mb-chip-important { background-color: @theme_selected_bg_color; color: @theme_selected_fg_color; }
.mb-action { color: #e0892b; font-size: small; font-weight: bold; }
.mb-frame button { padding: 2px 5px; min-height: 0; min-width: 0; }
.mb-frame row { padding: 6px 8px; border-bottom: 1px solid alpha(@theme_fg_color, 0.08); }
.mb-frame list { background-color: transparent; }
.mb-subject { font-weight: bold; }
.mb-row-important .mb-subject { font-size: larger; }
"""


def small_button(icons, fallback, tooltip, toggle=False):
    button = Gtk.ToggleButton() if toggle else Gtk.Button()
    theme = Gtk.IconTheme.get_default()
    name = next((n for n in icons if theme.has_icon(n)), None)
    if name:
        button.set_image(Gtk.Image.new_from_icon_name(name, Gtk.IconSize.MENU))
    else:
        button.set_label(fallback)
    button.set_relief(Gtk.ReliefStyle.NONE)
    button.set_tooltip_text(tooltip)
    button.set_can_focus(False)
    button.set_valign(Gtk.Align.CENTER)
    return button


def chip(text, css="mb-chip", extra=None):
    label = Gtk.Label(label=text)
    label.get_style_context().add_class("mb-chip")
    if extra:
        label.get_style_context().add_class(extra)
    label.set_valign(Gtk.Align.CENTER)
    return label


def friendly_date(ts):
    when = datetime.datetime.fromtimestamp(ts)
    today = datetime.date.today()
    if when.date() == today:
        return when.strftime("%H:%M")
    if when.date() == today - datetime.timedelta(days=1):
        return "yesterday " + when.strftime("%H:%M")
    return when.strftime("%b %d")


def deadline_text(iso):
    day = datetime.date.fromisoformat(iso)
    diff = (day - datetime.date.today()).days
    when = day.strftime("%b %d")
    if diff < 0:
        return f"{when} (passed)"
    if diff == 0:
        return f"{when} (today!)"
    if diff == 1:
        return f"{when} (tomorrow)"
    return f"{when} (in {diff} days)"


class MailWindow(Gtk.ApplicationWindow):
    WIDTH = 400
    LIST_HEIGHT = 440

    def __init__(self, app):
        super().__init__(application=app, title="Mail Brief")
        self.app = app
        self.settings = app.settings
        self.cache = load_json(CACHE_FILE, {})
        self.current = [m for m, v in self.cache.items() if v.get("unread")]
        self.checking = False
        self.status = ""
        self.expanded = False
        self._press = None
        self._flash_id = 0

        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_keep_above(self.settings.get_bool("pinned", False))
        self.stick()
        self.get_style_context().add_class("mb")
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None and screen.is_composited():
            self.set_visual(visual)

        self._build()
        self.set_default_size(self.WIDTH, -1)
        x, y = self.settings.get_int("x", -1), self.settings.get_int("y", -1)
        if x >= 0 and y >= 0:
            self.move(x, y)
        else:
            area = self.get_display().get_primary_monitor() or self.get_display().get_monitor(0)
            geo = area.get_workarea()
            self.move(geo.x + geo.width - self.WIDTH - 20, geo.y + 20)
        self.connect("configure-event", self._on_configure)
        self.connect("delete-event", lambda *_: self.hide() or True)
        self.refresh_ui()

        interval = max(10, self.settings.get_int("interval_minutes", 60))
        GLib.timeout_add_seconds(interval * 60, self._periodic)
        GLib.timeout_add_seconds(60, self._minute_tick)
        GLib.timeout_add_seconds(3, lambda: self.check() and False)

    # ---------------------------------------------------------------- UI
    def _build(self):
        self.frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.frame.get_style_context().add_class("mb-frame")
        self.add(self.frame)

        bar_events = Gtk.EventBox()
        bar_events.add_events(Gdk.EventMask.POINTER_MOTION_MASK)
        bar_events.connect("button-press-event", self._bar_press)
        bar_events.connect("motion-notify-event", self._bar_motion)
        bar_events.connect("button-release-event", self._bar_release)
        bar = Gtk.Box(spacing=6)
        bar.get_style_context().add_class("mb-bar")
        bar_events.add(bar)
        self.frame.pack_start(bar_events, False, False, 0)

        theme = Gtk.IconTheme.get_default()
        icon = next((n for n in ("mail-unread-symbolic", "mail-unread", "mail-message-new")
                     if theme.has_icon(n)), None)
        if icon:
            bar.pack_start(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.MENU), False, False, 0)
        self.count_label = Gtk.Label(xalign=0)
        self.count_label.get_style_context().add_class("mb-count")
        bar.pack_start(self.count_label, False, False, 0)
        self.deadline_chip = chip("")
        self.deadline_chip.set_no_show_all(True)
        bar.pack_start(self.deadline_chip, False, False, 0)
        self.status_label = Gtk.Label(xalign=0)
        self.status_label.get_style_context().add_class("mb-dim")
        self.status_label.set_ellipsize(Pango.EllipsizeMode.END)
        bar.pack_start(self.status_label, True, True, 0)
        self.spinner = Gtk.Spinner()
        self.spinner.set_no_show_all(True)
        bar.pack_start(self.spinner, False, False, 0)

        refresh = small_button(["view-refresh-symbolic"], "↻", "Check now")
        refresh.connect("clicked", lambda *_: self.check())
        bar.pack_start(refresh, False, False, 0)
        self.pin_button = small_button(["view-pin-symbolic"], "pin", "Keep above other windows", toggle=True)
        self.pin_button.set_active(self.settings.get_bool("pinned", False))
        self.pin_button.connect("toggled", self._on_pin)
        bar.pack_start(self.pin_button, False, False, 0)
        menu_button = small_button(["open-menu-symbolic"], "☰", "Menu")
        menu_button.connect("clicked", self._show_menu)
        bar.pack_start(menu_button, False, False, 0)
        self.expand_button = small_button(["pan-down-symbolic", "go-down-symbolic"], "▾", "Show emails")
        self.expand_button.connect("clicked", lambda *_: self.toggle_expand())
        bar.pack_start(self.expand_button, False, False, 0)

        self.revealer = Gtk.Revealer()
        self.revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.revealer.set_transition_duration(160)
        self.revealer.connect("notify::child-revealed", self._on_revealed)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_size_request(self.WIDTH, self.LIST_HEIGHT)
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.listbox.connect("row-activated", self._on_row)
        self.placeholder = Gtk.Label(label="No unread emails")
        self.placeholder.get_style_context().add_class("mb-dim")
        self.placeholder.set_margin_top(30)
        self.placeholder.show()
        self.listbox.set_placeholder(self.placeholder)
        scroller.add(self.listbox)
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        panel.pack_start(scroller, True, True, 0)
        self.footer = Gtk.Label(xalign=0)
        self.footer.get_style_context().add_class("mb-dim")
        self.footer.set_margin_start(10)
        self.footer.set_margin_bottom(4)
        self.footer.set_margin_top(2)
        self.footer.set_ellipsize(Pango.EllipsizeMode.END)
        panel.pack_start(self.footer, False, False, 0)
        self.revealer.add(panel)
        self.frame.pack_start(self.revealer, True, True, 0)

    def _visible_items(self):
        items = []
        for msgid in self.current:
            entry = self.cache.get(msgid)
            if not entry or entry.get("done") or not entry.get("summary"):
                continue
            items.append((msgid, entry))

        def order(pair):
            entry = pair[1]
            return (not entry.get("important"),
                    entry.get("deadline") or "9999-12-31",
                    -entry.get("date", 0))
        items.sort(key=order)
        return items

    def refresh_ui(self):
        items = self._visible_items()
        pending = sum(1 for m in self.current
                      if m in self.cache and not self.cache[m].get("summary") and not self.cache[m].get("done"))
        important = sum(1 for _m, e in items if e.get("important"))
        deadlines = sum(1 for _m, e in items if e.get("deadline"))
        total = len(items) + pending
        if not self.settings.ready:
            self.count_label.set_text("Mail Brief")
            self.status_label.set_text("click the menu > Settings to connect Gmail")
        else:
            text = f"{total} new" if total else "No new emails"
            if important:
                text += f" · {important} important"
            self.count_label.set_text(text)
        self.deadline_chip.set_text("DEADLINE" if deadlines == 1 else f"{deadlines} DEADLINES")
        self.deadline_chip.set_visible(deadlines > 0)

        for child in self.listbox.get_children():
            self.listbox.remove(child)
        for msgid, entry in items:
            self.listbox.add(self._make_row(msgid, entry))
        self.listbox.show_all()

    def _make_row(self, msgid, entry):
        row = Gtk.ListBoxRow()
        row.msgid = msgid
        row.set_tooltip_text("Open in Gmail")
        if entry.get("important"):
            row.get_style_context().add_class("mb-row-important")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        row.add(box)

        top = Gtk.Box(spacing=6)
        if entry.get("deadline"):
            top.pack_start(chip("DEADLINE"), False, False, 0)
        if entry.get("important"):
            top.pack_start(chip("IMPORTANT", extra="mb-chip-important"), False, False, 0)
        subject = Gtk.Label(label=entry["subject"], xalign=0)
        subject.get_style_context().add_class("mb-subject")
        subject.set_ellipsize(Pango.EllipsizeMode.END)
        top.pack_start(subject, True, True, 0)
        box.pack_start(top, False, False, 0)

        meta = Gtk.Label(label=f"{entry['sender']} · {friendly_date(entry['date'])}", xalign=0)
        meta.get_style_context().add_class("mb-dim")
        meta.set_ellipsize(Pango.EllipsizeMode.END)
        box.pack_start(meta, False, False, 0)

        summary = Gtk.Label(label=entry["summary"], xalign=0)
        summary.set_line_wrap(True)
        summary.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        summary.set_max_width_chars(40)
        box.pack_start(summary, False, False, 0)

        if entry.get("action"):
            action = Gtk.Label(label=f"To do: {entry['action']}", xalign=0)
            action.get_style_context().add_class("mb-action")
            action.set_line_wrap(True)
            box.pack_start(action, False, False, 0)
        if entry.get("deadline"):
            note = entry.get("deadline_note")
            text = f"Deadline: {deadline_text(entry['deadline'])}" + (f" – {note}" if note else "")
            dl = Gtk.Label(label=text, xalign=0)
            dl.get_style_context().add_class("mb-action")
            dl.set_line_wrap(True)
            box.pack_start(dl, False, False, 0)
        if entry.get("cut"):
            cut = Gtk.Label(label="(long email: only the first part was summarized)", xalign=0)
            cut.get_style_context().add_class("mb-dim")
            box.pack_start(cut, False, False, 0)

        buttons = Gtk.Box(spacing=4)
        buttons.set_halign(Gtk.Align.END)
        open_button = Gtk.Button(label="Open")
        open_button.set_tooltip_text("Open this email in Gmail")
        open_button.connect("clicked", lambda *_: self.open_mail(msgid))
        read_button = Gtk.Button(label="Mark read")
        read_button.set_tooltip_text("Mark as read in Gmail and remove it from here")
        read_button.connect("clicked", lambda *_: self.mark_read(msgid))
        done_button = Gtk.Button(label="Done")
        done_button.set_tooltip_text("Hide it here (it stays unread in Gmail)")
        done_button.connect("clicked", lambda *_: self.mark_done(msgid))
        for b in (open_button, read_button, done_button):
            b.set_can_focus(False)
            buttons.pack_start(b, False, False, 0)
        box.pack_start(buttons, False, False, 0)
        return row

    # ---------------------------------------------------------------- expand / move
    def toggle_expand(self, expand=None):
        self.expanded = (not self.expanded) if expand is None else expand
        self.revealer.set_reveal_child(self.expanded)
        icons = ["pan-up-symbolic", "go-up-symbolic"] if self.expanded else ["pan-down-symbolic", "go-down-symbolic"]
        theme = Gtk.IconTheme.get_default()
        name = next((n for n in icons if theme.has_icon(n)), None)
        if name:
            self.expand_button.set_image(Gtk.Image.new_from_icon_name(name, Gtk.IconSize.MENU))
        self.expand_button.set_tooltip_text("Hide emails" if self.expanded else "Show emails")

    def _on_revealed(self, *_):
        if not self.revealer.get_child_revealed():
            self.resize(self.WIDTH, 1)       # shrink back to the single line

    def _bar_press(self, _widget, event):
        if event.button == 1 and event.type == Gdk.EventType.BUTTON_PRESS:
            self._press = (event.x_root, event.y_root, event.time)
        return False

    def _bar_motion(self, _widget, event):
        if self._press and (abs(event.x_root - self._press[0]) > 4 or abs(event.y_root - self._press[1]) > 4):
            x, y, t = self._press
            self._press = None
            self.begin_move_drag(1, int(x), int(y), t)
        return False

    def _bar_release(self, _widget, event):
        if self._press and event.button == 1:
            self._press = None
            self.toggle_expand()          # a click (not a drag) opens / closes the list
        return False

    def _on_configure(self, *_):
        if getattr(self, "_geom_id", 0):
            GLib.source_remove(self._geom_id)

        def save():
            self._geom_id = 0
            x, y = self.get_position()
            self.settings.kf.set_integer(Settings.GROUP, "x", x)
            self.settings.set("y", y)
            return False
        self._geom_id = GLib.timeout_add(800, save)
        return False

    def _on_pin(self, button):
        self.settings.set("pinned", button.get_active())
        self.set_keep_above(button.get_active())

    def _show_menu(self, anchor):
        menu = Gtk.Menu()
        for label, callback in (("Check now", self.check), ("Settings…", self.open_settings),
                                ("Show hidden (done) emails again", self.unhide_all), ("Quit", self.app.quit)):
            item = Gtk.MenuItem(label=label)
            item.connect("activate", lambda _i, cb=callback: cb())
            menu.append(item)
        menu.show_all()
        menu.attach_to_widget(anchor, None)
        menu.popup_at_widget(anchor, Gdk.Gravity.SOUTH_EAST, Gdk.Gravity.NORTH_EAST, None)

    # ---------------------------------------------------------------- actions
    def gmail(self):
        return Gmail(self.settings.get("address", ""), self.settings.secrets.get("app_password", ""))

    def open_mail(self, msgid):
        entry = self.cache.get(msgid, {})
        url = self.gmail().link(entry.get("thread", 0))
        try:
            Gio.AppInfo.launch_default_for_uri(url, None)
        except GLib.Error as e:
            self.set_status(f"could not open the browser: {e.message}", in_line=True)
            print(f"mail-brief: open {url} failed: {e.message}", file=sys.stderr)

    def _on_row(self, _list, row):
        self.open_mail(row.msgid)

    def mark_read(self, msgid):
        entry = self.cache.get(msgid)
        if entry:
            entry["unread"] = False
        if msgid in self.current:
            self.current.remove(msgid)
        self.save_cache()
        self.refresh_ui()

        def done(result):
            if isinstance(result, Exception):
                self.set_status(f"could not mark as read: {result}", in_line=True)
        run_async(lambda: self.gmail().mark_read(msgid), done)

    def mark_done(self, msgid):
        if msgid in self.cache:
            self.cache[msgid]["done"] = True
            self.save_cache()
        self.refresh_ui()

    def unhide_all(self):
        for entry in self.cache.values():
            entry["done"] = False
        self.save_cache()
        self.refresh_ui()

    def set_status(self, text, in_line=False):
        """Status goes to the footer of the list; only progress and errors show in the line."""
        self.status = text
        self.footer.set_text(text)
        self.status_label.set_text(text if in_line else "")
        self.status_label.set_tooltip_text(text if in_line else None)

    def save_cache(self):
        # forget emails that are read and older than a month
        cutoff = time.time() - 31 * 86400
        for msgid in [m for m, e in self.cache.items() if not e.get("unread") and e.get("date", 0) < cutoff]:
            del self.cache[msgid]
        try:
            save_json(CACHE_FILE, self.cache, private=True)
        except OSError as e:
            print(f"mail-brief: could not save: {e}", file=sys.stderr)

    # ---------------------------------------------------------------- checking
    def _periodic(self):
        self.check()
        return True

    def _minute_tick(self):
        self.refresh_ui()        # keeps "today / tomorrow" and times fresh
        return True

    def check(self):
        if self.checking or not self.settings.ready:
            self.refresh_ui()
            return
        self.checking = True
        self.spinner.show()
        self.spinner.start()
        self.set_status("checking…", in_line=True)
        gmail = self.gmail()
        summarizer = Summarizer(self.settings.secrets.get("groq_key", ""), self.settings.get("model", DEFAULT_MODEL))
        known = {m for m, e in self.cache.items() if e.get("summary")}

        def progress(text):
            GLib.idle_add(lambda: self.set_status(text, in_line=True) and False)

        def work():
            unread, downloaded = gmail.unread(known)
            summaries = {}
            errors = []
            for i, (msgid, info) in enumerate(downloaded.items(), 1):
                progress(f"summarizing {i}/{len(downloaded)}…")
                try:
                    summaries[msgid] = {**info, **summarizer.summarize(info)}
                except PermissionError:
                    raise
                except Exception as e:     # keep going; this one is retried next time
                    errors.append(str(e))
            return unread, summaries, errors

        def done(result):
            self.checking = False
            self.spinner.stop()
            self.spinner.hide()
            if isinstance(result, Exception):
                self.set_status(str(result) if isinstance(result, PermissionError) else f"check failed: {result}",
                                in_line=True)
                print(f"mail-brief: {result}", file=sys.stderr)
                self.refresh_ui()
                return
            unread, summaries, errors = result
            unread_set = set(unread)
            for msgid, entry in self.cache.items():
                entry["unread"] = msgid in unread_set
            fresh = []
            for msgid, entry in summaries.items():
                entry.pop("body", None)          # the text itself is not stored
                entry["unread"] = True
                entry["done"] = False
                self.cache[msgid] = entry
                fresh.append(msgid)
            self.current = [m for m in unread if m in self.cache]
            self.save_cache()
            stamp = datetime.datetime.now().strftime("%H:%M")
            waiting = len([m for m in unread if m not in self.cache])
            status = f"checked {stamp}"
            if waiting:
                status += f" · {waiting} more next time"
            if errors:
                status += f" · {len(errors)} failed"
            minutes = max(10, self.settings.get_int("interval_minutes", 60))
            status += f" · next check in {minutes} min"
            self.set_status(status)
            self.refresh_ui()
            if fresh:
                self.announce(fresh)
        run_async(work, done)

    def announce(self, fresh):
        """New mail: highlight the line, show it if hidden, and send a notification."""
        entries = [self.cache[m] for m in fresh if m in self.cache]
        if not entries:
            return
        if not self.get_visible():
            self.show_all()
        ctx = self.frame.get_style_context()
        ctx.add_class("mb-new")
        if self._flash_id:
            GLib.source_remove(self._flash_id)
        self._flash_id = GLib.timeout_add_seconds(20, lambda: ctx.remove_class("mb-new") or
                                                  setattr(self, "_flash_id", 0) or False)
        important = [e for e in entries if e.get("important")]
        title = f"{len(entries)} new email" + ("s" if len(entries) != 1 else "")
        if important:
            title += f" · {len(important)} important"
        lines = []
        for e in (important or entries)[:4]:
            flag = "DEADLINE: " if e.get("deadline") else ""
            lines.append(f"{flag}{e['subject']} – {e['sender']}")
        notify(title, "\n".join(lines), urgent=bool(important))

    # ---------------------------------------------------------------- settings dialog
    def open_settings(self):
        dialog = Gtk.Dialog(title="Mail Brief settings", transient_for=self, modal=True)
        dialog.set_keep_above(True)
        dialog.set_default_size(460, -1)
        area = dialog.get_content_area()
        area.set_border_width(14)
        area.set_spacing(8)

        intro = Gtk.Label(xalign=0)
        intro.set_line_wrap(True)
        intro.set_max_width_chars(58)
        intro.set_markup(
            "<b>Gmail</b>: your address and an <b>app password</b> (Google Account > Security > "
            "2-Step Verification > App passwords). It is a 16-letter code, not your normal password.\n"
            "<b>Groq</b>: a free API key from <a href='https://console.groq.com/keys'>console.groq.com/keys</a>.")
        area.pack_start(intro, False, False, 0)

        grid = Gtk.Grid(column_spacing=10, row_spacing=6)
        area.pack_start(grid, False, False, 4)

        def field(row, label, value, secret=False):
            grid.attach(Gtk.Label(label=label, xalign=1), 0, row, 1, 1)
            entry = Gtk.Entry()
            entry.set_text(value or "")
            entry.set_hexpand(True)
            if secret:
                entry.set_visibility(False)
            grid.attach(entry, 1, row, 1, 1)
            return entry

        address = field(0, "Gmail address", self.settings.get("address", ""))
        password = field(1, "App password", self.settings.secrets.get("app_password", ""), secret=True)
        key = field(2, "Groq API key", self.settings.secrets.get("groq_key", ""), secret=True)
        grid.attach(Gtk.Label(label="AI model", xalign=1), 0, 3, 1, 1)
        model = Gtk.ComboBoxText()
        current_model = self.settings.get("model", DEFAULT_MODEL)
        for i, name in enumerate(MODELS):
            model.append_text(name)
            if name == current_model:
                model.set_active(i)
        if model.get_active() < 0:
            model.set_active(0)
        grid.attach(model, 1, 3, 1, 1)
        grid.attach(Gtk.Label(label="Check every (minutes)", xalign=1), 0, 4, 1, 1)
        interval = Gtk.SpinButton.new_with_range(10, 720, 10)
        interval.set_value(self.settings.get_int("interval_minutes", 60) or 60)
        grid.attach(interval, 1, 4, 1, 1)

        note = Gtk.Label(xalign=0)
        note.set_line_wrap(True)
        note.set_max_width_chars(58)
        note.get_style_context().add_class("mb-dim")
        note.set_text("The password and key are stored in ~/.config/mail-brief/secrets.json, "
                      "readable only by you. Email text goes to Groq for the summary; "
                      "Groq does not train on it.")
        area.pack_start(note, False, False, 0)

        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Save & check now", Gtk.ResponseType.OK)
        dialog.show_all()
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            old_minutes = self.settings.get_int("interval_minutes", 60)
            self.settings.set("address", address.get_text().strip())
            self.settings.set("model", model.get_active_text() or DEFAULT_MODEL)
            self.settings.set("interval_minutes", int(interval.get_value()))
            self.settings.set_secret("app_password", password.get_text().strip())
            self.settings.set_secret("groq_key", key.get_text().strip())
            if int(interval.get_value()) != old_minutes:
                self.set_status("new check interval applies after a restart")
            self.check()
        dialog.destroy()


def notify(title, body, urgent=False):
    """Desktop notification through the standard notification service."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        hints = {"urgency": GLib.Variant("y", 2 if urgent else 1),
                 "desktop-entry": GLib.Variant("s", "mail-brief")}
        bus.call_sync("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                      "org.freedesktop.Notifications", "Notify",
                      GLib.Variant("(susssasa{sv}i)", ("Mail Brief", 0, "mail-unread", title, body,
                                                       [], hints, 15000 if not urgent else 0)),
                      None, Gio.DBusCallFlags.NONE, 3000, None)
    except GLib.Error as e:
        print(f"mail-brief: notification failed: {e.message}", file=sys.stderr)


# --------------------------------------------------------------------------
# application
# --------------------------------------------------------------------------

class MailBriefApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.window = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.hold()
        self.settings = Settings()
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.window = MailWindow(self)

    def do_command_line(self, command_line):
        args = set(command_line.get_arguments()[1:])
        if "--quit" in args:
            self.quit()
        elif "--check" in args:
            self.window.show_all()
            self.window.check()
        elif "--toggle" in args and self.window.get_visible():
            self.window.hide()
        else:
            self.window.show_all()
            self.window.present()
            if not self.settings.ready:
                GLib.idle_add(lambda: self.window.open_settings() and False)
        return 0


def diagnose():
    """Step-by-step check of the Gmail login, the search and the Groq key (prints, no window)."""
    settings = Settings()
    address = settings.get("address", "")
    print(f"1. Settings: address={'set' if address else 'MISSING'}, "
          f"app password={'set' if settings.secrets.get('app_password') else 'MISSING'}, "
          f"Groq key={'set' if settings.secrets.get('groq_key') else 'MISSING'}")
    gmail = Gmail(address, settings.secrets.get("app_password", ""))
    try:
        conn = gmail._connect()
    except Exception as e:
        print(f"2. Gmail login: FAILED - {e}")
        return 1
    print("2. Gmail login: ok")
    try:
        try:
            typ, data = conn.uid("SEARCH", "X-GM-RAW", f'"{GMAIL_QUERY}"')
            uids = data[0].split() if typ == "OK" and data and data[0] else []
            print(f"3. Unread emails found by the search: {len(uids)}  ({typ})")
        except Exception as e:
            print(f"3. Search: FAILED - {e}")
            uids = []
        try:
            typ, data = conn.uid("SEARCH", "UNSEEN")
            print(f"   (all unread in the inbox, without filters: {len(data[0].split()) if data and data[0] else 0})")
        except Exception as e:
            print(f"   (could not count all unread: {e})")
        if uids:
            typ, data = conn.uid("FETCH", uids[-1].decode(), "(X-GM-MSGID X-GM-THRID)")
            header = data[0][0] if data and isinstance(data[0], tuple) else (data[0] if data else b"")
            print(f"4. Answer for the newest one: {header!r}")
            print(f"   understood as: {Gmail._ids(header or b'')}")
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    try:
        result = Summarizer(settings.secrets.get("groq_key", ""), settings.get("model", DEFAULT_MODEL)).summarize(
            {"sender": "Test", "address": "test@example.com", "date": time.time(), "subject": "Test",
             "body": "Hi! The math test moves to next Monday. Please bring a calculator."})
        print(f"5. Groq: ok - {result['summary']}")
    except Exception as e:
        print(f"5. Groq: FAILED - {e}")
        return 1
    return 0


def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        return 0
    if "--diagnose" in sys.argv:
        return diagnose()
    return MailBriefApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
