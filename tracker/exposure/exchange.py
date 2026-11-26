"""Exchange exposure (docs/design/multichain.md, 2.2 #2): direct deposits to or withdrawals
from a labelled exchange. The exchange knows the address is its customer's, and can be made
to tell."""

from .. import chains


def check(ctx):
    parties = ctx.labelled("cex")
    if parties:
        used = {m["chain"] for p in parties for m in p["sent"] + p["received"]}
        params = {
            "exchange": ctx.names(parties),
            "count": sum(p["count"] for p in parties),
            "chains": ", ".join(sorted(map(chains.name, used))),
        }
        yield "x-exchange-exposure", params, ctx.evidence(parties)
