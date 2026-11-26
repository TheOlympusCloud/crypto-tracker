"""Activity hours (docs/design/multichain.md, 2.2 #5): the hours at which a wallet sends, and
the time zone they suggest. Most people send nothing while they sleep, so the quietest eight
hours of the day show where the night falls, give or take a couple of hours. An estimate
about one wallet, never a link: millions of people share a time zone (2.8).

It rests on the transactions the wallet sent, by their block time: at least 20 of them, and
only when one stretch of eight hours holds at most a tenth and no other apart is as quiet (a
bot, or people in several time zones, show no night). A wallet of mine is read from what syncs stored, over all its
addresses; any other address from the scan's own read. On EVM chains only transactions the
address signed count, so a token pulled from it by someone else's transaction does not.
"""

from collections import Counter

from .. import lookup

ENOUGH = 20  # sent transactions an estimate needs
NIGHT = 8  # hours of a night, taken as midnight to 8:00 local time
QUIET = 0.1  # the most of the sent transactions a night holds


def estimate(times):
    """The finding's params for the Unix times `times`: how many, the quiet hours in UTC and
    the UTC offset they suggest; None below ENOUGH or when no night shows."""
    hours = Counter(t // 3600 % 24 for t in times)
    nights = [sum(hours[(h + i) % 24] for i in range(NIGHT)) for h in range(24)]
    low = min(nights)
    if len(times) < ENOUGH or low > QUIET * len(times):
        return None
    # A short day leaves several equally quiet nights in a row: take the middle one. Two such
    # stretches apart (busy at noon and at midnight) leave nothing to tell which is the night.
    starts = [h for h in range(24) if nights[h] == low and nights[h - 1] != low]
    if len(starts) > 1:
        return None
    first = starts[0]
    run = next(n for n in range(1, 24) if nights[(first + n) % 24] != low)
    start = (first + run // 2) % 24
    offset = -start % 24  # a night from midnight local starts at -offset UTC
    offset -= 24 if offset > 12 else 0
    quiet = f"{start:02d}:00–{(start + NIGHT) % 24:02d}:00"
    return {"count": len(times), "quiet": quiet, "zone": f"UTC{offset:+d}"}


def sent(db, address):
    """Block times of the transactions the wallet of `address` (all its addresses) sent."""
    rows = db.execute(
        "SELECT DISTINCT t.chain, t.txid, t.ts FROM transfer t WHERE t.ts > 0 AND t.amount > 0 "
        "AND t.src IN (SELECT address FROM node WHERE COALESCE(wallet, address) = "
        "(SELECT COALESCE(wallet, address) FROM node WHERE address = ?1) UNION SELECT ?1) "
        # On EVM chains a transfer from the address may be someone else's transaction.
        "AND (t.src NOT LIKE '0x%' OR t.txid IN (SELECT txid FROM sent WHERE address = t.src))",
        (address,),
    )
    return [r["ts"] for r in rows]


def check(ctx):
    mine = ctx.db.execute("SELECT 1 FROM node WHERE address = ? AND mine", (ctx.address,))
    if mine.fetchone():
        times = sent(ctx.db, ctx.address)
    else:
        _, read = lookup.READS.get((ctx.address, tuple(ctx.view["chains"])), (0, None))
        if not read:
            return
        signed = read.get("sent", set())
        times = {
            (t[0], t[1]): t[7]
            for t in read["transfers"]
            if t[2] == ctx.address and t[6] > 0 and t[7] > 0
            and (not ctx.address.startswith("0x") or t[1] in signed)
        }.values()  # fmt: skip
        times = list(times)
    if found := estimate(times):
        yield "x-activity-hours", found, []
