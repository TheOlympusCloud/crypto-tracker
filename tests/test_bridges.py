"""Bridge records read from the receipts of my own bridge transactions (tracker/bridges.py).

The receipts and Solana account reads in fixtures/bridges are real mainnet answers, recorded
from Routescan, Blockscout and the public Solana RPC.
"""

import json
import sqlite3
from pathlib import Path

import httpx
import pytest
from test_core import mine, transfer

from tracker import bridges, chains, graph
from tracker import db as database

FIXTURES = Path(__file__).parent / "fixtures" / "bridges"
ROUTESCAN = "https://api.routescan.io/v2/network/mainnet/evm/{}/etherscan/api"
EVM = {
    "ethereum": {"url": ROUTESCAN.format(1), "symbol": "ETH"},
    "avalanche": {"url": ROUTESCAN.format(43114), "symbol": "AVAX"},
}

CCTP_SENDER = "0x3135c461aea071fc041d331834a3131e919ec90f"
CCTP_TOKEN_ACCOUNT = "Zmceyp93fsocmJy96FRMUqrBMe5rPg24TxkeRpTerpB"
CCTP_OWNER = "HLHEDoVXx7Juy2TthR61CrHd8XrQbCMRWqsUZWbfwXrb"
WORMHOLE_SENDER = "0x999f938d97639e825dc963d375c69c78e5e03f38"
WORMHOLE_TOKEN_ACCOUNT = "7mN8VaVn5pVPBMhRYK6RZKyME9sggnfBqYesSmm6DFMT"
WORMHOLE_OWNER = "BnUBX6LJjoYtx5vwKnyFQKKBpkiZvqo4k7p9PPVAC83p"
ACROSS_SENDER = "0x3b385bcbc4b40a992a927a81e8a63b015f4dd0e9"
ACROSS_RECIPIENT = "0x0f7ae28de1c8532170ad4ee566b5801485c13a0e"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
USDT = "0xdac17f958d2ee523a2206206994597c13d831ec7"


def fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def receipt(name):
    return bridges.normal(fixture(name)["result"])


@pytest.mark.parametrize(
    "name, chain, expected",
    [
        ("cctp-v1-solana", "ethereum", [("Circle CCTP", CCTP_SENDER, CCTP_TOKEN_ACCOUNT, "solana")]),
        ("cctp-v2", "ethereum", [("Circle CCTP", "0xdedb0e722e69d7794cbae19bac32da650849490a",
                                  "0xdedb0e722e69d7794cbae19bac32da650849490a", "arbitrum")]),
        # Domain 8 is Sui: its 32-byte address is no address the tracker reads.
        ("cctp-v2-sui", "avalanche", []),
        ("across", "ethereum", [("Across", ACROSS_SENDER, ACROSS_RECIPIENT, "base")]),
        ("wormhole-solana", "ethereum",
         [("Wormhole", WORMHOLE_SENDER, WORMHOLE_TOKEN_ACCOUNT, "solana")]),
        ("wormhole-bsc", "ethereum", [("Wormhole", "0xbe9306ddf2c0ddd69227adb2c7e7ef4d0b5697d5",
                                       "0xbe9306ddf2c0ddd69227adb2c7e7ef4d0b5697d5", "bsc")]),
        ("op-base", "ethereum", [("OP Stack", "0xc6055b7daa191339542de04b30797cbd537cafdf",
                                  "0xa96d088ed2969950e75a3254349cc7a56a267508", "base")]),
        ("op-optimism-erc20", "ethereum",
         [("OP Stack", "0x114d1119aaadaebf16ef996878d9e663773ace58",
           "0x31c40b3acbeb5b06ed372ab503ad40719a67edf0", "optimism")]),
        ("arbitrum", "ethereum", [("Arbitrum", "0x492222c37faea031f1c2f7401c14a033c109d3cb",
                                   "0x492222c37faea031f1c2f7401c14a033c109d3cb", "arbitrum")]),
        # The same Across receipt read on another chain: no contract of it is a bridge there.
        ("across", "polygon", []),
    ],
)  # fmt: skip
def test_each_bridge_names_its_recipient_and_destination(name, chain, expected):
    assert bridges.decode(chain, receipt(name)) == expected


