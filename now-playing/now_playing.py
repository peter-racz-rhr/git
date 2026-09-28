#!/usr/bin/env python3
"""Now Playing - an always-on-top Spotify widget for Linux.

Shows the album cover (as a picture or old-school ASCII art), the controls,
a progress bar, the upcoming songs in the queue and lyrics that type
themselves in a little terminal.

Usage:
    now-playing             open the widget
    now-playing --toggle    show or hide it (handy for a keyboard shortcut)
    now-playing --quit      close it
    now-playing --debug-queue   print what Spotify reports as the queue

Playback control uses the Spotify desktop app over D-Bus (MPRIS). The queue,
shuffle, repeat, volume and seeking use the Spotify Web API, which needs a
one-time login with your own Spotify developer app (menu > Connect Spotify account).
Lyrics come from lrclib.net.
"""

import base64
import colorsys
import hashlib
import http.server
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango, PangoCairo  # noqa: E402

APP_ID = "io.github.nowplaying.NowPlaying"
CONFIG_DIR = os.path.join(GLib.get_user_config_dir(), "now-playing")
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.ini")
TOKEN_FILE = os.path.join(CONFIG_DIR, "token.json")
CACHE_DIR = os.path.join(GLib.get_user_cache_dir(), "now-playing")

MPRIS_NAME = os.environ.get("NOW_PLAYING_MPRIS", "org.mpris.MediaPlayer2.spotify")
MPRIS_PATH = "/org/mpris/MediaPlayer2"
MPRIS_PLAYER = "org.mpris.MediaPlayer2.Player"

API = os.environ.get("NOW_PLAYING_API", "https://api.spotify.com/v1")
ACCOUNTS = os.environ.get("NOW_PLAYING_ACCOUNTS", "https://accounts.spotify.com")
LRCLIB = os.environ.get("NOW_PLAYING_LRCLIB", "https://lrclib.net/api")
REDIRECT_PORT = 8888
REDIRECT_URI = f"http://127.0.0.1:{REDIRECT_PORT}/callback"
SCOPES = "user-read-playback-state user-modify-playback-state user-read-currently-playing"
USER_AGENT = "NowPlaying-widget/1.0 (personal Linux desktop widget)"

TERM_GREEN = (0.22, 1.0, 0.42)
TERM_DIM = (0.12, 0.55, 0.25)
TERM_BG = (0.0, 0.0, 0.0)
ASCII_RAMP = " .'`^\",:;Il!i><~+_-?][}{1)(|\\/tfjrxnuvczXYUJCLQ0OZmwqpdbkhao*#MW&8%B@$"
DEFAULT_BG = (0.09, 0.09, 0.09)

