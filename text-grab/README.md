# Text Grab

Copy the text from anything on your screen - a PDF, a video frame, a website that blocks
copying, a photo of the whiteboard - and read QR codes. Works offline; reads Hungarian,
English and German.

## Install

```bash
cd text-grab
./install.sh
```

It installs Tesseract (the text recognition, with Hungarian, English and German) and the
QR reader if they are missing (it asks for your password once), and starts with your
computer. Running it again updates it.

## Use

1. Press **Ctrl+Alt+G**. The screen freezes and gets darker.
2. Drag a box around the text (or QR code). **Esc** or right-click cancels.
3. The text is **in your clipboard** - paste it anywhere with Ctrl+V.

A small popup shows the first lines. Click it (or **Show all**) for the full text:

- edit it, **Copy** it again, or send it **To Quick Notes**
- **Reading order** (normal text, columns are read one after the other) or **Keep rows**
  (tables and lists: rows and spacing stay as on screen) - it reads the picture again
- **Grab again**

**QR codes**: grab one and its content (usually a link) is copied; **Open** opens the link.

**Images**: right-click a picture in the file manager > **Grab text from image**.

Show the last result again: `text-grab --last`.

## Tips

- Grab only what you need; the tighter the box, the better the result.
- Very small text reads better if you zoom in first (Ctrl + in the browser / PDF viewer).
- Light text on a dark background (dark mode, subtitles) is handled automatically.

## Settings

`~/.config/text-grab/settings.ini`:

```ini
[text-grab]
shortcut=<Primary><Alt>g
languages=hun+eng+deu
```

Other languages: install `tesseract-ocr-fra` (French) etc. and add `+fra`.

Uninstall with `./uninstall.sh`.
