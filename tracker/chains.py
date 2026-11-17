"""Chain adapters: fetch one address from a public or self-hosted indexer and normalise it
to transfer rows (chain, txid, src, dst, asset, token, amount, ts).

Bitcoin, Litecoin, Bitcoin Cash and Dogecoin share the UTXO code and its heuristics; the
EVM chains share one Etherscan-style adapter; Tron and Solana are read more shallowly.
"""

import json
import os
import time
from collections import Counter, namedtuple
from datetime import datetime

import httpx
from embit import base58

from . import db as database
from .heuristics import btc_inputs, btc_links, looks_like_coinjoin

ESPLORA = os.environ.get("CT_ESPLORA", "https://blockstream.info/api").rstrip("/")
# chain -> asset, API flavour, base URL, and what a reader of a verdict must know about it.
UTXO = {
    "bitcoin": ("BTC", "esplora", ESPLORA, ""),
    "litecoin": ("LTC", "esplora", os.environ.get("CT_LITECOIN", "https://litecoinspace.org/api"), ""),
    "bitcoincash": (
        "BCH", "haskoin", os.environ.get("CT_BITCOINCASH", "https://api.haskoin.com/bch"),
        "Bitcoin Cash: only the 100 newest transactions of an address are read.",
    ),
    "dogecoin": (
        "DOGE", "blockcypher",
        os.environ.get("CT_DOGECOIN", "https://api.blockcypher.com/v1/doge/main"),
        "Dogecoin: only the 50 newest transactions are read (about 100 lookups an hour).",
    ),
}  # fmt: skip
TRON = os.environ.get("CT_TRON", "https://api.trongrid.io").rstrip("/")
SOLANA = os.environ.get("CT_SOLANA", "https://api.mainnet-beta.solana.com")
SOLANA_TXS = int(os.environ.get("CT_SOLANA_TXS", "25"))
SOLANA_PAGE = 25  # signatures per page of a full read: each one is a request of its own
TRON_ROWS = 200  # rows per page of a Tron list
SHALLOW = {
    "tron": "Tron: the 200 newest transfers of an address are read; counterparties are not "
    "expanded automatically.",
    "solana": f"Solana: only the {SOLANA_TXS} newest transactions are read, transfers are net "
    "balance changes per transaction, and counterparties are not expanded automatically.",
}
DEEP = {"evm", "bitcoin", "litecoin", "bitcoincash"}  # chains whose counterparties get expanded

# EVM chains built in: chain id, native coin, DefiLlama's name for the chain.
EVM = {
    "ethereum": (1, "ETH", "ethereum"),
    "base": (8453, "ETH", "base"),
    "arbitrum": (42161, "ETH", "arbitrum"),
    "optimism": (10, "ETH", "optimism"),
    "polygon": (137, "POL", "polygon"),
    "bsc": (56, "BNB", "bsc"),
    "avalanche": (43114, "AVAX", "avax"),
}
KEYLESS = {"ethereum", "avalanche"}  # served by Routescan without a key
NAMES = {"bitcoincash": "Bitcoin Cash", "bsc": "BNB Chain", "evm": "EVM"}
TOKEN_PROGRAMS = (  # Solana's two token programs: SPL Token, and Token-2022 (e.g. xStocks)
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
)


def name(chain):
    return NAMES.get(chain, chain.capitalize())


def icon(chain, read=()):
    """The chain mark of an address: an EVM address shows its chain once exactly one is known
    (named by a payment URI, or the only one read), the generic EVM mark until then."""
    return read[0] if chain == "evm" and len(read) == 1 else chain


def builtin_evm(key):
    """Etherscan-style config per built-in chain. Without an Etherscan key only the keyless
    chains can be read; the others are marked instead of failing on every sync."""
    out = {}
    for chain, (chain_id, symbol, llama) in EVM.items():
        cfg = {"symbol": symbol, "llama": llama}
        if chain in KEYLESS:
            cfg["url"] = f"https://api.routescan.io/v2/network/mainnet/evm/{chain_id}/etherscan/api"
            cfg["tokenbalance"] = True  # Routescan lists every token held, without a key
        elif key:
            cfg |= {
                "url": "https://api.etherscan.io/v2/api",
                "params": {"chainid": str(chain_id)},
                "apikey": key,
            }
        else:
            cfg["needs_key"] = True
        out[chain] = cfg
    return out


