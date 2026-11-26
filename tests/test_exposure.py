import json
from pathlib import Path

import httpx
import pytest
from test_core import SOL, TRX, A, B, D, mine
from test_lookup import SPAMMER, stored
from test_lookup import client as indexers

from tracker import busy, chains, exposure, lookup
from tracker import db as database

SCAN = "/api/tracker/exposure/"
# One set of findings scored by both ports of score.ts: this test and the web's vitest.
client = indexers  # a fixture: the app over test_lookup's fake indexers
CASES = Path(__file__).parent.parent / "web/src/lib/tracker/__tests__/score-cases.json"


def label(db, address, name, category):
    db.execute(
        "INSERT INTO label(address, name, category, source) VALUES (?, ?, ?, 'etherscan-labels')",
        (address, name, category),
    )
    db.commit()


@pytest.mark.parametrize("case", json.loads(CASES.read_text()), ids=lambda c: c["name"])
def test_the_score_is_am_i_exposeds_address_score(case):
    got = exposure.score(case["findings"])
    assert (got["score"], got["grade"]) == (case["score"], case["grade"])
    assert [f["id"] for f in got["findings"]] == case["order"]
    assert got["partial"] is False


def test_an_exchange_funded_address_shows_the_exchange_with_evidence_links(db, client):
    label(db, D, "Binance 14", "cex")  # D sent A 2 ETH in h1, on both chains
    label(db, B, "Tornado.Cash", "mixer")  # A paid B
    label(db, SPAMMER, "Fake_Phishing123", "flagged")  # airdropped a token nobody vouches for
    before = stored(db)

    r = client.get(f"{SCAN}{A}?chains=ethereum,polygon").json()

    by = {f["id"]: f for f in r["findings"]}
    assert set(by) == {
        "x-exchange-exposure", "x-funder-exchange", "x-mixer-counterparty", "x-multi-chain"
    }  # fmt: skip
    cex = by["x-exchange-exposure"]
    assert (cex["severity"], cex["scoreImpact"], cex["confidence"]) == (
        "high",
        -15,
        "deterministic",
    )
    assert cex["adversaryTiers"] == ["kyc_exchange"] and cex["temporality"] == "historical"
    # ETH and USDC on Ethereum, POL on Polygon, where USDC has no reliable price
    assert cex["params"] == {"exchange": "Binance", "count": 3, "chains": "Ethereum, Polygon"}
    assert {"chain": "ethereum", "txid": "h1", "url": "https://etherscan.io/tx/h1"} in cex[
        "evidence"
    ]
    assert by["x-mixer-counterparty"]["params"]["name"] == "Tornado.Cash"
    assert by["x-multi-chain"]["params"] == {"count": 2, "chains": "Ethereum, Polygon"}
    # D also sent A its first coins on both chains: Binance knows A from its first transaction.
    assert by["x-funder-exchange"]["params"] == {
        "exchange": "Binance",
        "chains": "Ethereum, Polygon",
    }
    # The server sends ids and parameters, never sentences: the page words them.
    assert all("title" not in f and "description" not in f for f in r["findings"])
    assert (r["score"], r["grade"], r["partial"], r["gaps"]) == (
        93 - 15 - 10 - 2,  # the exchange's first-funder withdrawal is one of its transfers
        "C",
        False,
        [],
    )
    assert (r["chain"], r["chains"], r["icon"]) == ("evm", ["ethereum", "polygon"], "evm")
    assert r["findings"][0]["id"] == "x-exchange-exposure"  # the worst first
    # A stranger's airdrop is no dealing with a flagged address.
    assert "x-flagged-counterparty" not in by
    # Scanning is looking: nothing a verdict is made of has changed.
    assert stored(db) == before


