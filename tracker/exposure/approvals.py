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

Each chain is read on its own: the approvals of the chains read in full are listed, and a
chain not read yet (its history still filling in, its indexer down) is named in the grade.
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
ROWS = 1000  # logs per page, Etherscan's most
PAGES = 10  # pages of one window: Etherscan answers at most 10,000 rows of a query
WINDOW = 1_000_000  # blocks asked for at first; grown while the indexer answers
SETTLE = 200  # blocks below the head left for next time: the indexer's logs may trail it
PER_SYNC = 40  # calls a sync makes for approvals; the rest goes on at the next one
PER_ADDRESS = 20  # of them for one address, so that one slow to read never starves the rest
REFRESH = 86400  # seconds: a permit is signed off chain and submitted by others, so no
# transaction of mine announces it
EVIDENCE = 5
ERRORS = (httpx.HTTPError, RuntimeError, ValueError, KeyError, TypeError, IndexError)


class Spent(Exception):
    """The sync's calls for approvals are used up."""


class Turn(Exception):
    """One address's share of the sync's calls is used up."""


class Narrower(Exception):
    """Ask for fewer blocks: the indexer wants a narrower range, or (`crowded`) the range holds
    more of this address's approvals than the indexer pages through."""

    def __init__(self, crowded=False):
        self.crowded = crowded


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
    """The Approval events of `owner` in blocks low to high, page after page; Narrower when the
    indexer asks for a narrower range (Routescan: "Timeout reached, please use a narrower block
    range"), or when more pages would be needed than it serves."""
    query = {"module": "logs", "action": "getLogs", "fromBlock": low, "toBlock": high}
    query |= {"topic0": APPROVAL, "topic1": "0x" + owner[2:].rjust(64, "0")}
    query |= {"topic0_1_opr": "and", "offset": ROWS}
    rows = []
    for page in range(1, PAGES + 1):
        data = _ask(client, cfg, {**query, "page": page}, spend)
        got = data.get("result")
        if not isinstance(got, list):  # a list, empty when there are none
            if "range" in str(data.get("message")).lower():
                raise Narrower
            raise RuntimeError(f"getLogs: {str(data.get('message'))[:80]}")  # noqa: TRY004 - the API words an error as its result
        if rows and got[:1] == rows[:1]:
            raise Narrower(crowded=True)  # an indexer that ignores pages sends the first again
        rows += got
        if len(got) < ROWS:
            return rows
    raise Narrower(crowded=True)


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


def state(db, address, chain):
    """Where the read of `address` on `chain` stands: {"next": the next block to read, or None
    when it held nothing there; "done": read in full once}, or None before any read."""
    row = db.execute("SELECT value FROM meta WHERE key = ?", (f"approvals:{address}:{chain}",))
    row = row.fetchone()
    return json.loads(row[0]) if row else None


def _mark(db, address, chain, next, done):
    db.execute(
        "INSERT OR REPLACE INTO meta VALUES (?, ?)",
        (f"approvals:{address}:{chain}", json.dumps({"next": next, "done": done})),
    )
    db.commit()


