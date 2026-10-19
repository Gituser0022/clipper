import os
import time

import pytest
from fastapi.testclient import TestClient

import clipper.server as server
from clipper import storage


def make_file(folder, name, size, age=0):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(b"\0" * size)
    t = time.time() - age
    os.utime(path, (t, t))
    return path


def test_default_home(monkeypatch, tmp_path):
    monkeypatch.delenv("CLIPPER_HOME")
    monkeypatch.delenv("CLIPPER_DATA", raising=False)
    assert (storage.default_home() / "pyproject.toml").is_file()  # the checkout itself
    monkeypatch.setenv("CLIPPER_DATA", str(tmp_path / "old"))
    assert storage.default_home() == tmp_path / "old"


def test_settings_round_trip(tmp_path):
    assert storage.load_settings(tmp_path) == {"storage_limit_gb": None, "when_full": "stop"}
    storage.save_settings(tmp_path, {"storage_limit_gb": 20, "when_full": "delete_oldest"})
    assert storage.load_settings(tmp_path)["storage_limit_gb"] == 20
    (tmp_path / storage.SETTINGS_FILE).write_text("not json")
    assert storage.load_settings(tmp_path)["when_full"] == "stop"


def test_migrate_old_layout(tmp_path):
    make_file(tmp_path / "data" / "clips", "live_1.mp4", 10)
    make_file(tmp_path / "data" / "clips" / ".thumbs", "live_1.mp4.jpg", 5)
    make_file(tmp_path / "data" / "work" / "buffer_x", "seg_1.ts", 5)
    make_file(tmp_path / "Clips", "live_1.mp4", 3)  # name clash -> kept, other renamed
    assert storage.migrate_old_data(tmp_path) == 1
    assert sorted(p.name for p in (tmp_path / "Clips").iterdir()) == ["live_1-1.mp4", "live_1.mp4"]
    assert not (tmp_path / "data").exists()


def test_startup_lists_saved_files(clipper_home):
    make_file(clipper_home / "Clips", "live_ab12.mp4", 100, age=60)
    make_file(clipper_home / "Highlights", "vod_x.mp4", 100)
    make_file(clipper_home / "Downloads", "Great_talk-abc.mp3", 100)
    make_file(clipper_home / "Clips" / ".thumbs", "live_ab12.mp4.jpg", 10)
    with TestClient(server.app) as client:
        jobs = {j["file"]: j for j in client.get("/api/jobs").json()}
        assert set(jobs) == {"live_ab12.mp4", "vod_x.mp4", "Great_talk-abc.mp3"}
        assert jobs["live_ab12.mp4"]["kind"] == "live" and jobs["live_ab12.mp4"]["folder"] == "clips"
        assert jobs["vod_x.mp4"]["kind"] == "highlight"
        assert jobs["Great_talk-abc.mp3"]["kind"] == "download"
        assert jobs["Great_talk-abc.mp3"]["label"] == "Great talk-abc"
        assert client.get("/clips/Great_talk-abc.mp3").status_code == 200
        assert (clipper_home / "Logs").is_dir()
    server.jobs.clear()


def test_storage_info_limit_and_settings(clipper_home):
    client = TestClient(server.app)
    make_file(clipper_home / "Clips", "a.mp4", 3000)
    make_file(clipper_home / "Downloads", "b.mp4", 2000)
    make_file(clipper_home / ".work" / "buffer_1", "seg.ts", 500)
    info = client.get("/api/storage").json()
    assert info["used"]["total"] == 5000 and info["used"]["work"] == 500
    assert info["limit_bytes"] is None and info["full"] is False and info["percent"] is None
    assert info["folders"]["downloads"].endswith("Downloads") and info["disk_free_bytes"] > 0
    assert client.get("/api/state").json()["storage"]["used"]["clips"] == 3000

    assert client.put("/api/settings", json={"storage_limit_gb": 0.5}).status_code == 422
    assert client.put("/api/settings", json={"when_full": "panic"}).status_code == 422
    info = client.put("/api/settings", json={"storage_limit_gb": 1, "when_full": "stop"}).json()
    assert info["limit_bytes"] == storage.GB and info["percent"] < 0.01
    assert storage.load_settings(clipper_home)["storage_limit_gb"] == 1


