# Now Playing

A Spotify widget for Linux Mint: album cover, progress bar, controls, the upcoming songs in
your queue, and lyrics in sync with the song. Two looks:

- **Normal:** the background takes the album's color, and the lyrics are big and bold. The
  current line fills up while it is sung and scrolls along smoothly.
- **Retro (menu > Retro terminal look):** a green-on-black (pure black) terminal with scanlines. The cover becomes
  detailed ASCII art, and the lyrics type themselves out at a terminal prompt.

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
4. In the widget, open the **menu** (top right) > **Connect Spotify account**, paste the app's **Client ID** and press
   **Log in with Spotify**. Your browser opens; log in and allow access.

The login is stored in `~/.config/now-playing/token.json` (only readable by you).
Disconnect any time from the same menu.

## Using it

| Control | What it does |
|---|---|
| menu button (three lines, top right) | all settings: retro terminal look, show lyrics, lyrics only, keep on top, fullscreen, Spotify account, reload the queue, quit |
| `L` | lyrics only: big lyrics with the song name and previous / play / next at the bottom |
| F11 / double-click the top bar | fullscreen (Esc or F11 to leave) |
| progress bar | click or drag to jump in the song |
| shuffle, previous, play/pause, next, repeat | as in Spotify (repeat: off > all > this song) |
| volume slider | Spotify's volume |
| **Up next** | the next songs in your queue; scroll for more. Click one to play it. The reload button refreshes the list |
| `[` / `]` | lyrics a quarter second earlier / later (when the lyrics are off for a song) |
| Space / Left / Right | play-pause / previous / next (when the widget is focused) |
| Esc | hide the widget |
| top bar | drag to move; bottom-right corner resizes (everything scales with the window; in a small window the queue is hidden) |

The background color follows the album cover.

## Lyrics

Lyrics come from [LRCLIB](https://lrclib.net), a free lyrics database. Most songs have
timed lyrics, so every line lights up (or types itself out, in retro mode) at the moment it
is sung. For songs with only
plain lyrics, the lines are spread over the length of the song. Some songs have no lyrics
there at all; the terminal says so.

## Notes

- The queue is loaded when the song changes (and when you press reload), not constantly:
  Spotify's queue list is unreliable when asked repeatedly, especially with shuffle on.
  With shuffle on, Spotify may still play a different song next than its list says.
- On **autoplay** (after an album or playlist ends, shuffle and repeat are greyed out in
  Spotify), Spotify reports a different list than the songs it actually plays. The widget
  detects this and says so above the list. `now-playing --debug-queue` prints exactly what
  Spotify reports, to compare with the queue in the Spotify app.
- Clicking a song in the queue plays it directly inside your current playlist or album.
- Uninstall with `./uninstall.sh` (add `--forget` to also delete your Spotify login).

## Requirements

Python 3 with GTK 3 and cairo (`python3-gi`, `python3-gi-cairo`, `gir1.2-gtk-3.0`). Linux
Mint ships all of them. The Spotify desktop app (deb, Flatpak or Snap).
