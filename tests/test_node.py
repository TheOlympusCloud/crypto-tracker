"""A Bitcoin node of your own (tracker/node.py): the tracker and am-i-exposed use it, and wait
for it while it syncs instead of asking a public server."""

import importlib
import json
import os
import subprocess
import sys
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from test_core import BIP84, SATOSHI, A, mine

from tracker import chains, graph, history, node, swaps, sync

OWN = "http://mempool-web:8080"
TIP = "ab" * 32


class Mempool:
    """A mempool on an Electrum server, as the `mempool` profile runs it: no /txs/chain, but
    /txs?after_txid=, 10 a page, pending transactions first. Records every URL asked."""

    def __init__(self, confirmed=35, pending=2):
        sender = "12c6DSiU4Rq3P4ZxziKxzrL5LmMBrzjrJX"

        def tx(i, height):
            status = {"confirmed": True, "block_height": height, "block_time": 1_600_000_000 + i}
            return {
                "txid": f"t{i:05d}",
                "vin": [{"prevout": {"scriptpubkey_address": sender, "value": 1000}}],
                "vout": [{"scriptpubkey_address": SATOSHI, "value": 1000}],
                "status": status if height else {"confirmed": False},
            }

        self.txs = [tx(90_000 + i, 0) for i in range(pending)]
        self.txs += [tx(i, 100_000 - i) for i in range(confirmed)]
        self.tip_age, self.index, self.up = 600, True, True
        self.asked = []

    def __call__(self, request):
        self.asked.append(request)
        path = request.url.path
        if not self.up:
            raise httpx.ConnectError("connection refused")
        if path == "/api/blocks/tip/hash":
            return httpx.Response(200, text=TIP)
        if path == f"/api/block/{TIP}":
            return httpx.Response(200, json={"height": 925_000, "timestamp": self.when()})
        if path == f"/api/address/{node.PROBE}" and not self.index:
            return httpx.Response(500, text="Failed to get address")
        if path in (f"/api/address/{SATOSHI}", f"/api/address/{node.PROBE}"):
            confirmed = [t for t in self.txs if t["status"]["confirmed"]]
            stats = {"funded_txo_sum": 1000 * len(confirmed), "spent_txo_sum": 0}
            return httpx.Response(200, json={"chain_stats": stats | {"tx_count": len(confirmed)}})
        if path == f"/api/address/{SATOSHI}/txs":
            after = request.url.params.get("after_txid")
            ids = [t["txid"] for t in self.txs]
            start = ids.index(after) + 1 if after else 0
            return httpx.Response(200, json=self.txs[start : start + 10])
        return httpx.Response(404, text="Not Found")  # /txs/chain, among others

    def when(self):
        return int(time.time()) - self.tip_age


@pytest.fixture
def own(db, monkeypatch):
    """Bitcoin read from a mempool of your own, faked. An address sent anywhere else fails the
    test; other requests (prices) get a 404."""
    fake = Mempool()

    def only_mine(request):
        if request.url.host == "mempool-web":
            return fake(request)
        assert SATOSHI not in str(request.url) + request.content.decode(), request.url
        return httpx.Response(404)

    real, transport = httpx.Client, httpx.MockTransport(only_mine)
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=transport, **kw))
    monkeypatch.setattr(node, "MEMPOOL", OWN)
    monkeypatch.setattr(node, "TTL", -1)  # asked again every time
    monkeypatch.setitem(chains.UTXO, "bitcoin", ("BTC", "esplora", f"{OWN}/api", ""))
    monkeypatch.setattr(chains, "ELECTRUM", set())
    monkeypatch.setattr(chains, "DELAY", 0)
    fake.client = lambda: real(transport=transport)
    return fake


def settings(**env):
    """node's and chains' settings as a server started with `env` reads them."""
    code = (
        "import json; from tracker import chains, node; "
        "print(json.dumps([node.MEMPOOL, node.SITE, chains.ESPLORA]))"
    )
    keep = {k: v for k, v in os.environ.items() if not k.startswith("CT_")}
    done = subprocess.run(
        [sys.executable, "-c", code], env={**keep, **env}, capture_output=True, text=True,
        check=True, cwd=os.path.dirname(os.path.dirname(__file__)),
    )  # fmt: skip
    return json.loads(done.stdout)


