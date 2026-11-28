"""A lite Bitcoin node of your own (the `node-lite` profile): a pruned Bitcoin Core that watches
my own wallets, sized from one disk cap. My addresses are read from it only; other addresses
from the public server, and every view says so."""

import copy
import functools
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from embit.script import address_to_scriptpubkey
from fastapi.testclient import TestClient
from test_core import BIP84, SATOSHI, ZPUB, mine

from tracker import chains, detect, hd, history, labels, node, prices, swaps, sync, tokens
from tracker.app import NOT_READY, app

ROOT = Path(__file__).parent.parent
RPC = "http://bitcoind-lite:8332"
MINE = "bc1q432elxuhwenlg70f2cgms9fa0p09g239rj9z9d"
SENDER = "12c6DSiU4Rq3P4ZxziKxzrL5LmMBrzjrJX"  # a stranger who paid me
PRUNED_MSG = (
    "Rescan failed for descriptor with timestamp 1. There was an error reading a block from "
    "time 1700000000, which is after or within 7200 seconds of key creation, and could contain "
    "transactions pertaining to the desc. As a result, transactions and coins using this desc "
    "may not appear in the wallet. This error could be caused by pruning or data corruption "
    "(see bitcoind log for details) and could be dealt with by downloading and rescanning the "
    "relevant blocks (see -reindex option and rescanblockchain RPC)."
)
# The same, on a node started from a snapshot that has not pruned yet (Core 31.3's backup.cpp)
SNAPSHOT_MSG = PRUNED_MSG.split(" This error")[0] + (
    " This error is likely caused by an in-progress assumeutxo background sync. Check logs or "
    "getchainstates RPC for assumeutxo background sync progress and try again later."
)


def spk(address):
    return {"hex": address_to_scriptpubkey(address).data.hex(), "address": address}


def tx(txid, ins, outs):
    """A transaction as `getrawtransaction <txid> 2 <block>` shows it. `ins`: the coins it
    spends, (txid, vout, address, sats); `outs`: (address, sats)."""
    return {
        "txid": txid, "hash": txid, "version": 2, "size": 222, "vsize": 141, "weight": 561,
        "locktime": 0, "fee": 0.00001,
        "vin": [
            {"txid": t, "vout": n, "scriptSig": {"asm": "", "hex": ""}, "sequence": 4294967293,
             "prevout": {"generated": False, "height": 1, "value": sats / 1e8,
                         "scriptPubKey": spk(a)}}
            for t, n, a, sats in ins
        ],
        "vout": [
            {"value": sats / 1e8, "n": n, "scriptPubKey": spk(a)} for n, (a, sats) in enumerate(outs)
        ],
    }  # fmt: skip


@functools.cache
def derived(branch):
    """The first addresses of one branch of an HD wallet (`.../0/*`): enough for a test."""
    (d,) = hd.descriptors(branch)
    return frozenset(hd.address(d, 0, i) for i in range(25))


