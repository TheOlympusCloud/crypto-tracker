"""Solana links: fee payers, co-signers, and the rent of closed token accounts.

The transactions are getTransaction results recorded from the public RPC on 2026-11-25
(jsonParsed), trimmed to the fields the tracker reads (tests/fixtures/solana-signers.json).
"""

import copy
import json
from pathlib import Path

import httpx
import pytest
from test_core import mine

from tracker import chains, graph, heuristics, history, sync

TXS = json.loads((Path(__file__).parent / "fixtures" / "solana-signers.json").read_text())


def sig(prefix):
    return next(s for s in TXS if s.startswith(prefix))


# 2yQ9...: 8XjZ closes its token account PAf1 and sends the rent to Abha, who paid the fee.
OWNER, PAYER, ACCOUNT = (
    "8XjZAkgMufnNranwQLkeEzYcavxs6SJuGDfKZEBcipjo",
    "AbhaBP8oqn6Y12NohnjkZAfVL9355A54XV3tGLYxb1NZ",
    "PAf1KwLyB8fEHB25mVmPmLVVP2ENQGkaWUcpTv4CxDp8",
)
# 381r...: three signers; 5HQZ pays the fee, D4f1 sends 241.22 USDT to 5HQZ.
FIRST, SECOND, THIRD = (
    "5HQZd9ovzAF1TLnHRAq1zcSnXC9HAp3EwhoxMHvo8rxB",
    "D4f1i6UDYeVQPcYfT4oDpyoJSW48FHdYDdoZfxxrwe4k",
    "EbsUZEFAU23Z2SgAymFtU2hADLiyNLzaPLKiJfvpKnE7",
)
# 4zhY... and 2pRi...: a relayer (1000 signatures in two minutes when recorded) pays the fee
# of two strangers' transactions, each of which also pays a fee account in USDC.
RELAYER = "AgmLJBMDCqWynYnQiPCuj9ewsNNsBJXyzoUhD9LJzN51"
USER_1, USER_2 = (
    "9e6gcrJ3eWVjgT3r7fMnBje3pEmeid1h4hm9gQ1cv31K",
    "6QzxgzRpsLqHJgsghLHeaHMyQ2A1VVPmCue6VTsta6bG",
)
FEES = "R4rNJHaffSUotNmqSKNEfDcJE8A7zJUkaoM5Jkd7cYX"  # busy too: 1000 signatures


def test_the_fee_payer_and_the_other_signers_are_linked():
    s = sig("381r")
    pair = heuristics.pair
    assert heuristics.solana_links(SECOND, s, TXS[s]) == [
        (*pair(SECOND, FIRST), "fee-payer", 0.9, s),
        (*pair(SECOND, THIRD), "co-signer", 0.9, s),
    ]
    assert heuristics.solana_links(FIRST, s, TXS[s]) == [
        (*pair(FIRST, SECOND), "fee-payer", 0.9, s),
        (*pair(FIRST, THIRD), "fee-payer", 0.9, s),
    ]
    r = sig("2yQ9")
    assert heuristics.solana_links(OWNER, r, TXS[r]) == [(*pair(OWNER, PAYER), "fee-payer", 0.9, r)]
    assert heuristics.solana_links(ACCOUNT, r, TXS[r]) == []  # in the transaction, no signer


def test_the_rent_of_a_closed_token_account_is_booked_to_its_owner():
    r = sig("2yQ9")
    refund = [("solana", r, OWNER, PAYER, "SOL", "", 0.00148844, 1795647455)]
    assert chains.solana_transfers(OWNER, r, TXS[r]) == refund  # was nothing
    assert chains.solana_transfers(PAYER, r, TXS[r]) == refund  # was from the token account
    # The same close made by a program, as an inner instruction.
    inner = copy.deepcopy(TXS[r])
    close = inner["transaction"]["message"]["instructions"].pop()
    inner["meta"]["innerInstructions"] = [{"index": 0, "instructions": [close]}]
    assert chains.solana_transfers(PAYER, r, inner) == refund


def test_rent_that_goes_back_to_the_owner_is_no_transfer():
    owner, account = OWNER, ACCOUNT
    tx = {
        "blockTime": 1,
        "transaction": {
            "message": {
                "accountKeys": [
                    {"pubkey": owner, "signer": True},
                    {"pubkey": account, "signer": False},
                ],
                "instructions": [
                    {
                        "program": "spl-token",
                        "parsed": {
                            "type": "closeAccount",
                            "info": {"account": account, "destination": owner, "owner": owner},
                        },
                    }
                ],
            }
        },
        "meta": {
            "fee": 5000,
            "preBalances": [1_000_000, 2_039_280],
            "postBalances": [3_034_280, 0],
        },
    }
    assert chains.solana_transfers(owner, "s", tx) == []  # was "the token account paid me"


def short(rows):
    return [(r[2][:6], r[3][:6], r[4], round(r[6], 9)) for r in rows]


