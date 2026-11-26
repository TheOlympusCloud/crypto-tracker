"""Bridge records: the recipient a bridge names in its own event, read from the receipt of a
transaction in which one of my wallets paid into it (docs/design/multichain.md, 3.1).

Circle's CCTP, Across, Wormhole's Token Bridge, the OP Stack bridges into Base and Optimism
and Arbitrum's gateway router say on the source chain who gets the money on the other. That
ties both ends together for anyone who reads it, as a THORChain swap does: each record goes
into the `swap` table and becomes a 'bridge-record' link of 0.95. The amount-and-time match
(heuristics.match_bridges) stays for bridges without a decoder.

Receipts come from the indexer that already reads the wallet, or the chain's `rpc` in
CT_CHAINS; on Solana, the owner of the token account CCTP and Wormhole name comes from the
Solana RPC that reads my wallets. No new party learns anything, so CT_SWAPS does not apply.
Each receipt is read once; the links are made from the records stored at every sync, so a
recipient declared mine later is linked then.
"""

import time

import httpx
from embit import base58

from . import busy, chains
from . import db as database
from .heuristics import pair

CONFIDENCE = 0.95  # a protocol's own record, as a swap's (swaps.CONFIDENCE)
SOURCE = "Bridge receipts"  # its row in the failure table, which the verdicts read
PROTOCOLS = ("Circle CCTP", "Across", "Wormhole", "OP Stack", "Arbitrum")
PAGES = 20  # Blockscout pages of 50 logs read per receipt: a deposit is far smaller

# Topic 0 of each event read.
CCTP_V1 = "0x2fa9ca894982930190727e75500a97d8dc500233a5065e0f3126c48fbe0343c0"  # DepositForBurn
CCTP_V2 = "0x0c8c1cbdc5190613ebd485511d4e2812cfa45eecb79d845893331fedad5130a5"  # DepositForBurn
ACROSS = "0x32ed1a409ef04c7b0227189c3a103dc5ac10e775a15b785dcc510201f7c25ad3"  # FundsDeposited
ACROSS_V3 = "0xa123dc29aebf7d0c3322c8eeb5b999e859f39937950ed31056532713d0de396f"  # V3FundsDeposited
WORMHOLE = (
    "0x6eb224fb001ed210e379b335e35efe88672a8ce935d981a6896b27ffdf52a3b2"  # LogMessagePublished
)
OP_ETH = "0x35d79ab81f2b2017e19afb5c5571778877782d7a8786f5907f93b0f4702f4f23"  # ETHDepositInitiated
OP_ERC20 = (
    "0x718594027abd4eaed59f95162563e0cc6d0e8d5b86b1c7be8b1b0ac3343d0396"  # ERC20DepositInitiated
)
ARBITRUM = "0x85291dff2161a93c2f12c819d31889c96c63042116f5bc5a205aa701c2c429f5"  # TransferRouted