class Core:
    """Bitcoin Core 31.3 pruned, with a watch-only wallet, over JSON-RPC 2.0: just what the
    app asks of it. Blocks are added with `connect`; `prune` deletes them as -prune would."""

    def __init__(self):
        self.ibd, self.up = False, True
        self.blocks = []  # [{"hash", "height", "time", "txs": [tx]}], oldest first
        self.pruned = set()  # heights whose block is deleted
        self.wallet = None  # {"descs": [request], "txs": {txid: height}} once created
        self.on_disk = False  # the wallet survives a restart of bitcoind
        self.asked = []  # (method, params)
        self.refuse = None  # descriptors containing this are refused
        self.minfee = 0.000001  # BTC/kvB: 0.1 sat/vB, Core's default floor
        self.estimates = {1: 0.0002, 3: 0.00012, 6: 0.0001, 144: 0.00002}  # BTC/kvB by target
        self.mempool = set()  # txids
        self.refuse_tx = None  # (code, message) sendrawtransaction answers
        # assumeutxo: the heights its chainparams take a snapshot of; the snapshot's block once
        # loaded; how far its background chain state has checked it; headers it has (None: all)
        self.assumeutxo, self.base, self.checked, self.headers = {5}, None, 0, None
        self.have_pruned = False
        for _ in range(3):
            self.connect([])

    # -- the chain
    def connect(self, txs):
        height = len(self.blocks)
        block = {
            "hash": f"{height:064x}",
            "height": height,
            "time": int(time.time()) - 3600 + height,
        }
        self.blocks.append(block | {"txs": txs})
        if self.wallet:
            self._find([block | {"txs": txs}])
        return block["hash"]

    def prune(self, below):
        self.pruned |= set(range(below))
        self.have_pruned = True

    def tip(self):
        return self.blocks[-1]

    # -- what the wallet watches
    def watched(self):
        found = set()
        for r in self.wallet["descs"] if self.wallet else []:
            desc = r["desc"].split("#")[0]
            if desc.startswith("addr("):
                found.add(desc[5:-1])
            else:  # a branch of an HD wallet: enough of its range for a test
                found |= derived(desc)
        return found

    def _touches(self, t, addresses):
        outs = {o["scriptPubKey"].get("address") for o in t["vout"]}
        ins = {i["prevout"]["scriptPubKey"].get("address") for i in t["vin"] if "prevout" in i}
        return (outs | ins) & addresses

    def _find(self, blocks):
        """What the wallet records of `blocks` (connected, or rescanned on an import)."""
        mine_ = self.watched()
        for b in blocks:
            for t in b["txs"]:
                if self._touches(t, mine_):
                    self.wallet["txs"][t["txid"]] = b["height"]

    # -- JSON-RPC
    def __call__(self, request):
        if not self.up:
            raise httpx.ConnectError("connection refused")
        assert request.headers["authorization"].startswith("Basic ")
        body = json.loads(request.content)
        assert body["jsonrpc"] == "2.0"
        method, params = body["method"], body["params"]
        self.asked.append((method, params))
        on_wallet = request.url.path == f"/wallet/{node.WALLET}"
        try:
            if on_wallet and not self.wallet:
                raise Fault(-18, "Requested wallet does not exist or is not loaded")
            result = (
                getattr(self, method)(*params)
                if isinstance(params, list)
                else getattr(self, method)(**params)
            )
            if on_wallet != (method in self.WALLET_RPCS):
                raise Fault(-32601, f"{method} asked on the wrong endpoint")
        except Fault as f:
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"],
                                             "error": {"code": f.code, "message": f.message}})  # fmt: skip
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    WALLET_RPCS = frozenset({"importdescriptors", "listdescriptors", "getaddressinfo", "listsinceblock",
                   "gettransaction"})  # fmt: skip

    def getblockchaininfo(self):
        low = max(self.pruned, default=-1) + 1
        return {"blocks": self.tip()["height"], "time": self.tip()["time"],
                "initialblockdownload": self.ibd, "pruned": True, "pruneheight": low,
                "bestblockhash": self.tip()["hash"]}  # fmt: skip

    def getbestblockhash(self):
        return self.tip()["hash"]

    # -- assumeutxo
    def getchainstates(self):
        current = {"blocks": self.tip()["height"], "validated": True}
        headers = self.tip()["height"] if self.headers is None else self.headers
        if self.base is None:
            return {"headers": headers, "chainstates": [current]}
        current |= {"snapshot_blockhash": self.blocks[self.base]["hash"],
                    "validated": self.checked >= self.base}  # fmt: skip
        behind = [{"blocks": self.checked, "validated": True}] if self.checked < self.base else []
        return {"headers": headers, "chainstates": behind + [current]}

    def getblockheader(self, blockhash):
        return {"hash": blockhash, "height": int(blockhash, 16)}

    def listwalletdir(self):
        return {"wallets": [{"name": node.WALLET}] if self.on_disk else []}

    def loadtxoutset(self, path):
        """A snapshot file here holds the height of its block: Core's checks, in its order."""
        with open(path) as f:
            height = int(f.read())
        why = "Unable to load UTXO snapshot: "
        if self.base is not None:
            raise Fault(-32603, why + "Can't activate a snapshot-based chainstate more than once")
        if height not in self.assumeutxo:
            raise Fault(-32603, why + "assumeutxo block hash in snapshot metadata not recognized "
                        f"(hash: {height:064x}). The following snapshot heights are available: "
                        f"{', '.join(map(str, sorted(self.assumeutxo)))}. ({path})")  # fmt: skip
        if self.headers is not None and self.headers < height:
            raise Fault(-32603, why + f"The base block header ({height:064x}) must appear in the "
                        "headers chain. Make sure all headers are syncing, and call loadtxoutset "
                        f"again. ({path})")  # fmt: skip
        self.base = height
        self.pruned |= set(range(1, height + 1))  # the blocks before it are not on the node
        return {"coins_loaded": 164241311, "tip_hash": self.blocks[height]["hash"],
                "base_height": height, "path": path}  # fmt: skip

    def listwallets(self):
        return [node.WALLET] if self.wallet else []

    def loadwallet(self, name, load_on_startup=None):
        if not self.on_disk:
            raise Fault(
                -18,
                f'Wallet file verification failed. Failed to load database path "{name}". Path does not exist.',
            )
        self.wallet = self.on_disk_wallet

    def createwallet(self, wallet_name, disable_private_keys, blank, load_on_startup, **_):
        assert disable_private_keys and blank and load_on_startup
        self.wallet = self.on_disk_wallet = {"descs": [], "txs": {}}
        self.on_disk = True
        return {"name": wallet_name}

    def importdescriptors(self, requests):
        results = []
        for r in requests:
            assert r["timestamp"] == 0 and "#" in r["desc"]
            if "*" in r["desc"]:
                assert r["range"] == [0, 999]
            if self.refuse and self.refuse in r["desc"]:
                results.append(
                    {"success": False, "error": {"code": -5, "message": "Cannot parse it"}}
                )
                continue
            self.wallet["descs"].append(r)
            # A rescan reads the blocks still kept; the older ones are gone.
            gone = PRUNED_MSG if self.have_pruned else SNAPSHOT_MSG
            results.append({"success": not self.pruned} if not self.pruned else
                           {"success": False, "error": {"code": -1, "message": gone}})  # fmt: skip
        self._find([b for b in self.blocks if b["height"] not in self.pruned])
        return results

    def listdescriptors(self):
        return {"wallet_name": node.WALLET, "descriptors": [
            {k: v for k, v in r.items() if k in ("desc", "range")} for r in self.wallet["descs"]
        ]}  # fmt: skip

    def getaddressinfo(self, address):
        return {"address": address, "ismine": address in self.watched()}

    def listsinceblock(self, blockhash="", target_confirmations=1, include_watchonly=False,
                       include_removed=True):  # fmt: skip
        since = next((b["height"] for b in self.blocks if b["hash"] == blockhash), -1)
        entries = [
            {"txid": txid, "blockhash": self.blocks[h]["hash"], "blockheight": h,
             "blockindex": [t["txid"] for t in self.blocks[h]["txs"]].index(txid),
             "blocktime": self.blocks[h]["time"], "category": "receive"}
            for txid, h in self.wallet["txs"].items() if h > since
        ]  # fmt: skip
        return {"transactions": entries, "removed": [], "lastblock": self.tip()["hash"]}

    def getrawtransaction(self, txid, verbosity, blockhash):
        assert verbosity == 2
        block = next(b for b in self.blocks if b["hash"] == blockhash)
        if block["height"] in self.pruned:
            raise Fault(-1, "Block not available")
        found = next(t for t in block["txs"] if t["txid"] == txid)
        return copy.deepcopy(found) | {"blockhash": blockhash, "time": block["time"]}

    def gettransaction(self, txid, include_watchonly, verbose):
        h = self.wallet["txs"][txid]
        found = copy.deepcopy(next(t for t in self.blocks[h]["txs"] if t["txid"] == txid))
        for i in found["vin"]:
            del i["prevout"]  # no undo data without the block
        return {"txid": txid, "blockheight": h, "decoded": found}

    def scantxoutset(self, action, objects):
        assert action == "start"
        wanted = set()
        for o in objects:
            desc = (o["desc"] if isinstance(o, dict) else o).split("#")[0]
            if desc.startswith("addr("):
                wanted.add(desc[5:-1])
            else:
                wanted |= derived(desc)
        spent = {(i["txid"], i["vout"]) for b in self.blocks for t in b["txs"] for i in t["vin"]}
        unspents = [
            {"txid": t["txid"], "vout": o["n"], "scriptPubKey": o["scriptPubKey"]["hex"],
             "amount": o["value"], "height": b["height"]}
            for b in self.blocks for t in b["txs"] for o in t["vout"]
            if o["scriptPubKey"]["address"] in wanted and (t["txid"], o["n"]) not in spent
        ]  # fmt: skip
        return {"success": True, "height": self.tip()["height"], "unspents": unspents,
                "total_amount": sum(u["amount"] for u in unspents)}  # fmt: skip

    # -- what the privacy scanner asks: fee estimates and broadcasting
    def getmempoolinfo(self):
        return {"loaded": True, "size": len(self.mempool), "mempoolminfee": self.minfee,
                "minrelaytxfee": 0.000001}  # fmt: skip

    def estimatesmartfee(self, conf_target):
        if conf_target not in self.estimates:
            return {"errors": ["Insufficient data or no feerate found"], "blocks": 0}
        return {"feerate": self.estimates[conf_target], "blocks": max(conf_target, 2)}

    @staticmethod
    def txid_of(raw):
        return hashlib.sha256(hashlib.sha256(bytes.fromhex(raw)).digest()).digest()[::-1].hex()

    def sendrawtransaction(self, hexstring):
        if self.refuse_tx:
            raise Fault(*self.refuse_tx)
        self.mempool.add(self.txid_of(hexstring))
        return self.txid_of(hexstring)

    def testmempoolaccept(self, rawtxs):
        return [{"txid": self.txid_of(r), "allowed": True, "vsize": 141} for r in rawtxs]

    def getmempoolentry(self, txid):
        if txid not in self.mempool:
            raise Fault(-5, "Transaction not in mempool")
        return {"vsize": 141, "fees": {"base": 0.00001}}


