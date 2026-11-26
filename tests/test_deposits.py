"""Shared counterparties: exchange deposit addresses and busy strangers (tracker/deposits.py)."""

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from embit import base58
from fastapi.testclient import TestClient
from test_core import BINANCE, A, B, C, D, mine, transfer

from tracker import (
    alerts,
    chains,
    deposits,
    detect,
    exposure,
    graph,
    labels,
    prices,
    swaps,
    sync,
    tokens,
)

DEP, HOT, S1, S2, KRAKEN = ("0x" + c * 40 for c in "3e6f9")
USDT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"


def tron(n):
    return base58.encode_check(bytes([0x41]) + bytes([n]) * 20)


def solana(n):
    return base58.encode(bytes([n]) * 32)


def doge(n):
    return base58.encode_check(bytes([0x1E]) + bytes([n]) * 20)


T1, T2, TDEP, THOT = (tron(n) for n in (1, 2, 3, 4))

# Recorded answers of the fake indexers, per address: what an Etherscan-style API lists under
# txlist, and TronGrid under /transactions/trc20.
EVM, TRC20 = {}, {}
CALLS = []  # the address each request was about
LIMITED = set()  # addresses the second EVM chain ("base", no history) rate-limits
NOTOK = {"status": "0", "message": "NOTOK", "result": "Max calls per sec rate limit reached"}


def internet(request):
    host, path = request.url.host, request.url.path
    if host in ("eth", "base"):
        address, action = request.url.params["address"], request.url.params["action"]
        CALLS.append(address)
        if host == "base" and address in LIMITED:
            return httpx.Response(200, json=NOTOK)
        listed = EVM.get(address, {}) if host == "eth" else {}
        result = "0" if action == "balance" else listed.get(action, [])
        return httpx.Response(200, json={"status": "1", "result": result})
    if host == "api.trongrid.io":
        address = path.split("/")[3]
        CALLS.append(address)
        if path.endswith("/transactions/trc20"):
            return httpx.Response(200, json={"data": TRC20.get(address, []), "success": True})
        if path.endswith("/transactions"):
            return httpx.Response(200, json={"data": [], "success": True})
        return httpx.Response(200, json={"data": [{"address": address, "balance": 0}]})
    return httpx.Response(404)


def pay(src, dst, txid, eth, block=100):
    """An ETH payment, listed under both of its addresses as an indexer lists it."""
    row = {"from": src, "to": dst, "hash": txid, "value": str(int(eth * 10**18))}
    row |= {"timeStamp": "1700000000", "blockNumber": str(block), "isError": "0"}
    for address in (src, dst):
        EVM.setdefault(address, {}).setdefault("txlist", []).append(row)


def pay_usdt(src, dst, txid, usdt):
    row = {"transaction_id": txid, "from": src, "to": dst, "type": "Transfer"}
    row |= {"value": str(int(usdt * 10**6)), "block_timestamp": 1700000000000}
    row["token_info"] = {"address": USDT, "symbol": "USDT", "decimals": 6, "name": "Tether USD"}
    for address in (src, dst):
        TRC20.setdefault(address, []).append(row)


def wallet(db, address, name):
    db.execute("INSERT INTO node(address, mine, name) VALUES (?, 1, ?)", (address, name))
    db.commit()


def label(db, address, name, category="cex", source="etherscan-labels"):
    db.execute(
        "INSERT INTO label(address, name, category, source) VALUES (?, ?, ?, ?)",
        (address, name, category, source),
    )
    db.commit()


def links(db):
    return [tuple(r) for r in db.execute("SELECT * FROM link WHERE kind = 'deposit-address'")]


@pytest.fixture
def web(db, monkeypatch):
    """The fake indexers in place of the internet, and a sync without the steps that call
    other services (label sets, contract names, swaps, prices, token lists)."""
    real = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(internet), **kw)
    )
    monkeypatch.setattr(chains, "DELAY", 0)
    eth = {"url": "http://eth/api", "symbol": "ETH", "llama": "ethereum"}
    monkeypatch.setattr(chains, "DEFAULT_EVM", {"ethereum": eth})
    monkeypatch.setattr(labels, "due", lambda db: False)
    monkeypatch.setattr(detect, "contracts", lambda db, client: 0)
    monkeypatch.setattr(swaps, "sync", lambda db, client: [])
    monkeypatch.setattr(prices, "snapshot", lambda db, client: None)
    monkeypatch.setattr(tokens, "mark", lambda db, client: None)
    for kept in (EVM, TRC20, CALLS, LIMITED):
        kept.clear()


def sync_all():
    """One sync of all my wallets, as the Sync button starts it."""
    assert sync._lock.acquire(blocking=False)
    sync.status.update(error="", notes=[])
    sync.run()
    assert sync.status["error"] == ""


