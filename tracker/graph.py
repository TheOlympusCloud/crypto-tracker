"""Read side: the node graph and the traces between my wallets."""

import heapq
import math
import re
from collections import defaultdict
from itertools import combinations

from .heuristics import SERVICE

HOP_DECAY = 0.7  # each stranger a trace passes through weakens it by this factor

# Unknown tokens anyone can airdrop to two wallets would fake a link between them, so by
# default only native coins and tokens one of my wallets has itself sent are shown.
TRUSTED = "(token = '' OR token IN (SELECT token FROM transfer WHERE src IN (SELECT address FROM node WHERE mine)))"


def combine(confidences):
    """Independent pieces of evidence for the same link: 1 - prod(1 - c)."""
    return round(1 - math.prod(1 - c for c in confidences), 3)


def _links(db):
    by_pair = defaultdict(list)
    for r in db.execute("SELECT * FROM link"):
        by_pair[(r["a"], r["b"], r["kind"])].append(r)
    return [
        {
            "a": a,
            "b": b,
            "kind": kind,
            "confidence": combine(r["confidence"] for r in rows),
            "evidence": [r["evidence"] for r in rows[:5]],
            "count": len(rows),
        }
        for (a, b, kind), rows in by_pair.items()
    ]


def build(db, spam=False):
    where = "1" if spam else TRUSTED
    edges = [
        dict(r)
        for r in db.execute(
            f"""SELECT chain, src, dst, asset, token, COUNT(*) count, SUM(amount) amount,
                       MIN(ts) first, MAX(ts) last, MIN(txid) txid
                FROM transfer WHERE {where} GROUP BY chain, src, dst, token"""
        )
    ]
    links = _links(db)
    nodes = {r["address"]: dict(r) for r in db.execute("SELECT * FROM node")}
    for address in {x for e in edges for x in (e["src"], e["dst"])} | {
        x for k in links for x in (k["a"], k["b"])
    }:
        nodes.setdefault(address, {"address": address, "mine": 0, "public": 0, "name": ""})
    marks = ",".join("?" * len(nodes))
    for r in db.execute(f"SELECT * FROM label WHERE address IN ({marks})", list(nodes)):
        nodes[r["address"]].update(label=r["name"], category=r["category"])
    for r in db.execute(f"SELECT * FROM balance WHERE {where} ORDER BY amount DESC"):
        nodes[r["address"]].setdefault("balances", []).append(
            {"chain": r["chain"], "asset": r["asset"], "amount": r["amount"]}
        )
    return {"nodes": list(nodes.values()), "edges": edges, "links": links}


def _entity(name):
    return re.sub(r"[\s:#]*\d+$", "", name)  # "Binance 14" -> "Binance"


def trace(db, max_hops=4):
    """For every pair of my wallets: the most convincing path linking them on-chain.

    Confidence of a path = product of its edge confidences (a transfer is 1.0, a heuristic
    link its own confidence) x HOP_DECAY per stranger on the way. Paths never run through
    a service or a hub: sharing Binance with someone links nobody. Such shared services are
    reported separately, since the service itself can still connect the two wallets.
    """
    adj = defaultdict(dict)  # a -> b -> (confidence, kind)

    def connect(a, b, conf, kind):
        if a != b and conf > adj[a].get(b, (0,))[0]:
            adj[a][b] = adj[b][a] = (conf, kind)

    for r in db.execute(f"SELECT DISTINCT src, dst FROM transfer WHERE {TRUSTED}"):
        connect(r["src"], r["dst"], 1.0, "transfer")
    for k in _links(db):
        connect(k["a"], k["b"], k["confidence"], k["kind"])

    mine = {r["address"]: r for r in db.execute("SELECT * FROM node WHERE mine")}
    labels = {r["address"]: r for r in db.execute("SELECT * FROM label")}
    blocked = {a for a, r in labels.items() if r["category"] in SERVICE}
    blocked |= {r["address"] for r in db.execute("SELECT address FROM node WHERE hub AND NOT mine")}

    def best_paths(start):
        # Dijkstra over (node, hops) so the hop limit cannot hide a longer, stronger path.
        best, seen, heap = {}, set(), [(0.0, 0, start, (start,), ())]
        while heap:
            cost, hops, node, path, kinds = heapq.heappop(heap)
            if (node, hops) in seen:
                continue
            seen.add((node, hops))
            best.setdefault(node, (math.exp(-cost), path, kinds))
            # Another of my wallets ends the path; pairs chain transitively.
            if (node != start and node in mine) or hops == max_hops or node in blocked:
                continue
            for other, (conf, kind) in adj[node].items():
                if other not in path:
                    step = -math.log(conf) - (0 if node == start else math.log(HOP_DECAY))
                    heapq.heappush(
                        heap, (cost + step, hops + 1, other, (*path, other), (*kinds, kind))
                    )
        return best

    services = {
        a: {_entity(labels[b]["name"]) for b in adj[a] if b in blocked and b in labels}
        for a in mine
    }
    out = []
    for a, b in combinations(sorted(mine), 2):
        found = best_paths(a).get(b)
        shared = sorted(services[a] & services[b])
        if not found and not shared:
            continue
        out.append(
            {
                "a": a,
                "b": b,
                "confidence": round(found[0], 3) if found else None,
                "path": list(found[1]) if found else [],
                "kinds": list(found[2]) if found else [],
                "shared_services": shared,
                # The case the app exists for: a wallet I keep private tied to a public one.
                "exposure": bool(mine[a]["public"]) != bool(mine[b]["public"]),
            }
        )
    return sorted(out, key=lambda t: (not t["exposure"], -(t["confidence"] or 0)))