DEFAULT_EVM = builtin_evm(os.environ.get("CT_ETHERSCAN_KEY"))
BTC_PAGES = int(os.environ.get("CT_BTC_PAGES", "8"))  # 25 transactions per page
EVM_ROWS = int(os.environ.get("CT_EVM_ROWS", "1000"))  # newest rows per list call
FANOUT = 50  # counterparties kept per UTXO transaction
DELAY = float(os.environ.get("CT_DELAY", "0.25"))  # pause between calls, for free APIs


def evm_chains():
    """Built-in chains, plus or overridden by the CT_CHAINS file (self-hosted indexers)."""
    path = os.environ.get("CT_CHAINS")
    if not path:
        return DEFAULT_EVM
    with open(path) as f:
        return {**DEFAULT_EVM, **json.load(f)}


# chain -> (address page, transaction page); {} is the address or transaction id.
EXPLORER = {
    "bitcoin": ("https://mempool.space/address/{}", "https://mempool.space/tx/{}"),
    "litecoin": ("https://litecoinspace.org/address/{}", "https://litecoinspace.org/tx/{}"),
    "bitcoincash": (
        "https://blockchair.com/bitcoin-cash/address/{}",
        "https://blockchair.com/bitcoin-cash/transaction/{}",
    ),
    "dogecoin": (
        "https://blockchair.com/dogecoin/address/{}",
        "https://blockchair.com/dogecoin/transaction/{}",
    ),
    "ethereum": ("https://etherscan.io/address/{}", "https://etherscan.io/tx/{}"),
    "base": ("https://basescan.org/address/{}", "https://basescan.org/tx/{}"),
    "arbitrum": ("https://arbiscan.io/address/{}", "https://arbiscan.io/tx/{}"),
    "optimism": (
        "https://optimistic.etherscan.io/address/{}",
        "https://optimistic.etherscan.io/tx/{}",
    ),
    "polygon": ("https://polygonscan.com/address/{}", "https://polygonscan.com/tx/{}"),
    "bsc": ("https://bscscan.com/address/{}", "https://bscscan.com/tx/{}"),
    "avalanche": ("https://snowtrace.io/address/{}", "https://snowtrace.io/tx/{}"),
    "tron": ("https://tronscan.org/#/address/{}", "https://tronscan.org/#/transaction/{}"),
    "solana": ("https://solscan.io/account/{}", "https://solscan.io/tx/{}"),
}


def explorer(chain):
    """Link templates for a chain; an EVM address without a chain opens on Ethereum's."""
    address, tx = EXPLORER.get("ethereum" if chain == "evm" else chain, ("", ""))
    return {"explorer_address": address, "explorer_tx": tx}


def coverage():
    """What is read on each chain, for the UI and for honest verdicts."""
    out = [
        {"chain": chain, "asset": asset, "status": "ok", "note": note}
        for chain, (asset, _, _, note) in UTXO.items()
    ]
    for chain, cfg in evm_chains().items():
        needs = cfg.get("needs_key")
        note = "Needs a free Etherscan API key in CT_ETHERSCAN_KEY." if needs else ""
        out.append(
            {
                "chain": chain,
                "asset": cfg["symbol"],
                "status": "needs_key" if needs else "ok",
                "note": note,
            }
        )
    out.append({"chain": "tron", "asset": "TRX", "status": "ok", "note": SHALLOW["tron"]})
    out.append({"chain": "solana", "asset": "SOL", "status": "ok", "note": SHALLOW["solana"]})
    return [{**c, "name": name(c["chain"]), **explorer(c["chain"])} for c in out]


def notes(chain):
    """Caveats for a wallet with an address on `chain` (as returned by db.chain_of)."""
    if chain == "evm":
        missing = [c for c, cfg in evm_chains().items() if cfg.get("needs_key")]
        return (
            [f"Not read on {', '.join(missing)}: that needs a free Etherscan API key."]
            if missing
            else []
        )
    note = UTXO[chain][3] if chain in UTXO else SHALLOW.get(chain, "")
    return [note] if note else []


class RateLimited(RuntimeError):
    pass