def test_a_wormhole_message_counts_only_from_the_token_bridge_and_without_a_payload():
    """Any contract can publish a message through Wormhole's core; and a transfer with a
    payload names a contract that redeems it, not whoever gets the money."""
    r = receipt("wormhole-solana")
    message = next(log for log in r["logs"] if log["topics"][0] == bridges.WORMHOLE)
    other = {**message, "topics": [message["topics"][0], "0x" + "0" * 24 + "1" * 40]}
    data = message["data"]
    start = 2 + int(data[130:194], 16) * 2 + 64  # the payload, after its length
    assert data[start : start + 2] == "01"
    with_payload = {**message, "data": data[:start] + "03" + data[start + 2 :]}
    for log in (other, with_payload):
        assert bridges.decode("ethereum", {**r, "logs": [log]}) == []


def test_a_database_from_before_bridge_records_gains_their_columns(tmp_path):
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.execute(
        "CREATE TABLE swap(protocol TEXT, sender TEXT, recipient TEXT, tx_in TEXT, tx_out TEXT, "
        "PRIMARY KEY(protocol, tx_in, recipient))"
    )
    old.execute("INSERT INTO swap VALUES ('THORChain', 'a', 'b', 'in', 'out')")
    old.commit()
    old.close()
    db = database.connect(path)
    (row,) = db.execute("SELECT * FROM swap").fetchall()
    assert dict(row) == {"protocol": "THORChain", "sender": "a", "recipient": "b", "tx_in": "in",
                         "tx_out": "out", "chain": "", "note": ""}  # fmt: skip
    db.close()


def test_blockscout_has_no_proxy_module_so_the_transaction_page_gives_the_logs(monkeypatch):
    monkeypatch.setattr(chains.time, "sleep", lambda s: None)
    asked = []

    def handler(request):
        asked.append(dict(request.url.params))
        return httpx.Response(200, json=fixture("op-base-blockscout"))

    cfg = {"url": "https://api.blockscout.com/v2/api", "params": {"chain_id": "1"}}
    cfg |= {"apikey": "KEY", "tokenlist": True}
    txid = "0xeb48e482b345d22835b00748ede88886aa41e1a5c1a4b44cabdf4a60ec045499"
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        got = bridges.fetch(client, cfg, txid)
    assert bridges.decode("ethereum", got) == bridges.decode("ethereum", receipt("op-base"))
    assert asked == [
        {"chain_id": "1", "apikey": "KEY", "module": "transaction", "action": "gettxinfo",
         "txhash": txid}
    ]  # fmt: skip


class Chain:
    """Routescan and the Solana RPC, answering from the recordings; every call is noted."""

    def __init__(self, accounts=(), signatures=10):
        self.receipts = {
            fixture(n)["result"]["transactionHash"]: fixture(n)
            for n in ("cctp-v1-solana", "wormhole-solana", "across", "op-base")
        }
        self.accounts = dict(accounts)
        self.signatures = signatures
        self.asked = []

    def __call__(self, request):
        self.asked.append(request.url.host)
        if request.url.host == "api.routescan.io":
            q = request.url.params
            if q["module"] == "proxy":
                return httpx.Response(200, json=self.receipts[q["txhash"]])
            # an EVM stranger read by the busy check: a quiet address
            return httpx.Response(200, json={"status": "0", "message": "No transactions found"})
        body = json.loads(request.content)
        method, params = body["method"], body["params"]
        if method == "getAccountInfo":
            return httpx.Response(200, json=fixture(self.accounts[params[0]]))
        if method == "getSignaturesForAddress":
            sigs = [{"signature": f"s{i}"} for i in range(self.signatures)]
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": sigs})
        raise AssertionError(f"unexpected call {method}")


@pytest.fixture
def quiet(monkeypatch):
    monkeypatch.setattr(chains, "DEFAULT_EVM", EVM)
    monkeypatch.setattr(chains, "DELAY", 0)
    monkeypatch.setattr(chains.time, "sleep", lambda s: None)


