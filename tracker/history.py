"""The full history of my own addresses, filled in across syncs.

Each (address, source) keeps a bookmark: `oldest` is the source's own cursor where reading
backwards resumes, `newest` the position (block height, timestamp or slot) of the newest
transaction read, `gap`/`gap_until` an unfinished read of new transactions (more of them
than one sync's budget), and `complete` whether the address's first transaction was reached.
"""

from . import chains
from . import db as database

# An EVM address's histories. The NFT lists are read for my addresses only, never in a snapshot.
LISTS = ("txlist", "txlistinternal", "tokentx", *chains.NFT_LISTS)


def sources(address):
    """The histories one address has, each read and bookmarked on its own."""
    chain = database.chain_of(address)
    if chain == "evm":
        return [
            f"{c}:{action}"
            for c, cfg in chains.evm_chains().items()
            if not cfg.get("needs_key")
            # NodeReal reads the three lists at once (chains.page_nodereal)
            for action in (("transfers",) if cfg.get("api") == "nodereal" else LISTS)
        ]
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
    if all(m["complete"] and not m.get("gap") for m in marks):
        return {
            "complete": True,
            "oldest_ts": min((m["oldest_ts"] for m in marks if m["oldest_ts"]), default=None),
        }
    # An open gap is a hole among recent transactions: no date says how far back that is read.
    open_ = [
        None if m["complete"] else m["oldest_ts"] for m in marks if not m["complete"] or m["gap"]
    ]
    return {"complete": False, "oldest_ts": None if None in open_ else max(open_)}


# Pages per source per sync. Solana: 25 transactions each. BNB Chain: a window of 100,000
# blocks (about a day), two paced queries, about 4 s.
BUDGET = {"default": 40, "solana": 8, "bsc": 20}


def _reader(client, source, address):
    """A function cursor -> chains.Page for one source of an address."""
    chain, _, action = source.partition(":")
    if chain in chains.UTXO:
        return lambda cursor, stop: chains.page_utxo(client, chain, address, cursor)
    if action in LISTS:
        cfg = chains.evm_chains()[chain]
        return lambda cursor, stop: chains.page_evm(client, cfg, address, action, cursor)
    if action == "transfers":
        cfg = chains.evm_chains()[chain]
        return lambda cursor, stop: chains.page_nodereal(client, cfg, address, cursor)
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
        return {
            **nothing,
            "transfers": chains.tron_transfers(native, tokens)[0],
            "links": chains.tron_links(native),
        }
    if chain == "solana":
        rows = [r for sig, tx in items if tx for r in chains.solana_transfers(address, sig, tx)]
        links = [k for sig, tx in items if tx for k in chains.solana_links(address, sig, tx)]
        return {**nothing, "transfers": rows, "links": links}
    if action == "transfers":
        lists = chains.nodereal_lists(chain, chains.evm_chains()[chain], address, items)
        return {**nothing, **lists}
    if action in LISTS:
        symbol = chains.evm_chains()[chain]["symbol"]
        mine = [it for it in items if action == "txlist" and str(it.get("from")).lower() == address]
        if action in chains.NFT_LISTS:
            rows = chains.nft_transfers(chain, items)
        else:
            rows = chains.evm_transfers(chain, symbol, items, action == "tokentx")
        return {
            "transfers": rows,
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
    end, pages, error).

    A position of 0 or less is a pending transaction (Haskoin says 0, BlockCypher -1), listed
    first: it has no place in the history yet, so it is skipped; the plain read stores it."""
    items, where, used, error = [], [], 0, None
    while used < budget:
        try:
            page = read(cursor, stop)
        except (chains.httpx.HTTPError, RuntimeError, ValueError, KeyError, TypeError) as e:
            error = chains.describe(e)
            break
        used += 1
        for item, p in zip(page.items, page.positions):
            if p > 0 and (stop is None or p > stop):
                items.append(item)
                where.append(p)
        low, high = page.span or (None, None)
        if high is not None and (stop is None or high > stop):
            where.append(high)  # read up to there, rows or not: the next read stops there
        cursor = page.next
        if stop is not None and (
            any(0 < p <= stop for p in page.positions) or (low is not None and low <= stop)
        ):
            return items, where, cursor, True, False, used, None
        if page.last:
            return items, where, cursor, False, True, used, None
    return items, where, cursor, False, False, used, error


def advance(client, db, address, keep=(), total=None):
    """Read more of one of my address's histories: what is new since the last sync, an
    unfinished read of new transactions, then further back, within each source's budget.
    Returns what a sync stores, as chains.fetch does, and the errors met."""
    out = {"transfers": [], "links": [], "coinjoins": [], "wide": [], "sent": [], "errors": []}
    marks = []  # written at the end: a failure half-way moves no bookmark past unstored rows
    for source in sources(address):
        mark = get(db, address, source)
        budget = BUDGET.get(source.split(":")[0], BUDGET["default"])
        read = _reader(client, source, address)
        got, fields, error = [], {}, None
        if mark is None:  # a first read: from the newest, which also starts the way back
            new, at, nxt, _, end, used, error = walk(read, None, None, budget)
            budget -= used
            got += new
            fields |= {"oldest": nxt, "newest": max(at, default=None), "complete": int(end)}
            fields["seen"] = len(new)
        else:
            fields["seen"] = mark["seen"]
            gap = mark["gap"]
            # 1. An unfinished read of new transactions first, so that a later burst of new
            # ones can never starve it.
            if gap:
                more, _, nxt, reached, end, used, error = walk(read, gap, mark["gap_until"], budget)
                budget -= used
                got += more
                fields["seen"] += len(more)
                gap = None if reached or end else nxt
                fields |= {"gap": gap} if gap else {"gap": None, "gap_until": None}
            # 2. New since the newest read, with what the budget has left.
            if not gap and budget > 0 and not error:
                new, at, nxt, reached, end, used, error = walk(read, None, mark["newest"], budget)
                budget -= used
                got += new
                fields["seen"] += len(new)
                top = max(at, default=None)
                if reached or end:
                    fields["newest"] = max(top or 0, mark["newest"] or 0)
                elif not error and top is not None:  # more new than the budget: a gap to close
                    fields |= {"gap": nxt, "gap_until": mark["newest"], "newest": top}
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
        marks.append((source, fields))
        for k in ("transfers", "links", "coinjoins", "wide", "sent"):
            out[k] += part[k]
        if error:
            out["errors"].append(f"{source}: {error}")
    for source, fields in marks:
        put(db, address, source, **fields)
    return out
