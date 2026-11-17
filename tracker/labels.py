"""Address labels from open datasets (both MIT-licensed), mapped to a few categories."""

import io
import json
import re
import tarfile

import yaml

ETHERSCAN_LABELS = (
    "https://raw.githubusercontent.com/brianleect/etherscan-labels/main/"
    "data/etherscan/combined/combinedAllLabels.json"
)
TAGPACKS = "https://codeload.github.com/graphsense/graphsense-tagpacks/tar.gz/refs/heads/master"

LOADER = getattr(yaml, "CBaseLoader", yaml.BaseLoader)  # libyaml is ~10x faster

CEX = [
    "binance",
    "bitfinex",
    "bitstamp",
    "bittrex",
    "bybit",
    "coinbase",
    "crypto-com",
    "ftx",
    "gate-io",
    "gemini",
    "huobi",
    "kraken",
    "kucoin",
    "okex",
    "okx",
    "poloniex",
    "upbit",
    "bithumb",
    "mexc",
    "bitget",
    "exchange",
]
CATEGORIES = [  # first match wins
    ("mixer", ["tornado-cash", "mixing_service", "mixer", "coinjoin"]),
    ("bridge", ["bridge", "cross-chain", "multichain", "hop-protocol"]),
    ("cex", CEX),
    (
        "dex",
        [
            "dex",
            "defi_dex",
            "sushiswap",
            "uniswap",
            "balancer",
            "bancor",
            "curve-fi",
            "kyberswap",
            "1inch",
            "0x-protocol",
            "paraswap",
        ],
    ),
    (
        "defi",
        [
            "defi",
            "defi_lending",
            "aave",
            "compound",
            "maker",
            "yearn-finance",
            "lido",
            "staking",
            "yield-farming",
            "loans",
        ],
    ),
    (
        "service",
        [
            "service",
            "gambling",
            "market",
            "wallet_service",
            "payments",
            "fiat-gateway",
            "miner",
            "mining",
        ],
    ),
]


def categorize(slugs, name=""):
    slugs = {s.lower() for s in slugs}
    first = re.split(r"[\s:.]", name.lower(), maxsplit=1)[0]
    for category, keys in CATEGORIES:
        if slugs & set(keys) or (category == "cex" and first in CEX[:-1]):
            return category
    return "other"


def parse_etherscan(data):
    for address, v in data.items():
        slugs = v.get("labels") or []
        name = v.get("name") or (slugs[0] if slugs else "")
        if name:
            yield address.lower(), name[:80], categorize(slugs, name), "etherscan-labels"


def parse_tagpack(text):
    # BaseLoader builds no objects and keeps scalars as strings; the default
    # resolver would read an unquoted 0x address as a number.
    pack = yaml.load(text, Loader=LOADER)
    if not isinstance(pack, dict):
        return
    for tag in pack.get("tags") or []:
        tag = {**pack, **tag}
        address, label = tag.get("address"), tag.get("label")
        if not address or not label or tag.get("currency") not in ("BTC", "ETH", "LTC", "TRX"):
            continue
        address = str(address).lower() if tag["currency"] == "ETH" else str(address)
        slugs = [str(tag.get("category") or ""), *map(str, tag.get("concepts") or [])]
        yield address, str(label)[:80], categorize(slugs, str(label)), "graphsense-tagpacks"


def store(db, rows):
    # A label the user set by hand is never overwritten by a dataset.
    db.executemany(
        "INSERT INTO label VALUES (?, ?, ?, ?) ON CONFLICT(address) DO UPDATE SET "
        "name = excluded.name, category = excluded.category, source = excluded.source "
        "WHERE label.source != 'user'",
        rows,
    )
    db.commit()


def import_open(db, client):
    """Download both datasets and load them. Returns the number of labels read."""
    rows = list(parse_etherscan(json.loads(client.get(ETHERSCAN_LABELS).raise_for_status().text)))
    archive = client.get(TAGPACKS, follow_redirects=True).raise_for_status().content
    # Members are read in memory, never extracted to disk.
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar:
            if member.isfile() and "/packs/" in member.name and member.name.endswith(".yaml"):
                try:
                    rows += parse_tagpack(tar.extractfile(member).read().decode("utf-8", "replace"))
                except yaml.YAMLError:
                    continue
    store(db, rows)
    return len(rows)