class Fault(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message


class Public:
    """The public Esplora (blockstream.info): records what it was asked."""

    def __init__(self):
        self.asked = []

    def __call__(self, request):
        self.asked.append(str(request.url))
        address = request.url.path.split("/")[3]
        if request.url.path.endswith(f"/address/{address}"):
            n = int(address == SATOSHI)  # the one address it has a history for
            stats = {"funded_txo_sum": 5000 * n, "spent_txo_sum": 0, "tx_count": n}
            return httpx.Response(200, json={"chain_stats": stats})
        if request.url.path.endswith("/txs/chain") and address != SATOSHI:
            return httpx.Response(200, json=[])
        if request.url.path.endswith("/txs/chain"):
            paid = {
                "txid": "f" * 64,
                "vin": [{"prevout": {"scriptpubkey_address": SENDER, "value": 5000}}],
                "vout": [{"scriptpubkey_address": address, "value": 5000}],
                "status": {"confirmed": True, "block_height": 1, "block_time": 1_600_000_000},
            }
            return httpx.Response(200, json=[paid])
        return httpx.Response(404)


@pytest.fixture
def lite(db, monkeypatch, tmp_path):
    """The `node-lite` profile, its bitcoind faked. Anything asked of another server goes to
    the fake public Esplora, or gets a 404 (prices)."""
    core, public = Core(), Public()

    def route(request):
        if request.url.host == "bitcoind-lite":
            return core(request)
        if request.url.host == "snapshots.example":
            return serve(core.files, request)
        if request.url.host == "blockstream.info":
            return public(request)
        return httpx.Response(404)

    cookie = tmp_path / ".cookie"
    cookie.write_text("__cookie__:s3cret")
    real, transport = httpx.Client, httpx.MockTransport(route)
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=transport, **kw))
    monkeypatch.setattr(node, "BITCOIND", RPC)
    monkeypatch.setattr(node, "COOKIE", str(cookie))
    monkeypatch.setattr(node, "TTL", -1)  # asked again every time
    monkeypatch.setattr(node, "_scan", {"at": 0.0, "key": None, "sats": {}})
    monkeypatch.setattr(node, "refused", {})
    monkeypatch.setattr(node, "SNAPSHOT", "")
    monkeypatch.setattr(node, "SNAPSHOTS", str(tmp_path / "snapshot"))
    monkeypatch.setattr(node, "_snap", {"done": False, "said": "", "failed": ""})
    (tmp_path / "snapshot").mkdir()
    core.files = {}  # what snapshots.example serves: path -> bytes
    monkeypatch.setitem(chains.UTXO, "bitcoin", ("BTC", "esplora", chains.ESPLORA, chains.LITE))
    monkeypatch.setattr(chains, "DELAY", 0)
    core.public, core.client = public, lambda: real(transport=transport)
    return core


def serve(files, request):
    """A web server that serves byte ranges, as one serving a 9 GB snapshot does."""
    if request.url.path not in files:
        return httpx.Response(404)
    data = files[request.url.path]
    start = int(request.headers.get("range", "bytes=0-")[6:].split("-")[0])
    if start >= len(data):
        return httpx.Response(416, headers={"content-range": f"bytes */{len(data)}"})
    if not start:
        return httpx.Response(200, content=data)
    return httpx.Response(206, content=data[start:])


def never_public(core, *addresses):
    for address in addresses:
        assert not [u for u in core.public.asked if address in u], address


def settings(**env):
    code = (
        "import json; from tracker import chains, node; "
        "print(json.dumps([node.BITCOIND, node.MEMPOOL, chains.ESPLORA, chains.UTXO['bitcoin'][3]]))"
    )
    keep = {k: v for k, v in os.environ.items() if not k.startswith("CT_")}
    done = subprocess.run(
        [sys.executable, "-c", code], env={**keep, **env}, capture_output=True, text=True,
        check=True, cwd=ROOT,
    )  # fmt: skip
    return json.loads(done.stdout)


def sync_all(monkeypatch):
    """One sync of all my wallets, as the Sync button starts it; the steps that read other
    sources (labels, contracts, prices, tokens) left out."""
    monkeypatch.setattr(labels, "due", lambda db: False)
    monkeypatch.setattr(detect, "contracts", lambda db, client: 0)
    monkeypatch.setattr(prices, "snapshot", lambda db, client: None)
    monkeypatch.setattr(tokens, "mark", lambda db, client: None)
    assert sync._lock.acquire(blocking=False)
    sync.status.update(error="", notes=[])
    sync.run()
    assert sync.status["error"] == ""


def test_the_lite_profile_is_a_pruned_node_and_a_public_server_for_strangers():
    assert settings()[:2] == ["", ""]
    bitcoind, mempool, esplora, note = settings(CT_PROFILES="node-lite")
    assert (bitcoind, mempool, esplora) == (RPC, "", "https://blockstream.info/api")
    assert note == chains.LITE
    assert "your own node" in note and "blockstream.info, which sees them" in note


