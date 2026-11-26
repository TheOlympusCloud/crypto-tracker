"""Name records: ENS, Basenames, SNS. The replies in names_recorded.json were recorded from
Routescan (Ethereum), Base's public RPC and Solana's public RPC on 2026-11-25."""

import json
import time
from pathlib import Path

import httpx
import pytest
import test_lookup
from embit import base58
from test_core import DOGE, LTC, SOL, TRX, A, mine
from test_lookup import client  # noqa: F401 - a fixture: the app over fake indexers

from tracker import chains, graph
from tracker.exposure import names
from tracker.heuristics import pair

CLIENT = httpx.Client  # as imported: the app fixture swaps httpx.Client for a fake
RECORDED = json.loads((Path(__file__).parent / "names_recorded.json").read_text())
SCAN = "/api/tracker/exposure/"
VITALIK = "0xd8da6bf26964af9d7eed9e03e53415d37aa96045"  # vitalik.eth
BRANTLY = "0x983110309620d911731ac0932219af06091b6744"  # brantly.eth lists the next two
BRANTLY_BTC = "bc1qjqg9slurvjukfl92wp58y94480fvh4uc2pwa6n"
BRANTLY_DOGE = "DQ5kE8puJbqdCw424QXM6hR9rn1argyM6z"
JESSE = "0x2211d1d0020daea8039e46cf1367962070d77da9"  # jesse.base.eth; offchain on Ethereum
BONFIDA = "Fw1ETanDZafof7xEULsnq9UY6o71Tpds89tNwPkWLb1v"  # couponvault.sol


def key(request):
    """What identifies a name-service request: the call, not the host it went to."""
    if request.method == "GET":
        p = request.url.params
        return f"{p['to'].lower()} {p['data']}" if p.get("action") == "eth_call" else None
    body = json.loads(request.content)
    if body["method"] == "eth_call":
        call = body["params"][0]
        return f"{call['to'].lower()} {call['data']}"
    return f"{body['method']} {json.dumps(body['params'], separators=(',', ':'))}"


def reverse_key(address):
    ask = names._call("reverse(bytes,uint256)", bytes.fromhex(address[2:]), 60)
    return f"{names.UNIVERSAL} 0x{ask.hex()}"


def name_calls(requests):
    return [r for r in requests if (k := key(r)) and k.startswith(("0x", "getMultipleAccounts"))]


def answers(asked, extra=None, otherwise=None):
    """Name calls answered from the recordings (`extra` first); any other name call finds no
    name, and the rest goes to `otherwise`."""

    def handler(request):
        asked.append(request)
        k = key(request)
        if k in (extra or {}) or k in RECORDED:
            status, body = (extra or {}).get(k) or RECORDED[k]
            return httpx.Response(status, json=body)
        if otherwise:
            return otherwise(request)
        if k and k.startswith("getMultipleAccounts"):
            return httpx.Response(200, json={"result": {"value": [None, None]}})
        return httpx.Response(200, json={"result": "0x"})

    return handler


def replay(asked, extra=None, otherwise=None):
    return CLIENT(transport=httpx.MockTransport(answers(asked, extra, otherwise)))


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(chains, "DELAY", 0)


@pytest.fixture
def scans(client, monkeypatch):  # noqa: F811
    """The app over the fake indexers, which now also answer the recorded name calls."""
    asked = []
    monkeypatch.setattr(test_lookup, "indexers", answers(asked, otherwise=test_lookup.indexers))
    return client, asked


