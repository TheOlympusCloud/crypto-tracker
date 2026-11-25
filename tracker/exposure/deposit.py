"""Exchange deposit address shared (docs/design/multichain.md, 2.2 #3): this address and other
wallets of mine paid the same exchange deposit address (tracker/deposits.py). A deposit
address belongs to one exchange account, so the wallets are one customer to anyone looking."""


def check(ctx):
    rows = ctx.db.execute(
        "SELECT a, b, evidence FROM link WHERE kind = 'deposit-address' AND ?1 IN (a, b)",
        (ctx.address,),
    ).fetchall()
    if not rows:
        return
    paid = dict(r["evidence"].split(" > ", 1) for r in rows)  # deposit address -> exchange
    others = {r["b"] if r["a"] == ctx.address else r["a"] for r in rows}
    marks = ",".join("?" * len(others))
    names = ctx.db.execute(
        f"SELECT COALESCE(NULLIF(name, ''), address) FROM node WHERE address IN ({marks})",
        list(others),
    )
    marks = ",".join("?" * len(paid))
    evidence = ctx.db.execute(
        f"SELECT chain, txid FROM transfer WHERE src = ? AND dst IN ({marks}) ORDER BY ts DESC",
        (ctx.address, *paid),
    )
    params = {
        "exchange": ", ".join(sorted(set(paid.values()))),
        "count": len(others),
        "wallets": ", ".join(sorted(r[0] for r in names)),
    }
    yield (
        "x-deposit-address-shared",
        params,
        ctx.evidence([{"evidence": list(map(dict, evidence))}]),
    )
