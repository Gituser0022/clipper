"""Clip a section out of a finished video (YouTube VOD, past broadcast, etc.)."""

import subprocess
import uuid
from pathlib import Path

from .timeparse import parse_time
from .ytdlp import base_args

DEFAULT_FORMAT = "bv*[height<=1080]+ba/b[height<=1080]/b"


def build_vod_command(url, start, end, out_dir, precise=False, fmt=DEFAULT_FORMAT, name=None):
    s, e = parse_time(start), parse_time(end)
    if e <= s:
        raise ValueError("end must be after start")
    name = name or f"vod_{uuid.uuid4().hex[:8]}"
    cmd = [
        "yt-dlp",
        *base_args(),
        "--no-playlist",
        "--no-progress",
        "-f", fmt,
        "--merge-output-format", "mp4",
        "--download-sections", f"*{s}-{e}",
        "-o", str(Path(out_dir) / f"{name}.%(ext)s"),
        "--print", "after_move:filepath",
        "--no-simulate",
    ]
    if precise:
        # Re-encodes around the cut points so the clip starts exactly on time.
        cmd.append("--force-keyframes-at-cuts")
    cmd.append(url)
    return cmd


def clip_vod(url, start, end, out_dir, precise=False, fmt=DEFAULT_FORMAT) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = build_vod_command(url, start, end, out_dir, precise=precise, fmt=fmt)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {proc.stderr.strip()[-2000:]}")
    lines = [l for l in proc.stdout.splitlines() if l.strip()]
    if not lines:
        raise RuntimeError("yt-dlp did not report an output file")
    return Path(lines[-1].strip())
