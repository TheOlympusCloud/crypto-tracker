import time

import httpx
import pytest
from test_core import SOL, TRX, A, B, D, mine
from test_exposure import label
from test_lookup import PAYER, stored

from tracker import chains, exposure, history, lookup
from tracker.exposure import funder

SOL2 = "4Nd1mBQtrMJVYVfKf2PJy9NZUZdTAsp7D4xWLs4gDB4T"  # another Solana wallet of mine
FUNDER_TRX = "TXka46PPwttNPWfFDPtt3GUodbPThyufaV"  # hex 41ee...ee


@pytest.fixture(autouse=True)
def evm(monkeypatch):
    monkeypatch.setattr(chains, "DELAY", 0)
    monkeypatch.setattr(
        chains,
        "DEFAULT_EVM",
        {
            "ethereum": {"url": "http://eth/api", "symbol": "ETH"},
            "polygon": {"url": "http://poly/api", "symbol": "POL"},
            "bsc": {"url": "http://bsc/v1/key", "symbol": "BNB", "api": "nodereal"},
        },
    )


def indexers(txlist=(), tron=(), signatures=0):
    """The ascending reads a funder takes (an Etherscan-style txlist, a TronGrid page) and
    the Solana busy check. Every call is kept, to be counted."""
    calls = []

    def answer(request):
        calls.append(request)
        p = request.url.params
        if request.url.host in ("eth", "poly"):
            assert (p["action"], p["sort"], p["address"]) == ("txlist", "asc", A)
            return httpx.Response(200, json={"status": "1", "result": list(txlist)})
        if request.url.host == "api.trongrid.io":
            assert (p["only_to"], p["order_by"]) == ("true", "block_timestamp,asc")
            return httpx.Response(200, json={"data": list(tron)})
        if request.url.host == "api.mainnet-beta.solana.com":
            return httpx.Response(200, json={"result": [{"signature": "s"}] * signatures})
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(answer)), calls


def scan(db, http, address, chains_read=(), truncated=False, read=None):
    """The funder check alone, as exposure.run calls it, after a lookup read `read`."""
    chains_read = list(chains_read) or [db_chain(address)]
    if read is not None:
        lookup.READS[(address, tuple(chains_read))] = (time.time(), read)
    view = {"chains": chains_read, "truncated": truncated, "errors": [], "connections": []}
    return list(funder.check(exposure.Context(db, http, address, view)))


def db_chain(address):
    return "solana" if address in (SOL, SOL2) else "tron"


def complete(db, address):
    for source in history.sources(address):
        history.put(db, address, source, complete=1)


def transfer(db, chain, txid, src, dst, ts, asset="ETH", token=""):
    db.execute(
        "INSERT INTO transfer VALUES (?, ?, ?, ?, ?, ?, 1.0, ?)",
        (chain, txid, src, dst, asset, token, ts),
    )


def row(src, dst, txid, ts, value=10**18):
    return {"from": src, "to": dst, "hash": txid, "timeStamp": str(ts), "value": str(value)}


def test_my_wallets_first_funder_comes_from_its_stored_history_without_a_call(db):
    mine(db, A)
    mine(db, D)
    db.execute("UPDATE node SET name = 'Main' WHERE address = ?", (D,))
    transfer(db, "ethereum", "h2", B, A, 9)  # later coins, from a stranger
    transfer(db, "ethereum", "h1", D, A, 5)
    transfer(db, "ethereum", "h3", D, A, 7, "USDC", "0x" + "c" * 40)  # a token: no gas
    complete(db, A)
    http, calls = indexers()

    found = scan(db, http, A, ["ethereum", "bsc"], truncated=True)

    assert found == [
        (
            "x-funder-mine",
            {"funder": "Main", "chains": "Ethereum"},
            [{"chain": "ethereum", "txid": "h1"}],
        )
    ]
    assert calls == []


def test_while_my_history_fills_in_the_funder_is_read_as_any_other_addresss(db):
    mine(db, A)
    transfer(db, "ethereum", "h2", B, A, 9)  # the oldest stored so far, but not the first
    label(db, D, "Binance 14", "cex")
    read = {"transfers": [("ethereum", "h1", D, A, "ETH", "", 2.0, 5)], "hub": False}
    http, calls = indexers()

    found = scan(db, http, A, ["ethereum"], read=read)

    assert [(id, params) for id, params, _ in found] == [
        ("x-funder-exchange", {"exchange": "Binance", "chains": "Ethereum"})
    ]
    assert calls == []


def test_an_exchange_funder_found_in_the_read_takes_no_call(db):
    label(db, D, "Binance 14", "cex")
    read = {
        "transfers": [
            ("ethereum", "h4", B, A, "ETH", "", 1.0, 8),
            ("ethereum", "h1", D, A, "ETH", "", 2.0, 5),
            ("polygon", "p1", D, A, "POL", "", 3.0, 6),
            ("ethereum", "h0", A, B, "ETH", "", 1.0, 4),  # sent, not received
            ("ethereum", "h3", B, A, "USDC", "0x" + "c" * 40, 5.0, 3),  # a token pays no gas
        ],
        "hub": False,
    }
    http, calls = indexers()

    found = scan(db, http, A, ["ethereum", "polygon"], read=read)

    evidence = [{"chain": "ethereum", "txid": "h1"}, {"chain": "polygon", "txid": "p1"}]
    params = {"exchange": "Binance", "chains": "Ethereum, Polygon"}
    assert found == [("x-funder-exchange", params, evidence)]
    assert calls == []


