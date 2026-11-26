"""Flagged and mixer counterparties (docs/design/multichain.md, 2.2 #4), from the labels the
tracker holds: scams, hacks, OFAC's sanctions lists, Tether's and Circle's freezes, and
mixers. Exchanges freeze or question funds that touched them. A token a stranger airdropped
is no dealing: its transfers never make a counterparty (lookup.view). An address that is
flagged itself says so first."""

FINDINGS = {"flagged": "x-flagged-counterparty", "mixer": "x-mixer-counterparty"}


def check(ctx):
    if ctx.view.get("category") == "flagged" and not ctx.view.get("label_guess"):
        yield "x-flagged-address", {"name": ctx.view["label"]}, []
    for category, id in FINDINGS.items():
        if parties := ctx.labelled(category):
            params = {"name": ctx.names(parties), "count": sum(p["count"] for p in parties)}
            yield id, params, ctx.evidence(parties)
