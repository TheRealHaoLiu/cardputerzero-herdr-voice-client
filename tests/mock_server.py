"""A stand-in for the herdr-voice server, for testing the app on the device with no Mac involved.

Usage: mock_server.py <state-dir> [port]     (HTTPS on 127.0.0.1:<port>, CA certificate on <port>+1)
Prints the pairing link to use. Speaks the same JSON the real server does for the calls the app makes.
"""
import base64, datetime, http.server, io, ipaddress, json, math, os, ssl, struct, sys, threading, time, urllib.parse, wave

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

STATE, PORT = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 47999
KEY, CODE = "mock-access-key-0123456789", "mockcode123"
os.makedirs(STATE, exist_ok=True)


def make_certs():
    now = datetime.datetime.now(datetime.timezone.utc)
    def cert(subject, issuer, pub, signer, ca):
        b = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer).public_key(pub)
             .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
             .not_valid_after(now + datetime.timedelta(days=30))
             .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True))
        if not ca:
            b = b.add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        return b.sign(signer, hashes.SHA256())
    name = lambda cn: x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    cak, leafk = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    ca = cert(name("mock CA"), name("mock CA"), cak.public_key(), cak, True)
    leaf = cert(name("127.0.0.1"), name("mock CA"), leafk.public_key(), cak, False)
    pem = serialization.Encoding.PEM
    open(f"{STATE}/ca.crt", "wb").write(ca.public_bytes(pem))
    open(f"{STATE}/leaf.crt", "wb").write(leaf.public_bytes(pem))
    open(f"{STATE}/leaf.key", "wb").write(leafk.private_bytes(pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))


def beep(secs=0.5, hz=660):
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
        w.writeframes(b"".join(struct.pack("<h", int(9000 * math.sin(2 * math.pi * hz * i / 24000))) for i in range(int(24000 * secs))))
    return base64.b64encode(out.getvalue()).decode()


SESSIONS = [
    {"address": "w1:1", "machine": "This machine", "name": "notes", "title": None, "status": "idle", "folder": "notes", "active_ms": 3},
    {"address": "server/w2:1", "machine": "server", "name": None, "title": "patch pass", "status": "working", "folder": "deploy", "active_ms": 2},
    {"address": "laptop/w1:2", "machine": "laptop", "name": "assistant", "title": None, "status": "blocked", "folder": "helper", "active_ms": 1},
]
calls, turns = [], {}


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def handle_any(self):
        u = urllib.parse.urlsplit(self.path); q = urllib.parse.parse_qs(u.query)
        body = self.rfile.read(int(self.headers.get("content-length") or 0))
        calls.append(f"{self.command} {u.path} client={self.headers.get('x-voice-client')}")
        open(f"{STATE}/calls.log", "a").write(calls[-1] + "\n")
        if u.path == "/api/pair":
            ok = json.loads(body or b"{}").get("code") == CODE
            return self.reply({"key": KEY} if ok else {"error": "That code has expired or was already used. Show a new one in the app."}, 200 if ok else 403)
        if self.headers.get("x-voice-key") != KEY:
            return self.reply({"error": "bad key"}, 401)
        t = q.get("t", [""])[0]
        if u.path == "/api/sessions":
            return self.reply({"sessions": SESSIONS})
        if u.path == "/api/announcements":
            time.sleep(min(float(q.get("wait", ["0"])[0]), 3))
            return self.reply({"items": [], "last": 0, "epoch": 1})
        if u.path == "/api/hear":
            try:
                w = wave.open(io.BytesIO(body)); n, rate, ch = w.getnframes(), w.getframerate(), w.getnchannels()
                s = struct.unpack("<%dh" % (n * ch), w.readframes(n))
                heard = f"clip {n / rate:.1f}s {rate}Hz ch{ch} peak {max(abs(v) for v in s) if s else 0}"
            except Exception as e:
                heard = f"bad wav: {e}"
            return self.reply({"heard": heard})
        if u.path == "/api/say":
            if os.path.exists(f"{STATE}/steer"):  # pretend the session is mid-step
                return self.reply({"heard": json.loads(body)["text"], "steered": True, "status": "working"})
            turns[t] = 0
            return self.reply({"heard": json.loads(body)["text"], "status": "working", "spoken": "On it.", "audio": beep(0.3, 880), "screen": ""})
        if u.path == "/api/collect":
            turns[t] = turns.get(t, 0) + 1
            if t.startswith("laptop") or turns[t] == 2:
                return self.reply({"status": "blocked", "spoken": "", "audio": None, "blocked": True,
                                   "ask": {"text": "It is asking for permission: Bash(ssh server uptime)", "what": "Bash(ssh server uptime)", "can": True, "audio": beep(0.2, 440), "say": "It is asking for your permission."}})
            if turns[t] == 1:
                return self.reply({"status": "working", "spoken": "Checking now.", "audio": beep(0.3, 660), "screen": ""})
            return self.reply({"status": "idle", "spoken": "All done. Everything looks healthy and nothing needs you.", "audio": beep(0.5, 520), "screen": ""})
        if u.path == "/api/answer":
            turns[t] = 5
            return self.reply({"answered": True, "choice": q.get("choice", [""])[0], "status": "working"})
        if u.path == "/api/history":
            items = [{"who": "me", "text": f"earlier question in {t}"}, {"who": "agent", "text": f"earlier answer in {t}"}]
            if os.path.exists(f"{STATE}/long"):  # enough to scroll through
                items = [{"who": "agent" if i % 2 else "me", "text": f"line {i} of a longer conversation in {t}"} for i in range(1, 15)]
            return self.reply({"items": items})
        if u.path == "/api/now":
            return self.reply({"sent": True})
        if u.path == "/api/stop":
            return self.reply({"stopped": True, "status": "idle"})
        self.reply({"error": "not found"}, 404)

    do_GET = do_POST = handle_any


class CA(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = open(f"{STATE}/ca.crt", "rb").read() if self.path == "/ca.crt" else b""
        self.send_response(200 if body else 404); self.send_header("content-length", str(len(body))); self.end_headers(); self.wfile.write(body)


make_certs()
srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), H)
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(f"{STATE}/leaf.crt", f"{STATE}/leaf.key")
srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
threading.Thread(target=http.server.ThreadingHTTPServer(("127.0.0.1", PORT + 1), CA).serve_forever, daemon=True).start()
print(f"https://127.0.0.1:{PORT}/#pair={CODE}", flush=True)
srv.serve_forever()
