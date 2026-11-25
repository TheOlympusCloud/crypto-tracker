"""One privacy score per wallet of mine, across its chains (docs/design/multichain.md, 3.4)."""

import httpx
import pytest
from fastapi.testclient import TestClient
from test_core import BINANCE, A, B, C, D, mine, transfer

from tracker import alerts, exposure, graph, history
from tracker import db as database
from tracker.exposure import wallets

BTC1, BTC2 = "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2", "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"
BTC3, BTC4, BTC5 = (
    "3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy",
    "1dice8EMZmqKvrGE4Qc9bUFf9PX3xaYDp",
    "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq",
)


def synced(db, *addresses):
    for a in addresses:
        db.execute("UPDATE node SET synced_at = 1 WHERE address = ?", (a,))
    db.commit()


def label(db, address, name, category, source="etherscan-labels"):
    db.execute(
        "INSERT INTO label(address, name, category, source) VALUES (?, ?, ?, ?)",
        (address, name, category, source),
    )


def scores(db):
    return wallets.scores(db, graph.verdicts(db))


@pytest.fixture
def offline(db, monkeypatch):
    """The app, with every outgoing call failing the test."""
    from tracker.app import app

    def refuse(request):
        pytest.fail(f"called {request.url}")

    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(refuse)))
    return TestClient(app, base_url="http://localhost")


def test_a_wallet_linked_at_70_percent_to_a_public_one_loses_the_stated_impact(db):
    mine(db, A)
    mine(db, B, public=1)
    db.execute("UPDATE node SET name = 'Exchange withdrawals' WHERE address = ?", (B,))
    db.execute("INSERT INTO link VALUES (?, ?, 'change', 0.7, 'x')", (A, B))
    synced(db, A, B)

    got = scores(db)

    (f,) = got[A]["findings"]
    severity, full, *_ = exposure.IMPACT["x-linked-public"]
    assert (f["id"], f["scoreImpact"], f["severity"]) == ("x-linked-public", -49, severity)
    assert full == -70 and f["confidence"] == "medium"
    assert f["params"] == {"wallet": "Exchange withdrawals", "confidence": 70}
    assert (got[A]["score"], got[A]["grade"], got[A]["partial"]) == (93 - 49, "D", False)
    assert got[A]["by_chain"] == {"": [f]}  # a wallet's own finding: across its chains
    # The public wallet is tied to its owner anyway: the link exposes nothing more of it.
    assert got[B]["findings"] == [] and got[B]["grade"] == "A+"


def test_a_weak_link_to_a_public_wallet_costs_little_and_is_no_critical_finding(db):
    mine(db, A)
    mine(db, B, public=1)
    db.execute("INSERT INTO link VALUES (?, ?, 'exchange-flow', 0.2, 'x')", (A, B))
    synced(db, A, B)
    (f,) = scores(db)[A]["findings"]
    assert (f["scoreImpact"], f["severity"], f["confidence"]) == (-14, "medium", "low")


def test_a_certain_link_through_my_own_wallets_is_an_f(db):
    mine(db, A, public=1)
    mine(db, B)
    mine(db, C)
    transfer(db, "ethereum", "1", A, B, 1)
    transfer(db, "ethereum", "2", B, C, 1)
    synced(db, A, B, C)
    got = scores(db)[C]
    assert got["findings"][0]["confidence"] == "deterministic"
    assert (got["score"], got["grade"]) == (23, "F")


def test_a_wallet_whose_history_is_still_filling_in_is_partly_checked(db):
    mine(db, A)
    assert scores(db)[A] is None  # never read: no grade at all, rather than an empty A+
    synced(db, A)
    history.put(db, A, "ethereum:txlist", complete=0, oldest_ts=1_600_000_000)
    db.commit()
    assert scores(db)[A]["partial"] is True
    history.put(db, A, "ethereum:txlist", complete=1)
    db.commit()
    assert scores(db)[A]["partial"] is False


