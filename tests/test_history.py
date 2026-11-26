"""The full history of my own addresses, filled in across syncs (tracker/history.py)."""

import pytest
from test_core import BCH, DOGE, LTC, SATOSHI, SOL, TRX, A

from tracker import chains, history


@pytest.fixture
def evm(monkeypatch):
    monkeypatch.setattr(
        chains,
        "DEFAULT_EVM",
        {
            "ethereum": {"url": "http://eth/api", "symbol": "ETH", "llama": "ethereum"},
            "base": {"symbol": "ETH", "llama": "base", "needs_key": True},
        },
    )


def test_each_chain_has_its_sources(evm):
    assert history.sources(SATOSHI) == ["bitcoin"]
    assert history.sources(LTC) == ["litecoin"]
    assert history.sources(BCH) == ["bitcoincash"]
    assert history.sources(DOGE) == ["dogecoin"]
    # every EVM chain this server can read, each list on its own; none for a chain without key
    assert history.sources(A) == [
        "ethereum:txlist", "ethereum:txlistinternal", "ethereum:tokentx",
        "ethereum:tokennfttx", "ethereum:token1155tx",
    ]  # fmt: skip
    assert history.sources(TRX) == ["tron:native", "tron:trc20"]
    assert history.sources(SOL) == ["solana"]


def test_a_bookmark_round_trips(db):
    assert history.get(db, A, "ethereum:txlist") is None
    history.put(db, A, "ethereum:txlist", oldest="123", newest=456, oldest_ts=1000)
    history.put(db, A, "ethereum:txlist", complete=1)  # a later write keeps the rest
    mark = history.get(db, A, "ethereum:txlist")
    assert (mark["oldest"], mark["newest"], mark["oldest_ts"], mark["complete"]) == (
        "123",
        456,
        1000,
        1,
    )


def test_an_address_is_complete_only_when_every_source_is(db, evm):
    assert history.state(db, A) == {"complete": False, "oldest_ts": None}  # nothing read yet
    history.put(db, A, "ethereum:txlist", complete=1, oldest_ts=100)
    history.put(db, A, "ethereum:txlistinternal", complete=1, oldest_ts=200)
    history.put(db, A, "ethereum:tokentx", oldest_ts=300)
    for source in ("ethereum:tokennfttx", "ethereum:token1155tx"):
        history.put(db, A, source, complete=1)  # no NFT ever: no date either
    # Filling in: read back to the latest of the sources still filling in.
    assert history.state(db, A) == {"complete": False, "oldest_ts": 300}
    history.put(db, A, "ethereum:tokentx", complete=1)
    assert history.state(db, A) == {"complete": True, "oldest_ts": 100}


# A fake Esplora: `txs` newest first, pages of 25, `fail` cursors that answer 400 once.
class Esplora:
    def __init__(self, n, total=None):
        self.txs = [self.tx(i) for i in range(n)]
        self.total, self.fail = total, set()

    @staticmethod
    def tx(i, height=None):
        sender = "12c6DSiU4Rq3P4ZxziKxzrL5LmMBrzjrJX"  # one payer; each transaction is its own
        return {
            "txid": f"t{i:05d}",
            "vin": [{"prevout": {"scriptpubkey_address": sender, "value": 1000}}],
            "vout": [{"scriptpubkey_address": SATOSHI, "value": 1000}],
            "status": {
                "block_time": 1_600_000_000 + (height or 100_000 - i),
                "block_height": height or 100_000 - i,
            },
        }

    def __call__(self, request):
        import httpx

        path = request.url.path
        if path.endswith(f"/address/{SATOSHI}"):
            count = self.total if self.total is not None else len(self.txs)
            stats = {"funded_txo_sum": 1000 * len(self.txs), "spent_txo_sum": 0, "tx_count": count}
            return httpx.Response(200, json={"chain_stats": stats})
        cursor = path.rsplit("/txs/chain", 1)[1].strip("/")
        if cursor in self.fail:
            self.fail.discard(cursor)
            return httpx.Response(400)  # not retried: the page fails
        start = next(i for i, t in enumerate(self.txs) if t["txid"] == cursor) + 1 if cursor else 0
        return httpx.Response(200, json=self.txs[start : start + 25])


