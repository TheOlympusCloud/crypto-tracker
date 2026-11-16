"""Read side: the node graph, the links between my wallets and where their funds came from."""

import heapq
import math
import re
from collections import defaultdict
from itertools import combinations

from .heuristics import SERVICE, TRUSTED

HOP_DECAY = 0.7  # each stranger a trace passes through weakens it by this factor


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


def _labels(db):
    # Only addresses that occur in the graph: the label table holds half a million rows.
    return {
        r["address"]: r
        for r in db.execute(
            "SELECT * FROM label WHERE address IN "
            "(SELECT src FROM transfer UNION SELECT dst FROM transfer "
            "UNION SELECT a FROM link UNION SELECT b FROM link)"
        )
    }


def _wallets(db):
    """address -> wallet id for every address of mine. An HD wallet is one wallet however
    many addresses it has; a single address is its own wallet."""
    return {
        r["address"]: r["wallet"] or r["address"]
        for r in db.execute("SELECT address, wallet FROM node WHERE mine")
    }


def _entity(name):
    return re.sub(r"[\s:#]+\d+$", "", name)  # "Binance 14" -> "Binance"


def trace(db, max_hops=4):
    """For every pair of my wallets: the most convincing path linking them on-chain.

    Confidence of a path = product of its edge confidences (a transfer is 1.0, a heuristic
    link its own confidence) x HOP_DECAY per stranger on the way. Paths never run through
    a service or a hub: sharing Binance with someone links nobody. Such shared services are
    reported separately, since the service itself can still connect the two wallets.
    A path ends at the first of my wallets it reaches; `verdicts` chains the pairs.
    """
    adj = defaultdict(dict)  # a -> b -> (confidence, kind)

    def connect(a, b, conf, kind):
        if a != b and conf > adj[a].get(b, (0,))[0]:
            adj[a][b] = adj[b][a] = (conf, kind)

    for r in db.execute(f"SELECT DISTINCT src, dst FROM transfer WHERE {TRUSTED}"):
        connect(r["src"], r["dst"], 1.0, "transfer")
    for k in _links(db):
        connect(k["a"], k["b"], k["confidence"], k["kind"])

    mine = _wallets(db)  # address -> wallet it belongs to
    labels = _labels(db)
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
    paths = {a: best_paths(a) for a in mine}
    out = []
    for a, b in combinations(sorted(mine), 2):
        if mine[a] == mine[b]:
            continue  # two addresses of one HD wallet are known to belong together
        found = paths[a].get(b)
        shared = sorted(services[a] & services[b])
        if found or shared:
            out.append(
                {
                    "a": a,
                    "b": b,
                    "confidence": round(found[0], 3) if found else None,
                    "path": list(found[1]) if found else [],
                    "kinds": list(found[2]) if found else [],
                    "shared_services": shared,
                }
            )
    return out


def verdicts(db, max_hops=4):
    """Per wallet: every other wallet of mine it can be tied to, directly or through a chain
    of my own wallets (public A - private B - private C exposes C), plus what the answer
    rests on, so that "nothing found" is never read as "safe".

    `address` is the wallet's first address and identifies it in `links` and `via`;
    `wallet` is its id and `addresses` all its addresses (several for an HD wallet).
    """
    mine = {r["address"]: r for r in db.execute("SELECT * FROM node WHERE mine")}
    wallet = _wallets(db)
    members = defaultdict(list)
    for a in sorted(mine):
        members[wallet[a]].append(a)
    first = {w: addresses[0] for w, addresses in members.items()}
    labels = _labels(db)
    synced = {r["address"] for r in db.execute("SELECT address FROM node WHERE synced_at")}
    direct, shared = defaultdict(dict), defaultdict(dict)
    for p in trace(db, max_hops):
        a, b = wallet[p["a"]], wallet[p["b"]]
        if p["path"] and p["confidence"] > direct[a].get(b, (0,))[0]:
            direct[a][b] = (p["confidence"], p["path"], p["kinds"])
            direct[b][a] = (p["confidence"], p["path"][::-1], p["kinds"][::-1])
        for name in p["shared_services"]:
            shared[a].setdefault(b, set()).add(name)
            shared[b].setdefault(a, set()).add(name)

    out = []
    for w in sorted(members, key=first.get):
        # Strongest chain of pair links from w to each other wallet (product of confidences).
        best, done, heap = {w: (1.0, [], [], [w])}, set(), [(0.0, w)]
        while heap:
            _, u = heapq.heappop(heap)
            if u in done:
                continue
            done.add(u)
            for v, (conf, path, kinds) in direct[u].items():
                total = best[u][0] * conf
                if v not in done and total > best.get(v, (0,))[0]:
                    # Inside one of my HD wallets the path may jump between its addresses.
                    joined = best[u][1] + [a for a in path if a not in best[u][1][-1:]]
                    best[v] = (total, joined, best[u][2] + kinds, best[u][3] + [v])
                    heapq.heappush(heap, (-math.log(total), v))
        links = [
            {
                "wallet": first[v],
                "wallet_id": v,
                "public": bool(mine[first[v]]["public"]),
                "confidence": round(conf, 3),
                "path": path,
                "kinds": kinds,
                "via": [first[x] for x in chain[1:-1]],  # my own wallets the link passes through
            }
            for v, (conf, path, kinds, chain) in best.items()
            if v != w
        ]
        marks = ",".join("?" * len(members[w]))
        others = {
            r[0]
            for r in db.execute(
                f"SELECT dst FROM transfer WHERE src IN ({marks}) AND {TRUSTED} "
                f"UNION SELECT src FROM transfer WHERE dst IN ({marks}) AND {TRUSTED}",
                members[w] * 2,
            )
        }
        services = {
            (_entity(labels[a]["name"]), labels[a]["category"])
            for a in others
            if a in labels and labels[a]["category"] in SERVICE
        }
        strangers = {a for a in others if a not in labels and a not in mine}
        rows = [mine[a] for a in members[w]]
        out.append(
            {
                "address": first[w],
                "wallet": w,
                "addresses": members[w],
                "name": rows[0]["name"],
                "public": bool(rows[0]["public"]),
                "links": sorted(links, key=lambda k: -k["confidence"]),
                "services": [{"name": n, "category": c} for n, c in sorted(services)],
                "shared_services": [
                    {"wallet": first[v], "services": sorted(names)}
                    for v, names in sorted(shared[w].items())
                ],
                "coverage": {
                    "synced": all(a in synced for a in members[w]),
                    "truncated": any(r["hub"] for r in rows),
                    "error": "; ".join(r["error"] for r in rows if r["error"]) or None,
                    "counterparties": len(strangers),
                    "expanded": len(strangers & synced),
                },
            }
        )
    return out