# The contracts, checked on chain. CCTP v1: (TokenMessenger, which emits the event; TokenMinter,
# which takes the money). v2 has one address of each on every chain.
CCTP = {
    "ethereum": ("0xbd3fa81b58ba92a82136038b25adec7066af3155", "0xc4922d64a24675e16e1586e3e3aa56c06fabe907"),
    "avalanche": ("0x6b25532e1060ce10cc3b0a99e5683b91bfde6982", "0x420f5035fd5dc62a167e7e7f08b604335ae272b8"),
    "optimism": ("0x2b4069517957735be00cee0fadae88a26365528f", "0x33e76c5c31cb928dc6fe6487ab3b2c0769b1a1e3"),
    "arbitrum": ("0x19330d10d9cc8751218eaf51e8885d058642e08a", "0xe7ed1fa7f45d05c508232aa32649d89b73b8ba48"),
    "base": ("0x1682ae6375c4e4a97e4b583bc394c861a46d8962", "0xe45b133ddc64be80252b0e9c75a8e74ef280eed6"),
    "polygon": ("0x9daf8c91aefae50b9c0e69629d3f6ca40ca3b3fe", "0x10f7835f827d6cf035115e10c50a853d7fb2d2ec"),
}  # fmt: skip
CCTP_V2_MESSENGER = "0x28b5a0e9c621a5badaa536219b3a228c8168cf5d"
CCTP_V2_MINTER = "0xfd78ee919681417d192449715b2594ab58f5d002"
SPOKE_POOL = {  # Across: takes the money and emits the event
    "ethereum": "0x5c7bcd6e7de5423a257d81b442095a1a6ced35c5",
    "optimism": "0x6f26bf09b1c792e3228e5467807a900a503c0281",
    "arbitrum": "0xe35e9842fceaca96570b734083f4a58e8f7c5f2a",
    "base": "0x09aea4b2242abc8bb4bb78d537a67a245a7bec64",
    "polygon": "0x9295ee1d8c5b022be115a2ad3c30c72e34e7f096",
    "bsc": "0x4e8e101924ede233c13e2d8622dc8aed2872d505",
}
# Wormhole: (core contract, which emits the message; Token Bridge, which takes the money and
# sends the message).
WORMHOLE_CONTRACTS = {
    "ethereum": ("0x98f3c9e6e3face36baad05fe09d375ef1464288b", "0x3ee18b2214aff97000d974cf647e7c347e8fa585"),
    "bsc": ("0x98f3c9e6e3face36baad05fe09d375ef1464288b", "0xb6f6d86a8f9879a9c87f643768d9efc38c1da6e7"),
    "polygon": ("0x7a4b5a56256163f07b2c80a7ca55abe66c4ec4d7", "0x5a58505a96d1dbf8df91cb21b54419fc36e93fde"),
    "avalanche": ("0x54a8e5f9c4cba08f9943965859f6c34eaf03e26c", "0x0e082f06ff657d94310cb8ce8b0d9a04541d8052"),
    "arbitrum": ("0xa5f208e072434bc67592e4c49c1b991ba79bca46", "0x0b2402144bb366a632d14b83f244d2e0e21bd39c"),
    "optimism": ("0xee91c335eab126df5fdb3797ea9d6ad93aec9722", "0x1d68124e65fafc907325e3edbf8c4d84499daa8b"),
    "base": ("0xbebdb6c8ddc678ffa9f8748f85c815c556dd8ac6", "0x8d2de8d2f73f1f4cab472ac9a881c9b123c79627"),
}  # fmt: skip
OP_STACK = {  # L1StandardBridge on Ethereum -> the chain it deposits into
    "0x3154cf16ccdb4c6d922629664174b904d80f2c35": "base",
    "0x99c9fc46f92e8a1c0dec1b1747d010903e884be1": "optimism",
}
# Arbitrum's gateway router on Ethereum. The tokens go on to a gateway, but the ETH paying
# for the deposit on Arbitrum is always sent to the router itself.
ARBITRUM_ROUTER = "0x72ce9c846789fdb6fc1f34ac4ad25dd9ef7031ef"
# Each protocol's id of a destination -> the chain, for the chains the tracker reads. An id
# not listed (CCTP's Noble or Sui) names an address that is no address of these chains.
CCTP_DOMAINS = {0: "ethereum", 1: "avalanche", 2: "optimism", 3: "arbitrum", 5: "solana",
                6: "base", 7: "polygon"}  # fmt: skip
WORMHOLE_CHAINS = {1: "solana", 2: "ethereum", 4: "bsc", 5: "polygon", 6: "avalanche",
                   23: "arbitrum", 24: "optimism", 30: "base"}  # fmt: skip
# shortcut: Across to Solana is not decoded (none seen to check against); add it when one is.
ACROSS_CHAINS = {chain_id: chain for chain, (chain_id, _, _) in chains.EVM.items()}


def entries(chain):
    """The contracts a deposit into a bridge pays on `chain`: a transfer of mine to one of them
    picks the transaction whose receipt is read."""
    out = {CCTP_V2_MINTER, CCTP.get(chain, ("", ""))[1], SPOKE_POOL.get(chain, "")}
    out.add(WORMHOLE_CONTRACTS.get(chain, ("", ""))[1])
    return (out | ({*OP_STACK, ARBITRUM_ROUTER} if chain == "ethereum" else set())) - {""}