def test_a_guessed_label_is_no_finding(db, client):
    db.execute(
        "INSERT INTO label(address, name, category, source) "
        "VALUES (?, 'Binance deposit address', 'cex', 'heuristic')",
        (D,),
    )
    db.commit()
    r = client.get(f"{SCAN}{A}?chains=ethereum").json()
    assert r["findings"] == [] and (r["score"], r["grade"]) == (93, "A+")


def test_a_partial_read_is_partly_checked(db, client, monkeypatch):
    monkeypatch.setattr(chains, "EVM_ROWS", 2)  # a full page: only the newest rows were read
    r = client.get(f"{SCAN}{A}?chains=ethereum").json()
    assert r["partial"] is True and {"id": "truncated"} in r["gaps"]

    monkeypatch.setitem(chains.DEFAULT_EVM["polygon"], "url", "http://down/api")
    r = client.get(f"{SCAN}{A}?chains=ethereum,polygon").json()
    assert r["partial"] is True
    assert {"id": "error", "params": {"error": "polygon: HTTP 404"}} in r["gaps"]


def test_a_scan_right_after_a_lookup_reads_nothing_again(db, client, monkeypatch):
    reads = []
    real = chains.fetch_evm
    monkeypatch.setattr(chains, "fetch_evm", lambda *a, **k: reads.append(1) or real(*a, **k))
    client.get(f"/api/tracker/lookup/{A}?chains=ethereum")
    client.get(f"{SCAN}{A}?chains=ethereum")
    client.get(f"{SCAN}{A}?chains=ethereum")
    assert len(reads) == 1
    client.get(f"/api/tracker/lookup/{A}?chains=ethereum")  # a lookup always reads live
    client.get(f"{SCAN}{A}?chains=polygon")  # other chains, another read
    assert len(reads) == 3
    monkeypatch.setattr(lookup, "KEEP", 0)  # older than that: read again
    client.get(f"{SCAN}{A}?chains=ethereum")
    assert len(reads) == 4


def test_a_check_out_of_calls_leaves_the_scan_partly_checked(db, client, monkeypatch):
    def greedy(ctx):
        for _ in range(3):
            ctx.get("http://eth/api", {"module": "account", "action": "balance", "address": A})
        yield "x-multi-chain", {"count": 9, "chains": ""}, []

    monkeypatch.setattr(exposure, "BUDGET", 2)
    monkeypatch.setattr(exposure, "CHECKS", {"any": [greedy], "evm": []})
    r = client.get(f"{SCAN}{A}?chains=ethereum").json()
    assert r["findings"] == [] and r["partial"] is True
    assert r["gaps"] == [{"id": "skipped", "params": {"check": "greedy"}}]


def test_findings_a_check_made_before_it_ran_out_of_calls_are_kept(db, client, monkeypatch):
    def thrifty(ctx):
        yield "x-multi-chain", {"count": 2, "chains": "Ethereum, Polygon"}, []
        ctx.get("http://eth/api", {"module": "account", "action": "balance", "address": A})

    monkeypatch.setattr(exposure, "BUDGET", 0)
    monkeypatch.setattr(exposure, "CHECKS", {"any": [thrifty], "evm": []})
    r = client.get(f"{SCAN}{A}?chains=ethereum").json()
    assert [f["id"] for f in r["findings"]] == ["x-multi-chain"] and r["partial"] is True
    assert r["gaps"] == [{"id": "skipped", "params": {"check": "thrifty"}}]


def test_the_scan_takes_account_chains_only(db, client):
    assert client.get(f"{SCAN}{A}?chains=base").status_code == 422  # needs a key
    assert client.get(f"{SCAN}1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa").status_code == 422  # Bitcoin
    assert client.get(f"{SCAN}nonsense").status_code == 422
    assert client.get(f"{SCAN}{SOL}").json()["chain"] == "solana"


