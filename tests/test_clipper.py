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
    assert client.post("/api/vod/clip", json={"url": "https://youtu.be/x", "start": "10", "end": "5"}).status_code == 400
    assert client.post("/api/live/start", json={"url": "  "}).status_code == 422
    assert client.post("/api/vod/clip", json={"url": "not a link", "start": "0", "end": "5"}).status_code == 422

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


# -- range clipping, vertical crops ---------------------------------------------------

from clipper.auto import (AutoClipper, ChatTrigger, ClipPlanner, LoudnessTrigger, chat_source,
                          follow_twitch_chat)
from clipper.edit import make_vertical
from clipper.live import select_range


def fake_live(loud_after=None):
    """FAKE_LIVE, optionally quiet until `loud_after` seconds and loud afterwards."""
    if loud_after is None:
        return FAKE_LIVE
    cmd = list(FAKE_LIVE)
    i = cmd.index("-c:v")
    return cmd[:i] + ["-af", f"volume='if(lt(t,{loud_after}),0.02,1)':eval=frame"] + cmd[i:]


def test_select_range():
    segs = [Segment(Path(f"{i}.ts"), i * 2.0, i * 2.0 + 2) for i in range(10)]  # 0..20s
    chosen, offset = select_range(segs, 5, 9)
    assert [s.start for s in chosen] == [4, 6, 8] and offset == pytest.approx(1.0)
    chosen, offset = select_range(segs, -10, 3)  # clamped to what is buffered
    assert [s.start for s in chosen] == [0, 2] and offset == 0
    assert select_range(segs, 30, 40) == ([], 0.0)


def size(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return tuple(int(x) for x in out.strip().split(","))


def test_make_vertical(tmp_path):
    src = tmp_path / "wide.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25",
                    "-f", "lavfi", "-i", "sine", "-t", "2", "-c:v", "libx264", "-c:a", "aac", str(src)],
                   check=True)
    out = make_vertical(src, focus=0)
    assert out.name == "wide_9x16.mp4"
    assert size(out) == (1080, 1920)
    dur, kinds = duration(out)
    assert kinds == {"video", "audio"} and 1.5 <= dur <= 2.5


# -- auto-clip logic ------------------------------------------------------------------

def test_loudness_trigger():
    t = LoudnessTrigger(delta_db=8, min_history=10, refractory=6)
    quiet = [t.feed(s, -40 + (s % 4) * 0.5) for s in range(0, 20, 2)]
    assert not any(quiet)
    assert t.feed(20, -25)            # 15 dB above the usual level
    assert not t.feed(22, -25)        # still loud, but within the refractory period
    assert not t.feed(24, None)       # no audio
    assert not LoudnessTrigger(min_history=10).feed(0, -10)  # no history yet


def test_chat_trigger():
    c = ChatTrigger(window=10, baseline=120, factor=3, min_count=8, min_history=30)
    now = 1000.0
    for i in range(60):               # steady chat: 1 message every 2s for 2 minutes
        c.add(now + i * 2)
        assert not c.check(now + i * 2)
    now += 120
    for i in range(20):               # 20 messages in 4s
        c.add(now + i * 0.2)
    assert c.check(now + 4)
    assert not c.check(now + 5)       # refractory
    quiet = ChatTrigger(min_count=8, min_history=30)
    assert not quiet.check(0)
    for i in range(10):
        quiet.add(5 + i * 0.1)
    assert not quiet.check(6)         # too early to judge
    for i in range(10):
        quiet.add(40 + i * 0.1)
    assert quiet.check(41)            # quiet chat, then a burst of 10 >= min_count


def test_clip_planner():
    p = ClipPlanner(before=60, after=60, max_len=300)
    p.add(100, "loud")
    assert p.due(150) == []
    p.add(130, "chat")                # overlaps -> extends instead of a second clip
    assert p.due(180) == []
    assert p.due(190) == [{"start": 40, "end": 190, "reasons": ["loud", "chat"]}]
    p.add(200, "loud")                # starts where the last clip ended: no repeated footage
    assert p.due(260) == [{"start": 190, "end": 260, "reasons": ["loud"]}]
    p.add(1000, "loud")
    for t in range(1000, 1600, 30):   # non-stop hype is capped at max_len
        p.add(t, "loud")
    w = p.due(2000)[0]
    assert w["end"] - w["start"] == 300
    p.add(5, "loud")                  # entirely inside footage already saved
    assert p.pending is None


