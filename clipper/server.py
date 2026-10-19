"""FastAPI web app: clip VODs and live streams from the browser."""

import os
import re
import shutil
import signal
import sys
import subprocess
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from typing import Annotated, Literal
from urllib.parse import parse_qs, unquote, urlparse

from pydantic import BaseModel, Field, StringConstraints

from . import storage
from .auto import AutoClipper
from .edit import make_vertical
from .live import LiveRecorder
from .vod import clip_vod
from .ytdlp import base_args

HOME = storage.default_home()
CLIPS_DIR = HOME / storage.FOLDERS["clips"]
HIGHLIGHTS_DIR = HOME / storage.FOLDERS["highlights"]
DOWNLOADS_DIR = HOME / storage.FOLDERS["downloads"]
LOGS_DIR = HOME / storage.FOLDERS["logs"]
WORK_DIR = HOME / storage.WORK
STATIC_DIR = Path(__file__).parent / "static"

recorders: dict[str, LiveRecorder] = {}
autoclippers: dict[str, AutoClipper] = {}

# Rejects blank or non-link input before it reaches yt-dlp.
Url = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\w[\w+.-]*://\S+$")]


@asynccontextmanager
async def lifespan(app):
    storage.migrate_old_data(HOME)
    for folder in _saved_dirs().values():
        folder.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    # Buffers left behind by a crash or a forced quit are of no use any more.
    for stale in WORK_DIR.glob("buffer_*"):
        shutil.rmtree(stale, ignore_errors=True)
    _load_saved_files()
    yield
    for auto in autoclippers.values():
        auto.stop()
    for rec in recorders.values():
        rec.discard()


app = FastAPI(title="Clipper", lifespan=lifespan)
jobs: dict[str, dict] = {}


def _run_job(kind, label, fn, reports=False):
    """Run fn in the background as a job the UI can follow. With reports=True,
    fn is called as fn(report), where report(progress=0..1, stage="...")
    updates the job while it runs."""
    job_id = uuid.uuid4().hex[:8]
    job = {"id": job_id, "kind": kind, "label": label, "status": "running",
           "created": time.time(), "file": None, "error": None, "duration": None,
           "progress": None, "stage": None, "folder": None}
    jobs[job_id] = job

    def report(progress=None, stage=None):
        if progress is not None:
            job["progress"] = round(min(1.0, max(0.0, progress)), 3)
        if stage is not None:
            job["stage"] = stage

    def work():
        try:
            _check_room()
            path = Path(fn(report) if reports else fn())
            job["file"] = path.name
            job["folder"] = _folder_of(path)
            job["duration"] = _media_duration(path)
            job["status"] = "done"
            # One card per file: a re-download replaces the older card for it.
            for other_id, other in list(jobs.items()):
                if (other_id != job_id and other["status"] == "done"
                        and (other["folder"], other["file"]) == (job["folder"], job["file"])):
                    jobs.pop(other_id, None)
            _make_room(keep=path)
        except Exception as e:  # surfaced to the UI
            job["status"] = "error"
            job["error"] = str(e)
        finally:
            _storage_cache.clear()

    threading.Thread(target=work, daemon=True).start()
    return job


def _vertical_after(fn, vertical, focus=0.5):
    """Wrap a clip job so it ends with a 9:16 copy instead of the original."""
    if not vertical:
        return fn

    def work():
        path = Path(fn())
        out = make_vertical(path, focus)
        path.unlink(missing_ok=True)
        return out
    return work


class VodClipRequest(BaseModel):
    url: Url
    start: str
    end: str
    precise: bool = False
    vertical: bool = False


class AutoRequest(BaseModel):
    audio: bool = True
    chat: bool = True
    before: float = Field(60, ge=5, le=600)
    after: float = Field(60, ge=5, le=600)
    loud_db: float = Field(8, ge=3, le=30)
    vertical: bool = False