def _address(word):
    """The EVM address in a 32-byte word, or None when the word holds anything longer."""
    return "0x" + word[-40:] if len(word) == 64 and word[:24] == "0" * 24 else None


def _recipient(word, chain):
    """What a 32-byte recipient field holds on the destination chain."""
    return base58.encode(bytes.fromhex(word)) if chain == "solana" else _address(word)


def _wormhole(data):
    """(recipient word, Wormhole chain id) of a Token Bridge transfer, or None. Only a plain
    transfer (payload 1) names the recipient: with a payload (3), the address is a contract
    that redeems it for someone the tracker cannot see."""
    start = int(data[128:192], 16) * 2  # where the payload's length is, in hex digits
    size = int(data[start : start + 64], 16) * 2
    payload = data[start + 64 : start + 64 + size]
    return (payload[134:198], int(payload[198:202], 16)) if payload[:2] == "01" else None


def decode(chain, receipt):
    """(protocol, sender, recipient, destination chain) of each bridge deposit in a receipt.
    The sender is the depositor the event names, else whoever sent the transaction. A
    recipient on Solana is the token account CCTP and Wormhole name, not yet its owner."""
    out = []
    cctp = {CCTP.get(chain, ("",))[0], CCTP_V2_MESSENGER} - {""}
    core, token_bridge = WORMHOLE_CONTRACTS.get(chain, ("", ""))
    for log in receipt["logs"]:
        at, topics, data = log["address"], log["topics"], log["data"][2:]
        word = [data[i : i + 64] for i in range(0, len(data), 64)]
        try:
            if at in cctp and topics[0] in (CCTP_V1, CCTP_V2):
                depositor = topics[3] if topics[0] == CCTP_V1 else topics[2]
                found = ("Circle CCTP", depositor, word[1], CCTP_DOMAINS.get(int(word[2], 16)))
            elif at == SPOKE_POOL.get(chain) and topics[0] in (ACROSS, ACROSS_V3):
                dest = ACROSS_CHAINS.get(int(topics[1], 16))
                found = ("Across", topics[3], word[7], dest)
            elif at == core and topics[0] == WORMHOLE and _address(topics[1][2:]) == token_bridge:
                to, to_chain = _wormhole(data) or ("", 0)
                found = ("Wormhole", None, to, WORMHOLE_CHAINS.get(to_chain))
            elif chain == "ethereum" and at in OP_STACK and topics[0] in (OP_ETH, OP_ERC20):
                eth = topics[0] == OP_ETH
                found = ("OP Stack", topics[1 if eth else 3], topics[2][2:] if eth else word[0])
                found += (OP_STACK[at],)
            elif chain == "ethereum" and at == ARBITRUM_ROUTER and topics[0] == ARBITRUM:
                found = ("Arbitrum", topics[2], topics[3][2:], "arbitrum")
            else:
                continue
            protocol, depositor, to, dest = found
            sender = _address(depositor[2:]) if depositor else receipt["from"]
            recipient = dest and _recipient(to, dest)
        except (IndexError, ValueError):
            continue  # a log of the right contract and event that does not parse is no record
        if sender and recipient:
            out.append((protocol, sender, recipient, dest))
    return out


def normal(result):
    """An eth_getTransactionReceipt result, or a Blockscout transaction page: who sent the
    transaction, its block, and its logs with lower-case hex and no empty topics."""
    return {
        "from": result["from"].lower(),
        # Hex from a node or a proxy module, decimal from Blockscout's transaction page.
        "block": int(str(result.get("blockNumber") or 0), 0),
        "logs": [
            {
                "address": log["address"].lower(),
                "topics": [t.lower() for t in log["topics"] if t],
                "data": log["data"].lower(),
            }
            for log in result.get("logs") or []
        ],
    }


