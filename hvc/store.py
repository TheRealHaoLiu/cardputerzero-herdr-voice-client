"""What the app keeps: the server address, its certificate authority, the access key, a few settings.

Without a PIN the access key is a file only this user can read. With a PIN it is stored encrypted
under a key stretched from the PIN (scrypt), so the card alone does not give it up. A PIN is the
sequence of key codes pressed, so it does not depend on the keyboard layout.
"""
import base64, hashlib, json, os

DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "cardputerzero-herdr-voice-client")
_OLD_DIR = os.path.join(os.path.dirname(DIR), "herdr-voice-cardputer")   # the app's name before 2026-11-22
if os.path.isdir(_OLD_DIR) and not os.path.exists(DIR):
    os.rename(_OLD_DIR, DIR)
CONFIG, CA, KEY, KEY_ENC = (os.path.join(DIR, n) for n in ("config.json", "ca.crt", "key", "key.enc"))
DEFAULTS = {"server": "", "client": "cardputer"}


def _write(path, data: bytes):
    os.makedirs(DIR, mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())  # on the card before it replaces the old file: the battery can go at any moment
    os.replace(tmp, path)


def config():
    c = dict(DEFAULTS)
    try:
        with open(CONFIG) as f:
            c.update(json.load(f))
    except (OSError, ValueError):
        pass
    return c


def save_config(c):
    _write(CONFIG, json.dumps(c, indent=1).encode())


def save_ca(pem: bytes):
    _write(CA, pem)


def paired():
    return bool(config()["server"]) and os.path.exists(CA) and (os.path.exists(KEY) or os.path.exists(KEY_ENC))


def has_pin():
    return os.path.exists(KEY_ENC)


def _fernet(pin, salt: bytes):
    from cryptography.fernet import Fernet  # only with a PIN: about 9 MB resident otherwise spent at every start
    secret = ",".join(str(c) for c in pin).encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.scrypt(secret, salt=salt, n=2 ** 15, r=8, p=1, dklen=32, maxmem=80 * 1024 * 1024)))


def save_key(key: str, pin=None):
    for p in (KEY, KEY_ENC):
        try:
            os.unlink(p)
        except OSError:
            pass
    if pin:
        salt = os.urandom(16)
        _write(KEY_ENC, base64.b64encode(salt) + b"\n" + _fernet(pin, salt).encrypt(key.encode()))
    else:
        _write(KEY, key.encode())


def load_key(pin=None):
    """The access key, or None when the PIN is wrong or nothing is stored."""
    try:
        if os.path.exists(KEY_ENC):
            from cryptography.fernet import InvalidToken
            with open(KEY_ENC, "rb") as f:
                salt, token = f.read().split(b"\n", 1)
            try:
                return _fernet(pin or [], base64.b64decode(salt)).decrypt(token).decode()
            except InvalidToken:
                return None
        with open(KEY) as f:
            return f.read().strip()
    except (OSError, ValueError):
        return None


def forget():
    for p in (KEY, KEY_ENC, CA):
        try:
            os.unlink(p)
        except OSError:
            pass
    c = config(); c["server"] = ""; save_config(c)
