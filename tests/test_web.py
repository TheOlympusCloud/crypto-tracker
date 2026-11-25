import importlib

import pytest
from fastapi.testclient import TestClient

SATOSHI = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"


@pytest.fixture
def site(db, tmp_path, monkeypatch):
    """The server as the Docker image runs it: the web app's static build on /, the API on
    /api/tracker/. Routes are set up at import, so the module is imported again both ways."""
    out = tmp_path / "out"
    (out / "tracker").mkdir(parents=True)
    (out / "index.html").write_text("am-i-exposed home")
    (out / "tracker" / "index.html").write_text("tracker page")
    (out / "sw.js").write_text("// worker")
    monkeypatch.setenv("CT_WEB", str(out))
    import tracker.app as server

    yield TestClient(importlib.reload(server).app, base_url="http://localhost")
    monkeypatch.delenv("CT_WEB")
    importlib.reload(server)


def test_the_web_app_in_front_and_the_api_under_its_own_path(site, monkeypatch):
    assert site.get("/").text == "am-i-exposed home"
    assert site.get("/tracker/").text == "tracker page"
    assert site.post("/api/tracker/wallets", json={"address": SATOSHI, "name": "cold"}).is_success
    assert site.get("/api/tracker/graph").json()["nodes"][0]["address"] == SATOSHI
    # /api/ is the web app's: it reads a 404 there as "no local mempool".
    assert site.get("/api/graph").status_code == 404
    assert site.get("/api/local-info").status_code == 404

    # One password guards the pages and the API.
    monkeypatch.setenv("CT_PASSWORD", "s3cret")
    paths = ("/", "/tracker/", "/api/tracker/graph")
    assert {site.get(p).status_code for p in paths} == {401}
    assert site.get("/", auth=("any", "s3cret")).status_code == 200
    assert {site.get(p).status_code for p in paths} == {200}  # the cookie it earned


def test_an_update_reaches_the_browser_at_once(site):
    """The page's files are rechecked on every load (a 304 when unchanged), so a browser or an
    installed app never keeps running the code from before an update."""
    for path in ("/", "/tracker/", "/sw.js"):
        assert site.get(path).headers["cache-control"] == "no-cache", path
    tag = site.get("/sw.js").headers["etag"]
    assert site.get("/sw.js", headers={"if-none-match": tag}).status_code == 304


def test_without_a_build_only_the_api_answers(db):
    from tracker.app import app

    client = TestClient(app, base_url="http://localhost")
    assert client.get("/api/tracker/graph").is_success
    assert client.get("/").status_code == 404