def verdict(db, address):
    return next(v for v in graph.verdicts(db) if v["address"] == address)


def test_two_wallets_paying_one_deposit_address_are_linked_and_alerted(db, web):
    wallet(db, A, "Savings")
    wallet(db, B, "Spending")
    label(db, BINANCE, "Binance 14")
    pay(A, DEP, "h1", 1.0)
    pay(B, DEP, "h2", 0.5)
    pay(DEP, BINANCE, "h3", 1.5)  # swept to the exchange's hot wallet
    alerts.check(db)  # a comparison point, as after an earlier sync
    sync_all()

    assert links(db) == [(A, B, "deposit-address", 0.85, f"{DEP} > Binance")]
    (link,) = verdict(db, A)["links"]
    assert (link["confidence"], link["kinds"]) == (0.85, ["deposit-address"])
    (alert,) = [r["text"] for r in db.execute("SELECT text FROM alert")]
    assert (
        alert == "Savings can be tied to Spending with 85% confidence (no link was known before)."
    )
    guess = db.execute("SELECT name, source FROM label WHERE address = ?", (DEP,)).fetchone()
    assert tuple(guess) == ("Binance deposit address", "heuristic")

    from tracker.app import app

    scan = TestClient(app, base_url="http://localhost").get(f"/api/tracker/exposure/{A}")
    (found,) = [f for f in scan.json()["findings"] if f["id"] == "x-deposit-address-shared"]
    assert found["params"] == {"exchange": "Binance", "count": 1, "wallets": "Spending"}
    assert (found["severity"], found["scoreImpact"], found["confidence"]) == ("high", -10, "high")
    assert found["evidence"] == [
        {"chain": "ethereum", "txid": "h1", "url": "https://etherscan.io/tx/h1"}
    ]


def test_a_tron_usdt_deposit_address_never_read_before_links_two_wallets(db, web):
    wallet(db, T1, "Tron savings")
    wallet(db, T2, "Tron spending")
    label(db, THOT, "Binance", source="graphsense-tagpacks")
    pay_usdt(T1, TDEP, "u1", 100)
    pay_usdt(T2, TDEP, "u2", 50)
    pay_usdt(TDEP, THOT, "u3", 150)
    alerts.check(db)
    sync_all()

    assert CALLS.count(TDEP) == 3  # the account, its TRX list, its TRC-20 list
    assert links(db) == [(*sorted((T1, T2)), "deposit-address", 0.85, f"{TDEP} > Binance")]
    assert verdict(db, T1)["links"][0]["confidence"] == 0.85
    (alert,) = [r["text"] for r in db.execute("SELECT text FROM alert")]
    assert "85% confidence" in alert


def okx_list(db, *addresses):
    """Tron addresses as OKX's proof-of-reserves list names them: deposit addresses and hot
    wallets alike."""
    rows = [f"USDT-TRC20,Non Staking,TRON,87596323,{a},1.5,I am an OKX address,0x8cd7,,,," for a in addresses]  # fmt: skip
    labels.store(db, labels.parse_okx(rows))


def test_a_quiet_address_on_okx_list_that_sweeps_to_okx_links_two_wallets(db, web):
    wallet(db, T1, "Tron savings")
    wallet(db, T2, "Tron spending")
    okx_list(db, TDEP, THOT)
    pay_usdt(T1, TDEP, "u1", 100)
    pay_usdt(T2, TDEP, "u2", 50)
    pay_usdt(TDEP, THOT, "u3", 150)
    sync_all()

    assert CALLS.count(TDEP) == 3  # read, though named
    assert links(db) == [(*sorted((T1, T2)), "deposit-address", 0.85, f"{TDEP} > OKX")]
    assert verdict(db, T1)["links"][0]["confidence"] == 0.85
    named = db.execute("SELECT name, source FROM label WHERE address = ?", (TDEP,)).fetchone()
    assert tuple(named) == ("OKX", "okx-por")  # the list's name stays, not the guess


def test_a_busy_address_on_okx_list_is_read_and_ends_the_path(db, web):
    wallet(db, T1, "Tron savings")
    wallet(db, T2, "Tron spending")
    okx_list(db, THOT)
    pay_usdt(THOT, T1, "w1", 100)  # both withdrew from OKX's hot wallet
    pay_usdt(THOT, T2, "w2", 50)
    pay_usdt(T1, tron(250), "s1", 10)  # USDT one of mine sent too: its receipts count
    for n in range(chains.TRON_ROWS):
        pay_usdt(THOT, tron(10 + n), f"o{n}", 1)
    sync_all()

    assert CALLS.count(THOT) == 3
    assert db.execute("SELECT hub FROM node WHERE address = ?", (THOT,)).fetchone()["hub"] == 1
    assert links(db) == [] and verdict(db, T1)["links"] == []