def run(db, fake):
    with httpx.Client(transport=httpx.MockTransport(fake)) as client:
        return bridges.sync(db, client)


def links(db):
    return {
        (r["a"], r["b"]): (r["confidence"], r["evidence"]) for r in db.execute("SELECT * FROM link")
    }


def test_a_transfer_to_solana_links_the_owner_of_the_token_account(db, quiet):
    """CCTP and Wormhole name a token account on Solana: one getAccountInfo finds its owner.
    The CCTP owner is a wallet of mine, the Wormhole owner a stranger as busy as an exchange."""
    mine(db, CCTP_SENDER)
    mine(db, CCTP_OWNER)
    mine(db, WORMHOLE_SENDER)
    minter = "0xc4922d64a24675e16e1586e3e3aa56c06fabe907"  # CCTP v1's token minter
    transfer(db, "ethereum", fixture("cctp-v1-solana")["result"]["transactionHash"], CCTP_SENDER,
             minter, 5952.11047, asset="USDC", token=USDC)  # fmt: skip
    token_bridge = "0x3ee18b2214aff97000d974cf647e7c347e8fa585"
    transfer(db, "ethereum", fixture("wormhole-solana")["result"]["transactionHash"],
             WORMHOLE_SENDER, token_bridge, 1852.06, asset="X", token="0x" + "7" * 40)  # fmt: skip
    db.commit()
    fake = Chain(
        {
            CCTP_TOKEN_ACCOUNT: "solana-account-cctp",
            WORMHOLE_TOKEN_ACCOUNT: "solana-account-wormhole",
        },
        signatures=1000,
    )

    assert run(db, fake) == []

    cctp = fixture("cctp-v1-solana")["result"]["transactionHash"]
    assert links(db) == {
        tuple(sorted((CCTP_SENDER, CCTP_OWNER))): (
            0.95, f"Circle CCTP: 5952.11 USDC ethereum > solana ({cctp[:18]})"
        )
    }  # fmt: skip
    records = {r["protocol"]: dict(r) for r in db.execute("SELECT * FROM swap")}
    assert records["Circle CCTP"] | {"tx_in": ""} == {
        "protocol": "Circle CCTP", "sender": CCTP_SENDER, "recipient": CCTP_OWNER, "tx_in": "",
        "tx_out": "", "chain": "solana", "note": "",
    }  # fmt: skip
    assert records["Wormhole"]["recipient"] == WORMHOLE_OWNER  # kept: it is a fact, a hub one
    hub = db.execute("SELECT hub FROM node WHERE address = ?", (WORMHOLE_OWNER,)).fetchone()
    assert hub["hub"] == 1  # 1000 signatures: busy, so it links nobody
    solana = [m for m in fake.asked if m == "api.mainnet-beta.solana.com"]
    assert len(solana) == 3  # one getAccountInfo per record, one busy check of the stranger
    (verdict,) = [v for v in graph.verdicts(db) if v["address"] == CCTP_SENDER]
    assert verdict["links"][0]["kinds"] == ["bridge-record"]
    (wormhole,) = [v for v in graph.verdicts(db) if v["address"] == WORMHOLE_SENDER]
    assert wormhole["bridged"] == [
        {"protocol": "Wormhole", "address": WORMHOLE_OWNER, "chain": "solana",
         "linked": False, "closed": False}
    ]  # fmt: skip

    fake.asked.clear()
    assert run(db, fake) == [] and fake.asked == []  # every receipt is read once


