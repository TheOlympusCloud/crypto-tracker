"""The same address on several EVM chains (docs/design/multichain.md, 2.3 #3): whatever ties
it to a person on one chain ties it on all of them."""

from .. import chains


def check(ctx):
    used = {m["chain"] for p in ctx.view["connections"] for m in p["sent"] + p["received"]}
    if len(used) > 1:
        names = ", ".join(sorted(map(chains.name, used)))
        yield "x-multi-chain", {"count": len(used), "chains": names}, []