@pytest.fixture
def esplora(db, monkeypatch):
    import httpx
    from test_core import mine

    from tracker import sync

    monkeypatch.setattr(chains, "DELAY", 0)
    monkeypatch.setattr(chains, "BTC_PAGES", 1)  # the plain read sees only the newest page
    monkeypatch.setattr(history, "BUDGET", {"default": 3})
    monkeypatch.setitem(chains.UTXO, "bitcoin", ("BTC", "esplora", "http://esplora", ""))
    mine(db, SATOSHI)
    db.commit()
    fake = Esplora(150)

    def run():
        with httpx.Client(transport=httpx.MockTransport(fake)) as client:
            sync.sync_address(db, client, SATOSHI)

    fake.sync = run
    return fake


def stored(db):
    return db.execute("SELECT COUNT(DISTINCT txid) FROM transfer").fetchone()[0]


def test_history_fills_in_over_three_syncs(db, esplora):
    esplora.sync()
    assert stored(db) == 75 and not history.state(db, SATOSHI)["complete"]
    hub = db.execute("SELECT hub FROM node WHERE address = ?", (SATOSHI,)).fetchone()[0]
    assert hub == 1  # not complete yet: only part of the wallet is read
    esplora.sync()
    esplora.sync()
    assert stored(db) == 150 and history.state(db, SATOSHI)["complete"]
    assert db.execute("SELECT hub FROM node WHERE address = ?", (SATOSHI,)).fetchone()[0] == 0


def test_a_failed_page_keeps_the_bookmark(db, esplora):
    esplora.fail.add("t00049")  # the third page
    esplora.sync()
    assert stored(db) == 50  # the two pages before it are kept
    assert db.execute("SELECT error FROM node WHERE address = ?", (SATOSHI,)).fetchone()[0]
    for _ in range(4):
        esplora.sync()
    assert stored(db) == 150 and history.state(db, SATOSHI)["complete"]  # no hole


def test_new_and_old_are_both_read(db, esplora):
    esplora.sync()
    assert stored(db) == 75
    # 100 new transactions arrive: more than one sync's budget (3 pages of 25) reads.
    esplora.txs[:0] = [Esplora.tx(1000 + i, height=200_000 - i) for i in range(100)]
    esplora.sync()
    assert history.get(db, SATOSHI, "bitcoin")["gap"]  # the rest of the new ones: next time
    for _ in range(6):
        esplora.sync()
    assert stored(db) == 250 and history.state(db, SATOSHI)["complete"]
    assert history.get(db, SATOSHI, "bitcoin")["gap"] is None


def test_complete_needs_the_total(db, esplora):
    esplora.total = 160  # the indexer says 160 but serves 150: it stopped early
    for _ in range(6):
        esplora.sync()
    assert stored(db) == 150 and not history.state(db, SATOSHI)["complete"]


# A fake Etherscan-style API: `rows[action]` newest first, honouring endblock and offset, and
# refusing any page but the first, as a source with a 10,000-row window would beyond it.
class Etherscan:
    def __init__(self, n):
        payer = "0x" + "d" * 40
        self.rows = {
            "txlist": [
                {
                    "from": payer,
                    "to": A,
                    "hash": f"h{i:04d}",
                    "value": "1000",
                    "timeStamp": str(1_600_000_000 + 10_000 - i),
                    "blockNumber": str(10_000 - i),
                }
                for i in range(n)
            ],
            "txlistinternal": [],
            "tokentx": [],
            "tokennfttx": [],
            "token1155tx": [],
        }
        self.asked = []

    def __call__(self, request):
        import httpx

        q = request.url.params
        action = q["action"]
        if action == "balance":
            return httpx.Response(200, json={"status": "1", "result": "0"})
        self.asked.append((action, q.get("page"), q.get("endblock")))
        assert q.get("page", "1") == "1"  # never past the first page: no 10,000-row window
        rows = [
            r
            for r in self.rows[action]
            if not q.get("endblock") or int(r["blockNumber"]) <= int(q["endblock"])
        ]
        rows = rows[: int(q["offset"])]
        return httpx.Response(
            200,
            json={
                "status": "1" if rows else "0",
                "message": "OK" if rows else "No transactions found",
                "result": rows,
            },
        )


