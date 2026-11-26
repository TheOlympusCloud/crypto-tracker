"""Nodes of your own: the local stacks compose.yaml can start next to the app (its profiles),
or one on your network. For now Bitcoin's: a mempool on bitcoind and Fulcrum, or a pruned
bitcoind alone (the lite node), which watches my own wallets only.

While such a node is not ready, the app waits for it and says so: it never falls back to a
public server, which would see your addresses exactly when you set up a node to hide them.
With the lite node that holds for my own addresses. Any other has no index to be read from
there: it goes to the public server, and every view says so (chains.LITE).
"""

import json
import os
import re
import sys
import threading
import time
from collections import Counter
from datetime import UTC, datetime

import httpx
from embit.descriptor.checksum import add_checksum
from embit.script import address_to_scriptpubkey

from . import db as database
from . import hd

# COMPOSE_PROFILES, passed on by compose.yaml: the local stacks running next to the app.
PROFILES = {p.strip() for p in os.environ.get("CT_PROFILES", "").split(",") if p.strip()}


def local(profile, url):
    """`url` when compose.yaml runs `profile` next to the app, else "". The hook for local nodes:
    a chain's default URL is `CT_<CHAIN> or local(<profile>, <its service>) or <public URL>`."""
    return url if profile in PROFILES else ""


# A mempool instance (its web address; /api below it): set by hand for one on your network,
# or the one of the `mempool` profile.
MEMPOOL = (os.environ.get("CT_MEMPOOL") or local("mempool", "http://mempool-web:8080")).rstrip("/")
PORT = os.environ.get("CT_MEMPOOL_PORT", "")  # where the profile's mempool site is published
# Its site as the browser opens it: one on your network as it is; the profile's through a
# redirect to its port on the host the browser came by (app.py).
SITE = os.environ.get("CT_MEMPOOL", "").rstrip("/") or (MEMPOOL and "/api/tracker/mempool")

# The `node-lite` profile's pruned bitcoind: its RPC, signed in with the cookie it writes at
# each start (compose.yaml shares it read-only). It keeps the newest blocks only and has no
# address index: it watches my own wallets in a wallet of its own, and the app keeps what it
# finds there, senders included, while their blocks are still on disk.
BITCOIND = local("node-lite", "http://bitcoind-lite:8332")
COOKIE = "/rpc/.cookie"
WALLET = "crypto-tracker"
# shortcut: an HD branch used past index 999 is not watched beyond it; raise RANGE then.
RANGE = 1000  # addresses watched per branch of an HD wallet (sync.discover stops 20 past use)
WATCH_EVERY = 15  # seconds between looks at what it found: while it syncs, it prunes fast
SCAN_EVERY = 600  # seconds a balance scan is reused: one takes minutes on mainnet

STALE = 6 * 3600  # a chain tip older than this: the node is still catching up
PROBE = "bc1q432elxuhwenlg70f2cgms9fa0p09g239rj9z9d"  # unused: the index answers at once
TTL = 30  # seconds a status is reused
_last = {"at": 0.0, "status": None}

WAITS = "Bitcoin addresses are read once it is ready, never from a public server meanwhile."
# (Nor are they sent to the swap APIs while there is a node: swaps.py.)
NOTICES = {
    "down": "Bitcoin: your own node does not answer. It starts after bitcoind and Fulcrum, or "
    "it is stopped (docker compose ps).",
    "syncing": "Bitcoin: your own node is still syncing; it has the chain up to {date} "
    "(block {height:,}).",
    "indexing": "Bitcoin: your own node has the whole chain; its address index (Fulcrum) is "
    "still being built, which takes hours.",
}


LITE_WAITS = (
    "Your Bitcoin wallets are read from it once it is ready, never from a public server meanwhile."
)
LITE_NOTICES = {
    "down": "Bitcoin: your own node does not answer: it is starting, or it is stopped "
    "(docker compose ps).",
    "syncing": NOTICES["syncing"],
}
BUSY = (
    "Bitcoin: your own node is reading back through its blocks for a wallet added just now. "
    + LITE_WAITS
)
UNWATCHED = (
    "Bitcoin: this address of yours is not registered with your own node yet. It is read from "
    "the node once it is, never from a public server."
)


