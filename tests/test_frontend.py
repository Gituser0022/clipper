"""Tests for what the web UI needs from the server, and for the static page itself."""

import json
import re
import subprocess
import time
import types

import pytest
from fastapi.testclient import TestClient

import clipper.server as server


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "CLIPS_DIR", tmp_path / "clips")
    monkeypatch.setattr(server, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(server, "jobs", {})
    monkeypatch.setattr(server, "recorders", {})
    (tmp_path / "clips").mkdir()
    return TestClient(server.app)


# -- /api/state -------------------------------------------------------------------------

def test_state_combines_live_and_jobs(client):
    assert client.get("/api/state").json() == {"live": [], "jobs": []}

    now = time.time()
    server.jobs["a"] = {"id": "a", "kind": "vod", "label": "old", "status": "done",
                        "created": now - 10, "file": "a.mp4", "error": None}
    server.jobs["b"] = {"id": "b", "kind": "live", "label": "new", "status": "running",
                        "created": now, "file": None, "error": None}
    state = client.get("/api/state").json()
    assert state["jobs"] == client.get("/api/jobs").json()
    assert [j["id"] for j in state["jobs"]] == ["b", "a"]  # newest first
    assert state["live"] == client.get("/api/live").json() == []


def test_state_includes_recorders(client):
    class FakeRecorder:
        id = "r1"
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

        def info(self):
            return {"id": self.id, "url": self.url, "status": "recording", "error": None,
                    "buffer_seconds": 300, "buffered": 12.0, "started_at": 0}

    server.recorders["r1"] = FakeRecorder()
    live = client.get("/api/state").json()["live"]
    assert live == client.get("/api/live").json()
    assert live[0]["id"] == "r1" and live[0]["youtube_id"] == "dQw4w9WgXcQ" and live[0]["auto"] is None


def test_finished_jobs_report_their_duration(client, tmp_path):
    clip = tmp_path / "clips" / "x.mp4"
    make_video(clip, seconds=2)
    job = server._run_job("vod", "test", lambda: clip)
    assert job["duration"] is None
    deadline = time.time() + 10
    while job["status"] == "running" and time.time() < deadline:
        time.sleep(0.05)
    assert job["status"] == "done" and 1.5 <= job["duration"] <= 2.5


# -- /api/inspect -----------------------------------------------------------------------

@pytest.mark.parametrize("url,live", [
    ("https://www.youtube.com/@somechannel/live", True),
    ("https://youtube.com/live/abcdefghijk", True),
    ("https://www.twitch.tv/somechannel", True),
    ("https://m.twitch.tv/SomeChannel/", True),
])
def test_inspect_fast_path_needs_no_yt_dlp(client, monkeypatch, url, live):
    monkeypatch.setattr(server, "subprocess", types.SimpleNamespace(run=_never, TimeoutExpired=subprocess.TimeoutExpired))
    info = client.get("/api/inspect", params={"url": url}).json()
    assert info["is_live"] is live
    assert set(info) >= {"is_live", "title", "duration", "thumbnail", "youtube_id"}


def test_inspect_fast_path_youtube_live_id(client):
    assert client.get("/api/inspect", params={"url": "https://youtube.com/live/abcdefghijk"}).json()["youtube_id"] == "abcdefghijk"


def test_inspect_local_file(client, tmp_path):
    path = tmp_path / "talk.mp4"
    make_video(path, seconds=3)
    info = client.get("/api/inspect", params={"url": path.as_uri()}).json()
    assert info["is_live"] is False and info["title"] == "talk.mp4" and 2.5 <= info["duration"] <= 3.5


def _never(*a, **k):
    raise AssertionError("yt-dlp should not run for this link")


def fake_ytdlp(monkeypatch, returncode=0, stdout="", stderr=""):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if cmd[0] == "yt-dlp":
            return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)
        return subprocess.run(cmd, **kw)
    monkeypatch.setattr(server, "subprocess", types.SimpleNamespace(
        run=run, TimeoutExpired=subprocess.TimeoutExpired, SubprocessError=subprocess.SubprocessError))
    return calls


def test_inspect_asks_yt_dlp_for_other_links(client, monkeypatch):
    calls = fake_ytdlp(monkeypatch, stdout=json.dumps({
        "id": "dQw4w9WgXcQ", "extractor_key": "Youtube", "title": "A video", "duration": 212,
        "thumbnail": "https://i.ytimg.com/x.jpg", "is_live": False, "live_status": "not_live"}))
    info = client.get("/api/inspect", params={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}).json()
    assert info == {"is_live": False, "title": "A video", "duration": 212,
                    "thumbnail": "https://i.ytimg.com/x.jpg", "youtube_id": "dQw4w9WgXcQ"}
    cmd = calls[0]
    assert "--dump-single-json" in cmd and "--skip-download" in cmd and "--no-playlist" in cmd


