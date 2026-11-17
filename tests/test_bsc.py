"""BNB Chain through NodeReal's MegaNode API (free key), as its documentation shows it."""

import json

import httpx
import pytest
from test_core import A
from test_history import stored

from tracker import chains, history

PAYER, CONTRACT = "0x" + "e" * 40, "0x" + "c" * 40
USDT = "0x55d398326f99059fF775485246999027B3197955"  # as NodeReal writes it: mixed case


def nr(category, block, src, dst, value, **more):
    """One transfer as nr_getAssetTransfers answers it: hex block number and value."""
    return {
        "category": category, "blockNum": hex(block), "from": src, "to": dst, "value": hex(value),
        "asset": "BNB", "hash": f"0x{block:064x}", "blockTimeStamp": 1_700_000_000 + block,
        "receiptsStatus": 1,
    } | more  # fmt: skip


class NodeReal:
    """A fake bsc-mainnet.nodereal.io/v1/<key>: JSON-RPC, at most 100,000 blocks a query, and
    a page key after `per_page` rows."""

    def __init__(self, latest, transfers, per_page=1000):
        self.latest, self.transfers, self.per_page = latest, transfers, per_page
        self.asked = []

    def __call__(self, request):
        assert request.url.path == "/v1/NK"
        body = json.loads(request.content)
        method, params = body["method"], body["params"]
        self.asked.append((method, params))
        if method == "eth_blockNumber":
            result = hex(self.latest)
        elif method == "eth_getBalance":
            result = hex(3 * 10**18)
        elif method == "nr_getTokenHoldings":
            _, page, size = params
            assert int(size, 16) <= 100  # NodeReal's limit
            usdt = {"tokenAddress": USDT, "tokenName": "Tether USD", "tokenSymbol": "USDT"}
            usdt |= {"tokenDecimails": "0x12", "tokenBalance": hex(5 * 10**18)}
            result = {"totalCount": "0x1", "details": [usdt] if page == "0x1" else []}
        else:
            q = params[0]
            low, high = int(q["fromBlock"], 16), int(q["toBlock"], 16)
            if not 0 <= high - low <= 100_000:
                return httpx.Response(200, json={"error": {"message": "block range too large"}})
            side = "from" if "fromAddress" in q else "to"
            rows = sorted(
                (
                    t
                    for t in self.transfers
                    if t["category"] in q["category"]
                    and low <= int(t["blockNum"], 16) <= high
                    and t[side].lower() == q[side + "Address"]
                ),
                key=lambda t: -int(t["blockNum"], 16),
            )
            start = int(q.get("pageKey") or 0)
            result = {"transfers": rows[start : start + self.per_page]}
            if start + self.per_page < len(rows):
                result["pageKey"] = str(start + self.per_page)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})

    def windows(self):
        return [
            (int(p[0]["fromBlock"], 16), int(p[0]["toBlock"], 16))
            for m, p in self.asked
            if m == "nr_getAssetTransfers"
        ]


@pytest.fixture
def bsc(monkeypatch):
    monkeypatch.setattr(chains, "DELAY", 0)
    monkeypatch.setattr(chains, "NR_PAUSE", 0)
    cfg = chains.builtin_evm(nodereal="NK")["bsc"]
    monkeypatch.setattr(chains, "DEFAULT_EVM", {"bsc": cfg})
    return cfg


def test_bnb_chain_is_read_with_a_free_nodereal_key(bsc):
    assert "CT_NODEREAL_KEY" in chains.builtin_evm()["bsc"]["needs_key"]
    assert not bsc.get("needs_key") and bsc["url"] == "https://bsc-mainnet.nodereal.io/v1/NK"
    assert history.sources(A) == ["bsc:transfers"]  # one history: the three lists at once


