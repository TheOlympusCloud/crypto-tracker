"""Name records (docs/design/multichain.md, 2.3 #2 and 2.4 #5). A primary name is a public tag
on every transaction of the address, and the addresses of other chains a name lists tie them
to it for anyone who looks the name up.

- ENS: the Universal Resolver on Ethereum gives the address's primary name, then the name's
  address records in one call (a multicall).
- Basenames: the ENSIP-19 reverse registrar on Base, then the resolver the Basenames
  registry names.
- SNS: the primary .sol name, read from the name service's own accounts at the Solana RPC
  that already reads the wallet (CT_SOLANA), so no new party.

Anybody can point the reverse record of their own address at any name, so a primary name
counts only when the name points back at the address (the forward check). An `eth_call` goes
to the chain's "rpc" in CT_CHAINS (your node) when one is set, else to the indexer that reads
the wallet when it serves calls (Routescan, Etherscan), else to a public RPC
(docs/design/multichain.md, 4.9). CT_LABELS=off makes no name lookup at all.
"""

import base64
import hashlib
import json
import os
import time
from urllib.parse import urlparse

import httpx
from embit import base58
from embit.script import Script

from .. import busy, chains
from .. import db as database
from ..heuristics import pair

CONFIDENCE = 0.8  # a name lists another address (ENSIP-9): the name's owner says it is theirs
REFRESH = 86400  # seconds before the names of an address of mine are looked up again
PER_SYNC = 20  # addresses of mine looked up per sync, those checked longest ago first

UNIVERSAL = "0xeeeeeeee14d718c2b47d9923deab1335e144eeee"  # ENS Universal Resolver, Ethereum
L2_REVERSE = "0x0000000000d8e504002cc26e3ec46d81971c1664"  # ENSIP-19 reverse registrar, Base
BASENAMES = "0xb94704422c2a1e396835a571837aa5ae53285a95"  # Basenames registry, Base
PUBLIC = {  # where an eth_call goes when neither your node nor the indexer serves one
    "ethereum": "https://ethereum-rpc.publicnode.com",
    "base": "https://base-rpc.publicnode.com",
}
PROXY = {"api.routescan.io", "api.etherscan.io"}  # indexers whose proxy module serves eth_call
EVM_COIN = 0x80000000  # ENSIP-11: an EVM chain's coin type is this | its chain id; alone, any
BASE_COIN = EVM_COIN | chains.EVM["base"][0]
# ENSIP-9 coin type -> the chain whose address the record holds; 60 is Ethereum's.
# shortcut: Bitcoin Cash (145) is left out, it needs a CashAddr encoder; add one when asked.
COINS = {0: "bitcoin", 2: "litecoin", 3: "dogecoin", 195: "tron", 501: "solana", 60: "ethereum"}
COINS |= {EVM_COIN | i: c for c, (i, _, _) in chains.EVM.items() if c != "ethereum"}
COINS[EVM_COIN] = "evm"
SCRIPTS = {  # how a UTXO chain writes an output script as an address
    "bitcoin": {"p2pkh": b"\x00", "p2sh": b"\x05", "bech32": "bc"},
    "litecoin": {"p2pkh": b"\x30", "p2sh": b"\x32", "bech32": "ltc"},
    "dogecoin": {"p2pkh": b"\x1e", "p2sh": b"\x16", "bech32": None},
}
SNS = base58.decode("namesLPneVptA9Z5rqUDD9tMTWEJwofgaYwp8cawRkX")  # the name service program
SOL_TLD = base58.decode("58PwtjSDuFHuUkYjH9BYnnQKHfwo9reZhC2zMJv9JPkx")  # parent of .sol names
REVERSE_CLASS = base58.decode("33m47vH6Eav6jr5Ry86XjhRft2jRBLDnDgPSHoquXi2Z")
FAVOURITES = base58.decode("85iDfUvr3HJyLM2zcq5BXSiDvUWfw6cSE1FfNBo8Ap29")  # owners' primary names
ERRORS = (httpx.HTTPError, RuntimeError, ValueError, KeyError, TypeError, IndexError)


def _rol(lane, n):
    return ((lane << n) | (lane >> (64 - n))) & (2**64 - 1)


