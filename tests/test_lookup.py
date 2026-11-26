import io
import sys
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient
from test_core import BCH, DOGE, LTC, SATOSHI, SOL, TRX, A, B, C, D, mine, tx, vin, vout

from tracker import chains, graph, labels, lookup, tokens, uri
from tracker import db as database

SEGWIT = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"
OTHER = "12c6DSiU4Rq3P4ZxziKxzrL5LmMBrzjrJX"
CHECKSUMMED = "0xD8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
USDC_SOL = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
PAYER = "775HG7Z2skCZugQ6LERhgbf82TPb2eGh4ucwVkjJm4GB"
MSTRX = "XsP7xzNPvEHS1m6qfanPUGjNmdnmsLKEoNAnHjdxxyZ"  # a Token-2022 mint
WSOL = "So11111111111111111111111111111111111111112"
FAKE_USDC = "0x" + "7" * 40
SPL, TOKEN_2022 = chains.TOKEN_PROGRAMS
OKX_SOL = "5VCwKtCXgCJ6kit5FybXjvriW3xELsFDhYrPSqtJNmcD"  # OKX's Solana hot wallet
OKX_LIST = "https://static.okx.com/cdn/okx/por/chain/por_csv_2026110100_V10.zip"
# Recorded from OKX's proof-of-reserves download page (cut): it links every month's list.
# Versions 9 and 10 of one month are made up: OKX has gone up to 6 in a month so far.
OKX_PAGE = (
    '<a href="https://static.okx.com/cdn/okx/por/chain/por_csv_2026100300_V4.zip" download="">'
    '<a href="https://static.okx.com/cdn/okx/por/chain/por_csv_2026110100_V9.zip" download="">'
    f'<a href="{OKX_LIST}" download="">'
    '"auditList":[{"auditId":"508756245","createTime":1793462400000,"typeNum":4,'
    f'"download":"{OKX_LIST}",'
    '"merkleTreeDownload":"https://static.okx.com/cdn/okx/por/merkel/por_508756245_proof_data.zip"}'
)
# Recorded rows of that list (signatures cut): totals per coin, then every signed address.
OKX_CSV = """\
coin,amount
USDT,8265265024
SOL,9118220
coin,Type,Network,Snapshot Height,address,amount,message,signature1,signature2,redeem script/ public key,EOA1,EOA2
BTC,Non Staking,BTC,973994,bc1qf8kep70t232jtajg2x4r8dhtuvtd7kuea8ve9w02qy5k4wncyl7spmnfme,9558.96262646,I am an OKX address,IA/3KVJK,H0uWIAQs,5221032530,,
BCHN,Non Staking,BCH,975353,3JQrQQzsoqGZnDk3FaYS6EPia4sv5pGS2y,178014.51000383,I am an OKX address,H/Osgi2P,IEUBIJMu,522103ed0f,,
ETH,Native ETH Staking,ETH,26313657,0x800065c9ae3346eccad09c1e72019ba7b06f5049dda287d4163dd95bb7b64dd23acb6a79fe351677ed09de67fd69ab8f,32.011817826000000000,I am an OKX address,0x91ff6f6a,,,0xe839a3e9,
ETH,Non Staking,ETH,26313797,0xbe2ec156292587338d4e9f80f5439e6152d56fc5,80001.294962090482044761,I am an OKX address,0x8e8a0e53,,,,
APTOS,Non Staking,APTOS,1151935162,0x4c1ef44079cb31851349fba50d385f708a10ec7ac612859fdbf28888d1f7b572,11207438.37268685,I am an OKX address,0xd21b9170,,0xd7aa6673,,
USDT-TRC20,Non Staking,TRON,87596323,TCw6YaWm3y6DvxY7M8hrCDnrJGeGMumzGJ,472708183.276554,I am an OKX address,0x8cd71531,,,,
SOL,Non Staking,SOL,463747826,5VCwKtCXgCJ6kit5FybXjvriW3xELsFDhYrPSqtJNmcD,0.200615060,I am an OKX address,0xaf71b5ae,,0x2a8f1c3e,,
"""


def zipped(name, text):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, text)
    return out.getvalue()


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
    r = TestClient(app, base_url="http://localhost").post("/api/tracker/resolve", json={"q": wif})
    assert r.status_code == 422 and "private key" in r.text and wif not in r.text