def test_a_bnb_chain_snapshot_reads_the_newest_blocks_balances_and_tokens(bsc):
    claim = nr("external", 980_000, A, CONTRACT, 0)  # moves nothing, but A sent it
    fake = NodeReal(
        1_000_000,
        [
            nr("external", 990_000, PAYER, A, 10**18),
            claim,
            nr("internal", 970_000, CONTRACT, A, 5 * 10**17),
            nr("20", 960_000, PAYER, A, 7 * 10**18, asset="USDT", contractAddress=USDT)
            | {"decimal": "18"},
            nr("external", 950_000, PAYER, A, 10**18, receiptsStatus=0),  # failed
            nr("external", 800_000, PAYER, A, 10**18),  # older than the snapshot reads
        ],
    )
    with httpx.Client(transport=httpx.MockTransport(fake)) as client:
        out = chains.fetch_evm(client, A, {"bsc": bsc})
    usdt = USDT.lower()
    assert out["errors"] == []
    assert sorted(out["transfers"], key=lambda t: -t[7]) == [
        ("bsc", f"0x{990_000:064x}", PAYER, A, "BNB", "", 1.0, 1_700_990_000),
        ("bsc", f"0x{970_000:064x}", CONTRACT, A, "BNB", "", 0.5, 1_700_970_000),
        ("bsc", f"0x{960_000:064x}", PAYER, A, "USDT", usdt, 7.0, 1_700_960_000),
    ]
    assert claim["hash"] in out["sent"]  # the fake-token check needs what A sent
    assert out["balances"] == [("bsc", "BNB", "", 3.0), ("bsc", "USDT", usdt, 5.0)]
    assert out["names"] == {("bsc", usdt): "Tether USD"} and not out["hub"]
    assert all(high - low <= 100_000 for low, high in fake.windows())


def test_a_bnb_chain_wallet_fills_in_window_by_window(db, bsc, monkeypatch):
    from test_core import mine

    from tracker import sync

    monkeypatch.setattr(history, "BUDGET", {"default": 40, "bsc": 2})
    mine(db, A)
    db.commit()
    fake = NodeReal(
        450_000,
        [
            nr("external", 440_000, PAYER, A, 10**18),
            nr("external", 300_000, PAYER, A, 10**18),
            nr("external", 300_001, PAYER, A, 2 * 10**18),  # two in a window: a page key
            nr("external", 120_000, A, PAYER, 10**17),
            nr("internal", 5, CONTRACT, A, 10**18),
        ],
        per_page=1,
    )

    def run():
        with httpx.Client(transport=httpx.MockTransport(fake)) as client:
            sync.sync_address(db, client, A)

    for _ in range(4):  # five windows of 100,000 blocks; two a sync, one of them for new ones
        run()
    assert stored(db) == 5 and history.state(db, A)["complete"]
    assert all(high - low <= 100_000 for low, high in fake.windows())
    # Something new, more windows up than one sync reads: a gap, closed on the next sync.
    fake.latest = 700_000
    fake.transfers.append(nr("external", 650_000, PAYER, A, 10**18))
    run()
    assert stored(db) == 6 and not history.state(db, A)["complete"]
    run()
    assert stored(db) == 6 and history.state(db, A)["complete"]


@pytest.fixture
def quiet(db, bsc, monkeypatch):
    """A wallet of mine, two windows a sync: run(fake) syncs it once."""
    from test_core import mine

    from tracker import sync

    monkeypatch.setattr(history, "BUDGET", {"default": 40, "bsc": 2})
    mine(db, A)
    db.commit()

    def run(fake):
        with httpx.Client(transport=httpx.MockTransport(fake)) as client:
            sync.sync_address(db, client, A)

    return run


def test_a_wallet_quiet_for_longer_than_a_sync_reads_still_fills_in(db, quiet):
    # Empty windows between the chain's head and the newest transfer must not stop the read:
    # the bookmark is the newest block read, not the newest transfer.
    fake = NodeReal(450_000, [nr("external", 440_000, PAYER, A, 10**18)])
    fake.transfers.append(nr("external", 120_000, PAYER, A, 10**18))
    quiet(fake)
    fake.latest = 800_000  # 350,000 blocks later, nothing new
    for _ in range(6):
        quiet(fake)
    assert stored(db) == 2 and history.state(db, A)["complete"]


def test_an_address_never_used_on_bnb_chain_is_complete_once_read(db, quiet):
    fake = NodeReal(450_000, [])
    for _ in range(6):
        quiet(fake)
    assert history.state(db, A)["complete"]
    asked = len(fake.windows())
    quiet(fake)  # from then on, a sync reads the newest window and stops
    assert len(fake.windows()) - asked == 2 * 2  # the snapshot's window, and history's
