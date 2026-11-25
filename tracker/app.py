import hmac
import os
import secrets
import time
from base64 import b64decode

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator
from starlette.concurrency import run_in_threadpool
from starlette.routing import Mount

from . import alerts, chains, exposure, graph, hd, lookup, node, prices, sync, uri
from . import db as database

app = FastAPI(title="Crypto tracker", docs_url=None, redoc_url=None)
CATEGORIES = {"cex", "dex", "bridge", "mixer", "flagged", "defi", "service", "other"}


SESSION = "ct_session"
SESSION_DAYS = 400  # the longest a browser keeps a cookie


SECRETS = {}  # database path -> the random secret its install keys session cookies with


def session(password):
    """The session cookie's value: the password's HMAC under a secret made once per install,
    so the cookie tells nothing about the password, and a new password ends every session."""
    path = os.environ.get("CT_DB", "tracker.db")
    if path not in SECRETS:
        db = database.connect()
        try:
            with db:
                db.execute(
                    "INSERT OR IGNORE INTO meta VALUES ('session_secret', ?)",
                    (secrets.token_hex(32),),
                )
            row = db.execute("SELECT value FROM meta WHERE key = 'session_secret'").fetchone()
            SECRETS[path] = bytes.fromhex(row[0])
        finally:
            db.close()
    return hmac.new(SECRETS[path], password.encode(), "sha256").hexdigest()


@app.middleware("http")
async def revalidate(request: Request, call_next):
    """The page's files are rechecked on every load: without this a browser guesses how long
    to keep them, and an installed app runs the old code for days after an update."""
    response = await call_next(request)
    if "/api/" not in request.url.path:
        response.headers.setdefault("cache-control", "no-cache")
    return response


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    password = os.environ.get("CT_PASSWORD")
    if password:
        token = session(password)
        if secrets.compare_digest(request.cookies.get(SESSION, "").encode(), token.encode()):
            return await call_next(request)
        try:
            scheme, _, given = request.headers.get("authorization", "").partition(" ")
            given = b64decode(given).decode().partition(":")[2] if scheme == "Basic" else ""
        except ValueError:
            given = ""
        if not secrets.compare_digest(given.encode(), password.encode()):
            return Response(status_code=401, headers={"WWW-Authenticate": "Basic"})
        # Home-screen apps and WebViews forget HTTP Basic when they close: a cookie they keep.
        response = await call_next(request)
        tls = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        response.set_cookie(
            SESSION, token, max_age=SESSION_DAYS * 86400, path="/", secure=tls,
            httponly=True, samesite="lax",
        )  # fmt: skip
        return response
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


@app.get("/graph")
def get_graph(spam: bool = False):
    db = database.connect()
    try:
        # Sent as it is: FastAPI's encoder takes seconds over tens of thousands of edges.
        return JSONResponse(graph.build(db, spam))
    finally:
        db.close()


@app.get("/analysis")
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
                {**r, "chain": database.chain_of(r["address"])}
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


@app.post("/wallets")
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


@app.delete("/wallets/{raw}")
def delete_wallet(raw: str):
    """Stop treating the wallet as mine. Its transfers stay: they are public facts."""
    # hub too: a wallet still filling in its history must not be taken for a busy service.
    forget = "UPDATE node SET mine = 0, public = 0, name = '', wallet = NULL, hub = 0 WHERE "
    if raw.startswith("hd:"):
        write("DELETE FROM hdwallet WHERE id = ?", (raw,))
        write(forget + "wallet = ?", (raw,))
    else:
        write(forget + "address = ? AND wallet IS NULL", (address(raw),))
    return {"ok": True}


@app.post("/resolve")
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
        "icon": chains.icon(chain, [hint] if hint in readable else []),
    }


def readable(only):
    """The EVM chains a ?chains= list names, each one this server reads."""
    picked = [c for c in only.split(",") if c]
    for c in picked:
        if chains.evm_chains().get(c, {"needs_key": True}).get("needs_key"):
            raise HTTPException(422, f"{c} is not a chain this server can read")
    return picked


def live(read, *args):
    """`read(db, client, *args)`, answered from the indexers."""
    db = database.connect()
    try:
        with httpx.Client(timeout=30, headers={"User-Agent": "crypto-tracker"}) as client:
            return read(db, client, *args)
    except (httpx.HTTPError, RuntimeError, ValueError, KeyError, TypeError) as e:
        raise HTTPException(502, f"the indexer did not answer: {chains.describe(e)}") from None
    finally:
        db.close()


@app.get("/lookup/{raw}")
def get_lookup(raw: str, only: str = Query("", alias="chains")):
    """Read-only view of any address: assets, counterparties and links to my wallets."""
    one = address(raw)
    return live(lookup.view, one, readable(only))


@app.get("/exposure/{raw}")
def get_exposure(raw: str, only: str = Query("", alias="chains")):
    """Privacy scan of any EVM, Solana or Tron address: findings, score and grade. Like a
    lookup it stores nothing. Bitcoin addresses are scanned by am-i-exposed in the browser."""
    one = address(raw)
    if database.chain_of(one) not in exposure.FAMILIES:
        raise HTTPException(422, "only EVM, Solana and Tron addresses are scanned here")
    return live(exposure.run, one, readable(only))


