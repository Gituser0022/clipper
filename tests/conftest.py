import pytest


@pytest.fixture(autouse=True)
def clipper_home(tmp_path, monkeypatch):
    """Point every Clipper folder at a temporary home so tests never touch the real one."""
    import clipper.server as server
    from clipper import remote
    # Starlette's TestClient calls itself "testclient" at "testserver": count it as the Mac.
    monkeypatch.setattr(remote, "LOCAL_CLIENTS", remote.LOCAL_CLIENTS | {"testclient"})
    monkeypatch.setattr(remote, "LOCAL_HOST_NAMES", remote.LOCAL_HOST_NAMES | {"testserver"})
    home = tmp_path / "home"
    monkeypatch.setenv("CLIPPER_HOME", str(home))
    monkeypatch.setattr(server, "HOME", home)
    for name, folder in (("CLIPS_DIR", "Clips"), ("HIGHLIGHTS_DIR", "Highlights"),
                         ("DOWNLOADS_DIR", "Downloads"), ("LOGS_DIR", "Logs"), ("WORK_DIR", ".work")):
        monkeypatch.setattr(server, name, home / folder)
    server._storage_cache.clear()
    saved_jobs = dict(server.jobs)
    server.jobs.clear()
    yield home
    server.jobs.clear()
    server.jobs.update(saved_jobs)
    server._storage_cache.clear()
