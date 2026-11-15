"""Chain adapters: fetch one address from a public or self-hosted indexer and normalise it
to transfer rows (chain, txid, src, dst, asset, token, amount, ts)."""

import json
import os
import time
from collections import Counter

import httpx

from .heuristics import btc_inputs, btc_links

ESPLORA = os.environ.get("CT_ESPLORA", "https://blockstream.info/api").rstrip("/")
# Etherscan-compatible APIs: Routescan, Blockscout, Etherscan v2, or your own Blockscout.
# Only Ethereum has a usable endpoint without a key; see chains.example.json for more chains.
DEFAULT_EVM = {
    "ethereum": {
        "url": "https://api.routescan.io/v2/network/mainnet/evm/1/etherscan/api",
        "symbol": "ETH",
    },
}
BTC_PAGES = int(os.environ.get("CT_BTC_PAGES", "8"))  # 25 transactions per page
EVM_ROWS = int(os.environ.get("CT_EVM_ROWS", "1000"))  # newest rows per list call
FANOUT = 50  # counterparties kept per Bitcoin transaction
DELAY = float(os.environ.get("CT_DELAY", "0.25"))  # pause between calls, for free APIs


def evm_chains():
    path = os.environ.get("CT_CHAINS")
    if not path:
        return DEFAULT_EVM
    with open(path) as f:
        return json.load(f)


def get(client, url, params=None):
    for attempt in range(4):
        time.sleep(DELAY)
        r = client.get(url, params=params)
        if r.status_code != 429 and r.status_code < 500:
            break
        wait = r.headers.get("retry-after", "")
        time.sleep(min(int(wait), 30) if wait.isdigit() else 2 ** (attempt + 1))
    if r.status_code == 429:
        raise RuntimeError("rate limited by the API (HTTP 429): raise CT_DELAY or add an API key")
    r.raise_for_status()
    return r


def describe(error):
    """Error text safe to store and show: request URLs carry the API key, so never those."""
    if isinstance(error, httpx.HTTPStatusError):
        return f"HTTP {error.response.status_code}"
    if isinstance(error, httpx.HTTPError):
        return type(error).__name__
    return str(error).splitlines()[0][:160] if str(error) else type(error).__name__


def btc_transfers(address, tx):
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
    else:
        flows = [(a, address, outs[address] * v / total) for a, v in ins.items()]
    flows = sorted(flows, key=lambda f: -f[2])[:FANOUT]
    return [("bitcoin", tx["txid"], s, d, "BTC", "", v / 1e8, ts) for s, d, v in flows]


def fetch_btc(client, address):
    stats = get(client, f"{ESPLORA}/address/{address}").json()["chain_stats"]
    txs, last = [], None
    for _ in range(BTC_PAGES):
        page = get(client, f"{ESPLORA}/address/{address}/txs/chain" + (f"/{last}" if last else ""))
        page = page.json()
        txs += page
        if len(page) < 25:
            break
        last = page[-1]["txid"]
    return {
        "transfers": [row for tx in txs for row in btc_transfers(address, tx)],
        "links": [link for tx in txs for link in btc_links(address, tx)],
        "balances": [
            ("bitcoin", "BTC", "", (stats["funded_txo_sum"] - stats["spent_txo_sum"]) / 1e8)
        ],
        "hub": stats["tx_count"] > len(txs),
    }


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


def fetch_evm(client, address, chains=None):
    out = {"transfers": [], "links": [], "balances": [], "hub": False, "errors": []}
    for chain, cfg in (chains or evm_chains()).items():

        def call(action, cfg=cfg, **extra):
            params = {"module": "account", "action": action, "address": address}
            if cfg.get("apikey"):
                params["apikey"] = cfg["apikey"]
            data = get(client, cfg["url"], {**cfg.get("params", {}), **params, **extra}).json()
            # Etherscan reports an empty list as status 0, "No transactions found".
            if data.get("status") != "1" and not str(data.get("message", "")).startswith("No "):
                raise RuntimeError(
                    f"{action}: {data.get('message')} {str(data.get('result'))[:80]}"
                )
            return data.get("result") or []

        try:
            page = {"page": 1, "offset": EVM_ROWS, "sort": "desc"}
            for action in ("txlist", "txlistinternal", "tokentx"):
                items = call(action, **page)
                out["hub"] |= len(items) >= EVM_ROWS
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
        # One unreachable chain must not hide the others.
        except (httpx.HTTPError, RuntimeError, ValueError, KeyError, TypeError) as e:
            out["errors"].append(f"{chain}: {describe(e)}")
    return out