def _permute(s):
    """Keccak-f[1600] on 25 lanes, lane (x, y) at s[x + 5y]: FIPS 202's compact reference."""
    r = 1
    for _ in range(24):
        c = [s[x] ^ s[x + 5] ^ s[x + 10] ^ s[x + 15] ^ s[x + 20] for x in range(5)]
        for x in range(5):
            d = c[(x - 1) % 5] ^ _rol(c[(x + 1) % 5], 1)
            for y in range(0, 25, 5):
                s[x + y] ^= d
        x, y, lane = 1, 0, s[1]
        for t in range(24):
            x, y = y, (2 * x + 3 * y) % 5
            lane, s[x + 5 * y] = s[x + 5 * y], _rol(lane, (t + 1) * (t + 2) // 2 % 64)
        for y in range(0, 25, 5):
            row = s[y : y + 5]
            for x in range(5):
                s[x + y] = row[x] ^ (~row[(x + 1) % 5] & row[(x + 2) % 5])
        for j in range(7):
            r = ((r << 1) ^ ((r >> 7) * 0x71)) % 256
            if r & 2:
                s[0] ^= 1 << ((1 << j) - 1)


def keccak(data):
    """Keccak-256, Ethereum's hash; NIST's SHA3-256 in hashlib pads differently."""
    block = bytearray(data) + b"\x01" + bytes(-(len(data) + 1) % 136)
    block[-1] |= 0x80
    s = [0] * 25
    for at in range(0, len(block), 136):
        for i in range(17):
            s[i] ^= int.from_bytes(block[at + 8 * i : at + 8 * i + 8], "little")
        _permute(s)
    return b"".join(lane.to_bytes(8, "little") for lane in s[:4])


def namehash(name):
    node = bytes(32)
    for label in reversed(name.split(".")) if name else ():
        node = keccak(node + keccak(label.encode()))
    return node


def _encode(*args):
    """ABI encoding of static words (int) and dynamic byte strings (bytes)."""
    head, tail = b"", b""
    for a in args:
        if isinstance(a, int):
            head += a.to_bytes(32, "big")
        else:
            head += (32 * len(args) + len(tail)).to_bytes(32, "big")
            tail += len(a).to_bytes(32, "big") + a + bytes(-len(a) % 32)
    return head + tail


def _call(signature, *args):
    return keccak(signature.encode())[:4] + _encode(*args)


def _multicall(calls):
    """multicall(bytes[]): the array's length, then its items encoded as a tuple."""
    return keccak(b"multicall(bytes[])")[:4] + _encode(32, len(calls)) + _encode(*calls)


def _dynamic(data, i=0):
    """The `bytes` or `string` whose offset is word `i` of `data`."""
    at = int.from_bytes(data[32 * i : 32 * i + 32], "big")
    size = int.from_bytes(data[at : at + 32], "big")
    if len(data) < at + 32 + size:
        raise ValueError("short ABI data")
    return data[at + 32 : at + 32 + size]


def _array(data):
    """The items of a returned `bytes[]`."""
    body = data[int.from_bytes(data[:32], "big") :]
    count = int.from_bytes(body[:32], "big")
    if count > len(body) // 32:
        raise ValueError("short ABI data")
    return [_dynamic(body[32:], i) for i in range(count)]


def _dns(name):
    """A name as the Universal Resolver takes it: each label after its length."""
    labels = [label.encode() for label in name.split(".")]
    return b"".join(bytes([len(label)]) + label for label in labels) + b"\0"


def _target(chain):
    """(url, indexer config) an eth_call on `chain` goes to; no config means a JSON-RPC node."""
    cfg = chains.evm_chains().get(chain, {})
    if cfg.get("rpc"):
        return cfg["rpc"], None
    if urlparse(cfg.get("url", "")).hostname in PROXY and not cfg.get("needs_key"):
        return cfg["url"], cfg
    return PUBLIC[chain], None


def eth_call(client, chain, to, data):
    """`data` called on contract `to` of `chain`: the returned bytes, or None when the call
    reverted (no such name or record). Tried once: a lookup that fails is asked again on the
    next sync, and a revert must not wait through retries."""
    url, cfg = _target(chain)
    time.sleep(chains.DELAY)
    if cfg:
        params = {"module": "proxy", "action": "eth_call", "to": to, "data": "0x" + data.hex()}
        params |= {"tag": "latest", **({"apikey": cfg["apikey"]} if cfg.get("apikey") else {})}
        r = client.get(url, params={**cfg.get("params", {}), **params})
    else:
        call = [{"to": to, "data": "0x" + data.hex()}, "latest"]
        r = client.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "eth_call", "params": call})
    if r.status_code == 429:
        raise chains.RateLimited("rate limited by the API (HTTP 429): raise CT_DELAY")
    try:
        reply = r.json()
    except ValueError:
        reply = None
    error = reply.get("error") if isinstance(reply, dict) else None
    if isinstance(error, dict):
        # Code 3 is "execution reverted"; Routescan sends it with HTTP 500.
        if error.get("code") == 3 or "revert" in str(error.get("message")).lower():
            return None
        raise RuntimeError(f"eth_call: {str(error.get('message'))[:120]}")
    r.raise_for_status()
    result = reply.get("result") if isinstance(reply, dict) else None
    if not isinstance(result, str) or not result.startswith("0x"):
        raise RuntimeError(f"eth_call: {str(result)[:120]}")  # e.g. an Etherscan "NOTOK"
    return bytes.fromhex(result[2:])


