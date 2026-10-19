import time

import pytest
from fastapi.testclient import TestClient

import clipper.server as server
from clipper import remote, storage

PHONE = ("192.168.1.50", 5555)


@pytest.fixture
def mac():
    return TestClient(server.app)


def phone():
    return TestClient(server.app, client=PHONE, follow_redirects=False)


def connect(mac):
    """Turn Phone & iPad on from the Mac and return a phone that has scanned the code."""
    info = mac.put("/api/remote", json={"enabled": True}).json()
    key = storage.load_settings(server.HOME)["remote_key"]
    p = phone()
    r = p.get(f"/?key={key}")
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert remote.COOKIE in r.cookies
    return p, info


def test_host_name_parsing():
    assert remote.host_name("localhost:8000") == "localhost"
    assert remote.host_name("[::1]:8000") == "[::1]"
    assert remote.host_name("Mac.local") == "mac.local"


def test_phone_is_refused_until_connected(mac):
    p = phone()
    r = p.get("/api/state")
    assert r.status_code == 401 and "Scan the QR code" in r.json()["detail"]
    page = p.get("/")
    assert page.status_code == 401 and "Connect this device" in page.text
    # A key is useless while Phone & iPad is off, even the right one.
    storage.save_settings(server.HOME, {**storage.load_settings(server.HOME),
                                        "remote_key": "k" * 20, "remote_enabled": False})
    assert p.get("/?key=" + "k" * 20).status_code == 401


def test_connected_phone(mac, tmp_path):
    p, info = connect(mac)
    assert info["local"] and info["enabled"]
    assert info["qr_svg"] is None or "<svg" in info["qr_svg"]
    assert all("key=" in u for u in info["urls"])
    state = p.get("/api/state").json()
    assert state["client"] == {"local": False}
    assert mac.get("/api/state").json()["client"] == {"local": True}
    assert p.get("/api/remote").json() == {"local": False, "enabled": True, "after_phone_save": "bin"}
    assert p.get("/").status_code == 200
    assert p.get("/?key=wrong").status_code == 200  # a wrong key is ignored, not stored...
    assert p.get("/api/state").status_code == 200   # ...so the device stays connected
    assert phone().get("/?key=wrong").status_code == 401  # and it opens nothing on its own

    # Things only the Mac may do.
    assert p.post("/api/quit").status_code == 403
    assert p.post("/api/storage/open", json={}).status_code == 403
    assert p.put("/api/remote", json={"enabled": False}).status_code == 403
    assert p.post("/api/remote/new-link").status_code == 403
    assert p.get("/api/inspect", params={"url": "file:///etc/passwd"}).status_code == 400
    assert p.post("/api/highlights", json={"url": "file:///etc/passwd"}).status_code == 400

    # A new link signs every phone out.
    mac.post("/api/remote/new-link")
    assert p.get("/api/state").status_code == 401


def test_turning_off_signs_phones_out(mac):
    p, _ = connect(mac)
    mac.put("/api/remote", json={"enabled": False})
    assert p.get("/api/state").status_code == 401
    assert mac.get("/api/remote").json()["enabled"] is False


def test_other_sites_cannot_use_the_macs_browser(mac):
    r = mac.get("/api/state", headers={"host": "evil.example:8000"})
    assert r.status_code == 403  # DNS rebinding: right address, wrong name


@pytest.mark.parametrize("what,kept", [("delete", False), ("keep", True), ("bin", False)])
def test_saving_to_phone_removes_the_macs_copy(mac, monkeypatch, clipper_home, what, kept):
    binned = []
    monkeypatch.setattr(server, "_move_to_bin", lambda path: binned.append(path.name))
    monkeypatch.setattr(server.sys, "platform", "darwin")
    p, _ = connect(mac)
    assert mac.put("/api/remote/after-save", json={"after_phone_save": what}).json()["after_phone_save"] == what
    clip = clipper_home / "Clips" / "live_1.mp4"
    clip.parent.mkdir(parents=True, exist_ok=True)
    clip.write_bytes(b"\0" * 5000)
    r = p.get("/clips/live_1.mp4?save=1")
    assert r.status_code == 200 and len(r.content) == 5000
    assert "attachment" in r.headers["content-disposition"]
    time.sleep(0.2)
    assert clip.exists() is kept
    assert binned == (["live_1.mp4"] if what == "bin" else [])


def test_mac_downloads_and_plain_views_never_remove(mac, clipper_home):
    storage.save_settings(server.HOME, {**storage.load_settings(server.HOME), "after_phone_save": "delete"})
    clip = clipper_home / "Clips" / "live_2.mp4"
    clip.parent.mkdir(parents=True, exist_ok=True)
    clip.write_bytes(b"\0" * 100)
    assert mac.get("/clips/live_2.mp4?save=1").status_code == 200
    p, _ = connect(mac)
    assert p.get("/clips/live_2.mp4").status_code == 200  # watching, not saving
    assert clip.exists()


def test_delete_moves_to_bin_on_a_mac(mac, monkeypatch, clipper_home):
    clip = clipper_home / "Clips" / "vod_1.mp4"
    clip.parent.mkdir(parents=True, exist_ok=True)
    clip.write_bytes(b"\0" * 100)
    monkeypatch.setattr(server.sys, "platform", "darwin")

    def refuse(path):
        raise RuntimeError("macOS didn't let Clipper use the Bin.")
    monkeypatch.setattr(server, "_move_to_bin", refuse)
    r = mac.delete("/api/clips/vod_1.mp4")
    assert r.status_code == 400 and "Bin" in r.json()["detail"] and clip.exists()

    monkeypatch.setattr(server, "_move_to_bin", lambda path: path.rename(clipper_home / "Bin.mp4"))
    r = mac.delete("/api/clips/vod_1.mp4").json()
    assert r["trashed"] is True and r["can_trash"] is True and not clip.exists()

    monkeypatch.setattr(server.sys, "platform", "linux")
    other = clipper_home / "Clips" / "vod_2.mp4"
    other.write_bytes(b"\0")
    r = mac.delete("/api/clips/vod_2.mp4").json()
    assert r["trashed"] is False and r["can_trash"] is False and not other.exists()
