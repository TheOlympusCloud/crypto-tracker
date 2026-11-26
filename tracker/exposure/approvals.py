"""Open token approvals (docs/design/multichain.md, 2.3 #7): unlimited ERC-20 allowances that
my EVM wallets gave and never took back, with the spender's label. Security rather than
privacy: a spender that is hacked or turns hostile can take every such token the wallet
holds, now or later. The spenders also list the dApps the wallet uses. It never changes the
score (impact 0).

The `Approval` events that name my address as owner are read from the indexer that already
reads the wallet (its Etherscan-style `logs` module), so nobody new learns anything. The last
event of a token and spender is its allowance: a revocation is an approval of 0, and an
unlimited one stays unlimited when it is spent, since tokens skip the decrement at the
maximum. Events never change, so an address is read once from its first transaction, then
from where the last read stopped, in block windows the indexer can answer (Routescan times
out beyond about a million blocks on Ethereum). Only my wallets are read, by the sync: a
stranger's whole history would take a scan dozens of calls.
"""

import json
import time

import httpx

from .. import chains
from .. import db as database
from . import funder

APPROVAL = "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925"  # its topic0
PERMIT2 = "0x000000000022d473030f116ddee9f6b43ac78ba3"  # Uniswap's, the same on every chain
UNLIMITED = 2**96 - 1  # tokens with 96-bit balances (UNI, COMP) store "unlimited" as this
ROWS = 1000  # logs per answer, Etherscan's most: a full answer may be cut
WINDOW = 1_000_000  # blocks asked for at first; grown while the indexer answers
SETTLE = 200  # blocks below the head left for next time: the indexer's logs may trail it
PER_SYNC = 40  # calls a sync makes for approvals; the rest goes on at the next one
REFRESH = 86400  # seconds: a permit is signed off chain and submitted by others, so no
# transaction of mine announces it
EVIDENCE = 5
ERRORS = (httpx.HTTPError, RuntimeError, ValueError, KeyError, TypeError, IndexError)
windows, limits = {}, {}  # chain -> blocks per window that answered, and that did not


class Spent(Exception):
    """The sync's calls for approvals are used up."""


def readable():
    """The chains whose indexer has a `logs` module."""
    # shortcut: NodeReal (BNB Chain's free reader) has none; read it with eth_getLogs if asked.
    return {
        c: cfg
        for c, cfg in chains.evm_chains().items()
        if cfg.get("url") and not cfg.get("needs_key") and cfg.get("api") != "nodereal"
    }


def _ask(client, cfg, query, spend):
    spend()
    key = {"apikey": cfg["apikey"]} if cfg.get("apikey") else {}
    return chains.get(client, cfg["url"], {**cfg.get("params", {}), **key, **query}).json()


def _block(client, cfg, ts, spend):
    """The last block mined at or before the Unix time `ts`."""
    query = {"module": "block", "action": "getblocknobytime", "timestamp": int(ts)}
    found = _ask(client, cfg, {**query, "closest": "before"}, spend)["result"]
    return int(found["blockNumber"] if isinstance(found, dict) else found)  # Blockscout's: a dict


def _logs(client, cfg, owner, low, high, spend):
    """The Approval events of `owner` in blocks low to high; None when the indexer asks for a
    narrower range, or the answer is full and may be cut."""
    query = {"module": "logs", "action": "getLogs", "fromBlock": low, "toBlock": high}
    query |= {"topic0": APPROVAL, "topic1": "0x" + owner[2:].rjust(64, "0")}
    query |= {"topic0_1_opr": "and", "page": 1, "offset": ROWS}
    data = _ask(client, cfg, query, spend)
    rows = data.get("result")
    if isinstance(rows, list):  # empty when there are none
        return rows if len(rows) < ROWS else None
    if (
        "range" in str(data.get("message")).lower()
    ):  # "Timeout reached, please use a narrower block range"
        return None
    raise RuntimeError(f"getLogs: {str(data.get('message'))[:80]}")


def _keep(db, owner, chain, rows):
    """Apply Approval events, oldest first: each token and spender keeps its last."""

    def number(v):  # hex from Etherscan and Routescan, which sends 0 as "0x"; maybe decimal
        return int(v[2:] or "0", 16) if str(v).startswith("0x") else int(v or 0)

    for log in sorted(rows, key=lambda r: (number(r["blockNumber"]), number(r["logIndex"]))):
        topics, value = log["topics"], log["data"]
        if len(topics) != 3 or len(value) != 66:
            continue  # an ERC-721 approval: its token id is a fourth topic
        token, spender = log["address"].lower(), "0x" + topics[2][-40:].lower()
        if not (database.valid(token) and database.valid(spender)):
            continue  # indexer output is untrusted
        db.execute(
            "DELETE FROM approval WHERE address = ? AND chain = ? AND token = ? AND spender = ?",
            (owner, chain, token, spender),
        )
        if int(value, 16) >= UNLIMITED:
            db.execute(
                "INSERT INTO approval VALUES (?, ?, ?, ?, ?)",
                (owner, chain, token, spender, log["transactionHash"]),
            )


