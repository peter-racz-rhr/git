# Now Playing

An always-on-top Spotify widget for Linux Mint: album cover (as a picture or old-school
ASCII art), progress bar, controls, the upcoming songs in your queue, and lyrics that type
themselves in a little terminal, in sync with the song.

## Install

```bash
cd now-playing
./install.sh
```

Running it again always removes the old version first. Want the widget to start when you
log in? `AUTOSTART=1 ./install.sh`

For a keyboard shortcut, add a custom shortcut in *Keyboard > Shortcuts > Custom Shortcuts*
with the command `/home/<you>/.local/bin/now-playing --toggle` (shows / hides the widget).

## Connect your Spotify account (once)

Play/pause, next and previous work right away through the Spotify app. The **queue**,
shuffle, repeat, volume and seeking need your Spotify developer app:

1. Open <https://developer.spotify.com/dashboard> and open your app (or create one).
2. In its **Settings**, add the Redirect URI `http://127.0.0.1:8888/callback` and save.
3. Under **APIs used**, tick **Web API**.
4. In the widget, click the **gear** button, paste the app's **Client ID** and press
   **Log in with Spotify**. Your browser opens; log in and allow access.

The login is stored in `~/.config/now-playing/token.json` (only readable by you).
Disconnect any time from the gear button.

## Using it

| Control | What it does |
|---|---|
| **ASCII** | show the cover as green terminal ASCII art |
| **LYRICS** | show / hide the lyrics terminal |
| gear | connect / disconnect your Spotify account |
| progress bar | click or drag to jump in the song |
| shuffle, previous, play/pause, next, repeat | as in Spotify (repeat: off > all > this song) |
| volume slider | Spotify's volume |
| **Up next** | the next songs in your queue; scroll for more. Click one to skip to it |
| Space / Left / Right | play-pause / previous / next (when the widget is focused) |
| Esc | hide the widget |
| top bar | drag to move; bottom-right corner resizes |

The background color follows the album cover.

## Lyrics

Lyrics come from [LRCLIB](https://lrclib.net), a free lyrics database. Most songs have
timed lyrics, so every line types itself out at the moment it is sung. For songs with only
plain lyrics, the lines are spread over the length of the song. Some songs have no lyrics
there at all; the terminal says so.

## Notes

- Clicking a song in the queue skips forward to it (Spotify has no "play this queued song"
  command, so the widget presses Next the right number of times).
- Uninstall with `./uninstall.sh` (add `--forget` to also delete your Spotify login).

## Requirements

Python 3 with GTK 3 and cairo (`python3-gi`, `python3-gi-cairo`, `gir1.2-gtk-3.0`). Linux
Mint ships all of them. The Spotify desktop app (deb, Flatpak or Snap).