def test_a_closed_token_account_is_kept_without_a_link_and_says_why(db, quiet):
    mine(db, CCTP_SENDER)
    cctp = fixture("cctp-v1-solana")["result"]["transactionHash"]
    transfer(db, "ethereum", cctp, CCTP_SENDER, "0xc4922d64a24675e16e1586e3e3aa56c06fabe907", 5952.11,
             asset="USDC", token=USDC)  # fmt: skip
    db.commit()

    assert run(db, Chain({CCTP_TOKEN_ACCOUNT: "solana-account-closed"})) == []

    assert links(db) == {}
    (record,) = db.execute("SELECT recipient, note FROM swap").fetchall()
    assert tuple(record) == (CCTP_TOKEN_ACCOUNT, "closed")
    (verdict,) = graph.verdicts(db)
    assert verdict["bridged"] == [
        {"protocol": "Circle CCTP", "address": CCTP_TOKEN_ACCOUNT, "chain": "solana",
         "linked": False, "closed": True}
    ]  # fmt: skip


def test_a_bridge_to_an_undeclared_address_is_a_link_to_a_stranger(db, quiet):
    mine(db, ACROSS_SENDER)
    across = fixture("across")["result"]["transactionHash"]
    spoke_pool = "0x5c7bcd6e7de5423a257d81b442095a1a6ced35c5"
    transfer(db, "ethereum", across, ACROSS_SENDER, spoke_pool, 1.0, asset="USDT", token=USDT)
    db.commit()
    fake = Chain()

    assert run(db, fake) == []

    assert links(db) == {
        tuple(sorted((ACROSS_SENDER, ACROSS_RECIPIENT))): (
            0.95, f"Across: 1 USDT ethereum > base ({across[:18]})"
        )
    }  # fmt: skip
    # Read first, and quiet: a stranger, not a service (docs/design/multichain.md, 3.5).
    assert "api.routescan.io" in fake.asked
    (verdict,) = graph.verdicts(db)
    assert verdict["bridged"] == [
        {"protocol": "Across", "address": ACROSS_RECIPIENT, "chain": "base",
         "linked": True, "closed": False}
    ]  # fmt: skip
    drawn = {(k["a"], k["b"], k["kind"]) for k in graph.build(db)["links"]}
    assert (*sorted((ACROSS_SENDER, ACROSS_RECIPIENT)), "bridge-record") in drawn


def test_several_records_between_two_addresses_stay_at_095(db):
    """The same kind of public record made again is no new evidence (graph.COMBINE)."""
    for txid in ("0x01", "0x02"):
        db.execute(
            "INSERT INTO link VALUES (?, ?, 'bridge-record', 0.95, ?)",
            (ACROSS_SENDER, ACROSS_RECIPIENT, f"Across: 1 USDT ethereum > base ({txid})"),
        )
    (link,) = graph.build(db)["links"]
    assert (link["kind"], link["confidence"], link["count"]) == ("bridge-record", 0.95, 2)


def test_only_my_own_deposits_count(db, quiet):
    """The receipt names someone else as depositor: not my record, whoever paid in."""
    other = "0x" + "4" * 40
    mine(db, other)
    across = fixture("across")["result"]["transactionHash"]
    transfer(db, "ethereum", across, other, "0x5c7bcd6e7de5423a257d81b442095a1a6ced35c5", 1.0,
             asset="USDT", token=USDT)  # fmt: skip
    db.commit()

    assert run(db, Chain()) == []
    assert links(db) == {} and db.execute("SELECT * FROM swap").fetchall() == []