CSS = b"""
window.np { background-color: transparent; }
.np-frame {
    border-radius: 12px; border: 1px solid rgba(255,255,255,0.09);
    transition: background-color 900ms ease;
}
.np-frame label { color: #f2f2f2; }
.np-title { font-size: 17pt; font-weight: 800; }
.np-artist { font-size: 12pt; }
.np-frame label.np-album, .np-frame label.np-dim { color: rgba(255,255,255,0.55); }
.np-frame label.np-dim { font-size: 9pt; }
.np-frame label.np-section { font-size: 8pt; font-weight: bold; color: rgba(255,255,255,0.5); }
.np-frame label.np-brand { font-size: 9pt; font-weight: bold; color: rgba(255,255,255,0.6); }
.np-frame button {
    background: transparent; background-image: none; border: none; box-shadow: none;
    color: #f2f2f2; text-shadow: none; -gtk-icon-shadow: none;
    border-radius: 999px; padding: 6px; min-width: 0; min-height: 0;
}
.np-frame button:hover { background-color: rgba(255,255,255,0.12); }
.np-frame button:disabled { opacity: 0.35; }
.np-frame button.np-play { background-color: #f2f2f2; color: #121212; padding: 7px; }
.np-frame button.np-play:hover { background-color: #ffffff; }
.np-frame button.np-on { color: #1ed760; }
.np-frame button.np-pill {
    border-radius: 999px; padding: 1px 8px; font-size: 8pt; font-weight: normal;
    color: rgba(255,255,255,0.7);
}
.np-frame button.np-pill.np-on { background-color: rgba(30,215,96,0.16); }
.np-frame button.np-pill label { color: rgba(255,255,255,0.7); }
.np-frame button.np-on label, .np-frame button.np-on image { color: #1ed760; }
.np-frame image { color: #f2f2f2; }
.np-frame button.np-play image, .np-frame button.np-play label { color: #121212; }
.np-frame button.np-connect label { color: #121212; }
.np-frame button.np-connect {
    background-color: #1ed760; color: #121212; font-weight: bold; padding: 6px 14px;
}
.np-frame scale { padding: 6px 0; }
.np-frame scale trough {
    min-height: 3px; background-color: rgba(255,255,255,0.22);
    border: none; border-radius: 2px; background-image: none;
}
.np-frame scale highlight { background-color: #f2f2f2; border: none; border-radius: 2px; background-image: none; }
.np-frame scale:hover highlight { background-color: #1ed760; }
.np-frame scale slider {
    min-width: 10px; min-height: 10px; margin: -4px; border-radius: 5px;
    background-color: #ffffff; background-image: none; border: none; box-shadow: none;
}
.np-frame list, .np-frame row { background-color: transparent; }
.np-frame row { padding: 3px 6px; border-radius: 6px; }
.np-frame row:hover { background-color: rgba(255,255,255,0.09); }
.np-frame row:selected { background-color: transparent; }
.np-frame .np-grip { color: rgba(255,255,255,0.3); padding: 0 4px; }
.np-frame entry { background-color: rgba(255,255,255,0.1); color: #ffffff; border: none; }
.np-frame.np-retro { border-color: rgba(57,255,106,0.35); }
.np-retro label, .np-retro button label { color: #39ff6a; font-family: Monospace; }
.np-retro label.np-album, .np-retro label.np-dim, .np-retro label.np-section,
.np-retro label.np-brand { color: rgba(57,255,106,0.55); }
.np-retro .np-title { font-family: Monospace; font-weight: bold; }
.np-retro image { color: #39ff6a; }
.np-retro button:hover { background-color: rgba(57,255,106,0.14); }
.np-retro button.np-play { background-color: #39ff6a; border-radius: 3px; }
.np-retro button.np-play image, .np-retro button.np-play label { color: #041006; }
.np-retro button.np-pill { border-radius: 3px; }
.np-retro button.np-pill.np-on { background-color: #39ff6a; }
.np-retro button.np-pill.np-on label { color: #041006; }
.np-retro button.np-on image { color: #b6ffc8; }
.np-retro button.np-connect { background-color: #39ff6a; border-radius: 3px; }
.np-retro scale trough { background-color: rgba(57,255,106,0.22); border-radius: 0; }
.np-retro scale highlight, .np-retro scale:hover highlight { background-color: #39ff6a; border-radius: 0; }
.np-retro scale slider { background-color: #39ff6a; border-radius: 0; }
.np-retro row:hover { background-color: rgba(57,255,106,0.12); }
.np-frame label.np-focus-title { font-size: 12pt; font-weight: bold; }
.np-big label.np-focus-title { font-size: 16pt; }
.np-frame.np-fullscreen { border-radius: 0; border: none; }
.np-menu { background-color: #1f1f1f; border: 1px solid rgba(255,255,255,0.12); padding: 4px 0; }
.np-menu menuitem { background-color: transparent; padding: 5px 14px; }
.np-menu menuitem label { color: #f2f2f2; }
.np-menu menuitem:hover { background-color: rgba(255,255,255,0.10); }
.np-menu separator { background-color: rgba(255,255,255,0.12); margin: 3px 0; }
.np-menu check { color: #1ed760; }
.np-menu-retro, .np-menu-retro menuitem { background-color: #000000; font-family: Monospace; }
.np-menu-retro menuitem label { color: #39ff6a; }
.np-menu-retro menuitem:hover { background-color: #39ff6a; }
.np-menu-retro menuitem:hover label { color: #000000; }
.np-menu-retro separator { background-color: rgba(57,255,106,0.3); }
.np-big .np-title { font-size: 30pt; }
.np-big .np-artist { font-size: 18pt; }
.np-big label.np-album { font-size: 14pt; }
.np-big row label { font-size: 12pt; }
"""


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def fmt_time(ms):
    s = max(0, int(ms or 0) // 1000)
    return f"{s // 60}:{s % 60:02d}"


def run_async(work, done=None):
    """Run work() in a thread and hand its result (or exception) to done() on the main loop."""
    def runner():
        try:
            result = work()
        except Exception as e:  # handed to the caller
            result = e
        if done is not None:
            GLib.idle_add(lambda: done(result) and False)
    threading.Thread(target=runner, daemon=True).start()


def http_request(method, url, headers=None, data=None, timeout=15):
    """Returns (status, body_bytes, response_headers). Never raises for HTTP errors."""
    request = urllib.request.Request(url, data=data, method=method,
                                     headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read() or b"", dict(e.headers or {})


def cached_download(url):
    """Download url into the cache (once) and return the local path."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, hashlib.sha1(url.encode()).hexdigest())
    if not os.path.exists(path):
        status, body, _ = http_request("GET", url, timeout=20)
        if status != 200 or not body:
            raise IOError(f"download failed ({status})")
        tmp = path + ".part"
        with open(tmp, "wb") as f:
            f.write(body)
        os.replace(tmp, path)
    return path


def average_color(pixbuf):
    tiny = pixbuf.scale_simple(1, 1, GdkPixbuf.InterpType.HYPER)
    data = tiny.get_pixels()
    return data[0] / 255, data[1] / 255, data[2] / 255


def widget_background(rgb):
    """A dark, slightly saturated version of the cover's main color."""
    h, l, s = colorsys.rgb_to_hls(*rgb)
    return colorsys.hls_to_rgb(h, min(l, 0.20) * 0.9 + 0.04, min(s, 0.55))


def icon_button(icon_names, fallback, tooltip, size=16, css=None):
    button = Gtk.Button()
    theme = Gtk.IconTheme.get_default()
    name = next((n for n in icon_names if theme.has_icon(n)), None)
    if name:
        image = Gtk.Image.new_from_icon_name(name, Gtk.IconSize.BUTTON)
        image.set_pixel_size(size)
        button.set_image(image)
    else:
        button.set_label(fallback)
    button.set_tooltip_text(tooltip)
    button.set_can_focus(False)
    button.set_relief(Gtk.ReliefStyle.NONE)
    button.set_valign(Gtk.Align.CENTER)
    if css:
        button.get_style_context().add_class(css)
    return button


def set_button_icon(button, icon_names, fallback, size):
    theme = Gtk.IconTheme.get_default()
    name = next((n for n in icon_names if theme.has_icon(n)), None)
    if name:
        image = Gtk.Image.new_from_icon_name(name, Gtk.IconSize.BUTTON)
        image.set_pixel_size(size)
        button.set_image(image)
    else:
        button.set_label(fallback)


def pixbuf_to_ascii(pixbuf, cols, rows):
    small = pixbuf.scale_simple(cols, rows, GdkPixbuf.InterpType.BILINEAR)
    data = small.get_pixels()
    stride, channels = small.get_rowstride(), small.get_n_channels()
    lum = []
    for y in range(rows):
        row = []
        for x in range(cols):
            i = y * stride + x * channels
            row.append(0.2126 * data[i] + 0.7152 * data[i + 1] + 0.0722 * data[i + 2])
        lum.append(row)
    flat = sorted(v for row in lum for v in row)
    lo = flat[int(len(flat) * 0.02)]
    hi = flat[int(len(flat) * 0.98) - 1]
    span = max(1.0, hi - lo)
    ramp = ASCII_RAMP
    lines = []
    for row in lum:
        chars = []
        for v in row:
            t = min(1.0, max(0.0, (v - lo) / span))
            chars.append(ramp[int(t * (len(ramp) - 1))])
        lines.append("".join(chars))
    return lines


def parse_lrc(text):
    lines = []
    for raw in (text or "").splitlines():
        stamps = re.findall(r"\[(\d+):(\d+(?:\.\d+)?)\]", raw)
        if not stamps:
            continue
        words = re.sub(r"\[[^\]]*\]", "", raw).strip()
        for minutes, seconds in stamps:
            lines.append((int((int(minutes) * 60 + float(seconds)) * 1000), words))
    lines.sort(key=lambda item: item[0])
    return lines


def _find_images(widget):
    if isinstance(widget, Gtk.Image):
        return [widget]
    if isinstance(widget, Gtk.Container):
        return [img for child in widget.get_children() for img in _find_images(child)]
    return []


DOUBLE_CLICK = getattr(Gdk.EventType, "DOUBLE_BUTTON_PRESS", None) or getattr(Gdk.EventType, "_2BUTTON_PRESS")


class FlexColumn(Gtk.Box):
    """A vertical box whose preferred width follows the window, but that can still shrink."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.natural = 440

    def do_get_preferred_width(self):
        minimum, _natural = Gtk.Box.do_get_preferred_width(self)
        return minimum, max(minimum, self.natural)

    def set_natural(self, width):
        if width != self.natural:
            self.natural = width
            self.queue_resize()


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------

class Settings:
    GROUP = "now-playing"

    def __init__(self):
        self.kf = GLib.KeyFile()
        try:
            self.kf.load_from_file(SETTINGS_FILE, GLib.KeyFileFlags.KEEP_COMMENTS)
        except GLib.Error:
            pass

    def get(self, key, default=None):
        try:
            return self.kf.get_string(self.GROUP, key)
        except GLib.Error:
            return default

    def get_bool(self, key, default=False):
        try:
            return self.kf.get_boolean(self.GROUP, key)
        except GLib.Error:
            return default

    def get_int(self, key, default=0):
        try:
            return self.kf.get_integer(self.GROUP, key)
        except GLib.Error:
            return default

    def set(self, key, value):
        if isinstance(value, bool):
            self.kf.set_boolean(self.GROUP, key, value)
        elif isinstance(value, int):
            self.kf.set_integer(self.GROUP, key, value)
        else:
            self.kf.set_string(self.GROUP, key, str(value))
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            self.kf.save_to_file(SETTINGS_FILE)
        except (OSError, GLib.Error) as e:
            print(f"now-playing: could not save settings: {e}", file=sys.stderr)


# --------------------------------------------------------------------------
# Spotify Web API (login with PKCE, token refresh, requests)
# --------------------------------------------------------------------------

class SpotifyWeb:
    def __init__(self, settings):
        self.settings = settings
        self.lock = threading.Lock()
        self.token = {}
        try:
            with open(TOKEN_FILE, encoding="utf-8") as f:
                self.token = json.load(f)
        except (OSError, ValueError):
            self.token = {}

    @property
    def client_id(self):
        return (self.settings.get("client_id") or "").strip()

    @property
    def connected(self):
        return bool(self.client_id and self.token.get("refresh_token"))

    def _save_token(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        tmp = TOKEN_FILE + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self.token, f)
        os.replace(tmp, TOKEN_FILE)

    def _store(self, payload):
        self.token["access_token"] = payload["access_token"]
        self.token["expires_at"] = time.time() + int(payload.get("expires_in", 3600))
        if payload.get("refresh_token"):
            self.token["refresh_token"] = payload["refresh_token"]
        self._save_token()

    def disconnect(self):
        self.token = {}
        try:
            os.remove(TOKEN_FILE)
        except OSError:
            pass

    # ---- login -------------------------------------------------------------
    def login(self, client_id, done):
        """Opens the browser for the Spotify login; done(ok, message) on the main loop."""
        verifier = secrets.token_urlsafe(64)[:96]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        state = secrets.token_urlsafe(16)
        url = f"{ACCOUNTS}/authorize?" + urllib.parse.urlencode({
            "response_type": "code", "client_id": client_id, "scope": SCOPES,
            "redirect_uri": REDIRECT_URI, "code_challenge_method": "S256",
            "code_challenge": challenge, "state": state})

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != "/callback":
                    self.send_response(404)
                    self.end_headers()
                    return
                self.server.result = urllib.parse.parse_qs(parsed.query)
                ok = "code" in self.server.result
                body = ("<html><body style='background:#121212;color:#1ed760;font-family:monospace;"
                        "font-size:20px;padding:40px'>&gt; " +
                        ("login ok. you can close this tab_" if ok else "login failed. try again_") +
                        "</body></html>").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_):
                pass

        try:
            server = http.server.HTTPServer(("127.0.0.1", REDIRECT_PORT), Handler)
        except OSError as e:
            done(False, f"Port {REDIRECT_PORT} is busy ({e.strerror}). Close the program using it.")
            return
        server.timeout = 1
        server.result = None

        def work():
            deadline = time.time() + 300
            while server.result is None and time.time() < deadline:
                server.handle_request()
            server.server_close()
            result = server.result or {}
            if "code" not in result:
                return False, result.get("error", ["timed out"])[0]
            if result.get("state", [""])[0] != state:
                return False, "state mismatch, please try again"
            status, body, _ = http_request("POST", f"{ACCOUNTS}/api/token", data=urllib.parse.urlencode({
                "grant_type": "authorization_code", "code": result["code"][0],
                "redirect_uri": REDIRECT_URI, "client_id": client_id,
                "code_verifier": verifier}).encode(),
                headers={"Content-Type": "application/x-www-form-urlencoded"})
            if status != 200:
                return False, f"token exchange failed ({status}): {body[:200].decode('utf-8', 'replace')}"
            with self.lock:
                self.token = {}
                self._store(json.loads(body))
            return True, "connected"

        run_async(work, lambda result: done(*result) if isinstance(result, tuple)
                  else done(False, str(result)))
        try:
            Gio.AppInfo.launch_default_for_uri(url, None)
        except GLib.Error as e:
            done(False, f"could not open the browser: {e.message}\nOpen this link yourself:\n{url}")

    # ---- requests (call from worker threads only) --------------------------
    def _access_token(self, force_refresh=False):
        with self.lock:
            if not self.connected:
                return None
            if not force_refresh and self.token.get("access_token") and \
                    time.time() < self.token.get("expires_at", 0) - 60:
                return self.token["access_token"]
            status, body, _ = http_request("POST", f"{ACCOUNTS}/api/token", data=urllib.parse.urlencode({
                "grant_type": "refresh_token", "refresh_token": self.token["refresh_token"],
                "client_id": self.client_id}).encode(),
                headers={"Content-Type": "application/x-www-form-urlencoded"})
            if status != 200:
                if status in (400, 401):   # refresh token revoked
                    self.token = {}
                    try:
                        os.remove(TOKEN_FILE)
                    except OSError:
                        pass
                return None
            self._store(json.loads(body))
            return self.token["access_token"]

    def call(self, method, path, params=None, body=None):
        """Returns parsed JSON, {} for an empty success, or None on failure."""
        url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
        headers = {}
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        else:
            data = b"" if method in ("PUT", "POST") else None
        for attempt in (0, 1):
            token = self._access_token(force_refresh=attempt == 1)
            if token is None:
                return None
            status, body_bytes, _ = http_request(method, url, data=data,
                                                 headers={"Authorization": f"Bearer {token}", **headers})
            body = body_bytes
            if status == 401 and attempt == 0:
                continue
            if status in (200, 201):
                try:
                    return json.loads(body) if body else {}
                except ValueError:
                    return {}
            if status in (202, 204):
                return {}
            return None
        return None


# --------------------------------------------------------------------------
# the Spotify desktop app over D-Bus (MPRIS)
# --------------------------------------------------------------------------

class Mpris:
    def __init__(self, on_update):
        self.on_update = on_update
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.props = {}
        self.available = False
        self._busy = False
        self.bus.signal_subscribe(None, "org.freedesktop.DBus.Properties", "PropertiesChanged",
                                  MPRIS_PATH, None, Gio.DBusSignalFlags.NONE,
                                  lambda *_: self.poll())
        self.bus.signal_subscribe(None, MPRIS_PLAYER, "Seeked", MPRIS_PATH, None,
                                  Gio.DBusSignalFlags.NONE, lambda *_: self.poll())
        GLib.timeout_add(1000, lambda: self.poll() or True)
        self.poll()

    def poll(self):
        if self._busy:
            return
        self._busy = True
        self.bus.call(MPRIS_NAME, MPRIS_PATH, "org.freedesktop.DBus.Properties", "GetAll",
                      GLib.Variant("(s)", (MPRIS_PLAYER,)), GLib.VariantType("(a{sv})"),
                      Gio.DBusCallFlags.NO_AUTO_START, 1500, None, self._on_props)

    def _on_props(self, bus, result):
        self._busy = False
        try:
            self.props = bus.call_finish(result).unpack()[0]
            self.available = True
        except GLib.Error:
            self.props = {}
            self.available = False
        self.on_update()

    def _call(self, method, args=None):
        if not self.available:
            return
        self.bus.call(MPRIS_NAME, MPRIS_PATH, MPRIS_PLAYER, method, args, None,
                      Gio.DBusCallFlags.NO_AUTO_START, 2000, None,
                      lambda bus, res: self._finish(bus, res))

    def _finish(self, bus, result):
        try:
            bus.call_finish(result)
        except GLib.Error as e:
            print(f"now-playing: MPRIS call failed: {e.message}", file=sys.stderr)
        GLib.timeout_add(150, lambda: self.poll() or False)

    def set_property(self, name, variant):
        if not self.available:
            return
        self.bus.call(MPRIS_NAME, MPRIS_PATH, "org.freedesktop.DBus.Properties", "Set",
                      GLib.Variant("(ssv)", (MPRIS_PLAYER, name, variant)), None,
                      Gio.DBusCallFlags.NO_AUTO_START, 2000, None,
                      lambda bus, res: self._finish(bus, res))

    def play_pause(self):
        self._call("PlayPause")

    def next(self):
        self._call("Next")

    def previous(self):
        self._call("Previous")

    def set_position(self, track_path, ms):
        if track_path:
            self._call("SetPosition", GLib.Variant("(ox)", (track_path, int(ms) * 1000)))

    # ---- parsed properties ----------------------------------------------------
    @property
    def metadata(self):
        return self.props.get("Metadata") or {}

    @property
    def playing(self):
        return self.props.get("PlaybackStatus") == "Playing"

    def track(self):
        md = self.metadata
        track_id = str(md.get("mpris:trackid") or "")
        if not track_id and not md.get("xesam:title"):
            return None
        art = md.get("mpris:artUrl") or ""
        art = art.replace("https://open.spotify.com/image/", "https://i.scdn.co/image/")
        artists = md.get("xesam:artist") or []
        if isinstance(artists, str):
            artists = [artists]
        uri = md.get("xesam:url") or ""
        simple_id = track_id.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
        return {
            "key": track_id or f"{md.get('xesam:title')}|{artists}",
            "id": simple_id,
            "path": track_id if track_id.startswith("/") else None,
            "title": md.get("xesam:title") or "Unknown title",
            "artists": ", ".join(a for a in artists if a) or "Unknown artist",
            "first_artist": artists[0] if artists else "",
            "album": md.get("xesam:album") or "",
            "art_url": art,
            "duration_ms": int(md.get("mpris:length") or 0) // 1000,
            "uri": uri,
        }


# --------------------------------------------------------------------------
# album cover: a picture or old-school ASCII art
# --------------------------------------------------------------------------

class CoverView(Gtk.DrawingArea):
    MIN = 72

    def __init__(self):
        super().__init__()
        self.natural = 230
        self.pixbuf = None
        self.ascii = False
        self._scaled = None
        self._ascii_cache = None
        self.connect("draw", self._draw)

    def do_get_preferred_width(self):
        return self.MIN, max(self.MIN, self.natural)

    def do_get_preferred_height(self):
        return self.MIN, max(self.MIN, self.natural)

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.CONSTANT_SIZE

    def set_natural(self, size):
        if size != self.natural:
            self.natural = size
            self.queue_resize()

    def set_pixbuf(self, pixbuf):
        self.pixbuf = pixbuf
        self._scaled = None
        self._ascii_cache = None
        self.queue_draw()

    def set_ascii(self, on):
        self.ascii = on
        self.queue_draw()

    @staticmethod
    def _rounded(cr, x, y, w, h, r):
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -1.5708, 0)
        cr.arc(x + w - r, y + h - r, r, 0, 1.5708)
        cr.arc(x + r, y + h - r, r, 1.5708, 3.1416)
        cr.arc(x + r, y + r, r, 3.1416, 4.7124)
        cr.close_path()

    def _draw(self, _widget, cr):
        w, h = self.get_allocated_width(), self.get_allocated_height()
        size = min(w, h)
        x0, y0 = (w - size) / 2, (h - size) / 2
        self._rounded(cr, x0, y0, size, size, 8)
        cr.clip()

        if self.ascii:
            self._draw_ascii(cr, x0, y0, size)
            return False

        if self.pixbuf is None:
            cr.set_source_rgba(1, 1, 1, 0.06)
            cr.paint()
            layout = PangoCairo.create_layout(cr)
            layout.set_markup("<span size='40000' alpha='30%'>♪</span>", -1)
            tw, th = layout.get_pixel_size()
            cr.move_to(x0 + (size - tw) / 2, y0 + (size - th) / 2)
            cr.set_source_rgba(1, 1, 1, 0.5)
            PangoCairo.show_layout(cr, layout)
            return False
        if self._scaled is None or self._scaled.get_width() != int(size):
            self._scaled = self.pixbuf.scale_simple(int(size), int(size), GdkPixbuf.InterpType.BILINEAR)
        Gdk.cairo_set_source_pixbuf(cr, self._scaled, x0, y0)
        cr.paint()
        return False

    def _draw_ascii(self, cr, x0, y0, size):
        key = (id(self.pixbuf), int(size))
        if self._ascii_cache is None or self._ascii_cache[0] != key:
            self._ascii_cache = (key, render_ascii_surface(self.pixbuf, int(size)))
        cr.set_source_surface(self._ascii_cache[1], x0, y0)
        cr.paint()


def _green(level):
    """Phosphor green at a brightness between 0 and 1 (on the terminal background)."""
    r = TERM_BG[0] + (TERM_GREEN[0] - TERM_BG[0]) * level
    g = TERM_BG[1] + (TERM_GREEN[1] - TERM_BG[1]) * level
    b = TERM_BG[2] + (TERM_GREEN[2] - TERM_BG[2]) * level
    return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))


ASCII_LEVELS = 10
ASCII_COLORS = [_green(0.30 + 0.70 * (i / (ASCII_LEVELS - 1)) ** 0.7) for i in range(ASCII_LEVELS)]


def green_tint(pixbuf):
    """A green phosphor version of a small picture (for queue thumbnails in retro mode)."""
    pb = pixbuf.add_alpha(False, 0, 0, 0) if not pixbuf.get_has_alpha() else pixbuf.copy()
    w, h, stride = pb.get_width(), pb.get_height(), pb.get_rowstride()
    data = bytearray(pb.get_pixels())
    for y in range(h):
        for x in range(w):
            i = y * stride + x * 4
            lum = (0.2126 * data[i] + 0.7152 * data[i + 1] + 0.0722 * data[i + 2]) / 255
            level = 0.15 + 0.85 * lum
            data[i] = int(255 * (TERM_BG[0] + (TERM_GREEN[0] - TERM_BG[0]) * level))
            data[i + 1] = int(255 * (TERM_BG[1] + (TERM_GREEN[1] - TERM_BG[1]) * level))
            data[i + 2] = int(255 * (TERM_BG[2] + (TERM_GREEN[2] - TERM_BG[2]) * level))
    return GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(bytes(data)), GdkPixbuf.Colorspace.RGB,
                                           True, 8, w, h, stride)


def render_ascii_surface(pixbuf, size):
    """Draw the cover as high-density ASCII art, each character glowing as bright as its spot."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    cr.set_source_rgb(*TERM_BG)
    cr.paint()
    layout = PangoCairo.create_layout(cr)
    probe = Pango.FontDescription.from_string("Monospace Bold")
    probe.set_absolute_size(10 * Pango.SCALE)
    layout.set_font_description(probe)
    layout.set_text("M" * 10, -1)
    cw10 = layout.get_pixel_size()[0] / 10
    cols = int(min(170, max(48, size / 3.4)))
    font_px = 10 * (size - 6) / cols / cw10
    font = Pango.FontDescription.from_string("Monospace Bold")
    font.set_absolute_size(font_px * Pango.SCALE)
    layout.set_font_description(font)
    layout.set_text("M", -1)
    line_h = max(1.0, layout.get_extents()[1].height / Pango.SCALE)
    cw = layout.get_extents()[1].width / Pango.SCALE
    rows = max(1, int((size - 6) // line_h))
    if pixbuf is None:
        layout.set_text("no cover", -1)
        cr.set_source_rgb(*TERM_GREEN)
        tw, th = layout.get_pixel_size()
        cr.move_to((size - tw) / 2, (size - th) / 2)
        PangoCairo.show_layout(cr, layout)
        return surface

    small = pixbuf.scale_simple(cols, rows, GdkPixbuf.InterpType.HYPER)
    data = small.get_pixels()
    stride, channels = small.get_rowstride(), small.get_n_channels()
    lum = [[0.2126 * data[y * stride + x * channels] + 0.7152 * data[y * stride + x * channels + 1]
            + 0.0722 * data[y * stride + x * channels + 2] for x in range(cols)] for y in range(rows)]
    flat = sorted(v for row in lum for v in row)
    lo, hi = flat[int(len(flat) * 0.01)], flat[int(len(flat) * 0.99) - 1]
    span = max(1.0, hi - lo)
    ramp = ASCII_RAMP
    esc = GLib.markup_escape_text
    ox = (size - cols * cw) / 2
    oy = (size - rows * line_h) / 2
    for y, row in enumerate(lum):
        parts, run, run_level = [], "", None
        for v in row:
            t = min(1.0, max(0.0, (v - lo) / span)) ** 0.8
            char = ramp[int(t * (len(ramp) - 1))]
            level = int(t * (ASCII_LEVELS - 1))
            if level != run_level and run:
                parts.append(f"<span foreground='{ASCII_COLORS[run_level]}'>{esc(run)}</span>")
                run = ""
            run += char
            run_level = level
        if run:
            parts.append(f"<span foreground='{ASCII_COLORS[run_level]}'>{esc(run)}</span>")
        layout.set_markup("".join(parts), -1)
        cr.move_to(ox, oy + y * line_h)
        PangoCairo.show_layout(cr, layout)
    return surface


# --------------------------------------------------------------------------
# lyrics: a terminal that types them (retro mode) or big bold lines (normal mode)
# --------------------------------------------------------------------------

class LyricsView(Gtk.DrawingArea):
    TYPE_SPEED = 32.0      # characters per second
    PROMPT = "> "
    USER_HOST = f"{GLib.get_user_name()}@{GLib.get_host_name()}"

    def __init__(self, position_source):
        super().__init__()
        self.position = position_source
        self.set_size_request(170, -1)
        self.font_size = 10.5
        self.retro = False
        self.focus = False
        self._scroll = None
        self._last_frame = time.monotonic()
        self._bold_cache = None
        self.offset_ms = 0
        self._flash = ("", 0.0)
        self.state = "idle"            # idle | loading | synced | plain | none | instrumental | offline
        self.lines = []
        self.title = ""
        self.changed_at = time.monotonic()
        self.connect("draw", self._draw)
        GLib.timeout_add(45, self._tick)

    def _tick(self):
        if self.get_mapped():
            self.queue_draw()
        return True

    def set_song(self, title):
        self.title = title
        self.state = "loading"
        self.lines = []
        self.changed_at = time.monotonic()

    def set_lyrics(self, state, lines):
        self.state = state
        self.lines = lines
        self._scroll = None

    def flash(self, text):
        self._flash = (text, time.monotonic() + 1.6)

    def shift(self, delta_ms):
        self.offset_ms += delta_ms
        seconds = self.offset_ms / 1000
        self.flash(f"lyrics timing {seconds:+.2f}s" if self.offset_ms else "lyrics timing reset")

    def _items(self, pos_ms):
        """(text, style, typed_chars) for everything on screen, oldest first."""
        items = []
        since = time.monotonic() - self.changed_at
        command = f'{self.USER_HOST}:~$ lyrics "{self.title}"'
        typed = min(len(command), int(since * 45))
        items.append((command, "cmd", typed))
        if typed < len(command):
            return items, True

        if self.state == "idle":
            items.append(("waiting for spotify...", "dim", None))
            return items, True
        if self.state == "offline":
            items.append(("spotify is not running", "dim", None))
            return items, True
        if self.state == "loading":
            spin = "|/-\\"[int(since * 8) % 4]
            items.append((f"fetching lyrics {spin}", "dim", None))
            return items, False
        if self.state == "none":
            items.append(("no lyrics found for this song.", "dim", None))
            return items, True
        if self.state == "instrumental":
            items.append(("[instrumental]", "line", None))
            return items, True

        current = -1
        for i, (t, _text) in enumerate(self.lines):
            if t <= pos_ms:
                current = i
            else:
                break
        if current == -1:
            items.append(("", "line", 0))
            return items, True
        for i in range(current + 1):
            t, text = self.lines[i]
            text = text or "♪"
            if i < current:
                items.append((text, "old", None))
            else:
                chars = int(max(0, pos_ms - t) / 1000 * self.TYPE_SPEED)
                items.append((text, "line", min(len(text), chars)))
        return items, True

    def _draw(self, widget, cr):
        if self.retro:
            self._draw_terminal(cr)
        else:
            self._draw_bold(cr)
        return False

    def _current_index(self, pos_ms):
        current = -1
        for i, (t, _text) in enumerate(self.lines):
            if t <= pos_ms:
                current = i
            else:
                break
        return current

    def _draw_flash(self, cr, w, h, retro):
        text, until = self._flash
        if not text or time.monotonic() >= until:
            return
        note = PangoCairo.create_layout(cr)
        note.set_font_description(Pango.FontDescription.from_string(
            "Monospace 9" if retro else "Sans Bold 9"))
        note.set_text(f"[{text}]" if retro else text, -1)
        nw, nh = note.get_pixel_size()
        if retro:
            cr.set_source_rgba(*TERM_BG, 0.9)
        else:
            cr.set_source_rgba(0, 0, 0, 0.45)
        cr.rectangle(w - nw - 16, h - nh - 14, nw + 8, nh + 6)
        cr.fill()
        cr.set_source_rgb(*(TERM_GREEN if retro else (1, 1, 1)))
        cr.move_to(w - nw - 12, h - nh - 11)
        PangoCairo.show_layout(cr, note)

    # ---- normal mode: big bold lines, the current one fills up as it is sung
    def _draw_bold(self, cr):
        w, h = self.get_allocated_width(), self.get_allocated_height()
        now = time.monotonic()
        dt = min(0.2, now - self._last_frame)
        self._last_frame = now
        pad = 14
        width = max(40, w - 2 * pad)
        size = round(self.font_size * (2.45 if self.focus else 2.0), 1)
        font = Pango.FontDescription.from_string(f"Noto Sans,Ubuntu,Cantarell,DejaVu Sans {size}")
        font.set_weight(Pango.Weight.HEAVY)
        stroke = max(0.6, size * 0.055)    # a thin outline makes the letters extra chunky

        def paint(layout, x, y, alpha, clip=None):
            # Draw opaque in a group, then fade the whole thing, so the outline
            # and the fill don't add up to a darker edge.
            cr.push_group()
            if clip is not None:
                for rect in clip:
                    cr.rectangle(*rect)
                cr.clip()
            cr.move_to(x, y)
            PangoCairo.layout_path(cr, layout)
            cr.set_source_rgb(1, 1, 1)
            cr.fill_preserve()
            cr.set_line_width(stroke)
            cr.set_line_join(cairo.LINE_JOIN_ROUND)
            cr.stroke()
            cr.pop_group_to_source()
            cr.paint_with_alpha(alpha)

        def message(text):
            layout = PangoCairo.create_layout(cr)
            layout.set_font_description(font)
            layout.set_width(int(width * Pango.SCALE))
            layout.set_wrap(Pango.WrapMode.WORD_CHAR)
            layout.set_text(text, -1)
            paint(layout, pad, h * 0.35, 0.45)

        if self.state not in ("synced", "plain") or not self.lines:
            since = now - self.changed_at
            texts = {"loading": "Loading lyrics" + "." * (int(since * 3) % 4),
                     "none": "No lyrics for this song",
                     "instrumental": "\u266a Instrumental \u266a",
                     "offline": "Spotify is not running",
                     "idle": ""}
            message(texts.get(self.state, ""))
            self._draw_flash(cr, w, h, False)
            return

        # Lay out every line once per song/size, then only re-render the current one.
        key = (id(self.lines), width, size)
        if self._bold_cache is None or self._bold_cache[0] != key:
            layouts, tops, y = [], [], 0
            gap = size * 0.6
            for _t, text in self.lines:
                layout = PangoCairo.create_layout(cr)
                layout.set_font_description(font)
                layout.set_width(int(width * Pango.SCALE))
                layout.set_wrap(Pango.WrapMode.WORD_CHAR)
                if hasattr(layout, "set_line_spacing"):
                    layout.set_line_spacing(1.0)
                layout.set_text(text or "\u266a", -1)
                layouts.append(layout)
                tops.append(y)
                y += layout.get_pixel_size()[1] + gap
            self._bold_cache = (key, layouts, tops)
        _key, layouts, tops = self._bold_cache

        pos = self.position() + self.offset_ms
        current = self._current_index(pos)
        anchor = tops[max(current, 0)]
        target = anchor - h * 0.32
        if self._scroll is None or abs(target - self._scroll) > h * 3:
            self._scroll = target
        self._scroll += (target - self._scroll) * min(1.0, dt * 7)

        cr.push_group()
        for i, layout in enumerate(layouts):
            y = tops[i] - self._scroll
            lh = layout.get_pixel_size()[1]
            if y + lh < 0 or y > h:
                continue
            if i == current:
                t, text = self.lines[i]
                text = text or "\u266a"
                nxt = self.lines[i + 1][0] if i + 1 < len(self.lines) else t + 4000
                duration = max(800, min(8000, nxt - t)) * 0.85
                done = int(len(text) * min(1.0, max(0.0, (pos - t) / duration)))
                # dim version first, then the sung part bright on top of it
                paint(layout, pad, y, 0.5)
                if done:
                    edge = layout.index_to_pos(len(text[:done].encode()))
                    ex, ey = edge.x / Pango.SCALE, edge.y / Pango.SCALE
                    eh = edge.height / Pango.SCALE
                    clip = [(0, 0, w, y + ey), (0, y + ey, pad + ex, eh + 2)]
                    if done >= len(text):
                        clip = [(0, 0, w, h)]
                    paint(layout, pad, y, 1.0, clip)
            else:
                distance = i - current
                if distance < 0:
                    alpha = max(0.10, 0.32 - 0.06 * (-distance - 1))
                else:
                    alpha = max(0.12, 0.5 - 0.09 * (distance - 1))
                paint(layout, pad, y, alpha)
        group = cr.pop_group()
        # soft fade at the top and bottom edge
        mask = cairo.LinearGradient(0, 0, 0, h)
        mask.add_color_stop_rgba(0, 0, 0, 0, 0)
        mask.add_color_stop_rgba(0.12, 0, 0, 0, 1)
        mask.add_color_stop_rgba(0.85, 0, 0, 0, 1)
        mask.add_color_stop_rgba(1, 0, 0, 0, 0)
        cr.set_source(group)
        cr.mask(mask)
        self._draw_flash(cr, w, h, False)

    # ---- retro mode: a terminal that types the lyrics
    def _draw_terminal(self, cr):
        w, h = self.get_allocated_width(), self.get_allocated_height()
        CoverView._rounded(cr, 0, 0, w, h, 4)
        cr.clip()
        cr.set_source_rgb(*TERM_BG)
        cr.paint()
        cr.set_source_rgba(*TERM_GREEN, 0.35)
        CoverView._rounded(cr, 0.5, 0.5, w - 1, h - 1, 4)
        cr.set_line_width(1)
        cr.stroke()

        pad = 12
        width = w - 2 * pad
        body_top = pad
        items, cursor_on_last = self._items(self.position() + self.offset_ms)
        font = Pango.FontDescription.from_string(f"Monospace {self.font_size:.1f}")
        layouts = []
        for text, style, typed in items:
            layout = PangoCairo.create_layout(cr)
            layout.set_font_description(font)
            layout.set_width(width * Pango.SCALE)
            layout.set_wrap(Pango.WrapMode.WORD_CHAR)
            shown = text if typed is None else text[:typed]
            prefix = "" if style == "cmd" else self.PROMPT
            blink = int(time.monotonic() * 1.9) % 2 == 0
            is_last = len(layouts) == len(items) - 1
            cursor = "█" if (is_last and cursor_on_last and blink) else (" " if is_last else "")
            if style == "cmd":
                # Like a real Linux prompt: user@host in green, the path in blue.
                n = len(self.USER_HOST)
                esc = GLib.markup_escape_text
                markup = f"<b><span foreground='#39ff6a'>{esc(shown[:n])}</span></b>"
                rest = shown[n:]
                if rest:
                    markup += esc(rest[:1])
                    markup += f"<b><span foreground='#6ea8ff'>{esc(rest[1:2])}</span></b>"
                    markup += esc(rest[2:])
                layout.set_markup(markup + esc(cursor), -1)
            else:
                layout.set_text(prefix + shown + cursor, -1)
            layouts.append((layout, style))

        heights = [layout.get_pixel_size()[1] + 3 for layout, _ in layouts]
        available = h - body_top - pad
        start = len(layouts)
        used = 0
        while start > 0 and used + heights[start - 1] <= available:
            start -= 1
            used += heights[start]
        y = body_top
        for (layout, style), height in zip(layouts[start:], heights[start:]):
            if style == "line":
                cr.set_source_rgb(*TERM_GREEN)
            elif style == "cmd":
                cr.set_source_rgb(0.85, 0.88, 0.86)
            else:
                cr.set_source_rgb(*TERM_DIM)
            cr.move_to(pad, y)
            PangoCairo.show_layout(cr, layout)
            y += height

        self._draw_flash(cr, w, h, True)


# --------------------------------------------------------------------------
# the widget window
# --------------------------------------------------------------------------

class NowPlayingWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Now Playing")
        self.app = app
        self.settings = app.settings
        self.web = app.web
        self.mpris = None

        self.track = None
        self.track_key = None
        self.playing = False
        self.pos_base = 0
        self.pos_time = time.monotonic()
        self.shuffle = False
        self.repeat = "off"
        self.volume = None
        self.queue = []
        self.seeking = False
        self.volume_dragging = False
        self._updating = False
        self._api_busy = False
        self._api_ticks = 0
        self._queue_busy = False
        self._last_api_ok = 0
        self._mpris_position_ok = False
        self._lyrics_cache = {}
        self._seek_id = 0
        self._volume_id = 0
        self._skip_pending = 0
        self.bg = DEFAULT_BG
        self.context_uri = None
        self.autoplay = False
        self.queue_missed = False
        self.fullscreen_on = False
        self._layout_id = 0
        self._lyrics_offsets = {}

        self.set_decorated(False)
        self.set_keep_above(self.settings.get_bool("pinned", False))
        self.set_icon_name("multimedia-audio-player")
        self.get_style_context().add_class("np")
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None and screen.is_composited():
            self.set_visual(visual)
        self.bg_provider = Gtk.CssProvider()

        self._build()
        self._apply_bg(DEFAULT_BG)
        w = self.settings.get_int("width", 0)
        h = self.settings.get_int("height", 0)
        self.set_default_size(w if w > 300 else (860 if self.lyrics_button_on() else 480),
                              h if h > 300 else 620)
        if self.settings.get("x") is not None:
            self.move(self.settings.get_int("x"), self.settings.get_int("y"))

        self.connect("delete-event", self._on_delete)
        self.connect("configure-event", self._on_configure)
        self.connect("key-press-event", self._on_key)
        self.connect("window-state-event", self._on_window_state)
        self.connect("size-allocate", lambda *_: self._schedule_layout())

        self.mpris = Mpris(self._on_mpris)
        GLib.timeout_add(250, self._tick)
        GLib.timeout_add_seconds(3, self._api_poll)
        self._update_connect_ui()

    # ---------------------------------------------------------------- building
    def lyrics_button_on(self):
        return self.settings.get_bool("lyrics", True)

    def _build(self):
        self.frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.frame.get_style_context().add_class("np-frame")
        self.frame.get_style_context().add_provider(self.bg_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.add(self.frame)
        # Scanlines are painted right after the frame's contents (not as a layer on top,
        # which would swallow mouse clicks).
        self.frame.connect_after("draw", self._draw_scanlines)

        # header
        header_events = Gtk.EventBox()
        header_events.connect("button-press-event", self._on_header_press)
        header = Gtk.Box(spacing=4)
        header.set_margin_top(8)
        header.set_margin_start(16)
        header.set_margin_end(8)
        header_events.add(header)
        self.frame.pack_start(header_events, False, False, 0)

        brand = Gtk.Label(label="NOW PLAYING", xalign=0)
        brand.get_style_context().add_class("np-brand")
        header.pack_start(brand, False, False, 0)
        header.pack_start(Gtk.Box(), True, True, 0)

        self.ascii_button = Gtk.Button(label="ASCII")
        self.ascii_button.set_tooltip_text("Retro terminal mode: ASCII cover, green on black, typing lyrics")
        self.ascii_button.get_style_context().add_class("np-pill")
        self.ascii_button.set_can_focus(False)
        self.ascii_button.connect("clicked", lambda *_: self._toggle_ascii())

        self.lyrics_button = Gtk.Button(label="LYRICS")
        self.lyrics_button.set_tooltip_text("Show the lyrics terminal")
        self.lyrics_button.get_style_context().add_class("np-pill")
        self.lyrics_button.set_can_focus(False)
        self.lyrics_button.connect("clicked", lambda *_: self._toggle_lyrics())

        self.focus_button = Gtk.Button(label="LYRICS ONLY")
        self.focus_button.set_tooltip_text("Show only the lyrics (L)")
        self.focus_button.get_style_context().add_class("np-pill")
        self.focus_button.set_can_focus(False)
        self.focus_button.connect("clicked", lambda *_: self.toggle_focus())

        self.pin_button = icon_button(["view-pin-symbolic", "emblem-important-symbolic"], "pin",
                                      "Keep the widget above other windows", 16)
        self.pin_button.connect("clicked", lambda *_: self.toggle_pin())

        settings_button = icon_button(["emblem-system-symbolic", "preferences-system-symbolic"], "⚙",
                                      "Spotify account")
        settings_button.connect("clicked", lambda *_: self.open_setup())

        self.fullscreen_button = icon_button(["view-fullscreen-symbolic"], "⛶", "Fullscreen (F11)")
        self.fullscreen_button.connect("clicked", lambda *_: self.toggle_fullscreen())

        close_button = icon_button(["window-close-symbolic"], "✕", "Close")
        close_button.connect("clicked", lambda *_: self.app.quit())
        self.menu_button = icon_button(["open-menu-symbolic"], "\u2630", "Menu", 16)
        self.menu_button.connect("clicked", lambda b: self._show_menu(b))
        header.pack_start(self.menu_button, False, False, 0)
        header.pack_start(close_button, False, False, 0)

        # body: left column (player + queue), right column (lyrics terminal)
        body = self.body = Gtk.Box(spacing=18)
        body.set_margin_start(16)
        body.set_margin_end(16)
        body.set_margin_top(10)
        self.pages = Gtk.Stack()
        self.pages.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.pages.add_named(body, "player")
        self.frame.pack_start(self.pages, True, True, 0)

        left = self.left = FlexColumn()
        body.pack_start(left, False, True, 0)

        top = Gtk.Box(spacing=16)
        left.pack_start(top, False, False, 0)
        self.cover = CoverView()
        top.pack_start(self.cover, False, False, 0)

        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        info.set_size_request(110, -1)
        top.pack_start(info, True, True, 0)
        info.pack_start(Gtk.Box(), True, True, 0)
        self.title_label = Gtk.Label(label="Nothing playing", xalign=0)
        self.title_label.get_style_context().add_class("np-title")
        self.title_label.set_line_wrap(True)
        self.title_label.set_lines(2)
        self.title_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.title_label.set_max_width_chars(18)
        info.pack_start(self.title_label, False, False, 0)
        self.artist_label = Gtk.Label(label="", xalign=0)
        self.artist_label.get_style_context().add_class("np-artist")
        self.artist_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.artist_label.set_max_width_chars(22)
        info.pack_start(self.artist_label, False, False, 0)
        self.album_label = Gtk.Label(label="", xalign=0)
        self.album_label.get_style_context().add_class("np-album")
        self.album_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.album_label.set_max_width_chars(24)
        info.pack_start(self.album_label, False, False, 0)
        self.open_spotify_button = Gtk.Button(label="Open Spotify")
        self.open_spotify_button.get_style_context().add_class("np-connect")
        self.open_spotify_button.set_halign(Gtk.Align.START)
        self.open_spotify_button.set_can_focus(False)
        self.open_spotify_button.set_no_show_all(True)
        self.open_spotify_button.connect("clicked", lambda *_: self.launch_spotify())
        info.pack_start(self.open_spotify_button, False, False, 6)
        info.pack_start(Gtk.Box(), True, True, 0)

        # progress
        progress = Gtk.Box(spacing=8)
        left.pack_start(progress, False, False, 0)
        self.elapsed_label = Gtk.Label(label="0:00")
        self.elapsed_label.get_style_context().add_class("np-dim")
        self.elapsed_label.set_width_chars(5)
        progress.pack_start(self.elapsed_label, False, False, 0)
        self.progress = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1000, 1)
        self.progress.set_draw_value(False)
        self.progress.set_can_focus(False)
        self.progress.connect("button-press-event", self._on_seek_press)
        self.progress.connect("button-release-event", self._on_seek_release)
        self.progress.connect("change-value", self._on_seek_change)
        progress.pack_start(self.progress, True, True, 0)
        self.duration_label = Gtk.Label(label="0:00")
        self.duration_label.get_style_context().add_class("np-dim")
        self.duration_label.set_width_chars(5)
        progress.pack_start(self.duration_label, False, False, 0)

        # controls
        controls = Gtk.Box(spacing=10)
        controls.set_halign(Gtk.Align.CENTER)
        left.pack_start(controls, False, False, 0)
        self.shuffle_button = icon_button(["media-playlist-shuffle-symbolic"], "shuffle", "Shuffle", 16)
        self.shuffle_button.connect("clicked", lambda *_: self.toggle_shuffle())
        controls.pack_start(self.shuffle_button, False, False, 0)
        prev_button = icon_button(["media-skip-backward-symbolic"], "⏮", "Previous", 18)
        prev_button.connect("clicked", lambda *_: self.mpris.previous())
        controls.pack_start(prev_button, False, False, 0)
        self.play_button = icon_button(["media-playback-start-symbolic"], "▶", "Play / pause",
                                       18, "np-play")
        self.play_button.connect("clicked", lambda *_: self.play_pause())
        controls.pack_start(self.play_button, False, False, 0)
        next_button = icon_button(["media-skip-forward-symbolic"], "⏭", "Next", 18)
        next_button.connect("clicked", lambda *_: self.mpris.next())
        controls.pack_start(next_button, False, False, 0)
        self.repeat_button = icon_button(["media-playlist-repeat-symbolic"], "repeat", "Repeat", 16)
        self.repeat_button.connect("clicked", lambda *_: self.cycle_repeat())
        controls.pack_start(self.repeat_button, False, False, 0)

        volume_row = Gtk.Box(spacing=6)
        volume_row.set_halign(Gtk.Align.CENTER)
        left.pack_start(volume_row, False, False, 0)
        self.volume_icon = Gtk.Image.new_from_icon_name("audio-volume-high-symbolic", Gtk.IconSize.BUTTON)
        volume_row.pack_start(self.volume_icon, False, False, 0)
        self.volume_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.volume_scale.set_draw_value(False)
        self.volume_scale.set_can_focus(False)
        self.volume_scale.set_size_request(90, -1)
        self.volume_scale.connect("change-value", self._on_volume_change)
        self.volume_scale.connect("button-press-event", lambda *_: setattr(self, "volume_dragging", True))
        self.volume_scale.connect("button-release-event", lambda *_: setattr(self, "volume_dragging", False))
        volume_row.pack_start(self.volume_scale, False, False, 0)

        # queue
        queue_header = self.queue_header = Gtk.Box()
        up_next = Gtk.Label(label="UP NEXT", xalign=0)
        up_next.get_style_context().add_class("np-section")
        queue_header.pack_start(up_next, True, True, 0)
        refresh = icon_button(["view-refresh-symbolic"], "↻", "Reload the queue", 12)
        refresh.connect("clicked", lambda *_: self._fetch_queue())
        queue_header.pack_start(refresh, False, False, 0)
        left.pack_start(queue_header, False, False, 2)
        self.queue_note = Gtk.Label(xalign=0)
        self.queue_note.get_style_context().add_class("np-dim")
        self.queue_note.set_line_wrap(True)
        self.queue_note.set_max_width_chars(30)    # wrap instead of widening the column
        self.queue_note.set_no_show_all(True)
        left.pack_start(self.queue_note, False, False, 0)

        self.queue_stack = Gtk.Stack()
        self.queue_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        left.pack_start(self.queue_stack, True, True, 0)

        overlay = Gtk.Overlay()
        self.queue_scroller = Gtk.ScrolledWindow()
        self.queue_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.queue_scroller.set_size_request(-1, 96)
        self.queue_list = Gtk.ListBox()
        self.queue_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.queue_list.set_activate_on_single_click(True)
        self.queue_list.connect("row-activated", self._on_queue_row)
        self.queue_scroller.add(self.queue_list)
        overlay.add(self.queue_scroller)
        self.fade = Gtk.DrawingArea()
        self.fade.set_valign(Gtk.Align.END)
        self.fade.set_size_request(-1, 70)
        self.fade.connect("draw", self._draw_fade)
        overlay.add_overlay(self.fade)
        overlay.set_overlay_pass_through(self.fade, True)
        self.queue_stack.add_named(overlay, "list")

        connect_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, valign=Gtk.Align.CENTER)
        self.queue_message = Gtk.Label(label="")
        self.queue_message.get_style_context().add_class("np-dim")
        self.queue_message.set_line_wrap(True)
        self.queue_message.set_justify(Gtk.Justification.CENTER)
        connect_box.pack_start(self.queue_message, False, False, 0)
        self.connect_button = Gtk.Button(label="Connect Spotify account")
        self.connect_button.get_style_context().add_class("np-connect")
        self.connect_button.set_halign(Gtk.Align.CENTER)
        self.connect_button.set_can_focus(False)
        self.connect_button.set_no_show_all(True)
        self.connect_button.connect("clicked", lambda *_: self.open_setup())
        connect_box.pack_start(self.connect_button, False, False, 0)
        self.queue_stack.add_named(connect_box, "message")

        # lyrics terminal
        self.lyrics = LyricsView(self.position_ms)
        self.lyrics.set_margin_bottom(4)
        self.lyrics.set_no_show_all(True)
        body.pack_start(self.lyrics, True, True, 0)

        # "lyrics only" page: the lyrics fill the widget, a small bar below
        self.focus_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.focus_page.set_margin_start(16)
        self.focus_page.set_margin_end(16)
        self.focus_page.set_margin_top(10)
        self.focus_holder = Gtk.Box()
        self.focus_page.pack_start(self.focus_holder, True, True, 0)
        bar = Gtk.Box(spacing=8)
        now = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        self.focus_title = Gtk.Label(xalign=0)
        self.focus_title.set_ellipsize(Pango.EllipsizeMode.END)
        self.focus_title.get_style_context().add_class("np-focus-title")
        self.focus_artist = Gtk.Label(xalign=0)
        self.focus_artist.set_ellipsize(Pango.EllipsizeMode.END)
        self.focus_artist.get_style_context().add_class("np-dim")
        now.pack_start(self.focus_title, False, False, 0)
        now.pack_start(self.focus_artist, False, False, 0)
        bar.pack_start(now, True, True, 0)
        focus_prev = icon_button(["media-skip-backward-symbolic"], "\u23ee", "Previous", 18)
        focus_prev.connect("clicked", lambda *_: self.mpris.previous())
        bar.pack_start(focus_prev, False, False, 0)
        self.focus_play = icon_button(["media-playback-start-symbolic"], "\u25b6", "Play / pause", 18, "np-play")
        self.focus_play.connect("clicked", lambda *_: self.play_pause())
        bar.pack_start(self.focus_play, False, False, 0)
        focus_next = icon_button(["media-skip-forward-symbolic"], "\u23ed", "Next", 18)
        focus_next.connect("clicked", lambda *_: self.mpris.next())
        bar.pack_start(focus_next, False, False, 0)
        self.focus_page.pack_start(bar, False, False, 0)
        self.pages.add_named(self.focus_page, "focus")
        self.focus_on = False

        # footer: resize grip
        footer = Gtk.Box()
        footer.set_margin_end(4)
        footer.set_margin_bottom(4)
        grip = Gtk.EventBox()
        grip_label = Gtk.Label(label="◢")
        grip_label.get_style_context().add_class("np-grip")
        grip.add(grip_label)
        grip.connect("button-press-event", self._on_grip_press)
        grip.connect("realize", lambda w: w.get_window().set_cursor(
            Gdk.Cursor.new_from_name(w.get_display(), "se-resize")))
        footer.pack_end(grip, False, False, 0)
        self.frame.pack_start(footer, False, False, 0)

        for widget in (self.queue_header, self.queue_stack):
            widget.show_all()
            widget.set_no_show_all(True)
        self.queue_shown = True
        self.set_retro(self.settings.get_bool("ascii", False))
        self._set_pill(self.lyrics_button, self.lyrics_button_on())
        self._set_pill(self.pin_button, self.settings.get_bool("pinned", False))
        self.lyrics.set_visible(self.lyrics_button_on())

    # ---------------------------------------------------------------- look
    def _apply_bg(self, rgb):
        self.album_bg = rgb
        self._paint_bg()

    def _paint_bg(self):
        rgb = TERM_BG if getattr(self, "retro", False) else getattr(self, "album_bg", DEFAULT_BG)
        self.bg = rgb
        r, g, b = (int(c * 255) for c in rgb)
        self.bg_provider.load_from_data(f".np-frame {{ background-color: rgb({r},{g},{b}); }}".encode())
        self.fade.queue_draw()

    def _draw_fade(self, widget, cr):
        h = widget.get_allocated_height()
        gradient = cairo.LinearGradient(0, 0, 0, h)
        gradient.add_color_stop_rgba(0, *self.bg, 0)
        gradient.add_color_stop_rgba(0.75, *self.bg, 0.92)
        gradient.add_color_stop_rgba(1, *self.bg, 1)
        cr.set_source(gradient)
        cr.paint()
        return False

    @staticmethod
    def _set_pill(button, on):
        ctx = button.get_style_context()
        (ctx.add_class if on else ctx.remove_class)("np-on")

    def _toggle_ascii(self):
        self.set_retro(not self.retro)
        self.settings.set("ascii", self.retro)

    def set_retro(self, on):
        """Retro terminal mode: ASCII cover, green-on-black everything, typing lyrics."""
        self.retro = on
        self.cover.set_ascii(on)
        self.lyrics.retro = on
        self.lyrics.queue_draw()
        self._set_pill(self.ascii_button, on)
        ctx = self.frame.get_style_context()
        (ctx.add_class if on else ctx.remove_class)("np-retro")
        self.frame.queue_draw()
        self._paint_bg()
        for row in self.queue_list.get_children():
            for image in _find_images(row):
                original = getattr(image, "original", None)
                if original is not None:
                    image.set_from_pixbuf(green_tint(original) if on else original)

    def _draw_scanlines(self, widget, cr):
        if not getattr(self, "retro", False):
            return False
        w, h = widget.get_allocated_width(), widget.get_allocated_height()
        CoverView._rounded(cr, 0, 0, w, h, 0.01 if self.fullscreen_on else 12)
        cr.clip()
        cr.set_source_rgba(0, 0, 0, 0.22)
        y = 0
        while y < h:
            cr.rectangle(0, y, w, 1)
            y += 3
        cr.fill()
        return False

    def _show_menu(self, anchor):
        """One small menu with every setting (rebuilt each time so it shows the current state)."""
        menu = Gtk.Menu()
        menu.get_style_context().add_class("np-menu")
        if self.retro:
            menu.get_style_context().add_class("np-menu-retro")

        def check(label, active, callback):
            item = Gtk.CheckMenuItem(label=label)
            item.set_active(active)
            item.connect("toggled", lambda *_: callback())
            menu.append(item)

        def action(label, callback):
            item = Gtk.MenuItem(label=label)
            item.connect("activate", lambda *_: callback())
            menu.append(item)

        check("Retro terminal look", self.retro, self._toggle_ascii)
        check("Show lyrics", self.lyrics.get_visible() or self.focus_on, self._toggle_lyrics)
        check("Lyrics only  (L)", self.focus_on, self.toggle_focus)
        menu.append(Gtk.SeparatorMenuItem())
        check("Keep on top", self.settings.get_bool("pinned", False), self.toggle_pin)
        check("Fullscreen  (F11)", self.fullscreen_on, self.toggle_fullscreen)
        menu.append(Gtk.SeparatorMenuItem())
        action("Disconnect Spotify account\u2026" if self.web.connected else "Connect Spotify account\u2026",
               self.open_setup)
        action("Reload the queue", self._fetch_queue)
        menu.append(Gtk.SeparatorMenuItem())
        action("Quit", self.app.quit)
        menu.show_all()
        menu.attach_to_widget(anchor, None)
        menu.popup_at_widget(anchor, Gdk.Gravity.SOUTH_EAST, Gdk.Gravity.NORTH_EAST, None)

    def toggle_focus(self):
        """Lyrics-only mode: move the lyrics view to its own page and back."""
        self.focus_on = not self.focus_on
        parent = self.lyrics.get_parent()
        if parent is not None:
            parent.remove(self.lyrics)
        if self.focus_on:
            self.focus_holder.pack_start(self.lyrics, True, True, 0)
            self.lyrics.show()
            self.pages.set_visible_child_name("focus")
        else:
            self.body.pack_start(self.lyrics, True, True, 0)
            self.lyrics.set_visible(self.lyrics_button_on())
            self.pages.set_visible_child_name("player")
        self.lyrics.focus = self.focus_on
        self.lyrics._bold_cache = None
        self.lyrics._scroll = None
        self._set_pill(self.focus_button, self.focus_on)
        self.lyrics_button.set_sensitive(not self.focus_on)
        self._sync_focus_bar()
        self._schedule_layout()

    def _sync_focus_bar(self):
        title = self.title_label.get_text()
        artist = self.artist_label.get_text()
        if self.focus_title.get_text() != title:
            self.focus_title.set_text(title)
        if self.focus_artist.get_text() != artist:
            self.focus_artist.set_text(artist)

    def _toggle_lyrics(self):
        if self.focus_on:
            self.toggle_focus()
        on = not self.lyrics.get_visible()
        width, height = self.get_size()
        self.lyrics.set_visible(on)
        self._set_pill(self.lyrics_button, on)
        self.settings.set("lyrics", on)
        if not self.fullscreen_on:
            self.resize(max(width, 820) if on else max(360, min(width, 480)), height)
        self._schedule_layout()

    # ---------------------------------------------------------------- position
    def position_ms(self):
        if self.playing and not self.seeking:
            pos = self.pos_base + (time.monotonic() - self.pos_time) * 1000
        else:
            pos = self.pos_base
        duration = (self.track or {}).get("duration_ms") or 0
        return min(pos, duration) if duration else pos

    def _set_position(self, ms):
        self.pos_base = max(0, ms)
        self.pos_time = time.monotonic()

    # ---------------------------------------------------------------- updates from Spotify
    def _on_mpris(self):
        if not self.mpris.available:
            if self.track_key != "__offline__":
                self.track_key = "__offline__"
                self.track = None
                self.playing = False
                self.title_label.set_text("Spotify is not running")
                self.artist_label.set_text("")
                self.album_label.set_text("")
                self.open_spotify_button.show()
                self.cover.set_pixbuf(None)
                self._apply_bg(DEFAULT_BG)
                self.lyrics.set_song("")
                self.lyrics.set_lyrics("offline", [])
                self._set_queue([])
                self._update_play_icon()
            return

        self.open_spotify_button.hide()
        track = self.mpris.track()
        if self.mpris.playing != self.playing:
            self._set_position(self.position_ms())     # freeze/unfreeze at the current spot
            self.playing = self.mpris.playing
            self._update_play_icon()

        if track is None:
            if self.track_key != "__none__":
                self.track_key = "__none__"
                self.track = None
                self.title_label.set_text("Nothing playing")
                self.artist_label.set_text("Start a song in Spotify")
                self.album_label.set_text("")
                self.lyrics.set_song("")
                self.lyrics.set_lyrics("idle", [])
            return

        if track["key"] != self.track_key:
            self._on_new_track(track)

        # Without the Web API, use the position Spotify reports over D-Bus.
        pos_us = self.mpris.props.get("Position")
        if not self.web.connected or time.time() - self._last_api_ok > 10:
            if isinstance(pos_us, int) and pos_us > 0:
                self._mpris_position_ok = True
                if abs(pos_us / 1000 - self.position_ms()) > 1500 or not self.playing:
                    self._set_position(pos_us / 1000)
            if self.mpris.props.get("Shuffle") is not None:
                self.shuffle = bool(self.mpris.props.get("Shuffle"))
            loop = self.mpris.props.get("LoopStatus")
            if loop:
                self.repeat = {"None": "off", "Playlist": "context", "Track": "track"}.get(loop, "off")
            self._update_mode_buttons()

    def _on_new_track(self, track):
        # Did Spotify play what its queue said would come next?
        if self.queue and self.track_key not in (None, "__none__", "__offline__"):
            expected = self.queue[0]["title"].strip().lower()
            self.queue_missed = expected != track["title"].strip().lower()
            self._update_queue_note()
        self.track_key = track["key"]
        self.track = track
        self._set_position(0)
        self.title_label.set_text(track["title"])
        self.artist_label.set_text(track["artists"])
        self.album_label.set_text(track["album"])
        self.duration_label.set_text(fmt_time(track["duration_ms"]))
        self.load_cover(track["art_url"], track["key"])
        self.load_lyrics(track)
        GLib.timeout_add(1200, lambda: self._fetch_queue() or False)
        GLib.timeout_add(5000, lambda: self._fetch_queue() or False)
        self.lyrics.offset_ms = self._lyrics_offsets.get(track["key"], 0)
        GLib.timeout_add(400, lambda: self._api_poll() and False)

    def load_cover(self, url, key):
        if not url:
            self.cover.set_pixbuf(None)
            self._apply_bg(DEFAULT_BG)
            return
        if url.startswith("file://"):
            work = lambda: Gio.File.new_for_uri(url).get_path()  # noqa: E731
        else:
            work = lambda: cached_download(url)  # noqa: E731

        def done(result):
            if key != self.track_key or isinstance(result, Exception) or not result:
                return
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(result, 480, 480, True)
            except GLib.Error:
                return
            self.cover.set_pixbuf(pixbuf)
            self._apply_bg(widget_background(average_color(pixbuf)))
        run_async(work, done)

    def load_lyrics(self, track):
        self.lyrics.set_song(track["title"])
        key = track["key"]
        if key in self._lyrics_cache:
            self.lyrics.set_lyrics(*self._lyrics_cache[key])
            return

        def work():
            params = {"track_name": track["title"], "artist_name": track["first_artist"]}
            if track["album"]:
                params["album_name"] = track["album"]
            if track["duration_ms"]:
                params["duration"] = str(round(track["duration_ms"] / 1000))
            status, body, _ = http_request("GET", f"{LRCLIB}/get?" + urllib.parse.urlencode(params))
            record = json.loads(body) if status == 200 else None
            if record is None or not (record.get("syncedLyrics") or record.get("plainLyrics")
                                      or record.get("instrumental")):
                status, body, _ = http_request("GET", f"{LRCLIB}/search?" + urllib.parse.urlencode(
                    {"track_name": track["title"], "artist_name": track["first_artist"]}))
                results = json.loads(body) if status == 200 else []
                results = [r for r in results if isinstance(r, dict)]
                if track["duration_ms"]:
                    # Prefer versions of the same length (the same recording, so the timing fits).
                    target = track["duration_ms"] / 1000
                    results.sort(key=lambda r: abs((r.get("duration") or 0) - target) > 3)
                record = (next((r for r in results if r.get("syncedLyrics")), None)
                          or next((r for r in results if r.get("plainLyrics")), None))
            if not record:
                return ("none", [])
            if record.get("instrumental"):
                return ("instrumental", [])
            synced = parse_lrc(record.get("syncedLyrics"))
            if synced:
                return ("synced", synced)
            plain = [l.strip() for l in (record.get("plainLyrics") or "").splitlines()]
            plain = [l for l in plain if l]
            if not plain:
                return ("none", [])
            duration = track["duration_ms"] or 180000
            step = duration * 0.9 / len(plain)
            return ("plain", [(int(duration * 0.05 + i * step), l) for i, l in enumerate(plain)])

        def done(result):
            if isinstance(result, Exception):
                print(f"now-playing: lyrics lookup failed: {result}", file=sys.stderr)
                result = ("none", [])
            self._lyrics_cache[key] = result
            if key == self.track_key:
                self.lyrics.set_lyrics(*result)
        run_async(work, done)

    # ---------------------------------------------------------------- Web API polling
    def _api_poll(self):
        if not self.web.connected or self._api_busy or not (self.mpris and self.mpris.available):
            return True
        self._api_busy = True
        self._api_ticks += 1
        started = time.monotonic()

        def done(state):
            self._api_busy = False
            if isinstance(state, Exception) or state is None:
                self._update_connect_ui()
                return
            self._last_api_ok = time.time()
            if not state:        # 204: no active device
                return
            item = state.get("item") or {}
            if self.track and item.get("id") and item.get("id") != self.track.get("id"):
                return           # the API is still catching up with a track change
            if not self.seeking and state.get("progress_ms") is not None:
                latency = (time.monotonic() - started) * 500
                self._set_position(state["progress_ms"] + (latency if state.get("is_playing") else 0))
            self.shuffle = bool(state.get("shuffle_state"))
            self.repeat = state.get("repeat_state") or "off"
            device = state.get("device") or {}
            if device.get("volume_percent") is not None and not self.volume_dragging and not self._volume_id:
                self.volume = device["volume_percent"]
                self._updating = True
                self.volume_scale.set_value(self.volume)
                self._updating = False
            self.context_uri = (state.get("context") or {}).get("uri")
            disallows = (state.get("actions") or {}).get("disallows") or {}
            autoplay = bool(disallows.get("toggling_shuffle") and disallows.get("toggling_repeat_context"))
            if autoplay != self.autoplay:
                self.autoplay = autoplay
                self._update_queue_note()
            self._update_mode_buttons()

        run_async(lambda: self.web.call("GET", "/me/player"), done)
        return True

    def _fetch_queue(self):
        if not self.web.connected or self._queue_busy:
            self._update_connect_ui()
            return
        self._queue_busy = True

        def done(result):
            self._queue_busy = False
            if isinstance(result, Exception) or result is None:
                self._update_connect_ui()
                return
            items = []
            for entry in (result.get("queue") or [])[:30]:
                if not isinstance(entry, dict):
                    continue
                if entry.get("type") == "episode":
                    artist = (entry.get("show") or {}).get("name", "")
                    images = entry.get("images") or []
                else:
                    artist = ", ".join(a.get("name", "") for a in entry.get("artists") or [])
                    images = (entry.get("album") or {}).get("images") or []
                small = min(images, key=lambda im: im.get("width") or 9999)["url"] if images else None
                items.append({"title": entry.get("name", ""), "artist": artist,
                              "duration_ms": entry.get("duration_ms") or 0, "image": small,
                              "uri": entry.get("uri")})
            self._set_queue(items)
            self._update_connect_ui()
        run_async(lambda: self.web.call("GET", "/me/player/queue"), done)

    def _set_queue(self, items):
        if [(i["title"], i["artist"]) for i in items] == [(i["title"], i["artist"]) for i in self.queue]:
            return
        self.queue = items
        for child in self.queue_list.get_children():
            self.queue_list.remove(child)
        for index, item in enumerate(items):
            row = Gtk.ListBoxRow()
            row.index = index
            box = Gtk.Box(spacing=10)
            thumb = Gtk.Image()
            thumb.set_size_request(40, 40)
            box.pack_start(thumb, False, False, 0)
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
            title = Gtk.Label(label=item["title"], xalign=0)
            title.set_ellipsize(Pango.EllipsizeMode.END)
            title.set_markup(f"<b>{GLib.markup_escape_text(item['title'])}</b>")
            artist = Gtk.Label(label=item["artist"], xalign=0)
            artist.set_ellipsize(Pango.EllipsizeMode.END)
            artist.get_style_context().add_class("np-dim")
            text.pack_start(title, False, False, 0)
            text.pack_start(artist, False, False, 0)
            box.pack_start(text, True, True, 0)
            duration = Gtk.Label(label=fmt_time(item["duration_ms"]))
            duration.get_style_context().add_class("np-dim")
            box.pack_start(duration, False, False, 0)
            row.add(box)
            row.set_tooltip_text("Play this song")
            self.queue_list.add(row)
            if item["image"]:
                self._load_thumb(item["image"], thumb)
        self.queue_list.show_all()
        self.queue_scroller.get_vadjustment().set_value(0)

    def _load_thumb(self, url, image):
        def done(path):
            if isinstance(path, Exception):
                return
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 40, 40, True)
            except GLib.Error:
                return
            image.original = pixbuf
            image.set_from_pixbuf(green_tint(pixbuf) if self.retro else pixbuf)
        run_async(lambda: cached_download(url), done)

    def _on_queue_row(self, _list, row):
        item = self.queue[row.index] if row.index < len(self.queue) else {}
        uri = item.get("uri")
        if not uri or not self.web.connected:
            self._skip_forward(row.index + 1)
            return
        context = self.context_uri
        row.set_opacity(0.5)

        def work():
            # Play it inside the current playlist/album so the rest keeps going from there;
            # if the song isn't part of it (e.g. added to the queue by hand), play it on its own.
            if context and context.startswith(("spotify:playlist:", "spotify:album:")):
                if self.web.call("PUT", "/me/player/play",
                                 body={"context_uri": context, "offset": {"uri": uri}}) is not None:
                    return True
            return self.web.call("PUT", "/me/player/play", body={"uris": [uri]}) is not None

        def done(ok):
            if ok is not True:
                self._skip_forward(row.index + 1)
        run_async(work, done)

    def _skip_forward(self, steps):
        # Fallback: skip forward until we get there.
        self._skip_pending = steps

        def step():
            if self._skip_pending <= 0:
                return False
            self._skip_pending -= 1
            self.mpris.next()
            return self._skip_pending > 0
        step()
        if self._skip_pending:
            GLib.timeout_add(450, step)

    def _update_queue_note(self):
        if self.autoplay:
            text = ("Spotify is on autoplay (shuffle and repeat are greyed out). In this mode Spotify "
                    "reports a different list than the songs it will actually play.")
        elif self.queue_missed:
            text = "Spotify played a different song than its list said, so this list may be off."
        else:
            text = ""
        self.queue_note.set_text(text)
        self.queue_note.set_visible(bool(text) and getattr(self, "queue_shown", True))

    def toggle_pin(self):
        pinned = not self.settings.get_bool("pinned", False)
        self.settings.set("pinned", pinned)
        self.set_keep_above(pinned)
        self._set_pill(self.pin_button, pinned)
        self.pin_button.set_tooltip_text("Pinned: stays above other windows" if pinned
                                         else "Keep the widget above other windows")

    def _update_connect_ui(self):
        if self.web.connected and self.queue:
            self.queue_stack.set_visible_child_name("list")
        elif self.web.connected:
            self.queue_message.set_text("The queue is empty" if time.time() - self._last_api_ok < 15
                                        else "Waiting for Spotify…")
            self.connect_button.hide()
            self.queue_stack.set_visible_child_name("message")
        else:
            self.queue_message.set_text("Connect your Spotify account to see\n"
                                        "the upcoming songs in your queue.")
            self.connect_button.show()
            self.queue_stack.set_visible_child_name("message")

    # ---------------------------------------------------------------- controls
    def play_pause(self):
        self._set_position(self.position_ms())
        self.playing = not self.playing
        self._update_play_icon()
        self.mpris.play_pause()

    def _update_play_icon(self):
        for button in (self.play_button, self.focus_play):
            if self.playing:
                set_button_icon(button, ["media-playback-pause-symbolic"], "\u275a\u275a", 18)
            else:
                set_button_icon(button, ["media-playback-start-symbolic"], "\u25b6", 18)

    def toggle_shuffle(self):
        self.shuffle = not self.shuffle
        self._update_mode_buttons()
        state = self.shuffle
        if self.web.connected:
            run_async(lambda: self.web.call("PUT", "/me/player/shuffle", {"state": str(state).lower()}),
                      lambda _r: GLib.timeout_add(600, lambda: self._fetch_queue() or False))
        else:
            self.mpris.set_property("Shuffle", GLib.Variant("b", state))

    def cycle_repeat(self):
        order = ["off", "context", "track"]
        self.repeat = order[(order.index(self.repeat) + 1) % 3] if self.repeat in order else "context"
        self._update_mode_buttons()
        state = self.repeat
        if self.web.connected:
            run_async(lambda: self.web.call("PUT", "/me/player/repeat", {"state": state}))
        else:
            loop = {"off": "None", "context": "Playlist", "track": "Track"}[state]
            self.mpris.set_property("LoopStatus", GLib.Variant("s", loop))

    def _update_mode_buttons(self):
        self._set_pill(self.shuffle_button, self.shuffle)
        self._set_pill(self.repeat_button, self.repeat != "off")
        if self.repeat == "track":
            set_button_icon(self.repeat_button, ["media-playlist-repeat-song-symbolic",
                                                 "media-playlist-repeat-symbolic"], "repeat 1", 16)
            self.repeat_button.set_tooltip_text("Repeat: this song")
        else:
            set_button_icon(self.repeat_button, ["media-playlist-repeat-symbolic"], "repeat", 16)
            self.repeat_button.set_tooltip_text("Repeat: " + ("on" if self.repeat == "context" else "off"))

    def _on_seek_press(self, *_):
        self.seeking = True
        return False

    def _on_seek_change(self, _scale, _scroll, value):
        duration = (self.track or {}).get("duration_ms") or 0
        self.elapsed_label.set_text(fmt_time(value / 1000 * duration))
        if not self.seeking:           # keyboard or scroll wheel
            self._commit_seek(value)
        return False

    def _on_seek_release(self, scale, _event):
        GLib.idle_add(lambda: self._commit_seek(scale.get_value()) and False)
        return False

    def _commit_seek(self, value):
        self.seeking = False
        duration = (self.track or {}).get("duration_ms") or 0
        if not duration:
            return
        target = int(max(0, min(1, value / 1000)) * duration)
        self._set_position(target)
        if self.web.connected:
            run_async(lambda: self.web.call("PUT", "/me/player/seek", {"position_ms": target}))
        else:
            self.mpris.set_position((self.track or {}).get("path"), target)

    def _on_volume_change(self, _scale, _scroll, value):
        if self._updating:
            return False
        value = int(max(0, min(100, value)))
        self.volume = value
        if self._volume_id:
            GLib.source_remove(self._volume_id)

        def send():
            self._volume_id = 0
            if self.web.connected:
                run_async(lambda: self.web.call("PUT", "/me/player/volume", {"volume_percent": value}))
            else:
                self.mpris.set_property("Volume", GLib.Variant("d", value / 100))
            return False
        self._volume_id = GLib.timeout_add(250, send)
        return False

    def launch_spotify(self):
        for desktop_id in ("spotify.desktop", "com.spotify.Client.desktop", "spotify_spotify.desktop"):
            try:
                info = Gio.DesktopAppInfo.new(desktop_id)
            except TypeError:
                info = None
            if info is not None:
                info.launch([], None)
                return
        try:
            GLib.spawn_async(["spotify"], flags=GLib.SpawnFlags.SEARCH_PATH)
        except GLib.Error:
            self.title_label.set_text("Spotify is not installed?")

    # ---------------------------------------------------------------- periodic UI refresh
    def _tick(self):
        duration = (self.track or {}).get("duration_ms") or 0
        pos = self.position_ms()
        if not self.seeking:
            self._updating = True
            self.progress.set_value(pos / duration * 1000 if duration else 0)
            self._updating = False
            self.elapsed_label.set_text(fmt_time(pos))
        if self.focus_on:
            self._sync_focus_bar()
        return True

    # ---------------------------------------------------------------- Spotify account setup
    def open_setup(self):
        dialog = Gtk.Dialog(title="Spotify account", transient_for=self, modal=True)
        dialog.set_keep_above(True)
        dialog.set_default_size(520, -1)
        area = dialog.get_content_area()
        area.set_spacing(10)
        area.set_border_width(16)

        if self.web.connected:
            label = Gtk.Label(xalign=0)
            label.set_markup("<b>Connected to Spotify.</b>\nThe queue, shuffle, repeat, volume and "
                             "seeking use your account.")
            area.pack_start(label, False, False, 0)
            dialog.add_button("Disconnect", 1)
            dialog.add_button("Close", Gtk.ResponseType.CLOSE)
            dialog.show_all()
            if dialog.run() == 1:
                self.web.disconnect()
                self._set_queue([])
                self._update_connect_ui()
            dialog.destroy()
            return

        steps = Gtk.Label(xalign=0)
        steps.set_line_wrap(True)
        steps.set_max_width_chars(60)
        steps.set_markup(
            "<b>One-time setup (about 2 minutes)</b>\n\n"
            "1. Open <a href='https://developer.spotify.com/dashboard'>developer.spotify.com/dashboard</a> "
            "and open your app (or <b>Create app</b>; any name).\n"
            "2. In the app's <b>Settings</b>, add this <b>Redirect URI</b> and save:\n"
            f"      <tt>{REDIRECT_URI}</tt>\n"
            "3. Under <b>APIs used</b>, tick <b>Web API</b>.\n"
            "4. Copy the <b>Client ID</b> and paste it below, then press <b>Log in</b>.")
        area.pack_start(steps, False, False, 0)

        copy_button = Gtk.Button(label="Copy Redirect URI")
        copy_button.set_halign(Gtk.Align.START)
        copy_button.connect("clicked", lambda *_: Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
                            .set_text(REDIRECT_URI, -1))
        area.pack_start(copy_button, False, False, 0)

        entry = Gtk.Entry()
        entry.set_placeholder_text("Client ID")
        entry.set_text(self.web.client_id)
        area.pack_start(entry, False, False, 0)
        status = Gtk.Label(xalign=0)
        status.set_line_wrap(True)
        status.set_selectable(True)
        area.pack_start(status, False, False, 0)

        login = dialog.add_button("Log in with Spotify", 1)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)

        def on_response(dlg, response):
            if response != 1:
                dlg.destroy()
                return
            client_id = entry.get_text().strip()
            if not re.fullmatch(r"[0-9a-fA-F]{32}", client_id):
                status.set_markup("<span foreground='#e05555'>That doesn't look like a Client ID "
                                  "(32 letters and numbers).</span>")
                return
            self.settings.set("client_id", client_id)
            login.set_sensitive(False)
            status.set_text("Waiting for you to log in in the browser…")

            def done(ok, message):
                if ok:
                    dlg.destroy()
                    self._update_connect_ui()
                    self._api_poll()
                    self._fetch_queue()
                else:
                    login.set_sensitive(True)
                    status.set_markup(f"<span foreground='#e05555'>{GLib.markup_escape_text(message)}</span>")
            self.web.login(client_id, done)

        dialog.connect("response", on_response)
        dialog.show_all()

    # ---------------------------------------------------------------- adaptive layout
    def _schedule_layout(self):
        if not self._layout_id:
            self._layout_id = GLib.idle_add(self._relayout)

    def _relayout(self):
        self._layout_id = 0
        w, h = self.get_size()
        if self.focus_on:
            self.lyrics.font_size = round(10.5 * min(2.6, max(1.0, h / 480)), 1)
            ctx = self.frame.get_style_context()
            (ctx.add_class if self.fullscreen_on else ctx.remove_class)("np-big")
            return False
        lyrics_on = self.lyrics.get_visible()
        inner = max(200, w - 32)
        if lyrics_on:
            left_w = int(min(max(inner * 0.5, 280), 640))
        else:
            left_w = inner
        self.left.set_natural(left_w)
        self.body.set_child_packing(self.left, not lyrics_on, True, 0, Gtk.PackType.START)
        # Small window: drop the queue and give its room to the cover.
        show_queue = h >= 560
        if show_queue != self.queue_shown:
            self.queue_shown = show_queue
            self.queue_header.set_visible(show_queue)
            self.queue_stack.set_visible(show_queue)
            self._update_queue_note()
        # The cover gets the room the rest of the left column leaves over
        # (with the queue showing, it keeps room for about three songs).
        room = (h - 400) if show_queue else (h - 235)
        cover = int(min(left_w * 0.48, room if room > 90 else h * 0.2, 460))
        self.cover.set_natural(max(CoverView.MIN, cover))
        big = self.fullscreen_on or (w > 1150 and h > 750)
        ctx = self.frame.get_style_context()
        (ctx.add_class if big else ctx.remove_class)("np-big")
        self.lyrics.font_size = round(10.5 * min(2.1, max(0.85, h / 560)), 1)
        return False

    def toggle_fullscreen(self):
        if self.fullscreen_on:
            self.unfullscreen()
        else:
            self.fullscreen()

    def _on_window_state(self, _widget, event):
        self.fullscreen_on = bool(event.new_window_state & Gdk.WindowState.FULLSCREEN)
        ctx = self.frame.get_style_context()
        (ctx.add_class if self.fullscreen_on else ctx.remove_class)("np-fullscreen")   # square corners
        set_button_icon(self.fullscreen_button,
                        ["view-restore-symbolic"] if self.fullscreen_on else ["view-fullscreen-symbolic"],
                        "⛶", 16)
        self.fullscreen_button.set_tooltip_text("Leave fullscreen (F11)" if self.fullscreen_on
                                                else "Fullscreen (F11)")
        self._schedule_layout()
        return False

    # ---------------------------------------------------------------- window behaviour
    def _on_header_press(self, _widget, event):
        if event.button != 1:
            return False
        if event.type == DOUBLE_CLICK:
            self.toggle_fullscreen()
        elif event.type == Gdk.EventType.BUTTON_PRESS and not self.fullscreen_on:
            self.begin_move_drag(event.button, int(event.x_root), int(event.y_root), event.time)
        return False

    def _on_grip_press(self, _widget, event):
        if event.button == 1 and not self.fullscreen_on:
            self.begin_resize_drag(Gdk.WindowEdge.SOUTH_EAST, event.button,
                                   int(event.x_root), int(event.y_root), event.time)
        return True

    def _on_configure(self, *_):
        if getattr(self, "_geom_id", 0):
            GLib.source_remove(self._geom_id)

        def save():
            self._geom_id = 0
            x, y = self.get_position()
            w, h = self.get_size()
            for key, value in (("x", x), ("y", y), ("width", w), ("height", h)):
                self.settings.kf.set_integer(Settings.GROUP, key, value)
            self.settings.set("lyrics", self.lyrics.get_visible())
            return False
        self._geom_id = GLib.timeout_add(600, save)
        return False

    def _on_key(self, _widget, event):
        key = event.keyval
        if key == Gdk.KEY_space:
            self.play_pause()
        elif key == Gdk.KEY_Right:
            self.mpris.next()
        elif key == Gdk.KEY_Left:
            self.mpris.previous()
        elif key in (Gdk.KEY_l, Gdk.KEY_L):
            self.toggle_focus()
        elif key == Gdk.KEY_F11:
            self.toggle_fullscreen()
        elif key == Gdk.KEY_Escape:
            if self.fullscreen_on:
                self.unfullscreen()
            else:
                self.hide()
        elif key in (Gdk.KEY_bracketleft, Gdk.KEY_bracketright):
            self.lyrics.shift(-250 if key == Gdk.KEY_bracketleft else 250)
            if self.track_key:
                self._lyrics_offsets[self.track_key] = self.lyrics.offset_ms
        else:
            return False
        return True

    def _on_delete(self, *_):
        self.app.quit()
        return True


# --------------------------------------------------------------------------
# application
# --------------------------------------------------------------------------

class NowPlayingApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.window = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.settings = Settings()
        self.web = SpotifyWeb(self.settings)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.window = NowPlayingWindow(self)

    def do_command_line(self, command_line):
        args = set(command_line.get_arguments()[1:])
        if "--quit" in args:
            self.quit()
        elif "--toggle" in args and self.window.get_visible():
            self.window.hide()
        else:
            self.window.show_all()
            self.window.present()
        return 0


def debug_queue():
    """Print exactly what the Spotify Web API reports, to compare with the Spotify app."""
    web = SpotifyWeb(Settings())
    if not web.connected:
        print("Not connected: use the menu in the widget: Connect Spotify account.")
        return 1
    state = web.call("GET", "/me/player")
    if state is None:
        print("Spotify did not answer (network problem or login expired).")
        return 1
    if not state:
        print("Spotify reports no active player. Start playing something first.")
        return 0
    item = state.get("item") or {}
    artists = ", ".join(a.get("name", "") for a in item.get("artists") or [])
    print(f"Now playing : {item.get('name')} - {artists}")
    print(f"Context     : {(state.get('context') or {}).get('uri')}")
    print(f"Shuffle     : {state.get('shuffle_state')}   Repeat: {state.get('repeat_state')}")
    disallows = (state.get("actions") or {}).get("disallows") or {}
    print(f"Blocked     : {', '.join(k for k, v in disallows.items() if v) or 'nothing'}")
    queue = web.call("GET", "/me/player/queue") or {}
    print("Queue according to the Spotify Web API:")
    for i, entry in enumerate((queue.get("queue") or [])[:10], 1):
        names = ", ".join(a.get("name", "") for a in entry.get("artists") or []) or \
            (entry.get("show") or {}).get("name", "")
        print(f"  {i:2}. {entry.get('name')} - {names}")
    return 0


def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        return 0
    if "--debug-queue" in sys.argv:
        return debug_queue()
    return NowPlayingApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
