"""Mixer use that hides little (docs/design/multichain.md, 2.3 #5): Tornado Cash deposits by
another wallet of mine and a withdrawal by this one, or the reverse, with few other deposits
around them in the pool (tracker/mixers.py). Anyone watching the pool can bound the odds
that the two are one person."""

from .. import mixers


def check(ctx):
    found = [p for p in mixers.strongest(ctx.db) if ctx.address in (p["deposit"], p["withdrawal"])]
    if not found:
        return
    best = max(found, key=mixers.odds)
    others = {p["withdrawal"] if p["deposit"] == ctx.address else p["deposit"] for p in found}
    marks = ",".join("?" * len(others))
    names = ctx.db.execute(
        f"SELECT COALESCE(NULLIF(name, ''), address) FROM node WHERE address IN ({marks})",
        list(others),
    )
    params = {
        "pool": mixers.POOLS[best["chain"]][best["pool"]],
        "count": best["own_at"] + best["deposits"],
        "own": best["own"],
        "confidence": round(mixers.bound(best) * 100),
        "wallets": ", ".join(sorted(r[0] for r in names)),
    }
    evidence = [
        {"chain": best["chain"], "txid": best[side]} for side in ("deposit_tx", "withdrawal_tx")
    ]
    yield "x-mixer-linked", params, evidence