class NotReady(RuntimeError):
    pass


class RpcError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(f"your Bitcoin node: {message}")
        self.code = code


def status():
    """Where the node stands: {"state": "ready" | "syncing" | "indexing" | "down", "height",
    "time"}, or None without one. A mempool is asked through its own API, so one on your
    network is judged the same way as the local one; the lite node through its RPC."""
    if not (MEMPOOL or BITCOIND):
        return None
    if _last["at"] > time.time() - TTL:
        return _last["status"]
    found = _lite_status() if BITCOIND else _mempool_status()
    _last.update(at=time.time(), status=found)
    return found


def _lite_status():
    try:
        info = rpc("getblockchaininfo", timeout=5)
    except (httpx.HTTPError, OSError, ValueError, RpcError):  # -28: still loading its index
        return {"state": "down", "height": None, "time": None}
    behind = info["initialblockdownload"] or info["time"] < time.time() - STALE
    return {
        "state": "syncing" if behind else "ready",
        "height": info["blocks"],
        "time": info["time"],
    }


def _mempool_status():
    found = {"state": "down", "height": None, "time": None}
    with httpx.Client(timeout=5, headers={"User-Agent": "crypto-tracker"}) as client:
        try:
            tip = client.get(f"{MEMPOOL}/api/blocks/tip/hash").raise_for_status().text.strip()
            if not re.fullmatch(r"[0-9a-f]{64}", tip):
                raise ValueError("not a block hash")
            block = client.get(f"{MEMPOOL}/api/block/{tip}").raise_for_status().json()
            found |= {"height": int(block["height"]), "time": int(block["timestamp"])}
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            pass
        else:
            if found["time"] < time.time() - STALE:
                found["state"] = "syncing"
            else:
                try:
                    client.get(f"{MEMPOOL}/api/address/{PROBE}").raise_for_status()
                    found["state"] = "ready"
                except httpx.HTTPError:
                    found["state"] = "indexing"
    return found


def notice(found):
    """What to tell the user about a node that is not ready."""
    date = datetime.fromtimestamp(found["time"] or 0, UTC).strftime("%d %b %Y")
    said, waits = (LITE_NOTICES, LITE_WAITS) if BITCOIND else (NOTICES, WAITS)
    return said[found["state"]].format(date=date, height=found["height"] or 0) + " " + waits


def notes():
    """The notice, when there is a node and it is not ready."""
    found = status()
    return [notice(found)] if found and found["state"] != "ready" else []


def guard(url):
    """Before reading Bitcoin from MEMPOOL: stop with the notice while it is not ready."""
    if MEMPOOL and url == f"{MEMPOOL}/api" and (found := status())["state"] != "ready":
        raise NotReady(notice(found))


# ---- The lite node


def rpc(method, *params, wallet=False, timeout=60, **named):
    """One JSON-RPC 2.0 call to the lite node (its wallet's with `wallet`): errors come back as
    RpcError, the node unreachable as httpx.HTTPError or OSError (no cookie yet)."""
    with open(COOKIE) as f:
        user, _, password = f.read().strip().partition(":")
    body = {"jsonrpc": "2.0", "id": method, "method": method, "params": named or list(params)}
    with httpx.Client(timeout=timeout, auth=(user, password)) as client:
        r = client.post(f"{BITCOIND}/wallet/{WALLET}" if wallet else BITCOIND, json=body)
    r.raise_for_status()
    answer = r.json()
    if answer.get("error"):
        raise RpcError(answer["error"]["code"], answer["error"]["message"])
    return answer["result"]


def _down():
    return NotReady(notice({"state": "down", "height": None, "time": None}))


def _wallet():
    """Load the node's wallet, or make it (watch-only, empty): True when made just now, on a
    new node or one whose volume was wiped."""
    if WALLET in rpc("listwallets"):
        return False
    try:
        rpc("loadwallet", WALLET, True)
        return False
    except RpcError as e:
        if e.code != -18:  # RPC_WALLET_NOT_FOUND
            raise
    rpc(
        "createwallet", wallet_name=WALLET, disable_private_keys=True, blank=True,
        load_on_startup=True,
    )  # fmt: skip
    return True


