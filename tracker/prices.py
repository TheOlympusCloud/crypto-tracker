"""Fiat values from DefiLlama's free price API, cached. Only asset identifiers are sent
(token contracts and coin names), never addresses or amounts. CT_PRICES=off disables it."""

import os
import time
from collections import defaultdict

import httpx

from . import chains
from .heuristics import TRUSTED

LLAMA = "https://coins.llama.fi/prices/current/"
FX = "https://api.frankfurter.app/latest"  # ECB reference rates
TTL = 600
NATIVE = {
    "BTC": "coingecko:bitcoin",
    "ETH": "coingecko:ethereum",
    "POL": "coingecko:polygon-ecosystem-token",
    "BNB": "coingecko:binancecoin",
    "AVAX": "coingecko:avalanche-2",
    "XDAI": "coingecko:xdai",
    "LTC": "coingecko:litecoin",
    "DOGE": "coingecko:dogecoin",
    "BCH": "coingecko:bitcoin-cash",
    "SOL": "coingecko:solana",
    "TRX": "coingecko:tron",
}
# A token shows in the portfolio when one of my wallets has sent it, or when the price
# source knows it with high confidence. Spam airdrops fail both.
CONFIDENT = 0.9


def currency():
    return os.environ.get("CT_CURRENCY", "USD").upper()


def key(chain, asset, token):
    if not token:
        return NATIVE.get(asset.upper())
    return f"{chains.evm_chains().get(chain, {}).get('llama', chain)}:{token}"


def _holdings(db, everything=False):
    """My balances. Tokens nobody vouches for are left out unless `everything`."""
    known = {
        r["key"]: r for r in db.execute("SELECT * FROM price WHERE confidence >= ?", (CONFIDENT,))
    }
    trusted = {
        r["token"] for r in db.execute(f"SELECT DISTINCT token FROM transfer WHERE {TRUSTED}")
    }
    rows = db.execute(
        """SELECT b.address, b.chain, b.asset, b.token, b.amount, n.name, n.public, n.wallet
           FROM balance b JOIN node n ON n.address = b.address WHERE n.mine ORDER BY b.amount DESC"""
    ).fetchall()
    out = []
    for r in map(dict, rows):
        price = known.get(key(r["chain"], r["asset"], r["token"]))
        if price and r["token"] and r["chain"] == "solana":
            r["asset"] = price["symbol"] or r["asset"]  # Solana does not name its tokens on chain
        if everything or not r["token"] or r["token"] in trusted or price:
            out.append(r)
    return out


def refresh(db, client, extra=()):
    """Fetch prices older than TTL, for what I hold and the `extra` keys. A failure keeps
    the old prices: values go stale, not away."""
    if os.environ.get("CT_PRICES", "on") == "off":
        return
    held = _holdings(db, everything=True)
    wanted = ({key(r["chain"], r["asset"], r["token"]) for r in held} | set(extra)) - {None}
    if currency() != "USD":
        wanted.add(f"fx:{currency()}")
    fresh = {
        r["key"] for r in db.execute("SELECT key FROM price WHERE ts > ?", (time.time() - TTL,))
    }
    stale = sorted(wanted - fresh)
    now = int(time.time())
    try:
        coins = [k for k in stale if not k.startswith("fx:")]
        for i in range(0, len(coins), 80):
            data = client.get(LLAMA + ",".join(coins[i : i + 80])).raise_for_status().json()
            found = data.get("coins", {})
            db.executemany(
                "INSERT OR REPLACE INTO price VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        k,
                        float(v["price"]),
                        now,
                        str(v.get("symbol") or "")[:20],
                        v.get("confidence"),
                    )
                    for k, v in found.items()
                ]
                # Remember the unknown ones too, or every refresh would ask for them again.
                + [(k, None, now, "", 0) for k in coins[i : i + 80] if k not in found],
            )
        if f"fx:{currency()}" in stale:
            data = client.get(FX, params={"from": "USD", "to": currency()}, follow_redirects=True)
            rate = float(data.raise_for_status().json()["rates"][currency()])
            db.execute(
                "INSERT OR REPLACE INTO price VALUES (?, ?, ?, '', NULL)",
                (f"fx:{currency()}", rate, now),
            )
    except (httpx.HTTPError, KeyError, ValueError, TypeError):
        pass
    db.commit()


def rates(db):
    """The cached prices by key, and the factor from USD to the display currency (None
    while that rate is unknown)."""
    price = {r["key"]: r for r in db.execute("SELECT * FROM price WHERE usd IS NOT NULL")}
    fx = 1.0 if currency() == "USD" else (price.get(f"fx:{currency()}") or {"usd": None})["usd"]
    return price, fx


