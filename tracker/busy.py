"""Is a stranger busy: an exchange's hot wallet, a relayer, a rental service, a marketplace?

A link through such an address would join the wallets of everyone who used it, so a link
to a stranger is stored only once the stranger has been read and found quiet
(docs/design/multichain.md, 3.5). A busy stranger, or a service labelled by a sure source,
is stored as a hub, which ends every path through it (graph.trace). A quiet one is noted in
`quiet`, which nothing but this check reads, so it is read once.

A sync reads strangers only on the deep chains and only when asked for depth, so the
checks that link to strangers (Solana fee payers, Tron delegators, first funders, shared
deposit addresses) call this first, within the sync's budget.
"""

from . import chains, sync
from . import db as database
from .heuristics import SERVICE
from .labels import CONFIDENCE

SOLANA_BUSY = 1000  # signatures in one read: more than one person's wallet makes


def known(db, address):
    """What `check` would say without a call: True for a hub or a service known for sure,
    False for mine or a stranger read and found quiet, None for a stranger not read yet."""
    row = db.execute("SELECT mine, hub FROM node WHERE address = ?", (address,)).fetchone()
    if row and row["mine"]:
        return False  # mine, however busy: a wallet filling in its history is no service
    if row and row["hub"]:
        return True
    label = db.execute(
        "SELECT category, source FROM label WHERE address = ?", (address,)
    ).fetchone()
    # As in graph.trace: only a service known for sure ends a path. A guessed label, or a
    # person's name, is read like any stranger: a wrong guess must not hide a link.
    if label and label["category"] in SERVICE and label["source"] not in CONFIDENCE:
        return True
    # shortcut: a quiet answer is kept for good, so a fresh hot wallet read while new stays
    # one person. Store when it was read, and read it again after a while, if that happens.
    if db.execute("SELECT 1 FROM quiet WHERE address = ?", (address,)).fetchone():
        return False
    return None


def check(db, client, address):
    """True when `address`, not one of mine, is busy or a known service; False when it was
    read and is quiet. One call on Solana or Tron (the native list only); elsewhere a sync of
    the address; none when `known` already says. Raises when the read fails: an unread
    address is not a quiet one. The calls count against no budget here: each caller counts
    them in its own."""
    hub = known(db, address)
    if hub is False:
        return False
    if hub is None:
        hub = _read(db, client, address)
    if hub:  # a labelled service is stored as a hub too
        db.execute(
            "INSERT INTO node(address, hub) VALUES (?, 1) ON CONFLICT(address) DO UPDATE SET hub = 1",
            (address,),
        )
    else:
        db.execute("INSERT OR IGNORE INTO quiet VALUES (?)", (address,))
    db.commit()
    return hub


def _read(db, client, address):
    chain = database.chain_of(address)
    if chain == "solana":
        limit = {"limit": SOLANA_BUSY}
        return len(chains._rpc(client, "getSignaturesForAddress", address, limit)) >= SOLANA_BUSY
    if chain == "tron":
        return len(chains.page_tron(client, address, "native").items) >= chains.TRON_ROWS
    # Elsewhere a sync reads it, keeps what it read and sets node.hub.
    sync.sync_address(db, client, address)
    row = db.execute("SELECT hub, error FROM node WHERE address = ?", (address,)).fetchone()
    if row["error"]:
        raise RuntimeError(row["error"])
    return bool(row["hub"])
