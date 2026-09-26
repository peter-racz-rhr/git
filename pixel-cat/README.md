# Pixel Cat

A little pixel-art cat that lives on your desktop. She walks along the tops of your
windows, naps on them, jumps from one to another, climbs up the edges of the screen,
chases your mouse, and purrs when you pet her. When music plays she puts on a tiny
headset and bobs her head.

![Six coats to choose from](../docs/screenshots/pixel-cat-coats.png)

![What she does](../docs/screenshots/pixel-cat-showcase.png)

## Install

```bash
cd pixel-cat
./install.sh
```

The first time, a small window opens. Pick her coat (orange tabby, black, grey & white,
white, calico or siamese) and give her a name. Then she drops onto your desktop.

The installer also:
- starts her automatically when you log in
- adds **Pixel Cat** to the menu (opening it while she's running calls her to your mouse)
- sets up **Ctrl+Alt+C** to call her. If that's taken, she picks the next free one; the
  installer tells you which (also `pixel-cat --shortcut`)

Bigger or smaller cat: `SIZE=3 ./install.sh` (1 = small, 2 = normal, about 48 px, 3 = big).
Uninstall with `./uninstall.sh` (add `--forget` to also forget her name and mood).

## Living with her

| You do | She does |
|---|---|
| Rub the mouse back and forth over her | purrs, closes her eyes, and pixel hearts float up |
| Click her | a heart |
| Drag her | dangles from your pointer; let go (or throw her) and she lands on her feet |
| Move a window she's sitting on | rides along |
| Close or minimize that window | falls and lands on whatever is below |
| **Ctrl+Alt+C** | comes running to your mouse, or pops over to it |
| Right-click her | her mood and needs, **Give a treat**, **Play with yarn**, sounds on/off, change coat or name |
| Play music (Spotify or anything else) | puts on a headset, bobs her head, little music notes |
| Watch a video or give a presentation in fullscreen | hides until you're done |

On her own she walks around, sits, grooms, yawns and naps (more at night and more when
she's tired), jumps between windows, walks along the panel, and sometimes climbs up the
side of the screen and walks upside down along the top before dropping down. If your
mouse is near her, she may crouch, wiggle, and pounce on it. Her favourite nap spot is a
Quick Notes post-it.

## Her needs

She gets hungry, bored and sleepy over time, and she likes attention.

- **Hungry:** she meows and shows a thought bubble with a fish. Give her a treat from the
  right-click menu and she runs over to eat it.
- **Bored:** a thought bubble with a ball of yarn. **Play with yarn** and she chases and
  bats it around.
- **Sleepy:** she naps and wakes up by herself.
- **Ignored for a long time:** her ears go flat and she looks a bit sad until you pet or
  feed her.

It's gentle: she never gets sick or runs away, and her needs pause while the app isn't
running.

## Sounds

A quiet purr while you pet her and an occasional soft meow when she wants something.
Turn them off with right-click > **Sounds**.

## Settings

`~/.config/pixel-cat/settings.ini`:

```ini
[pixel-cat]
shortcut=<Primary><Alt>c     # none = no shortcut
size=2                       # 1 small, 2 normal, 3 big
```

Restart her after changing it (`pixel-cat --quit`, then start Pixel Cat from the menu).

## Notes

- She needs an X11 desktop (Linux Mint Cinnamon, MATE or Xfce). On Wayland she can't see
  or sit on your windows.
- Windows are found with Wnck (`gir1.2-wnck-3.0`); the installer adds it if it's missing.
- In testing she used about 2-3% of one CPU core.
- Her name and mood are saved in `~/.local/share/pixel-cat/cat.json`.

## Requirements

Python 3 with GTK 3, cairo and Wnck (`python3-gi`, `python3-gi-cairo`, `gir1.2-gtk-3.0`,
`gir1.2-wnck-3.0`), and `paplay` for sounds. Linux Mint has everything except possibly
Wnck, which the installer adds.
