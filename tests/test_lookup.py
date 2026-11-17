import httpx
import pytest
from fastapi.testclient import TestClient
from test_core import BCH, DOGE, LTC, SATOSHI, SOL, TRX, A, B, C, D, mine, tx, vin, vout

from tracker import chains, graph, uri
from tracker import db as database

SEGWIT = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"
OTHER = "12c6DSiU4Rq3P4ZxziKxzrL5LmMBrzjrJX"
CHECKSUMMED = "0xD8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
USDC_SOL = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
PAYER = "775HG7Z2skCZugQ6LERhgbf82TPb2eGh4ucwVkjJm4GB"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # plain addresses, as pasted
        (f"  {SATOSHI}\n", (SATOSHI, "bitcoin", None)),
        (CHECKSUMMED, (CHECKSUMMED.lower(), "evm", None)),
        (SOL, (SOL, "solana", None)),
        (BCH, (BCH, "bitcoincash", None)),
        # BIP-21 and its siblings, with and without parameters
        (f"bitcoin:{SATOSHI}", (SATOSHI, "bitcoin", None)),
        (f"bitcoin:{SATOSHI}?amount=0.5&label=Rent%20May", (SATOSHI, "bitcoin", None)),
        (f"BITCOIN:{SEGWIT.upper()}?amount=1", (SEGWIT, "bitcoin", None)),  # as QR codes pack it
        (f"litecoin:{LTC}", (LTC, "litecoin", None)),
        (f"litecoin:{LTC}?amount=2", (LTC, "litecoin", None)),
        (f"dogecoin:{DOGE}", (DOGE, "dogecoin", None)),
        (f"dogecoin:{DOGE}?amount=100&message=wow", (DOGE, "dogecoin", None)),
        (f"{BCH}?amount=1", (BCH, "bitcoincash", None)),
        (f"tron:{TRX}", (TRX, "tron", None)),
        (f"tron:{TRX}?amount=5", (TRX, "tron", None)),
        # Solana Pay
        (f"solana:{SOL}", (SOL, "solana", None)),
        (f"solana:{SOL}?amount=1&spl-token={USDC_SOL}&memo=x", (SOL, "solana", None)),
        # EIP-681
        (f"ethereum:{A}", (A, "evm", None)),
        (f"ethereum:{CHECKSUMMED}?value=1e18", (CHECKSUMMED.lower(), "evm", None)),
        (f"ethereum:{A}@1", (A, "evm", "ethereum")),
        (f"ethereum:{A}@137?value=2.014e18&gas=21000", (A, "evm", "polygon")),
        (f"ethereum:pay-{A}@8453", (A, "evm", "base")),
        (f"ethereum:{A}@424242", (A, "evm", None)),  # a chain this server does not know: ask
        # a token payment names the token first; the wallet is the payee
        (f"ethereum:{C}@1/transfer?address={B}&uint256=1e6", (B, "evm", "ethereum")),
        (f"polygon:{A}", (A, "evm", "polygon")),
        (f"avalanche:{A}?value=1", (A, "evm", "avalanche")),
    ],
)
def test_uri_names_the_address_and_drops_the_rest(text, expected):
    assert uri.parse(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "bitcoin:",
        "bitcoin:?amount=1",
        f"bitcoin:{SATOSHI[:-1]}x",  # a typo fails the checksum
        f"bitcoin:{A}",  # the scheme and the address disagree
        f"ethereum:{SATOSHI}",
        f"solana:{A}",
        f"ethereum:{C}/transfer?uint256=1",  # a token payment without a payee
        f"ethereum:{C}@1/approve?address={B}&uint256=1",  # some other call: no wallet named
        "ethereum:vitalik.eth",
        "solana:https://example.com/pay",  # a transaction request, not an address
        "https://example.com/" + A,
        "wc:7f6e504bfad60b485450578e05678ed3e8e8c4751d3c6160be17160d63ec90f9@2",
    ],
)
def test_uri_refuses_what_is_not_an_address(text):
    with pytest.raises(ValueError):
        uri.parse(text)


