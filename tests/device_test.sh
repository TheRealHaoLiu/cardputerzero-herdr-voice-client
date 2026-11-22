#!/bin/bash
# Drive the app on the device against the stand-in server. Run as the desktop user with
# XDG_RUNTIME_DIR set. Uses a fake screen file, a key FIFO and its own config folder, so the real
# screen, keyboard and settings are not touched. Leaves numbered screenshots in $OUT.
set -u
SRC=$(cd "$(dirname "$0")/.." && pwd)
RUN=${XDG_RUNTIME_DIR:?}
OUT=$RUN/hvc-test; rm -rf "$OUT"; mkdir -p "$OUT"
export XDG_CONFIG_HOME=$OUT/config HVC_FBDEV=$OUT/fb.bin HVC_KEYDEV=/nonexistent HVC_TEST_KEYS=$OUT/keys HVC_RUNDIR=$OUT   # clips and screenshots stay in the test folder, away from a running app
head -c 108800 /dev/zero > "$HVC_FBDEV"; mkfifo "$HVC_TEST_KEYS"
pkill -u "$(id -u)" -f "tests/mock_serve[r].py" 2>/dev/null; sleep 0.3
python3 "$SRC/tests/mock_server.py" "$OUT/mock" 47999 > "$OUT/mock.out" 2>"$OUT/mock.err" &
MOCK=$!
for i in $(seq 1 50); do [ -s "$OUT/mock.out" ] && break; sleep 0.2; done
export HVC_TEST_PAIR_URL=$(head -1 "$OUT/mock.out")
echo "pair link ready: ${HVC_TEST_PAIR_URL:+yes}"
n=0
start() { (cd "$SRC" && exec python3 -m hvc 2>>"$OUT/app.err") & APP=$!; sleep 2.5; }
put()   { timeout 2 sh -c "echo \"$1\" > \"$HVC_TEST_KEYS\"" || echo "key not delivered: $1"; }   # a FIFO write blocks when the app is gone
key()   { put "$1 1"; sleep 0.08; put "$1 0"; sleep "${2:-0.4}"; }
shot()  { n=$((n+1)); rm -f "$OUT/hvc-screen.png"; kill -USR1 $APP 2>/dev/null; sleep 0.5; cp "$OUT/hvc-screen.png" "$OUT/$(printf %02d $n)-$1.png" 2>/dev/null || echo "no shot: $1"; }
start
shot pair
key 28 2.5;            shot pin-new            # ENTER: pair through the test link
key 3; key 4; key 5;   shot pin-typed
key 28 0.6;            shot pin-again
key 3; key 4; key 5; key 28 3; shot main-empty
key 15 1.5;            shot sessions           # TAB
key 15; key 28 1;      shot picked-second
put "57 1"; sleep 0.4;     shot listening
sleep 1.4; put "57 0"; sleep 1.2; shot after-talk
sleep 4;               shot ask
key 28 5;              shot after-allow
key 50 0.5;            shot menu               # M
key 1 0.5                                       # ESC: back
put "1 1"; sleep 1.5; put "1 0"; sleep 1
kill -0 $APP 2>/dev/null && echo "FAIL: still running after ESC hold" || echo "ok: exited on ESC hold"
ls -l "$XDG_CONFIG_HOME/cardputerzero-herdr-voice-client/" | awk '{print $1, $NF}'
start;                 shot pin-unlock
key 9; key 9; key 28 2.5; shot pin-wrong
key 3; key 4; key 5; key 28 3.5; shot unlocked
kill $APP 2>/dev/null; sleep 0.5
kill $MOCK 2>/dev/null
echo "--- server calls:"; sort "$OUT/mock/calls.log" | uniq -c
echo "--- app errors:"; tail -20 "$OUT/app.err"
echo "--- mock errors:"; tail -5 "$OUT/mock.err"
ls "$OUT"/*.png | wc -l