def test_evm_history_fills_in_by_moving_endblock(db, monkeypatch, evm):
    import httpx
    from test_core import mine

    from tracker import sync

    monkeypatch.setattr(chains, "DELAY", 0)
    monkeypatch.setattr(chains, "EVM_ROWS", 10)  # the plain read sees the newest 10 rows
    monkeypatch.setattr(history, "BUDGET", {"default": 2})
    mine(db, A)
    db.commit()
    fake = Etherscan(35)
    for _ in range(4):
        with httpx.Client(transport=httpx.MockTransport(fake)) as client:
            sync.sync_address(db, client, A)
    assert stored(db) == 35 and history.state(db, A)["complete"]
    backwards = [end for action, _, end in fake.asked if action == "txlist" and end]
    assert backwards and all(int(e) < 10_000 for e in backwards)  # it moved endblock down


def tron_hex(i):
    from embit import base58

    raw = bytearray(base58.decode_check(TRX))
    raw[-1] = i % 256  # another valid Tron address
    return bytes(raw).hex()


class TronGrid:
    """Native and TRC-20 transfers to TRX, newest first, honouring max_timestamp and limit."""

    def __init__(self, native, trc20):
        from embit import base58

        payer = tron_hex(7)
        self.payer = base58.encode_check(bytes.fromhex(payer))
        self.native = [
            {"txID": f"n{i:03d}", "block_timestamp": 1_600_000_000_000 - i * 1000, "ret": [{"contractRet": "SUCCESS"}],
             "raw_data": {"contract": [{"type": "TransferContract", "parameter": {"value": {"owner_address": payer, "to_address": base58.decode_check(TRX).hex(), "amount": 1_000_000}}}]}}
            for i in range(native)
        ]  # fmt: skip
        self.trc20 = [
            {"transaction_id": f"k{i:03d}", "block_timestamp": 1_590_000_000_000 - i * 1000, "type": "Transfer", "from": self.payer, "to": TRX, "value": "5000000",
             "token_info": {"address": "TOLD" if i == trc20 - 1 else "TNEW", "symbol": "OLD" if i == trc20 - 1 else "NEW", "decimals": 6}}
            for i in range(trc20)
        ]  # fmt: skip

    def __call__(self, request):
        import httpx

        path, q = request.url.path, request.url.params
        if path.endswith(f"/accounts/{TRX}"):  # balances: one old token never in a newest page
            return httpx.Response(
                200, json={"data": [{"balance": 0, "trc20": [{"TOLD": "5000000"}]}]}
            )
        rows = self.trc20 if path.endswith("/trc20") else self.native
        if q.get("max_timestamp"):
            rows = [r for r in rows if r["block_timestamp"] <= int(q["max_timestamp"])]
        return httpx.Response(200, json={"data": rows[: int(q["limit"])]})


def test_tron_fills_in_with_timestamps_and_old_tokens_keep_their_name(db, monkeypatch):
    import httpx
    from test_core import mine

    from tracker import sync

    monkeypatch.setattr(chains, "DELAY", 0)
    monkeypatch.setattr(chains, "TRON_ROWS", 5)
    monkeypatch.setattr(history, "BUDGET", {"default": 2})
    mine(db, TRX)
    db.commit()
    fake = TronGrid(native=23, trc20=7)
    for _ in range(5):
        with httpx.Client(transport=httpx.MockTransport(fake)) as client:
            sync.sync_address(db, client, TRX)
    assert stored(db) == 30 and history.state(db, TRX)["complete"]
    # The OLD token only appears in the oldest transfer: once the history is whole, its
    # balance follows from it, with its name.
    old = db.execute("SELECT asset, amount FROM balance WHERE token = 'TOLD'").fetchone()
    assert tuple(old) == ("OLD", 5.0)


