"""A look at any address, mine or not: what it holds and who it deals with.

Read-only: the address is fetched live and nothing about it is stored, so it does not
become a wallet of mine and no verdict or alert can change until it is tracked. Only the
price cache is written. Like a sync, the address is sent to the indexer of its chain.
"""

import os
import time
from collections import defaultdict

from . import chains, prices, tokens
from . import db as database
from .graph import combine

LISTED = 200  # counterparties returned, busiest first
DRAWN = 60  # of those, how many are in the graph
NEW = 30 * 86400  # an address first seen this recently is new
WORDS = {"cex": "exchange", "dex": "DEX", "bridge": "bridge", "mixer": "mixer"}
WORDS |= {"flagged": "flagged", "defi": "DeFi", "service": "service", "other": "label"}


def view(db, client, address, evm=()):
    """`address` is canonical (db.norm). `evm` names the EVM chains to read; empty: all."""
    chain = database.chain_of(address)
    if chain == "evm":
        every = chains.evm_chains()
        read = {c: cfg for c, cfg in every.items() if (not evm or c in evm)}
        data = chains.fetch_evm(client, address, read)
        read = [c for c, cfg in read.items() if not cfg.get("needs_key")]
    else:
        data, read = chains.fetch(client, address), [chain]
    # Indexer output is untrusted, as in a sync.
    ok = database.valid
    transfers = [t for t in data["transfers"] if address in (t[2], t[3]) and ok(t[2]) and ok(t[3])]
    held = {(c, token): (asset, amount) for c, asset, token, amount in data["balances"]}
    if not data["hub"]:  # the whole history is loaded, so token balances follow from it
        sums, names = defaultdict(float), {}
        for c, _, _, dst, asset, token, amount, _ in transfers:
            if token:
                sums[(c, token)] += amount if dst == address else -amount
                names[(c, token)] = asset
        for k, v in sums.items():
            if v > 1e-9:
                held.setdefault(k, (names[k], v))

    keys = {prices.key(c, asset, token) for (c, token), (asset, _) in held.items()}
    keys |= {prices.key(t[0], t[4], t[5]) for t in transfers}
    prices.refresh(db, client, keys)
    price, fx = prices.rates(db)
    blind = os.environ.get("CT_PRICES", "on") == "off"

    def vouched(c, asset, token):
        """Anyone can airdrop a token: only the ones the price source knows well count."""
        p = price.get(prices.key(c, asset, token))
        return not token or blind or bool(p and (p["confidence"] or 0) >= prices.CONFIDENT)

    # Every token held is listed, with how far it can be trusted. A token that arrived in
    # transactions this address took no part in, and that it never moved, was not asked for.
    took_part = {t[1] for t in transfers if t[2] == address}
    arrived, moved = defaultdict(set), set()
    for c, txid, src, dst, _, token, _, _ in transfers:
        if token and dst == address:
            arrived[(c, token)].add(txid)
        if token and src == address:
            moved.add((c, token))
    listed = tokens.uniswap(client) if any(token for _, token in held) else {}
    famous = tokens.famous(listed)
    known = tokens.jupiter(client, [t for c, t in held if c == "solana" and t])
    assets = []
    for (c, token), (asset, amount) in held.items():
        p = price.get(prices.key(c, asset, token))
        jup = known.get(token) if c == "solana" else None
        if token and c == "solana":  # Solana does not name its tokens on chain
            asset = (jup or {}).get("symbol") or (p and p["symbol"]) or asset
        name = (jup or {}).get("name") or data.get("names", {}).get((c, token), "")
        on = "Uniswap's default token list" if tokens.listed_on(listed, c, token) else ""
        on = on or ("Jupiter's verified list" if (jup or {}).get("verified") else "")
        unasked = None  # the history is incomplete, or never showed the token arriving
        if not data["hub"] and arrived[(c, token)]:
            unasked = (c, token) not in moved and not arrived[(c, token)] & took_part
        trust = tokens.trust(c, token, asset, name, on, famous, p, unasked)
        # A scam token's price is part of the bait: only a reliable price gives a value.
        fair = p and fx and vouched(c, asset, token) and trust["level"] != "scam"
        assets.append(
            {"chain": c, "asset": asset, "token": token, "name": name, "amount": amount,
             "value": amount * p["usd"] * fx if fair else None, "trust": trust}
        )  # fmt: skip
    assets.sort(key=lambda a: (a["trust"]["level"] == "scam", -(a["value"] or 0), -a["amount"]))

    # Transfers go by the names of what is held: on Solana those come from Jupiter or prices.
    names = {(a["chain"], a["token"]): a["asset"] for a in assets if a["token"]}
    transfers = [
        (*t[:4], names.get((t[0], t[5]), t[4]), *t[5:])
        for t in transfers
        if vouched(t[0], t[4], t[5])
    ]
    edges, parties = {}, {}
    for c, txid, src, dst, asset, token, amount, ts in transfers:
        e = edges.setdefault(
            (c, src, dst, token),
            {"chain": c, "src": src, "dst": dst, "asset": asset, "token": token, "count": 0,
             "amount": 0.0, "first": ts, "last": ts, "txid": txid},
        )  # fmt: skip
        other, way = (dst, "sent") if src == address else (src, "received")
        p = parties.setdefault(
            other,
            {"address": other, "count": 0, "first": ts, "last": ts, "sent": {}, "received": {}},
        )
        for row in (e, p):
            row["count"] += 1
            row["first"], row["last"] = min(row["first"], ts), max(row["last"], ts)
        e["amount"] += amount
        p[way][(c, asset)] = p[way].get((c, asset), 0.0) + amount

    guesses = defaultdict(list)  # a guess that the address shares an owner with another
    for a, b, kind, confidence, evidence in data["links"]:
        if address in (a, b) and a != b and ok(a) and ok(b):
            guesses[(a, b, kind)].append((confidence, evidence))
    links = [
        {"a": a, "b": b, "kind": kind, "confidence": combine([c for c, _ in rows]),
         "evidence": [e for _, e in rows[:5]], "count": len(rows)}
        for (a, b, kind), rows in guesses.items()
    ]  # fmt: skip
    for k in links:
        other = k["b"] if k["a"] == address else k["a"]
        p = parties.setdefault(
            other, {"address": other, "count": 0, "first": 0, "last": 0, "sent": {}, "received": {}}
        )
        if k["confidence"] > p.get("guess", {"confidence": 0})["confidence"]:
            p["guess"] = {"kind": k["kind"], "confidence": k["confidence"]}

    # What the database already knows about these addresses: mine or not, and their labels.
    nodes = {address: {"address": address, "mine": 0, "public": 0, "name": ""}}
    nodes |= {a: {"address": a, "mine": 0, "public": 0, "name": ""} for a in parties}
    for i in range(0, len(nodes), 500):
        some = list(nodes)[i : i + 500]
        marks = ",".join("?" * len(some))
        for r in db.execute(
            f"SELECT address, mine, public, name, wallet FROM node WHERE address IN ({marks})", some
        ):
            nodes[r["address"]].update(dict(r))
        for r in db.execute(f"SELECT * FROM label WHERE address IN ({marks})", some):
            nodes[r["address"]].update(label=r["name"], category=r["category"])
    for node in nodes.values():
        node["chain"] = database.chain_of(node["address"])
        node.update(chains.explorer(node["chain"]))
    me = nodes[address]
    me["icon"] = chains.icon(chain, read)  # in the panel and on its node in the graph
    me["balances"] = [{k: a[k] for k in ("chain", "asset", "amount")} for a in assets]
    if len(read) == 1:
        me.update(chains.explorer(read[0]))  # an EVM address read on one chain opens there

    # My wallets first, then the busiest.
    ranked = sorted(parties.values(), key=lambda p: (-nodes[p["address"]]["mine"], -p["count"]))
    for p in ranked:
        for way in ("sent", "received"):
            p[way] = [{"chain": c, "asset": a, "amount": v} for (c, a), v in p[way].items()]
        p.update(nodes[p["address"]])
    drawn = {address} | {p["address"] for p in ranked[:DRAWN]}
    watch = db.execute("SELECT 1 FROM watch WHERE address = ?", (address,)).fetchone()
    values = [a["value"] for a in assets if a["value"] is not None]
    errors = data.get("errors", [])
    used = bool(transfers or any(a["amount"] for a in assets))
    where = f"{chains.name(chain)} address"
    if chain == "evm":
        where += f", read on {', '.join(map(chains.name, read)) or 'no chain'}"
    return {
        **{k: v for k, v in me.items() if k != "balances"},
        "chains": read,
        "watched": bool(watch),
        "assets": assets,
        "value": sum(values) if values else None,
        "currency": prices.currency() if fx else "USD",
        # tokens without a reliable price: their transfers are left out of the counterparties
        "unvouched": sum(not vouched(a["chain"], a["asset"], a["token"]) for a in assets),
        "risk": risk(where, me, transfers, ranked, twins(db, address), data["hub"], errors, used),
        "connections": ranked[:LISTED],
        "more": max(0, len(ranked) - LISTED),
        "truncated": bool(data["hub"]),
        "errors": errors,
        "notes": chains.notes(chain),
        "graph": {
            "nodes": [nodes[a] for a in drawn],
            "edges": [e for e in edges.values() if e["src"] in drawn and e["dst"] in drawn],
            "links": [k for k in links if k["a"] in drawn and k["b"] in drawn],
        },
    }