PRICES = {
    "coingecko:bitcoin": {"price": 50000, "symbol": "BTC", "confidence": 0.99},
    "coingecko:ethereum": {"price": 2000, "symbol": "ETH", "confidence": 0.99},
    "coingecko:solana": {"price": 100, "symbol": "SOL", "confidence": 0.99},
    f"ethereum:{C}": {"price": 1, "symbol": "USDC", "confidence": 0.99},
    f"solana:{USDC_SOL}": {"price": 1, "symbol": "USDC", "confidence": 0.99},
    f"solana:{MSTRX}": {"price": 300, "symbol": "MSTRX", "confidence": 0.95},
}
JUPITER = {
    USDC_SOL: {"id": USDC_SOL, "symbol": "USDC", "name": "USD Coin", "isVerified": True},
    MSTRX: {"id": MSTRX, "symbol": "MSTRx", "name": "MicroStrategy xStock", "isVerified": True},
}


def indexers(request):
    """One fake internet: Esplora, an Etherscan-style API, the Solana RPC and the prices."""
    host, path = request.url.host, request.url.path
    if host in ("ethereum-rpc.publicnode.com", "base-rpc.publicnode.com"):
        return httpx.Response(200, json={"result": "0x"})  # names: none (test_names.py has some)
    if host == "coins.llama.fi":
        asked = path.rsplit("/", 1)[1].split(",")
        return httpx.Response(200, json={"coins": {k: PRICES[k] for k in asked if k in PRICES}})
    if host == "tokens.uniswap.org":
        listed = [(1, C, "USDC"), (tokens.SOLANA_ID, USDC_SOL, "USDC")]
        return httpx.Response(
            200, json={"tokens": [{"chainId": i, "address": a, "symbol": s} for i, a, s in listed]}
        )
    if host == "lite-api.jup.ag":
        asked = request.url.params["query"].split(",")
        return httpx.Response(200, json=[JUPITER[m] for m in asked if m in JUPITER])
    if host == "www.okx.com":  # as OKX answers: the page in the reader's language
        if path == "/proof-of-reserves/download":
            return httpx.Response(302, headers={"location": "/en-eu/proof-of-reserves/download"})
        if path == "/en-eu/proof-of-reserves/download":
            return httpx.Response(200, text=OKX_PAGE)
    if str(request.url) == OKX_LIST:
        return httpx.Response(200, content=zipped("okx_por_2026110100_V10.csv", OKX_CSV))
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
            "tokennfttx": [],
            "token1155tx": [],
            "tokentx": [
                {**token, "value": "7000000", "tokenSymbol": "USDC", "contractAddress": C},
                # an airdrop nobody vouches for, from a stranger
                {**token, "from": SPAMMER, "value": "9000000", "tokenSymbol": "FREE"}
                | {"contractAddress": SPAM, "hash": "h9"},
            ],
            "balance": str(2 * 10**18),
            # Routescan's list of every token held, with names
            "addresstokenbalance": [
                {"TokenAddress": a, "TokenName": n, "TokenSymbol": s, "TokenDivisor": "6"}
                | {"TokenQuantity": q}
                for a, n, s, q in (
                    (C, "USD Coin", "USDC", "7000000"),
                    (SPAM, "Free Money", "FREE", "9000000"),
                    (FAKE_USDC, "USD Coin", "USDС", "500000000"),  # with a Cyrillic Es
                )
            ],
        }
        action = request.url.params["action"]
        result = answers[action] + EXTRA[action] if action in EXTRA else answers[action]
        return httpx.Response(200, json={"status": "1", "result": result})
    if host == "api.mainnet-beta.solana.com":
        import json

        method = json.loads(request.content)["method"]
        amount = lambda n: {"amount": str(n), "decimals": 6}
        params = json.loads(request.content)["params"]
        if method == "getTransaction" and params[1]["maxSupportedTransactionVersion"] < 1:
            # as the public RPC answers since version 1 transactions exist
            message = "Transaction version (1) is not supported by the requesting client."
            return httpx.Response(200, json={"error": {"code": -32015, "message": message}})
        accounts = {
            SPL: [
                {"mint": USDC_SOL, "tokenAmount": {"uiAmount": 112.5}},
                {"mint": WSOL, "tokenAmount": {"uiAmount": 3}},  # unknown to the price source
            ],
            TOKEN_2022: [{"mint": MSTRX, "tokenAmount": {"uiAmount": 0.5}}],
        }
        result = {
            "getMultipleAccounts": {"value": [None] * len(params[0])},  # no .sol name
            "getBalance": {"value": 1_000_000_000},
            "getTokenAccountsByOwner": {
                "value": [
                    {"account": {"data": {"parsed": {"info": info}}}}
                    for info in accounts.get(
                        method == "getTokenAccountsByOwner" and params[1]["programId"], []
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
EXTRA = {}  # action -> rows a test adds to the Etherscan-style answers
SPAM, SPAMMER = "0x" + "5" * 40, "0x" + "6" * 40


@pytest.fixture
def client(db, monkeypatch):
    from tracker.app import app

    real = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(indexers), **kw)
    )
    monkeypatch.setattr(chains, "DELAY", 0)
    monkeypatch.setattr(tokens, "CACHE", {})
    monkeypatch.setattr(
        chains,
        "DEFAULT_EVM",
        {
            "ethereum": {"url": "http://eth/api", "symbol": "ETH", "llama": "ethereum"}
            | {"tokenbalance": True},
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
    db.execute(
        "INSERT INTO label(address, name, category, source) "
        "VALUES (?, 'Kraken 4', 'cex', 'graphsense-tagpacks')",
        (D_BTC,),
    )
    db.commit()
    before, verdicts = stored(db), graph.verdicts(db)

    found = client.post("/api/tracker/resolve", json={"q": f"bitcoin:{SATOSHI}?amount=0.1"}).json()
    assert found == {
        "address": SATOSHI, "chain": "bitcoin", "chains": [], "hint": None, "needs_key": None,
        "icon": "bitcoin",
    }  # fmt: skip
    v = client.get(f"/api/tracker/lookup/{SATOSHI}").json()

    assert (v["address"], v["chain"], v["mine"], v["watched"]) == (SATOSHI, "bitcoin", 0, False)
    assert v["icon"] == "bitcoin"
    ((btc, trust),) = [(a, a.pop("trust")) for a in v["assets"]]
    assert btc == {
        "chain": "bitcoin", "asset": "BTC", "token": "", "name": "", "amount": 1.9,
        "value": 95000.0,
    }  # fmt: skip
    assert trust["level"] == "verified"
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
    assert {c["chain"] for c in v["connections"]} == {"bitcoin"}  # each one gets its chain mark
    # Before a transfer: what the address is, how it is used, and who it deals with.
    risk = v["risk"]
    assert (risk["level"], risk["headline"]) == ("ok", "No red flags found")
    said = " ".join(r["text"] for r in risk["reasons"])
    assert "Bitcoin address" in said and "2 transactions" in said
    assert "Kraken 4" in said and "Savings" in said

    # Looking is not touching: nothing a verdict is made of has changed.
    assert stored(db) == before and graph.verdicts(db) == verdicts
    assert client.get("/api/tracker/graph").json()["edges"] == []


def test_lookup_evm_reads_the_chosen_chains_and_lists_every_token(db, client):
    found = client.post("/api/tracker/resolve", json={"q": A}).json()
    assert found["chain"] == "evm" and found["chains"] == ["ethereum", "polygon"]
    assert found["icon"] == "evm"  # on every EVM chain: the generic mark until one is chosen
    hinted = client.post("/api/tracker/resolve", json={"q": f"ethereum:{A}@137"}).json()
    assert (hinted["hint"], hinted["icon"]) == ("polygon", "polygon")
    # A code for a chain that needs a key is no dead end: the readable chains are offered.
    keyless = client.post("/api/tracker/resolve", json={"q": f"ethereum:{A}@8453"}).json()
    assert (keyless["hint"], keyless["needs_key"], keyless["icon"]) == (None, "base", "evm")
    assert keyless["chains"] == ["ethereum", "polygon"]
    before = stored(db)
    assert client.get(f"/api/tracker/lookup/{A}?chains=base").status_code == 422  # needs a key
    assert client.get(f"/api/tracker/lookup/{A}?chains=nope").status_code == 422

    v = client.get(f"/api/tracker/lookup/{A}?chains=ethereum").json()
    assert v["chains"] == ["ethereum"] and v["icon"] == "ethereum"
    held = [
        (a["asset"], a["name"], a["amount"], a["value"], a["trust"]["level"]) for a in v["assets"]
    ]
    assert held == [
        ("ETH", "", 2.0, 4000.0, "verified"),
        ("USDC", "USD Coin", 7.0, 7.0, "verified"),  # on the token list
        # Listed too, but last, unvalued and marked: anyone can airdrop a token.
        ("USDС", "USD Coin", 500.0, None, "scam"),  # borrows the name of a listed token
        ("FREE", "Free Money", 9.0, None, "scam"),  # arrived unasked, no market
    ]
    reasons = {a["asset"]: a["trust"]["reason"] for a in v["assets"]}
    assert "Uniswap" in reasons["USDC"] and "not the" in reasons["USDС"]
    assert "unasked" in reasons["FREE"]
    assert v["value"] == 4007.0 and v["unvouched"] == 2
    (me,) = [n for n in v["graph"]["nodes"] if n["address"] == A]
    assert [b["asset"] for b in me["balances"]] == ["ETH", "USDC"]  # no scam bait in the graph
    assert {c["address"] for c in v["connections"]} == {D, B}  # nor is the airdropper a party
    assert v["explorer_address"].startswith("https://etherscan.io/")

    both = client.get(f"/api/tracker/lookup/{A}").json()
    assert both["chains"] == ["ethereum", "polygon"] and both["icon"] == "evm"
    assert {(a["chain"], a["asset"]) for a in both["assets"]} >= {("polygon", "POL")}
    assert stored(db) == before


def test_lookup_solana_lists_spl_and_token_2022_tokens_with_their_names(db, client):
    before = stored(db)
    v = client.get(f"/api/tracker/lookup/{SOL}").json()
    assert v["icon"] == "solana"
    held = [
        (a["asset"], a["name"], a["amount"], a["value"], a["trust"]["level"]) for a in v["assets"]
    ]
    assert held == [
        ("MSTRx", "MicroStrategy xStock", 0.5, 150.0, "verified"),  # Token-2022, Jupiter
        ("USDC", "USD Coin", 112.5, 112.5, "verified"),
        ("SOL", "", 1.0, 100.0, "verified"),
        ("So11...", "", 3.0, None, "unknown"),  # known to no list and no price source
    ]
    assert "Jupiter" in v["assets"][0]["trust"]["reason"]
    assert v["unvouched"] == 1
    (payer,) = v["connections"]
    assert payer["address"] == PAYER
    # what it dealt in goes by the names of what it holds, not by the start of a mint
    assert {r["asset"] for r in payer["received"]} == {"SOL", "USDC"}
    assert {e["asset"] for e in v["graph"]["edges"]} == {"SOL", "USDC"}
    assert v["notes"] and "Solana" in v["notes"][0]
    assert stored(db) == before


def test_tokens_from_my_wallets_a_service_or_a_claim_were_wanted(db, client, monkeypatch):
    NEWT, CLAIMED, DISTRIBUTOR = ("0x" + c * 40 for c in "894")
    arrives = {"to": A, "timeStamp": "5", "tokenDecimal": "6", "value": "1000000"}
    monkeypatch.setitem(EXTRA, "tokentx", [
        arrives | {"from": D, "hash": "h5", "tokenSymbol": "NEWT", "contractAddress": NEWT},
        arrives | {"from": DISTRIBUTOR, "hash": "h7", "tokenSymbol": "GOV"}
        | {"contractAddress": CLAIMED},
    ])  # fmt: skip
    # The wallet's own claim() call: no value moves, so it is no transfer, but it took part.
    claim = {"from": A, "to": DISTRIBUTOR, "hash": "h7", "timeStamp": "5", "value": "0"}
    monkeypatch.setitem(EXTRA, "txlist", [claim])

    def trust():
        v = client.get(f"/api/tracker/lookup/{A}?chains=ethereum").json()
        return {a["asset"]: a["trust"]["level"] for a in v["assets"]}

    assert trust()["GOV"] == "unknown"  # claimed: the wallet took part
    assert (trust()["NEWT"], trust()["FREE"]) == ("scam", "scam")  # both from strangers
    db.execute(
        "INSERT INTO label(address, name, category, source) VALUES (?, 'Binance 14', 'cex', 'etherscan-labels')",
        (D,),
    )
    db.commit()
    assert (trust()["NEWT"], trust()["FREE"]) == ("unknown", "scam")  # withdrawn from an exchange
    db.execute("DELETE FROM label")
    mine(db, D)
    db.commit()
    assert (trust()["NEWT"], trust()["FREE"]) == ("unknown", "scam")  # sent by my own wallet


def test_lookup_warns_before_sending_to_a_flagged_address(db, client):
    db.execute(
        "INSERT INTO label(address, name, category, source) VALUES (?, 'Ransomware wallet', 'flagged', 'graphsense-tagpacks')",
        (SATOSHI,),
    )
    db.execute(
        "INSERT INTO label(address, name, category, source) VALUES (?, 'Tornado.Cash', 'mixer', 'etherscan-labels')",
        (D_BTC,),
    )
    db.commit()
    risk = client.get(f"/api/tracker/lookup/{SATOSHI}").json()["risk"]
    assert (risk["level"], risk["headline"]) == ("bad", "Risky to send to")
    assert "Ransomware wallet" in risk["reason"]
    assert any("Tornado.Cash" in r["text"] for r in risk["reasons"])  # it dealt with a mixer


def test_an_exchange_hot_wallet_on_solana_is_named_from_its_proof_of_reserves(db, client):
    with httpx.Client() as web:
        _, failed = labels.import_open(db, web)
    assert "okx-por" not in failed  # the newest list, read through the page that links it
    named = db.execute("SELECT address FROM label WHERE source = 'okx-por' ORDER BY 1")
    assert [r[0] for r in named] == [
        "0xbe2ec156292587338d4e9f80f5439e6152d56fc5",
        OKX_SOL,
        "TCw6YaWm3y6DvxY7M8hrCDnrJGeGMumzGJ",
        "bc1qf8kep70t232jtajg2x4r8dhtuvtd7kuea8ve9w02qy5k4wncyl7spmnfme",
    ]  # not Bitcoin Cash (its legacy form reads as Bitcoin), Aptos, or a validator's key
    v = client.get(f"/api/tracker/lookup/{OKX_SOL}").json()
    assert (v["label"], v["category"], v["label_type"]) == ("OKX", "cex", "exchange")
    assert (v["label_source"], v["label_guess"]) == ("dataset", False)


def test_a_damaged_okx_list_is_one_failed_set_not_a_failed_sync(db, client, monkeypatch):
    good = zipped("l.csv", OKX_CSV)
    start = 30 + len("l.csv")  # past the zip's local header, the deflated data begins
    bad = good[:start] + b"\xff" + good[start + 1 :]  # an invalid deflate block: zlib.error
    monkeypatch.setattr(sys.modules[__name__], "zipped", lambda name, text: bad)
    with httpx.Client() as web:
        _, failed = labels.import_open(db, web)
    assert "okx-por" in failed
    assert not db.execute("SELECT 1 FROM label WHERE source = 'okx-por'").fetchone()


def test_a_new_okx_list_replaces_the_old_one_but_an_empty_one_does_not(db, client, monkeypatch):
    here = sys.modules[__name__]
    okx = lambda: {r[0] for r in db.execute("SELECT address FROM label WHERE source = 'okx-por'")}
    with httpx.Client() as web:
        labels.import_open(db, web)
        assert OKX_SOL in okx() and len(okx()) == 4
        later = "\n".join(line for line in OKX_CSV.splitlines() if OKX_SOL not in line)
        monkeypatch.setattr(here, "OKX_CSV", later)
        labels.import_open(db, web, everything=True)
        assert OKX_SOL not in okx() and len(okx()) == 3  # no longer OKX's: no longer named
        monkeypatch.setattr(here, "OKX_CSV", "coin,amount\nUSDT,8265265024\n")
        _, failed = labels.import_open(db, web, everything=True)
    assert "okx-por" in failed and len(okx()) == 3  # a list come empty keeps the old copy


def test_lookup_spots_an_address_made_to_look_like_one_i_use(db, client):
    mine(db, A)
    db.execute("INSERT INTO transfer VALUES ('ethereum', 'x', ?, ?, 'ETH', '', 1, 1)", (A, B))
    db.commit()
    for real, who in ((A, "your wallet"), (B, "an address you sent to")):
        poisoned = real[:6] + "e" * 32 + real[-4:]
        risk = client.get(f"/api/tracker/lookup/{poisoned}?chains=ethereum").json()["risk"]
        assert risk["level"] == "bad" and real in risk["reason"] and who in risk["reason"]
    # The real one is not its own lookalike.
    assert client.get(f"/api/tracker/lookup/{B}?chains=ethereum").json()["risk"]["level"] != "bad"


NOW = 1_800_000_000
DAY = 86400


def risk(real=(), **kw):
    return lookup.risk("Ethereum address", {"category": None}, list(real), [], [], now=NOW, **kw)


def row(txid, ts, src=A, dst=B):
    return ("ethereum", txid, src, dst, "ETH", "", 1.0, ts)


def test_risk_of_an_address_that_was_never_used_or_is_brand_new():
    never = risk(held=False)
    assert never["level"] == "warn" and "never been used" in never["reason"]
    new = risk([row("t1", NOW - 3 * DAY)])
    assert new["level"] == "warn" and "3 days ago" in new["reason"]
    old = risk([row("t1", NOW - 900 * DAY), row("t2", NOW - 2 * DAY)])
    assert (old["level"], old["headline"]) == ("ok", "No red flags found")
    assert "not a guarantee" in old["reason"]
    # Only part of a very active history is read: its first transaction is not its age.
    assert risk([row("t1", NOW - 3 * DAY)], hub=True)["level"] == "ok"


def test_risk_without_label_sets_says_it_cannot_recognise_flagged_addresses():
    blind = risk([row("t1", NOW - 900 * DAY)], labels=False)
    assert blind["level"] == "unknown" and "label set" in blind["reason"]


def test_risk_of_an_address_that_could_not_be_read_fully_says_so():
    partly = risk([row("t1", NOW - 900 * DAY)], errors=["polygon: HTTP 500"])
    assert partly["level"] == "unknown" and "polygon: HTTP 500" in partly["reason"]
    # An empty read is no proof that the address is unused.
    assert risk(held=False, errors=["ethereum: HTTP 500"])["level"] == "unknown"


def test_watching_keeps_an_address_out_of_my_wallets_until_it_is_tracked(db, client):
    assert client.post("/api/tracker/watch", json={"address": "nope"}).status_code == 422
    assert client.post("/api/tracker/watch", json={"address": SATOSHI}).is_success
    client.post(
        "/api/tracker/labels", json={"address": SATOSHI, "name": "Satoshi", "category": "other"}
    )
    analysis = client.get("/api/tracker/analysis").json()
    assert [(w["address"], w["label"]) for w in analysis["watched"]] == [(SATOSHI, "Satoshi")]
    assert analysis["verdicts"] == [] and client.get("/api/tracker/graph").json()["nodes"] == []
    v = client.get(f"/api/tracker/lookup/{SATOSHI}").json()
    assert (v["watched"], v["mine"], v["label"]) == (True, 0, "Satoshi")

    # Tracking makes it mine, private unless said otherwise, and no longer merely watched.
    assert client.post(
        "/api/tracker/wallets", json={"address": SATOSHI, "name": "Satoshi"}
    ).is_success
    analysis = client.get("/api/tracker/analysis").json()
    assert analysis["watched"] == []
    assert [(x["address"], x["public"]) for x in analysis["verdicts"]] == [(SATOSHI, False)]
    v = client.get(f"/api/tracker/lookup/{SATOSHI}").json()
    assert (v["watched"], v["mine"], v["public"]) == (False, 1, 0)

    assert client.post("/api/tracker/watch", json={"address": A}).is_success
    assert client.delete(f"/api/tracker/watch/{A}").is_success
    assert client.get("/api/tracker/analysis").json()["watched"] == []


def test_lookup_reports_an_indexer_that_does_not_answer(db, client, monkeypatch):
    monkeypatch.setitem(chains.UTXO, "bitcoin", ("BTC", "esplora", "http://down", ""))
    r = client.get(f"/api/tracker/lookup/{SATOSHI}")
    assert r.status_code == 502 and "HTTP 404" in r.json()["detail"]
    assert database.connect().execute("SELECT COUNT(*) FROM node").fetchone()[0] == 0


def test_risk_names_each_label_it_dealt_with_once():
    sanctioned = {"category": "flagged", "label": "OFAC SDN list", "mine": 0}
    kraken = {"category": "cex", "label": "Kraken", "mine": 0}
    parties = [{**sanctioned, "address": a} for a in (A, B)] + [{**kraken, "address": C}] * 2
    said = [
        r["text"] for r in lookup.risk("Ethereum address", {}, [], parties, [], now=NOW)["reasons"]
    ]
    assert sum("OFAC SDN list" in t for t in said) == 1 and sum("Kraken" in t for t in said) == 1


def judged(db, address):
    rows = db.execute("SELECT asset, chain, trust FROM balance WHERE address = ?", (address,))
    return {(r["chain"], r["asset"]): r["trust"] for r in rows}


def sync_and_mark(db):
    from tracker import prices, sync

    with httpx.Client() as web:  # the fake internet of the `client` fixture
        sync.sync_address(db, web, A)
        prices.refresh(db, web)
        tokens.mark(db, web)


def test_sync_marks_the_fake_tokens_of_my_wallets(db, client, monkeypatch):
    mine(db, A)
    db.commit()
    # An airdrop the wallet claimed itself: its claim() moves no value, but was wanted.
    claim = {"from": A, "to": D, "hash": "h7", "timeStamp": "5", "value": "0"}
    gov = {"from": D, "to": A, "hash": "h7", "timeStamp": "5", "tokenDecimal": "6"}
    gov |= {"value": "1000000", "tokenSymbol": "GOV", "contractAddress": "0x" + "8" * 40}
    monkeypatch.setitem(EXTRA, "txlist", [claim])
    monkeypatch.setitem(EXTRA, "tokentx", [gov])
    sync_and_mark(db)
    marks = judged(db, A)
    assert marks[("ethereum", "ETH")] == "verified" and marks[("polygon", "POL")] == "verified"
    assert marks[("ethereum", "USDC")] == "verified"  # on the token list
    assert marks[("ethereum", "USDС")] == "scam"  # a Cyrillic Es: dressed as USDC
    assert marks[("ethereum", "FREE")] == "scam"  # airdropped by a stranger, never moved
    assert marks[("ethereum", "GOV")] == "unknown"  # claimed by the wallet itself
    reason = db.execute("SELECT reason FROM balance WHERE asset = 'FREE'").fetchone()[0]
    assert "unasked" in reason


def test_marking_without_the_token_list_marks_nothing_fake_by_the_list(db, client, monkeypatch):
    import test_lookup

    real = test_lookup.indexers

    def down(request):
        if request.url.host == "tokens.uniswap.org":
            return httpx.Response(503)
        return real(request)

    monkeypatch.setattr(test_lookup, "indexers", down)
    mine(db, A)
    db.commit()
    sync_and_mark(db)
    marks = judged(db, A)
    assert marks[("ethereum", "ETH")] == "verified"
    assert marks[("ethereum", "USDC")] == "unknown"  # a reliable price, but no list to name it
    assert marks[("ethereum", "FREE")] == "scam"  # the airdrop rule needs no list


def test_a_database_from_before_trust_opens(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute(
        "CREATE TABLE balance(address TEXT, chain TEXT, asset TEXT, token TEXT, amount REAL, "
        "PRIMARY KEY(address, chain, token))"
    )
    old.execute("INSERT INTO balance VALUES ('a', 'bitcoin', 'BTC', '', 1)")
    old.commit()
    old.close()
    db = database.connect(str(path))
    assert {"name", "trust", "reason"} <= {
        r["name"] for r in db.execute("PRAGMA table_info(balance)")
    }
    assert tuple(db.execute("SELECT amount, trust FROM balance").fetchone()) == (1.0, "")


def test_a_new_sync_keeps_the_marks_until_they_are_judged_again(db, client):
    from tracker import sync

    mine(db, A)
    db.commit()
    sync_and_mark(db)
    with httpx.Client() as web:
        sync.sync_address(db, web, A)  # a sync that stops before marking (swaps or prices down)
    assert judged(db, A)[("ethereum", "FREE")] == "scam"
    assert judged(db, A)[("ethereum", "USDC")] == "verified"
    assert judged(db, A)[("polygon", "USDC")] == "scam"  # a balance derived from the history
