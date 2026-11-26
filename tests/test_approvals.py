"""Open token approvals (docs/design/multichain.md, 2.3 #7). The logs in
fixtures/approvals.json were recorded from Routescan on 2026-11-26: every Approval event of
one Ethereum owner. Among them, WETH approved without limit to the Multichain router and
revoked at block 13983674, after its exploit, and WETH approved without limit to Uniswap's
SwapRouter02, never revoked."""

import json
from itertools import pairwise
from pathlib import Path

import httpx
import pytest
from test_core import mine, transfer
from test_wallet_score import scores

from tracker import chains, history
from tracker.exposure import approvals

RECORDED = json.loads((Path(__file__).parent / "fixtures" / "approvals.json").read_text())
OWNER = RECORDED["owner"]
WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
USDT = "0xdac17f958d2ee523a2206206994597c13d831ec7"
SWAP_ROUTER = "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45"  # Uniswap's SwapRouter02
MULTICHAIN = "0x6b7a87899490ece95443e979ca9485cbe7e71522"  # revoked after its exploit
NOT_SIGNED = "0x1b890fd37cd50bea59346fc2f8ddb7cd9f5fabd5"  # unlimited, but no `sent` row here
SPAMMED = "0x" + "5" * 40  # an owner some token named in 1,500 approvals in one block
SPAM_BLOCK = 13_000_000
FIRST_TS = 1609459200  # the owners' first transfer, in this test
FIRST_BLOCK = 11538000
HEAD = int(RECORDED["head"]["result"])
WIDEST = 1_000_000  # the most blocks the fake indexer answers for, as Routescan on Ethereum


def spam(n=1500):
    """`n` unlimited approvals naming SPAMMED as owner, from as many tokens, in one block."""
    model = RECORDED["logs"][0]
    owner = "0x" + SPAMMED[2:].rjust(64, "0")
    return [
        {
            **model,
            "address": f"0x{i + 1:040x}",
            "topics": [model["topics"][0], owner, model["topics"][2]],
            "data": "0x" + "f" * 64,
            "blockNumber": hex(SPAM_BLOCK),
            "logIndex": hex(i),
            "transactionHash": f"0x{i:064x}",
        }
        for i in range(n)
    ]


def indexer(asked, fail=(), head=HEAD):
    """Routescan as recorded: an owner's Approval logs in the blocks asked for, a page at a
    time, and a timeout for a wider range. Chains in `fail` answer HTTP 400."""
    logs = {OWNER: RECORDED["logs"], SPAMMED: spam()}

    def handler(request):
        p = request.url.params
        asked.append({**p, "path": request.url.path})
        if any(f"/{chains.EVM[c][0]}/" in request.url.path for c in fail):
            return httpx.Response(400)
        if p["module"] == "block":
            block = FIRST_BLOCK if int(p["timestamp"]) <= FIRST_TS else head
            return httpx.Response(200, json={**RECORDED["head"], "result": str(block)})
        low, high = int(p["fromBlock"]), int(p["toBlock"])
        if high - low + 1 > WIDEST:
            return httpx.Response(200, json=RECORDED["timeout"])
        assert p["topic0"] == approvals.APPROVAL
        rows = logs["0x" + p["topic1"][-40:]]
        rows = [r for r in rows if low <= int(r["blockNumber"], 16) <= high]
        page, size = int(p["page"]), int(p["offset"])
        rows = rows[(page - 1) * size : page * size]
        return httpx.Response(200, json={"status": "1", "message": "OK", "result": rows})

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(chains, "DELAY", 0)


def own(db, address, *used):
    """`address` as a wallet of mine, its history read in full, used on the chains `used`."""
    mine(db, address)
    for source in history.sources(address):
        history.put(db, address, source, complete=1)
    for chain in used:
        txid = f"0x{chain}{address}"
        transfer(db, chain, txid, address, "0x" + "9" * 40, 1.0, FIRST_TS, "WETH", WETH)
        db.execute("INSERT INTO sent VALUES (?, ?)", (address, txid))
    db.execute("UPDATE node SET synced_at = 1 WHERE address = ?", (address,))
    db.execute("INSERT OR REPLACE INTO meta VALUES (?, '1')", (f"names_at:{address}",))
    db.commit()


@pytest.fixture
def owner(db):
    """The recorded owner, on Ethereum, with the approvals it sent (all but one's)."""
    own(db, OWNER, "ethereum")
    db.executemany(
        "INSERT OR IGNORE INTO sent VALUES (?, ?)",
        [(OWNER, r["transactionHash"]) for r in RECORDED["logs"] if r["address"] != NOT_SIGNED],
    )
    db.execute(
        "INSERT INTO label(address, name, category, source) "
        "VALUES (?, 'Uniswap V3: Router 2', 'dex', 'etherscan-labels')",
        (SWAP_ROUTER,),
    )
    db.execute("INSERT INTO contract VALUES (?, 'AnyswapV4Router', 0)", (MULTICHAIN,))
    return db


def approved(db, address=OWNER):
    found = scores(db)[address]["by_chain"].get("evm", [])
    return next((f for f in found if f["id"] == "x-open-approvals"), None)


def listed(db, address=OWNER):
    return approvals.stored(db, address)[0]


def unread(*names):
    return {"id": "error", "params": {"error": f"approvals: not read yet on {', '.join(names)}"}}


