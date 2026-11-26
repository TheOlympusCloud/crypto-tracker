"""NFT transfers between my wallets (ERC-721 and ERC-1155). recorded/nfts.json holds real rows
of Routescan's keyless tokennfttx and token1155tx lists for one Ethereum address, recorded on
2026-11-26, trimmed of their calldata (input, functionName, methodId)."""

import json
from pathlib import Path

import httpx
import pytest
from test_core import mine

from tracker import chains, graph, history, sync

RECORDED = json.loads((Path(__file__).parent / "recorded/nfts.json").read_text())
W = "0x020ca66c30bec2c4fe3861a94e4db4a498a35872"  # the address read
TUBBY = "0xd875e4d4637577e5e9ab4808cb321d8e03aa5c33"  # got Tubby Cats #4702 (ERC-721) from W
ROCKS = "0x612fdce48d0de2abae950440f72d8acf7c8aee40"  # got Mars Rocks #11 (ERC-1155) from W
AIRDROPPER = "0xb3d03803aab186c613df9afcd8104d653620b873"  # sent W an OpenSea storefront NFT
BAIT = "0x0a4c79ce84202b03e95b7a692e5d728d83c44c76"  # sent W a "claim rewards" NFT
STOREFRONT = "0x495f947276749ce646f68ac8c248420045cb7b5e"  # OpenSea's shared ERC-1155 contract


def test_nft_rows_are_transfers_of_one_nft_and_mints_are_not():
    nfts = chains.nft_transfers("ethereum", RECORDED["tokennfttx"] + RECORDED["token1155tx"])
    by_dst = {t[3]: t for t in nfts}
    assert by_dst[TUBBY] == (
        "ethereum", "0x0edf8eac429b723de5788b01bf313de1fb4c2935ae9d57cc612eceaa72520dbe", W,
        TUBBY, "TUBBY", "0xca7ca7bcc765f77339be2d648ba53ce9c8a262bd#4702", 1, 1795693619,
    )  # fmt: skip
    # An ERC-1155 row says how many; without a symbol, the collection's name stands for it.
    rocks = ("Tom Sachs: Rocket Fa", "0xedc227ad7fef4a2c6dbc2303a5732087314a4ce9#11", 1)
    assert by_dst[ROCKS][4:7] == rocks
    # The fake "BAYC" minted to W comes from the zero address: a mint is a transfer with nobody.
    assert {t[2] for t in nfts} == {W, AIRDROPPER, BAIT}
    assert chains.nft_transfers("ethereum", [{"hash": "x"}, None]) == []  # rows not understood


def indexer(rows):
    """An Etherscan-style indexer with `rows` per list: an address's rows, newest first, below
    `endblock`, `offset` at a time."""

    def handler(request):
        q = request.url.params
        if q["action"] == "balance":
            return httpx.Response(200, json={"status": "1", "result": "0"})
        address = q["address"]
        found = [
            r
            for r in rows.get(q["action"], [])
            if address in (r["from"], r["to"])
            and int(r["blockNumber"]) <= int(q.get("endblock") or 10**12)
        ][: int(q["offset"])]
        return httpx.Response(200, json={"status": "1", "message": "OK", "result": found})

    return handler


@pytest.fixture
def ethereum(monkeypatch):
    monkeypatch.setattr(chains, "DELAY", 0)
    monkeypatch.setattr(
        chains,
        "DEFAULT_EVM",
        {"ethereum": {"url": "http://eth/api", "symbol": "ETH", "llama": "ethereum"}},
    )


def run(db, rows, *addresses):
    with httpx.Client(transport=httpx.MockTransport(indexer(rows))) as client:
        for address in addresses:
            sync.sync_address(db, client, address)