def portfolio(db):
    """Everything I hold, per asset and per wallet, valued with the cached prices."""
    price, fx = rates(db)

    def unit(r):
        p = price.get(key(r["chain"], r["asset"], r["token"]))
        return p["usd"] * fx if p and fx else None

    total = lambda values: sum(v for v in values if v is not None)
    assets, wallets = {}, {}
    for w in db.execute("SELECT * FROM hdwallet"):
        wallets[w["id"]] = {
            "id": w["id"], "name": w["name"], "public": bool(w["public"]), "kind": "hd",
            "addresses": [], "balances": defaultdict(lambda: {"amount": 0.0, "value": None}),
        }  # fmt: skip
    for r in db.execute(
        "SELECT address, name, public, wallet FROM node WHERE mine ORDER BY address"
    ):
        w = wallets.setdefault(
            r["wallet"] or r["address"],
            {
                "id": r["address"], "name": r["name"], "public": bool(r["public"]),
                "kind": "address", "addresses": [],
                "balances": defaultdict(lambda: {"amount": 0.0, "value": None}),
            },
        )  # fmt: skip
        w["addresses"].append(r["address"])
    for r in _holdings(db):
        v = None if unit(r) is None else r["amount"] * unit(r)
        side = "public" if r["public"] else "private"
        # A token that merely calls itself AVAX or USDC must not be added to the real one:
        # only the native coin and tokens the price source knows count as that asset.
        verified = unit(r) is not None
        a = assets.setdefault(
            (r["asset"].upper(), verified),
            {"asset": r["asset"], "verified": verified, "chains": set(), "amount": 0.0,
             "private": 0.0, "public": 0.0,
             "price": unit(r), "value": None, "private_value": None, "public_value": None},
        )  # fmt: skip
        a["chains"].add(r["chain"])
        a["amount"] += r["amount"]
        a[side] += r["amount"]
        b = wallets[r["wallet"] or r["address"]]["balances"][(r["chain"], r["asset"], r["token"])]
        b["amount"] += r["amount"]
        if v is not None:
            a["value"] = (a["value"] or 0) + v
            a[f"{side}_value"] = (a[f"{side}_value"] or 0) + v
            b["value"] = (b["value"] or 0) + v
    for a in assets.values():
        a["chains"] = sorted(a["chains"])
    for w in wallets.values():
        w["balances"] = [
            {"chain": c, "asset": s, "token": t, **b} for (c, s, t), b in w["balances"].items()
        ]
        w["value"] = total(b["value"] for b in w["balances"])
    stamps = [p["ts"] for k, p in price.items() if not k.startswith("fx:")]
    return {
        "currency": currency() if fx else "USD",
        "priced_at": min(stamps) if stamps else None,
        "total": total(a["value"] for a in assets.values()),
        "private": total(a["private_value"] for a in assets.values()),
        "public": total(a["public_value"] for a in assets.values()),
        # Assets without a price are in the lists but not in the totals.
        "unpriced": sorted(a["asset"] for a in assets.values() if a["value"] is None),
        "assets": sorted(assets.values(), key=lambda a: -(a["value"] or 0)),
        "wallets": sorted(wallets.values(), key=lambda w: -w["value"]),
        "history": [dict(r) for r in db.execute("SELECT * FROM snapshot ORDER BY ts")],
    }


def snapshot(db, client):
    """Record today's totals, so the portfolio has a value history from the first sync on."""
    refresh(db, client)
    p = portfolio(db)
    if p["total"]:
        db.execute(
            "INSERT OR REPLACE INTO snapshot VALUES (?, ?, ?, ?)",
            (int(time.time()), p["private"], p["public"], p["currency"]),
        )
        db.commit()


def summary(db):
    """The portfolio's values in the compact form the pages use: totals, value per asset
    symbol, value per wallet (keyed by the wallet's first address). None before any price
    is known, so the pages show no value rather than a false zero."""
    p = portfolio(db)
    if p["priced_at"] is None:
        return None
    return {
        "currency": p["currency"],
        "total": p["total"],
        "private": p["private"],
        "public": p["public"],
        "unpriced": p["unpriced"],
        "priced_at": p["priced_at"],
        "assets": {a["asset"]: a["value"] for a in p["assets"] if a["verified"]},
        # A wallet none of whose addresses is known has no value to show, not a value of zero.
        "wallets": {
            (w["addresses"] or [w["id"]])[0]: w["value"] if w["addresses"] else None
            for w in p["wallets"]
        },
    }
