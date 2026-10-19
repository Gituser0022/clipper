"""FastAPI web app: clip VODs and live streams from the browser."""

import os
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from .live import LiveRecorder
from .vod import clip_vod

DATA_DIR = Path(os.environ.get("CLIPPER_DATA", "data")).resolve()
CLIPS_DIR = DATA_DIR / "clips"
WORK_DIR = DATA_DIR / "work"
STATIC_DIR = Path(__file__).parent / "static"

recorders: dict[str, LiveRecorder] = {}

# Rejects blank or non-link input before it reaches yt-dlp.
Url = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\w[\w+.-]*://\S+$")]


@asynccontextmanager
async def lifespan(app):
    yield
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


class VodClipRequest(BaseModel):
    url: Url
    start: str
    end: str
    precise: bool = False


class LiveStartRequest(BaseModel):
    url: Url
    buffer_seconds: int = Field(300, ge=10, le=3 * 3600)


class LiveClipRequest(BaseModel):
    seconds_before: float = Field(30, gt=0, le=3 * 3600)
    seconds_after: float = Field(0, ge=0, le=600)


@app.post("/api/vod/clip")
def vod_clip(req: VodClipRequest):
    from .timeparse import parse_time
    try:
        if parse_time(req.end) <= parse_time(req.start):
            raise ValueError("end must be after start")
    except ValueError as e:
        raise HTTPException(400, str(e))
    label = f"{req.start}–{req.end} · {req.url}"
    return _run_job("vod", label, lambda: clip_vod(req.url, req.start, req.end, CLIPS_DIR,
                                                   precise=req.precise))


@app.post("/api/live/start")
def live_start(req: LiveStartRequest):
    rec = LiveRecorder(req.url, WORK_DIR, buffer_seconds=req.buffer_seconds).start()
    recorders[rec.id] = rec
    return rec.info()


@app.get("/api/live")
def live_list():
    return [r.info() for r in recorders.values()]


def _recorder(rec_id):
    rec = recorders.get(rec_id)
    if not rec:
        raise HTTPException(404, "unknown recorder")
    return rec


@app.post("/api/live/{rec_id}/clip")
def live_clip(rec_id: str, req: LiveClipRequest):
    rec = _recorder(rec_id)
    label = f"last {req.seconds_before:g}s" + (f" + next {req.seconds_after:g}s" if req.seconds_after else "")
    return _run_job("live", f"{label} · {rec.url}",
                    lambda: rec.clip(req.seconds_before, req.seconds_after, CLIPS_DIR))


@app.post("/api/live/{rec_id}/stop")
def live_stop(rec_id: str):
    rec = _recorder(rec_id)
    rec.stop()
    return rec.info()


@app.delete("/api/live/{rec_id}")
def live_remove(rec_id: str):
    rec = recorders.pop(rec_id, None)
    if rec:
        rec.stop()
    return {"ok": True}


@app.get("/api/jobs")
def job_list():
    return sorted(jobs.values(), key=lambda j: j["created"], reverse=True)


@app.get("/clips/{name}")
def get_clip(name: str):
    path = (CLIPS_DIR / name).resolve()
    if path.parent != CLIPS_DIR or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path)


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