def test_other_solana_transfers_are_unchanged():
    one, two = sig("4zhY"), sig("2pRi")
    assert short(chains.solana_transfers(USER_1, one, TXS[one])) == [
        ("FdGbbK", USER_1[:6], "9Muz...", 306395.456325),
        (USER_1[:6], "8JA6Zg", "EPjF...", 6.96007),
        (USER_1[:6], FEES[:6], "EPjF...", 0.142042),
    ]
    assert short(chains.solana_transfers(RELAYER, two, TXS[two])) == [
        (RELAYER[:6], "4wvo8W", "SOL", 0.00150845),
        (RELAYER[:6], "6m7oRt", "SOL", 5.016e-06),
        (RELAYER[:6], "HjQjng", "SOL", 3.73e-07),
    ]


class RPC:
    """The Solana RPC over the recorded transactions: each address's signatures are those of
    the recorded transactions it signed; a stranger's busy read answers `busy` signatures."""

    def __init__(self, busy=None, broken=()):
        self.busy, self.broken, self.reads = busy or {}, broken, []

    def __call__(self, request):
        method, params = (json.loads(request.content)[k] for k in ("method", "params"))
        if method == "getBalance":
            result = {"value": 0}
        elif method == "getTokenAccountsByOwner":
            result = {"value": []}
        elif method == "getSignaturesForAddress" and params[1].get("limit") == 1000:
            self.reads.append(params[0])
            if params[0] in self.broken:
                return httpx.Response(200, json={"error": {"message": "node is behind"}})
            n = self.busy.get(params[0], 10)
            result = [{"signature": f"x{i}", "slot": 1} for i in range(n)]
        elif method == "getSignaturesForAddress":
            signed = [
                {"signature": s, "slot": tx["slot"]}
                for s, tx in TXS.items()
                if any(
                    k["signer"] and k["pubkey"] == params[0]
                    for k in tx["transaction"]["message"]["accountKeys"]
                )
            ]
            result = [] if params[1].get("before") else signed
        else:  # getTransaction
            result = TXS[params[0]]
        return httpx.Response(200, json={"result": result})


@pytest.fixture
def quick(monkeypatch):
    monkeypatch.setattr(chains, "DELAY", 0)


def links(db):
    return {(r["a"], r["b"], r["kind"]) for r in db.execute("SELECT * FROM link")}


def test_the_reader_returns_the_links_of_snapshots_and_full_reads(quick):
    s = sig("381r")
    with httpx.Client(transport=httpx.MockTransport(RPC())) as client:
        snapshot = chains.fetch(client, SECOND)
        page = chains.page_solana(client, SECOND)
    expected = heuristics.solana_links(SECOND, s, TXS[s])
    assert snapshot["links"] == expected
    assert history._convert("solana", SECOND, page.items, ())["links"] == expected


def test_a_relayer_never_read_is_read_once_and_links_nobody(db, quick):
    mine(db, USER_1)
    mine(db, USER_2)
    db.execute("INSERT INTO node(address, hub) VALUES (?, 1)", (FEES,))  # its own read
    db.commit()
    fake = RPC(busy={RELAYER: 1000})
    with httpx.Client(transport=httpx.MockTransport(fake)) as client:
        sync.sync_address(db, client, USER_1)
        sync.sync_address(db, client, USER_2)
    assert fake.reads == [RELAYER]  # read once: the second wallet finds it known
    assert db.execute("SELECT hub FROM node WHERE address = ?", (RELAYER,)).fetchone()["hub"]
    assert not [k for k in links(db) if RELAYER in k]
    assert not [p for p in graph.trace(db) if p["confidence"]]  # the two are not linked
    assert db.execute("SELECT COUNT(*) n FROM transfer").fetchone()["n"] == 8  # all stored


def test_my_co_signers_are_linked_and_a_quiet_stranger_after_its_read(db, quick):
    mine(db, FIRST)
    mine(db, SECOND)
    db.commit()
    fake = RPC()  # THIRD: 10 signatures, quiet
    with httpx.Client(transport=httpx.MockTransport(fake)) as client:
        sync.sync_address(db, client, SECOND)
        assert links(db) == {
            (*heuristics.pair(FIRST, SECOND), "fee-payer"),
            (*heuristics.pair(SECOND, THIRD), "co-signer"),
        }
        assert fake.reads == [THIRD]  # my own wallet is not read
        sync.sync_address(db, client, SECOND)  # the same transaction again: stored already
    assert fake.reads == [THIRD]
    path = next(p for p in graph.trace(db) if {p["a"], p["b"]} == {FIRST, SECOND})
    assert path["kinds"] == ["transfer"]  # the USDT payment; the fee-payer link backs it


def test_a_stranger_that_cannot_be_read_gets_no_link_and_the_sync_says_so(db, quick):
    mine(db, FIRST)
    mine(db, SECOND)
    db.commit()
    with httpx.Client(transport=httpx.MockTransport(RPC(broken={THIRD}))) as client:
        sync.sync_address(db, client, SECOND)
    assert links(db) == {(*heuristics.pair(FIRST, SECOND), "fee-payer")}
    error = db.execute("SELECT error FROM node WHERE address = ?", (SECOND,)).fetchone()["error"]
    assert THIRD in error and "node is behind" in error
    assert db.execute("SELECT COUNT(*) n FROM transfer").fetchone()["n"] == 1  # still stored
