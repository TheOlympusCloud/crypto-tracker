import pytest
from fastapi.testclient import TestClient

from tracker import chains, graph, heuristics, labels, swaps
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
    db.execute(
        "INSERT INTO label(address, name, category, source) VALUES (?, 'Across', 'bridge', 'user')",
        (BRIDGE,),
    )
    transfer(db, "ethereum", "out", A, BRIDGE, 1.0, ts=1000)
    transfer(db, "optimism", "in", BRIDGE, B, 0.998, ts=1200, asset="WETH", token=C)
    transfer(db, "optimism", "late", D, B, 0.998, ts=1000 + 7 * 3600)  # outside the window
    transfer(db, "optimism", "small", D, B, 0.5, ts=1100)  # amount too different
    transfer(db, "ethereum", "same-chain", D, B, 0.999, ts=1100)
    heuristics.match_bridges(db)
    (link,) = db.execute("SELECT * FROM link").fetchall()
    assert (link["a"], link["b"], link["kind"], link["confidence"]) == (A, B, "bridge", 0.95)
    assert link["evidence"] == "ethereum:out > optimism:in"

    transfer(db, "optimism", "fake", D, B, 1.0, ts=1100, asset="ETH", token=D)  # airdropped
    heuristics.match_bridges(db)
    assert db.execute("SELECT COUNT(*) FROM link").fetchone()[0] == 1

    transfer(db, "optimism", "in2", D, B, 0.99, ts=1300)  # a second plausible destination leg
    heuristics.match_bridges(db)
    assert sorted(r["confidence"] for r in db.execute("SELECT * FROM link")) == [0.42, 0.5]


def test_trace_finds_exposure_and_refuses_paths_through_services(db):
    mine(db, A, public=1)
    mine(db, B)
    mine(db, C)
    # A and B both used Binance: no on-chain link, but a shared service.
    db.execute(
        "INSERT INTO label(address, name, category, source) VALUES (?, 'Binance 14', 'cex', 'user')",
        (BINANCE,),
    )
    transfer(db, "ethereum", "1", A, BINANCE, 1)
    transfer(db, "ethereum", "2", BINANCE, B, 1)
    # A and C both paid the same stranger: a one-stranger path.
    transfer(db, "ethereum", "3", A, D, 1)
    transfer(db, "ethereum", "4", C, D, 1)
    by_pair = {(t["a"], t["b"]): t for t in graph.trace(db)}
    assert by_pair[(A, B)]["path"] == [] and by_pair[(A, B)]["shared_services"] == ["Binance"]
    assert by_pair[(A, C)]["path"] == [A, D, C]
    assert by_pair[(A, C)]["confidence"] == graph.HOP_DECAY
    assert (B, C) not in by_pair
    assert graph.trace(db, max_hops=1) == [by_pair[(A, B)]]

    verdict = {v["address"]: v for v in graph.verdicts(db)}
    assert verdict[B]["links"] == []  # nothing found on-chain...
    assert verdict[B]["shared_services"] == [{"wallet": A, "services": ["Binance"]}]  # ...but
    assert verdict[B]["services"] == [{"name": "Binance", "category": "cex", "guess": False}]
    (link,) = verdict[C]["links"]
    assert (link["wallet"], link["public"], link["confidence"]) == (A, True, 0.7)
    assert verdict[C]["coverage"] == {
        "synced": False,
        "truncated": False,
        "error": None,
        "counterparties": 1,
        "expanded": 0,
        "filling": None,  # no full history being read
        "notes": [
            (
                "Not read on arbitrum, polygon. Needs a free Blockscout key (CT_BLOCKSCOUT_KEY) "
                "or a free Etherscan key (CT_ETHERSCAN_KEY)."
            ),
            "Not read on base, optimism. Needs a free Blockscout key (CT_BLOCKSCOUT_KEY).",
            (
                "Not read on bsc. Needs a free NodeReal key (CT_NODEREAL_KEY): Etherscan's free "
                "plan no longer covers BNB Chain."
            ),
            swaps.NOTE,
        ],
    }


def test_private_wallet_behind_another_private_wallet_is_exposed(db):
    mine(db, A, public=1)
    mine(db, B)
    mine(db, C)
    transfer(db, "ethereum", "1", A, B, 1)
    transfer(db, "ethereum", "2", B, C, 1)
    db.execute("INSERT INTO link VALUES (?, ?, 'change', 0.5, 'x')", (B, C))  # weaker, ignored
    links = {v["address"]: v["links"] for v in graph.verdicts(db)}
    (to_a,) = [k for k in links[C] if k["wallet"] == A]
    assert to_a["public"] and to_a["confidence"] == 1.0
    assert to_a["path"] == [C, B, A] and to_a["via"] == [B]
    assert [k["wallet"] for k in links[A]] == [B, C]


def test_origins_follow_funds_upstream_pro_rata(db):
    mine(db, A)
    mine(db, B)
    db.execute(
        "INSERT INTO label(address, name, category, source) VALUES (?, 'Kraken 4', 'cex', 'user')",
        (BINANCE,),
    )
    db.execute("INSERT INTO node(address, synced_at) VALUES (?, 1)", (D,))
    transfer(db, "ethereum", "1", D, A, 4)  # a stranger forwards 4 ETH to A...
    transfer(db, "ethereum", "2", BINANCE, D, 3)  # ...which it got 3:1 from an exchange
    transfer(db, "ethereum", "3", C, D, 1)  # and from an address nobody expanded
    transfer(db, "ethereum", "4", B, A, 4)  # plus 4 ETH straight from my other wallet
    (eth,) = graph.origins(db)[A]
    assert (eth["asset"], eth["received"]) == ("ETH", 8)
    got = {s["address"]: (s["kind"], s["reason"], s["share"], s["path"]) for s in eth["sources"]}
    assert got == {
        B: ("own", "", 0.5, [A, B]),
        BINANCE: ("cex", "", 0.375, [A, D, BINANCE]),
        C: ("unknown", "not expanded", 0.125, [A, D, C]),
    }
    assert [s["public"] for s in eth["sources"]] == [False, False, False]
    (eth,) = graph.origins(db, max_hops=1)[A]
    assert {s["address"]: s["reason"] for s in eth["sources"]} == {B: "", D: "hop limit"}

    db.execute("UPDATE node SET public = 1 WHERE address = ?", (B,))  # funds from a public wallet
    (eth,) = graph.origins(db)[A]
    assert [(s["kind"], s["public"]) for s in eth["sources"] if s["address"] == B] == [
        ("own", True)
    ]


def test_origins_stay_fast_on_a_dense_graph_with_cycles(db):
    import time

    mine(db, A)
    crowd = [f"0x{i:040x}" for i in range(100, 140)]
    db.executemany("INSERT INTO node(address, synced_at) VALUES (?, 1)", [(a,) for a in crowd])
    transfer(db, "ethereum", "in", crowd[0], A, 1)
    for i, dst in enumerate(crowd):  # everyone is funded by the next 15, in a ring
        for k in range(1, 16):
            transfer(db, "ethereum", f"{i}-{k}", crowd[(i + k) % 40], dst, k)
    start = time.perf_counter()
    (eth,) = graph.origins(db, max_hops=6)[A]
    assert time.perf_counter() - start < 2  # took 75 s before the walk was memoised
    assert 0.5 < sum(s["share"] for s in eth["sources"]) <= 1.0001  # the 12 largest sources
    assert {s["reason"] for s in eth["sources"]} == {"hop limit"}


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
        "  category: mixing_service\n- address: 4xmr\n  currency: XMR\n"
    )
    assert [(r[0], r[2]) for r in rows] == [
        (A, "cex"),
        (B, "dex"),
        ("1Kraken", "cex"),
        ("0xabc", "mixer"),
    ]
    db.execute(
        "INSERT INTO label(address, name, category, source) VALUES (?, 'my cold wallet', 'other', 'user')",
        (A,),
    )
    labels.store(db, rows)
    assert db.execute("SELECT name FROM label WHERE address = ?", (A,)).fetchone()[0] == (
        "my cold wallet"
    )
    assert db.execute("SELECT COUNT(*) FROM label").fetchone()[0] == 4


def test_api_wallets_labels_and_auth(db, monkeypatch):
    from tracker.app import app

    client = TestClient(app, base_url="http://localhost")
    assert client.post("/api/tracker/wallets", json={"address": "nope"}).status_code == 422
    assert client.post(
        "/api/tracker/wallets", json={"address": A, "name": "hot", "public": True}
    ).is_success
    assert (
        client.post(
            "/api/tracker/labels", json={"address": B, "name": "x", "category": "?"}
        ).status_code
        == 422
    )
    assert client.post(
        "/api/tracker/labels", json={"address": B, "name": "Kraken", "category": "cex"}
    ).is_success
    (node,) = client.get("/api/tracker/graph").json()["nodes"]
    assert (node["address"], node["mine"], node["public"], node["name"]) == (A, 1, 1, "hot")
    assert client.delete(f"/api/tracker/wallets/{A}").is_success
    assert client.get("/api/tracker/graph").json()["nodes"][0]["mine"] == 0
    assert client.get("/api/tracker/analysis").json()["verdicts"] == []
    # A page that rebinds its own domain to this port must not read the wallet list.
    assert client.get("/api/tracker/graph", headers={"host": "evil.example"}).status_code == 403

    monkeypatch.setenv("CT_PASSWORD", "s3cret")
    assert client.get("/api/tracker/graph").status_code == 401
    assert client.get("/api/tracker/graph", auth=("any", "wrong")).status_code == 401
    assert client.get("/api/tracker/graph", auth=("any", "s3cret")).status_code == 200
    remote = {"host": "tracker.example"}
    assert (
        client.get("/api/tracker/graph", auth=("any", "s3cret"), headers=remote).status_code == 200
    )