def test_a_scanned_private_key_is_refused_and_not_echoed(db):
    from tracker.app import app

    wif = "5HueCGU8rMjxEXxiPuD5BDku4MkFqeZyd4dZ1jvhTVqvbTLvyTJ"
    r = TestClient(app, base_url="http://localhost").post("/api/resolve", json={"q": wif})
    assert r.status_code == 422 and "private key" in r.text and wif not in r.text


PRICES = {
    "coingecko:bitcoin": {"price": 50000, "symbol": "BTC", "confidence": 0.99},
    "coingecko:ethereum": {"price": 2000, "symbol": "ETH", "confidence": 0.99},
    "coingecko:solana": {"price": 100, "symbol": "SOL", "confidence": 0.99},
    f"ethereum:{C}": {"price": 1, "symbol": "USDC", "confidence": 0.99},
    f"solana:{USDC_SOL}": {"price": 1, "symbol": "USDC", "confidence": 0.99},
}


def indexers(request):
    """One fake internet: Esplora, an Etherscan-style API, the Solana RPC and the prices."""
    host, path = request.url.host, request.url.path
    if host == "coins.llama.fi":
        asked = path.rsplit("/", 1)[1].split(",")
        return httpx.Response(200, json={"coins": {k: PRICES[k] for k in asked if k in PRICES}})
    if host == "blockstream.info":
        if path.endswith("/txs/chain"):
            return httpx.Response(
                200,
                json=[
                    # spent together with OTHER, paying the exchange and my own wallet
                    tx(
                        [vin(SATOSHI, 60_000_000), vin(OTHER, 40_000_000)],
                        [vout(D_BTC, 70_000_000), vout(SEGWIT, 30_000_000)],
                        "t1",
                    ),
                    tx([vin(D_BTC, 250_000_000)], [vout(SATOSHI, 250_000_000)], "t2"),
                ],
            )
        stats = {"funded_txo_sum": 250_000_000, "spent_txo_sum": 60_000_000, "tx_count": 2}
        return httpx.Response(200, json={"chain_stats": stats})
    if host in ("eth", "poly"):
        row = {"from": D, "to": A, "hash": "h1", "timeStamp": "5"}
        token = {**row, "tokenDecimal": "6"}
        answers = {
            "txlist": [
                {**row, "value": str(2 * 10**18)},
                {**row, "to": B, "from": A, "value": "5"},
            ],
            "txlistinternal": [],
            "tokentx": [
                {**token, "value": "7000000", "tokenSymbol": "USDC", "contractAddress": C},
                # an airdrop nobody vouches for, from a stranger
                {**token, "from": SPAMMER, "value": "9000000", "tokenSymbol": "FREE"}
                | {"contractAddress": SPAM},
            ],
            "balance": str(2 * 10**18),
        }
        return httpx.Response(
            200, json={"status": "1", "result": answers[request.url.params["action"]]}
        )
    if host == "api.mainnet-beta.solana.com":
        import json

        method = json.loads(request.content)["method"]
        amount = lambda n: {"amount": str(n), "decimals": 6}
        params = json.loads(request.content)["params"]
        if method == "getTransaction" and params[1]["maxSupportedTransactionVersion"] < 1:
            # as the public RPC answers since version 1 transactions exist
            message = "Transaction version (1) is not supported by the requesting client."
            return httpx.Response(200, json={"error": {"code": -32015, "message": message}})
        result = {
            "getBalance": {"value": 1_000_000_000},
            "getTokenAccountsByOwner": {
                "value": [
                    {"account": {"data": {"parsed": {"info": info}}}}
                    for info in (
                        {"mint": USDC_SOL, "tokenAmount": {"uiAmount": 112.5}},
                        {"mint": "So11111111111111111111111111111111111111112"}
                        | {"tokenAmount": {"uiAmount": 3}},  # unknown to the price source
                    )
                ]
            },
            "getSignaturesForAddress": [{"signature": "sig"}],
            "getTransaction": {
                "blockTime": 1794188391,
                "transaction": {"message": {"accountKeys": [PAYER, SOL, "ata"]}},
                "meta": {
                    "fee": 5000,
                    "preBalances": [2_000_005_000, 0, 7],
                    "postBalances": [1_000_000_000, 1_000_000_000, 7],
                    "preTokenBalances": [
                        {"accountIndex": 2, "mint": USDC_SOL, "owner": PAYER}
                        | {"uiTokenAmount": amount(150_000_000)}
                    ],
                    "postTokenBalances": [
                        {"accountIndex": 2, "mint": USDC_SOL, "owner": PAYER}
                        | {"uiTokenAmount": amount(37_500_000)},
                        {"accountIndex": 1, "mint": USDC_SOL, "owner": SOL}
                        | {"uiTokenAmount": amount(112_500_000)},
                    ],
                },
            },
        }[method]
        return httpx.Response(200, json={"result": result})
    return httpx.Response(404)


