import re
from pathlib import Path

import pytest

from tracker import chains, labels, lookup, tokens

USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
FAKE = "0x" + "f" * 40
USDT = "0xdac17f958d2ee523a2206206994597c13d831ec7"
LISTED = {
    (1, USDC): ("USDC", "USD Coin"),
    (1, USDT): ("USDT", "Tether USD"),
    (137, "0x" + "e" * 40): ("WETH", "Wrapped Ether"),
}
FAMOUS = tokens.famous(LISTED)
SURE = {"usd": 1.0, "confidence": 0.99}
SHAKY = {"usd": 0.4, "confidence": 0.5}


def trust(token=FAKE, symbol="FOO", name="", chain="ethereum", **kw):
    return tokens.trust(chain, token, symbol, name, **{"famous": FAMOUS, **kw})


@pytest.mark.parametrize(
    ("symbol", "skeleton"),
    [("USDC", "USDC"), ("$usdc", "USDC"), ("USDС", "USDC"), ("ＵＳＤＣ", "USDC"), ("USD₮", "USDT")],
)
def test_lookalike_symbols_read_as_the_symbol_they_imitate(symbol, skeleton):
    assert tokens.skeleton(symbol) == skeleton  # "USDС" ends in a Cyrillic Es


def test_only_a_list_match_or_the_native_coin_is_verified():
    assert trust(token="", symbol="ETH")["level"] == "verified"
    on_list = trust(token=USDC, symbol="USDC", listed="Uniswap's default token list")
    assert on_list == {"level": "verified", "reason": on_list["reason"]}
    assert "Uniswap" in on_list["reason"]
    # A real market is not a list: it stays unknown, and says why.
    priced = trust(price=SURE)
    assert priced["level"] == "unknown" and "market price" in priced["reason"]
    assert trust()["level"] == "unknown"  # the default: nothing known either way


@pytest.mark.parametrize("symbol", ["USDC", "USDС", "usdc", "ETH", "WETH"])
def test_a_token_borrowing_a_listed_name_is_a_likely_scam(symbol):
    t = trust(symbol=symbol)
    assert t["level"] == "scam" and "not the" in t["reason"]


def test_a_borrowed_name_with_a_reliable_price_is_only_unknown():
    t = trust(symbol="USDC", price=SURE)
    assert t["level"] == "unknown" and "USDC" in t["reason"]
    assert trust(symbol="USDC", price=SHAKY)["level"] == "scam"


@pytest.mark.parametrize(
    ("symbol", "name"),
    [
        ("FREE", "Visit usdc-rewards.com to claim"),
        ("$ claim", "Uniswap"),
        ("X", "https://t.me/airdrop"),
        ("AIRDROP", "Your reward"),
    ],
)
def test_a_name_that_advertises_a_site_or_a_reward_is_a_likely_scam(symbol, name):
    t = trust(symbol=symbol, name=name)
    assert t["level"] == "scam" and "advertises" in t["reason"]
    # A token with a real market whose name happens to hold a domain ("yearn.finance") is not.
    assert trust(symbol=symbol, name=name, price=SURE)["level"] == "unknown"


@pytest.mark.parametrize(
    ("symbol", "name"),
    [
        ("USDC2", "USD Coin"),  # borrows a listed token's name
        ("FOO", "Tether USD"),
        ("XUSD", ""),  # a dollar token on no list, with no market
        ("ETH2", "Ether"),
        ("BTCB2", ""),
    ],
)
def test_a_token_dressed_as_a_dollar_or_ether_is_fake(symbol, name):
    t = trust(symbol=symbol, name=name)
    assert t["level"] == "scam" and "not the real" in t["reason"]
    assert "worth 0" not in t["reason"]  # the badge says that


def test_a_real_dollar_token_with_a_market_is_never_fake():
    assert trust(symbol="USDe", name="USDe", price=SURE)["level"] == "unknown"
    assert trust(symbol="METHANE")["level"] == "unknown"  # "ETH" inside a word is no dress


def test_unasked_dust_without_a_price_is_a_likely_scam():
    # Sent to the wallet in transactions it did not take part in, never moved, no market.
    assert trust(unasked=True)["level"] == "scam"
    assert "unasked" in trust(unasked=True)["reason"]
    assert trust(unasked=True, price=SHAKY)["level"] == "scam"  # a thin price is no market
    assert trust(unasked=True, price=SURE)["level"] == "unknown"
    assert trust(unasked=False)["level"] == "unknown"  # bought, or sent on: the owner wanted it
    assert trust(unasked=None)["level"] == "unknown"  # history incomplete: no claim either way