@app.post("/watch")
def put_watch(w: Wallet):
    """Keep an address at hand without calling it mine: it stays out of every verdict."""
    write("INSERT OR IGNORE INTO watch VALUES (?, ?)", (address(w.address), int(time.time())))
    return {"ok": True}


@app.delete("/watch/{raw}")
def delete_watch(raw: str):
    write("DELETE FROM watch WHERE address = ?", (address(raw),))
    return {"ok": True}


@app.get("/portfolio")
def get_portfolio():
    db = database.connect()
    try:
        with httpx.Client(timeout=8, headers={"User-Agent": "crypto-tracker"}) as client:
            prices.refresh(db, client)
        return prices.portfolio(db)
    finally:
        db.close()


@app.get("/alerts")
def get_alerts():
    db = database.connect()
    try:
        return alerts.recent(db)
    finally:
        db.close()


@app.post("/alerts/seen")
def seen_alerts():
    write("UPDATE alert SET seen = 1 WHERE NOT seen", ())
    return {"ok": True}


@app.post("/labels")
def put_label(label: Label):
    if label.category not in CATEGORIES:
        raise HTTPException(422, f"category must be one of {sorted(CATEGORIES)}")
    if label.name:
        write(
            "INSERT OR REPLACE INTO label(address, name, category, source) VALUES (?, ?, ?, 'user')",
            (address(label.address), label.name[:80], label.category),
        )
    else:
        write("DELETE FROM label WHERE address = ?", (address(label.address),))
    return {"ok": True}


@app.post("/sync")
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


@app.get("/sync")
def get_sync():
    # A node of your own that is not ready yet is said with the sync's notes, on every view.
    return {**sync.status, "notes": sync.status["notes"] + node.notes()}


@app.get("/mempool/{kind}/{ref}")
def to_mempool(kind: str, ref: str, request: Request):
    """An explorer link to the profile's mempool site, on its own port of this host."""
    if kind not in ("address", "tx") or not node.PORT or not ref.isalnum():
        raise HTTPException(404)
    host = request.url.hostname or "localhost"
    host = f"[{host}]" if ":" in host else host
    return RedirectResponse(f"http://{host}:{node.PORT}/{kind}/{ref}")


if os.environ.get("CT_SYNC_EVERY"):  # minutes
    sync.every(float(os.environ["CT_SYNC_EVERY"]))


async def local_info():
    """am-i-exposed asks here whether a mempool of the host's own is behind /api (Umbrel's
    answer, from its web server). Ours has no Tor sidecar: am-i-exposed keeps reaching its
    other services as on its public site, and never says they went through Tor."""
    site = os.environ.get("CT_MEMPOOL", "")
    return {
        "mempoolPort": "" if site else node.PORT,  # the profile's site, on this host
        "mempoolExternalUrl": site if site.startswith(("http://", "https://")) else "",
        "mempoolOnion": "",
        "bitcoinNetwork": "",
        "torProxy": False,
    }


async def mempool_api(path: str, request: Request):
    """am-i-exposed's Bitcoin requests, passed on to the mempool of your own. While it is not
    ready they get the notice and a 503, never an answer from a public server: am-i-exposed
    then says the local mempool is unreachable and to wait for it to finish syncing."""
    parts = path.split("/")
    # mempool's own web server would hand v1/services to mempool.space; and no way out of /api/
    if parts[:2] == ["v1", "services"] or {"", ".", ".."} & set(parts):
        raise HTTPException(404)
    found = await run_in_threadpool(node.status)
    if found["state"] != "ready":
        return PlainTextResponse(node.notice(found), status_code=503)
    # Only what the request is: no cookie or password of this app goes to the mempool.
    headers = {k: v for k, v in request.headers.items() if k in ("accept", "content-type")}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.request(
                request.method, f"{node.MEMPOOL}/api/{path}", params=request.query_params,
                content=await request.body(), headers=headers,
            )  # fmt: skip
    except httpx.HTTPError as e:
        return PlainTextResponse(f"your mempool did not answer: {chains.describe(e)}", 502)
    kind = r.headers.get("content-type")
    return Response(r.content, r.status_code, media_type=kind)


# The API answers under /api/tracker/; /api/ itself is the web app's, which looks for a
# self-hosted mempool there: with one of your own (node.MEMPOOL) this server passes /api/ on
# to it. CT_WEB is the web app's static build, served on everything else behind the same
# password.
app.router.routes = [Mount("/api/tracker", routes=list(app.routes))]
if node.MEMPOOL:
    app.add_api_route("/api/local-info", local_info)
    app.add_api_route("/api/{path:path}", mempool_api, methods=["GET", "HEAD", "POST"])
if web := os.environ.get("CT_WEB"):
    app.mount("/", StaticFiles(directory=web, html=True))
