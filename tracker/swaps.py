"""Cross-chain swaps through THORChain and Maya Protocol, read from their public Midgard
APIs. A swap names its destination address in the open, so it ties the two ends together
for anyone who looks: stored as a 'swap' link with confidence 0.95.

Each of my addresses is sent to these APIs, like to any other indexer. CT_SWAPS=off skips it.
"""

import os
import time

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


def vaults(db, sender, tx_in, recipient, tx_out):
    """The protocol's vault addresses in one swap, so origins can read "THORChain" instead
    of "unknown".

    The vault that paid out is the sender of the outbound transaction. The vault that took
    the money in is a destination of the inbound one, but a UTXO transaction also pays
    change, possibly to an address of mine that is not registered: so the inbound vault is
    only labelled when the sender paid a single address that is not mine.
    """
    theirs = "NOT IN (SELECT address FROM node WHERE mine)"
    same_tx = "lower(txid) IN (lower(?), '0x' || lower(?))"
    paid = [
        r[0]
        for r in db.execute(
            f"SELECT DISTINCT dst FROM transfer WHERE src = ? AND {same_tx} AND dst {theirs}",
            (sender, tx_in, tx_in),
        )
    ]
    return (paid if len(paid) == 1 else []) + [
        r[0]
        for r in db.execute(
            f"SELECT DISTINCT src FROM transfer WHERE dst = ? AND {same_tx} AND src {theirs}",
            (recipient, tx_out, tx_out),
        )
    ]


NOTE = "Swaps: only the 50 newest THORChain and Maya swaps of each address are read."


def unchecked(db):
    """Swap sources that did not answer at the last sync, as text for a verdict."""
    if os.environ.get("CT_SWAPS", "on") == "off":
        return []
    return [
        f"{r['source']} did not answer ({r['error']}), so swaps through it were not checked"
        for r in db.execute("SELECT * FROM failure ORDER BY source")
    ]


def sync(db, client):
    """Look up the swaps of every address of mine. Returns the sources that did not answer."""
    if os.environ.get("CT_SWAPS", "on") == "off":
        return []
    mine = [r["address"] for r in db.execute("SELECT address FROM node WHERE mine")]
    failed = []
    for protocol, url in SOURCES.items():
        found = set()
        try:
            for address in mine:
                bare = address.removeprefix("bitcoincash:")
                query = {"address": bare, "type": "swap", "limit": 50}
                data = chains.get(client, f"{url}/v2/actions", query).json()
                for a, b, evidence, tx_in, tx_out in parse(data.get("actions") or [], protocol):
                    db.execute(
                        "INSERT OR REPLACE INTO link VALUES (?, ?, 'swap', ?, ?)",
                        (*pair(a, b), CONFIDENCE, evidence),
                    )
                    found.update(vaults(db, a, tx_in, b, tx_out))
            # Vault labels are derived data, rebuilt so that a wrong one cannot linger. But
            # only once this protocol has answered for every address: if it is down, its
            # vaults keep their labels, or they would turn into strangers that link wallets.
            db.execute("DELETE FROM label WHERE source = 'swap-protocol' AND name = ?", (protocol,))
            db.executemany(
                "INSERT OR IGNORE INTO label(address, name, category, source) VALUES (?, ?, 'dex', 'swap-protocol')",
                [(v, protocol) for v in found],
            )
            db.execute("DELETE FROM failure WHERE source = ?", (protocol,))
        except Exception as e:  # noqa: BLE001 - whatever one source sends, the other and the sync go on
            reason = (
                chains.describe(e) if isinstance(e, httpx.HTTPError | RuntimeError) else "bad reply"
            )
            failed.append(f"{protocol}: {reason}")
            # Stored, not only reported: until this source answers again no wallet has
            # been fully checked, and its verdict must say so (see graph.verdicts).
            db.execute(
                "INSERT OR REPLACE INTO failure VALUES (?, ?, ?)",
                (protocol, reason, int(time.time())),
            )
    db.commit()
    return failed
