"""Mixer links: Tornado Cash deposits by one wallet of mine and a withdrawal to another
(tracker/mixers.py).

The receipts and pool logs in fixtures/mixers are real Ethereum answers recorded from
Routescan. In January 2024 one address made six deposits in a row into the 100 ETH pool
through Tornado's router (`DEPOSITS`, oldest first). Three later withdrawals from that pool
came after 2, 4 and 44 deposits by others.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from test_core import mine, transfer

from tracker import chains, detect, exposure, graph, labels, mixers, prices, swaps, sync, tokens
from tracker.exposure import names, wallets
from tracker.heuristics import pair

FIXTURES = Path(__file__).parent / "fixtures" / "mixers"
EVM = {
    "ethereum": {
        "url": "https://api.routescan.io/v2/network/mainnet/evm/1/etherscan/api",
        "symbol": "ETH",
    }
}
POOL = "0xa160cdab225685da1d56aa342ad8841c3b53f291"  # Tornado Cash, 100 ETH
ROUTER = "0xd90e2f925da726b50c4ed8d0fb90ad053324f31b"
DEPOSITOR = "0xb529ba5dae11cb5d452128129664def1d568ecbe"
TWO = "0x632ba6dab1c93d4ac42402f83d6bcbe5ccf78485"  # withdrew after 2 more deposits
FOUR = "0xcfb8f97f1602c3a8fa91d19f62c069457e1a8c37"  # after 4
CROWD = "0x455495b58b8096e429e624cdfaf4b875f0893156"  # after 44
RECEIPTS = json.loads((FIXTURES / "receipts.json").read_text())
PAGES = json.loads((FIXTURES / "deposits.json").read_text())  # "from-to-offset" -> answer
BLOCK = {tx: int(r["result"]["blockNumber"], 16) for tx, r in RECEIPTS.items()}
DEPOSITS = sorted((tx for tx, r in RECEIPTS.items() if r["result"]["from"] == DEPOSITOR),
                  key=BLOCK.get)  # fmt: skip
TX = {to: next(tx for tx, r in RECEIPTS.items() if r["result"]["from"] == to)
      for to in (TWO, FOUR, CROWD)}  # fmt: skip  # each withdrew itself, through the router


class Routescan:
    """Answers from the recordings; every query is noted."""

    def __init__(self, pages=PAGES):
        self.pages = pages
        self.asked = []

    def __call__(self, request):
        q = dict(request.url.params)
        self.asked.append(q)
        if q["module"] == "proxy":
            return httpx.Response(200, json=RECEIPTS[q["txhash"]])
        assert (q["module"], q["address"], q["topic0"]) == ("logs", POOL, mixers.DEPOSIT)
        return httpx.Response(
            200, json=self.pages[f"{q['fromBlock']}-{q['toBlock']}-{q['offset']}"]
        )


@pytest.fixture
def quiet(monkeypatch):
    monkeypatch.setattr(chains, "DEFAULT_EVM", EVM)
    monkeypatch.setattr(chains, "DELAY", 0)
    monkeypatch.setattr(chains.time, "sleep", lambda s: None)


def run(db, fake):
    with httpx.Client(transport=httpx.MockTransport(fake)) as client:
        return mixers.sync(db, client)


def legs(db, *withdrawals, deposits=DEPOSITS[-1:]):
    """The depositor's deposits through the router (its last one unless told), and the
    withdrawals to the wallets of mine named."""
    mine(db, DEPOSITOR)
    for tx in deposits:
        transfer(db, "ethereum", tx, DEPOSITOR, ROUTER, 100, ts=BLOCK[tx])
    for to in withdrawals:
        mine(db, to)
        transfer(db, "ethereum", TX[to], POOL, to, 100, ts=BLOCK[TX[to]])
    db.commit()


def links(db):
    return {(r["a"], r["b"]): r["confidence"] for r in db.execute("SELECT * FROM link")}


def logs(fake):
    return [
        (q["fromBlock"], q["toBlock"], q["offset"]) for q in fake.asked if q["module"] == "logs"
    ]


def test_few_deposits_between_give_the_bound_never_above_0_3(db, quiet):
    legs(db, TWO, FOUR)

    assert run(db, Routescan()) == []

    # 2 deposits between: 1/3, held at 0.3. 4 between: 1/5.
    assert links(db) == {pair(DEPOSITOR, TWO): 0.3, pair(DEPOSITOR, FOUR): 0.2}
    (evidence,) = [
        r["evidence"] for r in db.execute("SELECT * FROM link WHERE ? IN (a, b)", (FOUR,))
    ]
    assert evidence == f"Tornado Cash 100 ETH: 1 of the last 5 deposits ({DEPOSITS[-1][:18]} > " \
                       f"{TX[FOUR][:18]})"  # fmt: skip
    (link,) = [k for k in graph.build(db)["links"] if FOUR in (k["a"], k["b"])]
    assert (link["kind"], link["confidence"]) == ("mixer", 0.2)


def test_a_large_pool_gives_no_link(db, quiet):
    """44 deposits between: the answer holds a full page of 20, which is enough to say the pair
    is hidden in the crowd (a bound under 5%)."""
    legs(db, CROWD)
    fake = Routescan()

    assert run(db, fake) == []

    assert links(db) == {}
    (scan,) = db.execute("SELECT * FROM mixer_scan").fetchall()
    assert dict(scan) == {"chain": "ethereum", "pool": POOL, "low": 19010516, "high": 19045906,
                          "deposits": 20, "page": 20}  # fmt: skip


def test_several_deposits_of_one_wallet_count_together(db, quiet):
    """Each of the six deposits could be the one withdrawn: the oldest makes 6 of the last 10
    deposits before FOUR's withdrawal (0.6, held at 0.3), and 6 of the last 50 before CROWD's,
    which one deposit alone would leave hidden in the crowd."""
    legs(db, FOUR, CROWD, deposits=DEPOSITS)
    fake = Routescan()

    assert run(db, fake) == []

    assert links(db) == {pair(DEPOSITOR, FOUR): 0.3, pair(DEPOSITOR, CROWD): 0.12}
    (evidence,) = [
        r["evidence"] for r in db.execute("SELECT * FROM link WHERE ? IN (a, b)", (CROWD,))
    ]
    assert evidence.startswith("Tornado Cash 100 ETH: 6 of the last 50 deposits (")
    # One window per deposit, each in one page big enough for all six (6 x 20).
    assert {offset for _, _, offset in logs(fake)} == {"120"}
    assert len(logs(fake)) == 12


def test_older_deposits_are_not_counted_once_the_crowd_hides_them(db, quiet):
    """A page as big as 20 per deposit of the wallet that comes back full hides every older
    window too: one more deposit before it cannot reach 5%. So nothing older is asked."""
    a, b = "0x" + "a" * 40, "0x" + "b" * 40
    mine(db, a)
    mine(db, b)
    rows = [("d1", a, "deposit", 100), ("d2", a, "deposit", 1000), ("w", b, "withdrawal", 1100)]
    db.executemany(
        "INSERT INTO mixer_leg VALUES ('ethereum', ?, ?, ?, ?, ?)",
        [(tx, POOL, who, side, block) for tx, who, side, block in rows],
    )
    row = PAGES["19010516-19045906-20"]["result"][0]
    fake = Routescan({"1001-1099-40": {"status": "1", "message": "OK", "result": [row] * 40}})

    assert run(db, fake) == []

    assert logs(fake) == [("1001", "1099", "40")]
    assert links(db) == {}


def test_the_pool_scan_is_cached_by_block_range(db, quiet):
    legs(db, TWO)
    run(db, Routescan())
    again = Routescan()

    assert run(db, again) == []
    assert again.asked == []  # receipts read once, the count kept for its block range
    assert links(db) == {pair(DEPOSITOR, TWO): 0.3}

    # A later withdrawal is a new range: one more page, and the old range is not asked again.
    mine(db, FOUR)
    transfer(db, "ethereum", TX[FOUR], POOL, FOUR, 100)
    db.commit()
    later = Routescan()
    run(db, later)
    assert logs(later) == [("19010516", "19020223", "20")]
    assert links(db) == {pair(DEPOSITOR, TWO): 0.3, pair(DEPOSITOR, FOUR): 0.2}


def test_the_count_leaves_out_the_deposits_and_the_withdrawals_blocks(db, quiet):
    """A deposit later in the withdrawal's own block cannot be its source; one earlier in it
    could, and so could a stranger's in the deposit's block: leaving both blocks out errs
    toward a higher bound. Back to back, nothing is between them, and nothing is asked."""
    a, b = "0x" + "a" * 40, "0x" + "b" * 40
    mine(db, a)
    mine(db, b)
    db.executemany(
        "INSERT INTO mixer_leg VALUES ('ethereum', ?, ?, ?, ?, ?)",
        [("d", POOL, a, "deposit", 500), ("w", POOL, b, "withdrawal", 501)],
    )
    fake = Routescan({})

    assert run(db, fake) == []

    assert logs(fake) == []
    assert links(db) == {pair(a, b): 0.3}


def test_only_a_deposit_and_a_withdrawal_of_two_wallets_of_mine_link(db, quiet):
    # The depositor is a stranger: its deposit is no leg of mine, and nothing is asked.
    mine(db, TWO)
    transfer(db, "ethereum", DEPOSITS[-1], DEPOSITOR, ROUTER, 100)
    transfer(db, "ethereum", TX[TWO], POOL, TWO, 100)
    db.commit()
    fake = Routescan()
    run(db, fake)
    assert links(db) == {}
    assert [q["module"] for q in fake.asked] == ["proxy"]  # the withdrawal's receipt only


def test_a_withdrawal_to_someone_else_is_no_leg(db, quiet):
    """The pool pays a relayer its fee in the same transaction: the Withdrawal event names the
    recipient, and a wallet of mine that only got the fee did not withdraw."""
    legs(db)
    relayer = "0x" + "5" * 40
    mine(db, relayer)
    transfer(db, "ethereum", TX[FOUR], POOL, relayer, 0.5)
    db.commit()
    run(db, Routescan())
    assert [tuple(r) for r in db.execute("SELECT address, side FROM mixer_leg")] == [
        (DEPOSITOR, "deposit")
    ]
    assert links(db) == {}


def test_a_withdrawal_that_also_paid_a_relayer_of_mine_is_the_recipients_leg(db, quiet):
    """The receipt is read once, for whichever of the two transfers comes first: the leg goes
    to the recipient the Withdrawal event names."""
    legs(db)
    relayer = "0x" + "5" * 40
    mine(db, relayer)
    transfer(db, "ethereum", TX[FOUR], POOL, relayer, 0.5)
    mine(db, FOUR)
    transfer(db, "ethereum", TX[FOUR], POOL, FOUR, 99.5)
    db.commit()
    run(db, Routescan())
    assert links(db) == {pair(DEPOSITOR, FOUR): 0.2}


def test_a_withdrawal_before_the_deposit_or_by_the_same_wallet_links_nothing(db):
    a, b = "0x" + "a" * 40, "0x" + "b" * 40
    for address in (a, b):
        mine(db, address)
    db.execute("UPDATE node SET wallet = 'hd' WHERE address IN (?, ?)", (a, b))  # one HD wallet
    c = "0x" + "c" * 40
    mine(db, c)
    rows = [("d1", POOL, a, "deposit", 100), ("w1", POOL, b, "withdrawal", 200),  # same wallet
            ("w2", POOL, c, "withdrawal", 50)]  # fmt: skip  # before the deposit
    db.executemany("INSERT INTO mixer_leg VALUES ('ethereum', ?, ?, ?, ?, ?)", rows)
    assert mixers.pairs(db) == []


def test_a_mixer_link_is_a_finding_of_both_wallets(db, quiet):
    legs(db, TWO, FOUR, deposits=DEPOSITS)
    db.execute("UPDATE node SET name = 'Savings' WHERE address = ?", (DEPOSITOR,))
    run(db, Routescan())
    scan = lambda address: list(exposure.mixer.check(SimpleNamespace(db=db, address=address)))

    ((found, params, evidence),) = scan(FOUR)
    assert found == "x-mixer-linked"
    assert params == {"pool": "100 ETH", "count": 10, "own": 6, "confidence": 30,
                      "wallets": "Savings"}  # fmt: skip
    assert evidence == [{"chain": "ethereum", "txid": DEPOSITS[0]},
                        {"chain": "ethereum", "txid": TX[FOUR]}]  # fmt: skip
    ((found, params, _),) = scan(DEPOSITOR)  # the strongest of its two links
    assert params["wallets"] == f"{TWO}, {FOUR}"

    db.execute("UPDATE node SET synced_at = 1")
    got = wallets.scores(db, graph.verdicts(db))[FOUR]
    (f,) = [f for f in got["by_chain"]["evm"] if f["id"] == "x-mixer-linked"]
    assert (f["scoreImpact"], f["confidence"]) == (-5, "low")


def test_a_pool_without_a_label_ends_every_path(db):
    """A token deposit goes straight to the pool, and an ETH withdrawal comes straight from it:
    without a label, the pool would join the two wallets as a stranger they both paid."""
    a, b = "0x" + "a" * 40, "0x" + "b" * 40
    mine(db, a)
    mine(db, b)
    pool = "0x84443cfd09a48af6ef360c6976c5392ac5023a1f"  # 0.1 BNB, which no label set names
    token = "0x" + "7" * 40
    transfer(db, "bsc", "t1", a, pool, 0.1, asset="X", token=token)
    transfer(db, "bsc", "t2", pool, b, 0.1, asset="X", token=token)
    db.commit()
    assert graph.trace(db) == []


def test_an_indexer_that_fails_marks_the_verdict_partly_checked(db, quiet):
    legs(db, TWO)

    def down(request):
        return httpx.Response(503)

    assert run(db, down) == ["Mixer pools: ethereum: HTTP 503"]
    assert swaps.unchecked(db) == [
        "Mixer pools were not all read (ethereum: HTTP 503), so a mixer link may be missing"
    ]
    assert run(db, Routescan()) == []  # read again at the next sync
    assert swaps.unchecked(db) == []
    assert links(db) == {pair(DEPOSITOR, TWO): 0.3}


def test_bnb_chain_through_nodereal_reads_no_receipt(db, monkeypatch, quiet):
    """NodeReal has no logs module, so the deposits between could not be counted: its
    receipts are not read for nothing."""
    monkeypatch.setattr(chains, "DEFAULT_EVM", {"bsc": {"url": "https://nr", "api": "nodereal"}})
    mine(db, DEPOSITOR)
    transfer(db, "bsc", "0x" + "1" * 64, DEPOSITOR, "0x0d5550d52428e7e3175bfc9550207e4ad3859b17", 1)
    db.commit()

    def refuse(request):
        raise AssertionError("no call expected")

    assert run(db, refuse) == []


def test_a_sync_reads_my_mixer_legs_and_links_them(db, quiet, monkeypatch):
    legs(db, TWO)
    real = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(Routescan()), **kw)
    )
    # The steps that read anything else: the addresses themselves, labels, names, prices.
    monkeypatch.setattr(sync, "sync_address", lambda db, client, address: None)
    monkeypatch.setattr(labels, "due", lambda db: False)
    monkeypatch.setattr(detect, "contracts", lambda db, client: 0)
    monkeypatch.setattr(names, "sync", lambda db, client: [])
    monkeypatch.setattr(swaps, "sync", lambda db, client: [])
    monkeypatch.setattr(prices, "snapshot", lambda db, client: None)
    monkeypatch.setattr(tokens, "mark", lambda db, client: None)
    assert sync._lock.acquire(blocking=False)
    sync.status.update(error="", notes=[])

    sync.run()

    assert (sync.status["error"], sync.status["notes"]) == ("", [])
    assert links(db) == {pair(DEPOSITOR, TWO): 0.3}
