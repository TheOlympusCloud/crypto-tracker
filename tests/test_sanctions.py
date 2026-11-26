"""Sanctions lists and stablecoin freezes as flagged labels (docs/design/multichain.md, M8),
from recorded answers of 0xB10C's lists, Routescan's logs and TronGrid's events."""

import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from test_core import A, B, D, mine, transfer
from test_lookup import client as indexers  # noqa: F401 (a fixture: the app over fake indexers)
from test_lookup import indexers as fake_indexers

from tracker import chains, graph, labels

REAL = httpx.Client
RECORDED = json.loads((Path(__file__).parent / "recorded/sanctions.json").read_text())
ETH_SANCTIONED = "0x0330070fd38ec3bb94f58fa55d40368271e9e54a"  # from the list in mixed case
TRX_SANCTIONED = "TA3rH2A7iHnm6pKH8gr9cK1EZnShnmZdFg"
SOL_SANCTIONED = "42RLPACwZPx3vYYmxSueqsogfynBDqXK298EDsNoyoHi"
HEAD = 0x194479E  # Ethereum's newest block when the logs were recorded
SET = ("ofac-sanctions", "freezes-ethereum", "freezes-tron")


class Internet:
    """The recorded lists and events, behind the test's fake indexers. `wide`: blocks above
    which a getLogs query times out, as Routescan's does."""

    def __init__(self, ofac=None, wide=10**9, broken=()):
        self.ofac, self.wide, self.broken, self.asked = ofac or RECORDED["ofac"], wide, broken, []

    def __call__(self, request):
        url, q = request.url, parse_qs(request.url.query.decode())
        self.asked.append(request)
        if url.host in self.broken:
            return httpx.Response(404)
        if url.host == "raw.githubusercontent.com" and "/0xB10C/" in url.path:
            currency = url.path.rsplit("_", 1)[1].removesuffix(".txt")
            return httpx.Response(200, text=self.ofac.get(currency, ""))
        if url.host == "eth" and q.get("module") == ["proxy"]:
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": hex(HEAD)})
        if url.host == "eth" and q.get("module") == ["logs"]:
            low, high = int(q["fromBlock"][0]), int(q["toBlock"][0])
            if high - low + 1 > self.wide:
                message = "Timeout reached, please use a narrower block range"
                return httpx.Response(200, json={"status": "0", "message": message, "result": None})
            rows = [
                r
                for r in RECORDED["ethereum"][q["topic0"][0]]
                if r["address"] == q["address"][0].lower()
                and low <= int(r["blockNumber"], 16) <= high
            ][: int(q["offset"][0])]
            if not rows:
                return httpx.Response(200, json={"status": "0", "message": "No records found", "result": []})  # fmt: skip
            return httpx.Response(200, json={"status": "1", "message": "OK", "result": rows})
        if url.host == "api.trongrid.io" and "/events" in url.path:
            contract = url.path.split("/")[3]
            rows = [r for r in RECORDED["tron"][q["event_name"][0]] if r["contract_address"] == contract]  # fmt: skip
            start, size = int(q.get("fingerprint", ["0"])[0]), int(q["limit"][0])
            page = rows[start : start + size]
            meta = {"fingerprint": str(start + size)} if start + size < len(rows) else {}
            return httpx.Response(200, json={"data": page, "success": True, "meta": meta})
        if url.host == "api.trongrid.io":
            return httpx.Response(200, json={"data": []})  # an address with nothing on it
        if url.host in ("raw.githubusercontent.com", "codeload.github.com"):
            return httpx.Response(404)  # the other label sets
        return fake_indexers(request)


@pytest.fixture
def internet(indexers, monkeypatch):  # noqa: F811
    """The app (test_lookup's client) with the recorded lists and events behind it."""
    net = Internet()
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: REAL(transport=httpx.MockTransport(net), **kw)
    )
    monkeypatch.setattr(labels, "TRON_PAUSE", 0)
    return indexers, net


def load(db, net, everything=True):
    with REAL(transport=httpx.MockTransport(net)) as http:
        return labels.import_open(db, http, everything=everything)


def flagged(db):
    return {
        r["address"]: (r["name"], r["type"], r["source"])
        for r in db.execute("SELECT * FROM label WHERE category = 'flagged'")
    }


