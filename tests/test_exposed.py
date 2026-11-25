import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SATOSHI = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"


@pytest.fixture
def site(db, tmp_path, monkeypatch):
    """The server as the Docker image runs it: am-i-exposed's build in front, this app
    under /tracker/. The app is mounted at import, so it is imported again both ways."""
    out = tmp_path / "out"
    (out / "graph").mkdir(parents=True)
    (out / "index.html").write_text("am-i-exposed home")
    (out / "graph" / "index.html").write_text("am-i-exposed graph")
    monkeypatch.setenv("CT_EXPOSED", str(out))
    import tracker.app as server

    yield TestClient(importlib.reload(server).app, base_url="http://localhost")
    monkeypatch.delenv("CT_EXPOSED")
    importlib.reload(server)


def test_am_i_exposed_in_front_and_the_tracker_under_its_own_path(site, monkeypatch):
    assert site.get("/").text == "am-i-exposed home"
    assert site.get("/graph/").text == "am-i-exposed graph"
    assert "<title>Crypto tracker</title>" in site.get("/tracker/").text
    assert site.get("/tracker/sw.js").status_code == 200
    moved = site.get("/tracker", follow_redirects=False)
    assert moved.is_redirect and moved.headers["location"] == "tracker/"
    # The tracker's pages call api/ relative to themselves.
    assert site.post("/tracker/api/wallets", json={"address": SATOSHI, "name": "cold"}).is_success
    assert site.get("/tracker/api/graph").json()["nodes"][0]["address"] == SATOSHI
    assert site.get("/tracker/api/analysis").json()["exposed"] is True
    assert site.get("/api/graph").status_code == 404  # am-i-exposed's, for a local mempool

    # One password guards both apps.
    monkeypatch.setenv("CT_PASSWORD", "s3cret")
    paths = ("/", "/graph/", "/tracker/", "/tracker/api/graph")
    assert {site.get(p).status_code for p in paths} == {401}
    assert site.get("/", auth=("any", "s3cret")).status_code == 200
    assert {site.get(p).status_code for p in paths} == {200}  # the cookie it earned


def test_without_am_i_exposed_the_tracker_stays_in_front(db):
    from tracker.app import app

    client = TestClient(app, base_url="http://localhost")
    assert "<title>Crypto tracker</title>" in client.get("/").text
    assert client.get("/api/analysis").json()["exposed"] is False


def test_the_am_i_exposed_patches_leave_build_output_alone():
    """The build rewrites public/sitemap.xml; a patch holding it fails on the next release."""
    patches = Path(__file__).parent.parent / "patches" / "am-i-exposed"
    touched = {
        line.split(" b/")[-1]
        for patch in patches.glob("*.patch")
        for line in patch.read_text().splitlines()
        if line.startswith("diff --git ")
    }
    assert touched and "public/sitemap.xml" not in touched
