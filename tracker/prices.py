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
}


def currency():
    return os.environ.get("CT_CURRENCY", "USD").upper()


def key(chain, asset, token):
    if not token:
        return NATIVE.get(asset.upper())
    return f"{chains.evm_chains().get(chain, {}).get('llama', chain)}:{token}"


def _holdings(db):
    return db.execute(
        f"""SELECT b.address, b.chain, b.asset, b.token, b.amount, n.name, n.public, n.wallet
            FROM balance b JOIN node n ON n.address = b.address
            WHERE n.mine AND {TRUSTED}
            ORDER BY b.amount DESC"""
    ).fetchall()


def refresh(db, client):
    """Fetch prices older than TTL. A failure keeps the old prices: values go stale, not away."""
    if os.environ.get("CT_PRICES", "on") == "off":
        return
    wanted = {key(r["chain"], r["asset"], r["token"]) for r in _holdings(db)} - {None}
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
            db.executemany(
                "INSERT OR REPLACE INTO price VALUES (?, ?, ?)",
                [(k, float(v["price"]), now) for k, v in data.get("coins", {}).items()],
            )
        if f"fx:{currency()}" in stale:
            data = client.get(FX, params={"from": "USD", "to": currency()}, follow_redirects=True)
            rate = float(data.raise_for_status().json()["rates"][currency()])
            db.execute(
                "INSERT OR REPLACE INTO price VALUES (?, ?, ?)", (f"fx:{currency()}", rate, now)
            )
    except (httpx.HTTPError, KeyError, ValueError, TypeError):
        pass
    db.commit()


def portfolio(db):
    """Everything I hold, per asset and per wallet, valued with the cached prices."""
    price = {r["key"]: r for r in db.execute("SELECT * FROM price")}
    fx = 1.0 if currency() == "USD" else (price.get(f"fx:{currency()}") or {"usd": None})["usd"]

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
        a = assets.setdefault(
            r["asset"],
            {"asset": r["asset"], "chains": set(), "amount": 0.0, "private": 0.0, "public": 0.0,
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
