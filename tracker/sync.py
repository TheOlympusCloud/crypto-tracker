"""Crawl addresses into the database, then re-run the cross-chain matcher."""

import threading
import time

import httpx

from . import (
    alerts,
    bridges,
    busy,
    chains,
    deposits,
    detect,
    graph,
    hd,
    heuristics,
    history,
    labels,
    mixers,
    prices,
    swaps,
    tokens,
)
from . import db as database
from .exposure import names

EXPAND_PER_LEVEL = 40  # neighbours synced per depth level, most active first

# `notes` are things that did not stop the sync, e.g. a swap API that was down. `filling` is
# the address the background fill-in is reading.
status = {
    "running": False, "done": 0, "total": 0, "current": "", "error": "", "notes": [],
    "filling": "",
}  # fmt: skip
_lock = threading.Lock()  # one run at a time: a sync, or a pass of the fill-in
filling = threading.Event()  # set while the fill-in holds _lock
waiting = threading.Lock()  # held by a user's sync waiting for the fill-in to pause
FILL_EVERY = 5  # minutes between passes of the fill-in


def _client():
    return httpx.Client(timeout=30, headers={"User-Agent": "crypto-tracker"})


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
        mine = {r["address"] for r in db.execute("SELECT address FROM node WHERE mine")}
        data = chains.fetch(client, address, mine)
        if address in mine:
            # My own addresses are read in full over time: see history.advance.
            more = history.advance(client, db, address, mine, data.get("count"))
            for k in ("transfers", "links", "coinjoins", "wide"):
                data[k] = data.get(k, []) + more[k]
            data["sent"] = set(data.get("sent", ())) | set(more["sent"])
            data["errors"] = data.get("errors", []) + more["errors"]
            data["hub"] = not history.state(db, address)["complete"]
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
        links = [k for k in data["links"] if ok(k[0]) and ok(k[1])]
        db.executemany(
            "INSERT OR REPLACE INTO link VALUES (?,?,?,?,?)",
            [k for k in links if k[2] not in busy.CHECKED],
        )
        # The transactions it sent, also those that move no value (a claim()): see tokens.judge.
        db.executemany(
            "INSERT OR IGNORE INTO sent VALUES (?, ?)", [(address, t) for t in data.get("sent", ())]
        )
        db.executemany(
            "INSERT OR IGNORE INTO coinjoin VALUES (?, ?)",
            [(database.chain_of(address), txid) for txid in data.get("coinjoins", [])],
        )
        # Wide transactions are kept by id: a page read twice (plain read, then history) or
        # over several syncs counts each of them once.
        db.executemany(
            "INSERT OR IGNORE INTO wide VALUES (?, ?)", [(address, t) for t in data.get("wide", ())]
        )
        db.execute(
            "UPDATE node SET cut = (SELECT COUNT(*) FROM wide WHERE address = ?1) WHERE address = ?1",
            (address,),
        )
        # A token's verdict is kept until tokens.mark judges it again at the end of the sync,
        # so a sync that stops early never leaves a fake token unmarked.
        kept = [
            (r["name"], r["trust"], r["reason"], address, r["chain"], r["token"])
            for r in db.execute(
                "SELECT * FROM balance WHERE address = ? AND trust != ''", (address,)
            )
        ]
        db.execute("DELETE FROM balance WHERE address = ?", (address,))
        db.executemany(
            "INSERT OR REPLACE INTO balance(address, chain, asset, token, amount, name) "
            "VALUES (?,?,?,?,?,?)",
            [(address, *b, data.get("names", {}).get((b[0], b[2]), "")) for b in data["balances"]],
        )
        if not data["hub"]:
            # The whole history is loaded, so token balances follow from it; this covers
            # indexers without a token-balance call. Rebasing tokens will be off.
            db.execute(
                """INSERT OR IGNORE INTO balance(address, chain, asset, token, amount)
                   SELECT ?1, chain, asset, token, SUM(CASE WHEN dst = ?1 THEN amount ELSE -amount END)
                   FROM transfer WHERE token != '' AND ?1 IN (src, dst)
                   GROUP BY chain, token HAVING SUM(CASE WHEN dst = ?1 THEN amount ELSE -amount END) > 1e-9""",
                (address,),
            )
        db.executemany(  # after the balances derived from the history too
            "UPDATE balance SET name = CASE name WHEN '' THEN ? ELSE name END, trust = ?, "
            "reason = ? WHERE address = ? AND chain = ? AND token = ?",
            kept,
        )
        db.execute(
            "UPDATE node SET synced_at = ?, hub = ? WHERE address = ?",
            (int(time.time()), int(data["hub"]), address),
        )
    db.execute("UPDATE node SET error = ? WHERE address = ?", (error or None, address))
    db.commit()
    # Links to a stranger wait for its read (busy.vet), after the commit: a read that fails
    # or is cut short costs a link, never a transfer.
    checked = [k for k in links if k[2] in busy.CHECKED] if data else []
    if checked:
        kept, unread = busy.vet(db, client, checked)
        db.executemany("INSERT OR REPLACE INTO link VALUES (?,?,?,?,?)", kept)
        if unread:
            error = "; ".join([*filter(None, [error]), *unread])
            db.execute("UPDATE node SET error = ? WHERE address = ?", (error, address))
        db.commit()