def origins(db, max_hops=4):
    """Per wallet and asset: where the money received came from, followed upstream to a
    labelled source, another of my wallets, or the edge of what is known. Keyed by the
    wallet's first address; moves between addresses of one HD wallet are not a source.

    An address that forwards money passes on its own sources pro rata to what it received
    of that asset. This ignores timing, so it is an estimate of the mix, not a coin trace.
    """
    inbound = defaultdict(dict)  # (address, chain, token) -> src -> amount
    symbols = {}
    for r in db.execute(
        f"""SELECT chain, src, dst, asset, token, SUM(amount) amount FROM transfer
            WHERE {TRUSTED} GROUP BY chain, src, dst, token"""
    ):
        inbound[(r["dst"], r["chain"], r["token"])][r["src"]] = r["amount"]
        symbols[(r["chain"], r["token"])] = r["asset"]
    nodes = {r["address"]: r for r in db.execute("SELECT * FROM node")}
    labels = _labels(db)
    wallet = _wallets(db)
    members = defaultdict(list)
    for a in sorted(wallet):
        members[wallet[a]].append(a)

    def kind(address, hops, chain, token):
        """Why following stops at `address`, or None to keep going upstream."""
        node = nodes.get(address)
        if node and node["mine"]:
            return "own"
        if address in labels:
            return labels[address]["category"]
        if not node or not node["synced_at"]:
            return "unknown:not expanded"
        if node["hub"]:
            return "unknown:very active address"
        if hops >= max_hops:
            return "unknown:hop limit"
        if not inbound.get((address, chain, token)):
            return "unknown:no earlier transfer of this asset"
        return None

    memo = {}

    def upstream(address, chain, token, hops):
        """terminal address -> (share of the money, kind, path to it)."""
        key = (address, chain, token, hops)
        if key in memo:  # without this the walk repeats shared ancestors exponentially
            return memo[key]
        srcs = inbound[(address, chain, token)]
        total = sum(srcs.values())
        out = memo[key] = {}
        for src, amount in srcs.items():
            for terminal, (share, why, path) in resolve(src, chain, token, hops).items():
                old = out.get(terminal)
                share = amount / total * share + (old[0] if old else 0)
                out[terminal] = (share, old[1], old[2]) if old else (share, why, path)
        return out

    def resolve(src, chain, token, hops):
        why = kind(src, hops, chain, token)
        if why:
            return {src: (1.0, why, (src,))}
        # Money going round in a circle ends at the hop limit.
        return {
            t: (share, why, (src, *path))
            for t, (share, why, path) in upstream(src, chain, token, hops + 1).items()
        }

    out = {}
    for w, addresses in members.items():
        assets = []
        for chain, token in sorted({k[1:] for k in inbound if k[0] in addresses}):
            received, found = 0.0, {}
            for a in addresses:
                for src, amount in inbound.get((a, chain, token), {}).items():
                    if wallet.get(src) == w:
                        continue  # change and reshuffling inside the wallet
                    received += amount
                    for terminal, (share, why, path) in resolve(src, chain, token, 1).items():
                        old = found.get(terminal)
                        found[terminal] = (
                            (old[0] + amount * share, old[1], old[2])
                            if old
                            else (amount * share, why, (a, *path))
                        )
            if not received:
                continue
            sources = [
                {
                    "address": terminal,
                    "name": (nodes[terminal]["name"] if terminal in nodes else "")
                    or (labels[terminal]["name"] if terminal in labels else ""),
                    "kind": why.partition(":")[0],
                    "public": bool(terminal in nodes and nodes[terminal]["public"]),
                    "reason": why.partition(":")[2],
                    "share": round(amount / received, 4),
                    "amount": amount,
                    "path": list(path),
                }
                for terminal, (amount, why, path) in found.items()
            ]
            assets.append(
                {
                    "chain": chain,
                    "asset": symbols[(chain, token)],
                    "token": token,
                    "received": received,
                    "sources": sorted(sources, key=lambda x: -x["share"])[:12],
                }
            )
        out[addresses[0]] = sorted(assets, key=lambda x: (x["token"] != "", x["chain"], x["asset"]))
    return out