class SolanaRPC:
    """Signatures newest first, honouring before and limit; each pays SOL one lamport-ish."""

    def __init__(self, n):
        self.sigs = [{"signature": f"s{i:03d}", "slot": 500_000 - i} for i in range(n)]

    def __call__(self, request):
        import json

        import httpx

        body = json.loads(request.content)
        method, params = body["method"], body["params"]
        payer = "775HG7Z2skCZugQ6LERhgbf82TPb2eGh4ucwVkjJm4GB"
        if method == "getBalance":
            result = {"value": 0}
        elif method == "getTokenAccountsByOwner":
            result = {"value": []}
        elif method == "getSignaturesForAddress":
            opts = params[1]
            sigs = self.sigs
            if opts.get("before"):
                sigs = sigs[[s["signature"] for s in sigs].index(opts["before"]) + 1 :]
            result = sigs[: opts["limit"]]
        else:  # getTransaction
            i = int(params[0][1:])
            result = {
                "blockTime": 1_600_000_000 - i,
                "transaction": {"message": {"accountKeys": [payer, SOL]}},
                "meta": {
                    "fee": 0,
                    "preBalances": [2_000_000, 0],
                    "postBalances": [1_000_000, 1_000_000],
                },
            }
        return httpx.Response(200, json={"result": result})


def test_solana_fills_in_with_signatures(db, monkeypatch):
    import httpx
    from test_core import mine

    from tracker import sync

    monkeypatch.setattr(chains, "DELAY", 0)
    monkeypatch.setattr(chains, "SOLANA_TXS", 5)  # the plain read
    monkeypatch.setattr(chains, "SOLANA_PAGE", 5)
    monkeypatch.setattr(history, "BUDGET", {"default": 2, "solana": 2})
    mine(db, SOL)
    db.commit()
    fake = SolanaRPC(30)
    for _ in range(7):  # each sync: one page for what is new, one page further back
        with httpx.Client(transport=httpx.MockTransport(fake)) as client:
            sync.sync_address(db, client, SOL)
    assert stored(db) == 30 and history.state(db, SOL)["complete"]


class Midgard:
    """Swaps from A to BTC, newest first, honouring offset and limit. The oldest one paid
    into an old vault, which a later sync's newest page never shows again."""

    def __init__(self, n, recipient):
        self.actions = [
            {"type": "swap", "status": "success",
             "in": [{"address": A, "txID": f"IN{i:04d}", "coins": [{"amount": "100000000", "asset": "ETH.ETH"}]}],
             "out": [{"address": recipient, "txID": f"OUT{i:04d}", "coins": [{"amount": "1000", "asset": "BTC.BTC"}]}]}
            for i in range(n)
        ]  # fmt: skip

    def __call__(self, request):
        import httpx

        q = request.url.params
        start = int(q.get("offset", 0))
        return httpx.Response(200, json={"actions": self.actions[start : start + int(q["limit"])]})