refused = {}  # wallets (an address, or an HD wallet's id) the node would not watch -> why


def _register(db):
    """Have the node watch my Bitcoin addresses and HD wallets it does not watch yet, from the
    genesis block on: registered before it syncs, it sees all of their history. One added
    once it has pruned is read back as far as it still has blocks: bitcoind_watch keeps
    where that was."""
    if _wallet():
        db.execute("DELETE FROM bitcoind_watch")  # a new wallet watches nothing yet
        db.execute("DELETE FROM meta WHERE key = 'bitcoind_lastblock'")
    known = {r[0] for r in db.execute("SELECT key FROM bitcoind_watch")}
    wanted = {}
    for (address,) in db.execute("SELECT address FROM node WHERE mine AND wallet IS NULL"):
        if address not in known and database.chain_of(address) == "bitcoin":
            wanted[address] = [{"desc": add_checksum(f"addr({address})"), "timestamp": 0}]
    for w in db.execute("SELECT id, descriptor FROM hdwallet"):
        if w["id"] not in known:
            wanted[w["id"]] = [
                {"desc": add_checksum(str(b)), "timestamp": 0}
                | ({"range": [0, RANGE - 1]} if b.is_wildcard else {})
                for d in hd.descriptors(w["descriptor"])
                for b in (d.branch(i) for i in range(d.num_branches))
            ]
    if not wanted:
        return
    requests = [r for rs in wanted.values() for r in rs]
    results = iter(rpc("importdescriptors", requests, wallet=True, timeout=None))
    low = None
    for key, rs in wanted.items():
        errors = [r["error"]["message"] for r in (next(results) for _ in rs) if not r["success"]]
        if bad := [e for e in errors if "pruning" not in e]:
            refused[key] = bad[0]  # asked again at each look; its addresses wait meanwhile
            continue
        refused.pop(key, None)
        if errors and low is None:  # a rescan that met pruned blocks: read back to there
            low = rpc("getblockchaininfo")["pruneheight"]
        db.execute(
            "INSERT OR REPLACE INTO bitcoind_watch VALUES (?, ?)", (key, low if errors else 0)
        )
    db.commit()


def _capture(db):
    """Keep the transactions the node's wallet found since the last look, with the coins
    their inputs spend (`prevout`), which the node reads from the undo data of their block
    while it still has it. For one already pruned: the wallet's copy, with the coins that
    were mine filled in from their own transactions; a stranger's stays unknown."""
    row = db.execute("SELECT value FROM meta WHERE key = 'bitcoind_lastblock'").fetchone()
    found = rpc("listsinceblock", row[0] if row else "", 1, False, True, wallet=True)
    gone = [(e["txid"],) for e in found["removed"]]  # a reorg
    db.executemany("DELETE FROM bitcoind_tx WHERE txid = ?", gone)
    db.executemany("DELETE FROM bitcoind_addr WHERE txid = ?", gone)
    confirmed = {
        (e["blockheight"], e["txid"], e["blockhash"], e["blocktime"])
        for e in found["transactions"]
        if e.get("blockhash")
    }
    for height, txid, block, ts in sorted(confirmed):
        if db.execute("SELECT 1 FROM bitcoind_tx WHERE txid = ?", (txid,)).fetchone():
            continue
        try:
            tx = rpc("getrawtransaction", txid, 2, block)
        except RpcError:  # "Block not available": pruned
            tx = rpc("gettransaction", txid, True, True, wallet=True)["decoded"]
            for i in tx["vin"]:
                parent = db.execute(
                    "SELECT data FROM bitcoind_tx WHERE txid = ?", (i.get("txid"),)
                ).fetchone()
                if parent:
                    i["prevout"] = json.loads(parent[0])["vout"][i["vout"]]
        tx.pop("hex", None)
        coins = [o["scriptPubKey"] for o in tx["vout"]]
        coins += [i["prevout"]["scriptPubKey"] for i in tx["vin"] if i.get("prevout")]
        db.execute(
            "INSERT OR REPLACE INTO bitcoind_tx VALUES (?, ?, ?, ?)",
            (txid, height, ts, json.dumps(tx)),
        )
        db.executemany(
            "INSERT OR IGNORE INTO bitcoind_addr VALUES (?, ?)",
            [(c["address"], txid) for c in coins if c.get("address")],
        )
    db.execute(
        "INSERT OR REPLACE INTO meta VALUES ('bitcoind_lastblock', ?)", (found["lastblock"],)
    )
    db.commit()


