# cardputerzero-herdr-voice-client: design

Status on 2026-11-20: a first version is built and installed; `README.md` says what is and is not verified.

Changed from the draft while building: the screen is drawn with Pillow and numpy straight into
`/dev/fb0` (pygame is not used: its display layer has no driver for this framebuffer); session
switching is TAB rather than up/down, because the arrow keys on this keyboard are `fn` combinations.

## What it is

A small app on the CardputerZero that talks to the existing herdr-voice server the
same way the phone page does. The server and all speech work stay on the Mac. The
server needs no change for the first version.

Aims: switch to a session, talk to it, and have it act on whatever machine that session runs on;
a good client experience first, with the computer running the server as the brain; an optional PIN
inside the app.

## How one turn works

1. The app lists sessions (`GET /api/sessions`), grouped by machine.
2. Hold the talk key (SPACE): the app records the microphone, for at most two minutes.
3. Release: it sends the clip (`POST /api/hear`) and shows the words that come back.
4. It sends those words to the chosen session (`POST /api/say?t=<session>&spoken=1`).
5. The reply comes back as text plus a WAV clip; the app shows the text and plays the
   clip. While the session is still working it keeps asking (`POST /api/collect`).
6. If the session stops on a permission prompt, the app shows what is asked and offers
   Allow and Deny (`POST /api/answer`).
7. In the background it long-polls `GET /api/announcements` for lines from other
   sessions and plays them with "From <name>".

Every call carries the access key (`x-voice-key`) and a device name
(`x-voice-client`) over HTTPS, checked against the herdr-voice certificate authority.

## Screen (320 x 170) and keys

- Top line: chosen session and its state (idle, working, waiting) and battery.
- Middle: what you said and what came back, most recent at the bottom, large text.
- While holding to talk: a clear "listening" state on the whole screen.
- TAB opens the session list (F/X or TAB move, ENTER picks, ESC goes back); F/X scroll the
  conversation.
- Talk key: the keyboard's push-to-talk button. Its key code was not known at design time;
  SPACE is used instead.
- `ENTER` = Allow, `ESC` = Deny when a prompt is shown (`ESC` dismisses one that can only be
  answered in Herdr); `BACKSPACE` stops the agent while it works.
- Hold `ESC` exits to the launcher, as in M5Stack's own apps.

## Built with

Python 3.13 with Pillow and numpy, all already on the image, drawing to `/dev/fb0` and reading
`/dev/input/event3` directly, as the launcher's own apps do; `cryptography` (also on the image)
only when a PIN is set. HTTPS through the standard library. Audio through PipeWire
(`pw-record`, `pw-play`). One new package for the first version: `python3-zbar`, for
reading the pairing QR code. It appears as a tile
through a `.desktop` file in `/usr/share/APPLaunch/applications/`.

If Python proves too slow or too heavy in 352 MB of RAM, the fallback is M5Stack's
C++/LVGL app template.

## Audio

- The microphone is on the left channel only, at a low level. Record stereo, keep the
  left channel, normalise the level, send mono 16 kHz WAV.
- The recording lands on the RAM disk (`$XDG_RUNTIME_DIR`, 35 MB on the device) at 192 KB a
  second, and is converted while it grows: each step reads only what was added, into one 16 kHz
  buffer (32 KB a second). A recording stops at two minutes as if the key were let go (23 MB of
  RAM disk). `pw-record` is killed by the kernel if the app dies, and a leftover file is removed
  at start.
- While you talk, the clip so far is sent to the server for the words heard so far: every second
  at first, then less often as the clip grows (a fifth of its length), never two at once.
- Built-in speaker first. A Bluetooth headset is an open question.
- Speech level and recognition quality on this microphone are untested.

## Access key and the optional PIN

- Pairing uses herdr-voice's existing one-time code (`POST /api/pair`), so the
  long-lived key is never typed or shown.
- **The device's camera reads the pairing QR code** (decided in design). The Mac app's "Connect a Phone" window shows a QR holding the server
  address and a one-time code that lasts two minutes; the app photographs it, decodes
  it, and trades the code for the key. The camera stack is on the image (`picamera2`,
  `rpicam-still`); a QR decoder is not, so this needs one small package
  (`python3-zbar`, available from the configured apt sources). Untested: whether this
  fixed-focus camera reads a QR off a laptop screen.
- The same QR's link also points at the certificate authority download, which the app
  needs to trust the server; how the app verifies that certificate on first contact is
  to be settled in the build.
- PIN off: the key is stored in a file only the user can read. On an unencrypted card
  that means anyone holding the card can read it.
- PIN on: the app asks for the PIN at start and the key is stored encrypted with it.
  A short PIN can still be guessed offline from a stolen card.

## Added after the first version

- Words appearing while you talk (see Audio).
- Typing a message instead of speaking it (ENTER opens a typing line).

## Not built

- Working with the laptop closed (needs a herdr-voice server copy on a second, always-on machine).

## Improvement requests (not built)

- **Lower the spoken reply's volume while recording** (2026-11-20). While SPACE is held,
  duck or pause whatever the speaker is playing, and restore it on release. It should
  also stop the microphone picking up the app's own voice: the same evening a voice
  message reached the agent with Claude's spoken reply transcribed inside it.
- Show the subscription meter and a session's context on the device.
- Show what a typed slash command prints.

## Open questions

1. The talk button's key code (to be observed once on the device).
2. Pairing is by camera (decided); open is whether the camera reads the QR reliably
   off a screen, and the fallback if it does not.
3. Speaker only, or Bluetooth headset too.
4. The launcher's 30-second auto-lock: whether the app is one of the apps it locks,
   and what should happen to a reply that arrives while locked.
5. The server's `allow` rule may refuse the device's network address (HTTP 403). Either widen
   `allow` or give the device an address the rule admits; see the README.