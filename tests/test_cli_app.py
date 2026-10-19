"""Tests for the command line (new flags, `clipper vertical`) and the macOS
app builder in scripts/make-mac-app.sh (run on Linux with stubs)."""

import io
import json
import os
import plistlib
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from clipper import cli

ROOT = Path(__file__).resolve().parent.parent
MAKE_APP = ROOT / "scripts" / "make-mac-app.sh"


def make_video(path, seconds=1, size="320x240"):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", f"testsrc=size={size}:rate=25:duration={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest",
                    str(path)], check=True)
    return path


def video_size(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=width,height", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    s = json.loads(out)["streams"][0]
    return s["width"], s["height"]


# -- argument parsing ------------------------------------------------------------------

def test_parser_defaults_keep_old_behaviour():
    p = cli.build_parser()
    a = p.parse_args(["vod", "u", "--start", "1:00", "--end", "1:30"])
    assert (a.precise, a.vertical, a.focus, a.out) == (False, False, 0.5, "clips")
    a = p.parse_args(["live", "u"])
    assert (a.buffer, a.seconds, a.out, a.vertical, a.auto) == (300, 30, "clips", False, False)
    assert (a.loud, a.chat, a.before, a.after, a.loud_db) == (True, True, 60, 60, 8.0)
    a = p.parse_args(["serve"])
    assert (a.host, a.port, a.data) == ("127.0.0.1", 8000, "data")


def test_parser_new_flags():
    p = cli.build_parser()
    a = p.parse_args(["vod", "u", "--start", "0", "--end", "5", "--precise", "--vertical",
                      "--focus", "0.2"])
    assert a.precise and a.vertical and a.focus == 0.2 and a.fn is cli.cmd_vod
    a = p.parse_args(["live", "u", "--buffer", "600", "--seconds", "45", "--out", "x",
                      "--vertical", "--auto", "--no-loud", "--no-chat", "--before", "30",
                      "--after", "20", "--loud-db", "6"])
    assert (a.buffer, a.seconds, a.out, a.vertical, a.auto) == (600, 45, "x", True, True)
    assert (a.loud, a.chat, a.before, a.after, a.loud_db) == (False, False, 30, 20, 6)
    a = p.parse_args(["vertical", "clip.mp4", "--focus", "1"])
    assert (a.file, a.focus, a.fn) == ("clip.mp4", 1.0, cli.cmd_vertical)


def test_vod_vertical_keeps_only_9x16(tmp_path, monkeypatch, capsys):
    import clipper.vod
    src = make_video(tmp_path / "vod_x.mp4")
    calls = {}

    def fake_clip_vod(url, start, end, out_dir, precise=False):
        calls.update(url=url, start=start, end=end, out=out_dir, precise=precise)
        return src

    monkeypatch.setattr(clipper.vod, "clip_vod", fake_clip_vod)
    cli.main(["vod", "https://youtu.be/x", "--start", "1", "--end", "2", "--out", str(tmp_path),
              "--precise", "--vertical", "--focus", "0"])
    out = Path(capsys.readouterr().out.strip())
    assert calls == dict(url="https://youtu.be/x", start="1", end="2", out=str(tmp_path),
                         precise=True)
    assert out == tmp_path / "vod_x_9x16.mp4" and out.exists()
    assert not src.exists()
    assert video_size(out) == (1080, 1920)


class FakeRecorder:
    def __init__(self, url, work_dir, buffer_seconds=300):
        self.url, self.buffer_seconds = url, buffer_seconds
        self.status, self.error = "recording", None
        self.clips = []
        FakeRecorder.last = self

    def start(self):
        return self

    def clip(self, seconds, out_dir="."):
        self.clips.append(seconds)
        path = Path(out_dir) / f"live_{len(self.clips)}.mp4"
        path.write_bytes(b"x")
        return path

    def stop(self):
        events.append("recorder.stop")

    def discard(self):
        self.stop()


class FakeAuto:
    def __init__(self, rec, out_dir, **kw):
        self.rec, self.out_dir, self.kw = rec, out_dir, kw
        FakeAuto.last = self

    def start(self):
        events.append("auto.start")
        return self

    def stop(self):
        events.append("auto.stop")


events = []


def run_live(monkeypatch, argv, stdin):
    import clipper.auto
    import clipper.edit
    import clipper.live
    events.clear()
    monkeypatch.setattr(clipper.live, "LiveRecorder", FakeRecorder)
    monkeypatch.setattr(clipper.auto, "AutoClipper", FakeAuto)
    monkeypatch.setattr(clipper.edit, "make_vertical",
                        lambda src, focus=0.5: Path(src).with_name(f"{Path(src).stem}_9x16_{focus}.mp4"))
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    cli.main(argv)


def test_live_manual_clips(tmp_path, monkeypatch, capsys):
    run_live(monkeypatch, ["live", "u", "--out", str(tmp_path), "--seconds", "20"], "\n45\nq\n")
    assert FakeRecorder.last.clips == [20, 45]
    assert capsys.readouterr().out.split() == [str(tmp_path / "live_1.mp4"),
                                               str(tmp_path / "live_2.mp4")]
    assert events == ["recorder.stop"]  # no auto-clipper without --auto


def test_live_vertical_and_auto(tmp_path, monkeypatch, capsys):
    run_live(monkeypatch, ["live", "u", "--out", str(tmp_path), "--vertical", "--focus", "0.3",
                           "--auto", "--no-chat", "--before", "10", "--after", "5",
                           "--loud-db", "6"], "\nbogus\n")  # EOF also quits
    out = capsys.readouterr()
    assert out.out.split() == [str(tmp_path / "live_1_9x16_0.3.mp4")]
    assert not (tmp_path / "live_1.mp4").exists()  # horizontal removed
    assert "Clip failed" in out.err  # "bogus" isn't a number
    auto = FakeAuto.last
    assert auto.rec is FakeRecorder.last and auto.out_dir == str(tmp_path)
    assert auto.kw == dict(submit=cli.background_submit, audio=True, chat=False, before=10,
                           after=5, vertical=True, loud_db=6)
    assert events == ["auto.start", "auto.stop", "recorder.stop"]


def test_background_submit_reports_on_stderr(tmp_path, capsys):
    cli.background_submit("auto (loud) 120s", lambda: tmp_path / "auto_1.mp4").join(5)
    assert f"Auto clip saved: {tmp_path / 'auto_1.mp4'}" in capsys.readouterr().err

    def boom():
        raise RuntimeError("no segments")
    cli.background_submit("auto (chat) 120s", boom).join(5)
    assert "Auto clip failed (auto (chat) 120s): no segments" in capsys.readouterr().err


def test_vertical_command(tmp_path):
    src = make_video(tmp_path / "clip.mp4", size="640x360")
    proc = subprocess.run([sys.executable, "-m", "clipper", "vertical", str(src),
                           "--focus", "0.25"], capture_output=True, text=True, check=True)
    out = Path(proc.stdout.strip())
    assert out == tmp_path / "clip_9x16.mp4"
    assert video_size(out) == (1080, 1920)
    assert src.exists()  # the original is kept


def test_vertical_command_missing_file(tmp_path):
    proc = subprocess.run([sys.executable, "-m", "clipper", "vertical", str(tmp_path / "nope.mp4")],
                          capture_output=True, text=True)
    assert proc.returncode != 0 and "no such file" in proc.stderr


# -- macOS app builder --------------------------------------------------------------------

def fake_repo(tmp_path, clipper_stub="#!/bin/sh\n"):
    """A repo dir (with a space in its name) holding the build script and a
    .venv/bin/clipper stub."""
    repo = tmp_path / "my repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(MAKE_APP, repo / "scripts" / MAKE_APP.name)
    if clipper_stub is not None:
        stub = repo / ".venv" / "bin" / "clipper"
        stub.parent.mkdir(parents=True)
        stub.write_text(clipper_stub)
        stub.chmod(0o755)
    return repo


def build_app(repo, app_dir):
    return subprocess.run(["bash", str(repo / "scripts" / MAKE_APP.name)],
                          env={**os.environ, "APP_DIR": str(app_dir)},
                          capture_output=True, text=True, timeout=60)


def test_make_app_bundle(tmp_path):
    repo = fake_repo(tmp_path)
    proc = build_app(repo, tmp_path / "Apps")
    assert proc.returncode == 0, proc.stderr
    app = tmp_path / "Apps" / "Clipper.app"
    assert "Dock" in proc.stdout and "rm -rf" in proc.stdout

    info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleName"] == "Clipper"
    assert info["CFBundleIdentifier"] == "local.clipper"
    assert info["CFBundleExecutable"] == "Clipper"
    assert info["CFBundlePackageType"] == "APPL"

    launcher = app / "Contents" / "MacOS" / "Clipper"
    assert os.access(launcher, os.X_OK)
    text = launcher.read_text()
    assert text.startswith("#!/bin/bash\n")
    assert "REPO=" + str(repo).replace(" ", "\\ ") in text
    assert 'export PATH="/opt/homebrew/bin:/usr/local/bin:' in text
    assert "Library/Logs/Clipper.log" in text and "nohup" in text
    subprocess.run(["bash", "-n", str(launcher)], check=True)

    # Rebuilding replaces the bundle in place.
    assert build_app(repo, tmp_path / "Apps").returncode == 0


def test_make_app_refuses_without_venv(tmp_path):
    repo = fake_repo(tmp_path, clipper_stub=None)
    proc = build_app(repo, tmp_path / "Apps")
    assert proc.returncode == 1
    assert "python3 -m venv .venv" in proc.stderr and "pip install -e ." in proc.stderr
    assert not (tmp_path / "Apps").exists()


def test_make_app_wont_clobber_other_apps(tmp_path):
    repo = fake_repo(tmp_path)
    other = tmp_path / "Apps" / "Clipper.app" / "Contents"
    other.mkdir(parents=True)
    (other / "Info.plist").write_text("<string>com.someone.else</string>")
    proc = build_app(repo, tmp_path / "Apps")
    assert proc.returncode == 1 and other.exists()


# The launcher itself, with stub `open`/`osascript` and a stub clipper that
# serves /api/jobs with python's http.server.

STUB_SERVER = """#!/bin/sh
echo "$@" >> "$STUB_LOG/clipper-args"
echo $$ > "$STUB_LOG/server.pid"
cd "$STUB_LOG/www" && exec {python} -m http.server "$CLIPPER_PORT" --bind 127.0.0.1
"""


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def launcher_env(tmp_path):
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name in ("open", "osascript"):
        (stubs / name).write_text(f'#!/bin/sh\necho "$@" >> "$STUB_LOG/{name}"\n')
        (stubs / name).chmod(0o755)
    (tmp_path / "www" / "api").mkdir(parents=True)
    (tmp_path / "www" / "api" / "jobs").write_text("[]")
    return {**os.environ, "PATH": f"{stubs}:{os.environ['PATH']}", "HOME": str(tmp_path / "home"),
            "STUB_LOG": str(tmp_path), "CLIPPER_PORT": str(free_port())}


@pytest.mark.skipif(not shutil.which("curl"), reason="needs curl")
def test_launcher_starts_server_once_and_opens_page(tmp_path):
    repo = fake_repo(tmp_path, STUB_SERVER.format(python=sys.executable))
    assert build_app(repo, tmp_path / "Apps").returncode == 0
    launcher = tmp_path / "Apps" / "Clipper.app" / "Contents" / "MacOS" / "Clipper"
    env = launcher_env(tmp_path)
    port = env["CLIPPER_PORT"]
    try:
        t0 = time.time()
        subprocess.run([str(launcher)], env=env, check=True, timeout=30)
        assert time.time() - t0 < 15
        assert (tmp_path / "open").read_text().split() == [f"http://localhost:{port}"]
        args = (tmp_path / "clipper-args").read_text().splitlines()
        assert args == [f"serve --host 127.0.0.1 --port {port} --data {repo}/data"]
        assert (tmp_path / "home" / "Library" / "Logs" / "Clipper.log").exists()

        # Second launch: server already answers, so just reopen the page.
        subprocess.run([str(launcher)], env=env, check=True, timeout=30)
        assert len((tmp_path / "open").read_text().splitlines()) == 2
        assert len((tmp_path / "clipper-args").read_text().splitlines()) == 1
        assert not (tmp_path / "osascript").exists()
    finally:
        pid = tmp_path / "server.pid"
        if pid.exists():
            os.kill(int(pid.read_text()), 15)


@pytest.mark.skipif(not shutil.which("curl"), reason="needs curl")
def test_launcher_alerts_when_server_dies(tmp_path):
    repo = fake_repo(tmp_path, "#!/bin/sh\necho 'boom: no ffmpeg' >&2\nexit 1\n")
    assert build_app(repo, tmp_path / "Apps").returncode == 0
    launcher = tmp_path / "Apps" / "Clipper.app" / "Contents" / "MacOS" / "Clipper"
    proc = subprocess.run([str(launcher)], env=launcher_env(tmp_path), timeout=30)
    assert proc.returncode == 1
    assert "Clipper.log" in (tmp_path / "osascript").read_text()
    assert not (tmp_path / "open").exists()
    log = tmp_path / "home" / "Library" / "Logs" / "Clipper.log"
    assert "boom: no ffmpeg" in log.read_text()
