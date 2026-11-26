"""Mixer use that hides little (docs/design/multichain.md, 2.3 #5): a Tornado Cash deposit by
another wallet of mine and a withdrawal by this one, or the reverse, with few deposits
between them in the pool (tracker/mixers.py). Anyone watching the pool can bound the odds
that the two are one person."""

from .. import mixers


def check(ctx):
    found = [
        (mixers.bound(p["deposits"]), p)
        for p in mixers.pairs(ctx.db)
        if ctx.address in (p["deposit"], p["withdrawal"])
        and p["deposits"] is not None
        and mixers.bound(p["deposits"]) is not None
    ]
    if not found:
        return
    confidence, best = max(found, key=lambda f: f[0])
    others = {p["withdrawal"] if p["deposit"] == ctx.address else p["deposit"] for _, p in found}
    marks = ",".join("?" * len(others))
    names = ctx.db.execute(
        f"SELECT COALESCE(NULLIF(name, ''), address) FROM node WHERE address IN ({marks})",
        list(others),
    )
    params = {
        "pool": mixers.POOLS[best["chain"]][best["pool"]],
        "count": best["deposits"],
        "confidence": round(confidence * 100),
        "wallets": ", ".join(sorted(r[0] for r in names)),
    }
    evidence = [
        {"chain": best["chain"], "txid": best[side]} for side in ("deposit_tx", "withdrawal_tx")
    ]
    yield "x-mixer-linked", params, evidence
