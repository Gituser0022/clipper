"""Auto-clipping for live streams.

An AutoClipper watches a LiveRecorder for two kinds of moments:

- loud: a buffer segment is much louder than the recent median (shouting,
  cheering, a big reaction);
- chat: chat suddenly gets much busier than usual (YouTube and Twitch).

Each moment saves `before` seconds before it and `after` seconds after it.
Moments that overlap a clip still waiting to be saved extend that clip instead
of producing a duplicate.
"""

import array
import math
import random
import socket
import ssl
import statistics
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from urllib.parse import urlparse

from .ytdlp import base_args


# -- detectors (pure logic, fed with numbers) ----------------------------------

class LoudnessTrigger:
    """Fires when a segment is `delta_db` louder than the median of the last
    `history` seconds."""

    def __init__(self, delta_db=8.0, history=120.0, min_history=20.0, floor_db=-50.0,
                 refractory=15.0):
        self.delta_db = delta_db
        self.history_s = history
        self.min_history = min_history
        self.floor_db = floor_db
        self.refractory = refractory
        self.history = deque()  # (stream time, dB)
        self.last_fire = -math.inf

    def feed(self, t, db):
        """`t` is the segment's stream time, `db` its loudness. True on a spike."""
        if db is None:
            return False
        past = [d for pt, d in self.history if pt < t]
        span = t - self.history[0][0] if self.history else 0.0
        fire = (span >= self.min_history and len(past) >= 3
                and db >= statistics.median(past) + self.delta_db
                and db >= self.floor_db
                and t - self.last_fire >= self.refractory)
        self.history.append((t, db))
        while self.history and self.history[0][0] < t - self.history_s:
            self.history.popleft()
        if fire:
            self.last_fire = t
        return fire


class ChatTrigger:
    """Fires when the last `window` seconds hold `factor` times more messages
    than the usual rate over the last `baseline` seconds, and at least
    `min_count`."""

    def __init__(self, window=10.0, baseline=120.0, factor=3.0, min_count=8,
                 min_history=30.0, refractory=15.0):
        self.window = window
        self.baseline = baseline
        self.factor = factor
        self.min_count = min_count
        self.min_history = min_history
        self.refractory = refractory
        self.times = deque()
        self.started = None
        self.last_fire = -math.inf
        self.rate = 0  # messages in the last window, for the UI

    def add(self, t):
        self.times.append(t)

    def check(self, now):
        if self.started is None:
            self.started = now
        while self.times and self.times[0] < now - self.baseline:
            self.times.popleft()
        recent = sum(1 for t in self.times if t > now - self.window)
        self.rate = recent
        older_span = min(self.baseline, now - self.started) - self.window
        if older_span < self.min_history - self.window:
            return False
        older = len(self.times) - recent
        usual = older / older_span * self.window if older_span > 0 else 0.0
        fire = (recent >= max(self.min_count, self.factor * usual)
                and now - self.last_fire >= self.refractory)
        if fire:
            self.last_fire = now
        return fire


class ClipPlanner:
    """Turns trigger times into clip windows [t - before, t + after]. A trigger
    that overlaps the pending window extends it, up to `max_len` seconds."""

    def __init__(self, before=60.0, after=60.0, max_len=300.0):
        self.before = before
        self.after = after
        self.max_len = max_len
        self.pending = None
        self.ready = []
        self.last_end = -math.inf  # nothing before this is re-saved

    def add(self, t, reason):
        start = max(t - self.before, self.last_end)
        end = t + self.after
        if end <= start:
            return
        p = self.pending
        if p and start <= p["end"]:
            p["end"] = min(max(p["end"], end), p["start"] + self.max_len)
            p["reasons"].append(reason)
            return
        if p:
            self._finish()
            start = max(start, self.last_end)
        self.pending = {"start": start, "end": end, "reasons": [reason]}

    def _finish(self):
        self.ready.append(self.pending)
        self.last_end = self.pending["end"]
        self.pending = None

    def due(self, now):
        """Windows whose end the recording has reached by stream time `now`."""
        if self.pending and now >= self.pending["end"]:
            self._finish()
        out, self.ready = self.ready, []
        return out


# -- measuring -------------------------------------------------------------------

