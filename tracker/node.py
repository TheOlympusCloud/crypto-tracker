"""Nodes of your own: the local stacks compose.yaml can start next to the app (its profiles),
or one on your network. For now Bitcoin's: a mempool on bitcoind and Fulcrum.

While such a node is not ready, the app waits for it and says so: it never falls back to a
public server, which would see your addresses exactly when you set up a node to hide them.
"""

import os
import re
import time
from datetime import UTC, datetime

import httpx

# COMPOSE_PROFILES, passed on by compose.yaml: the local stacks running next to the app.
PROFILES = {p.strip() for p in os.environ.get("CT_PROFILES", "").split(",") if p.strip()}


def local(profile, url):
    """`url` when compose.yaml runs `profile` next to the app, else "". The hook for local nodes:
    a chain's default URL is `CT_<CHAIN> or local(<profile>, <its service>) or <public URL>`."""
    return url if profile in PROFILES else ""


# A mempool instance (its web address; /api below it): set by hand for one on your network,
# or the one of the `mempool` profile.
MEMPOOL = (os.environ.get("CT_MEMPOOL") or local("mempool", "http://mempool-web:8080")).rstrip("/")
PORT = os.environ.get("CT_MEMPOOL_PORT", "")  # where the profile's mempool site is published
# Its site as the browser opens it: one on your network as it is; the profile's through a
# redirect to its port on the host the browser came by (app.py).
SITE = os.environ.get("CT_MEMPOOL", "").rstrip("/") or (MEMPOOL and "/api/tracker/mempool")

STALE = 6 * 3600  # a chain tip older than this: the node is still catching up
PROBE = "bc1q432elxuhwenlg70f2cgms9fa0p09g239rj9z9d"  # unused: the index answers at once
TTL = 30  # seconds a status is reused
_last = {"at": 0.0, "status": None}

WAITS = "Bitcoin addresses are read once it is ready, never from a public server meanwhile."
NOTICES = {
    "down": "Bitcoin: your own node does not answer. It starts after bitcoind and Fulcrum, or "
    "it is stopped (docker compose ps).",
    "syncing": "Bitcoin: your own node is still syncing; it has the chain up to {date} "
    "(block {height:,}).",
    "indexing": "Bitcoin: your own node has the whole chain; its address index (Fulcrum) is "
    "still being built, which takes hours.",
}


class NotReady(RuntimeError):
    pass


def status():
    """Where the mempool in MEMPOOL stands: {"state": "ready" | "syncing" | "indexing" | "down",
    "height", "time"}, or None without one. Asked from its own API, so a mempool on your
    network is judged the same way as the local one."""
    if not MEMPOOL:
        return None
    if _last["at"] > time.time() - TTL:
        return _last["status"]
    found = {"state": "down", "height": None, "time": None}
    with httpx.Client(timeout=5, headers={"User-Agent": "crypto-tracker"}) as client:
        try:
            tip = client.get(f"{MEMPOOL}/api/blocks/tip/hash").raise_for_status().text.strip()
            if not re.fullmatch(r"[0-9a-f]{64}", tip):
                raise ValueError("not a block hash")
            block = client.get(f"{MEMPOOL}/api/block/{tip}").raise_for_status().json()
            found |= {"height": int(block["height"]), "time": int(block["timestamp"])}
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            pass
        else:
            if found["time"] < time.time() - STALE:
                found["state"] = "syncing"
            else:
                try:
                    client.get(f"{MEMPOOL}/api/address/{PROBE}").raise_for_status()
                    found["state"] = "ready"
                except httpx.HTTPError:
                    found["state"] = "indexing"
    _last.update(at=time.time(), status=found)
    return found


def notice(found):
    """What to tell the user about a node that is not ready."""
    date = datetime.fromtimestamp(found["time"] or 0, UTC).strftime("%d %b %Y")
    return NOTICES[found["state"]].format(date=date, height=found["height"] or 0) + " " + WAITS


def notes():
    """The notice, when there is a node and it is not ready."""
    found = status()
    return [notice(found)] if found and found["state"] != "ready" else []


def guard(url):
    """Before reading Bitcoin from MEMPOOL: stop with the notice while it is not ready."""
    if MEMPOOL and url == f"{MEMPOOL}/api" and (found := status())["state"] != "ready":
        raise NotReady(notice(found))