def rpc(signatures, rows=0):
    """A Solana RPC answering getSignaturesForAddress, and TronGrid answering one page."""
    calls = []

    def answer(request):
        calls.append(request)
        if request.url.host == "api.trongrid.io":
            return httpx.Response(200, json={"data": [{"block_timestamp": 1}] * rows})
        method, params = (json.loads(request.content)[k] for k in ("method", "params"))
        assert method == "getSignaturesForAddress" and params[1] == {"limit": 1000}
        return httpx.Response(
            200, json={"result": [{"signature": f"s{i}"} for i in range(signatures)]}
        )

    return httpx.Client(transport=httpx.MockTransport(answer)), calls


@pytest.fixture
def quick(monkeypatch):
    monkeypatch.setattr(chains, "DELAY", 0)


def test_a_busy_solana_stranger_is_stored_as_a_hub(db, quick):
    http, calls = rpc(1000)
    assert busy.check(db, http, SOL) is True and len(calls) == 1
    assert db.execute("SELECT hub FROM node WHERE address = ?", (SOL,)).fetchone()["hub"] == 1
    assert busy.check(db, http, SOL) is True and len(calls) == 1  # known by now


def test_a_quiet_solana_stranger_is_read_once_and_stores_nothing_a_verdict_reads(db, quick):
    http, calls = rpc(10)
    before = stored(db)
    assert busy.known(db, SOL) is None
    assert busy.check(db, http, SOL) is False and len(calls) == 1
    assert stored(db) == before
    assert busy.known(db, SOL) is False
    assert busy.check(db, http, SOL) is False and len(calls) == 1  # known by now


def test_a_full_tron_page_or_a_label_makes_a_hub(db, quick):
    http, calls = rpc(0, rows=chains.TRON_ROWS)
    assert busy.check(db, http, TRX) is True and len(calls) == 1
    label(db, D, "Binance 14", "cex")
    assert busy.check(db, http, D) is True and len(calls) == 1  # labelled: no call
    assert db.execute("SELECT hub FROM node WHERE address = ?", (D,)).fetchone()["hub"] == 1


@pytest.mark.parametrize(
    ("name", "category", "source"),
    [
        ("Binance deposit address", "cex", "heuristic"),  # the tracker's own guess
        ("UniswapV2Router02", "dex", "explorer"),  # a contract name: the type is a guess
        ("Bob", "other", "user"),  # a person, named by the user: no service
        ("OKX", "cex", "okx-por"),  # an exchange's own list: mostly one customer's addresses
    ],
)
def test_a_guessed_or_personal_label_is_no_hub(db, quick, name, category, source):
    """As in graph.trace: only a service known for sure ends a path; a guess must not hide a link."""
    db.execute(
        "INSERT INTO label(address, name, category, source) VALUES (?, ?, ?, ?)",
        (SOL, name, category, source),
    )
    db.commit()
    http, calls = rpc(10)
    before = stored(db)
    assert busy.check(db, http, SOL) is False and len(calls) == 1  # read like any stranger
    assert stored(db) == before


def test_my_own_wallet_is_never_a_hub(db, quick):
    mine(db, SOL)
    http, calls = rpc(1000)
    assert busy.check(db, http, SOL) is False and calls == []
    assert db.execute("SELECT hub FROM node WHERE address = ?", (SOL,)).fetchone()["hub"] == 0


def test_an_evm_stranger_is_read_by_a_sync(db, client, monkeypatch):
    with httpx.Client() as http:  # the fake indexers
        assert busy.check(db, http, D) is False
        monkeypatch.setattr(chains, "EVM_ROWS", 2)
        assert busy.check(db, http, B) is True
        monkeypatch.setitem(chains.DEFAULT_EVM["ethereum"], "url", "http://down/api")
        with pytest.raises(RuntimeError):  # unread is not quiet
            busy.check(db, http, "0x" + "e" * 40)
    rows = {r["address"]: r["hub"] for r in db.execute("SELECT address, hub FROM node")}
    assert rows[D] == 0 and rows[B] == 1
    assert database.valid(D)
