"""Recording and playback through PipeWire.

The microphone is on the left channel only and quiet, so a clip is recorded in stereo at the
codec's own rate, the left channel is kept, levelled, and sent as 16 kHz mono WAV.

The clip is converted while it is being recorded: each call reads only what pw-record has added to
the file since the last one, so the work per call stays small and the clip is held once, as 16 kHz
mono 16-bit samples (32 KB a second).
"""
import base64, ctypes, io, os, queue, signal, struct, subprocess, tempfile, threading, wave

import numpy as np

RUN = os.environ.get("HVC_RUNDIR") or os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()  # a RAM disk: clips never touch the card
REC = os.path.join(RUN, "hvc-rec.wav")
MAX_GAIN = 60.0   # a quiet room is not turned into a roar
MIN_SECS = 0.35
MAX_SECS = 120    # a recording ends here as if the key were let go: 23 MB of the RAM disk at 48 kHz stereo
RATE, CHANNELS, OUT_RATE = 48000, 2, 16000
STEP = RATE // OUT_RATE        # input frames averaged into one output sample
CHUNK = 96000                  # bytes read from the recording at a time: half a second

try:
    _prctl = ctypes.CDLL(None, use_errno=True).prctl  # Linux only
except (OSError, AttributeError):
    _prctl = None
_APP = os.getpid()


def _die_with_app():
    """Run in pw-record just before it starts: the kernel kills it when the thread that started it
    ends, so a killed app does not leave it filling the RAM disk. Recordings start on the UI thread."""
    _prctl(1, signal.SIGKILL)  # PR_SET_PDEATHSIG
    if os.getppid() != _APP:   # the app was already gone
        os._exit(1)


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


