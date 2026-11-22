"""Test helper: press keys on the built-in keyboard device, or save the real screen as a PNG.
usage: hvc-key.py press <code>[:hold_secs] ... | shot <out.png>"""
import os, struct, sys, time
E = struct.Struct("llHHi")
def ev(fd, t, c, v):
    now = time.time(); os.write(fd, E.pack(int(now), int((now % 1) * 1e6), t, c, v))
if sys.argv[1] == "press":
    fd = os.open("/dev/input/event3", os.O_WRONLY)
    for a in sys.argv[2:]:
        code, _, hold = a.partition(":")
        ev(fd, 1, int(code), 1); ev(fd, 0, 0, 0); time.sleep(float(hold or 0.08))
        ev(fd, 1, int(code), 0); ev(fd, 0, 0, 0); time.sleep(0.35)
else:
    import numpy as np
    from PIL import Image
    a = np.frombuffer(open("/dev/fb0", "rb").read(320 * 170 * 2), dtype="<u2").reshape(170, 320)
    rgb = np.dstack(((a >> 11) << 3, ((a >> 5) & 63) << 2, (a & 31) << 3)).astype(np.uint8)
    Image.fromarray(rgb).resize((640, 340), Image.NEAREST).save(sys.argv[2])