def get(client, url, params=None, patient=True):
    """GET with a pause before, and a few retries on 429/5xx. `patient=False` is for APIs
    with an hourly quota, where waiting half a minute cannot help."""
    attempts = 4 if patient else 1
    for attempt in range(attempts):
        time.sleep(DELAY)
        r = client.get(url, params=params)
        if (r.status_code != 429 and r.status_code < 500) or attempt == attempts - 1:
            break
        wait = r.headers.get("retry-after", "")
        time.sleep(min(int(wait), 30) if wait.isdigit() else 2 ** (attempt + 1))
    if r.status_code == 429:
        raise RateLimited("rate limited by the API (HTTP 429): raise CT_DELAY or add an API key")
    r.raise_for_status()
    return r


def describe(error):
    """Error text safe to store and show: request URLs carry the API key, so never those."""
    if isinstance(error, httpx.HTTPStatusError):
        return f"HTTP {error.response.status_code}"
    if isinstance(error, httpx.HTTPError):
        return type(error).__name__
    return str(error).splitlines()[0][:160] if str(error) else type(error).__name__


def utxo_transfers(address, tx, chain="bitcoin", asset="BTC", unit=1e8, token="", keep=()):
    ins = btc_inputs(tx)
    total = sum(ins.values()) or 1
    ts = tx["status"].get("block_time") or 0
    outs = Counter()
    for o in tx["vout"]:
        if o.get("scriptpubkey_address"):
            outs[o["scriptpubkey_address"]] += o["value"]
    # An address graph of a UTXO transaction: each input funds each output pro rata.
    if address in ins:
        flows = [(address, a, v * ins[address] / total) for a, v in outs.items() if a not in ins]
    elif address in outs:
        flows = [(a, address, outs[address] * v / total) for a, v in ins.items()]
    else:
        return []
    # A very wide transaction is cut to its largest flows (see `wide`), but never loses a
    # flow to or from an address in `keep`: my own wallets, where a dropped flow is a
    # missed link.
    flows.sort(key=lambda f: ((f[1] if f[0] == address else f[0]) not in keep, -f[2]))
    return [
        (chain, tx["txid"], s, d, asset, token, v / unit, ts) for s, d, v in flows[:FANOUT] if v > 0
    ]


def wide(address, tx):
    """True when `utxo_transfers` cuts this transaction: more counterparties than FANOUT."""
    ins = btc_inputs(tx)
    outs = {o["scriptpubkey_address"] for o in tx["vout"] if o.get("scriptpubkey_address")}
    if address in ins:
        return len(outs - set(ins)) > FANOUT
    return address in outs and len(ins) > FANOUT


btc_transfers = utxo_transfers


def _esplora(client, url, address):
    stats = get(client, f"{url}/address/{address}").json()["chain_stats"]
    txs, last = [], None
    for _ in range(BTC_PAGES):
        page = get(client, f"{url}/address/{address}/txs/chain" + (f"/{last}" if last else ""))
        page = page.json()
        txs += page
        if len(page) < 25:
            break
        last = page[-1]["txid"]
    return txs, stats["funded_txo_sum"] - stats["spent_txo_sum"], stats["tx_count"]


def _coin(address, value, kind):
    return {"scriptpubkey_address": address, "value": value or 0, "scriptpubkey_type": kind}


COOLDOWN = {}  # indexer URL -> time until which its quota is spent


def _blockcypher(client, url, address):
    # About 100 lookups an hour without a key. Once the quota is spent, stop asking for an
    # hour: what was fetched stays, and the remaining addresses follow on a later sync.
    waiting = "the indexer's hourly limit is used up; the rest follows on a later sync"
    if COOLDOWN.get(url, 0) > time.time():
        raise RateLimited(waiting)
    try:
        data = get(client, f"{url}/addrs/{address}/full", {"limit": 50}, patient=False).json()
    except RateLimited:
        COOLDOWN[url] = time.time() + 3600
        raise RateLimited(waiting) from None
    txs = [_cypher_tx(t) for t in data.get("txs", [])]
    return txs, data["final_balance"], data["n_tx"]


