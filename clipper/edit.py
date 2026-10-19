"""Post-processing for finished clips."""

import subprocess
from pathlib import Path


def build_vertical_command(src, dst, focus=0.5, height=1920):
    """ffmpeg command that crops a 9:16 slice out of `src` and scales it to
    1080x1920. `focus` picks the slice: 0 = left edge, 0.5 = centre, 1 = right."""
    focus = min(1.0, max(0.0, float(focus)))
    width = height * 9 // 16
    vf = (f"crop=w='min(iw,ih*9/16)':h='min(ih,iw*16/9)':x='(iw-ow)*{focus}':y='(ih-oh)/2',"
          f"scale={width}:{height},setsar=1")
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
            "-map", "0:v:0", "-map", "0:a:0?", "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(dst)]


def make_vertical(src, focus=0.5, dst=None) -> Path:
    """Write a 9:16 copy of `src` next to it (name_9x16.mp4) and return its path."""
    src = Path(src)
    dst = Path(dst) if dst else src.with_name(f"{src.stem}_9x16.mp4")
    proc = subprocess.run(build_vertical_command(src, dst, focus), capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr.strip()[-2000:]}")
    return dst