def test_a_sanctioned_eth_trx_and_sol_address_shows_as_flagged(db, internet):
    app, net = internet
    _, failed = load(db, net)
    assert not set(SET) & set(failed)
    for address, chain in (
        (ETH_SANCTIONED, "ethereum"),
        (TRX_SANCTIONED, ""),
        (SOL_SANCTIONED, ""),
    ):
        assert flagged(db)[address] == ("Sanctioned by OFAC", "sanctioned (OFAC)", "ofac-sanctions")
        query = f"?chains={chain}" if chain else ""
        risk = app.get(f"/api/tracker/lookup/{address}{query}").json()["risk"]
        assert (risk["level"], risk["headline"]) == ("bad", "Risky to send to")
        assert "Sanctioned by OFAC" in risk["reason"]
        scan = app.get(f"/api/tracker/exposure/{address}{query}").json()
        (first, *_) = scan["findings"]
        assert (first["id"], first["severity"], first["params"]) == (
            "x-flagged-address",
            "critical",
            {"name": "Sanctioned by OFAC"},
        )
    # Every list of a chain the tracker reads, in its canonical form; a Tron address on the
    # USDT list too.
    got = flagged(db)
    assert "bitcoincash:qpf2cphc5dkuclkqur7lhj2yuqq9pk3hmukle77vhq" in got
    assert {"123WBUDmSJv4GctdVEz6Qq6z8nXSKrJ4KX", "LNf2JDiuunBz7GMDKFYHN4rq5meXWxiwfb"} <= set(got)
    assert {"TA3941uFAvmVibSkQ6fMJXxmaSNovX86mz", "0x4f47bc496083c727c5fbe3ce9cdf2b0f6496270c"} <= set(got)  # fmt: skip


def test_freezes_come_from_the_events_and_an_unfreeze_lifts_them(db, internet, monkeypatch):
    _, net = internet
    net.wide = 1_000_000  # wider getLogs queries time out: the window is halved until they answer
    monkeypatch.setattr(labels, "LOG_ROWS", 2)  # and a full page is read on from its last block
    monkeypatch.setattr(chains, "TRON_ROWS", 1)  # TronGrid's pages, one event each
    _, failed = load(db, net)
    assert not set(SET) & set(failed)
    tether = ("Frozen by Tether (USDT)", "frozen by Tether")
    circle = ("Frozen by Circle (USDC)", "frozen by Circle")
    assert {a: v for a, v in flagged(db).items() if v[2] != "ofac-sanctions"} == {
        "0x008fe40574e881e7247b50b991c0cc057d66647f": (*tether, "freezes-ethereum"),
        "0xaa05f7c7eb9af63d6cc03c36c4f4ef6c37431ee0": (*circle, "freezes-ethereum"),
        "TVE2XCeDRqbLQQQhbcp6g6vit52zLgYzzT": (*tether, "freezes-tron"),
        "TMRsDN3kDnqs7oa6QjUmE1xHzeo7PYcRVs": (*circle, "freezes-tron"),
    }  # 0x9faf55... on Ethereum and TWjD1H... on Tron were frozen, then unfrozen
    logs = [parse_qs(r.url.query.decode()) for r in net.asked if "getLogs" in str(r.url)]
    answered = [q for q in logs if int(q["toBlock"][0]) - int(q["fromBlock"][0]) < 1_000_000]
    assert len(answered) < len(logs) and max(int(q["toBlock"][0]) for q in logs) == HEAD


def test_a_list_read_again_drops_who_left_it_and_a_failed_read_keeps_it(db, internet):
    _, net = internet
    load(db, net)
    assert ETH_SANCTIONED in flagged(db)
    net.ofac = {**RECORDED["ofac"], "ETH": "0x038989cbb1710c72b9920dc4fa529158f463e72c\n"}
    net.broken = ("api.trongrid.io",)
    _, failed = load(db, net)
    assert "freezes-tron" in failed and not {"ofac-sanctions", "freezes-ethereum"} & set(failed)
    assert ETH_SANCTIONED not in flagged(db)  # delisted
    assert "TVE2XCeDRqbLQQQhbcp6g6vit52zLgYzzT" in flagged(db)  # the old copy stays


def test_sanctions_outrank_the_open_sets_but_not_you(db, internet):
    _, net = internet
    garantex = "TA82wQ77kb9DieW4C8q7C4KwMfnCzfziqN"  # on the TRX list
    labels.store(db, [(garantex, "Garantex", "cex", "exchange", "graphsense-tagpacks")])
    load(db, net)
    labels.store(db, [(garantex, "Garantex", "cex", "exchange", "graphsense-tagpacks")])
    assert flagged(db)[garantex] == ("Sanctioned by OFAC", "sanctioned (OFAC)", "ofac-sanctions")
    labels.store(db, [(garantex, "my exchange", "cex", "", "user")])
    assert garantex not in flagged(db)


def test_ct_labels_off_reads_no_list(db, internet, monkeypatch):
    _, net = internet
    monkeypatch.setenv("CT_LABELS", "off")
    assert load(db, net) == (0, []) and net.asked == [] and labels.stale(db) == []


def test_money_from_a_sanctioned_address_is_named_in_the_verdict(db, internet):
    _, net = internet
    load(db, net)
    mine(db, A)
    mine(db, B)
    transfer(db, "ethereum", "1", ETH_SANCTIONED, A, 2)
    transfer(db, "ethereum", "2", D, A, 1)
    db.commit()
    (eth,) = graph.origins(db)[A]
    biggest = next((s["address"], s["kind"], s["name"]) for s in eth["sources"])
    assert biggest == (ETH_SANCTIONED, "flagged", "Sanctioned by OFAC")
