"""Reading the pairing QR code with the device's camera."""
import os, subprocess, tempfile

from PIL import Image

RUN = os.environ.get("HVC_RUNDIR") or os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
SHOT = os.path.join(RUN, "hvc-shot.jpg")


def available():
    try:
        import zbar  # noqa: F401
    except ImportError:
        return "The QR reader is not installed (python3-zbar)."
    return None


def shoot():
    """One frame as a PIL image, or None. The file lives on the RAM disk and is removed at once."""
    try:
        subprocess.run(["rpicam-still", "-n", "-t", "350", "--width", "1640", "--height", "1232", "-q", "80", "-o", SHOT],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=True)
        with Image.open(SHOT) as im:
            im.load()
            return im.convert("L")
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        try:
            os.unlink(SHOT)
        except OSError:
            pass


def decode(gray: Image.Image):
    """The text of the first QR code in the picture, or None."""
    import zbar
    scanner = zbar.ImageScanner()
    scanner.parse_config("enable")
    for im in (gray, gray.resize((gray.width // 2, gray.height // 2))):  # a screen's pixel grid sometimes reads better smaller
        z = zbar.Image(im.width, im.height, "Y800", im.tobytes())
        scanner.scan(z)
        for sym in z:
            data = sym.data
            return data.decode(errors="replace") if isinstance(data, bytes) else data  # a binary code is no pairing link
    return None
