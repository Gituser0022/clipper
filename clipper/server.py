"""FastAPI web app: clip VODs and live streams from the browser."""

import os
import re
import signal
import subprocess
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from typing import Annotated
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel, Field, StringConstraints

from .auto import AutoClipper
from .edit import make_vertical
from .live import LiveRecorder
from .vod import clip_vod
from .ytdlp import base_args

DATA_DIR = Path(os.environ.get("CLIPPER_DATA", "data")).resolve()
CLIPS_DIR = DATA_DIR / "clips"
WORK_DIR = DATA_DIR / "work"
STATIC_DIR = Path(__file__).parent / "static"

recorders: dict[str, LiveRecorder] = {}
autoclippers: dict[str, AutoClipper] = {}

# Rejects blank or non-link input before it reaches yt-dlp.
Url = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\w[\w+.-]*://\S+$")]


@asynccontextmanager
async def lifespan(app):
    yield
    for auto in autoclippers.values():
        auto.stop()
    for rec in recorders.values():
        rec.stop()


app = FastAPI(title="Clipper", lifespan=lifespan)
jobs: dict[str, dict] = {}


def _run_job(kind, label, fn):
    job_id = uuid.uuid4().hex[:8]
    job = {"id": job_id, "kind": kind, "label": label, "status": "running",
           "created": time.time(), "file": None, "error": None}
    jobs[job_id] = job

    def work():
        try:
            job["file"] = Path(fn()).name
            job["status"] = "done"
        except Exception as e:  # surfaced to the UI
            job["status"] = "error"
            job["error"] = str(e)

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
        rec.stop()
    return {"ok": True}


def _clip_path(name):
    path = (CLIPS_DIR / name).resolve()
    if path.parent != CLIPS_DIR or not path.is_file():
        raise HTTPException(404)
    return path


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


@app.get("/clips/{name}")
def get_clip(name: str):
    return FileResponse(_clip_path(name))


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
