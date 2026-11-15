"""Ownership heuristics. Each one yields links with an explicit confidence in [0, 1].

A link is a guess that two addresses share an owner; a transfer is a fact. They are stored
and drawn separately so a guess never looks like a fact.
"""

from bisect import bisect_left, bisect_right
from collections import Counter

# Confidence of each heuristic, in one place so the UI legend and README can quote them.
COMMON_INPUT = 0.9  # addresses spent together in one transaction share a signer
COMMON_INPUT_COINJOIN = 0.2  # ...unless the transaction looks like a CoinJoin
CHANGE_SCRIPT = 0.6  # the output with the same script type as the inputs is the change
CHANGE_ROUND = 0.5  # the non-round output of a payment is the change
CHANGE_BOTH = 0.75  # both change rules point at the same output

BRIDGE_WINDOW = 6 * 3600  # seconds between the two legs
BRIDGE_FEE = 0.03  # the destination leg may be this much smaller than the source leg
SERVICE = {"cex", "dex", "bridge", "mixer", "defi", "service"}

# Unknown tokens anyone can airdrop to two wallets would fake a link between them, so only
# native coins and tokens one of my wallets has itself sent count as evidence.
TRUSTED = (
    "(token = '' OR token IN "
    "(SELECT token FROM transfer WHERE src IN (SELECT address FROM node WHERE mine)))"
)

_SYMBOL = {"WETH": "ETH", "USDC.E": "USDC", "USDBC": "USDC", "USDT0": "USDT", "XDAI": "DAI"}


def pair(a, b):
    return (a, b) if a < b else (b, a)


def btc_inputs(tx):
    """address -> satoshis spent by it in this transaction."""
    ins = Counter()
    for vin in tx["vin"]:
        prev = vin.get("prevout") or {}
        if prev.get("scriptpubkey_address"):
            ins[prev["scriptpubkey_address"]] += prev["value"]
    return ins


def looks_like_coinjoin(tx):
    values = Counter(o["value"] for o in tx["vout"])
    return len(btc_inputs(tx)) >= 3 and max(values.values(), default=0) >= 3


def change_output(tx):
    """(address, confidence) of the likely change output of a 2-output payment, or None."""
    outs = [o for o in tx["vout"] if o.get("scriptpubkey_address")]
    ins = btc_inputs(tx)
    if (
        len(outs) != 2
        or len(tx["vout"]) != 2
        or any(o["scriptpubkey_address"] in ins for o in outs)
    ):
        return None
    in_types = {(v.get("prevout") or {}).get("scriptpubkey_type") for v in tx["vin"]}
    votes = {}
    same_type = [o for o in outs if {o.get("scriptpubkey_type")} == in_types]
    if len(same_type) == 1:
        votes[same_type[0]["scriptpubkey_address"]] = CHANGE_SCRIPT
    round_ = [o for o in outs if o["value"] % 100_000 == 0]  # multiple of 0.001 BTC
    if len(round_) == 1:
        other = outs[1] if round_[0] is outs[0] else outs[0]
        if other["scriptpubkey_address"] in votes:
            return other["scriptpubkey_address"], CHANGE_BOTH
        votes[other["scriptpubkey_address"]] = CHANGE_ROUND
    if len(votes) != 1:  # no rule fired, or the two rules disagree
        return None
    return next(iter(votes.items()))


def btc_links(address, tx, cap=50):
    """Links (a, b, kind, confidence, evidence) that this transaction implies for `address`."""
    ins = btc_inputs(tx)
    if address not in ins:
        return []
    coinjoin = looks_like_coinjoin(tx)
    conf = COMMON_INPUT_COINJOIN if coinjoin else COMMON_INPUT
    others = [a for a in ins if a != address][:cap]
    links = [(*pair(address, a), "common-input", conf, tx["txid"]) for a in others]
    change = None if coinjoin else change_output(tx)
    if change:
        links.append((*pair(address, change[0]), "change", change[1], tx["txid"]))
    return links


def symbol(asset):
    asset = asset.upper()
    return _SYMBOL.get(asset, asset)


def match_bridges(db):
    """Pair an outgoing transfer of one of my wallets with an incoming transfer of another of
    my wallets on a different chain: same asset, slightly smaller amount, shortly after.

    shortcut: same-asset bridges only; swaps that change the asset (THORChain, BTC<->ETH)
    need price data to compare amounts, add it when such a trace matters.
    """
    mine = "(SELECT address FROM node WHERE mine)"
    # A bridged token often lands in a wallet that never sent it, so the receiving leg is
    # also trusted when it comes from a labelled bridge.
    from_bridge = "src IN (SELECT address FROM label WHERE category = 'bridge')"
    outs = db.execute(
        f"SELECT * FROM transfer WHERE {TRUSTED} AND src IN {mine} ORDER BY ts"
    ).fetchall()
    ins = db.execute(
        f"SELECT * FROM transfer WHERE ({TRUSTED} OR {from_bridge}) AND dst IN {mine} ORDER BY ts"
    ).fetchall()
    times = [i["ts"] for i in ins]
    bridges = {
        r["address"] for r in db.execute("SELECT address FROM label WHERE category = 'bridge'")
    }
    candidates = []
    for o in outs:
        window = slice(bisect_left(times, o["ts"]), bisect_right(times, o["ts"] + BRIDGE_WINDOW))
        for i in ins[window]:
            if (
                i["chain"] != o["chain"]
                and i["dst"] != o["src"]
                and symbol(i["asset"]) == symbol(o["asset"])
                and o["amount"] * (1 - BRIDGE_FEE) <= i["amount"] <= o["amount"] * 1.001
            ):
                candidates.append((o, i))
    per_out = Counter(
        (o["chain"], o["txid"], o["src"], o["dst"], o["token"]) for o, _ in candidates
    )
    per_in = Counter((i["chain"], i["txid"], i["src"], i["dst"], i["token"]) for _, i in candidates)
    db.execute("DELETE FROM link WHERE kind = 'bridge'")
    for o, i in candidates:
        conf = 0.5
        if o["dst"] in bridges or i["src"] in bridges:
            conf += 0.25
        if i["amount"] >= o["amount"] * 0.995:
            conf += 0.15
        if i["ts"] - o["ts"] <= 600:
            conf += 0.1
        # Several transfers fitting the same leg make each individual match less likely.
        conf /= max(
            per_out[(o["chain"], o["txid"], o["src"], o["dst"], o["token"])],
            per_in[(i["chain"], i["txid"], i["src"], i["dst"], i["token"])],
        )
        db.execute(
            "INSERT OR REPLACE INTO link VALUES (?, ?, 'bridge', ?, ?)",
            (
                *pair(o["src"], i["dst"]),
                round(min(conf, 0.95), 2),
                f"{o['chain']}:{o['txid']} > {i['chain']}:{i['txid']}",
            ),
        )
    db.commit()
