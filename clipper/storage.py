"""Where Clipper keeps its files, and how much room they may take.

Everything lives in one home folder (by default the Clipper checkout, e.g.
~/clipper): Clips/, Highlights/, Downloads/ and Logs/, plus a hidden .work/
for live-stream buffers and settings.json for the storage limit.
"""

import json
import os
import shutil
from pathlib import Path

FOLDERS = {"clips": "Clips", "highlights": "Highlights", "downloads": "Downloads", "logs": "Logs"}
SAVED = ("clips", "highlights", "downloads")  # what the storage limit applies to
WORK = ".work"
SETTINGS_FILE = "settings.json"
DEFAULT_SETTINGS = {"storage_limit_gb": None, "when_full": "stop",
                    # Phone & iPad access (see remote.py)
                    "remote_enabled": False, "remote_key": None, "after_phone_save": "bin"}
MEDIA = {".mp4", ".mkv", ".webm", ".mov", ".mp3", ".m4a", ".opus", ".ogg", ".wav", ".flac"}
GB = 1024 ** 3


def default_home():
    """CLIPPER_HOME (or the older CLIPPER_DATA), else the checkout Clipper runs
    from, else ~/Clipper."""
    env = os.environ.get("CLIPPER_HOME") or os.environ.get("CLIPPER_DATA")
    if env:
        return Path(env).expanduser().resolve()
    repo = Path(__file__).resolve().parent.parent
    if (repo / "pyproject.toml").is_file():
        return repo
    return Path.home() / "Clipper"


def folder_size(path):
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.stat(os.path.join(root, name), follow_symlinks=False).st_size
            except OSError:
                pass
    return total


def media_files(folder):
    """Saved clips/downloads in a folder (not thumbnails or partial files), oldest first."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    files = [p for p in folder.iterdir()
             if p.is_file() and p.suffix.lower() in MEDIA and not p.name.startswith(".")]
    return sorted(files, key=lambda p: p.stat().st_mtime)


def load_settings(home):
    try:
        saved = json.loads((Path(home) / SETTINGS_FILE).read_text())
    except (OSError, ValueError):
        saved = {}
    return {k: saved.get(k, v) for k, v in DEFAULT_SETTINGS.items()}


def save_settings(home, settings):
    path = Path(home) / SETTINGS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(settings, indent=2))
    tmp.replace(path)


def migrate_old_data(home):
    """Move clips from the old layout (data/clips, data/work) into Clips/.
    Returns how many files moved."""
    old = Path(home) / "data"
    moved = 0
    clips = Path(home) / FOLDERS["clips"]
    if (old / "clips").is_dir():
        clips.mkdir(parents=True, exist_ok=True)
        for f in media_files(old / "clips"):
            dest = clips / f.name
            n = 1
            while dest.exists():
                dest = clips / f"{f.stem}-{n}{f.suffix}"
                n += 1
            shutil.move(str(f), dest)
            moved += 1
        shutil.rmtree(old / "clips" / ".thumbs", ignore_errors=True)
    shutil.rmtree(old / "work", ignore_errors=True)
    for d in (old / "clips", old):
        try:
            d.rmdir()  # only if empty: never delete anything we didn't move
        except OSError:
            pass
    return moved