def test_my_wallets_are_registered_as_soon_as_the_node_answers_even_while_it_syncs(db, lite):
    lite.ibd = True
    mine(db, MINE)
    db.execute("INSERT INTO hdwallet(id, descriptor) VALUES ('hd:z', ?)", (ZPUB,))
    db.commit()
    node.refresh()
    imported = [r["desc"].split("#")[0] for r in lite.wallet["descs"]]
    (d,) = hd.descriptors(ZPUB)
    assert imported == [f"addr({MINE})", str(d.branch(0)), str(d.branch(1))]
    assert lite.wallet["descs"][0]["desc"] == f"addr({MINE})#06l628a2"  # Core wants checksums
    node.refresh()  # nothing new: nothing imported again
    assert sum(m == "importdescriptors" for m, _ in lite.asked) == 1
    # bitcoind's volume was wiped: a new wallet, and everything registered with it again
    lite.wallet, lite.on_disk = None, False
    node.refresh()
    assert len(lite.wallet["descs"]) == 3


def test_my_wallet_is_read_from_the_node_with_who_paid_me(db, lite):
    mine(db, MINE)
    db.commit()
    lite.ibd = True
    node.refresh()  # registered at the start of the sync
    lite.connect(
        [tx("a" * 64, [("c" * 64, 0, SENDER, 70_000)], [(MINE, 50_000), (SENDER, 19_000)])]
    )
    node.refresh()  # the watcher, while that block is still on disk
    lite.connect([tx("b" * 64, [("a" * 64, 0, MINE, 50_000)], [(SATOSHI, 30_000), (MINE, 19_000)])])
    lite.prune(5)  # both blocks gone before the sync reads them...
    lite.connect([])
    lite.ibd = False
    with lite.client() as client:
        sync.sync_address(db, client, MINE)
    got = {(r["txid"], r["src"], r["dst"]) for r in db.execute("SELECT * FROM transfer")}
    # ...the first was kept with its sender; the second's input is mine, from the first
    assert ("a" * 64, SENDER, MINE) in got and ("b" * 64, MINE, SATOSHI) in got
    row = db.execute("SELECT amount FROM balance WHERE address = ?", (MINE,)).fetchone()
    assert row["amount"] == 19_000 / 1e8  # scantxoutset: what is unspent now
    assert tuple(
        db.execute("SELECT error, hub FROM node WHERE address = ?", (MINE,)).fetchone()
    ) == (None, 0)
    assert history.state(db, MINE)["complete"]
    never_public(lite, MINE)


def test_a_wallet_added_after_the_first_look_gets_its_history_too(db, lite):
    mine(db, MINE)
    db.commit()
    node.refresh()  # the watcher's first look
    lite.connect([tx("9" * 64, [("c" * 64, 3, SENDER, 9000)], [(SATOSHI, 8000)])])
    node.refresh()  # and the next: it has seen that block
    mine(db, SATOSHI)  # a second wallet, added later, paid in a block still kept
    db.commit()
    with lite.client() as client:
        sync.sync_address(db, client, SATOSHI)
    got = {(r["txid"], r["src"], r["dst"]) for r in db.execute("SELECT * FROM transfer")}
    assert ("9" * 64, SENDER, SATOSHI) in got
    never_public(lite, SATOSHI)


def test_the_balance_follows_a_new_transaction(db, lite):
    mine(db, MINE)
    db.commit()
    lite.connect([tx("1" * 64, [("c" * 64, 4, SENDER, 9000)], [(MINE, 8000)])])
    with lite.client() as client:
        sync.sync_address(db, client, MINE)
        lite.connect([tx("2" * 64, [("c" * 64, 5, SENDER, 6000)], [(MINE, 5000)])])
        sync.sync_address(db, client, MINE)  # well within the scan's 10 minutes
    row = db.execute("SELECT amount FROM balance WHERE address = ?", (MINE,)).fetchone()
    assert row["amount"] == 13_000 / 1e8


def test_a_parent_in_the_same_pruned_block_is_read_first(db, lite):
    mine(db, MINE)
    db.commit()
    node.refresh()
    parent = tx("f" * 64, [("c" * 64, 6, SENDER, 9000)], [(MINE, 8000)])
    child = tx("0" * 64, [("f" * 64, 0, MINE, 8000)], [(SATOSHI, 7000)])  # first by txid
    lite.connect([parent, child])
    lite.prune(len(lite.blocks))  # gone before the next look
    lite.connect([])
    with lite.client() as client:
        sync.sync_address(db, client, MINE)
    got = {(r["txid"], r["src"], r["dst"]) for r in db.execute("SELECT * FROM transfer")}
    assert ("0" * 64, MINE, SATOSHI) in got  # its input, mine, filled in from the parent


def test_strangers_are_read_from_the_public_server_and_my_bitcoin_waits_for_the_node(db, lite):
    mine(db, MINE)
    db.commit()
    lite.ibd = True
    with lite.client() as client:
        sync.sync_address(db, client, MINE)
        sync.sync_address(db, client, SATOSHI)
    said = db.execute("SELECT error FROM node WHERE address = ?", (MINE,)).fetchone()[0]
    assert "still syncing" in said and "never from a public server" in said
    assert db.execute("SELECT error FROM node WHERE address = ?", (SATOSHI,)).fetchone()[0] is None
    assert any(f"/address/{SATOSHI}" in u for u in lite.public.asked)
    never_public(lite, MINE)
    covered = {c["chain"]: c["note"] for c in chains.coverage()}
    assert covered["bitcoin"] == chains.LITE
    assert chains.LITE in chains.notes("bitcoin")  # on each Bitcoin wallet's verdict too
    lite.up = False
    assert "does not answer" in node.notes()[0]
    with lite.client() as client:
        sync.sync_address(db, client, SATOSHI)  # with the node down, nobody is asked
    assert (
        "does not answer"
        in db.execute("SELECT error FROM node WHERE address = ?", (SATOSHI,)).fetchone()[0]
    )