def _cypher_tx(t):
    """A BlockCypher transaction in Esplora's shape."""
    return {
        "txid": t["hash"],
        "vin": [
            {
                "prevout": _coin(
                    (i.get("addresses") or [None])[0], i.get("output_value"), i.get("script_type")
                )
            }
            for i in t["inputs"]
        ],
        "vout": [
            _coin((o.get("addresses") or [None])[0], o.get("value"), o.get("script_type"))
            for o in t["outputs"]
        ],
        "status": {
            "block_time": int(datetime.fromisoformat(t["confirmed"]).timestamp())
            if t.get("confirmed")
            else 0,
            "block_height": t.get("block_height") or 0,
        },
    }


def _haskoin(client, url, address):
    bare = address.removeprefix("bitcoincash:")
    balance = get(client, f"{url}/address/{bare}/balance").json()
    data = get(client, f"{url}/address/{bare}/transactions/full", {"limit": 100}).json()

    return [_haskoin_tx(t) for t in data], balance["confirmed"], balance["txs"]


def _haskoin_tx(t):
    """A Haskoin transaction in Esplora's shape."""

    def kind(coin):  # the first script bytes are enough to tell address types apart
        return (coin.get("pkscript") or "")[:4]

    return {
        "txid": t["txid"],
        "vin": [{"prevout": _coin(i.get("address"), i.get("value"), kind(i))} for i in t["inputs"]],
        "vout": [_coin(o.get("address"), o.get("value"), kind(o)) for o in t["outputs"]],
        "status": {
            "block_time": t.get("time") or 0,
            "block_height": (t.get("block") or {}).get("height") or 0,
        },
    }


def fetch_utxo(client, address, chain="bitcoin", keep=()):
    asset, api, url, _ = UTXO[chain]
    txs, balance, count = {"esplora": _esplora, "blockcypher": _blockcypher, "haskoin": _haskoin}[
        api
    ](client, url, address)
    return {
        "transfers": [
            row for tx in txs for row in utxo_transfers(address, tx, chain, asset, keep=keep)
        ],
        "links": [link for tx in txs for link in btc_links(address, tx)],
        # Transactions whose transfers say nothing about ownership, and those that were cut.
        "coinjoins": [tx["txid"] for tx in txs if looks_like_coinjoin(tx)],
        "wide": [tx["txid"] for tx in txs if wide(address, tx)],
        "balances": [(chain, asset, "", balance / 1e8)],
        "hub": count > len(txs),
        "count": count,  # the address's transactions, as the indexer counts them
    }


# One page of an address's history, newest first: the raw items, their positions (block
# height, timestamp or slot), the cursor of the next older page, and whether it is the last.
Page = namedtuple("Page", "items positions next last")


def page_utxo(client, chain, address, cursor=None):
    """One page of a UTXO address's history from `cursor` back (None: the newest)."""
    _, api, url, _ = UTXO[chain]
    if api == "esplora":
        tail = f"/{cursor}" if cursor else ""
        txs = get(client, f"{url}/address/{address}/txs/chain{tail}").json()
        size, nxt = 25, (txs[-1]["txid"] if txs else cursor)
    elif api == "haskoin":
        offset = int(cursor or 0)
        bare = address.removeprefix("bitcoincash:")
        params = {"limit": 100, "offset": offset}
        txs = [
            _haskoin_tx(t)
            for t in get(client, f"{url}/address/{bare}/transactions/full", params).json()
        ]
        size, nxt = 100, str(offset + len(txs))
    else:  # blockcypher: by block height, and within the hourly quota
        if COOLDOWN.get(url, 0) > time.time():
            raise RateLimited("the indexer's hourly limit is used up; the rest follows later")
        params = {"limit": 50, **({"before": cursor} if cursor else {})}
        try:
            data = get(client, f"{url}/addrs/{address}/full", params, patient=False).json()
        except RateLimited:
            COOLDOWN[url] = time.time() + 3600
            raise
        txs = [_cypher_tx(t) for t in data.get("txs", [])]
        low = min((t["status"]["block_height"] for t in txs), default=None)
        # `before` leaves out its own block: start again inside the last one, unless a single
        # block filled the page, where that would read the same page forever.
        nxt = cursor if low is None else str(low + 1 if str(low + 1) != cursor else low)
        size = 50
    positions = [t["status"].get("block_height") or 0 for t in txs]
    return Page(txs, positions, nxt, len(txs) < size)


def fetch_btc(client, address):
    return fetch_utxo(client, address, "bitcoin")


