"""Is a token the one it claims to be? Each holding is matched against public token lists and
checked for the marks of airdrop scams. "verified" only ever means that a list names this exact
contract, never that the token is safe; without a list match the best a token gets is "unknown".

Uniswap's list is downloaded whole, so it learns nothing about any address. Jupiter is asked
about the Solana mints an address holds, like the price source; CT_PRICES=off spares it too.
"""

import os
import re
import time
import unicodedata
from collections import defaultdict

import httpx

from . import chains, prices
from .heuristics import SERVICE

UNISWAP = "https://tokens.uniswap.org"  # Uniswap Labs' default list: the built-in EVM chains
JUPITER = "https://lite-api.jup.ag/tokens/v2/search"  # Jupiter's token API, up to 100 mints a call
SOLANA_ID = 501000101  # Uniswap's chain id for Solana
TTL = 86400
CACHE = {}  # url or mint -> (time, value); lists change slowly

# Letters that look Latin but are not, as airdrop scams use them: "USDС" with a Cyrillic Es.
LOOKALIKE = str.maketrans(
    "АВЕКМНОРСТХЅІЈаеорсхуѕіјΑΒΕΖΗΙΚΜΝΟΡΤΥΧ₮", "ABEKMHOPCTXSIJaeopcxysijABEZHIKMNOPTYXT"
)
# A website or a reward in a token's name: the bait of airdrop scams.
BAIT = re.compile(
    r"https?:|www\.|t\.me/|\.(com|io|org|net|xyz|app|site|top|vip|live|cc|me|link|fun|club"
    r"|gift|claim|info|pro|finance|network|us)\b|claim|reward|airdrop|visit|voucher|bonus",
    re.IGNORECASE,
)


def skeleton(symbol):
    """The symbol as it reads: "$usdc", "ＵＳＤＣ" and "USDС" are all USDC."""
    text = unicodedata.normalize("NFKC", symbol).translate(LOOKALIKE).upper()
    return re.sub(r"[^A-Z0-9]", "", text)


def _cached(key, fetch):
    hit = CACHE.get(key)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    value = fetch()
    CACHE[key] = (time.time(), value)
    return value


def parse_list(data):
    """(chain id, contract) -> (symbol, name) of a token list; EVM contracts in lower case."""
    out = {}
    for t in data.get("tokens", []):
        try:
            address = str(t["address"])
            key = address.lower() if address.startswith("0x") else address
            out[(int(t["chainId"]), key)] = (str(t["symbol"])[:20], str(t.get("name") or "")[:40])
        except (KeyError, ValueError, TypeError):
            continue
    return out


RETRY = 600  # seconds before a list that did not answer is asked again


def uniswap(client):
    """Uniswap's default list, or {} while it cannot be fetched: then nothing is verified,
    which errs on the side of caution."""
    try:
        return _cached(UNISWAP, lambda: parse_list(client.get(UNISWAP).raise_for_status().json()))
    except (httpx.HTTPError, ValueError, AttributeError):
        # Remembered for a while: one sync asks it once, not once per wallet.
        CACHE[UNISWAP] = (time.time() - TTL + RETRY, {})
        return {}


def listed_on(listed, chain, token):
    chain_id = SOLANA_ID if chain == "solana" else chains.EVM.get(chain, (None,))[0]
    return (chain_id, token) in listed


def famous(listed):
    """What is worth imitating: the symbols and names of listed tokens on any chain, and the
    native coins."""
    return {skeleton(text) for pair in listed.values() for text in pair if text} | set(
        prices.NATIVE
    )


# What a fake dollar, ether or bitcoin is dressed as: one of these at the start or the end of
# its symbol (XUSD, USDC2, ETH2), never in the middle of a word (METHANE).
DRESS = {"USD": "a dollar token", "ETH": "ETH", "BTC": "BTC"}


def dressed(symbol, name, famous):
    """What a token pretends to be, from its symbol or name, or None."""
    for text in (symbol, name):
        if text and skeleton(text) in famous:
            return text
    bare = skeleton(symbol)
    return next((what for k, what in DRESS.items() if bare.startswith(k) or bare.endswith(k)), None)


def jupiter(client, mints):
    """mint -> {"symbol", "name", "verified"} for the Solana mints Jupiter knows."""
    if os.environ.get("CT_PRICES", "on") == "off":
        return {}
    now = time.time()
    missing = [m for m in mints if not (m in CACHE and now - CACHE[m][0] < TTL)]
    try:
        for i in range(0, len(missing), 100):
            some = missing[i : i + 100]
            answer = client.get(JUPITER, params={"query": ",".join(some)}).raise_for_status()
            found = {
                t["id"]: {
                    "symbol": str(t.get("symbol") or "")[:20],
                    "name": str(t.get("name") or "")[:40],
                    "verified": t.get("isVerified") is True,
                }
                for t in answer.json()
                if isinstance(t, dict) and t.get("id") in some
            }
            for m in some:  # unknown mints are remembered too, so they are not asked again
                CACHE[m] = (now, found.get(m))
    except (httpx.HTTPError, ValueError, AttributeError):
        pass
    return {m: CACHE[m][1] for m in mints if m in CACHE and CACHE[m][1]}