class LiveStartRequest(BaseModel):
    url: Url
    buffer_seconds: int = Field(300, ge=10, le=3 * 3600)
    auto: AutoRequest | None = None


class LiveClipRequest(BaseModel):
    seconds_before: float = Field(30, gt=0, le=3 * 3600)
    seconds_after: float = Field(0, ge=0, le=600)
    vertical: bool = False


class VerticalRequest(BaseModel):
    focus: float = Field(0.5, ge=0, le=1)


@app.post("/api/vod/clip")
def vod_clip(req: VodClipRequest):
    from .timeparse import parse_time
    try:
        if parse_time(req.end) <= parse_time(req.start):
            raise ValueError("end must be after start")
    except ValueError as e:
        raise HTTPException(400, str(e))
    label = f"{req.start}–{req.end}{' · 9:16' if req.vertical else ''} · {req.url}"
    fn = lambda: clip_vod(req.url, req.start, req.end, CLIPS_DIR, precise=req.precise)
    return _run_job("vod", label, _vertical_after(fn, req.vertical))


def youtube_id(url):
    """The video id of a YouTube link, or None."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host == "youtu.be":
        vid = parsed.path.strip("/").split("/")[0]
    elif host == "youtube.com" or host.endswith(".youtube.com"):
        vid = parse_qs(parsed.query).get("v", [""])[0]
        m = re.match(r"^/(?:live|shorts|embed)/([^/?#]+)", parsed.path)
        if not vid and m:
            vid = m.group(1)
    else:
        return None
    return vid if re.fullmatch(r"[\w-]{6,20}", vid or "") else None


@app.get("/api/preview")
def preview(url: str):
    """How the page can play a link: a YouTube embed, or a direct media URL."""
    vid = youtube_id(url)
    if vid:
        return {"kind": "youtube", "id": vid}
    try:
        proc = subprocess.run(["yt-dlp", *base_args(), "--no-playlist", "--no-warnings", "-g",
                               "-f", "b[ext=mp4]/b", url],
                              capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "timed out looking up the video")
    lines = [l for l in proc.stdout.splitlines() if l.strip()]
    if proc.returncode != 0 or not lines:
        raise HTTPException(400, (proc.stderr.strip().splitlines() or ["no playable video found"])[-1])
    return {"kind": "direct", "url": lines[0]}


def _start_auto(rec, req: AutoRequest):
    old = autoclippers.pop(rec.id, None)
    if old:
        old.stop()
    autoclippers[rec.id] = AutoClipper(
        rec, CLIPS_DIR, submit=lambda label, fn: _run_job("auto", label, fn),
        audio=req.audio, chat=req.chat, before=req.before, after=req.after,
        loud_db=req.loud_db, vertical=req.vertical).start()


def _info(rec):
    info = rec.info()
    auto = autoclippers.get(rec.id)
    info["auto"] = auto.info() if auto else None
    info["youtube_id"] = youtube_id(rec.url)
    return info


@app.post("/api/live/start")
def live_start(req: LiveStartRequest):
    rec = LiveRecorder(req.url, WORK_DIR, buffer_seconds=req.buffer_seconds).start()
    recorders[rec.id] = rec
    if req.auto and (req.auto.audio or req.auto.chat):
        _start_auto(rec, req.auto)
    return _info(rec)


@app.get("/api/live")
def live_list():
    return [_info(r) for r in recorders.values()]


def _recorder(rec_id):
    rec = recorders.get(rec_id)
    if not rec:
        raise HTTPException(404, "unknown recorder")
    return rec


@app.post("/api/live/{rec_id}/clip")
def live_clip(rec_id: str, req: LiveClipRequest):
    rec = _recorder(rec_id)
    label = f"last {req.seconds_before:g}s" + (f" + next {req.seconds_after:g}s" if req.seconds_after else "")
    label += " · 9:16" if req.vertical else ""
    fn = lambda: rec.clip(req.seconds_before, req.seconds_after, CLIPS_DIR)
    return _run_job("live", f"{label} · {rec.url}", _vertical_after(fn, req.vertical))


@app.post("/api/live/{rec_id}/auto")
def live_auto(rec_id: str, req: AutoRequest):
    rec = _recorder(rec_id)
    if req.audio or req.chat:
        _start_auto(rec, req)
    else:
        auto = autoclippers.pop(rec_id, None)
        if auto:
            auto.stop()
    return _info(rec)


@app.post("/api/live/{rec_id}/stop")
def live_stop(rec_id: str):
    rec = _recorder(rec_id)
    rec.stop()
    return _info(rec)


@app.delete("/api/live/{rec_id}")
def live_remove(rec_id: str):
    auto = autoclippers.pop(rec_id, None)
    if auto:
        auto.stop()
    rec = recorders.pop(rec_id, None)
    if rec:
        rec.discard()
    return {"ok": True}


def _clip_path(name):
    """A saved file by name, from Clips, Highlights or Downloads."""
    for folder in _saved_dirs().values():
        path = (folder / name).resolve()
        if path.parent == folder.resolve() and path.is_file():
            return path
    raise HTTPException(404)


@app.post("/api/clips/{name}/vertical")
def clip_vertical(name: str, req: VerticalRequest):
    path = _clip_path(name)
    return _run_job("edit", f"9:16 of {name}", lambda: make_vertical(path, req.focus))


def _shutdown():
    os.kill(os.getpid(), signal.SIGINT)


@app.post("/api/quit")
def quit_app():
    """Stop Clipper (used by the Quit button, since the Mac app has no window)."""
    threading.Timer(0.3, _shutdown).start()
    return {"ok": True}


@app.get("/api/jobs")
def job_list():
    return sorted(jobs.values(), key=lambda j: j["created"], reverse=True)


@app.get("/api/state")
def state():
    """Everything the page polls for, in one request: recorders and jobs."""
    return {"live": live_list(), "jobs": job_list(), "storage": storage_info()}


# -- storage: one folder for everything, with a size limit ------------------------------

_storage_cache: dict = {}


def _saved_dirs():
    return {"clips": CLIPS_DIR, "highlights": HIGHLIGHTS_DIR, "downloads": DOWNLOADS_DIR}


def _folder_of(path):
    parent = Path(path).resolve().parent
    return next((k for k, d in _saved_dirs().items() if d.resolve() == parent), None)


def _guess_kind(folder, name):
    if folder == "highlights":
        return "highlight"
    if folder == "downloads":
        return "download"
    return next((k for k in ("live", "auto", "vod") if name.startswith(k + "_")), "vod")


def _load_saved_files():
    """Show files already in the folders in My clips (jobs only live in memory)."""
    known = {(j["folder"], j["file"]) for j in jobs.values()}
    for folder, d in _saved_dirs().items():
        for path in storage.media_files(d):
            if (folder, path.name) in known:
                continue
            job_id = uuid.uuid4().hex[:8]
            jobs[job_id] = {"id": job_id, "kind": _guess_kind(folder, path.name),
                            "label": path.stem.replace("_", " "), "status": "done",
                            "created": path.stat().st_mtime, "file": path.name, "error": None,
                            "duration": None, "progress": None, "stage": None, "folder": folder}
    # Durations need ffprobe per file, so fill them in without holding up startup.
    def fill():
        for j in list(jobs.values()):
            if j["status"] == "done" and j["duration"] is None and j["folder"]:
                path = _saved_dirs()[j["folder"]] / j["file"]
                if path.is_file():
                    j["duration"] = _media_duration(path)
    threading.Thread(target=fill, daemon=True).start()


def _settings():
    return storage.load_settings(HOME)


def storage_info(fresh=False):
    """Usage, limit and folders. Cached for 5s because the page polls it."""
    if not fresh and _storage_cache.get("at", 0) > time.time() - 5:
        return _storage_cache["info"]
    used = {k: storage.folder_size(d) for k, d in _saved_dirs().items()}
    used["total"] = sum(used.values())
    used["work"] = storage.folder_size(WORK_DIR)
    used["logs"] = storage.folder_size(LOGS_DIR)
    settings = _settings()
    limit = settings["storage_limit_gb"]
    limit_bytes = int(limit * storage.GB) if limit else None
    try:
        free = shutil.disk_usage(HOME if HOME.exists() else HOME.parent).free
    except OSError:
        free = None
    info = {"folder": str(HOME),
            "folders": {**{k: str(d) for k, d in _saved_dirs().items()}, "logs": str(LOGS_DIR)},
            "used": used, "limit_bytes": limit_bytes, "when_full": settings["when_full"],
            "disk_free_bytes": free,
            "full": bool(limit_bytes and used["total"] >= limit_bytes),
            "percent": round(min(1.0, used["total"] / limit_bytes), 4) if limit_bytes else None}
    _storage_cache.update(at=time.time(), info=info)
    return info


def _gb(n):
    return f"{n / storage.GB:.1f} GB"


def _check_room():
    """Refuse new files when the limit is reached and the user chose 'stop'."""
    info = storage_info(fresh=True)
    if info["full"] and info["when_full"] == "stop":
        raise RuntimeError(f"Storage limit reached: {_gb(info['used']['total'])} of "
                           f"{_gb(info['limit_bytes'])} used. Delete some clips or raise "
                           f"the limit in Settings.")


def _delete_file(path):
    path.unlink(missing_ok=True)
    (path.parent / THUMBS / f"{path.name}.jpg").unlink(missing_ok=True)
    folder = _folder_of(path)
    for job_id, j in list(jobs.items()):
        if j["file"] == path.name and j["folder"] in (folder, None):
            jobs.pop(job_id, None)


def _make_room(keep=None):
    """With 'delete_oldest', delete the oldest saved files until under the limit."""
    info = storage_info(fresh=True)
    if not info["limit_bytes"] or info["when_full"] != "delete_oldest":
        return
    total = info["used"]["total"]
    oldest = sorted((p for d in _saved_dirs().values() for p in storage.media_files(d)),
                    key=lambda p: p.stat().st_mtime)
    for path in oldest:
        if total < info["limit_bytes"]:
            break
        if keep is not None and path.resolve() == Path(keep).resolve():
            continue
        total -= path.stat().st_size
        _delete_file(path)


class SettingsRequest(BaseModel):
    storage_limit_gb: float | None = Field(None, ge=1, le=5000)
    when_full: Literal["stop", "delete_oldest"] = "stop"


@app.get("/api/storage")
def get_storage():
    return storage_info(fresh=True)


@app.put("/api/settings")
def put_settings(req: SettingsRequest):
    HOME.mkdir(parents=True, exist_ok=True)
    storage.save_settings(HOME, req.model_dump())
    _make_room()
    return storage_info(fresh=True)


def _open_in_finder(path):
    subprocess.Popen(["open", str(path)])


class OpenRequest(BaseModel):
    folder: Literal["clips", "highlights", "downloads", "logs"] | None = None


@app.post("/api/storage/open")
def open_folder(req: OpenRequest):
    if sys.platform != "darwin":
        raise HTTPException(400, "Opening folders only works on a Mac")
    path = {**_saved_dirs(), "logs": LOGS_DIR}.get(req.folder, HOME)
    path.mkdir(parents=True, exist_ok=True)
    _open_in_finder(path)
    return {"ok": True}


@app.delete("/api/clips/{name}")
def delete_clip(name: str):
    _delete_file(_clip_path(name))
    return storage_info(fresh=True)


@app.get("/clips/{name}")
def get_clip(name: str):
    return FileResponse(_clip_path(name))


# -- helpers for the web UI: link inspection and clip thumbnails ----------------------

def _media_duration(path):
    """Length of a media file in seconds, or None if ffprobe can't tell."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=nw=1:nk=1", str(path)],
                             capture_output=True, text=True, timeout=15).stdout
        return round(float(out.strip()), 2)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


