import pytest
from fastapi.testclient import TestClient

from tracker import chains, graph, heuristics, labels
from tracker import db as database

A, B, C, D = ("0x" + c * 40 for c in "abcd")
BINANCE = "0x" + "1" * 40
BRIDGE = "0x" + "2" * 40


def vin(address, value, kind="v0_p2wpkh"):
    return {"prevout": {"scriptpubkey_address": address, "value": value, "scriptpubkey_type": kind}}


def vout(address, value, kind="v0_p2wpkh"):
    return {"scriptpubkey_address": address, "value": value, "scriptpubkey_type": kind}


def tx(vins, vouts, txid="t1"):
    return {"txid": txid, "vin": vins, "vout": vouts, "status": {"block_time": 1000}}


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CT_DB", str(tmp_path / "t.db"))
    db = database.connect()
    yield db
    db.close()


def transfer(db, chain, txid, src, dst, amount, ts=0, asset="ETH", token=""):
    db.execute(
        "INSERT INTO transfer VALUES (?,?,?,?,?,?,?,?)",
        (chain, txid, src, dst, asset, token, amount, ts),
    )


def mine(db, address, public=0):
    db.execute("INSERT INTO node(address, mine, public) VALUES (?, 1, ?)", (address, public))


def test_norm_validates_and_canonicalises():
    assert database.norm(" 0xD8dA6BF26964aF9D7eEd9e03E53415D37aA96045 ") == (
        "0xd8da6bf26964af9d7eed9e03e53415d37aa96045"
    )
    assert (
        database.norm("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa") == "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"
    )
    for bad in ("", "0x123", "'; DROP TABLE node;--", "<script>"):
        with pytest.raises(ValueError):
            database.norm(bad)


def test_btc_transfers_split_outputs_pro_rata():
    t = tx([vin("me", 300), vin("other", 100)], [vout("shop", 200), vout("me", 190)])
    assert chains.btc_transfers("me", t) == [
        ("bitcoin", "t1", "me", "shop", "BTC", "", 150e-8, 1000)
    ]
    assert chains.btc_transfers("shop", t) == [
        ("bitcoin", "t1", "me", "shop", "BTC", "", 150e-8, 1000),
        ("bitcoin", "t1", "other", "shop", "BTC", "", 50e-8, 1000),
    ]


def test_common_input_links_cosigners():
    t = tx([vin("me", 300), vin("other", 100)], [vout("shop", 390)])
    assert heuristics.btc_links("me", t) == [("me", "other", "common-input", 0.9, "t1")]
    assert heuristics.btc_links("shop", t) == []


def test_coinjoin_gets_low_confidence_and_no_change_guess():
    t = tx(
        [vin("me", 110), vin("p2", 120), vin("p3", 130)],
        [vout("o1", 100), vout("o2", 100), vout("o3", 100), vout("c1", 9)],
    )
    assert {link[3] for link in heuristics.btc_links("me", t)} == {0.2}


def test_change_detection():
    def change(vouts):
        return heuristics.change_output(tx([vin("me", 1_000_000)], vouts))

    # Script type and round amount agree, only one rule fires, the rules disagree.
    assert change([vout("shop", 500_000, "p2pkh"), vout("chg", 499_123)]) == ("chg", 0.75)
    assert change([vout("shop", 500_000), vout("chg", 499_123)]) == ("chg", 0.5)
    assert change([vout("shop", 500_123, "p2pkh"), vout("chg", 499_123)]) == ("chg", 0.6)
    assert change([vout("shop", 500_123, "p2pkh"), vout("x", 400_000)]) is None
    assert change([vout("shop", 500_000), vout("me", 499_123)]) is None  # change to self


def test_evm_transfers_skip_failed_zero_and_malformed_rows():
    base = {"from": A.upper().replace("X", "x"), "to": B, "hash": "h", "timeStamp": "5"}
    items = [
        {**base, "value": "1000000000000000000"},
        {**base, "value": "1", "isError": "1"},
        {**base, "value": "0"},
        {**base, "value": "1", "to": ""},
        {"value": "nonsense"},
    ]
    assert chains.evm_transfers("ethereum", "ETH", items) == [
        ("ethereum", "h", A, B, "ETH", "", 1.0, 5)
    ]
    token = {**base, "value": "2500000", "tokenDecimal": "6", "tokenSymbol": "USDC" * 10}
    token["contractAddress"] = C.upper().replace("X", "x")
    assert chains.evm_transfers("ethereum", "ETH", [token], token=True) == [
        ("ethereum", "h", A, B, "USDCUSDCUSDCUSDCUSDC", C, 2.5, 5)
    ]


