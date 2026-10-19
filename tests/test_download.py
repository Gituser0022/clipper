import functools
import http.server
import subprocess
import threading
import time

import pytest
from fastapi.testclient import TestClient

import clipper.server as server
from clipper.download import build_download_command, cookie_args, describe, download


@pytest.fixture(scope="module")
def video_url(tmp_path_factory):
    """A 3s mp4 served from a local web server, so yt-dlp can 'download' it offline."""
    root = tmp_path_factory.mktemp("site")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
                    "-f", "lavfi", "-i", "sine", "-t", "3", "-c:v", "libx264", "-c:a", "aac",
                    "-movflags", "+faststart", str(root / "clip.mp4")], check=True)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    httpd.RequestHandlerClass.log_message = lambda *a: None
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/clip.mp4"
    httpd.shutdown()


def test_describe_single_and_multi():
    single = describe({"title": "Talk", "extractor_key": "Youtube", "duration": 60,
                       "formats": [{"vcodec": "avc1", "height": 2160}, {"vcodec": "none", "acodec": "mp4a"}]})
    assert single["entries"] is None and single["duration"] == 60 and single["site"] == "Youtube"
    ids = [c["id"] for c in single["choices"]]
    assert ids == ["compatible", "highest", "h1080", "h720", "h480", "audio"]
    assert [c["id"] for c in single["choices"] if c["recommended"]] == ["compatible"]

    post = describe({"_type": "playlist", "title": "A post", "entries": [
        {"title": "one", "duration": 5, "formats": [{"vcodec": "avc1", "height": 720}]},
        None,
        {"title": "two", "duration": 7, "playlist_index": 3, "formats": [{"vcodec": "avc1", "height": 720}]}]})
    assert [(e["index"], e["title"]) for e in post["entries"]] == [(1, "one"), (3, "two")]
    assert [c["id"] for c in post["choices"]] == ["compatible", "h720", "h480", "audio"]

    sound = describe({"title": "Song", "formats": [{"vcodec": "none", "acodec": "opus"}]})
    assert [(c["id"], c["recommended"]) for c in sound["choices"]] == [("audio", True)]
    with pytest.raises(RuntimeError):
        describe({"_type": "playlist", "entries": []})


def test_commands():
    cmd = build_download_command("https://x.com/a/status/1", "audio", "/out", item=2, browser="chrome")
    assert cmd[-1] == "https://x.com/a/status/1"
    assert cmd[cmd.index("--playlist-items") + 1] == "2"
    assert cmd[cmd.index("--cookies-from-browser") + 1] == "chrome"
    assert "--audio-format" in cmd and "--no-simulate" in cmd
    assert "--playlist-items" not in build_download_command("u", "compatible", "/out")
    assert cookie_args(None) == []
    with pytest.raises(ValueError):
        cookie_args("netscape")
    with pytest.raises(ValueError):
        build_download_command("u", "8k", "/out")


def test_download_video_and_audio(video_url, tmp_path):
    seen = []
    path = download(video_url, "compatible", tmp_path, report=lambda **kw: seen.append(kw))
    assert path.suffix == ".mp4" and path.parent == tmp_path and path.stat().st_size > 1000
    assert {"progress": 1.0, "stage": "Done"} in seen
    assert any(kw.get("stage") == "Downloading video" for kw in seen)
    audio = download(video_url, "audio", tmp_path)
    assert audio.suffix == ".mp3"
    with pytest.raises(RuntimeError, match="404"):
        download(video_url.replace("clip.mp4", "missing.mp4"), "compatible", tmp_path)


def test_download_api(video_url, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "CLIPS_DIR", tmp_path / "clips")
    client = TestClient(server.app)
    info = client.post("/api/download/inspect", json={"url": video_url}).json()
    assert info["entries"] is None and info["choices"][0]["id"] == "compatible"
    assert client.post("/api/download/inspect", json={"url": "nope"}).status_code == 422
    assert client.post("/api/download/inspect", json={"url": video_url, "browser": "netscape"}).status_code == 422
    bad = client.post("/api/download/inspect", json={"url": video_url.replace("clip", "gone")})
    assert bad.status_code == 400 and "404" in bad.json()["detail"]
    assert client.post("/api/download", json={"url": video_url, "choice": "8k"}).status_code == 422

    jobs = client.post("/api/download", json={"url": video_url, "choice": "compatible",
                                              "title": "Test clip"}).json()["jobs"]
    assert len(jobs) == 1 and jobs[0]["kind"] == "download"
    assert jobs[0]["label"] == "Download · Best for Mac · Test clip"
    job = jobs[0]
    deadline = time.time() + 60
    while job["status"] == "running" and time.time() < deadline:
        time.sleep(0.3)
        job = next(j for j in client.get("/api/jobs").json() if j["id"] == job["id"])
    assert job["status"] == "done", job["error"]
    assert job["progress"] == 1.0 and job["stage"] == "Done" and job["duration"] > 2
    assert client.get(f"/clips/{job['file']}").status_code == 200