_lock = threading.Lock()  # one look at the node at a time: the watcher's or a read's


def refresh(db=None):
    """Register my wallets with the lite node, then keep what it found since the last look."""
    own = db is None
    db = db or database.connect()
    try:
        _register(db)
        _capture(db)
    finally:
        if own:
            db.close()


def missing(db, key):
    """What the node does not have of a wallet (an address, or an HD wallet's id) registered
    once it had pruned, or ""."""
    row = db.execute("SELECT since FROM bitcoind_watch WHERE key = ?", (key,)).fetchone()
    if not row or not row[0]:
        return ""
    return (
        f"Bitcoin: read from your own node from block {row[0]:,} on: it no longer had the "
        "older blocks when this wallet was added; resync the node to read all of it (README, "
        '"A lite node").'
    )


def read(address, mine=()):
    """Lite node: (the transactions of `address` it found, newest first, as its own JSON with
    "height" and "time"; what it misses) when it watches the address. None for an address
    that is not mine, to be read from the public server; never for one in `mine` or marked
    mine in the database."""
    if not _lock.acquire(timeout=30):
        raise NotReady(BUSY)
    db = database.connect()
    try:
        refresh(db)
        if not rpc("getaddressinfo", address, wallet=True)["ismine"]:
            listed = db.execute("SELECT 1 FROM node WHERE address = ? AND mine", (address,))
            if address in mine or listed.fetchone():
                why = "".join(f" Your node refused {k}: {e}" for k, e in refused.items())
                raise NotReady(UNWATCHED + why)
            return None
        if (found := status())["state"] != "ready":
            raise NotReady(notice(found))
        rows = db.execute(
            "SELECT t.height, t.time, t.data FROM bitcoind_tx t JOIN bitcoind_addr a USING (txid) "
            "WHERE a.address = ? ORDER BY t.height DESC",
            (address,),
        ).fetchall()
        txs = [json.loads(r["data"]) | {"height": r["height"], "time": r["time"]} for r in rows]
        return txs, missing(db, address)
    except (httpx.HTTPError, OSError, ValueError) as e:
        raise _down() from e
    finally:
        db.close()
        _lock.release()


_scan = {"at": 0.0, "key": None, "sats": {}}
_scanning = threading.Lock()


def balance(address):
    """Satoshis `address` holds now, from the node's UTXO set (scantxoutset): whole even for a
    wallet whose older history the node no longer has. One scan covers every descriptor the
    node watches, and is reused while it is new."""
    try:
        with _scanning:
            descs = rpc("listdescriptors", wallet=True)["descriptors"]
            objects = [{"desc": d["desc"], "range": d["range"]} if "range" in d else d["desc"]
                       for d in descs]  # fmt: skip
            key = json.dumps(objects)
            if key != _scan["key"] or _scan["at"] < time.time() - SCAN_EVERY:
                found = rpc("scantxoutset", "start", objects, timeout=3600)
                sats = Counter()
                for u in found["unspents"]:
                    sats[u["scriptPubKey"]] += round(u["amount"] * 1e8)
                _scan.update(at=time.time(), key=key, sats=sats)
            return _scan["sats"].get(address_to_scriptpubkey(address).data.hex(), 0)
    except (httpx.HTTPError, OSError, ValueError) as e:
        raise _down() from e


def watch():
    """Look at what the lite node found every WATCH_EVERY seconds while the server runs: while
    it syncs it prunes a block minutes after reading it, and the senders of a transaction
    are on disk only with its block."""

    def loop():
        said = ""
        while True:
            try:
                with _lock:
                    refresh()
                said = ""
            except Exception as e:  # noqa: BLE001 - a background thread must report, not die
                if str(e) != said:
                    print(f"bitcoin node: {e}", file=sys.stderr, flush=True)
                    said = str(e)
            time.sleep(WATCH_EVERY)

    threading.Thread(target=loop, daemon=True).start()
