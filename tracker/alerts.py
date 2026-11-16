"""Tell the user when a verdict gets worse: a new link between two wallets, or a stronger one."""

import time

from . import graph
from .heuristics import pair

BASELINE = ("baseline", "")  # marker row: a comparison point exists
STEP = 0.05  # a confidence rise smaller than this is not news


def check(db):
    """Compare the links between my wallets with those known after the last sync."""
    names, public, now = {}, {}, {}
    for v in graph.verdicts(db):
        names[v["wallet"]] = v["name"] or v["address"][:12]
        public[v["wallet"]] = v["public"]
        for k in v["links"]:
            now[pair(v["wallet"], k["wallet_id"])] = k["confidence"]
    known = {(r["a"], r["b"]): r["confidence"] for r in db.execute("SELECT * FROM known_link")}
    # The very first check (new install, or first sync after an upgrade) has nothing to
    # compare with: it records what exists instead of announcing every link as new.
    first = known.pop(BASELINE, None) is None
    for (a, b), confidence in {} if first else now.items():
        before = known.get((a, b))
        if before is not None and confidence < before + STEP:
            continue
        was = f"was {before:.0%}" if before is not None else "no link was known before"
        exposed = " A private wallet is now tied to a public one." if public[a] != public[b] else ""
        db.execute(
            "INSERT INTO alert(ts, wallet, other, text) VALUES (?, ?, ?, ?)",
            (
                int(time.time()),
                a,
                b,
                f"{names[a]} can be tied to {names[b]} with {confidence:.0%} confidence ({was}).{exposed}",
            ),
        )
    db.execute("DELETE FROM known_link")
    db.executemany(
        "INSERT INTO known_link VALUES (?, ?, ?)",
        [(*BASELINE, 0), *[(a, b, c) for (a, b), c in now.items()]],
    )
    db.commit()