def test_old_swaps_are_read_in_full_and_an_old_vault_keeps_its_label(db, monkeypatch):
    import httpx
    from test_core import BIP84, mine, transfer

    from tracker import swaps

    monkeypatch.setattr(chains, "DELAY", 0)
    monkeypatch.setattr(swaps, "SOURCES", {"Maya Protocol": "http://maya"})
    monkeypatch.setattr(swaps, "BUDGET", 2)  # pages per address and protocol per sync
    mine(db, A)
    mine(db, BIP84[0])
    old_vault = "0x" + "5" * 40
    transfer(db, "ethereum", "0xin0119", A, old_vault, 1.0)  # the oldest swap's inbound leg
    db.commit()
    fake = Midgard(120, BIP84[0])

    def labelled():
        row = db.execute("SELECT name FROM label WHERE address = ?", (old_vault,)).fetchone()
        return row and row[0]

    for _ in range(3):
        with httpx.Client(transport=httpx.MockTransport(fake)) as client:
            swaps.sync(db, client)
    swaps_read = db.execute("SELECT COUNT(*) FROM link WHERE kind = 'swap'").fetchone()[0]
    assert swaps_read == 120 and history.get(db, A, "swaps:Maya Protocol")["complete"]
    assert labelled() == "Maya Protocol"
    with httpx.Client(transport=httpx.MockTransport(fake)) as client:
        swaps.sync(db, client)  # reads only what is new: the oldest swap is not in it
    assert labelled() == "Maya Protocol"  # rebuilt from every stored swap, not the newest page


def test_a_wallet_filling_in_is_partly_checked(db):
    from test_core import mine

    from tracker import graph

    mine(db, A)
    db.execute("UPDATE node SET synced_at = 1 WHERE address = ?", (A,))
    db.commit()
    (v,) = graph.verdicts(db)
    assert v["checked"] == "full"  # no bookmark yet (before the first full read): as before
    history.put(db, A, "ethereum:txlist", complete=0, oldest_ts=1_600_000_000)
    history.put(db, A, "swaps:Maya Protocol", complete=1)
    db.commit()
    (v,) = graph.verdicts(db)
    # Never "no link found" while older transactions are still to be read.
    assert v["checked"] == "partial" and v["coverage"]["filling"] == 1_600_000_000
    history.put(db, A, "ethereum:tokentx", complete=0)  # nothing dated read there yet
    db.commit()
    (v,) = graph.verdicts(db)
    assert v["checked"] == "partial" and v["coverage"]["filling"] == 0  # no date to claim
    history.put(db, A, "ethereum:tokentx", complete=1)
    history.put(db, A, "ethereum:txlist", complete=1)
    db.commit()
    (v,) = graph.verdicts(db)
    assert v["checked"] == "full" and v["coverage"]["filling"] is None


def test_a_removed_wallet_is_not_taken_for_a_service(db):
    from fastapi.testclient import TestClient
    from test_core import mine

    from tracker.app import app

    mine(db, A)
    db.execute("UPDATE node SET hub = 1 WHERE address = ?", (A,))  # it was still filling in
    db.commit()
    assert (
        TestClient(app, base_url="http://localhost").delete(f"/api/tracker/wallets/{A}").is_success
    )
    assert db.execute("SELECT hub FROM node WHERE address = ?", (A,)).fetchone()[0] == 0


SECOND = "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2"


@pytest.fixture
def background(esplora, monkeypatch):
    """The background fill-in and the user's sync, both on the fake Esplora; `gate` holds
    every request while it is closed."""
    import threading

    import httpx

    from tracker import labels, sync

    monkeypatch.setenv("CT_SWAPS", "off")
    monkeypatch.setenv("CT_PRICES", "off")
    monkeypatch.setattr(labels, "due", lambda db: False)
    esplora.asked, esplora.entered, esplora.gate = [], threading.Event(), threading.Event()
    esplora.gate.set()

    def answer(request):
        esplora.asked.append((threading.current_thread().name, request.url.path))
        esplora.entered.set()
        esplora.gate.wait(5)
        return esplora(request) if SATOSHI in request.url.path else httpx.Response(400)

    client = lambda: httpx.Client(transport=httpx.MockTransport(answer))
    monkeypatch.setattr(sync, "_client", client)
    return esplora


def test_fill_in_continues_on_its_own(db, background):
    from tracker import sync

    background.sync()  # the user's sync reads the newest part
    assert stored(db) == 75
    sync.fill_in()
    sync.fill_in()
    assert stored(db) == 150 and history.state(db, SATOSHI)["complete"]
    asked = len(background.asked)
    sync.fill_in()
    assert len(background.asked) == asked  # nothing left to read: it asks nothing
    assert sync.status["filling"] == ""


