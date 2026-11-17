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
    assert history.sources(A) == ["ethereum:txlist", "ethereum:txlistinternal", "ethereum:tokentx"]
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