def _read(db, client, cfg, chain, address, heads, spend):
    """Read the address's new Approval events on `chain`, window by window; True once read up
    to the settled head, False while its first transaction there is not known yet."""
    key = f"approvals:{address}:{chain}"
    mark = db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    if not mark:
        if not funder.complete(db, address, chain):
            return False  # its history is still filling in: start from its first transaction
        first = db.execute(
            "SELECT MIN(ts) FROM transfer WHERE chain = ? AND ? IN (src, dst)", (chain, address)
        ).fetchone()[0]
        if first is None:
            return True  # it never held anything there to approve
    if chain not in heads:
        heads[chain] = _block(client, cfg, time.time(), spend) - SETTLE
    top = heads[chain]
    low = int(mark[0]) if mark else _block(client, cfg, first, spend)
    while low <= top:
        size = windows.get(chain, WINDOW)
        high = min(top, low + size - 1)
        found = _logs(client, cfg, address, low, high, spend)
        if found is None:
            if high == low:
                raise RuntimeError("getLogs: too many approvals in one block")
            refused = high - low + 1
            limits[chain] = min(limits.get(chain, refused), refused)
            windows[chain] = refused // 2
            continue
        _keep(db, address, chain, found)
        low = high + 1
        db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, str(low)))
        db.commit()
        # Grow while the indexer answers, below the smallest window it refused.
        windows[chain] = min(size * 2, limits[chain] // 2) if chain in limits else size * 2
    db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, str(low)))
    return True


def sync(db, client):
    """Read the approvals of my EVM addresses that are due: never read in full, read more than
    a day ago, or that sent a transaction since. Returns what did not answer."""
    cfgs = readable()
    at = {
        r["key"].removeprefix("approvals_at:"): json.loads(r["value"])
        for r in db.execute("SELECT key, value FROM meta WHERE key LIKE 'approvals_at:%'")
    }
    sent = dict(db.execute("SELECT address, COUNT(*) FROM sent GROUP BY address").fetchall())
    mine = [r[0] for r in db.execute("SELECT address FROM node WHERE mine AND address LIKE '0x%'")]
    due = [
        a
        for a in mine
        if a not in at or time.time() - at[a]["at"] > REFRESH or at[a]["sent"] != sent.get(a, 0)
    ]
    left = [PER_SYNC]

    def spend():
        if left[0] <= 0:
            raise Spent
        left[0] -= 1

    heads, failed = {}, {}
    try:
        for address in sorted(due, key=lambda a: at.get(a, {"at": 0})["at"]):
            done = True
            for chain, cfg in cfgs.items():
                if chain in failed:
                    done = False  # its indexer did not answer: the rest waits for the next sync
                    continue
                try:
                    done &= _read(db, client, cfg, chain, address, heads, spend)
                except ERRORS as e:
                    failed[chain] = chains.describe(e)
                    done = False
            if done:
                seen = {"at": time.time(), "sent": sent.get(address, 0)}
                db.execute(
                    "INSERT OR REPLACE INTO meta VALUES (?, ?)",
                    (f"approvals_at:{address}", json.dumps(seen)),
                )
            db.commit()
    except Spent:
        db.commit()
    return [f"approvals: {chain}: {why}" for chain, why in failed.items()]


def stored(db, address):
    """The open unlimited approvals a sync keeps for an address of mine; None before its first
    read in full. A token can emit an Approval naming anyone as owner, so only an approval the
    address signed counts, or one of a token it moved itself."""
    # shortcut: a permit submitted by a dApp's relayer, for a token the wallet never moved in
    # a transaction of its own, is not listed; drop the condition if such ones go missing.
    if not db.execute("SELECT 1 FROM meta WHERE key = ?", (f"approvals_at:{address}",)).fetchone():
        return None
    return db.execute(
        "SELECT * FROM approval a WHERE address = ?1 AND (txid IN (SELECT txid FROM sent "
        "WHERE address = ?1) OR token IN (SELECT t.token FROM transfer t JOIN sent s ON "
        "s.address = t.src AND s.txid = t.txid WHERE t.src = ?1)) ORDER BY chain, rowid",
        (address,),
    ).fetchall()


def _named(db, address, chain=None):
    """What the tracker calls a contract: a token's symbol (given its `chain`), else its label,
    else its verified name."""
    row = db.execute(
        "SELECT COALESCE((SELECT asset FROM transfer WHERE chain = ?2 AND token = ?1 LIMIT 1), "
        "(SELECT name FROM label WHERE address = ?1), "
        "(SELECT NULLIF(name, '') FROM contract WHERE address = ?1))",
        (address, chain),
    ).fetchone()[0]
    return row or ("Permit2" if address == PERMIT2 else address)


def check(ctx):
    """The finding of an address of mine, from its last read by a sync. One never read in full
    yet has none, and its grade is not partial for it: it cannot change the score."""
    found = stored(ctx.db, ctx.address) or []
    if found:
        listed = [
            f"{_named(ctx.db, r['token'], r['chain'])} → {_named(ctx.db, r['spender'])} "
            f"({chains.name(r['chain'])})"
            for r in found
        ]
        evidence = [{"chain": r["chain"], "txid": r["txid"]} for r in found][:EVIDENCE]
        yield "x-open-approvals", {"count": len(found), "approvals": "; ".join(listed)}, evidence
