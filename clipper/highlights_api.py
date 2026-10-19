"""Web API for highlights: analyse a video's audio, pick moments, cut clips."""

import threading
import time
import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, StringConstraints

from .highlights import find_moments, measure_levels, video_info
from .timeparse import format_time
from .vod import clip_vod

router = APIRouter(prefix="/api/highlights")
analyses: dict[str, dict] = {}
_stops: dict[str, threading.Event] = {}
_clip_slots = threading.Semaphore(2)  # downloads at once; the rest wait their turn

Url = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\w[\w+.-]*://\S+$")]


class AnalyseRequest(BaseModel):
    url: Url


class Window(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)


class ClipsRequest(BaseModel):
    moments: list[Window] = Field(min_length=1, max_length=50)
    vertical: bool = False


def _server():
    from . import server  # imported lazily: server.py includes this router
    return server


def _analyse(a, stop):
    try:
        try:
            info = video_info(a["url"])
            a["title"], a["duration"] = info["title"], info["duration"]
            if info["is_live"]:
                raise RuntimeError("this is a live stream - use Start watching for live streams")
        except RuntimeError as e:
            if "live stream" in str(e):
                raise
            # A local file or a site without metadata still has audio to analyse.

        def on_level(t, db):
            levels = a["levels"]
            levels.extend([None] * (t + 1 - len(levels)))
            levels[t] = round(db, 1)
            if a["duration"]:
                a["progress"] = min(0.99, len(levels) / a["duration"])

        measure_levels(a["url"], on_level, stop)
        if not stop.is_set():
            if not a["levels"]:
                raise RuntimeError("no audio found in this video")
            a["duration"] = a["duration"] or len(a["levels"])
            a["progress"] = 1.0
            a["status"] = "done"
    except Exception as e:  # surfaced to the UI
        a["status"] = "error"
        a["error"] = str(e)


def _get(analysis_id):
    a = analyses.get(analysis_id)
    if not a:
        raise HTTPException(404, "unknown analysis")
    return a


def _summary(a):
    return {k: v for k, v in a.items() if k != "levels"}


@router.post("")
def start_analysis(req: AnalyseRequest):
    a = {"id": uuid.uuid4().hex[:8], "url": req.url, "title": req.url, "duration": None,
         "youtube_id": _server().youtube_id(req.url), "status": "analyzing", "progress": 0.0,
         "error": None, "created": time.time(), "levels": []}
    analyses[a["id"]] = a
    _stops[a["id"]] = stop = threading.Event()
    threading.Thread(target=_analyse, args=(a, stop), daemon=True).start()
    return a


@router.get("")
def list_analyses():
    return sorted((_summary(a) for a in analyses.values()), key=lambda a: a["created"], reverse=True)


@router.get("/{analysis_id}")
def get_analysis(analysis_id: str):
    return _get(analysis_id)


@router.get("/{analysis_id}/moments")
def moments(analysis_id: str, count: int = Query(10, ge=1, le=50),
            before: float = Query(45, ge=5, le=300), after: float = Query(45, ge=5, le=300)):
    a = _get(analysis_id)
    return {"moments": find_moments(list(a["levels"]), count, before, after)}


@router.post("/{analysis_id}/clips")
def make_clips(analysis_id: str, req: ClipsRequest):
    a = _get(analysis_id)
    srv = _server()
    jobs = []
    for k, m in enumerate(sorted(req.moments, key=lambda m: m.start), 1):
        if m.end <= m.start:
            raise HTTPException(400, f"moment {k}: end must be after start")
        label = f"Highlight #{k} {format_time(m.start)}–{format_time(m.end)}"
        label += " · 9:16" if req.vertical else ""

        def clip(start=m.start, end=m.end):
            with _clip_slots:
                return clip_vod(a["url"], start, end, srv.CLIPS_DIR)

        jobs.append(srv._run_job("highlight", f"{label} · {a['title']}",
                                 srv._vertical_after(clip, req.vertical)))
    return {"jobs": jobs}


@router.delete("/{analysis_id}")
def delete_analysis(analysis_id: str):
    stop = _stops.pop(analysis_id, None)
    if stop:
        stop.set()
    analyses.pop(analysis_id, None)
    return {"ok": True}