def test_a_read_that_failed_on_one_chain_is_read_again_and_makes_the_link(db, web, monkeypatch):
    base = {"url": "http://base/api", "symbol": "ETH", "llama": "base"}
    monkeypatch.setitem(chains.DEFAULT_EVM, "base", base)
    wallet(db, A, "Savings")
    wallet(db, B, "Spending")
    label(db, BINANCE, "Binance 14")
    pay(A, DEP, "h1", 1.0)
    pay(B, DEP, "h2", 0.5)
    pay(DEP, BINANCE, "h3", 1.5)
    LIMITED.add(DEP)  # Base turns the deposit address's one read away
    sync_all()
    error = db.execute("SELECT error FROM node WHERE address = ?", (DEP,)).fetchone()[0]
    assert error.startswith("base: ") and links(db) == []

    LIMITED.clear()
    sync_all()
    assert links(db) == [(A, B, "deposit-address", 0.85, f"{DEP} > Binance")]


def deposit(db):
    """Two wallets of mine paid DEP, which was read in full and swept to Binance only."""
    mine(db, A)
    mine(db, B)
    label(db, BINANCE, "Binance 14")
    db.execute("INSERT INTO node(address, synced_at) VALUES (?, 1)", (DEP,))
    transfer(db, "ethereum", "h1", A, DEP, 1)
    transfer(db, "ethereum", "h2", B, DEP, 1)
    transfer(db, "ethereum", "h3", DEP, BINANCE, 2)


TWISTS = {
    "a labelled hot wallet": lambda db: label(db, DEP, "Binance 7"),
    "a hub": lambda db: db.execute("UPDATE node SET hub = 1 WHERE address = ?", (DEP,)),
    "four senders": lambda db: [
        transfer(db, "ethereum", f"s{i}", s, DEP, 1) for i, s in enumerate((S1, S2))
    ],
    "one that also pays anyone else": lambda db: transfer(db, "ethereum", "x", DEP, S1, 1),
    "one that pays two exchanges": lambda db: [
        label(db, KRAKEN, "Kraken 4"),
        transfer(db, "ethereum", "x", DEP, KRAKEN, 1),
    ],
    "one read with an error": lambda db: db.execute(
        "UPDATE node SET error = 'ethereum: HTTP 500' WHERE address = ?", (DEP,)
    ),
    "one never read": lambda db: db.execute("DELETE FROM node WHERE address = ?", (DEP,)),
    "two addresses of one wallet": lambda db: db.execute(
        "UPDATE node SET wallet = 'hd' WHERE mine"
    ),
}


@pytest.mark.parametrize("case", TWISTS.values(), ids=TWISTS.keys())
def test_what_never_makes_the_link(db, case):
    deposit(db)
    deposits.link(db)
    assert len(links(db)) == 1  # the rule holds without the twist
    case(db)
    deposits.link(db)
    detect.behaviour(db)
    assert links(db) == []
    if case is TWISTS["two addresses of one wallet"]:
        return  # still a deposit address, of one customer
    guess = db.execute("SELECT name FROM label WHERE address = ?", (DEP,)).fetchone()
    assert not guess or "deposit address" not in guess["name"]  # the guess is as strict


def test_three_senders_still_make_one_customer(db):
    deposit(db)
    transfer(db, "ethereum", "gas", BINANCE, DEP, 0.01)  # the exchange topping up gas
    detect.behaviour(db)
    deposits.link(db)
    deposits.link(db)  # rebuilt, not added up
    assert links(db) == [(A, B, "deposit-address", 0.85, f"{DEP} > Binance")]
    # A second shared deposit address is more evidence; a payment again to the same one is not.
    transfer(db, "ethereum", "h4", A, DEP, 3)
    other = "0x" + "7" * 40
    db.execute("INSERT INTO node(address, synced_at) VALUES (?, 1)", (other,))
    for txid, src, dst in (("h5", A, other), ("h6", B, other), ("h7", other, BINANCE)):
        transfer(db, "ethereum", txid, src, dst, 1)
    deposits.link(db)
    (link,) = [k for k in graph.build(db)["links"] if k["kind"] == "deposit-address"]
    assert (link["count"], link["confidence"]) == (2, graph.combine([0.85, 0.85]))