def _address(chain, raw):
    """An address record's bytes (ENSIP-9) as the address the tracker stores, or None."""
    if not any(raw):
        return None  # unset
    try:
        if chain in SCRIPTS:
            text = Script(raw).address(SCRIPTS[chain])
        elif chain == "solana":
            text = base58.encode(raw) if len(raw) == 32 else ""
        elif chain == "tron":
            text = base58.encode_check(raw) if len(raw) == 21 else ""
        else:
            text = "0x" + raw.hex() if len(raw) == 20 else ""
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        return None
    return text if database.valid(text) else None


def _asks(name):
    node = int.from_bytes(namehash(name), "big")
    return _multicall([_call("addr(bytes32,uint256)", node, coin) for coin in COINS])


def _named(name, address, results, forward):
    """The name and the addresses it lists, when one of its `forward` records is `address`."""
    records = {}
    for coin, item in zip(COINS, results):
        try:
            text = _address(COINS[coin], _dynamic(item))
        except ValueError:
            continue  # the resolver has no such record: an error in its place
        if text:
            records[coin] = text
    if address not in (records.get(coin) for coin in forward):
        return None  # a reverse record pointing at somebody else's name
    return {"name": name, "listed": {COINS[c]: a for c, a in records.items() if a != address}}


def ens(client, address, spend):
    # shortcut: a name read through a gateway (CCIP-read: cb.id, or a .base.eth name set on
    # Ethereum) reverts here and counts as none; following gateways adds a party per name.
    spend()
    ask = _call("reverse(bytes,uint256)", bytes.fromhex(address[2:]), 60)
    reply = eth_call(client, "ethereum", UNIVERSAL, ask)
    name = _dynamic(reply).decode() if reply else ""
    if not name:
        return None
    spend()
    reply = eth_call(
        client, "ethereum", UNIVERSAL, _call("resolve(bytes,bytes)", _dns(name), _asks(name))
    )
    return _named(name, address, _array(_dynamic(reply)), (60,)) if reply else None


def basename(client, address, spend):
    # shortcut: only names under base.eth are checked forward; an ENS name set as the primary
    # name on Base is resolved on Ethereum through a gateway, which this does not ask.
    spend()
    reply = eth_call(client, "base", L2_REVERSE, _call("nameForAddr(address)", int(address, 16)))
    name = _dynamic(reply).decode() if reply else ""
    if not name.endswith(".base.eth"):
        return None
    spend()
    node = int.from_bytes(namehash(name), "big")
    reply = eth_call(client, "base", BASENAMES, _call("resolver(bytes32)", node))
    if not reply or not any(reply[12:32]):
        return None
    spend()
    reply = eth_call(client, "base", "0x" + reply[12:32].hex(), _asks(name))
    return _named(name, address, _array(reply), (60, BASE_COIN)) if reply else None


P = 2**255 - 19  # ed25519's field
D = -121665 * pow(121666, -1, P) % P


