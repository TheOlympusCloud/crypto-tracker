"""Mixer links: a Tornado Cash deposit by one wallet of mine and a withdrawal to another, from
the same pool (docs/design/multichain.md, 2.3 #5 and 3.5).

A Tornado pool hides which deposit a withdrawal spends among the deposits it could be. The
deposits made between mine and the withdrawal are an upper bound on how well that works: with
n of them, the withdrawal is mine with odds of at most 1 / (1 + n). That is a `mixer` link,
capped at 0.3 so that it never raises an alert on its own (the real crowd is every unspent
earlier deposit, which this does not count). From 20 deposits between, the bound is under 5%
and the pair is hidden in the crowd: no link.

Each receipt of a transaction in which my wallet paid a pool (or Tornado's router) or a pool
paid my wallet is read once, from the indexer that already reads the wallet; the pool's
Deposit and Withdrawal events give the pool and the block. The deposits between two blocks
are counted from the pool's own logs (one page of the indexer's `logs` module) and kept by
block range, since they never change.
"""

import time

import httpx

from . import bridges, chains
from .heuristics import pair

CAP = 0.3  # a bound, never a fact: kept below the alert threshold on its own
CROWD = 20  # deposits between from which the pair is hidden: a bound under 5%, no link
SOURCE = "Mixer pools"  # its row in the failure table, which the verdicts read
DEPOSIT = "0xa945e51eec50ab98c161376f0db4cf2aeba3ec92755fe2fcd388bdbbb80ff196"  # Deposit
WITHDRAWAL = "0xe9e508bad6d4c3227e881ca19068f099da81b5164dd6d62b2eaf1e8bc6c34931"  # Withdrawal

# Tornado Cash's pools: address -> denomination, each checked on chain (denomination(), and
# token() for a token pool). Etherscan's labels misname some of them, so these come from the
# contracts. The same addresses serve other coins on other chains.
_ETH = {
    "0x84443cfd09a48af6ef360c6976c5392ac5023a1f": "0.1",
    "0xd47438c816c9e7f2e2888e060936a499af9582b3": "1",
    "0x330bdfade01ee9bf63c209ee33102dd334618e0a": "10",
    "0x1e34a77868e19a6647b1f2f47b51ed72dede95dd": "100",
}
POOLS = {
    "ethereum": {
        "0x12d66f87a04a9e220743712ce6d9bb1b5616b8fc": "0.1 ETH",
        "0x47ce0c6ed5b0ce3d3a51fdb1c52dc66a7c3c2936": "1 ETH",
        "0x910cbd523d972eb0a6f4cae4618ad62622b39dbf": "10 ETH",
        "0xa160cdab225685da1d56aa342ad8841c3b53f291": "100 ETH",
        "0x178169b423a011fff22b9e3f3abea13414ddd0f1": "0.1 WBTC",
        "0x610b717796ad172b316836ac95a2ffad065ceab4": "1 WBTC",
        "0xbb93e510bbcd0b7beb5a853875f9ec60275cf498": "10 WBTC",
        "0xd4b88df4d29f5cedd6857912842cff3b20c8cfa3": "100 DAI",
        "0xfd8610d20aa15b7b2e3be39b396a1bc3516c7144": "1,000 DAI",
        "0xf60dd140cff0706bae9cd734ac3ae76ad9ebc32a": "1,000 DAI",
        "0x07687e702b410fa43f4cb4af7fa097918ffd2730": "10,000 DAI",
        "0x23773e65ed146a459791799d01336db287f25334": "100,000 DAI",
        "0xd96f2b1c14db8458374d9aca76e26c3d18364307": "100 USDC",
        "0x4736dcf1b7a3d580672cce6e7c65cd5cc9cfba9d": "1,000 USDC",
        "0xd691f27f38b395864ea86cfc7253969b409c362d": "10,000 USDC",
        "0x169ad27a470d064dede56a2d3ff727986b15d52b": "100 USDT",
        "0x0836222f2b2b24a3f36f98668ed8f0b38d1a872f": "1,000 USDT",
        "0xf67721a2d8f736e75a49fdd7fad2e31d8676542a": "10,000 USDT",
        "0x9ad122c22b14202b4490edaf288fdb3c7cb3ff5e": "100,000 USDT",
        "0x22aaa7720ddd5388a3c0a3333430953c68f1849b": "5,000 cDAI",
        "0xba214c1c1928a32bffe790263e38b4af9bfcd659": "50,000 cDAI",
        "0x03893a7c7463ae47d46bc7f091665f1893656003": "50,000 cDAI",
        "0xb1c8094b234dce6e03f10a5b673c1d8c69739a00": "500,000 cDAI",
        "0x2717c5e28cf931547b621a5dddb772ab6a35b701": "500,000 cDAI",
        "0xd21be7248e0197ee08e0c20d4a96debdac3d20af": "5,000,000 cDAI",
        "0xaeaac358560e11f52454d997aaff2c5731b6f8a6": "5,000 cUSDC",
        "0x1356c899d8c9467c7f71c195612f8a395abf2f0a": "50,000 cUSDC",
        "0xa60c772958a3ed56c1f15dd055ba37ac8e523a0d": "500,000 cUSDC",
    },
    "bsc": {a: f"{d} BNB" for a, d in _ETH.items()},
    "arbitrum": {a: f"{d} ETH" for a, d in _ETH.items()},
    "optimism": {a: f"{d} ETH" for a, d in _ETH.items()},
    "polygon": {
        "0x1e34a77868e19a6647b1f2f47b51ed72dede95dd": "100 POL",
        "0xdf231d99ff8b6c6cbf4e9b9a945cbacef9339178": "1,000 POL",
        "0xaf4c0b70b2ea9fb7487c7cbb37ada259579fe040": "10,000 POL",
        "0xa5c2254e4253490c54cef0a4347fddb8f75a4998": "100,000 POL",
    },
    "avalanche": {
        "0x330bdfade01ee9bf63c209ee33102dd334618e0a": "10 AVAX",
        "0x1e34a77868e19a6647b1f2f47b51ed72dede95dd": "100 AVAX",
        "0xaf8d1839c3c67cf571aa74b5c12398d4901147b3": "500 AVAX",
    },
}
# What deposits are sent through, checked as the recipient of real deposits: on Ethereum its
# old proxy (2020-21), proxy (2021-22) and router (since); elsewhere one light proxy.
_LIGHT = {"0x0d5550d52428e7e3175bfc9550207e4ad3859b17"}
ROUTERS = {
    "ethereum": {
        "0x905b63fff465b9ffbf41dea908ceb12478ec7601",
        "0x722122df12d4e14e13ac3b6895a86e84145b6967",
        "0xd90e2f925da726b50c4ed8d0fb90ad053324f31b",
    },
    **{chain: _LIGHT for chain in ("bsc", "arbitrum", "optimism", "polygon", "avalanche")},
}