def test_one_good_password_keeps_a_phone_signed_in(db, monkeypatch, tmp_path):
    """Home-screen apps and the Android app's WebView forget HTTP Basic between launches, so
    one good password earns a cookie, which they keep."""
    from tracker.app import app

    monkeypatch.setenv("CT_PASSWORD", "s3cret")
    phone = TestClient(app, base_url="http://localhost")
    assert phone.get("/api/tracker/graph", auth=("any", "wrong")).status_code == 401
    assert not phone.cookies  # a wrong password earns nothing
    cookie = phone.get("/api/tracker/graph", auth=("any", "s3cret")).headers["set-cookie"]
    assert "s3cret" not in cookie  # it proves the password without carrying it
    for flag in ("HttpOnly", "Path=/", "SameSite=lax", "Max-Age=34560000"):
        assert flag in cookie
    assert "Secure" not in cookie  # over plain http a Secure cookie would never come back
    assert phone.get("/api/tracker/graph").status_code == 200  # the next launch: the cookie alone
    # Behind TLS, also when a reverse proxy ends it, the cookie only travels encrypted.
    https = TestClient(app, base_url="https://localhost")
    assert "Secure" in https.get("/", auth=("any", "s3cret")).headers["set-cookie"]
    proxied = {"x-forwarded-proto": "https"}
    assert (
        "Secure"
        in TestClient(app, base_url="http://localhost")
        .get("/", auth=("any", "s3cret"), headers=proxied)
        .headers["set-cookie"]
    )
    # A made-up cookie is refused, and a new password signs every device out.
    forged = TestClient(app, base_url="http://localhost", cookies={"ct_session": "0" * 64})
    assert forged.get("/api/tracker/graph").status_code == 401
    # The cookie is keyed by a random secret of this install: it tells nothing about the
    # password, outlives a restart, and is worth nothing at another install.
    import tracker.app as server

    server.SECRETS.clear()  # a restart
    assert phone.get("/api/tracker/graph").status_code == 200
    monkeypatch.setenv("CT_DB", str(tmp_path / "another-install.db"))
    assert phone.get("/api/tracker/graph").status_code == 401
    monkeypatch.setenv("CT_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("CT_PASSWORD", "n3w")
    assert phone.get("/api/tracker/graph").status_code == 401


def test_the_session_secret_does_not_break_the_label_refresh(db, monkeypatch):
    """meta is shared: the cookie's secret (hex) sits next to the label sets' dates, and a
    sync used to fail on it with "could not convert string to float"."""
    import tracker.app as server

    server.session("s3cret")  # what the first request with a password does
    assert labels.stale(db) == labels.SETS  # never downloaded: all due, and no crash


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


def test_errors_never_contain_the_api_key(db, monkeypatch):
    import httpx

    from tracker import sync

    monkeypatch.setattr(chains, "DELAY", 0)
    monkeypatch.setattr(
        chains, "DEFAULT_EVM", {"x": {"url": "http://b/api", "symbol": "ETH", "apikey": "SECRET"}}
    )

    def refuse(request):
        return httpx.Response(403)

    def unreachable(request):
        raise httpx.ConnectError("no route", request=request)

    for handler in (refuse, unreachable):
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            sync.sync_address(db, client, A)
        error = db.execute("SELECT error FROM node WHERE address = ?", (A,)).fetchone()[0]
        assert error in ("x: HTTP 403", "x: ConnectError") and "SECRET" not in error


def test_failed_address_does_not_stop_the_run_and_bad_counterparties_are_dropped(db, monkeypatch):
    import httpx

    from tracker import sync

    monkeypatch.setattr(chains, "DELAY", 0)
    monkeypatch.setattr(chains.time, "sleep", lambda s: None)
    monkeypatch.setattr(chains, "DEFAULT_EVM", {"x": {"url": "http://evm/api", "symbol": "ETH"}})
    rows = [
        {"from": "../../admin", "to": A, "hash": "h1", "timeStamp": "5", "value": "1"},
        {"from": D, "to": A, "hash": "h2", "timeStamp": "5", "value": "1"},
    ]

    def handler(request):
        if request.url.host != "evm":
            return httpx.Response(429)  # Bitcoin indexer is rate limiting
        ok = request.url.params["action"] == "txlist"
        return httpx.Response(200, json={"status": "1", "result": rows if ok else []})

    btc = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        sync.sync_address(db, client, btc)
        sync.sync_address(db, client, A)
    assert "429" in db.execute("SELECT error FROM node WHERE address = ?", (btc,)).fetchone()[0]
    assert [r["src"] for r in db.execute("SELECT src FROM transfer")] == [D]


ZPUB = "zpub6rFR7y4Q2AijBEqTUquhVz398htDFrtymD9xYYfG1m4wAcvPhXNfE3EfH1r1ADqtfSdVCToUG868RvUUkgDKf31mGDtKsAYz2oz2AGutZYs"
XPRV = "xprv9s21ZrQH143K3QTDL4LXw2F7HEK3wJUD2nW2nRk4stbPy6cq3jPPqjiChkVvvNKmPGJxWUtg6LnF5kejMRNNU3TGtRBeJgk33yuGBxrMPHi"
BIP84 = ["bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu", "bc1qnjg0jd8228aq7egyzacy8cys3knf9xvrerkf9g"]
SATOSHI = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"


def test_hd_derivation_matches_bip84_and_refuses_private_keys():
    from tracker import hd

    (d,) = hd.descriptors(ZPUB)
    assert [hd.address(d, 0, i) for i in (0, 1)] == BIP84  # the BIP84 test vector
    assert hd.address(d, 1, 0) == "bc1q8c6fshw2dlwun7ekn9qwf37cu2rn755upcp6el"
    xpub = "xpub6CatWdiZiodmUeTDp8LT5or8nmbKNcuyvz7WyksVFkKB4RHwCD3XyuvPEbvqAQY3rAPshWcMLoP2fMFMKHPJ4ZeZXYVUhLv1VMrjPC7PW6V"
    assert len(hd.descriptors(xpub)) == 4  # a plain xpub does not say which address type
    (d,) = hd.descriptors(f"wpkh({xpub}/0/*)")
    assert hd.address(d, 0, 1) == BIP84[1] and d.num_branches == 1
    for secret in (XPRV, f"wpkh({XPRV}/0/*)"):
        with pytest.raises(ValueError, match="private key"):
            hd.descriptors(secret)
    for junk in ("xpubnonsense", "wpkh(", "zpub" + "1" * 107):
        with pytest.raises(ValueError, match="not a valid"):
            hd.descriptors(junk)
    assert hd.looks_like(ZPUB) and hd.looks_like(XPRV) and not hd.looks_like(BIP84[0])


def test_hd_wallet_is_discovered_grouped_and_treated_as_one_wallet(db, monkeypatch):
    import httpx

    from tracker import hd, sync

    monkeypatch.setattr(chains, "DELAY", 0)
    monkeypatch.setattr(hd, "GAP", 3)
    (d,) = hd.descriptors(ZPUB)
    first, third, change = hd.address(d, 0, 0), hd.address(d, 0, 2), hd.address(d, 1, 0)
    history = {
        first: [tx([vin(SATOSHI, 5000)], [vout(first, 5000)], "fund1")],
        third: [tx([vin(SATOSHI, 7000)], [vout(third, 7000)], "fund2")],
        # Both receive addresses are spent together, with change back into the wallet.
        change: [
            tx(
                [vin(first, 5000), vin(third, 7000)],
                [vout(SATOSHI, 9000), vout(change, 2900)],
                "spend",
            )
        ],
    }
    history[first].append(history[change][0])
    history[third].append(history[change][0])
    asked = []

    def handler(request):
        address = request.url.path.split("/")[3]  # /api/address/<address>/...
        asked.append(address)
        if request.url.path.endswith("/txs/chain"):
            return httpx.Response(200, json=history.get(address, []))
        stats = {"funded_txo_sum": 2900 if address == change else 0, "spent_txo_sum": 0}
        return httpx.Response(200, json={"chain_stats": {**stats, "tx_count": 1}})

    client = TestClient(
        __import__("tracker.app", fromlist=["app"]).app, base_url="http://localhost"
    )
    assert client.post("/api/tracker/wallets", json={"address": XPRV}).status_code == 422
    made = client.post(
        "/api/tracker/wallets", json={"address": ZPUB, "name": "Ledger", "public": True}
    ).json()
    wallet = db.execute("SELECT * FROM hdwallet").fetchone()
    assert wallet["id"] == made["wallet"] and wallet["id"].startswith("hd:")
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        assert sync.discover(db, http, wallet) == [first, third, change]
    # Receive branch: used, unused, used, then three unused in a row end the scan.
    assert len(set(asked)) == 6 + 4
    rows = {r["address"]: r for r in db.execute("SELECT * FROM node WHERE mine")}
    assert set(rows) == {first, third, change}
    assert {(r["wallet"], r["name"], r["public"]) for r in rows.values()} == {
        (wallet["id"], "Ledger", 1)
    }
    assert (
        db.execute("SELECT COUNT(*) FROM node WHERE NOT mine AND address LIKE 'bc1%'").fetchone()[0]
        == 0
    )

    # Common-input links inside the wallet are not a finding; the wallet is one verdict.
    assert db.execute("SELECT COUNT(*) FROM link WHERE kind = 'common-input'").fetchone()[0] == 1
    (verdict,) = graph.verdicts(db)
    assert verdict["wallet"] == wallet["id"] and verdict["addresses"] == sorted(rows)
    assert verdict["links"] == [] and verdict["name"] == "Ledger" and verdict["public"]
    # Its funds came from outside; the change it paid itself is not a source.
    (btc,) = graph.origins(db)[verdict["address"]]
    assert btc["received"] == pytest.approx(12000e-8)
    assert [s["address"] for s in btc["sources"]] == [SATOSHI]

    from tracker import prices

    db.execute("INSERT INTO price VALUES ('coingecko:bitcoin', 50000, 1, 'BTC', 0.99)")
    db.commit()
    p = prices.portfolio(db)
    (held,) = p["wallets"]
    assert (held["id"], held["kind"], held["name"], len(held["addresses"])) == (
        wallet["id"],
        "hd",
        "Ledger",
        3,
    )
    assert held["value"] == pytest.approx(2900e-8 * 50000) == p["public"] == p["total"]
    assert p["private"] == 0 and p["assets"][0]["price"] == pytest.approx(50000)

    card = next(n for n in graph.build(db)["nodes"] if n.get("addresses"))
    assert card["address"] == min(rows) and [a["address"] for a in card["addresses"]] == sorted(
        rows
    )
    assert card["wallet_balances"] == [
        {"chain": "bitcoin", "asset": "BTC", "token": "", "amount": pytest.approx(2900e-8)}
    ]
    assert client.get("/api/tracker/analysis").json()["fiat"]["wallets"] == {
        card["address"]: pytest.approx(1.45)
    }

    # Making one of its addresses private speaks for the whole wallet; removing it forgets it.
    client.post("/api/tracker/wallets", json={"address": first, "name": "Ledger", "public": False})
    assert {r["public"] for r in db.execute("SELECT public FROM node WHERE mine")} == {0}
    assert (
        "descriptor" not in str(client.get("/api/tracker/portfolio").json())
        and ZPUB not in client.get("/api/tracker/portfolio").text
    )
    assert client.delete(f"/api/tracker/wallets/{wallet['id']}").is_success
    assert db.execute("SELECT COUNT(*) FROM node WHERE mine").fetchone()[0] == 0


def test_links_chain_through_the_addresses_of_an_hd_wallet(db):
    mine(db, A, public=1)
    mine(db, D)
    for address in (B, C):  # one HD wallet with two addresses
        db.execute("INSERT INTO node(address, mine, wallet) VALUES (?, 1, 'hd:x')", (address,))
    transfer(db, "ethereum", "1", A, B, 1)
    transfer(db, "ethereum", "2", C, D, 1)
    links = {v["address"]: v["links"] for v in graph.verdicts(db)}
    (to_a,) = [k for k in links[D] if k["wallet"] == A]
    assert to_a["confidence"] == 1.0 and to_a["via"] == [B] and to_a["path"] == [D, C, B, A]
    assert [k["wallet_id"] for k in links[B]] == [A, D]


def test_exchange_flow_links_deposit_and_withdrawal(db):
    mine(db, A, public=1)
    mine(db, B)
    deposit, other = "0x" + "3" * 40, "0x" + "4" * 40
    db.execute(
        "INSERT INTO label(address, name, category, source) VALUES (?, 'Kraken 4', 'cex', 'user')",
        (BINANCE,),
    )
    db.execute(
        "INSERT INTO label(address, name, category, source) VALUES (?, 'Kraken 7', 'cex', 'user')",
        (BRIDGE,),
    )
    transfer(db, "ethereum", "dep", A, deposit, 1.37, ts=1000)
    transfer(db, "ethereum", "sweep", deposit, BINANCE, 1.37, ts=1500)  # so it is a deposit address
    transfer(db, "ethereum", "wd", BRIDGE, B, 1.365, ts=1000 + 1800)  # 0.4% less, 30 min later
    transfer(db, "ethereum", "late", BRIDGE, B, 1.365, ts=1000 + 3 * 86400)
    transfer(db, "ethereum", "stranger", other, B, 1.37, ts=1100)  # not from an exchange
    heuristics.match_exchange_flows(db)
    (link,) = db.execute("SELECT * FROM link").fetchall()
    assert (link["a"], link["b"], link["kind"], link["confidence"]) == (A, B, "exchange-flow", 0.75)
    assert link["evidence"] == "ethereum:dep > Kraken > ethereum:wd"
    (verdict,) = [v for v in graph.verdicts(db) if v["address"] == B]
    assert verdict["links"][0]["kinds"] == ["exchange-flow"] and verdict["links"][0]["public"]

    # A round amount to an address nobody expanded is only a weak guess.
    db.execute("DELETE FROM transfer")
    transfer(db, "ethereum", "dep", A, deposit, 2.0, ts=1000)
    transfer(db, "ethereum", "wd", BRIDGE, B, 1.97, ts=9000)
    heuristics.match_exchange_flows(db)
    assert [r["confidence"] for r in db.execute("SELECT * FROM link")] == [0.09]
    # ...and none at all once that address is known not to be an exchange deposit.
    db.execute("INSERT INTO node(address, synced_at) VALUES (?, 1)", (deposit,))
    heuristics.match_exchange_flows(db)
    assert db.execute("SELECT COUNT(*) FROM link").fetchone()[0] == 0


def test_alert_when_a_link_appears_or_gets_stronger(db):
    from tracker import alerts

    mine(db, A, public=1)
    mine(db, B)
    db.execute("UPDATE node SET name = 'Hot' WHERE address = ?", (A,))
    alerts.check(db)
    assert db.execute("SELECT COUNT(*) FROM alert").fetchone()[0] == 0
    db.execute("INSERT INTO link VALUES (?, ?, 'change', 0.5, 'x')", (A, B))
    alerts.check(db)
    alerts.check(db)  # nothing new the second time
    (alert,) = db.execute("SELECT * FROM alert").fetchall()
    assert "Hot can be tied to" in alert["text"] and "50%" in alert["text"]
    assert "private wallet is now tied to a public one" in alert["text"] and not alert["seen"]
    transfer(db, "ethereum", "1", A, B, 1)
    alerts.check(db)
    assert (
        "100% confidence (was 50%)"
        in db.execute("SELECT text FROM alert ORDER BY id DESC").fetchone()[0]
    )


def test_prices_are_fetched_cached_and_survive_an_outage(db, monkeypatch):
    import httpx

    from tracker import prices

    mine(db, A)
    transfer(db, "ethereum", "1", A, B, 1, asset="USDC", token=C)  # makes USDC a trusted token
    db.executemany(
        "INSERT INTO balance(address, chain, asset, token, amount) VALUES (?, 'ethereum', ?, ?, ?)",
        [(A, "ETH", "", 2.0), (A, "USDC", C, 100.0), (A, "SCAM", D, 9e9)],
    )
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.host == "api.frankfurter.app":
            return httpx.Response(200, json={"rates": {"EUR": 0.5}})
        return httpx.Response(
            200,
            json={"coins": {"coingecko:ethereum": {"price": 2000}, f"ethereum:{C}": {"price": 1}}},
        )

    monkeypatch.setenv("CT_CURRENCY", "eur")
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        prices.snapshot(db, http)
        prices.refresh(db, http)  # cached: no second round of calls
    # Token contracts are asked about (also the unknown one, once); my address never is.
    assert len(calls) == 2 and A not in "".join(calls) and D in calls[0]
    p = prices.portfolio(db)
    assert (p["currency"], p["total"], p["private"], p["public"]) == ("EUR", 2050.0, 2050.0, 0)
    assert [(a["asset"], a["value"]) for a in p["assets"]] == [("ETH", 2000.0), ("USDC", 50.0)]
    assert [(h["private"], h["currency"]) for h in p["history"]] == [(2050.0, "EUR")]

    db.execute("UPDATE price SET ts = 0")
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as http:
        prices.refresh(db, http)
    assert prices.portfolio(db)["total"] == 2050.0  # stale, not gone
    monkeypatch.setenv("CT_PRICES", "off")
    client = TestClient(
        __import__("tracker.app", fromlist=["app"]).app, base_url="http://localhost"
    )
    assert client.get("/api/tracker/portfolio").json()["total"] == 2050.0
    assert client.get("/api/tracker/alerts").json() == {"unread": 0, "items": []}
    assert client.post("/api/tracker/alerts/seen").is_success
    fiat = client.get("/api/tracker/analysis").json()["fiat"]
    assert (fiat["currency"], fiat["total"], fiat["assets"], fiat["wallets"]) == (
        "EUR", 2050.0, {"ETH": 2000.0, "USDC": 50.0}, {A: 2050.0},
    )  # fmt: skip


LTC, DOGE, TRX = (
    "LaMT348PWRnrqeeWArpwQPbuanpXDZGEUz",
    "DH5yaieqoZN36fDVciNyRueRGvGLR3mr7L",
    "TLa2f6VPqDgRE67v1736s7bJ8Ray5wYjU7",
)
BCH = "bitcoincash:qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a"
SOL = "vines1vzrYbzLMRdu58ou5XTby4qAqVRLmqo36NKPTg"


def test_addresses_are_recognised_per_chain_and_typos_refused():
    cases = {
        "0xD8dA6BF26964aF9D7eEd9e03E53415D37aA96045": (
            "evm",
            "0xd8da6bf26964af9d7eed9e03e53415d37aa96045",
        ),
        BIP84[0].upper(): ("bitcoin", BIP84[0]),
        "bc1p8knh0enfv47gmpuf66528zd4jtkgjq4sv5w5l2gqwgk8exu2ynns9g8c9m": ("bitcoin", None),
        SATOSHI: ("bitcoin", SATOSHI),
        LTC: ("litecoin", LTC),
        "ltc1qsph85c96stnph4avsn52ekhvkt3gv72khcfqu9": ("litecoin", None),
        DOGE: ("dogecoin", DOGE),
        BCH.removeprefix("bitcoincash:"): ("bitcoincash", BCH),
        TRX: ("tron", TRX),
        SOL: ("solana", SOL),
    }
    for raw, (chain, canonical) in cases.items():
        assert database.parse(f" {raw} ") == (chain, canonical or raw)
    # One wrong character fails the checksum instead of becoming an empty wallet.
    for typo in (SATOSHI[:-1] + "b", BIP84[0][:-1] + "x", TRX[:-1] + "8", "0x123", "hello", ""):
        with pytest.raises(ValueError):
            database.parse(typo)


def test_blockscout_answers_with_what_it_has_and_names_its_tokens(monkeypatch):
    import httpx

    token = "0x" + "ab" * 20
    answers = {
        "txlist": {"status": "1", "message": "OK", "result": []},
        # Blockscout (seen on Optimism): some blocks are not traced yet, here is the rest.
        "txlistinternal": {
            "status": "2",
            "message": "Some internal transactions within this block range have not yet been "
            "processed",
            "result": [],
        },
        "tokentx": {"status": "0", "message": "No token transfers found", "result": []},
        "balance": {"status": "1", "result": "0"},
        "tokenlist": {
            "status": "1",
            "result": [
                {"type": "ERC-20", "balance": "5000000", "decimals": "6", "symbol": "USDC"}
                | {"name": "USD Coin", "contractAddress": token.upper().replace("0X", "0x")},
                {"type": "ERC-721", "balance": "1", "decimals": "", "symbol": "NFT"}
                | {"name": "x", "contractAddress": "0x" + "cd" * 20},
            ],
        },
    }
    monkeypatch.setattr(chains, "DELAY", 0)
    reply = lambda r: httpx.Response(200, json=answers[r.url.params["action"]])
    op = {"op": {"url": "http://bs/api", "symbol": "ETH", "tokenlist": True}}
    with httpx.Client(transport=httpx.MockTransport(reply)) as client:
        out = chains.fetch_evm(client, A, op)
    assert out["errors"] == []  # the chain's other lists and its tokens are still read
    assert out["balances"] == [("op", "USDC", token, 5.0)]
    assert out["names"] == {("op", token): "USD Coin"}  # the fake-token check reads names


def test_evm_chains_are_built_in_and_say_when_they_need_a_key(monkeypatch):
    import httpx

    without = chains.builtin_evm()
    etherscan, blockscout = chains.builtin_evm(etherscan="KEY"), chains.builtin_evm(blockscout="BK")
    assert set(without) == {
        "ethereum",
        "base",
        "arbitrum",
        "optimism",
        "polygon",
        "bsc",
        "avalanche",
    }

    def readable(evm):
        return [c for c, cfg in evm.items() if not cfg.get("needs_key")]

    assert readable(without) == ["ethereum", "avalanche"]  # Routescan, without a key
    # Etherscan's free plan still covers Arbitrum and Polygon, no longer Base, OP or BNB Chain:
    # asking it for those fails on every sync.
    assert readable(etherscan) == ["ethereum", "arbitrum", "polygon", "avalanche"]
    assert etherscan["polygon"]["params"] == {"chainid": "137"}
    assert etherscan["polygon"]["apikey"] == "KEY"
    # One free Blockscout key reads Base, Arbitrum, Optimism and Polygon, token list included.
    assert readable(blockscout) == [
        "ethereum", "base", "arbitrum", "optimism", "polygon", "avalanche"
    ]  # fmt: skip
    base = blockscout["base"]
    assert (base["url"], base["params"], base["apikey"]) == (
        "https://api.blockscout.com/v2/api",
        {"chain_id": "8453"},
        "BK",
    )
    assert base["tokenlist"]
    # Each chain not read says what it takes.
    assert "CT_BLOCKSCOUT_KEY" in without["base"]["needs_key"]
    assert "BNB Chain" in blockscout["bsc"]["needs_key"]
    assert (without["polygon"]["symbol"], without["bsc"]["llama"]) == ("POL", "bsc")

    monkeypatch.setattr(chains, "DELAY", 0)
    monkeypatch.setattr(chains, "DEFAULT_EVM", without)
    asked = []

    def handler(request):
        asked.append(str(request.url))
        return httpx.Response(200, json={"status": "1", "result": []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert chains.fetch(client, A)["errors"] == []  # a missing key is not an error per address
    assert {u.split("/evm/")[1].split("/")[0] for u in asked} == {"1", "43114"}
    status = {c["chain"]: c["status"] for c in chains.coverage()}
    assert status["base"] == "needs_key" and status["ethereum"] == status["solana"] == "ok"
    assert (
        chains.notes("evm")
        and chains.notes("bitcoin") == []
        and "25 newest" in chains.notes("solana")[0]
    )


def test_dogecoin_and_bitcoin_cash_reuse_the_utxo_heuristics(monkeypatch):
    import httpx

    monkeypatch.setattr(chains, "DELAY", 0)
    other, shop = "DSP4tRb1gSftwfTXu9t2CKbeymGMnPVY3T", "DFPaGaNkRfAAL3X2EHRbsV58gFT4K6ZPzs"
    doge = {
        "final_balance": 150_000_000,
        "n_tx": 7,
        "txs": [
            {
                "hash": "d1",
                "confirmed": "2026-10-20T15:35:34Z",
                "inputs": [
                    {
                        "addresses": [DOGE],
                        "output_value": 300_000_000,
                        "script_type": "pay-to-pubkey-hash",
                    },
                    {
                        "addresses": [other],
                        "output_value": 100_000_000,
                        "script_type": "pay-to-pubkey-hash",
                    },
                ],
                "outputs": [
                    {"addresses": [shop], "value": 390_000_000, "script_type": "pay-to-pubkey-hash"}
                ],
            }
        ],
    }
    peer = "bitcoincash:qplwfa57v5y4wya6750u25l29fmkm3v94g6zwperhv"
    bch = [
        {
            "txid": "b1",
            "time": 1790698978,
            "inputs": [{"address": peer, "value": 107313874, "pkscript": "76a914aa88ac"}],
            "outputs": [{"address": BCH, "value": 36900, "pkscript": "76a914bb88ac"}],
        }
    ]

    def handler(request):
        path = request.url.path
        if "blockcypher" in request.url.host:
            return httpx.Response(200, json=doge)
        assert "bitcoincash:" not in path
        return httpx.Response(
            200, json={"confirmed": 36900, "txs": 1} if path.endswith("/balance") else bch
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        d, b = chains.fetch(client, DOGE), chains.fetch(client, BCH)
    assert d["transfers"] == [("dogecoin", "d1", DOGE, shop, "DOGE", "", 2.925, 1792510534)]
    assert d["links"] == [(*sorted((DOGE, other)), "common-input", 0.9, "d1")]
    assert d["balances"] == [("dogecoin", "DOGE", "", 1.5)] and d["hub"]
    assert b["transfers"] == [("bitcoincash", "b1", peer, BCH, "BCH", "", 0.000369, 1790698978)]
    assert b["balances"] == [("bitcoincash", "BCH", "", 0.000369)] and not b["hub"]


def test_tron_transfers_and_balances(monkeypatch):
    import httpx
    from embit import base58

    monkeypatch.setattr(chains, "DELAY", 0)
    usdt, peer = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", "TCCwQTDPodnpn62a7xeGtEjpujBqHxkL8d"
    as_hex = lambda a: base58.decode_check(a).hex()
    answers = {
        "": {"data": [{"balance": 5_000_000, "trc20": [{usdt: "8500000"}, {"TUnknown": "9"}]}]},
        "/transactions": {
            "data": [
                {
                    "txID": "n1",
                    "block_timestamp": 1794686802000,
                    "ret": [{"contractRet": "SUCCESS"}],
                    "raw_data": {
                        "contract": [
                            {
                                "type": "TransferContract",
                                "parameter": {
                                    "value": {
                                        "owner_address": as_hex(peer),
                                        "to_address": as_hex(TRX),
                                        "amount": 2_500_000,
                                    }
                                },
                            }
                        ]
                    },
                },
                {
                    "txID": "n2",
                    "ret": [{"contractRet": "SUCCESS"}],
                    "raw_data": {"contract": [{"type": "TriggerSmartContract"}]},
                },
            ]
        },
        "/transactions/trc20": {
            "data": [
                {
                    "transaction_id": "t1",
                    "token_info": {"symbol": "USDT", "address": usdt, "decimals": 6},
                    "block_timestamp": 1794686802000,
                    "from": peer,
                    "to": TRX,
                    "type": "Transfer",
                    "value": "8500000",
                }
            ]
        },
    }

    def handler(request):
        return httpx.Response(200, json=answers[request.url.path.split(TRX)[1]])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        t = chains.fetch(client, TRX)
    assert t["transfers"] == [
        ("tron", "n1", peer, TRX, "TRX", "", 2.5, 1794686802),
        ("tron", "t1", peer, TRX, "USDT", usdt, 8.5, 1794686802),
    ]
    assert t["balances"] == [("tron", "TRX", "", 5.0), ("tron", "USDT", usdt, 8.5)]


def test_solana_transfers_come_from_balance_changes():
    payer, mint = (
        "775HG7Z2skCZugQ6LERhgbf82TPb2eGh4ucwVkjJm4GB",
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    )
    amount = lambda n: {"amount": str(n), "decimals": 6}
    tx = {
        "blockTime": 1794188391,
        "transaction": {
            "message": {"accountKeys": [{"pubkey": payer}, {"pubkey": SOL}, {"pubkey": "ata1"}]}
        },
        "meta": {
            "fee": 5000,
            "preBalances": [2_000_005_000, 0, 7],
            "postBalances": [1_000_000_000, 1_000_000_000, 7],  # 1 SOL moved, the fee went nowhere
            "preTokenBalances": [
                {
                    "accountIndex": 2,
                    "mint": mint,
                    "owner": payer,
                    "uiTokenAmount": amount(150_000_000),
                }
            ],
            "postTokenBalances": [
                {
                    "accountIndex": 2,
                    "mint": mint,
                    "owner": payer,
                    "uiTokenAmount": amount(37_200_184),
                },
                {
                    "accountIndex": 1,
                    "mint": mint,
                    "owner": SOL,
                    "uiTokenAmount": amount(112_799_816),
                },
            ],
        },
    }
    assert chains.solana_transfers(SOL, "sig", tx) == [
        ("solana", "sig", payer, SOL, "SOL", "", 1.0, 1794188391),
        ("solana", "sig", payer, SOL, "EPjF...", mint, 112.799816, 1794188391),
    ]
    assert chains.solana_transfers("11111111111111111111111111111111", "sig", tx) == []


def test_a_token_that_only_borrows_a_name_is_not_added_to_the_real_asset(db):
    from tracker import prices

    mine(db, A)
    transfer(db, "avalanche", "1", A, B, 1, asset="AVAX", token=C)  # my wallet sent the look-alike
    db.executemany(
        "INSERT INTO balance(address, chain, asset, token, amount) VALUES (?, 'avalanche', 'AVAX', ?, ?)",
        [(A, "", 2.0), (A, C, 5.0)],
    )
    db.execute("INSERT INTO price VALUES ('coingecko:avalanche-2', 10, 1, 'AVAX', 0.99)")
    assets = prices.portfolio(db)["assets"]
    assert [(a["asset"], a["verified"], a["amount"], a["value"]) for a in assets] == [
        ("AVAX", True, 2.0, 20.0),
        ("AVAX", False, 5.0, None),
    ]


def test_swaps_tie_both_ends_and_label_the_protocol_vaults(db, monkeypatch):
    import httpx

    from tracker import swaps

    monkeypatch.setattr(chains, "DELAY", 0)
    monkeypatch.setattr(chains.time, "sleep", lambda s: None)
    vault_in, vault_out = "0x" + "5" * 40, "bc1q8c6fshw2dlwun7ekn9qwf37cu2rn755upcp6el"
    mine(db, A, public=1)
    mine(db, BIP84[0])
    transfer(db, "ethereum", "0xd48a", A, vault_in, 1920, asset="USDT", token=C)
    transfer(db, "bitcoin", "736cd5", vault_out, BIP84[0], 0.00028231, asset="BTC")
    db.commit()
    action = {
        "type": "swap",
        "status": "success",
        "in": [
            {
                "address": A,
                "txID": "D48A",
                "coins": [{"amount": "192000000000", "asset": "ETH.USDT-0XDAC1"}],
            }
        ],
        "out": [
            {
                "address": "maya1dl7un46w7l7f3ewrnrm6nq58nerjtp0dr2n7aa",
                "affiliate": True,
                "coins": [],
                "txID": "",
            },
            {
                "address": BIP84[0],
                "txID": "736CD5",
                "coins": [{"amount": "28231", "asset": "BTC.BTC"}],
            },
        ],
    }
    asked = []

    def handler(request):
        asked.append(request.url.params["address"])
        if request.url.host == "gateway.liquify.com":
            return httpx.Response(503)
        hit = request.url.params["address"] == A
        return httpx.Response(
            200, json={"actions": [action, {"type": "swap", "status": "pending"}] if hit else []}
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert swaps.sync(db, client) == ["THORChain: HTTP 503"]  # one source down, the other used
    (link,) = db.execute("SELECT * FROM link").fetchall()
    assert (link["kind"], link["confidence"]) == ("swap", 0.95) and {link["a"], link["b"]} == {
        A,
        BIP84[0],
    }
    assert link["evidence"] == "Maya Protocol: 1920 ETH.USDT > 0.00028231 BTC.BTC (D48A)"
    labelled = {r["address"]: (r["name"], r["category"]) for r in db.execute("SELECT * FROM label")}
    assert labelled == {vault_in: ("Maya Protocol", "dex"), vault_out: ("Maya Protocol", "dex")}
    (verdict,) = [v for v in graph.verdicts(db) if v["address"] == BIP84[0]]
    assert verdict["links"][0]["kinds"] == ["swap"] and verdict["links"][0]["public"]
    (btc,) = graph.origins(db)[BIP84[0]]
    assert [(s["name"], s["kind"]) for s in btc["sources"]] == [("Maya Protocol", "dex")]

    monkeypatch.setenv("CT_SWAPS", "off")
    before = len(asked)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert swaps.sync(db, client) == [] and len(asked) == before


def test_explorer_links_come_from_the_api(db):
    by_chain = {c["chain"]: c for c in chains.coverage()}
    assert set(by_chain) == {*chains.UTXO, *chains.EVM, "tron", "solana"}
    assert all(
        "{}" in c["explorer_address"] and "{}" in c["explorer_tx"] for c in by_chain.values()
    )
    mine(db, A)
    mine(db, TRX)
    nodes = {n["address"]: n for n in graph.build(db)["nodes"]}
    assert (nodes[A]["chain"], nodes[A]["explorer_address"]) == (
        "evm",
        "https://etherscan.io/address/{}",
    )
    assert (nodes[TRX]["chain"], nodes[TRX]["explorer_tx"]) == (
        "tron",
        "https://tronscan.org/#/transaction/{}",
    )


def test_dogecoin_quota_is_respected_across_addresses_and_runs(db, monkeypatch):
    import httpx

    from tracker import sync

    monkeypatch.setattr(chains, "DELAY", 0)
    monkeypatch.setattr(chains, "COOLDOWN", {})
    slept, calls = [], []
    monkeypatch.setattr(chains.time, "sleep", slept.append)
    second = "DSP4tRb1gSftwfTXu9t2CKbeymGMnPVY3T"

    def handler(request):
        calls.append(request.url.path)
        if len(calls) > 1:
            return httpx.Response(429)
        return httpx.Response(200, json={"final_balance": 100_000_000, "n_tx": 0, "txs": []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        sync.sync_address(db, client, DOGE)  # within quota
        sync.sync_address(db, client, second)  # quota spent: no half-minute of retries
        sync.sync_address(db, client, second)  # cooling down: not even asked
        sync.sync_address(db, client, DOGE)  # read a moment ago: not asked either
    assert len(calls) == 2 and [s for s in slept if s] == []
    rows = {r["address"]: r for r in db.execute("SELECT * FROM node")}
    assert rows[DOGE]["synced_at"] and rows[DOGE]["error"] is None
    assert "follows on a later sync" in rows[second]["error"] and rows[second]["synced_at"] is None
    assert db.execute("SELECT amount FROM balance WHERE address = ?", (DOGE,)).fetchone()[0] == 1.0


def test_hd_wallet_scan_that_fails_is_reported_not_shown_as_empty(db, monkeypatch):
    import httpx

    from tracker import hd, sync

    monkeypatch.setattr(chains, "DELAY", 0)
    monkeypatch.setattr(chains.time, "sleep", lambda s: None)
    db.execute(
        "INSERT INTO hdwallet(id, name, public, descriptor) VALUES ('hd:x', 'Ledger', 0, ?)",
        (ZPUB,),
    )
    wallet = db.execute("SELECT * FROM hdwallet").fetchone()
    asked = set()

    def down(request):
        asked.add(request.url.path)
        return httpx.Response(503)

    sync.status["notes"] = []
    with httpx.Client(transport=httpx.MockTransport(down)) as client:
        assert sync.discover(db, client, wallet) == []
    # Three failures end a branch; they are not twenty "unused" addresses.
    assert len(asked) == 3 * 2 < hd.GAP
    assert "scan incomplete" in sync.status["notes"][0] and "HTTP 503" in sync.status["notes"][0]
    (verdict,) = graph.verdicts(db)
    assert (verdict["wallet"], verdict["addresses"], verdict["links"]) == ("hd:x", [], [])
    assert not verdict["coverage"]["synced"] and "HTTP 503" in verdict["coverage"]["error"]

    def empty(request):
        stats = {"funded_txo_sum": 0, "spent_txo_sum": 0, "tx_count": 0}
        return httpx.Response(
            200, json=[] if request.url.path.endswith("/chain") else {"chain_stats": stats}
        )

    monkeypatch.setattr(hd, "GAP", 2)
    with httpx.Client(transport=httpx.MockTransport(empty)) as client:
        sync.discover(db, client, wallet)  # the indexer is back: the error clears
    assert graph.verdicts(db)[0]["coverage"]["error"] is None


def test_repeated_exchange_matches_never_pass_the_cap(db):
    assert graph.repeated([0.5]) == 0.5
    assert graph.repeated([0.5] * 5) < graph.repeated([0.5] * 50) <= 0.65
    assert graph.repeated([0.15] * 10) < 0.5 and graph.repeated([0.8] * 9) == 0.8
    mine(db, A)
    mine(db, B)
    for week in range(5):
        db.execute(
            "INSERT INTO link VALUES (?, ?, 'exchange-flow', 0.5, ?)", (A, B, f"week {week}")
        )
    (link,) = graph.build(db)["links"]
    assert link["count"] == 5 and link["confidence"] <= 0.65 < heuristics.EXCHANGE_CAP
    assert graph.verdicts(db)[0]["links"][0]["confidence"] == link["confidence"]


def test_swap_vault_label_spares_the_change_output(db):
    from tracker import swaps

    vault, change, payer = "bc1q8c6fshw2dlwun7ekn9qwf37cu2rn755upcp6el", BIP84[1], "0x" + "6" * 40
    mine(db, BIP84[0])
    mine(db, A)
    transfer(db, "bitcoin", "aa11", BIP84[0], vault, 0.5, asset="BTC")
    transfer(
        db, "bitcoin", "aa11", BIP84[0], change, 0.2, asset="BTC"
    )  # change, address not registered
    transfer(db, "ethereum", "0xbb22", payer, A, 9)
    assert swaps.vaults(db, BIP84[0], "AA11", A, "BB22") == [payer]  # only the payout vault
    db.execute("DELETE FROM transfer WHERE dst = ?", (change,))
    assert swaps.vaults(db, BIP84[0], "AA11", A, "BB22") == [vault, payer]


def test_private_keys_are_named_as_such_and_never_echoed(db):
    from tracker.app import app

    wif = "5HueCGU8rMjxEXxiPuD5BDku4MkFqeZyd4dZ1jvhTVqvbTLvyTJ"
    with pytest.raises(ValueError, match="private key"):
        database.parse(wif)
    client = TestClient(app, base_url="http://localhost")
    for secret in (wif, "correct horse battery staple"):
        reply = client.post("/api/tracker/wallets", json={"address": secret})
        assert reply.status_code == 422 and secret not in reply.text


def test_a_malformed_swap_reply_does_not_stop_the_sync(db, monkeypatch):
    import httpx

    from tracker import swaps

    monkeypatch.setattr(chains, "DELAY", 0)
    mine(db, A)
    replies = iter([{"actions": None}, ["not", "a", "dict"]])
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=next(replies)))
    with httpx.Client(transport=transport) as client:
        assert swaps.sync(db, client) == [
            "Maya Protocol: bad reply"
        ]  # None is "no swaps", a list is not


def test_first_alert_check_only_records_a_baseline(db):
    from tracker import alerts

    mine(db, A, public=1)
    mine(db, B)
    transfer(db, "ethereum", "1", A, B, 1)  # a link that already existed before alerts did
    alerts.check(db)
    assert db.execute("SELECT COUNT(*) FROM alert").fetchone()[0] == 0
    mine(db, C)
    transfer(db, "ethereum", "2", B, C, 1)
    alerts.check(db)
    assert (
        db.execute("SELECT COUNT(*) FROM alert").fetchone()[0] == 2
    )  # C tied to B, and to A through B


def test_alert_feed_points_at_the_wallet_and_counts_unread(db):
    from tracker import alerts

    mine(db, A, public=1)
    mine(db, B)
    alerts.check(db)
    transfer(db, "ethereum", "1", A, B, 1)
    alerts.check(db)
    feed = alerts.recent(db)
    assert (
        feed["unread"] == 1
        and feed["items"][0]["address"] in (A, B)
        and "100%" in feed["items"][0]["text"]
    )
    db.execute("UPDATE alert SET seen = 1")
    assert alerts.recent(db)["unread"] == 0


def test_partial_hd_scan_is_marked_and_an_unscanned_wallet_has_no_value(db, monkeypatch):
    import httpx

    from tracker import hd, prices, sync

    monkeypatch.setattr(chains, "DELAY", 0)
    monkeypatch.setattr(chains.time, "sleep", lambda s: None)
    db.execute(
        "INSERT INTO hdwallet(id, name, public, descriptor) VALUES ('hd:x', 'Ledger', 0, ?)",
        (ZPUB,),
    )
    db.execute("INSERT INTO price VALUES ('coingecko:bitcoin', 50000, 1, 'BTC', 0.99)")
    wallet = db.execute("SELECT * FROM hdwallet").fetchone()
    (d,) = hd.descriptors(ZPUB)
    funded = hd.address(d, 0, 0)

    def flaky(request):  # the first address answers, every later lookup fails
        if funded not in request.url.path:
            return httpx.Response(503)
        if request.url.path.endswith("/chain"):
            return httpx.Response(
                200, json=[tx([vin(SATOSHI, 5000)], [vout(funded, 5000)], "fund")]
            )
        stats = {"funded_txo_sum": 5000, "spent_txo_sum": 0, "tx_count": 1}
        return httpx.Response(200, json={"chain_stats": stats})

    # Before any scan: not checked, and no value rather than a zero.
    (verdict,) = graph.verdicts(db)
    assert verdict["checked"] == "none"
    assert prices.summary(db)["wallets"] == {"hd:x": None}

    sync.status["notes"] = []
    with httpx.Client(transport=httpx.MockTransport(flaky)) as client:
        assert sync.discover(db, client, wallet) == [funded]
    (verdict,) = graph.verdicts(db)
    # One address was found and nothing links it, but the scan was cut short: this must not
    # be presented as "no link found".
    assert verdict["links"] == [] and verdict["coverage"]["synced"]
    assert verdict["checked"] == "partial" and "scan incomplete" in verdict["coverage"]["error"]
    assert prices.summary(db)["wallets"] == {funded: pytest.approx(2.5)}

    db.execute("UPDATE hdwallet SET error = NULL")
    assert graph.verdicts(db)[0]["checked"] == "full"


def test_hd_wallet_can_be_toggled_and_rescanned_by_its_id(db, monkeypatch):
    from tracker import sync
    from tracker.app import app

    db.execute(
        "INSERT INTO hdwallet(id, name, public, descriptor) VALUES ('hd:x', 'Ledger', 0, ?)",
        (ZPUB,),
    )
    db.commit()
    client = TestClient(app, base_url="http://localhost")
    assert client.post(
        "/api/tracker/wallets", json={"address": "hd:x", "name": "Ledger", "public": True}
    ).is_success
    assert tuple(db.execute("SELECT name, public FROM hdwallet").fetchone()) == ("Ledger", 1)
    assert client.get("/api/tracker/analysis").json()["verdicts"][0]["public"] is True
    assert client.post("/api/tracker/wallets", json={"address": "hd:nope"}).status_code == 404

    started = []
    monkeypatch.setattr(sync, "start", lambda *args: started.append(args) or True)
    assert client.post("/api/tracker/sync", json={"addresses": ["hd:x"]}).is_success
    assert client.post("/api/tracker/sync", json={"addresses": [A, "hd:x"], "depth": 1}).is_success
    assert client.post("/api/tracker/sync", json={}).is_success
    assert started == [([], 0, False, ["hd:x"]), ([A], 1, False, ["hd:x"]), (None, 0, False, [])]
    assert client.post("/api/tracker/sync", json={"addresses": ["nonsense"]}).status_code == 422
    assert client.post("/api/tracker/sync", json={"addresses": ["hd:nope"]}).status_code == 404
    assert len(started) == 3


def test_stale_vault_labels_are_rebuilt_and_chains_have_proper_names(db, monkeypatch):
    import httpx

    from tracker import swaps

    monkeypatch.setattr(chains, "DELAY", 0)
    mine(db, A)
    db.execute(
        "INSERT INTO label(address, name, category, source) VALUES (?, 'THORChain', 'dex', 'swap-protocol')",
        (B,),
    )  # a wrong old one
    db.execute(
        "INSERT INTO label(address, name, category, source) VALUES (?, 'Kraken', 'cex', 'user')",
        (C,),
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"actions": []}))
    with httpx.Client(transport=transport) as client:
        assert swaps.sync(db, client) == []
    assert [r["address"] for r in db.execute("SELECT address FROM label")] == [C]
    names = {c["chain"]: c["name"] for c in chains.coverage()}
    assert (names["bitcoincash"], names["bsc"], names["ethereum"]) == (
        "Bitcoin Cash",
        "BNB Chain",
        "Ethereum",
    )


def test_vault_labels_survive_a_sync_in_which_their_protocol_is_down(db, monkeypatch):
    import httpx

    from tracker import alerts, swaps

    monkeypatch.setattr(chains, "DELAY", 0)
    monkeypatch.setattr(chains.time, "sleep", lambda s: None)
    vault = "0x" + "5" * 40
    mine(db, A, public=1)
    mine(db, B)
    # Both wallets dealt with the same vault: a link through it only if it loses its label.
    transfer(db, "ethereum", "1", A, vault, 1)
    transfer(db, "ethereum", "2", vault, B, 1)
    db.execute(
        "INSERT INTO label(address, name, category, source) VALUES (?, 'THORChain', 'dex', 'swap-protocol')",
        (vault,),
    )
    db.execute(
        "INSERT INTO label(address, name, category, source) VALUES (?, 'Maya Protocol', 'dex', 'swap-protocol')",
        (C,),
    )  # stale
    alerts.check(db)  # baseline

    def handler(request):
        if request.url.host == "gateway.liquify.com":
            return httpx.Response(503)  # THORChain is down, Maya answers with no swaps
        return httpx.Response(200, json={"actions": []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert swaps.sync(db, client) == ["THORChain: HTTP 503"]
    labels_now = {r["address"]: r["name"] for r in db.execute("SELECT * FROM label")}
    assert labels_now == {
        vault: "THORChain"
    }  # kept while its source is down; Maya's stale one is gone
    alerts.check(db)
    assert (
        graph.verdicts(db)[1]["links"] == []
        and db.execute("SELECT COUNT(*) FROM alert").fetchone()[0] == 0
    )


def test_a_wallet_is_renamed_by_posting_it_again(db):
    from tracker.app import app

    client = TestClient(app, base_url="http://localhost")
    assert client.post(
        "/api/tracker/wallets", json={"address": A, "name": "hot", "public": True}
    ).is_success
    assert client.post(
        "/api/tracker/wallets", json={"address": A, "name": "  Daily\n spending\t ", "public": True}
    ).is_success
    row = db.execute("SELECT name, public, mine FROM node WHERE address = ?", (A,)).fetchone()
    assert tuple(row) == ("Daily spending", 1, 1)  # one line, and still public
    assert client.post(
        "/api/tracker/wallets", json={"address": A, "name": "x" * 200, "public": True}
    ).is_success
    assert len(db.execute("SELECT name FROM node").fetchone()[0]) == 80
    assert client.post(
        "/api/tracker/wallets", json={"address": A, "name": "", "public": True}
    ).is_success
    assert (
        client.get("/api/tracker/graph").json()["nodes"][0]["name"] == ""
    )  # no name: the address is shown
    assert (
        client.post("/api/tracker/wallets", json={"address": "nope", "name": "x"}).status_code
        == 422
    )

    # An HD wallet is renamed as a whole, by one of its addresses or by its id.
    db.execute(
        "INSERT INTO hdwallet(id, name, public, descriptor) VALUES ('hd:x', 'Ledger', 0, ?)",
        (ZPUB,),
    )
    db.execute(
        "INSERT INTO node(address, mine, name, wallet) VALUES (?, 1, 'Ledger', 'hd:x')", (B,)
    )
    db.execute(
        "INSERT INTO node(address, mine, name, wallet) VALUES (?, 1, 'Ledger', 'hd:x')", (C,)
    )
    db.commit()
    assert client.post("/api/tracker/wallets", json={"address": B, "name": "Cold"}).is_success
    assert {r[0] for r in db.execute("SELECT name FROM node WHERE wallet = 'hd:x'")} == {"Cold"}
    assert client.post("/api/tracker/wallets", json={"address": "hd:x", "name": "Vault"}).is_success
    names = {r[0] for r in db.execute("SELECT name FROM node WHERE wallet = 'hd:x'")}
    assert names == {"Vault"} and db.execute("SELECT name FROM hdwallet").fetchone()[0] == "Vault"


def _esplora(history, balance=0):
    """A mock Esplora that knows the transactions of some addresses."""
    import httpx

    def handler(request):
        address = request.url.path.split("/")[3]
        if request.url.path.endswith("/txs/chain"):
            return httpx.Response(200, json=history.get(address, []))
        stats = {
            "funded_txo_sum": balance,
            "spent_txo_sum": 0,
            "tx_count": len(history.get(address, [])),
        }
        return httpx.Response(200, json={"chain_stats": stats})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_a_coinjoin_is_never_a_certain_link_and_raises_no_alert(db, monkeypatch):
    from tracker import alerts, sync

    monkeypatch.setattr(chains, "DELAY", 0)
    public, private = BIP84[0], BIP84[1]
    others = ["bc1q8c6fshw2dlwun7ekn9qwf37cu2rn755upcp6el", "3GtVZYzsKF6Feikdjd4bDyPdAiyeHANY9b",
              "1JaUQDVNRdhfNsVncGkXedaPSM5Gc54Hso", SATOSHI]  # fmt: skip
    mix = tx(
        [vin(public, 110_000), vin(others[0], 120_000), vin(others[1], 130_000)],
        [
            vout(private, 100_000),
            vout(others[2], 100_000),
            vout(others[3], 100_000),
            vout(others[0], 55_000),
        ],
        "mix",
    )
    assert heuristics.looks_like_coinjoin(mix)
    mine(db, public, public=1)
    mine(db, private)
    alerts.check(db)  # baseline: nothing linked yet
    with _esplora({public: [mix], private: [mix]}) as client:
        sync.sync_address(db, client, public)
        sync.sync_address(db, client, private)
    assert [tuple(r) for r in db.execute("SELECT * FROM coinjoin")] == [("bitcoin", "mix")]
    (verdict,) = [v for v in graph.verdicts(db) if v["address"] == private]
    (link,) = verdict["links"]
    assert link["confidence"] == heuristics.COMMON_INPUT_COINJOIN and link["kinds"] == ["coinjoin"]
    alerts.check(db)
    assert db.execute("SELECT COUNT(*) FROM alert").fetchone()[0] == 0
    # The amounts are still facts: what the wallet received, and from which inputs.
    (btc,) = graph.origins(db)[private]
    assert btc["received"] == pytest.approx(0.001)
    assert graph.build(db)["edges"][0]["coinjoin"] == 1

    # An ordinary payment between the same two wallets is a certain link again.
    plain = tx([vin(public, 50_000)], [vout(private, 49_000)], "plain")
    with _esplora({private: [mix, plain]}) as client:
        sync.sync_address(db, client, private)
    again = next(v for v in graph.verdicts(db) if v["address"] == private)
    assert again["links"][0]["confidence"] == 1.0


def test_a_swap_source_that_is_down_makes_every_verdict_partial(db, monkeypatch):
    import httpx

    from tracker import swaps

    monkeypatch.setattr(chains, "DELAY", 0)
    monkeypatch.setattr(chains.time, "sleep", lambda s: None)
    for address, public in ((A, 1), (BIP84[0], 0)):
        db.execute(
            "INSERT INTO node(address, mine, public, synced_at) VALUES (?, 1, ?, 1)",
            (address, public),
        )
    assert {v["checked"] for v in graph.verdicts(db)} == {"full"}

    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503))) as client:
        assert len(swaps.sync(db, client)) == 2
    for verdict in graph.verdicts(db):
        # They may have swapped straight into each other: "no link found" would be a lie.
        assert verdict["checked"] == "partial" and verdict["links"] == []
        assert (
            "THORChain did not answer (HTTP 503), so swaps through it were not checked"
            in verdict["coverage"]["error"]
        )

    def up(request):
        return httpx.Response(200, json={"actions": []})

    with httpx.Client(transport=httpx.MockTransport(up)) as client:
        assert swaps.sync(db, client) == []
    assert {v["checked"] for v in graph.verdicts(db)} == {"full"}
    assert graph.verdicts(db)[0]["coverage"]["error"] is None


def test_a_wide_transaction_is_flagged_and_keeps_my_own_wallets(db, monkeypatch):
    from tracker import hd, sync

    monkeypatch.setattr(chains, "DELAY", 0)
    (d,) = hd.descriptors(ZPUB)
    outputs = [hd.address(d, 0, i) for i in range(2, 62)]  # 60 recipients
    payer, small = BIP84[0], outputs[-1]
    batch = tx(
        [vin(payer, 10_000_000)], [vout(a, 100_000 - i) for i, a in enumerate(outputs)], "batch"
    )
    assert chains.wide(payer, batch) and not chains.wide(
        payer, tx([vin(payer, 5)], [vout(small, 4)])
    )
    cut = chains.utxo_transfers(payer, batch)
    assert len(cut) == chains.FANOUT and small not in {
        t[3] for t in cut
    }  # the smallest is dropped...
    kept = chains.utxo_transfers(payer, batch, keep={small})
    assert len(kept) == chains.FANOUT and small in {t[3] for t in kept}  # ...unless it is mine

    mine(db, payer, public=1)
    mine(db, small)
    with _esplora({payer: [batch]}) as client:
        sync.sync_address(db, client, payer)
    assert db.execute("SELECT cut FROM node WHERE address = ?", (payer,)).fetchone()[0] == 1
    verdict = {v["address"]: v for v in graph.verdicts(db)}
    assert verdict[small]["links"][0]["confidence"] == 1.0  # the link to my own wallet survived
    (note,) = [n for n in verdict[payer]["coverage"]["notes"] if "very large transaction" in n]
    assert note.startswith("1 very large") and "50 biggest counterparties" in note
    assert not any("very large" in n for n in verdict[small]["coverage"]["notes"])


def test_repeated_swaps_are_one_kind_of_evidence(db):
    mine(db, A)
    mine(db, B)
    for n in range(2):
        db.execute("INSERT INTO link VALUES (?, ?, 'swap', 0.95, ?)", (A, B, f"swap {n}"))
    (link,) = graph.build(db)["links"]
    assert (link["count"], link["confidence"]) == (2, 0.95)  # not 0.9975 shown as "100%"


def test_weak_links_do_not_raise_alerts(db):
    from tracker import alerts

    mine(db, A, public=1)
    mine(db, B)
    alerts.check(db)
    db.execute("INSERT INTO link VALUES (?, ?, 'common-input', 0.2, 'x')", (A, B))
    alerts.check(db)
    assert db.execute("SELECT COUNT(*) FROM alert").fetchone()[0] == 0
    assert graph.verdicts(db)[0]["links"][0]["confidence"] == 0.2  # still listed in the verdict


def test_labels_have_a_type_a_source_and_a_pecking_order(db):
    assert labels.categorize(["1inch"], "1inch v5: Aggregation Router") == ("dex", "DEX aggregator")
    assert labels.categorize([], "Kraken 4") == ("cex", "exchange")
    assert labels.categorize(["defi_lending"]) == ("defi", "lending")
    assert labels.categorize(["marketplace"], "OpenSea") == ("service", "NFT marketplace")
    assert labels.categorize(["airdrop-hunter"]) == ("other", "")
    assert labels.contract("UniswapV2Router02") == ("dex", "DEX contract")
    assert labels.contract("GnosisSafeProxy") == ("other", "multisig or smart wallet")
    assert labels.contract("Foo") == ("other", "contract")
    programs = list(
        labels.parse_solana_programs(
            "    '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8': {\n        deployments: ALL,\n"
            "        name: 'Raydium AMM Program',\n    },\n"
            "    TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA: {\n        name: PROGRAM_NAMES.TOKEN,\n    },\n"
            "    TOKEN = 'Token Program',\n"
        )
    )
    assert [(p[1], p[2], p[3]) for p in programs] == [
        ("Raydium AMM Program", "dex", "DEX program"), ("Token Program", "other", "program"),
    ]  # fmt: skip

    def put(source, name):
        labels.store(db, [(A, name, "cex", "exchange", source)])
        return db.execute("SELECT name, source FROM label").fetchone()["name"]

    assert put("heuristic", "guess") == "guess"
    assert put("explorer", "SomeContract") == "SomeContract"
    assert put("heuristic", "guess again") == "SomeContract"  # a guess never replaces a name
    assert put("etherscan-labels", "Kraken 4") == "Kraken 4"
    assert put("explorer", "SomeContract") == "Kraken 4"
    assert put("user", "My exchange account") == "My exchange account"
    assert put("etherscan-labels", "Kraken 4") == "My exchange account"  # the user always wins
    mine(db, B)
    transfer(db, "ethereum", "1", B, A, 1)
    node = next(n for n in graph.build(db)["nodes"] if n["address"] == A)
    assert (node["label"], node["label_type"], node["label_source"], node["label_guess"]) == (
        "My exchange account", "exchange", "user", False,
    )  # fmt: skip


def test_behaviour_names_deposit_addresses_swap_contracts_and_hubs(db):
    from tracker import detect

    deposit, pool, hub, other = ("0x" + c * 40 for c in "3456")
    mine(db, A)
    db.execute(
        "INSERT INTO label(address, name, category, source) VALUES (?, 'Kraken 4', 'cex', 'etherscan-labels')",
        (BINANCE,),
    )
    db.execute(
        "INSERT INTO label(address, name, category, source) VALUES (?, 'Mine to name', 'other', 'user')",
        (other,),
    )
    db.execute("INSERT INTO node(address, synced_at) VALUES (?, 1)", (deposit,))
    db.execute("INSERT INTO node(address, synced_at, hub) VALUES (?, 1, 1)", (hub,))
    transfer(db, "ethereum", "1", A, deposit, 2)
    transfer(
        db, "ethereum", "2", deposit, BINANCE, 2
    )  # read in full, forwards to the exchange only
    transfer(db, "ethereum", "swap", A, pool, 1)  # ETH out...
    transfer(
        db, "ethereum", "swap", pool, A, 1800, asset="USDC", token=C
    )  # ...USDC back, one transaction
    transfer(db, "ethereum", "3", A, hub, 1)
    transfer(db, "ethereum", "4", A, hub, 10, asset="USDC", token=C)  # a token my wallet has sent
    transfer(db, "ethereum", "swap2", A, other, 1)
    transfer(
        db, "ethereum", "swap2", other, A, 5, asset="USDC", token=C
    )  # same pattern, but named by hand
    assert detect.behaviour(db) == 3
    got = {
        r["address"]: (r["name"], r["category"], r["type"], r["source"])
        for r in db.execute("SELECT * FROM label")
    }
    assert got[deposit] == (
        "Kraken deposit address",
        "cex",
        "exchange deposit address",
        "heuristic",
    )
    assert got[pool] == ("Swap contract", "dex", "DEX pool or router", "heuristic")
    assert got[hub] == ("High-volume address", "service", "service (very active)", "heuristic")
    assert got[other][0] == "Mine to name" and got[BINANCE][3] == "etherscan-labels"
    node = next(n for n in graph.build(db)["nodes"] if n["address"] == deposit)
    assert (node["label_source"], node["label_guess"], node["label_confidence"]) == (
        "heuristic",
        True,
        0.6,
    )
    (usdc,) = [a for a in graph.origins(db)[A] if a["asset"] == "USDC"]
    first = usdc["sources"][0]
    assert (first["name"], first["guess"], first["type"]) == (
        "Swap contract",
        True,
        "DEX pool or router",
    )

    # Exactly one unit of a token back is how an NFT sale looks: the seller is a person.
    seller = "0x" + "a1" * 20
    transfer(db, "ethereum", "nft", A, seller, 0.3)
    transfer(db, "ethereum", "nft", seller, A, 1, asset="APE", token=D)
    detect.behaviour(db)
    assert db.execute("SELECT COUNT(*) FROM label WHERE address = ?", (seller,)).fetchone()[0] == 0

    # A guess that stops being true goes away on the next run.
    db.execute("DELETE FROM transfer WHERE txid = 'swap'")
    detect.behaviour(db)
    assert db.execute("SELECT COUNT(*) FROM label WHERE address = ?", (pool,)).fetchone()[0] == 0


def test_a_guessed_service_weakens_a_path_and_a_known_one_cuts_it(db):
    from tracker import detect

    deposit = "0x" + "3" * 40
    mine(db, A, public=1)
    mine(db, B)
    db.execute(
        "INSERT INTO label(address, name, category, source) VALUES (?, 'Kraken 4', 'cex', 'etherscan-labels')",
        (BINANCE,),
    )
    db.execute("INSERT INTO node(address, synced_at) VALUES (?, 1)", (deposit,))
    # Both of my wallets paid into the same address, which forwards to the exchange.
    transfer(db, "ethereum", "1", A, deposit, 1)
    transfer(db, "ethereum", "2", B, deposit, 1)
    transfer(db, "ethereum", "3", deposit, BINANCE, 2)
    before = graph.verdicts(db)[1]["links"][0]["confidence"]
    assert before == graph.HOP_DECAY  # an unnamed stranger in between: 0.7

    detect.behaviour(db)  # now guessed to be a deposit address
    (link,) = graph.verdicts(db)[1]["links"]
    # Still a link, never a fact: weaker than through a stranger, far from 100%.
    assert link["confidence"] == pytest.approx(
        graph.HOP_DECAY * (1 - labels.CONFIDENCE["heuristic"]), abs=1e-3
    )
    assert 0 < link["confidence"] < before
    guessed = {"name": "Kraken deposit address", "category": "cex", "guess": True}
    assert guessed in graph.verdicts(db)[1]["services"]

    # The same address named by a dataset, or by the user, is a known service: no path at all.
    labels.store(db, [(deposit, "Kraken 12", "cex", "exchange", "user")])
    assert graph.verdicts(db)[1]["links"] == []


def test_an_address_on_okx_list_is_named_but_ends_no_path_nor_crawl(db):
    from tracker import sync

    deposit = "0x" + "3" * 40
    mine(db, A, public=1)
    mine(db, B)
    transfer(db, "ethereum", "1", A, deposit, 1)
    transfer(db, "ethereum", "2", B, deposit, 1)
    listed = f"ETH,Non Staking,ETH,26313797,{deposit},0.5,I am an OKX address,0x8e8a0e53,,,,"
    labels.store(db, labels.parse_okx([listed]))
    # Mostly deposit addresses, each one customer's: a name, but no barrier, as a stranger's.
    verdict = graph.verdicts(db)[1]
    assert verdict["links"][0]["confidence"] == graph.HOP_DECAY
    assert {"name": "OKX", "category": "cex", "guess": False} in verdict["services"]
    assert sync.neighbours(db, [A]) == [deposit]  # crawled and judged like a stranger
    # An open set that names the address an exchange's still ends the path, and the list
    # does not take its place.
    labels.store(db, [(deposit, "OKX 3", "cex", "exchange", "etherscan-labels")])
    labels.store(db, labels.parse_okx([listed]))
    assert graph.verdicts(db)[1]["links"] == [] and sync.neighbours(db, [A]) == []


def test_contract_names_come_from_the_explorer_once_and_only_for_counterparties(db, monkeypatch):
    import httpx

    from tracker import detect

    monkeypatch.setattr(chains, "DELAY", 0)
    monkeypatch.setattr(
        chains,
        "DEFAULT_EVM",
        {"ethereum": {"url": "http://scan/api", "symbol": "ETH", "apikey": "KEY"}},
    )
    router, person, known = ("0x" + c * 40 for c in "789")
    mine(db, A)
    db.execute(
        "INSERT INTO label(address, name, category, source) VALUES (?, 'Kraken 4', 'cex', 'etherscan-labels')",
        (known,),
    )
    for n, other in enumerate((router, person, known)):
        transfer(db, "ethereum", str(n), A, other, 1)
    transfer(db, "tron", "t", A, TRX, 1)  # not an EVM address: never asked
    asked = []

    def handler(request):
        asked.append(request.url.params["address"])
        name = "UniswapV2Router02" if request.url.params["address"] == router else ""
        return httpx.Response(200, json={"status": "1", "result": [{"ContractName": name}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert detect.contracts(db, client) == 1
        assert detect.contracts(db, client) == 0  # answers are kept, also "not a contract"
    assert sorted(asked) == sorted([router, person])  # not my wallet, not the labelled one
    row = db.execute("SELECT * FROM label WHERE address = ?", (router,)).fetchone()
    assert (row["name"], row["category"], row["type"], row["source"]) == (
        "UniswapV2Router02",
        "dex",
        "DEX contract",
        "explorer",
    )

    monkeypatch.setenv("CT_LABELS", "off")
    db.execute("DELETE FROM contract")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert detect.contracts(db, client) == 0 and len(asked) == 2


def test_label_sets_are_refreshed_when_due(db, monkeypatch):
    import httpx

    assert labels.due(db)

    def handler(request):
        if "etherscan/combined" in request.url.path:
            return httpx.Response(200, json={A: {"name": "Kraken 4", "labels": ["kraken"]}})
        return httpx.Response(404)  # every other set is unreachable: the import still succeeds

    asked = []

    def counting(request):
        asked.append(request.url.path)
        return handler(request)

    with httpx.Client(transport=httpx.MockTransport(counting)) as client:
        count, failed = labels.import_open(db, client)
        assert count == 1 and "graphsense-tagpacks" in failed and "etherscan-labels" not in failed
        first = len(asked)
        # The sets that failed are due again at once, and only they are asked for again.
        assert labels.due(db) and "etherscan-labels" not in labels.stale(db)
        labels.import_open(db, client)
        assert len(asked) == 2 * first - 1 and not any(
            "etherscan/combined" in p for p in asked[first:]
        )
    row = db.execute("SELECT * FROM label").fetchone()
    assert (row["name"], row["type"], row["source"]) == ("Kraken 4", "exchange", "etherscan-labels")
    db.execute("UPDATE meta SET value = '0'")
    assert "etherscan-labels" in labels.stale(db)
    monkeypatch.setenv("CT_LABELS", "off")
    assert not labels.due(db)


def test_an_unregistered_wallet_between_two_of_mine_is_not_called_a_deposit_address(db):
    from tracker import detect

    middle = "0x" + "3" * 40
    mine(db, A, public=1)
    mine(db, B)
    db.execute(
        "INSERT INTO label(address, name, category, source) VALUES (?, 'Binance 14', 'cex', 'etherscan-labels')",
        (BINANCE,),
    )
    db.execute("INSERT INTO node(address, synced_at) VALUES (?, 1)", (middle,))
    transfer(db, "ethereum", "1", A, middle, 2)
    transfer(db, "ethereum", "2", middle, B, 1)  # it pays my other wallet...
    transfer(db, "ethereum", "3", middle, BINANCE, 1)  # ...and once sent to an exchange

    def state():
        (link,) = graph.verdicts(db)[1]["links"]
        (eth,) = graph.origins(db)[B]
        return link["confidence"], [(s["address"], s["kind"]) for s in eth["sources"]]

    before = state()
    assert before == (graph.HOP_DECAY, [(A, "own")])
    detect.behaviour(db)
    assert db.execute("SELECT COUNT(*) FROM label WHERE address = ?", (middle,)).fetchone()[0] == 0
    assert state() == before  # the link is as strong as before, and the origin is still my wallet

    # Even when such an address does carry a guessed label (here a contract name), the money
    # is followed past it: the guess must not become the origin.
    labels.store(db, [(middle, "GnosisSafeProxy", "other", "multisig or smart wallet", "explorer")])
    assert state()[1] == [(A, "own")]
    # Where it cannot be followed any further, the guess is the answer.
    db.execute("UPDATE node SET synced_at = NULL WHERE address = ?", (middle,))
    assert state()[1] == [(middle, "other")]
    # And a label that is not a guess ends the walk, as before.
    labels.store(db, [(middle, "Kraken 9", "cex", "exchange", "user")])
    assert graph.origins(db)[B][0]["sources"][0]["kind"] == "cex"


def test_a_protocol_vault_outranks_a_contract_name_and_cuts_the_path(db, monkeypatch):
    import httpx

    from tracker import swaps

    monkeypatch.setattr(chains, "DELAY", 0)
    monkeypatch.setattr(chains.time, "sleep", lambda s: None)
    router = "0x" + "5" * 40
    mine(db, A, public=1)
    mine(db, B)
    mine(db, BIP84[0])
    transfer(db, "ethereum", "0xd48a", A, router, 5)
    transfer(db, "ethereum", "0xother", B, router, 3)  # my other wallet used the same router
    # The explorer named the router before the swap records were read.
    labels.store(db, [(router, "THORChain_Router", "dex", "DEX contract", "explorer")])
    (weak,) = next(v for v in graph.verdicts(db) if v["address"] == B)["links"]
    assert 0 < weak["confidence"] < 0.2  # a guessed service only weakens the path between A and B

    action = {
        "type": "swap",
        "status": "success",
        "in": [
            {"address": A, "txID": "D48A", "coins": [{"amount": "500000000", "asset": "ETH.ETH"}]}
        ],
        "out": [
            {"address": BIP84[0], "txID": "", "coins": [{"amount": "28231", "asset": "BTC.BTC"}]}
        ],
    }

    def handler(request):
        hit = request.url.params["address"] == A
        return httpx.Response(200, json={"actions": [action] if hit else []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert swaps.sync(db, client) == []
    row = db.execute("SELECT * FROM label WHERE address = ?", (router,)).fetchone()
    assert row["source"] == "swap-protocol" and row["name"] in ("THORChain", "Maya Protocol")
    verdict = next(v for v in graph.verdicts(db) if v["address"] == B)
    assert [
        k for k in verdict["links"] if k["wallet"] == A and k["kinds"] == ["transfer", "transfer"]
    ] == []
    assert any(
        s["wallet"] == A for s in verdict["shared_services"]
    )  # named as a shared service instead

    # A label set by hand still wins over the protocol's.
    labels.store(db, [(router, "My note", "dex", "", "user")])
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        swaps.sync(db, client)
    assert (
        db.execute("SELECT name FROM label WHERE address = ?", (router,)).fetchone()[0] == "My note"
    )


def test_a_fake_usdc_is_its_own_row_worth_0(db):
    from tracker import graph, prices

    mine(db, A)
    mine(db, B)
    db.executemany(
        "INSERT INTO balance(address, chain, asset, token, amount, trust, reason) "
        "VALUES (?, 'ethereum', 'USDC', ?, ?, ?, ?)",
        [
            (A, C, 100.0, "verified", "This exact contract is on Uniswap's default token list."),
            (B, D, 5000.0, "scam", "It calls itself USDC, like USDC, but it is not the real one."),
        ],
    )
    db.execute(f"INSERT INTO price VALUES ('ethereum:{C}', 1.0, 1, 'USDC', 0.99)")
    db.execute(f"INSERT INTO price VALUES ('ethereum:{D}', 1.0, 1, 'USDC', 0.95)")  # bait price
    db.commit()
    p = prices.portfolio(db)
    rows = sorted((a["asset"], a["fake"], a["amount"], a["value"]) for a in p["assets"])
    assert rows == [("USDC", False, 100.0, 100.0), ("USDC", True, 5000.0, 0)]
    assert p["total"] == 100.0 and p["unpriced"] == []
    assert prices.summary(db)["assets"] == {"USDC": 100.0}
    # The graph data carries the mark, also without "Unknown tokens", so the page can show it.
    (fake,) = next(n for n in graph.build(db)["nodes"] if n["address"] == B)["balances"]
    assert (fake["token"], fake["trust"]["level"]) == (D, "scam") and "real" in fake["trust"][
        "reason"
    ]


def test_an_hd_wallet_keeps_its_fake_token_apart(db):
    from tracker import graph

    for address in (A, B):
        db.execute("INSERT INTO node(address, mine, wallet) VALUES (?, 1, 'hd:x')", (address,))
    db.executemany(
        "INSERT INTO balance(address, chain, asset, token, amount, trust) "
        "VALUES (?, 'ethereum', 'USDC', ?, ?, ?)",
        [(A, C, 100.0, "verified"), (B, C, 50.0, "verified"), (B, D, 5000.0, "scam")],
    )
    db.commit()
    (hd,) = [n for n in graph.build(db)["nodes"] if n.get("wallet_balances")]
    got = sorted((b["amount"], b.get("trust", {}).get("level")) for b in hd["wallet_balances"])
    assert got == [(150.0, "verified"), (5000.0, "scam")]  # never 5150 USDC


def test_wallets_that_only_sent_fake_tokens_are_not_in_the_graph(db):
    """Pablo: the wallets that only sent me tokens without value are not shown in the graph."""
    from tracker import graph, sync

    fake = "0x" + "9" * 40
    airdropper, payer, both = ("0x" + c * 40 for c in "456")
    mine(db, A)
    db.execute("INSERT INTO node(address, synced_at) VALUES (?, 1)", (airdropper,))  # expanded once
    db.execute(  # its gas money: a balance of a node that is not shown must not break the graph
        "INSERT INTO balance(address, chain, asset, token, amount) VALUES (?, 'ethereum', 'ETH', '', 0.5)",
        (airdropper,),
    )
    db.execute(
        "INSERT INTO balance(address, chain, asset, token, amount, trust) "
        "VALUES (?, 'ethereum', 'USDC', ?, 1000, 'scam')",
        (A, fake),
    )
    transfer(db, "ethereum", "t1", airdropper, A, 1000, asset="USDC", token=fake)
    transfer(db, "ethereum", "t2", payer, A, 1, asset="ETH")
    transfer(db, "ethereum", "t3", both, A, 5, asset="USDC", token=fake)
    transfer(db, "ethereum", "t4", both, A, 2, asset="ETH")
    transfer(db, "ethereum", "t5", A, B, 1000, asset="USDC", token=fake)  # I passed the fake on
    db.commit()
    for spam in (False, True):  # also with "Unknown tokens" ticked
        g = graph.build(db, spam)
        shown = {n["address"] for n in g["nodes"]}
        assert airdropper not in shown and B not in shown, spam
        assert {A, payer, both} <= shown, spam
        assert all(e["token"] != fake for e in g["edges"]), spam
    assert set(sync.neighbours(db, [A])) == {payer, both}  # nobody crawls an airdropper
