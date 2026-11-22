"""The herdr-voice server, called the way its phone page calls it."""
import http.client, json, ssl, urllib.error, urllib.parse, urllib.request

from . import store


class ApiError(Exception):
    pass


class NetworkError(ApiError):
    """The call did not get a whole answer: no connection, a timeout, or the line dropped mid-reply."""


def tls(**ca):
    """Verify the chain and the address against the server's own certificate authority.

    Python 3.13 also demands key-identifier extensions by default, which herdr-voice's certificates
    do not carry; that one extra check is switched off, the rest stays.
    """
    ctx = ssl.create_default_context(**ca)
    ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return ctx


def unreachable(e):
    cert = isinstance(getattr(e, "reason", e), ssl.SSLCertVerificationError)
    return ApiError("The server's certificate was not accepted. Pair again.") if cert else NetworkError("Can't reach the voice server.")


class Api:
    def __init__(self, server: str, key: str, client: str):
        self.server, self.key, self.client = server.rstrip("/"), key, client
        self.ctx = tls(cafile=store.CA)

    def _call(self, method, path, body=None, ctype=None, timeout=40):
        headers = {"x-voice-key": self.key, "x-voice-client": self.client}
        if ctype:
            headers["content-type"] = ctype
        req = urllib.request.Request(self.server + path, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=self.ctx) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code == 403:
                raise ApiError("The server refused this device's address.")
            try:
                msg = json.loads(e.read()).get("error")
            except (ValueError, OSError, http.client.HTTPException):
                msg = None
            raise ApiError(msg or f"Server said {e.code}.")
        except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as e:
            raise unreachable(e) from e

    def _t(self, address):
        return "t=" + urllib.parse.quote(address, safe="")

    def sessions(self):
        return self._call("GET", "/api/sessions", timeout=20).get("sessions", [])

    def hear(self, wav: bytes, timeout=45):
        return self._call("POST", "/api/hear", wav, "audio/wav", timeout=timeout)

    def say(self, address, text, spoken=True):
        """Send words to a session. spoken=True marks them as dictated, so the server tidies them as it does speech."""
        return self._call("POST", f"/api/say?{self._t(address)}{'&spoken=1' if spoken else ''}", json.dumps({"text": text}).encode(), "application/json", timeout=200)

    def collect(self, address):
        return self._call("POST", f"/api/collect?{self._t(address)}", timeout=200)

    def answer(self, address, choice, what):
        return self._call("POST", f"/api/answer?{self._t(address)}&choice={choice}", json.dumps({"what": what}).encode(), "application/json", timeout=60)

    def stop(self, address):
        return self._call("POST", f"/api/stop?{self._t(address)}", timeout=30)

    def history(self, address, limit=30):
        return self._call("GET", f"/api/history?{self._t(address)}&limit={limit}", timeout=20).get("items", [])

    def now(self, address):
        return self._call("POST", f"/api/now?{self._t(address)}", timeout=30)

    def announcements(self, after, wait=25):
        return self._call("GET", f"/api/announcements?after={after}&wait={wait}", timeout=wait + 40)


def pair(url: str):
    """Trade the one-time code in a pairing link for the access key.

    The link is https://<address>:<port>/#pair=<code>. The server's certificate authority is fetched
    from its plain-HTTP side door on the next port and trusted from then on, as a phone does.
    Returns (server, key). Raises ApiError with words for the screen.
    """
    try:
        u = urllib.parse.urlsplit(url.strip())
        code = urllib.parse.parse_qs(u.fragment).get("pair", [""])[0]
        port = u.port or 443  # raises on a port that is not a number in range
    except ValueError:
        code = ""
    if not code or u.scheme != "https" or not u.hostname:
        raise ApiError("That is not a herdr-voice pairing code.")
    server = f"https://{u.hostname}:{port}"
    try:
        with urllib.request.urlopen(f"http://{u.hostname}:{port + 1}/ca.crt", timeout=10) as r:
            pem = r.read()
    except urllib.error.HTTPError as e:
        raise ApiError("The server refused this device's address." if e.code == 403 else "Could not fetch the server's certificate.")
    except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
        raise ApiError("Can't reach the voice server.") from e
    if b"BEGIN CERTIFICATE" not in pem:
        raise ApiError("Could not fetch the server's certificate.")
    try:
        ctx = tls(cadata=pem.decode())
        req = urllib.request.Request(server + "/api/pair", data=json.dumps({"code": code}).encode(), method="POST",
                                     headers={"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
            key = json.loads(r.read()).get("key")
    except urllib.error.HTTPError as e:
        raise ApiError("The server refused this device's address." if e.code == 403 and not e.headers.get("content-type", "").startswith("application/json")
                       else "That code has expired or was used. Show a new one.")
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as e:
        raise unreachable(e) from e
    if not key:
        raise ApiError("The server did not hand over a key.")
    store.save_ca(pem)
    return server, key
