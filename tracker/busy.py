"""Is a stranger busy: an exchange's hot wallet, a relayer, a rental service, a marketplace?

A link through such an address would join the wallets of everyone who used it, so a link
to a stranger is stored only once the stranger has been read and found quiet
(docs/design/multichain.md, 3.5). A busy or labelled stranger is stored as a hub, which ends
every path through it (graph.trace). A quiet one stores nothing.

A sync reads strangers only on the deep chains and only when asked for depth, so the
checks that link to strangers (Solana fee payers, Tron delegators, first funders, shared
deposit addresses) call this first, within the sync's budget.
"""

from . import chains, sync
from . import db as database

SOLANA_BUSY = 1000  # signatures in one read: more than one person's wallet makes


def check(db, client, address):
    """True when `address`, not one of mine, is busy or labelled; False when it was read and
    is quiet. One call on Solana or Tron; elsewhere a sync of the address. Raises when the
    read fails: an unread address is not a quiet one."""
    row = db.execute("SELECT mine, hub FROM node WHERE address = ?", (address,)).fetchone()
    if row and row["mine"]:
        return False  # mine, however busy: a wallet filling in its history is no service
    if row and row["hub"]:
        return True
    chain = database.chain_of(address)
    if db.execute("SELECT 1 FROM label WHERE address = ?", (address,)).fetchone():
        hub = True
    elif chain == "solana":
        limit = {"limit": SOLANA_BUSY}
        hub = len(chains._rpc(client, "getSignaturesForAddress", address, limit)) >= SOLANA_BUSY
    elif chain == "tron":
        hub = len(chains.page_tron(client, address, "native").items) >= chains.TRON_ROWS
    else:  # a sync reads it, keeps what it read and sets node.hub
        sync.sync_address(db, client, address)
        row = db.execute("SELECT hub, error FROM node WHERE address = ?", (address,)).fetchone()
        if row["error"]:
            raise RuntimeError(row["error"])
        return bool(row["hub"])
    if hub:
        db.execute(
            "INSERT INTO node(address, hub) VALUES (?, 1) ON CONFLICT(address) DO UPDATE SET hub = 1",
            (address,),
        )
        db.commit()
    return hub
