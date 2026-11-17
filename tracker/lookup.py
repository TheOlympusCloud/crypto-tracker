"""A look at any address, mine or not: what it holds and who it deals with.

Read-only: the address is fetched live and nothing about it is stored, so it does not
become a wallet of mine and no verdict or alert can change until it is tracked. Only the
price cache is written. Like a sync, the address is sent to the indexer of its chain.
"""

import os
from collections import defaultdict

from . import chains, prices
from . import db as database
from .graph import combine

LISTED = 200  # counterparties returned, busiest first
DRAWN = 60  # of those, how many are in the graph


def view(db, client, address, evm=()):
    """`address` is canonical (db.norm). `evm` names the EVM chains to read; empty: all."""
    chain = database.chain_of(address)
    if chain == "evm":
        every = chains.evm_chains()
        read = {c: cfg for c, cfg in every.items() if (not evm or c in evm)}
        data = chains.fetch_evm(client, address, read)
        read = [c for c, cfg in read.items() if not cfg.get("needs_key")]
    else:
        data, read = chains.fetch(client, address), [chain]
    # Indexer output is untrusted, as in a sync.
    ok = database.valid
    transfers = [t for t in data["transfers"] if address in (t[2], t[3]) and ok(t[2]) and ok(t[3])]
    held = {(c, token): (asset, amount) for c, asset, token, amount in data["balances"]}
    if not data["hub"]:  # the whole history is loaded, so token balances follow from it
        sums, names = defaultdict(float), {}
        for c, _, _, dst, asset, token, amount, _ in transfers:
            if token:
                sums[(c, token)] += amount if dst == address else -amount
                names[(c, token)] = asset
        for k, v in sums.items():
            if v > 1e-9:
                held.setdefault(k, (names[k], v))

    keys = {prices.key(c, asset, token) for (c, token), (asset, _) in held.items()}
    keys |= {prices.key(t[0], t[4], t[5]) for t in transfers}
    prices.refresh(db, client, keys)
    price, fx = prices.rates(db)
    blind = os.environ.get("CT_PRICES", "on") == "off"

    def vouched(c, asset, token):
        """Anyone can airdrop a token: only the ones the price source knows well count."""
        p = price.get(prices.key(c, asset, token))
        return not token or blind or bool(p and (p["confidence"] or 0) >= prices.CONFIDENT)

    assets = []
    for (c, token), (asset, amount) in held.items():
        if not vouched(c, asset, token):
            continue
        p = price.get(prices.key(c, asset, token))
        if p and token and c == "solana":
            asset = p["symbol"] or asset  # Solana does not name its tokens on chain
        value = amount * p["usd"] * fx if p and fx else None
        assets.append(
            {"chain": c, "asset": asset, "token": token, "amount": amount, "value": value}
        )
    assets.sort(key=lambda a: (-(a["value"] or 0), -a["amount"]))

    transfers = [t for t in transfers if vouched(t[0], t[4], t[5])]
    edges, parties = {}, {}
    for c, txid, src, dst, asset, token, amount, ts in transfers:
        e = edges.setdefault(
            (c, src, dst, token),
            {"chain": c, "src": src, "dst": dst, "asset": asset, "token": token, "count": 0,
             "amount": 0.0, "first": ts, "last": ts, "txid": txid},
        )  # fmt: skip
        other, way = (dst, "sent") if src == address else (src, "received")
        p = parties.setdefault(
            other,
            {"address": other, "count": 0, "first": ts, "last": ts, "sent": {}, "received": {}},
        )
        for row in (e, p):
            row["count"] += 1
            row["first"], row["last"] = min(row["first"], ts), max(row["last"], ts)
        e["amount"] += amount
        p[way][(c, asset)] = p[way].get((c, asset), 0.0) + amount

    guesses = defaultdict(list)  # a guess that the address shares an owner with another
    for a, b, kind, confidence, evidence in data["links"]:
        if address in (a, b) and a != b and ok(a) and ok(b):
            guesses[(a, b, kind)].append((confidence, evidence))
    links = [
        {"a": a, "b": b, "kind": kind, "confidence": combine([c for c, _ in rows]),
         "evidence": [e for _, e in rows[:5]], "count": len(rows)}
        for (a, b, kind), rows in guesses.items()
    ]  # fmt: skip
    for k in links:
        other = k["b"] if k["a"] == address else k["a"]
        p = parties.setdefault(
            other, {"address": other, "count": 0, "first": 0, "last": 0, "sent": {}, "received": {}}
        )
        if k["confidence"] > p.get("guess", {"confidence": 0})["confidence"]:
            p["guess"] = {"kind": k["kind"], "confidence": k["confidence"]}

    # What the database already knows about these addresses: mine or not, and their labels.
    known = {address: {"address": address, "mine": 0, "public": 0, "name": ""}}
    known |= {a: {"address": a, "mine": 0, "public": 0, "name": ""} for a in parties}
    for i in range(0, len(known), 500):
        some = list(known)[i : i + 500]
        marks = ",".join("?" * len(some))
        for r in db.execute(
            f"SELECT address, mine, public, name, wallet FROM node WHERE address IN ({marks})", some
        ):
            known[r["address"]].update(dict(r))
        for r in db.execute(f"SELECT * FROM label WHERE address IN ({marks})", some):
            known[r["address"]].update(label=r["name"], category=r["category"])
    for node in known.values():
        node["chain"] = database.chain_of(node["address"])
        node.update(chains.explorer(node["chain"]))
    me = known[address]
    me["balances"] = [{k: a[k] for k in ("chain", "asset", "amount")} for a in assets]
    if len(read) == 1:
        me.update(chains.explorer(read[0]))  # an EVM address read on one chain opens there

    # My wallets first, then the busiest.
    ranked = sorted(parties.values(), key=lambda p: (-known[p["address"]]["mine"], -p["count"]))
    for p in ranked:
        for way in ("sent", "received"):
            p[way] = [{"chain": c, "asset": a, "amount": v} for (c, a), v in p[way].items()]
        p.update({k: v for k, v in known[p["address"]].items() if k != "chain"})
    drawn = {address} | {p["address"] for p in ranked[:DRAWN]}
    watch = db.execute("SELECT 1 FROM watch WHERE address = ?", (address,)).fetchone()
    values = [a["value"] for a in assets if a["value"] is not None]
    return {
        **{k: v for k, v in me.items() if k != "balances"},
        "chains": read,
        "watched": bool(watch),
        "assets": assets,
        "value": sum(values) if values else None,
        "currency": prices.currency() if fx else "USD",
        "hidden": len(held) - len(assets),  # tokens nobody vouches for
        "connections": ranked[:LISTED],
        "more": max(0, len(ranked) - LISTED),
        "truncated": bool(data["hub"]),
        "errors": data.get("errors", []),
        "notes": chains.notes(chain),
        "graph": {
            "nodes": [known[a] for a in drawn],
            "edges": [e for e in edges.values() if e["src"] in drawn and e["dst"] in drawn],
            "links": [k for k in links if k["a"] in drawn and k["b"] in drawn],
        },
    }