def _on_curve(key):
    """True when 32 bytes decode to an ed25519 point, as curve25519-dalek decompresses one."""
    y = int.from_bytes(key, "little") & ((1 << 255) - 1)
    x2 = (y * y - 1) * pow(D * y * y + 1, -1, P) % P
    return x2 == 0 or pow(x2, (P - 1) // 2, P) == 1


def _pda(seeds, program):
    """Solana's find_program_address: the first bump from 255 down whose hash is off the curve,
    so that no private key can sign for the account."""
    for bump in range(255, -1, -1):
        key = b"".join(seeds) + bytes([bump]) + program + b"ProgramDerivedAddress"
        key = hashlib.sha256(key).digest()
        if not _on_curve(key):
            return key
    raise ValueError("no program address")


def _accounts(client, keys):
    """The data of Solana accounts, None where there is none."""
    keys = [base58.encode(k) for k in keys]
    reply = chains._rpc(client, "getMultipleAccounts", keys, {"encoding": "base64"})
    return [base64.b64decode(a["data"][0]) if a else None for a in reply["value"]]


def sns(client, address, spend):
    owner = base58.decode(address)
    spend()
    favourite = _accounts(client, [_pda([b"favourite_domain", owner], FAVOURITES)])[0]
    if not favourite or len(favourite) < 33:
        return None
    account = favourite[1:33]
    hashed = hashlib.sha256(b"SPL Name Service" + base58.encode(account).encode()).digest()
    spend()
    record, reverse = _accounts(client, [account, _pda([hashed, REVERSE_CLASS, bytes(32)], SNS)])
    # The forward check: a name sold since still has the seller's old pointer.
    # shortcut: subdomains and wrapped (tokenized) names fail it; read their parent or the
    # wrapper's holder if they turn out to matter.
    if not record or not reverse or record[:32] != SOL_TLD or record[32:64] != owner:
        return None
    size = int.from_bytes(reverse[96:100], "little")
    return {"name": reverse[100 : 100 + size].decode().strip("\0") + ".sol", "listed": {}}


SERVICES = {"evm": [("ethereum", ens), ("base", basename)], "solana": [("solana", sns)]}


def lookup(client, address, spend=lambda: None):
    """The verified names of `address`: [{"chain", "name", "listed": {chain: address}}].
    `spend` is called before each request (the scan's budget)."""
    if os.environ.get("CT_LABELS", "on") == "off":
        return []
    out = []
    for chain, find in SERVICES.get(database.chain_of(address), []):
        if found := find(client, address, spend):
            out.append({"chain": chain, **found})
    return out


def stored(db, address):
    """The names `sync` kept for an address of mine, as `lookup` gives them; None when it
    never looked them up."""
    if not db.execute("SELECT 1 FROM meta WHERE key = ?", (f"names_at:{address}",)).fetchone():
        return None
    rows = db.execute("SELECT * FROM name WHERE address = ? ORDER BY rowid", (address,))
    return [
        {"chain": r["chain"], "name": r["name"], "listed": json.loads(r["listed"])} for r in rows
    ]


def check(ctx):
    """The scan's finding: the address's names, and the addresses they list. An address of
    mine is answered from its last lookup by a sync, without a call (a wallet's grade)."""
    found = stored(ctx.db, ctx.address)
    if found is None:
        found = lookup(ctx.client, ctx.address, ctx._spend)
    if not found:
        return
    names = ", ".join(f["name"] for f in found)
    yield "x-name-record", {"name": names}, []
    listed = {a: c for f in found for c, a in f["listed"].items()}
    if listed:
        params = {"name": names, "count": len(listed), "addresses": ", ".join(sorted(listed))}
        params["chains"] = ", ".join(sorted({chains.name(c) for c in listed.values()}))
        yield "x-name-addresses", params, []


def sync(db, client):
    """Look up the names of my EVM and Solana addresses not checked for a day, keep them, and
    link each address to the addresses its names list: a stranger only once it was read and
    found quiet (busy.check), as for every new kind of link. Returns what did not answer."""
    if os.environ.get("CT_LABELS", "on") == "off":
        return []
    mine = [r["address"] for r in db.execute("SELECT address FROM node WHERE mine")]
    rows = db.execute("SELECT key, value FROM meta WHERE key LIKE 'names_at:%'")
    at = {r["key"].removeprefix("names_at:"): float(r["value"]) for r in rows}
    due = [
        a for a in mine if database.chain_of(a) in SERVICES and time.time() - at.get(a, 0) > REFRESH
    ]
    failed = set()
    for address in sorted(due, key=lambda a: at.get(a, 0))[:PER_SYNC]:
        try:
            found = lookup(client, address)
            links = {
                (f["name"], other)
                for f in found
                for other in f["listed"].values()
                if not busy.check(db, client, other)
            }
        except ERRORS as e:
            failed.add(chains.describe(e))
            continue  # what was found before stays until a lookup answers
        db.execute(  # only this address's links: another wallet of mine can hold the same name
            "DELETE FROM link WHERE kind = 'name' AND ? IN (a, b) AND evidence IN "
            "(SELECT name FROM name WHERE address = ?)",
            (address, address),
        )
        db.execute("DELETE FROM name WHERE address = ?", (address,))
        db.executemany(
            "INSERT OR REPLACE INTO name VALUES (?, ?, ?, ?)",
            [(address, f["chain"], f["name"], json.dumps(f["listed"])) for f in found],
        )
        db.executemany(
            "INSERT OR REPLACE INTO link VALUES (?, ?, 'name', ?, ?)",
            [(*pair(address, other), CONFIDENCE, name) for name, other in links],
        )
        db.execute(
            "INSERT OR REPLACE INTO meta VALUES (?, ?)", (f"names_at:{address}", str(time.time()))
        )
        db.commit()
    return [f"names: {e}" for e in sorted(failed)]
