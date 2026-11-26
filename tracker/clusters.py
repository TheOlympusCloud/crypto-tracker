"""What an observer sees: my wallets that look like one person, across chains."""

from collections import defaultdict

from . import db as database
from .alerts import FLOOR
from .heuristics import TRUSTED

WORST = ["none", "partial", "full"]  # a group is checked as far as its least checked wallet


def groups(db, verdicts):
    """The connected parts of my wallets under their direct links of FLOOR (the alert
    threshold) or more, from `graph.verdicts`; nothing is fetched. Wallets are named by their
    first address, as in the verdicts. Each group lists its wallets, their chains, the public
    ones among them, how far all were checked, and the links holding it together, weakest
    first. `apart` names the two parts a link alone holds together, else it is None.
    Groups with a public wallet come first: anyone who knows it can name the others."""
    by = {v["address"]: v for v in verdicts}
    links, near = {}, defaultdict(set)
    for v in verdicts:
        for k in v["direct"]:
            if k["confidence"] >= FLOOR and v["address"] < k["wallet"]:
                links[v["address"], k["wallet"]] = k
                near[v["address"]].add(k["wallet"])
                near[k["wallet"]].add(v["address"])

    def reach(start, cut=()):
        seen, todo = {start}, [start]
        while todo:
            u = todo.pop()
            for x in near[u] - seen:
                if {u, x} != set(cut):
                    seen.add(x)
                    todo.append(x)
        return seen

    out, done = [], set()
    for w in sorted(near):
        if w in done:
            continue
        members = reach(w)
        done |= members
        held = []
        # shortcut: one walk per link, fine for the tens of wallets one person tracks
        for (a, b), k in links.items():
            if a in members:
                side = reach(a, (a, b))
                apart = None if b in side else [sorted(side), sorted(members - side)]
                held.append(
                    {"a": a, "b": b, "confidence": k["confidence"], "path": k["path"],
                     "kinds": k["kinds"], "apart": apart}
                )  # fmt: skip
        out.append(
            {
                "wallets": sorted(members),
                "public": sorted(a for a in members if by[a]["public"]),
                "chains": _chains(db, [x for a in members for x in by[a]["addresses"]]),
                "checked": min((by[a]["checked"] for a in members), key=WORST.index),
                "links": sorted(held, key=lambda k: (k["confidence"], k["a"], k["b"])),
            }
        )
    return sorted(out, key=lambda g: (not g["public"], -len(g["wallets"]), g["wallets"]))


def _chains(db, addresses):
    """The chains these addresses dealt on; an address with no transfer counts on its own
    chain ("evm" for an EVM address, which exists on all of them, unless another EVM address
    names a chain)."""
    marks = ",".join("?" * len(addresses))
    used = defaultdict(set)
    for r in db.execute(
        f"SELECT src, chain FROM transfer WHERE src IN ({marks}) AND {TRUSTED} "
        f"UNION SELECT dst, chain FROM transfer WHERE dst IN ({marks}) AND {TRUSTED}",
        addresses * 2,
    ):
        used[r[0]].add(r[1])
    found = {c for a in addresses for c in used[a] or {database.chain_of(a)}}
    if any(used[a] for a in addresses if database.chain_of(a) == "evm"):
        found.discard("evm")
    return sorted(found)