def test_no_new_host_with_swaps_off_and_a_failed_read_is_retried(db, quiet, monkeypatch):
    """Receipts go to the indexer that reads the wallet, token accounts to the Solana RPC that
    reads my Solana wallets: CT_SWAPS=off has nothing to turn off. An indexer that fails
    leaves the receipt unread, says so, and the next sync reads it."""
    monkeypatch.setenv("CT_SWAPS", "off")
    mine(db, ACROSS_SENDER)
    mine(db, CCTP_SENDER)
    mine(db, "0xc6055b7daa191339542de04b30797cbd537cafdf")
    db.execute("UPDATE node SET synced_at = 1")  # read in full: only the bridge records wait
    transfer(db, "ethereum", fixture("across")["result"]["transactionHash"], ACROSS_SENDER,
             "0x5c7bcd6e7de5423a257d81b442095a1a6ced35c5", 1.0, asset="USDT", token=USDT)  # fmt: skip
    transfer(db, "ethereum", fixture("cctp-v1-solana")["result"]["transactionHash"], CCTP_SENDER,
             "0xc4922d64a24675e16e1586e3e3aa56c06fabe907", 5952.11, asset="USDC", token=USDC)  # fmt: skip
    transfer(db, "ethereum", fixture("op-base")["result"]["transactionHash"],
             "0xc6055b7daa191339542de04b30797cbd537cafdf",
             "0x3154cf16ccdb4c6d922629664174b904d80f2c35", 0.5)  # fmt: skip
    db.commit()
    fake = Chain({CCTP_TOKEN_ACCOUNT: "solana-account-cctp"})
    down = True

    def flaky(request):
        if down and request.url.params.get("module") == "proxy":
            return httpx.Response(503)
        return fake(request)

    failed = run(db, flaky)

    assert failed == ["Bridge receipts: ethereum: HTTP 503"]
    assert db.execute("SELECT COUNT(*) FROM swap").fetchone()[0] == 0
    (verdict,) = [v for v in graph.verdicts(db) if v["address"] == ACROSS_SENDER]
    assert verdict["checked"] == "partial"  # a record may be missing
    down = False
    assert run(db, flaky) == []
    assert db.execute("SELECT COUNT(*) FROM swap").fetchone()[0] == 3
    assert set(fake.asked) <= {"api.routescan.io", "api.mainnet-beta.solana.com"}
    (verdict,) = [v for v in graph.verdicts(db) if v["address"] == ACROSS_SENDER]
    assert verdict["checked"] == "full"


def test_a_recipient_declared_mine_later_is_linked_with_no_new_read(db, quiet):
    """A busy recipient links nobody; once declared a wallet of mine, the next sync links it
    from the record stored, without reading the receipt again."""
    mine(db, WORMHOLE_SENDER)
    wormhole = fixture("wormhole-solana")["result"]["transactionHash"]
    transfer(db, "ethereum", wormhole, WORMHOLE_SENDER, "0x3ee18b2214aff97000d974cf647e7c347e8fa585",
             1852.06, asset="X", token="0x" + "7" * 40)  # fmt: skip
    db.commit()
    fake = Chain({WORMHOLE_TOKEN_ACCOUNT: "solana-account-wormhole"}, signatures=1000)
    assert run(db, fake) == [] and links(db) == {}

    db.execute("UPDATE node SET mine = 1 WHERE address = ?", (WORMHOLE_OWNER,))  # as app.py
    db.commit()
    fake.asked.clear()
    assert run(db, fake) == [] and fake.asked == []

    assert links(db) == {
        tuple(sorted((WORMHOLE_SENDER, WORMHOLE_OWNER))): (
            0.95, f"Wormhole: 1852.06 X ethereum > solana ({wormhole[:18]})"
        )
    }  # fmt: skip
    (verdict,) = [v for v in graph.verdicts(db) if v["address"] == WORMHOLE_SENDER]
    assert verdict["links"][0]["kinds"] == ["bridge-record"] and verdict["bridged"] == []


def test_an_arbitrum_token_deposit_names_the_token_not_the_eth_for_gas(db, quiet, monkeypatch):
    """The token goes to a gateway, the ETH for the deposit to the router: the link says what
    was bridged."""
    sender, other = "0x492222c37faea031f1c2f7401c14a033c109d3cb", "0x" + "5" * 40
    mine(db, sender)
    mine(db, other)
    r = receipt("arbitrum")
    routed = r["logs"][0]
    r["logs"][0] = {**routed, "topics": [*routed["topics"][:3], "0x" + "0" * 24 + other[2:]]}
    monkeypatch.setattr(bridges, "fetch", lambda client, cfg, txid: r)
    txid = fixture("arbitrum")["result"]["transactionHash"]
    transfer(db, "ethereum", txid, sender, bridges.ARBITRUM_ROUTER, 0.0004)
    transfer(db, "ethereum", txid, sender, "0xa3a7b6f88361f48403514059f1f16c8e78d60eec",
             0.07676676, asset="WBTC", token="0x2260fac5e5542a773aa44fbcfedf7c193bc2c599")  # fmt: skip
    db.commit()

    assert run(db, Chain()) == []

    assert links(db) == {
        tuple(sorted((sender, other))): (
            0.95, f"Arbitrum: 0.0767668 WBTC ethereum > arbitrum ({txid[:18]})"
        )
    }  # fmt: skip


