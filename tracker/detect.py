"""Name counterparties no dataset knows, from what they are and how they behave.

Both kinds of label rank below every dataset and the user, are marked as such, and are
rebuilt or re-checked on each sync. They are guesses: a path through such an address is
weakened, not cut (graph.trace).
"""

import os
import time

import httpx

from . import chains, graph, labels

ASKED = 15  # contracts looked up at the explorer per sync, busiest first
SENDERS = 3  # a deposit address paid by more is no one customer's (the exchange's gas is one)
ME = "SELECT address, COALESCE(wallet, address) w FROM node WHERE mine"


def deposit_addresses(db):
    """address -> exchange, for each address that behaves like one exchange's deposit address:
    read in full without an error, not a hub, not mine, named by no source but this module,
    every payment it made going to that one exchange, and paid by no more than SENDERS
    senders (two addresses of one wallet of mine are one sender). One that also pays anybody
    else is more likely a wallet nobody registered, perhaps the user's own."""
    exchange = {
        r["address"]: r["name"].rstrip("0123456789 #:")  # "Binance 14" -> "Binance"
        for r in db.execute(
            "SELECT address, name FROM label WHERE category = 'cex' AND source != 'heuristic' "
            "AND address IN (SELECT dst FROM transfer)"
        )
    }
    read = """SELECT address FROM node WHERE synced_at AND NOT hub AND NOT mine
              AND error IS NULL
              AND address NOT IN (SELECT address FROM label WHERE source != 'heuristic')"""
    paid = {}  # address -> the exchanges it paid; None for anybody else
    for r in db.execute(
        f"SELECT src, dst FROM transfer WHERE src IN ({read}) AND NOT {graph.FAKE}"
    ):
        paid.setdefault(r["src"], set()).add(exchange.get(r["dst"]))
    found = {a: next(iter(to)) for a, to in paid.items() if len(to) == 1 and None not in to}
    for r in db.execute(
        f"""WITH me AS ({ME})
            SELECT dst, COUNT(DISTINCT COALESCE(me.w, src)) n FROM transfer
            LEFT JOIN me ON me.address = src WHERE dst IN ({read}) AND NOT {graph.FAKE}
            GROUP BY dst"""
    ):
        if r["n"] > SENDERS:
            found.pop(r["dst"], None)
    return found


def behaviour(db):
    """Heuristic labels from the synced transfers. Returns how many were set."""
    mine = "(SELECT address FROM node WHERE mine)"
    known = "(SELECT address FROM label WHERE source != 'heuristic')"
    rows = [
        (address, f"{exchange} deposit address"[:80], "cex", "exchange deposit address")
        for address, exchange in deposit_addresses(db).items()
    ]
    # In one transaction a wallet of mine pays one asset out and gets another one back: the
    # addresses on the other side are a pool or a router, whatever they are called.
    # shortcut: exactly one unit of a token is taken for an NFT changing hands, where the
    # other side is a person, and is left out (so is a swap of exactly 1.0 of a token);
    # needs token decimals in the transfer rows to be told apart properly.
    for r in db.execute(
        f"""SELECT DISTINCT o.dst paid, i.src payer FROM transfer o
            JOIN transfer i ON i.chain = o.chain AND i.txid = o.txid AND i.dst = o.src
            WHERE o.src IN {mine} AND i.token != o.token
              AND NOT (o.token != '' AND o.amount = 1) AND NOT (i.token != '' AND i.amount = 1)"""
    ):
        for address in (r["paid"], r["payer"]):
            rows.append((address, "Swap contract", "dex", "DEX pool or router"))
    # Read only in part because it has too much history: some kind of service.
    for r in db.execute(f"SELECT address FROM node WHERE hub AND address NOT IN {mine}"):
        rows.append((r["address"], "High-volume address", "service", "service (very active)"))
    db.execute("DELETE FROM label WHERE source = 'heuristic'")
    skip = {
        r[0] for r in db.execute(f"SELECT address FROM node WHERE mine UNION SELECT * FROM {known}")
    }
    found = {
        a: (a, name, category, kind, "heuristic")
        for a, name, category, kind in rows
        if a not in skip
    }
    labels.store(db, list(found.values()))
    return len(found)


def contracts(db, client):
    """Ask the explorer already used for syncing what the busiest unnamed EVM counterparties
    of my wallets are called. Each address is asked once and the answer kept, also "not a
    verified contract". The explorer learns nothing new about my wallets: it served their
    histories, in which these addresses appear. Returns how many got a name."""
    if os.environ.get("CT_LABELS", "on") == "off":
        return 0
    mine = "(SELECT address FROM node WHERE mine)"
    candidates = db.execute(
        f"""SELECT other, chain, COUNT(*) n FROM (
              SELECT dst other, chain FROM transfer WHERE src IN {mine}
              UNION ALL SELECT src, chain FROM transfer WHERE dst IN {mine})
            WHERE other LIKE '0x%' AND other NOT IN {mine}
              AND other NOT IN (SELECT address FROM label WHERE source != 'heuristic')
              AND other NOT IN (SELECT address FROM contract)
            GROUP BY other ORDER BY n DESC LIMIT ?""",
        (ASKED,),
    ).fetchall()
    named = []
    for r in candidates:
        cfg = chains.evm_chains().get(r["chain"], {})
        if not cfg.get("url"):
            continue
        params = {"module": "contract", "action": "getsourcecode", "address": r["other"]}
        if cfg.get("apikey"):
            params["apikey"] = cfg["apikey"]
        try:
            reply = chains.get(client, cfg["url"], {**cfg.get("params", {}), **params}).json()
            name = str((reply.get("result") or [{}])[0].get("ContractName") or "")[:80]
        except (
            httpx.HTTPError,
            RuntimeError,
            ValueError,
            AttributeError,
            IndexError,
            KeyError,
            TypeError,
        ):
            break  # the explorer is not answering this: try again on the next sync
        db.execute(
            "INSERT OR REPLACE INTO contract VALUES (?, ?, ?)", (r["other"], name, int(time.time()))
        )
        if name:
            category, kind = labels.contract(name)
            named.append((r["other"], name, category, kind, "explorer"))
    labels.store(db, named)
    return len(named)
