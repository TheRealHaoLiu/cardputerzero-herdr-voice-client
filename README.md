# cardputerzero-herdr-voice-client

A push-to-talk client for [herdr-voice](https://github.com/TheRealHaoLiu/herdr-voice) on the
M5Stack CardputerZero: pick an agent session, hold a key, talk, hear the reply. The herdr-voice
server and all speech work stay on the computer that runs it (a Mac); this app records, sends,
shows and plays. It talks to the server the same way herdr-voice's phone page does, so the server
needs no change.

Status: a working prototype (November 2026), built on and tested with a CardputerZero running its
stock image (Python 3.13). Design and reasons: `DESIGN.md`.

## What you need

- An **M5Stack CardputerZero** with its stock image and launcher (`/usr/share/APPLaunch`),
  reachable over Wi-Fi from the computer that runs the server.
- A running **herdr-voice server** in `network` mode (see its README, "The phone page"), with the
  agent sessions you want to reach.
- On the device: `numpy`, `Pillow` and `cryptography` for Python 3, PipeWire's `pw-record` and
  `pw-play`, and `rpicam-still` for the camera. The stock image it was tested on has them all. `install.sh` adds
  `python3-zbar` (the QR reader) and stops with a message if anything else is missing.
- The device must reach the server on its port and on the next port up (the certificate download
  used once at pairing). If you set an `allow` rule in the server's `config.json`, it must admit
  the device's address; otherwise the device gets HTTP 403. Give the device a fixed address
  (a DHCP reservation) so the rule and the server's certificate keep matching.

## Use

Open the **Voice** tile in the launcher.

1. **Pair (once).** On the server computer: HerdrVoice > Connect a Phone. Point the Cardputer's
   camera at the QR code and press ENTER. The code is good for two minutes.
2. **PIN (optional).** After pairing it asks "Set a PIN?". Press ENTER alone for none,
   or type any keys and ENTER, then the same again. With a PIN the access key is
   stored encrypted and the app asks for the PIN each time it starts.
3. **Talk.** Hold SPACE, speak, release. Your words appear, then the reply is
   shown and spoken. A recording stops at two minutes and is sent as if you had let go.

| Key | Does |
|---|---|
| SPACE | hold to talk (two minutes at most): your words appear as you speak; ESC while holding cancels the recording |
| ENTER | open a typing line: type, ENTER sends, ESC cancels |
| F / X | scroll up to older messages / back down |
| TAB | session list: F/X (or TAB) move, ENTER pick, ESC back |
| ENTER / ESC on a permission prompt | allow / deny; ESC dismisses a prompt that can only be answered in Herdr |
| BACKSPACE | stop what the agent is doing (like Ctrl-C) and anything being spoken |
| N | after talking to an agent that is mid-task: send the waiting message now |
| M | menu: refresh sessions, pair again, quit |
| hold ESC one second | leave the app |

Each session shows its own conversation, loaded from the server when you switch to it.
A message sent while an agent is mid-task shows "Not read yet" until the agent has read
it; N has it read at once (Claude Code sessions only).

## Install or update

Copy this folder to the device (for example with `scp -r`) and run, on the device:

```
sudo sh install.sh
```

It installs one package (`python3-zbar`, the QR reader), copies the app to
`/opt/cardputerzero-herdr-voice-client`, and adds the launcher tile (`herdr-voice.desktop`, a
start script and an icon under `/usr/share/APPLaunch/`). The app runs as the logged-in
user, never as root.

What it keeps, per user, in `~/.config/cardputerzero-herdr-voice-client/`: `config.json` (server
address, device name, last session), `ca.crt` (the server's certificate authority) and
`key` or, with a PIN, `key.enc`. The server address and device name are written by pairing; `config.example.json` shows the format, should you want to edit `config.json` by hand (`client` is the device name the server sees). Nothing in this repository holds your server address or key. Recordings and reply audio only ever sit on the RAM
disk (`$XDG_RUNTIME_DIR`) and are deleted after use; the two-minute limit keeps a recording under
23 MB there. While it runs, the app writes to the card only when you pair, set a PIN, pick a
different session, or choose "Pair again". Errors go to
`$XDG_RUNTIME_DIR/hvc.log`.

## Testing

There are no unit tests. The scripts in `tests/` are manual smoke tests that need the device.

`./tests/device_test.sh` drives the app on the device against `tests/mock_server.py`, a
stand-in for the herdr-voice server, with a fake screen file, a key FIFO and its own
config folder, so the real screen, keyboard and settings are untouched. It leaves
numbered screenshots. Run it as the desktop user with `XDG_RUNTIME_DIR` set.

The other `tests/device_*_test.sh` scripts check one feature each (live words, typing,
scrolling, the first round of feedback) the same way.

`sudo python3 tests/device_keys.py` presses keys on the real keyboard device and saves
the real screen as a PNG, for checking the installed app through the launcher.

Passed on 2026-11-20: pairing through the test link, setting a PIN, wrong and right
PIN on restart, session list and pick, a real microphone recording reaching the server
as 16 kHz mono, reply text and audio calls, a permission prompt with Allow, the menu,
exit on ESC hold; and, on the real screen, the tile showing, the app opening from the
launcher and returning to it.

## Not verified

Not yet tried on the device:

- Long sessions against a real herdr-voice server beyond the first trials.
- The camera reading the QR code off a laptop screen. The camera delivers a frame in
  about 3 seconds and the decoder runs on it; a real code has not been tried.
- Speech recognition quality from this microphone, and how loud replies are.
- The push-to-talk button's key code.
- What the launcher's 30-second auto-lock does while the app is open.
- Announcements from other sessions (the stand-in server sends none).

## Licence

Apache-2.0, see `LICENSE`. Copyright 2026 Hao Liu.