def test_keccak_and_namehash_match_known_values():
    assert names.keccak(b"").hex() == (
        "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    )
    assert names.keccak(b"abc").hex() == (
        "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45"
    )
    # Around the 136-byte block and over several blocks (values from @noble/hashes).
    assert names.keccak(b"a" * 135).hex().startswith("34367dc248bbd832")
    assert names.keccak(b"a" * 136).hex().startswith("a6c4d403279fe3e0")
    assert names.keccak(b"a" * 300).hex().startswith("5b7e0e47a96f32a8")
    assert names.keccak(b"transfer(address,uint256)")[:4].hex() == "a9059cbb"
    # ENS's own examples (EIP-137)
    assert names.namehash("") == bytes(32)
    assert names.namehash("eth").hex() == (
        "93cdeb708b7545dc668eb9280176169d1c33cfd8ed6f04690a0bcc88a93fc4ae"
    )
    assert names.namehash("foo.eth").hex() == (
        "de9b09fd7c5f901e23a3f19fecc54828e9c848539801e86591bd9801b019f84f"
    )


def test_solana_program_addresses_are_derived_as_on_chain():
    hashed = names.hashlib.sha256(b"SPL Name Service" + b"bonfida").digest()
    key = names._pda([hashed, bytes(32), names.SOL_TLD], names.SNS)
    assert base58.encode(key) == "Crf8hzfthWGbGbLTVCiqRqV5MVnbpHB1L9KQMd6gsinb"  # bonfida.sol
    assert not names._on_curve(key)
    assert names._on_curve(base58.decode(SOL)) and names._on_curve(base58.decode(BONFIDA))


@pytest.mark.parametrize(
    ("chain", "raw", "expected"),
    [
        (
            "bitcoin",
            "76a91462e907b15cbf27d5425399ebf6f0fb50ebb88f1888ac",
            "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa",
        ),
        ("litecoin", f"76a914{base58.decode_check(LTC)[1:].hex()}88ac", LTC),
        ("dogecoin", f"76a914{base58.decode_check(DOGE)[1:].hex()}88ac", DOGE),
        ("tron", base58.decode_check(TRX).hex(), TRX),
        ("solana", base58.decode(SOL).hex(), SOL),
        ("base", "ab" * 20, "0x" + "ab" * 20),
        ("evm", "00" * 20, None),  # unset
        ("evm", "ab" * 19, None),
        ("bitcoin", "6a0401020304", None),  # an OP_RETURN script is no address
    ],
)
def test_address_records_read_as_the_addresses_the_tracker_stores(chain, raw, expected):
    assert names._address(chain, bytes.fromhex(raw)) == expected


def test_a_known_ens_name_is_found_and_shown(db, scans):
    app, _ = scans
    r = app.get(f"{SCAN}{VITALIK}?chains=ethereum").json()
    by = {f["id"]: f for f in r["findings"]}
    assert by["x-name-record"]["params"] == {"name": "vitalik.eth"}
    assert (by["x-name-record"]["severity"], by["x-name-record"]["scoreImpact"]) == ("high", -15)
    assert "x-name-addresses" not in by  # vitalik.eth lists no address of another chain
    assert r["gaps"] == []

    r = app.get(f"{SCAN}{BRANTLY}?chains=ethereum").json()
    by = {f["id"]: f for f in r["findings"]}
    assert by["x-name-record"]["params"] == {"name": "brantly.eth"}
    assert by["x-name-addresses"]["params"] == {
        "name": "brantly.eth",
        "count": 2,
        "chains": "Bitcoin, Dogecoin",
        "addresses": f"{BRANTLY_DOGE}, {BRANTLY_BTC}",
    }
    assert by["x-name-addresses"]["scoreImpact"] == -5


def test_basenames_and_sol_names_are_found(db, scans):
    app, _ = scans
    r = app.get(f"{SCAN}{JESSE}?chains=ethereum").json()
    # On Ethereum his name is read through a gateway: the call reverts, no name, no gap.
    assert [f["params"] for f in r["findings"] if f["id"] == "x-name-record"] == [
        {"name": "jesse.base.eth"}
    ]
    assert r["gaps"] == []
    r = app.get(f"{SCAN}{BONFIDA}").json()
    assert [f["params"] for f in r["findings"] if f["id"].startswith("x-name")] == [
        {"name": "couponvault.sol"}
    ]


def test_a_malformed_solana_account_is_a_gap_not_a_crash(db, client, monkeypatch):  # noqa: F811
    indexers = test_lookup.indexers

    def empty(request):
        if (key(request) or "").startswith("getMultipleAccounts"):
            return httpx.Response(200, json={"result": {"value": [{"data": []}]}})
        return indexers(request)

    monkeypatch.setattr(test_lookup, "indexers", empty)
    r = client.get(f"{SCAN}{SOL}")
    assert r.status_code == 200, r.text
    assert [g["params"]["error"] for g in r.json()["gaps"]] == ["names: list index out of range"]


def test_a_reverse_record_whose_name_points_elsewhere_is_ignored():
    asked = []
    # A's reverse record says vitalik.eth, but vitalik.eth points at Vitalik's address.
    extra = {reverse_key(A): RECORDED[reverse_key(VITALIK)]}
    assert names.lookup(replay(asked, extra), A) == []
    resolve = names.keccak(b"resolve(bytes,bytes)")[:4].hex()
    assert any(resolve in (key(r) or "") for r in asked)  # the name was looked up, then dropped


def test_a_reverted_call_is_no_name_and_is_not_retried():
    asked = []
    assert names.lookup(replay(asked), JESSE) == [
        {"chain": "base", "name": "jesse.base.eth", "listed": {}}
    ]
    assert [key(r) for r in asked].count(reverse_key(JESSE)) == 1


def test_eth_call_goes_to_routescan_for_ethereum_or_to_your_node(monkeypatch, tmp_path):
    monkeypatch.setattr(chains, "DEFAULT_EVM", chains.builtin_evm())  # no keys, as installed
    asked = []
    found = [{"chain": "ethereum", "name": "vitalik.eth", "listed": {}}]
    assert names.lookup(replay(asked), VITALIK) == found
    ethereum = [r for r in asked if r.url.host == "api.routescan.io"]
    assert len(ethereum) == 2 and all(
        r.method == "GET"
        and "/evm/1/" in r.url.path
        and (r.url.params["module"], r.url.params["action"]) == ("proxy", "eth_call")
        for r in ethereum
    )
    assert {r.url.host for r in asked} == {"api.routescan.io", "base-rpc.publicnode.com"}

    own = {"ethereum": {"rpc": "http://node.lan:8545"}, "base": {"rpc": "http://base.lan:8545"}}
    for chain, cfg in own.items():
        cfg |= chains.builtin_evm()[chain]
    (tmp_path / "chains.json").write_text(json.dumps(own))
    monkeypatch.setenv("CT_CHAINS", str(tmp_path / "chains.json"))
    asked.clear()
    assert names.lookup(replay(asked), VITALIK) == found
    assert {(r.method, r.url.host) for r in asked} == {("POST", "node.lan"), ("POST", "base.lan")}


def dogecoin(transactions):
    """The Dogecoin indexer, for the read of a listed stranger (busy.check)."""
    body = {"txs": [], "final_balance": 0, "n_tx": transactions}
    return lambda request: httpx.Response(200, json=body)


def name_links(db):
    rows = db.execute("SELECT * FROM link WHERE kind = 'name'")
    return {(r["a"], r["b"]): (r["confidence"], r["evidence"]) for r in rows}


def test_addresses_a_name_lists_become_links(db, monkeypatch):
    mine(db, BRANTLY)
    mine(db, BRANTLY_BTC)  # brantly.eth lists this wallet of mine, and a Dogecoin stranger
    asked = []
    assert names.sync(db, replay(asked, otherwise=dogecoin(0))) == []
    assert name_links(db) == {
        pair(BRANTLY, BRANTLY_BTC): (0.8, "brantly.eth"),
        pair(BRANTLY, BRANTLY_DOGE): (0.8, "brantly.eth"),  # read once and found quiet
    }
    row = db.execute("SELECT * FROM name").fetchone()
    assert (row["address"], row["chain"], row["name"]) == (BRANTLY, "ethereum", "brantly.eth")
    assert json.loads(row["listed"]) == {"bitcoin": BRANTLY_BTC, "dogecoin": BRANTLY_DOGE}
    # The verdict between my two wallets rests on the name.
    (verdict,) = graph.trace(db)
    assert (verdict["confidence"], verdict["kinds"]) == (0.8, ["name"])

    # Looked up again only after a day.
    asked.clear()
    names.sync(db, replay(asked))
    assert asked == []

    # A lookup that fails keeps what was found before.
    monkeypatch.setattr(names, "REFRESH", -1)
    down = lambda request: httpx.Response(503)
    notes = names.sync(db, CLIENT(transport=httpx.MockTransport(down)))
    assert notes == ["names: HTTP 503"] and len(name_links(db)) == 2

    # A name that is gone takes its links with it.
    gone = {reverse_key(BRANTLY): [200, {"result": "0x"}]}
    assert names.sync(db, replay([], gone)) == []
    assert name_links(db) == {} and db.execute("SELECT * FROM name").fetchall() == []


def test_a_refresh_keeps_the_links_of_another_wallet_with_the_same_name(db):
    # A Basename passes the forward check for both the address of its Ethereum record and
    # that of its Base record, so two wallets of mine can hold one name.
    for address in (BRANTLY, BRANTLY_BTC, A):
        mine(db, address)
    for address in (BRANTLY, A):  # both looked up before
        db.execute("INSERT INTO name VALUES (?, 'base', 'brantly.eth', '{}')", (address,))
    db.execute("INSERT INTO link VALUES (?, ?, 'name', 0.8, 'brantly.eth')", pair(A, BRANTLY_DOGE))
    db.execute("INSERT INTO meta VALUES (?, ?)", (f"names_at:{A}", str(time.time())))
    assert names.sync(db, replay([], otherwise=dogecoin(0))) == []  # refreshes BRANTLY only
    assert name_links(db)[pair(A, BRANTLY_DOGE)] == (0.8, "brantly.eth")
    assert name_links(db)[pair(BRANTLY, BRANTLY_DOGE)] == (0.8, "brantly.eth")


def test_a_busy_stranger_a_name_lists_links_nobody(db):
    mine(db, BRANTLY)
    mine(db, BRANTLY_BTC)
    names.sync(db, replay([], otherwise=dogecoin(5000)))  # read in part: some service
    assert name_links(db) == {pair(BRANTLY, BRANTLY_BTC): (0.8, "brantly.eth")}
    hub = db.execute("SELECT hub FROM node WHERE address = ?", (BRANTLY_DOGE,)).fetchone()
    assert hub["hub"] == 1


def test_several_names_listing_one_address_do_not_add_up(db):
    rows = [(*pair(A, BRANTLY_BTC), "name", 0.8, n) for n in ("a.eth", "a.base.eth")]
    db.executemany("INSERT INTO link VALUES (?, ?, ?, ?, ?)", rows)
    assert [k["confidence"] for k in graph._links(db)] == [0.8]


def test_no_name_lookup_with_labels_off(db, scans, monkeypatch):
    monkeypatch.setenv("CT_LABELS", "off")
    app, asked = scans
    r = app.get(f"{SCAN}{VITALIK}?chains=ethereum").json()
    assert not [f for f in r["findings"] if f["id"].startswith("x-name")]
    mine(db, VITALIK)
    calls = []
    assert names.sync(db, replay(calls)) == []
    assert name_calls(asked) == [] and calls == []
