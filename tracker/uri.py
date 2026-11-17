"""What a pasted line or a scanned QR code names: a plain address, or a payment URI
(BIP-21 `bitcoin:`, EIP-681 `ethereum:`, Solana Pay `solana:` and their siblings).
Only the address is kept: amounts, labels and memos are dropped."""

from urllib.parse import parse_qs

from . import chains
from . import db as database

SCHEMES = {"bitcoin", "litecoin", "dogecoin", "bitcoincash", "tron", "solana"}


def parse(text):
    """(address, chain, EVM chain the URI names or None), or ValueError."""
    text = text.strip()
    scheme, colon, rest = text.partition(":")
    scheme = scheme.lower()
    evm = chains.evm_chains()
    if not colon or scheme not in SCHEMES | {"ethereum"} | set(evm):
        chain, address = database.parse(text)
        return address, chain, None
    target, _, query = rest.partition("?")
    target = target.lstrip("/")
    hint = None
    if scheme not in SCHEMES:  # EIP-681: [pay-]target[@chain id][/function]
        target, _, function = target.removeprefix("pay-").partition("/")
        target, _, chain_id = target.partition("@")
        if function not in ("", "transfer"):
            raise ValueError("that is a contract call, not a wallet's address")
        if function == "transfer":  # a token payment: the target is the token, not the payee
            target = parse_qs(query).get("address", [""])[0]
        by_id = {str(v[0]): name for name, v in chains.EVM.items() if name in evm}
        hint = by_id.get(chain_id) if chain_id else scheme if scheme != "ethereum" else None
    if scheme == "bitcoincash":
        target = "bitcoincash:" + target
    chain, address = database.parse(target)
    if chain != ("evm" if scheme not in SCHEMES else scheme):
        raise ValueError(f"not a {scheme} address this app can read")
    return address, chain, hint