def test_a_user_sync_goes_first(db, background):
    import threading
    import time

    from fastapi.testclient import TestClient
    from test_core import mine

    from tracker import sync
    from tracker.app import app

    mine(db, SECOND)  # also not read in full: the fill-in would read it next
    db.commit()
    background.gate.clear()
    filler = threading.Thread(target=sync.fill_in, name="fill-in")
    filler.start()
    assert background.entered.wait(5)  # the fill-in is reading its first address
    r = TestClient(app, base_url="http://localhost").post(
        "/api/tracker/sync", json={"addresses": []}
    )
    assert r.status_code == 200 and r.json()["running"]  # not a 409: it waits its turn
    background.gate.set()
    filler.join(5)
    deadline = time.time() + 10
    while sync.status["running"] and time.time() < deadline:
        time.sleep(0.01)
    assert not sync.status["running"] and sync.status["error"] == ""
    # The fill-in let the user go first after one address; the user's sync read both.
    by_fill_in = {
        p.split("/address/")[1].split("/")[0] for t, p in background.asked if t == "fill-in"
    }
    by_user = {p.split("/address/")[1].split("/")[0] for t, p in background.asked if t != "fill-in"}
    assert len(by_fill_in) == 1 and by_user == {SATOSHI, SECOND}


# Review of the gap bookkeeping: bursts of new transactions while the history fills in.


def test_two_bursts_in_a_row(db, esplora):
    esplora.sync()
    esplora.txs[:0] = [Esplora.tx(1000 + i, height=200_000 - i) for i in range(100)]
    esplora.sync()  # more new than one budget: a gap opens
    esplora.txs[:0] = [Esplora.tx(2000 + i, height=300_000 - i) for i in range(100)]
    for _ in range(12):  # another burst before it closed: it must not stop everything
        esplora.sync()
    assert stored(db) == 350 and history.state(db, SATOSHI)["complete"]


def test_failure_inside_gap_and_backfill(db, esplora):
    esplora.sync()
    esplora.txs[:0] = [Esplora.tx(1000 + i, height=200_000 - i) for i in range(100)]
    esplora.sync()
    esplora.fail |= {"t01074", "t00099"}
    for _ in range(10):
        esplora.sync()
    assert stored(db) == 250 and history.state(db, SATOSHI)["complete"]


def test_a_wallet_with_an_open_gap_is_not_complete(db, esplora):
    from tracker import graph

    for _ in range(3):
        esplora.sync()
    esplora.txs[:0] = [Esplora.tx(1000 + i, height=200_000 - i) for i in range(100)]
    esplora.sync()  # 25 new transactions are still unread here
    assert stored(db) == 225
    assert not history.state(db, SATOSHI)["complete"]
    assert db.execute("SELECT hub FROM node WHERE address = ?", (SATOSHI,)).fetchone()[0] == 1
    (v,) = graph.verdicts(db)
    # The hole is recent, not old: no "back to" date to claim.
    assert v["checked"] == "partial" and v["coverage"]["filling"] == 0
    esplora.sync()
    assert stored(db) == 250 and history.state(db, SATOSHI)["complete"]


def test_walk_with_a_pending_tx_on_top():
    # Haskoin answers a mempool tx with height 0, BlockCypher with -1; both come first.
    pages = {
        None: chains.Page(["pending", "a", "b"], [0, 210, 209], "p2", False),
        "p2": chains.Page(["c", "d"], [208, 207], "p3", False),
        "p3": chains.Page(["e", "old"], [206, 200], "p4", False),
    }
    items, _, _, reached, _, _, _ = history.walk(lambda c, s: pages[c], None, 200, 10)
    assert items == ["a", "b", "c", "d", "e"] and reached
    items = history.walk(lambda c, s: pages[c], None, None, 3)[0]  # a first read: no stop
    assert items == ["a", "b", "c", "d", "e", "old"]


