#!/bin/sh
# Install or update the app on the CardputerZero. Run on the device as root, from this folder:
#   sudo sh install.sh
# It adds one package (the QR reader), checks that everything else the app needs is there, copies
# the app to /opt, and adds a launcher tile.
set -eu
[ "$(id -u)" = 0 ] || { echo "run as root: sudo sh install.sh" >&2; exit 1; }
HERE=$(cd "$(dirname "$0")" && pwd)
APPL=/usr/share/APPLaunch
[ -d "$APPL/applications" ] || { echo "no launcher at $APPL: is this a CardputerZero?" >&2; exit 1; }

python3 -c 'import zbar' 2>/dev/null || { apt-get update -q && apt-get install -y -q python3-zbar; }

missing=""
for m in numpy PIL cryptography.fernet zbar; do
    python3 -c "import $m" 2>/dev/null || missing="$missing python3:$m"
done
for p in pw-record pw-play rpicam-still; do
    command -v "$p" >/dev/null 2>&1 || missing="$missing $p"
done
[ -z "$missing" ] || { echo "not installed on this device:$missing. Install what is missing, then run this again." >&2; exit 1; }

[ -d /opt/herdr-voice-cardputer ] && [ ! -e /opt/cardputerzero-herdr-voice-client ] && mv /opt/herdr-voice-cardputer /opt/cardputerzero-herdr-voice-client   # the name before 2026-11-22
install -d -m 755 /opt/cardputerzero-herdr-voice-client/hvc
install -m 644 "$HERE"/hvc/*.py /opt/cardputerzero-herdr-voice-client/hvc/
install -m 755 "$HERE/packaging/herdr-voice" "$APPL/bin/herdr-voice"
python3 "$HERE/packaging/make_icon.py" "$APPL/share/images/herdr-voice.png"
install -m 644 "$HERE/packaging/herdr-voice.desktop" "$APPL/applications/herdr-voice.desktop"
python3 -m compileall -q /opt/cardputerzero-herdr-voice-client/hvc
echo "installed. The Voice tile appears in the launcher (restart the launcher if it does not)."
