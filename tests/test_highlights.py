import random
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

import clipper.highlights_api as hapi
import clipper.server as server
from clipper.highlights import SILENCE_DB, find_moments, measure_levels, parse_levels


def test_parse_levels():
    lines = ["frame:0    pts:0       pts_time:0", "lavfi.astats.Overall.RMS_level=-40.5",
             "frame:1    pts:8000    pts_time:1", "lavfi.astats.Overall.RMS_level=-inf",
             "frame:2    pts:16000   pts_time:2", "lavfi.astats.Overall.RMS_level=-12"]
    assert list(parse_levels(lines)) == [(0, -40.5), (1, SILENCE_DB), (2, -12.0)]


def quiet_with_spikes(n, spikes, seed=1):
    rnd = random.Random(seed)
    levels = [-40 + rnd.uniform(-1.5, 1.5) for _ in range(n)]
    for t, db in spikes.items():
        levels[t] = levels[t + 1] = levels[t - 1] = db
    return levels


def test_find_moments_picks_biggest_non_overlapping_spikes():
    levels = quiet_with_spikes(900, {100: -20, 300: -10, 320: -15, 600: -25, 850: -18})
    moments = find_moments(levels, count=3, before=30, after=30)
    assert [m["t"] for m in moments] == [100, 300, 850]         # time order
    assert {m["t"]: m["rank"] for m in moments} == {300: 1, 850: 2, 100: 3}
    assert moments[0]["start"] == 70 and moments[0]["end"] == 130
    assert moments[2]["end"] == 880 and moments[1]["score"] > 20
    starts_ends = sorted((m["start"], m["end"]) for m in moments)
    assert all(a[1] <= b[0] for a, b in zip(starts_ends, starts_ends[1:]))  # no overlaps
    # 320 is too close to 300 to get its own clip; 600 is next in line.
    assert [m["t"] for m in find_moments(levels, count=4, before=30, after=30)] == [100, 300, 600, 850]


def test_find_moments_edges():
    assert find_moments([], count=5) == []
    assert find_moments([None] * 100, count=5) == []             # silence only
    short = quiet_with_spikes(100, {10: -10})
    m = find_moments(short, count=50, before=45, after=45)
    assert len(m) <= 2 and m[0]["start"] == 0                    # clamped to the video
    assert all(x["end"] <= 100 for x in m)


@pytest.fixture
def loud_file(tmp_path):
    """70s of quiet tone with loud bursts at 20s and 50s, plus a black video track."""
    path = tmp_path / "talk.mp4"
    vol = "if(between(t,20,22)+between(t,50,52),1,0.02)"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=black:size=160x90:rate=5",
                    "-f", "lavfi", "-i", "sine=frequency=330", "-t", "70",
                    "-af", f"volume='{vol}':eval=frame", "-c:v", "libx264", "-c:a", "aac",
                    "-shortest", str(path)], check=True)
    return path


def test_measure_levels_finds_bursts_in_a_file(loud_file):
    levels = {}
    measure_levels(str(loud_file), lambda t, db: levels.__setitem__(t, db))
    series = [levels.get(i) for i in range(max(levels) + 1)]
    assert 69 <= len(series) <= 71
    moments = find_moments(series, count=2, before=5, after=5)
    assert [m["t"] for m in moments] == pytest.approx([21, 51], abs=1)


def test_highlights_api(loud_file, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "CLIPS_DIR", tmp_path / "clips")
    cut = []

    def fake_clip_vod(url, start, end, out_dir, **kw):
        cut.append((url, start, end))
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"hl_{int(start)}.mp4"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", str(start), "-to", str(end),
                        "-i", url[len("file://"):], "-c", "copy", str(out)], check=True)
        return out

    monkeypatch.setattr(hapi, "clip_vod", fake_clip_vod)
    client = TestClient(server.app)
    assert client.post("/api/highlights", json={"url": "  "}).status_code == 422
    url = f"file://{loud_file}"
    a = client.post("/api/highlights", json={"url": url}).json()
    assert a["status"] == "analyzing" and a["levels"] == []
    deadline = time.time() + 30
    while a["status"] == "analyzing" and time.time() < deadline:
        time.sleep(0.3)
        a = client.get(f"/api/highlights/{a['id']}").json()
    assert a["status"] == "done", a["error"]
    assert a["progress"] == 1.0 and a["duration"] >= 69 and len(a["levels"]) >= 69
    assert "levels" not in client.get("/api/highlights").json()[0]

    moments = client.get(f"/api/highlights/{a['id']}/moments",
                         params={"count": 2, "before": 5, "after": 5}).json()["moments"]
    assert [m["t"] for m in moments] == pytest.approx([21, 51], abs=1)
    assert client.get(f"/api/highlights/{a['id']}/moments", params={"count": 51}).status_code == 422

    jobs = client.post(f"/api/highlights/{a['id']}/clips",
                       json={"moments": [{"start": m["start"], "end": m["end"]} for m in moments]}).json()["jobs"]
    assert [j["kind"] for j in jobs] == ["highlight", "highlight"]
    assert jobs[0]["label"].startswith("Highlight #1 0:1")
    deadline = time.time() + 30
    while any(j["status"] == "running" for j in jobs) and time.time() < deadline:
        time.sleep(0.3)
        state = {j["id"]: j for j in client.get("/api/jobs").json()}
        jobs = [state[j["id"]] for j in jobs]
    assert [j["status"] for j in jobs] == ["done", "done"], jobs
    assert sorted(c[1] for c in cut) == [m["start"] for m in moments]
    assert client.post(f"/api/highlights/{a['id']}/clips",
                       json={"moments": [{"start": 10, "end": 5}]}).status_code == 400
    assert client.delete(f"/api/highlights/{a['id']}").json() == {"ok": True}
    assert client.get(f"/api/highlights/{a['id']}").status_code == 404