def test_the_profile_or_an_address_turns_your_own_node_on():
    assert settings() == ["", "", "https://blockstream.info/api"]
    # COMPOSE_PROFILES=mempool in .env: compose.yaml hands it on as CT_PROFILES.
    assert settings(CT_PROFILES="mempool") == [OWN, "/api/tracker/mempool", f"{OWN}/api"]
    assert settings(CT_PROFILES="other, mempool")[0] == OWN
    # One on your network: its address, which the browser opens as it is.
    lan = "http://umbrel.local:3006"
    assert settings(CT_MEMPOOL=lan + "/") == [lan, lan, f"{lan}/api"]
    # CT_ESPLORA set by hand still decides where the tracker reads Bitcoin.
    assert settings(CT_PROFILES="mempool", CT_ESPLORA="https://x/api")[2] == "https://x/api"


def test_a_mempool_on_electrum_is_paged_by_after_txid(own):
    with own.client() as client:
        data = chains.fetch_utxo(client, SATOSHI)
    txids = {t[1] for t in data["transfers"]}
    assert len(txids) == 35 and not any(t.startswith("t9") for t in txids)  # pending left out
    assert (data["count"], data["hub"], data["balances"][0][3]) == (35, False, 35_000 / 1e8)
    assert chains.ELECTRUM == {f"{OWN}/api"}  # /txs/chain is not asked again
    with own.client() as client:
        chains.fetch_utxo(client, SATOSHI)
    assert sum("/txs/chain" in r.url.path for r in own.asked) == 1


def test_my_wallet_is_read_in_full_from_it(db, own, monkeypatch):
    monkeypatch.setattr(chains, "BTC_PAGES", 1)  # the plain read: 25 of them
    monkeypatch.setattr(history, "BUDGET", {"default": 3})  # 30 more per sync
    mine(db, SATOSHI)
    db.commit()
    with own.client() as client:
        sync.sync_address(db, client, SATOSHI)
        assert not history.state(db, SATOSHI)["complete"]
        sync.sync_address(db, client, SATOSHI)
    assert history.state(db, SATOSHI)["complete"]
    assert db.execute("SELECT COUNT(DISTINCT txid) FROM transfer").fetchone()[0] == 35
    assert db.execute("SELECT error FROM node").fetchone()[0] is None


def test_where_the_node_stands(own):
    assert node.status() == {"state": "ready", "height": 925_000, "time": own.when()}
    own.index = False
    assert node.status()["state"] == "indexing"
    assert "address index (Fulcrum) is still being built" in node.notes()[0]
    own.tip_age = 400 * 86400
    found = node.status()
    assert found["state"] == "syncing"
    said = node.notice(found)
    assert "still syncing" in said and "block 925,000" in said and "never from a public" in said
    assert time.strftime("%Y", time.gmtime(own.when())) in said
    own.up = False
    assert node.status() == {"state": "down", "height": None, "time": None}


def test_no_node_no_status(monkeypatch):
    monkeypatch.setattr(node, "MEMPOOL", "")
    assert node.status() is None and node.notes() == []
    node.guard("https://blockstream.info/api")  # nothing to wait for


def test_while_it_syncs_bitcoin_waits_and_the_app_says_so(db, own):
    """Fail closed: no address goes anywhere until the node is ready, and every view says why."""
    from tracker.app import app

    own.tip_age = 400 * 86400
    mine(db, SATOSHI)
    db.commit()
    with own.client() as client:
        sync.sync_address(db, client, SATOSHI)
    said = db.execute("SELECT error FROM node").fetchone()[0]
    assert "still syncing" in said and said.endswith("never from a public server meanwhile.")
    assert not [r for r in own.asked if SATOSHI in r.url.path]  # never asked, not even mine

    web = TestClient(app, base_url="http://localhost")
    assert "still syncing" in " ".join(web.get("/api/tracker/sync").json()["notes"])
    looked = web.get(f"/api/tracker/lookup/{SATOSHI}")
    assert looked.status_code == 502 and looked.json()["detail"].endswith(said)
    assert not [r for r in own.asked if SATOSHI in r.url.path]

    own.tip_age = 600  # caught up: read as usual, and nothing more to say
    assert web.get("/api/tracker/sync").json()["notes"] == []
    assert web.get(f"/api/tracker/lookup/{SATOSHI}").is_success


@pytest.fixture
def site(own, monkeypatch):
    """The server with a mempool of its own (the profile's, published on port 8080)."""
    import tracker.app as server

    monkeypatch.setattr(node, "PORT", "8080")
    monkeypatch.setattr(node, "SITE", "/api/tracker/mempool")
    real, transport = httpx.AsyncClient, httpx.MockTransport(own)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))
    yield TestClient(importlib.reload(server).app, base_url="http://localhost")
    monkeypatch.undo()
    importlib.reload(server)