def segment_loudness(path):
    """RMS loudness of a media file's first audio track in dBFS, or None if it
    has no audio."""
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
                           "-map", "0:a:0", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                          capture_output=True)
    samples = array.array("h")
    samples.frombytes(proc.stdout[: len(proc.stdout) // 2 * 2])
    if proc.returncode != 0 or not samples:
        return None
    rms = math.sqrt(sum(s * s for s in samples) / len(samples))
    return 20 * math.log10(max(rms, 1.0) / 32768)


# -- chat sources ------------------------------------------------------------------

def chat_source(url):
    """("youtube", url), ("twitch", channel) or (None, None)."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host == "twitch.tv" or host.endswith(".twitch.tv"):
        channel = parsed.path.strip("/").split("/")[0]
        return ("twitch", channel.lower()) if channel else (None, None)
    if host in ("youtu.be", "youtube.com") or host.endswith(".youtube.com"):
        return "youtube", url
    return None, None


def follow_lines(directory, pattern, stop, on_line, alive=lambda: True):
    """Call on_line for every line appended to the first file in `directory`
    matching `pattern`, until `stop` is set or `alive()` turns false."""
    pos, partial = 0, ""
    while not stop.is_set():
        files = sorted(Path(directory).glob(pattern))
        data = None
        if files:
            try:
                with files[0].open(encoding="utf-8", errors="replace") as f:
                    f.seek(pos)
                    data = f.read()
                    pos = f.tell()
            except OSError:  # not created yet, or being renamed - try again shortly
                pass
        if data:
            lines = (partial + data).split("\n")
            partial = lines.pop()
            for line in lines:
                if line.strip():
                    on_line(line)
        if not alive():
            return
        stop.wait(0.5)


def follow_youtube_chat(url, work_dir, stop, on_message, on_status, cmd=None):
    """Live chat via yt-dlp, which appends one JSON line per chat action."""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    cmd = cmd or ["yt-dlp", *base_args(), "--skip-download", "--write-subs",
                  "--sub-langs", "live_chat", "--no-part", "--quiet", "--no-warnings",
                  "-o", str(work_dir / "chat.%(ext)s"), url]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    on_status("connecting")
    seen = False

    def line(text):
        nonlocal seen
        if not seen:
            seen = True
            on_status("connected")
        if '"addChatItemAction"' in text:
            on_message()

    try:
        follow_lines(work_dir, "chat*.json*", stop, line, alive=lambda: proc.poll() is None)
        if not stop.is_set():
            err = proc.stderr.read().decode(errors="replace").strip().splitlines()
            on_status("unavailable: " + (err[-1] if err else "chat ended"))
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def follow_twitch_chat(channel, stop, on_message, on_status,
                       host="irc.chat.twitch.tv", port=6697, tls=True):
    """Anonymous read-only Twitch chat over IRC. Reconnects until stopped."""
    delay = 2
    while not stop.is_set():
        try:
            on_status("connecting")
            raw = socket.create_connection((host, port), timeout=10)
            sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host) if tls else raw
            with sock:
                nick = f"justinfan{random.randint(10000, 99999)}"
                sock.sendall(f"NICK {nick}\r\nJOIN #{channel}\r\n".encode())
                sock.settimeout(1.0)
                buf = b""
                while not stop.is_set():
                    try:
                        data = sock.recv(4096)
                    except socket.timeout:
                        continue
                    if not data:
                        raise ConnectionError("chat disconnected")
                    buf += data
                    *lines, buf = buf.split(b"\r\n")
                    for line in lines:
                        text = line.decode(errors="replace")
                        if text.startswith("PING"):
                            sock.sendall(b"PONG" + line[4:] + b"\r\n")
                        elif " JOIN #" in text:
                            on_status("connected")
                            delay = 2
                        elif " PRIVMSG #" in text:
                            on_message()
        except OSError as e:
            on_status(f"reconnecting: {e}")
            stop.wait(delay)
            delay = min(delay * 2, 60)


# -- the auto-clipper --------------------------------------------------------------

def _run_now(label, fn):
    fn()


class AutoClipper:
    def __init__(self, rec, out_dir, submit=_run_now, audio=True, chat=True,
                 before=60, after=60, vertical=False, loud_db=8.0, chat_factor=3.0,
                 chat_min=8, max_len=300, min_history=20.0, chat_cmd=None):
        self.rec = rec
        self.out_dir = Path(out_dir)
        self.submit = submit
        self.audio = audio
        self.chat = chat
        self.before = before
        self.after = after
        self.vertical = vertical
        self.chat_cmd = chat_cmd
        # The buffer has to hold a whole window, so make sure it is big enough.
        rec.buffer_seconds = max(rec.buffer_seconds, max(before + after, max_len) + 30)
        self.loud = LoudnessTrigger(delta_db=loud_db, min_history=min_history)
        self.chat_trigger = ChatTrigger(factor=chat_factor, min_count=chat_min,
                                        min_history=max(min_history, 12.0))
        self.planner = ClipPlanner(before, after, max_len)
        self.events = deque(maxlen=20)
        self.chat_status = "off" if not chat else "starting"
        self.level_db = None
        self.clips = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._plan_loop, daemon=True).start()
        if self.audio:
            threading.Thread(target=self._audio_loop, daemon=True).start()
        if self.chat:
            threading.Thread(target=self._chat_loop, daemon=True).start()
        return self

    def stop(self):
        self._stop.set()

    def _trigger(self, t, kind, detail):
        with self._lock:
            self.planner.add(t, kind)
            self.events.append({"time": time.time(), "stream_t": round(t, 1),
                                "kind": kind, "detail": detail})

    def _audio_loop(self):
        segs = self.rec.segments()
        # Only look back a little when switched on mid-stream.
        seen = {s.path.name for s in segs if s.end < (segs[-1].end - 60 if segs else 0)}
        while not self._stop.is_set():
            for seg in self.rec.segments():
                if seg.path.name in seen:
                    continue
                seen.add(seg.path.name)
                try:
                    db = segment_loudness(seg.path)
                except OSError:
                    continue
                self.level_db = None if db is None else round(db, 1)
                if self.loud.feed(seg.start, db):
                    self._trigger(seg.start, "loud", f"{db:.0f} dB")
            self._stop.wait(1.0)

    def _chat_loop(self):
        kind, target = chat_source(self.rec.url)
        if self.chat_cmd:
            kind = "youtube"

        def on_message():
            with self._lock:
                self.chat_trigger.add(time.time())

        def on_status(s):
            self.chat_status = s

        if kind == "youtube":
            follow_youtube_chat(target or self.rec.url, self.rec.buf_dir / "chat", self._stop,
                                on_message, on_status, cmd=self.chat_cmd)
        elif kind == "twitch":
            follow_twitch_chat(target, self._stop, on_message, on_status)
        else:
            self.chat_status = "not supported for this site (YouTube and Twitch only)"

    def _plan_loop(self):
        while not self._stop.is_set():
            with self._lock:
                fire = self.chat_trigger.check(time.time())
                rate = self.chat_trigger.rate
            if fire:
                self._trigger(self.rec.stream_time(), "chat", f"{rate} messages in 10s")
            ended = self.rec.status != "recording"
            with self._lock:
                windows = self.planner.due(math.inf if ended else self.rec.stream_time())
            for w in windows:
                self._save(w)
            if ended:
                return
            self._stop.wait(1.0)

    def _save(self, w):
        reasons = " + ".join(dict.fromkeys(w["reasons"]))
        label = f"auto ({reasons}) {w['end'] - w['start']:.0f}s · {self.rec.url}"

        def work():
            path = self.rec.clip_range(w["start"], w["end"], self.out_dir,
                                       name=f"auto_{time.strftime('%H%M%S')}_{self.rec.id}")
            if self.vertical:
                from .edit import make_vertical
                vertical = make_vertical(path)
                path.unlink(missing_ok=True)
                path = vertical
            self.clips += 1
            return path

        self.submit(label, work)

    def info(self):
        return {
            "audio": self.audio,
            "chat": self.chat,
            "chat_status": self.chat_status,
            "chat_rate": self.chat_trigger.rate,
            "level_db": self.level_db,
            "before": self.before,
            "after": self.after,
            "vertical": self.vertical,
            "pending": self.planner.pending is not None,
            "clips": self.clips,
            "events": list(self.events)[-5:],
        }
