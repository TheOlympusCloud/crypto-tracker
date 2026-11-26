"""First funder and activator (docs/design/multichain.md, 2.3 #1, 2.4 #3, 2.5 #1): who sent
an address its first coins on each chain, the gas it needed (on Tron, the transfer that
activated it). A Sybil hunter looks there first. A wallet of mine that did is tied to it from
its first transaction; an exchange that did knows whose address it is; a stranger who also
funded another wallet of mine ties the two, unless it is a busy service, which the busy check
tells and stores as a hub.

My own wallets are answered from their stored history once it reaches the first transaction,
without a call. Any other address from its read when that holds the whole history, else with
one ascending call per chain. The transfer itself is already in the graph: the findings name
who made it.
"""

import httpx

from .. import busy, chains, history, lookup
from .. import db as database
from ..labels import CONFIDENCE

SYNC = 5  # calls a sync makes per EVM chain (chains.fetch_evm): the busy check of an EVM stranger
TXLIST = 20  # oldest rows of one ascending txlist read


def check(ctx):
    mine = ctx.db.execute("SELECT 1 FROM node WHERE address = ? AND mine", (ctx.address,))
    mine = bool(mine.fetchone())
    # The read the scan's lookup kept (lookup.fetch), when it holds the whole history.
    _, read = lookup.READS.get((ctx.address, tuple(ctx.view["chains"])), (0, None))
    whole = read is not None and not ctx.view["truncated"] and not ctx.view["errors"]
    funded = {}  # funder -> evidence, one transaction per chain
    failed = None  # one chain that did not answer spares the others, then says so
    for chain in ctx.view["chains"] if ctx.chain == "evm" else [ctx.chain]:
        cfg = chains.evm_chains().get(chain, {})
        if mine and complete(ctx.db, ctx.address, chain):
            rows = ctx.db.execute(
                "SELECT src, txid, ts FROM transfer WHERE dst = ? AND chain = ? AND token = ''",
                (ctx.address, chain),
            ).fetchall()
        elif cfg.get("api") == "nodereal":
            continue  # a call reads about a day there, never back to the first transaction
        elif whole:
            rows = [
                (t[2], t[1], t[7])
                for t in read["transfers"]
                if t[0] == chain and t[3] == ctx.address and not t[5]
            ]
        elif ctx.chain == "solana":
            continue  # no call lists an address from its first transaction
        else:
            try:
                rows = activator(ctx) if chain == "tron" else oldest(ctx, chain, cfg)
            except (httpx.HTTPError, RuntimeError, ValueError, KeyError, TypeError) as e:
                failed = failed or RuntimeError(f"{chain}: {chains.describe(e)}")
                continue
        if rows:
            src, txid, _ = min(rows, key=lambda r: r[2])
            if database.valid(src):  # indexer output is untrusted
                funded.setdefault(src, []).append({"chain": chain, "txid": txid})
    cex = []
    for src, evidence in funded.items():
        if found := name(ctx, src, evidence, mine):
            yield found
            if found[0] == "x-funder-exchange":
                cex.append(found)
    # The withdrawal is one of the transfers exchange exposure counts, unless the read did
    # not hold it (cut to the newest transactions, or a chain that did not answer): then the
    # exposure is the funder's alone, and counts once.
    if cex and not ctx.labelled("cex"):
        evidence = [e for _, _, ev in cex for e in ev]
        params = {
            "exchange": ", ".join(sorted({p["exchange"] for _, p, _ in cex})),
            "count": len(evidence),
            "chains": ", ".join(sorted({chains.name(e["chain"]) for e in evidence})),
        }
        yield "x-exchange-exposure", params, evidence
    if failed:
        raise failed


def complete(db, address, chain):
    """Whether the stored history of `address` on `chain` reaches its first transaction."""
    marks = [
        history.get(db, address, s) for s in history.sources(address) if s.split(":")[0] == chain
    ]
    return bool(marks) and all(m and m["complete"] for m in marks)


def activator(ctx):
    """The transfer that activated a Tron account: the oldest one it received."""
    # shortcut: TRX only, as in the stored history; an account activated by a TRC-10 token or
    # CreateAccount names no activator. Read those contracts too if such accounts matter.
    params = {"only_to": "true", "only_confirmed": "true", "order_by": "block_timestamp,asc"}
    params["limit"] = 1
    first = ctx.get(f"{chains.TRON}/v1/accounts/{ctx.address}/transactions", params).json()
    first = first.get("data", [])
    return [(t[2], t[1], t[7]) for t in chains.tron_transfers(first, [])[0] if t[3] == ctx.address]


def oldest(ctx, chain, cfg):
    """The first coins an EVM address received on `chain`, from its oldest transactions."""
    # shortcut: one call, txlist only, which has no internal transfers. If the first coins
    # came by one (a bridge fill, a payout contract) and a normal transfer reached the
    # address before it sent anything, that later sender is named. Read txlistinternal too,
    # a second call per chain, if such misnamed funders show up.
    ctx._spend()
    rows = chains._evm_call(
        ctx.client, cfg, ctx.address, "txlist", page=1, offset=TXLIST, sort="asc"
    )
    for it in rows:
        if str(it.get("from")).lower() == ctx.address:
            # It paid gas before any coin reached it in this list: its first coins came by an
            # internal transfer (a contract, a bridge), which txlist does not show.
            return []
        if got := chains.evm_transfers(chain, cfg["symbol"], [it]):
            return [(got[0][2], got[0][1], got[0][7])]
    return []


def name(ctx, src, evidence, mine):
    """The finding that names the funder `src` of the scanned address, or None."""
    on = ", ".join(sorted({chains.name(e["chain"]) for e in evidence}))
    node = ctx.db.execute("SELECT mine, name FROM node WHERE address = ?", (src,)).fetchone()
    label = ctx.db.execute("SELECT name, category, source FROM label WHERE address = ?", (src,))
    label = label.fetchone()
    if node and node["mine"]:
        return "x-funder-mine", {"funder": node["name"] or src, "chains": on}, evidence
    # As everywhere in the scan, only a label from a source that states it names an exchange.
    if label and label["category"] == "cex" and label["source"] not in CONFIDENCE:
        exchange = ctx.names([{"label": label["name"]}])
        return "x-funder-exchange", {"exchange": exchange, "chains": on}, evidence
    if not mine:
        return None  # ties two of my wallets only when the scanned one is mine: no read for others
    others = ctx.db.execute(
        "SELECT DISTINCT n.address, n.name FROM transfer t JOIN node n ON n.address = t.dst "
        "WHERE n.mine AND t.src = ? AND t.token = '' AND t.dst != ? ORDER BY n.address",
        (src, ctx.address),
    ).fetchall()
    if not others:
        return None
    # Read it first, once: an unlabelled exchange hot wallet must not pass for one person who
    # funded both. A busy one is stored as a hub, which ends every path through it. Without
    # calls (a wallet's grade) one not read yet is skipped, and the grade says so.
    hub = busy.known(ctx.db, src)
    if hub is None:
        evm = sum(not cfg.get("needs_key") for cfg in chains.evm_chains().values())
        for _ in range(1 if ctx.chain in ("solana", "tron") else SYNC * evm):
            ctx._spend()
        hub = busy.check(ctx.db, ctx.client, src)
    id = "x-funder-hub" if hub else "x-funder-shared"
    wallets = ", ".join(r["name"] or r["address"] for r in others)
    return id, {"funder": src, "wallets": wallets, "chains": on}, evidence