def test_a_listed_token_is_verified_whatever_its_name_says():
    t = trust(token=USDC, symbol="USDC", name="claim.com", listed="Uniswap", unasked=True)
    assert t["level"] == "verified"


def test_lists_keep_contracts_per_chain_and_mints_verbatim():
    data = {
        "tokens": [
            {
                "chainId": 1,
                "address": "0xA0b86991c6218b36c1D19D4a2e9Eb0cE3606eB48",
                "symbol": "USDC",
            },
            {"chainId": tokens.SOLANA_ID, "address": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"}
            | {"symbol": "USDC"},
            {"chainId": 1, "symbol": "BROKEN"},  # a row without an address is skipped
        ]
    }
    listed = tokens.parse_list(data)
    assert listed == {
        (1, USDC): ("USDC", ""),
        (tokens.SOLANA_ID, "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"): ("USDC", ""),
    }
    assert tokens.listed_on(listed, "ethereum", USDC) and not tokens.listed_on(listed, "base", USDC)
    assert tokens.listed_on(listed, "solana", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")


@pytest.mark.parametrize(
    ("chain", "read", "icon"),
    [
        ("bitcoin", [], "bitcoin"),
        ("solana", [], "solana"),
        ("tron", [], "tron"),
        ("evm", [], "evm"),  # the same address on every EVM chain: no chain until one is chosen
        ("evm", ["ethereum", "polygon"], "evm"),
        ("evm", ["polygon"], "polygon"),
    ],
)
def test_the_chain_icon_follows_the_detected_chain(chain, read, icon):
    assert chains.icon(chain, read) == icon


def test_every_chain_icon_the_server_names_has_a_mark_in_the_page():
    source = (Path(chains.__file__).parent / "static" / "kinds.js").read_text()
    block = source.split("export const CHAINS = {", 1)[1].split("\n};", 1)[0]
    drawn = set(re.findall(r"^  (\w+): \[", block, re.MULTILINE))
    named = {*chains.UTXO, *chains.EVM, "evm", "tron", "solana"}
    assert named <= drawn, named - drawn


def test_open_label_sets_flag_scams_hacks_and_sanctions():
    flagged = ("flagged", "scam, hack or sanctions")
    assert labels.categorize(["phish-hack"]) == flagged
    assert labels.categorize(["take-action"], "Multichain Hack Alert 1003") == flagged
    assert labels.categorize([], "Fake_Phishing1234") == flagged
    assert labels.categorize(["blocked", "ofac-sanctions-lists"]) == flagged
    assert labels.categorize([], "Asset listed under US Treasury OFAC Sanctions List") == flagged
    # A sanctioned mixer is still a mixer: the verdicts say what that means.
    assert labels.categorize(["ofac-sanctions-lists", "tornado-cash"]) == ("mixer", "mixer")
    assert labels.categorize(["exchange"], "Binance 14") == ("cex", "exchange")


@pytest.mark.parametrize(
    ("a", "b", "alike"),
    [
        ("0xabcd" + "1" * 32 + "9f3e", "0xabcd" + "2" * 32 + "9f3e", True),
        ("0xabcd" + "1" * 32 + "9f3e", "0xabcd" + "1" * 32 + "9f3e", False),  # the same one
        ("0xabcd" + "1" * 32 + "9f3e", "0xabce" + "1" * 32 + "9f3e", False),
        # only the prefix every Bitcoin Cash address has, and the end, agree
        ("bitcoincash:qpm2qs" + "a" * 30 + "9x6a", "bitcoincash:qzzzzz" + "b" * 30 + "9x6a", False),
        ("bitcoincash:qpm2qs" + "a" * 30 + "9x6a", "bitcoincash:qpm2qs" + "b" * 30 + "9x6a", True),
    ],
)
def test_a_lookalike_starts_and_ends_like_an_address_in_use(a, b, alike):
    assert lookup.lookalike(a, b) is alike


def test_a_token_list_that_does_not_answer_is_not_asked_again_at_once(monkeypatch):
    import httpx

    monkeypatch.setattr(tokens, "CACHE", {})
    calls = []

    def down(request):
        calls.append(request.url.host)
        return httpx.Response(503)

    with httpx.Client(transport=httpx.MockTransport(down)) as client:
        assert tokens.uniswap(client) == {} and tokens.uniswap(client) == {}
    assert calls == ["tokens.uniswap.org"]  # one sync with many wallets waits once, not per wallet


def test_the_reason_names_what_is_imitated_as_written():
    assert "like Tether USD," in trust(symbol="FOO", name="Tether USD")["reason"]