def test_a_pending_tx_does_not_end_a_blockcypher_page(monkeypatch):
    import httpx

    monkeypatch.setattr(chains, "COOLDOWN", {})
    monkeypatch.setitem(chains.UTXO, "dogecoin", ("DOGE", "blockcypher", "http://cypher", ""))

    def tx(name, height):
        return {"hash": name, "block_height": height, "inputs": [], "outputs": []}

    page = {"txs": [tx("pending", -1)] + [tx(f"c{i}", 500 - i) for i in range(49)]}
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=page))) as c:
        p = chains.page_utxo(c, "dogecoin", DOGE)
    # Read on from inside the lowest confirmed block, never "before 0", which reads nothing.
    assert p.next == str(500 - 48 + 1) and not p.last


def test_a_wallet_of_several_addresses_claims_no_date_while_one_has_none(db):
    from test_core import mine

    from tracker import graph

    for a in (A, SATOSHI):
        mine(db, a)
    db.execute("UPDATE node SET synced_at = 1, wallet = 'hd:x' WHERE mine")
    db.execute("INSERT INTO hdwallet(id, descriptor) VALUES ('hd:x', 'x')")
    history.put(db, A, "ethereum:txlist", complete=0, oldest_ts=1_600_000_000)
    history.put(db, SATOSHI, "bitcoin", complete=0)  # nothing dated read there yet
    db.commit()
    (v,) = graph.verdicts(db)
    assert v["checked"] == "partial" and v["coverage"]["filling"] == 0


def test_a_failure_half_way_moves_no_bookmark(db, monkeypatch):
    # The sync stores nothing of an address whose read raised: no bookmark may pass those rows.
    page = chains.Page(["x"], [10], "next", True)
    monkeypatch.setattr(history, "sources", lambda address: ["one", "two"])
    monkeypatch.setattr(history, "_reader", lambda client, source, address: lambda c, s: page)

    def convert(source, address, items, keep):
        if source == "two":
            raise ValueError("an indexer row of an unexpected shape")
        return {k: [] for k in ("transfers", "links", "coinjoins", "wide", "sent")}

    monkeypatch.setattr(history, "_convert", convert)
    with pytest.raises(ValueError):
        history.advance(None, db, A)
    assert history.get(db, A, "one") is None


def test_untraced_internal_transactions_keep_the_history_open(db, monkeypatch, evm):
    import httpx
    from test_core import mine

    from tracker import sync

    class Blockscout(Etherscan):
        """Blockscout answers status 2, with what it has, while some blocks are not traced."""

        untraced = True

        def __call__(self, request):
            reply = super().__call__(request)
            if self.untraced and request.url.params["action"] == "txlistinternal":
                body = reply.json() | {
                    "status": "2",
                    "message": "Some internal transactions "
                    "within this block range have not yet been processed",
                }
                body["result"] = body["result"][:1]
                return httpx.Response(200, json=body)
            return reply

    monkeypatch.setattr(chains, "DELAY", 0)
    mine(db, A)
    db.commit()
    fake = Blockscout(5)
    contract = "0x" + "c" * 40
    fake.rows["txlistinternal"] = [
        {"from": contract, "to": A, "hash": f"i{i}", "value": "1000"}
        | {"timeStamp": str(1_600_000_000 + 9_000 - i), "blockNumber": str(9_000 - i)}
        for i in range(3)
    ]  # fmt: skip

    def run():
        with httpx.Client(transport=httpx.MockTransport(fake)) as client:
            sync.sync_address(db, client, A)

    run()
    assert stored(db) == 5 + 1  # the snapshot shows what Blockscout has...
    assert not history.state(db, A)["complete"]  # ...but the history is not called complete
    fake.untraced = False  # Blockscout traced the rest
    run()
    assert stored(db) == 5 + 3 and history.state(db, A)["complete"]