D_BTC = "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2"
SPAM, SPAMMER = "0x" + "5" * 40, "0x" + "6" * 40


@pytest.fixture
def client(db, monkeypatch):
    from tracker.app import app

    real = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(indexers), **kw)
    )
    monkeypatch.setattr(chains, "DELAY", 0)
    monkeypatch.setattr(
        chains,
        "DEFAULT_EVM",
        {
            "ethereum": {"url": "http://eth/api", "symbol": "ETH", "llama": "ethereum"},
            "polygon": {"url": "http://poly/api", "symbol": "POL", "llama": "polygon"},
            "base": {"symbol": "ETH", "llama": "base", "needs_key": True},
        },
    )
    return TestClient(app, base_url="http://localhost")


def stored(db):
    """Everything a verdict is made of."""
    tables = ("node", "transfer", "link", "balance", "label", "alert", "known_link", "watch")
    return {t: [tuple(r) for r in db.execute(f"SELECT * FROM {t} ORDER BY 1, 2")] for t in tables}


def test_lookup_bitcoin_shows_assets_connections_and_links_to_my_wallets(db, client):
    mine(db, SEGWIT, public=1)
    db.execute("UPDATE node SET name = 'Savings' WHERE address = ?", (SEGWIT,))
    db.execute("INSERT INTO label VALUES (?, 'Kraken 4', 'cex', 'user')", (D_BTC,))
    db.commit()
    before, verdicts = stored(db), graph.verdicts(db)

    found = client.post("/api/resolve", json={"q": f"bitcoin:{SATOSHI}?amount=0.1"}).json()
    assert found == {
        "address": SATOSHI, "chain": "bitcoin", "chains": [], "hint": None, "needs_key": None
    }  # fmt: skip
    v = client.get(f"/api/lookup/{SATOSHI}").json()

    assert (v["address"], v["chain"], v["mine"], v["watched"]) == (SATOSHI, "bitcoin", 0, False)
    assert v["assets"] == [
        {"chain": "bitcoin", "asset": "BTC", "token": "", "amount": 1.9, "value": 95000.0}
    ]
    assert (v["value"], v["currency"]) == (95000.0, "USD")
    by = {c["address"]: c for c in v["connections"]}
    assert v["connections"][0]["address"] == SEGWIT  # my wallets come first
    assert (by[SEGWIT]["mine"], by[SEGWIT]["public"], by[SEGWIT]["name"]) == (1, 1, "Savings")
    assert by[SEGWIT]["sent"] == [{"chain": "bitcoin", "asset": "BTC", "amount": 0.18}]
    assert (by[D_BTC]["label"], by[D_BTC]["category"], by[D_BTC]["count"]) == ("Kraken 4", "cex", 2)
    assert by[D_BTC]["received"] == [{"chain": "bitcoin", "asset": "BTC", "amount": 2.5}]
    assert by[OTHER]["guess"] == {"kind": "common-input", "confidence": 0.9}
    nodes = {n["address"] for n in v["graph"]["nodes"]}
    assert nodes == {SATOSHI, SEGWIT, D_BTC, OTHER}
    assert all(SATOSHI in (e["src"], e["dst"]) for e in v["graph"]["edges"])
    assert {k["kind"] for k in v["graph"]["links"]} == {"common-input"}

    # Looking is not touching: nothing a verdict is made of has changed.
    assert stored(db) == before and graph.verdicts(db) == verdicts
    assert client.get("/api/graph").json()["edges"] == []


