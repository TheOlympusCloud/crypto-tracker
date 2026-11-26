"""One privacy score per wallet of mine, across its chains (docs/design/multichain.md, 3.4).

The server makes it once, for everything that shows it or alerts on it, so the grade is the
same on every device and is the one an alert names. It is made from what syncs stored:
opening the page costs no call, and a check that would need one is skipped and says so.

A wallet's score holds its own findings (tied to a public wallet; reused UTXO addresses)
and the scan's checks: those of the counterparties over the stored transfers of all its
addresses, once per chain, and those of an address's family once per address.
Each finding counts once, at its worst; reuse counts per address, so one slip in an HD
wallet is not the F that am-i-exposed gives a single reused address. am-i-exposed's deep
scan of a Bitcoin address runs in the browser and is not part of it.
"""

from collections import defaultdict

from .. import chains
from .. import db as database
from ..heuristics import TRUSTED
from ..labels import describe
from . import CHECKS, IMPACT, Context, checked, finding, score

EVEN = 0.5  # below even odds a link is a guess to look at, as the verdict and alerts read it
REUSE_CAP = -40  # reused addresses cost IMPACT's -10 each, this much at most
EVIDENCE = 5
MINE = "(SELECT address FROM node WHERE mine)"


def _parties(db, owner):
    """Wallet -> chain -> counterparty -> what lookup.view lists for it, from the stored
    transfers of all the wallet's addresses (`owner`: address -> wallet; the trusted
    transfers, as the verdict reads them); and my address -> the transactions that paid it."""
    known = {r["address"] for r in db.execute("SELECT address FROM node WHERE mine")}
    parties = defaultdict(lambda: defaultdict(dict))
    paid = defaultdict(dict)
    for r in db.execute(
        f"SELECT chain, txid, src, dst, asset, amount FROM transfer "
        f"WHERE {TRUSTED} AND (src IN {MINE} OR dst IN {MINE}) ORDER BY ts"
    ):
        paid[r["dst"]].setdefault(r["txid"], r["chain"])
        for me, other, way in ((r["src"], r["dst"], "sent"), (r["dst"], r["src"], "received")):
            if me not in owner:
                continue
            p = parties[owner[me]][r["chain"]].setdefault(
                other,
                {"address": other, "count": 0, "sent": {}, "received": {}, "evidence": []},
            )  # fmt: skip
            p["count"] += 1
            p[way][r["asset"]] = p[way].get(r["asset"], 0.0) + r["amount"]
            if len(p["evidence"]) < 3 and r["txid"] not in {e["txid"] for e in p["evidence"]}:
                p["evidence"].append({"chain": r["chain"], "txid": r["txid"]})
    others = list({o for by in parties.values() for found in by.values() for o in found})
    labels = {}
    for i in range(0, len(others), 500):
        some = others[i : i + 500]
        marks = ",".join("?" * len(some))
        for r in db.execute(f"SELECT * FROM label WHERE address IN ({marks})", some):
            labels[r["address"]] = describe(r)
    for by in parties.values():
        for chain, found in by.items():
            for p in found.values():
                for way in ("sent", "received"):
                    p[way] = [{"chain": chain, "asset": a, "amount": v} for a, v in p[way].items()]
                p.update(labels.get(p["address"], {}), mine=int(p["address"] in known))
    return parties, paid


def _context(db, address, view):
    """A scan's context over stored data: no client, and no call to make."""
    ctx = Context(db, None, address, {"truncated": False, "errors": [], **view})
    ctx.left = 0  # opening the page makes no call
    return ctx


def _level(confidence):
    """am-i-exposed's confidence level of a link's confidence."""
    if confidence >= 1:
        return "deterministic"
    return "high" if confidence >= 0.8 else "medium" if confidence >= EVEN else "low"


def scores(db, verdicts):
    """The wallet's first address -> its ScoringResult, the findings by chain ("" for the
    wallet's own, across its chains) and gaps; None for a wallet never read. `verdicts` are
    graph.verdicts at the default hops, so the grade is always the one alerts compare."""
    parties, paid = _parties(db, {a: v["wallet"] for v in verdicts for a in v["addresses"]})
    names = {v["wallet"]: v["name"] or v["address"][:12] for v in verdicts}
    own = {
        r["address"]: describe(r)
        for r in db.execute(f"SELECT * FROM label WHERE address IN {MINE}")
    }
    out = {}
    for v in verdicts:
        if v["checked"] == "none":
            out[v["address"]] = None  # never "A+" for a wallet nothing was read of
            continue
        found, gaps = [], []  # (chain, finding)
        public = next((k for k in v["links"] if k["public"]), None)
        if public and not v["public"]:  # a public wallet is tied to its owner anyway
            c = public["confidence"]
            f = finding(
                "x-linked-public",
                {"wallet": names[public["wallet_id"]], "confidence": round(c * 100)},
                [],
            )
            f.update(scoreImpact=round(f["scoreImpact"] * c), confidence=_level(c))
            if c < EVEN:
                f["severity"] = "medium"
            found.append(("", f))
        by = parties[v["wallet"]]
        everything = [p for rows in by.values() for p in rows.values()]
        for chain in sorted(by):  # one finding per chain, counted over all the wallet's addresses
            ctx = _context(db, v["address"], {"connections": list(by[chain].values())})
            found += [(chain, f) for f in checked(ctx, CHECKS["any"], gaps)]
        for address in v["addresses"]:
            # What the address is itself (on a sanctions list, frozen), as a scan's view says.
            ctx = _context(db, address, {"connections": [], **own.get(address, {})})
            found += [(ctx.chain, f) for f in checked(ctx, CHECKS["any"], gaps)]
            # Its family's checks read the address on all its chains.
            ctx = _context(db, address, {"chains": sorted(by), "connections": everything})
            found += [(ctx.chain, f) for f in checked(ctx, CHECKS.get(ctx.chain, []), gaps)]
        reused = [
            a for a in v["addresses"] if database.chain_of(a) in chains.UTXO and len(paid[a]) > 1
        ]
        if reused:
            receipts = [{"chain": c, "txid": t} for a in reused for t, c in paid[a].items()]
            f = finding("x-address-reuse", {"count": len(reused)}, receipts[:EVIDENCE])
            f["scoreImpact"] = max(REUSE_CAP, IMPACT["x-address-reuse"][1] * len(reused))
            found.append((database.chain_of(reused[0]), f))
        worst, by_chain = {}, defaultdict(dict)
        for chain, f in found:
            kept = by_chain[chain].setdefault(f["id"], f)
            if kept is not f:  # another address of the wallet found it too: one row, its proof
                # shortcut: the row keeps the first address's params; merge them per id if
                # an HD wallet's addresses ever find one id with different ones.
                more = [e for e in f["evidence"] if e not in kept["evidence"]]
                kept["evidence"] = (kept["evidence"] + more)[:EVIDENCE]
            elif f["scoreImpact"] < worst.get(f["id"], {"scoreImpact": 1})["scoreImpact"]:
                worst[f["id"]] = f  # one exchange is enough to be exposed
        partial = v["checked"] != "full" or bool(gaps)
        # A check skipped here needed a call: the address's own scan makes it, once.
        gaps = [{**g, "id": "unread"} if g["id"] == "skipped" else g for g in gaps]
        out[v["address"]] = {
            **score(list(worst.values()), partial),
            "by_chain": {chain: list(rows.values()) for chain, rows in by_chain.items()},
            "gaps": [g for i, g in enumerate(gaps) if g not in gaps[:i]],  # once per HD wallet
        }
    return out