def test_bridge_matching_scores_and_penalises_ambiguity(db):
    mine(db, A)
    mine(db, B)
    db.execute("INSERT INTO label VALUES (?, 'Across', 'bridge', 'user')", (BRIDGE,))
    transfer(db, "ethereum", "out", A, BRIDGE, 1.0, ts=1000)
    transfer(db, "optimism", "in", BRIDGE, B, 0.998, ts=1200, asset="WETH", token=C)
    transfer(db, "optimism", "late", D, B, 0.998, ts=1000 + 7 * 3600)  # outside the window
    transfer(db, "optimism", "small", D, B, 0.5, ts=1100)  # amount too different
    transfer(db, "ethereum", "same-chain", D, B, 0.999, ts=1100)
    heuristics.match_bridges(db)
    (link,) = db.execute("SELECT * FROM link").fetchall()
    assert (link["a"], link["b"], link["kind"], link["confidence"]) == (A, B, "bridge", 0.95)
    assert link["evidence"] == "ethereum:out > optimism:in"

    transfer(db, "optimism", "in2", D, B, 0.99, ts=1300)  # a second plausible destination leg
    heuristics.match_bridges(db)
    assert sorted(r["confidence"] for r in db.execute("SELECT * FROM link")) == [0.42, 0.5]


def test_trace_finds_exposure_and_refuses_paths_through_services(db):
    mine(db, A, public=1)
    mine(db, B)
    mine(db, C)
    # A and B both used Binance: no on-chain link, but a shared service.
    db.execute("INSERT INTO label VALUES (?, 'Binance 14', 'cex', 'user')", (BINANCE,))
    transfer(db, "ethereum", "1", A, BINANCE, 1)
    transfer(db, "ethereum", "2", BINANCE, B, 1)
    # A and C both paid the same stranger: a one-stranger path.
    transfer(db, "ethereum", "3", A, D, 1)
    transfer(db, "ethereum", "4", C, D, 1)
    by_pair = {(t["a"], t["b"]): t for t in graph.trace(db)}
    assert by_pair[(A, B)]["path"] == [] and by_pair[(A, B)]["shared_services"] == ["Binance"]
    assert by_pair[(A, C)]["path"] == [A, D, C]
    assert by_pair[(A, C)]["confidence"] == graph.HOP_DECAY
    assert by_pair[(A, C)]["exposure"] and (B, C) not in by_pair
    assert graph.trace(db, max_hops=1) == [by_pair[(A, B)]]


def test_trace_prefers_strong_path_within_hop_limit(db):
    mine(db, A)
    mine(db, B)
    db.execute("INSERT INTO link VALUES (?, ?, 'common-input', 0.1, 'x')", (A, B))
    transfer(db, "ethereum", "1", A, D, 1)
    transfer(db, "ethereum", "2", D, B, 1)
    (t,) = graph.trace(db)
    assert t["path"] == [A, D, B] and t["kinds"] == ["transfer", "transfer"]
    (t,) = graph.trace(db, max_hops=1)
    assert t["path"] == [A, B] and t["confidence"] == 0.1


def test_graph_hides_tokens_i_never_sent(db):
    mine(db, A)
    transfer(db, "ethereum", "1", A, B, 1)
    transfer(db, "ethereum", "2", D, A, 9, asset="SCAM", token=C)
    assert [e["asset"] for e in graph.build(db)["edges"]] == ["ETH"]
    assert len(graph.build(db, spam=True)["edges"]) == 2


def test_combine_evidence():
    assert graph.combine([0.5, 0.5]) == 0.75