class Downmix:
    """The left channel of a 48 kHz stereo recording, averaged down to 16 kHz as the file grows."""

    def __init__(self):
        self.start = self.pos = None   # where the samples begin in the file, and the next unread byte
        self.rest = b""                # bytes read that do not yet make a whole group of STEP frames
        self.out = np.empty(OUT_RATE * (MAX_SECS + 2), dtype="<i2")  # memory is only touched as it fills
        self.n = 0                     # samples in out
        self.total = self.frames = 0   # sum and count of left-channel samples, for the level offset

    def feed(self, path, final=False):
        """Convert what has been added to the file since the last call. final: pw-record has stopped,
        so its header now says how long the samples are; anything after them is not audio."""
        try:
            with open(path, "rb") as f:
                if self.start is None:
                    # The file is still being written, so its header does not yet say how long it is:
                    # the samples are taken as everything after the header.
                    head = f.read(512)
                    at = head.find(b"data", 12)
                    if at < 0 or len(head) < at + 8:
                        return
                    self.start = self.pos = at + 8
                end = None
                if final:
                    f.seek(self.start - 4)
                    size = struct.unpack("<I", f.read(4))[0]
                    if 0 < size <= os.fstat(f.fileno()).st_size - self.start:
                        end = self.start + size
                f.seek(self.pos)
                while end is None or self.pos < end:
                    data = f.read(CHUNK if end is None else min(CHUNK, end - self.pos))
                    if not data:
                        break
                    self.pos += len(data)
                    self._add(data)
        except (OSError, struct.error):
            pass

    def _add(self, data):
        group = STEP * CHANNELS * 2
        data = self.rest + data
        k = min(len(data) // group, len(self.out) - self.n)  # whole groups, as far as there is room
        self.rest = data[k * group:] if self.n + k < len(self.out) else b""
        if not k:
            return
        left = np.frombuffer(data, dtype="<i2", count=k * group // 2)[::CHANNELS]
        sums = left.reshape(-1, STEP).sum(axis=1, dtype=np.int32)
        self.total += int(sums.sum(dtype=np.int64))
        self.frames += len(left)
        self.out[self.n: self.n + k] = (sums + STEP // 2) // STEP  # the average, rounded: a rough low-pass, enough for speech
        self.n += k

    def wav(self):
        """(16 kHz mono WAV of the left channel so far, levelled; seconds; peak 0..1), or (None, seconds, peak)
        if too short or silent."""
        tail = np.frombuffer(self.rest[: len(self.rest) // 4 * 4], dtype="<i2")[::CHANNELS]  # whole frames not yet in a group
        frames = self.frames + len(tail)
        secs = frames / float(RATE)
        if secs < MIN_SECS:
            return None, secs, 0.0
        mean = np.float32((self.total + int(tail.sum(dtype=np.int64))) / frames)
        x = self.out[: self.n]
        peak = float(max(np.float32(x.max()) - mean, mean - np.float32(x.min()))) if self.n else 0.0
        if peak < 4:  # nothing but the noise floor
            return None, secs, peak / 32768
        gain = min(MAX_GAIN, 29000.0 / peak)
        out = io.BytesIO()
        with wave.open(out, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(OUT_RATE); w.setnframes(self.n)
            for i in range(0, self.n, CHUNK // 4):
                y = x[i: i + CHUNK // 4].astype(np.float32)
                y -= mean; y *= gain
                w.writeframesraw(np.clip(y, -32767, 32767, out=y).astype("<i2").tobytes())
        return out.getvalue(), secs, peak / 32768


class Recorder:
    def __init__(self):
        self.proc = None
        self.mix = None
        self.lock = threading.Lock()   # the live-words thread and the UI thread both read the clip
        _unlink(REC)                   # left by an app that was killed

    def start(self):
        self.stop_quietly()
        _unlink(REC)
        with self.lock:
            self.mix = Downmix()
        self.proc = subprocess.Popen(
            ["pw-record", "--rate", str(RATE), "--channels", str(CHANNELS), "--format", "s16", REC],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=_die_with_app if _prctl else None)

    def stop_quietly(self):
        if self.proc:
            self.proc.send_signal(signal.SIGINT)
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None

    def cancel(self):
        """Stop and throw the clip away."""
        self.stop_quietly()
        with self.lock:
            self.mix = None
        _unlink(REC)

    def update(self):
        """Convert what has been recorded since the last call; cheap enough to call every second."""
        with self.lock:
            if self.proc and self.mix:
                self.mix.feed(REC)

    def finish(self):
        """Stop and return (wav_bytes, seconds, peak 0..1), or (None, seconds, peak) if too short or silent.
        Only the part not yet converted is read now."""
        self.stop_quietly()
        with self.lock:
            mix, self.mix = self.mix, None
        if mix is None:
            return None, 0.0, 0.0
        mix.feed(REC, final=True)
        _unlink(REC)
        return mix.wav()

    def so_far(self):
        """The clip recorded up to now, as finish() would return it, while recording goes on."""
        with self.lock:
            if not self.proc or not self.mix:
                return None, 0.0, 0.0
            self.mix.feed(REC)
            return self.mix.wav()


class Player:
    """Clips play one at a time, in the order they arrive. stop() cuts the current one and drops the rest."""

    def __init__(self):
        self.q = queue.Queue()
        self.proc = None
        self.n = 0
        threading.Thread(target=self._run, daemon=True).start()

    def play_b64(self, b64):
        if b64:
            self.q.put(b64)

    def stop(self):
        while True:
            try:
                self.q.get_nowait()
            except queue.Empty:
                break
        p = self.proc
        if p:
            p.terminate()

    def busy(self):
        return self.proc is not None or not self.q.empty()

    def _run(self):
        while True:
            b64 = self.q.get()
            self.n += 1
            path = os.path.join(RUN, f"hvc-play-{self.n % 4}.wav")
            try:
                with open(path, "wb") as f:
                    f.write(base64.b64decode(b64))
                self.proc = subprocess.Popen(["pw-play", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.proc.wait()
            except (OSError, ValueError):
                pass
            finally:
                self.proc = None
                try:
                    os.unlink(path)
                except OSError:
                    pass
