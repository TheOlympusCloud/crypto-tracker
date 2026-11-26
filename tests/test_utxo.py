"""The Esplora calls of a Litecoin, Dogecoin or Bitcoin Cash scan (/api/tracker/utxo/), answered
from BlockCypher and Haskoin responses recorded on 2026-11-25 (tests/recorded/, trimmed)."""

import json
import time
from collections import OrderedDict
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from tracker import chains, utxo

RECORDED = Path(__file__).parent / "recorded"
DOGE = "DPtotDSjTaSvNQxQvXWQd3wgbdBteez78z"
DOGE_TX = "46c5427656c8339012824da9f6dd944fd5940f76ec19df5c02b469826aab0653"
BCH = "qpc2hdexmqwy06jzvdxddkazyp500kqf9vvmvgxnqm"
BCH_TX = "5d2760c4024c2368ccb6cfed5bb8d04cb839e0dc00d8ad2b0d11e5a94cbf137c"
BCH_SPENT = "ef87c892113ebe841a5ecfb314a88e7775a9e51865ac6d64f884829b39e3dd67"
LTC = "ltc1q4rdwpcksqmsv6nxxze6ls8f0e3a4z3u3z43kjr"
U = "/api/tracker/utxo/"
REAL = httpx.Client
QUIET = "qprxlj8jm8kvta5l3lya3l6x6z7pd2lx2qtwreyy6q"  # paid by BCH_TX, unspent since


def indexers(monkeypatch, answer):
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: REAL(transport=httpx.MockTransport(answer), **kw)
    )


@pytest.fixture
def scan(db, monkeypatch):
    """The app over the recorded indexers, and the requests they got."""
    doge, bch = (
        json.loads((RECORDED / f"{c}.json").read_text()) for c in ("dogecoin", "bitcoincash")
    )
    answers = {
        f"/v1/doge/main/txs/{DOGE_TX}": doge["tx"],
        f"/v1/doge/main/addrs/{DOGE}/full": doge["full"],
        f"/v1/doge/main/addrs/{DOGE}": doge["unspent"],
        f"/bch/transaction/{BCH_TX}": bch["tx"],
        f"/bch/transaction/{BCH_SPENT}": bch["spent_tx"],
        f"/bch/address/{BCH}/balance": bch["balance"],
        f"/bch/address/{BCH}/transactions/full": bch["txs"],
        f"/bch/address/{BCH}/unspent": [],
        f"/bch/address/{QUIET}/unspent": bch["unspent"],
    }
    calls = []

    def answer(request):
        calls.append(request)
        if request.url.host == "litecoinspace.org":
            return httpx.Response(200, json={"passed": request.url.path})
        if request.url.path in answers:
            return httpx.Response(200, json=answers[request.url.path])
        return httpx.Response(404, json={})

    indexers(monkeypatch, answer)
    monkeypatch.setattr(chains, "DELAY", 0)
    monkeypatch.setattr(chains, "COOLDOWN", {})
    monkeypatch.setattr(utxo, "READS", OrderedDict())
    from tracker.app import app

    return TestClient(app, base_url="http://localhost"), calls


def test_a_dogecoin_address_reads_as_esplora_in_two_calls(scan):
    client, calls = scan
    stats = client.get(f"{U}dogecoin/address/{DOGE}").json()
    txs = client.get(f"{U}dogecoin/address/{DOGE}/txs").json()
    utxos = client.get(f"{U}dogecoin/address/{DOGE}/utxo").json()
    # One read of the address serves its stats and its transactions: the quota is small.
    assert len(calls) == 2
    assert json.dumps(stats["chain_stats"]) == json.dumps(
        {
            "funded_txo_count": 3,  # counted in the 3 transactions read; the address has 124
            "funded_txo_sum": 345048716837977,
            "spent_txo_count": 1,  # none in those 3, but the address has spent: at least 1
            "spent_txo_sum": 334597220430557,
            "tx_count": 124,
        }
    )  # as JSON: a count is a number, never true
    first = txs[0]
    assert (first["txid"], first["version"], first["locktime"], first["fee"]) == (
        DOGE_TX,
        1,
        0,
        22440000,
    )
    assert (first["size"], first["weight"]) == (372, 1488)
    assert first["status"] == {"confirmed": True, "block_time": 1795645180, "block_height": 6472814}
    vin = first["vin"][0]
    assert (vin["txid"][:8], vin["vout"], vin["sequence"], vin["is_coinbase"]) == (
        "b901d556",
        1,
        4294967293,
        False,
    )
    assert (
        vin["prevout"]["scriptpubkey_type"] == "p2sh" and vin["prevout"]["value"] == 413173720673669
    )
    assert [(o["scriptpubkey_address"], o["scriptpubkey_type"]) for o in first["vout"]] == [
        (DOGE, "p2pkh"),
        ("A9adXhg8XH3fSziwQaee164B28nom8zXgP", "p2sh"),
    ]
    assert first["vout"][0]["scriptpubkey"] == "76a914cdb6a9ab3a170232b21e5fd201469d141a4d732288ac"
    assert utxos[0] == {
        "txid": DOGE_TX,
        "vout": 0,
        "value": 2175991441440,
        "status": {"confirmed": True, "block_height": 6472814},
    }
    # Only the newest are read: there is no older page.
    assert client.get(f"{U}dogecoin/address/{DOGE}/txs/chain/{txs[-1]['txid']}").json() == []