# twitch.tv/<name> paths that are not channels.
_TWITCH_NOT_CHANNELS = {"videos", "directory", "downloads", "settings", "search", "p", "jobs",
                        "turbo", "subscriptions", "inventory", "wallet", "drops", "friends"}


def _inspect_fast(url):
    """Live links we can recognise without asking yt-dlp, else None."""
    parsed = urlparse(url)
    if parsed.scheme == "file":  # a local video (also what the highlights tests use)
        path = Path(unquote(parsed.path))
        return {"is_live": False, "title": path.name, "thumbnail": None, "youtube_id": None,
                "duration": _media_duration(path) if path.is_file() else None}
    host = (parsed.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    parts = [p for p in parsed.path.split("/") if p]
    if host in ("youtube.com", "youtu.be") or host.endswith(".youtube.com"):
        if parts and (parts[-1] == "live" or (len(parts) == 2 and parts[0] == "live")):
            return {"is_live": True, "title": None, "duration": None, "thumbnail": None,
                    "youtube_id": youtube_id(url)}
    if host == "twitch.tv" and len(parts) == 1 and parts[0].lower() not in _TWITCH_NOT_CHANNELS:
        return {"is_live": True, "title": parts[0], "duration": None, "thumbnail": None,
                "youtube_id": None}
    return None


@app.get("/api/inspect")
def inspect(url: Url):
    """What a link points at, so the page can pick video or live mode."""
    fast = _inspect_fast(url)
    if fast:
        return fast
    import json
    try:
        proc = subprocess.run(["yt-dlp", *base_args(), "--dump-single-json", "--skip-download",
                               "--no-playlist", "--no-warnings", url],
                              capture_output=True, text=True, timeout=45)
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "timed out looking up the link")
    try:
        info = json.loads(proc.stdout) if proc.returncode == 0 else None
    except ValueError:
        info = None
    if not isinstance(info, dict):
        raise HTTPException(400, (proc.stderr.strip().splitlines() or ["couldn't read that link"])[-1])
    vid = youtube_id(url)
    if not vid and info.get("extractor_key") == "Youtube":
        vid = info.get("id")
    return {"is_live": bool(info.get("is_live")) or info.get("live_status") == "is_live",
            "title": info.get("title"), "duration": info.get("duration"),
            "thumbnail": info.get("thumbnail"), "youtube_id": vid}


