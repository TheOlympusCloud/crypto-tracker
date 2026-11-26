"""Who an address belongs to: a service name and a type for every counterparty we can name.

Labels come from four places, and the stronger one wins:

    user          set by hand in the app; nothing overwrites it
    protocol      THORChain/Maya vaults, from the protocols' own records (swaps.py)
    dataset       open label sets, downloaded whole and looked up locally
    explorer      the verified contract name at the explorer already used for syncing
    heuristic     behaviour seen in the synced data (detect.py); a guess, never a fact

The datasets are downloaded in full, so looking an address up in them tells nobody which
addresses are of interest. brianleect/etherscan-labels, graphsense/graphsense-tagpacks and
the program names of solana-labs/explorer are MIT-licensed; OKX publishes its
proof-of-reserves list, every address signed "I am an OKX address", for anyone to check.
"""

import csv
import io
import json
import os
import re
import tarfile
import tempfile
import time
import zipfile

import httpx
import yaml

from . import db as database

EXPLORER_DUMPS = (
    "https://raw.githubusercontent.com/brianleect/etherscan-labels/main/data/{}/combined/"
    "combinedAllLabels.json"
)
EXPLORERS = ["etherscan", "bscscan", "polygonscan", "arbiscan", "optimism", "avalanche", "ftmscan"]
TAGPACKS = "https://codeload.github.com/graphsense/graphsense-tagpacks/tar.gz/refs/heads/master"
SOLANA_PROGRAMS = (
    "https://raw.githubusercontent.com/solana-labs/explorer/master/app/utils/programs.ts"
)
OKX_POR = "https://www.okx.com/proof-of-reserves/download"
# A new list each month; the download page links every one of them.
OKX_LISTS = re.compile(r"https://static\.okx\.com/cdn/okx/por/chain/por_csv_\d+_V\d+\.zip")
# The networks of OKX's list that are read here. Bitcoin Cash is left out: its addresses are
# listed in the legacy form, which reads as Bitcoin.
OKX_NETWORKS = {"BTC", "LTC", "DOGE", "TRON", "SOL", "ETH", "ARBITRUM", "AVAXC", "BASE",
                "OPTIMISM", "POLYGON"}  # fmt: skip
REFRESH = 30 * 86400  # the sets are downloaded again when the copy is older than this

LOADER = getattr(yaml, "CBaseLoader", yaml.BaseLoader)  # libyaml is ~10x faster

# The source column holds 'user', 'swap-protocol', 'explorer', 'heuristic' or a dataset's name.
_RANK = (
    "(CASE {} WHEN 'user' THEN 5 WHEN 'swap-protocol' THEN 4 WHEN 'explorer' THEN 2 "
    "WHEN 'heuristic' THEN 1 ELSE 3 END)"
)
# How sure a label is, by where it came from. Below 1 it is shown as a guess, and a path
# through such an address is weakened instead of cut (graph.trace).
CONFIDENCE = {"heuristic": 0.6, "explorer": 0.8}

CEX = [
    "binance", "bitfinex", "bitstamp", "bittrex", "bybit", "coinbase", "crypto-com", "ftx",
    "gate-io", "gemini", "huobi", "kraken", "kucoin", "okex", "okx", "poloniex", "upbit",
    "bithumb", "mexc", "bitget", "exchange",
]  # fmt: skip
# (type shown to the user, category that drives colour and tracing, dataset slugs). First
# match wins, so the specific comes before the general.
TYPES = [
    ("mixer", "mixer", ["tornado-cash", "mixing_service", "mixer", "coinjoin"]),
    # Scams, hacks and sanctions: never send money there.
    ("scam, hack or sanctions", "flagged", ["phish-hack", "phishing", "scam", "heist", "exploit",
                                            "hack", "ransomware", "sextortion", "fraud", "ponzi",
                                            "take-action", "spam-token", "ofac-sanctions-lists",
                                            "sanctions"]),
    ("bridge", "bridge", ["bridge", "cross-chain", "multichain", "hop-protocol"]),
    ("exchange", "cex", CEX),
    ("DEX aggregator", "dex", ["1inch", "paraswap", "0x-protocol", "aggregator"]),
    ("DEX", "dex", ["dex", "defi_dex", "sushiswap", "uniswap", "balancer", "bancor", "curve-fi",
                    "kyberswap"]),
    ("lending", "defi", ["defi_lending", "aave", "compound", "maker", "loans"]),
    ("staking", "defi", ["lido", "staking", "rocket-pool"]),
    ("DeFi", "defi", ["defi", "yearn-finance", "yield-farming"]),
    ("NFT marketplace", "service", ["marketplace", "opensea", "looksrare", "rarible", "blur"]),
    ("payment processor", "service", ["payments", "fiat-gateway"]),
    ("gambling", "service", ["gambling"]),
    ("mining", "service", ["miner", "mining"]),
    ("service", "service", ["service", "market", "wallet_service"]),
]  # fmt: skip
# Verified contract names say what a contract is, not who runs it.
CONTRACTS = [
    (r"tornado|mixer", "mixer", "mixer"),
    (r"aggregat", "DEX aggregator", "dex"),
    (r"router|swap|pair|pool|amm", "DEX contract", "dex"),
    (r"bridge|portal|gateway|spoke|messenger|inbox", "bridge", "bridge"),
    (r"lend|comet|atoken|vault|staking|stake", "DeFi contract", "defi"),
    (r"safe|multisig|wallet", "multisig or smart wallet", "other"),
]