def test_a_dogecoin_transaction_and_where_its_outputs_went_in_one_call(scan):
    client, calls = scan
    tx = client.get(f"{U}dogecoin/tx/{DOGE_TX}").json()
    spends = client.get(f"{U}dogecoin/tx/{DOGE_TX}/outspends").json()
    assert len(calls) == 1 and calls[0].url.params["limit"] == "500"
    assert tx["txid"] == DOGE_TX and len(tx["vin"]) == 1 and len(tx["vout"]) == 2
    assert spends == [{"spent": False}, {"spent": False}]


def test_a_bitcoin_cash_address_and_transaction_read_as_esplora_without_the_prefix(scan):
    client, _ = scan
    stats = client.get(f"{U}bitcoincash/address/bitcoincash:{BCH}").json()
    assert stats["address"] == BCH
    assert stats["chain_stats"] == {
        "funded_txo_count": 1,
        "funded_txo_sum": 4401766724,
        "spent_txo_count": 1,
        "spent_txo_sum": 4401766724,
        "tx_count": 2,
    }
    txs = client.get(f"{U}bitcoincash/address/{BCH}/txs").json()
    assert [t["txid"] for t in txs] == [BCH_TX, BCH_SPENT]
    assert "bitcoincash:" not in json.dumps(txs)
    tx = client.get(f"{U}bitcoincash/tx/{BCH_TX}").json()
    assert tx["vin"][0]["prevout"] == {
        "scriptpubkey_address": BCH,
        "value": 4401766724,
        "scriptpubkey_type": "p2pkh",
        "scriptpubkey": "76a91470abb726d81c47ea42634cd6dba22068f7d8092b88ac",
    }
    assert (tx["vin"][0]["txid"], tx["vin"][0]["vout"], tx["vin"][0]["sequence"]) == (
        BCH_SPENT,
        0,
        0xFFFFFFFF,
    )
    assert (tx["fee"], tx["weight"], tx["status"]["confirmed"]) == (226, 904, True)
    assert client.get(f"{U}bitcoincash/tx/{BCH_SPENT}/outspends").json() == [
        {"spent": True, "txid": BCH_TX, "vin": 0},
        {
            "spent": True,
            "txid": "d961f4471c79fd54e9b10a291e97ad262e39fad1b206f13c30028926c6b43e57",
            "vin": 53,
        },
    ]
    assert client.get(f"{U}bitcoincash/address/{BCH}/utxo").json() == []
    assert client.get(f"{U}bitcoincash/address/{QUIET}/utxo").json() == [
        {
            "txid": BCH_TX,
            "vout": 0,
            "value": 3722967,
            "status": {"confirmed": True, "block_height": 978979},
        }
    ]


def test_litecoin_is_passed_through_to_its_esplora(scan):
    client, _ = scan
    got = client.get(f"{U}litecoin/address/{LTC}/txs/chain/{BCH_TX}").json()
    assert got == {"passed": f"/api/address/{LTC}/txs/chain/{BCH_TX}"}


def test_only_the_scans_calls_on_these_chains_and_their_addresses(scan):
    client, calls = scan
    assert client.get(f"{U}bitcoin/address/1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa").status_code == 404
    assert client.get(f"{U}litecoin/v1/historical-price?currency=USD").status_code == 404
    assert client.get(f"{U}litecoin/address/1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa").status_code == 422
    assert client.get(f"{U}bitcoincash/address/{DOGE}").status_code == 422
    assert client.get(f"{U}dogecoin/address/{DOGE[:-1]}x").status_code == 422  # a typo
    assert calls == []
    assert client.get(f"{U}dogecoin/tx/{'0' * 64}").status_code == 404  # unknown to the indexer


def test_dogecoins_spent_quota_answers_429_and_asks_nothing_for_an_hour(scan, monkeypatch):
    client, calls = scan
    indexers(monkeypatch, lambda r: calls.append(r) or httpx.Response(429))
    first = client.get(f"{U}dogecoin/tx/{DOGE_TX}")
    again = client.get(f"{U}dogecoin/address/{DOGE}")
    assert (first.status_code, again.status_code) == (429, 429)
    assert "hourly limit" in again.json()["detail"]
    assert len(calls) == 1 and chains.COOLDOWN[chains.UTXO["dogecoin"][2]] > time.time() + 3500