def test_chat_source():
    assert chat_source("https://www.twitch.tv/SomeOne") == ("twitch", "someone")
    assert chat_source("https://www.youtube.com/live/abc")[0] == "youtube"
    assert chat_source("https://youtu.be/abc")[0] == "youtube"
    assert chat_source("https://kick.com/x") == (None, None)


def test_youtube_id():
    from clipper.server import youtube_id
    assert youtube_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=1") == "dQw4w9WgXcQ"
    assert youtube_id("https://www.youtube.com/live/yufNYN7MSmc?si=x") == "yufNYN7MSmc"
    assert youtube_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert youtube_id("https://www.youtube.com/@channel/live") is None
    assert youtube_id("https://twitch.tv/x") is None


def test_twitch_chat_counts_messages():
    import socket
    import threading
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    got, statuses, stop = [], [], threading.Event()

    def fake_twitch():
        conn, _ = server.accept()
        assert b"JOIN #chan" in conn.recv(1024)
        conn.sendall(b":me!me@me JOIN #chan\r\nPING :tmi.twitch.tv\r\n" +
                     b":a!a@a PRIVMSG #chan :hi\r\n" * 3)
        assert conn.recv(1024).startswith(b"PONG :tmi.twitch.tv")
        time.sleep(0.5)
        stop.set()
        conn.close()

    threading.Thread(target=fake_twitch, daemon=True).start()
    follow_twitch_chat("chan", stop, lambda: got.append(1), statuses.append,
                       host="127.0.0.1", port=server.getsockname()[1], tls=False)
    assert len(got) == 3 and "connected" in statuses


def wait_for(cond, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.5)
    return False


def test_auto_clip_on_loud_moment(tmp_path):
    rec = LiveRecorder("fake://live", tmp_path / "work", buffer_seconds=60, segment_seconds=1,
                       producer_cmd=fake_live(loud_after=9)).start()
    auto = AutoClipper(rec, tmp_path / "clips", audio=True, chat=False, before=3, after=3,
                       min_history=5, vertical=True).start()
    try:
        assert wait_for(lambda: auto.clips, 45), (rec.error, auto.info())
        event = auto.info()["events"][0]
        assert event["kind"] == "loud" and 8 <= event["stream_t"] <= 10
        clip = next((tmp_path / "clips").glob("auto_*_9x16.mp4"))
        assert size(clip) == (1080, 1920)
        assert 5 <= duration(clip)[0] <= 7
        assert all(p.stem.endswith("_9x16") for p in (tmp_path / "clips").iterdir())  # original replaced
    finally:
        auto.stop()
        rec.stop()


def test_auto_clip_on_chat_burst(tmp_path):
    import sys
    # Fake yt-dlp: quiet chat for 14s, then a burst of 30 messages.
    script = ("import time,sys,json\n"
              "f=open(sys.argv[1],'a',buffering=1)\n"
              "for i in range(7): f.write(json.dumps({'addChatItemAction':i})+'\\n'); time.sleep(2)\n"
              "for i in range(30): f.write(json.dumps({'addChatItemAction':i})+'\\n'); time.sleep(0.05)\n"
              "time.sleep(60)\n")
    rec = LiveRecorder("https://www.youtube.com/watch?v=x", tmp_path / "work", buffer_seconds=60,
                       segment_seconds=1, producer_cmd=FAKE_LIVE).start()
    chat_file = rec.buf_dir / "chat" / "chat.live_chat.json"  # where yt-dlp would write it
    auto = AutoClipper(rec, tmp_path / "clips", audio=False, chat=True, before=3, after=3,
                       min_history=12, chat_cmd=[sys.executable, "-c", script, str(chat_file)]).start()
    try:
        assert wait_for(lambda: auto.clips, 45), (rec.error, auto.info())
        info = auto.info()
        assert info["chat_status"] == "connected"
        assert [e["kind"] for e in info["events"]] == ["chat"]
        assert 5 <= duration(next((tmp_path / "clips").glob("auto_*.mp4")))[0] <= 7
    finally:
        auto.stop()
        rec.stop()