def test_a_busy_address_takes_one_ascending_call_per_chain_and_none_on_nodereal(db):
    label(db, D, "Coinbase 3", "cex")
    http, calls = indexers(txlist=[row(B, A, "h5", 5, value=0), row(D, A, "h6", 6)])

    found = scan(db, http, A, ["ethereum", "polygon", "bsc"], truncated=True)

    assert [c.url.host for c in calls] == ["eth", "poly"]  # BNB Chain reads a day per call
    assert found[0][:2] == (
        "x-funder-exchange",
        {"exchange": "Coinbase", "chains": "Ethereum, Polygon"},
    )


def test_an_address_that_sent_before_any_coin_reached_it_by_txlist_names_no_funder(db):
    """Its gas came by an internal transfer, which txlist does not show: unknown, not wrong."""
    label(db, D, "Coinbase 3", "cex")
    http, calls = indexers(txlist=[row(A, B, "h7", 5, value=0), row(D, A, "h8", 6)])  # a call
    assert scan(db, http, A, ["ethereum"], truncated=True) == [] and len(calls) == 1


def test_the_tron_activator_is_one_ascending_call(db):
    mine(db, FUNDER_TRX)
    activation = {
        "txID": "t1",
        "block_timestamp": 5000,
        "ret": [{"contractRet": "SUCCESS"}],
        "raw_data": {
            "contract": [
                {
                    "type": "TransferContract",
                    "parameter": {
                        "value": {
                            "owner_address": "41" + "ee" * 20,
                            "to_address": "4174472e7d35395a6b5add427eecb7f4b62ad2b071",
                            "amount": 1_000_000,
                        }
                    },
                }
            ]
        },
    }
    http, calls = indexers(tron=[activation])

    found = scan(db, http, TRX, truncated=True)

    assert found == [
        (
            "x-funder-mine",
            {"funder": FUNDER_TRX, "chains": "Tron"},
            [{"chain": "tron", "txid": "t1"}],
        )
    ]
    assert len(calls) == 1 and calls[0].url.params["limit"] == "1"


def test_a_solana_funder_comes_from_a_whole_read_and_is_left_out_of_a_cut_one(db):
    mine(db, PAYER)
    read = {"transfers": [("solana", "sig", PAYER, SOL, "SOL", "", 1.0, 1794188391)], "hub": False}
    http, calls = indexers()
    assert scan(db, http, SOL, read=read)[0][0] == "x-funder-mine"
    # No call reads a Solana address from its first transaction: the scan is partly checked.
    assert scan(db, http, SOL, truncated=True) == [] and calls == []


@pytest.mark.parametrize(
    ("signatures", "id", "hub"), [(1000, "x-funder-hub", 1), (10, "x-funder-shared", 0)]
)
def test_a_stranger_who_funded_two_wallets_of_mine_is_read_first(db, signatures, id, hub):
    mine(db, SOL)
    mine(db, SOL2)
    db.execute("UPDATE node SET name = 'Trading' WHERE address = ?", (SOL2,))
    transfer(db, "solana", "s1", PAYER, SOL, 1, "SOL")
    transfer(db, "solana", "s2", PAYER, SOL2, 2, "SOL")
    complete(db, SOL)
    http, calls = indexers(signatures=signatures)

    found = scan(db, http, SOL)

    params = {"funder": PAYER, "wallets": "Trading", "chains": "Solana"}
    assert found == [(id, params, [{"chain": "solana", "txid": "s1"}])]
    assert len(calls) == 1  # the busy check
    row = db.execute("SELECT hub FROM node WHERE address = ?", (PAYER,)).fetchone()
    assert (row["hub"] if row else 0) == hub


@pytest.mark.parametrize("case", ["another address", "a lone wallet of mine"])
def test_a_stranger_is_read_only_when_it_funded_two_wallets_of_mine(db, case):
    mine(db, SOL2)
    if case == "another address":  # a scan of it makes no call beyond one per chain
        transfer(db, "solana", "s2", PAYER, SOL2, 2, "SOL")
    else:
        mine(db, SOL)
        complete(db, SOL)
        transfer(db, "solana", "s1", PAYER, SOL, 1, "SOL")
    read = {"transfers": [("solana", "s1", PAYER, SOL, "SOL", "", 1.0, 1)], "hub": False}
    http, calls = indexers(signatures=1000)
    before = stored(db)
    assert scan(db, http, SOL, read=read) == [] and calls == [] and stored(db) == before


def test_the_check_is_registered_for_every_account_chain():
    assert funder.check in exposure.CHECKS["any"]  # EVM, Solana and Tron
    assert {"x-funder-mine", "x-funder-exchange", "x-funder-shared", "x-funder-hub"} <= set(
        exposure.IMPACT
    )
