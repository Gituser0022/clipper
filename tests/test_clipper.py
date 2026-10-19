import json
import subprocess
import time
from pathlib import Path

import pytest

from clipper.live import LiveRecorder, Segment, select_segments
from clipper.timeparse import format_time, parse_time
from clipper.vod import build_vod_command

# A fake "live stream": ffmpeg generating video+audio in real time on stdout.
FAKE_LIVE = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-re",
             "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
             "-f", "lavfi", "-i", "sine=frequency=440",
             "-c:v", "libx264", "-preset", "ultrafast", "-g", "25",
             "-c:a", "aac", "-f", "mpegts", "pipe:1"]


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-show_entries", "stream=codec_type", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    return float(data["format"]["duration"]), {s["codec_type"] for s in data["streams"]}


@pytest.mark.parametrize("text,expected", [
    ("90", 90), ("1:30", 90), ("01:02:03", 3723), ("0:05.5", 5.5), (12, 12),
])
def test_parse_time(text, expected):
    assert parse_time(text) == expected


@pytest.mark.parametrize("bad", ["", "abc", "1:2:3:4", "-5"])
def test_parse_time_rejects(bad):
    with pytest.raises(ValueError):
        parse_time(bad)


def test_format_time():
    assert format_time(90) == "1:30"
    assert format_time(3723.25) == "1:02:03.250"


def test_vod_command():
    cmd = build_vod_command("https://youtu.be/x", "1:00", "1:30", "/out", name="c")
    assert cmd[cmd.index("--download-sections") + 1] == "*60.0-90.0"
    assert "--force-keyframes-at-cuts" not in cmd
    assert "--force-keyframes-at-cuts" in build_vod_command("u", "0", "5", "/o", precise=True)
    with pytest.raises(ValueError):
        build_vod_command("u", "10", "5", "/o")


def test_select_segments():
    segs = [Segment(Path(f"{i}.ts"), i * 2.0, i * 2.0 + 2) for i in range(10)]  # 0..20s
    chosen, offset = select_segments(segs, 5)
    assert [s.start for s in chosen] == [14, 16, 18]
    assert offset == pytest.approx(1.0)
    chosen, offset = select_segments(segs, 100)  # more than buffered -> everything
    assert len(chosen) == 10 and offset == 0
    assert select_segments([], 5) == ([], 0.0)


def test_live_recorder_end_to_end(tmp_path):
    rec = LiveRecorder("fake://live", tmp_path / "work", buffer_seconds=8,
                       segment_seconds=1, producer_cmd=FAKE_LIVE).start()
    try:
        deadline = time.time() + 30
        while rec.buffered_seconds() < 6 and time.time() < deadline:
            time.sleep(0.5)
        assert rec.status == "recording", rec.error
        clip = rec.clip(4, out_dir=tmp_path / "clips")
        dur, kinds = duration(clip)
        assert kinds == {"video", "audio"}
        assert 3 <= dur <= 5.5

        # Rolling buffer: old segments get deleted once past buffer_seconds.
        time.sleep(6)
        assert rec.buffered_seconds() <= 8 + 2

        # "Clip the next few seconds too": waits, then includes them.
        t0 = time.time()
        clip2 = rec.clip(2, seconds_after=2, out_dir=tmp_path / "clips")
        assert time.time() - t0 >= 2
        assert 3 <= duration(clip2)[0] <= 5.5
    finally:
        rec.stop()
    assert rec.status == "stopped"


def test_server_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import clipper.server as server
    monkeypatch.setattr(server, "CLIPS_DIR", tmp_path / "clips")
    monkeypatch.setattr(server, "WORK_DIR", tmp_path / "work")
    orig = server.LiveRecorder
    monkeypatch.setattr(server, "LiveRecorder",
                        lambda url, work, buffer_seconds: orig(url, work, buffer_seconds,
                                                               segment_seconds=1, producer_cmd=FAKE_LIVE))
    client = TestClient(server.app)
    assert "Clipper" in client.get("/").text
    assert client.post("/api/vod/clip", json={"url": "u", "start": "10", "end": "5"}).status_code == 400

    rec = client.post("/api/live/start", json={"url": "fake://live", "buffer_seconds": 60}).json()
    try:
        deadline = time.time() + 30
        while client.get("/api/live").json()[0]["buffered"] < 4 and time.time() < deadline:
            time.sleep(0.5)
        job = client.post(f"/api/live/{rec['id']}/clip", json={"seconds_before": 3}).json()
        deadline = time.time() + 20
        while job["status"] == "running" and time.time() < deadline:
            time.sleep(0.3)
            job = next(j for j in client.get("/api/jobs").json() if j["id"] == job["id"])
        assert job["status"] == "done", job["error"]
        resp = client.get(f"/clips/{job['file']}")
        assert resp.status_code == 200 and len(resp.content) > 1000
        assert client.get("/clips/..%2Fsecret").status_code == 404
    finally:
        client.post(f"/api/live/{rec['id']}/stop")