def hd_wallet(db, *addresses):
    db.execute(
        "INSERT INTO hdwallet(id, name, public, descriptor) VALUES ('hd:x', 'Ledger', 0, 'xpub')"
    )
    for a in addresses:
        db.execute("INSERT INTO node(address, mine, wallet) VALUES (?, 1, 'hd:x')", (a,))
    synced(db, *addresses)


def test_a_bitcoin_hd_wallet_pays_10_per_reused_address_not_an_f(db):
    hd_wallet(db, BTC1, BTC2)
    transfer(db, "bitcoin", "t1", D, BTC1, 0.1, asset="BTC")
    transfer(db, "bitcoin", "t2", D, BTC1, 0.2, asset="BTC")  # BTC1 received twice
    transfer(db, "bitcoin", "t3", BTC1, BTC2, 0.3, asset="BTC")  # BTC2 once, from the wallet

    (got,) = scores(db).values()

    (f,) = got["findings"]
    assert (f["id"], f["scoreImpact"], f["params"]) == ("x-address-reuse", -10, {"count": 1})
    assert {e["txid"] for e in f["evidence"]} == {"t1", "t2"}
    assert f["evidence"][0]["url"].endswith("/tx/t1")
    assert (got["score"], got["grade"], got["partial"]) == (83, "B", False)
    assert got["by_chain"] == {"bitcoin": [f]}


def test_reuse_costs_at_most_40(db):
    every = (BTC1, BTC2, BTC3, BTC4, BTC5)
    hd_wallet(db, *every)
    for i, a in enumerate(every):
        transfer(db, "bitcoin", f"a{i}", D, a, 0.1, asset="BTC")
        transfer(db, "bitcoin", f"b{i}", D, a, 0.1, asset="BTC")
    ((f,),) = [s["findings"] for s in scores(db).values()]
    assert (f["scoreImpact"], f["params"]["count"]) == (-40, 5)


def test_an_account_address_receiving_often_is_no_reuse(db):
    mine(db, A)
    transfer(db, "ethereum", "1", D, A, 1)
    transfer(db, "ethereum", "2", D, A, 1)
    synced(db, A)
    assert scores(db)[A]["findings"] == []


def test_dealings_are_scored_once_and_shown_by_chain(db):
    mine(db, A)
    label(db, BINANCE, "Binance 14", "cex")
    label(db, D, "Tornado.Cash", "mixer")
    label(db, C, "Binance deposit address", "cex", source="heuristic")  # a guess: no finding
    transfer(db, "ethereum", "1", BINANCE, A, 1)
    transfer(db, "polygon", "2", BINANCE, A, 5, asset="POL")
    transfer(db, "polygon", "3", A, D, 5, asset="POL")
    transfer(db, "ethereum", "4", A, C, 1)
    # A token a stranger airdropped is no dealing with the flagged address it came from.
    label(db, B, "Fake_Phishing", "flagged")
    transfer(db, "ethereum", "5", B, A, 1000, asset="SCAM", token="0x" + "9" * 40)
    synced(db, A)

    got = scores(db)[A]

    by = {chain: [f["id"] for f in found] for chain, found in got["by_chain"].items()}
    assert by == {
        "ethereum": ["x-exchange-exposure"],
        "polygon": ["x-exchange-exposure", "x-mixer-counterparty"],
        "evm": ["x-multi-chain"],
    }
    polygon = got["by_chain"]["polygon"][0]
    assert polygon["params"] == {"exchange": "Binance", "count": 1, "chains": "Polygon"}
    assert polygon["evidence"] == [
        {"chain": "polygon", "txid": "2", "url": "https://polygonscan.com/tx/2"}
    ]
    # One exchange exposure is enough: each finding counts once, at its worst.
    assert sorted(f["id"] for f in got["findings"]) == [
        "x-exchange-exposure",
        "x-mixer-counterparty",
        "x-multi-chain",
    ]
    assert got["score"] == 93 - 15 - 10 - 2