THUMBS = ".thumbs"


@app.get("/clips/{name}/thumb.jpg")
def clip_thumb(name: str):
    """One frame of a clip (cached in clips/.thumbs) for the gallery."""
    path = _clip_path(name)
    thumb = path.parent / THUMBS / f"{path.name}.jpg"
    if not thumb.is_file() or thumb.stat().st_mtime < path.stat().st_mtime:
        thumb.parent.mkdir(exist_ok=True)
        length = _media_duration(path) or 0
        # About 10% in (at least 1s), then ffmpeg's `thumbnail` filter picks the most
        # typical of the next frames, which skips black or blurry ones.
        at = max(1.0, length * 0.1) if length > 3 else length / 2
        tmp = thumb.with_name(f"{thumb.name}.{uuid.uuid4().hex[:6]}.jpg")
        try:
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{at:.2f}", "-i", str(path),
                            "-frames:v", "1", "-vf", "thumbnail=30,scale='min(640,iw)':-2", "-q:v", "4", str(tmp)],
                           capture_output=True, timeout=30, check=True)
            tmp.replace(thumb)
        except (OSError, subprocess.SubprocessError):
            tmp.unlink(missing_ok=True)
            raise HTTPException(404, "no thumbnail")
    return FileResponse(thumb, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})


from .highlights_api import router as highlights_router  # noqa: E402 (needs app's helpers)

app.include_router(highlights_router)
from .download_api import router as download_router  # noqa: E402

app.include_router(download_router)
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