def _readable(cfgs):
    """The chains whose indexer answers logs: an Etherscan-style one. shortcut: NodeReal (BNB
    Chain's free reader) has no logs module, so its pools are not read; read them through
    eth_getLogs when someone needs it."""
    return {
        c
        for c, cfg in cfgs.items()
        if c in POOLS and cfg.get("url") and not cfg.get("needs_key") and cfg.get("api") is None
    }


def _pending(db, cfgs):
    """(chain, txid) -> (my address, "deposit" or "withdrawal") for the receipts not read yet:
    my transfers into a pool or a router, and a pool's transfers to me."""
    every = sorted({a for c in POOLS for a in {*POOLS[c], *ROUTERS[c]}})
    marks = ",".join("?" * len(every))
    rows = db.execute(
        f"""SELECT chain, txid, src, dst FROM transfer
            WHERE ((src IN (SELECT address FROM node WHERE mine) AND dst IN ({marks}))
                   OR (dst IN (SELECT address FROM node WHERE mine) AND src IN ({marks})))
              AND NOT EXISTS (SELECT 1 FROM receipt r
                              WHERE r.chain = transfer.chain AND r.txid = transfer.txid)""",
        every * 2,
    )
    todo, readable = {}, _readable(cfgs)
    for r in rows:
        if r["chain"] not in readable:
            continue
        if r["dst"] in POOLS[r["chain"]].keys() | ROUTERS[r["chain"]]:
            todo.setdefault((r["chain"], r["txid"]), (r["src"], "deposit"))
        elif r["src"] in POOLS[r["chain"]]:
            todo.setdefault((r["chain"], r["txid"]), (r["dst"], "withdrawal"))
    return todo


def _read(db, client, cfg, chain, txid, me, side):
    """Store my legs in one receipt, then mark the receipt read. A deposit is the pool's
    Deposit event in a transaction my wallet paid; a withdrawal the pool's Withdrawal event
    naming a wallet of mine as the recipient (not a relayer's fee paid to it)."""
    receipt = bridges.fetch(client, cfg, txid)
    for log in receipt["logs"]:
        pool, topics, data = log["address"], log["topics"], log["data"][2:]
        if pool not in POOLS[chain] or not topics:
            continue
        if side == "deposit" and topics[0] == DEPOSIT:
            leg = me
        elif side == "withdrawal" and topics[0] == WITHDRAWAL:
            leg = "0x" + data[24:64]
        else:
            continue
        db.execute(
            "INSERT OR IGNORE INTO mixer_leg SELECT ?, ?, ?, address, ?, ? FROM node "
            "WHERE address = ? AND mine",
            (chain, txid, pool, side, receipt["block"], leg),
        )
    db.execute("INSERT OR IGNORE INTO receipt VALUES (?, ?)", (chain, txid))
    db.commit()