def test_every_address_of_a_wallet_that_paid_the_deposit_address_has_the_finding(db):
    deposit(db)
    for address in (A, C, D):  # one wallet of three addresses; D never paid DEP
        db.execute("INSERT OR IGNORE INTO node(address, mine) VALUES (?, 1)", (address,))
        db.execute("UPDATE node SET wallet = 'hd' WHERE address = ?", (address,))
    transfer(db, "ethereum", "h8", C, DEP, 1)
    deposits.link(db)
    assert links(db) == [(A, B, "deposit-address", 0.85, f"{DEP} > Binance")]  # kept on A
    scan = lambda address: list(
        exposure.deposit.check(
            SimpleNamespace(db=db, address=address, evidence=exposure.Context.evidence)
        )
    )
    ((found, params, evidence),) = scan(C)
    assert found == "x-deposit-address-shared"
    assert params == {"exchange": "Binance", "count": 1, "wallets": B}
    assert evidence == [{"chain": "ethereum", "txid": "h8"}]
    assert scan(D) == []


def test_a_sync_reads_ten_candidates_busiest_first_and_the_eleventh_waits(db, web):
    wallet(db, A, "Savings")
    wallet(db, B, "Spending")
    strangers = ["0x" + f"{i:02x}" * 20 for i in range(0x20, 0x2B)]
    for i, s in enumerate(strangers):
        for n in range(11 - i):  # the first is the busiest
            pay(A, s, f"a{i}-{n}", 0.1)
        pay(B, s, f"b{i}", 0.1)
    lonely, named = "0x" + "71" * 20, "0x" + "72" * 20
    for n in range(20):  # busier than any, but only one wallet of mine dealt with it
        pay(A, lonely, f"l{n}", 0.1)
    for txid, src in (("n1", A), ("n2", B)):
        pay(src, named, txid, 0.1)
    label(db, named, "Uniswap", "dex")

    sync_all()
    read = lambda: {
        r[0] for r in db.execute("SELECT address FROM node WHERE synced_at AND NOT mine")
    }
    assert read() == set(strangers[:10])
    sync_all()
    assert read() == set(strangers)
    assert lonely not in CALLS and named not in CALLS


def test_a_candidate_whose_read_failed_is_asked_again_after_the_unread_ones(db):
    mine(db, A)
    mine(db, B)
    fresh = "0x" + "7" * 40
    db.execute(
        "INSERT INTO node(address, synced_at, error) VALUES (?, 1, 'base: HTTP 429')", (DEP,)
    )
    for i, src in enumerate((A, B, A, B)):  # the busier one
        transfer(db, "ethereum", f"d{i}", src, DEP, 1)
    for i, src in enumerate((A, B)):
        transfer(db, "ethereum", f"f{i}", src, fresh, 1)
    assert deposits.candidates(db) == [fresh, DEP]
    db.execute("UPDATE node SET error = NULL WHERE address = ?", (DEP,))
    assert deposits.candidates(db) == [fresh]  # read without an error: done


def test_solana_candidates_take_one_call_each_and_dogecoin_none(db, monkeypatch):
    busy_, quiet = solana(7), solana(8)
    wallets, dogs = (solana(1), solana(2)), (doge(1), doge(2))
    for address in (*wallets, *dogs):
        db.execute("INSERT INTO node(address, mine, synced_at) VALUES (?, 1, 1)", (address,))
    for i, w in enumerate(wallets):
        transfer(db, "solana", f"b{i}", w, busy_, 1, asset="SOL")
        transfer(db, "solana", f"q{i}", w, quiet, 1, asset="SOL")
    for i, w in enumerate(dogs):
        transfer(db, "dogecoin", f"d{i}", w, doge(9), 1, asset="DOGE")
    asked = []

    def rpc(request):
        method, params = (json.loads(request.content)[k] for k in ("method", "params"))
        assert method == "getSignaturesForAddress" and params[1] == {"limit": 1000}
        asked.append(params[0])
        n = 1000 if params[0] == busy_ else 10
        return httpx.Response(200, json={"result": [{"signature": f"s{i}"} for i in range(n)]})

    monkeypatch.setattr(chains, "DELAY", 0)
    with httpx.Client(transport=httpx.MockTransport(rpc)) as http:
        deposits.read(db, http)
        assert sorted(asked) == sorted((busy_, quiet))  # Dogecoin: nothing asked
        assert {r[0] for r in db.execute("SELECT address FROM node WHERE hub")} == {busy_}
        # The busy one stops paths; the quiet one is a stranger like any other.
        (link,) = verdict(db, min(wallets))["links"]
        assert quiet in link["path"] and busy_ not in link["path"]
        deposits.read(db, http)
        assert len(asked) == 2  # each is checked once


def test_every_finding_of_the_scan_is_worded():
    locales = Path(__file__).parent.parent / "web" / "public" / "locales" / "en"
    words = json.loads((locales / "tracker-exposure.json").read_text())
    for id in exposure.IMPACT:
        for field in ("title", "recommendation"):
            assert f"finding.{id}.{field}" in words, (id, field)
        assert {f"finding.{id}.description", f"finding.{id}.description_other"} & set(words), id
