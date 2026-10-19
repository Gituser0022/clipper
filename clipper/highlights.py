"""Find the loudest moments of a finished video.

The video's audio is streamed through ffmpeg, which reports its loudness once
per second. A moment is a second that is much louder than the audio around it
(a reaction, a scream, a crowd going wild), so quiet videos and loud videos
both get their own highlights.
"""

import json
import math
import statistics
import subprocess
from pathlib import Path

from .ytdlp import base_args

SILENCE_DB = -90.0
LEVEL_FILTER = ("aresample=8000,asetnsamples=n=8000:p=0,astats=metadata=1:reset=1,"
                "ametadata=mode=print:key=lavfi.astats.Overall.RMS_level:file=-")


def video_info(url, timeout=120):
    """Title, duration and whether it's live, from yt-dlp (no download)."""
    proc = subprocess.run(["yt-dlp", *base_args(), "--no-playlist", "--no-warnings",
                           "--skip-download", "-J", url],
                          capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr.strip().splitlines() or ["yt-dlp failed"])[-1])
    info = json.loads(proc.stdout)
    return {"title": info.get("title") or url, "duration": info.get("duration"),
            "is_live": bool(info.get("is_live"))}


def level_commands(source):
    """(producer, ffmpeg) commands that print one loudness line per second.
    A local file goes straight to ffmpeg; a URL is streamed through yt-dlp."""
    analyse = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-vn",
               "-ac", "1", "-af", LEVEL_FILTER, "-f", "null", "-"]
    path = source[len("file://"):] if source.startswith("file://") else source
    if Path(path).is_file():
        analyse[analyse.index("pipe:0")] = path
        return None, analyse
    producer = ["yt-dlp", *base_args(), "--no-playlist", "--no-part", "--quiet", "--no-warnings",
                "-f", "ba/b", "-o", "-", source]
    return producer, analyse


def parse_levels(lines):
    """Yield (second, dB) from ffmpeg's ametadata output. Silence is SILENCE_DB."""
    t = None
    for line in lines:
        line = line.strip()
        if line.startswith("frame:"):
            t = int(round(float(line.rsplit("pts_time:", 1)[1])))
        elif line.startswith("lavfi.astats.Overall.RMS_level=") and t is not None:
            try:
                db = float(line.split("=", 1)[1])
            except ValueError:
                db = SILENCE_DB
            yield t, max(SILENCE_DB, db) if math.isfinite(db) else SILENCE_DB


def measure_levels(source, on_level, stop=None):
    """Stream `source` (URL or file) and call on_level(second, dB) as it goes."""
    producer_cmd, analyse_cmd = level_commands(source)
    producer = None
    if producer_cmd:
        producer = subprocess.Popen(producer_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    ffmpeg = subprocess.Popen(analyse_cmd, stdin=producer.stdout if producer else subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if producer:
        producer.stdout.close()  # ffmpeg owns the pipe now
    try:
        for t, db in parse_levels(ffmpeg.stdout):
            if stop is not None and stop.is_set():
                return
            on_level(t, db)
        ffmpeg.wait()
        if producer:
            producer.wait()
        if producer and producer.returncode != 0:
            err = producer.stderr.read().decode(errors="replace").strip().splitlines()
            raise RuntimeError(err[-1] if err else "could not download the audio")
        if ffmpeg.returncode != 0:
            err = ffmpeg.stderr.read().strip().splitlines()
            raise RuntimeError(err[-1] if err else "could not read the audio")
    finally:
        for proc in (ffmpeg, producer):
            if proc and proc.poll() is None:
                proc.kill()
                proc.wait()


def _smooth(values, width):
    half = width // 2
    out, prefix = [], [0.0]
    for v in values:
        prefix.append(prefix[-1] + v)
    for i in range(len(values)):
        lo, hi = max(0, i - half), min(len(values), i + half + 1)
        out.append((prefix[hi] - prefix[lo]) / (hi - lo))
    return out


def _local_median(values, window, step=5):
    """Median of the `window` seconds around each second (sampled every `step`)."""
    half = window // 2
    samples = {}
    for i in range(0, len(values), step):
        samples[i] = statistics.median(values[max(0, i - half): i + half + 1])
    return [samples[i - i % step] for i in range(len(values))]


def find_moments(levels, count=10, before=45, after=45, smooth=3, baseline=120):
    """The `count` biggest loudness spikes, as non-overlapping clip windows.

    `levels` is dB per second (None for unknown). Each moment is
    {rank, t, start, end, score, db}: `t` is the spike second, the clip runs
    [start, end), `score` is how many dB it stands above the audio around it
    and rank 1 is the biggest. Returned in time order."""
    values = [SILENCE_DB if v is None else v for v in levels]
    if not values:
        return []
    loud = _smooth(values, smooth)
    norm = _local_median(values, baseline)
    score = [l - n for l, n in zip(loud, norm)]
    order = sorted(range(len(values)), key=lambda i: (score[i], loud[i]), reverse=True)
    gap = before + after
    picked = []
    for i in order:
        if len(picked) >= count:
            break
        if loud[i] <= SILENCE_DB + 1:
            continue
        if all(abs(i - j) >= gap for j in picked):
            picked.append(i)
    end_of_video = len(values)
    moments = [{"rank": rank, "t": i,
                "start": max(0, i - before), "end": min(end_of_video, i + after),
                "score": round(score[i], 1), "db": round(loud[i], 1)}
               for rank, i in enumerate(picked, 1)]
    return sorted(moments, key=lambda m: m["t"])