def tron_transfers(native, tokens):
    """Transfer rows of Tron's native and TRC-20 lists, and the tokens seen, by contract."""

    def b58(hex_address):  # the native transfer list speaks hex
        return base58.encode_check(bytes.fromhex(hex_address))

    transfers, info = [], {}
    for t in native:
        try:
            contract = t["raw_data"]["contract"][0]
            v = contract["parameter"]["value"]
            if contract["type"] != "TransferContract" or t["ret"][0]["contractRet"] != "SUCCESS":
                continue
            row = ("tron", t["txID"], b58(v["owner_address"]), b58(v["to_address"]), "TRX", "")
            transfers.append((*row, v["amount"] / 1e6, t["block_timestamp"] // 1000))
        except (KeyError, IndexError, ValueError, TypeError):
            continue
    for t in tokens:
        try:
            token = t["token_info"]
            info[token["address"]] = token
            amount = int(t["value"]) / 10 ** int(token.get("decimals") or 0)
            if t.get("type") == "Transfer" and amount:
                row = ("tron", t["transaction_id"], t["from"], t["to"], token["symbol"][:20])
                transfers.append((*row, token["address"], amount, t["block_timestamp"] // 1000))
        except (KeyError, ValueError, TypeError):
            continue
    return transfers, info


def page_tron(client, address, kind, cursor=None):
    """One page of a Tron list ("native" or "trc20") from timestamp `cursor` (ms) down."""
    tail = "/trc20" if kind == "trc20" else ""
    params = {
        "limit": TRON_ROWS,
        "only_confirmed": "true",
        **({"max_timestamp": cursor} if cursor else {}),
    }
    rows = get(client, f"{TRON}/v1/accounts/{address}/transactions{tail}", params).json()
    rows = rows.get("data", [])
    positions = [int(r.get("block_timestamp") or 0) for r in rows]
    low = min(positions, default=None)
    # Read the lowest timestamp again (stored once), unless one timestamp filled the page.
    nxt = cursor if low is None else str(low if low != max(positions) else low - 1)
    return Page(rows, positions, nxt, len(rows) < TRON_ROWS)


def fetch_tron(client, address):
    account = (get(client, f"{TRON}/v1/accounts/{address}").json().get("data") or [{}])[0]
    page = {"limit": TRON_ROWS, "only_confirmed": "true"}
    native = get(client, f"{TRON}/v1/accounts/{address}/transactions", page).json().get("data", [])
    tokens = get(client, f"{TRON}/v1/accounts/{address}/transactions/trc20", page).json()
    tokens = tokens.get("data", [])
    transfers, info = tron_transfers(native, tokens)
    balances = [("tron", "TRX", "", account.get("balance", 0) / 1e6)]
    for entry in account.get("trc20", []):
        for contract, raw in entry.items():
            if contract in info and int(raw):  # tokens never seen in a transfer have no name
                token = info[contract]
                amount = int(raw) / 10 ** int(token.get("decimals") or 0)
                balances.append(("tron", token["symbol"][:20], contract, amount))
    return {
        "transfers": transfers,
        "links": [],
        "balances": balances,
        "hub": len(native) >= TRON_ROWS or len(tokens) >= TRON_ROWS,
    }


def _rpc(client, method, *params):
    for attempt in range(4):
        time.sleep(DELAY)
        r = client.post(
            SOLANA, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        )
        if r.status_code != 429 and r.status_code < 500:
            break
        time.sleep(2 ** (attempt + 1))
    if r.status_code == 429:
        raise RuntimeError("rate limited by the Solana RPC (HTTP 429): raise CT_DELAY")
    data = r.raise_for_status().json()
    if "error" in data:
        raise RuntimeError(f"{method}: {str(data['error'].get('message'))[:120]}")
    return data["result"]


def solana_transfers(address, sig, tx):
    """Solana has no simple transfer list: read each transaction's balance changes and
    treat the accounts that lost money as inputs and those that gained as outputs."""
    meta, keys = tx["meta"], tx["transaction"]["message"]["accountKeys"]
    keys = [k["pubkey"] if isinstance(k, dict) else k for k in keys]
    status = {"block_time": tx.get("blockTime") or 0}

    def flows(deltas, asset, unit, token=""):
        pseudo = {
            "txid": sig,
            "status": status,
            "vin": [{"prevout": _coin(a, -d, None)} for a, d in deltas.items() if d < 0],
            "vout": [_coin(a, d, None) for a, d in deltas.items() if d > 0],
        }
        return utxo_transfers(address, pseudo, "solana", asset, unit, token)

    native = {
        k: post - pre for k, pre, post in zip(keys, meta["preBalances"], meta["postBalances"])
    }
    native[keys[0]] += meta.get("fee", 0)  # the fee payer did not send the fee to anybody
    rows = flows(native, "SOL", 1e9)
    tokens = {}
    for sign, side in ((-1, "preTokenBalances"), (1, "postTokenBalances")):
        for b in meta.get(side) or []:
            per_owner = tokens.setdefault((b["mint"], b["uiTokenAmount"]["decimals"]), Counter())
            per_owner[b.get("owner") or keys[b["accountIndex"]]] += sign * int(
                b["uiTokenAmount"]["amount"]
            )
    for (mint, decimals), deltas in tokens.items():
        # The symbol is not on chain; the portfolio fills it in from the price source.
        rows += flows(deltas, mint[:4] + "...", 10**decimals, mint)
    return rows


def page_solana(client, address, cursor=None, stop=None):
    """One page of a Solana address's signatures from signature `cursor` back, with the
    transaction of each one newer than slot `stop` (an older one is already stored)."""
    opts = {"limit": SOLANA_PAGE, **({"before": cursor} if cursor else {})}
    sigs = _rpc(client, "getSignaturesForAddress", address, opts)
    items = []
    for s in sigs:
        tx = None
        if not s.get("err") and (stop is None or (s.get("slot") or 0) > stop):
            tx = _rpc(
                client, "getTransaction", s["signature"],
                {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1},
            )  # fmt: skip
        items.append((s["signature"], tx if tx and tx.get("meta") else None))
    positions = [s.get("slot") or 0 for s in sigs]
    nxt = sigs[-1]["signature"] if sigs else cursor
    return Page(items, positions, nxt, len(sigs) < SOLANA_PAGE)


def fetch_solana(client, address):
    lamports = _rpc(client, "getBalance", address)["value"]
    held = [
        account
        for program in TOKEN_PROGRAMS
        for account in _rpc(
            client,
            "getTokenAccountsByOwner",
            address,
            {"programId": program},
            {"encoding": "jsonParsed"},
        )["value"]
    ]
    balances = Counter()
    for account in held:
        token = account["account"]["data"]["parsed"]["info"]
        balances[token["mint"]] += token["tokenAmount"].get("uiAmount") or 0
    signatures = _rpc(client, "getSignaturesForAddress", address, {"limit": SOLANA_TXS})
    transfers = []
    for s in signatures:
        if s.get("err"):
            continue
        tx = _rpc(
            client,
            "getTransaction",
            s["signature"],
            {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1},  # v1 parses like v0
        )
        if tx and tx.get("meta"):
            transfers += solana_transfers(address, s["signature"], tx)
    return {
        "transfers": transfers,
        "links": [],
        "balances": [
            ("solana", "SOL", "", lamports / 1e9),
            *[
                ("solana", mint[:4] + "...", mint, amount)
                for mint, amount in balances.items()
                if amount
            ],
        ],
        "hub": len(signatures) >= SOLANA_TXS,
    }


def fetch(client, address, keep=()):
    """Everything known about `address` on the chain(s) its format belongs to. `keep` are
    addresses whose flows survive the cut of a very wide transaction."""
    chain = database.chain_of(address)
    if chain in UTXO:
        return fetch_utxo(client, address, chain, keep)
    return {"evm": fetch_evm, "tron": fetch_tron, "solana": fetch_solana}[chain](client, address)


def evm_transfers(chain, native, items, token=False):
    rows = []
    for it in items:
        try:
            value = int(it.get("value") or 0)
            src = it["from"].lower()
            dst = (it.get("to") or "").lower()
            if it.get("isError") == "1" or not value or not dst or src == dst:
                continue  # failed, zero-value (address poisoning) or contract creation
            if token:
                # Token names are attacker-controlled text: keep them short.
                asset = (it.get("tokenSymbol") or "?")[:20]
                contract = it["contractAddress"].lower()
                amount = value / 10 ** int(it.get("tokenDecimal") or 18)
            else:
                asset, contract, amount = native, "", value / 1e18
            txid = it.get("hash") or it["transactionHash"]
            rows.append((chain, txid, src, dst, asset, contract, amount, int(it["timeStamp"])))
        except (KeyError, ValueError, TypeError):
            continue  # an indexer row we do not understand (e.g. an NFT) is not a transfer
    return rows


def _evm_call(client, cfg, address, action, **extra):
    params = {"module": "account", "action": action, "address": address}
    if cfg.get("apikey"):
        params["apikey"] = cfg["apikey"]
    data = get(client, cfg["url"], {**cfg.get("params", {}), **params, **extra}).json()
    # Etherscan reports an empty list as status 0, "No transactions found".
    if data.get("status") != "1" and not str(data.get("message", "")).startswith("No "):
        raise RuntimeError(f"{action}: {data.get('message')} {str(data.get('result'))[:80]}")
    return data.get("result") or []


def page_evm(client, cfg, address, action, cursor=None):
    """One page of an EVM list (txlist, txlistinternal, tokentx) from block `cursor` down.
    Always the first page of a query that ends at a lower block, so a source that serves only
    the newest 10,000 rows of a query never stops it."""
    extra = {
        "page": 1,
        "offset": EVM_ROWS,
        "sort": "desc",
        **({"endblock": cursor} if cursor else {}),
    }
    items = _evm_call(client, cfg, address, action, **extra)
    positions = [int(it.get("blockNumber") or 0) for it in items]
    low = min(positions, default=None)
    # The next query ends at the lowest block read, which it reads again (stored once), unless
    # one block filled the whole page: then it ends below it.
    nxt = cursor if low is None else str(low if low != max(positions) else low - 1)
    return Page(items, positions, nxt, len(items) < EVM_ROWS)


def fetch_evm(client, address, chains=None):
    # "names": (chain, contract) -> the token's full name, where the indexer gives one.
    # "sent": the transactions the address sent, also those that move no value (a claim()).
    out = {"transfers": [], "links": [], "balances": [], "hub": False, "errors": [], "names": {}}
    out["sent"] = set()
    for chain, cfg in (chains or evm_chains()).items():
        if cfg.get("needs_key"):
            continue  # reported once by coverage(), not as an error on every address

        def call(action, cfg=cfg, **extra):
            return _evm_call(client, cfg, address, action, **extra)

        try:
            page = {"page": 1, "offset": EVM_ROWS, "sort": "desc"}
            for action in ("txlist", "txlistinternal", "tokentx"):
                items = call(action, **page)
                out["hub"] |= len(items) >= EVM_ROWS
                if action == "txlist":
                    mine = [it for it in items if str(it.get("from")).lower() == address]
                    out["sent"] |= {it.get("hash") for it in mine}
                out["transfers"] += evm_transfers(chain, cfg["symbol"], items, action == "tokentx")
            native = int(call("balance") or 0) / 1e18
            if native:
                out["balances"].append((chain, cfg["symbol"], "", native))
            if cfg.get("tokenlist"):  # a Blockscout extension
                for t in call("tokenlist"):
                    if t.get("type") == "ERC-20" and int(t.get("balance") or 0):
                        amount = int(t["balance"]) / 10 ** int(t.get("decimals") or 18)
                        symbol = (t.get("symbol") or "?")[:20]
                        out["balances"].append(
                            (chain, symbol, t["contractAddress"].lower(), amount)
                        )
            if cfg.get("tokenbalance"):  # a Routescan extension; last, so its errors spare the rest
                # shortcut: one page of EVM_ROWS tokens; page on if spam-heavy wallets need all
                for t in call("addresstokenbalance", page=1, offset=EVM_ROWS):
                    amount = int(t["TokenQuantity"]) / 10 ** int(t.get("TokenDivisor") or 18)
                    if amount:
                        token = t["TokenAddress"].lower()
                        symbol = (t.get("TokenSymbol") or "?")[:20]
                        out["balances"].append((chain, symbol, token, amount))
                        out["names"][(chain, token)] = (t.get("TokenName") or "")[:40]
        # One unreachable chain must not hide the others.
        except (httpx.HTTPError, RuntimeError, ValueError, KeyError, TypeError) as e:
            out["errors"].append(f"{chain}: {describe(e)}")
    return out
