import os
import secrets
from base64 import b64decode
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db as database
from . import graph, sync

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
    return await call_next(request)


class Wallet(BaseModel):
    address: str
    name: str = ""
    public: bool = False


class Label(BaseModel):
    address: str
    name: str = ""
    category: str = "other"


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


@app.get("/api/trace")
def get_trace(max_hops: int = 4):
    db = database.connect()
    try:
        return graph.trace(db, max(1, min(max_hops, 6)))
    finally:
        db.close()


@app.post("/api/wallets")
def put_wallet(w: Wallet):
    db = database.connect()
    with db:
        db.execute(
            "INSERT INTO node(address, mine, public, name) VALUES (?, 1, ?, ?) "
            "ON CONFLICT(address) DO UPDATE SET mine = 1, public = excluded.public, "
            "name = excluded.name",
            (address(w.address), int(w.public), w.name[:80]),
        )
    db.close()
    return {"ok": True}


@app.delete("/api/wallets/{raw}")
def delete_wallet(raw: str):
    """Stop treating the address as mine. Its transfers stay: they are public facts."""
    db = database.connect()
    with db:
        db.execute(
            "UPDATE node SET mine = 0, public = 0, name = '' WHERE address = ?", (address(raw),)
        )
    db.close()
    return {"ok": True}


@app.post("/api/labels")
def put_label(label: Label):
    if label.category not in CATEGORIES:
        raise HTTPException(422, f"category must be one of {sorted(CATEGORIES)}")
    db = database.connect()
    with db:
        if label.name:
            db.execute(
                "INSERT OR REPLACE INTO label VALUES (?, ?, ?, 'user')",
                (address(label.address), label.name[:80], label.category),
            )
        else:
            db.execute("DELETE FROM label WHERE address = ?", (address(label.address),))
    db.close()
    return {"ok": True}


@app.post("/api/sync")
def post_sync(req: Sync):
    addresses = [address(a) for a in req.addresses]
    if not addresses:
        db = database.connect()
        addresses = [r["address"] for r in db.execute("SELECT address FROM node WHERE mine")]
        db.close()
    if not sync.start(addresses, max(0, min(req.depth, 3)), req.labels):
        raise HTTPException(409, "a sync is already running")
    return sync.status


@app.get("/api/sync")
def get_sync():
    return sync.status


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True))
