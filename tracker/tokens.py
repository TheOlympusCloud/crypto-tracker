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

import httpx

from . import chains, prices

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
    """(chain id, contract) -> symbol of a token list; EVM contracts in lower case."""
    out = {}
    for t in data.get("tokens", []):
        try:
            address = str(t["address"])
            key = address.lower() if address.startswith("0x") else address
            out[(int(t["chainId"]), key)] = str(t["symbol"])[:20]
        except (KeyError, ValueError, TypeError):
            continue
    return out


def uniswap(client):
    """Uniswap's default list, or {} while it cannot be fetched: then nothing is verified,
    which errs on the side of caution."""
    try:
        return _cached(UNISWAP, lambda: parse_list(client.get(UNISWAP).raise_for_status().json()))
    except (httpx.HTTPError, ValueError, AttributeError):
        return {}


def listed_on(listed, chain, token):
    chain_id = SOLANA_ID if chain == "solana" else chains.EVM.get(chain, (None,))[0]
    return (chain_id, token) in listed


def famous(listed):
    """Symbols worth imitating: those of listed tokens on any chain, and the native coins."""
    return {skeleton(s) for s in listed.values()} | set(prices.NATIVE)


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
    twin = skeleton(symbol) in famous
    if twin and not sure:
        reason = f"It calls itself {symbol}, but it is not the real {skeleton(symbol)}: no token "
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
