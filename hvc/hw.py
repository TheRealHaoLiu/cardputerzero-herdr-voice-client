"""The device: screen, keyboard, battery. No desktop in between, like the launcher's own apps."""
import fcntl, mmap, os, select, struct, time

import numpy as np
from PIL import Image

W, H = 320, 170
FBDEV = os.environ.get("HVC_FBDEV", "/dev/fb0")
KEYDEV = os.environ.get("HVC_KEYDEV", "/dev/input/event3")
TEST_KEYS = os.environ.get("HVC_TEST_KEYS")  # a FIFO of "<code> <1|0>" lines, for driving the app in tests
FBIOBLANK = 0x4611

# Linux key codes this app names. Anything else is passed through by number.
ESC, BACKSPACE, TAB, ENTER, SPACE, UP, DOWN = 1, 14, 15, 28, 57, 103, 108
KPENTER = 96
LEFTSHIFT, RIGHTSHIFT = 42, 54

# What a key types. Letters and digits use the standard Linux codes. Symbols come from the keypad's
# own layer and arrive as the codes in /usr/share/keymaps/tca8418_keypad_m5stack_keymap.map, which
# are not the standard ones (39 is "#" here, not ";").
_ROWS = {16: "qwertyuiop", 30: "asdfghjkl", 44: "zxcvbnm"}
CHARS = {SPACE: " "}
for _first, _letters in _ROWS.items():
    for _i, _c in enumerate(_letters):
        CHARS[_first + _i] = _c
for _i, _c in enumerate("1234567890"):
    CHARS[2 + _i] = _c
CHARS.update({26: "!", 27: "@", 39: "#", 40: "$", 41: "%", 43: "^", 51: "&", 52: "*", 53: "(", 94: ")",
              55: "~", 69: "`", 70: "_", 71: "-", 72: "+", 73: "=", 74: "[", 75: "]", 76: "{", 77: "}",
              79: ";", 80: ":", 81: "'", 82: '"', 83: "<", 85: ">", 86: "\\", 89: "|",
              90: ",", 91: ".", 92: "/", 93: "?"})
EVENT = struct.Struct("llHHi")  # struct input_event on a 64-bit kernel
EV_KEY = 1


class Screen:
    def __init__(self):
        self.fd = os.open(FBDEV, os.O_RDWR)
        self.map = mmap.mmap(self.fd, W * H * 2)
        try:
            fcntl.ioctl(self.fd, FBIOBLANK, 0)  # wake the panel if the launcher blanked it
        except OSError:
            pass

    def show(self, img: Image.Image):
        a = np.asarray(img.convert("RGB"), dtype=np.uint16)
        rgb565 = ((a[:, :, 0] >> 3) << 11) | ((a[:, :, 1] >> 2) << 5) | (a[:, :, 2] >> 3)
        self.map.seek(0)
        self.map.write(rgb565.astype("<u2").tobytes())

    def close(self):
        self.map.close()
        os.close(self.fd)


class Keys:
    """Key presses and releases as (code, is_down) pairs. Auto-repeat is dropped."""

    def __init__(self):
        self.fds = []
        try:
            self.fds.append(os.open(KEYDEV, os.O_RDONLY | os.O_NONBLOCK))
        except OSError:
            if not TEST_KEYS:
                raise
        self.test = None
        if TEST_KEYS:
            self.test = os.open(TEST_KEYS, os.O_RDWR | os.O_NONBLOCK)  # read-write so the FIFO never reports end-of-file between writers
            self.fds.append(self.test)
        self.waker, self.wake_fd = os.pipe()
        os.set_blocking(self.waker, False); os.set_blocking(self.wake_fd, False)
        self.fds.append(self.waker)
        self.buf = b""

    def wake(self):
        """Make a read() that is waiting return at once: another thread has something to show."""
        try:
            os.write(self.wake_fd, b"!")
        except BlockingIOError:
            pass  # already woken plenty

    def read(self, timeout: float):
        out = []
        ready, _, _ = select.select(self.fds, [], [], timeout)
        for fd in ready:
            try:
                data = os.read(fd, 4096)
            except OSError:
                continue
            if fd == self.waker:
                continue
            if fd == self.test:
                self.buf += data
                *lines, self.buf = self.buf.split(b"\n")
                for line in lines:
                    try:
                        code, down = line.split()
                        out.append((int(code), down == b"1"))
                    except ValueError:
                        pass
                continue
            for i in range(0, len(data) - EVENT.size + 1, EVENT.size):
                _, _, typ, code, value = EVENT.unpack_from(data, i)
                if typ == EV_KEY and value in (0, 1):
                    out.append((code, value == 1))
        return out


def battery():
    """Percent and whether it is charging, or (None, False) when the gauge cannot be read."""
    base = "/sys/class/power_supply/bq27220-0/"
    try:
        with open(base + "capacity") as f:
            pct = int(f.read().strip())
        with open(base + "status") as f:
            return pct, f.read().strip() == "Charging"
    except (OSError, ValueError):
        return None, False


def now():
    return time.monotonic()