def lookalike(a, b):
    """Address poisoning: a stranger's address made to start and end like one in use, so
    that a payment copied from the history goes to it. The prefix every address of a
    Bitcoin Cash shares is no likeness."""
    a, b = a.removeprefix("bitcoincash:"), b.removeprefix("bitcoincash:")
    return a != b and a[:6] == b[:6] and a[-4:] == b[-4:]


def twins(db, address):
    """[(address, whose)] of my wallets and of the addresses they paid that `address` imitates."""
    mine = [r[0] for r in db.execute("SELECT address FROM node WHERE mine")]
    paid = db.execute(
        "SELECT DISTINCT dst FROM transfer WHERE src IN (SELECT address FROM node WHERE mine)"
    )
    used = [(a, "your wallet") for a in mine] + [(r[0], "an address you sent to") for r in paid]
    return [(a, whose) for a, whose in used if lookalike(address, a)]


def risk(where, me, real, parties, twins, hub=False, errors=(), held=True, now=None):
    """Is this address risky to send to? {"level", "headline", "reason", "reasons"}; each
    reason is {"level": bad | warn | unknown | info, "text"}. It never says safe: the best it
    says is that no red flag was found, and what was looked at."""
    now = now or time.time()
    day = lambda ts: time.strftime("%Y-%m-%d", time.gmtime(ts))
    out = []
    say = lambda level, text: out.append({"level": level, "text": text})
    label, category = me.get("label"), me.get("category")
    if category == "flagged":
        say("bad", f"Flagged: {label}. A scam, a hack or sanctions, by its label.")
    elif category == "mixer":
        say(
            "bad",
            f"A mixer, {label}: what is sent there cannot be followed, and mixers can be sanctioned.",
        )
    for other, whose in twins:
        say("bad", f"It starts and ends like {whose}, {other}, but is a different address: the "
            "trap of address poisoning. Copy addresses from the source, never from the history.")  # fmt: skip
    say("info", f"{where}.")
    for text in errors:
        say("unknown", f"Not everything could be read ({text}).")
    times = [t[7] for t in real if t[7]]
    count = len({t[1] for t in real})
    if not real and not held:
        if not errors:
            say(
                "warn",
                "No transaction found: it has probably never been used. Check every character.",
            )
    elif hub:
        say("info", f"Very active: only its newest {count} transactions were read.")
    elif times and now - min(times) < NEW:
        say(
            "warn",
            f"New: first seen {day(min(times))}, {int((now - min(times)) // 86400)} days ago.",
        )
    elif times:
        say("info", f"{count} transactions, {day(min(times))} to {day(max(times))}.")
    if category and category not in ("flagged", "mixer"):
        say("info", f"Known as {label} ({WORDS.get(category, category)}).")
    if me.get("mine"):
        say("info", "This is one of your own wallets.")
    ours = [p.get("name") or p["address"] for p in parties if p.get("mine")]
    if ours:
        say(
            "info",
            f"It dealt with your wallet{'s' if len(ours) > 1 else ''} {', '.join(ours[:3])}.",
        )
    # One line per label: an entity's many addresses often share it.
    labelled = {p["label"]: p for p in parties if p.get("category") and not p.get("mine")}
    shady = [p for p in labelled.values() if p["category"] in ("flagged", "mixer")]
    for p in shady[:3]:
        say("warn", f"It dealt with {p['label']} ({WORDS[p['category']]}).")
    named = [p for p in labelled.values() if p not in shady]
    if named:
        names = ", ".join(f"{p['label']} ({WORDS.get(p['category'], 'label')})" for p in named[:3])
        say("info", f"It deals with {names}.")
    first = lambda level: next((r["text"] for r in out if r["level"] == level), None)
    for level, headline in (
        ("bad", "Risky to send to"),
        ("warn", "Check before sending"),
        ("unknown", "Not fully checked"),
    ):
        if first(level):
            return {"level": level, "headline": headline, "reason": first(level), "reasons": out}
    return {
        "level": "ok",
        "headline": "No red flags found",
        "reason": "That is not a guarantee: only what is listed here was checked.",
        "reasons": out,
    }