def trust(chain, token, symbol, name="", listed="", famous=frozenset(), price=None, unasked=None):
    """{"level": verified | unknown | scam, "reason"} of one holding.

    `listed` names the list this exact contract is on, or is empty. `price` is its price row.
    `unasked` is True when the whole history shows the token arrived in transactions the
    wallet took no part in and was never sent on, False when it was, None when unknown.
    """
    if not token:
        return {"level": "verified", "reason": f"The native coin of {chains.name(chain)}."}
    if listed:
        return {"level": "verified", "reason": f"This exact contract is on {listed}."}
    sure = bool(price and price["usd"] and (price["confidence"] or 0) >= prices.CONFIDENT)
    if BAIT.search(f"{symbol} {name}") and not sure:
        reason = "Its name advertises a website or a reward: the usual bait of airdrop scams."
        return {"level": "scam", "reason": reason + " Never visit it or sign anything there."}
    twin = dressed(symbol, name, famous)
    if twin and not sure:
        like = "," if skeleton(twin) == skeleton(symbol) else f", like {twin},"
        reason = f"It calls itself {symbol}{like} but it is not the real one: no token "
        return {"level": "scam", "reason": reason + "list names it, and it has no reliable price."}
    if unasked and not sure:
        reason = "It arrived unasked, in transactions this wallet took no part in, was never "
        return {"level": "scam", "reason": reason + "moved since, and has no reliable price."}
    if twin:
        reason = f"It shares the name {symbol} with a listed token but is another contract; "
        return {"level": "unknown", "reason": reason + "it does have a market price."}
    if sure:
        return {"level": "unknown", "reason": "It has a market price, but is on no token list."}
    return {"level": "unknown", "reason": "It is on no token list and has no reliable price."}


def wanted(db, addresses):
    """Those of `addresses` that are my wallets or labelled as a service other than a mixer."""
    some, out = list(addresses), set()
    services = sorted(SERVICE - {"mixer"})
    for i in range(0, len(some), 400):
        part = some[i : i + 400]
        marks = ",".join("?" * len(part))
        out |= {
            r[0]
            for r in db.execute(
                f"SELECT address FROM node WHERE mine AND address IN ({marks}) UNION "
                f"SELECT address FROM label WHERE category IN ({','.join('?' * len(services))}) "
                f"AND address IN ({marks})",
                [*part, *services, *part],
            )
        }
    return out


def judge(db, client, price, held, transfers, address, complete, names=None, sent=()):
    """How far each token held can be trusted: {(chain, token): {"asset", "name", "trust"}}.
    `held` is {(chain, token): (asset, amount)}, `transfers` the address's transfer rows,
    `price` the cached prices by key, `complete` whether the whole history is loaded, `names`
    the tokens' full names where the indexer gave them, `sent` the transactions it sent.

    A token was not asked for when it arrived from strangers, in transactions this address
    took no part in, and was never moved: what my wallets or a known service (an exchange
    withdrawal) send was wanted."""
    names = names or {}
    took_part = {t[1] for t in transfers if t[2] == address} | set(sent)
    arrived, moved, senders = defaultdict(set), set(), defaultdict(set)
    for c, txid, src, dst, _, token, _, _ in transfers:
        if token and dst == address:
            arrived[(c, token)].add(txid)
            senders[(c, token)].add(src)
        if token and src == address:
            moved.add((c, token))
    known_senders = wanted(db, set().union(*senders.values()))
    listed = uniswap(client) if any(token for _, token in held) else {}
    famed = famous(listed)
    known = jupiter(client, [t for c, t in held if c == "solana" and t])
    out = {}
    for (c, token), (asset, _) in held.items():
        p = price.get(prices.key(c, asset, token))
        jup = known.get(token) if c == "solana" else None
        if token and c == "solana":  # Solana does not name its tokens on chain
            asset = (jup or {}).get("symbol") or (p and p["symbol"]) or asset
        name = (jup or {}).get("name") or names.get((c, token), "")
        on = "Uniswap's default token list" if listed_on(listed, c, token) else ""
        on = on or ("Jupiter's verified list" if (jup or {}).get("verified") else "")
        unasked = None  # the history is incomplete, or never showed the token arriving
        if complete and arrived[(c, token)]:
            unasked = (c, token) not in moved and not arrived[(c, token)] & took_part
            unasked = unasked and not senders[(c, token)] & known_senders
        level = trust(c, token, asset, name, on, famed, p, unasked)
        out[(c, token)] = {"asset": asset, "name": name, "trust": level}
    return out


def mark(db, client):
    """Judge every token my addresses hold, and keep the verdict with the balance, where the
    portfolio and the graph read it."""
    price, _ = prices.rates(db)
    rows = db.execute(
        "SELECT b.*, n.hub FROM balance b JOIN node n USING (address) WHERE n.mine"
    ).fetchall()
    for address in {r["address"] for r in rows}:
        own = [r for r in rows if r["address"] == address]
        held = {(r["chain"], r["token"]): (r["asset"], r["amount"]) for r in own}
        names = {(r["chain"], r["token"]): r["name"] for r in own if r["name"]}
        transfers = db.execute(
            "SELECT chain, txid, src, dst, asset, token, amount, ts FROM transfer "
            "WHERE ?1 IN (src, dst)",
            (address,),
        ).fetchall()
        sent = [r[0] for r in db.execute("SELECT txid FROM sent WHERE address = ?", (address,))]
        judged = judge(
            db, client, price, held, [tuple(t) for t in transfers], address,
            not own[0]["hub"], names, sent,
        )  # fmt: skip
        db.executemany(
            "UPDATE balance SET asset = ?, name = ?, trust = ?, reason = ? "
            "WHERE address = ? AND chain = ? AND token = ?",
            [
                (j["asset"], j["name"], j["trust"]["level"], j["trust"]["reason"], address, *key)
                for key, j in judged.items()
            ],
        )
    db.commit()