def test_an_open_unlimited_approval_is_listed_with_its_spenders_label_and_a_revoked_one_is_not(
    owner,
):
    before = scores(owner)[OWNER]
    assert approved(owner) is None
    assert unread("Ethereum", "Avalanche") in before["gaps"]  # no list that reads as "none"
    assert approvals.sync(owner, indexer([])) == []
    f = approved(owner)
    assert "WETH → Uniswap V3: Router 2 (Ethereum)" in f["params"]["approvals"]
    # A verified contract name is its deployer's choice: the address goes with it.
    assert "→ AnyswapV4Router 0x6b7a…1522 (Ethereum)" in f["params"]["approvals"]
    spenders = {r["spender"] for r in listed(owner) if r["token"] == WETH}
    assert SWAP_ROUTER in spenders and MULTICHAIN not in spenders  # revoked at 13983674
    assert USDT in {r["token"] for r in listed(owner)}  # to Multichain: still open
    assert NOT_SIGNED not in {r["token"] for r in listed(owner)}
    assert f["scoreImpact"] == 0 and f["evidence"][0]["url"].startswith("https://etherscan.io/tx/")
    # Shown, never scored: only the gap went away.
    after = scores(owner)[OWNER]
    assert (after["score"], after["grade"], after["gaps"]) == (before["score"], before["grade"], [])


def test_a_chain_that_does_not_answer_hides_only_its_own(owner):
    transfer(owner, "avalanche", "0xa", OWNER, "0x" + "9" * 40, 1.0, FIRST_TS, "AVAX")
    for _ in range(3):
        notes = approvals.sync(owner, indexer([], fail=["avalanche"]))
    assert notes == ["approvals: avalanche: HTTP 400"]
    assert "WETH → Uniswap V3: Router 2 (Ethereum)" in approved(owner)["params"]["approvals"]
    got = scores(owner)[OWNER]
    assert got["gaps"] == [unread("Avalanche")] and got["partial"]


def test_it_reads_in_windows_the_indexer_answers_then_only_what_is_new(owner):
    asked = []
    approvals.sync(owner, indexer(asked))
    ranges = [(int(p["fromBlock"]), int(p["toBlock"])) for p in asked if p["module"] == "logs"]
    answered = [r for r in ranges if r[1] - r[0] < WIDEST]
    # From the block of its first transaction to the settled head, without a gap or overlap.
    assert answered[0][0] == FIRST_BLOCK and answered[-1][1] == HEAD - approvals.SETTLE
    assert all(b[0] == a[1] + 1 for a, b in pairwise(answered))
    assert len(ranges) - len(answered) <= 1  # one refusal, and the window learnt from it
    assert not [p for p in asked if "/43114/" in p["path"]]  # Avalanche: it never used it
    asked.clear()
    approvals.sync(owner, indexer(asked))
    assert asked == []  # read today, and it sent nothing since
    owner.execute("INSERT INTO sent VALUES (?, '0xnew')", (OWNER,))
    approvals.sync(owner, indexer(asked, head=HEAD + 100))
    logs = [p for p in asked if p["module"] == "logs"]
    assert [int(p["fromBlock"]) for p in logs] == [HEAD - approvals.SETTLE + 1]


def test_a_sync_stops_at_its_calls_and_the_next_goes_on(owner, monkeypatch):
    monkeypatch.setattr(approvals, "PER_SYNC", 5)
    asked = []
    approvals.sync(owner, indexer(asked))
    assert len(asked) == 5 and approved(owner) is None
    for _ in range(10):
        approvals.sync(owner, indexer(asked))
    assert approved(owner)


def test_a_full_page_is_paged_through(db):
    own(db, SPAMMED, "ethereum")
    asked = []
    assert approvals.sync(db, indexer(asked)) == []
    assert {p["page"] for p in asked if p["module"] == "logs"} == {"1", "2"}
    kept = db.execute("SELECT COUNT(*) FROM approval WHERE address = ?", (SPAMMED,))
    assert kept.fetchone()[0] == 1500
    assert approvals.state(db, SPAMMED, "ethereum")["done"]


def test_an_address_too_crowded_to_read_holds_up_no_other(db, monkeypatch):
    own(db, SPAMMED, "ethereum")  # read first: neither was read, and it was added first
    own(db, OWNER, "ethereum")
    monkeypatch.setattr(approvals, "PAGES", 1)  # its 1,500 approvals in one block do not fit
    monkeypatch.setattr(approvals, "PER_ADDRESS", 100)
    monkeypatch.setattr(approvals, "PER_SYNC", 200)
    asked = []
    notes = approvals.sync(db, indexer(asked))
    too_many = f"getLogs: too many approvals in block {SPAM_BLOCK}"
    assert notes == [f"approvals: ethereum: {SPAMMED}: {too_many}"]
    assert not approvals.state(db, SPAMMED, "ethereum")["done"]
    # The other owner reads with the indexer's window, not the crowded one's, to the end.
    first = next(p for p in asked if p["module"] == "logs" and OWNER[2:] in p["topic1"])
    assert int(first["toBlock"]) - int(first["fromBlock"]) + 1 == WIDEST
    assert approvals.state(db, OWNER, "ethereum")["done"]


def test_one_address_cannot_use_the_whole_sync(db, monkeypatch):
    own(db, SPAMMED, "ethereum")
    own(db, OWNER, "ethereum")
    monkeypatch.setattr(approvals, "PAGES", 1)
    asked = []
    approvals.sync(db, indexer(asked))
    assert len([p for p in asked if SPAMMED[2:] in p.get("topic1", "")]) < approvals.PER_ADDRESS
    assert [p for p in asked if OWNER[2:] in p.get("topic1", "")]  # the other one had its turn


def test_an_approval_of_a_token_id_or_with_no_amount_is_skipped(db):
    nft = {**RECORDED["logs"][0], "topics": RECORDED["logs"][0]["topics"] + ["0x" + "0" * 64]}
    empty = {**RECORDED["logs"][1], "data": "0x"}
    approvals._keep(db, OWNER, "ethereum", [nft, empty])
    assert db.execute("SELECT COUNT(*) FROM approval").fetchone()[0] == 0
