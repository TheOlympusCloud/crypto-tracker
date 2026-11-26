"""Esplora's answers for Litecoin, Dogecoin and Bitcoin Cash, for am-i-exposed's engine in the
browser (/api/tracker/utxo/<chain>/...): an address, its transactions and unspent outputs, a
transaction and where its outputs went. Litecoin's indexer speaks Esplora and is passed
through; Dogecoin's (BlockCypher) and Bitcoin Cash's (Haskoin) are converted with chains.py's
converters. Like a lookup it stores nothing: the indexer's answers are kept in memory for 10
minutes, so the calls of one scan read the indexer once."""

import re
import threading
import time
from collections import OrderedDict

from . import chains
from . import db as database

CHAINS = ("litecoin", "dogecoin", "bitcoincash")
NEWEST = {"dogecoin": 50, "bitcoincash": 100}  # transactions read, as a sync's first page
IO = 500  # inputs and outputs BlockCypher lists per transaction (its default is 20)
READS = OrderedDict()  # (indexer, path, params) -> (time read, its answer)
KEEP, KEPT = 600, 200  # seconds an answer is kept, and how many
_reads = threading.Lock()


def _read(client, chain, path, params=None):
    _, api, url, _ = chains.UTXO[chain]
    key = (url, path, tuple(sorted((params or {}).items())))
    with _reads:
        ts, data = READS.get(key, (0, None))
    if data is not None and time.time() - ts < KEEP:
        return data
    if api == "blockcypher":  # within its hourly quota
        data = chains.cypher(client, url, path, params)
    else:
        data = chains.get(client, f"{url}{path}", params).json()
    with _reads:
        READS.pop(key, None)
        READS[key] = (time.time(), data)
        while len(READS) > KEPT:
            READS.popitem(last=False)
    return data


def _history(client, chain, address):
    """(newest transactions, transaction count, sum received, sum spent) of a Dogecoin or
    Bitcoin Cash address."""
    if chain == "dogecoin":
        d = _read(client, chain, f"/addrs/{address}/full", {"limit": NEWEST[chain], "txlimit": IO})
        txs = [chains._cypher_tx(t) for t in d.get("txs", [])]
        return txs, d["n_tx"], d["total_received"], d["total_sent"]
    bare = address.removeprefix("bitcoincash:")
    b = _read(client, chain, f"/address/{bare}/balance")
    raw = _read(client, chain, f"/address/{bare}/transactions/full", {"limit": NEWEST[chain]})
    spent = b["received"] - b["confirmed"] - b["unconfirmed"]
    return [chains._haskoin_tx(t) for t in raw], b["txs"], b["received"], spent


def address(client, chain, address):
    if chain == "litecoin":
        return _read(client, chain, f"/address/{address}")
    txs, count, received, spent = _history(client, chain, address)
    # The indexers do not count outputs: these are counted in the transactions read, at least 1
    # where the sums say so. Lower bounds when some were not read; reuse needs no more.
    funded = sum(o["scriptpubkey_address"] == address for t in txs for o in t["vout"])
    spends = sum((i["prevout"] or {}).get("scriptpubkey_address") == address
                 for t in txs for i in t["vin"])  # fmt: skip
    funded, spends = max(funded, int(received > 0)), max(spends, int(spent > 0))
    stats = {
        "funded_txo_count": funded,
        "funded_txo_sum": received,
        "spent_txo_count": spends,
        "spent_txo_sum": spent,
        "tx_count": count,
    }
    return {"address": address, "chain_stats": stats, "mempool_stats": dict.fromkeys(stats, 0)}


def txs(client, chain, address, after=None):
    if chain == "litecoin":
        tail = f"/chain/{after}" if after else ""
        return _read(client, chain, f"/address/{address}/txs{tail}")
    return [] if after else _history(client, chain, address)[0]  # only the newest are read


def utxos(client, chain, address):
    if chain == "litecoin":
        return _read(client, chain, f"/address/{address}/utxo")
    if chain == "dogecoin":
        d = _read(client, chain, f"/addrs/{address}", {"unspentOnly": "true", "limit": 2000})
        refs = d.get("txrefs", []) + d.get("unconfirmed_txrefs", [])
        rows = [(r["tx_hash"], r["tx_output_n"], r["value"], r.get("block_height")) for r in refs]
    else:
        bare = address.removeprefix("bitcoincash:")
        rows = [
            (u["txid"], u["index"], u["value"], (u.get("block") or {}).get("height"))
            for u in _read(client, chain, f"/address/{bare}/unspent")
        ]
    mined = lambda h: (
        {"confirmed": True, "block_height": h} if (h or 0) > 0 else {"confirmed": False}
    )
    return [{"txid": t, "vout": n, "value": v, "status": mined(h)} for t, n, v, h in rows]


def _raw(client, chain, txid):
    if chain == "dogecoin":
        return _read(client, chain, f"/txs/{txid}", {"limit": IO})
    return _read(client, chain, f"/transaction/{txid}")


def tx(client, chain, txid):
    if chain == "litecoin":
        return _read(client, chain, f"/tx/{txid}")
    raw = _raw(client, chain, txid)
    return chains._cypher_tx(raw) if chain == "dogecoin" else chains._haskoin_tx(raw)


def outspends(client, chain, txid):
    if chain == "litecoin":
        return _read(client, chain, f"/tx/{txid}/outspends")
    outs = _raw(client, chain, txid)["outputs"]
    if chain == "dogecoin":  # BlockCypher names the spending transaction, not its input
        return [{"spent": True, "txid": o["spent_by"]} if o.get("spent_by") else {"spent": False}
                for o in outs]  # fmt: skip
    return [
        {"spent": True, "txid": o["spender"]["txid"], "vin": o["spender"]["input"]}
        if o.get("spender")
        else {"spent": False}
        for o in outs
    ]


def _bare(value):
    """Bitcoin Cash addresses without their prefix, the form the scanner takes them in."""
    if isinstance(value, dict):
        return {k: _bare(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_bare(v) for v in value]
    return value.removeprefix("bitcoincash:") if isinstance(value, str) else value


TXID = "([0-9a-f]{64})"
ROUTES = [  # Esplora's paths that am-i-exposed's scans call
    (re.compile(r"address/([^/]+)"), address),
    (re.compile(r"address/([^/]+)/txs"), txs),
    (re.compile(rf"address/([^/]+)/txs/chain/{TXID}"), txs),
    (re.compile(r"address/([^/]+)/utxo"), utxos),
    (re.compile(rf"tx/{TXID}"), tx),
    (re.compile(rf"tx/{TXID}/outspends"), outspends),
]


def route(chain, path):
    """The read answering Esplora's `path` on `chain`, as a function of an HTTP client.
    LookupError: no such call here; ValueError: not an address of that chain."""
    if chain not in CHAINS:
        raise LookupError(chain)
    for pattern, read in ROUTES:
        if m := pattern.fullmatch(path):
            break
    else:
        raise LookupError(path)
    args = list(m.groups())
    if read in (address, txs, utxos):
        got, args[0] = database.parse(args[0])  # checksums too: a typo is refused
        if got != chain:
            raise ValueError(f"not a {chains.name(chain)} address")

    def answer(client):
        out = read(client, chain, *args)
        return _bare(out) if chain == "bitcoincash" else out

    return answer
