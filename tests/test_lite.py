"""A lite Bitcoin node of your own (the `node-lite` profile): a pruned Bitcoin Core that watches
my own wallets, sized from one disk cap. My addresses are read from it only; other addresses
from the public server, and every view says so."""

import copy
import functools
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from embit.script import address_to_scriptpubkey
from test_core import BIP84, SATOSHI, ZPUB, mine

from tracker import chains, detect, hd, history, labels, node, prices, swaps, sync, tokens

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
            results.append({"success": not self.pruned} if not self.pruned else
                           {"success": False, "error": {"code": -1, "message": PRUNED_MSG}})  # fmt: skip
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
    monkeypatch.setitem(chains.UTXO, "bitcoin", ("BTC", "esplora", chains.ESPLORA, chains.LITE))
    monkeypatch.setattr(chains, "DELAY", 0)
    core.public, core.client = public, lambda: real(transport=transport)
    return core


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


def start(tmp_path, cap):
    """The wrapper with the image's entrypoint replaced by one that prints its arguments."""
    entry = tmp_path / "entrypoint.sh"
    entry.write_text('#!/bin/sh\necho "$@"\n')
    entry.chmod(0o755)
    script = tmp_path / "lite.sh"
    script.write_text(WRAPPER.read_text().replace("/entrypoint.sh", str(entry)))
    env = {k: v for k, v in os.environ.items() if k != "NODE_MAX_DISK_GB"}
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
    for bad in ("19", "", "fifty", "50.5", None):
        refused = start(tmp_path, bad)
        assert refused.returncode == 1 and "NODE_MAX_DISK_GB" in refused.stderr, bad
        assert "at least 20" in refused.stderr and not refused.stdout


def test_compose_runs_the_lite_profile_without_an_index():
    import yaml

    spec = yaml.safe_load((ROOT / "compose.yaml").read_text())
    lite = spec["services"]["bitcoind-lite"]
    assert lite["profiles"] == ["node-lite"]
    assert lite["entrypoint"] == ["/bin/sh", "/bitcoind-lite.sh"]
    assert not [a for a in lite["command"] if "txindex" in a or "disablewallet" in a]
    assert "-nodebuglogfile=1" in lite["command"]  # logs go to Docker's, capped at 30 MB
    assert lite["environment"]["NODE_MAX_DISK_GB"] == "${NODE_MAX_DISK_GB:-50}"
    assert "bitcoind-rpc:/rpc:ro" in spec["services"]["crypto-tracker"]["volumes"]
    # the full profile is untouched
    assert "-txindex=1" in spec["services"]["bitcoind"]["command"]
