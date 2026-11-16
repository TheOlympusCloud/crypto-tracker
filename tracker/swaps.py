"""Cross-chain swaps through THORChain and Maya Protocol, read from their public Midgard
APIs. A swap names its destination address in the open, so it ties the two ends together
for anyone who looks: stored as a 'swap' link with confidence 0.95.

Each of my addresses is sent to these APIs, like to any other indexer. CT_SWAPS=off skips it.
"""

import os

import httpx

from . import chains
from . import db as database
from .heuristics import pair

CONFIDENCE = 0.95  # a public protocol record, short only of proof that both ends are one person
SOURCES = {
    "THORChain": os.environ.get(
        "CT_THORCHAIN", "https://gateway.liquify.com/chain/thorchain_midgard"
    ).rstrip("/"),
    "Maya Protocol": os.environ.get("CT_MAYA", "https://midgard.mayachain.info").rstrip("/"),
}


def _address(raw):
    try:
        return database.norm(raw)
    except (ValueError, AttributeError):
        return None  # thor1.../maya1... and anything else that is not a chain we read


def _coins(leg):
    return ", ".join(
        f"{int(c['amount']) / 1e8:g} {c['asset'].split('-')[0]}" for c in leg.get("coins", [])
    )


def parse(actions, protocol):
    """(sender, recipient, evidence, inbound txid, outbound txid) per successful swap."""
    out = []
    for action in actions:
        try:
            if action.get("type") != "swap" or action.get("status") != "success":
                continue
            sent = action["in"][0]
            for got in action["out"]:
                a, b = _address(sent["address"]), _address(got["address"])
                if a and b and a != b and not got.get("affiliate"):
                    evidence = f"{protocol}: {_coins(sent)} > {_coins(got)} ({sent['txID'][:16]})"
                    out.append((a, b, evidence, sent["txID"], got.get("txID") or ""))
        except (KeyError, IndexError, ValueError, TypeError):
            continue
    return out


def sync(db, client):
    """Look up the swaps of every address of mine. Returns the sources that did not answer."""
    if os.environ.get("CT_SWAPS", "on") == "off":
        return []
    mine = [r["address"] for r in db.execute("SELECT address FROM node WHERE mine")]
    failed = []
    for protocol, url in SOURCES.items():
        try:
            for address in mine:
                bare = address.removeprefix("bitcoincash:")
                query = {"address": bare, "type": "swap", "limit": 50}
                data = chains.get(client, f"{url}/v2/actions", query).json()
                for a, b, evidence, tx_in, tx_out in parse(data.get("actions", []), protocol):
                    db.execute(
                        "INSERT OR REPLACE INTO link VALUES (?, ?, 'swap', ?, ?)",
                        (*pair(a, b), CONFIDENCE, evidence),
                    )
                    # The vault that took the money in, and the one that paid out, are the
                    # protocol's: label them so origins read "THORChain", not "unknown".
                    for txid, column, other, known in (
                        (tx_in, "dst", "src", a),
                        (tx_out, "src", "dst", b),
                    ):
                        db.execute(
                            f"""INSERT OR IGNORE INTO label
                                SELECT DISTINCT {column}, ?, 'dex', 'swap-protocol' FROM transfer
                                WHERE {other} = ? AND lower(txid) IN (lower(?), '0x' || lower(?))
                                  AND {column} NOT IN (SELECT address FROM node WHERE mine)""",
                            (protocol, known, txid, txid),
                        )
        except (httpx.HTTPError, RuntimeError, ValueError) as e:
            failed.append(f"{protocol}: {chains.describe(e)}")  # one source down, try the other
    db.commit()
    return failed