def test_my_address_the_node_does_not_watch_yet_is_never_sent_out(db, lite, monkeypatch):
    monkeypatch.setattr(node, "_register", lambda db: node._wallet())  # no import happened
    mine(db, MINE)
    db.commit()
    with lite.client() as client:
        sync.sync_address(db, client, MINE)
        with pytest.raises(node.NotReady, match="not registered"):
            chains.fetch(client, MINE)  # a lookup: no `keep`, mine all the same
    assert "not registered" in db.execute("SELECT error FROM node").fetchone()[0]
    never_public(lite, MINE)
    # Nor an HD wallet's addresses, which are not marked mine before they are read.
    db.execute("INSERT INTO hdwallet(id, descriptor) VALUES ('hd:z', ?)", (ZPUB,))
    db.commit()
    with lite.client() as client:
        sync.discover(db, client, db.execute("SELECT * FROM hdwallet").fetchone())
    assert "not registered" in db.execute("SELECT error FROM hdwallet").fetchone()[0]
    never_public(lite, *BIP84)


def test_a_wallet_the_node_refuses_holds_up_nothing_else(db, lite):
    lite.refuse = "wpkh("
    mine(db, MINE)
    db.execute("INSERT INTO hdwallet(id, descriptor) VALUES ('hd:z', ?)", (ZPUB,))
    db.commit()
    with lite.client() as client:
        sync.sync_address(db, client, MINE)
        sync.sync_address(db, client, SATOSHI)
        sync.discover(db, client, db.execute("SELECT * FROM hdwallet").fetchone())
    errors = dict(db.execute("SELECT address, error FROM node").fetchall())
    assert errors == {MINE: None, SATOSHI: None}
    said = db.execute("SELECT error FROM hdwallet").fetchone()[0]
    assert "not registered" in said and "refused hd:z: Cannot parse it" in said
    never_public(lite, *BIP84)


def test_an_hd_wallet_is_read_from_the_node_only(db, lite, monkeypatch):
    db.execute("INSERT INTO hdwallet(id, descriptor) VALUES ('hd:z', ?)", (ZPUB,))
    db.commit()
    lite.connect([tx("d" * 64, [("c" * 64, 1, SENDER, 9000)], [(BIP84[0], 8000)])])
    sync_all(monkeypatch)
    row = db.execute("SELECT mine, wallet FROM node WHERE address = ?", (BIP84[0],)).fetchone()
    assert tuple(row) == (1, "hd:z")
    assert db.execute("SELECT error FROM hdwallet").fetchone()[0] is None
    assert lite.public.asked == []  # not one derived address went out


def test_a_wallet_added_after_the_node_pruned_says_what_it_misses(db, lite, monkeypatch):
    lite.connect([tx("e" * 64, [("c" * 64, 2, SENDER, 9000)], [(MINE, 8000)])])
    for _ in range(3):
        lite.connect([])
    lite.prune(4)
    mine(db, MINE)
    db.execute("INSERT INTO hdwallet(id, descriptor) VALUES ('hd:z', ?)", (ZPUB,))
    db.commit()
    with lite.client() as client:
        sync.sync_address(db, client, MINE)
    said = db.execute("SELECT error FROM node WHERE address = ?", (MINE,)).fetchone()[0]
    assert "from block 4 " in said and "resync" in said
    row = db.execute("SELECT amount FROM balance WHERE address = ?", (MINE,)).fetchone()
    assert row["amount"] == 8000 / 1e8  # the balance is whole all the same
    sync_all(monkeypatch)
    assert "from block 4 " in db.execute("SELECT error FROM hdwallet").fetchone()[0]


# ---- Starting from a UTXO snapshot (assumeutxo): NODE_SNAPSHOT


def methods(core):
    return [m for m, _ in core.asked]


def test_a_new_node_loads_the_snapshot_before_it_watches_my_wallets(db, lite, monkeypatch):
    # The chain: a payment to me at block 3, before the snapshot's block 5, and one after it
    lite.connect([tx("3" * 64, [("c" * 64, 0, SENDER, 9000)], [(MINE, 8000)])])
    lite.connect([])
    lite.connect([])
    lite.connect([tx("6" * 64, [("c" * 64, 1, SENDER, 6000)], [(MINE, 5000)])])
    mine(db, MINE)
    db.commit()
    file = Path(node.SNAPSHOTS) / "utxo-5.dat"
    file.write_text("5")
    monkeypatch.setattr(node, "SNAPSHOT", "snapshot/utxo-5.dat")  # as given, or just its name
    lite.headers = 2  # a new node: it fetches the block headers first
    node.snapshot()
    assert lite.base is None and lite.wallet is None and file.exists()
    said = " · ".join(node.notes())
    assert "fetching the chain's block headers" in said and "2 so far" in said
    with lite.client() as client:  # my wallet waits, and says why
        sync.sync_address(db, client, MINE)
    waits = db.execute("SELECT error FROM node WHERE address = ?", (MINE,)).fetchone()[0]
    assert "block headers" in waits and "never from a public server" in waits

    lite.headers = None
    node.snapshot()
    assert lite.base == 5 and not file.exists()  # loaded, then deleted
    (said,) = node.notes()  # nothing to wait for, only the check going on behind
    assert said.startswith("Bitcoin: your own node started from a snapshot of block 5")
    node.refresh()  # only now is the wallet made, my addresses registered with it
    assert methods(lite).index("loadtxoutset") < methods(lite).index("createwallet")
    with lite.client() as client:
        sync.sync_address(db, client, MINE)
    got = {r["txid"] for r in db.execute("SELECT txid FROM transfer")}
    assert got == {"6" * 64}  # from the snapshot's block on: the node never had block 3
    said = db.execute("SELECT error FROM node WHERE address = ?", (MINE,)).fetchone()[0]
    assert "from block 6 on" in said and "without NODE_SNAPSHOT" in said
    row = db.execute("SELECT amount FROM balance WHERE address = ?", (MINE,)).fetchone()
    assert row["amount"] == 13_000 / 1e8  # the balance is whole: the UTXO set has both
    never_public(lite, MINE)

    # It checks the snapshot from the genesis block on: the status line follows it
    lite.checked = 2
    checking = (
        "Bitcoin: your own node started from a snapshot of block 5, which it is checking in the "
        "background from the genesis block: block 2 of 5 (40%). Until then it trusts the "
        "snapshot's coins, whose hash Bitcoin Core carries."
    )
    assert node.notes() == [checking]
    lite.checked = 5
    assert node.notes() == []
    # The app restarts once the node has checked it and dropped the second chain state
    lite.base, loads = None, methods(lite).count("loadtxoutset")
    monkeypatch.setattr(node, "_snap", {"done": False, "said": "", "failed": ""})
    node.snapshot()
    assert node._snap["done"] and node.notes() == []
    assert methods(lite).count("loadtxoutset") == loads  # not loaded again, nothing to say