def neighbours(db, addresses):
    """Unsynced, unlabelled counterparties of `addresses`, most transfers first; a label from
    a name-only set does not count. Services and hubs are never expanded: everybody is two
    hops from Binance."""
    if not addresses:
        return []
    marks = ",".join("?" * len(addresses))
    rows = db.execute(
        f"""SELECT other, COUNT(*) n FROM (
              SELECT dst other FROM transfer WHERE src IN ({marks}) AND NOT {graph.FAKE}
              UNION ALL SELECT src FROM transfer WHERE dst IN ({marks}) AND NOT {graph.FAKE})
            WHERE other NOT IN (SELECT address FROM node WHERE synced_at IS NOT NULL)
              AND other NOT IN (SELECT address FROM label WHERE {labels.STOPS})
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


def run(addresses=None, depth=0, import_labels=False, wallets=()):
    """Sync `addresses` and rescan the HD `wallets` given by id (everything of mine when
    `addresses` is None), then `depth` levels of counterparties."""
    db = database.connect()
    try:
        with _client() as client:
            if import_labels or labels.due(db):
                status["current"] = "labels"
                _, missing = labels.import_open(db, client, everything=import_labels)
                status["notes"] += [f"label set not downloaded: {name}" for name in missing]
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
                marks = ",".join("?" * len(wallets))
                chosen = db.execute(f"SELECT * FROM hdwallet WHERE id IN ({marks})", list(wallets))
                done = [a for w in chosen.fetchall() for a in discover(db, client, w)]
                frontier = list(addresses)
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
            status["current"] = "labels"
            detect.contracts(db, client)
            status["notes"] += names.sync(db, client)
            status["current"] = "swaps"
            status["notes"] += swaps.sync(db, client)
            status["notes"] += bridges.sync(db, client)
            status["notes"] += mixers.sync(db, client)
            deposits.read(db, client)  # after the labels: a named address is not read
            detect.behaviour(db)  # after the swaps, whose vault labels outrank a guess
            heuristics.match_bridges(db)
            heuristics.match_exchange_flows(db)
            deposits.link(db)
            alerts.check(db)
            status["current"] = "prices"
            prices.snapshot(db, client)
            status["current"] = "tokens"
            tokens.mark(db, client)  # with fresh prices: a reliable price spares a token
    except Exception as e:  # noqa: BLE001 - a background thread must report, not die silently
        status["error"] = str(e)[:200]
    finally:
        db.close()
        status.update(running=False, current="")
        _lock.release()


def start(addresses=None, depth=0, import_labels=False, wallets=()):
    """Run in the background; False when a run is already going. The background fill-in
    gives way: the run waits for it to finish the address it is reading, then goes."""
    queued = False
    if not _lock.acquire(blocking=False):
        if not filling.is_set() or not waiting.acquire(blocking=False):
            return False
        queued = True
    current = "waiting" if queued else ""
    status.update(running=True, done=0, total=0, current=current, error="", notes=[])
    args = (addresses, depth, import_labels, wallets)
    threading.Thread(target=_after_fill_in if queued else run, args=args, daemon=True).start()
    return True


def _after_fill_in(*args):
    _lock.acquire()  # the fill-in lets go between two addresses
    waiting.release()
    run(*args)


def fill_in():
    """One pass of the background fill-in: each address of mine not read in full reads one
    more budget (history.advance), and so do unfinished swap records. It stops between
    addresses as soon as a user's sync is waiting; that sync reads everything anyway."""
    if not _lock.acquire(blocking=False):
        return
    filling.set()
    db = database.connect()
    try:
        mine = [r["address"] for r in db.execute("SELECT address FROM node WHERE mine")]
        todo = [a for a in mine if not history.state(db, a)["complete"]]
        swapping = db.execute(
            "SELECT 1 FROM history WHERE source LIKE 'swaps:%' AND NOT complete"
        ).fetchone()
        if not todo and not swapping:
            return
        with _client() as client:
            for address in todo:
                if waiting.locked():
                    return
                status["filling"] = address
                sync_address(db, client, address)
            if waiting.locked():
                return
            if swapping:
                swaps.sync(db, client)
            bridges.sync(db, client)  # bridges paid in older transactions
            mixers.sync(db, client)  # and mixers
            # What follows from the older transfers, as at the end of a sync.
            detect.behaviour(db)
            heuristics.match_bridges(db)
            heuristics.match_exchange_flows(db)
            deposits.link(db)
            alerts.check(db)
            tokens.mark(db, client)  # "arrived unasked" needs the whole history
    except Exception as e:  # noqa: BLE001 - a background thread must report, not die silently
        status["error"] = f"filling in history: {e}"[:200]
    finally:
        db.close()
        status["filling"] = ""
        filling.clear()
        _lock.release()


def every(minutes, job=start):
    """Run `job` (a sync of all my wallets) on an interval, for as long as the server runs."""

    def loop():
        while True:
            time.sleep(minutes * 60)
            job()

    threading.Thread(target=loop, daemon=True).start()
