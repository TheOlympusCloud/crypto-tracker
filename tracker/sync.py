"""Crawl addresses into the database, then re-run the cross-chain matcher."""

import threading
import time

import httpx

from . import chains, heuristics, labels
from . import db as database

EXPAND_PER_LEVEL = 40  # neighbours synced per depth level, most active first

status = {"running": False, "done": 0, "total": 0, "current": "", "error": ""}
_lock = threading.Lock()


def sync_address(db, client, address):
    fetch = chains.fetch_btc if database.family(address) == "btc" else chains.fetch_evm
    try:
        data = fetch(client, address)
        error = "; ".join(data.get("errors", []))
    except (httpx.HTTPError, RuntimeError, ValueError, KeyError, TypeError) as e:
        data, error = None, chains.describe(e)
    db.execute("INSERT OR IGNORE INTO node(address) VALUES (?)", (address,))
    if data:
        # Indexer output is untrusted: an address that is not one would later be crawled.
        ok = database.valid
        db.executemany(
            "INSERT OR IGNORE INTO transfer VALUES (?,?,?,?,?,?,?,?)",
            [t for t in data["transfers"] if ok(t[2]) and ok(t[3])],
        )
        db.executemany(
            "INSERT OR REPLACE INTO link VALUES (?,?,?,?,?)",
            [k for k in data["links"] if ok(k[0]) and ok(k[1])],
        )
        db.execute("DELETE FROM balance WHERE address = ?", (address,))
        db.executemany(
            "INSERT OR REPLACE INTO balance VALUES (?,?,?,?,?)",
            [(address, *b) for b in data["balances"]],
        )
        if not data["hub"]:
            # The whole history is loaded, so token balances follow from it; this covers
            # indexers without a token-balance call. Rebasing tokens will be off.
            db.execute(
                """INSERT OR IGNORE INTO balance
                   SELECT ?1, chain, asset, token, SUM(CASE WHEN dst = ?1 THEN amount ELSE -amount END)
                   FROM transfer WHERE token != '' AND ?1 IN (src, dst)
                   GROUP BY chain, token HAVING SUM(CASE WHEN dst = ?1 THEN amount ELSE -amount END) > 1e-9""",
                (address,),
            )
        db.execute(
            "UPDATE node SET synced_at = ?, hub = ? WHERE address = ?",
            (int(time.time()), int(data["hub"]), address),
        )
    db.execute("UPDATE node SET error = ? WHERE address = ?", (error or None, address))
    db.commit()


def neighbours(db, addresses):
    """Unsynced, unlabelled counterparties of `addresses`, most transfers first.
    Services and hubs are never expanded: everybody is two hops from Binance."""
    marks = ",".join("?" * len(addresses))
    rows = db.execute(
        f"""SELECT other, COUNT(*) n FROM (
              SELECT dst other FROM transfer WHERE src IN ({marks})
              UNION ALL SELECT src FROM transfer WHERE dst IN ({marks}))
            WHERE other NOT IN (SELECT address FROM node WHERE synced_at IS NOT NULL)
              AND other NOT IN (SELECT address FROM label)
            GROUP BY other ORDER BY n DESC LIMIT ?""",
        (*addresses, *addresses, EXPAND_PER_LEVEL),
    )
    return [r["other"] for r in rows]


def run(addresses, depth=0, import_labels=False):
    """Sync `addresses`, then `depth` levels of their counterparties."""
    db = database.connect()
    try:
        with httpx.Client(timeout=30, headers={"User-Agent": "crypto-tracker"}) as client:
            if import_labels:
                status["current"] = "labels"
                labels.import_open(db, client)
            frontier = list(addresses)
            for level in range(depth + 1):
                status["total"] += len(frontier)
                for address in frontier:
                    status["current"] = address
                    sync_address(db, client, address)
                    status["done"] += 1
                if level < depth and frontier:
                    frontier = neighbours(db, frontier)
        heuristics.match_bridges(db)
    except Exception as e:  # noqa: BLE001 - a background thread must report, not die silently
        status["error"] = str(e)[:200]
    finally:
        db.close()
        status.update(running=False, current="")
        _lock.release()


def start(addresses, depth=0, import_labels=False):
    """Run in the background; False when a run is already going."""
    if not _lock.acquire(blocking=False):
        return False
    status.update(running=True, done=0, total=0, current="", error="")
    threading.Thread(target=run, args=(addresses, depth, import_labels), daemon=True).start()
    return True