def test_am_i_exposed_asks_my_node_through_the_app(site, own):
    info = site.get("/api/local-info").json()
    # A mempool of the host's own, its site on port 8080, and no Tor sidecar to claim.
    assert info["mempoolPort"] == "8080" and not info["mempoolExternalUrl"]
    assert info["torProxy"] is False

    r = site.get(
        f"/api/address/{SATOSHI}/txs?after_txid=t00003",
        headers={"cookie": "ct_session=secret", "authorization": "Basic eDp5"},
    )
    assert r.is_success and r.json()[0]["txid"] == "t00004"
    sent = own.asked[-1]
    assert str(sent.url) == f"{OWN}/api/address/{SATOSHI}/txs?after_txid=t00003"
    assert "cookie" not in sent.headers and "authorization" not in sent.headers  # ours only

    own.asked.clear()
    assert site.post("/api/tx", content="0200", headers={"content-type": "text/plain"})
    assert own.asked[-1].method == "POST" and own.asked[-1].content == b"0200"
    # mempool's own web server would pass these on to mempool.space
    assert site.get("/api/v1/services/accelerator/accelerations").status_code == 404
    assert not [r for r in own.asked if "services" in r.url.path]
    # Nor around that refusal, nor out of /api/.
    for sneaky in ("/api/v1//services/x", "/api/v1/%2e/services/x", "/api/x/%2e%2e/%2e%2e/out"):
        assert site.get(sneaky).status_code == 404, sneaky
    assert not [r for r in own.asked if "services" in r.url.path or "out" in r.url.path]
    assert site.get("/api/tracker/graph").is_success  # the tracker's API is still its own


def test_am_i_exposed_hears_the_node_is_not_ready(site, own, monkeypatch):
    own.tip_age = 400 * 86400
    r = site.get("/api/blocks/tip/height")  # am-i-exposed's health probe
    assert r.status_code == 503 and "still syncing" in r.text
    own.up = False
    assert site.get(f"/api/address/{SATOSHI}").status_code == 503
    # One password guards it like the rest of the app.
    monkeypatch.setenv("CT_PASSWORD", "s3cret")
    assert site.get("/api/local-info").status_code == 401


def test_explorer_links_open_my_own_mempool(site):
    covered = site.get("/api/tracker/analysis").json()["chains"]
    bitcoin = next(c for c in covered if c["chain"] == "bitcoin")
    assert bitcoin["note"] == "Read from your own node."
    assert bitcoin["explorer_address"] == "/api/tracker/mempool/address/{}"
    r = site.get(f"/api/tracker/mempool/address/{SATOSHI}", follow_redirects=False)
    assert r.headers["location"] == f"http://localhost:8080/address/{SATOSHI}"
    assert site.get("/api/tracker/mempool/block/x", follow_redirects=False).status_code == 404
    v6 = TestClient(site.app, base_url="http://[::1]")
    r = v6.get(f"/api/tracker/mempool/tx/{'cd' * 32}", follow_redirects=False)
    assert r.headers["location"] == f"http://[::1]:8080/tx/{'cd' * 32}"


def test_with_a_node_my_bitcoin_addresses_go_to_no_swap_api(db, monkeypatch):
    """THORChain and Maya are asked about my other addresses only. A swap between a Bitcoin
    wallet and another of mine is still found from the other side, and the verdict says so."""
    monkeypatch.setattr(node, "MEMPOOL", OWN)
    monkeypatch.setattr(chains, "DELAY", 0)
    mine(db, A)
    mine(db, BIP84[0])
    db.commit()
    leg = {"coins": [{"amount": "100000", "asset": "BTC.BTC"}]}
    swap = {
        "type": "swap", "status": "success",
        "in": [{**leg, "address": A, "txID": "D48A"}],
        "out": [{**leg, "address": BIP84[0], "txID": "736CD5"}],
    }  # fmt: skip
    asked = []

    def midgard(request):
        asked.append(request.url.params["address"])
        return httpx.Response(200, json={"actions": [swap] if asked[-1] == A else []})

    with httpx.Client(transport=httpx.MockTransport(midgard)) as client:
        assert swaps.sync(db, client) == []
    assert A in asked and BIP84[0] not in asked
    found = {(r["kind"], frozenset((r["a"], r["b"]))) for r in db.execute("SELECT * FROM link")}
    assert found == {("swap", frozenset((A, BIP84[0])))}  # from both protocols' records
    notes = {v["address"]: v["coverage"]["notes"] for v in graph.verdicts(db)}
    assert swaps.OWN_NODE in notes[BIP84[0]] and swaps.OWN_NODE not in notes[A]

    monkeypatch.setattr(node, "MEMPOOL", "")  # no node: asked about as before
    asked.clear()
    with httpx.Client(transport=httpx.MockTransport(midgard)) as client:
        swaps.sync(db, client)
    assert BIP84[0] in asked