def _read(db, client, cfg, chain, address, learnt, spend):
    """Read the address's new Approval events on `chain`, window by window; True once read up
    to the settled head, False while its first transaction there is not known yet. `learnt`
    holds what this sync found out about the chain's indexer: its head, and its windows."""
    known = state(db, address, chain)
    first = None
    if not known or known["next"] is None:
        if not funder.complete(db, address, chain):
            return False  # its history is still filling in: start from its first transaction
        first = db.execute(
            "SELECT MIN(ts) FROM transfer WHERE chain = ? AND ? IN (src, dst)", (chain, address)
        ).fetchone()[0]
        if first is None:
            _mark(db, address, chain, None, True)  # it never held anything there to approve
            return True
    on = learnt.setdefault(chain, {"window": WINDOW})
    if "top" not in on:
        on["top"] = _block(client, cfg, time.time(), spend) - SETTLE
    low = _block(client, cfg, first, spend) if first else known["next"]
    done = bool(known and known["next"] is not None and known["done"])
    size = on["window"]  # this read's: a crowded stretch narrows it for this address only
    while low <= on["top"]:
        high = min(on["top"], low + size - 1)
        try:
            found = _logs(client, cfg, address, low, high, spend)
        except Narrower as e:
            if high == low:
                # shortcut: more than 10,000 approvals of the address in one block stop its
                # read on this chain (it stays "not read yet"); skip the block if that happens.
                raise RuntimeError(f"getLogs: too many approvals in block {low}") from None
            refused, size = high - low + 1, (high - low + 1) // 2
            if not e.crowded:  # the indexer's limit, for every address in this sync
                on["limit"] = min(on.get("limit", refused), refused)
                on["window"] = min(on["window"], size)
            continue
        _keep(db, address, chain, found)
        low = high + 1
        _mark(db, address, chain, low, done)
        # Grow while the indexer answers, below the smallest window it refused in this sync.
        size = min(size * 2, on.get("limit", 4 * size) // 2)
    _mark(db, address, chain, low, True)
    return True


def sync(db, client):
    """Read the approvals of my EVM addresses that are due: not read in full on every chain,
    read more than a day ago, or that sent a transaction since. Returns what did not answer."""
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
    left = [PER_SYNC, PER_ADDRESS]  # the sync's calls, and the current address's

    def spend():
        if left[0] <= 0:
            raise Spent
        if left[1] <= 0:
            raise Turn
        left[0] -= 1
        left[1] -= 1

    learnt, down, failed = {}, set(), {}
    try:
        for address in sorted(due, key=lambda a: at.get(a, {"at": 0})["at"]):
            done, left[1] = True, PER_ADDRESS
            for chain, cfg in cfgs.items():
                if chain in down:
                    done = False  # its indexer did not answer: the rest waits for the next sync
                    continue
                try:
                    done &= _read(db, client, cfg, chain, address, learnt, spend)
                except Turn:
                    done = False  # it goes on from its bookmarks at the next sync
                    break
                except (httpx.HTTPError, chains.RateLimited) as e:
                    down.add(chain)
                    failed[chain] = chains.describe(e)
                    done = False
                except ERRORS as e:  # this address only: the chain goes on for the others
                    failed.setdefault(chain, f"{address}: {chains.describe(e)}")
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
    """(the open unlimited approvals of the chains read in full, the readable chains not read
    in full yet) for an address of mine. A token can emit an Approval naming anyone as owner,
    so only an approval the address signed counts, or one of a token it moved itself there."""
    # shortcut: a permit submitted by a dApp's relayer, for a token the wallet never moved in
    # a transaction of its own, is not listed; drop the condition if such ones go missing.
    read, unread = [], []
    for chain in readable():
        known = state(db, address, chain)
        (read if known and known["done"] else unread).append(chain)
    marks = ",".join("?" * len(read))
    rows = db.execute(
        f"SELECT * FROM approval a WHERE address = ?1 AND chain IN ({marks}) AND (txid IN "
        "(SELECT txid FROM sent WHERE address = ?1) OR token IN (SELECT t.token FROM transfer "
        "t JOIN sent s ON s.address = t.src AND s.txid = t.txid WHERE t.src = ?1 AND "
        "t.chain = a.chain)) ORDER BY chain, rowid",
        (address, *read),
    )
    return rows.fetchall(), unread


def _named(db, address, chain=None):
    """What the tracker calls a contract: a token's symbol (given its `chain`), else its label.
    A verified contract name is its deployer's choice, so the address goes with it."""
    row = db.execute(
        "SELECT COALESCE((SELECT asset FROM transfer WHERE chain = ?2 AND token = ?1 LIMIT 1), "
        "(SELECT name FROM label WHERE address = ?1)), "
        "(SELECT NULLIF(name, '') FROM contract WHERE address = ?1)",
        (address, chain),
    ).fetchone()
    if row[0]:
        return row[0]
    if address == PERMIT2:
        return "Permit2"
    return f"{row[1]} {address[:6]}…{address[-4:]}" if row[1] else address


def check(ctx):
    """The finding of an address of mine, from the chains a sync read in full. A chain not read
    yet is a gap: the grade cannot change for it (impact 0), but no list must read as "none"."""
    mine = ctx.db.execute("SELECT 1 FROM node WHERE address = ? AND mine", (ctx.address,))
    if not mine.fetchone():
        return  # a stranger's are never read
    found, unread = stored(ctx.db, ctx.address)
    if found:
        listed = [
            f"{_named(ctx.db, r['token'], r['chain'])} → {_named(ctx.db, r['spender'])} "
            f"({chains.name(r['chain'])})"
            for r in found
        ]
        evidence = [{"chain": r["chain"], "txid": r["txid"]} for r in found][:EVIDENCE]
        yield "x-open-approvals", {"count": len(found), "approvals": "; ".join(listed)}, evidence
    if unread:
        raise RuntimeError(f"not read yet on {', '.join(map(chains.name, unread))}")
