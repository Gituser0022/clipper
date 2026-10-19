"""Web API for downloading whole videos (or their audio) from any link."""

import subprocess
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, StringConstraints

from .download import FORMATS, MAX_ITEMS, download, inspect_link

router = APIRouter(prefix="/api/download")

Url = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\w[\w+.-]*://\S+$")]
Browser = Literal["chrome", "safari", "firefox", "edge", "brave"] | None
Choice = Literal[tuple(FORMATS)]

CHOICE_LABELS = {"compatible": "Best for Mac", "highest": "Highest quality",
                 "h1080": "1080p", "h720": "720p", "h480": "480p", "audio": "Audio only"}


class InspectRequest(BaseModel):
    url: Url
    browser: Browser = None


class DownloadRequest(BaseModel):
    url: Url
    choice: Choice = "compatible"
    items: list[Annotated[int, Field(ge=1)]] | None = Field(None, max_length=MAX_ITEMS)
    browser: Browser = None
    title: str | None = Field(None, max_length=300)


def _server():
    from . import server  # imported lazily: server.py includes this router
    return server


@router.post("/inspect")
def inspect(req: InspectRequest):
    try:
        return inspect_link(req.url, req.browser)
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "the site took too long to answer - try again")
    except (RuntimeError, ValueError) as e:
        raise HTTPException(400, str(e))


@router.post("")
def start_download(req: DownloadRequest):
    srv = _server()
    title = req.title or req.url
    items = sorted(set(req.items)) if req.items else [None]
    jobs = []
    for item in items:
        label = f"Download · {CHOICE_LABELS[req.choice]} · {title}"
        if item is not None and len(items) > 1:
            label += f" (#{item})"

        def fn(report, item=item):
            return download(req.url, req.choice, srv.DOWNLOADS_DIR, item, req.browser, report)

        jobs.append(srv._run_job("download", label, fn, reports=True))
    return {"jobs": jobs}
