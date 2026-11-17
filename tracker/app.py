import os
import secrets
import time
from base64 import b64decode
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from . import alerts, chains, graph, hd, lookup, prices, sync, uri
from . import db as database

app = FastAPI(title="Crypto tracker", docs_url=None, redoc_url=None)
CATEGORIES = {"cex", "dex", "bridge", "mixer", "defi", "service", "other"}


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    password = os.environ.get("CT_PASSWORD")
    if password:
        try:
            scheme, _, token = request.headers.get("authorization", "").partition(" ")
            given = b64decode(token).decode().partition(":")[2] if scheme == "Basic" else ""
        except ValueError:
            given = ""
        if not secrets.compare_digest(given.encode(), password.encode()):
            return Response(status_code=401, headers={"WWW-Authenticate": "Basic"})
    elif request.url.hostname not in ("127.0.0.1", "localhost", "::1"):
        # Without a password only loopback names are served, so a hostile web page cannot
        # rebind its own domain to this port and read the wallet list.
        return Response("Set CT_PASSWORD to use this host name.", status_code=403)
    return await call_next(request)


class Wallet(BaseModel):
    address: str
    name: str = ""
    public: bool = False

    @field_validator("name")
    @classmethod
    def tidy(cls, name):
        """A name is one line of at most 80 characters: line breaks, tabs and runs of
        spaces become single spaces. Posting a wallet again with another name renames it."""
        return " ".join(name.split())[:80]


class Label(BaseModel):
    address: str
    name: str = ""
    category: str = "other"


class Pasted(BaseModel):
    q: str


class Sync(BaseModel):
    addresses: list[str] = []  # empty: all my wallets
    depth: int = 0
    labels: bool = False


def address(raw):
    try:
        return database.norm(raw)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None


@app.get("/api/graph")
def get_graph(spam: bool = False):
    db = database.connect()
    try:
        return graph.build(db, spam)
    finally:
        db.close()


@app.get("/api/analysis")
def get_analysis(max_hops: int = 4):
    max_hops = max(1, min(max_hops, 6))
    db = database.connect()
    try:
        return {
            "max_hops": max_hops,
            "hop_decay": graph.HOP_DECAY,
            "chains": chains.coverage(),
            "fiat": prices.summary(db),
            "alerts": alerts.recent(db),
            "watched": [
                dict(r)
                for r in db.execute(
                    "SELECT w.address, l.name label, l.category FROM watch w "
                    "LEFT JOIN label l USING (address) ORDER BY w.ts DESC"
                )
            ],
            "verdicts": graph.verdicts(db, max_hops),
            "origins": graph.origins(db, max_hops),
        }
    finally:
        db.close()


def write(sql, params):
    db = database.connect()
    try:
        with db:
            db.execute(sql, params)
    finally:
        db.close()


@app.post("/api/wallets")
def put_wallet(w: Wallet):
    """Add or update a wallet: a single address, or an xpub/ypub/zpub/descriptor whose
    addresses are discovered on the next sync."""
    if w.address.startswith("hd:"):  # an HD wallet by its id, e.g. one with no address found yet
        db = database.connect()
        try:
            with db:
                values = (int(w.public), w.name[:80], w.address)
                done = db.execute("UPDATE hdwallet SET public = ?, name = ? WHERE id = ?", values)
                db.execute("UPDATE node SET public = ?, name = ? WHERE wallet = ?", values)
        finally:
            db.close()
        if not done.rowcount:
            raise HTTPException(404, "no such HD wallet")
        return {"ok": True, "wallet": w.address}
    if hd.looks_like(w.address):
        try:
            hd.descriptors(w.address)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        write(
            "INSERT INTO hdwallet(id, name, public, descriptor) VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
            "name = excluded.name, public = excluded.public",
            (hd.wallet_id(w.address), w.name[:80], int(w.public), w.address.strip()),
        )
        return {"ok": True, "wallet": hd.wallet_id(w.address)}
    one = address(w.address)
    db = database.connect()
    try:
        with db:
            row = db.execute("SELECT wallet FROM node WHERE address = ?", (one,)).fetchone()
            if row and row["wallet"]:  # an address of an HD wallet speaks for the whole wallet
                values = (int(w.public), w.name[:80], row["wallet"])
                db.execute("UPDATE hdwallet SET public = ?, name = ? WHERE id = ?", values)
                db.execute("UPDATE node SET public = ?, name = ? WHERE wallet = ?", values)
            else:
                db.execute(
                    "INSERT INTO node(address, mine, public, name) VALUES (?, 1, ?, ?) "
                    "ON CONFLICT(address) DO UPDATE SET mine = 1, public = excluded.public, "
                    "name = excluded.name",
                    (one, int(w.public), w.name[:80]),
                )
            db.execute("DELETE FROM watch WHERE address = ?", (one,))  # tracked now, not watched
    finally:
        db.close()
    return {"ok": True, "wallet": one}


