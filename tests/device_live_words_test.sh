#!/bin/bash
# While SPACE is held the listening screen shows what has been heard so far.
# Same isolation as device_test.sh. The stand-in server answers each clip with its length.
set -u
SRC=$(cd "$(dirname "$0")/.." && pwd)
OUT=${XDG_RUNTIME_DIR:?}/hvc-live-test; rm -rf "$OUT"; mkdir -p "$OUT/mock"
export XDG_CONFIG_HOME=$OUT/config HVC_FBDEV=$OUT/fb.bin HVC_KEYDEV=/nonexistent HVC_TEST_KEYS=$OUT/keys HVC_RUNDIR=$OUT
head -c 108800 /dev/zero > "$HVC_FBDEV"; mkfifo "$HVC_TEST_KEYS"
pkill -u "$(id -u)" -f "tests/mock_serve[r].py" 2>/dev/null; sleep 0.3
python3 "$SRC/tests/mock_server.py" "$OUT/mock" 47999 > "$OUT/mock.out" 2>"$OUT/mock.err" &
MOCK=$!
for i in $(seq 1 50); do [ -s "$OUT/mock.out" ] && break; sleep 0.2; done
export HVC_TEST_PAIR_URL=$(head -1 "$OUT/mock.out")
(cd "$SRC" && exec python3 -m hvc 2>>"$OUT/app.err") & APP=$!; sleep 2.5
put() { timeout 2 sh -c "echo \"$1\" > \"$HVC_TEST_KEYS\"" || echo "key not delivered: $1"; }
key() { put "$1 1"; sleep 0.08; put "$1 0"; sleep "${2:-0.4}"; }
n=0
shot() { n=$((n+1)); rm -f "$OUT/hvc-screen.png"; kill -USR1 $APP; sleep 0.5; cp "$OUT/hvc-screen.png" "$OUT/$n-$1.png" || echo "no shot: $1"; }
key 28 2.5; key 28 3.5
put "57 1"; sleep 0.5; shot just-pressed
sleep 2.2; shot after-2s
sleep 2.2; shot after-5s
put "57 0"; sleep 6; shot sent
kill $APP $MOCK 2>/dev/null
echo "--- app errors:"; tail -8 "$OUT/app.err"
echo "hear calls: $(grep -c "POST /api/hear" "$OUT/mock/calls.log")"
