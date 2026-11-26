"""Who an address belongs to: a service name and a type for every counterparty we can name.

Labels come from four places, and the stronger one wins:

    user          set by hand in the app; nothing overwrites it
    protocol      THORChain/Maya vaults, from the protocols' own records (swaps.py)
    dataset       open label sets, downloaded whole and looked up locally
    explorer      the verified contract name at the explorer already used for syncing
    heuristic     behaviour seen in the synced data (detect.py); a guess, never a fact

The datasets are downloaded in full, so looking an address up in them tells nobody which
addresses are of interest. All are MIT-licensed: brianleect/etherscan-labels,
graphsense/graphsense-tagpacks, the program names of solana-labs/explorer and 0xB10C's
OFAC lists. Tether's and Circle's freezes are read whole from the stablecoins' own events
(docs/design/multichain.md, 2.2 #4). Sanctions and freezes are flagged labels that outrank
the other datasets, and a list read anew replaces its old copy, so a delisted or unfrozen
address loses its label.
"""

import io
import json
import os
import re
import tarfile
import time

import httpx
import yaml
from embit import base58

from . import chains
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
OFAC = (
    "https://raw.githubusercontent.com/0xB10C/ofac-sanctioned-digital-currency-addresses/lists/"
    "sanctioned_addresses_{}.txt"
)
# The lists of the chains the tracker reads (XBT is Bitcoin; USDT holds Ethereum and Tron ones).
OFAC_LISTS = ["XBT", "LTC", "BCH", "ETH", "BSC", "ARB", "USDT", "USDC", "TRX", "SOL"]
# Freeze and unfreeze events: Tether's AddedBlackList/RemovedBlackList, Circle's
# Blacklisted/UnBlacklisted. On Ethereum (issuer, token, contract, first block, topics).
ETH_FREEZES = [
    ("Tether", "USDT", "0xdac17f958d2ee523a2206206994597c13d831ec7", 4634748,
     "0x42e160154868087d6bfdc0ca23d96a1c1cfa32f1b72ba9ba27b69b98a0d819dc",
     "0xd7e9ec6e6ecd65492dce6bf513cd6867560d49544421d0783ddf06e76c24470c"),
    ("Circle", "USDC", "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", 6082465,
     "0xffa4e6181777692565cf28528fc88fd1516ea86b56da075235fa575af6a4b855",
     "0x117e3210bb9aa7d9baff172026820255c6f6c30ba8999d1c2fd88e2848137c4e"),
]  # fmt: skip
TRON_FREEZES = [
    ("Tether", "USDT", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", "AddedBlackList", "RemovedBlackList"),
    ("Circle", "USDC", "TEkxiTehnzSmSe2XqrBj4w32RUN966rdz8", "Blacklisted", "UnBlacklisted"),
]
SPAN = 2_000_000  # blocks per getLogs query: Routescan gives up on wider ones in the busy years
LOG_ROWS = 1000  # logs per answer, Etherscan's most
TRON_PAUSE = 1.5  # seconds between TronGrid event pages: without a key it suspends the hasty
REFRESH = 30 * 86400  # the sets are downloaded again when the copy is older than this

LOADER = getattr(yaml, "CBaseLoader", yaml.BaseLoader)  # libyaml is ~10x faster

# The source column holds 'user', 'swap-protocol', 'explorer', 'heuristic' or a dataset's name.
# A sanction or a freeze matters more to whoever sends money there than who runs the address
# (Garantex is an exchange, and sanctioned), so they outrank the other datasets; OFAC first.
_RANK = (
    "(CASE {} WHEN 'user' THEN 6 WHEN 'ofac-sanctions' THEN 5 WHEN 'swap-protocol' THEN 4 "
    "WHEN 'freezes-ethereum' THEN 4 WHEN 'freezes-tron' THEN 4 WHEN 'explorer' THEN 2 "
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


def parse_list(text, name, kind, source):
    """Flagged rows for a list of addresses, one per line, in their canonical form. A line
    that is no address of a chain read here is skipped."""
    for line in text.splitlines():
        try:
            yield database.norm(line), name, "flagged", kind, source
        except ValueError:
            continue


def read_ofac(client):
    rows = []
    for currency in OFAC_LISTS:
        text = client.get(OFAC.format(currency)).raise_for_status().text
        rows += parse_list(text, "Sanctioned by OFAC", "sanctioned (OFAC)", "ofac-sanctions")
    return rows


def frozen(source, issuer, token, added, removed):
    """Flagged rows for the addresses `issuer` froze and has not unfrozen since. `added` and
    `removed` are [(position in the chain, address)]; what the indexer says is untrusted."""
    state = {}
    for _, address, on in sorted([(*e, True) for e in added] + [(*e, False) for e in removed]):
        state[address] = on
    name, kind = f"Frozen by {issuer} ({token})", f"frozen by {issuer}"
    return [
        (a, name, "flagged", kind, source) for a, on in state.items() if on and database.valid(a)
    ]


def _head(client, url, base):
    """The newest block: Etherscan and Routescan say it in their proxy module, Blockscout in
    its block module."""
    for module, action in (("proxy", "eth_blockNumber"), ("block", "eth_block_number")):
        got = chains.get(client, url, {**base, "module": module, "action": action}).json()
        if str(got.get("result")).startswith("0x"):
            return int(got["result"], 16)
    raise RuntimeError("the indexer says no block number")


def evm_logs(client, cfg, head, contract, topic, start):
    """[((block, log index), address)] of one event of `contract`, from block `start` to
    `head`, in windows of blocks: a public indexer gives up on a wide range, and a full
    answer is read on from its last block (read again, kept once). The address is the
    event's only argument, indexed (in the topics) or not (in the data)."""
    base = {**cfg.get("params", {}), **({"apikey": cfg["apikey"]} if cfg.get("apikey") else {})}
    hexint = lambda v: int(v[2:] or "0", 16)  # Etherscan writes a zero as "0x"
    found, low, span = {}, start, SPAN
    while low <= head:
        high = min(low + span - 1, head)
        query = {"module": "logs", "action": "getLogs", "address": contract, "topic0": topic}
        query |= {"fromBlock": low, "toBlock": high, "page": 1, "offset": LOG_ROWS}
        data = chains.get(client, cfg["url"], {**base, **query}).json()
        rows = data.get("result")
        if not isinstance(rows, list):  # a list, empty when nothing was found
            if "narrower" in str(data.get("message")) and span > 10_000:
                span //= 2
                continue
            raise RuntimeError(f"getLogs: {data.get('message')} {str(rows)[:80]}")
        for r in rows:
            word = r["topics"][1] if len(r["topics"]) > 1 else r["data"]
            found[(hexint(r["blockNumber"]), hexint(r["logIndex"]))] = "0x" + word[-40:].lower()
        if len(rows) < LOG_ROWS:
            low = high + 1
        elif (last := max(p[0] for p in found)) > low:
            low = last
        else:
            raise RuntimeError("getLogs: more events in one block than an answer holds")
    return list(found.items())


def read_freezes_ethereum(client):
    cfg = chains.evm_chains().get("ethereum", {})
    if not cfg.get("url") or cfg.get("needs_key") or cfg.get("api"):  # NodeReal has no logs
        raise RuntimeError("Ethereum is read by no Etherscan-style indexer")
    head = _head(client, cfg["url"], cfg.get("params", {}))
    rows = []
    for issuer, token, contract, start, on, off in ETH_FREEZES:
        added, removed = (evm_logs(client, cfg, head, contract, t, start) for t in (on, off))
        rows += frozen("freezes-ethereum", issuer, token, added, removed)
    return rows


def tron_events(client, contract, event):
    """[((block, index), address)] of one event of a Tron contract, a page at a time. The
    event names the address in hex (0x or 41, then 20 bytes); here it takes its usual form."""
    url = f"{chains.TRON}/v1/contracts/{contract}/events"
    query = {"event_name": event, "limit": chains.TRON_ROWS, "only_confirmed": "true"}
    query["order_by"] = "block_timestamp,asc"
    out = []
    while True:
        data = chains.get(client, url, query).json()
        if "data" not in data:  # TronGrid answers a suspension with an error, not a page
            raise RuntimeError(str(data.get("Error") or data)[:120])
        for r in data["data"]:
            raw = bytes.fromhex(r["result"]["0"][-40:])
            out.append(((r["block_number"], r["event_index"]), base58.encode_check(b"\x41" + raw)))
        fingerprint = data.get("meta", {}).get("fingerprint")
        if len(data["data"]) < chains.TRON_ROWS or not fingerprint:
            return out
        query["fingerprint"] = fingerprint
        time.sleep(TRON_PAUSE)


def read_freezes_tron(client):
    rows = []
    for issuer, token, contract, on, off in TRON_FREEZES:
        added, removed = (tron_events(client, contract, e) for e in (on, off))
        rows += frozen("freezes-tron", issuer, token, added, removed)
    return rows


# Lists read whole each time, replacing their old copy. Read last, so they are stored last.
FACTS = {
    "freezes-ethereum": read_freezes_ethereum,
    "freezes-tron": read_freezes_tron,
    "ofac-sanctions": read_ofac,
}


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


SETS = [f"{explorer}-labels" for explorer in EXPLORERS] + ["graphsense-tagpacks", "solana-programs"]
SETS += list(FACTS)


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
    if os.environ.get("CT_LABELS", "on") == "off":
        return 0, []  # not even when asked for everything
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
    replaced = []
    for name, read in FACTS.items():
        if name not in wanted:
            continue
        try:
            rows += read(client)
            done.append(name)
            replaced.append((name,))
        except (httpx.HTTPError, RuntimeError, ValueError, KeyError, TypeError, IndexError):
            failed.append(name)  # its old copy stays until a read succeeds
    db.executemany("DELETE FROM label WHERE source = ?", replaced)
    store(db, rows)
    now = str(time.time())
    db.executemany(
        "INSERT OR REPLACE INTO meta VALUES (?, ?)", [(f"labels_at:{n}", now) for n in done]
    )
    db.commit()
    return len(rows), failed
