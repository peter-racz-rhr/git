# Mail Brief

A small line on your desktop that tells you what's in your Gmail:

**`5 new · 2 important  [DEADLINE]`**

Click it and it opens into a list of your unread emails. Each one shows:

- **DEADLINE** (red) and **IMPORTANT** (blue) tags
- who sent it and when
- a very short AI summary, in the email's own language, that keeps every date, place,
  name and request
- **To do:** what you have to do, if anything, and the **deadline** with a countdown
- **Open** (opens the email in Gmail), **Mark read**, **Done** (hides it here)

It checks every hour (plus the refresh button) and pops up a notification when new
mail arrives. Gmail's Promotions and Social tabs (ads, social network notifications)
are skipped, so no AI credit is spent on them. Each email is summarized only once.

## Install

```bash
cd mail-brief
./install.sh
```

It starts automatically when you log in (`AUTOSTART=0 ./install.sh` to turn that off).
Running the installer again replaces the old version and keeps your settings.

## One-time setup

The first time it opens, the settings window asks for three things.

### 1. Your Gmail address

### 2. A Gmail app password

An app password is a special 16-letter password just for this app. Your real password
is never used, and you can delete the app password any time.

1. Open <https://myaccount.google.com/security> and turn on **2-Step Verification**
   (Google requires it for app passwords).
2. Open <https://myaccount.google.com/apppasswords>.
3. Type a name like `Mail Brief` and press **Create**.
4. Copy the 16-letter code into the settings window.

### 3. A free Groq API key

Groq has a free tier (no credit card). The account holder has to meet Groq's age
requirement, so a parent may need to create it.

1. Sign up at <https://console.groq.com>.
2. Open **API Keys** (<https://console.groq.com/keys>), press **Create API Key**, copy it.
3. Paste it into the settings window.

Press **Save & check now**. After a few seconds the line shows your emails.

## Privacy

- The app password and the Groq key are stored in `~/.config/mail-brief/secrets.json`,
  readable only by you.
- The text of each new email is sent to Groq to write the summary. Groq's terms say it
  does not train on your data and does not keep it.
- Only the summaries (not the email text) are stored on your laptop.
- Very long emails are cut to the first ~6000 characters before summarizing; the widget
  says so under that email.

## Settings

Menu (three lines) > **Settings**: address, app password, Groq key, the AI model
(`llama-3.3-70b-versatile` by default) and how often to check.

Uninstall with `./uninstall.sh` (`--forget` also deletes settings and summaries).
