"""Real-time clipping of live streams.

A LiveRecorder pipes the live stream (via yt-dlp) into ffmpeg, which writes
short MPEG-TS segments into a rolling buffer. Clipping "the last N seconds"
just stitches the newest segments together, so it is near-instant and needs
no re-encoding.
"""

import csv
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from .ytdlp import base_args

# A single combined stream if there is one; otherwise H.264 video + AAC audio,
# which yt-dlp merges on the fly (these codecs fit in the MPEG-TS buffer).
LIVE_FORMAT = "b/bv*[vcodec^=avc1][height<=1080]+ba[acodec^=mp4a]/bv*+ba"


@dataclass
class Segment:
    path: Path
    start: float
    end: float


def select_segments(segments, seconds):
    """Return the newest segments covering at least `seconds`, plus the offset
    into the first one where the clip should start."""
    if not segments:
        return [], 0.0
    return select_range(segments, segments[-1].end - seconds, segments[-1].end)


def select_range(segments, start, end):
    """Return the segments overlapping stream time [start, end), plus the offset
    into the first one where the clip should start. `start` is clamped to what
    is still buffered."""
    start = max(segments[0].start, start) if segments else start
    chosen = [s for s in segments if s.end > start and s.start < end]
    if not chosen:
        return [], 0.0
    return chosen, max(0.0, start - chosen[0].start)


class LiveRecorder:
    def __init__(self, url, work_dir, buffer_seconds=300, segment_seconds=2,
                 fmt=LIVE_FORMAT, producer_cmd=None):
        self.id = uuid.uuid4().hex[:8]
        self.url = url
        self.buffer_seconds = buffer_seconds
        self.segment_seconds = segment_seconds
        self.fmt = fmt
        self.producer_cmd = producer_cmd
        self.buf_dir = Path(work_dir) / f"buffer_{self.id}"
        self.list_file = self.buf_dir / "segments.csv"
        self.started_at = None
        self.status = "idle"
        self.error = None
        self._producer = None
        self._consumer = None
        self._stderr_tail = deque(maxlen=40)
        self._lock = threading.Lock()
        self._stop = threading.Event()

    # -- process management -------------------------------------------------

    def _producer_command(self):
        if self.producer_cmd:
            return self.producer_cmd
        return ["yt-dlp", *base_args(), "--no-playlist", "--no-part", "--quiet", "--no-warnings",
                "-f", self.fmt, "-o", "-", self.url]

    def _consumer_command(self):
        return [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", "pipe:0",
            "-map", "0:v:0?", "-map", "0:a:0?",
            "-c", "copy",
            "-f", "segment",
            "-segment_time", str(self.segment_seconds),
            "-segment_format", "mpegts",
            "-segment_list", str(self.list_file),
            "-segment_list_type", "csv",
            "-segment_list_flags", "+live",
            str(self.buf_dir / "seg_%08d.ts"),
        ]

    def start(self):
        self.buf_dir.mkdir(parents=True, exist_ok=True)
        self._producer = subprocess.Popen(self._producer_command(), stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE)
        self._consumer = subprocess.Popen(self._consumer_command(), stdin=self._producer.stdout,
                                          stderr=subprocess.PIPE)
        self._producer.stdout.close()  # consumer owns the pipe now
        self.started_at = time.time()
        self.status = "recording"
        for proc in (self._producer, self._consumer):
            threading.Thread(target=self._drain, args=(proc.stderr,), daemon=True).start()
        threading.Thread(target=self._watch, daemon=True).start()
        return self

    def _drain(self, stream):
        for line in iter(stream.readline, b""):
            self._stderr_tail.append(line.decode(errors="replace").rstrip())

    def _watch(self):
        while not self._stop.is_set():
            self._prune()
            if self._consumer.poll() is not None:
                if not self._stop.is_set():
                    code = self._consumer.returncode
                    self.status = "ended" if code == 0 else "error"
                    if code != 0 or not self.segments():
                        self.error = "\n".join(self._stderr_tail) or f"ffmpeg exited with {code}"
                        self.status = "error"
                return
            self._stop.wait(1.0)

    def stop(self):
        self._stop.set()
        for proc in (self._producer, self._consumer):
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        if self.status == "recording":
            self.status = "stopped"

    # -- buffer ---------------------------------------------------------------

    def segments(self):
        """Completed segments still on disk, oldest first."""
        if not self.list_file.exists():
            return []
        out = []
        with self.list_file.open(newline="") as f:
            for row in csv.reader(f):
                if len(row) < 3:
                    continue
                path = self.buf_dir / row[0]
                if path.exists():
                    out.append(Segment(path, float(row[1]), float(row[2])))
        return out

    def buffered_seconds(self):
        segs = self.segments()
        return segs[-1].end - segs[0].start if segs else 0.0

    def _prune(self):
        segs = self.segments()
        if not segs:
            return
        cutoff = segs[-1].end - self.buffer_seconds
        with self._lock:
            for s in segs:
                if s.end < cutoff:
                    s.path.unlink(missing_ok=True)

    # -- clipping -------------------------------------------------------------

    def clip(self, seconds_before=30, seconds_after=0, out_dir=".", name=None) -> Path:
        """Save the last `seconds_before` seconds. If `seconds_after` > 0, first
        keep recording that long so the clip also includes what happens next."""
        if seconds_after > 0:
            time.sleep(seconds_after)
        segs = self.segments()
        if not segs:
            raise RuntimeError("nothing buffered yet - wait a few seconds")
        end = segs[-1].end
        return self.clip_range(end - seconds_before - seconds_after, end, out_dir, name)

    def stream_time(self):
        """Stream time of the live edge (end of the newest finished segment)."""
        segs = self.segments()
        return segs[-1].end if segs else 0.0

    def clip_range(self, start, end, out_dir=".", name=None) -> Path:
        """Save stream time [start, end) from the buffer."""
        with self._lock:
            chosen, offset = select_range(self.segments(), start, end)
            if not chosen:
                raise RuntimeError("nothing buffered yet - wait a few seconds")
            duration = min(end, chosen[-1].end) - max(start, chosen[0].start)
            out_dir = Path(out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            out = out_dir / f"{name or 'live_' + uuid.uuid4().hex[:8]}.mp4"
            concat = "concat:" + "|".join(str(s.path) for s in chosen)
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                   "-ss", f"{offset:.3f}", "-i", concat, "-t", f"{duration:.3f}",
                   "-map", "0:v:0?", "-map", "0:a:0?",
                   "-c", "copy", "-movflags", "+faststart", str(out)]
            proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {proc.stderr.strip()[-2000:]}")
        return out

    def info(self):
        return {
            "id": self.id,
            "url": self.url,
            "status": self.status,
            "error": self.error,
            "buffer_seconds": self.buffer_seconds,
            "buffered": round(self.buffered_seconds(), 1),
            "started_at": self.started_at,
        }
