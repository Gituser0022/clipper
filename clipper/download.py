"""Download whole videos, or just their audio, from any site yt-dlp supports
(YouTube, X/Twitter, Instagram, TikTok, Facebook, Reddit, Vimeo, Twitch...)."""

import json
import re
import subprocess
import threading
from pathlib import Path

from .ytdlp import base_args

BROWSERS = ("chrome", "safari", "firefox", "edge", "brave")
HEIGHTS = (1080, 720, 480)
MAX_ITEMS = 50
_slots = threading.Semaphore(2)  # downloads at once; the rest wait their turn

# yt-dlp format options per choice. "compatible" prefers H.264 + AAC so the
# file plays in QuickTime, on phones and in editors.
FORMATS = {
    "compatible": ["-f", "bv*+ba/b", "-S", "vcodec:h264,res:1080,acodec:aac",
                   "--merge-output-format", "mp4"],
    "highest": ["-f", "bv*+ba/b", "-S", "res,fps", "--merge-output-format", "mp4"],
    **{f"h{h}": ["-f", "bv*+ba/b", "-S", f"res:{h},vcodec:h264,acodec:aac",
                 "--merge-output-format", "mp4"] for h in HEIGHTS},
    "audio": ["-f", "ba/b", "-x", "--audio-format", "mp3", "--audio-quality", "0"],
}
PROGRESS = "[clipper] %(progress._percent_str)s"
POSTPROCESS = "[clipper-pp] %(progress.postprocessor)s %(progress.status)s"


def cookie_args(browser):
    """Use the login saved in the user's browser (for private or login-only posts)."""
    if not browser:
        return []
    if browser not in BROWSERS:
        raise ValueError(f"unknown browser: {browser}")
    return ["--cookies-from-browser", browser]


def _first_line_error(stderr, fallback):
    lines = [l for l in stderr.strip().splitlines() if l.strip()]
    errors = [l for l in lines if l.startswith("ERROR:")]
    return (errors or lines or [fallback])[-1].removeprefix("ERROR: ").strip()


def _video_formats(info):
    return [f for f in info.get("formats") or [info] if f.get("vcodec") != "none"]


def describe(info):
    """Turn yt-dlp's JSON into what the page needs: title, entries and choices."""
    entries = None
    video = info
    if info.get("_type") == "playlist" or "entries" in info:
        items = [e for e in info.get("entries") or [] if e]
        if not items:
            raise RuntimeError("no videos found at this link")
        video = items[0]
        if len(items) > 1:
            entries = [{"index": e.get("playlist_index") or i,
                        "title": e.get("title") or f"Video {i}",
                        "duration": e.get("duration"), "thumbnail": e.get("thumbnail")}
                       for i, e in enumerate(items[:MAX_ITEMS], 1)]
    every = [video] if entries is None else [e for e in info.get("entries") or [] if e]
    heights = {f.get("height") for v in every for f in _video_formats(v) if f.get("height")}
    has_video = any(_video_formats(v) for v in every)
    choices = []
    if has_video:
        choices.append({"id": "compatible", "label": "Best for Mac", "kind": "video",
                        "note": "Plays everywhere: QuickTime, phones, editors. Up to 1080p.",
                        "recommended": True})
        if not heights or max(heights) > 1080:
            choices.append({"id": "highest", "label": "Highest quality", "kind": "video",
                            "note": "Up to 4K if the site has it. Some files need VLC to play.",
                            "recommended": False})
        for h in HEIGHTS:
            if heights and max(heights) >= h:
                choices.append({"id": f"h{h}", "label": f"{h}p", "kind": "video",
                                "note": "Smaller file" if h < 1080 else "Full HD",
                                "recommended": False})
    choices.append({"id": "audio", "label": "Audio only (MP3)", "kind": "audio",
                    "note": "Just the sound, as an MP3 file.", "recommended": not has_video})
    return {
        "title": info.get("title") or video.get("title") or info.get("webpage_url"),
        "site": info.get("extractor_key") or video.get("extractor_key") or info.get("extractor"),
        "thumbnail": info.get("thumbnail") or video.get("thumbnail"),
        "duration": None if entries else video.get("duration"),
        "uploader": info.get("uploader") or video.get("uploader"),
        "is_live": bool(video.get("is_live")),
        "entries": entries,
        "choices": choices,
    }


def inspect_link(url, browser=None, timeout=60):
    """What's at this link, without downloading it."""
    proc = subprocess.run(["yt-dlp", *base_args(), *cookie_args(browser), "--no-warnings",
                           "--no-playlist", "--playlist-items", f"1-{MAX_ITEMS}", "-J", url],
                          capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(_first_line_error(proc.stderr, "could not read this link"))
    return describe(json.loads(proc.stdout))


def build_download_command(url, choice, out_dir, item=None, browser=None):
    if choice not in FORMATS:
        raise ValueError(f"unknown choice: {choice}")
    cmd = ["yt-dlp", *base_args(), *cookie_args(browser), "--no-warnings", "--no-playlist",
           *FORMATS[choice], "--restrict-filenames",
           "-o", str(Path(out_dir) / "%(title).70B-%(id)s.%(ext)s"),
           "--newline", "--progress",
           "--progress-template", f"download:{PROGRESS}",
           "--progress-template", f"postprocess:{POSTPROCESS}",
           "--print", "after_move:filepath", "--no-simulate"]
    if item is not None:
        cmd += ["--playlist-items", str(int(item))]
    return cmd + [url]


def download(url, choice, out_dir, item=None, browser=None, report=lambda **kw: None):
    """Download one video (or one item of a multi-video post) and return its path.
    `report(progress=, stage=)` is called as it goes."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = build_download_command(url, choice, out_dir, item, browser)
    report(stage="Waiting for another download")
    with _slots:
        report(progress=0.0, stage="Starting")
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        part, last_pct, path, log = 1, 0.0, None, []
        for line in proc.stdout:
            line = line.strip()
            m = re.match(r"\[clipper\]\s+([\d.]+)%", line)
            if m:
                pct = float(m.group(1)) / 100
                if pct + 0.2 < last_pct:  # a second stream started (the audio)
                    part += 1
                last_pct = pct
                stage = "Downloading audio" if choice == "audio" or part > 1 else "Downloading video"
                report(progress=pct, stage=stage)
            elif line.startswith("[clipper-pp]"):
                report(stage="Converting to MP3" if "ExtractAudio" in line else "Finishing up")
            elif line and Path(line).is_file():
                path = Path(line)
            elif line:
                log.append(line)
        proc.wait()
    if proc.returncode != 0 or path is None:
        raise RuntimeError(_first_line_error("\n".join(log), "download failed"))
    report(progress=1.0, stage="Done")
    return path