def test_an_nft_moved_between_my_wallets_links_them_and_an_airdrop_draws_nothing(db, ethereum):
    for address in (W, TUBBY, ROCKS):
        mine(db, address)
    # W also sends one storefront NFT of its own, to show that what my wallets send is trusted
    # one NFT at a time: the airdropped one, in the same shared contract, stays ignored.
    airdrop = next(r for r in RECORDED["token1155tx"] if r["from"] == AIRDROPPER)
    sent = airdrop | {"hash": "0x" + "5" * 64, "from": W, "to": ROCKS, "tokenID": "7"}
    rows = {**RECORDED, "token1155tx": RECORDED["token1155tx"] + [sent]}
    run(db, rows, W, TUBBY, ROCKS)

    verdict = {v["address"]: v for v in graph.verdicts(db)}
    linked = {k["wallet"]: k for k in verdict[W]["links"]}
    assert set(linked) == {TUBBY, ROCKS}
    assert linked[TUBBY]["confidence"] == 1.0 and linked[TUBBY]["path"] == [W, TUBBY]
    assert linked[ROCKS]["confidence"] == 1.0 and linked[ROCKS]["path"] == [W, ROCKS]
    drawn = graph.build(db)
    edges = drawn["edges"]
    assert {(e["src"], e["dst"]) for e in edges} == {(W, TUBBY), (W, ROCKS)}
    # Drawn per collection: Mars Rocks and the storefront NFT are two edges, one NFT each.
    assert sorted((e["token"], e["amount"]) for e in edges if e["dst"] == ROCKS) == [
        (STOREFRONT, 1),
        ("0xedc227ad7fef4a2c6dbc2303a5732087314a4ce9", 1),
    ]
    assert not {AIRDROPPER, BAIT, "0x" + "0" * 40} & {n["address"] for n in drawn["nodes"]}
    # An NFT is no money: it has no funds origin, and is no balance of the portfolio, also
    # where balances follow from the history.
    assert not graph.origins(db).get(TUBBY) and not graph.origins(db).get(ROCKS)
    assert db.execute("SELECT COUNT(*) FROM balance WHERE token LIKE '%#%'").fetchone()[0] == 0


def test_the_nft_lists_fill_in_like_the_others(db, ethereum, monkeypatch):
    monkeypatch.setattr(chains, "EVM_ROWS", 1)  # a page per row
    monkeypatch.setattr(history, "BUDGET", {"default": 2})
    mine(db, W)
    assert history.sources(W) == [
        "ethereum:txlist", "ethereum:txlistinternal", "ethereum:tokentx",
        "ethereum:tokennfttx", "ethereum:token1155tx",
    ]  # fmt: skip
    run(db, RECORDED, W)
    assert not history.state(db, W)["complete"]  # 5 ERC-1155 pages to read, 2 per sync
    for _ in range(3):
        run(db, RECORDED, W)
    assert history.state(db, W)["complete"]
    nft = history.get(db, W, "ethereum:tokennfttx")
    assert nft["complete"] and nft["newest"] == 26499168 and nft["oldest_ts"] == 1795693619
    many = history.get(db, W, "ethereum:token1155tx")
    assert many["complete"] and many["newest"] == 26419969
    assert many["oldest_ts"] == 1742156927  # the bait's: the mint before it is no transfer
    stored = db.execute("SELECT COUNT(*) FROM transfer WHERE token LIKE '%#%'").fetchone()[0]
    assert stored == 4  # each read once, the mint left out


def test_an_nft_is_never_taken_for_a_bridged_amount(db):
    from test_core import A, B, C, D, transfer

    from tracker import heuristics

    for address in (A, B, C):
        mine(db, address)
    # Two NFTs of one collection, moved on two chains an hour apart: "1 TUBBY" out on Ethereum
    # and "1 TUBBY" in on Base is no bridge between A and B.
    transfer(db, "ethereum", "out", A, D, 1, ts=1000, asset="TUBBY", token=f"{D}#1")
    transfer(db, "base", "in", C, B, 1, ts=4600, asset="TUBBY", token=f"{D}#2")
    heuristics.match_bridges(db)
    assert db.execute("SELECT COUNT(*) FROM link WHERE kind = 'bridge'").fetchone()[0] == 0


def test_an_nft_is_never_taken_for_an_exchange_flow(db):
    from test_core import BINANCE, A, B, C, D, transfer

    from tracker import heuristics

    for address in (A, B):
        mine(db, address)
    db.execute(
        "INSERT INTO label(address, name, category, source) VALUES (?, 'Coinbase', 'cex', 'user')",
        (BINANCE,),
    )
    # One NFT of a collection leaves A, another reaches B from an exchange two hours later and
    # moves on (so it is trusted): "1 TUBBY" out and "1 TUBBY" in are no flow through it.
    transfer(db, "ethereum", "t1", A, D, 1, ts=1000, asset="TUBBY", token=f"{C}#1")
    transfer(db, "ethereum", "t2", BINANCE, B, 1, ts=8200, asset="TUBBY", token=f"{C}#2")
    transfer(db, "ethereum", "t3", B, D, 1, ts=9000, asset="TUBBY", token=f"{C}#2")
    heuristics.match_exchange_flows(db)
    assert db.execute("SELECT COUNT(*) FROM link WHERE kind = 'exchange-flow'").fetchone()[0] == 0
