import subprocess
import time

import pytest
from fastapi.testclient import TestClient

import clipper.server as server
from test_clipper import FAKE_LIVE


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "CLIPS_DIR", tmp_path / "clips")
    monkeypatch.setattr(server, "WORK_DIR", tmp_path / "work")
    orig = server.LiveRecorder
    monkeypatch.setattr(server, "LiveRecorder",
                        lambda url, work, buffer_seconds: orig(url, work, buffer_seconds,
                                                               segment_seconds=1, producer_cmd=FAKE_LIVE))
    c = TestClient(server.app)
    yield c
    for rec_id in list(server.recorders):
        c.delete(f"/api/live/{rec_id}")


def wait_job(client, job, timeout=30):
    deadline = time.time() + timeout
    while job["status"] == "running" and time.time() < deadline:
        time.sleep(0.3)
        job = next(j for j in client.get("/api/jobs").json() if j["id"] == job["id"])
    return job


def test_preview_youtube_needs_no_lookup(client):
    assert client.get("/api/preview", params={"url": "https://youtu.be/dQw4w9WgXcQ"}).json() == \
        {"kind": "youtube", "id": "dQw4w9WgXcQ"}


def test_live_auto_toggle_and_info(client):
    rec = client.post("/api/live/start", json={
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "buffer_seconds": 60,
        "auto": {"audio": True, "chat": False, "before": 30, "after": 20}}).json()
    assert rec["youtube_id"] == "dQw4w9WgXcQ"
    assert rec["auto"]["before"] == 30 and rec["auto"]["chat_status"] == "off"
    assert rec["buffer_seconds"] >= 330  # grown so a whole auto window fits
    off = client.post(f"/api/live/{rec['id']}/auto", json={"audio": False, "chat": False}).json()
    assert off["auto"] is None
    on = client.post(f"/api/live/{rec['id']}/auto", json={"before": 5, "after": 5}).json()
    assert on["auto"]["audio"] and on["auto"]["chat"]
    assert client.post(f"/api/live/{rec['id']}/auto", json={"before": 1}).status_code == 422


def test_live_clip_vertical_and_make_vertical(client):
    rec = client.post("/api/live/start", json={"url": "fake://live", "buffer_seconds": 60}).json()
    deadline = time.time() + 30
    while client.get("/api/live").json()[0]["buffered"] < 3 and time.time() < deadline:
        time.sleep(0.5)
    job = wait_job(client, client.post(f"/api/live/{rec['id']}/clip",
                                       json={"seconds_before": 2, "vertical": True}).json())
    assert job["status"] == "done", job["error"]
    assert job["file"].endswith("_9x16.mp4")
    assert [p.name for p in server.CLIPS_DIR.iterdir()] == [job["file"]]  # original removed

    plain = wait_job(client, client.post(f"/api/live/{rec['id']}/clip", json={"seconds_before": 2}).json())
    edit = wait_job(client, client.post(f"/api/clips/{plain['file']}/vertical", json={"focus": 1}).json())
    assert edit["status"] == "done" and edit["file"] == plain["file"].replace(".mp4", "_9x16.mp4")
    assert client.post("/api/clips/nope.mp4/vertical", json={}).status_code == 404


def test_quit_calls_shutdown(client, monkeypatch):
    called = []
    monkeypatch.setattr(server, "_shutdown", lambda: called.append(1))
    assert client.post("/api/quit").json() == {"ok": True}
    time.sleep(0.6)
    assert called == [1]