def test_a_snapshot_from_a_url_is_downloaded_first_and_carries_on_where_it_stopped(
    db, lite, monkeypatch
):
    for _ in range(3):
        lite.connect([])  # the chain up to the snapshot's block 5
    monkeypatch.setattr(node, "SNAPSHOT", "https://snapshots.example/mainnet-5-utxos.dat")
    whole = b"5" + b" " * 2_000_000  # the fake Core reads the height and ignores the rest
    lite.files["/mainnet-5-utxos.dat"] = whole
    part = Path(f"{node._file()[0]}.part")
    part.write_bytes(whole[:1_500_000])  # the app stopped half way
    node.snapshot()
    assert lite.base == 5
    assert not list(Path(node.SNAPSHOTS).iterdir())  # downloaded, loaded, deleted
    node.refresh()
    assert lite.wallet is not None


def test_a_whole_download_the_app_stopped_before_naming_is_not_downloaded_again(
    db, lite, monkeypatch
):
    for _ in range(3):
        lite.connect([])
    monkeypatch.setattr(node, "SNAPSHOT", "https://snapshots.example/mainnet-5-utxos.dat")
    lite.files["/mainnet-5-utxos.dat"] = whole = b"5" + b" " * 1000
    Path(f"{node._file()[0]}.part").write_bytes(whole)  # every byte, then the app stopped
    node.snapshot()  # the server has nothing past its end (416): it is whole
    assert lite.base == 5 and not list(Path(node.SNAPSHOTS).iterdir())


def test_a_refused_download_gives_way_to_a_new_url_and_another_url_s_part_is_never_resumed(
    db, lite, monkeypatch
):
    for _ in range(3):
        lite.connect([])
    lite.files["/mainnet-4-utxos.dat"] = b"4" + b" " * 1000
    lite.files["/mainnet-5-utxos.dat"] = b"5" + b" " * 1000
    kept = Path(node.SNAPSHOTS) / "mine.dat"
    kept.write_text("5")  # a file I put there myself: never the app's to delete
    monkeypatch.setattr(node, "SNAPSHOT", "https://snapshots.example/mainnet-4-utxos.dat")
    node.snapshot()
    assert "not recognized" in node.notes()[0] and lite.base is None
    assert list(Path(node.SNAPSHOTS).iterdir()) == [kept]  # the download, of no use: gone

    # Another URL whose download stopped half way, then the one that fits; the app restarted
    monkeypatch.setattr(node, "SNAPSHOT", "https://snapshots.example/other-5-utxos.dat")
    Path(f"{node._file()[0]}.part").write_bytes(b"4" + b" " * 500)
    monkeypatch.setattr(node, "_snap", {"done": False, "said": "", "failed": ""})
    monkeypatch.setattr(node, "SNAPSHOT", "https://snapshots.example/mainnet-5-utxos.dat")
    node.snapshot()
    assert lite.base == 5  # downloaded whole from the new URL, not resumed from the old part
    assert list(Path(node.SNAPSHOTS).iterdir()) == [kept]


def test_a_snapshot_that_cannot_be_had_or_is_refused_holds_my_wallets_and_says_why(
    db, lite, monkeypatch
):
    mine(db, MINE)
    db.commit()
    monkeypatch.setattr(node, "SNAPSHOT", "https://snapshots.example/gone.dat")
    node.snapshot()  # a 404: asked once, not every 5 seconds
    node.snapshot()
    assert "HTTP 404" in node.notes()[0] and "remove the setting" in node.notes()[0]
    with pytest.raises(node.NotReady, match="HTTP 404"):
        node.read(MINE)
    assert node.read(SATOSHI) is None  # a stranger's address goes to the public server as ever
    assert lite.wallet is None

    monkeypatch.setattr(node, "_snap", {"done": False, "said": "", "failed": ""})
    monkeypatch.setattr(node, "SNAPSHOT", "utxo-4.dat")
    node.snapshot()
    assert "is not in snapshot/ next to compose.yaml" in node.notes()[0]
    (Path(node.SNAPSHOTS) / "utxo-4.dat").write_text("4")  # put there later: found then
    node.snapshot()
    node.snapshot()
    said = node.notes()[0]  # a height Bitcoin Core 31.3 has no hash for: its own words
    assert "not recognized" in said and "The following snapshot heights are available: 5" in said
    assert methods(lite).count("loadtxoutset") == 1 and lite.wallet is None
    assert (Path(node.SNAPSHOTS) / "utxo-4.dat").exists()  # not deleted: it was not loaded


def test_a_node_that_started_without_the_snapshot_keeps_going_and_says_it_is_not_used(
    db, lite, monkeypatch
):
    mine(db, MINE)
    db.commit()
    node.refresh()  # the node's wallet, from before the setting
    (Path(node.SNAPSHOTS) / "u.dat").write_text("5")
    monkeypatch.setattr(node, "SNAPSHOT", "u.dat")
    node.snapshot()
    assert "loadtxoutset" not in methods(lite)  # it would stop bitcoind from starting again
    assert "NODE_SNAPSHOT is not used" in node.notes()[0]
    node.read(MINE)  # my wallets carry on from the node
    assert (Path(node.SNAPSHOTS) / "u.dat").exists()


def test_the_status_line_keeps_asking_while_the_node_has_something_to_say(db, lite, monkeypatch):
    web = TestClient(app, base_url="http://localhost")
    assert web.get("/api/tracker/sync").json()["node"] is False
    lite.ibd = True
    status = web.get("/api/tracker/sync").json()
    assert status["node"] is True and "still syncing" in status["notes"][0]


def test_with_the_lite_node_my_bitcoin_addresses_go_to_no_swap_api(db, monkeypatch):
    monkeypatch.setattr(node, "BITCOIND", RPC)
    mine(db, MINE)
    mine(db, "0x" + "ab" * 20)
    db.commit()
    asked = []

    def midgard(request):
        asked.append(request.url.params["address"])
        return httpx.Response(200, json={"actions": []})

    with httpx.Client(transport=httpx.MockTransport(midgard)) as client:
        swaps.sync(db, client)
    assert MINE not in asked and "0x" + "ab" * 20 in asked
    assert swaps.OWN_NODE in swaps.notes({"bitcoin"})


# ---- node/bitcoind-lite.sh: the disk cap, as compose.yaml starts bitcoind with it

