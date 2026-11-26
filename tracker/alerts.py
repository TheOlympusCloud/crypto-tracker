"""Tell the user when a verdict gets worse: a new link between two wallets, or a stronger one,
or a wallet's privacy grade that drops."""

import json
import time

from . import graph
from .exposure import GRADES, wallets
from .heuristics import pair

BASELINE = ("baseline", "")  # marker row: a comparison point exists
FLOOR = 0.5  # a weaker guess is listed in the verdict but is not worth an alarm
STEP = 0.05  # a confidence rise smaller than this is not news


def check(db):
    """Compare the links between my wallets, and their grades, with those known after the last
    sync."""
    names, public, now = {}, {}, {}
    verdicts = graph.verdicts(db)
    for v in verdicts:
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
        if confidence < FLOOR or (before is not None and confidence < before + STEP):
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
    _grades(db, verdicts)
    db.commit()


ORDER = [g for _, g in GRADES] + ["F"]  # best first


def _grades(db, verdicts):
    """An alert when a wallet's grade drops, naming the grade its page shows: both come from
    exposure.wallets on the same verdicts. A wallet seen for the first time sets its baseline.
    The page words the alert (`kind` and `params`), as it words the findings."""
    known = {r["wallet"]: r["grade"] for r in db.execute("SELECT * FROM known_grade")}
    scores = wallets.scores(db, verdicts)
    grades = {v["wallet"]: scores[v["address"]] for v in verdicts if scores[v["address"]]}
    for wallet, s in grades.items():
        before = known.get(wallet)
        if before and ORDER.index(s["grade"]) > ORDER.index(before):
            params = {"from": before, "to": s["grade"], "score": s["score"]}
            db.execute(
                "INSERT INTO alert(ts, wallet, text, kind, params) VALUES (?, ?, '', 'grade', ?)",
                (int(time.time()), wallet, json.dumps(params)),
            )
    db.execute("DELETE FROM known_grade")
    db.executemany(
        "INSERT INTO known_grade VALUES (?, ?)", [(w, s["grade"]) for w, s in grades.items()]
    )


def recent(db):
    """The latest alerts, each pointing at the first address of the wallet it is about."""
    first = {}
    for r in db.execute("SELECT address, wallet FROM node WHERE mine ORDER BY address"):
        first.setdefault(r["wallet"] or r["address"], r["address"])
    rows = db.execute("SELECT * FROM alert ORDER BY id DESC LIMIT 100").fetchall()
    return {
        "unread": sum(not r["seen"] for r in rows),
        "items": [
            {
                "time": r["ts"],
                "text": r["text"],
                "address": first.get(r["wallet"], r["wallet"]),
                "seen": bool(r["seen"]),
                "kind": r["kind"],  # "link": `text` says it; "grade": the page words `params`
                "params": json.loads(r["params"]) if r["params"] else None,
            }
            for r in rows
        ],
    }
