"""herdr-voice on the CardputerZero: pick a session, hold the talk key, speak, hear the reply."""
import functools, os, signal, sys, threading, time, traceback

from PIL import Image, ImageDraw, ImageFont

from . import api, audio, camera, hw, store

FONTS = "/usr/share/APPLaunch/share/font/"
BG, FG, DIM, ACCENT, WARN, GOOD = (10, 12, 16), (236, 238, 242), (128, 136, 150), (255, 176, 46), (255, 99, 88), (96, 208, 140)
EXIT_HOLD = 1.0  # seconds of ESC to leave, as in M5Stack's own apps
KEY_F, KEY_X = 33, 45  # up and down on this keyboard, as on the launcher's home screen
LIVE_EVERY = 0.2  # the wait between live-words sends, as a share of the clip so far (at least a second)
MAX_TEXT = 4000   # characters kept of any one message, so a pasted blob cannot stall the screen
COLLECT_GAP = 1.0  # seconds from one "what now?" call to the next, at least


def font(size, bold=False):
    for name in (("Montserrat-Bold.ttf",) if bold else ()) + ("DejaVuSans.ttf",):
        try:
            return ImageFont.truetype(FONTS + name, size)
        except OSError:
            pass
    return ImageFont.load_default()


F_SMALL, F_BODY, F_BIG = font(11), font(16), font(22, bold=True)


def clip_text(text):
    text = str(text)
    return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 1] + "…"


_MEASURE = ImageDraw.Draw(Image.new("RGB", (1, 1)))


@functools.lru_cache(maxsize=256)  # the same conversation is drawn again on every change
def wrap(text, fnt, width):
    def fits(s):
        return _MEASURE.textlength(s, font=fnt) <= width

    def fitting(word):
        """How many characters from the start of the word fit (at least one): found by doubling, then halving."""
        lo, hi = 1, 2
        while hi <= len(word) and fits(word[:hi]):
            lo, hi = hi, hi * 2
        hi = min(hi, len(word) + 1)
        while hi - lo > 1:
            mid = (lo + hi) // 2
            lo, hi = (mid, hi) if fits(word[:mid]) else (lo, mid)
        return lo

    lines = []
    for para in clip_text(text).split("\n"):
        line = ""
        for word in para.split(" "):
            trial = (line + " " + word).strip()
            if fits(trial):
                line = trial
                continue
            if line:
                lines.append(line)
            if not fits(word):  # one very long word: cut into pieces that fit
                cut = fitting(word)
                while cut < len(word):
                    lines.append(word[:cut]); word = word[cut:]
                    cut = fitting(word)
            line = word
        lines.append(line)
    return tuple(lines)


def session_name(s):
    return s.get("name") or s.get("title") or s.get("folder") or s.get("address", "?")