WRAPPER = ROOT / "node" / "bitcoind-lite.sh"


def start(tmp_path, cap, **more):
    """The wrapper with the image's entrypoint replaced by one that prints its arguments."""
    entry = tmp_path / "entrypoint.sh"
    entry.write_text('#!/bin/sh\necho "$@"\n')
    entry.chmod(0o755)
    script = tmp_path / "lite.sh"
    script.write_text(WRAPPER.read_text().replace("/entrypoint.sh", str(entry)))
    ours = ("NODE_MAX_DISK_GB", "NODE_SNAPSHOT", "BITCOIN_DATA")
    env = {k: v for k, v in os.environ.items() if k not in ours}
    env |= {"BITCOIN_DATA": str(tmp_path / "data")} | more  # the image sets BITCOIN_DATA
    env |= {"NODE_MAX_DISK_GB": cap} if cap is not None else {}
    return subprocess.run(
        ["sh", script, "-server=1"],
        check=False,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_one_setting_sizes_the_node(tmp_path):
    done = start(tmp_path, "50")
    assert done.returncode == 0, done.stderr
    # 50 GB less the 18 the chain state and the rest take: 32 GB of blocks, in MiB
    assert done.stdout.split() == ["-server=1", f"-prune={32 * 1024}"]
    assert start(tmp_path, "20").stdout.split()[-1] == "-prune=2048"  # the floor
    for bad in ("19", "", "fifty", "50.5", "050", None):
        refused = start(tmp_path, bad)
        assert refused.returncode == 1 and "NODE_MAX_DISK_GB" in refused.stderr, bad
        assert "at least 20" in refused.stderr and not refused.stdout


def test_a_snapshot_makes_room_for_a_second_chain_state(tmp_path):
    # While the node checks a snapshot it keeps two chain states: 17 GB more than without one
    done = start(tmp_path, "50", NODE_SNAPSHOT="utxo-935000.dat")
    assert done.returncode == 0, done.stderr
    assert done.stdout.split()[-1] == f"-prune={15 * 1024}"
    assert "second chain state" in done.stderr  # said in its log, not only in the README
    assert start(tmp_path, "37", NODE_SNAPSHOT="u.dat").stdout.split()[-1] == "-prune=2048"
    refused = start(tmp_path, "36", NODE_SNAPSHOT="https://example.org/u.dat")
    assert refused.returncode == 1 and not refused.stdout
    assert "at least 37 with a snapshot" in refused.stderr and "1100 MiB" in refused.stderr
    # NODE_SNAPSHOT removed before the node has checked it: the second chain state is still there
    (tmp_path / "data" / "chainstate_snapshot").mkdir(parents=True)
    assert start(tmp_path, "50").stdout.split()[-1] == f"-prune={15 * 1024}"


def test_the_floor_is_the_same_in_the_installer_and_the_node():
    floor = [
        line.split("=")[1].split()[0]
        for path in (WRAPPER, ROOT / "proxmox" / "install.sh")
        for line in path.read_text().splitlines()
        if line.startswith(("MIN=", "NODE_MIN_DISK=", "SECOND=", "NODE_SNAPSHOT_DISK="))
    ]
    assert floor == ["20", "17", "20", "17"]


def test_compose_runs_the_lite_profile_without_an_index():
    import yaml

    spec = yaml.safe_load((ROOT / "compose.yaml").read_text())
    lite = spec["services"]["bitcoind-lite"]
    assert lite["profiles"] == ["node-lite"]
    assert lite["entrypoint"] == ["/bin/sh", "/bitcoind-lite.sh"]
    assert not [a for a in lite["command"] if "txindex" in a or "disablewallet" in a]
    assert "-nodebuglogfile=1" in lite["command"]  # logs go to Docker's, capped at 30 MB
    assert lite["environment"]["NODE_MAX_DISK_GB"] == "${NODE_MAX_DISK_GB:-50}"
    app = spec["services"]["crypto-tracker"]
    assert "bitcoind-rpc:/rpc:ro" in app["volumes"]
    # A snapshot: the app downloads or finds it in snapshot/, the node reads it from there
    assert app["environment"]["CT_SNAPSHOT"] == "${NODE_SNAPSHOT:-}"
    assert lite["environment"]["NODE_SNAPSHOT"] == "${NODE_SNAPSHOT:-}"  # sizes its cap
    assert "./snapshot:/snapshot" in app["volumes"] and "./snapshot:/snapshot:ro" in lite["volumes"]
    assert node.SNAPSHOTS == "/snapshot"
    # 9 GB that never go into an image build, nor into git
    assert "snapshot" in (ROOT / ".dockerignore").read_text().split()
    assert "snapshot/" in (ROOT / ".gitignore").read_text().split()
    # the full profile is untouched
    assert "-txindex=1" in spec["services"]["bitcoind"]["command"]


def test_capped_logs_name_their_driver():
    # Debian's Docker defaults to journald, which refuses max-size and max-file at start
    import yaml

    services = yaml.safe_load((ROOT / "compose.yaml").read_text())["services"]
    capped = {n: s["logging"] for n, s in services.items() if s.get("logging", {}).get("options")}
    assert {"bitcoind", "fulcrum", "bitcoind-lite"} <= capped.keys()
    # json-file keeps the README's cap: 3 files of 10 MB, uncompressed
    assert {n: log.get("driver") for n, log in capped.items()} == dict.fromkeys(capped, "json-file")


# ---- The privacy scanner's fees and broadcasts, through the lite node

SIGNED = "02000000000101" + "ab" * 60  # a signed transaction, as far as the fake node cares


@pytest.fixture
def web(lite):
    return TestClient(app, base_url="http://localhost")


def test_the_scanner_takes_its_fee_estimates_from_the_node(web, lite):
    assert web.get("/api/tracker/node").json() == {"state": "ready"}
    lite.minfee = 0.00003  # a full mempool: nothing below 3 sat/vB gets in
    fees = web.get("/api/tracker/node/v1/fees/recommended").json()
    # mempool's shape, in sat/vB: what am-i-exposed's own client reads
    assert fees == {"fastestFee": 20.0, "halfHourFee": 12.0, "hourFee": 10.0, "economyFee": 3.0,
                    "minimumFee": 3.0}  # fmt: skip
    assert ("estimatesmartfee", [6]) in lite.asked
    assert web.get("/api/tracker/node/blocks/tip/height").text == str(lite.tip()["height"])

    del lite.estimates[144]  # a node just synced has no long estimates yet
    r = web.get("/api/tracker/node/v1/fees/recommended")
    assert r.status_code == 503 and r.json()["detail"] == node.NO_FEES
    assert not lite.public.asked  # nothing went to a public server


def test_the_scanner_broadcasts_through_the_node(web, lite, monkeypatch):
    plain = {"content-type": "text/plain", "sec-fetch-site": "same-origin"}
    txid = lite.txid_of(SIGNED)
    dry = web.post("/api/tracker/node/txs/test", content=f'["{SIGNED}"]', headers=plain)
    assert dry.json() == [{"txid": txid, "allowed": True, "vsize": 141}]
    status = f"/api/tracker/node/tx/{txid}/status"
    assert web.get(status).status_code == 404  # "probably not sent"

    sent = web.post("/api/tracker/node/tx", content=SIGNED, headers=plain)
    assert sent.status_code == 200 and sent.text == txid
    assert ("sendrawtransaction", [SIGNED]) in lite.asked
    assert web.get(status).json() == {"confirmed": False}
    assert not lite.public.asked

    # Refused: the node's own code and words, as mempool passes them on.
    lite.refuse_tx = (-26, "min relay fee not met, 100 < 141")
    r = web.post("/api/tracker/node/tx", content=SIGNED, headers=plain)
    assert r.status_code == 400 and r.json()["code"] == -26
    assert r.json()["message"] == "your Bitcoin node: min relay fee not met, 100 < 141"
    asked = len(lite.asked)
    r = web.post("/api/tracker/node/tx", content="not hex", headers=plain)
    assert r.status_code == 400 and r.json()["code"] == -22
    assert not [m for m, _ in lite.asked[asked:] if m == "sendrawtransaction"]
    bad = web.post("/api/tracker/node/txs/test", content="[12]", headers=plain)
    assert bad.status_code == 400
    # The node's own refusal of the dry run shows before sending, as one of its answers.
    monkeypatch.setattr(lite, "testmempoolaccept", lambda raws: (_ for _ in ()).throw(
        Fault(-22, "TX decode failed")))  # fmt: skip
    dry = web.post("/api/tracker/node/txs/test", content=f'["{SIGNED}"]', headers=plain).json()
    assert dry == [{"allowed": False, "reject-reason": "your Bitcoin node: TX decode failed"}]


def test_a_node_that_is_not_ready_says_so_and_sends_nothing(web, lite, monkeypatch):
    lite.ibd = True
    r = web.post("/api/tracker/node/tx", content=SIGNED)
    # A code, so the scanner reads a refusal; without one a 5xx would read "maybe sent".
    assert r.status_code == 503 and r.json()["code"] == NOT_READY
    assert "still syncing" in r.json()["message"]
    dry = web.post("/api/tracker/node/txs/test", content=f'["{SIGNED}"]').json()
    assert not dry[0]["allowed"] and "still syncing" in dry[0]["reject-reason"]
    assert web.get("/api/tracker/node/v1/fees/recommended").status_code == 503
    assert web.get("/api/tracker/node/blocks/tip/height").status_code == 503
    assert web.get("/api/tracker/node").json() == {"state": "syncing"}

    lite.up = False
    r = web.post("/api/tracker/node/tx", content=SIGNED)
    assert r.json()["code"] == NOT_READY and "does not answer" in r.json()["message"]
    # Down between the check and the send: never reached it, so a refusal too.
    monkeypatch.setattr(node, "unready", lambda: "")
    r = web.post("/api/tracker/node/tx", content=SIGNED)
    assert r.json()["code"] == NOT_READY and "does not answer" in r.json()["message"]
    assert not [m for m, _ in lite.asked if m == "sendrawtransaction"]

    # A connection never made is a refusal too.
    lite.up = True
    monkeypatch.setattr(lite, "sendrawtransaction", lambda h: (_ for _ in ()).throw(
        httpx.ConnectTimeout("no connection")))  # fmt: skip
    r = web.post("/api/tracker/node/tx", content=SIGNED)
    assert r.json()["code"] == NOT_READY and "does not answer" in r.json()["message"]

    # Lost on the way back: it may have gone out. No code, so the scanner offers to check.
    monkeypatch.setattr(lite, "sendrawtransaction", lambda h: (_ for _ in ()).throw(
        httpx.ReadTimeout("timed out")))  # fmt: skip
    r = web.post("/api/tracker/node/tx", content=SIGNED)
    assert r.status_code == 502 and "code" not in r.text


def test_broadcasts_take_the_password_and_this_app_s_own_pages(web, lite, monkeypatch):
    other_site = {"content-type": "text/plain", "sec-fetch-site": "cross-site"}
    assert web.post("/api/tracker/node/tx", content=SIGNED, headers=other_site).status_code == 403
    # Over plain http on a LAN address browsers send no Sec-Fetch-Site: the Origin then counts.
    for origin in ("http://evil.example", "http://localhost:8000", "null"):
        r = web.post("/api/tracker/node/tx", content=SIGNED, headers={"origin": origin})
        assert r.status_code == 403, origin
    dry = web.post("/api/tracker/node/txs/test", content=f'["{SIGNED}"]', headers=other_site)
    assert dry.status_code == 403
    monkeypatch.setenv("CT_PASSWORD", "s3cret")
    for path in ("/api/tracker/node", "/api/tracker/node/v1/fees/recommended"):
        assert web.get(path).status_code == 401
    assert web.post("/api/tracker/node/tx", content=SIGNED).status_code == 401
    assert not [m for m, _ in lite.asked if m in ("sendrawtransaction", "estimatesmartfee")]
    signed_in = web.post("/api/tracker/node/tx", content=SIGNED, auth=("", "s3cret"))
    assert signed_in.text == lite.txid_of(SIGNED)
    own_page = {"origin": "http://localhost"}
    r = web.post("/api/tracker/node/tx", content=SIGNED, auth=("", "s3cret"), headers=own_page)
    assert r.text == lite.txid_of(SIGNED)


def test_without_a_lite_node_the_scanner_keeps_its_own_servers(web, monkeypatch):
    monkeypatch.setattr(node, "BITCOIND", "")  # no node, or the full mempool profile
    assert web.get("/api/tracker/node").status_code == 404
    assert web.get("/api/tracker/node/v1/fees/recommended").status_code == 404
    assert web.post("/api/tracker/node/tx", content=SIGNED).status_code == 404