def test_full_storage_stops_new_clips(clipper_home, monkeypatch):
    monkeypatch.setattr(storage, "GB", 1000)  # make "1 GB" tiny for the test
    storage.save_settings(clipper_home, {"storage_limit_gb": 1, "when_full": "stop"})
    make_file(clipper_home / "Clips", "old.mp4", 1500)
    job = server._run_job("vod", "test", lambda: make_file(clipper_home / "Clips", "new.mp4", 10))
    deadline = time.time() + 5
    while job["status"] == "running" and time.time() < deadline:
        time.sleep(0.05)
    assert job["status"] == "error" and "Storage limit reached" in job["error"]
    assert not (clipper_home / "Clips" / "new.mp4").exists()
    assert TestClient(server.app).get("/api/storage").json()["full"] is True


def test_delete_oldest_makes_room(clipper_home, monkeypatch):
    monkeypatch.setattr(storage, "GB", 1000)
    make_file(clipper_home / "Clips", "oldest.mp4", 400, age=300)
    make_file(clipper_home / "Downloads", "older.mp4", 400, age=200)
    make_file(clipper_home / "Highlights", "recent.mp4", 100, age=100)
    client = TestClient(server.app)
    # Lowering the limit below current usage frees space right away.
    info = client.put("/api/settings", json={"storage_limit_gb": 1, "when_full": "delete_oldest"}).json()
    assert info["used"]["total"] == 900 and not info["full"]
    job = server._run_job("vod", "test", lambda: make_file(clipper_home / "Clips", "new.mp4", 300))
    deadline = time.time() + 5
    while job["status"] == "running" and time.time() < deadline:
        time.sleep(0.05)
    assert job["status"] == "done" and job["folder"] == "clips"
    left = sorted(p.name for d in ("Clips", "Downloads", "Highlights")
                  for p in (clipper_home / d).iterdir() if p.is_file())
    assert left == ["new.mp4", "older.mp4", "recent.mp4"]  # only the oldest went
    server.jobs.clear()


def test_delete_clip_and_open_folder(clipper_home, monkeypatch):
    client = TestClient(server.app)
    path = make_file(clipper_home / "Downloads", "talk.mp4", 50)
    make_file(clipper_home / "Downloads" / ".thumbs", "talk.mp4.jpg", 5)
    server.jobs["j1"] = {"id": "j1", "kind": "download", "label": "x", "status": "done", "created": 1,
                         "file": "talk.mp4", "error": None, "duration": 1, "progress": None,
                         "stage": None, "folder": "downloads"}
    assert client.delete("/api/clips/talk.mp4").json()["used"]["downloads"] == 0
    assert not path.exists() and "j1" not in server.jobs
    assert not (clipper_home / "Downloads" / ".thumbs" / "talk.mp4.jpg").exists()
    assert client.delete("/api/clips/talk.mp4").status_code == 404
    outside = make_file(clipper_home, "keep.mp4", 5)
    assert client.delete("/api/clips/..%2Fkeep.mp4").status_code != 200
    assert outside.exists()  # nothing outside the three folders can be deleted

    opened = []
    monkeypatch.setattr(server, "_open_in_finder", opened.append)
    monkeypatch.setattr(server.sys, "platform", "linux")
    assert client.post("/api/storage/open", json={}).status_code == 400
    monkeypatch.setattr(server.sys, "platform", "darwin")
    assert client.post("/api/storage/open", json={"folder": "logs"}).json() == {"ok": True}
    assert opened == [clipper_home / "Logs"]
    assert client.post("/api/storage/open", json={"folder": "secrets"}).status_code == 422
