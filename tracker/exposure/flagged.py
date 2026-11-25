"""Flagged and mixer counterparties (docs/design/multichain.md, 2.2 #4), from the labels the
tracker holds: scams, hacks and sanctions, and mixers. Exchanges freeze or question funds
that touched them. A token a stranger airdropped is no dealing: its transfers never make a
counterparty (lookup.view)."""

FINDINGS = {"flagged": "x-flagged-counterparty", "mixer": "x-mixer-counterparty"}


def check(ctx):
    for category, id in FINDINGS.items():
        if parties := ctx.labelled(category):
            params = {"name": ctx.names(parties), "count": sum(p["count"] for p in parties)}
            yield id, params, ctx.evidence(parties)