def test_labels_parse_and_keep_user_labels(db):
    rows = list(
        labels.parse_etherscan(
            {
                A.upper().replace("X", "x"): {"name": "Binance 14", "labels": ["binance"]},
                B: {"name": "Uniswap V2: Router 2", "labels": ["dex"]},
                C: {"name": "", "labels": []},
            }
        )
    )
    rows += labels.parse_tagpack(
        "label: kraken.com\ncategory: exchange\ncurrency: BTC\n"
        "tags:\n- address: 1Kraken\n- address: 0xABC\n  currency: ETH\n  label: Tornado\n"
        "  category: mixing_service\n- address: ltc1\n  currency: LTC\n"
    )
    assert [(r[0], r[2]) for r in rows] == [
        (A, "cex"),
        (B, "dex"),
        ("1Kraken", "cex"),
        ("0xabc", "mixer"),
    ]
    db.execute("INSERT INTO label VALUES (?, 'my cold wallet', 'other', 'user')", (A,))
    labels.store(db, rows)
    assert db.execute("SELECT name FROM label WHERE address = ?", (A,)).fetchone()[0] == (
        "my cold wallet"
    )
    assert db.execute("SELECT COUNT(*) FROM label").fetchone()[0] == 4


def test_api_wallets_labels_and_auth(db, monkeypatch):
    from tracker.app import app

    client = TestClient(app)
    assert client.post("/api/wallets", json={"address": "nope"}).status_code == 422
    assert client.post(
        "/api/wallets", json={"address": A, "name": "hot", "public": True}
    ).is_success
    assert (
        client.post("/api/labels", json={"address": B, "name": "x", "category": "?"}).status_code
        == 422
    )
    assert client.post(
        "/api/labels", json={"address": B, "name": "Kraken", "category": "cex"}
    ).is_success
    (node,) = client.get("/api/graph").json()["nodes"]
    assert (node["address"], node["mine"], node["public"], node["name"]) == (A, 1, 1, "hot")
    assert client.delete(f"/api/wallets/{A}").is_success
    assert client.get("/api/graph").json()["nodes"][0]["mine"] == 0
    assert client.get("/api/trace").json() == []
    assert client.get("/").status_code == 200

    monkeypatch.setenv("CT_PASSWORD", "s3cret")
    assert client.get("/api/graph").status_code == 401
    assert client.get("/api/graph", auth=("any", "wrong")).status_code == 401
    assert client.get("/api/graph", auth=("any", "s3cret")).status_code == 200


def test_sync_evm_address_reports_bad_chain_and_derives_token_balance(db, monkeypatch):
    import httpx

    from tracker import sync

    row = {"from": D, "to": A, "hash": "h1", "timeStamp": "5"}
    answers = {
        "txlist": {"status": "1", "result": [{**row, "value": str(2 * 10**18)}]},
        "txlistinternal": {"status": "0", "message": "No transactions found", "result": []},
        "tokentx": {
            "status": "1",
            "result": [
                {**row, "value": "7000000", "tokenDecimal": "6", "tokenSymbol": "USDC"}
                | {"contractAddress": C}
            ],
        },
        "balance": {"status": "1", "result": str(2 * 10**18)},
    }

    def handler(request):
        if request.url.host == "bad":
            return httpx.Response(200, json={"status": "0", "message": "chain not supported"})
        return httpx.Response(200, json=answers[request.url.params["action"]])

    monkeypatch.setattr(chains, "DELAY", 0)
    monkeypatch.setattr(
        chains,
        "DEFAULT_EVM",
        {
            "good": {"url": "http://good/api", "symbol": "ETH"},
            "bad": {"url": "http://bad/api", "symbol": "X"},
        },
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        sync.sync_address(db, client, A)
    node = db.execute("SELECT * FROM node WHERE address = ?", (A,)).fetchone()
    assert node["synced_at"] and node["error"] == "bad: txlist: chain not supported None"
    assert db.execute("SELECT COUNT(*) FROM transfer").fetchone()[0] == 2
    assert {(r["asset"], r["amount"]) for r in db.execute("SELECT * FROM balance")} == {
        ("ETH", 2.0),
        ("USDC", 7.0),
    }


def test_rate_limit_is_reported_not_swallowed(monkeypatch):
    import httpx

    monkeypatch.setattr(chains, "DELAY", 0)
    monkeypatch.setattr(chains.time, "sleep", lambda s: None)
    transport = httpx.MockTransport(lambda request: httpx.Response(429))
    with httpx.Client(transport=transport) as client, pytest.raises(RuntimeError, match="429"):
        chains.get(client, "http://x/")