def test_lookup_evm_reads_the_chosen_chains_and_hides_airdrops(db, client):
    found = client.post("/api/resolve", json={"q": A}).json()
    assert found["chain"] == "evm" and found["chains"] == ["ethereum", "polygon"]
    assert client.post("/api/resolve", json={"q": f"ethereum:{A}@137"}).json()["hint"] == "polygon"
    # A code for a chain that needs a key is no dead end: the readable chains are offered.
    keyless = client.post("/api/resolve", json={"q": f"ethereum:{A}@8453"}).json()
    assert (keyless["hint"], keyless["needs_key"]) == (None, "base")
    assert keyless["chains"] == ["ethereum", "polygon"]
    before = stored(db)
    assert client.get(f"/api/lookup/{A}?chains=base").status_code == 422  # needs a key
    assert client.get(f"/api/lookup/{A}?chains=nope").status_code == 422

    v = client.get(f"/api/lookup/{A}?chains=ethereum").json()
    assert v["chains"] == ["ethereum"]
    assert [(a["chain"], a["asset"], a["amount"], a["value"]) for a in v["assets"]] == [
        ("ethereum", "ETH", 2.0, 4000.0),
        ("ethereum", "USDC", 7.0, 7.0),
    ]
    assert v["value"] == 4007.0 and v["hidden"] == 1  # the airdropped token is not an asset
    assert {c["address"] for c in v["connections"]} == {D, B}  # nor is its sender a counterparty
    assert v["explorer_address"].startswith("https://etherscan.io/")

    both = client.get(f"/api/lookup/{A}").json()
    assert both["chains"] == ["ethereum", "polygon"]
    assert {(a["chain"], a["asset"]) for a in both["assets"]} >= {("polygon", "POL")}
    assert stored(db) == before


def test_lookup_solana_names_tokens_from_the_price_source(db, client):
    before = stored(db)
    v = client.get(f"/api/lookup/{SOL}").json()
    assert [(a["asset"], a["amount"], a["value"]) for a in v["assets"]] == [
        ("USDC", 112.5, 112.5),
        ("SOL", 1.0, 100.0),
    ]
    assert v["hidden"] == 1
    (payer,) = v["connections"]
    assert payer["address"] == PAYER and len(payer["received"]) == 2
    assert v["notes"] and "Solana" in v["notes"][0]
    assert stored(db) == before


def test_watching_keeps_an_address_out_of_my_wallets_until_it_is_tracked(db, client):
    assert client.post("/api/watch", json={"address": "nope"}).status_code == 422
    assert client.post("/api/watch", json={"address": SATOSHI}).is_success
    client.post("/api/labels", json={"address": SATOSHI, "name": "Satoshi", "category": "other"})
    analysis = client.get("/api/analysis").json()
    assert [(w["address"], w["label"]) for w in analysis["watched"]] == [(SATOSHI, "Satoshi")]
    assert analysis["verdicts"] == [] and client.get("/api/graph").json()["nodes"] == []
    v = client.get(f"/api/lookup/{SATOSHI}").json()
    assert (v["watched"], v["mine"], v["label"]) == (True, 0, "Satoshi")

    # Tracking makes it mine, private unless said otherwise, and no longer merely watched.
    assert client.post("/api/wallets", json={"address": SATOSHI, "name": "Satoshi"}).is_success
    analysis = client.get("/api/analysis").json()
    assert analysis["watched"] == []
    assert [(x["address"], x["public"]) for x in analysis["verdicts"]] == [(SATOSHI, False)]
    v = client.get(f"/api/lookup/{SATOSHI}").json()
    assert (v["watched"], v["mine"], v["public"]) == (False, 1, 0)

    assert client.post("/api/watch", json={"address": A}).is_success
    assert client.delete(f"/api/watch/{A}").is_success
    assert client.get("/api/analysis").json()["watched"] == []


def test_lookup_reports_an_indexer_that_does_not_answer(db, client, monkeypatch):
    monkeypatch.setitem(chains.UTXO, "bitcoin", ("BTC", "esplora", "http://down", ""))
    r = client.get(f"/api/lookup/{SATOSHI}")
    assert r.status_code == 502 and "HTTP 404" in r.json()["detail"]
    assert database.connect().execute("SELECT COUNT(*) FROM node").fetchone()[0] == 0