FLAGGED_NAMES = re.compile(r"phish|scam|exploiter|hacker|drainer|heist|sanction", re.IGNORECASE)


def categorize(slugs, name=""):
    """(category, type) for a dataset entry, from its slugs or the first word of its name."""
    slugs = {s.lower() for s in slugs}
    first = re.split(r"[\s:.]", name.lower(), maxsplit=1)[0]
    for kind, category, keys in TYPES:
        if (
            slugs & set(keys)
            or (category == "cex" and first in CEX[:-1])
            or (category == "flagged" and FLAGGED_NAMES.search(name))
        ):
            return category, kind
    return "other", ""


def contract(name):
    """(category, type) for a verified contract name."""
    for pattern, kind, category in CONTRACTS:
        if re.search(pattern, name, re.IGNORECASE):
            return category, kind
    return "other", "contract"


def provenance(source):
    if source in ("user", "explorer", "heuristic"):
        return source
    return "protocol" if source == "swap-protocol" else "dataset"


def describe(row):
    """What the API says about a label: name, type, where it is from and how sure it is."""
    confidence = CONFIDENCE.get(row["source"], 1.0)
    return {
        "label": row["name"],
        "category": row["category"],
        "label_type": row["type"],
        "label_source": provenance(row["source"]),
        "label_confidence": confidence,
        "label_guess": confidence < 1,
    }


def parse_etherscan(data, source="etherscan-labels"):
    for address, v in data.items():
        slugs = v.get("labels") or []
        name = v.get("name") or (slugs[0] if slugs else "")
        if name:
            yield (address.lower(), name[:80], *categorize(slugs, name), source)


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
        yield (address, str(label)[:80], *categorize(slugs, str(label)), "graphsense-tagpacks")


def parse_solana_programs(text):
    """Program names from the Solana explorer's source file. An entry reads
    `<id>: { ..., name: PROGRAM_NAMES.X }` with `X = '<name>'` in an enum above it, or
    carries the name as a literal."""
    names = dict(re.findall(r"^\s*(\w+) = '([^']+)',?$", text, re.MULTILINE))
    entry = re.compile(
        r"""['"]?([1-9A-HJ-NP-Za-km-z]{32,44})['"]?:\s*\{\s*(?:[a-zA-Z]+:[^\n]*\n\s*)*?"""
        r"""name:\s*(?:PROGRAM_NAMES\.(\w+)|'([^']+)')"""
    )
    for program, key, literal in entry.findall(text):
        name = literal or names.get(key)
        if not name:
            continue
        category, kind = contract(name)
        if re.search(r"serum|raydium|orca|mango|market|swap", name, re.IGNORECASE):
            category, kind = "dex", "DEX program"
        elif re.search(r"wormhole", name, re.IGNORECASE):
            category, kind = "bridge", "bridge"
        kind = "program" if kind == "contract" else kind
        yield program, name[:80], category, kind, "solana-programs"


