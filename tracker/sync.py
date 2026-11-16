"""Crawl addresses into the database, then re-run the cross-chain matcher."""

import threading
import time

import httpx

from . import alerts, chains, hd, heuristics, labels, prices, swaps
from . import db as database

EXPAND_PER_LEVEL = 40  # neighbours synced per depth level, most active first

# `notes` are things that did not stop the sync, e.g. a swap API that was down.
status = {"running": False, "done": 0, "total": 0, "current": "", "error": "", "notes": []}
_lock = threading.Lock()


def sync_address(db, client, address):
    if database.chain_of(address) == "dogecoin":
        # The Dogecoin indexer's hourly quota goes to addresses not read in the last hour.
        fresh = db.execute(
            "SELECT 1 FROM node WHERE address = ? AND synced_at > ? AND error IS NULL",
            (address, time.time() - 3600),
        ).fetchone()
        if fresh:
            return
    try:
        data = chains.fetch(client, address)
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
    if not addresses:
        return []
    marks = ",".join("?" * len(addresses))
    rows = db.execute(
        f"""SELECT other, COUNT(*) n FROM (
              SELECT dst other FROM transfer WHERE src IN ({marks})
              UNION ALL SELECT src FROM transfer WHERE dst IN ({marks}))
            WHERE other NOT IN (SELECT address FROM node WHERE synced_at IS NOT NULL)
              AND other NOT IN (SELECT address FROM label)
            GROUP BY other ORDER BY n DESC""",
        (*addresses, *addresses),
    )
    # Chains that are only read shallowly are not crawled outwards.
    return [r["other"] for r in rows if database.chain_of(r["other"]) in chains.DEEP][
        :EXPAND_PER_LEVEL
    ]


def used(db, address):
    return db.execute(
        "SELECT 1 FROM transfer WHERE src = ?1 OR dst = ?1 LIMIT 1", (address,)
    ).fetchone()


def discover(db, client, wallet):
    """Walk an HD wallet's branches until GAP unused addresses in a row, syncing as it goes.
    Addresses that were never used are forgotten again.

    A lookup that fails says nothing about the address, so it neither counts as unused nor
    is forgotten quietly: after three failures in a row the branch is given up and the
    wallet is marked as not fully scanned, which its verdict and the sync status show.
    """
    found, problem = [], None
    for descriptor in hd.descriptors(wallet["descriptor"]):
        for branch in range(descriptor.num_branches):
            unused = failures = index = 0
            while unused < hd.GAP and failures < 3:
                address = hd.address(descriptor, branch, index)
                status["current"] = address
                sync_address(db, client, address)
                status["done"] += 1
                status["total"] += 1
                error = db.execute(
                    "SELECT error FROM node WHERE address = ?", (address,)
                ).fetchone()
                if used(db, address):
                    unused = failures = 0
                    found.append(address)
                    db.execute(
                        "UPDATE node SET mine = 1, public = ?, name = ?, wallet = ? WHERE address = ?",
                        (wallet["public"], wallet["name"], wallet["id"], address),
                    )
                else:
                    if error and error["error"]:
                        failures += 1
                        problem = error["error"]
                    else:
                        unused, failures = unused + 1, 0
                    db.execute("DELETE FROM node WHERE address = ? AND NOT mine", (address,))
                    db.execute("DELETE FROM balance WHERE address = ?", (address,))
                db.commit()
                index += 1
                if not descriptor.is_wildcard:
                    break
    if problem:
        problem = f"address scan incomplete, some lookups failed ({problem})"
        status["notes"].append(f"{wallet['name'] or wallet['id']}: {problem}")
    db.execute("UPDATE hdwallet SET error = ? WHERE id = ?", (problem, wallet["id"]))
    db.commit()
    return found


def run(addresses=None, depth=0, import_labels=False):
    """Sync `addresses` (all my wallets when None), then `depth` levels of counterparties."""
    db = database.connect()
    try:
        with httpx.Client(timeout=30, headers={"User-Agent": "crypto-tracker"}) as client:
            if import_labels:
                status["current"] = "labels"
                labels.import_open(db, client)
            if addresses is None:
                done = [
                    a
                    for w in db.execute("SELECT * FROM hdwallet").fetchall()
                    for a in discover(db, client, w)
                ]
                rest = [
                    r["address"]
                    for r in db.execute("SELECT address FROM node WHERE mine AND wallet IS NULL")
                ]
                addresses = [*done, *rest]
                frontier = rest
            else:
                done, frontier = [], list(addresses)
            for level in range(depth + 1):
                status["total"] += len(frontier)
                for address in frontier:
                    status["current"] = address
                    sync_address(db, client, address)
                    status["done"] += 1
                if level < depth:
                    frontier = neighbours(db, [*frontier, *done] if level == 0 else frontier)
                    if not frontier:
                        break
            status["current"] = "swaps"
            status["notes"] += swaps.sync(db, client)
            heuristics.match_bridges(db)
            heuristics.match_exchange_flows(db)
            alerts.check(db)
            status["current"] = "prices"
            prices.snapshot(db, client)
    except Exception as e:  # noqa: BLE001 - a background thread must report, not die silently
        status["error"] = str(e)[:200]
    finally:
        db.close()
        status.update(running=False, current="")
        _lock.release()


def start(addresses=None, depth=0, import_labels=False):
    """Run in the background; False when a run is already going."""
    if not _lock.acquire(blocking=False):
        return False
    status.update(running=True, done=0, total=0, current="", error="", notes=[])
    threading.Thread(target=run, args=(addresses, depth, import_labels), daemon=True).start()
    return True


def every(minutes):
    """Sync all my wallets on an interval, for as long as the server runs."""

    def loop():
        while True:
            time.sleep(minutes * 60)
            start()

    threading.Thread(target=loop, daemon=True).start()