def test_a_receipt_that_fails_holds_back_no_other_of_its_chain(db, quiet):
    """An answer that cannot be read (here a token account that is a mint) keeps its receipt
    unread and says so; the chain's other receipts are read all the same."""
    mine(db, CCTP_SENDER)
    mine(db, ACROSS_SENDER)
    db.execute("UPDATE node SET synced_at = 1")  # read in full: only the bridge records wait
    cctp, across = (fixture(n)["result"]["transactionHash"] for n in ("cctp-v1-solana", "across"))
    transfer(db, "ethereum", cctp, CCTP_SENDER, "0xc4922d64a24675e16e1586e3e3aa56c06fabe907",
             9000.0, asset="USDC", token=USDC)  # fmt: skip
    transfer(db, "ethereum", across, ACROSS_SENDER, "0x5c7bcd6e7de5423a257d81b442095a1a6ced35c5",
             1.0, asset="USDT", token=USDT)  # fmt: skip
    db.commit()
    mint = fixture("solana-account-cctp")
    del mint["result"]["value"]["data"]["parsed"]["info"]["owner"]
    fake = Chain()

    def answer(request):
        if request.url.host == "api.mainnet-beta.solana.com":
            body = json.loads(request.content)
            if body["method"] == "getAccountInfo":
                return httpx.Response(200, json=mint)
        return fake(request)

    for _ in range(2):  # the same at every sync: it never holds back the Across deposit
        assert run(db, answer) == ["Bridge receipts: ethereum: 'owner'"]
    assert [tuple(r) for r in db.execute("SELECT * FROM receipt")] == [("ethereum", across)]
    assert tuple(sorted((ACROSS_SENDER, ACROSS_RECIPIENT))) in links(db)
    (verdict,) = [v for v in graph.verdicts(db) if v["address"] == ACROSS_SENDER]
    assert verdict["checked"] == "partial"
    assert verdict["coverage"]["error"] == (
        "Bridge receipts were not all read (ethereum: 'owner'), so a bridge record may be missing"
    )


def test_blockscout_logs_are_read_page_by_page(monkeypatch):
    """Blockscout gives a transaction's logs 50 at a time: a bridge event on a later page is
    read, and a transaction wider than PAGES pages is not taken as read."""
    monkeypatch.setattr(chains.time, "sleep", lambda s: None)
    whole = fixture("op-base-blockscout")["result"]
    txid = whole["hash"]
    more = {"action": "gettxinfo", "index": 120, "module": "transaction", "txhash": txid}
    first = {**whole, "logs": whole["logs"][:1], "next_page_params": more}
    rest = {**whole, "logs": whole["logs"][1:], "next_page_params": None}
    asked = []

    def handler(request):
        asked.append(dict(request.url.params))
        page = rest if request.url.params.get("index") == "120" else first
        return httpx.Response(200, json={"message": "OK", "result": page, "status": "1"})

    cfg = {"url": "https://api.blockscout.com/v2/api", "params": {"chain_id": "1"}}
    cfg |= {"apikey": "KEY", "tokenlist": True}
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        got = bridges.fetch(client, cfg, txid)
    assert got == receipt("op-base-blockscout")
    assert bridges.decode("ethereum", got) == bridges.decode("ethereum", receipt("op-base"))
    assert [q.get("index") for q in asked] == [None, "120"] and asked[1]["apikey"] == "KEY"

    def endless(request):
        return httpx.Response(200, json={"message": "OK", "result": first, "status": "1"})

    with (
        httpx.Client(transport=httpx.MockTransport(endless)) as client,
        pytest.raises(RuntimeError, match="more than 20 pages"),
    ):
        bridges.fetch(client, cfg, txid)
