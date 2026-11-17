"""The full history of my own addresses, filled in across syncs.

Each (address, source) keeps a bookmark: `oldest` is the source's own cursor where reading
backwards resumes, `newest` the position (block height, timestamp or slot) of the newest
transaction read, `gap`/`gap_until` an unfinished read of new transactions (more of them
than one sync's budget), and `complete` whether the address's first transaction was reached.
"""

from . import chains
from . import db as database

LISTS = ("txlist", "txlistinternal", "tokentx")  # an EVM address's three histories


def sources(address):
    """The histories one address has, each read and bookmarked on its own."""
    chain = database.chain_of(address)
    if chain == "evm":
        readable = [c for c, cfg in chains.evm_chains().items() if not cfg.get("needs_key")]
        return [f"{c}:{action}" for c in readable for action in LISTS]
    if chain == "tron":
        return ["tron:native", "tron:trc20"]
    return [chain]


def get(db, address, source):
    row = db.execute(
        "SELECT * FROM history WHERE address = ? AND source = ?", (address, source)
    ).fetchone()
    return dict(row) if row else None


def put(db, address, source, **fields):
    """Set some fields of a bookmark, keeping the others."""
    db.execute("INSERT OR IGNORE INTO history(address, source) VALUES (?, ?)", (address, source))
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        db.execute(
            f"UPDATE history SET {sets} WHERE address = ? AND source = ?",
            (*fields.values(), address, source),
        )


def state(db, address):
    """{"complete", "oldest_ts"} of an address over all its sources. While filling in,
    `oldest_ts` is how far back everything is read: the latest of the unfinished sources."""
    marks = [get(db, address, s) or {"complete": 0, "oldest_ts": None} for s in sources(address)]
    if all(m["complete"] for m in marks):
        return {
            "complete": True,
            "oldest_ts": min((m["oldest_ts"] for m in marks if m["oldest_ts"]), default=None),
        }
    open_ = [m["oldest_ts"] for m in marks if not m["complete"]]
    return {"complete": False, "oldest_ts": None if None in open_ else max(open_)}
