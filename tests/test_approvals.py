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
from test_wallet_score import scores, synced

from tracker import chains, history
from tracker.exposure import approvals

RECORDED = json.loads((Path(__file__).parent / "fixtures" / "approvals.json").read_text())
OWNER = RECORDED["owner"]
WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
USDT = "0xdac17f958d2ee523a2206206994597c13d831ec7"
SWAP_ROUTER = "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45"  # Uniswap's SwapRouter02
MULTICHAIN = "0x6b7a87899490ece95443e979ca9485cbe7e71522"  # revoked after its exploit
NOT_SIGNED = "0x1b890fd37cd50bea59346fc2f8ddb7cd9f5fabd5"  # unlimited, but no `sent` row here
FIRST_TS = 1609459200  # the owner's first transfer, in this test
FIRST_BLOCK = 11538000
HEAD = int(RECORDED["head"]["result"])
WIDEST = 1_000_000  # the most blocks the fake indexer answers for, as Routescan on Ethereum


def indexer(asked, refuse_above=WIDEST, fail=False, head=HEAD):
    """Routescan as recorded: the owner's Approval logs in the blocks asked for, a timeout
    for a wider range."""

    def handler(request):
        p = request.url.params
        asked.append({**p, "path": request.url.path})
        if fail:
            return httpx.Response(400)
        if p["module"] == "block":
            ts = int(p["timestamp"])
            block = FIRST_BLOCK if ts <= FIRST_TS else head
            return httpx.Response(200, json={**RECORDED["head"], "result": str(block)})
        low, high = int(p["fromBlock"]), int(p["toBlock"])
        if high - low + 1 > refuse_above:
            return httpx.Response(200, json=RECORDED["timeout"])
        assert p["topic1"] == "0x" + OWNER[2:].rjust(64, "0") and p["topic0"] == approvals.APPROVAL
        rows = [r for r in RECORDED["logs"] if low <= int(r["blockNumber"], 16) <= high]
        return httpx.Response(200, json={"status": "1", "message": "OK", "result": rows})

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(chains, "DELAY", 0)
    monkeypatch.setattr(approvals, "windows", {})
    monkeypatch.setattr(approvals, "limits", {})


@pytest.fixture
def owner(db):
    """The recorded owner as a wallet of mine, its history read in full, with a WETH transfer
    and the approvals it sent (all but one)."""
    mine(db, OWNER)
    for source in history.sources(OWNER):
        history.put(db, OWNER, source, complete=1)
    transfer(db, "ethereum", "0xw", OWNER, "0x" + "9" * 40, 1.0, FIRST_TS, "WETH", WETH)
    db.execute("INSERT INTO sent VALUES (?, '0xw')", (OWNER,))
    db.executemany(
        "INSERT OR IGNORE INTO sent VALUES (?, ?)",
        [(OWNER, r["transactionHash"]) for r in RECORDED["logs"] if r["address"] != NOT_SIGNED],
    )
    db.execute(
        "INSERT INTO label(address, name, category, source) VALUES (?, ?, 'dex', 'etherscan-labels')",
        (SWAP_ROUTER, "Uniswap V3: Router 2"),
    )
    synced(db, OWNER)
    return db


def approved(db):
    found = scores(db)[OWNER]["by_chain"].get("evm", [])
    return next((f for f in found if f["id"] == "x-open-approvals"), None)


def test_an_open_unlimited_approval_is_listed_with_its_spenders_label_and_a_revoked_one_is_not(
    owner,
):
    before = scores(owner)[OWNER]
    assert approved(owner) is None  # never read: nothing listed, and the grade is not partial
    assert not before["partial"]
    asked = []
    assert approvals.sync(owner, indexer(asked)) == []
    f = approved(owner)
    assert "WETH → Uniswap V3: Router 2 (Ethereum)" in f["params"]["approvals"]
    spenders = {r["spender"] for r in approvals.stored(owner, OWNER) if r["token"] == WETH}
    assert SWAP_ROUTER in spenders and MULTICHAIN not in spenders  # revoked at 13983674
    assert USDT in {r["token"] for r in approvals.stored(owner, OWNER)}  # to Multichain: open
    assert NOT_SIGNED not in {r["token"] for r in approvals.stored(owner, OWNER)}
    assert f["scoreImpact"] == 0 and f["evidence"][0]["url"].startswith("https://etherscan.io/tx/")
    # Shown, never scored.
    after = scores(owner)[OWNER]
    assert (after["score"], after["grade"], after["partial"]) == (
        before["score"],
        before["grade"],
        before["partial"],
    )


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
    assert len(asked) == 5 and approvals.stored(owner, OWNER) is None
    for _ in range(10):
        approvals.sync(owner, indexer(asked))
    assert approvals.stored(owner, OWNER)


def test_an_indexer_that_fails_is_reported_and_lists_nothing(owner):
    notes = approvals.sync(owner, indexer([], fail=True))
    assert notes == ["approvals: ethereum: HTTP 400"]
    assert approvals.stored(owner, OWNER) is None and approved(owner) is None


def test_an_approval_of_a_token_id_or_with_no_amount_is_skipped(db):
    nft = {**RECORDED["logs"][0], "topics": RECORDED["logs"][0]["topics"] + ["0x" + "0" * 64]}
    empty = {**RECORDED["logs"][1], "data": "0x"}
    approvals._keep(db, OWNER, "ethereum", [nft, empty])
    assert db.execute("SELECT COUNT(*) FROM approval").fetchone()[0] == 0
