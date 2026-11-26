"""Shared counterparties (docs/design/multichain.md, 2.2 #3 and 3.5).

An address two or more of my wallets dealt with is read once, even where a sync does not
expand counterparties (depth 0, Tron, Solana): a busy one becomes a hub and ends every path
through it, and a quiet one that sweeps everything to one exchange is that exchange's deposit
address. A deposit address belongs to one exchange account, so two wallets of mine that paid
it are one customer: a `deposit-address` link.
"""

import httpx

from . import busy, detect, graph, sync
from . import db as database
from .heuristics import TRUSTED, pair

DEPOSIT = 0.85  # two of my wallets paid one deposit address
READS = 10  # candidates read per sync, busiest first; the rest wait for the next one
# Read in full on these chains; Solana gets the one-call busy check, and Dogecoin's indexer
# allows too few calls an hour to spend them on strangers.
CHAINS = {"evm", "tron", "bitcoin", "litecoin", "bitcoincash", "solana"}


def candidates(db):
    """Unread, unlabelled addresses that two or more of my wallets dealt with, busiest first.
    A read with an error counts as unread (one EVM chain of six failing leaves synced_at set
    and rules the address out as a deposit address), but waits behind the never-read ones so
    a few that keep failing cannot take every slot."""
    rows = db.execute(
        f"""WITH me AS ({detect.ME})
            SELECT other FROM (
              SELECT dst other, w FROM transfer JOIN me ON me.address = src
              WHERE {TRUSTED} AND NOT {graph.FAKE}
              UNION ALL SELECT src, w FROM transfer JOIN me ON me.address = dst
              WHERE {TRUSTED} AND NOT {graph.FAKE})
            WHERE other NOT IN (
                SELECT address FROM node WHERE (synced_at AND error IS NULL) OR hub OR mine)
              AND other NOT IN (SELECT address FROM label)
              AND other NOT IN (SELECT address FROM quiet)
            GROUP BY other HAVING COUNT(DISTINCT w) >= 2
            ORDER BY other IN (SELECT address FROM node WHERE error IS NOT NULL),
                     COUNT(*) DESC, other"""
    )
    return [r["other"] for r in rows if database.chain_of(r["other"]) in CHAINS][:READS]


def read(db, client):
    """Read this sync's candidates. A Solana one takes one call (busy.check) and is not asked
    again when quiet; the others are synced, which keeps their transfers and sets node.hub."""
    for address in candidates(db):
        sync.status["current"] = address
        if database.chain_of(address) != "solana":
            sync.sync_address(db, client, address)  # a failed read is tried again next sync
            continue
        try:
            busy.check(db, client, address)  # a quiet one is noted in `quiet`, never asked again
        except (httpx.HTTPError, RuntimeError, ValueError, KeyError, TypeError):
            continue  # unread is not quiet: asked again on the next sync


def link(db):
    """Rebuild the `deposit-address` links: one row per deposit address and pair of my wallets
    that paid it, so paying one address again adds nothing and a second address does."""
    db.execute("DELETE FROM link WHERE kind = 'deposit-address'")
    found = detect.deposit_addresses(db)
    payers = {}  # deposit address -> for each wallet of mine that paid it, one address
    for r in db.execute(
        f"""WITH me AS ({detect.ME}) SELECT dst, w, MIN(src) src FROM transfer
            JOIN me ON me.address = src WHERE NOT {graph.FAKE} GROUP BY dst, w"""
    ):
        if r["dst"] in found:
            payers.setdefault(r["dst"], []).append(r["src"])
    db.executemany(
        "INSERT OR REPLACE INTO link VALUES (?, ?, 'deposit-address', ?, ?)",
        [
            (*pair(a, b), DEPOSIT, f"{address} > {found[address]}")
            for address, srcs in payers.items()
            for i, a in enumerate(srcs)
            for b in srcs[i + 1 :]
        ],
    )
    db.commit()