class App:
    def __init__(self):
        self.screen, self.keys = hw.Screen(), hw.Keys()
        self.rec, self.player = audio.Recorder(), audio.Player()
        self.cfg = store.config()
        self.lock = threading.Lock()
        self.api = None
        self.mode = "start"        # start, pin, pair, main, sessions, menu
        self.logs = {}             # session address -> [(kind, text)]; kinds: you, said, sys, err, from
        self.sessions, self.pick, self.target = [], 0, None
        self.loading = False       # a session list is on its way
        self.busy = ""             # words for what is happening now, or ""
        self.listening = False
        self.listen_id = 0         # goes up each time recording starts, so a late answer for an old clip is ignored
        self.listen_began = 0.0
        self.live_lock = threading.Lock()  # held while a live-words clip is on its way to the server
        self.partial = ""          # what has been heard so far in the clip being recorded
        self.ask = None            # a permission prompt: {text, what, can}
        self.queued = None         # address of a session holding a mid-task message it has not read yet
        self.scroll = 0            # lines scrolled up from the newest; 0 shows the latest
        self.typing = None         # the message being typed, or None when the typing line is closed
        self.shift_held = False
        self.shift_next = False    # shift tapped by itself: the next letter is a capital
        self.pin, self.pin_note, self.pin_stage, self.pin_first = [], "", "", None
        self.pending_key = None    # a key just received by pairing, waiting for the PIN choice
        self.pair_note, self.pair_thumb, self.pairing = "", None, False
        self.menu_pick = 0
        self.esc_down = None
        self.esc_used = False      # this press of ESC already did something; its release and hold do nothing more
        self.dirty, self.running = True, True
        self.batt = hw.battery()   # (percent, charging), read again on the 20-second tick, not on every redraw
        self.frame = None

    # ---- state shared with worker threads -------------------------------------------------
    def say(self, kind, text, address=None):
        """Add a line to a session's conversation: the given one, or the one on screen."""
        if not text:
            return
        with self.lock:
            if address is None:
                address = (self.target or {}).get("address", "")
            log = self.logs.setdefault(address, [])
            log.append((kind, clip_text(text)))
            del log[:-60]
            self.dirty = True
        self.keys.wake()

    def set(self, **kw):
        with self.lock:
            for k, v in kw.items():
                setattr(self, k, v)
            self.dirty = True
        self.keys.wake()

    def work(self, fn, *a):
        threading.Thread(target=fn, args=a, daemon=True).start()

    # ---- drawing --------------------------------------------------------------------------
    def draw(self):
        img = Image.new("RGB", (hw.W, hw.H), BG)
        d = ImageDraw.Draw(img)
        getattr(self, "draw_" + self.mode, self.draw_main)(img, d)
        self.frame = img
        self.screen.show(img)

    def header(self, d, left, colour=FG):
        d.rectangle((0, 0, hw.W, 17), fill=(24, 28, 36))
        pct, charging = self.batt
        right = "" if pct is None else f"{pct}%{'+' if charging else ''}"
        d.text((hw.W - 4 - d.textlength(right, font=F_SMALL), 3), right, font=F_SMALL, fill=DIM)
        room = hw.W - 16 - d.textlength(right, font=F_SMALL)
        while left and d.textlength(left, font=F_SMALL) > room:
            left = left[:-2] + "…" if not left.endswith("…") else left[:-2] + "…"
        d.text((5, 3), left, font=F_SMALL, fill=colour)

    def footer(self, d, text):
        d.text((5, hw.H - 14), text, font=F_SMALL, fill=DIM)

    def centre(self, d, big, small="", colour=FG):
        d.text(((hw.W - d.textlength(big, font=F_BIG)) / 2, 58), big, font=F_BIG, fill=colour)
        y = 92
        for line in wrap(small, F_BODY, hw.W - 20)[:4]:
            d.text(((hw.W - d.textlength(line, font=F_BODY)) / 2, y), line, font=F_BODY, fill=DIM); y += 19

    def draw_start(self, img, d):
        self.header(d, "herdr-voice")
        self.centre(d, "Starting…")

    def draw_pin(self, img, d):
        self.header(d, "herdr-voice")
        title = {"unlock": "Enter PIN", "new": "Set a PIN?", "again": "PIN again"}[self.pin_stage]
        self.centre(d, title, self.pin_note or ("Any keys. ENTER when done." if self.pin_stage != "new" else "Type any keys, then ENTER. ENTER alone = no PIN."))
        dots = "•" * len(self.pin)
        d.text(((hw.W - d.textlength(dots, font=F_BIG)) / 2, 28), dots, font=F_BIG, fill=ACCENT)
        self.footer(d, "ENTER ok   BKSP clear   hold ESC quit")

    def draw_pair(self, img, d):
        self.header(d, "Pair with your Mac")
        if self.pair_thumb is not None:
            img.paste(self.pair_thumb, (hw.W - self.pair_thumb.width - 6, 24))
        y = 26
        for line in wrap(self.pair_note or "On the Mac: HerdrVoice > Connect a Phone. Point the camera at the code, then press ENTER.", F_BODY, hw.W - (120 if self.pair_thumb is not None else 14)):
            d.text((7, y), line, font=F_BODY, fill=FG); y += 19
        self.footer(d, "ENTER scan   hold ESC quit")

    def draw_sessions(self, img, d):
        self.header(d, "Sessions")
        if not self.sessions:
            self.centre(d, "No sessions", "The server sees no agent sessions.")
        rows = 6
        top = max(0, min(self.pick - rows // 2, len(self.sessions) - rows))
        for i, s in enumerate(self.sessions[top: top + rows]):
            y = 22 + i * 22
            chosen = top + i == self.pick
            if chosen:
                d.rectangle((0, y - 1, hw.W, y + 20), fill=(40, 46, 60))
            st = s.get("status")
            mark, colour = ("●", ACCENT) if st == "working" else ("!", WARN) if st == "blocked" else ("·", DIM)
            d.text((6, y), mark, font=F_BODY, fill=colour)
            label = session_name(s)
            machine = s.get("machine") or ""
            if machine and machine != "This machine":
                label += "  @" + machine
            while d.textlength(label, font=F_BODY) > hw.W - 28:
                label = label[:-2] + "…" if not label.endswith("…") else label[:-3] + "…"
            d.text((20, y), label, font=F_BODY, fill=FG if chosen else DIM)
        self.footer(d, "F/X move   ENTER pick   ESC back")

    MENU = ["Back", "Refresh sessions", "Pair again", "Quit"]

    def draw_menu(self, img, d):
        self.header(d, "Menu")
        for i, item in enumerate(self.MENU):
            y = 22 + i * 24
            if i == self.menu_pick:
                d.rectangle((0, y - 1, hw.W, y + 21), fill=(40, 46, 60))
            d.text((10, y), item, font=F_BODY, fill=FG if i == self.menu_pick else DIM)
        self.footer(d, "F/X move   ENTER pick   ESC back")

    def draw_main(self, img, d):
        t = self.target
        st = (t or {}).get("status")
        self.header(d, (session_name(t) if t else "No session") + ("  · working" if st == "working" else "  · needs you" if st == "blocked" else ""),
                    ACCENT if st == "working" else WARN if st == "blocked" else FG)
        if self.listening:
            d.rectangle((0, 18, hw.W, hw.H), fill=(60, 16, 20))
            if not self.partial:
                self.centre(d, "Listening", "Release to send. ESC to cancel.", colour=(255, 214, 210))
                return
            # Your words as they are heard so far: the end of them, if there are more than fit.
            d.text((7, 21), "Listening", font=F_SMALL, fill=(255, 170, 160))
            y = 36
            for line in wrap(self.partial, F_BODY, hw.W - 14)[-6:]:
                d.text((7, y), line, font=F_BODY, fill=(255, 232, 228)); y += 19
            self.footer(d, "release to send   ESC cancel")
            return
        if self.ask:
            d.rectangle((0, 18, hw.W, hw.H), fill=(48, 36, 8))
            y = 24
            for line in wrap(self.ask.get("text", "It is asking for permission."), F_BODY, hw.W - 14)[:6]:
                d.text((7, y), line, font=F_BODY, fill=FG); y += 19
            self.footer(d, "ENTER allow   ESC deny" if self.ask.get("can") else "Answer it in Herdr.   ESC dismiss")
            return
        lines = []
        for kind, text in self.logs.get((t or {}).get("address", ""), []):
            colour = {"you": DIM, "said": FG, "sys": DIM, "err": WARN, "from": GOOD}.get(kind, FG)
            for i, line in enumerate(wrap(("› " if kind == "you" else "") + text, F_BODY, hw.W - 16)):
                lines.append((line, colour))
        room = 6 if self.busy or self.typing is not None or (self.queued is not None and t and self.queued == t.get("address")) else 7
        self.scroll = max(0, min(self.scroll, len(lines) - room))
        end = len(lines) - self.scroll
        y = 22
        for line, colour in lines[max(0, end - room): end]:
            d.text((6, y), line, font=F_BODY, fill=colour); y += 19
        if len(lines) > room:  # a thin bar on the right: how much there is, and where you are in it
            top, height = 20, room * 19
            bar = max(8, height * room // len(lines))
            pos = top + (height - bar) * (len(lines) - room - self.scroll) // (len(lines) - room)
            d.rectangle((hw.W - 4, top, hw.W - 2, top + height), fill=(30, 34, 44))
            d.rectangle((hw.W - 4, pos, hw.W - 2, pos + bar), fill=ACCENT if self.scroll else DIM)
        if not lines and not self.busy:
            self.centre(d, "Hold to talk", "Hold SPACE, speak, release." if t else "No session picked. Press TAB.")
        waiting = self.queued is not None and t and self.queued == t.get("address")
        if self.typing is not None:
            d.rectangle((0, hw.H - 37, hw.W, hw.H - 15), fill=(30, 34, 44))
            shown = "> " + self.typing + "_"
            while d.textlength(shown, font=F_BODY) > hw.W - 12:  # keep the end of a long message in view
                shown = "> …" + shown[4:]
            d.text((6, hw.H - 35), shown, font=F_BODY, fill=FG)
            self.footer(d, "type your message   ENTER send   ESC cancel")
            return
        if self.busy:
            d.text((6, hw.H - 34), self.busy, font=F_BODY, fill=ACCENT)
        elif waiting:
            d.text((6, hw.H - 34), "Not read yet.  N = send now", font=F_BODY, fill=ACCENT)
        self.footer(d, "SPACE talk  ENTER type  F/X scroll  TAB list  M menu")

    # ---- starting up, PIN, pairing ----------------------------------------------------------
    def begin(self):
        if not store.paired():
            return self.set(mode="pair")
        if store.has_pin():
            return self.set(mode="pin", pin_stage="unlock", pin=[], pin_note="")
        self.connect(store.load_key())

    def connect(self, key):
        if not key:
            return self.set(mode="pair", pair_note="No access key stored. Pair again.")
        self.cfg = store.config()
        self.api = api.Api(self.cfg["server"], key, self.cfg["client"])
        self.set(mode="main", busy="Connecting…")
        self.work(self.load_sessions, True)
        self.work(self.announcements)

    def load_sessions(self, first=False):
        a = self.api  # worker threads keep their own reference: "Pair again" clears self.api
        with self.lock:
            if not a or (self.loading and not first):
                return  # the list already on its way brings the same news
            self.loading = True
        try:
            rows = a.sessions()
        except api.ApiError as e:
            self.say("err", str(e)); self.set(busy="")
            return
        finally:
            with self.lock:
                self.loading = False
        with self.lock:
            self.sessions = rows
            keep = self.target and next((s for s in rows if s["address"] == self.target["address"]), None)
            last = next((s for s in rows if s["address"] == self.cfg.get("last")), None)
            self.target = keep or last or (rows[0] if rows else None)
            self.pick = rows.index(self.target) if self.target in rows else 0
            self.busy = ""
            if self.queued and not any(s["address"] == self.queued and s.get("status") == "working" for s in rows):
                self.queued = None  # that session is no longer mid-step: it has taken the message
            self.dirty = True
        self.keys.wake()
        if first and self.target:
            self.work(self.load_history, self.target["address"])
            if self.target.get("status") == "blocked":
                self.work(self.check_ask)

    def load_history(self, address):
        """Show what was already said in this session, from the server's record of it."""
        a = self.api
        if not a:
            return
        try:
            items = a.history(address)
        except api.ApiError:
            return
        lines = [("you" if x.get("who") == "me" else "said", clip_text(x.get("text"))) for x in items if x.get("text")]
        with self.lock:
            self.logs[address] = lines[-60:]
            self.dirty = True
        self.keys.wake()

    def check_ask(self):
        a, t = self.api, self.target
        if not a or not t:
            return
        try:
            self.show_result(a.collect(t["address"]), quiet=True, address=t["address"])
        except api.ApiError:
            pass

    def scan(self):
        try:
            self.scan_once()
        except Exception:  # whatever went wrong, ENTER must be able to try again
            traceback.print_exc()
            self.set(pair_note="That did not work. ENTER to try again.")
        finally:
            self.set(pairing=False)

    def scan_once(self):
        test_link = os.environ.get("HVC_TEST_PAIR_URL")  # tests hand over the link the camera would have read
        if test_link:
            try:
                server, key = api.pair(test_link)
            except api.ApiError as e:
                return self.set(pair_note=f"{e} ENTER to try again.")
            cfg = store.config(); cfg["server"] = server; store.save_config(cfg)
            return self.set(pending_key=key, mode="pin", pin_stage="new", pin=[], pin_note="")
        problem = camera.available()
        if problem:
            return self.set(pair_note=problem)
        for attempt in range(1, 21):
            if self.mode != "pair":
                return
            self.set(pair_note=f"Looking for the code… ({attempt})")
            shot = camera.shoot()
            if shot is None:
                return self.set(pair_note="The camera did not give a picture. ENTER to try again.")
            thumb = shot.resize((108, 81)).convert("RGB")
            self.set(pair_thumb=thumb)
            text = camera.decode(shot)
            if text:
                self.set(pair_note="Found it. Pairing…")
                try:
                    server, key = api.pair(text)
                except api.ApiError as e:
                    return self.set(pair_note=f"{e} ENTER to try again.")
                cfg = store.config(); cfg["server"] = server; store.save_config(cfg)
                return self.set(pending_key=key, pair_thumb=None, mode="pin", pin_stage="new", pin=[], pin_note="")
        self.set(pair_note="No code seen. Hold it steadier or closer, then ENTER.")

    def pin_done(self):
        if self.pin_stage == "unlock":
            self.set(pin_note="Checking…"); self.draw()
            key = store.load_key(self.pin)
            if not key:
                return self.set(pin=[], pin_note="Wrong PIN. Try again.")
            self.pin = []
            return self.connect(key)
        if self.pin_stage == "new":
            if not self.pin:
                store.save_key(self.pending_key); key, self.pending_key = self.pending_key, None
                return self.connect(key)
            return self.set(pin_first=list(self.pin), pin=[], pin_stage="again", pin_note="")
        if self.pin != self.pin_first:
            return self.set(pin=[], pin_first=None, pin_stage="new", pin_note="They did not match. Start again.")
        self.set(pin_note="Saving…"); self.draw()
        store.save_key(self.pending_key, self.pin); key, self.pending_key = self.pending_key, None
        self.pin = []
        self.connect(key)

    # ---- one turn ---------------------------------------------------------------------------
    def live_words(self, listen_id, began):
        """While a clip is being recorded, ask what has been heard so far. The server hears the whole clip
        each time, so the longer it gets, the longer the wait between sends; never two at once."""
        a = self.api
        time.sleep(0.9)
        last = None
        while self.running and self.listening and self.listen_id == listen_id and a:
            started = hw.now()
            self.rec.update()  # converts what was recorded since the last second
            due = last is None or started - last >= max(1.0, (started - began) * LIVE_EVERY)
            if due and self.live_lock.acquire(blocking=False):
                try:
                    wav, _, _ = self.rec.so_far()
                    if wav is not None:
                        last = started
                        heard = a.hear(wav, timeout=12).get("heard") or ""
                        if heard and self.listening and self.listen_id == listen_id:
                            self.set(partial=heard)
                except api.ApiError:
                    pass
                finally:
                    self.live_lock.release()
            time.sleep(max(0.1, 1.0 - (hw.now() - started)))

    def turn(self, wav):
        t, a = self.target, self.api
        try:
            if not a:
                return
            self.set(busy="Hearing you…")
            h = a.hear(wav)
            if not h.get("heard"):
                self.say("err", h.get("error") or "I didn't catch anything.", t["address"])
                return
            self.exchange(a, t, h["heard"], spoken=True)
        except api.ApiError as e:
            self.say("err", str(e), t["address"])
        finally:
            self.set(busy="")
            self.work(self.load_sessions)

    def send_typed(self, text):
        t, a = self.target, self.api
        try:
            if a:
                self.exchange(a, t, text, spoken=False)
        except api.ApiError as e:
            self.say("err", str(e), t["address"])
        finally:
            self.set(busy="")
            self.work(self.load_sessions)

    def exchange(self, a, t, words, spoken):
        """Send the words to the session and show what comes back, until it stops working."""
        self.say("you", words, t["address"])
        self.set(busy="Sent. Waiting…")
        since = hw.now()
        j = a.say(t["address"], words, spoken)  # never sent twice: it may have reached the agent
        self.show_result(j, address=t["address"])
        self.follow(a, t["address"], j, since)

    def follow(self, a, address, j, since):
        """While the session is working, keep asking what it has to say, and show it. Each call starts at
        least COLLECT_GAP after the one before (started at `since`), so a server that answers "working" at
        once cannot make a busy loop. Asking again is harmless, so a network error is tried twice more."""
        failed = 0
        while j.get("status") == "working" and not j.get("steered") and self.running:
            self.set(busy="Still working…")
            time.sleep(max(0.0, COLLECT_GAP - (hw.now() - since)))
            since = hw.now()
            try:
                j = a.collect(address)
            except api.NetworkError:
                failed += 1
                if failed > 2:
                    raise
                continue
            failed = 0
            self.show_result(j, address=address)

    def show_result(self, j, quiet=False, address=None):
        if address is None:
            address = (self.target or {}).get("address", "")
        on_screen = address == (self.target or {}).get("address", "")
        if j.get("note"):
            self.say("sys", j["note"], address)
        if j.get("steered"):
            # The agent only reads a mid-task message when its current step ends. Say so, and keep
            # saying so on the status line until it has been read or pushed through.
            self.say("sys", "Sent mid-task. It reads this when its current step ends.", address)
            self.set(queued=address)
        if j.get("spoken"):
            self.say("said", j["spoken"], address); self.player.play_b64(j.get("audio"))
        if j.get("ask") and on_screen:  # a prompt belongs to the session on screen; another one's shows when you switch to it
            self.set(ask=j["ask"]); self.player.play_b64(j["ask"].get("audio"))
        elif j.get("error") and not quiet:
            self.say("err", j["error"], address)

    def answer(self, choice, ask):
        t, a = self.target, self.api
        self.set(busy="Allowing…" if choice == "allow" else "Denying…")
        try:
            if not a:
                return
            since = hw.now()
            try:
                r = a.answer(t["address"], choice, ask.get("what") or "")
            except api.ApiError:
                with self.lock:
                    if self.ask is None and (self.target or {}).get("address") == t["address"]:
                        self.ask = ask  # it was not answered: show it again
                raise
            if r.get("ask"):
                self.say("sys", "The request changed. Look again.", t["address"]); self.set(ask=r["ask"])
            elif not r.get("answered"):
                self.say("err", r.get("error") or "Could not answer it.", t["address"])
            elif choice == "allow":
                self.follow(a, t["address"], {"status": "working"}, since)
            else:
                self.say("sys", "Denied.", t["address"])
        except api.ApiError as e:
            self.say("err", str(e), t["address"])
        finally:
            self.set(busy="")
            self.work(self.load_sessions)

    def send_now(self):
        address, a = self.queued, self.api
        if not address or not a:
            return
        try:
            r = a.now(address)
            self.say("sys", "Pushed through now." if r.get("sent") else (r.get("error") or "Could not push it through."), address)
            if r.get("sent"):
                self.set(queued=None)
        except api.ApiError as e:
            self.say("err", str(e), address)

    def stop_task(self):
        self.set(queued=None)
        self.player.stop()
        t, a = self.target, self.api
        if not t or not a:
            return
        address = t["address"]
        try:
            r = a.stop(address)
            self.say("sys", "Stopped." if r.get("stopped") else "Nothing to stop.", address)
        except api.ApiError as e:
            self.say("err", str(e), address)

    def announcements(self):
        last, epoch, pause = -1, None, 5
        mine = self.api
        while self.running and self.api is mine:
            try:
                r = mine.announcements(last)
                if epoch is not None and r.get("epoch") != epoch:
                    last = -1  # the server restarted: start from now
                epoch = r.get("epoch")
                for x in r.get("items", []):
                    if x.get("address") == self.queued:
                        self.set(queued=None)  # it is talking again, so it has read the message
                    current = (self.target or {}).get("address", "")
                    self.say("said", x.get("text"), x.get("address") or current)  # kept with its own session
                    if x.get("address") and x.get("address") != current:
                        self.say("from", f"{x.get('from')}: {x.get('text')}", current)  # and shown where you are
                    if x.get("play"):
                        self.player.play_b64(x.get("audio"))
                last = r.get("last", last) if r.get("items") or last < 0 else max(last, max((x["id"] for x in r.get("items", [])), default=last))
                pause = 5
            except Exception as e:  # each try is a new TLS handshake: wait longer each time, up to a minute
                if not isinstance(e, api.ApiError):
                    traceback.print_exc()  # a reply it did not expect; the thread carries on
                time.sleep(pause)
                pause = min(60, pause * 2)

    # ---- keys -------------------------------------------------------------------------------
    def key(self, code, down):
        if code == hw.ESC:
            if down:
                if self.listening:  # changed your mind: nothing is sent
                    self.listening, self.esc_used, self.dirty = False, True, True
                    self.rec.cancel()
                    self.say("sys", "Cancelled.")
                    return
                if self.mode == "main" and self.typing is not None:  # close the typing line, send nothing
                    self.typing, self.esc_used, self.dirty = None, True, True
                    return
                self.esc_down = hw.now()
                return
            if self.esc_used:
                self.esc_used = False
                return
            pressed, self.esc_down = self.esc_down, None
            if pressed is None:
                return
            if hw.now() - pressed >= EXIT_HOLD:
                self.running = False
                return
            down = True  # a short press and release: the screen gets it as one ESC press (back, deny, dismiss)
        handler = getattr(self, "key_" + self.mode, None)
        if handler:
            handler(code, down)
            self.dirty = True

    def key_pin(self, code, down):
        if not down:
            return
        if code in (hw.ENTER, hw.KPENTER):
            self.pin_done()
        elif code == hw.BACKSPACE:
            self.pin = []
        elif code != hw.ESC and len(self.pin) < 32:
            self.pin.append(code)

    def key_pair(self, code, down):
        if down and code in (hw.ENTER, hw.KPENTER) and not self.pairing:
            self.pairing = True
            self.work(self.scan)

    def key_menu(self, code, down):
        if not down:
            return
        if code in (hw.TAB, hw.DOWN, KEY_X):
            self.menu_pick = (self.menu_pick + 1) % len(self.MENU)
        elif code in (hw.UP, KEY_F):
            self.menu_pick = (self.menu_pick - 1) % len(self.MENU)
        elif code == hw.ESC:
            self.mode = "main"
        elif code in (hw.ENTER, hw.KPENTER):
            item = self.MENU[self.menu_pick]
            self.mode = "main"
            if item == "Refresh sessions":
                self.work(self.load_sessions)
            elif item == "Pair again":
                store.forget(); self.api = None
                self.mode, self.pair_note, self.logs = "pair", "", {}
            elif item == "Quit":
                self.running = False

    def key_sessions(self, code, down):
        if not down:
            return
        n = len(self.sessions)
        if code in (hw.TAB, hw.DOWN, KEY_X) and n:
            self.pick = (self.pick + 1) % n
        elif code in (hw.UP, KEY_F) and n:
            self.pick = (self.pick - 1) % n
        elif code == hw.ESC:
            self.mode = "main"
        elif code in (hw.ENTER, hw.KPENTER):
            if n:
                self.target = self.sessions[self.pick]
                c = store.config()
                if c.get("last") != self.target["address"]:  # written only when it changes: the card wears
                    c["last"] = self.target["address"]; store.save_config(c)
                self.cfg["last"] = c["last"]
                self.ask = None
                self.scroll = 0
                self.work(self.load_history, self.target["address"])
                if self.target.get("status") == "blocked":
                    self.work(self.check_ask)
            self.mode = "main"

    def end_listening(self):
        """SPACE let go, or the recording reached its limit: send what was recorded."""
        self.listening, self.dirty = False, True
        wav, secs, _ = self.rec.finish()
        if wav is None:
            self.say("sys", "Too short." if secs < audio.MIN_SECS else "I heard nothing. Is the microphone covered?")
        else:
            self.work(self.turn, wav)

    def key_typing(self, code, down):
        """Every key is text while the typing line is open."""
        if code in (hw.LEFTSHIFT, hw.RIGHTSHIFT):
            self.shift_held = down
            if down:
                self.shift_next = not self.shift_next  # a tap arms one capital; a second tap disarms it
            return
        if not down:
            return
        if code in (hw.ENTER, hw.KPENTER):
            text, self.typing = self.typing.strip(), None
            if text and self.target and self.api:
                self.scroll = 0
                self.work(self.send_typed, text)
        elif code == hw.BACKSPACE:
            self.typing = self.typing[:-1]
        elif code in hw.CHARS and len(self.typing) < 500:
            c = hw.CHARS[code]
            if self.shift_held or self.shift_next:
                c = c.upper()
            self.shift_next = False
            self.typing += c

    def key_main(self, code, down):
        if self.typing is not None:
            return self.key_typing(code, down)
        if code == hw.SPACE:
            if down and not self.listening and self.target and not self.ask and self.api:
                self.player.stop()
                self.scroll = 0
                self.listen_id += 1
                self.partial = ""
                self.rec.start(); self.listening, self.listen_began = True, hw.now()
                self.work(self.live_words, self.listen_id, self.listen_began)
            elif not down and self.listening:
                self.end_listening()
            return
        if not down or self.listening:
            return
        if self.ask:
            enter, esc = code in (hw.ENTER, hw.KPENTER), code == hw.ESC
            if self.ask.get("can") and (enter or esc):
                ask, self.ask = self.ask, None  # taken off the screen here, so a second quick ENTER cannot answer it again
                self.work(self.answer, "allow" if enter else "deny", ask)
            elif esc:
                self.ask = None
            return
        if code in (hw.ENTER, hw.KPENTER) and self.target and self.api:
            self.typing, self.shift_held, self.shift_next = "", False, False
        elif code in (KEY_F, hw.UP):      # older
            self.scroll += 3
        elif code in (KEY_X, hw.DOWN):  # newer
            self.scroll = max(0, self.scroll - 3)
        elif code == hw.TAB:
            self.mode = "sessions"
            self.work(self.load_sessions)
        elif code == hw.BACKSPACE:
            self.work(self.stop_task)
        elif code == 49 and self.queued:  # N
            self.work(self.send_now)
        elif code == 50:  # M
            self.mode, self.menu_pick = "menu", 0

    # ---- main loop --------------------------------------------------------------------------
    def run(self):
        signal.signal(signal.SIGTERM, lambda *_: (setattr(self, "running", False), self.keys.wake()))
        signal.signal(signal.SIGUSR1, lambda *_: self.frame and self.frame.save(os.path.join(audio.RUN, "hvc-screen.png")))
        self.draw()
        self.begin()
        tick = 0.0
        try:
            while self.running:
                # Sleep until something can happen: a key, a worker thread waking the reader, or the next deadline.
                due = [tick + (4 if self.queued else 20)]
                if self.esc_down is not None:
                    due.append(self.esc_down + EXIT_HOLD)
                if self.listening:
                    due.append(self.listen_began + audio.MAX_SECS)
                for code, down in self.keys.read(max(0.0, min(due) - hw.now())):
                    self.key(code, down)
                if self.esc_down is not None and hw.now() - self.esc_down >= EXIT_HOLD:
                    self.running = False
                if self.listening and hw.now() - self.listen_began >= audio.MAX_SECS:
                    self.say("sys", f"Stopped at the {audio.MAX_SECS // 60}-minute limit.")
                    self.end_listening()  # as if SPACE were let go
                if hw.now() - tick >= (4 if self.queued else 20):  # the battery figure and session states go stale
                    tick, self.dirty, self.batt = hw.now(), True, hw.battery()
                    if self.queued and self.api:
                        self.work(self.load_sessions)  # notices when the queued message has been read
                if self.dirty:
                    self.dirty = False
                    self.draw()
        finally:
            self.running = False
            self.rec.stop_quietly()
            self.player.stop()
            img = Image.new("RGB", (hw.W, hw.H), (0, 0, 0))
            self.screen.show(img)
            self.screen.close()


def main():
    App().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
