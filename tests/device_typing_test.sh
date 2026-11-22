#!/bin/bash
# ENTER opens a typing line; keys become text; ENTER sends; ESC cancels. Same isolation as device_test.sh.
set -u
SRC=$(cd "$(dirname "$0")/.." && pwd)
OUT=${XDG_RUNTIME_DIR:?}/hvc-typing-test; rm -rf "$OUT"; mkdir -p "$OUT/mock"
export XDG_CONFIG_HOME=$OUT/config HVC_FBDEV=$OUT/fb.bin HVC_KEYDEV=/nonexistent HVC_TEST_KEYS=$OUT/keys HVC_RUNDIR=$OUT
head -c 108800 /dev/zero > "$HVC_FBDEV"; mkfifo "$HVC_TEST_KEYS"
pkill -u "$(id -u)" -f "tests/mock_serve[r].py" 2>/dev/null; sleep 0.3
python3 "$SRC/tests/mock_server.py" "$OUT/mock" 47999 > "$OUT/mock.out" 2>"$OUT/mock.err" &
MOCK=$!
for i in $(seq 1 50); do [ -s "$OUT/mock.out" ] && break; sleep 0.2; done
export HVC_TEST_PAIR_URL=$(head -1 "$OUT/mock.out")
(cd "$SRC" && exec python3 -m hvc 2>>"$OUT/app.err") & APP=$!; sleep 2.5
put() { timeout 2 sh -c "echo \"$1\" > \"$HVC_TEST_KEYS\"" || echo "key not delivered: $1"; }
key() { put "$1 1"; sleep 0.06; put "$1 0"; sleep "${2:-0.15}"; }
n=0
shot() { n=$((n+1)); rm -f "$OUT/hvc-screen.png"; kill -USR1 $APP; sleep 0.5; cp "$OUT/hvc-screen.png" "$OUT/$n-$1.png" || echo "no shot: $1"; }
key 28 2.5; key 28 3.5
key 28 0.5; shot typing-open
key 42; key 35; key 23; key 57; key 20; key 35; key 18; key 19; key 18; key 93   # shift-tap, h i space t h e r e ?
key 33; key 45; key 49; key 50                                                   # f x n m are text here, not commands
shot typed
key 14; key 14; key 14; key 14; shot after-backspace
key 28 6; shot sent
key 28 0.5; key 30; key 30
put "1 1"; sleep 0.1; put "1 0"; sleep 0.5; shot cancelled-with-esc
kill -0 $APP 2>/dev/null && echo "ok: still running after ESC cancel" || echo "FAIL: exited on ESC cancel"
kill $APP $MOCK 2>/dev/null
echo "--- app errors:"; tail -8 "$OUT/app.err"
grep -c "POST /api/say" "$OUT/mock/calls.log"