def fetch(client, cfg, txid):
    """The receipt of `txid` from the chain's indexer, or from its own node when CT_CHAINS
    gives it an `rpc`."""
    if cfg.get("rpc"):
        result = chains._rpc(client, "eth_getTransactionReceipt", txid, url=cfg["rpc"])
    elif cfg.get("api") == "nodereal":
        result = chains._nr(client, cfg, "eth_getTransactionReceipt", txid)
    else:
        # Blockscout (whose extension is `tokenlist`) has no proxy module: the transaction's
        # own page has the logs, 50 at a time.
        query = (
            {"module": "transaction", "action": "gettxinfo"}
            if cfg.get("tokenlist")
            else {"module": "proxy", "action": "eth_getTransactionReceipt"}
        )
        key = {"apikey": cfg["apikey"]} if cfg.get("apikey") else {}
        params = {**cfg.get("params", {}), **key, **query, "txhash": txid}
        result, logs = None, []
        for _ in range(PAGES):
            result = _answer(chains.get(client, cfg["url"], params).json().get("result"))
            logs += result.get("logs") or []
            if not result.get("next_page_params"):
                break
            params |= result["next_page_params"]
        else:  # unread is not empty: a bridge event may sit on a page not read
            raise RuntimeError(f"more than {PAGES} pages of logs")
        result["logs"] = logs
    return normal(_answer(result))


def _answer(result):
    if not isinstance(result, dict):
        # Not indexed yet, or an error the API words as its result.
        raise RuntimeError("no receipt yet")  # noqa: TRY004 - an answer, not a bug
    return result


def owner(client, account):
    """The wallet that owns a Solana token account; None once the account is closed. An
    account that is not a token account is a wallet itself."""
    value = chains._rpc(client, "getAccountInfo", account, {"encoding": "jsonParsed"})["value"]
    if value is None:
        return None
    if value["owner"] not in chains.TOKEN_PROGRAMS:
        return account
    return value["data"]["parsed"]["info"]["owner"]


def _pending(db):
    """(chain, txid) -> the transfer of mine that paid a bridge in it, for the receipts not
    read yet. The token bridged comes before the coin paid for the deposit's gas."""
    cfgs = chains.evm_chains()
    known = sorted(set().union(*(entries(c) for c in cfgs)))
    rows = db.execute(
        f"""SELECT * FROM transfer
            WHERE src IN (SELECT address FROM node WHERE mine)
              AND (dst IN ({",".join("?" * len(known))})
                   OR dst IN (SELECT address FROM label WHERE category = 'bridge'))
              AND NOT EXISTS (SELECT 1 FROM receipt r
                              WHERE r.chain = transfer.chain AND r.txid = transfer.txid)
            ORDER BY token = '', amount DESC""",
        known,
    )
    labelled = {r[0] for r in db.execute("SELECT address FROM label WHERE category = 'bridge'")}
    todo = {}
    for r in rows:
        cfg = cfgs.get(r["chain"])
        if (
            cfg
            and not cfg.get("needs_key")
            and (r["dst"] in labelled or r["dst"] in entries(r["chain"]))
        ):
            todo.setdefault((r["chain"], r["txid"]), r)
    return todo


def _read(db, client, cfg, chain, txid, me):
    """Store the records of my deposits in one receipt, then mark the receipt read."""
    receipt = fetch(client, cfg, txid)
    for protocol, sender, recipient, dest in decode(chain, receipt):
        if me not in (sender, receipt["from"]):
            continue  # someone else's deposit in the same transaction
        note = ""
        if dest == "solana":  # CCTP and Wormhole name a token account there
            found = owner(client, recipient)
            recipient, note = (found, "") if found else (recipient, "closed")
        if database.valid(recipient):
            db.execute(
                "INSERT OR IGNORE INTO swap(protocol, sender, recipient, tx_in, tx_out, chain, "
                "note) VALUES (?, ?, ?, ?, '', ?, ?)",
                (protocol, me, recipient, txid, dest, note),
            )
    db.execute("INSERT OR IGNORE INTO receipt VALUES (?, ?)", (chain, txid))
    db.commit()