def parse_okx(lines):
    """OKX's proof-of-reserves CSV: totals per coin, then one row per address and coin
    (coin, Type, Network, Snapshot Height, address, ...). Mostly deposit addresses."""
    for row in csv.reader(lines):
        if len(row) > 4 and row[2] in OKX_NETWORKS:
            try:
                yield database.norm(row[4]), "OKX", "cex", "exchange", "okx-por"
            except ValueError:
                continue  # the keys of Ethereum validators, listed for staked ETH


def store(db, rows):
    """Insert (address, name, category, type, source) rows. A label only replaces one from
    an equal or weaker source: a dataset never overwrites the user, a guess never a dataset."""
    db.executemany(
        "INSERT INTO label(address, name, category, type, source) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(address) DO UPDATE SET name = excluded.name, category = excluded.category, "
        "type = excluded.type, source = excluded.source "
        f"WHERE {_RANK.format('excluded.source')} >= {_RANK.format('label.source')}",
        rows,
    )
    db.commit()


SETS = [f"{explorer}-labels" for explorer in EXPLORERS] + [
    "graphsense-tagpacks", "solana-programs", "okx-por"
]  # fmt: skip


def stale(db):
    """The sets to download now: never fetched, or older than REFRESH. Each set has its own
    date, so one that failed is tried again on the next sync without re-reading the rest."""
    if os.environ.get("CT_LABELS", "on") == "off":
        return []
    # meta is shared (the session cookie's secret is there too): read only the sets' dates.
    rows = db.execute("SELECT key, value FROM meta WHERE key LIKE 'labels_at:%'")
    at = {r["key"]: float(r["value"]) for r in rows}
    return [name for name in SETS if time.time() - at.get(f"labels_at:{name}", 0) > REFRESH]


def due(db):
    return bool(stale(db))


def import_open(db, client, everything=False):
    """Download the stale label sets (all of them with `everything`) and load them. One set
    failing does not stop the others. Returns (labels read, names of the sets not read)."""
    wanted = SETS if everything else stale(db)
    rows, done, failed = [], [], []
    for explorer in EXPLORERS:
        name = f"{explorer}-labels"
        if name not in wanted:
            continue
        try:
            reply = client.get(EXPLORER_DUMPS.format(explorer)).raise_for_status()
            rows += parse_etherscan(json.loads(reply.text), name)
            done.append(name)
        except (httpx.HTTPError, ValueError, AttributeError, TypeError):
            failed.append(name)
    if "graphsense-tagpacks" in wanted:
        try:
            archive = client.get(TAGPACKS, follow_redirects=True).raise_for_status().content
            # Members are read in memory, never extracted to disk.
            with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
                for member in tar:
                    if (
                        member.isfile()
                        and "/packs/" in member.name
                        and member.name.endswith(".yaml")
                    ):
                        try:
                            text = tar.extractfile(member).read().decode("utf-8", "replace")
                            rows += parse_tagpack(text)
                        except yaml.YAMLError:
                            continue
            done.append("graphsense-tagpacks")
        except (httpx.HTTPError, tarfile.TarError, OSError):
            failed.append("graphsense-tagpacks")
    if "solana-programs" in wanted:
        try:
            rows += parse_solana_programs(client.get(SOLANA_PROGRAMS).raise_for_status().text)
            done.append("solana-programs")
        except httpx.HTTPError:
            failed.append("solana-programs")
    if "okx-por" in wanted:
        try:
            page = client.get(OKX_POR, follow_redirects=True).raise_for_status().text
            with tempfile.TemporaryFile() as file:  # about 110 MB, so not kept in memory
                with client.stream("GET", max(OKX_LISTS.findall(page))) as reply:
                    for chunk in reply.raise_for_status().iter_bytes():
                        file.write(chunk)
                with zipfile.ZipFile(file) as archive:
                    for name in archive.namelist():
                        with archive.open(name) as text:
                            rows += parse_okx(io.TextIOWrapper(text, "utf-8", errors="replace"))
            done.append("okx-por")
        except (httpx.HTTPError, ValueError, zipfile.BadZipFile, csv.Error, OSError):
            failed.append("okx-por")  # ValueError: no list linked on the page
    store(db, rows)
    now = str(time.time())
    db.executemany(
        "INSERT OR REPLACE INTO meta VALUES (?, ?)", [(f"labels_at:{n}", now) for n in done]
    )
    db.commit()
    return len(rows), failed