def pairs(db):
    """Each withdrawal to a wallet of mine, with the latest earlier deposit into the same pool
    by each other wallet of mine: the deposits between them in `deposits`, None until
    counted. The latest deposit has the fewest after it, so it gives the strongest bound."""
    legs = db.execute(
        """SELECT l.*, COALESCE(n.wallet, n.address) owner FROM mixer_leg l
           JOIN node n ON n.address = l.address AND n.mine ORDER BY l.block"""
    ).fetchall()
    out = []
    for w in (leg for leg in legs if leg["side"] == "withdrawal"):
        latest = {}
        for d in legs:
            same = (d["chain"], d["pool"]) == (w["chain"], w["pool"])
            if (
                d["side"] == "deposit"
                and same
                and d["owner"] != w["owner"]
                and d["block"] < w["block"]
            ):
                latest[d["address"]] = d  # ordered by block: the last one stays
        for d in latest.values():
            low, high = d["block"] + 1, w["block"]
            row = db.execute(
                "SELECT deposits FROM mixer_scan WHERE chain = ? AND pool = ? AND low = ? "
                "AND high = ?",
                (w["chain"], w["pool"], low, high),
            ).fetchone()
            out.append({
                "chain": w["chain"], "pool": w["pool"], "low": low, "high": high,
                "deposit": d["address"], "withdrawal": w["address"],
                "deposit_tx": d["txid"], "withdrawal_tx": w["txid"],
                "deposits": row and row[0],
            })  # fmt: skip
    return out


def bound(deposits):
    """The link of a pair with `deposits` between; None when the crowd hides it."""
    return None if deposits >= CROWD else min(CAP, round(1 / (1 + deposits), 3))


def count(db, client, cfg, p):
    """The pool's deposits between the pair's blocks, at most a page of CROWD, kept."""
    query = {"module": "logs", "action": "getLogs", "address": p["pool"], "topic0": DEPOSIT}
    query |= {"fromBlock": p["low"], "toBlock": p["high"], "page": 1, "offset": CROWD}
    key = {"apikey": cfg["apikey"]} if cfg.get("apikey") else {}
    data = chains.get(client, cfg["url"], {**cfg.get("params", {}), **key, **query}).json()
    rows = data.get("result")
    if not isinstance(rows, list):  # a list, empty when there were none
        # An error the API words as its result.
        raise RuntimeError(f"getLogs: {data.get('message')} {str(rows)[:80]}")  # noqa: TRY004
    # Each row counts, even one an indexer garbled (Routescan has served an empty `data`).
    db.execute(
        "INSERT OR REPLACE INTO mixer_scan VALUES (?, ?, ?, ?, ?)",
        (p["chain"], p["pool"], p["low"], p["high"], min(len(rows), CROWD)),
    )
    db.commit()


def link(db):
    """Rebuild the `mixer` links from the counted pairs: no call."""
    db.execute("DELETE FROM link WHERE kind = 'mixer'")
    for p in pairs(db):
        if p["deposits"] is None or (confidence := bound(p["deposits"])) is None:
            continue
        name = POOLS[p["chain"]][p["pool"]]
        evidence = f"Tornado Cash {name}: {p['deposits']} deposits between "
        evidence += f"({p['deposit_tx'][:18]} > {p['withdrawal_tx'][:18]})"
        db.execute(
            "INSERT OR REPLACE INTO link VALUES (?, ?, 'mixer', ?, ?)",
            (*pair(p["deposit"], p["withdrawal"]), confidence, evidence),
        )
    db.commit()


def sync(db, client):
    """Read the receipts of my mixer transactions not read yet, count the deposits between
    each new pair, then link. Returns what failed, which the next sync tries again."""
    cfgs = chains.evm_chains()
    down, failed = set(), {}

    def attempt(chain, job, *args):
        if chain in down:
            return  # its indexer is down: the rest waits for the next sync
        try:
            job(db, client, cfgs[chain], *args)
        except (httpx.HTTPError, chains.RateLimited) as e:
            down.add(chain)
            failed[chain] = chains.describe(e)
        except (RuntimeError, ValueError, KeyError, TypeError, IndexError) as e:
            failed.setdefault(chain, chains.describe(e))  # this one only: tried again next sync

    for (chain, txid), (me, side) in _pending(db, cfgs).items():
        attempt(chain, _read, chain, txid, me, side)
    readable = _readable(cfgs)
    for p in pairs(db):
        if p["deposits"] is None and p["chain"] in readable:
            attempt(p["chain"], count, p)
    link(db)
    reason = "; ".join(f"{where}: {why}" for where, why in failed.items())
    if failed:  # stored: until it is read, a link may be missing from every verdict
        db.execute(
            "INSERT OR REPLACE INTO failure VALUES (?, ?, ?)", (SOURCE, reason, int(time.time()))
        )
    else:
        db.execute("DELETE FROM failure WHERE source = ?", (SOURCE,))
    db.commit()
    return [f"{SOURCE}: {reason}"] if failed else []