def test_inspect_detects_live_from_yt_dlp(client, monkeypatch):
    fake_ytdlp(monkeypatch, stdout=json.dumps({"id": "x", "extractor_key": "Generic", "title": "Live!",
                                               "live_status": "is_live"}))
    info = client.get("/api/inspect", params={"url": "https://kick.com/somebody"}).json()
    assert info["is_live"] is True and info["youtube_id"] is None


def test_inspect_errors(client, monkeypatch):
    fake_ytdlp(monkeypatch, returncode=1, stderr="ERROR: Unsupported URL: https://example.com/x\n")
    r = client.get("/api/inspect", params={"url": "https://example.com/x"})
    assert r.status_code == 400 and "Unsupported URL" in r.json()["detail"]
    assert client.get("/api/inspect", params={"url": "not a link"}).status_code == 422


# -- /clips/{name}/thumb.jpg ----------------------------------------------------------------

def make_video(path, seconds):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size=320x180:rate=10:duration={seconds}",
                    "-c:v", "libx264", "-preset", "ultrafast", str(path)], check=True)


def test_thumbnail_is_made_once_and_cached(client, tmp_path):
    make_video(tmp_path / "clips" / "clip_1.mp4", seconds=4)
    r = client.get("/clips/clip_1.mp4/thumb.jpg")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.content[:2] == b"\xff\xd8"  # a JPEG
    cached = tmp_path / "clips" / ".thumbs" / "clip_1.mp4.jpg"
    assert cached.is_file()
    stamp = cached.stat().st_mtime_ns
    assert client.get("/clips/clip_1.mp4/thumb.jpg").content == r.content
    assert cached.stat().st_mtime_ns == stamp  # served from the cache


def test_thumbnail_of_a_very_short_clip(client, tmp_path):
    make_video(tmp_path / "clips" / "short.mp4", seconds=1)
    assert client.get("/clips/short.mp4/thumb.jpg").status_code == 200


def test_thumbnail_path_safety(client, tmp_path):
    (tmp_path / "secret.mp4").write_bytes(b"x")
    assert client.get("/clips/..%2Fsecret.mp4/thumb.jpg").status_code == 404
    assert client.get("/clips/missing.mp4/thumb.jpg").status_code == 404
    assert client.get("/clips/.thumbs/thumb.jpg").status_code == 404
    (tmp_path / "clips" / "broken.mp4").write_bytes(b"not a video")
    assert client.get("/clips/broken.mp4/thumb.jpg").status_code == 404


# -- the page -------------------------------------------------------------------------

def page_sources():
    static = server.STATIC_DIR
    html = (static / "index.html").read_text()
    js = {p.name: p.read_text() for p in (static / "js").glob("*.js")}
    return static, html, js


def test_page_is_self_contained():
    static, html, js = page_sources()
    everything = html + (static / "app.css").read_text() + "".join(js.values())
    # No frameworks or CDNs: the only remote script is the YouTube player API.
    scripts = {u for u in re.findall(r"""https?://[^\s"'`<>)]+""", everything) if "iframe_api" in u or u.endswith(".js")}
    assert scripts == {"https://www.youtube.com/iframe_api"}
    assert not re.search(r'<(script|link)[^>]+(src|href)="(https?:)?//', html)
    # Local files the page refers to all exist (and are packaged: static/, fonts/, js/).
    for ref in re.findall(r'(?:src|href)="((?:js|fonts)/[^"]+|app\.css)"', html):
        assert (static / ref).is_file(), ref
    for ref in re.findall(r'url\("(fonts/[^"]+)"\)', (static / "app.css").read_text()):
        assert (static / ref).is_file(), ref
    assert list(static.glob("fonts/OFL-*.txt")), "font licences ship next to the fonts"
    for name, src in js.items():
        for imp in re.findall(r'from "\./([\w.]+)"|import "\./([\w.]+)"', src):
            assert (static / "js" / (imp[0] or imp[1])).is_file(), (name, imp)


def test_page_elements_exist():
    _, html, js = page_sources()
    ids = set(re.findall(r'\bid="([\w-]+)"', html))
    used = set()
    for src in js.values():
        used |= set(re.findall(r'\$\("([\w-]+)"\)', src))
    assert used and used <= ids, used - ids
    assert "/api/state" in js["core.js"] and "/api/highlights" in js["highlights.js"]


def test_static_files_are_served(client):
    assert "Clipper" in client.get("/").text
    for path in ("/app.css", "/js/main.js", "/fonts/pacifico-latin.woff2"):
        assert client.get(path).status_code == 200, path
    assert "javascript" in client.get("/js/main.js").headers["content-type"]
