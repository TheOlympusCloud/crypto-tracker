"""HD wallets: an extended public key or output descriptor stands for all its addresses.

Only public keys are accepted. The key itself never leaves this machine; the addresses
derived from it are what gets sent to the indexer.
"""

import hashlib
import re

from embit import bip32
from embit.descriptor import Descriptor
from embit.networks import NETWORKS

GAP = 20  # consecutive unused addresses that end the scan of a branch (BIP44)
PRIVATE = re.compile(r"(?<![1-9A-HJ-NP-Za-km-z])[a-zA-Z]prv[1-9A-HJ-NP-Za-km-z]{50,}")
# SLIP-132 prefixes say which script the wallet uses. A plain xpub does not: wallets export
# it for every address type, so all of them are scanned.
SCRIPTS = {
    "xpub": ["pkh({})", "sh(wpkh({}))", "wpkh({})", "tr({})"],
    "ypub": ["sh(wpkh({}))"],
    "zpub": ["wpkh({})"],
}


def looks_like(text):
    text = text.strip()
    return "(" in text or text[:4] in SCRIPTS or bool(PRIVATE.search(text))


def descriptors(text):
    """The descriptors a pasted xpub/ypub/zpub or descriptor stands for, or ValueError."""
    text = text.strip()
    if PRIVATE.search(text):
        raise ValueError("that is a private key: never enter one here, use the xpub")
    private = False
    try:
        if "(" in text:
            found = [Descriptor.from_string(text)]
        else:
            key = bip32.HDKey.from_string(text)
            private = key.is_private
            xpub = key.to_base58(version=NETWORKS["main"]["xpub"])
            found = [Descriptor.from_string(t.format(xpub + "/<0;1>/*")) for t in SCRIPTS[text[:4]]]
        private = private or any(key.is_private for d in found for key in d.keys)
    except Exception as e:
        raise ValueError("not a valid xpub, ypub, zpub or descriptor") from e
    if private:
        raise ValueError("that is a private key: never enter one here, use the xpub")
    return found


def wallet_id(text):
    return "hd:" + hashlib.sha256(text.strip().encode()).hexdigest()[:16]


def address(descriptor, branch, index):
    return descriptor.derive(index, branch_index=branch).address()
