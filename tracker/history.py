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


BUDGET = {"default": 40, "solana": 8}  # pages per source per sync (Solana: 25 transactions each)


def _reader(client, source, address):
    """A function cursor -> chains.Page for one source of an address."""
    chain, _, action = source.partition(":")
    if chain in chains.UTXO:
        return lambda cursor, stop: chains.page_utxo(client, chain, address, cursor)
    if action in LISTS:
        cfg = chains.evm_chains()[chain]
        return lambda cursor, stop: chains.page_evm(client, cfg, address, action, cursor)
    if chain == "tron":
        return lambda cursor, stop: chains.page_tron(client, address, action, cursor)
    if chain == "solana":
        return lambda cursor, stop: chains.page_solana(client, address, cursor, stop)
    raise KeyError(source)


def _convert(source, address, items, keep):
    """What a sync stores from raw items: transfers, links, CoinJoins and wide transactions."""
    chain, _, action = source.partition(":")
    nothing = {"links": [], "coinjoins": [], "wide": [], "sent": []}
    if chain == "tron":
        native, tokens = (items, []) if action == "native" else ([], items)
        return {"transfers": chains.tron_transfers(native, tokens)[0], **nothing}
    if chain == "solana":
        rows = [r for sig, tx in items if tx for r in chains.solana_transfers(address, sig, tx)]
        return {"transfers": rows, **nothing}
    if action in LISTS:
        symbol = chains.evm_chains()[chain]["symbol"]
        mine = [it for it in items if action == "txlist" and str(it.get("from")).lower() == address]
        return {
            "transfers": chains.evm_transfers(chain, symbol, items, action == "tokentx"),
            "links": [], "coinjoins": [], "wide": [],
            "sent": [it.get("hash") for it in mine],  # claims too: see tokens.judge
        }  # fmt: skip
    asset = chains.UTXO[chain][0]
    return {
        "sent": [],
        "transfers": [
            r for tx in items for r in chains.utxo_transfers(address, tx, chain, asset, keep=keep)
        ],
        "links": [k for tx in items for k in chains.btc_links(address, tx)],
        "coinjoins": [tx["txid"] for tx in items if chains.looks_like_coinjoin(tx)],
        "wide": [tx["txid"] for tx in items if chains.wide(address, tx)],
    }


def walk(read, cursor, stop, budget):
    """Read pages from `cursor` back until one reaches position `stop` (None: never), the
    last page, or `budget` pages. A page that fails ends the walk: what came before stays.
    Returns (items newer than `stop`, their positions, next cursor, reached `stop`, at the
    end, pages, error)."""
    items, where, used, error = [], [], 0, None
    while used < budget:
        try:
            page = read(cursor, stop)
        except (chains.httpx.HTTPError, RuntimeError, ValueError, KeyError, TypeError) as e:
            error = chains.describe(e)
            break
        used += 1
        for item, p in zip(page.items, page.positions):
            if stop is None or p > stop:
                items.append(item)
                where.append(p)
        cursor = page.next
        if stop is not None and any(p <= stop for p in page.positions):
            return items, where, cursor, True, False, used, None
        if page.last:
            return items, where, cursor, False, True, used, None
    return items, where, cursor, False, False, used, error


def advance(client, db, address, keep=(), total=None):
    """Read more of one of my address's histories: what is new since the last sync, an
    unfinished read of new transactions, then further back, within each source's budget.
    Returns what a sync stores, as chains.fetch does, and the errors met."""
    out = {"transfers": [], "links": [], "coinjoins": [], "wide": [], "sent": [], "errors": []}
    for source in sources(address):
        mark = get(db, address, source)
        budget = BUDGET.get(source.split(":")[0], BUDGET["default"])
        read = _reader(client, source, address)
        got, fields = [], {}
        # 1. New since the newest read; on a first read, this is also the start of the way back.
        new, at, nxt, reached, end, used, error = walk(read, None, mark and mark["newest"], budget)
        budget -= used
        got += new
        top = max(at, default=None)
        if mark is None:
            fields |= {"oldest": nxt, "newest": top, "complete": int(end), "seen": len(new)}
        else:
            fields["seen"] = mark["seen"] + len(new)
            if reached or end:
                fields["newest"] = max(top or 0, mark["newest"] or 0)
            elif not mark["gap"] and not error:  # more new than one budget: a gap to close
                fields |= {"gap": nxt, "gap_until": mark["newest"], "newest": top}
            # 2. An unfinished read of new transactions, from where it stopped.
            if mark["gap"] and (reached or end) and budget > 0 and not error:
                more, _, nxt2, reached2, end2, used, error = walk(
                    read, mark["gap"], mark["gap_until"], budget
                )
                budget -= used
                got += more
                fields["seen"] += len(more)
                fields |= {"gap": None, "gap_until": None} if reached2 or end2 else {"gap": nxt2}
            # 3. Further back, from the oldest point read.
            if not mark["complete"] and budget > 0 and not error:
                old, _, nxt3, _, end3, used, error = walk(read, mark["oldest"], None, budget)
                got += old
                fields["seen"] += len(old)
                fields |= {"oldest": nxt3, "complete": int(end3)}
        if fields.get("complete") and total is not None and fields["seen"] < total:
            fields["complete"] = 0  # a short page, but fewer than the indexer counts: not the end
        part = _convert(source, address, got, keep)
        times = [t[7] for t in part["transfers"] if t[7]]
        if times:
            fields["oldest_ts"] = min(
                times + ([mark["oldest_ts"]] if mark and mark["oldest_ts"] else [])
            )
        put(db, address, source, **fields)
        for k in ("transfers", "links", "coinjoins", "wide", "sent"):
            out[k] += part[k]
        if error:
            out["errors"].append(f"{source}: {error}")
    return out