def _link(db, client, failed):
    """Link every record of mine stored, not only this sync's: to a wallet of mine, or to a
    stranger read and found quiet. So a record whose recipient is declared mine later is
    linked then, with no receipt read again."""
    rows = db.execute(
        f"""SELECT s.protocol, s.sender, s.recipient, s.tx_in, s.chain dest, t.chain, t.amount,
                   t.asset, COALESCE(n.mine, 0) mine, COALESCE(n.hub, 0) hub,
                   EXISTS (SELECT 1 FROM link WHERE kind = 'bridge-record'
                           AND a = MIN(s.sender, s.recipient)
                           AND b = MAX(s.sender, s.recipient)) linked
            FROM swap s JOIN transfer t ON t.txid = s.tx_in AND t.src = s.sender
            LEFT JOIN node n ON n.address = s.recipient
            WHERE s.protocol IN ({",".join("?" * len(PROTOCOLS))}) AND s.note = ''
              AND s.sender != s.recipient AND s.sender IN (SELECT address FROM node WHERE mine)
            ORDER BY t.token = '', t.amount DESC""",
        PROTOCOLS,
    )
    records = {}
    for r in rows:  # my transfers in the deposit: the token bridged before the coin for gas
        records.setdefault((r["protocol"], r["tx_in"], r["recipient"]), r)
    hubs = {}
    for r in records.values():
        to = r["recipient"]
        if not r["mine"] and not r["linked"] and to not in hubs:
            # A stranger is read first: one that is busy or a service links nobody. One found
            # quiet has its link, and is not read again.
            try:
                hubs[to] = busy.check(db, client, to)
            except (httpx.HTTPError, RuntimeError, ValueError, KeyError, TypeError) as e:
                hubs[to] = True  # unread is not quiet: read again at the next sync
                failed.setdefault(to[:12] + "...", chains.describe(e))
        if r["mine"] or not (r["hub"] or hubs.get(to)):
            coins = f"{r['amount']:g} {r['asset']}"
            evidence = f"{r['protocol']}: {coins} {r['chain']} > {r['dest']} ({r['tx_in'][:18]})"
            db.execute(
                "INSERT OR REPLACE INTO link VALUES (?, ?, 'bridge-record', ?, ?)",
                (*pair(r["sender"], to), CONFIDENCE, evidence),
            )
    db.commit()


def sync(db, client):
    """Read the receipts of my deposits into bridges not read yet, store their records, then
    link the records. Returns what failed, which the next sync tries again."""
    cfgs = chains.evm_chains()
    down, failed = set(), {}
    for (chain, txid), paid in _pending(db).items():
        if chain in down:
            continue  # its indexer is down: the other receipts wait for the next sync
        try:
            _read(db, client, cfgs[chain], chain, txid, paid["src"])
        except (httpx.HTTPError, chains.RateLimited) as e:
            down.add(chain)
            failed[chain] = chains.describe(e)
        except (RuntimeError, ValueError, KeyError, TypeError) as e:
            # This receipt only (not indexed yet, an answer that does not parse): the chain's
            # other receipts are still read, and this one again at the next sync.
            failed.setdefault(chain, chains.describe(e))
    _link(db, client, failed)
    reason = "; ".join(f"{where}: {why}" for where, why in failed.items())
    if failed:  # stored: until it is read, a record may be missing from every verdict
        db.execute(
            "INSERT OR REPLACE INTO failure VALUES (?, ?, ?)", (SOURCE, reason, int(time.time()))
        )
    else:
        db.execute("DELETE FROM failure WHERE source = ?", (SOURCE,))
    db.commit()
    return [f"{SOURCE}: {reason}"] if failed else []


def strangers(db, addresses):
    """The bridge records of these addresses whose recipient is not a wallet of mine: linked
    to it, or not because it is busy or a service, or a token account closed since."""
    marks = ",".join("?" * len(addresses))
    rows = db.execute(
        f"""SELECT protocol, recipient address, chain, note = 'closed' closed,
                   MAX(EXISTS (SELECT 1 FROM link WHERE kind = 'bridge-record'
                               AND a = MIN(sender, recipient) AND b = MAX(sender, recipient))) linked
            FROM swap
            WHERE sender IN ({marks}) AND protocol IN ({",".join("?" * len(PROTOCOLS))})
              AND recipient NOT IN (SELECT address FROM node WHERE mine)
            GROUP BY protocol, recipient, chain, note ORDER BY protocol, recipient""",
        [*addresses, *PROTOCOLS],
    )
    return [dict(r) | {"linked": bool(r["linked"]), "closed": bool(r["closed"])} for r in rows]
