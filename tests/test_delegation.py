"""Tron resource delegation: a delegator and its receiver share an owner, unless the delegator
is a rental service.

The rows are TronGrid native-list rows recorded on 2026-11-26, trimmed to the fields the
tracker reads (tests/fixtures/tron-delegation.json). RENTAL delegated to WALLET and to OTHER;
its own list was a full page of 200 delegations and undelegations, as a rental service's is.
"""

import json
from pathlib import Path

import httpx
import pytest
from embit import base58
from test_core import mine

from tracker import chains, graph, heuristics, history, sync
from tracker import db as database

ROWS = json.loads((Path(__file__).parent / "fixtures" / "tron-delegation.json").read_text())
RENTAL = "TDfaxYr8TJkdqzsch6574caKgUYxGij86H"
WALLET = "TR2GBptJBBFbkYdmDxPmFLQGYVimuXv8qC"  # rented bandwidth from RENTAL, b1f1...
OTHER = "TUPSVqaXaesRcUKpUagmDdgZuc5pQg2ezR"  # rented energy from RENTAL, daae...
PEER = "TG2CMGxnTPgQ6V58kiKd7wbyN8ewtAmY76"  # paid WALLET 25 TRX, got 18.3035 back


def txid(prefix):
    return next(r["txID"] for r in ROWS if r["txID"].startswith(prefix))


def parties(row):
    v = row["raw_data"]["contract"][0]["parameter"]["value"]
    hexes = (v.get(k) for k in ("owner_address", "to_address", "receiver_address"))
    return {base58.encode_check(bytes.fromhex(h)) for h in hexes if h}


class TronGrid:
    """TronGrid over the recorded rows: an address's native list is the rows it is a party to,
    newest first; an address in `busy` answers a full page."""

    def __init__(self, busy=()):
        self.busy, self.reads = set(busy), []

    def __call__(self, request):
        path, q = request.url.path, request.url.params
        address = path.split("/")[3]  # /v1/accounts/<address>[/transactions[/trc20]]
        if path.endswith("/trc20"):
            return httpx.Response(200, json={"data": []})
        if not path.endswith("/transactions"):
            return httpx.Response(200, json={"data": [{"balance": 0}]})
        self.reads.append(address)
        if address in self.busy:
            rows = [{**ROWS[0], "txID": f"r{i}"} for i in range(chains.TRON_ROWS)]
        else:
            rows = sorted(
                (r for r in ROWS if address in parties(r)), key=lambda r: -r["block_timestamp"]
            )
        if q.get("max_timestamp"):
            rows = [r for r in rows if r["block_timestamp"] <= int(q["max_timestamp"])]
        return httpx.Response(200, json={"data": rows[: int(q["limit"])]})


@pytest.fixture
def quick(monkeypatch):
    monkeypatch.setattr(chains, "DELAY", 0)


def links(db):
    return {(r["a"], r["b"], r["kind"], r["confidence"]) for r in db.execute("SELECT * FROM link")}


def test_a_delegation_links_its_two_ends_and_undelegating_adds_nothing():
    assert chains.tron_links(ROWS) == [
        (*heuristics.pair(RENTAL, WALLET), "delegation", 0.8, txid("b1f1")),
        (*heuristics.pair(RENTAL, OTHER), "delegation", 0.8, txid("daae")),
    ]
    failed = {**ROWS[0], "ret": [{"contractRet": "REVERT"}]}
    assert chains.tron_links([failed]) == []


def test_tron_transfers_are_unchanged():
    assert chains.tron_transfers(ROWS, [])[0] == [
        ("tron", txid("4402"), WALLET, PEER, "TRX", "", 18.3035, 1791836886),
        ("tron", txid("f0e6"), PEER, WALLET, "TRX", "", 25.0, 1791836799),
    ]


def test_the_reader_returns_delegations_of_snapshots_and_full_reads(quick):
    with httpx.Client(transport=httpx.MockTransport(TronGrid())) as client:
        snapshot = chains.fetch(client, WALLET)
        page = chains.page_tron(client, WALLET, "native")
    expected = [(*heuristics.pair(RENTAL, WALLET), "delegation", 0.8, txid("b1f1"))]
    assert snapshot["links"] == expected
    assert history._convert("tron:native", WALLET, page.items, ())["links"] == expected


def test_a_delegation_between_two_wallets_of_mine_links_them_at_0_8(db, quick):
    mine(db, RENTAL)  # declared mine here: then however busy, it is no stranger to read
    mine(db, WALLET)
    db.commit()
    fake = TronGrid(busy={RENTAL})
    with httpx.Client(transport=httpx.MockTransport(fake)) as client:
        sync.sync_address(db, client, WALLET)
    assert RENTAL not in fake.reads  # mine: never read to tell whether it is busy
    assert links(db) == {(*heuristics.pair(RENTAL, WALLET), "delegation", 0.8)}
    path = next(p for p in graph.trace(db) if {p["a"], p["b"]} == {RENTAL, WALLET})
    assert path["confidence"] == 0.8 and path["kinds"] == ["delegation"]


def test_a_rental_service_never_read_is_read_once_and_links_nobody(db, quick):
    mine(db, WALLET)
    mine(db, OTHER)
    db.commit()
    fake = TronGrid(busy={RENTAL})
    with httpx.Client(transport=httpx.MockTransport(fake)) as client:
        sync.sync_address(db, client, WALLET)
        sync.sync_address(db, client, OTHER)
    assert fake.reads.count(RENTAL) == 1  # the second wallet finds it known
    assert db.execute("SELECT hub FROM node WHERE address = ?", (RENTAL,)).fetchone()["hub"]
    assert links(db) == set()
    assert not [p for p in graph.trace(db) if p["confidence"]]  # the two are not linked
    assert db.execute("SELECT COUNT(*) n FROM transfer").fetchone()["n"] == 2  # all stored


def test_a_quiet_delegator_is_linked_after_its_read(db, quick):
    mine(db, WALLET)
    db.commit()
    fake = TronGrid()  # RENTAL answers its three recorded rows: quiet
    with httpx.Client(transport=httpx.MockTransport(fake)) as client:
        sync.sync_address(db, client, WALLET)
        sync.sync_address(db, client, WALLET)  # stored already: not read again
    assert fake.reads.count(RENTAL) == 1
    assert links(db) == {(*heuristics.pair(RENTAL, WALLET), "delegation", 0.8)}


def test_a_wallet_read_in_full_before_the_upgrade_is_read_again_once(db, quick, monkeypatch):
    mine(db, RENTAL)
    mine(db, WALLET)
    for source in history.sources(WALLET):
        history.put(db, WALLET, source, oldest="end", newest=2**62, complete=1)
    db.commit()
    monkeypatch.setattr(chains, "TRON_ROWS", 1)  # the snapshot holds only the newest row
    fake = TronGrid()
    with httpx.Client(transport=httpx.MockTransport(fake)) as client:
        sync.sync_address(db, client, WALLET)
        assert links(db) == set()  # below the bookmark: never read again
        db.execute("DELETE FROM meta WHERE key = 'tron_reread'")  # a database from before
        db.commit()
        upgraded = database.connect()
        sync.sync_address(upgraded, client, WALLET)
    assert (*heuristics.pair(RENTAL, WALLET), "delegation", 0.8) in links(upgraded)
    assert history.state(upgraded, WALLET)["complete"]
    database.connect().close()  # once: the bookmark read since stays
    assert history.get(upgraded, WALLET, "tron:native")["complete"]
    assert history.get(upgraded, WALLET, "tron:trc20")["complete"]  # never dropped
    upgraded.close()