@app.delete("/api/wallets/{raw}")
def delete_wallet(raw: str):
    """Stop treating the wallet as mine. Its transfers stay: they are public facts."""
    forget = "UPDATE node SET mine = 0, public = 0, name = '', wallet = NULL WHERE "
    if raw.startswith("hd:"):
        write("DELETE FROM hdwallet WHERE id = ?", (raw,))
        write(forget + "wallet = ?", (raw,))
    else:
        write(forget + "address = ? AND wallet IS NULL", (address(raw),))
    return {"ok": True}


@app.post("/api/resolve")
def resolve(pasted: Pasted):
    """What a pasted line or a scanned QR code names. A POST, so that a secret scanned by
    mistake does not end up in an access log."""
    try:
        one, chain, hint = uri.parse(pasted.q[:500])
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    readable = [c for c, cfg in chains.evm_chains().items() if not cfg.get("needs_key")]
    return {
        "address": one,
        "chain": chain,
        "chains": readable if chain == "evm" else [],  # the choice to offer for an EVM address
        # The EVM chain a payment URI names. One this server cannot read is not a hint:
        # the chains it can read are offered instead, and the missing one is named.
        "hint": hint if hint in readable else None,
        "needs_key": hint if hint and hint not in readable else None,
    }


@app.get("/api/lookup/{raw}")
def get_lookup(raw: str, only: str = Query("", alias="chains")):
    """Read-only view of any address: assets, counterparties and links to my wallets."""
    one = address(raw)
    picked = [c for c in only.split(",") if c]
    for c in picked:
        if chains.evm_chains().get(c, {"needs_key": True}).get("needs_key"):
            raise HTTPException(422, f"{c} is not a chain this server can read")
    db = database.connect()
    try:
        with httpx.Client(timeout=30, headers={"User-Agent": "crypto-tracker"}) as client:
            return lookup.view(db, client, one, picked)
    except (httpx.HTTPError, RuntimeError, ValueError, KeyError, TypeError) as e:
        raise HTTPException(502, f"the indexer did not answer: {chains.describe(e)}") from None
    finally:
        db.close()


@app.post("/api/watch")
def put_watch(w: Wallet):
    """Keep an address at hand without calling it mine: it stays out of every verdict."""
    write("INSERT OR IGNORE INTO watch VALUES (?, ?)", (address(w.address), int(time.time())))
    return {"ok": True}


@app.delete("/api/watch/{raw}")
def delete_watch(raw: str):
    write("DELETE FROM watch WHERE address = ?", (address(raw),))
    return {"ok": True}


@app.get("/api/portfolio")
def get_portfolio():
    db = database.connect()
    try:
        with httpx.Client(timeout=8, headers={"User-Agent": "crypto-tracker"}) as client:
            prices.refresh(db, client)
        return prices.portfolio(db)
    finally:
        db.close()


@app.get("/api/alerts")
def get_alerts():
    db = database.connect()
    try:
        return alerts.recent(db)
    finally:
        db.close()


@app.post("/api/alerts/seen")
def seen_alerts():
    write("UPDATE alert SET seen = 1 WHERE NOT seen", ())
    return {"ok": True}


@app.post("/api/labels")
def put_label(label: Label):
    if label.category not in CATEGORIES:
        raise HTTPException(422, f"category must be one of {sorted(CATEGORIES)}")
    if label.name:
        write(
            "INSERT OR REPLACE INTO label VALUES (?, ?, ?, 'user')",
            (address(label.address), label.name[:80], label.category),
        )
    else:
        write("DELETE FROM label WHERE address = ?", (address(label.address),))
    return {"ok": True}


@app.post("/api/sync")
def post_sync(req: Sync):
    # An "hd:" id rescans that HD wallet; anything else must be an address.
    wallets = [a for a in req.addresses if a.startswith("hd:")]
    addresses = [address(a) for a in req.addresses if not a.startswith("hd:")]
    if wallets:
        db = database.connect()
        known = {r["id"] for r in db.execute("SELECT id FROM hdwallet")}
        db.close()
        if not set(wallets) <= known:
            raise HTTPException(404, "no such HD wallet")
    everything = not wallets and not addresses
    depth = max(0, min(req.depth, 3))
    if not sync.start(None if everything else addresses, depth, req.labels, wallets):
        raise HTTPException(409, "a sync is already running")
    return sync.status


@app.get("/api/sync")
def get_sync():
    return sync.status


if os.environ.get("CT_SYNC_EVERY"):  # minutes
    sync.every(float(os.environ["CT_SYNC_EVERY"]))

app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True))