def test_a_check_that_would_need_a_call_is_skipped_and_partly_checked(db, monkeypatch):
    def greedy(ctx):
        ctx.get("http://eth/api", {"module": "account"})
        yield "x-multi-chain", {"count": 9, "chains": ""}, []

    monkeypatch.setitem(exposure.CHECKS, "evm", [greedy])
    mine(db, A)
    synced(db, A)
    got = scores(db)[A]
    assert got["findings"] == [] and got["partial"] is True
    assert got["gaps"] == [{"id": "skipped", "params": {"check": "greedy"}}]


def test_opening_the_page_makes_no_api_call(db, offline):
    mine(db, A)
    mine(db, B, public=1)
    label(db, BINANCE, "Binance 14", "cex")
    transfer(db, "ethereum", "1", BINANCE, A, 1)
    transfer(db, "ethereum", "2", A, B, 1)
    synced(db, A, B)
    db.commit()

    analysis = offline.get("/api/tracker/analysis").json()

    got = analysis["exposure"][A]
    assert {f["id"] for f in got["findings"]} == {"x-exchange-exposure", "x-linked-public"}
    assert got["grade"] == "F"


def test_the_alerted_grade_is_the_displayed_one(db, offline):
    mine(db, A)
    mine(db, B, public=1)
    db.execute("UPDATE node SET name = 'Savings' WHERE address = ?", (A,))
    synced(db, A, B)
    alerts.check(db)  # baseline: A+ for both
    assert db.execute("SELECT COUNT(*) FROM alert").fetchone()[0] == 0

    # Tied to the public wallet through two strangers: a link the default 4 hops find.
    transfer(db, "ethereum", "1", A, C, 1)
    transfer(db, "ethereum", "2", C, D, 1)
    transfer(db, "ethereum", "3", D, B, 1)
    db.commit()
    alerts.check(db)
    alerts.check(db)  # the same grade again is no news

    (alert,) = [a for a in alerts.recent(db)["items"] if a["kind"] == "grade"]
    shown = offline.get("/api/tracker/analysis").json()["exposure"][A]
    assert alert["address"] == A and alert["params"]["from"] == "A+"
    assert (alert["params"]["to"], alert["params"]["score"]) == (shown["grade"], shown["score"])
    assert (shown["score"], shown["grade"]) == (59, "C")  # 93 - 70 x 0.7 x 0.7
    # Asked with fewer hops, the verdict changes but the grade stays the alerted one.
    short = offline.get("/api/tracker/analysis?max_hops=1").json()
    assert short["verdicts"][0]["links"] == [] and short["exposure"][A] == shown


def test_a_better_grade_raises_no_alert(db):
    mine(db, A)
    mine(db, B, public=1)
    db.execute("INSERT INTO link VALUES (?, ?, 'change', 0.7, 'x')", (A, B))
    synced(db, A, B)
    alerts.check(db)
    db.execute("DELETE FROM link")
    alerts.check(db)
    assert [a["kind"] for a in alerts.recent(db)["items"]] == []


def test_an_alert_from_before_grades_reads_as_a_link_alert(tmp_path):
    path = tmp_path / "old.db"
    old = database.sqlite3.connect(path)
    old.execute(
        "CREATE TABLE alert(id INTEGER PRIMARY KEY, ts INT, wallet TEXT, other TEXT, text TEXT, "
        "seen INT NOT NULL DEFAULT 0)"
    )
    old.execute("INSERT INTO alert(ts, wallet, text) VALUES (1, ?, 'A is tied to B')", (A,))
    old.commit()
    old.close()
    db = database.connect(str(path))
    (item,) = alerts.recent(db)["items"]
    db.close()
    assert (item["kind"], item["params"], item["text"]) == ("link", None, "A is tied to B")
