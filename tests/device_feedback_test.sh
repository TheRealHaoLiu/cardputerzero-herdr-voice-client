#!/bin/bash
# Checks the changes from the first evening of real use: each session shows its own history,
# ESC cancels a recording, a mid-task message shows "not read yet" until read or sent with N,
# and the menu no longer offers a talk-key setting. Same isolation as device_test.sh.
set -u
SRC=$(cd "$(dirname "$0")/.." && pwd)
OUT=${XDG_RUNTIME_DIR:?}/hvc-feedback-test; rm -rf "$OUT"; mkdir -p "$OUT/mock"
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
key 28 2.5; key 28 3.5               # pair through the test link, no PIN
shot first-session-history
put "57 1"; sleep 0.8; shot listening
put "1 1"; sleep 0.1; put "1 0"; sleep 0.3; put "57 0"; sleep 1.5   # ESC while holding SPACE
shot cancelled
kill -0 $APP 2>/dev/null && echo "ok: still running after ESC cancel" || echo "FAIL: app exited on ESC cancel"
key 15 1.2; key 15; key 28 1.5       # pick the second session (reported as working)
shot second-session-history
touch "$OUT/mock/steer"
put "57 1"; sleep 1.4; put "57 0"; sleep 3
shot queued
key 49 2; shot sent-now              # N
key 15 1.2; key 15; key 15; key 28 1.5 # back to the first session (the list has three, and wraps)
shot back-to-first
key 50 0.6; shot menu                # M
key 1 0.4
put "1 1"; sleep 1.5; put "1 0"; sleep 1
kill -0 $APP 2>/dev/null && { echo "FAIL: still running after ESC hold"; kill $APP; } || echo "ok: exited on ESC hold"
kill $MOCK 2>/dev/null
echo "--- app errors:"; tail -8 "$OUT/app.err"
sort "$OUT/mock/calls.log" | uniq -c
ls "$OUT"/hvc-rec.wav 2>/dev/null && echo "FAIL: a recording was left behind" || echo "ok: no recording left behind"
