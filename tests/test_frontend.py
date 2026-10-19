"""Tests for the web UI's needs: the combined /api/state endpoint and the static page."""

import re
import time

import pytest
from fastapi.testclient import TestClient

import clipper.server as server


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "CLIPS_DIR", tmp_path / "clips")
    monkeypatch.setattr(server, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(server, "jobs", {})
    monkeypatch.setattr(server, "recorders", {})
    return TestClient(server.app)


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


def test_state_includes_recorders(client, monkeypatch):
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


def test_page_is_self_contained():
    html = (server.STATIC_DIR / "index.html").read_text()
    # No frameworks or CDNs: the only remote script is the YouTube player API.
    remote = set(re.findall(r"""https?://[^\s"'`<>)]+""", html))
    scripts = {u for u in remote if "iframe_api" in u or u.endswith(".js")}
    assert scripts == {"https://www.youtube.com/iframe_api"}
    assert "<script src" not in html and not re.search(r'<link[^>]+href="(https?:)?//', html)
    # Every element the script looks up by id exists in the page.
    ids = set(re.findall(r'\bid="([\w-]+)"', html))
    used = set(re.findall(r'\$\("([\w-]+)"\)', html))
    assert used and used <= ids, used - ids
    assert "/api/state" in html
